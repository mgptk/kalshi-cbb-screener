"""Shared data shapes for the scraper layer.

Everything here is plain dataclasses with `to_dict()` so the v1 dashboard can
serialise a whole refresh to JSON without an ORM or a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


# --------------------------------------------------------------------------
# Kalshi
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PriceLevel:
    price: float
    size: float


@dataclass
class OrderBook:
    """Resting depth on one Kalshi market, both sides, best price first."""

    yes: list[PriceLevel] = field(default_factory=list)
    no: list[PriceLevel] = field(default_factory=list)

    @property
    def yes_depth(self) -> float:
        return sum(level.size for level in self.yes)

    @property
    def no_depth(self) -> float:
        return sum(level.size for level in self.no)

    def to_dict(self) -> dict[str, Any]:
        return {
            "yes": [[lvl.price, lvl.size] for lvl in self.yes],
            "no": [[lvl.price, lvl.size] for lvl in self.no],
            "yes_depth": self.yes_depth,
            "no_depth": self.no_depth,
        }


@dataclass
class KalshiContract:
    """One Kalshi binary market: yes = the thing named in `title` happens."""

    ticker: str
    event_ticker: str
    title: str
    yes_label: str
    no_label: str
    status: str
    yes_bid: Optional[float]
    yes_ask: Optional[float]
    no_bid: Optional[float]
    no_ask: Optional[float]
    last_price: Optional[float]
    volume: float = 0.0
    volume_24h: float = 0.0
    open_interest: float = 0.0
    liquidity: float = 0.0
    yes_bid_size: float = 0.0
    yes_ask_size: float = 0.0
    close_time: Optional[datetime] = None
    occurrence: Optional[datetime] = None
    floor_strike: Optional[float] = None
    orderbook: Optional[OrderBook] = None

    @property
    def yes_mid(self) -> Optional[float]:
        """Midpoint of the yes market — the implied probability we trade off.

        Kalshi quotes probability directly, so no devig is needed here; the
        bid/ask spread is the only friction and `spread` exposes it.
        """
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return (self.yes_bid + self.yes_ask) / 2.0

    @property
    def spread(self) -> Optional[float]:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return self.yes_ask - self.yes_bid

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "event_ticker": self.event_ticker,
            "title": self.title,
            "yes_label": self.yes_label,
            "no_label": self.no_label,
            "status": self.status,
            "yes_bid": self.yes_bid,
            "yes_ask": self.yes_ask,
            "yes_mid": self.yes_mid,
            "no_bid": self.no_bid,
            "no_ask": self.no_ask,
            "spread": self.spread,
            "last_price": self.last_price,
            "volume": self.volume,
            "volume_24h": self.volume_24h,
            "open_interest": self.open_interest,
            "liquidity": self.liquidity,
            "yes_bid_size": self.yes_bid_size,
            "yes_ask_size": self.yes_ask_size,
            "close_time": self.close_time.isoformat() if self.close_time else None,
            "occurrence": self.occurrence.isoformat() if self.occurrence else None,
            "floor_strike": self.floor_strike,
            "orderbook": self.orderbook.to_dict() if self.orderbook else None,
        }


@dataclass
class KalshiGame:
    """A game event: two independent per-team contracts, not one yes/no pair."""

    event_ticker: str
    kickoff: Optional[datetime]
    away_team: str
    home_team: str
    away: Optional[KalshiContract] = None
    home: Optional[KalshiContract] = None

    @property
    def mid_sum(self) -> Optional[float]:
        """Both yes mids summed. Meaningfully off 1.0 flags a Kalshi-internal edge."""
        if not self.away or not self.home:
            return None
        a, h = self.away.yes_mid, self.home.yes_mid
        if a is None or h is None:
            return None
        return a + h

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_ticker": self.event_ticker,
            "kickoff": self.kickoff.isoformat() if self.kickoff else None,
            "away_team": self.away_team,
            "home_team": self.home_team,
            "away": self.away.to_dict() if self.away else None,
            "home": self.home.to_dict() if self.home else None,
            "mid_sum": self.mid_sum,
        }


# --------------------------------------------------------------------------
# Sportsbooks
# --------------------------------------------------------------------------


class QuoteStatus(str, Enum):
    """Why a cell has no price matters as much as the price itself.

    The three not-OK cases are deliberately separate. A blank that means "the
    book won't price this side" is information about the market; one that means
    "our scraper broke" is a bug to go fix. They must never look alike.
    """

    OK = "ok"
    """A real two-sided moneyline came back."""

    MONEYLINE_OFF = "moneyline_off"
    """The book lists this game but posts no two-sided moneyline on it.

    Normal for a very large favourite — books pull the moneyline rather than
    hang -50000. This is the book's answer, not a scraping failure.
    """

    NOT_ON_BOARD = "not_on_board"
    """The book was read fine; this game simply isn't on its board."""

    UNAVAILABLE = "unavailable"
    """We could not read the book at all — see `note`. This one is a problem."""


@dataclass
class BookQuote:
    """One book's moneyline on one game, plus its devigged probabilities."""

    book: str
    status: QuoteStatus
    away_american: Optional[float] = None
    home_american: Optional[float] = None
    away_prob: Optional[float] = None
    home_prob: Optional[float] = None
    hold_pct: Optional[float] = None
    source: Optional[str] = None
    note: Optional[str] = None

    @classmethod
    def unavailable(cls, book: str, note: str) -> "BookQuote":
        return cls(book=book, status=QuoteStatus.UNAVAILABLE, note=note)

    @classmethod
    def moneyline_off(
        cls,
        book: str,
        note: str = "no moneyline posted - typically a favourite too large to price",
        away_american: Optional[float] = None,
        home_american: Optional[float] = None,
    ) -> "BookQuote":
        """The book has the game but not a two-sided moneyline.

        Any one side it did post is carried through, so the UI can show that the
        book is on the game even though the pair can't be devigged.
        """
        return cls(
            book=book,
            status=QuoteStatus.MONEYLINE_OFF,
            away_american=away_american,
            home_american=home_american,
            note=note,
        )

    @classmethod
    def not_on_board(cls, book: str, note: str = "game not on this book's board") -> "BookQuote":
        return cls(book=book, status=QuoteStatus.NOT_ON_BOARD, note=note)

    def to_dict(self) -> dict[str, Any]:
        return {
            "book": self.book,
            "status": self.status.value,
            "away_american": self.away_american,
            "home_american": self.home_american,
            "away_prob": self.away_prob,
            "home_prob": self.home_prob,
            "hold_pct": self.hold_pct,
            "source": self.source,
            "note": self.note,
        }


@dataclass
class Matchup:
    """A game as the books see it, used to line book feeds up with Kalshi."""

    away_team: str
    home_team: str
    kickoff: Optional[datetime]
    source_id: Optional[str] = None
    neutral_site: bool = False
