"""Offline tests for the college-basketball scraper.

Run with: python -m tests.test_cbb

Covers what actually differs from football — BPI's renamed stats, Eastern-day
bucketing, the basketball rules text — rather than re-testing the shared
machinery, which `test_scrapers.py` already pins.
"""

from __future__ import annotations

import sys
import traceback
from datetime import date, datetime, timezone
from typing import Callable

from cbb.espn import BpiClient, BpiPrediction, _parse_event, _parse_team, _prob
from cbb.kenpom import KenPomClient, parse_ratings
from cbb.espn import EspnGame
from cbb.sections import (
    GAME_SERIES,
    KALSHI_OCCURRENCE_LEAD,
    MoneylineRow,
    MoneylineSection,
    _attach_espn_tipoffs,
    _participants,
    _slate_day,
)
from cbb.view import Pairing, build_board, favourite_side
from core.models import BookQuote, KalshiContract, KalshiGame, QuoteStatus
from core.kalshi import parse_contract
from core.teams import normalize

# --- fixtures: real payload slices, captured 2026-11-01 ---------------------

# Note the stat names: BPI renames every one of FPI's. Reading football's
# `gameprojection` off this returns None, silently.
BPI_TEAM = {
    "stats": [
        {"name": "matchupquality", "value": 48.3858},
        {"name": "teampredwinpct", "value": 98.97},
        {"name": "teampredmov", "value": 28.521},
        {"name": "opponentpredwinpct", "value": 1.03},
    ]
}

KALSHI_MARKET = {
    "ticker": "KXNCAAMBGAME-26NOV05CHATPSU-PSU",
    "event_ticker": "KXNCAAMBGAME-26NOV05CHATPSU",
    "title": "Penn St. wins",
    "yes_sub_title": "Penn St.",
    "no_sub_title": "Penn St.",
    "status": "active",
    "yes_bid_dollars": "0.9100",
    "yes_ask_dollars": "0.9400",
    "no_bid_dollars": "0.0600",
    "no_ask_dollars": "0.0900",
    "occurrence_datetime": "2026-11-06T04:00:00Z",
    "rules_primary": (
        "If Penn St. wins the Chattanooga vs Penn St. men's college basketball "
        "game originally scheduled for Nov 5, 2026, then the market resolves to Yes."
    ),
}

ESPN_EVENT = {
    "id": "401906848",
    "shortName": "WOF @ TENN",
    "date": "2026-11-03T23:00Z",
    "competitions": [
        {
            "neutralSite": False,
            "competitors": [
                {"homeAway": "home", "team": {"location": "Tennessee", "id": "2633"}},
                {"homeAway": "away", "team": {"location": "Wofford", "id": "2747"}},
            ],
        }
    ],
}


# Two rows of the ratings table as kenpom.com serves them, header included.
# The seed span inside a team cell is what a tournament team carries there.
KENPOM_HTML = """
<table id="ratings-table">
<thead><tr class="thead2"><th>Rk</th><th>Team</th><th>Conf</th><th>W-L</th><th>NetRtg</th></tr></thead>
<tbody>
<tr><td class="hard_left">1</td><td class="next_left"><a href="team.php?team=Duke">Duke</a>
    <span class="seed">1</span></td><td class="conf"><a href="conf.php?c=ACC">ACC</a></td>
    <td class="wl">1-0</td><td>+34.26</td><td class="td-left divide">123.5</td></tr>
<tr><td class="hard_left">160</td><td class="next_left"><a href="team.php?team=CSUN">CSUN</a></td>
    <td class="conf"><a href="conf.php?c=BW">BW</a></td><td class="wl">0-0</td><td>-0.91</td>
    <td class="td-left divide">99.1</td></tr>
</tbody>
</table>
"""


# --- tests ------------------------------------------------------------------


def test_series_is_mens_basketball_not_baseball() -> None:
    # KXNCAABBGAME is college *baseball* and KXNCAABGAME has no open markets;
    # picking either by sight would look plausible and return nothing useful.
    assert GAME_SERIES == "KXNCAAMBGAME"


def test_bpi_uses_its_own_stat_names() -> None:
    team = _parse_team(BPI_TEAM)
    assert team.win_prob == 98.97
    assert team.opponent_win_prob == 1.03
    assert team.margin == 28.521
    assert team.matchup_quality == 48.3858
    assert _prob(98.97) == 0.9897
    assert _prob(None) is None


def test_bpi_reads_football_stat_names_as_nothing() -> None:
    # The failure this guards against: football's names on a basketball payload
    # parse cleanly and yield an empty prediction rather than an error.
    football_shaped = {"stats": [{"name": "gameprojection", "value": 98.15}]}
    assert _parse_team(football_shaped).win_prob is None


def test_bpi_orientation_and_margin_follow_the_away_team() -> None:
    client = BpiClient()
    client._cache["E"] = {
        "AWAY": _parse_team(
            {
                "stats": [
                    {"name": "teampredwinpct", "value": 31.93},
                    {"name": "opponentpredwinpct", "value": 68.07},
                    {"name": "teampredmov", "value": -4.73},
                ]
            }
        ),
        "HOME": _parse_team(
            {
                "stats": [
                    {"name": "teampredwinpct", "value": 68.07},
                    {"name": "opponentpredwinpct", "value": 31.93},
                    {"name": "teampredmov", "value": 4.73},
                ]
            }
        ),
    }
    p = client.prediction("E", "AWAY", "HOME")
    assert round(p.away_win_prob, 4) == 0.3193
    assert round(p.home_win_prob, 4) == 0.6807
    assert p.predicted_margin == -4.73  # negative favours home


def test_bpi_fills_both_sides_from_one_team() -> None:
    # Each team's document carries its opponent's probability too, so a single
    # side is enough to populate the row.
    client = BpiClient()
    client._cache["E"] = {"HOME": _parse_team(BPI_TEAM)}
    p = client.prediction("E", "AWAY", "HOME")
    assert round(p.home_win_prob, 4) == 0.9897
    assert round(p.away_win_prob, 4) == 0.0103


def test_bpi_of_an_unknown_event_is_empty() -> None:
    assert BpiClient().prediction("nope", "1", "2").available is False
    assert BpiPrediction().available is False


def test_slate_days_are_eastern_not_utc() -> None:
    # 2026-11-06T04:00Z is 11pm Eastern on Nov 5 — the day Kalshi's own ticker
    # (…26NOV05…) and ESPN's `dates=` parameter both file it under.
    late = datetime(2026, 11, 6, 4, 0, tzinfo=timezone.utc)
    assert _slate_day(late).isoformat() == "2026-11-05"
    assert late.date().isoformat() == "2026-11-06"  # what UTC bucketing would say

    afternoon = datetime(2026, 11, 3, 23, 0, tzinfo=timezone.utc)
    assert _slate_day(afternoon).isoformat() == "2026-11-03"


def test_participants_come_from_the_basketball_rules_text() -> None:
    contract = parse_contract(KALSHI_MARKET)
    away, home = _participants([KALSHI_MARKET], [contract])
    assert (away, home) == ("Chattanooga", "Penn St.")


def test_kalshi_ask_is_what_the_row_prices() -> None:
    contract = parse_contract(KALSHI_MARKET)
    assert contract.yes_ask == 0.94
    assert contract.no_ask == 0.09
    assert contract.yes_mid == 0.925  # present, but not what gets displayed


def test_espn_event_parses_teams_and_ids() -> None:
    game = _parse_event(ESPN_EVENT)
    assert game is not None
    assert (game.away_team, game.home_team) == ("Wofford", "Tennessee")
    assert (game.away_team_id, game.home_team_id) == ("2747", "2633")
    assert game.tipoff is not None and game.tipoff.hour == 23


def test_basketball_school_aliases_resolve() -> None:
    for kalshi_name, espn_name in [
        ("San Jose St.", "San José State"),   # accent folding, not an alias
        ("Grambling St.", "Grambling"),
        ("Central Connecticut St.", "Central Connecticut"),
        ("New Orleans", "LSU New Orleans"),
        ("Seattle", "Seattle U"),
        ("FDU", "Fairleigh Dickinson"),
    ]:
        assert normalize(kalshi_name) == normalize(espn_name), kalshi_name
    # The reason there is no general "drop State" rule: these are two schools.
    assert normalize("Illinois") != normalize("Illinois State")


def test_kenpom_rows_parse_without_their_seeds() -> None:
    teams = parse_ratings(KENPOM_HTML)
    assert [t.rank for t in teams] == [1, 160]
    # The tournament seed sits inside the team cell; taking the cell's whole
    # text would name the school "Duke 1".
    assert teams[0].team == "Duke"
    assert (teams[0].conference, teams[0].record) == ("ACC", "1-0")
    assert teams[0].net_rating == 34.26
    assert teams[1].net_rating == -0.91


def test_kenpom_ranks_resolve_through_the_alias_table() -> None:
    client = KenPomClient()
    client._adopt(parse_ratings(KENPOM_HTML))
    # KenPom is the only feed here that writes Northridge as CSUN.
    assert client.rank("Cal State Northridge").rank == 160
    assert client.rank("Duke").rank == 1
    assert client.rank("Bethesda") is None  # NAIA: genuinely unranked


def test_kenpom_without_a_table_reports_nothing_rather_than_raising() -> None:
    # Cloudflare's interstitial is a valid HTML document with no table in it.
    assert parse_ratings("<html><body>Just a moment...</body></html>") == []


def test_kalshi_occurrence_runs_three_hours_past_the_tip_off() -> None:
    # Measured across a full 16-game slate: every Kalshi occurrence sat exactly
    # three hours after ESPN's scheduled start. It marks when the market can
    # settle, not when the ball goes up.
    occurrence = datetime(2026, 11, 6, 5, 0, tzinfo=timezone.utc)
    estimated = occurrence - KALSHI_OCCURRENCE_LEAD
    assert estimated == datetime(2026, 11, 6, 2, 0, tzinfo=timezone.utc)

    # A 9pm Eastern tip-off. Taken at face value Kalshi's own time files it
    # under the *next* Eastern day, which then asks ESPN for the wrong date and
    # costs the game its match - and drops it from a one-day window entirely.
    assert _slate_day(estimated) == date(2026, 11, 5)
    assert _slate_day(occurrence) == date(2026, 11, 6)


def test_espn_supplies_the_tip_off_whenever_it_has_the_game() -> None:
    published = datetime(2026, 11, 3, 23, 0, tzinfo=timezone.utc)
    row = MoneylineRow(
        game=KalshiGame(
            event_ticker="KXNCAAMBGAME-26NOV03WOFTENN",
            kickoff=datetime(2026, 11, 3, 23, 30, tzinfo=timezone.utc),  # the estimate
            away_team="Wofford",
            home_team="Tennessee",
        )
    )
    espn_game = EspnGame(
        event_id="401906848",
        short_name="WOF @ TENN",
        tipoff=published,
        away_team="Wofford",
        home_team="Tennessee",
    )
    _attach_espn_tipoffs([row], {espn_game.key: espn_game})
    assert row.game.kickoff == published
    assert row.tipoff_from_espn


def test_an_unmatched_game_keeps_its_estimate_and_says_so() -> None:
    estimate = datetime(2026, 11, 3, 23, 30, tzinfo=timezone.utc)
    row = MoneylineRow(
        game=KalshiGame(
            event_ticker="KXNCAAMBGAME-26NOV03AAABBB",
            kickoff=estimate,
            away_team="Bethesda",
            home_team="Pacific",
        )
    )
    _attach_espn_tipoffs([row], {})
    assert row.game.kickoff == estimate
    assert not row.tipoff_from_espn  # the page marks this one "approx"


def _contract(ticker: str, ask: float, volume: float = 1000.0) -> KalshiContract:
    return KalshiContract(
        ticker=ticker, event_ticker=ticker[:-4], title=ticker, yes_label="y",
        no_label="n", status="active", yes_bid=ask - 0.02, yes_ask=ask,
        no_bid=1 - ask - 0.02, no_ask=1 - ask, last_price=ask, volume=volume,
    )


def _board_row(away_ask: float, home_ask: float, books: dict) -> MoneylineRow:
    """One game: the away side is the underdog unless a test says otherwise."""
    game = KalshiGame(
        event_ticker="KXNCAAMBGAME-26NOV03AWAYHOME",
        kickoff=datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc),
        away_team="Wofford",
        home_team="Tennessee",
        away=_contract("KXNCAAMBGAME-26NOV03AWAYHOME-AWY", away_ask),
        home=_contract("KXNCAAMBGAME-26NOV03AWAYHOME-HOM", home_ask),
    )
    return MoneylineRow(
        game=game, books=books, composite_away=0.05, composite_home=0.95,
        matched_to_book_slate=True, tipoff_from_espn=True,
    )


def _section(rows: list) -> MoneylineSection:
    return MoneylineSection(rows=rows, book_notes={"DraftKings": None})


def test_board_pairs_the_favourite_first_without_touching_away_home() -> None:
    row = _board_row(0.08, 0.94, {})
    assert favourite_side(row) == "home"

    fav = build_board(_section([row]), books=[], pairing=Pairing.FAV_DOG, show_books=False)
    assert len(fav.frame) == 1  # one row per game, not three
    assert fav.frame.iloc[0]["Fav"] == "Tennessee"
    assert fav.frame.iloc[0]["Dog"] == "Wofford"
    # The rank is its own numeric column, never a prefix on the name: as text
    # "100 Duke" sorts before "9 Yale".
    assert list(fav.frame.columns)[:5] == ["Tip-off", "KP Fav", "KP Dog", "Fav", "Dog"]

    plain = build_board(
        _section([row]), books=[], pairing=Pairing.AWAY_HOME, show_books=False
    )
    assert plain.frame.iloc[0]["Away"] == "Wofford"
    assert plain.frame.iloc[0]["Home"] == "Tennessee"


def test_board_keeps_the_edges_numeric_when_prices_turn_american() -> None:
    # Edge is a difference in percentage points, so American odds do not apply
    # to it - which is what keeps the sort that matters numeric in both modes.
    row = _board_row(0.08, 0.94, {})
    board = build_board(
        _section([row]), books=[], pairing=Pairing.FAV_DOG, american=True, show_books=False
    )
    cells = board.frame.iloc[0]
    assert isinstance(cells["Edge Fav"], float)
    assert cells["Edge Fav"] == 1.0  # composite 95.0 - ask 94.0
    assert isinstance(cells["Kalshi Fav"], str) and cells["Kalshi Fav"].startswith("-")
    assert "Edge Fav" in board.edge_columns
    assert "Kalshi Fav" not in board.percent_columns


def test_board_says_which_kind_of_blank_each_book_cell_is() -> None:
    books = {
        "DraftKings": BookQuote(
            book="DraftKings", status=QuoteStatus.OK, away_american=900,
            home_american=-1500, away_prob=0.1, home_prob=0.9,
        ),
        "BetMGM": BookQuote(book="BetMGM", status=QuoteStatus.MONEYLINE_OFF),
        "Bovada": BookQuote.unavailable("Bovada", note="scrape failed"),
        "Pinnacle": BookQuote(book="Pinnacle", status=QuoteStatus.NOT_ON_BOARD),
    }
    board = build_board(
        _section([_board_row(0.08, 0.94, books)]),
        books=list(books),
        pairing=Pairing.AWAY_HOME,
    )
    cells = board.frame.iloc[0]
    assert cells["DraftKings Away"] == "10.0"
    assert cells["BetMGM Away"] == "off"
    assert cells["Bovada Away"] == "x"  # the only one that means something broke
    assert cells["Pinnacle Away"] == "--"


def main() -> int:
    tests: list[Callable[[], None]] = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
        except Exception:  # noqa: BLE001 - this is the test runner
            failures += 1
            print(f"  FAIL  {test.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
