# Phase 5: Profit Management - Context

**Gathered:** 2026-04-08
**Status:** Ready for planning

<domain>
## Phase Boundary

Optimize wheel returns through 50% profit-taking, defensive rolling, and expiration monitoring. New scheduler job for 30-min P&L monitoring. Telegram notifications for profit targets, roll suggestions, and DTE warnings. No UI transition (Phase 6).

</domain>

<decisions>
## Implementation Decisions

### Profit-Taking Mechanics
- New scheduler job every 30 min during market hours (9:30-4:00 ET) to check option P&L. If option dropped to 50% of entry premium, trigger buy-to-close suggestion.
- Buy-to-close suggestion via Telegram message with current P&L + Approve/Reject buttons. Show: option symbol, entry premium, current ask, profit %, savings from closing early.
- After buy-to-close: if put (SHORT_PUT), transition to CASH. If covered call (COVERED_CALL), transition to HOLDING_SHARES. Premium profit recorded.
- `profit_target_pct` configurable on WheelStrategy, default 0.50. No Telegram config for v1.

### Defensive Rolling
- "Position tested" = IBIT price within 2% of strike. Check during same 30-min monitoring job.
- Roll suggestion via Telegram: current position details + suggested new strike/expiry + Approve/Reject. Show: current strike, DTE, new strike (further OTM), new expiry (30-45 DTE), net credit/debit.
- Execute roll as two-step: buy-to-close current + sell-to-open new. Sequential execution. Record both legs.
- Track `roll_count` on options_positions record. Block roll suggestions when count >= 2. Warn "max rolls reached" at limit.
- Net-debit roll prevention: track cumulative roll cost. Block rolls where debit exceeds remaining premium.

### Expiration Monitoring
- DTE warning once at 21 DTE via Telegram notification. Informational only, no action buttons. Flag to prevent duplicate alerts.
- Alert shows: option type, strike, expiry, DTE, current P&L %. Suggest "consider closing for profit" if profitable, "prepare for assignment/expiry" if not.
- No action at expiration — Phase 3's `detect_and_process_expiry()` handles outcomes.
- Runs as part of the 30-min monitoring job.

### Claude's Discretion
- Internal method structure for monitoring job
- How to track DTE alert sent flag (DB column or in-memory)
- How to calculate current option price (bid vs mark vs last)
- Roll strike selection algorithm details

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `WheelStrategy` class — extend with monitoring methods
- `ETradeClient.get_ibit_options_chain()` — fetch current prices for P&L calc
- `ETradeClient.preview_options_order()` / `place_options_order()` — buy-to-close and roll orders
- `Database.get_active_cycle()` / `get_cycle_positions()` — query active positions
- `TelegramBot` — existing approval pattern for profit-taking and roll suggestions
- `SmartScheduler` — add new 30-min monitoring job

### Established Patterns
- APScheduler `IntervalTrigger` for recurring jobs (every 30 min)
- Telegram approval flow with Approve/Reject buttons
- `get_et_now()` for timestamps
- `run_async()` bridge for sync→async

### Integration Points
- New monitoring job in SmartScheduler
- New methods on WheelStrategy for P&L calculation and roll logic
- New Telegram handlers for profit-taking and roll approval
- Database: add `roll_count` column to options_positions, add `dte_alert_sent` flag

</code_context>

<specifics>
## Specific Ideas

- Monitoring job should be efficient — only query E*TRADE when there's an active position
- Roll is two separate orders, not a spread
- DTE alert is one-time per position (flag prevents repeats)
- 2-roll limit is per position, not per cycle

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 05-profit-management*
*Context gathered: 2026-04-08 via smart discuss*
