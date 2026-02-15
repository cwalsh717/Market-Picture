"""Tests for the Claude API narrative generation module.

Covers:
- _call_anthropic: Anthropic API call with mocked client
- generate_narrative: full narrative generation (happy + fallback)
- _build_fallback: structured fallback text with enriched data
- Config consistency for summary settings
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.intelligence.summary import (
    SummaryResult,
    _build_fallback,
    _call_anthropic,
    _FALLBACK_PREFIX,
    generate_narrative,
)


# ---------------------------------------------------------------------------
# Test data factories
# ---------------------------------------------------------------------------


def _make_payload(
    narrative_type: str = "after_close",
    label: str = "RISK-ON",
    confidence: str = "3 of 5 signals bullish",
) -> dict:
    """Build a narrative payload dict for tests."""
    return {
        "generated_at": "2026-02-09T20:00:00+00:00",
        "narrative_type": narrative_type,
        "data_freshness": "close",
        "regime": {
            "label": label,
            "changed_since_last": False,
            "previous_label": label,
            "signals": {
                "sp500_trend": {"signal": "bullish", "detail": "SPY above 50-day MA"},
                "vix": {"signal": "low", "level": 12.5},
                "credit_spreads": {"signal": "tightening", "ig": 1.1, "hy": 3.2},
                "yield_curve": {"signal": "normal", "spread_2s10s": 0.45},
                "usd_strength": {"signal": "stable", "detail": "UUP 7d change +0.1%"},
            },
            "confidence": confidence,
        },
        "asset_snapshot": {
            "SPY": {"price": 5200.0, "change_pct": 0.75},
            "QQQ": {"price": 18500.0, "change_pct": 1.2},
            "BTC/USD": {"price": 98000.0, "change_pct": -0.3},
        },
        "rates": {
            "us_2y": 4.15,
            "us_10y": 4.60,
            "spread_2s10s": 0.45,
            "ig_spread": 1.1,
            "hy_spread": 3.2,
        },
        "previous_narrative": None,
    }


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
# generate_narrative
# ---------------------------------------------------------------------------


class TestGenerateNarrative:
    @pytest.mark.asyncio
    async def test_happy_path_close(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="SPY gained 0.75% as risk appetite held firm.")]
        mock_client.messages.create.return_value = mock_response

        payload = _make_payload(narrative_type="after_close")

        result = await generate_narrative(payload, client=mock_client)

        assert result["period"] == "close"
        assert result["summary_text"] == "SPY gained 0.75% as risk appetite held firm."
        assert result["regime_label"] == "RISK-ON"
        assert result["timestamp"]

    @pytest.mark.asyncio
    async def test_happy_path_premarket(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Overnight markets were calm.")]
        mock_client.messages.create.return_value = mock_response

        payload = _make_payload(narrative_type="pre_market")

        result = await generate_narrative(payload, client=mock_client)

        assert result["period"] == "premarket"
        assert result["summary_text"] == "Overnight markets were calm."

    @pytest.mark.asyncio
    async def test_api_failure_returns_fallback(self):
        mock_client = AsyncMock()
        mock_client.messages.create.side_effect = RuntimeError("API down")

        payload = _make_payload(label="RISK-OFF", confidence="4 of 5 signals bearish")

        result = await generate_narrative(payload, client=mock_client)

        assert result["summary_text"].startswith(_FALLBACK_PREFIX)
        assert "RISK-OFF" in result["summary_text"]
        assert result["regime_label"] == "RISK-OFF"

    @pytest.mark.asyncio
    async def test_result_has_summary_result_shape(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="OK.")]
        mock_client.messages.create.return_value = mock_response

        payload = _make_payload()

        result = await generate_narrative(payload, client=mock_client)

        required_keys = {"period", "summary_text", "regime_label", "regime_reason", "timestamp"}
        assert required_keys == set(result.keys())

    @pytest.mark.asyncio
    async def test_system_prompt_includes_date(self):
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="test")]
        mock_client.messages.create.return_value = mock_response

        payload = _make_payload()
        await generate_narrative(payload, client=mock_client)

        call_kwargs = mock_client.messages.create.call_args
        system = call_kwargs.kwargs["system"]
        # Should contain day name and date
        assert "Today is" in system

    @pytest.mark.asyncio
    async def test_user_message_is_json(self):
        import json

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="test")]
        mock_client.messages.create.return_value = mock_response

        payload = _make_payload()
        await generate_narrative(payload, client=mock_client)

        call_kwargs = mock_client.messages.create.call_args
        user_msg = call_kwargs.kwargs["messages"][0]["content"]
        # Should be valid JSON
        parsed = json.loads(user_msg)
        assert parsed["regime"]["label"] == "RISK-ON"


# ---------------------------------------------------------------------------
# _build_fallback
# ---------------------------------------------------------------------------


class TestBuildFallback:
    def test_starts_with_prefix(self):
        payload = _make_payload()
        result = _build_fallback(payload)
        assert result.startswith(_FALLBACK_PREFIX)

    def test_contains_regime_label(self):
        payload = _make_payload(label="RISK-OFF")
        result = _build_fallback(payload)
        assert "RISK-OFF" in result

    def test_contains_confidence(self):
        payload = _make_payload(confidence="4 of 5 signals bearish")
        result = _build_fallback(payload)
        assert "4 of 5 signals bearish" in result

    def test_includes_top_movers(self):
        payload = _make_payload()
        result = _build_fallback(payload)
        assert "Top movers:" in result
        assert "QQQ" in result  # QQQ has +1.2%, largest abs move

    def test_empty_asset_snapshot(self):
        payload = _make_payload()
        payload["asset_snapshot"] = {}
        result = _build_fallback(payload)
        assert result.startswith(_FALLBACK_PREFIX)
        assert "Top movers:" not in result


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

    def test_narrative_system_prompt_has_placeholders(self):
        from backend.config import NARRATIVE_SYSTEM_PROMPT
        assert "{day_of_week}" in NARRATIVE_SYSTEM_PROMPT
        assert "{date}" in NARRATIVE_SYSTEM_PROMPT

    def test_narrative_system_prompt_is_non_empty(self):
        from backend.config import NARRATIVE_SYSTEM_PROMPT
        assert isinstance(NARRATIVE_SYSTEM_PROMPT, str)
        assert len(NARRATIVE_SYSTEM_PROMPT) > 100

    def test_fallback_prefix_is_non_empty(self):
        assert isinstance(_FALLBACK_PREFIX, str)
        assert len(_FALLBACK_PREFIX) > 0
