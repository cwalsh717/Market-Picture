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
    "vix_risk_off": 25.0,          # VIX above this → risk-off signal
    "vix_risk_on": 15.0,           # VIX below this → risk-on signal
    "hy_spread_risk_off": 5.0,     # HY spread above this → risk-off
    "hy_spread_risk_on": 3.5,      # HY spread below this → risk-on
    "dxy_strong": 105.0,           # DXY above this → strong dollar
    "dxy_weak": 100.0,             # DXY below this → weak dollar
    "yield_curve_inverted": 0.0,   # 2s10s below this → inverted
    "gold_safe_haven_pct": 1.5,    # Gold up >1.5% on a risk-off day → flight to safety
    "correlation_threshold": 0.7,  # Minimum r for "moving together" grouping
}

# ---------------------------------------------------------------------------
# Twelve Data assets (23 symbols)
# ---------------------------------------------------------------------------
ASSETS: dict[str, dict[str, str]] = {
    "equities": {
        "SPX": "S&P 500",
        "NDX": "Nasdaq 100",
        "RUT": "Russell 2000",
        "VIX": "VIX",
    },
    "international": {
        "NKY": "Nikkei 225",
        "UKX": "FTSE 100",
        "SX5E": "Euro Stoxx 50",
        "HSI": "Hang Seng",
    },
    "currencies": {
        "DXY": "US Dollar Index",
    },
    "commodities": {
        "WTI": "Crude Oil (WTI)",
        "NG": "Natural Gas",
        "XAU": "Gold",
        "XCU": "Copper",
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
