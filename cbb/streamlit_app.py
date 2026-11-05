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

FALLBACK_BOOKS = ("DraftKings", "BetMGM")
"""Only for a refresh that produced no books at all."""
DAY_CHOICES = (1, 2, 3, 5, 7)
STALE_AFTER_SECONDS = 90
FREE_TIER_CREDITS = 500
"""The Odds API's monthly free allowance, for the counter to count against."""
LOW_CREDITS = 50
"""Below this the counter says so plainly rather than just getting smaller."""

# Mid-tone rather than the old board's pastels: Streamlit's theme can be light
# or dark and these have to stay legible on both.
POSITIVE = "#16a34a"
NEGATIVE = "#dc2626"


def _secret(name: str, default: Any = None) -> Any:
    """One setting, or the default. No secrets file at all is the local case."""
    try:
        return st.secrets.get(name, default)
    except Exception:  # noqa: BLE001 - not having one is not a problem
        return default


def browser_scrapes() -> bool:
    """Whether this host can drive a browser, and so scrape boards and KenPom.

    True everywhere a person runs it; a deployment that cannot says so in its
    secrets.
    """
    return bool(_secret("browser_scrapes", True))


def ratings_choice() -> str:
    """Which ratings to use: see `cbb.refresh.choose_ratings`."""
    return str(_secret("ratings", "auto") or "auto")


def odds_api_key() -> str:
    """The Odds API key, if one is configured.

    With it, a host that cannot drive a browser still gets DraftKings and
    BetMGM - the same two books, resold over plain HTTPS - and so keeps the
    composite and the Edge columns that are the point of the board.
    """
    return str(_secret("odds_api_key", "") or "")


@st.cache_data(show_spinner=False, max_entries=8)
def load(
    days: int,
    orderbooks: bool,
    browser: bool,
    api_key: str,
    ratings: str,
    refresh_token: int,
) -> Refresh:
    """One fetch, cached.

    `refresh_token` is not used in the body - it is in the signature so that
    pressing Refresh is a different cache key and nothing else is. The key is
    in the signature for the same reason: adding one should re-fetch.
    """
    return refresh_all(
        days=days,
        with_orderbooks=orderbooks,
        use_browser=browser,
        ratings=ratings,
        odds_api_key=api_key or None,
    )


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
            help="Drops BPI and volume - the phone-sized board.",
        )
        show_bpi = st.checkbox("BPI", value=True, disabled=compact)
        show_volume = st.checkbox("Volume", value=True, disabled=compact)
    if compact:
        show_bpi = False
        show_volume = False
    return {
        "days": days,
        "orderbooks": orderbooks,
        "pairing": pairing,
        "american": american,
        "show_bpi": show_bpi,
        "show_volume": show_volume,
    }


def books_in(section: Any) -> list[str]:
    """The books this refresh actually produced, in the order they came.

    Scraping gives the two configured boards; The Odds API gives whatever it
    carries, which is currently eight, ordered by how much of the slate each
    one priced.
    """
    return list(section.book_notes) or list(FALLBACK_BOOKS)


def credit_counter(data: Refresh) -> None:
    """The Odds API budget, beside the button that spends it.

    The free tier is 500 credits a month and one refresh costs one, so the
    number belongs next to Refresh rather than in a footer: that is where
    the decision to spend one is made.
    """
    if data.odds_credits is None:
        return
    with st.sidebar:
        st.divider()
        st.metric(
            "Odds API credits",
            f"{data.odds_credits:,}",
            delta=f"{FREE_TIER_CREDITS - data.odds_credits:,} used of {FREE_TIER_CREDITS}",
            delta_color="off",
            help=(
                "Left this month on the free tier's "
                f"{FREE_TIER_CREDITS}. One refresh spends one, whichever "
                "books come back."
            ),
        )
        if data.odds_credits <= LOW_CREDITS:
            st.warning(
                f"{data.odds_credits} left - the board loses its book "
                "columns when they run out.",
                icon="⚠️",
            )


def header(data: Refresh) -> None:
    age = (datetime.now(timezone.utc) - data.generated_at).total_seconds()
    stamp = fmt.timestamp(data.generated_at)
    if age > STALE_AFTER_SECONDS:
        st.caption(f"as of {stamp} · :orange[{int(age // 60)} min old]")
    else:
        st.caption(f"as of {stamp}")


def notes(section: Any, browser: bool, books_from_api: bool) -> None:
    counts = section.to_dict()["counts"]
    st.caption(
        f"{section.window_label} · {counts['games']} games · "
        f"{counts['with_composite']} with a book composite · "
        f"{counts['with_bpi']} with BPI · "
        f"{counts['with_ratings']} with both {section.rating_label} ranks"
    )
    if not browser and not books_from_api:
        # One honest sentence beats two scrapers each reporting their own
        # failure, which reads like something broke rather than a host that was
        # never going to be able to do it.
        st.info(
            "No browser here and no Odds API key, so the **book prices, the composite "
            "and Edge are not available** - those two books have to be scraped. "
            "Kalshi, ESPN's BPI and the "
            f"{section.rating_label} ranks are live. Add an `odds_api_key` to the "
            "app's secrets to get the book columns back.",
            icon="ℹ️",
        )
    else:
        # With a book source in play, each book's own note is worth seeing:
        # that is a feed that should have worked and did not. Without one they
        # would only repeat the sentence above.
        for book, note in section.book_notes.items():
            if note:
                st.warning(f"**{book}** - {note}", icon="⚠️")
    if section.rating_note:
        st.warning(f"**{section.rating_label}** - {section.rating_note}", icon="⚠️")
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


def column_config(board: Board, rank_label: str, rank_source: str) -> dict[str, Any]:
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
        elif column == "Bks":
            config[column] = st.column_config.NumberColumn(
                "Bks",
                help="How many books the composite averages for this game",
                format="%d",
            )
        elif column.startswith(f"{rank_label} "):
            config[column] = st.column_config.NumberColumn(
                column, help=f"{rank_source} rank, 1 to 365 (1 is best)", format="%d"
            )
    return config


def detail(section: Any, selection: Any, board: Board) -> None:
    rows = getattr(selection, "selection", {}).get("rows", []) if selection else []
    if not rows:
        st.caption(
            "Select a row for every book's own price on that game, the two Kalshi "
            "quotes and the rating lines."
        )
        return
    ticker = board.frame.iloc[rows[0]][TICKER]
    row = next((r for r in section.rows if r.game.event_ticker == ticker), None)
    if row is None:
        return
    with st.container(border=True):
        for line in game_detail(row, books_in(section)):
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
    # Folded away: on a phone this paragraph is the whole first screen, and it
    # is reference rather than something you reread every visit.
    with st.expander("What the columns mean"):
        st.markdown(
            "**Edge** is the book composite minus the Kalshi ask, per team - "
            ":green[positive] means Kalshi is priced below the books, so that side is "
            "the cheap one to buy. **BPI−K** does the same against ESPN's model.\n\n"
            "**Bks** is how many books went into that composite - one book and eight "
            "are very different numbers.\n\n"
            "Each measure appears twice, once per side, so sorting a column sorts on "
            "the side you mean. **Select a row** for the individual books' prices, "
            "each one's reason for a blank (`off` = no moneyline posted, `--` = not "
            "on that board, `x` = we could not read it), both Kalshi quotes and the "
            "rating lines."
        )

    options = sidebar()
    browser = browser_scrapes()
    api_key = odds_api_key()
    ratings = ratings_choice()
    token = st.session_state.get("refresh_token", 0)
    # Name what is actually being fetched: a browser scrape takes a minute or
    # two, and everything else takes seconds.
    slow = (browser and not api_key) or (browser and ratings in ("auto", "kenpom"))
    waiting = (
        "Fetching Kalshi, ESPN, the odds feed and the ratings."
        if not slow
        else "Fetching Kalshi and ESPN, and driving a browser for the rest. "
        "This takes a minute or two."
    )
    with st.spinner(waiting):
        data = load(
            options["days"], options["orderbooks"], browser, api_key, ratings, token
        )

    header(data)
    notes(data.moneylines, browser, bool(api_key))

    credit_counter(data)
    board = build_board(
        data.moneylines,
        pairing=options["pairing"],
        american=options["american"],
        show_ranks=True,
        rank_label=data.moneylines.rating_short_label,
        show_composite=browser or bool(api_key),
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
        column_config=column_config(
            board,
            data.moneylines.rating_short_label,
            data.moneylines.rating_label,
        ),
        hide_index=True,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
    )
    detail(data.moneylines, selection, board)

    all_books = books_in(data.moneylines)
    book_source = (
        f"The Odds API ({len(all_books)} books)" if api_key
        else ("scraped boards" if browser else "none")
    )
    ranks = data.moneylines.rating_label
    parts = [
        "Prices: Kalshi",
        f"Books: {book_source}",
        # When the ranks are ESPN's too, saying "ESPN BPI" twice reads like a
        # mistake rather than like one source doing two jobs.
        "Ranks and model: ESPN BPI" if ranks == "ESPN BPI"
        else f"Ranks: {ranks} · Model: ESPN BPI",
    ]
    st.caption(" · ".join(parts))

