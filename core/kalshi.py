"""Kalshi market-data client.

Read-only market data on the elections API is public: no API key, no request
signing. That was an open question in the v1 spec; it is answered here, and
`REQUIRES_AUTH` documents it for anyone who reads this later. Only order
placement and portfolio endpoints need credentials.

Field names confirmed against the live API on 2026-09-05:

  status              "active" while tradable (not "open"/"settled")
  *_dollars           prices as decimal strings, 0.0000-1.0000
  *_fp                sizes/volumes as decimal strings
  floor_strike        numeric threshold on greater_or_equal markets (win totals)
  event_ticker        groups the contracts that belong to one game/team/week
"""

from __future__ import annotations

import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import httpx

from .models import KalshiContract, OrderBook, PriceLevel

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

REQUIRES_AUTH = False
"""Market data, including full order books, is served unauthenticated."""

GAME_SERIES = "KXNCAAFGAME"
AP_RANK_SERIES = "KXNCAAFTOPAPRANK"
WINS_SERIES = "KXNCAAFWINS"

ACTIVE_STATUSES = frozenset({"active"})
"""Statuses that count as "still tradable" for the lowest-open-rung logic."""


def _to_float(value: Any) -> Optional[float]:
    """Kalshi sends numbers as decimal strings; empty means absent."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_timestamp(value: Any) -> Optional[datetime]:
    """Kalshi timestamps are RFC 3339 with a Z suffix; returns UTC-aware."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


class KalshiError(RuntimeError):
    pass


class KalshiClient:
    """Thin, retrying wrapper over the public market-data endpoints."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        max_retries: int = 3,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self._client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"Accept": "application/json", "User-Agent": "ncaaf-dashboard/0.1"},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "KalshiClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- transport ---------------------------------------------------------

    def _get(self, path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries):
            retry_after: Optional[float] = None
            try:
                response = self._client.get(url, params=params)
                if response.status_code == 429 or response.status_code >= 500:
                    retry_after = _retry_after(response)
                    raise httpx.HTTPStatusError(
                        f"{response.status_code} from {url}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt < self.max_retries - 1:
                    # Exponential backoff with jitter, so a burst of concurrent
                    # order-book calls that trips the rate limit doesn't retry
                    # in lockstep and trip it again.
                    backoff = retry_after or (1.0 * (2**attempt) + random.uniform(0, 0.4))
                    log.debug("retrying %s in %.1fs after %s", url, backoff, exc)
                    time.sleep(backoff)
        raise KalshiError(f"GET {url} failed after {self.max_retries} attempts: {last_error}")

    # -- endpoints ---------------------------------------------------------

    def get_series(self, series_ticker: str) -> dict[str, Any]:
        return self._get(f"/series/{series_ticker}")["series"]

    def list_markets(
        self,
        series_ticker: str,
        status: Optional[str] = "open",
        page_size: int = 1000,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """Every market in a series, following the cursor to the end.

        `status="open"` is the API's filter name for markets accepting orders;
        the markets themselves come back with `status: "active"`.
        """
        markets: list[dict[str, Any]] = []
        cursor: Optional[str] = None
        for _ in range(max_pages):
            params: dict[str, Any] = {"series_ticker": series_ticker, "limit": page_size}
            if status:
                params["status"] = status
            if cursor:
                params["cursor"] = cursor
            payload = self._get("/markets", params)
            page = payload.get("markets") or []
            markets.extend(page)
            cursor = payload.get("cursor")
            if not cursor or not page:
                break
        else:
            log.warning("hit max_pages=%s paging %s; results may be truncated", max_pages, series_ticker)
        return markets

    def get_orderbook(self, ticker: str, depth: int = 10) -> OrderBook:
        payload = self._get(f"/markets/{ticker}/orderbook", {"depth": depth})
        book = payload.get("orderbook_fp") or payload.get("orderbook") or {}
        return OrderBook(
            yes=_levels(book.get("yes_dollars") or book.get("yes")),
            no=_levels(book.get("no_dollars") or book.get("no")),
        )

    def attach_orderbooks(
        self,
        contracts: Iterable[KalshiContract],
        depth: int = 10,
        workers: int = 4,
    ) -> None:
        """Fetch books for many contracts concurrently, in place.

        One HTTP call per contract, so this is the expensive part of a refresh —
        callers gate it behind a flag. A failure on a single contract leaves its
        `orderbook` as None rather than sinking the whole refresh.
        """
        targets = list(contracts)
        if not targets:
            return

        def fetch(contract: KalshiContract) -> None:
            try:
                contract.orderbook = self.get_orderbook(contract.ticker, depth=depth)
            except KalshiError as exc:
                log.warning("orderbook for %s unavailable: %s", contract.ticker, exc)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(fetch, targets))


def _retry_after(response: httpx.Response) -> Optional[float]:
    """Honour the server's own backoff hint when it sends one."""
    raw = response.headers.get("Retry-After")
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def _levels(raw: Any) -> list[PriceLevel]:
    """Kalshi returns [[price, size], ...] ascending; we want best price first."""
    if not raw:
        return []
    levels = [PriceLevel(price=float(price), size=float(size)) for price, size in raw]
    levels.sort(key=lambda lvl: lvl.price, reverse=True)
    return levels


def parse_contract(raw: dict[str, Any]) -> KalshiContract:
    """Turn one raw market payload into a KalshiContract."""
    return KalshiContract(
        ticker=raw["ticker"],
        event_ticker=raw.get("event_ticker", ""),
        title=raw.get("title", ""),
        yes_label=raw.get("yes_sub_title") or "",
        no_label=raw.get("no_sub_title") or "",
        status=raw.get("status", ""),
        yes_bid=_to_float(raw.get("yes_bid_dollars")),
        yes_ask=_to_float(raw.get("yes_ask_dollars")),
        no_bid=_to_float(raw.get("no_bid_dollars")),
        no_ask=_to_float(raw.get("no_ask_dollars")),
        last_price=_to_float(raw.get("last_price_dollars")),
        volume=_to_float(raw.get("volume_fp")) or 0.0,
        volume_24h=_to_float(raw.get("volume_24h_fp")) or 0.0,
        open_interest=_to_float(raw.get("open_interest_fp")) or 0.0,
        liquidity=_to_float(raw.get("liquidity_dollars")) or 0.0,
        yes_bid_size=_to_float(raw.get("yes_bid_size_fp")) or 0.0,
        yes_ask_size=_to_float(raw.get("yes_ask_size_fp")) or 0.0,
        close_time=parse_timestamp(raw.get("close_time")),
        occurrence=parse_timestamp(raw.get("occurrence_datetime")),
        floor_strike=_to_float(raw.get("floor_strike")),
    )


def is_active(raw_or_contract: Any) -> bool:
    status = (
        raw_or_contract.status
        if isinstance(raw_or_contract, KalshiContract)
        else raw_or_contract.get("status", "")
    )
    return status in ACTIVE_STATUSES
