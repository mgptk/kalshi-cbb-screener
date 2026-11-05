"""The college-basketball board as a Streamlit page.

    .venv/Scripts/streamlit run streamlit_app.py

Streamlit re-runs this whole file on every widget interaction, which matters
more here than in most apps: the books cost an Odds API credit per fetch (or a
minute of browser scraping), so nothing but a button can cause another one.

There are two fetches, cached separately:

* `fetch_books` - the sportsbooks, the expensive part. Keyed by `books_token`,
  which only **Refresh all** bumps (see `_tokens`). It is `st.cache_resource`
  because the loaded book objects are reused as they are, not copied.
* `load` - Kalshi, ESPN and the board built from them. Keyed by both tokens, so
  **Refresh Kalshi** (which bumps only `kalshi_token`) re-reads the markets
  against the books already in hand and spends nothing.

Changing the pairing, the price representation or the day window re-reads
those caches. A new day window does rebuild the board, but from the same books.

Layout only lives here. The table itself is built by `cbb/view.py`, which has
no Streamlit import and is tested on its own.

A hosted copy (Streamlit Community Cloud) has no browser to drive, so the
sportsbook boards cannot be scraped there. Setting `browser_scrapes`
to false in that app's secrets makes it fetch only what a plain HTTP client can
reach - Kalshi's prices and ESPN's schedule, BPI and ranks - and say in one line which
columns are therefore missing, rather than three scrapers each reporting their
own failure. The README has the deployment steps.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, tzinfo
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import streamlit as st

from core import format as fmt

from cbb.refresh import LoadedBook, Refresh, load_books, refresh_all
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
    """Whether this host can drive a browser, and so scrape the books' boards.

    True everywhere a person runs it; a deployment that cannot says so in its
    secrets.
    """
    return bool(_secret("browser_scrapes", True))


def viewer_zone() -> Optional[tzinfo]:
    """The reader's own time zone, as their browser reports it.

    Tip-offs are formatted on the server, and a hosted server runs in UTC - so
    without this a phone in New York reads "10:00 PM UTC" for a 5 PM game.
    None (the server's own zone) only when the browser has not said, which is
    also the right answer on a laptop.
    """
    name = getattr(st.context, "timezone", None)
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log.info("unknown browser time zone %r; using the server's", name)
        return None


def odds_api_key() -> str:
    """The Odds API key, if one is configured.

    With it, a host that cannot drive a browser still gets DraftKings and
    BetMGM - the same two books, resold over plain HTTPS - and so keeps the
    composite and the Edge columns that are the point of the board.
    """
    return str(_secret("odds_api_key", "") or "")


@st.cache_resource(show_spinner=False, max_entries=2)
def fetch_books(
    browser: bool, api_key: str, books_token: int
) -> tuple[list[LoadedBook], datetime]:
    """Every book's prices, fetched once per **Refresh all**.

    `books_token` is not used in the body - it is in the signature so that
    pressing Refresh all is a different cache key and nothing else is.
    """
    books = load_books(odds_api_key=api_key or None, use_browser=browser)
    return books, datetime.now(timezone.utc)


@st.cache_data(show_spinner=False, max_entries=8)
def load(
    days: int,
    orderbooks: bool,
    browser: bool,
    api_key: str,
    books_token: int,
    kalshi_token: int,
) -> Refresh:
    """Kalshi and ESPN, fetched fresh, against the books already in hand.

    Both tokens are in the signature only to be part of the key: either button
    makes this a new fetch, and only Refresh all makes `fetch_books` one too.
    """
    books, fetched_at = fetch_books(browser, api_key, books_token)
    return refresh_all(
        books=books,
        books_fetched_at=fetched_at,
        days=days,
        with_orderbooks=orderbooks,
        use_browser=browser,
        odds_api_key=api_key or None,
    )


@st.cache_resource
def _tokens() -> dict[str, int]:
    """The refresh counters, shared by every session on this server.

    Per-session counters would start each new visit at zero - and opening the
    page on a phone is a new visit - so it would show whatever was fetched
    first, or fetch again and spend a credit. Shared, a visit shows the latest
    fetch anyone made.
    """
    return {"books": 0, "kalshi": 0}


def _bump(token: str) -> None:
    _tokens()[token] += 1


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
        paid = bool(odds_api_key())
        if st.button(
            "Refresh all",
            type="primary",
            width="stretch",
            help=(
                "Kalshi, ESPN and the sportsbooks. Spends one Odds API credit."
                if paid
                else "Kalshi, ESPN and the sportsbooks."
            ),
        ):
            _bump("books")
            _bump("kalshi")
        if st.button(
            "Refresh Kalshi",
            width="stretch",
            help=(
                "Kalshi prices and ESPN only, against the book prices already "
                "fetched. Free - no Odds API credit."
                if paid
                else "Kalshi prices and ESPN only, against the book prices "
                "already fetched. Skips the slow browser scrape."
            ),
        ):
            _bump("kalshi")

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


def _as_of(label: str, when: datetime, zone: Optional[tzinfo]) -> str:
    age = (datetime.now(timezone.utc) - when).total_seconds()
    stamp = fmt.timestamp(when, zone)
    if age > STALE_AFTER_SECONDS:
        return f"{label} {stamp} · :orange[{int(age // 60)} min old]"
    return f"{label} {stamp}"


def header(data: Refresh, zone: Optional[tzinfo], has_books: bool) -> None:
    parts = [_as_of("Kalshi as of", data.generated_at, zone)]
    # After a Kalshi-only refresh the two differ, and the Edge columns are only
    # as fresh as the older of them - so the books get their own stamp.
    if has_books and data.books_fetched_at is not None:
        parts.append(_as_of("books as of", data.books_fetched_at, zone))
    st.caption(" · ".join(parts))


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
            "Tip-off", help="ESPN's published start, in your browser's time zone"
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


def detail(
    section: Any, selection: Any, board: Board, zone: Optional[tzinfo]
) -> None:
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
        for line in game_detail(row, books_in(section), zone):
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
    zone = viewer_zone()
    books_token = _tokens()["books"]
    kalshi_token = _tokens()["kalshi"]
    # Name what is actually being fetched: a browser scrape takes a minute or
    # two, and everything else takes seconds.
    slow = browser and not api_key
    waiting = (
        "Fetching Kalshi, ESPN and the odds feed."
        if not slow
        else "Fetching Kalshi and ESPN, and driving a browser for the books. "
        "This takes a minute or two."
    )
    with st.spinner(waiting):
        data = load(
            options["days"],
            options["orderbooks"],
            browser,
            api_key,
            books_token,
            kalshi_token,
        )

    has_books = browser or bool(api_key)
    header(data, zone, has_books)
    notes(data.moneylines, browser, bool(api_key))

    credit_counter(data)
    board = build_board(
        data.moneylines,
        pairing=options["pairing"],
        american=options["american"],
        show_ranks=True,
        rank_label=data.moneylines.rating_short_label,
        show_composite=has_books,
        show_bpi=options["show_bpi"],
        show_volume=options["show_volume"],
        show_depth=any(
            c is not None and c.orderbook is not None
            for row in data.moneylines.rows
            for c in (row.game.away, row.game.home)
        ),
        zone=zone,
    )
    selection = st.dataframe(
        style(board),
        column_config=column_config(
            board,
            data.moneylines.rating_short_label,
            data.moneylines.rating_label,
        ),
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
    )
    detail(data.moneylines, selection, board, zone)

    all_books = books_in(data.moneylines)
    book_source = (
        f"The Odds API ({len(all_books)} books)" if api_key
        else ("scraped boards" if browser else "none")
    )
    parts = [
        "Prices: Kalshi",
        f"Books: {book_source}",
        "Ranks and model: ESPN BPI",
    ]
    st.caption(" · ".join(parts))

