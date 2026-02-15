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
# Auth
# ---------------------------------------------------------------------------
JWT_SECRET: str = os.getenv("JWT_SECRET", "dev-secret-change-in-production")
JWT_ALGORITHM: str = "HS256"
JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "10080"))  # 7 days
COOKIE_SECURE: bool = os.getenv("COOKIE_SECURE", "true").lower() == "true"

# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------
WATCHLIST_MAX_SIZE: int = 50

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
# US equity symbols (get prepost=true for extended-hours quotes)
# ---------------------------------------------------------------------------
US_EQUITY_SYMBOLS: set[str] = {"SPY", "QQQ", "IWM", "VIXY"}

# ---------------------------------------------------------------------------
# Technical indicator symbols (fetched daily at 4:35 PM ET)
# ---------------------------------------------------------------------------
TECHNICAL_SIGNAL_SYMBOLS: list[str] = ["SPY", "QQQ", "GLD", "BTC/USD", "UUP", "VIXY"]

# ---------------------------------------------------------------------------
# LLM summary settings
# ---------------------------------------------------------------------------
SUMMARY_CONFIG: dict[str, object] = {
    "model": "claude-sonnet-4-5-20250929",
    "max_tokens": 1024,
    "temperature": 0.3,
}

NARRATIVE_SYSTEM_PROMPT: str = (
    "You are a market analyst writing a daily briefing for a finance-focused "
    "reader who prefers facts over narrative. Be direct and concise. Describe "
    "what happened and what it means structurally \u2014 do not predict direction.\n\n"
    "Structure your response in exactly these sections:\n\n"
    "REGIME STATUS (1-2 sentences): Current regime label, whether it changed, "
    'signal confidence (e.g. "4 of 5 signals bearish"). If regime flipped, '
    "lead with that.\n\n"
    "WHAT HAPPENED (3-5 sentences): Factual price action across asset classes. "
    "Lead with biggest moves. Specific numbers. Note any unusual volume "
    "(volume_vs_avg above 1.5 or below 0.5) or RSI extremes (above 70 or "
    "below 30). Compare to prior session when relevant using the "
    "previous_narrative context.\n\n"
    "CROSS-ASSET SIGNALS (2-3 sentences): Notable confirmations or divergences "
    "across asset classes. Examples: gold rising while yields also rise, credit "
    "spreads widening while equities are flat, crypto diverging from risk "
    "assets. Only include this section if there is something genuinely "
    "notable \u2014 omit entirely if all assets are moving as expected for the "
    "current regime.\n\n"
    "LEVELS TO WATCH (2-3 bullets): Key technical levels from the data \u2014 "
    "52-week highs/lows being approached or tested, major SMA crossovers "
    "(price crossing 50-day or 200-day), extreme RSI readings. Facts only, "
    "no predictions.\n\n"
    "Keep the entire response under 200 words. No greetings, no sign-offs, "
    'no hedging. Do not say "markets" when you can name the specific '
    "instrument. Today is {day_of_week}, {date}."
)
