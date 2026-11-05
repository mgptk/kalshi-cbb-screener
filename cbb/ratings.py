"""What the board needs from a ratings source.

There is one source now - ESPN's BPI ranking (`cbb/espnrank.py`), which
answers from anywhere and matches schools by ESPN team id. KenPom (behind
Cloudflare, so browser-only) and Barttorvik's T-Rank (403 to a datacenter
address) used to sit ahead of it in a fallback chain; they were dropped so the
board shows the same ranks wherever it runs. The protocol stays so the board
and the detail panel do not depend on which source it is.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class RatingTeam(Protocol):
    """One school's line in a ratings table."""

    rank: int
    team: str
    conference: str
    record: str
    net_rating: Optional[float]

    @property
    def summary(self) -> str:
        """One line naming the source, for the detail panel."""

    def to_dict(self) -> dict[str, Any]:
        ...


@runtime_checkable
class RatingsSource(Protocol):
    """A ratings table, loaded once per refresh. `load()` must not raise."""

    label: str
    """How to name it to a reader: "ESPN BPI"."""

    short_label: str
    """The same, short enough for a column header: "BPI#"."""

    note: Optional[str]
    """Why the ranks are missing, when they are."""

    def load(self) -> None:
        ...

    def rank(self, team: str) -> Optional[RatingTeam]:
        ...


__all__ = ["RatingTeam", "RatingsSource"]
