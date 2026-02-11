"""Cross-asset correlation detection and anomaly flagging.

Calculates rolling correlations between asset pairs, detects unusual
convergence, broken traditional correlations, and scarcity-asset divergence.
Output feeds into the LLM summary prompt.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TypedDict

import aiosqlite

from backend.config import (
    ASSETS,
    BASELINE_CORRELATIONS,
    CORRELATION_CONFIG,
    FRED_SERIES,
    REGIME_THRESHOLDS,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class CoMovingGroup(TypedDict):
    """A cluster of assets moving in the same direction."""

    direction: str           # "up" or "down"
    avg_change_pct: float
    symbols: list[str]
    labels: list[str]


class CorrelationAnomaly(TypedDict):
    """A flagged deviation from expected correlation behaviour."""

    anomaly_type: str        # "unexpected_convergence" | "broken_correlation"
                             # | "scarcity_divergence"
    symbols: list[str]
    expected: float
    actual: float
    detail: str


class DivergingPair(TypedDict):
    """A pair of normally-correlated assets moving in opposite directions."""

    symbol_a: str
    symbol_b: str
    label_a: str           # human-readable
    label_b: str
    change_pct_a: float
    change_pct_b: float
    baseline_r: float      # expected correlation


class PairCorrelation(TypedDict):
    """Correlation between one asset pair."""

    symbol_a: str
    symbol_b: str
    correlation: float
    data_points: int


class CorrelationResult(TypedDict):
    """Full output of detect_correlations."""

    period: str
    timestamp: str
    data_points: int
    groups: list[CoMovingGroup]
    anomalies: list[CorrelationAnomaly]
    notable_pairs: list[PairCorrelation]
    diverging: list[DivergingPair]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCARCITY_SYMBOLS: list[str] = ["URA", "LIT", "REMX"]
_RISK_SYMBOLS: list[str] = ["SPY", "QQQ"]


def _label(symbol: str) -> str:
    """Look up the human-readable name for a symbol."""
    for _cat, symbols in ASSETS.items():
        if symbol in symbols:
            return symbols[symbol]
    if symbol in FRED_SERIES:
        return FRED_SERIES[symbol]
    return symbol


def _normalize_pair(a: str, b: str) -> tuple[str, str]:
    """Return a symbol pair in canonical sorted order."""
    return (a, b) if a <= b else (b, a)


def _all_symbols() -> list[str]:
    """Return every tracked symbol (Twelve Data + FRED + synthetic)."""
    symbols: list[str] = []
    for _cat, syms in ASSETS.items():
        symbols.extend(syms.keys())
    symbols.extend(FRED_SERIES.keys())
    symbols.append("SPREAD_2S10S")
    return symbols


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _get_all_latest_snapshots(
    conn: aiosqlite.Connection,
) -> dict[str, dict]:
    """Return the most recent snapshot for every symbol."""
    cursor = await conn.execute(
        """
        SELECT s.symbol, s.asset_class, s.price, s.change_pct,
               s.change_abs, s.timestamp
        FROM market_snapshots s
        INNER JOIN (
            SELECT symbol, MAX(timestamp) AS max_ts
            FROM market_snapshots
            GROUP BY symbol
        ) latest ON s.symbol = latest.symbol AND s.timestamp = latest.max_ts
        """,
    )
    rows = await cursor.fetchall()
    return {row["symbol"]: dict(row) for row in rows}


async def _get_daily_close_series(
    conn: aiosqlite.Connection,
    symbols: list[str],
    days_back: int,
) -> dict[str, list[tuple[str, float]]]:
    """Fetch daily close prices for each symbol over the last *days_back* days.

    Groups intraday snapshots by date, keeping the latest price per day.
    Returns ``{symbol: [(date, close), ...]}`` sorted oldest-to-newest.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()
    placeholders = ",".join("?" for _ in symbols)
    cursor = await conn.execute(
        f"""
        SELECT symbol, date(timestamp) AS day, price, timestamp
        FROM market_snapshots
        WHERE symbol IN ({placeholders}) AND timestamp >= ?
        ORDER BY symbol, timestamp DESC
        """,
        (*symbols, cutoff),
    )
    rows = await cursor.fetchall()

    # Group by symbol, deduplicate by day (keep first row = latest timestamp).
    raw: dict[str, dict[str, float]] = {}
    for row in rows:
        sym = row["symbol"]
        day = row["day"]
        if sym not in raw:
            raw[sym] = {}
        if day not in raw[sym]:
            raw[sym][day] = row["price"]

    # Sort each series oldest-to-newest.
    result: dict[str, list[tuple[str, float]]] = {}
    for sym, day_map in raw.items():
        result[sym] = sorted(day_map.items(), key=lambda x: x[0])
    return result


# ---------------------------------------------------------------------------
# Pure computation
# ---------------------------------------------------------------------------


def _get_period_days(period: str) -> int:
    """Convert a period label to calendar-day lookback."""
    if period == "1W":
        return 9      # 7 + weekend buffer
    if period == "1M":
        return 35     # ~30 + buffer
    if period == "YTD":
        jan1 = datetime(datetime.now(timezone.utc).year, 1, 1, tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - jan1).days + 1
    return 9  # fallback


def _compute_daily_returns(series: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Compute daily percent changes from a price series.

    Returns ``[(date, pct_change), ...]`` with *n − 1* entries for *n* prices.
    """
    returns: list[tuple[str, float]] = []
    for i in range(1, len(series)):
        prev_price = series[i - 1][1]
        if prev_price == 0.0:
            continue
        pct = (series[i][1] - prev_price) / prev_price * 100.0
        returns.append((series[i][0], pct))
    return returns


def _align_returns(
    returns_a: list[tuple[str, float]],
    returns_b: list[tuple[str, float]],
) -> tuple[list[float], list[float]]:
    """Align two return series on shared dates."""
    map_b = dict(returns_b)
    xs: list[float] = []
    ys: list[float] = []
    for date, val_a in returns_a:
        if date in map_b:
            xs.append(val_a)
            ys.append(map_b[date])
    return xs, ys


def _pearson_r(xs: list[float], ys: list[float]) -> float | None:
    """Compute Pearson correlation coefficient between two series.

    Returns ``None`` if series are too short or have zero variance.
    """
    min_pts = int(CORRELATION_CONFIG["min_data_points"])
    n = min(len(xs), len(ys))
    if n < min_pts:
        return None
    xs, ys = xs[:n], ys[:n]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0.0 or var_y == 0.0:
        return None
    cov = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    return cov / (var_x * var_y) ** 0.5


def _compute_correlation_matrix(
    returns_by_symbol: dict[str, list[tuple[str, float]]],
) -> dict[tuple[str, str], PairCorrelation]:
    """Compute pairwise Pearson correlations for all symbol pairs."""
    symbols = sorted(returns_by_symbol.keys())
    result: dict[tuple[str, str], PairCorrelation] = {}
    for i, sym_a in enumerate(symbols):
        for sym_b in symbols[i + 1:]:
            xs, ys = _align_returns(
                returns_by_symbol[sym_a],
                returns_by_symbol[sym_b],
            )
            r = _pearson_r(xs, ys)
            if r is not None:
                pair_key = _normalize_pair(sym_a, sym_b)
                result[pair_key] = PairCorrelation(
                    symbol_a=pair_key[0],
                    symbol_b=pair_key[1],
                    correlation=round(r, 4),
                    data_points=min(len(xs), len(ys)),
                )
    return result


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def _group_by_comovement(
    snapshots: dict[str, dict],
) -> list[CoMovingGroup]:
    """Group assets by co-movement direction and magnitude for 1D period."""
    min_change = CORRELATION_CONFIG["comovement_min_change_pct"]
    band = CORRELATION_CONFIG["comovement_magnitude_band"]

    up: list[tuple[str, float]] = []
    down: list[tuple[str, float]] = []
    for sym, snap in snapshots.items():
        pct = snap.get("change_pct")
        if pct is None:
            continue
        if pct >= min_change:
            up.append((sym, pct))
        elif pct <= -min_change:
            down.append((sym, pct))

    groups: list[CoMovingGroup] = []
    for direction, assets in [("up", up), ("down", down)]:
        if not assets:
            continue
        # Sort by magnitude descending.
        assets.sort(key=lambda x: abs(x[1]), reverse=True)
        # Cluster into bands.
        clusters: list[list[tuple[str, float]]] = [[assets[0]]]
        for sym, pct in assets[1:]:
            if abs(abs(pct) - abs(clusters[-1][-1][1])) <= band:
                clusters[-1].append((sym, pct))
            else:
                clusters.append([(sym, pct)])

        for cluster in clusters:
            if len(cluster) < 2:
                continue
            syms = [s for s, _ in cluster]
            avg = sum(p for _, p in cluster) / len(cluster)
            groups.append(CoMovingGroup(
                direction=direction,
                avg_change_pct=round(avg, 2),
                symbols=syms,
                labels=[_label(s) for s in syms],
            ))

    return groups


def _group_by_correlation(
    pair_correlations: dict[tuple[str, str], PairCorrelation],
    returns_by_symbol: dict[str, list[tuple[str, float]]],
    threshold: float,
) -> list[CoMovingGroup]:
    """Form groups of highly correlated assets using greedy merging."""
    # Filter to pairs above threshold.
    high_pairs = [
        (pair, pc)
        for pair, pc in pair_correlations.items()
        if pc["correlation"] >= threshold
    ]
    high_pairs.sort(key=lambda x: x[1]["correlation"], reverse=True)

    # Greedy merge: for each pair, join an existing group or start a new one.
    groups: list[set[str]] = []
    for (sym_a, sym_b), _pc in high_pairs:
        merged = False
        for group in groups:
            if sym_a in group or sym_b in group:
                group.add(sym_a)
                group.add(sym_b)
                merged = True
                break
        if not merged:
            groups.append({sym_a, sym_b})

    # Determine direction from average daily return of last observation.
    result: list[CoMovingGroup] = []
    for group in groups:
        if len(group) < 2:
            continue
        syms = sorted(group)
        avg_return = 0.0
        count = 0
        for sym in syms:
            series = returns_by_symbol.get(sym)
            if series:
                avg_return += series[-1][1]  # last daily return
                count += 1
        avg_return = avg_return / count if count else 0.0
        result.append(CoMovingGroup(
            direction="up" if avg_return >= 0 else "down",
            avg_change_pct=round(avg_return, 2),
            symbols=syms,
            labels=[_label(s) for s in syms],
        ))

    return result


# ---------------------------------------------------------------------------
# Anomaly detectors
# ---------------------------------------------------------------------------


def _detect_unexpected_convergence(
    pair_correlations: dict[tuple[str, str], PairCorrelation],
) -> list[CorrelationAnomaly]:
    """Flag pairs that are normally uncorrelated but are now moving together."""
    threshold = CORRELATION_CONFIG["anomaly_deviation_threshold"]
    anomalies: list[CorrelationAnomaly] = []
    for pair_key, pc in pair_correlations.items():
        expected = BASELINE_CORRELATIONS.get(pair_key)
        if expected is None:
            continue
        if abs(expected) < 0.3 and pc["correlation"] > expected + threshold:
            anomalies.append(CorrelationAnomaly(
                anomaly_type="unexpected_convergence",
                symbols=list(pair_key),
                expected=expected,
                actual=pc["correlation"],
                detail=(
                    f"{_label(pair_key[0])} and {_label(pair_key[1])} are "
                    f"unusually correlated (r={pc['correlation']:.2f}, "
                    f"normally ~{expected:.2f})"
                ),
            ))
    return anomalies


def _detect_broken_correlations(
    pair_correlations: dict[tuple[str, str], PairCorrelation],
) -> list[CorrelationAnomaly]:
    """Flag pairs whose traditional correlation has broken down."""
    threshold = CORRELATION_CONFIG["anomaly_deviation_threshold"]
    anomalies: list[CorrelationAnomaly] = []
    for pair_key, pc in pair_correlations.items():
        expected = BASELINE_CORRELATIONS.get(pair_key)
        if expected is None:
            continue
        deviation = abs(pc["correlation"] - expected)
        if deviation > threshold and abs(expected) >= 0.5:
            anomalies.append(CorrelationAnomaly(
                anomaly_type="broken_correlation",
                symbols=list(pair_key),
                expected=expected,
                actual=pc["correlation"],
                detail=(
                    f"{_label(pair_key[0])} and {_label(pair_key[1])} "
                    f"correlation has shifted (r={pc['correlation']:.2f}, "
                    f"normally ~{expected:.2f})"
                ),
            ))
    return anomalies


def _detect_scarcity_divergence(
    pair_correlations: dict[tuple[str, str], PairCorrelation],
) -> list[CorrelationAnomaly]:
    """Flag when critical mineral ETFs diverge from broad equity risk."""
    threshold = CORRELATION_CONFIG["anomaly_deviation_threshold"]
    anomalies: list[CorrelationAnomaly] = []
    for scarcity in _SCARCITY_SYMBOLS:
        for risk in _RISK_SYMBOLS:
            pair_key = _normalize_pair(scarcity, risk)
            pc = pair_correlations.get(pair_key)
            expected = BASELINE_CORRELATIONS.get(pair_key)
            if pc is None or expected is None:
                continue
            if pc["correlation"] < expected - threshold:
                anomalies.append(CorrelationAnomaly(
                    anomaly_type="scarcity_divergence",
                    symbols=[scarcity, risk],
                    expected=expected,
                    actual=pc["correlation"],
                    detail=(
                        f"{_label(scarcity)} is diverging from "
                        f"{_label(risk)} (r={pc['correlation']:.2f}, "
                        f"normally ~{expected:.2f})"
                    ),
                ))
    return anomalies


def _detect_1d_anomalies(
    snapshots: dict[str, dict],
) -> list[CorrelationAnomaly]:
    """Detect anomalous co-movement from single-day changes."""
    min_change = CORRELATION_CONFIG["comovement_min_change_pct"]
    anomalies: list[CorrelationAnomaly] = []

    for (sym_a, sym_b), expected in BASELINE_CORRELATIONS.items():
        snap_a = snapshots.get(sym_a)
        snap_b = snapshots.get(sym_b)
        if snap_a is None or snap_b is None:
            continue
        pct_a = snap_a.get("change_pct")
        pct_b = snap_b.get("change_pct")
        if pct_a is None or pct_b is None:
            continue
        if abs(pct_a) < min_change and abs(pct_b) < min_change:
            continue

        same_direction = (pct_a > 0 and pct_b > 0) or (pct_a < 0 and pct_b < 0)

        # Normally inversely correlated but moving together today.
        if expected < -0.5 and same_direction:
            anomalies.append(CorrelationAnomaly(
                anomaly_type="broken_correlation",
                symbols=[sym_a, sym_b],
                expected=expected,
                actual=0.0,
                detail=(
                    f"{_label(sym_a)} and {_label(sym_b)} moving in the "
                    f"same direction today ({pct_a:+.1f}% and {pct_b:+.1f}%)"
                ),
            ))

        # Normally uncorrelated but moving in lockstep today.
        if (
            abs(expected) < 0.3
            and same_direction
            and abs(pct_a) >= min_change
            and abs(pct_b) >= min_change
        ):
            anomalies.append(CorrelationAnomaly(
                anomaly_type="unexpected_convergence",
                symbols=[sym_a, sym_b],
                expected=expected,
                actual=0.0,
                detail=(
                    f"{_label(sym_a)} and {_label(sym_b)} moving together "
                    f"today ({pct_a:+.1f}% and {pct_b:+.1f}%)"
                ),
            ))

        # Normally positively correlated but moving opposite today.
        if expected > 0.5 and not same_direction:
            # Check that both moves are meaningful.
            if abs(pct_a) >= min_change and abs(pct_b) >= min_change:
                anomalies.append(CorrelationAnomaly(
                    anomaly_type="broken_correlation",
                    symbols=[sym_a, sym_b],
                    expected=expected,
                    actual=0.0,
                    detail=(
                        f"{_label(sym_a)} and {_label(sym_b)} moving in "
                        f"opposite directions today "
                        f"({pct_a:+.1f}% vs {pct_b:+.1f}%)"
                    ),
                ))

    return anomalies


def _detect_diverging_pairs(
    snapshots: dict[str, dict],
) -> list[DivergingPair]:
    """Detect normally-correlated pairs moving in opposite directions today."""
    min_change = CORRELATION_CONFIG["comovement_min_change_pct"]
    threshold = CORRELATION_CONFIG["diverging_baseline_threshold"]
    result: list[DivergingPair] = []

    for (sym_a, sym_b), expected in BASELINE_CORRELATIONS.items():
        if expected < threshold:
            continue
        snap_a = snapshots.get(sym_a)
        snap_b = snapshots.get(sym_b)
        if snap_a is None or snap_b is None:
            continue
        pct_a = snap_a.get("change_pct")
        pct_b = snap_b.get("change_pct")
        if pct_a is None or pct_b is None:
            continue
        if abs(pct_a) < min_change or abs(pct_b) < min_change:
            continue

        same_direction = (pct_a > 0 and pct_b > 0) or (pct_a < 0 and pct_b < 0)
        if same_direction:
            continue

        result.append(DivergingPair(
            symbol_a=sym_a,
            symbol_b=sym_b,
            label_a=_label(sym_a),
            label_b=_label(sym_b),
            change_pct_a=pct_a,
            change_pct_b=pct_b,
            baseline_r=expected,
        ))

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def detect_correlations(
    conn: aiosqlite.Connection,
    period: str = "1D",
) -> CorrelationResult:
    """Detect cross-asset correlations and anomalies.

    For ``period="1D"``, groups assets by co-movement (direction + magnitude)
    and detects directional anomalies against baseline expectations.

    For ``period`` in ``("1W", "1M", "YTD")``, computes Pearson correlations
    on daily percent-change series and compares against baselines.
    """
    now = datetime.now(timezone.utc).isoformat()

    if period == "1D":
        snapshots = await _get_all_latest_snapshots(conn)
        groups = _group_by_comovement(snapshots)
        anomalies = _detect_1d_anomalies(snapshots)
        diverging = _detect_diverging_pairs(snapshots)
        return CorrelationResult(
            period="1D",
            timestamp=now,
            data_points=0,
            groups=groups,
            anomalies=anomalies,
            notable_pairs=[],
            diverging=diverging,
        )

    # Multi-day correlation path.
    days = _get_period_days(period)
    symbols = _all_symbols()
    series = await _get_daily_close_series(conn, symbols, days)

    min_pts = int(CORRELATION_CONFIG["min_data_points"])
    returns_by_symbol: dict[str, list[tuple[str, float]]] = {}
    for sym, prices in series.items():
        returns = _compute_daily_returns(prices)
        if len(returns) >= min_pts:
            returns_by_symbol[sym] = returns

    if not returns_by_symbol:
        return CorrelationResult(
            period=period,
            timestamp=now,
            data_points=0,
            groups=[],
            anomalies=[],
            notable_pairs=[],
            diverging=[],
        )

    pair_correlations = _compute_correlation_matrix(returns_by_symbol)
    corr_threshold = REGIME_THRESHOLDS["correlation_threshold"]

    groups = _group_by_correlation(
        pair_correlations, returns_by_symbol, corr_threshold,
    )
    anomalies = (
        _detect_unexpected_convergence(pair_correlations)
        + _detect_broken_correlations(pair_correlations)
        + _detect_scarcity_divergence(pair_correlations)
    )
    notable = [
        pc for pc in pair_correlations.values()
        if abs(pc["correlation"]) >= corr_threshold
    ]
    notable.sort(key=lambda x: abs(x["correlation"]), reverse=True)

    data_points = min(len(r) for r in returns_by_symbol.values())

    return CorrelationResult(
        period=period,
        timestamp=now,
        data_points=data_points,
        groups=groups,
        anomalies=anomalies,
        notable_pairs=notable,
        diverging=[],
    )
