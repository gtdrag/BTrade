# Architecture

**Analysis Date:** 2026-03-18

## Pattern Overview

**Overall:** Modular Monolith with Mixin-Based Component Composition

**Key Characteristics:**
- Layered separation: Strategy → Execution → Broker/Data/Notifications
- Async/sync bridge pattern for crossing framework boundaries (APScheduler ↔ Telegram)
- Mixin-based composition for TradingBot feature aggregation (no deep inheritance)
- Singleton database for state persistence across async/sync contexts
- Signal-driven architecture: Strategy generates signals, Scheduler orchestrates execution

## Layers

**Strategy Layer:**
- Purpose: Evaluate market conditions and generate trading signals
- Location: `src/smart_strategy.py`
- Contains: Signal generation logic, position action rules, threshold configurations
- Depends on: MarketDataManager, Database (for daily trade flags)
- Used by: TradingBot (via get_today_signal), SmartScheduler

**Execution Layer:**
- Purpose: Execute trades in paper or live mode, manage order lifecycle
- Location: `src/trading_bot/execution_mixin.py`
- Contains: Trade execution, Telegram approval flow, order fill polling, duplicate prevention
- Depends on: ETradeClient (live mode), NotificationManager, TelegramNotifier, Database
- Used by: SmartScheduler (via execute_signal)

**Broker/Integration Layer:**
- Purpose: Connect to external services
- Location: `src/etrade_client.py`, `src/data_providers.py`, `src/telegram/bot.py`
- Contains: OAuth flow, quote fetching, order placement, Telegram command routing
- Depends on: requests-oauthlib, python-telegram-bot, yfinance, Alpaca API
- Used by: Execution layer, Strategy layer

**Data/Persistence Layer:**
- Purpose: Store trades, state, logs, strategy parameters
- Location: `src/database.py`
- Contains: SQLite schema, connection pooling, WAL mode for concurrency
- Depends on: sqlite3 (standard library)
- Used by: All layers for audit trail and state recovery

**Orchestration Layer:**
- Purpose: Schedule and coordinate execution
- Location: `src/smart_scheduler.py`, `src/worker.py`
- Contains: APScheduler job setup, event handlers, signal routing
- Depends on: APScheduler (Background Scheduler), TradingBot, TelegramBot
- Used by: Entry points (run_bot.py, worker.py, app.py)

## Data Flow

**Morning Signal Flow (9:35 AM Entry):**

1. SmartScheduler job fires (APScheduler trigger)
2. Calls `TradingBot.execute_signal()`
3. ExecutionMixin.execute_signal():
   - Fetches current market data via MarketDataManager
   - Calls SmartStrategy.get_today_signal()
   - Strategy evaluates thresholds, checks overnight BTC filter, position context
   - Returns TodaySignal with signal type, ETF, quantity, reason
4. If signal is CASH, returns early (no trade)
5. If signal is trading (BITU/SBIT):
   - Check for duplicate trade today (prevents multiple entries)
   - Get quote for price (fallback chain: E*TRADE → Alpaca → Finnhub → Yahoo)
   - Calculate position size based on available capital
   - If approval_mode=required: Send Telegram approval request, wait for response
   - If approved or auto-approve: Place order (E*TRADE or paper)
   - Poll for order fill (max 30 retries, 30-second intervals)
   - Record trade in database, log to event_log
   - Send trade confirmation via Telegram
6. SmartScheduler logs signal check to database for analytics

**Crash/Pump Day Intraday Flow (9:45 AM - 3:30 PM):**

1. SmartScheduler runs monitoring job every 15 minutes (configurable check_times)
2. Calls `TradingBot.execute_signal()` with skip_approval=True
3. Strategy checks if IBIT has moved ±1.5% from open (configurable)
4. If threshold hit and not already traded today: Execute immediately (no approval wait)
5. Send trade confirmation (auto-approved)

**Position Management Flow:**

1. HedgeMixin monitors open positions during market hours
2. If trailing hedge enabled: Check if position down ≥1% intraday
3. If position down ≥2% (reversal_threshold): Trigger reversal
   - Close current position (BITU or SBIT)
   - Enter opposite position (SBIT or BITU)
   - Log reversal reason to database
4. SmartScheduler 3:55 PM job closes all open positions
5. Record exit time, P&L, hold duration

**State Management:**

- Daily Trade Flags: Reset at midnight ET
  - `_crash_day_traded_today`, `_pump_day_traded_today` in SmartStrategy
  - Used to prevent duplicate signal executions
- Position Lock: `threading.RLock` in TradingBot
  - Prevents concurrent modification races from APScheduler jobs
  - Allows reversal (close → enter) within same thread
- Telegram Approval Event: Async event waits for user response
  - Uses asyncio.Event for blocking until approval/rejection
  - Timeout after N minutes (default 10)

**State Recovery:**

Database persists:
- `trades` table: Entry time, symbol, quantity, entry price, exit time, exit price, P&L
- `bot_state` table: Last signal check, last trade, trading mode, approval mode
- `event_log` table: All strategy checks, approvals, errors, reversals
- `strategy_params` table: User-customized thresholds (from /review command)

On restart, worker loads persisted strategy params and applies them to SmartStrategy.

## Key Abstractions

**TradaySignal:**
- Purpose: Encapsulate a single day's trading signal
- Location: `src/smart_strategy.py`
- Contains: signal (Signal enum), etf (str), quantity (int), reason (str), position_action
- Pattern: Immutable dataclass, returned by strategy.get_today_signal()
- Used by: ExecutionMixin to decide if/what to trade

**Signal (Enum):**
- Purpose: Standardize signal types across codebase
- Values: CASH, MEAN_REVERSION, CRASH_DAY, PUMP_DAY, TEN_AM_DUMP, DISCOVERED, HOLD, CLOSE_LONG, CLOSE_SHORT
- Pattern: Used in database queries, Telegram notifications, test assertions
- Location: `src/smart_strategy.py`

**TradeResult:**
- Purpose: Record outcome of a trade execution attempt
- Location: `src/trading_bot/config.py`
- Contains: success (bool), signal (Signal), etf (str), action (str), quantity (int), entry_price, exit_price, p_l, error_msg, is_paper (bool)
- Pattern: Used for audit trail, Telegram confirmation message, database logging

**Quote:**
- Purpose: Standardized price data from any provider
- Location: `src/data_providers.py`
- Contains: symbol, current_price, open_price, high/low, bid/ask, volume, source, is_realtime
- Pattern: Enables provider abstraction - ExecutionMixin doesn't care if data comes from E*TRADE or Yahoo

**MarketDataManager:**
- Purpose: Provide quotes with automatic fallback
- Location: `src/data_providers.py`
- Pattern: Tries E*TRADE (real-time, live mode only) → Alpaca → Finnhub → Yahoo (15-min delay)
- Used by: SmartStrategy, ExecutionMixin for price fetches

**Database Singleton:**
- Purpose: Single persistent connection across all components
- Location: `src/database.py`
- Pattern: `get_database()` factory function returns module-level instance
- Ensures: All audit trails go to same file, threading-safe with WAL mode

## Entry Points

**run_bot.py (CLI):**
- Location: `/Users/georgedrag/APP_PROJECTS/ibit/run_bot.py`
- Triggers: Manual execution, development testing, `--live` for live trading, `--once` for signal check only
- Responsibilities:
  - Parse CLI args (--live, --once)
  - Create BotConfig from CLI/env vars
  - Instantiate TradingBot, SmartScheduler
  - Start scheduler loop (or single signal check if --once)
  - Handle graceful shutdown on Ctrl+C

**worker.py (Railway/Production):**
- Location: `/Users/georgedrag/APP_PROJECTS/ibit/src/worker.py`
- Triggers: Container startup via `python -m src.worker`
- Responsibilities:
  - Load persisted strategy params from database
  - Create E*TRADE client (with token refresh logic)
  - Instantiate TradingBot, SmartScheduler
  - Start Telegram bot polling (receives /commands)
  - Run async event loop (keeps polling alive)
  - Handle SIGTERM/SIGINT for graceful shutdown

**app.py (Streamlit Dashboard):**
- Location: `/Users/georgedrag/APP_PROJECTS/ibit/app.py`
- Triggers: `streamlit run app.py`
- Responsibilities:
  - Display real-time position monitoring (45-second refresh)
  - Show trade history, P&L
  - Allow manual /approve /reject via buttons
  - Settings panel (toggle strategies, adjust position %)
  - Persist UI settings to `.user_settings.json`

**Telegram Commands (via TelegramBot):**
- Location: `src/telegram/bot.py` (router), command mixins in `src/telegram/`
- Triggers: User messages `/command` to Telegram chat
- Responsibilities:
  - `/mode` - Switch paper/live
  - `/pause` - Suspend scheduler
  - `/resume` - Restart scheduler
  - `/positions` - Query open positions
  - `/balance` - Show available capital
  - `/signal` - Manual signal check
  - `/approve` - Approve pending trade
  - `/sellall` - Liquidate all positions
  - `/review` - Run strategy review, save recommendations

## Error Handling

**Strategy:** Explicit Signal.CASH when conditions not met

**Execution:**
- E*TRADE auth errors → log to database, send Telegram alert, continue in paper mode
- Quote fetch failures → fallback to next provider in chain
- Order placement failures → TradeResult.error_msg, notify user, no position recorded
- Order fill timeout → log "unfilled" state, may need manual intervention
- Approval timeout → trade rejected, logged to database

**Telegram:**
- Command auth failures → silent (prevents unauthorized control)
- Async exceptions → logged with traceback, sent to error_alerting
- Polling disconnects → auto-reconnect with exponential backoff

**Database:**
- Connection failures → retry with 30-second timeout
- WAL mode handles concurrent readers/writers
- Schema migrations apply on startup (add column with default)

## Cross-Cutting Concerns

**Logging:**
- Framework: Python logging module
- Format: `[timestamp] [level] [module]: message`
- Configured in `src/config.py` setup_logging()
- All database operations logged to event_log table

**Validation:**
- Input: Strategy thresholds validated in StrategyConfig dataclass
- Business: SmartStrategy checks market hours before generating signals
- Trade: ExecutionMixin validates capital > 0 before calculating position size

**Authentication:**
- E*TRADE: OAuth 1.0a with token refresh, tokens stored in `.etrade_tokens.json`
- Telegram: Bot token verified on startup, chat_id used for authorization
- Database: No auth (local SQLite)

**Timezone Handling:**
- Always use `get_et_now()` from `src/utils.py`, never `datetime.now()`
- All strategy triggers, scheduled jobs, database timestamps in ET
- Database stores timezone-aware datetime objects

**Async/Sync Bridging:**
- Pattern 1 (sync→async): `run_async_from_sync(coro)` - Used by APScheduler jobs calling Telegram
- Pattern 2 (async→sync): `await run_sync_in_executor(func, *args)` - Used by Telegram handlers calling E*TRADE
- Location: `src/async_utils.py`
- Thread pool: Single shared executor across application

---

*Architecture analysis: 2026-03-18*
