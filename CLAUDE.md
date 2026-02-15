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
- Database: PostgreSQL (Railway managed)
- Frontend: HTML/CSS/JS, Tailwind CSS (CDN), Chart.js (sparklines), TradingView Lightweight Charts v5 (chart page)
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
│   ├── db.py                    # PostgreSQL setup + SQLAlchemy models (users, watchlists)
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
│   │   ├── scheduler.py         # APScheduler setup
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
│   ├── bradan.html              # Bradán brand page
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
- **Commodities:** WTI (Crude Oil), UNG (Natural Gas), GLD (Gold), CPER (Copper)
- **Critical Minerals:** URA (Uranium), LIT (Lithium), REMX (Rare Earths)
- **Crypto:** BTC/USD, ETH/USD

### FRED:
- 2Y Treasury yield (DGS2), 10Y Treasury yield (DGS10), 2s10s spread (calculated)
- IG credit spread, HY credit spread

### On-Demand Symbols (any Twelve Data symbol):
- Fetched on first search or watchlist add
- Historical OHLCV cached permanently in PostgreSQL
- Daily incremental updates for all cached symbols

## Data Refresh Schedule
- Twelve Data (equities, FX, commodities, minerals): every 10 min during market hours
- Twelve Data (crypto): every 10 min, 24/7
- Twelve Data (international): every 10 min during respective market hours
- FRED (rates, credit): once daily ~3:30 PM ET
- LLM narrative: twice daily (pre-market ~8 AM ET, after close ~4:30 PM ET)
- Historical cache: daily append for all cached symbols (~4:30 PM ET)

## Historical Data Strategy: Fetch on Demand, Cache Forever
Instead of bulk backfilling every symbol, historical daily OHLCV data is fetched the first time any symbol is requested. Once fetched, it's stored permanently. Daily incremental updates keep cached symbols current.

**Twelve Data constraints:**
- Grow tier: 55 API credits/minute (resets each minute)
- Time series = 1 credit per symbol
- Max 5,000 data points per request (~20 years of daily bars)
- EOD data available 30+ years back

**Rate limiting:** Queue backfill requests, never exceed 55 credits/min. Use exponential backoff on 429 errors.

## Narrative Archive
LLM-generated narratives are stored permanently in the `narrative_archive` table and browsable via the Journal page.

### Endpoints:
- `GET /api/narratives?date=YYYY-MM-DD`
- `GET /api/narratives/recent?days=7`
- `GET /api/regime-history`

## Chart Page (symbol deep dive)
When a user searches for or clicks on any symbol, they open a full chart page.

### Features:
- TradingView Lightweight Charts v5 (candlestick + line chart toggle)
- Volume bars (color-coded by candle direction)
- Moving average overlays: 20-day, 50-day, 200-day (toggleable)
- RSI (14) in separate synced pane with overbought/oversold lines
- Time range selector: 1D, 5D, 1M, 3M, 6M, 1Y, 5Y, Max
- Crosshair with OHLC data display
- Price stats: open, high, low, close, volume, 52-week high/low
- Back to dashboard navigation

### Chart page API:
- `GET /api/history/{symbol}?range=1Y&interval=1day` — returns OHLCV array
- `GET /api/profile/{symbol}` — returns name, exchange, sector, etc.

## Auth System
Email + password authentication with JWT tokens stored in httpOnly cookies.

### Endpoints:
- `POST /api/auth/register` — create account, set JWT cookie
- `POST /api/auth/login` — validate credentials, set JWT cookie
- `POST /api/auth/logout` — clear cookie
- `GET /api/auth/me` — return current user (id, email)
- `PUT /api/auth/change-password` — update password (requires current password)
- `PUT /api/auth/change-email` — update email (requires password), re-issues JWT

### Frontend:
- Polished auth modal (login/register toggle, close button, logo, password visibility toggle, loading spinner, animations)
- User dropdown menu when logged in (avatar circle, account settings, sign out)
- Account settings modal (change email, change password with inline feedback)

## Product Pages

### Page 1: The Market Picture (landing page = the product)
- Hero: Regime badge — big, bold, color-coded
- Sub-hero: One-liner reason for the regime
- Narrative: Claude-generated daily market story
- Asset class cards: Prices, change %, expandable charts
- Today's Movers: Up / Down buckets
- Search bar: Type any symbol → on-demand fetch → chart page
- No login required

### Page 2: Chart Page (symbol deep dive)
- Full candlestick chart with all analysis tools
- Reached via search or clicking any symbol
- No login required

### Page 3: Journal (narrative archive)
- Browse past narratives by date
- Filter by pre-market / after-close
- Regime badge + signal pills per entry

### Page 4: About
- Project description, how it works, who built it

### Logged-In Features (free account):
- User account management (change email, change password)
- Watchlists: Save symbols, personal tab (DB table exists, UI not yet built)
- Alerts (future): Regime change, VIX spike, price thresholds via email

## v2 Build Order

### Phase 5: PostgreSQL Migration — DONE
### Phase 6: Narrative Archive — DONE
### Phase 7: On-Demand Historical Data Cache — DONE
### Phase 8: Chart Page + Better Charting — DONE
### Phase 9: Auth + Watchlists — DONE
- Users table (id, email, password_hash, created_at)
- Email + password registration/login
- JWT token auth (httpOnly cookies)
- bcrypt password hashing
- Watchlists table (DB ready, UI not yet built)
- Frontend: login/register modal

### Phase 10: Landing Page Polish + Auth UX — DONE
- Mobile hamburger nav (collapsible below 640px)
- Touch targets (44px nav links, 40px buttons)
- Sparkline lazy loading (IntersectionObserver)
- Auth modal polish (close button, logo, password toggle, spinner, animations)
- User dropdown menu (avatar, account settings, sign out)
- Account settings modal (change email, change password)
- Change-password and change-email API endpoints

## What's Next
- Watchlist UI (frontend tab, CRUD endpoints — DB table already exists)
- Alerts (regime change, VIX spike, price thresholds via email)
- Further mobile/performance polish

## Code Conventions
- Python 3.11+, type hints everywhere
- Async where appropriate (FastAPI is async-native)
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
