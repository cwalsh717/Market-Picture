# Market Picture

<!-- Replace with actual dashboard screenshot -->
![Market Picture Dashboard](docs/screenshot.png)

A cross-asset market dashboard that shows what's moving together and why. Instead of scrolling through dozens of tickers, Market Picture groups correlated moves, labels the current market regime (risk-on, risk-off, or mixed), and generates a plain-English narrative explaining what it all means.

Built for people who want to understand the market, not just watch prices.

## What It Does

**Regime classification** -- Five signals (S&P 500 trend, volatility, high-yield credit spreads, dollar strength, gold vs equities) are evaluated in real time and combined into a single label: RISK-ON, RISK-OFF, or MIXED.

**Correlation detection** -- Every asset is compared against every other. The system groups assets that are rallying or selling together, flags pairs that normally move in lockstep but are diverging today, and surfaces unusual behavior like crypto suddenly tracking equities.

**Moving-together groups** -- The raw correlation data is translated into frontend-ready groups: "Rallying together: S&P 500, Nasdaq 100, Russell 2000 (up avg 1.8%)" or "Diverging: Nasdaq 100 (+0.6%) vs S&P 500 (-1.2%)".

**LLM narrative** -- Twice daily, all of the above is fed to Claude to produce a concise market summary in plain English. A pre-market briefing at 8 AM ET covers overnight moves and what to watch. An after-close summary at 4:30 PM ET wraps up the day's story.

**Scarcity vs abundance** -- Critical minerals (uranium, lithium, rare earths) are tracked alongside the broader market. When these commodities decouple from equities, the system flags it as a scarcity signal.

## Asset Coverage

| Asset Class | Symbols |
|---|---|
| **Equities** | S&P 500, Nasdaq 100, Russell 2000, VIX |
| **International** | Japan, FTSE 100, Euro Stoxx 50, Hong Kong |
| **Rates** | 2Y Treasury, 10Y Treasury, 2s10s spread |
| **Credit** | IG corporate spread, HY corporate spread |
| **Currencies** | US Dollar |
| **Commodities** | Crude Oil, Natural Gas, Gold, Copper |
| **Critical Minerals** | Uranium (URA), Lithium (LIT), Rare Earths (REMX) |
| **Crypto** | Bitcoin, Ethereum |

27 instruments total -- 23 via Twelve Data, 4 via FRED.

## Tech Stack

- **Backend:** Python, FastAPI, APScheduler, SQLite
- **Frontend:** HTML/CSS/JS, Tailwind CSS, Chart.js
- **Data providers:** [Twelve Data](https://twelvedata.com/) (equities, FX, commodities, crypto), [FRED](https://fred.stlouisfed.org/) (rates, credit spreads)
- **LLM:** Anthropic Claude API (market narrative generation)

## Project Structure

```
market-picture/
├── backend/
│   ├── main.py                  # FastAPI app + routes
│   ├── config.py                # Asset lists, thresholds, regime rules
│   ├── db.py                    # SQLite setup + migrations
│   ├── providers/
│   │   ├── base.py              # DataProvider interface
│   │   ├── twelve_data.py       # Twelve Data implementation
│   │   └── fred.py              # FRED implementation
│   ├── intelligence/
│   │   ├── regime.py            # Rule-based regime classification
│   │   ├── correlations.py      # Cross-asset correlation detection
│   │   └── summary.py           # Claude API narrative generation
│   ├── jobs/
│   │   ├── scheduler.py         # APScheduler setup
│   │   └── daily_update.py      # Fetch → compute → summarize pipeline
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── styles.css
│   └── app.js
└── .env
```

## Setup

### Prerequisites

- Python 3.9+
- A [Twelve Data](https://twelvedata.com/) API key (Grow tier or above)
- A [FRED](https://fred.stlouisfed.org/docs/api/api_key.html) API key (free)
- An [Anthropic](https://console.anthropic.com/) API key (for LLM summaries)

### Installation

```bash
git clone https://github.com/cwalsh717/Market-Picture.git
cd Market-Picture
```

Install dependencies:

```bash
pip install -r backend/requirements.txt
```

Create a `.env` file in the project root:

```
TWELVE_DATA_API_KEY=your_key_here
FRED_API_KEY=your_key_here
ANTHROPIC_API_KEY=your_key_here
```

### Running locally

Initialize the database and start the server:

```bash
uvicorn backend.main:app --reload
```

### Running tests

```bash
pip install pytest pytest-asyncio
python -m pytest backend/tests/ -v
```

## Data Refresh Schedule

| Source | Frequency |
|---|---|
| Twelve Data (equities, FX, commodities, minerals) | Every 10 min during US market hours |
| Twelve Data (crypto) | Every 10 min, 24/7 |
| FRED (rates, credit spreads) | Once daily, ~3:30 PM ET |
| Pre-market summary (LLM) | ~8:00 AM ET |
| After-close summary (LLM) | ~4:30 PM ET |

## License

MIT
