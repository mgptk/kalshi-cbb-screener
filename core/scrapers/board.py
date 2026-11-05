"""Shared machinery for scraping a sportsbook's own board in a browser.

All three books render their odds client-side, so each one is the same job:
drive a browser to a URL, let it render, hand the HTML to a book-specific
parser, then answer per-matchup lookups. Only `URL` and `parse` differ.

The important detail is the failure taxonomy. A book that we read successfully
but which has no moneyline on a game is reporting something real — usually a
favourite too large to price — and that is not the same as a scrape that broke.
`BoardScraper` keeps the two apart so a missing number is never ambiguous.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

from ..models import BookQuote, Matchup
from ..teams import normalize, pair_key
from .base import build_quote
from .browser import hit_a_bot_wall, open_browser

log = logging.getLogger(__name__)

_ODDS = re.compile(r"^[+−\-]?\d{2,6}$")


@dataclass
class BoardLine:
    """One game as a book has it. Either price may be missing on its own."""

    away_american: Optional[float]
    home_american: Optional[float]
    away_team: str
    home_team: str

    @property
    def two_sided(self) -> bool:
        return self.away_american is not None and self.home_american is not None

    @property
    def on_board(self) -> bool:
        """True whenever the book lists the game, priced moneyline or not."""
        return True


def american(text: str) -> Optional[float]:
    """Parse a displayed price. Books render minus as U+2212, not a hyphen."""
    cleaned = (text or "").replace("−", "-").replace("+", "").strip()
    if not cleaned or not _ODDS.match(cleaned.replace("-", "", 1)):
        try:
            return float(cleaned)
        except ValueError:
            return None
    try:
        return float(cleaned)
    except ValueError:
        return None


class BoardScraper:
    """Base class: subclasses set `name`/`URL` and implement `parse`."""

    name: str = "book"
    URL: str = ""
    RENDER_WAIT_SECONDS: float = 10.0
    READY_SELECTOR: Optional[str] = None
    """CSS selector proving the board itself rendered, not just its shell."""
    READY_TIMEOUT: float = 40.0
    SCROLL_PASSES: int = 6
    SCROLL_PIXELS: int = 2200
    WINDOW: tuple[int, int] = (1920, 4000)

    def __init__(
        self,
        use_browser: bool = True,
        headless: bool = True,
        profile_dir: Optional[str] = None,
    ) -> None:
        self._use_browser = use_browser
        self._headless = headless
        self._profile_dir = profile_dir
        self._lines: dict[frozenset[str], BoardLine] = {}
        self.note: Optional[str] = None
        self.source = f"{self.name.lower()}:board"
        self.loaded_ok = False

    # -- subclass hook -----------------------------------------------------

    def parse(self, html: str) -> dict[frozenset[str], BoardLine]:  # pragma: no cover
        raise NotImplementedError

    # -- loading -----------------------------------------------------------

    def load(self) -> None:
        if not self._use_browser:
            self.note = "browser scraping disabled"
            return
        html = self._render()
        if html is None:
            return
        if hit_a_bot_wall(html):
            log.warning("%s: served a human-verification challenge", self.name)
            self.note = (
                f"{self.name} served a human-verification challenge; "
                "no prices read (this is a block, not a missing line)"
            )
            return
        try:
            self._lines = self.parse(html)
        except Exception as exc:  # noqa: BLE001 - a parser bug must not sink a refresh
            log.warning("%s: parsing the board failed: %s", self.name, exc)
            self.note = f"could not parse {self.name}'s board: {exc}"
            return
        if not self._lines:
            self.note = f"{self.name}'s board rendered but no games were parsed"
            return
        self.loaded_ok = True
        priced = sum(1 for line in self._lines.values() if line.two_sided)
        log.info(
            "%s: %d games on the board, %d with a two-sided moneyline",
            self.name,
            len(self._lines),
            priced,
        )

    def _await_content(self, driver: Any) -> None:
        """Wait until the board's own markup appears, not for a fixed interval.

        A sleep long enough for a book on a good day is too short on a bad one,
        and the failure is silent in the worst way: the page shell parses fine
        and the book reports "rendered but no games", which reads like a dead
        selector rather than a slow load. Polling for `READY_SELECTOR` removes
        the guess; `RENDER_WAIT_SECONDS` stays as the floor and the fallback for
        a book that declares no selector.
        """
        time.sleep(min(self.RENDER_WAIT_SECONDS, 4.0))
        if not self.READY_SELECTOR:
            time.sleep(max(self.RENDER_WAIT_SECONDS - 4.0, 0))
            return

        deadline = time.monotonic() + self.READY_TIMEOUT
        script = f"return document.querySelectorAll({self.READY_SELECTOR!r}).length"
        while time.monotonic() < deadline:
            try:
                if int(driver.execute_script(script) or 0) > 0:
                    return
            except Exception:  # noqa: BLE001 - a flaky probe must not end the load
                pass
            time.sleep(1.0)
        log.warning(
            "%s: %r never appeared within %.0fs; parsing whatever rendered",
            self.name,
            self.READY_SELECTOR,
            self.READY_TIMEOUT,
        )

    def _render(self) -> Optional[str]:
        with open_browser(
            headless=self._headless, window=self.WINDOW, profile_dir=self._profile_dir
        ) as driver:
            if driver is None:
                self.note = "no browser available; install selenium and an Edge driver"
                return None
            try:
                driver.get(self.URL)
                self._await_content(driver)
                for _ in range(self.SCROLL_PASSES):
                    driver.execute_script(f"window.scrollBy(0,{self.SCROLL_PIXELS})")
                    time.sleep(1.5)
                return driver.page_source
            except Exception as exc:  # noqa: BLE001
                log.warning("%s: page load failed: %s", self.name, exc)
                self.note = f"could not load {self.name}'s board: {exc}"
                return None

    # -- lookup ------------------------------------------------------------

    def quote(self, matchup: Matchup) -> BookQuote:
        """Answer for one game, distinguishing every reason a price is missing."""
        if not self.loaded_ok:
            return BookQuote.unavailable(self.name, self.note or "book could not be read")

        line = self._lines.get(pair_key(matchup.away_team, matchup.home_team))
        if line is None:
            return BookQuote.not_on_board(self.name)

        away, home = line.away_american, line.home_american
        if normalize(line.away_team) != normalize(matchup.away_team):
            away, home = home, away

        quote = build_quote(self.name, away, home, source=self.source)
        if quote.note is None and self.note:
            quote.note = self.note
        return quote
