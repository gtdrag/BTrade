---
phase: "04"
plan: "02"
subsystem: telegram-call-approval
tags: [telegram, covered-call, wheel-strategy, options, approval-flow, scheduler]
dependency_graph:
  requires: ["04-01"]
  provides: ["covered-call-telegram-approval", "call-approval-scheduler-wiring"]
  affects: ["src/telegram/bot.py", "src/smart_scheduler.py"]
tech_stack:
  added: []
  patterns:
    - "Separate asyncio.Event per approval type (call vs put vs intraday) to prevent collision"
    - "Stale signal guard: re-validate strike >= cost_basis by reading fresh DB cycle before placing order"
    - "Hard cost-basis filter in adjust flow: only present strikes >= cost_basis as alternatives"
    - "Annualized return calculation on call-away: (pnl / capital) * (365 / days) * 100"
key_files:
  created:
    - tests/test_call_approval.py
  modified:
    - src/telegram/bot.py
    - src/smart_scheduler.py
decisions:
  - "call_* callbacks inserted AFTER put_reject_ and BEFORE apply_param_ to prevent call_reject_ from falling through to generic reject_ handler"
  - "Stale signal guard reads get_active_cycle() fresh at execution time, not at signal generation time"
  - "Hard cost-basis filter in _handle_call_adjust removes below-basis strikes entirely, not just warns"
  - "call_expired_otm handled in _job_assignment_detection alongside assigned/expired_otm/called_away"
metrics:
  duration: "~25 minutes"
  completed_date: "2026-04-08"
  tasks_completed: 2
  files_changed: 3
  tests_added: 20
  total_tests: 281
---

# Phase 04 Plan 02: Covered Call Telegram Approval Flow Summary

**One-liner:** Telegram covered call approval flow with cost-basis-protected adjust, stale-signal guard, and scheduler wiring for assignment and OTM-expiry call suggestions.

## What Was Built

### TelegramBot additions (src/telegram/bot.py)

**State isolation:** Added `_call_approval_event`, `_call_approval_result`, `_call_approval_signal`, `_call_approval_chain` as separate instance variables — preventing any collision with the existing put approval (`_put_*`) and intraday (`_approval_*`) state.

**Callback dispatch:** Inserted `call_approve_`, `call_adjust_`, `call_alt_reject_`, `call_alt_`, `call_reject_` handlers in `_handle_callback` in the correct position: after `put_reject_` and before `apply_param_`. This ensures `call_reject_` cannot fall through to the generic `reject_` handler that sets `_approval_result` (intraday).

**`request_call_approval(signal, chain, client, db, account_id_key)`:** Sends a Telegram message with the call signal details (symbol, strike, expiry, delta, premium, total_premium, cost_basis, IV) and 3 inline buttons (Approve, Adjust, Reject). Waits on `_call_approval_event` with timeout. On approve, calls `_execute_call_order`. On adjust with alternative strike, reconstructs a `CallSignal` from the chain entry and executes.

**`_execute_call_order(signal, client, db, account_id_key)`:** 
- Stale signal guard (T-04-08): reads `db.get_active_cycle()` fresh, rejects if `signal.strike < cycle["cost_basis"]`
- Places `SELL_OPEN` CALL order via `preview_options_order` + `place_options_order`
- Calls `db.transition_wheel_state(COVERED_CALL, "call_sold", cost_basis=old-premium, covered_call_premiums_collected=accumulated)`
- Calls `db.open_wheel_position` with option_type="CALL"
- Logs `call_order_placed` event (T-04-11 audit trail)

**`_handle_call_adjust(query, data)`:** Filters chain to CALL contracts with delta in [0.20, 0.45], then applies hard cost-basis filter (T-04-10): removes any strike below `signal.cost_basis`. If nothing remains, shows warning. Otherwise presents up to 5 alternatives as inline buttons with strike/delta/bid/DTE info.

### SmartScheduler additions (src/smart_scheduler.py)

**`result == "assigned"` branch:** Replaced Phase 4 placeholder with real call suggestion logic. Calls `get_call_signal()`. If None: sends "no profitable call strikes" notification (T-04-12: graceful handling). If signal: sends assignment notification + triggers `request_call_approval` via `run_async`.

**`result == "called_away"` branch (new):** Reads last cycle from `get_cycle_history`. Calculates `days_in_cycle` from `opened_at`/`closed_at` ISO timestamps. Computes annualized return: `(pnl / capital_at_risk) * (365 / days) * 100`. Sends full-cycle summary to Telegram.

**`result == "call_expired_otm"` branch (new):** OTM call expiry loop-back. Reads active cycle cost_basis, calls `get_call_signal()`, and either warns (None) or triggers `request_call_approval` for a new covered call.

## Tests

20 new tests in `tests/test_call_approval.py` across 5 test classes:
- `TestCallApprovalFlow`: approve/reject/timeout/event isolation (4 tests)
- `TestExecuteCallOrder`: sell-to-open CALL, DB transitions, stale signal guard (4 tests)
- `TestCallAdjust`: alternatives filtered above cost basis, warning when none (2 tests)
- `TestCallbackRouting`: call_* prefix dispatch, isolation from intraday result (6 tests)
- `TestSchedulerCallSuggestion`: assignment wiring, OTM expiry wiring, no-signal warning, called_away summary (4 tests)

Full suite: 281 tests, 0 failures.

## Deviations from Plan

None — plan executed exactly as written.

## Threat Mitigations Implemented

| ID | Mitigation | Location |
|----|------------|----------|
| T-04-07 | `_is_authorized()` at top of `_handle_callback` covers all `call_*` callbacks | bot.py:_handle_callback |
| T-04-08 | Re-read `get_active_cycle()` and verify `strike >= cost_basis` before order | bot.py:_execute_call_order |
| T-04-10 | Hard filter in adjust: `float(c["strike"]) >= cost_basis` — no below-basis strikes | bot.py:_handle_call_adjust |
| T-04-11 | `db.log_event("call_order_placed", ...)` with strike, delta, premium, cycle_id | bot.py:_execute_call_order |
| T-04-12 | `get_call_signal()` returning None handled with notification, no exception | smart_scheduler.py |
| T-04-13 | Financial details sent only to authorized chat_id via existing `_is_authorized()` | bot.py |
| T-04-14 | Assignment notification sent first, then call suggestion attempted | smart_scheduler.py |

## Known Stubs

None — all data flows are wired. The approval loop is complete: scheduler detects assignment → sends Telegram message with real call signal data → user approves/adjusts/rejects → order placed via real E*TRADE client.

## Self-Check: PASSED

| Item | Result |
|------|--------|
| tests/test_call_approval.py exists | FOUND |
| src/telegram/bot.py exists | FOUND |
| src/smart_scheduler.py exists | FOUND |
| 04-02-SUMMARY.md exists | FOUND |
| Commit 70fea1f (failing tests) | FOUND |
| Commit 6caac8d (implementation) | FOUND |
| 20 call approval tests pass | PASSED |
| Full suite 281 tests, 0 failures | PASSED |
