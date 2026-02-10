"""Claude API summary generation for pre-market and after-close narratives.

Builds structured prompts from regime classification and correlation
detection output, calls the Anthropic API, and returns user-facing
summaries with plain-English narratives and co-movement groupings.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TypedDict

import anthropic

from backend.config import (
    ANTHROPIC_API_KEY,
    ASSETS,
    CLOSE_USER_TEMPLATE,
    FRED_SERIES,
    PREMARKET_USER_TEMPLATE,
    SUMMARY_CONFIG,
    SUMMARY_SYSTEM_PROMPT,
)
from backend.intelligence.correlations import (
    CoMovingGroup,
    CorrelationAnomaly,
    CorrelationResult,
)
from backend.intelligence.regime import RegimeResult, Signal

logger = logging.getLogger(__name__)

_SCARCITY_SYMBOLS = {"URA", "LIT", "REMX"}
_INTERNATIONAL_CLASSES = {"international"}
_CRYPTO_CLASS = "crypto"


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class MovingTogetherGroup(TypedDict):
    """A user-facing group of assets moving in concert."""

    label: str           # "Rallying together" | "Selling together"
    assets: list[str]    # Human-readable names
    detail: str          # e.g. "Up avg 2.1%"


class SummaryResult(TypedDict):
    """Output of a summary generation call."""

    period: str                          # "premarket" | "close"
    summary_text: str                    # LLM narrative (or fallback)
    moving_together: list[MovingTogetherGroup]
    regime_label: str
    regime_reason: str
    timestamp: str                       # ISO-8601


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _label(symbol: str) -> str:
    """Look up the human-readable name for a symbol."""
    for _cat, symbols in ASSETS.items():
        if symbol in symbols:
            return symbols[symbol]
    if symbol in FRED_SERIES:
        return FRED_SERIES[symbol]
    return symbol


def _asset_class(symbol: str) -> str | None:
    """Return the asset class for *symbol*, or ``None`` if unknown."""
    for cls, symbols in ASSETS.items():
        if symbol in symbols:
            return cls
    return None


# ---------------------------------------------------------------------------
# Pure formatters (prompt building blocks)
# ---------------------------------------------------------------------------


def _format_comovement_groups(groups: list[CoMovingGroup]) -> str:
    """Format co-movement groups into readable prompt text."""
    if not groups:
        return "No significant co-movement detected."
    lines: list[str] = []
    for g in groups:
        tag = "Rallying together" if g["direction"] == "up" else "Selling together"
        names = ", ".join(_label(s) for s in g["symbols"])
        lines.append(f"{tag} ({g['avg_change_pct']:+.1f}% avg): {names}")
    return "\n".join(lines)


def _format_anomalies(anomalies: list[CorrelationAnomaly]) -> str:
    """Format anomaly list into readable prompt text."""
    if not anomalies:
        return "No unusual correlation behavior detected."
    return "\n".join(a["detail"] for a in anomalies)


def _format_regime_signals(signals: list[Signal]) -> str:
    """Format individual regime signals into readable lines."""
    return "\n".join(
        f"- {s['name']} ({s['direction']}): {s['detail']}" for s in signals
    )


def _format_scarcity_summary(
    corr_1d: CorrelationResult,
    corr_1m: CorrelationResult | None = None,
) -> str:
    """Extract and format scarcity-related data from correlation results."""
    parts: list[str] = []

    # Scarcity anomalies from both periods
    for cr in [corr_1d] + ([corr_1m] if corr_1m else []):
        for a in cr["anomalies"]:
            if a["anomaly_type"] == "scarcity_divergence":
                parts.append(a["detail"])

    # Scarcity symbols in co-movement groups (1D)
    for g in corr_1d["groups"]:
        scarcity_in_group = [s for s in g["symbols"] if s in _SCARCITY_SYMBOLS]
        if scarcity_in_group:
            tag = "rallying" if g["direction"] == "up" else "selling"
            names = ", ".join(_label(s) for s in scarcity_in_group)
            parts.append(f"{names} {tag} with the group ({g['avg_change_pct']:+.1f}% avg)")

    if not parts:
        return "No notable scarcity-related moves today."
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Moving-together builder (structured output for frontend)
# ---------------------------------------------------------------------------


def _build_moving_together(
    groups: list[CoMovingGroup],
) -> list[MovingTogetherGroup]:
    """Transform internal CoMovingGroup into user-facing MovingTogetherGroup."""
    result: list[MovingTogetherGroup] = []
    for g in groups:
        label = "Rallying together" if g["direction"] == "up" else "Selling together"
        assets = [_label(s) for s in g["symbols"]]
        sign = "Up" if g["direction"] == "up" else "Down"
        detail = f"{sign} avg {abs(g['avg_change_pct']):.1f}%"
        result.append(MovingTogetherGroup(label=label, assets=assets, detail=detail))
    return result


# ---------------------------------------------------------------------------
# Prompt builders (public for testability)
# ---------------------------------------------------------------------------


def build_premarket_prompt(
    date_str: str,
    regime: RegimeResult,
    corr_1d: CorrelationResult,
) -> str:
    """Build the user prompt for the pre-market summary."""
    # Filter groups by asset class for overnight focus
    intl_groups = [
        g for g in corr_1d["groups"]
        if any(_asset_class(s) in _INTERNATIONAL_CLASSES for s in g["symbols"])
    ]
    crypto_groups = [
        g for g in corr_1d["groups"]
        if any(_asset_class(s) == _CRYPTO_CLASS for s in g["symbols"])
    ]

    return PREMARKET_USER_TEMPLATE.format(
        date=date_str,
        overnight_data=_format_comovement_groups(intl_groups) if intl_groups else "No significant overnight moves.",
        crypto_data=_format_comovement_groups(crypto_groups) if crypto_groups else "No significant crypto moves.",
        regime_label=regime["label"],
        regime_reason=regime["reason"],
        comovement_summary=_format_comovement_groups(corr_1d["groups"]),
        anomalies_summary=_format_anomalies(corr_1d["anomalies"]),
    )


def build_close_prompt(
    date_str: str,
    regime: RegimeResult,
    corr_1d: CorrelationResult,
    corr_1m: CorrelationResult,
) -> str:
    """Build the user prompt for the after-close summary."""
    return CLOSE_USER_TEMPLATE.format(
        date=date_str,
        regime_label=regime["label"],
        regime_reason=regime["reason"],
        regime_signals=_format_regime_signals(regime["signals"]),
        comovement_1d=_format_comovement_groups(corr_1d["groups"]),
        comovement_1m=_format_comovement_groups(corr_1m["groups"]),
        anomalies_1d=_format_anomalies(corr_1d["anomalies"]),
        anomalies_1m=_format_anomalies(corr_1m["anomalies"]),
        scarcity_summary=_format_scarcity_summary(corr_1d, corr_1m),
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
    corr_1d: CorrelationResult,
    corr_1m: CorrelationResult | None = None,
) -> str:
    """Build a structured plain-text summary when the API is unavailable."""
    parts = [
        _FALLBACK_PREFIX,
        "",
        f"Market Regime: {regime['label']}",
        regime["reason"],
        "",
        _format_comovement_groups(corr_1d["groups"]),
    ]
    if corr_1m:
        parts.append("")
        parts.append("Monthly co-movement:")
        parts.append(_format_comovement_groups(corr_1m["groups"]))

    anomalies_1d = _format_anomalies(corr_1d["anomalies"])
    if anomalies_1d != "No unusual correlation behavior detected.":
        parts.append("")
        parts.append("Anomalies:")
        parts.append(anomalies_1d)

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def generate_premarket(
    regime: RegimeResult,
    corr_1d: CorrelationResult,
    client: anthropic.AsyncAnthropic | None = None,
) -> SummaryResult:
    """Generate the pre-market summary (~8 AM ET).

    Calls the Anthropic API with regime and overnight correlation data.
    Falls back to a structured plain-text summary on API failure.
    """
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prompt = build_premarket_prompt(date_str, regime, corr_1d)

    try:
        text = await _call_anthropic(SUMMARY_SYSTEM_PROMPT, prompt, client)
    except Exception:
        logger.exception("Anthropic API call failed for premarket summary")
        text = _build_fallback_summary("premarket", regime, corr_1d)

    return SummaryResult(
        period="premarket",
        summary_text=text,
        moving_together=_build_moving_together(corr_1d["groups"]),
        regime_label=regime["label"],
        regime_reason=regime["reason"],
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


async def generate_close(
    regime: RegimeResult,
    corr_1d: CorrelationResult,
    corr_1m: CorrelationResult,
    client: anthropic.AsyncAnthropic | None = None,
) -> SummaryResult:
    """Generate the after-close summary (~4:30 PM ET).

    Calls the Anthropic API with regime, 1D, and 1M correlation data.
    Falls back to a structured plain-text summary on API failure.
    """
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prompt = build_close_prompt(date_str, regime, corr_1d, corr_1m)

    try:
        text = await _call_anthropic(SUMMARY_SYSTEM_PROMPT, prompt, client)
    except Exception:
        logger.exception("Anthropic API call failed for close summary")
        text = _build_fallback_summary("close", regime, corr_1d, corr_1m)

    return SummaryResult(
        period="close",
        summary_text=text,
        moving_together=_build_moving_together(corr_1d["groups"]),
        regime_label=regime["label"],
        regime_reason=regime["reason"],
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
