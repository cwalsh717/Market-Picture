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
    "vix_risk_on": 20.0,           # VIX below this → risk-on signal
    "hy_spread_risk_off": 5.0,     # HY spread above this → risk-off
    "hy_spread_risk_on": 3.5,      # HY spread below this → risk-on
    "hy_spread_widening_bps": 10,  # HY spread WoW increase > this (bps) → widening
    "dxy_strong": 105.0,           # DXY above this → strong dollar
    "dxy_weak": 100.0,             # DXY below this → weak dollar
    "dxy_spike_pct": 1.0,          # DXY daily change > this % → spiking
    "spx_ma_period": 20,           # Number of trading days for S&P 500 MA
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

# ---------------------------------------------------------------------------
# Symbol → market region mapping (for scheduler market-hours checks)
# ---------------------------------------------------------------------------
# "24/7" means always active (crypto).
# Other values must be keys in MARKET_HOURS.
SYMBOL_MARKET_MAP: dict[str, str] = {
    # Equities (US hours)
    "SPX": "US",
    "NDX": "US",
    "RUT": "US",
    "VIX": "US",
    # International
    "NKY": "Japan",
    "UKX": "UK",
    "SX5E": "Europe",
    "HSI": "HK",
    # Currencies (US hours)
    "DXY": "US",
    # Commodities (US hours)
    "WTI": "US",
    "NG": "US",
    "XAU": "US",
    "XCU": "US",
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
# Correlation detection
# ---------------------------------------------------------------------------
CORRELATION_CONFIG: dict[str, float] = {
    "min_data_points": 5,          # Minimum daily observations for Pearson r
    "anomaly_deviation_threshold": 0.4,  # |actual − expected| to flag anomaly
    "comovement_magnitude_band": 1.5,    # Pct-point band for 1D grouping
    "comovement_min_change_pct": 0.3,    # Filter flat assets in 1D mode
}

# Expected baseline correlations (long-run approximations).
# Keys are (symbol_a, symbol_b) sorted alphabetically.
BASELINE_CORRELATIONS: dict[tuple[str, str], float] = {
    # Traditionally high positive (equity indices)
    ("NDX", "SPX"): 0.90,
    ("RUT", "SPX"): 0.85,
    ("NDX", "RUT"): 0.80,
    ("SX5E", "UKX"): 0.85,
    # Traditionally negative
    ("SPX", "VIX"): -0.80,
    # Normally uncorrelated (crypto vs traditional)
    ("BTC/USD", "NDX"): 0.15,
    ("BTC/USD", "RUT"): 0.10,
    ("BTC/USD", "SPX"): 0.10,
    ("ETH/USD", "SPX"): 0.10,
    # Scarcity-risk (critical minerals vs broad risk)
    ("LIT", "NDX"): 0.45,
    ("LIT", "SPX"): 0.50,
    ("NDX", "REMX"): 0.40,
    ("NDX", "URA"): 0.35,
    ("REMX", "SPX"): 0.45,
    ("SPX", "URA"): 0.40,
    # Scarcity internal
    ("LIT", "REMX"): 0.65,
    ("LIT", "URA"): 0.55,
    ("REMX", "URA"): 0.60,
    # Cross-asset
    ("DGS10", "SPX"): 0.15,
    ("DXY", "SPX"): -0.20,
    ("SPX", "WTI"): 0.35,
    ("SPX", "XAU"): -0.10,
    ("SPX", "XCU"): 0.50,
}

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
    "Overnight & international moves:\n{overnight_data}\n\n"
    "Crypto (24/7):\n{crypto_data}\n\n"
    "Current regime: {regime_label} — {regime_reason}\n\n"
    "Co-movement groups:\n{comovement_summary}\n\n"
    "Anomalies:\n{anomalies_summary}\n\n"
    "Cover: (1) overnight international moves, (2) crypto, "
    "(3) what regime and correlations suggest to watch today."
)

CLOSE_USER_TEMPLATE: str = (
    "Write an after-close market summary for {date}.\n\n"
    "Regime: {regime_label} — {regime_reason}\n"
    "Signals:\n{regime_signals}\n\n"
    "Today's co-movement (1D):\n{comovement_1d}\n\n"
    "Monthly co-movement (1M):\n{comovement_1m}\n\n"
    "Anomalies:\n{anomalies_1d}\n{anomalies_1m}\n\n"
    "Scarcity vs abundance:\n{scarcity_summary}\n\n"
    "Cover: (1) regime and what drove it, (2) what moved together/diverged, "
    "(3) scarcity theme, (4) what this means for someone watching markets."
)
