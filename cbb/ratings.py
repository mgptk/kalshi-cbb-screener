"""What the board needs from a ratings source, whichever one it is.

Two sources answer the same question - how good is this school, ranked 1 to
365 - and which one a run can use depends on the machine. KenPom's table is
behind Cloudflare and so needs a browser; Barttorvik's T-Rank is a plain JSON
file and works anywhere. The board does not care, as long as both can be asked
for a rank by school name and can say why they have none.
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
    """How to name it to a reader: "KenPom", "T-Rank"."""

    short_label: str
    """The same, short enough for a column header: "KP", "TRk"."""

    note: Optional[str]
    """Why the ranks are missing, when they are."""

    def load(self) -> None:
        ...

    def rank(self, team: str) -> Optional[RatingTeam]:
        ...


__all__ = ["RatingTeam", "RatingsSource"]
