"""On-demand company-specific analysis using Claude API.

Assembles per-symbol data from the database (snapshot, technicals, current
regime) and sends it to the Anthropic API for a focused single-asset briefing.
Follows the same patterns as ``summary.py`` and ``narrative_data.py``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text

from backend.db import get_session

logger = logging.getLogger(__name__)

_ET = ZoneInfo("US/Eastern")

COMPANY_ANALYSIS_SYSTEM_PROMPT = """You are a market analyst writing a focused briefing on a single asset for a finance-aware reader. Be direct, factual, and concise.

Structure your response:

CURRENT POSITION (2-3 sentences): Price action, where it sits relative to recent range, any notable moves.

TECHNICAL PICTURE (2-3 sentences): Key levels from the data — RSI, moving averages, 52-week range position. Note any extreme readings or crossovers. Facts only.

CONTEXT (2-3 sentences): How this asset relates to the broader market regime and its asset class. Any cross-asset signals worth noting.

Keep it under 150 words. No predictions, no recommendations. Today is {day_of_week}, {date}."""


# ---------------------------------------------------------------------------
# Payload assembly
# ---------------------------------------------------------------------------


async def assemble_company_payload(symbol: str) -> dict:
    """Build enriched data payload for a single symbol from the DB.

    Reads the latest market snapshot, most recent technical signals, and
    current regime context.  Returns a dict suitable for JSON serialization
    and LLM prompt injection.
    """
    session = await get_session()
    try:
        # 1. Latest snapshot for this symbol
        snap_result = await session.execute(
            text("""
                SELECT s.symbol, s.price, s.change_pct, s.change_abs,
                       s.timestamp, s.average_volume,
                       s.fifty_two_week_high, s.fifty_two_week_low,
                       s.fifty_two_week_high_change_pct,
                       s.fifty_two_week_low_change_pct,
                       s.rolling_1d_change, s.rolling_7d_change
                FROM market_snapshots s
                INNER JOIN (
                    SELECT symbol, MAX(id) AS max_id
                    FROM market_snapshots
                    WHERE symbol = :symbol
                    GROUP BY symbol
                ) latest ON s.id = latest.max_id
            """),
            {"symbol": symbol},
        )
        snap_row = snap_result.mappings().first()

        # 2. Latest technical signals
        tech_result = await session.execute(
            text("""
                SELECT rsi_14, atr_14, sma_50, sma_200, close
                FROM technical_signals
                WHERE symbol = :symbol
                ORDER BY date DESC
                LIMIT 1
            """),
            {"symbol": symbol},
        )
        tech_row = tech_result.mappings().first()

        # 3. Current regime for context
        regime_result = await session.execute(
            text("""
                SELECT regime_label, regime_reason
                FROM summaries
                ORDER BY date DESC, id DESC
                LIMIT 1
            """),
        )
        regime_row = regime_result.mappings().first()
    finally:
        await session.close()

    return {
        "symbol": symbol,
        "snapshot": dict(snap_row) if snap_row else None,
        "technicals": dict(tech_row) if tech_row else None,
        "current_regime": dict(regime_row) if regime_row else None,
    }


# ---------------------------------------------------------------------------
# Analysis generation
# ---------------------------------------------------------------------------


async def generate_company_analysis(symbol: str) -> str:
    """Generate a Claude-powered analysis for a single symbol.

    Assembles the data payload, formats the system prompt with today's date,
    and calls the Anthropic API via the shared ``_call_anthropic`` helper.

    Args:
        symbol: Ticker symbol (e.g. ``"SPY"``, ``"BTC/USD"``).

    Returns:
        The analysis text string.

    Raises:
        Exception: Propagates any Anthropic API or data-assembly errors.
    """
    payload = await assemble_company_payload(symbol)

    now_et = datetime.now(_ET)
    system_prompt = COMPANY_ANALYSIS_SYSTEM_PROMPT.format(
        day_of_week=now_et.strftime("%A"),
        date=now_et.strftime("%Y-%m-%d"),
    )

    user_message = json.dumps(payload, indent=2, default=str)

    # Reuse the shared Anthropic client helper from summary.py
    from backend.intelligence.summary import _call_anthropic

    return await _call_anthropic(system_prompt, user_message)
