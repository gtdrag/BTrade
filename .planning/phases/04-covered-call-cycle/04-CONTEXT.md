# Phase 4: Covered Call Cycle - Context

**Gathered:** 2026-04-08
**Status:** Ready for planning

<domain>
## Phase Boundary

Complete the full wheel cycle by suggesting covered calls after put assignment, detecting call-away events, and handling OTM call expiry. Extends Phase 3's WheelStrategy and assignment detection. No profit management (Phase 5), no UI transition (Phase 6).

</domain>

<decisions>
## Implementation Decisions

### Covered Call Timing & Trigger
- Suggest covered calls immediately in the assignment notification — extend the Phase 3 assignment message with concrete call parameters. No delay; user decides when to approve.
- Filter chain to 0.25-0.35 delta, pick strike at or above adjusted cost basis with highest premium. Reuse `get_ibit_options_chain()`, similar pattern to `select_put_strike()`.
- Only suggest calls for shares acquired through the wheel cycle — manual positions are out of scope.
- Same 30-45 DTE as puts (consistent). Already filtered by `get_ibit_options_chain()`.

### Telegram Approval & Cost Basis Protection
- Reuse same 3-button pattern (Approve/Adjust/Reject) for consistency. Show: symbol, strike, expiration, delta, premium, cost basis, DTE. Same `_handle_call_adjust()` pattern.
- Hard block — don't show strikes below adjusted cost basis. Filter them out before presenting. If no strikes above cost basis, warn "no profitable strikes available" and suggest waiting.
- On call-away: auto-transition cycle to CASH, record full-cycle P&L, send Telegram summary. P&L = total premiums (put + call) - any losses. Close all positions in DB.
- Cycle history stored in DB (Phase 2 already supports this). Phase 6 adds Telegram and Streamlit display. No new UI in Phase 4.

### Call-Away Detection & Cycle Completion
- Same reconciliation pattern as assignment detection — poll E*TRADE positions. If covered call disappears AND IBIT shares disappear, shares were called away.
- Extend existing `detect_and_process_expiry()` to handle COVERED_CALL state. No new scheduler job needed — same 8:30 AM ET job.
- On OTM call expiry (shares kept): close call position, keep cycle in HOLDING_SHARES, suggest new covered call. Premium from expired call is pure profit.
- Full-cycle P&L: sum all premiums (put + call(s)). Report in Telegram notification with entry date, total premiums, days in cycle, annualized return. Use `compute_cycle_pnl()` from Phase 2.

### Claude's Discretion
- Internal method names for call suggestion and execution
- Telegram message formatting for covered call details
- How to handle edge case where shares are partially called away
- Error handling for E*TRADE API failures during call-away check

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `WheelStrategy` class in `src/wheel_strategy.py` — extend with `get_call_signal()` and `select_call_strike()`
- `WheelStrategy.detect_and_process_expiry()` — extend to handle COVERED_CALL state
- `TelegramBot.request_put_approval()` — pattern for `request_call_approval()`
- `TelegramBot._handle_put_adjust()` — pattern for `_handle_call_adjust()`
- `SmartScheduler._job_assignment_detection()` — extend to suggest calls after assignment
- `Database.transition_wheel_state()` — already supports HOLDING_SHARES → COVERED_CALL
- `Database.open_wheel_position()` / `close_wheel_position()` — reuse for call positions
- `Database.compute_cycle_pnl()` — full P&L calculation

### Established Patterns
- Delta filtering with `abs()` for sign handling
- Separate `_call_approval_event` / `_call_approval_result` for event isolation
- TDD approach: tests first, then implementation
- `get_et_now()` for all timestamps
- `run_async()` bridge for sync→async in scheduler

### Integration Points
- `WheelStrategy` gets new methods for call signal generation
- `TelegramBot` gets `request_call_approval()` and related handlers
- `SmartScheduler._job_assignment_detection()` extended to trigger call suggestion
- `detect_and_process_expiry()` handles both SHORT_PUT and COVERED_CALL states

</code_context>

<specifics>
## Specific Ideas

- Follow Phase 3's patterns exactly — same button layout, same approval flow, same event isolation
- Cost basis protection is a hard filter, not a warning
- OTM call expiry loops back to suggesting another call (doesn't restart the whole cycle)
- Full-cycle summary should include annualized return calculation

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 04-covered-call-cycle*
*Context gathered: 2026-04-08 via smart discuss*
