"""Twelve Data API provider for equities, FX, commodities, crypto."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from backend.config import ASSETS, TWELVE_DATA_API_KEY, US_EQUITY_SYMBOLS
from backend.providers.base import DataProvider

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.twelvedata.com"
_TIMEOUT = 15.0
_MAX_CONCURRENT = 8

_PERIOD_MAP: dict[str, dict[str, str | int]] = {
    "1D": {"interval": "1day", "outputsize": 1},
    "1W": {"interval": "1day", "outputsize": 5},
    "1M": {"interval": "1day", "outputsize": 22},
    # YTD handled dynamically in _build_history_params
}


class TwelveDataError(Exception):
    """Raised when the Twelve Data API returns an application-level error."""


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


def _all_symbols() -> list[str]:
    """Flatten ASSETS config into a list of all symbols."""
    return [sym for group in ASSETS.values() for sym in group]


def _parse_quote(raw: dict) -> dict:
    """Normalize a single quote response into a standard dict.

    Extracts enriched fields (52-week, average volume, rolling changes)
    alongside the core price data.  Missing enriched fields are omitted
    rather than set to None so callers can use ``.get()`` safely.
    """
    result = {
        "price": float(raw["close"]),
        "change_pct": float(raw["percent_change"]),
        "change_abs": float(raw["change"]),
        "timestamp": raw.get("datetime", raw.get("timestamp", "")),
    }

    # Enriched fields — best-effort extraction
    for src_key, dst_key in (
        ("average_volume", "average_volume"),
        ("rolling_1d_change", "rolling_1d_change"),
        ("rolling_7d_change", "rolling_7d_change"),
    ):
        val = raw.get(src_key)
        if val is not None:
            try:
                result[dst_key] = float(val)
            except (ValueError, TypeError):
                pass

    ftw = raw.get("fifty_two_week") or {}
    for src_key, dst_key in (
        ("high", "fifty_two_week_high"),
        ("low", "fifty_two_week_low"),
        ("high_change_percent", "fifty_two_week_high_change_pct"),
        ("low_change_percent", "fifty_two_week_low_change_pct"),
    ):
        val = ftw.get(src_key)
        if val is not None:
            try:
                result[dst_key] = float(val)
            except (ValueError, TypeError):
                pass

    return result


def _parse_batch_quotes(raw: dict, symbols: list[str]) -> dict[str, dict]:
    """Parse a batch /quote response.

    Single-symbol responses return a flat dict; multi-symbol responses
    return a nested dict keyed by symbol.
    """
    results: dict[str, dict] = {}

    if len(symbols) == 1:
        sym = symbols[0]
        if "code" in raw:
            logger.warning("Quote error for %s: %s", sym, raw.get("message"))
            return results
        try:
            results[sym] = _parse_quote(raw)
        except (KeyError, ValueError) as exc:
            logger.warning("Failed to parse quote for %s: %s", sym, exc)
        return results

    for sym in symbols:
        entry = raw.get(sym)
        if entry is None:
            logger.warning("No data returned for %s", sym)
            continue
        if "code" in entry:
            logger.warning("Quote error for %s: %s", sym, entry.get("message"))
            continue
        try:
            results[sym] = _parse_quote(entry)
        except (KeyError, ValueError) as exc:
            logger.warning("Failed to parse quote for %s: %s", sym, exc)

    return results


def _build_history_params(symbol: str, period: str) -> dict:
    """Convert a period string to Twelve Data /time_series query params."""
    params: dict[str, str | int] = {"symbol": symbol}

    if period == "YTD":
        now = datetime.now(timezone.utc)
        params["interval"] = "1day"
        params["start_date"] = f"{now.year}-01-01"
    elif period in _PERIOD_MAP:
        params.update(_PERIOD_MAP[period])
    else:
        raise ValueError(f"Unknown period: {period!r}")

    return params


def _parse_time_series(raw: dict) -> list[dict]:
    """Normalize /time_series values into a list of OHLCV dicts."""
    values = raw.get("values", [])
    bars: list[dict] = []
    for v in values:
        bars.append({
            "date": v["datetime"],
            "open": float(v["open"]),
            "high": float(v["high"]),
            "low": float(v["low"]),
            "close": float(v["close"]),
            "volume": int(v["volume"]) if v.get("volume") else None,
        })
    return bars


def _parse_search_results(raw: dict) -> list[dict]:
    """Normalize /symbol_search data into a list of result dicts."""
    return [
        {
            "symbol": item["symbol"],
            "name": item["instrument_name"],
            "type": item["instrument_type"],
            "exchange": item["exchange"],
        }
        for item in raw.get("data", [])
    ]


# ---------------------------------------------------------------------------
# Provider class
# ---------------------------------------------------------------------------


class TwelveDataProvider(DataProvider):
    """Twelve Data implementation of the DataProvider interface."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=_BASE_URL,
            timeout=_TIMEOUT,
            params={"apikey": TWELVE_DATA_API_KEY},
        )
        self._semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def _request(self, endpoint: str, params: dict) -> dict:
        """Rate-limited GET; raises TwelveDataError on API-level errors."""
        async with self._semaphore:
            resp = await self._client.get(endpoint, params=params)
            resp.raise_for_status()
            data = resp.json()

        if isinstance(data, dict) and data.get("code") and data.get("status") == "error":
            raise TwelveDataError(f"{data.get('code')}: {data.get('message')}")

        return data

    # -- Public interface ----------------------------------------------------

    async def get_quote(self, symbol: str) -> dict:
        """Fetch the latest quote for a single symbol."""
        try:
            raw = await self._request("/quote", {"symbol": symbol})
            return _parse_quote(raw)
        except (httpx.HTTPError, TwelveDataError, KeyError, ValueError) as exc:
            logger.error("get_quote(%s) failed: %s", symbol, exc)
            return {}

    async def _batch_quote_request(
        self,
        symbols: list[str],
        extra_params: dict | None = None,
    ) -> dict[str, dict]:
        """Fetch quotes for a list of symbols in one HTTP call."""
        if not symbols:
            return {}
        params: dict[str, str] = {"symbol": ",".join(symbols)}
        if extra_params:
            params.update(extra_params)
        try:
            raw = await self._request("/quote", params)
            return _parse_batch_quotes(raw, symbols)
        except (httpx.HTTPError, TwelveDataError, KeyError, ValueError) as exc:
            logger.error("Batch quote request failed: %s", exc)
            return {}

    async def get_all_quotes(self) -> dict[str, dict]:
        """Batch-fetch quotes for all configured assets.

        US equities (SPY, QQQ, IWM, VIXY) are fetched with ``prepost=true``
        for extended-hours data; all other symbols are fetched without it.
        """
        symbols = _all_symbols()
        us_eq = [s for s in symbols if s in US_EQUITY_SYMBOLS]
        others = [s for s in symbols if s not in US_EQUITY_SYMBOLS]

        results: dict[str, dict] = {}
        if us_eq:
            results.update(
                await self._batch_quote_request(us_eq, {"prepost": "true"})
            )
        if others:
            results.update(await self._batch_quote_request(others))
        return results

    async def get_quotes_for_symbols(self, symbols: list[str]) -> dict[str, dict]:
        """Batch-fetch quotes for a specific list of symbols.

        US equities are fetched with ``prepost=true``; others without.
        """
        if not symbols:
            return {}
        us_eq = [s for s in symbols if s in US_EQUITY_SYMBOLS]
        others = [s for s in symbols if s not in US_EQUITY_SYMBOLS]

        results: dict[str, dict] = {}
        if us_eq:
            results.update(
                await self._batch_quote_request(us_eq, {"prepost": "true"})
            )
        if others:
            results.update(await self._batch_quote_request(others))
        return results

    async def get_history(self, symbol: str, period: str) -> list[dict]:
        """Fetch historical OHLCV bars for a symbol over a given period."""
        try:
            params = _build_history_params(symbol, period)
            raw = await self._request("/time_series", params)
            return _parse_time_series(raw)
        except (httpx.HTTPError, TwelveDataError, KeyError, ValueError) as exc:
            logger.error("get_history(%s, %s) failed: %s", symbol, period, exc)
            return []

    async def get_full_history(self, symbol: str) -> list[dict]:
        """Fetch maximum daily history (~20 years) for a symbol.

        Uses outputsize=5000 for up to 5000 daily bars.
        """
        try:
            raw = await self._request(
                "/time_series",
                {"symbol": symbol, "interval": "1day", "outputsize": 5000},
            )
            return _parse_time_series(raw)
        except (httpx.HTTPError, TwelveDataError, KeyError, ValueError) as exc:
            logger.error("get_full_history(%s) failed: %s", symbol, exc)
            return []

    async def get_history_since(self, symbol: str, start_date: str) -> list[dict]:
        """Fetch daily bars from *start_date* (YYYY-MM-DD) to now.

        Used for incremental cache updates.
        """
        try:
            raw = await self._request(
                "/time_series",
                {"symbol": symbol, "interval": "1day", "start_date": start_date},
            )
            return _parse_time_series(raw)
        except (httpx.HTTPError, TwelveDataError, KeyError, ValueError) as exc:
            logger.error("get_history_since(%s, %s) failed: %s", symbol, start_date, exc)
            return []

    async def search(self, query: str) -> list[dict]:
        """Search for instruments matching a query string."""
        try:
            raw = await self._request("/symbol_search", {"symbol": query})
            return _parse_search_results(raw)
        except (httpx.HTTPError, TwelveDataError, KeyError, ValueError) as exc:
            logger.error("search(%s) failed: %s", query, exc)
            return []

    async def get_intraday(self, symbol: str) -> list[dict]:
        """Fetch 5-minute intraday bars for today."""
        try:
            raw = await self._request(
                "/time_series",
                {
                    "symbol": symbol,
                    "interval": "5min",
                    "outputsize": 78,  # ~6.5 hours of 5min bars
                },
            )
            return _parse_time_series(raw)
        except (httpx.HTTPError, TwelveDataError, KeyError, ValueError) as exc:
            logger.error("get_intraday(%s) failed: %s", symbol, exc)
            return []
