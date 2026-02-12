"""Tests for the Claude API summary generation module.

Covers:
- _format_regime_signals: Signal list → prompt text
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
    SummaryResult,
    _build_fallback_summary,
    _format_regime_signals,
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
# build_premarket_prompt
# ---------------------------------------------------------------------------


class TestBuildPremarketPrompt:
    def test_contains_date(self):
        regime = _make_regime()
        prompt = build_premarket_prompt("2026-02-09", regime)
        assert "2026-02-09" in prompt

    def test_contains_regime_info(self):
        regime = _make_regime(label="RISK-ON", reason="VIXY falling; S&P above MA")
        prompt = build_premarket_prompt("2026-02-09", regime)
        assert "RISK-ON" in prompt
        assert "VIXY falling" in prompt

    def test_contains_signals(self):
        signals = [
            {"name": "vix", "direction": "risk_on", "detail": "VIXY falling (-7.0%)"},
        ]
        regime = _make_regime(signals=signals)
        prompt = build_premarket_prompt("2026-02-09", regime)
        assert "- vix (risk_on): VIXY falling (-7.0%)" in prompt


# ---------------------------------------------------------------------------
# build_close_prompt
# ---------------------------------------------------------------------------


class TestBuildClosePrompt:
    def test_contains_date(self):
        regime = _make_regime()
        prompt = build_close_prompt("2026-02-09", regime)
        assert "2026-02-09" in prompt

    def test_contains_regime_label_and_reason(self):
        regime = _make_regime(label="RISK-OFF", reason="VIXY spiking; HY spreads widening")
        prompt = build_close_prompt("2026-02-09", regime)
        assert "RISK-OFF" in prompt
        assert "VIXY spiking" in prompt

    def test_contains_regime_signals(self):
        signals = [
            {"name": "vix", "direction": "risk_off", "detail": "VIXY spiking (+8.0%)"},
            {"name": "hy_spread", "direction": "risk_off", "detail": "HY spreads widening (+15 bps WoW)"},
        ]
        regime = _make_regime(signals=signals)
        prompt = build_close_prompt("2026-02-09", regime)
        assert "- vix (risk_off): VIXY spiking (+8.0%)" in prompt
        assert "- hy_spread (risk_off): HY spreads widening" in prompt


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

        result = await generate_premarket(regime, client=mock_client)

        assert result["period"] == "premarket"
        assert result["summary_text"] == "Overnight markets were calm."
        assert result["regime_label"] == regime["label"]
        assert result["regime_reason"] == regime["reason"]
        assert result["timestamp"]

    @pytest.mark.asyncio
    async def test_api_failure_returns_fallback(self):
        mock_client = AsyncMock()
        mock_client.messages.create.side_effect = RuntimeError("API down")

        regime = _make_regime(label="MIXED", reason="Conflicting signals")

        result = await generate_premarket(regime, client=mock_client)

        assert result["period"] == "premarket"
        assert result["summary_text"].startswith(_FALLBACK_PREFIX)
        assert "MIXED" in result["summary_text"]
        assert result["regime_label"] == "MIXED"

    @pytest.mark.asyncio
    async def test_result_has_summary_result_shape(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="OK.")]
        mock_client.messages.create.return_value = mock_response

        regime = _make_regime()

        result = await generate_premarket(regime, client=mock_client)

        required_keys = {"period", "summary_text",
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

        result = await generate_close(regime, client=mock_client)

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

        result = await generate_close(regime, client=mock_client)

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

        result = await generate_close(regime, client=mock_client)

        assert result["regime_label"] == "MIXED"
        assert result["regime_reason"] == "Conflicting signals in credit and equity"


# ---------------------------------------------------------------------------
# _build_fallback_summary
# ---------------------------------------------------------------------------


class TestBuildFallbackSummary:
    def test_starts_with_prefix(self):
        regime = _make_regime()
        result = _build_fallback_summary("premarket", regime)
        assert result.startswith(_FALLBACK_PREFIX)

    def test_contains_regime_label_and_reason(self):
        regime = _make_regime(label="RISK-OFF", reason="VIXY spiking; HY widening")
        result = _build_fallback_summary("close", regime)
        assert "RISK-OFF" in result
        assert "VIXY spiking; HY widening" in result

    def test_premarket_with_empty_signals(self):
        regime = _make_regime(label="MIXED", reason="Insufficient data")
        result = _build_fallback_summary("premarket", regime)
        assert _FALLBACK_PREFIX in result
        assert "MIXED" in result
        assert "Insufficient data" in result


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
            "{regime_label}",
            "{regime_reason}",
            "{regime_signals}",
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
