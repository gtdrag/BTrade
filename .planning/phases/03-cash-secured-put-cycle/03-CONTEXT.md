# Phase 3: Cash-Secured Put Cycle - Context

**Gathered:** 2026-04-08
**Status:** Ready for planning

<domain>
## Phase Boundary

Sell signal-based cash-secured puts on IBIT and detect option assignments. New WheelStrategy class generates put entry signals. Telegram approval flow for options trades with approve/adjust/reject. Assignment detection via E*TRADE positions polling. No covered calls (Phase 4), no profit management (Phase 5), no UI transition (Phase 6).

</domain>

<decisions>
## Implementation Decisions

### Signal Integration & Put Entry Logic
- New `WheelStrategy` class separate from `SmartStrategy` — own `get_put_signal()` method. SmartStrategy handles intraday equity; WheelStrategy handles options. Clean separation per mixin pattern.
- IBIT pullback signal defined as: IBIT drops >=2% from recent high (5-day). Uses AlpacaProvider/MarketDataManager already built.
- Bot checks for put entry signals once daily at 10:00 AM ET — after morning volatility settles. New APScheduler job in SmartScheduler.
- Put signals do NOT fire when active wheel cycle is in SHORT_PUT or HOLDING_SHARES state. Only signal when cycle is CASH or no cycle exists. Prevents stacking.

### Strike Selection & Telegram Presentation
- Filter chain contracts to 0.20-0.30 delta range, pick highest premium within range. Uses Phase 1's `get_ibit_options_chain()` which returns delta.
- Single Telegram message with key details + 3 inline buttons (Approve / Adjust / Reject). Shows: symbol, strike, expiration, delta, premium, max risk (strike x 100), DTE.
- "Adjust" shows 3-5 nearby alternatives (+-2 strikes from suggested), each with delta/premium/DTE. User taps one to approve, or rejects all. Single round of adjustment.
- Cash collateral validated before sending suggestion: paper/live capital >= strike x 100. If insufficient, don't suggest — log reason.

### Assignment Detection & Notification
- Poll E*TRADE positions API via new scheduled job. Compare portfolio positions against open put records in `options_positions` table. If shares appear and put disappears, assignment occurred.
- Assignment detection runs at 8:30 AM ET on weekdays. Assignments processed overnight; check early before market opens.
- On assignment: auto-transition cycle to HOLDING_SHARES, record in DB, send Telegram notification showing shares acquired (100), cost basis (strike - premium), original put details.
- On OTM expiry (no assignment): auto-close position and cycle, record full premium as profit. Transition cycle back to CASH, close position in DB. Telegram notification with P&L.

### Claude's Discretion
- Internal structure of WheelStrategy class
- How to integrate WheelStrategy with SmartScheduler job registration
- Telegram message formatting details
- Error handling for E*TRADE API failures during assignment check
- How to determine if put expired (check DTE/expiration date vs current date)

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `ETradeClient.get_ibit_options_chain()` — returns contracts with delta, gamma, theta, vega, IV, bid/ask/last, strike, expiration
- `ETradeClient.preview_options_order()` / `place_options_order()` — two-step options order flow
- `ETradeClient.get_options_positions()` — query current options positions from portfolio
- `Database.create_wheel_cycle()` / `get_active_wheel_cycle()` — wheel cycle CRUD
- `Database.transition_wheel_state()` — state machine transitions with validation
- `Database.open_wheel_position()` / `close_wheel_position()` — options position recording
- `WheelState` enum (CASH, SHORT_PUT, HOLDING_SHARES, COVERED_CALL) in `src/wheel_state.py`
- `WheelCycle` dataclass with cost basis tracking
- `SmartScheduler.setup_jobs()` — APScheduler job registration pattern
- `TelegramBot.request_trade_approval()` — existing approval flow with InlineKeyboardButtons
- `AlpacaProvider` / `MarketDataManager` — market data for signal generation

### Established Patterns
- Mixin-based class composition (ExecutionMixin, PositionsMixin, etc.)
- APScheduler jobs with `CronTrigger` in SmartScheduler
- Telegram InlineKeyboardButton for approve/reject with callback_data prefixes
- `get_et_now()` for all timestamps
- Async/sync bridge via `run_async()` utility
- Paper mode via `BotConfig.mode` toggle

### Integration Points
- New `WheelStrategy` class in `src/wheel_strategy.py`
- New APScheduler jobs in `SmartScheduler.setup_jobs()` for put signal check and assignment detection
- New Telegram handler for options approval callbacks
- Calls Phase 2's database methods for cycle/position CRUD
- Calls Phase 1's E*TRADE client methods for chain fetch and order placement

</code_context>

<specifics>
## Specific Ideas

- Follow existing `execute_signal()` → `request_trade_approval()` pattern but adapted for options
- Use `get_et_now()` for all timestamps per CLAUDE.md rules
- Reuse existing `_request()` method in ETradeClient for all API calls
- Assignment detection should be idempotent (running twice doesn't double-record)

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 03-cash-secured-put-cycle*
*Context gathered: 2026-04-08 via smart discuss*
