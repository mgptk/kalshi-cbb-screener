"""Book moneylines from The Odds API, for hosts that cannot drive a browser.

DraftKings and BetMGM both render their boards client-side and refuse plain
HTTP clients outright - their own JSON endpoints answer 403 even from a
residential address, because the block is on the client fingerprint rather
than the address. So there is no way to read them without a browser, and a
hosted container has none. The Odds API resells the same two books' lines over
ordinary HTTPS with a key, which is the one route into a cloud deployment.

One request covers both books: `markets=h2h` with `regions=us` costs a single
credit, and naming up to ten bookmakers still counts as one region. The free
tier's 500 credits a month is about sixteen refreshes a day.

Prices arrive as American odds, exactly what `build_quote` already devigs, so
a quote from here is the same shape as one scraped off a board and the
composite, Edge and the rest are unchanged.

Team names are the catch: this API spells a school with its mascot attached
("Saint Louis Billikens"), which no other feed here does. `_candidates`
handles that by also registering the name with its trailing words removed, and
any key that two different games would claim is dropped rather than guessed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import httpx

from core.models import BookQuote, Matchup
from core.scrapers.base import build_quote
from core.teams import normalize

log = logging.getLogger(__name__)

BASE_URL = "https://api.the-odds-api.com/v4"
SPORT = "basketball_ncaab"
MARKET = "h2h"
"""The API's name for a moneyline."""

REGION = "us"

FALLBACK_BOOKS = {"draftkings": "DraftKings", "betmgm": "BetMGM"}
"""Only used when the fetch fails: columns to carry the failure, so a dead
feed shows as two unreadable books rather than no books at all."""

LOW_CREDITS = 50
"""Below this, the board says so rather than waiting to go blank."""

MAX_WORDS_DROPPED = 2
"""How much of a trailing mascot to try removing: "Blue Devils" is two words."""


def _candidates(name: str) -> list[str]:
    """Normalised spellings a school might be known by, longest first.

    "Duke Blue Devils" -> duke blue devils, duke blue, duke. Dropping words is
    crude, but it only ever has to meet a name from another feed halfway, and
    an ambiguous result is thrown away rather than guessed at.
    """
    words = normalize(name).split()
    out: list[str] = []
    for drop in range(0, MAX_WORDS_DROPPED + 1):
        if len(words) - drop < 1:
            break
        # Normalise again rather than just rejoining: the alias table is
        # applied at the end of normalize(), so a truncation that only now
        # became an alias - "SMU Mustangs" -> "smu" -> southern methodist -
        # would otherwise never reach it, and the two feeds could not meet.
        candidate = normalize(" ".join(words[: len(words) - drop]))
        if len(candidate) >= 3 and candidate not in out:
            out.append(candidate)
    return out


def _same_school(one: str, other: str) -> bool:
    """Whether two spellings name the same school, mascots allowed.

    "Wofford Terriers" and "Wofford" are the same team; comparing the
    normalised strings directly says they are not, which is how the sides of
    a game end up swapped.
    """
    return bool(set(_candidates(one)) & set(_candidates(other)))


def _pair_keys(away: str, home: str) -> list[frozenset[str]]:
    """Every pairing of the two sides' candidate spellings."""
    keys = []
    for a in _candidates(away):
        for h in _candidates(home):
            key = frozenset({a, h})
            if len(key) == 2 and key not in keys:
                keys.append(key)
    return keys


@dataclass
class ApiGame:
    """One game as the API has it, with each book's two-sided price."""

    away_team: str
    home_team: str
    prices: dict[str, tuple[Optional[float], Optional[float]]] = field(default_factory=dict)
    """bookmaker key -> (away american, home american)."""
    titles: dict[str, str] = field(default_factory=dict)
    """bookmaker key -> the book's display name, as the API spells it."""


def parse_events(payload: Any) -> list[ApiGame]:
    """The API's event list into games, keeping only two-sided moneylines."""
    games: list[ApiGame] = []
    if not isinstance(payload, list):
        return games
    for event in payload:
        if not isinstance(event, dict):
            continue
        away, home = event.get("away_team"), event.get("home_team")
        if not away or not home:
            continue
        game = ApiGame(away_team=str(away), home_team=str(home))
        for book in event.get("bookmakers") or []:
            key = book.get("key")
            if not key:
                continue
            for market in book.get("markets") or []:
                if market.get("key") != MARKET:
                    continue
                side: dict[str, Optional[float]] = {}
                for outcome in market.get("outcomes") or []:
                    name, price = outcome.get("name"), outcome.get("price")
                    if name is None or price is None:
                        continue
                    try:
                        side[str(name)] = float(price)
                    except (TypeError, ValueError):
                        continue
                game.prices[key] = (side.get(str(away)), side.get(str(home)))
                game.titles[key] = str(book.get("title") or key)
        games.append(game)
    return games


class OddsApiClient:
    """One request, shared by every book that reads from it."""

    def __init__(
        self,
        api_key: str,
        client: Optional[httpx.Client] = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self._client = client
        self._timeout = timeout
        self._games: dict[frozenset[str], ApiGame] = {}
        self.titles: dict[str, str] = {}
        """Bookmaker key -> the name to put at the top of its column."""
        self.coverage: dict[str, int] = {}
        """Bookmaker key -> how many of the slate's games it priced."""
        self.note: Optional[str] = None
        self.loaded_ok = False
        self.credits_remaining: Optional[int] = None
        self._loaded = False

    # -- loading -----------------------------------------------------------

    def load(self) -> None:
        """Fetch once per refresh, however many books ask for it."""
        if self._loaded:
            return
        self._loaded = True
        if not self.api_key:
            self.note = "no Odds API key configured"
            return

        # No `bookmakers` filter: the whole region costs the same single
        # credit and returns every book it carries, and a composite over
        # eight books is worth more than one over two.
        params = {
            "apiKey": self.api_key,
            "regions": REGION,
            "markets": MARKET,
            "oddsFormat": "american",
            "dateFormat": "iso",
        }
        client = self._client or httpx.Client(timeout=self._timeout, follow_redirects=True)
        try:
            response = client.get(f"{BASE_URL}/sports/{SPORT}/odds", params=params)
            self._read_quota(response)
            if response.status_code == 401:
                self.note = "The Odds API rejected the key (401)"
                return
            if response.status_code == 429:
                self.note = "The Odds API quota is used up for this month (429)"
                return
            response.raise_for_status()
            games = parse_events(response.json())
        except Exception as exc:  # noqa: BLE001 - a dead feed is a column, not a crash
            log.warning("the-odds-api: %s", exc)
            self.note = f"could not read The Odds API: {exc}"
            return
        finally:
            if self._client is None:
                client.close()

        self._index(games)
        self._survey(games)
        self.loaded_ok = True
        if self.credits_remaining is not None and self.credits_remaining <= LOW_CREDITS:
            # Worth saying before the month runs out and the board quietly
            # loses its book columns.
            self.note = (
                f"{self.credits_remaining} Odds API credits left this month "
                f"(one per refresh)"
            )
        log.info(
            "the-odds-api: %d games, %d books, %s credits left",
            len(games),
            len(self.titles),
            self.credits_remaining if self.credits_remaining is not None else "?",
        )

    def _survey(self, games: Sequence[ApiGame]) -> None:
        """Which books came back, and how much of the slate each one priced.

        The API decides this, not us: a book that has gone quiet simply stops
        appearing. Coverage orders the columns, so the books that price most
        of the slate sit nearest the prices they are being compared against.
        """
        for game in games:
            for key, title in game.titles.items():
                self.titles.setdefault(key, title or key)
                if game.prices.get(key, (None, None))[0] is not None:
                    self.coverage[key] = self.coverage.get(key, 0) + 1

    def book_keys(self) -> list[str]:
        """The books found, most of the slate first, then alphabetically."""
        return sorted(self.titles, key=lambda k: (-self.coverage.get(k, 0), k))

    def _read_quota(self, response: httpx.Response) -> None:
        raw = response.headers.get("x-requests-remaining")
        if raw is None:
            return
        try:
            self.credits_remaining = int(float(raw))
        except ValueError:
            self.credits_remaining = None

    def _index(self, games: Sequence[ApiGame]) -> None:
        """Key each game by every spelling of its two schools.

        A key two different games would both claim is ambiguous - two Miamis,
        say - so it is dropped. Losing a game to a missing price is a dash on
        the board; matching it to the wrong one is a wrong number.
        """
        claimed: dict[frozenset[str], ApiGame] = {}
        ambiguous: set[frozenset[str]] = set()
        for game in games:
            for key in _pair_keys(game.away_team, game.home_team):
                if key in claimed and claimed[key] is not game:
                    ambiguous.add(key)
                claimed.setdefault(key, game)
        for key in ambiguous:
            claimed.pop(key, None)
        if ambiguous:
            log.info("the-odds-api: dropped %d ambiguous name pairings", len(ambiguous))
        self._games = claimed

    # -- lookup ------------------------------------------------------------

    def game_for(self, matchup: Matchup) -> Optional[ApiGame]:
        return self._games.get(frozenset({normalize(matchup.away_team), normalize(matchup.home_team)}))

    def quote(self, key: str, matchup: Matchup) -> BookQuote:
        """One book's moneyline on one game, in the usual blank-reason terms."""
        display_name = self.titles.get(key, key)
        if not self.loaded_ok:
            return BookQuote.unavailable(display_name, self.note or "The Odds API could not be read")
        game = self.game_for(matchup)
        if game is None:
            return BookQuote.not_on_board(display_name)
        if key not in game.prices:
            return BookQuote.not_on_board(display_name)

        away, home = game.prices[key]
        # The API's home/away can disagree with ours on a neutral-site game.
        # Swap only on positive evidence that our away side is their home one:
        # an inconclusive comparison must leave the orientation alone.
        if _same_school(game.home_team, matchup.away_team) and not _same_school(
            game.away_team, matchup.away_team
        ):
            away, home = home, away
        return build_quote(display_name, away, home, source=f"the-odds-api:{key}")


class OddsApiBook:
    """A `BookScraper` view onto the shared client, one per book."""

    def __init__(self, key: str, name: str, client: OddsApiClient) -> None:
        self.key = key
        self.name = name
        self._client = client
        self.source = f"the-odds-api:{key}"

    @property
    def note(self) -> Optional[str]:
        return self._client.note

    @property
    def credits_remaining(self) -> Optional[int]:
        """What the API's own x-requests-remaining header last said."""
        return self._client.credits_remaining

    def load(self) -> None:
        self._client.load()

    def quote(self, matchup: Matchup) -> BookQuote:
        return self._client.quote(self.key, matchup)


def odds_api_books(api_key: str, client: Optional[OddsApiClient] = None) -> list[OddsApiBook]:
    """Every book the API carries, in coverage order, from one request.

    Which books exist is only known once the request has been made, so this
    fetches before deciding the columns. A fetch that failed still yields the
    two books everyone expects, each carrying the reason, so the board reports
    a dead feed instead of quietly having no book columns at all.
    """
    source = client or OddsApiClient(api_key=api_key)
    source.load()
    found = source.book_keys()
    if not found:
        return [OddsApiBook(key, name, source) for key, name in FALLBACK_BOOKS.items()]
    return [OddsApiBook(key, source.titles[key], source) for key in found]


__all__ = [
    "OddsApiBook",
    "OddsApiClient",
    "odds_api_books",
    "parse_events",
    "FALLBACK_BOOKS",
    "SPORT",
]
