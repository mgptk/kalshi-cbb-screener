"""Builds the one section this dashboard has: college-basketball moneylines.

Kalshi lists `KXNCAAMBGAME` days ahead — a few hundred games at a time — while
the books only hang lines on roughly the next couple of days. Showing Kalshi's
whole list would bury the comparable games under a hundred rows with nothing to
compare against, so the slate is a date window anchored on Kalshi's own earliest
open tip-off, `days` wide. Everything past the window is counted, not silently
dropped.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Optional, Sequence

from core.devig import composite
from core.kalshi import KalshiClient, is_active, parse_contract
from core.models import BookQuote, KalshiContract, KalshiGame, Matchup, QuoteStatus
from core.scrapers.base import BookScraper
from core.teams import normalize, pair_key

from .espn import BpiClient, BpiPrediction, EspnClient, EspnGame, index_by_pair
from .ratings import RatingsSource, RatingTeam

log = logging.getLogger(__name__)

GAME_SERIES = "KXNCAAMBGAME"
"""Men's college basketball. Note `KXNCAABBGAME` is college *baseball* and
`KXNCAABGAME` is a dormant generic series with no open markets."""

# "If Penn St. wins the Chattanooga vs Penn St. men's college basketball game
#  originally scheduled for Nov 5, 2026, then the market resolves to Yes."
_GAME_RULE = re.compile(
    r"wins the (.+?) vs\.? (.+?) men's college basketball game", re.IGNORECASE
)

EASTERN = ZoneInfo("America/New_York")
"""Slate days are Eastern days - the day a game is filed under, not the clock
the page shows, which is local.

A 9pm Eastern tip-off is the *next* UTC date, and both Kalshi's event ticker
(`...26NOV05...`) and ESPN's `dates=` parameter file it under the Eastern day
it was played. Bucketing by UTC date instead splits a single evening across two
buckets and asks ESPN for the wrong one, which costs those games their ESPN
match and so their BPI — silently, since a game with no match still renders.
"""

DEFAULT_DAYS = 1
"""Today's games only. The window is widened from the picker on the page, which
re-slices the same fetch rather than re-scraping the books."""

KALSHI_OCCURRENCE_LEAD = timedelta(hours=3)
"""Kalshi's `occurrence_datetime` is not the tip-off; it runs three hours late.

Measured, not assumed: across a full 16-game slate every Kalshi occurrence sat
exactly three hours after ESPN's scheduled start, which is what you would
expect of a time that marks when the market can settle rather than when the
ball goes up. Taken at face value it showed every game three hours late and,
worse, filed a 10pm Eastern tip-off under the following Eastern day, which then
asked ESPN for the wrong date and cost that game its match.

So Kalshi's time is only ever an estimate of the tip-off, used to pick the
window and to fill in games ESPN does not have. Wherever a game matched ESPN,
`_attach_espn_tipoffs` replaces it with ESPN's own scheduled start.
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slate_day(when: datetime) -> date:
    """The Eastern calendar day a tip-off belongs to."""
    return when.astimezone(EASTERN).date()


def _diff(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return None if a is None or b is None else a - b


@dataclass
class MoneylineRow:
    game: KalshiGame
    books: dict[str, BookQuote] = field(default_factory=dict)
    composite_away: Optional[float] = None
    composite_home: Optional[float] = None
    espn_event_id: Optional[str] = None
    neutral_site: bool = False
    matched_to_book_slate: bool = False
    bpi: BpiPrediction = field(default_factory=BpiPrediction)
    rating_away: Optional[RatingTeam] = None
    rating_home: Optional[RatingTeam] = None
    """Each school's line in whichever ratings source was used."""
    tipoff_from_espn: bool = False
    """False means the time is Kalshi's occurrence less the usual lead, so it
    is an estimate rather than a published start."""

    @property
    def edge_away(self) -> Optional[float]:
        """The book composite minus Kalshi's ask. Positive == Kalshi is cheap.

        Measured against the ask because that is what it costs to buy Yes; an
        edge against the midpoint is an edge against a price nobody is showing.
        A positive number is the discount you are buying at, so it is the side
        worth taking.
        """
        return _diff(self.composite_away, self.game.away.yes_ask if self.game.away else None)

    @property
    def edge_home(self) -> Optional[float]:
        return _diff(self.composite_home, self.game.home.yes_ask if self.game.home else None)

    @property
    def bpi_edge_away(self) -> Optional[float]:
        return _diff(
            self.bpi.away_win_prob, self.game.away.yes_ask if self.game.away else None
        )

    @property
    def bpi_edge_home(self) -> Optional[float]:
        return _diff(
            self.bpi.home_win_prob, self.game.home.yes_ask if self.game.home else None
        )

    @property
    def moneyline_off_everywhere(self) -> bool:
        """Nothing priced, and at least one book said so deliberately."""
        quotes = list(self.books.values())
        if not quotes or any(q.status is QuoteStatus.OK for q in quotes):
            return False
        if any(q.status is QuoteStatus.UNAVAILABLE for q in quotes):
            return False
        return any(q.status is QuoteStatus.MONEYLINE_OFF for q in quotes)

    @property
    def books_unreadable(self) -> list[str]:
        return [n for n, q in self.books.items() if q.status is QuoteStatus.UNAVAILABLE]

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.game.to_dict(),
            "espn_event_id": self.espn_event_id,
            "neutral_site": self.neutral_site,
            "tipoff_from_espn": self.tipoff_from_espn,
            "matched_to_book_slate": self.matched_to_book_slate,
            "books": {name: quote.to_dict() for name, quote in self.books.items()},
            "composite_away": self.composite_away,
            "composite_home": self.composite_home,
            "edge_away": self.edge_away,
            "edge_home": self.edge_home,
            "bpi": self.bpi.to_dict(),
            "rating_away": self.rating_away.to_dict() if self.rating_away else None,
            "rating_home": self.rating_home.to_dict() if self.rating_home else None,
            "bpi_edge_away": self.bpi_edge_away,
            "bpi_edge_home": self.bpi_edge_home,
            "moneyline_off_everywhere": self.moneyline_off_everywhere,
            "books_unreadable": self.books_unreadable,
        }


@dataclass
class MoneylineSection:
    rows: list[MoneylineRow]
    book_notes: dict[str, Optional[str]]
    first_day: Optional[date] = None
    last_day: Optional[date] = None
    beyond_window: int = 0
    """Kalshi games further out than the window — listed, just not shown."""
    rating_note: Optional[str] = None
    """Why the ranks are missing, when they are."""
    rating_label: str = "ESPN BPI"
    """Which ratings source the ranks came from, for the page to name."""
    rating_short_label: str = "BPI#"
    """The same, short enough for a column header."""
    generated_at: datetime = field(default_factory=_now)

    @property
    def window_label(self) -> str:
        if not self.first_day:
            return "no games"
        if self.last_day and self.last_day != self.first_day:
            return f"{self.first_day:%a %b %d} - {self.last_day:%a %b %d}"
        return f"{self.first_day:%a %b %d}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "section": "moneylines",
            "sport": "mens-college-basketball",
            "generated_at": self.generated_at.isoformat(),
            "window": {
                "first_day": self.first_day.isoformat() if self.first_day else None,
                "last_day": self.last_day.isoformat() if self.last_day else None,
                "label": self.window_label,
            },
            "book_notes": self.book_notes,
            "rating_note": self.rating_note,
            "rating_source": self.rating_label,
            "beyond_window": self.beyond_window,
            "counts": {
                "games": len(self.rows),
                "matched_to_book_slate": sum(1 for r in self.rows if r.matched_to_book_slate),
                "with_composite": sum(1 for r in self.rows if r.composite_home is not None),
                "with_bpi": sum(1 for r in self.rows if r.bpi.available),
                "with_ratings": sum(
                    1 for r in self.rows if r.rating_away and r.rating_home
                ),
                "moneyline_off_everywhere": sum(
                    1 for r in self.rows if r.moneyline_off_everywhere
                ),
                "rows_with_an_unreadable_book": sum(1 for r in self.rows if r.books_unreadable),
            },
            "rows": [row.to_dict() for row in self.rows],
        }


def build_moneylines(
    kalshi: KalshiClient,
    espn: EspnClient,
    books: Sequence[BookScraper],
    days: int = DEFAULT_DAYS,
    with_orderbooks: bool = False,
    depth: int = 10,
    bpi: Optional[BpiClient] = None,
    ratings: Optional[RatingsSource] = None,
) -> MoneylineSection:
    """Every Kalshi basketball game in the window, paired with each book's line."""
    listed = _kalshi_games(kalshi)
    tipoffs = [g.kickoff for g in listed if g.kickoff]
    if not tipoffs:
        return MoneylineSection(rows=[], book_notes={b.name: getattr(b, "note", None) for b in books})

    first = _slate_day(min(tipoffs))
    last = first + timedelta(days=max(days - 1, 0))
    in_window = [
        g for g in listed if g.kickoff and first <= _slate_day(g.kickoff) <= last
    ]
    beyond = len(listed) - len(in_window)

    by_pair = index_by_pair(espn.games_between(first, last))

    for book in books:
        book.load()

    rows: list[MoneylineRow] = []
    for game in in_window:
        espn_game = by_pair.get(pair_key(game.away_team, game.home_team))
        if espn_game is not None and normalize(espn_game.away_team) == normalize(game.home_team):
            # ESPN knows who is actually at home; Kalshi's rules text only
            # implies it from word order.
            game.away_team, game.home_team = game.home_team, game.away_team
            game.away, game.home = game.home, game.away

        row = MoneylineRow(
            game=game,
            espn_event_id=espn_game.event_id if espn_game else None,
            neutral_site=espn_game.neutral_site if espn_game else False,
            matched_to_book_slate=espn_game is not None,
        )
        matchup = Matchup(
            away_team=game.away_team,
            home_team=game.home_team,
            kickoff=game.kickoff,
            source_id=row.espn_event_id,
            neutral_site=row.neutral_site,
        )
        for book in books:
            row.books[book.name] = book.quote(matchup)

        priced = [q for q in row.books.values() if q.status is QuoteStatus.OK]
        row.composite_away = composite(q.away_prob for q in priced)
        row.composite_home = composite(q.home_prob for q in priced)
        rows.append(row)

    _attach_espn_tipoffs(rows, by_pair)
    rows.sort(key=lambda r: (r.game.kickoff or datetime.max.replace(tzinfo=timezone.utc)))
    _attach_bpi(rows, by_pair, bpi)
    _attach_ratings(rows, ratings, by_pair)

    if with_orderbooks:
        kalshi.attach_orderbooks(
            [c for row in rows for c in (row.game.away, row.game.home) if c], depth=depth
        )
    if beyond:
        log.info("%d Kalshi games tip off beyond the %d-day window", beyond, days)

    return MoneylineSection(
        rows=rows,
        book_notes={book.name: getattr(book, "note", None) for book in books},
        first_day=first,
        last_day=last,
        beyond_window=beyond,
        rating_note=ratings.note if ratings is not None else None,
        rating_label=ratings.label if ratings is not None else "none",
        rating_short_label=ratings.short_label if ratings is not None else "Rk",
    )


def _attach_espn_tipoffs(
    rows: list[MoneylineRow], by_pair: dict[frozenset[str], EspnGame]
) -> None:
    """Take the scheduled start from ESPN, which is the schedule of record.

    Only a game ESPN does not carry keeps Kalshi's estimate, and the row says
    which it is so the page can mark an approximate time rather than present it
    as exact.
    """
    for row in rows:
        espn_game = by_pair.get(pair_key(row.game.away_team, row.game.home_team))
        if espn_game is not None and espn_game.tipoff is not None:
            row.game.kickoff = espn_game.tipoff
            row.tipoff_from_espn = True


def _attach_ratings(
    rows: list[MoneylineRow],
    ratings: Optional[RatingsSource],
    by_pair: dict[frozenset[str], EspnGame],
) -> None:
    """Hang each school's rank on the row.

    By ESPN's team id where the source knows one - which is exact, and skips
    the whole question of how a feed spells a school - and by name otherwise.
    Ranks are per team rather than per game, so an unmatched school costs one
    rank and not the pair; the opponent still shows its own.
    """
    if ratings is None:
        return
    ratings.load()
    for row in rows:
        espn_game = by_pair.get(pair_key(row.game.away_team, row.game.home_team))
        away_id = home_id = None
        if espn_game is not None:
            away_id, home_id = espn_game.away_team_id, espn_game.home_team_id
            if normalize(espn_game.away_team) != normalize(row.game.away_team):
                away_id, home_id = home_id, away_id
        row.rating_away = _rating_for(ratings, row.game.away_team, away_id)
        row.rating_home = _rating_for(ratings, row.game.home_team, home_id)
    matched = sum(1 for r in rows if r.rating_away) + sum(1 for r in rows if r.rating_home)
    log.info("%s: ranked %d of %d teams on the slate", ratings.label, matched, len(rows) * 2)


def _rating_for(
    ratings: RatingsSource, team: str, espn_id: Optional[str]
) -> Optional[RatingTeam]:
    by_id = getattr(ratings, "rank_by_espn_id", None)
    if by_id is not None:
        hit = by_id(espn_id)
        if hit is not None:
            return hit
    return ratings.rank(team)


def _attach_bpi(
    rows: list[MoneylineRow],
    by_pair: dict[frozenset[str], EspnGame],
    bpi: Optional[BpiClient],
) -> None:
    if bpi is None:
        return
    bpi.fetch_many([r.espn_event_id for r in rows if r.espn_event_id])
    for row in rows:
        espn_game = by_pair.get(pair_key(row.game.away_team, row.game.home_team))
        if espn_game is None or not row.espn_event_id:
            continue
        prediction = bpi.prediction(
            row.espn_event_id, espn_game.away_team_id, espn_game.home_team_id
        )
        if normalize(espn_game.away_team) != normalize(row.game.away_team):
            prediction = BpiPrediction(
                away_win_prob=prediction.home_win_prob,
                home_win_prob=prediction.away_win_prob,
                predicted_margin=(
                    -prediction.predicted_margin
                    if prediction.predicted_margin is not None
                    else None
                ),
                matchup_quality=prediction.matchup_quality,
            )
        row.bpi = prediction


def _kalshi_games(kalshi: KalshiClient) -> list[KalshiGame]:
    """Group the per-team contracts of `KXNCAAMBGAME` into games."""
    raw = [m for m in kalshi.list_markets(GAME_SERIES) if is_active(m)]
    by_event: dict[str, list[dict[str, Any]]] = {}
    for market in raw:
        by_event.setdefault(market.get("event_ticker", ""), []).append(market)

    games: list[KalshiGame] = []
    for event_ticker, markets in by_event.items():
        contracts = [parse_contract(m) for m in markets]
        occurrence = next((c.occurrence for c in contracts if c.occurrence), None)
        # An estimate until ESPN confirms it; the contracts keep the raw value.
        tipoff = occurrence - KALSHI_OCCURRENCE_LEAD if occurrence else None
        away_name, home_name = _participants(markets, contracts)
        games.append(
            KalshiGame(
                event_ticker=event_ticker,
                kickoff=tipoff,
                away_team=away_name,
                home_team=home_name,
                away=_contract_for(contracts, away_name),
                home=_contract_for(contracts, home_name),
            )
        )
    return games


def _participants(
    markets: Sequence[dict[str, Any]], contracts: Sequence[KalshiContract]
) -> tuple[str, str]:
    """Kalshi's rules text names the game "Away vs Home"; labels are the fallback."""
    for market in markets:
        match = _GAME_RULE.search(market.get("rules_primary", "") or "")
        if match:
            return match.group(1).strip(), match.group(2).strip()
    labels = [c.yes_label for c in contracts if c.yes_label]
    if len(labels) >= 2:
        return labels[0], labels[1]
    return (labels[0] if labels else ""), ""


def _contract_for(
    contracts: Sequence[KalshiContract], team: str
) -> Optional[KalshiContract]:
    if not team:
        return None
    target = normalize(team)
    return next((c for c in contracts if normalize(c.yes_label) == target), None)


# `KalshiGame.kickoff` is the neutral field name the shared model uses; in this
# sport it is a tip-off. Kept as-is so the shared dataclass stays shared.
