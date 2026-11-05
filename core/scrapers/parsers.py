"""HTML parsers for the DraftKings and BetMGM boards.

Lifted verbatim from the football project's book scrapers, which is sound
because both books render basketball with exactly the same markup — verified,
not assumed: DraftKings' basketball board parses to 100 games and its away/home
orientation was checked against Kalshi's own per-team contracts across 78
games, all agreeing.

Only the parsing came across. Each book's scraper *class* is bound to a
football board URL, and DraftKings' also reaches into ESPN's football feed to
backfill games its forward-looking board drops; `cbb/books.py` supplies the
basketball URLs instead.

DraftKings renders negative prices with U+2212 MINUS SIGN rather than an ASCII
hyphen, which `american()` normalises.

    DraftKings  div.cb-market__template--2-columns        one game
                button[data-testid="component-builder-market-button-
                       {eventId}-{marketCode}_{outcome}"]  ML = moneyline
                a[href="/event/{slug}/{eventId}"]       slug is "away-@-home"

    BetMGM      ms-six-pack-event                         one game
                .participant                              two, away first
                ms-option-group                           [spread, total, money]
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import unquote

from bs4 import BeautifulSoup

from ..teams import pair_key
from .board import BoardLine, american

_TESTID = re.compile(r"component-builder-market-button-(\d+)-(\w+?)_(\d+)$")
_EVENT_HREF = re.compile(r"^/event/([^/?]+)/(\d+)")

MONEY_GROUP_INDEX = 2
"""BetMGM's option groups per event are [spread, total, money]."""


def parse_draftkings_board(html: str) -> dict[frozenset[str], BoardLine]:
    """Pull every game off a rendered DraftKings league page.

    Games with a spread but no moneyline are kept with both prices None, so the
    caller can tell "DraftKings has this game, moneyline off" apart from
    "DraftKings doesn't list this game".
    """
    soup = BeautifulSoup(html, "html.parser")

    slugs: dict[str, tuple[str, str]] = {}
    for anchor in soup.select('a[href^="/event/"]'):
        match = _EVENT_HREF.match(anchor.get("href", ""))
        if not match:
            continue
        teams = _split_slug(match.group(1))
        if teams:
            slugs.setdefault(match.group(2), teams)

    lines: dict[frozenset[str], BoardLine] = {}
    for template in soup.select(".cb-market__template--2-columns"):
        event_id: Optional[str] = None
        moneylines: list[Optional[float]] = []
        for button in template.select("button[data-testid]"):
            match = _TESTID.match(button.get("data-testid", ""))
            if not match:
                continue
            event_id = event_id or match.group(1)
            if "ML" in match.group(2):
                moneylines.append(american(button.get_text(" ", strip=True)))
        if event_id is None:
            continue
        teams = slugs.get(event_id)
        if not teams:
            continue
        away_team, home_team = teams
        # Document order within a template is away then home, matching the
        # "away-@-home" order in the event slug.
        lines[pair_key(away_team, home_team)] = BoardLine(
            away_american=moneylines[0] if len(moneylines) > 0 else None,
            home_american=moneylines[1] if len(moneylines) > 1 else None,
            away_team=away_team,
            home_team=home_team,
        )
    return lines


def _split_slug(slug: str) -> Optional[tuple[str, str]]:
    """"oregon-state-%2540-houston" -> ("oregon state", "houston")."""
    decoded = unquote(unquote(slug))
    for separator in ("-@-", "-vs-", "-at-"):
        if separator in decoded:
            away, home = decoded.split(separator, 1)
            return away.replace("-", " ").strip(), home.replace("-", " ").strip()
    return None


def parse_betmgm_board(html: str) -> dict[frozenset[str], BoardLine]:
    soup = BeautifulSoup(html, "html.parser")
    lines: dict[frozenset[str], BoardLine] = {}

    for event in soup.select("ms-six-pack-event"):
        teams = [
            re.sub(r"\s+", " ", p.get_text(" ", strip=True))
            for p in event.select(".participant")[:2]
        ]
        if len(teams) != 2 or not all(teams):
            continue
        away_team, home_team = teams

        groups = event.select("ms-option-group")
        away_ml = home_ml = None
        if len(groups) > MONEY_GROUP_INDEX:
            options = groups[MONEY_GROUP_INDEX].select("ms-option")
            prices = [_first_price(o) for o in options]
            # Two options means both sides are priced; one means BetMGM has
            # taken the moneyline off the other side.
            if len(prices) >= 2:
                away_ml, home_ml = prices[0], prices[1]
            elif len(prices) == 1:
                away_ml = prices[0]

        lines[pair_key(away_team, home_team)] = BoardLine(
            away_american=away_ml,
            home_american=home_ml,
            away_team=away_team,
            home_team=home_team,
        )
    return lines


def _first_price(option: object) -> Optional[float]:
    text = re.sub(r"\s+", " ", option.get_text(" ", strip=True))  # type: ignore[attr-defined]
    for token in text.split():
        value = american(token)
        if value is not None:
            return value
    return None
