# Technology Stack

**Analysis Date:** 2026-03-18

## Languages

**Primary:**
- Python 3.8+ - All backend logic, trading bot, data processing

**Secondary:**
- JavaScript/TypeScript - None (pure Python project)

## Runtime

**Environment:**
- Python 3.9.6 (development) / 3.11 (production Docker image)
- Platform: macOS (development), Linux containerized (production)

**Package Manager:**
- pip - Primary dependency manager
- Lockfile: `requirements.txt` (primary)
- Secondary config: `pyproject.toml` (tool configuration)

## Frameworks

**Core:**
- APScheduler 3.10+ - Job scheduling for trading signals (market open, market close)
- Streamlit 1.28+ - Web dashboard UI for monitoring positions and settings

**Async/Concurrency:**
- asyncio (stdlib) - Async/await for concurrent Telegram operations
- threading (stdlib) - Background thread management

**HTTP & Authentication:**
- requests 2.31+ - HTTP client for API calls
- requests-oauthlib 1.3+ - OAuth 1.0a client for E*TRADE authentication
- python-telegram-bot 20+ - Telegram bot framework with polling/webhook support

**Data Processing:**
- pandas 2.0+ - Historical data analysis, backtesting results
- numpy 1.24+ - Numerical calculations, performance metrics
- yfinance 0.2.33+ - Yahoo Finance fallback data source

**Visualization:**
- plotly 5.18+ - Interactive charts in Streamlit dashboard

## Key Dependencies

**Critical:**
- anthropic 0.75+ - Claude API for monthly strategy pattern analysis
- alpaca-py 0.20+ - Alpaca Markets API client for real-time market data
- resend 2.0+ - HTTP-based email delivery (SMTP blocked on Railway)

**Infrastructure & Reliability:**
- structlog 23.0+ - Structured JSON logging with correlation IDs for observability
- tenacity 8.2+ - Retry logic with exponential backoff for flaky APIs
- keyring 24+ - Secure credential storage (macOS Keychain, Windows Credential Manager)
- twilio 8.0+ - SMS notifications for critical alerts (infrastructure failures, execution failures)

**Development & Quality:**
- ruff 0.1.6+ - Fast Python linter and formatter
- pre-commit 3.5+ - Git hooks for code quality checks
- pytest 7.4+ - Unit testing framework
- pytest-cov 4.1+ - Code coverage measurement
- python-dotenv 1.0+ - Environment variable management from `.env` files
- typing-extensions 4.8+ - Type hints for Python 3.8 compatibility

**Database:**
- sqlite3 (stdlib) - Persistent storage for trades, bot state, event logs
- Default path: `./trades.db` (configurable via `DATABASE_PATH` env var)

## Configuration

**Environment:**
- Loaded from `.env` file at startup (via `python-dotenv`)
- `.env.example` documents all available variables
- Critical variables:
  - `ETRADE_CONSUMER_KEY`, `ETRADE_CONSUMER_SECRET`, `ETRADE_ACCOUNT_ID` - Trading API
  - `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` - Market data provider
  - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` - Notifications & approvals
  - `ANTHROPIC_API_KEY` - Monthly strategy reviews
  - `TRADING_MODE` - `paper` (simulated) or `live` (real money)

**Build:**
- `pyproject.toml` - Python project metadata, ruff configuration, pytest settings
- `requirements.txt` - Pinned dependency versions
- `Dockerfile` - Containerized deployment (Python 3.11-slim base)
- `Procfile` - Railway deployment specification (worker: python -m src.worker)
- `railway.toml` - Railway platform configuration

## Platform Requirements

**Development:**
- Python 3.8+ (tested 3.9.6)
- Unix-like shell (bash/zsh)
- ~500MB disk (dependencies + database)
- E*TRADE account with API access (for live trading)
- Internet connection (for APIs)

**Production:**
- Deployment target: Railway platform
- Docker containerized environment
- Python 3.11
- Persistent volume for SQLite database (`DATABASE_PATH=/data/trades.db` when mounted)
- Linux container runtime
- TZ environment variable set to `America/New_York` (trading hours timezone)

## Build & Start Commands

```bash
# Development setup
pip install -r requirements.txt

# Run trading bot (background scheduler)
python run_bot.py              # Paper mode
python run_bot.py --live       # Live trading
python run_bot.py --once       # Single execution

# Run Streamlit dashboard (local monitoring)
streamlit run app.py

# Run background worker (Railway deployment)
python -m src.worker

# Run tests
pytest tests/ -v
pytest tests/ -k "strategy"    # Specific test subset
pytest tests/ --cov           # With coverage
```

## Security Notes

- E*TRADE tokens stored in `.etrade_tokens.json` (gitignored)
- API keys managed via environment variables (never committed)
- Keyring integration enables secure credential storage on development machines
- `filelock>=3.20.1` pinned to address CVE-2025-68146 (requires Python 3.10+ on Railway)

---

*Stack analysis: 2026-03-18*
