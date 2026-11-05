"""The two sportsbooks, pointed at their college-basketball boards.

Both books render basketball with exactly the markup they use for football, so
the parsers are imported rather than rewritten — only the URLs change. That was
verified rather than assumed: DraftKings' basketball board parses to 100 games
and its away/home orientation was checked against Kalshi's own per-team
contracts across 78 games, all agreeing.

BetMGM's NCAAM board needs a longer settle than its football one. At the
football timing it rendered the page shell with zero events and looked, from
the outside, exactly like a parser that had stopped working.
"""

from __future__ import annotations

from typing import Optional

from core.scrapers.parsers import parse_betmgm_board as parse_betmgm
from core.scrapers.board import BoardLine, BoardScraper
from core.scrapers.parsers import parse_draftkings_board as parse_draftkings

DRAFTKINGS_URL = "https://sportsbook.draftkings.com/leagues/basketball/ncaab"
BETMGM_URL = "https://sports.nj.betmgm.com/en/sports/basketball-7/betting/usa-9/ncaam-264"


class DraftKingsScraper(BoardScraper):
    name = "DraftKings"
    URL = DRAFTKINGS_URL
    RENDER_WAIT_SECONDS = 10.0
    READY_SELECTOR = ".cb-market__template--2-columns"
    SCROLL_PASSES = 8

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.source = "draftkings:ncaab-board"

    def parse(self, html: str) -> dict[frozenset[str], BoardLine]:
        return parse_draftkings(html)


class BetMgmScraper(BoardScraper):
    name = "BetMGM"
    URL = BETMGM_URL
    RENDER_WAIT_SECONDS = 10.0
    READY_SELECTOR = "ms-six-pack-event"
    """The NCAAM board renders its shell first and fills the games in a beat
    later, and how long that beat lasts varies a lot run to run. A fixed sleep
    produced an empty parse on roughly every other attempt, so this one waits
    for the events themselves to exist."""
    SCROLL_PASSES = 10

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.source = "betmgm:ncaam-board"

    def parse(self, html: str) -> dict[frozenset[str], BoardLine]:
        return parse_betmgm(html)


def default_books(use_browser: bool = True, headless: bool = True) -> list[BoardScraper]:
    """Both books, in display order. Each is read from its own board."""
    return [
        DraftKingsScraper(use_browser=use_browser, headless=headless),
        BetMgmScraper(use_browser=use_browser, headless=headless),
    ]
