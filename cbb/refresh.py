"""One refresh: the college-basketball moneyline board, fetched live.

Three of the four feeds are plain HTTP and work anywhere - Kalshi's prices,
ESPN's schedule and BPI, and ESPN's BPI ranking. The books are the exception:
DraftKings and BetMGM render their boards client-side and refuse plain
clients, so reading them directly needs a browser. Given an Odds API key
instead, the books arrive over ordinary HTTPS and no browser is involved.

The books are also the one feed that costs something - an Odds API credit per
fetch - so they can be loaded on their own (`load_books`) and handed back to
`refresh_all` as already loaded. That is what lets the page refresh Kalshi
and ESPN without spending a credit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from core.kalshi import KalshiClient
from core.models import BookQuote, Matchup
from core.scrapers.base import BookScraper

from .books import default_books
from .espn import BpiClient, EspnClient
from .espnrank import EspnRatings
from .oddsapi import odds_api_books
from .sections import DEFAULT_DAYS, MoneylineSection, build_moneylines

log = logging.getLogger(__name__)


@dataclass
class Refresh:
    moneylines: MoneylineSection
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    odds_credits: Optional[int] = None
    """Odds API credits left this month, when that is where the books came
    from. The free tier is 500 and a refresh spends one, so it is worth
    seeing before it runs out rather than after."""
    books_fetched_at: Optional[datetime] = None
    """When the book prices were fetched, which is earlier than
    `generated_at` after a Kalshi-only refresh reused them."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "books_fetched_at": (
                self.books_fetched_at.isoformat() if self.books_fetched_at else None
            ),
            "odds_credits_remaining": self.odds_credits,
            "moneylines": self.moneylines.to_dict(),
        }


def choose_books(
    books: Optional[Sequence[BookScraper]],
    odds_api_key: Optional[str],
    use_browser: bool,
    headless: bool,
) -> list[BookScraper]:
    """Scrape the boards, or read the same books from The Odds API."""
    if books is not None:
        return list(books)
    if odds_api_key:
        return list(odds_api_books(odds_api_key))
    return list(default_books(use_browser, headless))


class LoadedBook:
    """A book that has already fetched, standing in for itself on a later refresh.

    `load()` is a no-op, so building the board again re-reads the prices it
    already has instead of fetching (and, from The Odds API, paying) again.
    Everything else - `name`, `note`, `quote`, `credits_remaining` - is the
    wrapped book's own.
    """

    def __init__(self, book: BookScraper) -> None:
        self._book = book

    def __getattr__(self, attr: str) -> Any:
        if attr == "_book":  # not set yet, as during unpickling: no recursion
            raise AttributeError(attr)
        return getattr(self._book, attr)

    def load(self) -> None:
        return None

    def quote(self, matchup: Matchup) -> BookQuote:
        return self._book.quote(matchup)


def load_books(
    odds_api_key: Optional[str] = None,
    use_browser: bool = True,
    headless: bool = True,
) -> list[LoadedBook]:
    """Fetch every book once, for `refresh_all(books=...)` to reuse."""
    books = choose_books(None, odds_api_key, use_browser, headless)
    for book in books:
        book.load()
    return [LoadedBook(b) for b in books]


def refresh_all(
    books: Optional[Sequence[BookScraper]] = None,
    days: int = DEFAULT_DAYS,
    with_orderbooks: bool = False,
    depth: int = 10,
    use_browser: bool = True,
    headless: bool = True,
    with_bpi: bool = True,
    with_ranks: bool = True,
    odds_api_key: Optional[str] = None,
    books_fetched_at: Optional[datetime] = None,
) -> Refresh:
    """Fetch and build the board.

    `books` already loaded (from `load_books`) are reused as they are; pass
    `books_fetched_at` with them so the page can say how old their prices are.
    """
    espn = EspnClient()
    kalshi = KalshiClient()
    bpi = BpiClient() if with_bpi else None
    rating_source = EspnRatings() if with_ranks else None
    try:
        scrapers = choose_books(books, odds_api_key, use_browser, headless)
        log.info(
            "sources: books=%s ratings=%s",
            ", ".join(b.name for b in scrapers) or "none",
            getattr(rating_source, "label", "none"),
        )
        moneylines = build_moneylines(
            kalshi,
            espn,
            scrapers,
            days=days,
            with_orderbooks=with_orderbooks,
            depth=depth,
            bpi=bpi,
            ratings=rating_source,
        )
        credits = next(
            (
                left
                for left in (getattr(b, "credits_remaining", None) for b in scrapers)
                if left is not None
            ),
            None,
        )
        return Refresh(
            moneylines=moneylines,
            odds_credits=credits,
            books_fetched_at=books_fetched_at if books is not None else None,
        )
    finally:
        kalshi.close()
        espn.close()
        if bpi is not None:
            bpi.close()
