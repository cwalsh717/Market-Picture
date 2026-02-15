"""Claude API narrative generation using structured JSON payloads.

Receives an enriched narrative payload from ``narrative_data.py``, serializes
it as JSON, calls the Anthropic API with the new structured system prompt,
and returns the generated narrative text.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TypedDict
from zoneinfo import ZoneInfo

import anthropic

from backend.config import ANTHROPIC_API_KEY, NARRATIVE_SYSTEM_PROMPT, SUMMARY_CONFIG

logger = logging.getLogger(__name__)

_ET = ZoneInfo("US/Eastern")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class SummaryResult(TypedDict):
    """Output of a narrative generation call."""

    period: str          # "premarket" | "close"
    summary_text: str    # LLM narrative (or fallback)
    regime_label: str
    regime_reason: str
    timestamp: str       # ISO-8601


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


def _build_fallback(payload: dict) -> str:
    """Build a structured plain-text summary when the API is unavailable.

    Uses enriched payload data when available for a richer fallback.
    """
    regime = payload.get("regime", {})
    label = regime.get("label", "UNKNOWN")
    confidence = regime.get("confidence", "")

    parts = [
        _FALLBACK_PREFIX,
        "",
        f"Market Regime: {label}",
        confidence,
    ]

    # Include top movers from asset_snapshot
    assets = payload.get("asset_snapshot", {})
    movers = sorted(
        (
            (sym, d.get("change_pct", 0) or 0)
            for sym, d in assets.items()
            if d.get("change_pct") is not None
        ),
        key=lambda x: abs(x[1]),
        reverse=True,
    )[:5]

    if movers:
        parts.append("")
        parts.append("Top movers:")
        for sym, pct in movers:
            parts.append(f"  {sym}: {pct:+.2f}%")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def generate_narrative(
    payload: dict,
    client: anthropic.AsyncAnthropic | None = None,
) -> SummaryResult:
    """Generate a narrative from an enriched payload.

    Serializes the payload as JSON and sends it to the Claude API with
    the structured system prompt.  Falls back to a plain-text summary
    on API failure.

    Args:
        payload: Structured dict from ``assemble_narrative_payload()``.
        client:  Optional injectable Anthropic client (for testing).
    """
    now_et = datetime.now(_ET)
    date_str = now_et.strftime("%Y-%m-%d")
    day_of_week = now_et.strftime("%A")

    system_prompt = NARRATIVE_SYSTEM_PROMPT.format(
        day_of_week=day_of_week,
        date=date_str,
    )

    user_message = json.dumps(payload, indent=2, default=str)

    narrative_type = payload.get("narrative_type", "after_close")
    period = "premarket" if narrative_type == "pre_market" else "close"

    regime = payload.get("regime", {})
    label = regime.get("label", "UNKNOWN")
    confidence = regime.get("confidence", "")

    try:
        text = await _call_anthropic(system_prompt, user_message, client)
    except Exception:
        logger.exception("Anthropic API call failed for %s narrative", period)
        text = _build_fallback(payload)

    return SummaryResult(
        period=period,
        summary_text=text,
        regime_label=label,
        regime_reason=confidence,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
