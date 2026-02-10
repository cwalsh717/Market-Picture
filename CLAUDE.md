# Market Picture

## What This Is
A web dashboard showing how the broad market behaves across asset classes. It highlights what's moving together and why, translates it into plain English, and labels the current market regime. Includes a scarcity vs. abundance lens — tracking critical minerals alongside the broader market. Built for laypeople.

The narrative and cross-asset correlation story is the product. Prices are supporting evidence.

## Tech Stack
- Backend: Python 3.11+, FastAPI, APScheduler, SQLite
- Frontend: HTML/CSS/JS, Tailwind CSS, Chart.js
- Data: Twelve Data Grow tier (equities, international, FX, commodities, critical minerals ETFs, crypto), FRED (rates, credit)
- LLM: Anthropic Claude API (pre-market + after-close summaries)
- Hosting: Railway or Render

## Project Structure
market-picture/
├── backend/
│   ├── main.py                  # FastAPI app + routes
│   ├── config.py                # Env vars, thresholds, asset lists, regime rules
│   ├── db.py                    # SQLite setup + models
│   ├── providers/
│   │   ├── base.py              # DataProvider abstract interface
│   │   ├── twelve_data.py       # Twelve Data implementation
│   │   └── fred.py              # FRED implementation
│   ├── intelligence/
│   │   ├── regime.py            # Regime classification (rule-based)
│   │   ├── correlations.py      # Cross-asset correlation detection
│   │   └── summary.py           # Claude API summaries
│   ├── jobs/
│   │   ├── scheduler.py         # APScheduler setup
│   │   └── daily_update.py      # Orchestrates: fetch → compute → summarize
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── .env
├── .gitignore
├── CLAUDE.md
├── README.md
└── Dockerfile

## Asset Coverage (23 Twelve Data + 4 FRED)

### Twelve Data:
- Equities: S&P 500 (SPX), Nasdaq 100 (NDX), Russell 2000 (IWM), VIX (VIXY)
- International: Japan (EWJ), FTSE 100 (UKX), Euro Stoxx 50 (FEZ), Hong Kong (EWH)
- Currencies: US Dollar (UUP)
- Commodities: Crude Oil (WTI), Natural Gas (NG), Gold (XAU), Copper (CPER)
- Critical Minerals: Uranium (URA), Lithium (LIT), Rare Earths (REMX)
- Crypto: Bitcoin (BTC), Ethereum (ETH)

### FRED:
- 2Y Treasury yield, 10Y Treasury yield, 2s10s spread (calculated)
- IG credit spread, HY credit spread

## Data Refresh
- Twelve Data (equities, FX, commodities, minerals): every 10 min during market hours
- Twelve Data (crypto): every 10 min, 24/7
- Twelve Data (international): every 10 min during respective market hours
- FRED (rates, credit): once daily ~3:30 PM ET
- LLM summary #1: pre-market ~8:00 AM ET (overnight recap, what to watch)
- LLM summary #2: after close ~4:30 PM ET (full day narrative, correlations, regime)

## Code Conventions
- Python 3.11+, type hints everywhere
- Async where appropriate (FastAPI is async-native)
- Small, single-purpose functions (under 30 lines)
- DataProvider abstraction: providers/base.py defines the interface, implementations don't leak into business logic
- Config in config.py or .env, never hardcoded
- API keys via environment variables, never committed
- Docstrings on public functions
- Handle API failures gracefully: stale data + "last updated" timestamp

## Key Design Rules
- Correlation narrative + regime label = above the fold, shown first
- "What's moving together" grouping sits right below narrative
- Search bar below the narrative — users can quote any stock
- Price tables are supporting evidence below the fold
- Every asset has ⓘ plain-English explainer
- Dark theme, minimal UI, mobile-responsive
- Green = up, Red = down
- Time period toggle: Today, 1W, 1M, YTD

## Git Workflow
- Main branch: main (always deployable)
- Feature branches: feature/description
- Commit often, short descriptive messages
- PR before merging to main