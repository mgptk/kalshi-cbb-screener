"""Common shape for a sportsbook moneyline source.

Every book is a `BookScraper`: it loads once per refresh, then answers
per-matchup lookups. A book that can't be reached returns UNAVAILABLE quotes
rather than raising, so one dead feed degrades a column instead of the page.
"""

from __future__ import annotations

import logging
from typing import Optional, Protocol, runtime_checkable

from ..devig import devig_two_way
from ..models import BookQuote, Matchup, QuoteStatus

log = logging.getLogger(__name__)


@runtime_checkable
class BookScraper(Protocol):
    name: str

    def load(self) -> None:
        """Fetch whatever the book exposes for the current slate. Must not raise."""

    def quote(self, matchup: Matchup) -> BookQuote:
        """This book's moneyline for one matchup. Must not raise."""


def build_quote(
    book: str,
    away_american: Optional[float],
    home_american: Optional[float],
    source: str,
) -> BookQuote:
    """Devig a two-sided American price into a quote, or report why we can't.

    Proportional devig needs both sides, so a one-sided price can't produce a
    probability — but it does prove the book has the game and simply won't hang
    a moneyline on the favourite, which is `MONEYLINE_OFF`, not a failure.
    """
    if away_american is None or home_american is None:
        return BookQuote.moneyline_off(
            book,
            away_american=away_american,
            home_american=home_american,
        )
    try:
        devigged = devig_two_way(away_american, home_american)
    except ValueError as exc:
        log.warning("%s: unusable price %s/%s (%s)", book, away_american, home_american, exc)
        return BookQuote.unavailable(book, note=f"unusable price from {source}: {exc}")
    return BookQuote(
        book=book,
        status=QuoteStatus.OK,
        away_american=away_american,
        home_american=home_american,
        away_prob=devigged.prob_a,
        home_prob=devigged.prob_b,
        hold_pct=devigged.hold_pct,
        source=source,
    )


class UnavailableScraper:
    """Stands in for a book whose feed we can't reach from this host.

    Keeps the column in the UI (as a dash) and records the reason, instead of
    the book vanishing and the composite silently changing its basis.
    """

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason

    def load(self) -> None:
        return None

    def quote(self, matchup: Matchup) -> BookQuote:
        return BookQuote.unavailable(self.name, self.reason)
