#!/usr/bin/env python3
"""End-to-end test of the full intelligence pipeline with live data.

Fetches live quotes from Twelve Data and FRED, runs regime classification,
correlation detection (1D), moving-together grouping, and generates a Claude
API summary. Verifies everything stores correctly in the summaries table.
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
    from backend.config import DATABASE_PATH
    from backend.db import init_db, get_connection
    from backend.providers.twelve_data import TwelveDataProvider
    from backend.providers.fred import FredProvider
    from backend.jobs.daily_update import save_quotes
    from backend.intelligence.regime import classify_regime
    from backend.intelligence.correlations import detect_correlations
    from backend.intelligence.summary import generate_close
    from datetime import datetime
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("US/Eastern")

    print("=" * 70)
    print("  MARKET PICTURE — END-TO-END PIPELINE TEST")
    print("=" * 70)
    print(f"\nDatabase: {DATABASE_PATH}")

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
    conn = await get_connection()
    try:
        regime = await classify_regime(conn)
    finally:
        await conn.close()

    print(f"\n  REGIME: {regime['label']}")
    print(f"  Reason: {regime['reason']}")
    print(f"  Signals:")
    for sig in regime["signals"]:
        arrow = {"risk_on": "↑", "risk_off": "↓", "neutral": "→"}.get(sig["direction"], "?")
        print(f"    {arrow} {sig['name']:20s} [{sig['direction']:8s}]  {sig['detail']}")

    # -----------------------------------------------------------------------
    # Step 5: Correlation detection (1D)
    # -----------------------------------------------------------------------
    print("\n--- Step 5: Correlation detection (1D) ---")
    conn = await get_connection()
    try:
        corr_1d = await detect_correlations(conn, period="1D")
    finally:
        await conn.close()

    print(f"  Data points: {corr_1d['data_points']}")
    print(f"  Co-movement groups: {len(corr_1d['groups'])}")
    for g in corr_1d["groups"]:
        direction = "▲" if g["direction"] == "up" else "▼"
        names = ", ".join(g.get("labels", g["symbols"]))
        print(f"    {direction} {g['avg_change_pct']:+.2f}%  {names}")

    print(f"  Anomalies: {len(corr_1d['anomalies'])}")
    for a in corr_1d["anomalies"]:
        print(f"    ⚠ {a['detail']}")

    print(f"  Diverging pairs: {len(corr_1d.get('diverging', []))}")
    for d in corr_1d.get("diverging", []):
        print(f"    ↕ {d['label_a']} ({d['change_pct_a']:+.1f}%) vs "
              f"{d['label_b']} ({d['change_pct_b']:+.1f}%) "
              f"[baseline r={d['baseline_r']:.2f}]")

    # -----------------------------------------------------------------------
    # Step 6: Generate Claude API summary (after-close style)
    # -----------------------------------------------------------------------
    print("\n--- Step 6: Generate Claude API summary (after-close) ---")
    # Use 1D for both periods since we're just testing the pipeline
    summary = await generate_close(regime, corr_1d, corr_1d)

    print(f"\n  Moving-Together Groups ({len(summary['moving_together'])}):")
    for mt in summary["moving_together"]:
        emoji = {"Rallying together": "🟢", "Selling together": "🔴", "Diverging": "↕"}.get(mt["label"], "•")
        print(f"    {emoji} {mt['label']}: {', '.join(mt['assets'])}")
        print(f"      {mt['detail']}")

    # -----------------------------------------------------------------------
    # Step 7: Store in summaries table
    # -----------------------------------------------------------------------
    print("\n--- Step 7: Store in summaries table ---")
    today = datetime.now(_ET).date().isoformat()
    conn = await get_connection()
    try:
        await conn.execute(
            """
            INSERT INTO summaries
                (date, period, summary_text, regime_label, regime_reason,
                 regime_signals_json, moving_together_json, correlations_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                today,
                "close",
                summary["summary_text"],
                regime["label"],
                regime["reason"],
                json.dumps(regime["signals"]),
                json.dumps(summary["moving_together"]),
                json.dumps({"1D": corr_1d}),
            ),
        )
        await conn.commit()

        # Verify it was stored
        cursor = await conn.execute(
            "SELECT * FROM summaries WHERE date = ? AND period = ? ORDER BY id DESC LIMIT 1",
            (today, "close"),
        )
        row = await cursor.fetchone()
    finally:
        await conn.close()

    if row is None:
        print("  ERROR: Row not found in summaries table after insert!")
        return 1

    print(f"  Stored row id={row['id']}, date={row['date']}, period={row['period']}")
    print(f"  regime_label: {row['regime_label']}")
    print(f"  regime_reason: {row['regime_reason']}")

    # Verify JSON columns parse correctly
    signals_parsed = json.loads(row["regime_signals_json"])
    mt_parsed = json.loads(row["moving_together_json"])
    corr_parsed = json.loads(row["correlations_json"])

    print(f"  regime_signals_json: {len(signals_parsed)} signals ✓")
    print(f"  moving_together_json: {len(mt_parsed)} groups ✓")
    print(f"  correlations_json: keys={list(corr_parsed.keys())} ✓")

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
