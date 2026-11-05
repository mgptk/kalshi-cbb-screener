"""ESPN's own BPI ranking, 1 to 365, as a ratings source.

The third rank source, and the only one a hosted container can actually
reach. KenPom needs a browser for Cloudflare. Barttorvik answers a laptop
happily but returns 403 to a datacenter address, which is exactly where a
deployed copy runs - measured the hard way, on a live Community Cloud app.
ESPN answers from anywhere, which the board already relies on for the
schedule and the per-game BPI predictions.

It has one advantage the others cannot have: ESPN gives a team id, and the
board already knows each game's ESPN team ids from the schedule. So the rank
is matched by id rather than by name, and the whole business of spelling -
"W Illinois", "Miss Valley St" - never arises. Names are indexed too, as a
fallback for a game ESPN did not match.

The payload is one ~2 MB document for the whole of Division I, so it is
fetched once a day like the others.
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

URL = (
    "https://site.web.api.espn.com/apis/fitt/v3/sports/"
    "basketball/mens-college-basketball/powerindex"
)
EASTERN = ZoneInfo("America/New_York")

BPI_CATEGORY = "bpi"
BPI_VALUE = "bpi"
BPI_RANK = "bpirank"
WINS, LOSSES = "wins", "losses"
"""Stat names inside the bpi category, which is positional: `names` lists the
columns and `values` holds them in the same order."""

MIN_PLAUSIBLE_TEAMS = 300
_HEADERS = {"User-Agent": "ncaab-dashboard/0.1", "Accept": "application/json"}


@dataclass(frozen=True)
class EspnRatingTeam:
    """One school's BPI line."""

    rank: int
    team: str
    conference: str
    record: str
    net_rating: Optional[float]
    """BPI itself: expected point margin per 70 possessions against average."""
    espn_id: Optional[str] = None

    @property
    def summary(self) -> str:
        parts = [f"ESPN BPI #{self.rank}", self.record]
        if self.net_rating is not None:
            parts.append(f"BPI {self.net_rating:+.2f}")
        return " · ".join(p for p in parts if p)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "team": self.team,
            "conference": self.conference,
            "record": self.record,
            "net_rating": self.net_rating,
            "espn_id": self.espn_id,
        }


FALLBACK_NAMES = (BPI_VALUE, BPI_RANK)
"""Positions 0 and 1, used only if the document ships no glossary."""


def _column_names(payload: dict[str, Any]) -> list[str]:
    """The bpi column names, which live once at the top of the document.

    Each team's own category carries `values` but not `names`: reading the
    names off the team is how this returned nothing at all the first time.
    """
    for category in payload.get("categories") or []:
        if category.get("name") == BPI_CATEGORY:
            return [str(n) for n in category.get("names") or []]
    return list(FALLBACK_NAMES)


def _stats(entry: dict[str, Any], names: list[str]) -> dict[str, Any]:
    """One team's bpi values, keyed by the document's column names."""
    for category in entry.get("categories") or []:
        if category.get("name") == BPI_CATEGORY:
            return dict(zip(names, category.get("values") or []))
    return {}


def _record(stats: dict[str, Any]) -> str:
    wins, losses = stats.get(WINS), stats.get(LOSSES)
    if wins is None or losses is None:
        return ""
    return f"{int(wins)}-{int(losses)}"


def parse_power_index(payload: Any) -> list[EspnRatingTeam]:
    """The power-index document into teams, skipping anything unrankable."""
    teams: list[EspnRatingTeam] = []
    if not isinstance(payload, dict):
        return teams
    names = _column_names(payload)
    for entry in payload.get("teams") or []:
        if not isinstance(entry, dict):
            continue
        team = entry.get("team") or {}
        stats = _stats(entry, names)
        rank = stats.get(BPI_RANK)
        name = team.get("shortDisplayName") or team.get("displayName") or team.get("nickname")
        if rank is None or not name:
            continue
        try:
            rank_int = int(rank)
        except (TypeError, ValueError):
            continue
        value = stats.get(BPI_VALUE)
        teams.append(
            EspnRatingTeam(
                rank=rank_int,
                team=str(name),
                conference="",  # not in this payload; the panel does without it
                record=_record(stats),
                net_rating=round(float(value), 2) if isinstance(value, (int, float)) else None,
                espn_id=str(team["id"]) if team.get("id") is not None else None,
            )
        )
    return teams


_cache: dict[date, list[EspnRatingTeam]] = {}


class EspnRatings:
    """BPI ranks by ESPN team id, or by name when there is no id."""

    label = "ESPN BPI"
    short_label = "BPI#"
    source_url = "https://www.espn.com/mens-college-basketball/bpi"

    def __init__(self, client: Optional[httpx.Client] = None, timeout: float = 30.0) -> None:
        self._client = client
        self._timeout = timeout
        self._index: TeamIndex[EspnRatingTeam] = TeamIndex()
        self._by_id: dict[str, EspnRatingTeam] = {}
        self.teams: list[EspnRatingTeam] = []
        self.note: Optional[str] = None
        self.loaded_ok = False

    def load(self) -> None:
        today = datetime.now(EASTERN).date()
        cached = _cache.get(today)
        if cached:
            self._adopt(cached)
            log.info("espn bpi: reusing today's %d ranks", len(cached))
            return

        client = self._client or httpx.Client(
            timeout=self._timeout, follow_redirects=True, headers=_HEADERS
        )
        try:
            response = client.get(URL, params={"region": "us", "lang": "en", "limit": 400})
            response.raise_for_status()
            teams = parse_power_index(response.json())
        except Exception as exc:  # noqa: BLE001 - a dead rating is a column, not a crash
            log.warning("espn bpi: could not read the power index (%s)", exc)
            self.note = f"could not read ESPN's BPI ranking: {exc}"
            return
        finally:
            if self._client is None:
                client.close()

        if not teams:
            self.note = "ESPN's power index returned no teams"
            return
        if len(teams) < MIN_PLAUSIBLE_TEAMS:
            self.note = (
                f"ESPN returned only {len(teams)} of ~365 teams; "
                "ranks may be missing from the board"
            )
        _cache[today] = teams
        self._adopt(teams)
        log.info("espn bpi: %d teams ranked", len(teams))

    def _adopt(self, teams: list[EspnRatingTeam]) -> None:
        self.teams = teams
        self._index = TeamIndex()
        self._by_id = {}
        for team in teams:
            self._index.register([team.team], team)
            if team.espn_id:
                self._by_id[team.espn_id] = team
        self.loaded_ok = bool(teams)

    def rank(self, team: str) -> Optional[EspnRatingTeam]:
        return self._index.lookup(team) if self.loaded_ok else None

    def rank_by_espn_id(self, team_id: Optional[str]) -> Optional[EspnRatingTeam]:
        """Exact, and the reason this source needs no aliases."""
        if not (self.loaded_ok and team_id):
            return None
        return self._by_id.get(str(team_id))


__all__ = ["EspnRatings", "EspnRatingTeam", "parse_power_index", "URL"]
