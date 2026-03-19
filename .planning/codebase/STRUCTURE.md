# Codebase Structure

**Analysis Date:** 2026-03-18

## Directory Layout

```
ibit/
├── app.py                      # Streamlit dashboard (45-sec refresh, monitoring + settings)
├── run_bot.py                  # CLI entry point (python run_bot.py [--live] [--once])
├── pyproject.toml              # Python project config (pytest, ruff)
├── requirements.txt            # Production dependencies
├── CLAUDE.md                   # AI agent instructions (critical rules, component map)
├── README.md                   # Project overview, setup guide
├── .env.example                # Template for required env vars
├── Dockerfile                  # Container image (Railway deployment)
├── Procfile                    # Railway process definition
│
├── src/                        # Main application code
│   ├── __init__.py
│   ├── worker.py               # Railway worker (24/7 scheduler + Telegram polling)
│   ├── async_utils.py          # Async/sync bridge utilities (critical pattern file)
│   ├── utils.py                # Timezone (ET), market hours, day-of-week helpers
│   ├── config.py               # Logging setup
│   ├── database.py             # SQLite singleton, schema init, WAL mode
│   ├── etrade_client.py        # OAuth client, order placement, quote fetching
│   ├── data_providers.py       # MarketDataManager, Quote abstraction, provider chain
│   ├── error_alerting.py       # Telegram error notifications
│   ├── notifications.py        # NotificationConfig, NotificationManager
│   ├── smart_strategy.py       # Signal generation (CASH/CRASH/PUMP/MR/10AM), StrategyConfig
│   ├── smart_scheduler.py      # APScheduler orchestration (9:35/10:45/3:55 jobs)
│   ├── trailing_hedge.py       # HedgeManager for trailing stop/loss reversal
│   ├── pattern_discovery.py    # Pattern registry for AI-discovered signals (deprecated)
│   ├── backtester.py           # Historical simulation for strategy testing
│   ├── strategy_review.py      # Historical analysis and parameter optimization
│   │
│   ├── trading_bot/            # TradingBot with mixin composition
│   │   ├── __init__.py         # Exports create_trading_bot() factory
│   │   ├── core.py             # TradingBot class, __init__, factory function
│   │   ├── config.py           # BotConfig, TradingMode, ApprovalMode, TradeResult
│   │   ├── execution_mixin.py  # execute_signal(), order placement, approval flow
│   │   ├── positions_mixin.py  # get_open_positions(), close_all_positions()
│   │   ├── orders_mixin.py     # Order tracking, fill polling, duplicate prevention
│   │   ├── hedge_mixin.py      # Trailing hedge, loss reversal logic
│   │   └── notifications_mixin.py  # Trade logging, Telegram notifications
│   │
│   ├── telegram/               # Modular Telegram bot with command mixins
│   │   ├── __init__.py
│   │   ├── bot.py              # TelegramBot class, handler registration, polling
│   │   ├── base.py             # BaseMixin base class
│   │   ├── utils.py            # escape_markdown(), ApprovalResult, TradeApprovalRequest
│   │   ├── trading_commands.py # /mode, /pause, /resume, /balance, /positions, /sellall
│   │   ├── analysis_commands.py # /analyze, /patterns, /review, /hedge, /promote, /retire
│   │   ├── auth_commands.py    # /auth, /verify (E*TRADE OAuth flow)
│   │   ├── backtest_commands.py # /backtest, /simulate
│   │   └── notifier.py         # TelegramNotifier for async notifications
│   │
│   └── strategy_review/        # Strategy optimization package
│       ├── __init__.py
│       ├── config.py           # ReviewConfig dataclass
│       ├── models.py           # PatternResult, ReviewResult dataclasses
│       ├── backtesting.py      # Run-on-date backtesting
│       ├── market_data.py      # Historical data fetching
│       └── reviewer.py         # StrategyReviewer (runs simulations, saves recommendations)
│
├── tests/                      # Unit and integration tests
│   ├── __init__.py
│   ├── test_smart_strategy.py  # Config defaults, signal types, dataclass methods
│   ├── test_trading_bot.py     # Execution, paper mode, position management
│   ├── test_database.py        # Persistence, concurrent access
│   ├── test_strategy.py        # Historical strategy backtesting
│   ├── test_backtester.py      # Backtester simulation
│   ├── test_async_utils.py     # Async/sync bridge patterns
│   └── test_integration_async.py # APScheduler + Telegram bridging
│
├── analysis/                   # Research notebooks (historical data analysis)
│   ├── strategy_deep_analysis.py
│   ├── btc_correlation_analysis.py
│   ├── crash_day_analysis.py
│   └── ... (14 analysis scripts - exploratory, not production)
│
├── scripts/                    # Utility scripts
│   ├── etrade_setup.py         # OAuth flow setup (manual E*TRADE token generation)
│   └── backtest_10am_dump.py   # One-off backtest for 10 AM strategy
│
├── docs/                       # Documentation
│   ├── architecture.md         # System design overview
│   ├── prd.md                  # Product requirements (signals, live vs paper)
│   ├── STRATEGY_GUIDE.md       # Strategy thresholds, filters, validation rules
│   ├── deployment-plan.md      # Railway, Docker, environment setup
│   └── sprint-artifacts/       # Agile sprint documentation
│
├── legacy/                     # Deprecated code (kept for reference)
│   ├── app_legacy.py           # Old Streamlit app
│   ├── run_backtests.py
│   └── run_bitx_backtest.py
│
├── patterns/                   # Discovered patterns registry
│   └── active_patterns.json
│
├── .planning/                  # GSD planning output (docs for future agents)
│   └── codebase/               # Codebase analysis docs (this directory)
│       ├── ARCHITECTURE.md
│       └── STRUCTURE.md
│
├── data/                       # Local data files
│   └── trading.db              # Historical trades (development)
│
├── trades.db                   # Main database (production)
├── trades.db-shm               # SQLite WAL shared memory
├── trades.db-wal               # SQLite WAL write-ahead log
└── .env                        # Secrets (not committed)
```

## Directory Purposes

**src/:**
- Core application logic
- All business rules, integrations, persistence
- Organized by layer (strategy, execution, data, telegr­am)

**src/trading_bot/:**
- TradingBot and its component mixins
- Separates concerns: execution, order tracking, position management, hedging
- Mixins allow adding features without deep inheritance

**src/telegram/:**
- Modular Telegram bot implementation
- Each command module imports BaseMixin
- TelegramBot combines all mixins via class inheritance

**src/strategy_review/:**
- Backtest engine for strategy optimization
- Used by /review Telegram command
- Generates recommendations, saves to database

**tests/:**
- Unit tests for critical paths (strategy, execution, database)
- Integration tests for async/sync bridging
- Test files co-located naming: test_{module_name}.py

**analysis/:**
- Historical data exploration (14 notebooks)
- Used during strategy development
- Not part of production execution

**scripts/:**
- One-off utilities (E*TRADE OAuth setup, single-strategy backtests)
- Manually invoked

**docs/:**
- Design docs, PRD, strategy guide
- Architecture diagrams

**legacy/:**
- Old implementations kept for reference
- Not imported by production code

## Key File Locations

**Entry Points:**
- `app.py`: Streamlit web interface
- `run_bot.py`: CLI scheduler runner
- `src/worker.py`: Railway container worker

**Configuration:**
- `.env`: Environment variables (secrets - not committed)
- `.env.example`: Template
- `pyproject.toml`: Python project metadata
- `CLAUDE.md`: AI instructions

**Core Logic:**
- `src/smart_strategy.py`: Signal generation (StrategyConfig, SmartStrategy, Signal enum)
- `src/trading_bot/core.py`: TradingBot class composition
- `src/trading_bot/execution_mixin.py`: Trade execution flow
- `src/smart_scheduler.py`: APScheduler job setup

**Integrations:**
- `src/etrade_client.py`: OAuth, orders, account queries
- `src/data_providers.py`: Quote fetching with fallback chain
- `src/telegram/bot.py`: Command routing, polling
- `src/database.py`: SQLite persistence

**Utilities:**
- `src/async_utils.py`: Sync→async and async→sync bridging
- `src/utils.py`: Timezone (ET), market hours, date helpers

**Testing:**
- `tests/test_smart_strategy.py`: Strategy config, signal types
- `tests/test_trading_bot.py`: Execution, paper mode
- `tests/test_async_utils.py`: Bridge patterns

## Naming Conventions

**Files:**
- Underscores for multi-word: `smart_strategy.py`, `etrade_client.py`
- Mixins: `{component}_mixin.py` (e.g., `execution_mixin.py`)
- Commands: `{type}_commands.py` (e.g., `trading_commands.py`)
- Tests: `test_{module}.py` for unit tests

**Directories:**
- Lowercase, underscores: `src/trading_bot/`, `src/telegram/`, `src/strategy_review/`
- Related files grouped in packages (e.g., all telegram code in `src/telegram/`)

**Classes:**
- PascalCase: `TradingBot`, `SmartStrategy`, `ETradeClient`, `MarketDataManager`
- Enums: `Signal`, `TradingMode`, `ApprovalMode`, `DataSource`
- Dataclasses: `BotConfig`, `StrategyConfig`, `TradeResult`, `TodaySignal`
- Mixins: `ExecutionMixin`, `PositionsMixin`, `OrdersMixin`

**Functions:**
- Lowercase with underscores: `get_today_signal()`, `execute_signal()`, `get_open_positions()`
- Boolean predicates: `is_market_open()`, `is_trading_day()`
- Factories: `create_trading_bot()`, `create_data_manager()`
- Private: Prefix with `_`: `_wait_for_order_fill()`, `_check_duplicate_trade()`

**Variables:**
- Lowercase with underscores: `entry_price`, `position_size`, `available_capital`
- Constants: UPPERCASE: `ET` (timezone), `ETRADE_PRODUCTION_BASE` (URL)
- Private: Prefix with `_`: `_paper_capital`, `_trades_today`, `_position_lock`

**Database Tables:**
- Lowercase, underscores: `trades`, `bot_state`, `event_log`, `strategy_params`

## Where to Add New Code

**New Strategy Signal:**
1. Add new Signal enum value in `src/smart_strategy.py` (e.g., Signal.NEW_PATTERN)
2. Add config option in StrategyConfig dataclass
3. Implement signal logic in SmartStrategy.get_today_signal()
4. Add test in `tests/test_smart_strategy.py`
5. If needs scheduler job, update `src/smart_scheduler.py` setup_jobs()

**New Execution Feature (reversal, hedge, etc.):**
1. Create `{feature}_mixin.py` in `src/trading_bot/`
2. Implement as mixin class accessing `self.*` from TradingBot
3. Import and add to TradingBot inheritance chain in `src/trading_bot/core.py`
4. Add tests in `tests/test_trading_bot.py`

**New Telegram Command:**
1. Create method in appropriate mixin (`src/telegram/trading_commands.py`, etc.)
2. Decorate with `@run_bot_protected` (auth check)
3. Register handler in `TelegramBot.__init__()` setup_handlers()
4. Add docstring with command name and description

**Database Changes:**
1. Add migration SQL in `src/database.py` _init_db()
2. Use ALTER TABLE ... ADD COLUMN ... DEFAULT ... for backward compatibility
3. Never delete columns (breaks old backups)
4. Test with existing database file

**New Notification:**
1. Add method to `NotificationManager` (if custom format)
2. Or use `TelegramNotifier.send_message()` directly
3. Location in code: In ExecutionMixin after trade result

**Utilities:**
- Timezone helpers: `src/utils.py`
- Market hour predicates: `src/utils.py`
- Async/sync bridge: `src/async_utils.py`
- Shared data models: `src/data_providers.py` (Quote) or `src/trading_bot/config.py` (TradeResult)

## Special Directories

**trades.db & trades.db-wal:**
- Purpose: Production SQLite database
- Generated: On first app run (_init_db creates schema)
- Committed: No (databases not in git)
- Backed up: Manual backup recommended before major updates

**.planning/codebase/:**
- Purpose: Documentation for future AI agents (GSD codebase mapping)
- Generated: By /gsd:map-codebase orchestrator commands
- Committed: Yes (kept in git for agent reference)
- Files: ARCHITECTURE.md, STRUCTURE.md, CONVENTIONS.md, TESTING.md, CONCERNS.md

**patterns/active_patterns.json:**
- Purpose: Registry of AI-discovered trading patterns
- Generated: By /promote Telegram command
- Committed: Yes (represents model's pattern library)
- Format: JSON array of PatternResult objects

**.env:**
- Purpose: Runtime secrets (API keys, tokens, chat IDs)
- Generated: Manual creation from .env.example
- Committed: No (.gitignore prevents accidental leak)
- Required vars: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, (ETRADE_* for live mode)

**legacy/:**
- Purpose: Historical reference implementations
- Committed: Yes (no cleanup)
- Used: Never imported by production code

---

*Structure analysis: 2026-03-18*
