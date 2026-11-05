"""Turns a `MoneylineSection` into the one-row-per-game table the board shows.

Kept apart from any UI so it can be tested without one, and so the Streamlit
page is layout only.

The shape is the whole design decision. The old HTML board gave a game three
rows - a header and one per team - which meant a sort had to move whole blocks
and every measure had two numbers to choose from; that is what the four-way
sort dropdown was for. One row per game instead pairs every measure into two
columns, so the table sorts natively on whichever side the reader means and the
dropdown disappears.

Which side is which is the reader's choice: `Pairing.AWAY_HOME` keeps the
sportsbook's own orientation, `Pairing.FAV_DOG` puts the favourite first, which
is how the edges are usually read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import tzinfo
from enum import Enum
from typing import Any, Optional, Sequence

import pandas as pd

from core import format as fmt
from core.models import QuoteStatus

from .sections import MoneylineRow, MoneylineSection

STATUS_MARK = {
    QuoteStatus.MONEYLINE_OFF: "off",
    QuoteStatus.NOT_ON_BOARD: "--",
    QuoteStatus.UNAVAILABLE: "x",
}
"""Why a book cell is blank. Only `x` means something is broken."""

STATUS_TITLE = {
    "off": "the book has this game but posts no moneyline, typically a favourite too large to price",
    "--": "this game is not on the book's board",
    "x": "could not read this book - a scraping problem, not a missing line",
}

TICKER = "event_ticker"
"""Carried in the frame so a selected display row maps back to its game."""


class Pairing(str, Enum):
    AWAY_HOME = "Away / Home"
    FAV_DOG = "Favourite / Underdog"


def favourite_side(row: MoneylineRow) -> str:
    """Which side the market calls the favourite: "away" or "home".

    One choice per game, shared by every column, so the favourite is the same
    team in the Edge pair as in the Kalshi pair. Kalshi's own ask decides it,
    since that is the one price every game has; the book composite and then BPI
    stand in when Kalshi is unpriced, and a game with nothing priced at all
    keeps the home side as the nominal favourite.
    """
    contenders = (
        (
            row.game.away.yes_ask if row.game.away else None,
            row.game.home.yes_ask if row.game.home else None,
        ),
        (row.composite_away, row.composite_home),
        (row.bpi.away_win_prob, row.bpi.home_win_prob),
    )
    for away, home in contenders:
        if away is not None and home is not None and away != home:
            return "away" if away > home else "home"
    return "home"


def _sides(row: MoneylineRow, pairing: Pairing) -> tuple[str, str]:
    if pairing is Pairing.FAV_DOG:
        first = favourite_side(row)
        return first, ("home" if first == "away" else "away")
    return "away", "home"


def _labels(pairing: Pairing) -> tuple[str, str]:
    return ("Fav", "Dog") if pairing is Pairing.FAV_DOG else ("Away", "Home")


def _pct(value: Optional[float]) -> Optional[float]:
    """A probability as the percentage number the table sorts on."""
    return None if value is None else round(value * 100, 1)


def _price(value: Optional[float], american: bool) -> Any:
    """One price, as a sortable number or as the American string."""
    if not american:
        return _pct(value)
    cell = fmt.price_cell(value)
    return cell.american


def _team(row: MoneylineRow, side: str) -> str:
    """Just the school.

    The rank used to be prefixed here, which read well but sorted as
    text: "100 Duke" came before "9 Yale". The rank has its own numeric column
    beside this one instead, so both sort the way they look.
    """
    return row.game.away_team if side == "away" else row.game.home_team


def _rank(row: MoneylineRow, side: str) -> Optional[int]:
    rank = row.rating_away if side == "away" else row.rating_home
    return rank.rank if rank else None


def _flags(row: MoneylineRow) -> str:
    """The short warnings that used to be tags on the game's header row."""
    out: list[str] = []
    if not row.tipoff_from_espn:
        out.append("approx time")
    if row.neutral_site:
        out.append("neutral")
    if not row.matched_to_book_slate:
        out.append("no ESPN match")
    if row.game.mid_sum is not None:
        off = (row.game.mid_sum - 1) * 100
        if abs(off) >= 1.5:
            out.append(f"Kalshi sums {off:+.1f}%")
    if row.moneyline_off_everywhere:
        out.append("no moneyline offered")
    if row.books_unreadable:
        out.append("unread: " + ", ".join(row.books_unreadable))
    return ", ".join(out)


# Columns whose numbers are percentage points either way, so they stay numeric
# and sortable whichever representation the prices are in.
def _edge(row: MoneylineRow, side: str) -> Optional[float]:
    value = row.edge_away if side == "away" else row.edge_home
    return None if value is None else round(value * 100, 1)


def _bpi_edge(row: MoneylineRow, side: str) -> Optional[float]:
    value = row.bpi_edge_away if side == "away" else row.bpi_edge_home
    return None if value is None else round(value * 100, 1)


def _bpi(row: MoneylineRow, side: str) -> Optional[float]:
    return _pct(row.bpi.away_win_prob if side == "away" else row.bpi.home_win_prob)


def _composite(row: MoneylineRow, side: str, american: bool) -> Any:
    return _price(row.composite_away if side == "away" else row.composite_home, american)


def _kalshi(row: MoneylineRow, side: str, american: bool) -> Any:
    contract = row.game.away if side == "away" else row.game.home
    return _price(contract.yes_ask if contract else None, american)


def _priced_books(row: MoneylineRow) -> int:
    """How many books the composite is averaging for this game.

    With eight books of uneven coverage the basis moves game to game, and a
    composite over one book is a very different number from one over six.
    """
    return sum(1 for q in row.books.values() if q.status is QuoteStatus.OK)


def _volume(row: MoneylineRow) -> Optional[float]:
    volumes = [c.volume for c in (row.game.away, row.game.home) if c]
    return sum(volumes) if volumes else None


def _depth(row: MoneylineRow) -> Optional[float]:
    depths = [
        c.orderbook.yes_depth
        for c in (row.game.away, row.game.home)
        if c is not None and c.orderbook is not None
    ]
    return sum(depths) if depths else None


@dataclass(frozen=True)
class Board:
    """The table, plus what the page needs to describe and annotate it."""

    frame: pd.DataFrame
    edge_columns: tuple[str, ...]
    """Columns coloured by sign: positive means Kalshi is the cheap side."""
    percent_columns: tuple[str, ...]
    """Numeric columns holding percentage points."""


def build_board(
    section: MoneylineSection,
    rank_label: str = "BPI#",
    pairing: Pairing = Pairing.FAV_DOG,
    american: bool = False,
    show_ranks: bool = True,
    show_composite: bool = True,
    show_bpi: bool = True,
    show_volume: bool = True,
    show_depth: bool = False,
    zone: Optional[tzinfo] = None,
) -> Board:
    """One row per game, two columns per measure.

    Edge and BPI−K are differences in percentage points, so they stay numeric
    whatever the price representation is - which keeps the sorts that matter
    numeric even when the prices are showing as American odds.

    The individual books are deliberately not columns. Eight of them is
    sixteen columns of numbers nobody reads across, and what the board is
    actually for is the composite and the gap to Kalshi. `Bks` says how many
    books that composite had; `game_detail` has each book's own price for the
    one game a reader picks.
    """
    a, b = _labels(pairing)
    rows: list[dict[str, Any]] = []
    for row in section.rows:
        first, second = _sides(row, pairing)
        # Insertion order is column order: the ranks sit between the tip-off
        # and the schools, so a row reads "when, how good, who".
        record: dict[str, Any] = {"Tip-off": fmt.kickoff(row.game.kickoff, zone)}
        if show_ranks:
            record[f"{rank_label} {a}"] = _rank(row, first)
            record[f"{rank_label} {b}"] = _rank(row, second)
        record.update({
            a: _team(row, first),
            b: _team(row, second),
            f"Kalshi {a}": _kalshi(row, first, american),
            f"Kalshi {b}": _kalshi(row, second, american),
        })
        if show_composite:
            # Both come from the books, so a host that cannot read the books
            # drops them rather than printing two columns of dashes.
            record[f"Comp {a}"] = _composite(row, first, american)
            record[f"Comp {b}"] = _composite(row, second, american)
            record["Bks"] = _priced_books(row)
            record[f"Edge {a}"] = _edge(row, first)
            record[f"Edge {b}"] = _edge(row, second)
        if show_bpi:
            record[f"BPI {a}"] = _bpi(row, first)
            record[f"BPI {b}"] = _bpi(row, second)
            record[f"BPI−K {a}"] = _bpi_edge(row, first)
            record[f"BPI−K {b}"] = _bpi_edge(row, second)
        if show_volume:
            record["Vol"] = _volume(row)
        if show_depth:
            record["Depth"] = _depth(row)
        record["Flags"] = _flags(row)
        record[TICKER] = row.game.event_ticker
        rows.append(record)

    frame = pd.DataFrame(rows)
    edges = tuple(c for c in frame.columns if c.startswith(("Edge ", "BPI−K ")))
    percents = edges + tuple(
        c
        for c in frame.columns
        if not american and c.startswith(("Kalshi ", "Comp ", "BPI "))
    )
    return Board(frame=frame, edge_columns=edges, percent_columns=percents)


def game_detail(
    row: MoneylineRow, books: Sequence[str], zone: Optional[tzinfo] = None
) -> list[str]:
    """The lines behind one game, which used to live in hover titles.

    `st.dataframe` can only caption a whole column, so everything that was a
    `title=` attribute - the two-sided quotes, each blank book cell's reason,
    the rating line - is written out for the game the reader picks instead.
    """
    lines = [f"**{row.game.away_team} @ {row.game.home_team}** - `{row.game.event_ticker}`"]
    when = fmt.kickoff(row.game.kickoff, zone)
    source = "ESPN's published start" if row.tipoff_from_espn else (
        "estimated from Kalshi's settle time; ESPN does not carry this game"
    )
    lines.append(f"- Tip-off {when} ({source})")

    for side in ("away", "home"):
        contract = row.game.away if side == "away" else row.game.home
        team = row.game.away_team if side == "away" else row.game.home_team
        if contract is None or contract.yes_bid is None:
            lines.append(f"- **{team}** - no Kalshi quote")
            continue
        quote = f"yes {contract.yes_bid:.2f} bid / {contract.yes_ask:.2f} ask"
        if contract.no_bid is not None and contract.no_ask is not None:
            quote += f" | no {contract.no_bid:.2f} bid / {contract.no_ask:.2f} ask"
        lines.append(f"- **{team}** - {quote}")
        rank = row.rating_away if side == "away" else row.rating_home
        if rank:
            lines.append(f"    - {rank.summary}")

    for book in books:
        quote = row.books.get(book)
        if quote is None:
            lines.append(f"- **{book}** - not configured")
            continue
        if quote.status is QuoteStatus.OK:
            lines.append(
                f"- **{book}** - {quote.away_american:+.0f} / {quote.home_american:+.0f}"
                + (f" (hold {quote.hold_pct:.1f}%)" if quote.hold_pct is not None else "")
            )
            continue
        mark = STATUS_MARK.get(quote.status, "--")
        lines.append(f"- **{book}** - `{mark}`: {quote.note or STATUS_TITLE.get(mark, '')}")

    if row.bpi.predicted_margin is not None:
        lines.append(f"- BPI margin {row.bpi.predicted_margin:+.1f} (from the away side)")
    if row.game.mid_sum is not None:
        lines.append(f"- Kalshi's two yes mids sum to {row.game.mid_sum * 100:.1f}%")
    return lines


__all__ = ["Board", "Pairing", "build_board", "favourite_side", "game_detail"]
