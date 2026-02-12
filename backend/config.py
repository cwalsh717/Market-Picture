"""Configuration: env vars, market hours, asset lists, regime thresholds."""

import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------
TWELVE_DATA_API_KEY: str = os.getenv("TWELVE_DATA_API_KEY", "")
FRED_API_KEY: str = os.getenv("FRED_API_KEY", "")
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASE_URL: str = os.getenv("DATABASE_URL", "")
DATABASE_PATH: str = os.getenv("DATABASE_PATH", "market_picture.db")

# ---------------------------------------------------------------------------
# Market hours (all times in ET, 24-hour format)
# ---------------------------------------------------------------------------
MARKET_HOURS: dict[str, dict[str, str]] = {
    "US": {"open": "09:30", "close": "16:00"},
    "Japan": {"open": "20:00", "close": "02:00"},   # ET equivalent
    "UK": {"open": "03:00", "close": "11:30"},
    "Europe": {"open": "03:00", "close": "11:30"},
    "HK": {"open": "21:30", "close": "04:00"},      # ET equivalent
}

# ---------------------------------------------------------------------------
# Regime thresholds
# ---------------------------------------------------------------------------
REGIME_THRESHOLDS: dict[str, float] = {
    "vixy_spike_pct": 5.0,         # VIXY up > this % → vol spiking → risk-off
    "vixy_drop_pct": -5.0,         # VIXY down > this % → vol falling → risk-on
    "hy_spread_risk_off": 5.0,     # HY spread above this → risk-off
    "hy_spread_risk_on": 3.5,      # HY spread below this → risk-on
    "hy_spread_widening_bps": 10,  # HY spread WoW increase > this (bps) → widening
    "uup_spike_pct": 1.0,          # UUP daily change > this % → dollar spiking
    "spx_ma_period": 20,           # Number of trading days for S&P 500 MA
    "yield_curve_inverted": 0.0,   # 2s10s below this → inverted
    "gold_safe_haven_pct": 1.5,    # Gold up >1.5% on a risk-off day → flight to safety
}

# ---------------------------------------------------------------------------
# Twelve Data assets (23 symbols)
# ---------------------------------------------------------------------------
ASSETS: dict[str, dict[str, str]] = {
    "equities": {
        "SPY": "S&P 500 (SPY)",
        "QQQ": "Nasdaq 100 (QQQ)",
        "IWM": "Russell 2000",
        "VIXY": "VIX (Short-Term Futures)",
    },
    "international": {
        "EWJ": "Japan (EWJ)",
        "UKX": "FTSE 100",
        "FEZ": "Euro Stoxx 50 (FEZ)",
        "EWH": "Hong Kong (EWH)",
    },
    "currencies": {
        "UUP": "US Dollar (UUP)",
    },
    "commodities": {
        "USO": "Crude Oil (USO)",
        "UNG": "Natural Gas (UNG)",
        "GLD": "Gold (GLD)",
        "CPER": "Copper (CPER)",
    },
    "critical_minerals": {
        "URA": "Uranium ETF",
        "LIT": "Lithium ETF",
        "REMX": "Rare Earths ETF",
    },
    "crypto": {
        "BTC/USD": "Bitcoin",
        "ETH/USD": "Ethereum",
    },
}

# ---------------------------------------------------------------------------
# FRED series
# ---------------------------------------------------------------------------
FRED_SERIES: dict[str, str] = {
    "DGS2": "2-Year Treasury Yield",
    "DGS10": "10-Year Treasury Yield",
    "BAMLC0A0CM": "IG Corporate Bond Spread",
    "BAMLH0A0HYM2": "HY Corporate Bond Spread",
}

# ---------------------------------------------------------------------------
# Symbol → market region mapping (for scheduler market-hours checks)
# ---------------------------------------------------------------------------
# "24/7" means always active (crypto).
# Other values must be keys in MARKET_HOURS.
SYMBOL_MARKET_MAP: dict[str, str] = {
    # Equities (US hours)
    "SPY": "US",
    "QQQ": "US",
    "IWM": "US",
    "VIXY": "US",
    # International (US-listed ETF proxies — trade during US hours)
    "EWJ": "US",
    "UKX": "UK",
    "FEZ": "US",
    "EWH": "US",
    # Currencies (US hours)
    "UUP": "US",
    # Commodities (US hours)
    "USO": "US",
    "UNG": "US",
    "GLD": "US",
    "CPER": "US",
    # Critical minerals (US-listed ETFs)
    "URA": "US",
    "LIT": "US",
    "REMX": "US",
    # Crypto (24/7)
    "BTC/USD": "24/7",
    "ETH/USD": "24/7",
}

# ---------------------------------------------------------------------------
# Symbol → asset class mapping (derived from ASSETS, for DB writes)
# ---------------------------------------------------------------------------
SYMBOL_ASSET_CLASS: dict[str, str] = {
    symbol: asset_class
    for asset_class, symbols in ASSETS.items()
    for symbol in symbols
}
for _series_id in FRED_SERIES:
    SYMBOL_ASSET_CLASS[_series_id] = "rates"
SYMBOL_ASSET_CLASS["SPREAD_2S10S"] = "rates"

# ---------------------------------------------------------------------------
# LLM summary settings
# ---------------------------------------------------------------------------
SUMMARY_CONFIG: dict[str, object] = {
    "model": "claude-sonnet-4-5-20250929",
    "max_tokens": 1024,
    "temperature": 0.3,
}

SUMMARY_SYSTEM_PROMPT: str = (
    "You are a market analyst writing for a general audience. "
    "Explain cross-asset market moves in plain English, no jargon. "
    "Focus on the narrative: what is moving together and why it matters. "
    "Mention the scarcity vs abundance theme (critical minerals — uranium, "
    "lithium, rare earths) when relevant. "
    "Keep to 3-5 concise paragraphs of flowing prose. No bullet points or headers."
)

PREMARKET_USER_TEMPLATE: str = (
    "Write a pre-market briefing for {date}.\n\n"
    "Current regime: {regime_label} — {regime_reason}\n\n"
    "Signals:\n{regime_signals}\n\n"
    "Cover: (1) the regime and what's driving it, "
    "(2) what to watch today."
)

CLOSE_USER_TEMPLATE: str = (
    "Write an after-close market summary for {date}.\n\n"
    "Regime: {regime_label} — {regime_reason}\n"
    "Signals:\n{regime_signals}\n\n"
    "Cover: (1) regime and what drove it, "
    "(2) what this means for someone watching markets."
)
