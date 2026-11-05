"""Odds conversion and vig removal.

v1 uses basic proportional (multiplicative) devig per the spec: convert both
sides to raw implied probability, sum to get the overround, divide each side
by that sum. Shin's method can slot in behind the same interface later.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence


def american_to_prob(odds: float) -> float:
    """Raw (vig-inclusive) implied probability of an American moneyline."""
    if odds == 0:
        raise ValueError("American odds of 0 are not a price")
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return -odds / (-odds + 100.0)


def prob_to_american(prob: float) -> int:
    """Inverse of american_to_prob, rounded to the nearest whole number."""
    if not 0.0 < prob < 1.0:
        raise ValueError(f"probability out of range: {prob}")
    if prob >= 0.5:
        return -round(100.0 * prob / (1.0 - prob))
    return round(100.0 * (1.0 - prob) / prob)


@dataclass(frozen=True)
class TwoWayDevig:
    """Both sides of a two-way market, with and without vig."""

    raw_a: float
    raw_b: float
    prob_a: float
    prob_b: float

    @property
    def overround(self) -> float:
        """Sum of the raw implied probabilities. 1.05 == a 5% hold."""
        return self.raw_a + self.raw_b

    @property
    def hold_pct(self) -> float:
        return (self.overround - 1.0) * 100.0


def devig_two_way(american_a: float, american_b: float) -> TwoWayDevig:
    """Proportional devig of a two-sided moneyline.

    `american_a` and `american_b` are the two sides of the same market, so the
    result's prob_a + prob_b is 1 by construction.
    """
    raw_a = american_to_prob(american_a)
    raw_b = american_to_prob(american_b)
    total = raw_a + raw_b
    if total <= 0:
        raise ValueError("degenerate market: implied probabilities sum to zero")
    return TwoWayDevig(raw_a=raw_a, raw_b=raw_b, prob_a=raw_a / total, prob_b=raw_b / total)


def composite(probs: Iterable[Optional[float]]) -> Optional[float]:
    """Equal-weighted mean of the books that carry the game.

    Books with no line contribute `None` and are dropped rather than counted as
    zero, per the spec. Returns None when no book has a price.
    """
    present: Sequence[float] = [p for p in probs if p is not None]
    if not present:
        return None
    return sum(present) / len(present)
