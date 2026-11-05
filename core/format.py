"""Turns numbers into the two representations the page toggles between.

Every price cell carries both a devigged percentage and an American equivalent,
so the toggle is pure client-side JS and never re-fetches. A cell that has no
price carries neither and renders as a dash.

Times render in the zone the caller names - the reader's browser zone, on the
Streamlit page - and fall back to this machine's own zone when none is given.
A hosted server runs in UTC, so the fallback is only right on a laptop.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Optional
from zoneinfo import ZoneInfo

from .devig import prob_to_american

EASTERN = ZoneInfo("America/New_York")
"""Kept for the slate-day arithmetic, which is Eastern by definition.

Display is a separate question: tip-offs are shown in the reader's zone (see
`kickoff`). The bucketing in `cbb/sections.py` stays Eastern regardless,
because that is how Kalshi tickers and ESPN's `dates=` parameter file a game.
"""

# Below/above this, American odds stop being meaningful (a 99.9% shot prices at
# -99900) and the page shows the percentage instead of a wall of digits.
_AMERICAN_FLOOR = 0.005
_AMERICAN_CEILING = 0.995


@dataclass(frozen=True)
class Cell:
    """One price, in both representations. `pct` is None when there is no price."""

    pct: Optional[str] = None
    american: Optional[str] = None
    title: Optional[str] = None

    @property
    def empty(self) -> bool:
        return self.pct is None

    @property
    def default(self) -> str:
        return self.pct or "--"


def price_cell(prob: Optional[float], title: Optional[str] = None) -> Cell:
    """A probability rendered as both a percentage and American odds."""
    if prob is None:
        return Cell(title=title)
    pct = f"{prob * 100:.1f}%"
    american: Optional[str] = None
    if _AMERICAN_FLOOR < prob < _AMERICAN_CEILING:
        value = prob_to_american(prob)
        american = f"{value:+d}"
    else:
        # Past the rails the percentage is the honest reading of the price.
        american = pct
    return Cell(pct=pct, american=american, title=title)


def american_cell(odds: Optional[float], prob: Optional[float], title: Optional[str] = None) -> Cell:
    """A book's quote: its real posted American price, plus the devigged percent.

    Unlike `price_cell` this shows what the book actually hangs on the board
    rather than a round-trip through the probability, so -110 stays -110.
    """
    if prob is None and odds is None:
        return Cell(title=title)
    pct = f"{prob * 100:.1f}%" if prob is not None else None
    american = f"{odds:+.0f}" if odds is not None else None
    return Cell(pct=pct, american=american or pct, title=title)


def signed_pct(value: Optional[float]) -> Optional[str]:
    return None if value is None else f"{value * 100:+.1f}"


def zone_label(when: datetime) -> str:
    """The short name of a datetime's zone: "EST", "CDT", "AEST".

    Windows spells the zone out in full - `tzname()` there returns "Eastern
    Standard Time", not "EST" - so a multi-word name is reduced to its
    initials. Platforms that already return an abbreviation keep it as is.
    """
    name = when.tzname() or ""
    words = name.split()
    if len(words) > 1:
        initials = "".join(w[0] for w in words if w[:1].isupper())
        if initials:
            return initials
    return name


def kickoff(when: Optional[datetime], zone: Optional[tzinfo] = None) -> str:
    """e.g. "Sat Sep 5, 3:30 PM EST", in `zone`, or this machine's own zone.

    Built by hand: strftime's no-pad flag is %-d on Linux and %#d on Windows,
    and this has to run on both.
    """
    if when is None:
        return "TBD"
    local = when.astimezone(zone)
    hour = local.hour % 12 or 12
    meridiem = "AM" if local.hour < 12 else "PM"
    label = zone_label(local)
    return f"{local:%a %b} {local.day}, {hour}:{local:%M} {meridiem} {label}".rstrip()


def timestamp(when: datetime, zone: Optional[tzinfo] = None) -> str:
    local = when.astimezone(zone)
    return f"{local:%b %d, %I:%M:%S %p} {zone_label(local)}".rstrip()
