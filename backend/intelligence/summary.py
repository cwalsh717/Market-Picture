"""Claude API summary generation for pre-market and after-close narratives.

Builds structured prompts from regime classification output, calls the
Anthropic API, and returns user-facing summaries with plain-English narratives.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TypedDict

import anthropic

from backend.config import (
    ANTHROPIC_API_KEY,
    CLOSE_USER_TEMPLATE,
    PREMARKET_USER_TEMPLATE,
    SUMMARY_CONFIG,
    SUMMARY_SYSTEM_PROMPT,
)
from backend.intelligence.regime import RegimeResult, Signal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class SummaryResult(TypedDict):
    """Output of a summary generation call."""

    period: str                          # "premarket" | "close"
    summary_text: str                    # LLM narrative (or fallback)
    regime_label: str
    regime_reason: str
    timestamp: str                       # ISO-8601


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_regime_signals(signals: list[Signal]) -> str:
    """Format individual regime signals into readable lines."""
    return "\n".join(
        f"- {s['name']} ({s['direction']}): {s['detail']}" for s in signals
    )


# ---------------------------------------------------------------------------
# Prompt builders (public for testability)
# ---------------------------------------------------------------------------


def build_premarket_prompt(
    date_str: str,
    regime: RegimeResult,
) -> str:
    """Build the user prompt for the pre-market summary."""
    return PREMARKET_USER_TEMPLATE.format(
        date=date_str,
        regime_label=regime["label"],
        regime_reason=regime["reason"],
        regime_signals=_format_regime_signals(regime["signals"]),
    )


def build_close_prompt(
    date_str: str,
    regime: RegimeResult,
) -> str:
    """Build the user prompt for the after-close summary."""
    return CLOSE_USER_TEMPLATE.format(
        date=date_str,
        regime_label=regime["label"],
        regime_reason=regime["reason"],
        regime_signals=_format_regime_signals(regime["signals"]),
    )


# ---------------------------------------------------------------------------
# Anthropic API call
# ---------------------------------------------------------------------------


async def _call_anthropic(
    system_prompt: str,
    user_prompt: str,
    client: anthropic.AsyncAnthropic | None = None,
) -> str:
    """Call the Anthropic API and return the response text."""
    if client is None:
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    response = await client.messages.create(
        model=str(SUMMARY_CONFIG["model"]),
        max_tokens=int(SUMMARY_CONFIG["max_tokens"]),
        temperature=float(SUMMARY_CONFIG["temperature"]),
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------

_FALLBACK_PREFIX = "[Auto-generated \u2014 LLM summary unavailable]"


def _build_fallback_summary(
    period: str,
    regime: RegimeResult,
) -> str:
    """Build a structured plain-text summary when the API is unavailable."""
    parts = [
        _FALLBACK_PREFIX,
        "",
        f"Market Regime: {regime['label']}",
        regime["reason"],
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def generate_premarket(
    regime: RegimeResult,
    client: anthropic.AsyncAnthropic | None = None,
) -> SummaryResult:
    """Generate the pre-market summary (~8 AM ET).

    Calls the Anthropic API with regime data.
    Falls back to a structured plain-text summary on API failure.
    """
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prompt = build_premarket_prompt(date_str, regime)

    try:
        text = await _call_anthropic(SUMMARY_SYSTEM_PROMPT, prompt, client)
    except Exception:
        logger.exception("Anthropic API call failed for premarket summary")
        text = _build_fallback_summary("premarket", regime)

    return SummaryResult(
        period="premarket",
        summary_text=text,
        regime_label=regime["label"],
        regime_reason=regime["reason"],
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


async def generate_close(
    regime: RegimeResult,
    client: anthropic.AsyncAnthropic | None = None,
) -> SummaryResult:
    """Generate the after-close summary (~4:30 PM ET).

    Calls the Anthropic API with regime data.
    Falls back to a structured plain-text summary on API failure.
    """
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prompt = build_close_prompt(date_str, regime)

    try:
        text = await _call_anthropic(SUMMARY_SYSTEM_PROMPT, prompt, client)
    except Exception:
        logger.exception("Anthropic API call failed for close summary")
        text = _build_fallback_summary("close", regime)

    return SummaryResult(
        period="close",
        summary_text=text,
        regime_label=regime["label"],
        regime_reason=regime["reason"],
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
