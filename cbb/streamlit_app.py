"""The college-basketball board as a Streamlit page.

    .venv/Scripts/streamlit run streamlit_app.py

Streamlit re-runs this whole file on every widget interaction, which matters
more here than in most apps: a refresh drives a real browser at two sportsbooks
and at KenPom and takes a minute or two. So the fetch sits behind
`st.cache_data` and nothing but the Refresh button can cause another one -
changing the pairing, the price representation or the day window re-reads the
cached fetch instead. `refresh_token` in session state is what Refresh bumps;
clearing the cache outright would also throw away the other day windows.

Layout only lives here. The table itself is built by `cbb/view.py`, which has
no Streamlit import and is tested on its own.

A hosted copy (Streamlit Community Cloud) has no browser to drive, so the
sportsbook boards and KenPom cannot be read there. Setting `browser_scrapes`
to false in that app's secrets makes it fetch only what a plain HTTP client can
reach - Kalshi's prices and ESPN's schedule and BPI - and say in one line which
columns are therefore missing, rather than three scrapers each reporting their
own failure. The README has the deployment steps.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import streamlit as st

from core import format as fmt

from cbb.refresh import Refresh, refresh_all
from cbb.sections import DEFAULT_DAYS
from cbb.view import Board, Pairing, TICKER, build_board, game_detail

log = logging.getLogger(__name__)

BOOKS = ("DraftKings", "BetMGM")
DAY_CHOICES = (1, 2, 3, 5, 7)
STALE_AFTER_SECONDS = 90

# Mid-tone rather than the old board's pastels: Streamlit's theme can be light
# or dark and these have to stay legible on both.
POSITIVE = "#16a34a"
NEGATIVE = "#dc2626"


def browser_scrapes() -> bool:
    """Whether this host can drive a browser, and so read the books and KenPom.

    True everywhere a person runs it; a deployment that cannot says so once in
    its secrets. Reading secrets throws when there is no secrets file at all,
    which is the ordinary local case.
    """
    try:
        return bool(st.secrets.get("browser_scrapes", True))
    except Exception:  # noqa: BLE001 - no secrets file is not a problem
        return True


@st.cache_data(show_spinner=False, max_entries=8)
def load(days: int, orderbooks: bool, browser: bool, refresh_token: int) -> Refresh:
    """One fetch, cached.

    `refresh_token` is not used in the body - it is in the signature so that
    pressing Refresh is a different cache key and nothing else is.
    """
    return refresh_all(days=days, with_orderbooks=orderbooks, use_browser=browser)


def sidebar() -> dict[str, Any]:
    with st.sidebar:
        st.header("Board")
        days = st.selectbox(
            "Days of slate",
            DAY_CHOICES,
            index=DAY_CHOICES.index(DEFAULT_DAYS),
            help="Widening the window re-reads the same fetch; it does not re-scrape.",
        )
        orderbooks = st.toggle(
            "Fetch order-book depth",
            value=False,
            help="Slower: pulls full Kalshi depth for every contract.",
        )
        if st.button("Refresh", type="primary", use_container_width=True):
            st.session_state.refresh_token = st.session_state.get("refresh_token", 0) + 1

        st.divider()
        st.subheader("Columns")
        pairing = st.radio(
            "Pair the two sides as",
            list(Pairing),
            index=list(Pairing).index(Pairing.FAV_DOG),
            format_func=lambda p: p.value,
            help=(
                "The favourite is whichever side Kalshi prices higher, decided once "
                "per game so every measure means the same team."
            ),
        )
        american = st.toggle(
            "American odds",
            value=False,
            help=(
                "Prices only. Edge and BPI−K are differences in percentage points "
                "either way, so they stay numeric and sortable."
            ),
        )
        compact = st.checkbox(
            "Compact",
            value=False,
            help="Drops the book prices and the volume - the phone-sized board.",
        )
        show_books = st.checkbox("Book prices", value=True, disabled=compact)
        show_bpi = st.checkbox("BPI", value=True, disabled=compact)
        show_volume = st.checkbox("Volume", value=True, disabled=compact)
    if compact:
        show_books = False
        show_volume = False
    return {
        "days": days,
        "orderbooks": orderbooks,
        "pairing": pairing,
        "american": american,
        "show_books": show_books,
        "show_bpi": show_bpi,
        "show_volume": show_volume,
    }


def header(data: Refresh) -> None:
    age = (datetime.now(timezone.utc) - data.generated_at).total_seconds()
    stamp = fmt.timestamp(data.generated_at)
    if age > STALE_AFTER_SECONDS:
        st.caption(f"as of {stamp} · :orange[{int(age // 60)} min old]")
    else:
        st.caption(f"as of {stamp}")


def notes(section: Any, browser: bool) -> None:
    counts = section.to_dict()["counts"]
    st.caption(
        f"{section.window_label} · {counts['games']} games · "
        f"{counts['with_composite']} with a book composite · "
        f"{counts['with_bpi']} with BPI · "
        f"{counts['with_kenpom']} with both KenPom ranks"
    )
    if not browser:
        # One honest sentence beats three scrapers each reporting their own
        # failure, which reads like something broke rather than a host that was
        # never going to be able to do it.
        st.info(
            "This copy has no browser to drive, so the **book prices, the composite, "
            "Edge and the KenPom ranks are not available** here - those come from "
            "scraping. Kalshi's prices and ESPN's BPI are fetched over plain HTTP and "
            "are live. Run it locally for the full board.",
            icon="ℹ️",
        )
    else:
        problems = [
            f"**{book}** - {note}" for book, note in section.book_notes.items() if note
        ]
        if section.kenpom_note:
            problems.append(f"**KenPom** - {section.kenpom_note}")
        for problem in problems:
            st.warning(problem, icon="⚠️")
    if section.beyond_window:
        st.caption(
            f"{section.beyond_window} further Kalshi games tip off beyond this window - "
            "widen it in the sidebar. The books only hang lines a day or two out, so "
            "most of those have nothing to compare against yet."
        )


def style(board: Board) -> Any:
    """Colour the edges by sign and fix the number formats.

    Formatting is done here rather than in `column_config` because a Styler and
    a column format fight over the same cell; the Styler wins, so it does both.
    """
    def colour(value: Any) -> str:
        if value is None or not isinstance(value, (int, float)) or value != value:
            return ""
        if value > 0:
            return f"color: {POSITIVE}; font-weight: 600"
        if value < 0:
            return f"color: {NEGATIVE}"
        return ""

    styler = board.frame.style
    if board.edge_columns:
        styler = styler.map(colour, subset=list(board.edge_columns))
    formats = {c: "{:+.1f}" for c in board.edge_columns}
    formats.update(
        {c: "{:.1f}" for c in board.percent_columns if c not in board.edge_columns}
    )
    if "Vol" in board.frame.columns:
        formats["Vol"] = "{:,.0f}"
    if "Depth" in board.frame.columns:
        formats["Depth"] = "{:,.0f}"
    return styler.format(formats, na_rep="--")


def column_config(board: Board) -> dict[str, Any]:
    config: dict[str, Any] = {
        TICKER: None,  # carried for the detail panel, not shown
        "Tip-off": st.column_config.TextColumn(
            "Tip-off", help="ESPN's published start, in this machine's time zone"
        ),
        "Flags": st.column_config.TextColumn(
            "Flags", help="Anything about this game worth knowing before trading it"
        ),
    }
    for column in board.frame.columns:
        if column.startswith("Edge "):
            config[column] = st.column_config.NumberColumn(
                column,
                help="Book composite minus the Kalshi ask, in points. Positive: Kalshi is cheap.",
            )
        elif column.startswith("BPI−K "):
            config[column] = st.column_config.NumberColumn(
                column, help="BPI minus the Kalshi ask, in points. Positive: Kalshi is cheap."
            )
        elif column.startswith("KP "):
            config[column] = st.column_config.NumberColumn(
                column, help="KenPom rank, 1 to 365", format="%d"
            )
    return config


def detail(section: Any, selection: Any, board: Board) -> None:
    rows = getattr(selection, "selection", {}).get("rows", []) if selection else []
    if not rows:
        st.caption(
            "Select a row for its two-sided quotes, each book's reason for a blank, "
            "and its KenPom lines."
        )
        return
    ticker = board.frame.iloc[rows[0]][TICKER]
    row = next((r for r in section.rows if r.game.event_ticker == ticker), None)
    if row is None:
        return
    with st.container(border=True):
        for line in game_detail(row, BOOKS):
            st.markdown(line)


def main() -> None:
    st.set_page_config(
        page_title="NCAAM Sharp Line",
        page_icon="\U0001f3c0",
        layout="wide",
        # On a phone the sidebar would cover the board; the controls stay one
        # tap away and the table gets the whole screen.
        initial_sidebar_state="collapsed",
    )
    st.title("NCAAM Sharp Line")
    st.markdown(
        "**Edge** is the book composite minus the Kalshi ask, per team - "
        ":green[positive] means Kalshi is priced below the books, so that side is the "
        "cheap one to buy. **BPI−K** does the same against ESPN's model. Blank book "
        "cells say which kind of blank they are: `off` (no moneyline posted), `--` (not "
        "on that board), `x` (we could not read the book - the only one that means "
        "something is broken)."
    )

    options = sidebar()
    browser = browser_scrapes()
    token = st.session_state.get("refresh_token", 0)
    waiting = (
        "Scraping the books, KenPom and Kalshi. This takes a minute or two."
        if browser
        else "Fetching Kalshi and ESPN."
    )
    with st.spinner(waiting):
        data = load(options["days"], options["orderbooks"], browser, token)

    header(data)
    notes(data.moneylines, browser)

    board = build_board(
        data.moneylines,
        books=BOOKS,
        pairing=options["pairing"],
        american=options["american"],
        show_books=options["show_books"] and browser,
        show_kenpom=browser,
        show_composite=browser,
        show_bpi=options["show_bpi"],
        show_volume=options["show_volume"],
        show_depth=any(
            c is not None and c.orderbook is not None
            for row in data.moneylines.rows
            for c in (row.game.away, row.game.home)
        ),
    )
    selection = st.dataframe(
        style(board),
        column_config=column_config(board),
        hide_index=True,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
    )
    detail(data.moneylines, selection, board)

