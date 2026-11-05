"""What the board needs from a ratings source, whichever one it is.

Two sources answer the same question - how good is this school, ranked 1 to
365 - and which one a run can use depends on the machine. KenPom's table is
behind Cloudflare and so needs a browser; Barttorvik's T-Rank is a plain JSON
file and works anywhere. The board does not care, as long as both can be asked
for a rank by school name and can say why they have none.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Protocol, runtime_checkable

log = logging.getLogger(__name__)


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


class ChainRatings:
    """Try each source in turn and keep the first that loads.

    Which rank source works depends on where the board is running, and a
    deployment should not have to be told: KenPom needs a browser, Barttorvik
    answers a laptop but 403s a datacenter, ESPN answers anywhere. Falling
    through in order gets the best available rank without configuration.

    A source that failed before one that worked is logged, not surfaced: it is
    not a problem the reader can act on, and the column header already says
    which source the ranks came from.
    """

    def __init__(self, *sources: RatingsSource) -> None:
        if not sources:
            raise ValueError("a chain needs at least one source")
        self.sources = sources
        self._chosen: Optional[RatingsSource] = None

    @property
    def label(self) -> str:
        return self._chosen.label if self._chosen else self.sources[0].label

    @property
    def short_label(self) -> str:
        return self._chosen.short_label if self._chosen else self.sources[0].short_label

    @property
    def note(self) -> Optional[str]:
        if self._chosen is not None:
            return self._chosen.note
        # Nothing worked: say so with the last reason, which is the most
        # specific one the reader has.
        return next(
            (s.note for s in reversed(self.sources) if s.note),
            "no ratings source could be read",
        )

    def load(self) -> None:
        for source in self.sources:
            source.load()
            if getattr(source, "loaded_ok", False):
                self._chosen = source
                return
            log.info("ratings: %s unavailable (%s)", source.label, source.note)

    def rank(self, team: str) -> Optional[RatingTeam]:
        return self._chosen.rank(team) if self._chosen else None

    def rank_by_espn_id(self, team_id: Optional[str]) -> Optional[RatingTeam]:
        """Forwarded when the chosen source can match on ESPN's team id."""
        by_id = getattr(self._chosen, "rank_by_espn_id", None)
        return by_id(team_id) if by_id else None


__all__ = ["ChainRatings", "RatingTeam", "RatingsSource"]
