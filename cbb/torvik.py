"""Barttorvik's T-Rank ratings: the same job as KenPom, over plain HTTP.

KenPom's table is public but sits behind Cloudflare, which answers anything
that is not a real browser with a challenge. That is fine on a machine with a
browser and impossible on a hosted container, so a board that has to run in
the cloud needs a rating it can simply fetch. Barttorvik publishes the whole
season as a JSON array - no key, no challenge, no browser - and T-Rank is the
same kind of tempo-free rating, so the column means what it always meant even
though the numbers are not KenPom's.

The file is positional, not keyed, which is why the indices below are named
constants: the first four are rank, school, conference and record, and the
efficiencies follow in pairs of value-then-rank.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

from core.teams import TeamIndex

log = logging.getLogger(__name__)

URL = "https://barttorvik.com/{season}_team_results.json"
EASTERN = ZoneInfo("America/New_York")

RANK, TEAM, CONFERENCE, RECORD, ADJ_O, _, ADJ_D = range(7)
"""Positions in a row. Column 5 is AdjO's own rank, which we do not use."""

MIN_PLAUSIBLE_TEAMS = 300
"""D-I is ~365 schools; a much shorter list is a partial or changed file."""

_HEADERS = {"User-Agent": "ncaab-dashboard/0.1", "Accept": "application/json"}


def season_year(today: Optional[date] = None) -> int:
    """The season a date belongs to, as Barttorvik numbers it.

    A season runs November to April and is filed under the calendar year it
    ends in, so November 2026 is the 2027 file. June is the cut: everything
    from then on belongs to the season about to start.
    """
    today = today or datetime.now(EASTERN).date()
    return today.year + 1 if today.month >= 6 else today.year


@dataclass(frozen=True)
class TorvikTeam:
    """One school's line in the ratings."""

    rank: int
    team: str
    conference: str
    record: str
    net_rating: Optional[float]
    """AdjO minus AdjD: points per 100 possessions against an average team."""

    @property
    def summary(self) -> str:
        parts = [f"T-Rank #{self.rank}", self.conference, self.record]
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


def parse_ratings(payload: Any) -> list[TorvikTeam]:
    """Rows to teams, skipping anything that is not shaped like one.

    The file is a list of positional lists, so a row that has grown or lost a
    column should drop out rather than silently become a team with the wrong
    rating in it.
    """
    teams: list[TorvikTeam] = []
    if not isinstance(payload, list):
        return teams
    for row in payload:
        if not isinstance(row, list) or len(row) <= ADJ_D:
            continue
        try:
            rank = int(row[RANK])
            name = str(row[TEAM]).strip()
            net = float(row[ADJ_O]) - float(row[ADJ_D])
        except (TypeError, ValueError):
            continue
        if not name:
            continue
        teams.append(
            TorvikTeam(
                rank=rank,
                team=name,
                conference=str(row[CONFERENCE]).strip(),
                record=str(row[RECORD]).strip(),
                net_rating=round(net, 2),
            )
        )
    return teams


# A successful fetch, keyed by the Eastern day it describes.
_cache: dict[date, list[TorvikTeam]] = {}


class TorvikClient:
    """Season ranks by school name. `load()` must not raise."""

    label = "T-Rank"
    short_label = "TRk"
    source_url = "https://barttorvik.com/"

    def __init__(self, client: Optional[httpx.Client] = None, timeout: float = 30.0) -> None:
        self._client = client
        self._timeout = timeout
        self._index: TeamIndex[TorvikTeam] = TeamIndex()
        self.teams: list[TorvikTeam] = []
        self.note: Optional[str] = None
        self.loaded_ok = False

    def load(self) -> None:
        today = datetime.now(EASTERN).date()
        cached = _cache.get(today)
        if cached:
            self._adopt(cached)
            log.info("torvik: reusing today's %d ranks", len(cached))
            return

        url = URL.format(season=season_year(today))
        client = self._client or httpx.Client(
            timeout=self._timeout, follow_redirects=True, headers=_HEADERS
        )
        try:
            response = client.get(url)
            response.raise_for_status()
            teams = parse_ratings(response.json())
        except Exception as exc:  # noqa: BLE001 - a dead rating is a column, not a crash
            log.warning("torvik: could not read %s (%s)", url, exc)
            self.note = f"could not read the T-Rank ratings: {exc}"
            return
        finally:
            if self._client is None:
                client.close()

        if not teams:
            self.note = "the T-Rank file returned no teams"
            return
        if len(teams) < MIN_PLAUSIBLE_TEAMS:
            self.note = (
                f"T-Rank returned only {len(teams)} of ~365 teams; "
                "ranks may be missing from the board"
            )
        _cache[today] = teams
        self._adopt(teams)
        log.info("torvik: %d teams ranked", len(teams))

    def _adopt(self, teams: list[TorvikTeam]) -> None:
        self.teams = teams
        self._index = TeamIndex()
        for team in teams:
            self._index.register([team.team], team)
        self.loaded_ok = bool(teams)

    def rank(self, team: str) -> Optional[TorvikTeam]:
        return self._index.lookup(team) if self.loaded_ok else None


__all__ = ["TorvikClient", "TorvikTeam", "parse_ratings", "season_year", "URL"]
