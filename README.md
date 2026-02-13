# Market Picture

Like checking the weather, but for markets. Open it, see the regime, read the story, go on with your day.

Market Picture is a cross-asset market dashboard that tracks 27 instruments across equities, rates, credit, currencies, commodities, critical minerals, and crypto. It classifies the current market regime as **RISK-ON**, **RISK-OFF**, or **MIXED**, and generates a plain-English narrative explaining what's happening -- twice daily, powered by Claude.

No login required. The landing page is the product.

## Features

**Regime classification** -- Five signals (S&P 500 trend, VIX, high-yield credit spreads, US dollar, gold vs equities) are combined into a single regime label with color-coded badge and per-signal breakdown.

**Daily narratives** -- Claude writes a pre-market briefing (~8 AM ET) and an after-close summary (~4:30 PM ET). Every narrative is archived and browsable in the journal.

**Dashboard** -- Asset cards grouped by class, each with live price, change %, and intraday sparkline. Period toggle switches between Today (5-min bars), 1W, 1M, and YTD views.

**Chart page** -- Click any symbol for a full deep-dive chart built on TradingView Lightweight Charts v5. Candlestick and line modes, volume bars, moving average overlays (20/50/200-day), RSI(14), zoom controls, and time range selector from 1D to Max.

**Search** -- Type any ticker in the nav bar to fetch on-demand data from Twelve Data and open its chart. Historical data is cached permanently with daily incremental updates.

**Journal** -- Browse the narrative archive by date. Each entry shows the regime label, signal breakdown, and full narrative text.

## Asset Coverage

| Asset Class | Instruments |
|---|---|
| **Equities** | SPY, QQQ, IWM, VIXY |
| **International** | EWJ (Japan), UKX (FTSE 100), FEZ (Euro Stoxx 50), EWH (Hong Kong) |
| **Rates** | 2Y Treasury, 10Y Treasury, 2s10s spread |
| **Credit** | IG spread, HY spread |
| **Currencies** | UUP (Dollar Index) |
| **Commodities** | USO (Crude Oil), UNG (Nat Gas), GLD (Gold), CPER (Copper) |
| **Critical Minerals** | URA (Uranium), LIT (Lithium), REMX (Rare Earths) |
| **Crypto** | BTC/USD, ETH/USD |

23 via [Twelve Data](https://twelvedata.com/) + 4 via [FRED](https://fred.stlouisfed.org/).

## Tech Stack

- **Backend:** Python, FastAPI, SQLAlchemy (async), APScheduler
- **Database:** SQLite locally, PostgreSQL in production
- **Frontend:** HTML/CSS/JS, Tailwind CSS, TradingView Lightweight Charts v5, Chart.js (sparklines)
- **Data:** Twelve Data API (Grow tier), FRED API
- **LLM:** Anthropic Claude API
- **Hosting:** Railway (auto-deploys on push to main)

## Project Structure

```
Market-Picture/
├── backend/
│   ├── main.py                  # FastAPI app, API routes, static file serving
│   ├── config.py                # Assets, thresholds, regime rules, LLM prompts
│   ├── db.py                    # SQLAlchemy async ORM + schema
│   ├── providers/
│   │   ├── base.py              # DataProvider abstract interface
│   │   ├── twelve_data.py       # Twelve Data: quotes, history, intraday, search
│   │   └── fred.py              # FRED: Treasury yields, credit spreads
│   ├── intelligence/
│   │   ├── regime.py            # Rule-based regime classification (5 signals)
│   │   └── summary.py           # Claude API narrative generation + archive writes
│   ├── services/
│   │   └── history_cache.py     # On-demand fetch, permanent cache, daily updates
│   ├── jobs/
│   │   ├── scheduler.py         # APScheduler: market-hours-aware scheduling
│   │   └── daily_update.py      # Orchestrates fetch → compute → summarize → store
│   └── tests/
├── frontend/
│   ├── index.html               # Dashboard (landing page)
│   ├── chart.html               # Symbol deep-dive chart page
│   ├── journal.html             # Narrative archive browser
│   ├── app.js                   # Dashboard logic + sparklines
│   ├── chart.js                 # TradingView chart, MAs, RSI, zoom
│   ├── journal.js               # Journal page logic
│   ├── nav.js                   # Shared navigation + search
│   └── styles.css               # Dark theme styles
├── Dockerfile
├── CLAUDE.md
└── .env                         # API keys (not committed)
```

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /api/snapshot` | Latest prices for all assets, grouped by class |
| `GET /api/summary` | Most recent regime + LLM narrative |
| `GET /api/history/{symbol}?range=1Y` | Daily OHLCV bars (1D, 1W, 1M, 3M, 6M, 1Y, 5Y, Max) |
| `GET /api/intraday/{symbol}` | 5-minute bars for today |
| `GET /api/search/{ticker}` | Live quote for any Twelve Data symbol |
| `GET /api/narratives?date=YYYY-MM-DD` | Archived narratives for a date |
| `GET /api/narratives/recent?days=7` | Recent narrative history |
| `GET /api/regime-history` | Regime labels for the last 90 days |
| `POST /api/admin/fetch-now` | Manual trigger: fetch all data + run pipeline |

## Setup

### Prerequisites

- Python 3.9+
- [Twelve Data](https://twelvedata.com/) API key (Grow tier)
- [FRED](https://fred.stlouisfed.org/docs/api/api_key.html) API key (free)
- [Anthropic](https://console.anthropic.com/) API key

### Install and run

```bash
git clone https://github.com/cwalsh717/Market-Picture.git
cd Market-Picture
pip install -r backend/requirements.txt
```

Create `.env` in the project root:

```
TWELVE_DATA_API_KEY=your_key
FRED_API_KEY=your_key
ANTHROPIC_API_KEY=your_key
```

Start the server:

```bash
uvicorn backend.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000). The database initializes automatically on first run, and historical data backfills in the background.

### Running tests

```bash
python -m pytest backend/tests/ -v
```

## Data Refresh

| Source | Schedule |
|---|---|
| Twelve Data (equities, FX, commodities, minerals) | Every 10 min during market hours |
| Twelve Data (crypto) | Every 10 min, 24/7 |
| Twelve Data (international) | Every 10 min during respective market hours |
| FRED (Treasury yields, credit spreads) | Once daily, ~3:30 PM ET |
| Pre-market narrative | ~8:00 AM ET |
| After-close narrative | ~4:30 PM ET |
| Historical cache (daily bars) | ~4:30 PM ET for all cached symbols |

## License

MIT
