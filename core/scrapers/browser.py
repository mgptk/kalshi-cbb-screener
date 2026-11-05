"""Selenium session helper for the books that render odds client-side.

Both DraftKings and FanDuel stopped server-rendering their boards, so a plain
`requests` + BeautifulSoup pass gets a shell with no prices in it. A real
browser is the only way to see what a human sees.

Selenium is an optional dependency: if it isn't installed, or no Edge/Chrome
driver can start, `open_browser` returns None and the caller falls back to a
non-browser source rather than the whole refresh failing.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Iterator, Optional

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0"
)


@contextlib.contextmanager
def open_browser(
    headless: bool = True,
    window: tuple[int, int] = (1920, 3000),
    profile_dir: Optional[str] = None,
) -> Iterator[Optional[object]]:
    """Yield a Selenium driver, or None if one can't be started.

    `profile_dir` points Edge at a persistent user-data directory. That matters
    for any site that has challenged you once: complete the challenge by hand in
    that profile, and the browser carries the resulting cookie afterwards.
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.edge.options import Options
    except ImportError:
        log.info("selenium is not installed; skipping the browser-based source")
        yield None
        return

    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument(f"--window-size={window[0]},{window[1]}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(f"user-agent={USER_AGENT}")
    if profile_dir:
        options.add_argument(f"--user-data-dir={profile_dir}")
    with contextlib.suppress(Exception):
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

    driver = None
    try:
        driver = webdriver.Edge(options=options)
        driver.set_page_load_timeout(90)
        yield driver
    except Exception as exc:  # noqa: BLE001 - any driver failure is a fallback, not a crash
        log.warning("could not start a browser: %s", exc)
        yield None
    finally:
        if driver is not None:
            with contextlib.suppress(Exception):
                driver.quit()


BOT_WALL_MARKERS = (
    "Press & Hold",
    "Access to this page has been denied",
    "confirm you are a human",
    "Verifying you are human",
    # Cloudflare's interstitial, which is what a plain request to KenPom gets.
    "Just a moment...",
)


def hit_a_bot_wall(page_source: str) -> bool:
    """Whether the page we got back is a human-verification challenge.

    We detect these so the scraper can say so plainly and stop. Solving them is
    off the table; the fix is for a person to clear the challenge once in a
    persistent browser profile.
    """
    return any(marker in page_source for marker in BOT_WALL_MARKERS)
