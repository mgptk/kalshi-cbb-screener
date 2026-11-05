"""One refresh: the college-basketball moneyline board, fetched live.

Where the numbers come from depends on what the machine can do, and this is
the one place that decides it. Two of the four feeds are plain HTTP and work
anywhere - Kalshi's prices, ESPN's schedule and BPI. The other two are not:

* **The books.** DraftKings and BetMGM render their boards client-side and
  refuse plain clients outright, so reading them needs a browser. Given an
  Odds API key instead, the same two books' prices arrive over ordinary HTTPS
  and no browser is involved.
* **The ranks.** KenPom needs a browser for the same reason (Cloudflare);
  Barttorvik's T-Rank is a plain JSON file. `ratings="auto"` picks whichever
  the host can actually reach.

So a laptop runs browser scrapes and KenPom; a hosted container runs the Odds
API and T-Rank; both produce the same board.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from core.kalshi import KalshiClient
from core.scrapers.base import BookScraper

from .books import default_books
from .espn import BpiClient, EspnClient
from .kenpom import KenPomClient
from .oddsapi import odds_api_books
from .ratings import RatingsSource
from .sections import DEFAULT_DAYS, MoneylineSection, build_moneylines
from .torvik import TorvikClient

log = logging.getLogger(__name__)

RATINGS_CHOICES = ("auto", "kenpom", "torvik", "none")


@dataclass
class Refresh:
    moneylines: MoneylineSection
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "moneylines": self.moneylines.to_dict(),
        }


def choose_ratings(which: str, use_browser: bool) -> Optional[RatingsSource]:
    """The ratings source to use, given what this host can reach.

    "auto" is the useful one: KenPom where a browser can clear its challenge,
    T-Rank everywhere else. They measure the same thing and matched every
    school on a 102-game slate, so the board reads the same either way; only
    the numbers differ, and the column says which it is showing.
    """
    if which == "none":
        return None
    if which == "kenpom":
        return KenPomClient(use_browser=use_browser)
    if which == "torvik":
        return TorvikClient()
    if which != "auto":
        raise ValueError(f"unknown ratings source {which!r}; pick one of {RATINGS_CHOICES}")
    return KenPomClient(use_browser=True) if use_browser else TorvikClient()


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


def refresh_all(
    books: Optional[Sequence[BookScraper]] = None,
    days: int = DEFAULT_DAYS,
    with_orderbooks: bool = False,
    depth: int = 10,
    use_browser: bool = True,
    headless: bool = True,
    with_bpi: bool = True,
    ratings: str = "auto",
    odds_api_key: Optional[str] = None,
) -> Refresh:
    espn = EspnClient()
    kalshi = KalshiClient()
    bpi = BpiClient() if with_bpi else None
    rating_source = choose_ratings(ratings, use_browser)
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
        return Refresh(moneylines=moneylines)
    finally:
        kalshi.close()
        espn.close()
        if bpi is not None:
            bpi.close()
