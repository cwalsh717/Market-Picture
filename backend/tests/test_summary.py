"""Tests for the Claude API summary generation module.

Covers:
- _label: symbol → human-readable name lookup
- _asset_class: symbol → asset class lookup
- _format_comovement_groups: CoMovingGroup list → prompt text
- _format_anomalies: CorrelationAnomaly list → prompt text
- _format_regime_signals: Signal list → prompt text
- _format_scarcity_summary: scarcity-related data extraction
- _format_diverging: DivergingPair list → prompt text
- _build_moving_together: CoMovingGroup → MovingTogetherGroup
- _build_diverging_together: DivergingPair → MovingTogetherGroup
- build_premarket_prompt: prompt assembly for pre-market summary
- build_close_prompt: prompt assembly for after-close summary
- _call_anthropic: Anthropic API call with mocked client
- generate_premarket: full pre-market generation (happy + fallback)
- generate_close: full after-close generation (happy + fallback)
- _build_fallback_summary: structured fallback text
- Config consistency for summary settings
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.intelligence.summary import (
    MovingTogetherGroup,
    SummaryResult,
    _build_fallback_summary,
    _build_moving_together,
    _format_anomalies,
    _format_comovement_groups,
    _format_diverging,
    _format_regime_signals,
    _format_scarcity_summary,
    _label,
    _asset_class,
    build_premarket_prompt,
    build_close_prompt,
    _call_anthropic,
    generate_premarket,
    generate_close,
    _FALLBACK_PREFIX,
)


# ---------------------------------------------------------------------------
# Test data factories
# ---------------------------------------------------------------------------


def _make_regime(
    label: str = "RISK-ON",
    reason: str = "VIXY falling; S&P above 20-day MA",
    signals: list = None,
    timestamp: str = "2026-02-09T15:00:00+00:00",
) -> dict:
    """Build a RegimeResult dict for tests."""
    if signals is None:
        signals = [
            {"name": "vix", "direction": "risk_on", "detail": "VIXY falling (-7.0%)"},
            {"name": "spx_trend", "direction": "risk_on", "detail": "S&P above 20-day MA (5200 vs 5000)"},
        ]
    return {
        "label": label,
        "reason": reason,
        "signals": signals,
        "timestamp": timestamp,
    }


def _make_corr(
    period: str = "1D",
    groups: list = None,
    anomalies: list = None,
    diverging: list = None,
) -> dict:
    """Build a CorrelationResult dict for tests."""
    return {
        "period": period,
        "timestamp": "2026-02-09T15:00:00+00:00",
        "data_points": 0,
        "groups": groups if groups is not None else [],
        "anomalies": anomalies if anomalies is not None else [],
        "diverging": diverging if diverging is not None else [],
        "notable_pairs": [],
    }


def _make_group(
    direction: str = "up",
    avg_change_pct: float = 2.5,
    symbols: list = None,
    labels: list = None,
) -> dict:
    """Build a CoMovingGroup dict for tests."""
    if symbols is None:
        symbols = ["SPY", "QQQ"]
    if labels is None:
        labels = ["S&P 500 (SPY)", "Nasdaq 100 (QQQ)"]
    return {
        "direction": direction,
        "avg_change_pct": avg_change_pct,
        "symbols": symbols,
        "labels": labels,
    }


def _make_anomaly(
    anomaly_type: str = "unexpected_convergence",
    symbols: list = None,
    expected: float = 0.10,
    actual: float = 0.75,
    detail: str = "Bitcoin and S&P 500 are unusually correlated (r=0.75, normally ~0.10)",
) -> dict:
    """Build a CorrelationAnomaly dict for tests."""
    if symbols is None:
        symbols = ["BTC/USD", "SPY"]
    return {
        "anomaly_type": anomaly_type,
        "symbols": symbols,
        "expected": expected,
        "actual": actual,
        "detail": detail,
    }


def _make_diverging_pair(
    symbol_a: str = "QQQ",
    symbol_b: str = "SPY",
    label_a: str = "Nasdaq 100 (QQQ)",
    label_b: str = "S&P 500 (SPY)",
    change_pct_a: float = -2.0,
    change_pct_b: float = 1.5,
    baseline_r: float = 0.90,
) -> dict:
    """Build a DivergingPair dict for tests."""
    return {
        "symbol_a": symbol_a,
        "symbol_b": symbol_b,
        "label_a": label_a,
        "label_b": label_b,
        "change_pct_a": change_pct_a,
        "change_pct_b": change_pct_b,
        "baseline_r": baseline_r,
    }


# ---------------------------------------------------------------------------
# _label
# ---------------------------------------------------------------------------


class TestLabel:
    def test_known_equity_symbol(self):
        assert _label("SPY") == "S&P 500 (SPY)"

    def test_known_crypto_symbol(self):
        assert _label("BTC/USD") == "Bitcoin"

    def test_known_commodity_symbol(self):
        assert _label("GLD") == "Gold (GLD)"

    def test_known_critical_mineral(self):
        assert _label("URA") == "Uranium ETF"

    def test_unknown_symbol_returns_verbatim(self):
        assert _label("AAPL") == "AAPL"

    def test_fred_series_lookup(self):
        assert _label("DGS10") == "10-Year Treasury Yield"

    def test_fred_hy_spread(self):
        assert _label("BAMLH0A0HYM2") == "HY Corporate Bond Spread"

    def test_international_symbol(self):
        assert _label("EWJ") == "Japan (EWJ)"

    def test_currency_symbol(self):
        assert _label("UUP") == "US Dollar (UUP)"


# ---------------------------------------------------------------------------
# _asset_class
# ---------------------------------------------------------------------------


class TestAssetClass:
    def test_equities(self):
        assert _asset_class("SPY") == "equities"
        assert _asset_class("VIXY") == "equities"

    def test_international(self):
        assert _asset_class("EWJ") == "international"
        assert _asset_class("EWH") == "international"

    def test_currencies(self):
        assert _asset_class("UUP") == "currencies"

    def test_commodities(self):
        assert _asset_class("USO") == "commodities"
        assert _asset_class("GLD") == "commodities"

    def test_critical_minerals(self):
        assert _asset_class("URA") == "critical_minerals"
        assert _asset_class("LIT") == "critical_minerals"
        assert _asset_class("REMX") == "critical_minerals"

    def test_crypto(self):
        assert _asset_class("BTC/USD") == "crypto"
        assert _asset_class("ETH/USD") == "crypto"

    def test_unknown_returns_none(self):
        assert _asset_class("AAPL") is None

    def test_fred_series_not_in_assets(self):
        """FRED series are not in ASSETS dict, so _asset_class returns None."""
        assert _asset_class("DGS10") is None


# ---------------------------------------------------------------------------
# _format_comovement_groups
# ---------------------------------------------------------------------------


class TestFormatComovementGroups:
    def test_up_group(self):
        groups = [_make_group(direction="up", avg_change_pct=2.5, symbols=["SPY", "QQQ"])]
        result = _format_comovement_groups(groups)
        assert "Rallying together" in result
        assert "+2.5% avg" in result
        assert "S&P 500" in result
        assert "Nasdaq 100" in result

    def test_down_group(self):
        groups = [_make_group(direction="down", avg_change_pct=-1.8, symbols=["USO", "CPER"])]
        result = _format_comovement_groups(groups)
        assert "Selling together" in result
        assert "-1.8% avg" in result
        assert "Crude Oil (USO)" in result
        assert "Copper (CPER)" in result

    def test_empty_groups(self):
        result = _format_comovement_groups([])
        assert result == "No significant co-movement detected."

    def test_multiple_groups(self):
        groups = [
            _make_group(direction="up", avg_change_pct=2.0, symbols=["SPY", "QQQ"]),
            _make_group(direction="down", avg_change_pct=-1.5, symbols=["USO", "UNG"]),
        ]
        result = _format_comovement_groups(groups)
        lines = result.split("\n")
        assert len(lines) == 2
        assert "Rallying together" in lines[0]
        assert "Selling together" in lines[1]

    def test_uses_human_readable_names(self):
        groups = [_make_group(direction="up", avg_change_pct=1.0, symbols=["BTC/USD", "ETH/USD"])]
        result = _format_comovement_groups(groups)
        assert "Bitcoin" in result
        assert "Ethereum" in result
        # Raw symbols should not appear
        assert "BTC/USD" not in result
        assert "ETH/USD" not in result

    def test_unknown_symbol_passes_through(self):
        groups = [_make_group(direction="up", avg_change_pct=1.0, symbols=["AAPL", "TSLA"])]
        result = _format_comovement_groups(groups)
        assert "AAPL" in result
        assert "TSLA" in result


# ---------------------------------------------------------------------------
# _format_anomalies
# ---------------------------------------------------------------------------


class TestFormatAnomalies:
    def test_renders_detail_field(self):
        anomalies = [
            _make_anomaly(detail="BTC and SPX are unusually correlated"),
            _make_anomaly(detail="NDX and SPX diverging today"),
        ]
        result = _format_anomalies(anomalies)
        assert "BTC and SPX are unusually correlated" in result
        assert "NDX and SPX diverging today" in result

    def test_empty_anomalies(self):
        result = _format_anomalies([])
        assert result == "No unusual correlation behavior detected."

    def test_single_anomaly(self):
        anomalies = [_make_anomaly(detail="Gold outperforming equities")]
        result = _format_anomalies(anomalies)
        assert result == "Gold outperforming equities"


# ---------------------------------------------------------------------------
# _format_regime_signals
# ---------------------------------------------------------------------------


class TestFormatRegimeSignals:
    def test_formats_each_signal(self):
        signals = [
            {"name": "vix", "direction": "risk_on", "detail": "VIXY falling (-7.0%)"},
            {"name": "dxy", "direction": "neutral", "detail": "UUP stable (+0.2%)"},
        ]
        result = _format_regime_signals(signals)
        lines = result.split("\n")
        assert len(lines) == 2
        assert lines[0] == "- vix (risk_on): VIXY falling (-7.0%)"
        assert lines[1] == "- dxy (neutral): UUP stable (+0.2%)"

    def test_empty_signals(self):
        result = _format_regime_signals([])
        assert result == ""

    def test_single_signal(self):
        signals = [{"name": "hy_spread", "direction": "risk_off", "detail": "HY spreads widening (+15 bps WoW)"}]
        result = _format_regime_signals(signals)
        assert result == "- hy_spread (risk_off): HY spreads widening (+15 bps WoW)"


# ---------------------------------------------------------------------------
# _format_scarcity_summary
# ---------------------------------------------------------------------------


class TestFormatScarcitySummary:
    def test_scarcity_divergence_anomaly(self):
        anomaly = _make_anomaly(
            anomaly_type="scarcity_divergence",
            symbols=["URA", "SPY"],
            detail="Uranium ETF is diverging from S&P 500 (r=-0.20, normally ~0.40)",
        )
        corr_1d = _make_corr(anomalies=[anomaly])
        result = _format_scarcity_summary(corr_1d)
        assert "Uranium ETF is diverging" in result

    def test_scarcity_in_comovement_group_up(self):
        group = _make_group(
            direction="up",
            avg_change_pct=3.0,
            symbols=["SPY", "URA", "LIT"],
        )
        corr_1d = _make_corr(groups=[group])
        result = _format_scarcity_summary(corr_1d)
        assert "Uranium ETF" in result
        assert "Lithium ETF" in result
        assert "rallying" in result
        assert "+3.0% avg" in result

    def test_scarcity_in_comovement_group_down(self):
        group = _make_group(
            direction="down",
            avg_change_pct=-2.0,
            symbols=["REMX", "QQQ"],
        )
        corr_1d = _make_corr(groups=[group])
        result = _format_scarcity_summary(corr_1d)
        assert "Rare Earths ETF" in result
        assert "selling" in result

    def test_no_scarcity_data(self):
        corr_1d = _make_corr(groups=[], anomalies=[])
        result = _format_scarcity_summary(corr_1d)
        assert result == "No notable scarcity-related moves today."

    def test_scarcity_divergence_from_1m(self):
        """Scarcity divergence in the 1M correlation result is also picked up."""
        anomaly = _make_anomaly(
            anomaly_type="scarcity_divergence",
            symbols=["LIT", "QQQ"],
            detail="Lithium ETF is diverging from Nasdaq 100",
        )
        corr_1d = _make_corr(anomalies=[])
        corr_1m = _make_corr(period="1M", anomalies=[anomaly])
        result = _format_scarcity_summary(corr_1d, corr_1m)
        assert "Lithium ETF is diverging" in result

    def test_non_scarcity_anomaly_ignored(self):
        anomaly = _make_anomaly(
            anomaly_type="unexpected_convergence",
            symbols=["BTC/USD", "SPY"],
            detail="BTC and SPX are unusually correlated",
        )
        corr_1d = _make_corr(anomalies=[anomaly])
        result = _format_scarcity_summary(corr_1d)
        assert result == "No notable scarcity-related moves today."

    def test_non_scarcity_group_ignored(self):
        """A comovement group with no scarcity symbols produces no scarcity output."""
        group = _make_group(direction="up", avg_change_pct=2.0, symbols=["SPY", "QQQ"])
        corr_1d = _make_corr(groups=[group])
        result = _format_scarcity_summary(corr_1d)
        assert result == "No notable scarcity-related moves today."

    def test_both_anomaly_and_group(self):
        """Both scarcity divergence anomaly and scarcity in group are combined."""
        anomaly = _make_anomaly(
            anomaly_type="scarcity_divergence",
            detail="Uranium ETF diverging from S&P 500",
        )
        group = _make_group(
            direction="up",
            avg_change_pct=1.5,
            symbols=["LIT", "QQQ"],
        )
        corr_1d = _make_corr(groups=[group], anomalies=[anomaly])
        result = _format_scarcity_summary(corr_1d)
        assert "Uranium ETF diverging" in result
        assert "Lithium ETF" in result


# ---------------------------------------------------------------------------
# _build_moving_together
# ---------------------------------------------------------------------------


class TestBuildMovingTogether:
    def test_up_group(self):
        groups = [_make_group(direction="up", avg_change_pct=2.1, symbols=["SPY", "QQQ"])]
        result = _build_moving_together(groups)
        assert len(result) == 1
        assert result[0]["label"] == "Up"
        assert result[0]["detail"] == "Up avg 2.1%"
        assert "S&P 500 (SPY)" in result[0]["assets"]
        assert "Nasdaq 100 (QQQ)" in result[0]["assets"]

    def test_down_group(self):
        groups = [_make_group(direction="down", avg_change_pct=-1.8, symbols=["USO", "UNG"])]
        result = _build_moving_together(groups)
        assert len(result) == 1
        assert result[0]["label"] == "Down"
        assert result[0]["detail"] == "Down avg 1.8%"
        assert "Crude Oil (USO)" in result[0]["assets"]
        assert "Natural Gas (UNG)" in result[0]["assets"]

    def test_empty_input(self):
        result = _build_moving_together([])
        assert result == []

    def test_symbols_converted_to_human_readable(self):
        groups = [_make_group(direction="up", avg_change_pct=5.0, symbols=["BTC/USD", "ETH/USD"])]
        result = _build_moving_together(groups)
        assert "Bitcoin" in result[0]["assets"]
        assert "Ethereum" in result[0]["assets"]

    def test_multiple_groups(self):
        groups = [
            _make_group(direction="up", avg_change_pct=2.0, symbols=["SPY", "QQQ"]),
            _make_group(direction="down", avg_change_pct=-1.0, symbols=["USO", "CPER"]),
        ]
        result = _build_moving_together(groups)
        assert len(result) == 2
        labels = [r["label"] for r in result]
        assert "Up" in labels
        assert "Down" in labels

    def test_result_is_moving_together_group_shape(self):
        groups = [_make_group(direction="up", avg_change_pct=1.0, symbols=["SPY"])]
        result = _build_moving_together(groups)
        assert len(result) == 1
        entry = result[0]
        assert "label" in entry
        assert "assets" in entry
        assert "detail" in entry


# ---------------------------------------------------------------------------
# _format_diverging
# ---------------------------------------------------------------------------


class TestFormatDiverging:
    def test_single_pair(self):
        pairs = [_make_diverging_pair()]
        result = _format_diverging(pairs)
        assert "Nasdaq 100" in result
        assert "S&P 500" in result
        assert "-2.0%" in result
        assert "+1.5%" in result
        assert "r~0.90" in result

    def test_empty_list(self):
        result = _format_diverging([])
        assert result == "No normally-correlated pairs diverging today."

    def test_multiple_pairs(self):
        pairs = [
            _make_diverging_pair(),
            _make_diverging_pair(
                symbol_a="USO", symbol_b="CPER",
                label_a="Crude Oil (USO)", label_b="Copper (CPER)",
                change_pct_a=3.0, change_pct_b=-1.0,
                baseline_r=0.75,
            ),
        ]
        result = _format_diverging(pairs)
        lines = result.split("\n")
        assert len(lines) == 2


# ---------------------------------------------------------------------------
# build_premarket_prompt
# ---------------------------------------------------------------------------


class TestBuildPremarketPrompt:
    def test_contains_date(self):
        regime = _make_regime()
        corr_1d = _make_corr()
        prompt = build_premarket_prompt("2026-02-09", regime, corr_1d)
        assert "2026-02-09" in prompt

    def test_contains_regime_info(self):
        regime = _make_regime(label="RISK-ON", reason="VIXY falling; S&P above MA")
        corr_1d = _make_corr()
        prompt = build_premarket_prompt("2026-02-09", regime, corr_1d)
        assert "RISK-ON" in prompt
        assert "VIXY falling" in prompt

    def test_contains_overnight_section(self):
        """International groups should appear in the overnight section."""
        intl_group = _make_group(
            direction="up", avg_change_pct=1.5,
            symbols=["EWJ", "UKX"],
        )
        corr_1d = _make_corr(groups=[intl_group])
        regime = _make_regime()
        prompt = build_premarket_prompt("2026-02-09", regime, corr_1d)
        # EWJ is international, so overnight section should have data
        assert "Japan (EWJ)" in prompt

    def test_contains_crypto_section(self):
        """Crypto groups should appear in the crypto section."""
        crypto_group = _make_group(
            direction="up", avg_change_pct=5.0,
            symbols=["BTC/USD", "ETH/USD"],
        )
        corr_1d = _make_corr(groups=[crypto_group])
        regime = _make_regime()
        prompt = build_premarket_prompt("2026-02-09", regime, corr_1d)
        assert "Bitcoin" in prompt

    def test_no_overnight_moves(self):
        """When no international groups, 'No significant overnight moves' appears."""
        corr_1d = _make_corr(groups=[])
        regime = _make_regime()
        prompt = build_premarket_prompt("2026-02-09", regime, corr_1d)
        assert "No significant overnight moves" in prompt

    def test_no_crypto_moves(self):
        """When no crypto groups, 'No significant crypto moves' appears."""
        corr_1d = _make_corr(groups=[])
        regime = _make_regime()
        prompt = build_premarket_prompt("2026-02-09", regime, corr_1d)
        assert "No significant crypto moves" in prompt

    def test_contains_anomalies(self):
        anomaly = _make_anomaly(detail="BTC tracking SPX unusually")
        corr_1d = _make_corr(anomalies=[anomaly])
        regime = _make_regime()
        prompt = build_premarket_prompt("2026-02-09", regime, corr_1d)
        assert "BTC tracking SPX unusually" in prompt

    def test_contains_comovement_summary(self):
        group = _make_group(direction="up", avg_change_pct=2.0, symbols=["SPY", "QQQ"])
        corr_1d = _make_corr(groups=[group])
        regime = _make_regime()
        prompt = build_premarket_prompt("2026-02-09", regime, corr_1d)
        assert "S&P 500" in prompt
        assert "Nasdaq 100" in prompt

    def test_contains_diverging_section(self):
        pair = _make_diverging_pair()
        corr_1d = _make_corr(diverging=[pair])
        regime = _make_regime()
        prompt = build_premarket_prompt("2026-02-09", regime, corr_1d)
        assert "normally correlated" in prompt


# ---------------------------------------------------------------------------
# build_close_prompt
# ---------------------------------------------------------------------------


class TestBuildClosePrompt:
    def test_contains_date(self):
        regime = _make_regime()
        corr_1d = _make_corr()
        corr_1m = _make_corr(period="1M")
        prompt = build_close_prompt("2026-02-09", regime, corr_1d, corr_1m)
        assert "2026-02-09" in prompt

    def test_contains_regime_label_and_reason(self):
        regime = _make_regime(label="RISK-OFF", reason="VIXY spiking; HY spreads widening")
        corr_1d = _make_corr()
        corr_1m = _make_corr(period="1M")
        prompt = build_close_prompt("2026-02-09", regime, corr_1d, corr_1m)
        assert "RISK-OFF" in prompt
        assert "VIXY spiking" in prompt

    def test_contains_regime_signals(self):
        signals = [
            {"name": "vix", "direction": "risk_off", "detail": "VIXY spiking (+8.0%)"},
            {"name": "hy_spread", "direction": "risk_off", "detail": "HY spreads widening (+15 bps WoW)"},
        ]
        regime = _make_regime(signals=signals)
        corr_1d = _make_corr()
        corr_1m = _make_corr(period="1M")
        prompt = build_close_prompt("2026-02-09", regime, corr_1d, corr_1m)
        assert "- vix (risk_off): VIXY spiking (+8.0%)" in prompt
        assert "- hy_spread (risk_off): HY spreads widening" in prompt

    def test_contains_1d_comovement(self):
        group = _make_group(direction="up", avg_change_pct=2.0, symbols=["SPY", "QQQ"])
        corr_1d = _make_corr(groups=[group])
        corr_1m = _make_corr(period="1M")
        regime = _make_regime()
        prompt = build_close_prompt("2026-02-09", regime, corr_1d, corr_1m)
        assert "S&P 500" in prompt
        assert "Rallying together" in prompt

    def test_contains_1m_comovement(self):
        corr_1d = _make_corr()
        group_1m = _make_group(direction="down", avg_change_pct=-1.0, symbols=["USO", "CPER"])
        corr_1m = _make_corr(period="1M", groups=[group_1m])
        regime = _make_regime()
        prompt = build_close_prompt("2026-02-09", regime, corr_1d, corr_1m)
        assert "Crude Oil (USO)" in prompt
        assert "Selling together" in prompt

    def test_contains_scarcity_section(self):
        anomaly = _make_anomaly(
            anomaly_type="scarcity_divergence",
            detail="Uranium ETF diverging from S&P 500",
        )
        corr_1d = _make_corr(anomalies=[anomaly])
        corr_1m = _make_corr(period="1M")
        regime = _make_regime()
        prompt = build_close_prompt("2026-02-09", regime, corr_1d, corr_1m)
        assert "Uranium ETF diverging" in prompt

    def test_contains_anomalies_from_both_periods(self):
        anomaly_1d = _make_anomaly(detail="1D anomaly detected")
        anomaly_1m = _make_anomaly(detail="1M anomaly detected")
        corr_1d = _make_corr(anomalies=[anomaly_1d])
        corr_1m = _make_corr(period="1M", anomalies=[anomaly_1m])
        regime = _make_regime()
        prompt = build_close_prompt("2026-02-09", regime, corr_1d, corr_1m)
        assert "1D anomaly detected" in prompt
        assert "1M anomaly detected" in prompt

    def test_contains_diverging_section(self):
        pair = _make_diverging_pair()
        corr_1d = _make_corr(diverging=[pair])
        corr_1m = _make_corr(period="1M")
        regime = _make_regime()
        prompt = build_close_prompt("2026-02-09", regime, corr_1d, corr_1m)
        assert "normally correlated" in prompt


# ---------------------------------------------------------------------------
# _call_anthropic
# ---------------------------------------------------------------------------


class TestCallAnthropic:
    @pytest.mark.asyncio
    async def test_returns_response_text(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Markets rallied today.")]
        mock_client.messages.create.return_value = mock_response

        result = await _call_anthropic("system prompt", "user prompt", client=mock_client)
        assert result == "Markets rallied today."

    @pytest.mark.asyncio
    async def test_passes_correct_model(self):
        from backend.config import SUMMARY_CONFIG

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="test")]
        mock_client.messages.create.return_value = mock_response

        await _call_anthropic("system", "user", client=mock_client)

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["model"] == str(SUMMARY_CONFIG["model"])

    @pytest.mark.asyncio
    async def test_passes_correct_max_tokens(self):
        from backend.config import SUMMARY_CONFIG

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="test")]
        mock_client.messages.create.return_value = mock_response

        await _call_anthropic("system", "user", client=mock_client)

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["max_tokens"] == int(SUMMARY_CONFIG["max_tokens"])

    @pytest.mark.asyncio
    async def test_passes_correct_temperature(self):
        from backend.config import SUMMARY_CONFIG

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="test")]
        mock_client.messages.create.return_value = mock_response

        await _call_anthropic("system", "user", client=mock_client)

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["temperature"] == float(SUMMARY_CONFIG["temperature"])

    @pytest.mark.asyncio
    async def test_passes_system_and_user_prompts(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="test")]
        mock_client.messages.create.return_value = mock_response

        await _call_anthropic("my system prompt", "my user prompt", client=mock_client)

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["system"] == "my system prompt"
        assert call_kwargs.kwargs["messages"] == [{"role": "user", "content": "my user prompt"}]

    @pytest.mark.asyncio
    async def test_api_error_propagates(self):
        mock_client = AsyncMock()
        mock_client.messages.create.side_effect = RuntimeError("API timeout")

        with pytest.raises(RuntimeError, match="API timeout"):
            await _call_anthropic("system", "user", client=mock_client)


# ---------------------------------------------------------------------------
# generate_premarket
# ---------------------------------------------------------------------------


class TestGeneratePremarket:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Overnight markets were calm.")]
        mock_client.messages.create.return_value = mock_response

        regime = _make_regime()
        corr_1d = _make_corr()

        result = await generate_premarket(regime, corr_1d, client=mock_client)

        assert result["period"] == "premarket"
        assert result["summary_text"] == "Overnight markets were calm."
        assert result["regime_label"] == regime["label"]
        assert result["regime_reason"] == regime["reason"]
        assert result["timestamp"]
        assert isinstance(result["moving_together"], list)

    @pytest.mark.asyncio
    async def test_api_failure_returns_fallback(self):
        mock_client = AsyncMock()
        mock_client.messages.create.side_effect = RuntimeError("API down")

        regime = _make_regime(label="MIXED", reason="Conflicting signals")
        corr_1d = _make_corr()

        result = await generate_premarket(regime, corr_1d, client=mock_client)

        assert result["period"] == "premarket"
        assert result["summary_text"].startswith(_FALLBACK_PREFIX)
        assert "MIXED" in result["summary_text"]
        assert result["regime_label"] == "MIXED"

    @pytest.mark.asyncio
    async def test_moving_together_populated(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Summary text.")]
        mock_client.messages.create.return_value = mock_response

        group = _make_group(direction="up", avg_change_pct=2.0, symbols=["SPY", "QQQ"])
        regime = _make_regime()
        corr_1d = _make_corr(groups=[group])

        result = await generate_premarket(regime, corr_1d, client=mock_client)

        assert len(result["moving_together"]) == 1
        assert result["moving_together"][0]["label"] == "Up"

    @pytest.mark.asyncio
    async def test_moving_together_empty_when_no_groups(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Summary.")]
        mock_client.messages.create.return_value = mock_response

        regime = _make_regime()
        corr_1d = _make_corr(groups=[])

        result = await generate_premarket(regime, corr_1d, client=mock_client)
        assert result["moving_together"] == []

    @pytest.mark.asyncio
    async def test_result_has_summary_result_shape(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="OK.")]
        mock_client.messages.create.return_value = mock_response

        regime = _make_regime()
        corr_1d = _make_corr()

        result = await generate_premarket(regime, corr_1d, client=mock_client)

        required_keys = {"period", "summary_text", "moving_together",
                         "regime_label", "regime_reason", "timestamp"}
        assert required_keys == set(result.keys())


# ---------------------------------------------------------------------------
# generate_close
# ---------------------------------------------------------------------------


class TestGenerateClose:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Markets closed higher today.")]
        mock_client.messages.create.return_value = mock_response

        regime = _make_regime(label="RISK-ON", reason="VIXY falling")
        corr_1d = _make_corr()
        corr_1m = _make_corr(period="1M")

        result = await generate_close(regime, corr_1d, corr_1m, client=mock_client)

        assert result["period"] == "close"
        assert result["summary_text"] == "Markets closed higher today."
        assert result["regime_label"] == "RISK-ON"
        assert result["regime_reason"] == "VIXY falling"
        assert result["timestamp"]

    @pytest.mark.asyncio
    async def test_api_failure_returns_fallback(self):
        mock_client = AsyncMock()
        mock_client.messages.create.side_effect = RuntimeError("API error")

        regime = _make_regime(label="RISK-OFF", reason="VIXY spiking; HY widening")
        corr_1d = _make_corr()
        corr_1m = _make_corr(period="1M")

        result = await generate_close(regime, corr_1d, corr_1m, client=mock_client)

        assert result["period"] == "close"
        assert result["summary_text"].startswith(_FALLBACK_PREFIX)
        assert "RISK-OFF" in result["summary_text"]

    @pytest.mark.asyncio
    async def test_regime_label_and_reason_pass_through(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="text")]
        mock_client.messages.create.return_value = mock_response

        regime = _make_regime(label="MIXED", reason="Conflicting signals in credit and equity")
        corr_1d = _make_corr()
        corr_1m = _make_corr(period="1M")

        result = await generate_close(regime, corr_1d, corr_1m, client=mock_client)

        assert result["regime_label"] == "MIXED"
        assert result["regime_reason"] == "Conflicting signals in credit and equity"

    @pytest.mark.asyncio
    async def test_moving_together_from_1d_groups(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Summary.")]
        mock_client.messages.create.return_value = mock_response

        group = _make_group(direction="down", avg_change_pct=-3.0, symbols=["USO", "CPER"])
        regime = _make_regime()
        corr_1d = _make_corr(groups=[group])
        corr_1m = _make_corr(period="1M")

        result = await generate_close(regime, corr_1d, corr_1m, client=mock_client)

        assert len(result["moving_together"]) == 1
        assert result["moving_together"][0]["label"] == "Down"

    @pytest.mark.asyncio
    async def test_fallback_includes_1m_data(self):
        mock_client = AsyncMock()
        mock_client.messages.create.side_effect = RuntimeError("fail")

        group_1m = _make_group(direction="up", avg_change_pct=1.0, symbols=["SPY", "QQQ"])
        regime = _make_regime()
        corr_1d = _make_corr()
        corr_1m = _make_corr(period="1M", groups=[group_1m])

        result = await generate_close(regime, corr_1d, corr_1m, client=mock_client)

        assert "Monthly co-movement:" in result["summary_text"]
        assert "S&P 500" in result["summary_text"]


# ---------------------------------------------------------------------------
# _build_fallback_summary
# ---------------------------------------------------------------------------


class TestBuildFallbackSummary:
    def test_starts_with_prefix(self):
        regime = _make_regime()
        corr_1d = _make_corr()
        result = _build_fallback_summary("premarket", regime, corr_1d)
        assert result.startswith(_FALLBACK_PREFIX)

    def test_contains_regime_label_and_reason(self):
        regime = _make_regime(label="RISK-OFF", reason="VIXY spiking; HY widening")
        corr_1d = _make_corr()
        result = _build_fallback_summary("close", regime, corr_1d)
        assert "RISK-OFF" in result
        assert "VIXY spiking; HY widening" in result

    def test_contains_comovement_data(self):
        group = _make_group(direction="up", avg_change_pct=2.0, symbols=["SPY", "QQQ"])
        corr_1d = _make_corr(groups=[group])
        regime = _make_regime()
        result = _build_fallback_summary("close", regime, corr_1d)
        assert "S&P 500" in result
        assert "Rallying together" in result

    def test_includes_anomalies_when_present(self):
        anomaly = _make_anomaly(detail="BTC tracking SPX unusually")
        corr_1d = _make_corr(anomalies=[anomaly])
        regime = _make_regime()
        result = _build_fallback_summary("premarket", regime, corr_1d)
        assert "Anomalies:" in result
        assert "BTC tracking SPX unusually" in result

    def test_no_anomalies_section_when_none(self):
        corr_1d = _make_corr(anomalies=[])
        regime = _make_regime()
        result = _build_fallback_summary("premarket", regime, corr_1d)
        assert "Anomalies:" not in result

    def test_includes_1m_comovement_when_provided(self):
        corr_1d = _make_corr()
        group_1m = _make_group(direction="down", avg_change_pct=-1.5, symbols=["USO", "UNG"])
        corr_1m = _make_corr(period="1M", groups=[group_1m])
        regime = _make_regime()
        result = _build_fallback_summary("close", regime, corr_1d, corr_1m)
        assert "Monthly co-movement:" in result
        assert "Crude Oil (USO)" in result

    def test_no_1m_section_when_none(self):
        corr_1d = _make_corr()
        regime = _make_regime()
        result = _build_fallback_summary("premarket", regime, corr_1d)
        assert "Monthly co-movement:" not in result

    def test_premarket_with_empty_data(self):
        regime = _make_regime(label="MIXED", reason="Insufficient data")
        corr_1d = _make_corr()
        result = _build_fallback_summary("premarket", regime, corr_1d)
        assert _FALLBACK_PREFIX in result
        assert "MIXED" in result
        assert "Insufficient data" in result

    def test_includes_diverging_when_present(self):
        pair = _make_diverging_pair()
        corr_1d = _make_corr(diverging=[pair])
        regime = _make_regime()
        result = _build_fallback_summary("premarket", regime, corr_1d)
        assert "Diverging:" in result

    def test_no_diverging_section_when_empty(self):
        corr_1d = _make_corr(diverging=[])
        regime = _make_regime()
        result = _build_fallback_summary("premarket", regime, corr_1d)
        assert "Diverging:" not in result


# ---------------------------------------------------------------------------
# Config consistency
# ---------------------------------------------------------------------------


class TestSummaryConfig:
    def test_summary_config_has_model(self):
        from backend.config import SUMMARY_CONFIG
        assert "model" in SUMMARY_CONFIG
        assert isinstance(SUMMARY_CONFIG["model"], str)

    def test_summary_config_has_max_tokens(self):
        from backend.config import SUMMARY_CONFIG
        assert "max_tokens" in SUMMARY_CONFIG
        assert int(SUMMARY_CONFIG["max_tokens"]) > 0

    def test_summary_config_has_temperature(self):
        from backend.config import SUMMARY_CONFIG
        assert "temperature" in SUMMARY_CONFIG
        temp = float(SUMMARY_CONFIG["temperature"])
        assert 0.0 <= temp <= 1.0

    def test_premarket_template_has_placeholders(self):
        from backend.config import PREMARKET_USER_TEMPLATE
        required_placeholders = [
            "{date}",
            "{overnight_data}",
            "{crypto_data}",
            "{regime_label}",
            "{regime_reason}",
            "{comovement_summary}",
            "{diverging_1d}",
            "{anomalies_summary}",
        ]
        for ph in required_placeholders:
            assert ph in PREMARKET_USER_TEMPLATE, f"Missing placeholder: {ph}"

    def test_close_template_has_placeholders(self):
        from backend.config import CLOSE_USER_TEMPLATE
        required_placeholders = [
            "{date}",
            "{regime_label}",
            "{regime_reason}",
            "{regime_signals}",
            "{comovement_1d}",
            "{diverging_1d}",
            "{comovement_1m}",
            "{anomalies_1d}",
            "{anomalies_1m}",
            "{scarcity_summary}",
        ]
        for ph in required_placeholders:
            assert ph in CLOSE_USER_TEMPLATE, f"Missing placeholder: {ph}"

    def test_system_prompt_is_non_empty_string(self):
        from backend.config import SUMMARY_SYSTEM_PROMPT
        assert isinstance(SUMMARY_SYSTEM_PROMPT, str)
        assert len(SUMMARY_SYSTEM_PROMPT) > 50

    def test_fallback_prefix_is_non_empty(self):
        assert isinstance(_FALLBACK_PREFIX, str)
        assert len(_FALLBACK_PREFIX) > 0
