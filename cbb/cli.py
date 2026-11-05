"""Terminal view of the basketball board.

    python -m cbb.cli --days 2
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from .kenpom import KenPomTeam
from .refresh import refresh_all
from .sections import DEFAULT_DAYS, MoneylineSection


def _pct(value: Optional[float]) -> str:
    return "  -- " if value is None else f"{value * 100:5.1f}"


def _signed(value: Optional[float]) -> str:
    return "  -- " if value is None else f"{value * 100:+5.1f}"


def _ranked(team: str, kenpom: Optional[KenPomTeam]) -> str:
    """Prefixes the KenPom rank: Duke -> "1 Duke". Unranked schools go bare."""
    return f"{kenpom.rank} {team}" if kenpom is not None else team


def print_moneylines(section: MoneylineSection, limit: Optional[int] = None) -> None:
    counts = section.to_dict()["counts"]
    print(f"\n=== MEN'S CBB MONEYLINES ({section.window_label}) ===")
    for book, note in section.book_notes.items():
        if note:
            print(f"  ! {book}: {note}")
    print(
        f"  {counts['games']} games | {counts['with_composite']} with a book composite "
        f"| {counts['with_bpi']} with BPI | {counts['with_kenpom']} with both KenPom ranks"
    )
    if section.kenpom_note:
        print(f"  ! KenPom: {section.kenpom_note}")
    if section.beyond_window:
        print(f"  {section.beyond_window} further Kalshi games tip off beyond the window")
    header = (
        f"  {'matchup':<42} {'Kask-a':>6} {'Kask-h':>6} {'C-away':>6} {'C-home':>6} "
        f"{'edgeA':>6} {'edgeH':>6} {'BPI-a':>6} {'BPI-Ka':>7}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for row in (section.rows[:limit] if limit else section.rows):
        game = row.game
        flag = ""
        if row.books_unreadable:
            flag = "  X " + ",".join(row.books_unreadable)
        elif row.moneyline_off_everywhere:
            flag = "  off: no book prices this moneyline"
        matchup = (
            f"{_ranked(game.away_team, row.kenpom_away)} @ "
            f"{_ranked(game.home_team, row.kenpom_home)}"
        )
        print(
            f"  {matchup:<42.42} "
            f"{_pct(game.away.yes_ask if game.away else None):>6} "
            f"{_pct(game.home.yes_ask if game.home else None):>6} "
            f"{_pct(row.composite_away):>6} {_pct(row.composite_home):>6} "
            f"{_signed(row.edge_away):>6} {_signed(row.edge_home):>6} "
            f"{_pct(row.bpi.away_win_prob):>6} {_signed(row.bpi_edge_away):>7}{flag}"
        )
    if limit and len(section.rows) > limit:
        print(f"  ... {len(section.rows) - limit} more")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="cbb.cli", description=__doc__)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="days of slate to show")
    parser.add_argument("--limit", type=int, default=25, help="rows to print (0 = all)")
    parser.add_argument("--json", metavar="PATH", help="also write the payload here")
    parser.add_argument("--orderbooks", action="store_true", help="fetch order-book depth")
    parser.add_argument("--no-browser", action="store_true", help="skip the book scrapes")
    parser.add_argument("--no-kenpom", action="store_true", help="skip the KenPom ranks")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    refresh = refresh_all(
        days=args.days,
        with_orderbooks=args.orderbooks,
        use_browser=not args.no_browser,
        with_kenpom=not args.no_kenpom,
    )
    print_moneylines(refresh.moneylines, args.limit or None)
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(refresh.to_dict(), indent=2), encoding="utf-8")
        print(f"\nwrote {out} ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
