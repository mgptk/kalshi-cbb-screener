"""ESPN's men's college basketball feed: the schedule and the BPI predictor.

Two differences from the football side drove a separate module rather than a
parameter on the existing one:

* **No weeks.** The football calendar hands back week windows with explicit
  start and end dates. Basketball's calendar is a flat list of dates, because
  the sport runs on a daily schedule — so a slate here is a date range, not a
  week.
* **BPI, not FPI.** The endpoint shape is identical but every stat is renamed:
  `teampredwinpct` rather than `gameprojection`, `teampredmov` rather than
  `teampredptdiff`. Reading football's names off a basketball payload returns
  nothing at all, silently.

BPI also publishes `opponentpredwinpct`, so one team's document describes both
sides of a game and the second request per game is only a cross-check.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import httpx

from core.teams import pair_key

log = logging.getLogger(__name__)

SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
)
CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/mens-college-basketball"

DIVISION_I_GROUP = 50
"""ESPN's group id for D-I men's basketball."""

_HEADERS = {"User-Agent": "ncaab-dashboard/0.1", "Accept": "application/json"}


# --------------------------------------------------------------------------
# Schedule
# --------------------------------------------------------------------------


@dataclass
class EspnGame:
    event_id: str
    short_name: str
    tipoff: Optional[datetime]
    away_team: str
    home_team: str
    away_team_id: Optional[str] = None
    home_team_id: Optional[str] = None
    neutral_site: bool = False

    @property
    def key(self) -> frozenset[str]:
        return pair_key(self.away_team, self.home_team)


def _dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


class EspnClient:
    """Schedule lookups, cached per date so a refresh asks once per day."""

    def __init__(self, timeout: float = 30.0, client: Optional[httpx.Client] = None) -> None:
        self._client = client or httpx.Client(
            timeout=timeout, follow_redirects=True, headers=_HEADERS
        )
        self._by_date: dict[str, list[EspnGame]] = {}

    def close(self) -> None:
        self._client.close()

    def games_on(self, day: date) -> list[EspnGame]:
        stamp = day.strftime("%Y%m%d")
        if stamp in self._by_date:
            return self._by_date[stamp]
        try:
            response = self._client.get(
                SCOREBOARD, params={"groups": DIVISION_I_GROUP, "limit": 500, "dates": stamp}
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("ESPN schedule for %s unavailable: %s", stamp, exc)
            self._by_date[stamp] = []
            return []
        games = [g for g in (_parse_event(e) for e in payload.get("events", [])) if g]
        self._by_date[stamp] = games
        return games

    def games_between(self, first: date, last: date) -> list[EspnGame]:
        """Every D-I game across an inclusive date range, deduplicated."""
        out: dict[str, EspnGame] = {}
        day = first
        while day <= last:
            for game in self.games_on(day):
                out.setdefault(game.event_id, game)
            day += timedelta(days=1)
        return list(out.values())


def _parse_event(event: dict[str, Any]) -> Optional[EspnGame]:
    competitions = event.get("competitions") or []
    if not competitions:
        return None
    competition = competitions[0]
    sides = {c.get("homeAway"): c for c in competition.get("competitors", [])}
    if "home" not in sides or "away" not in sides:
        return None

    def name(side: str) -> str:
        team = sides[side].get("team", {})
        return team.get("location") or team.get("displayName") or team.get("name") or ""

    def team_id(side: str) -> Optional[str]:
        raw = sides[side].get("team", {}).get("id")
        return str(raw) if raw is not None else None

    return EspnGame(
        event_id=str(event.get("id")),
        short_name=event.get("shortName", ""),
        tipoff=_dt(event.get("date")),
        away_team=name("away"),
        home_team=name("home"),
        away_team_id=team_id("away"),
        home_team_id=team_id("home"),
        neutral_site=bool(competition.get("neutralSite")),
    )


def index_by_pair(games: Iterable[EspnGame]) -> dict[frozenset[str], EspnGame]:
    index: dict[frozenset[str], EspnGame] = {}
    for game in games:
        index.setdefault(game.key, game)
    return index


# --------------------------------------------------------------------------
# BPI matchup predictor
# --------------------------------------------------------------------------


@dataclass
class BpiPrediction:
    """ESPN BPI's view of one game, oriented to the caller's away/home."""

    away_win_prob: Optional[float] = None
    home_win_prob: Optional[float] = None
    predicted_margin: Optional[float] = None
    """Predicted margin **from the away team's side**; negative favours home."""
    matchup_quality: Optional[float] = None

    @property
    def available(self) -> bool:
        return self.away_win_prob is not None or self.home_win_prob is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "away_win_prob": self.away_win_prob,
            "home_win_prob": self.home_win_prob,
            "predicted_margin": self.predicted_margin,
            "matchup_quality": self.matchup_quality,
            "source": "espn:bpi-matchup-predictor",
        }


@dataclass
class _TeamBpi:
    win_prob: Optional[float] = None
    opponent_win_prob: Optional[float] = None
    margin: Optional[float] = None
    matchup_quality: Optional[float] = None


class BpiClient:
    """Fetches BPI predictions for a set of ESPN event ids, concurrently."""

    def __init__(self, timeout: float = 20.0, client: Optional[httpx.Client] = None) -> None:
        self._client = client or httpx.Client(
            timeout=timeout, follow_redirects=True, headers=_HEADERS
        )
        self._cache: dict[str, dict[str, _TeamBpi]] = {}

    def close(self) -> None:
        self._client.close()

    def fetch_many(self, event_ids: list[str], workers: int = 6) -> None:
        todo = [e for e in dict.fromkeys(event_ids) if e and e not in self._cache]
        if not todo:
            return
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(self._fetch_event, todo))
        log.info("BPI: predictions for %d of %d games", len(self._cache), len(event_ids))

    def _fetch_event(self, event_id: str) -> None:
        try:
            index = self._get(f"{CORE}/events/{event_id}/competitions/{event_id}/powerindex")
        except httpx.HTTPError:
            return
        by_team: dict[str, _TeamBpi] = {}
        for item in index.get("items", []):
            ref = item.get("$ref", "")
            team_id = ref.rstrip("/").split("?")[0].rsplit("/", 1)[-1]
            try:
                by_team[team_id] = _parse_team(self._get(ref.replace("http://", "https://")))
            except httpx.HTTPError:
                continue
        if by_team:
            self._cache[event_id] = by_team

    def _get(self, url: str) -> dict[str, Any]:
        response = self._client.get(url)
        response.raise_for_status()
        return response.json()

    def prediction(
        self, event_id: Optional[str], away_team_id: Optional[str], home_team_id: Optional[str]
    ) -> BpiPrediction:
        if not event_id:
            return BpiPrediction()
        by_team = self._cache.get(event_id)
        if not by_team:
            return BpiPrediction()

        away = by_team.get(str(away_team_id)) if away_team_id else None
        home = by_team.get(str(home_team_id)) if home_team_id else None
        if away is None and home is None:
            return BpiPrediction()

        # Each team's document also carries its opponent's win probability, so
        # one side alone is enough to fill both columns.
        away_prob = _prob(away.win_prob) if away else _prob(
            home.opponent_win_prob if home else None
        )
        home_prob = _prob(home.win_prob) if home else _prob(
            away.opponent_win_prob if away else None
        )

        if away is not None and away.margin is not None:
            margin = away.margin
        elif home is not None and home.margin is not None:
            margin = -home.margin
        else:
            margin = None

        either = away or home
        return BpiPrediction(
            away_win_prob=away_prob,
            home_win_prob=home_prob,
            predicted_margin=margin,
            matchup_quality=either.matchup_quality if either else None,
        )


def _parse_team(payload: dict[str, Any]) -> _TeamBpi:
    stats = {s.get("name"): s.get("value") for s in payload.get("stats", [])}
    return _TeamBpi(
        win_prob=_num(stats.get("teampredwinpct")),
        opponent_win_prob=_num(stats.get("opponentpredwinpct")),
        margin=_num(stats.get("teampredmov")),
        matchup_quality=_num(stats.get("matchupquality")),
    )


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _prob(percent: Optional[float]) -> Optional[float]:
    """BPI reports percentages (98.97); everything downstream wants 0-1."""
    return None if percent is None else percent / 100.0
