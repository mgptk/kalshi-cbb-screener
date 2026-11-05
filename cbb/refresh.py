"""One refresh: the college-basketball moneyline board, fetched live."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from core.kalshi import KalshiClient
from core.scrapers.base import BookScraper

from .books import default_books
from .espn import BpiClient, EspnClient
from .kenpom import KenPomClient
from .sections import DEFAULT_DAYS, MoneylineSection, build_moneylines


@dataclass
class Refresh:
    moneylines: MoneylineSection
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "moneylines": self.moneylines.to_dict(),
        }


def refresh_all(
    books: Optional[Sequence[BookScraper]] = None,
    days: int = DEFAULT_DAYS,
    with_orderbooks: bool = False,
    depth: int = 10,
    use_browser: bool = True,
    headless: bool = True,
    with_bpi: bool = True,
    with_kenpom: bool = True,
) -> Refresh:
    espn = EspnClient()
    kalshi = KalshiClient()
    bpi = BpiClient() if with_bpi else None
    kenpom = KenPomClient(use_browser=use_browser, headless=headless) if with_kenpom else None
    try:
        scrapers = list(books) if books is not None else default_books(use_browser, headless)
        moneylines = build_moneylines(
            kalshi,
            espn,
            scrapers,
            days=days,
            with_orderbooks=with_orderbooks,
            depth=depth,
            bpi=bpi,
            kenpom=kenpom,
        )
        return Refresh(moneylines=moneylines)
    finally:
        kalshi.close()
        espn.close()
        if bpi is not None:
            bpi.close()
