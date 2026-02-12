#!/usr/bin/env python3
"""End-to-end test of the full intelligence pipeline with live data.

Fetches live quotes from Twelve Data and FRED, runs regime classification,
and generates a Claude API summary. Verifies everything stores correctly
in the summaries table.
"""

import asyncio
import json
import logging
import sys
import os

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("e2e_test")


async def main() -> int:
    from sqlalchemy import text

    from backend.db import init_db, get_session
    from backend.providers.twelve_data import TwelveDataProvider
    from backend.providers.fred import FredProvider
    from backend.jobs.daily_update import save_quotes
    from backend.intelligence.regime import classify_regime
    from backend.intelligence.summary import generate_close
    from datetime import datetime
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("US/Eastern")

    print("=" * 70)
    print("  MARKET PICTURE — END-TO-END PIPELINE TEST")
    print("=" * 70)

    # -----------------------------------------------------------------------
    # Step 1: Initialize DB
    # -----------------------------------------------------------------------
    print("\n--- Step 1: Initialize database ---")
    await init_db()
    print("  Tables created/verified.")

    # -----------------------------------------------------------------------
    # Step 2: Fetch live Twelve Data quotes
    # -----------------------------------------------------------------------
    print("\n--- Step 2: Fetch Twelve Data quotes (all 23 symbols) ---")
    td = TwelveDataProvider()
    try:
        td_quotes = await td.get_all_quotes()
    finally:
        await td.close()

    if not td_quotes:
        print("  ERROR: Twelve Data returned no quotes. Check API key / rate limits.")
        return 1

    print(f"  Received {len(td_quotes)} quotes:")
    for sym, data in sorted(td_quotes.items()):
        chg = data.get("change_pct", 0) or 0
        print(f"    {sym:12s}  ${data['price']:>10.2f}  {chg:+.2f}%")

    saved_td = await save_quotes(td_quotes)
    print(f"  Saved {saved_td} rows to market_snapshots.")

    # -----------------------------------------------------------------------
    # Step 3: Fetch live FRED quotes
    # -----------------------------------------------------------------------
    print("\n--- Step 3: Fetch FRED quotes (rates + credit spreads) ---")
    fred = FredProvider()
    try:
        fred_quotes = await fred.get_all_quotes()
    finally:
        await fred.close()

    if not fred_quotes:
        print("  WARNING: FRED returned no quotes. Regime signals may be limited.")
    else:
        print(f"  Received {len(fred_quotes)} quotes:")
        for sym, data in sorted(fred_quotes.items()):
            chg = data.get("change_pct", 0) or 0
            print(f"    {sym:20s}  {data['price']:>8.4f}  {chg:+.2f}%")
        saved_fred = await save_quotes(fred_quotes)
        print(f"  Saved {saved_fred} rows to market_snapshots.")

    # -----------------------------------------------------------------------
    # Step 4: Regime classification
    # -----------------------------------------------------------------------
    print("\n--- Step 4: Regime classification ---")
    session = await get_session()
    try:
        regime = await classify_regime(session)
    finally:
        await session.close()

    print(f"\n  REGIME: {regime['label']}")
    print(f"  Reason: {regime['reason']}")
    print(f"  Signals:")
    for sig in regime["signals"]:
        arrow = {"risk_on": "\u2191", "risk_off": "\u2193", "neutral": "\u2192"}.get(sig["direction"], "?")
        print(f"    {arrow} {sig['name']:20s} [{sig['direction']:8s}]  {sig['detail']}")

    # -----------------------------------------------------------------------
    # Step 5: Generate Claude API summary (after-close style)
    # -----------------------------------------------------------------------
    print("\n--- Step 5: Generate Claude API summary (after-close) ---")
    summary = await generate_close(regime)

    # -----------------------------------------------------------------------
    # Step 6: Store in summaries table
    # -----------------------------------------------------------------------
    print("\n--- Step 6: Store in summaries table ---")
    today = datetime.now(_ET).date().isoformat()
    session = await get_session()
    try:
        await session.execute(
            text("""
                INSERT INTO summaries
                    (date, period, summary_text, regime_label, regime_reason,
                     regime_signals_json)
                VALUES (:date, :period, :summary_text, :regime_label, :regime_reason,
                        :regime_signals_json)
            """),
            {
                "date": today,
                "period": "close",
                "summary_text": summary["summary_text"],
                "regime_label": regime["label"],
                "regime_reason": regime["reason"],
                "regime_signals_json": json.dumps(regime["signals"]),
            },
        )
        await session.commit()

        # Verify it was stored
        result = await session.execute(
            text(
                "SELECT * FROM summaries WHERE date = :date AND period = :period ORDER BY id DESC LIMIT 1"
            ),
            {"date": today, "period": "close"},
        )
        row = result.mappings().first()
    finally:
        await session.close()

    if row is None:
        print("  ERROR: Row not found in summaries table after insert!")
        return 1

    print(f"  Stored row id={row['id']}, date={row['date']}, period={row['period']}")
    print(f"  regime_label: {row['regime_label']}")
    print(f"  regime_reason: {row['regime_reason']}")

    # Verify JSON columns parse correctly
    signals_parsed = json.loads(row["regime_signals_json"])
    print(f"  regime_signals_json: {len(signals_parsed)} signals \u2713")

    # -----------------------------------------------------------------------
    # Final: Print the full narrative
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("  FULL NARRATIVE SUMMARY")
    print("=" * 70)
    print(f"\n{summary['summary_text']}")
    print("\n" + "=" * 70)
    print("  END-TO-END TEST COMPLETE — ALL STEPS PASSED")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
