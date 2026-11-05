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
from zoneinfo import ZoneInfo
from typing import Callable

from cbb.espn import BpiClient, BpiPrediction, _parse_event, _parse_team, _prob
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
from cbb.espnrank import EspnRatings, parse_power_index
from cbb.oddsapi import OddsApiClient, _candidates, parse_events
from cbb.refresh import LoadedBook
from cbb.view import Pairing, build_board, favourite_side, game_detail
from core.models import BookQuote, KalshiContract, KalshiGame, Matchup, QuoteStatus
from core.kalshi import parse_contract
from core import format as fmt
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


# One game as The Odds API returns it, with the mascots it attaches to every
# school and a second book that has no moneyline up.
ODDS_API_EVENTS = [
    {
        "id": "abc123",
        "sport_key": "basketball_ncaab",
        "commence_time": "2026-11-06T00:00:00Z",
        "home_team": "Tennessee Volunteers",
        "away_team": "Wofford Terriers",
        "bookmakers": [
            {
                "key": "draftkings",
                "title": "DraftKings",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Wofford Terriers", "price": 900},
                            {"name": "Tennessee Volunteers", "price": -1500},
                        ],
                    }
                ],
            },
            {
                "key": "betmgm",
                "title": "BetMGM",
                "markets": [
                    {"key": "h2h", "outcomes": [{"name": "Tennessee Volunteers", "price": -1400}]}
                ],
            },
        ],
    }
]


# ESPN's power index, cut down. The column names live ONCE at the top of the
# document; each team carries only values, in that order.
POWER_INDEX = {
    "categories": [
        {
            "name": "bpi",
            "names": ["bpi", "bpirank", "bpisevendaychangerank", "bpioffense",
                      "bpidefense", "chancewinconfortie", "sosremrank", "wins", "losses"],
        }
    ],
    "teams": [
        {
            "team": {"id": "150", "displayName": "Duke Blue Devils",
                     "shortDisplayName": "Duke"},
            "categories": [
                {"name": "bpi",
                 "values": [22.7509, 1.0, None, 11.12, 11.63, 86.43, 28.0, 1.0, 0.0]},
                {"name": "resume", "values": []},
            ],
        },
        {
            "team": {"id": "2633", "displayName": "Tennessee Volunteers",
                     "shortDisplayName": "Tennessee"},
            "categories": [
                {"name": "bpi",
                 "values": [19.124, 4.0, None, 9.0, 10.1, 50.0, 30.0, 0.0, 1.0]},
            ],
        },
        {"team": {"id": "1", "shortDisplayName": "No Rank"}, "categories": []},
    ],
}


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

    fav = build_board(_section([row]), pairing=Pairing.FAV_DOG)
    assert len(fav.frame) == 1  # one row per game, not three
    assert fav.frame.iloc[0]["Fav"] == "Tennessee"
    assert fav.frame.iloc[0]["Dog"] == "Wofford"
    # The rank is its own numeric column, never a prefix on the name: as text
    # "100 Duke" sorts before "9 Yale".
    assert list(fav.frame.columns)[:5] == ["Tip-off", "BPI# Fav", "BPI# Dog", "Fav", "Dog"]

    plain = build_board(_section([row]), pairing=Pairing.AWAY_HOME)
    assert plain.frame.iloc[0]["Away"] == "Wofford"
    assert plain.frame.iloc[0]["Home"] == "Tennessee"


def test_board_keeps_the_edges_numeric_when_prices_turn_american() -> None:
    # Edge is a difference in percentage points, so American odds do not apply
    # to it - which is what keeps the sort that matters numeric in both modes.
    row = _board_row(0.08, 0.94, {})
    board = build_board(_section([row]), pairing=Pairing.FAV_DOG, american=True)
    cells = board.frame.iloc[0]
    assert isinstance(cells["Edge Fav"], float)
    assert cells["Edge Fav"] == 1.0  # composite 95.0 - ask 94.0
    assert isinstance(cells["Kalshi Fav"], str) and cells["Kalshi Fav"].startswith("-")
    assert "Edge Fav" in board.edge_columns
    assert "Kalshi Fav" not in board.percent_columns


def test_the_books_stay_out_of_the_table_and_live_in_the_detail() -> None:
    books = {
        "DraftKings": BookQuote(
            book="DraftKings", status=QuoteStatus.OK, away_american=900,
            home_american=-1500, away_prob=0.1, home_prob=0.9,
        ),
        "BetMGM": BookQuote(book="BetMGM", status=QuoteStatus.MONEYLINE_OFF),
        "Bovada": BookQuote.unavailable("Bovada", note="scrape failed"),
        "Pinnacle": BookQuote(book="Pinnacle", status=QuoteStatus.NOT_ON_BOARD),
    }
    row = _board_row(0.08, 0.94, books)
    board = build_board(_section([row]), pairing=Pairing.AWAY_HOME)

    # Eight books would be sixteen columns nobody reads across. The table
    # carries the composite and how many books it had; the books themselves
    # are in the panel for the one game a reader picks.
    assert not [c for c in board.frame.columns if c.startswith(tuple(books))]
    assert board.frame.iloc[0]["Bks"] == 1  # only DraftKings priced it

    detail = "\n".join(game_detail(row, list(books)))
    assert "+900" in detail and "-1500" in detail
    assert "`off`" in detail          # the book posts no moneyline
    assert "`x`" in detail            # we could not read it: the broken one
    assert "scrape failed" in detail
    assert "`--`" in detail           # not on that book's board


def _odds_client(events: object = None) -> OddsApiClient:
    client = OddsApiClient(api_key="test")
    games = parse_events(events if events is not None else ODDS_API_EVENTS)
    client._index(games)
    client._survey(games)
    client.loaded_ok = True
    return client


def test_odds_api_matches_schools_through_their_mascots() -> None:
    # No other feed here spells a school "Wofford Terriers".
    client = _odds_client()
    matchup = Matchup(away_team="Wofford", home_team="Tennessee", kickoff=None)
    quote = client.quote("draftkings", matchup)
    assert quote.status is QuoteStatus.OK
    assert (quote.away_american, quote.home_american) == (900, -1500)
    assert quote.away_prob is not None and quote.away_prob < 0.1  # devigged


def test_odds_api_keeps_each_price_on_its_own_side() -> None:
    # The sides are matched by school, not by string equality: comparing
    # "Wofford Terriers" to "Wofford" says they differ, which silently hands
    # the favourite's price to the underdog.
    client = _odds_client()
    straight = client.quote(
        "draftkings", Matchup(away_team="Wofford", home_team="Tennessee", kickoff=None)
    )
    assert (straight.away_american, straight.home_american) == (900, -1500)
    assert straight.away_prob < straight.home_prob

    # And if our feed has the game the other way round, the prices follow.
    flipped = client.quote(
        "draftkings", Matchup(away_team="Tennessee", home_team="Wofford", kickoff=None)
    )
    assert (flipped.away_american, flipped.home_american) == (-1500, 900)


def test_odds_api_keeps_the_blank_reasons_apart() -> None:
    client = _odds_client()
    here = Matchup(away_team="Wofford", home_team="Tennessee", kickoff=None)
    # A book with only one side has pulled the moneyline; that is the book's
    # answer, not a failure.
    assert client.quote("betmgm", here).status is QuoteStatus.MONEYLINE_OFF
    # A game the API does not carry at all.
    away = Matchup(away_team="Duke", home_team="Kansas", kickoff=None)
    assert client.quote("draftkings", away).status is QuoteStatus.NOT_ON_BOARD
    # And a client that never loaded is a failure, not a missing line.
    dead = OddsApiClient(api_key="")
    dead.load()
    assert dead.quote("draftkings", here).status is QuoteStatus.UNAVAILABLE


def test_odds_api_drops_a_pairing_two_games_would_claim() -> None:
    # Both Miamis on one slate: "Miami (OH) RedHawks" and "Miami Hurricanes"
    # both shorten to "miami", so the shortened pairings are ambiguous and
    # must be thrown away rather than matched to the wrong game.
    events = [
        {
            "home_team": "Miami Hurricanes",
            "away_team": "Duke Blue Devils",
            "bookmakers": [
                {
                    "key": "draftkings",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Duke Blue Devils", "price": -200},
                                {"name": "Miami Hurricanes", "price": 170},
                            ],
                        }
                    ],
                }
            ],
        },
        {
            "home_team": "Miami (OH) RedHawks",
            "away_team": "Duke Blue Devils",
            "bookmakers": [],
        },
    ]
    client = _odds_client(events)
    # "duke" + "miami" is claimed by both, so neither gets it.
    assert client.game_for(Matchup(away_team="Duke", home_team="Miami", kickoff=None)) is None
    # The full spelling is still unambiguous.
    assert client.game_for(Matchup(away_team="Duke Blue Devils", home_team="Miami Hurricanes", kickoff=None))


def test_odds_api_candidates_go_back_through_the_alias_table() -> None:
    # The alias table is applied at the end of normalize(), so a truncation
    # that only becomes an alias once its mascot is gone has to be normalised
    # again. Without this, "SMU Mustangs" stops at "smu" while our own feed
    # says "southern methodist", and the two can never meet - which silently
    # cost four games their prices on a live slate.
    assert normalize("SMU") in _candidates("SMU Mustangs")
    assert normalize("LIU") in _candidates("LIU Sharks")
    assert normalize("Loyola Chicago") in _candidates("Loyola (Chi) Ramblers")


def test_odds_api_credits_reach_the_book_that_reports_them() -> None:
    # The counter in the sidebar reads this off whichever book came from the
    # API, so the client's header value has to survive the trip.
    from cbb.oddsapi import OddsApiBook

    client = _odds_client()
    client.credits_remaining = 487
    book = OddsApiBook("draftkings", "DraftKings", client)
    assert book.credits_remaining == 487
    # A scraped book has no such attribute, which is how refresh_all tells
    # "no API in play" from "no credits left".
    assert getattr(object(), "credits_remaining", None) is None


def test_odds_api_takes_every_book_the_region_carries() -> None:
    # One credit buys the whole region, so the composite should be over all
    # of them, ordered by how much of the slate each one priced.
    events = [
        {
            "home_team": "Tennessee Volunteers",
            "away_team": "Wofford Terriers",
            "bookmakers": [
                {
                    "key": "fanduel",
                    "title": "FanDuel",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Wofford Terriers", "price": 850},
                                {"name": "Tennessee Volunteers", "price": -1400},
                            ],
                        }
                    ],
                },
                {
                    "key": "bovada",
                    "title": "Bovada",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Wofford Terriers", "price": 800},
                                {"name": "Tennessee Volunteers", "price": -1300},
                            ],
                        }
                    ],
                },
            ],
        },
        {
            "home_team": "Duke Blue Devils",
            "away_team": "Kansas Jayhawks",
            "bookmakers": [
                {
                    "key": "fanduel",
                    "title": "FanDuel",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Kansas Jayhawks", "price": 120},
                                {"name": "Duke Blue Devils", "price": -140},
                            ],
                        }
                    ],
                }
            ],
        },
    ]
    client = _odds_client(events)
    # FanDuel priced both games, Bovada one, so FanDuel's column comes first.
    assert client.book_keys() == ["fanduel", "bovada"]
    assert client.titles["bovada"] == "Bovada"
    assert client.coverage == {"fanduel": 2, "bovada": 1}


def test_espn_power_index_reads_its_column_names_from_the_document() -> None:
    # Each team's category carries values but no names - reading the names
    # off the team returns nothing at all, which is how this first shipped.
    teams = parse_power_index(POWER_INDEX)
    assert [t.rank for t in teams] == [1, 4]  # the team with no bpi drops out
    assert teams[0].team == "Duke"
    assert teams[0].net_rating == 22.75
    assert teams[0].record == "1-0"
    assert teams[1].record == "0-1"


def test_espn_ranks_match_on_team_id_not_spelling() -> None:
    source = EspnRatings()
    source._adopt(parse_power_index(POWER_INDEX))
    # The id is exact, which is the point: no alias can be wrong.
    assert source.rank_by_espn_id("2633").rank == 4
    assert source.rank_by_espn_id("9999") is None
    assert source.rank_by_espn_id(None) is None
    # Names still work for a game ESPN did not match.
    assert source.rank("Duke").rank == 1


def test_tip_offs_read_in_the_zone_the_reader_is_in() -> None:
    # A hosted server runs in UTC. Without the reader's zone, a 7 PM Eastern
    # tip-off reads "Fri Nov 6, 12:00 AM UTC" - the wrong day as well.
    tip = datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)
    assert fmt.kickoff(tip, ZoneInfo("America/New_York")) == "Thu Nov 5, 7:00 PM EST"
    assert fmt.kickoff(tip, ZoneInfo("America/Los_Angeles")) == "Thu Nov 5, 4:00 PM PST"
    assert fmt.kickoff(tip, timezone.utc) == "Fri Nov 6, 12:00 AM UTC"
    assert fmt.kickoff(None, timezone.utc) == "TBD"
    assert fmt.timestamp(tip, ZoneInfo("America/New_York")) == "Nov 05, 07:00:00 PM EST"


def test_the_board_and_the_detail_both_use_the_readers_zone() -> None:
    row = _board_row(0.08, 0.94, {})  # tips at 00:00 UTC on Nov 4
    eastern = ZoneInfo("America/New_York")
    board = build_board(_section([row]), zone=eastern)
    assert board.frame.iloc[0]["Tip-off"] == "Tue Nov 3, 7:00 PM EST"
    assert any("Tue Nov 3, 7:00 PM EST" in line for line in game_detail(row, [], eastern))


class _CountingBook:
    """A book that counts its fetches, the way an Odds API credit would."""

    name = "DraftKings"
    note = None
    credits_remaining = 480

    def __init__(self) -> None:
        self.loads = 0

    def load(self) -> None:
        self.loads += 1

    def quote(self, matchup: Matchup) -> BookQuote:
        return BookQuote.unavailable(self.name, "test")


def test_a_kalshi_only_refresh_reuses_the_books_without_fetching_them() -> None:
    book = _CountingBook()
    book.load()  # the one fetch Refresh all paid for
    kept = LoadedBook(book)
    kept.load()  # what build_moneylines does on every refresh
    kept.load()
    assert book.loads == 1
    # Everything else is the book's own, so the board cannot tell the
    # difference - and the credit counter still reads.
    assert kept.name == "DraftKings"
    assert kept.credits_remaining == 480
    assert kept.quote(None).status is QuoteStatus.UNAVAILABLE  # type: ignore[arg-type]


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
