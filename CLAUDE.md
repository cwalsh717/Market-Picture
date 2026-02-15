# Bradán — Daily Market Intelligence

## What This Is
A daily market weather report. Open it, see the regime (Risk-On / Risk-Off / Mixed), read a plain-English narrative about what's happening across asset classes, and go on with your day. 30 seconds. Like checking the weather, but for markets.

The product IS the landing page — no marketing gate, no signup wall. The regime label + daily narrative is the core value prop. No other free tool gives you a plain-English cross-asset market story every day.

**Target user:** People who care about markets but aren't staring at Bloomberg all day.

**Design principles:**
- Facts over narrative — data-driven, not opinion-driven
- Depth on demand — glanceable by default, drillable for the curious
- The landing page is the product — no gates
- Build for scale from the start

## Tech Stack
- Backend: Python 3.11+, FastAPI, APScheduler
- Database: PostgreSQL (Railway managed), SQLAlchemy async ORM
- Frontend: HTML/CSS/JS, Tailwind CSS (CDN), Chart.js (sparklines), TradingView Lightweight Charts v5.1 (chart page)
- Data: Twelve Data Grow tier ($79/mo, 55 credits/min), FRED (free)
- LLM: Anthropic Claude API (pre-market + after-close summaries)
- Auth: Email + password, JWT tokens (httpOnly cookies), bcrypt
- Hosting: Railway (auto-deploys on push to main)

## Project Structure
```
Market-Picture/
├── backend/
│   ├── main.py                  # FastAPI app + routes + static file serving
│   ├── auth.py                  # Auth routes, JWT, bcrypt, change-password/email
│   ├── config.py                # Env vars, thresholds, asset lists, regime rules
│   ├── db.py                    # PostgreSQL setup + SQLAlchemy models
│   ├── providers/
│   │   ├── base.py              # DataProvider abstract interface
│   │   ├── twelve_data.py       # Twelve Data implementation
│   │   └── fred.py              # FRED implementation
│   ├── intelligence/
│   │   ├── regime.py            # Regime classification (rule-based, 5 signals)
│   │   ├── narrative_data.py    # Structured data pipeline for LLM prompts
│   │   └── summary.py           # Claude API summaries + narrative archive writes
│   ├── services/
│   │   └── history_cache.py     # On-demand OHLCV fetch, cache, and backfill
│   ├── jobs/
│   │   ├── scheduler.py         # APScheduler setup (7 scheduled jobs)
│   │   └── daily_update.py      # Orchestrates: fetch → compute → summarize → archive
│   ├── tests/
│   │   ├── test_regime.py       # Regime classification tests
│   │   ├── test_summary.py      # Summary/narrative tests
│   │   └── test_data_pipeline.py # Data pipeline tests
│   └── requirements.txt
├── frontend/
│   ├── index.html               # Main dashboard (the landing page)
│   ├── app.js                   # Dashboard logic (regime, cards, lazy sparklines)
│   ├── auth.js                  # Auth modal, user dropdown, account settings
│   ├── chart.html               # Symbol deep-dive chart page
│   ├── chart.js                 # TradingView Lightweight Charts logic
│   ├── journal.html             # Narrative archive browser
│   ├── journal.js               # Journal page logic
│   ├── about.html               # About page
│   ├── bradan.html              # Bradán brand/mythology page
│   ├── nav.js                   # Shared nav bar + search + mobile hamburger
│   ├── styles.css               # All page styles
│   └── static/
│       └── bradan-logo.jpg      # Logo image
├── scripts/
│   └── e2e_pipeline_test.py     # End-to-end pipeline test
├── .env                         # Local API keys (gitignored)
├── .gitignore
├── .dockerignore
├── Dockerfile
├── CLAUDE.md                    # You're reading this
└── README.md
```

## Asset Coverage (23 Twelve Data + 4 FRED)

### Dashboard Symbols (always refreshed):
- **Equities:** SPY (S&P 500), QQQ (Nasdaq 100), IWM (Russell 2000), VIXY (VIX)
- **International:** EWJ (Japan), UKX (FTSE 100), FEZ (Euro Stoxx 50), EWH (Hong Kong)
- **Currencies:** UUP (Dollar Index)
- **Commodities:** USO (Crude Oil), UNG (Natural Gas), GLD (Gold), CPER (Copper)
- **Critical Minerals:** URA (Uranium), LIT (Lithium), REMX (Rare Earths)
- **Crypto:** BTC/USD, ETH/USD

### FRED:
- 2Y Treasury yield (DGS2), 10Y Treasury yield (DGS10), 2s10s spread (calculated)
- IG credit spread (BAMLC0A0CM), HY credit spread (BAMLH0A0HYM2)

### On-Demand Symbols (any Twelve Data symbol):
- Fetched on first search or watchlist add
- Historical OHLCV cached permanently in PostgreSQL
- Daily incremental updates for all cached symbols

## Database Tables

| Table | Purpose |
|-------|---------|
| `market_snapshots` | Latest price quotes (symbol, price, change_pct, 52-week data, rolling changes) |
| `daily_history` | OHLCV bars — permanent cache (symbol + date unique constraint) |
| `summaries` | LLM market summaries (date, period, regime_label, regime_signals_json) |
| `narrative_archive` | Historical narratives (regime, signals, movers snapshot) |
| `users` | User accounts (email, password_hash, created_at) |
| `watchlists` | Saved symbols per user (user_id, symbol, display_order) |
| `technical_signals` | Technical indicators (RSI-14, ATR-14, SMA-50, SMA-200 per symbol) |
| `search_cache` | Cached symbol search results |

## API Routes

### Public (main.py)
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/health` | Service health check |
| GET | `/api/snapshot` | Latest prices for all dashboard assets, grouped by class |
| GET | `/api/history/{symbol}?range=1Y` | OHLCV history (fetches on demand, caches permanently) |
| GET | `/api/intraday/{symbol}` | 5-min intraday bars for today |
| GET | `/api/summary` | Latest regime + narrative |
| GET | `/api/search/{ticker}` | Live quote lookup via Twelve Data |
| GET | `/api/narratives?date=YYYY-MM-DD` | Archived narratives for a specific date |
| GET | `/api/narratives/recent?days=7` | Recent narratives (default 7 days) |
| GET | `/api/regime-history` | Regime labels for last 90 days |
| POST | `/api/admin/fetch-now` | Manual trigger: fetch all quotes + run intelligence |

### Auth (auth.py)
| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/auth/register` | Create account, set JWT cookie |
| POST | `/api/auth/login` | Validate credentials, set JWT cookie |
| POST | `/api/auth/logout` | Clear auth cookie |
| GET | `/api/auth/me` | Return current user (id, email) |
| PUT | `/api/auth/change-password` | Update password (requires current password) |
| PUT | `/api/auth/change-email` | Update email (requires password), re-issues JWT |

## Scheduled Jobs (7 total, ET timezone)

| Job | Schedule | Purpose |
|-----|----------|---------|
| `twelve_data_quotes` | Every 10 min | Fetch quotes for currently-open markets |
| `fred_quotes` | Mon-Fri 3:30 PM | Fetch FRED series (rates + credit spreads) |
| `premarket_quotes` | Mon-Fri 7:45 AM | Pre-market refresh with extended hours data |
| `technical_signals` | Mon-Fri 4:35 PM | Fetch RSI, ATR, SMA for 6 key symbols |
| `daily_history_append` | Mon-Fri 4:45 PM | Append latest bars for all cached symbols |
| `premarket_summary` | Mon-Fri 9:45 AM | Claude API morning narrative + archive |
| `close_summary` | Mon-Fri 4:50 PM | Claude API evening narrative + archive |

## Data Refresh Schedule
- Twelve Data (equities, FX, commodities, minerals): every 10 min during market hours
- Twelve Data (crypto): every 10 min, 24/7
- Twelve Data (international): every 10 min during respective market hours
- FRED (rates, credit): once daily ~3:30 PM ET
- LLM narrative: twice daily (pre-market 9:45 AM ET, after close 4:50 PM ET)
- Technical signals: daily ~4:35 PM ET
- Historical cache append: daily ~4:45 PM ET

## Historical Data Strategy: Fetch on Demand, Cache Forever
Instead of bulk backfilling every symbol, historical daily OHLCV data is fetched the first time any symbol is requested. Once fetched, it's stored permanently. Daily incremental updates keep cached symbols current.

**Twelve Data constraints:**
- Grow tier: 55 API credits/minute (resets each minute)
- Time series = 1 credit per symbol
- Max 5,000 data points per request (~20 years of daily bars)
- EOD data available 30+ years back

**Rate limiting:** Queue backfill requests, never exceed 55 credits/min. Use exponential backoff on 429 errors.

## Intelligence Pipeline

### Regime Classification (regime.py)
Five signals evaluated from latest market data:
1. **spx_trend** — SPY price vs 20-day SMA
2. **vix** — VIXY daily change (>5% = risk-off, <-5% = risk-on)
3. **hy_spread** — HY credit spread level + week-over-week widening
4. **dxy** — UUP daily change (>1% = risk-off)
5. **gold_vs_equities** — Gold outperforming equities (safe haven signal)

**Aggregation:** Risk-Off >= 2 → RISK-OFF; Risk-On >= 2 AND zero risk-off → RISK-ON; else MIXED

### Narrative Data (narrative_data.py)
Assembles enriched JSON payload for Claude API: regime classification, per-symbol prices + technicals (RSI, ATR, SMAs, 52-week data), rates, credit spreads, previous narrative context.

### Summary Generation (summary.py)
Calls Claude API with structured payload from narrative_data. Falls back to plain-text summary prefixed with "[Auto-generated — LLM summary unavailable]" on API failure. Archives narratives with movers snapshot.

## Chart Page (symbol deep dive)
When a user searches for or clicks on any symbol, they open a full chart page.

### Features:
- TradingView Lightweight Charts v5.1 (candlestick + line chart toggle)
- Volume bars (color-coded by candle direction)
- Moving average overlays: 20-day, 50-day, 200-day (toggleable, color-coded)
- RSI (14) in separate synced pane with overbought/oversold lines
- Time range selector: 1D, 5D, 1M, 3M, 6M, 1Y, 5Y, Max
- Zoom controls (in/out/reset)
- Crosshair with OHLC data display
- Price stats: open, high, low, close, volume, 52-week high/low
- FRED symbol detection: auto-forces line mode for yield/spread data
- Back to dashboard navigation

## Auth System
Email + password authentication with JWT tokens stored in httpOnly cookies.

### Frontend:
- Polished auth modal (login/register toggle, close button, logo, password visibility toggle, loading spinner, animations)
- User dropdown menu when logged in (avatar circle, account settings, sign out)
- Account settings modal (change email, change password with inline feedback)

## Product Pages

### Page 1: Dashboard (landing page = the product)
- Hero: Regime badge — big, bold, color-coded (emerald/red/amber)
- Sub-hero: One-liner reason for the regime
- Signal pills with tooltips (S&P Trend, Volatility, Credit Spreads, Dollar, Gold vs Equities)
- Narrative: Claude-generated daily market story
- Period toggle: 1D / 1W / 1M / YTD
- Asset class sections: Equities, International, Rates, Credit, Currencies, Commodities, Critical Minerals, Crypto
- Asset cards with prices, change %, explainer tooltips, lazy-loaded sparklines
- Search bar: Type any symbol → on-demand fetch → chart page
- No login required

### Page 2: Chart Page (symbol deep dive)
- Full candlestick chart with all analysis tools
- Reached via search or clicking any symbol
- No login required

### Page 3: Journal (narrative archive)
- Browse past narratives by date or recent (last 30 days)
- Pre-market / after-close type badges
- Regime badge + signal pills per entry
- Narrative text split into paragraphs

### Page 4: About
- Project description, how it works, who built it
- Links to GitHub, LinkedIn, email, Bradán page

### Page 5: Bradán (brand story)
- Irish mythology of An Bradán Feasa (The Salmon of Knowledge)
- Embedded YouTube video
- Connection to the project name

### Logged-In Features (free account):
- User account management (change email, change password)
- Watchlists: Save symbols, personal tab (DB table exists, UI not yet built)
- Alerts (future): Regime change, VIX spike, price thresholds via email

## V2 Summary (Complete)
All V2 phases delivered:
- Phase 5: PostgreSQL Migration
- Phase 6: Narrative Archive + Journal page
- Phase 7: On-Demand Historical Data Cache
- Phase 8: Chart Page (TradingView Lightweight Charts v5.1)
- Phase 9: Auth (email/password, JWT, bcrypt, users + watchlists tables)
- Phase 10: Landing Page Polish + Auth UX (mobile nav, sparkline lazy loading, account management)

## What's Next (V3)
- Watchlist UI (frontend tab, CRUD endpoints — DB table already exists)
- Alerts (regime change, VIX spike, price thresholds via email)
- Further mobile/performance polish
- Narrative quality improvements

## Code Conventions
- Python 3.11+, type hints everywhere
- Async where appropriate (FastAPI is async-native)
- SQLAlchemy async ORM for all DB access (PostgreSQL in prod, SQLite locally)
- Small, single-purpose functions (under 30 lines)
- DataProvider abstraction: providers/base.py defines the interface
- Config in config.py or .env, never hardcoded
- API keys via environment variables, never committed
- Docstrings on public functions
- Handle API failures gracefully: stale data + "last updated" timestamp
- Frontend: dark theme, minimal UI, mobile-responsive
- Green = up, Red = down

## Environment Variables
```
TWELVE_DATA_API_KEY=your_key
FRED_API_KEY=your_key
ANTHROPIC_API_KEY=your_key
DATABASE_URL=postgresql://user:pass@host:port/dbname
JWT_SECRET=your_secret
```

## Git Workflow
- Push to `main` directly (solo project)
- Commit after every working piece
- Railway auto-deploys on push to main
- Use `/clear` between tasks in Claude Code
