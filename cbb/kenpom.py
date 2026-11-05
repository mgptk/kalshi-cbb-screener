"""KenPom's season ratings, read for one number: each school's overall rank.

The ratings table on the front page of kenpom.com is public — the paywall is on
the per-team pages and the historical tools, not on the ranking itself — so no
account is involved here. What *is* in the way is Cloudflare: a plain HTTP get
comes back as the "Just a moment..." interstitial with no table in it. A real
browser renders the page the way a person's would, so this reuses the same
Selenium helper the books already need, and falls back to a plain request first
in case the challenge is not being served that day.

Nothing here tries to defeat the challenge. If the browser is also shown one,
the client says so in `note` and the dashboard renders without ranks rather
than guessing at them.

Ranks move once a day, after the previous night's games, so a successful scrape
is kept for the rest of that Eastern day: pressing Refresh re-reads the books
and Kalshi without paying for another browser launch here.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from core.scrapers.browser import hit_a_bot_wall, open_browser
from core.teams import TeamIndex

log = logging.getLogger(__name__)

KENPOM_URL = "https://kenpom.com/"
TABLE_ID = "ratings-table"
EASTERN = ZoneInfo("America/New_York")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0"
    ),
    "Accept": "text/html,application/xhtml+xml",
}

MIN_PLAUSIBLE_TEAMS = 300
"""D-I is ~365 schools. A table much shorter than that is a partial render."""

_NUMBER = re.compile(r"^[+-]?\d+(?:\.\d+)?$")


@dataclass(frozen=True)
class KenPomTeam:
    """One school's line in the ratings table."""

    rank: int
    team: str
    conference: str
    record: str
    net_rating: Optional[float]

    @property
    def summary(self) -> str:
        """What the rank cell says on hover."""
        parts = [f"KenPom #{self.rank}", self.conference, self.record]
        if self.net_rating is not None:
            parts.append(f"net {self.net_rating:+.2f}")
        return " · ".join(p for p in parts if p)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "team": self.team,
            "conference": self.conference,
            "record": self.record,
            "net_rating": self.net_rating,
        }


def _float(text: str) -> Optional[float]:
    cleaned = (text or "").replace("−", "-").strip()
    return float(cleaned) if _NUMBER.match(cleaned) else None


def parse_ratings(html: str) -> list[KenPomTeam]:
    """Pull the ratings table out of the front page.

    The table is split across ten `<tbody>` elements with a repeated header
    between them, so rows are taken by shape — a leading integer rank and a
    team cell — rather than by position. Tournament seeds ride along inside the
    team cell as a `<span class="seed">`; the anchor text is the school.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id=TABLE_ID)
    if table is None:
        return []

    teams: list[KenPomTeam] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 5:
            continue
        rank_text = cells[0].get_text(strip=True)
        if not rank_text.isdigit():
            continue
        link = cells[1].find("a")
        name = (link or cells[1]).get_text(strip=True)
        if not name:
            continue
        teams.append(
            KenPomTeam(
                rank=int(rank_text),
                team=name,
                conference=cells[2].get_text(strip=True),
                record=cells[3].get_text(strip=True),
                net_rating=_float(cells[4].get_text(strip=True)),
            )
        )
    return teams


# A successful scrape, keyed by the Eastern day it describes.
_cache: dict[date, list[KenPomTeam]] = {}


class KenPomClient:
    """Season ranks, looked up by any spelling of a school name.

    Like a `BookScraper`, `load()` must not raise: a dead KenPom costs the page
    its rank column, not the refresh.
    """

    label = "KenPom"
    short_label = "KP"
    source_url = KENPOM_URL

    RENDER_TIMEOUT = 45.0
    WINDOW = (1400, 3000)

    def __init__(
        self,
        use_browser: bool = True,
        headless: bool = True,
        profile_dir: Optional[str] = None,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._use_browser = use_browser
        self._headless = headless
        self._profile_dir = profile_dir
        self._client = client
        self._index: TeamIndex[KenPomTeam] = TeamIndex()
        self.teams: list[KenPomTeam] = []
        self.note: Optional[str] = None
        self.loaded_ok = False

    # -- loading -----------------------------------------------------------

    def load(self) -> None:
        today = datetime.now(EASTERN).date()
        cached = _cache.get(today)
        if cached:
            self._adopt(cached)
            log.info("kenpom: reusing today's %d ranks", len(cached))
            return

        teams = self._fetch_plain()
        if len(teams) < MIN_PLAUSIBLE_TEAMS:
            teams = self._fetch_rendered() or teams
        if not teams:
            self.note = self.note or "could not read the KenPom ratings table"
            return
        if len(teams) < MIN_PLAUSIBLE_TEAMS:
            # Half a table means half the ranks are silently missing, which
            # looks identical to "these schools are unranked". Say so instead.
            self.note = (
                f"KenPom returned only {len(teams)} of ~365 teams; "
                "ranks may be missing from the board"
            )
        _cache[today] = teams
        self._adopt(teams)
        log.info("kenpom: %d teams ranked", len(teams))

    def _adopt(self, teams: list[KenPomTeam]) -> None:
        self.teams = teams
        self._index = TeamIndex()
        for team in teams:
            self._index.register([team.team], team)
        self.loaded_ok = bool(teams)

    def _fetch_plain(self) -> list[KenPomTeam]:
        """The cheap attempt. Usually returns nothing: Cloudflare sits here."""
        client = self._client or httpx.Client(timeout=30.0, follow_redirects=True, headers=_HEADERS)
        try:
            response = client.get(KENPOM_URL)
            if response.status_code != 200 or hit_a_bot_wall(response.text):
                log.info("kenpom: plain request was challenged (%s)", response.status_code)
                return []
            return parse_ratings(response.text)
        except Exception as exc:  # noqa: BLE001 - fall through to the browser
            log.info("kenpom: plain request failed (%s)", exc)
            return []
        finally:
            if self._client is None:
                client.close()

    def _fetch_rendered(self) -> list[KenPomTeam]:
        if not self._use_browser:
            self.note = "browser scraping disabled; no KenPom ranks"
            return []
        with open_browser(
            headless=self._headless, window=self.WINDOW, profile_dir=self._profile_dir
        ) as driver:
            if driver is None:
                self.note = "no browser available; install selenium and an Edge driver"
                return []
            try:
                driver.get(KENPOM_URL)
                self._await_table(driver)
                html = driver.page_source
            except Exception as exc:  # noqa: BLE001
                log.warning("kenpom: page load failed: %s", exc)
                self.note = f"could not load KenPom: {exc}"
                return []
        if hit_a_bot_wall(html):
            self.note = (
                "KenPom served a human-verification challenge; no ranks read "
                "(clear it once by hand in a persistent browser profile)"
            )
            return []
        return parse_ratings(html)

    def _await_table(self, driver: Any) -> None:
        """Wait for the table's rows, not for a fixed interval.

        The Cloudflare interstitial reloads itself into the real page, so the
        first page source is a challenge even on a run that ends up fine.
        """
        script = f"return document.querySelectorAll('#{TABLE_ID} tr').length"
        deadline = time.monotonic() + self.RENDER_TIMEOUT
        while time.monotonic() < deadline:
            try:
                if int(driver.execute_script(script) or 0) >= MIN_PLAUSIBLE_TEAMS:
                    return
            except Exception:  # noqa: BLE001 - a flaky probe must not end the load
                pass
            time.sleep(1.0)
        log.warning("kenpom: the ratings table never filled in within %.0fs", self.RENDER_TIMEOUT)

    # -- lookup ------------------------------------------------------------

    def rank(self, team: str) -> Optional[KenPomTeam]:
        return self._index.lookup(team) if self.loaded_ok else None


__all__ = ["KenPomClient", "KenPomTeam", "parse_ratings", "KENPOM_URL"]
