"""FRED (Federal Reserve Economic Data) provider for rates and credit spreads."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx

from backend.config import FRED_API_KEY, FRED_SERIES
from backend.providers.base import DataProvider

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.stlouisfed.org/fred"
_TIMEOUT = 15.0
_MAX_CONCURRENT = 4

_SPREAD_SYMBOL = "SPREAD_2S10S"


class FredError(Exception):
    """Raised when the FRED API returns an application-level error."""


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


def _parse_latest_observation(observations: list[dict]) -> dict | None:
    """Return the most recent observation with a valid numeric value.

    FRED sometimes returns ``"."`` for dates with no data -- those are
    skipped.  *observations* are expected in descending date order.

    Returns ``{"value": float, "date": str}`` or ``None``.
    """
    for obs in observations:
        if obs.get("value") not in (None, "."):
            try:
                return {"value": float(obs["value"]), "date": obs["date"]}
            except (ValueError, KeyError):
                continue
    return None


def _compute_change(observations: list[dict]) -> tuple[float, float]:
    """Compute absolute and percentage change from the two most recent valid values.

    *observations* must be sorted descending by date.  Returns
    ``(change_abs, change_pct)`` or ``(0.0, 0.0)`` when fewer than two
    valid values exist.
    """
    valid: list[float] = []
    for obs in observations:
        if obs.get("value") not in (None, "."):
            try:
                valid.append(float(obs["value"]))
            except ValueError:
                continue
        if len(valid) == 2:
            break

    if len(valid) < 2:
        return 0.0, 0.0

    latest, previous = valid[0], valid[1]
    change_abs = latest - previous
    change_pct = (change_abs / previous * 100.0) if previous != 0 else 0.0
    return change_abs, change_pct


def _observation_start_date(period: str) -> str:
    """Map a period string to a YYYY-MM-DD start date for the FRED query.

    Supported periods: ``'1D'``, ``'1W'``, ``'1M'``, ``'YTD'``.
    """
    today = datetime.now(timezone.utc).date()

    if period == "1D":
        return today.isoformat()
    if period == "1W":
        return (today - timedelta(days=7)).isoformat()
    if period == "1M":
        return (today - timedelta(days=30)).isoformat()
    if period == "YTD":
        return f"{today.year}-01-01"

    raise ValueError(f"Unknown period: {period!r}")


def _parse_history(observations: list[dict]) -> list[dict]:
    """Convert FRED observations to our standard history format.

    FRED data is a single value per date (not OHLCV), so *open*, *high*,
    *low*, and *close* all map to that value and *volume* is ``None``.
    Observations where *value* is ``"."`` are skipped.

    Returns rows in **ascending** date order (chronological).
    """
    bars: list[dict] = []
    for obs in observations:
        if obs.get("value") in (None, "."):
            continue
        try:
            val = float(obs["value"])
        except (ValueError, KeyError):
            continue
        bars.append({
            "date": obs["date"],
            "open": val,
            "high": val,
            "low": val,
            "close": val,
            "volume": None,
        })

    # FRED returns descending by default; reverse to chronological order.
    bars.reverse()
    return bars


def _parse_search_results(raw: dict) -> list[dict]:
    """Normalize a ``/series/search`` response into a list of result dicts."""
    return [
        {
            "symbol": item["id"],
            "name": item["title"],
            "type": item.get("frequency", ""),
            "exchange": "FRED",
        }
        for item in raw.get("seriess", [])
    ]


# ---------------------------------------------------------------------------
# Provider class
# ---------------------------------------------------------------------------


class FredProvider(DataProvider):
    """FRED implementation of the DataProvider interface."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=_BASE_URL,
            timeout=_TIMEOUT,
            params={"api_key": FRED_API_KEY, "file_type": "json"},
        )
        self._semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def _request(self, endpoint: str, params: dict) -> dict:
        """Rate-limited GET; raises FredError on API-level errors."""
        async with self._semaphore:
            resp = await self._client.get(endpoint, params=params)
            resp.raise_for_status()
            data = resp.json()

        if isinstance(data, dict) and "error_message" in data:
            raise FredError(data["error_message"])

        return data

    async def _fetch_observations(
        self, series_id: str, limit: int = 2, observation_start: str | None = None,
    ) -> list[dict]:
        """Fetch observations for a single series.

        Returns the raw list of observation dicts (descending date order).
        """
        params: dict[str, str | int] = {
            "series_id": series_id,
            "sort_order": "desc",
            "limit": limit,
        }
        if observation_start is not None:
            params["observation_start"] = observation_start
            # When fetching a date range we want all observations, not just 2.
            params.pop("limit", None)

        raw = await self._request("/series/observations", params)
        return raw.get("observations", [])

    # -- Public interface ----------------------------------------------------

    async def get_quote(self, series_id: str) -> dict:
        """Fetch the latest value for a FRED series (or the synthetic 2s10s spread).

        Returns a dict matching the standard quote format:
        ``{price, change_pct, change_abs, timestamp}``.
        """
        if series_id == _SPREAD_SYMBOL:
            return await self._get_spread_quote()

        try:
            observations = await self._fetch_observations(series_id, limit=10)
            latest = _parse_latest_observation(observations)
            if latest is None:
                return {}

            change_abs, change_pct = _compute_change(observations)
            return {
                "price": latest["value"],
                "change_pct": change_pct,
                "change_abs": change_abs,
                "timestamp": latest["date"],
            }
        except (httpx.HTTPError, FredError, KeyError, ValueError) as exc:
            logger.error("get_quote(%s) failed: %s", series_id, exc)
            return {}

    async def _get_spread_quote(self) -> dict:
        """Compute the synthetic 2s10s spread quote (DGS10 - DGS2)."""
        try:
            dgs2_obs, dgs10_obs = await asyncio.gather(
                self._fetch_observations("DGS2", limit=10),
                self._fetch_observations("DGS10", limit=10),
            )

            latest_2 = _parse_latest_observation(dgs2_obs)
            latest_10 = _parse_latest_observation(dgs10_obs)
            if latest_2 is None or latest_10 is None:
                return {}

            spread = latest_10["value"] - latest_2["value"]

            # Compute previous spread for change calculation.
            prev_2 = _parse_latest_observation(dgs2_obs[1:])
            prev_10 = _parse_latest_observation(dgs10_obs[1:])
            if prev_2 is not None and prev_10 is not None:
                prev_spread = prev_10["value"] - prev_2["value"]
                change_abs = spread - prev_spread
                change_pct = (change_abs / abs(prev_spread) * 100.0) if prev_spread != 0 else 0.0
            else:
                change_abs, change_pct = 0.0, 0.0

            return {
                "price": spread,
                "change_pct": change_pct,
                "change_abs": change_abs,
                "timestamp": max(latest_2["date"], latest_10["date"]),
            }
        except (httpx.HTTPError, FredError, KeyError, ValueError) as exc:
            logger.error("get_quote(%s) failed: %s", _SPREAD_SYMBOL, exc)
            return {}

    async def get_all_quotes(self) -> dict[str, dict]:
        """Fetch latest quotes for all configured FRED series concurrently.

        Includes the synthetic ``SPREAD_2S10S`` (= DGS10 - DGS2).
        """
        series_ids = list(FRED_SERIES.keys())

        try:
            results_list = await asyncio.gather(
                *(self.get_quote(sid) for sid in series_ids),
                return_exceptions=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("get_all_quotes gather failed: %s", exc)
            return {}

        quotes: dict[str, dict] = {}
        for sid, result in zip(series_ids, results_list):
            if isinstance(result, Exception):
                logger.warning("get_all_quotes: %s raised %s", sid, result)
                continue
            if result:
                quotes[sid] = result

        # Add synthetic 2s10s spread.
        spread_quote = await self.get_quote(_SPREAD_SYMBOL)
        if spread_quote:
            quotes[_SPREAD_SYMBOL] = spread_quote

        return quotes

    async def get_history(self, series_id: str, period: str) -> list[dict]:
        """Fetch historical observations for a series over a given period.

        For ``SPREAD_2S10S``, fetches both DGS2 and DGS10 history, aligns
        by date, and computes the difference.
        """
        if series_id == _SPREAD_SYMBOL:
            return await self._get_spread_history(period)

        try:
            start = _observation_start_date(period)
            observations = await self._fetch_observations(
                series_id, observation_start=start,
            )
            return _parse_history(observations)
        except (httpx.HTTPError, FredError, KeyError, ValueError) as exc:
            logger.error("get_history(%s, %s) failed: %s", series_id, period, exc)
            return []

    async def _get_spread_history(self, period: str) -> list[dict]:
        """Compute synthetic 2s10s spread history by aligning DGS2 and DGS10."""
        try:
            start = _observation_start_date(period)
            dgs2_obs, dgs10_obs = await asyncio.gather(
                self._fetch_observations("DGS2", observation_start=start),
                self._fetch_observations("DGS10", observation_start=start),
            )

            # Build lookup of valid DGS2 values by date.
            dgs2_by_date: dict[str, float] = {}
            for obs in dgs2_obs:
                if obs.get("value") not in (None, "."):
                    try:
                        dgs2_by_date[obs["date"]] = float(obs["value"])
                    except (ValueError, KeyError):
                        continue

            # Walk DGS10 observations and compute spread where both exist.
            bars: list[dict] = []
            for obs in dgs10_obs:
                if obs.get("value") in (None, "."):
                    continue
                date = obs["date"]
                if date not in dgs2_by_date:
                    continue
                try:
                    spread = float(obs["value"]) - dgs2_by_date[date]
                except (ValueError, KeyError):
                    continue
                bars.append({
                    "date": date,
                    "open": spread,
                    "high": spread,
                    "low": spread,
                    "close": spread,
                    "volume": None,
                })

            # FRED returns descending; reverse to chronological order.
            bars.reverse()
            return bars
        except (httpx.HTTPError, FredError, KeyError, ValueError) as exc:
            logger.error("get_history(%s, %s) failed: %s", _SPREAD_SYMBOL, period, exc)
            return []

    async def search(self, query: str) -> list[dict]:
        """Search FRED series matching a query string."""
        try:
            raw = await self._request("/series/search", {
                "search_text": query,
                "limit": 10,
            })
            return _parse_search_results(raw)
        except (httpx.HTTPError, FredError, KeyError, ValueError) as exc:
            logger.error("search(%s) failed: %s", query, exc)
            return []
