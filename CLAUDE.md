# AI Quick Reference - BTrade

> Concise guide for AI agents maintaining this codebase. For full docs, see `docs/architecture.md` and `docs/prd.md`.

## What This System Does

Bitcoin ETF trading bot that:
1. Runs **intraday only** (9:35 AM entry, 3:55 PM exit, never holds overnight)
2. Trades **BITU** (2x long) or **SBIT** (2x inverse) based on signals
3. Stays in **cash** when there's no high-probability setup (~67% of days)
4. Uses E*TRADE for execution, Telegram for approvals/notifications

## Trading Strategies (The "Why")

| Strategy | Trigger | Action | Rationale |
|----------|---------|--------|-----------|
| **Mean Reversion** | IBIT dropped ≥2% yesterday + BTC up overnight | Buy BITU | 84% win rate with BTC filter |
| **10 AM Dump** | Every day 9:35-10:30 AM | Buy SBIT | Captures morning weakness pattern |
| **Crash Day** | IBIT drops ≥1.5% intraday | Buy SBIT | Momentum continuation |
| **Pump Day** | IBIT rises ≥1.5% intraday | Buy BITU | Momentum continuation |
| **Reversal** | Holding position drops ≥2% | Flip to opposite ETF | Cut losses, ride reversal |

**Key insight:** Don't predict direction. Use leverage only on high-probability setups.

## Component Map

```
┌─────────────────────────────────────────────────────────────────┐
│                        ENTRY POINTS                              │
├─────────────────────────────────────────────────────────────────┤
│  app.py (44K)         - Streamlit dashboard                      │
│  run_bot.py           - CLI runner                               │
│  src/worker.py        - Background worker for Railway            │
└─────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                      CORE COMPONENTS                             │
├─────────────────────────────────────────────────────────────────┤
│  src/smart_scheduler.py (1.3K) - APScheduler jobs, orchestration │
│  src/smart_strategy.py (1.3K)  - Signal generation logic         │
│  src/trading_bot/      (pkg)   - Trade execution, positions      │
│  src/telegram/         (pkg)   - Telegram bot, approvals         │
└─────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                    INFRASTRUCTURE                                │
├─────────────────────────────────────────────────────────────────┤
│  src/database.py       - SQLite (trades, state, logs)            │
│  src/etrade_client.py  - E*TRADE API wrapper                     │
│  src/data_providers.py - Alpaca, Finnhub, Yahoo (market data)    │
│  src/error_alerting.py - Telegram alerts for errors              │
└─────────────────────────────────────────────────────────────────┘
```

## Key Files for Common Changes

| Task | Primary File(s) |
|------|-----------------|
| Change strategy thresholds | `src/smart_strategy.py` → `StrategyConfig` |
| Modify trade execution | `src/trading_bot/execution_mixin.py` |
| Add Telegram command | `src/telegram/handlers.py` |
| Change scheduled times | `src/smart_scheduler.py` → `setup_jobs()` |
| Add database table | `src/database.py` → `_init_db()` |
| Monthly strategy review | `src/strategy_review/` package |

## Critical Patterns

### 1. Async/Sync Bridge
The codebase mixes async (Telegram) and sync (APScheduler) code. Use the utility:
```python
from src.utils import run_async
run_async(some_async_function())  # Safe from sync context
```

### 2. Database Singleton
Always use the factory, never instantiate directly:
```python
from src.database import get_database
db = get_database()  # Returns singleton
```

### 3. Telegram Approvals
Trades require user approval via Telegram (unless `skip_approval=True`):
```python
result = await bot.execute_signal(signal, skip_approval=False)  # Waits for user
result = await bot.execute_signal(signal, skip_approval=True)   # Auto-executes
```

### 4. Paper vs Live Trading
Controlled by `BotConfig.mode`:
- `TradingMode.PAPER` - Simulated trades, no real orders
- `TradingMode.LIVE` - Real E*TRADE orders

## Environment Variables

```bash
# Required
ANTHROPIC_API_KEY=        # Claude API for strategy reviews
TELEGRAM_BOT_TOKEN=       # Telegram bot
TELEGRAM_CHAT_ID=         # Your chat ID

# E*TRADE (for live trading)
ETRADE_CONSUMER_KEY=
ETRADE_CONSUMER_SECRET=
ETRADE_ACCOUNT_ID=

# Market Data
ALPACA_API_KEY=
ALPACA_SECRET_KEY=

# Optional
DATABASE_PATH=            # Default: ./trades.db
```

## Gotchas and Footguns

1. **Time zones**: All trading logic uses ET. Use `get_et_now()` from `src/utils.py`, never `datetime.now()`.

2. **Daily trade flags**: Strategies track "already traded today" to prevent duplicates. These reset at midnight ET. See `_crash_day_traded_today`, `_pump_day_traded_today` in `SmartStrategy`.

3. **E*TRADE OAuth**: Tokens expire. The client handles refresh, but initial auth requires browser flow. Check `src/etrade_client.py`.

4. **Signal priority**: When multiple signals fire, priority is configurable (`signal_priority` param). Default: 10 AM Dump blocks Mean Reversion on same day.

5. **Position reversal**: If a BITU position drops ≥2%, system flips to SBIT. This is intentional, not a bug.

6. **Database migrations**: Schema changes must be backward-compatible. Add columns with defaults, never remove.

7. **Mixin pattern**: `TradingBot`, `TelegramBot`, `StrategyReviewer` use mixins. Mixins access `self.*` defined in the main class - check the base class for available attributes.

## Testing

```bash
python -m pytest tests/ -v          # Run all tests
python -m pytest tests/ -k "crash"  # Run tests matching "crash"
```

Key test files:
- `tests/test_smart_strategy.py` - Strategy signal tests
- `tests/test_trading_bot.py` - Execution tests
- `tests/test_database.py` - Persistence tests

## Quick Debugging

```bash
# Check bot status
sqlite3 trades.db "SELECT * FROM bot_state ORDER BY updated_at DESC LIMIT 5;"

# Check recent trades
sqlite3 trades.db "SELECT * FROM trades ORDER BY entry_time DESC LIMIT 10;"

# Check event log
sqlite3 trades.db "SELECT * FROM event_log ORDER BY timestamp DESC LIMIT 20;"
```
