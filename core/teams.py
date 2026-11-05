"""Team-name normalisation.

Kalshi, ESPN and the sportsbooks all spell schools differently ("Appalachian
St." / "App State", "Louisiana-Monroe" / "UL Monroe"). Everything that has to
line two feeds up goes through `normalize()` first, then the alias table, then
a conservative fuzzy fallback so an unlisted variant still lands rather than
silently dropping a game.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Generic, Iterable, Optional, TypeVar

_PUNCT = re.compile(r"[^a-z0-9 ]")
_SPACES = re.compile(r"\s+")
_ST_ABBREV = re.compile(r"\bst\.?\b")
_NOISE = re.compile(r"\b(university|univ|college|the)\b")

# Mascots Kalshi appends to some non-FBS schools ("Tusculum Pioneers") that no
# other feed carries. Stripping them costs nothing and rescues a few matches.
_TRAILING_MASCOTS = (
    "argonauts", "cougars", "dragons", "golden bears", "marauders", "pioneers",
    "rams", "thorobreds", "tigers", "vikings", "warriors", "wildcats",
    "yellow jackets", "owls",
)

ALIASES: dict[str, str] = {
    # left: a normalized spelling seen in the wild -> right: canonical form.
    # Confirmed divergences between Kalshi's and ESPN's 2026 CFB feeds.
    "app state": "appalachian state",
    "nicholls": "nicholls state",
    "se louisiana": "southeastern louisiana",
    "ul monroe": "louisiana monroe",
    "louisiana lafayette": "louisiana",
    "ul lafayette": "louisiana",
    "miami fl": "miami",
    "miami florida": "miami",
    "miami oh": "miami ohio",
    "ole miss": "mississippi",
    "pitt": "pittsburgh",
    "penn": "pennsylvania",
    "uconn": "connecticut",
    "umass": "massachusetts",
    "usc": "southern california",
    "smu": "southern methodist",
    "tcu": "texas christian",
    "lsu": "louisiana state",
    "byu": "brigham young",
    "utep": "texas el paso",
    "utsa": "texas san antonio",
    "unlv": "nevada las vegas",
    "ucf": "central florida",
    "fiu": "florida international",
    "fau": "florida atlantic",
    "hawaii": "hawaii",
    "hawaii rainbow warriors": "hawaii",
    "n c state": "north carolina state",
    "nc state": "north carolina state",
    "sam houston": "sam houston state",
    "texas am": "texas aandm",
    "tennessee martin": "ut martin",
    "university at albany": "albany",
    "liu": "long island",
    "st thomas": "saint thomas",
    "st francis": "saint francis",
    # Found against men's basketball, but these are school-name facts rather
    # than sport ones. Each is a case where one feed keeps "State"/"St." and
    # the other drops it — note there is no general rule to be had here, since
    # Illinois and Illinois State are genuinely different schools.
    "grambling state": "grambling",
    "central connecticut state": "central connecticut",
    "new orleans": "lsu new orleans",
    "seattle": "seattle u",
    "new college of florida": "new college",
    "fdu": "fairleigh dickinson",
    # KenPom's own abbreviations, which no other feed here uses.
    "csun": "cal state northridge",
    "cal baptist": "california baptist",
}


def normalize(name: str) -> str:
    """Collapse a school name to a comparison key.

    "Appalachian St." -> "appalachian state"; "Texas A&M" -> "texas aandm".
    """
    if not name:
        return ""
    key = name.lower().strip()
    # Fold accents before punctuation is stripped. "San José State" would
    # otherwise lose the e entirely and normalise to "san jos state", which
    # matches nothing — a whole-school miss from one character.
    key = "".join(
        c for c in unicodedata.normalize("NFKD", key) if not unicodedata.combining(c)
    )
    key = key.replace("&", "and").replace("'", "").replace("’", "")
    key = _ST_ABBREV.sub("state", key)
    key = _PUNCT.sub(" ", key)
    key = _NOISE.sub(" ", key)
    key = _SPACES.sub(" ", key).strip()
    for mascot in _TRAILING_MASCOTS:
        if key.endswith(" " + mascot):
            key = key[: -len(mascot) - 1].strip()
            break
    return ALIASES.get(key, key)


T = TypeVar("T")


class TeamIndex(Generic[T]):
    """Lookup from any spelling of a school to whatever payload was registered.

    Exact normalized key first, then a fuzzy fallback with a high cutoff — loose
    enough for "Nicholls"/"Nicholls St.", tight enough not to confuse the many
    near-identical directional schools (Northern/Southern/Eastern Illinois).
    """

    def __init__(self, cutoff: float = 0.9) -> None:
        self._exact: dict[str, T] = {}
        self.cutoff = cutoff

    def register(self, names: Iterable[str], payload: T) -> None:
        for name in names:
            key = normalize(name)
            if key:
                self._exact.setdefault(key, payload)

    def lookup(self, name: str) -> Optional[T]:
        key = normalize(name)
        if not key:
            return None
        hit = self._exact.get(key)
        if hit is not None:
            return hit
        close = difflib.get_close_matches(key, self._exact.keys(), n=1, cutoff=self.cutoff)
        return self._exact[close[0]] if close else None

    def __len__(self) -> int:
        return len(self._exact)


def pair_key(team_a: str, team_b: str) -> frozenset[str]:
    """Order-independent key for a matchup, so home/away disagreements don't matter."""
    return frozenset({normalize(team_a), normalize(team_b)})
