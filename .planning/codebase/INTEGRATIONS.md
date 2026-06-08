# External Integrations

**Analysis Date:** 2026-03-18

## APIs & External Services

**Broker/Trading:**
- E*TRADE - Primary trading execution platform
  - SDK/Client: `requests-oauthlib` (custom wrapper in `src/etrade_client.py`)
  - Auth: OAuth 1.0a with token refresh
  - Env vars: `ETRADE_CONSUMER_KEY`, `ETRADE_CONSUMER_SECRET`, `ETRADE_ACCOUNT_ID`
  - Token storage: `.etrade_tokens.json` (persisted, auto-refreshed)
  - Endpoints used:
    - `/oauth/request_token`, `/oauth/access_token` - Authentication flow
    - `/oauth/renew_access_token` - Token refresh
    - Quotes - Real-time price data
    - Orders - Place/preview trades
    - Accounts - Balance and positions

**Market Data:**
- Alpaca Markets API - Primary real-time data source (recommended)
  - SDK/Client: `alpaca-py` 0.20+
  - Auth: API key + secret in headers
  - Env vars: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`
  - Endpoints:
    - `https://data.alpaca.markets/v2/stocks/{symbol}/snapshot` - Real-time quotes
    - `https://data.alpaca.markets/v1beta3/crypto/us/latest/quotes` - Crypto (BTC) quotes
    - Historical bars for backtesting
  - Fallback provider in `src/data_providers.py`

- Finnhub API - Secondary real-time data source (optional backup)
  - SDK/Client: HTTP requests via `requests`
  - Auth: API key in query param
  - Env var: `FINNHUB_API_KEY`
  - Base URL: `https://finnhub.io/api/v1`
  - Fallback provider in `src/data_providers.py`

- Yahoo Finance - Tertiary fallback (15-min delay, no auth)
  - SDK/Client: `yfinance` 0.2.33+
  - Auth: None (public endpoint)
  - Used as last-resort fallback when primary sources unavailable
  - Historical data provider for backtesting
  - Fallback provider in `src/data_providers.py`

**Data Flow:**
1. Primary: Alpaca (real-time, free tier)
2. Secondary: Finnhub (if Alpaca unavailable)
3. Fallback: Yahoo Finance (if both unavailable)
See `src/data_providers.py` for provider chain implementation.

## Data Storage

**Databases:**
- SQLite 3 (stdlib)
  - Connection: Configured via `DATABASE_PATH` env var, defaults to `./trades.db`
  - Client: Built-in `sqlite3` module (no ORM)
  - Schema management: Manual SQL in `src/database.py` (`_init_db()`)
  - Tables:
    - `trades` - Entry/exit prices, timestamps, P&L
    - `bot_state` - Current position, mode, session metadata
    - `event_log` - Activity history (signals, approvals, errors)
  - Production deployment: Persistent volume at `/data/trades.db` on Railway

**File Storage:**
- Local filesystem only
  - Token files: `.etrade_tokens.json`, `.etrade_request_token.json`
  - Settings: `.user_settings.json` (Streamlit session persistence)
  - Configuration: `config.json` (optional, usually env vars instead)
  - Database: `trades.db` and write-ahead log (`trades.db-shm`, `trades.db-wal`)

**Caching:**
- None - All data fetched fresh on demand

## Authentication & Identity

**Auth Provider:**
- E*TRADE OAuth 1.0a - Custom implementation
  - Implementation: `src/etrade_client.py` (`ETradeClient` class)
  - Token lifecycle:
    1. Initial auth via browser (user visits E*TRADE authorization URL)
    2. Tokens stored in `.etrade_tokens.json`
    3. Automatic refresh via `OAuth_Renew_Token` endpoint
    4. Manual revoke via `OAuth_Revoke_Token` if needed
  - Expires: Tokens persist until manually revoked (not time-limited)

- Telegram Token Auth - Bot identity only
  - Bot token: `TELEGRAM_BOT_TOKEN`
  - Chat ID: `TELEGRAM_CHAT_ID` (your private chat)
  - Client: `python-telegram-bot` 20+
  - User approval: Inline keyboard buttons for trade confirmation

## Notifications & Messaging

**Telegram Bot:**
- SDK/Client: `python-telegram-bot` 20+
- Bot token: `TELEGRAM_BOT_TOKEN` (created via @BotFather)
- Chat ID: `TELEGRAM_CHAT_ID` (your Telegram user ID)
- Base: `src/telegram/` package
- Features:
  - Real-time trade notifications (`src/telegram/trading_commands.py`)
  - Trade approval with inline buttons (`/approve` flows)
  - Backtest results (`/backtest` command)
  - Analysis summaries (`/analysis` command)
  - Auth commands (`/ping`, `/status`)
  - Error alerts via `src/error_alerting.py`
  - Markdown formatting with escape utilities
- Connection: Long-polling (not webhook)
- Implementation: `src/telegram/bot.py` (`TelegramBot` class with mixins)

**Email Notifications (Optional):**
- Resend API - HTTP-based email delivery
  - Why: SMTP ports blocked on Railway platform
  - SDK/Client: `resend` 2.0+
  - API key: `RESEND_API_KEY` env var
  - Sender: `onboarding@resend.dev` (default template)
  - Implementation: `src/email_reports.py` (`send_email()` function)
  - Usage: Monthly strategy review reports

**SMS Notifications (Infrastructure):**
- Twilio - SMS for critical infrastructure alerts
  - SDK/Client: `twilio` 8.0+
  - When used: Critical errors (API failures, execution failures)
  - Configuration: Not fully implemented in current codebase (infrastructure for future)

**Desktop Notifications:**
- Plyer 2.1+ - Native desktop notifications (development only)
  - Not available on Railway (headless environment)
  - Used locally via `src/notifications.py`

## AI & Analysis

**Claude API (Anthropic):**
- SDK/Client: `anthropic` 0.75+
- API key: `ANTHROPIC_API_KEY` env var
- Purpose: Monthly strategy pattern discovery
- Implementation: `src/strategy_review/reviewer.py` (`StrategyReviewer` class)
- Features:
  - Analyzes trade history for performance patterns
  - Makes parameter recommendations (thresholds, enable/disable flags)
  - Uses structured tool calling for decisions
  - Generates natural language reports
- Usage: One monthly analysis (scheduled or manual)

## Monitoring & Observability

**Error Tracking:**
- Telegram alerts - All critical errors sent to `TELEGRAM_CHAT_ID`
- Implementation: `src/error_alerting.py` (`alert_error()` function)
- Severity levels: CRITICAL, WARNING, INFO, ANOMALY
- Rate limiting: CRITICAL (immediate), WARNING (5 min), INFO (30 min), ANOMALY (10 min)

**Logs:**
- Approach: Structured logging via `structlog` 23+
- Output: Console/stdout (JSON structured logs for Railway)
- Files: Optional file rotation (configurable in `src/config.py`)
- Correlation IDs: Tracked per trade for request tracing
- Levels: DEBUG, INFO, WARNING, ERROR, CRITICAL

## CI/CD & Deployment

**Hosting:**
- Railway platform (primary)
- Docker containerized (Python 3.11-slim)
- Procfile: Single worker process (`python -m src.worker`)

**CI Pipeline:**
- None detected - No GitHub Actions, GitLab CI, or similar
- Pre-commit hooks available: `ruff` linting and formatting

**Build Process:**
- Docker build in Railway (automatic)
- Base: `python:3.11-slim`
- Steps: Install system deps → pip install requirements → copy code → set env vars
- Restart policy: Always (restarts on crash, max 3 retries)

## Environment Configuration

**Required env vars (live trading):**
- `ETRADE_CONSUMER_KEY` - E*TRADE API key
- `ETRADE_CONSUMER_SECRET` - E*TRADE API secret
- `ETRADE_ACCOUNT_ID` - Account number for trading
- `TELEGRAM_BOT_TOKEN` - Telegram bot token
- `TELEGRAM_CHAT_ID` - Your Telegram user ID
- `ALPACA_API_KEY` - Alpaca market data key
- `ALPACA_SECRET_KEY` - Alpaca market data secret

**Optional env vars:**
- `FINNHUB_API_KEY` - Finnhub fallback data (optional)
- `ANTHROPIC_API_KEY` - Claude for monthly reviews
- `RESEND_API_KEY` - Email delivery via Resend
- `REPORT_EMAIL` - Email recipient for reports
- `TRADING_MODE` - `paper` (default) or `live`
- `DATABASE_PATH` - Custom SQLite database location
- `MAX_POSITION_PCT` - Position size as % of cash (default: 75)
- `MAX_POSITION_USD` - Position size in dollars (optional limit)
- `APPROVAL_MODE` - `required` (default) or `notify_only` or `auto_execute`

**Secrets location:**
- `.env` file (gitignored, never committed)
- Keyring system for development machines (via `keyring` 24+)
- Railway environment variables (configured in platform UI)

## Webhooks & Callbacks

**Incoming:**
- Telegram bot commands (no webhook, polling-based)
  - `/approve` - Trade approval callback
  - `/reject` - Trade rejection callback
  - `/backtest` - Analysis request
  - Other commands handled via callback handlers

**Outgoing:**
- None - Unidirectional outbound calls only
- E*TRADE: One-way orders
- Alpaca: One-way data queries
- Telegram: One-way notifications
- Resend: One-way email send
- Anthropic: One-way analysis requests

## API Rate Limits & Quotas

**E*TRADE:**
- Production: Standard rate limits (documented in API)
- Requests: Quote calls throttled in code (logging on failures)

**Alpaca:**
- Free tier: Real-time data available
- Requests: One-time snapshot per quote call

**Finnhub:**
- Free tier: 60 API calls per minute
- Fallback behavior: Retried on rate limit via `tenacity`

**Telegram:**
- Limits: Polling-based (not webhook rate-limited)
- Backoff: Automatic via `python-telegram-bot`

**Anthropic (Claude):**
- Rate limit: Standard Claude API limits
- Retry policy: `tenacity` with exponential backoff (configured in reviewer)

---

*Integration audit: 2026-03-18*
