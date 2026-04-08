---
phase: 03-cash-secured-put-cycle
plan: "02"
subsystem: telegram-approval
tags: [telegram, options, wheel-strategy, scheduler, put-approval]
dependency_graph:
  requires: [03-01]
  provides: [put-approval-flow, put-signal-scheduler-job]
  affects: [src/telegram/bot.py, src/smart_scheduler.py, tests/test_put_approval.py]
tech_stack:
  added: []
  patterns: [async-event-wait, sync-async-bridge-run_async, inline-keyboard-approval]
key_files:
  created:
    - tests/test_put_approval.py
  modified:
    - src/telegram/bot.py
    - src/smart_scheduler.py
decisions:
  - Use bid price as limit price for sell-to-open (conservative; avoids needing ask in PutSignal)
  - Separate _put_approval_event from _approval_event (no collision with intraday approvals)
  - put_alt_reject_ prefix checked before put_alt_ to prevent partial match ambiguity
  - WheelStrategy initialized in SmartScheduler.__init__ only when bot.client is available
metrics:
  duration_minutes: 25
  completed_date: "2026-04-08"
  tasks_completed: 2
  tasks_total: 2
  files_modified: 3
---

# Phase 03 Plan 02: Telegram Put Approval Flow and Scheduler Job Summary

**One-liner:** Telegram 3-button put approval flow (Approve/Adjust/Reject with alternative strikes) wired to SmartScheduler job running at 10:00 AM ET via CronTrigger.

## What Was Built

### Task 1: TelegramBot put approval flow (`src/telegram/bot.py`)

Added four new instance variables in `__init__` (separate namespace from intraday approvals):
- `_put_approval_event` — `asyncio.Event` created fresh per approval request
- `_put_approval_result` — `str | None`: `"approved"`, `"rejected"`, or a numeric strike string from adjust flow
- `_put_approval_signal` — `PutSignal` stored for callback handlers
- `_put_approval_chain` — `List[Dict]` options chain stored for adjust alternatives

Added three async methods:
- **`request_put_approval(signal, chain, client, db, account_id_key)`** — sends a formatted Telegram message with put details (strike, expiry, delta, premium, max risk, DTE, IV, pullback %) and three inline buttons. Waits on `_put_approval_event` with configurable timeout. On approval (direct or alternative strike), calls `_execute_put_order()`. Returns `ApprovalResult`.
- **`_execute_put_order(signal, client, db, account_id_key)`** — previews order via `preview_options_order()`, places via `place_options_order()`, then on success: `create_wheel_cycle()` → `transition_wheel_state(SHORT_PUT)` → `open_wheel_position()`. Sends confirmation message. No DB writes on failure (T-03-08).
- **`_handle_put_adjust(query, data)`** — filters chain to puts with abs(delta) in [0.15, 0.40], finds 2 below + 2 above the suggested strike, renders as inline buttons. Adds "Reject All" as final button.

Added callback routing in `_handle_callback()` for five new prefixes (checked before intraday `approve_`/`reject_`):
- `put_approve_` → set result `"approved"`, fire event
- `put_adjust_` → delegate to `_handle_put_adjust()`
- `put_reject_` → set result `"rejected"`, fire event
- `put_alt_reject_` → set result `"rejected"`, fire event
- `put_alt_` → extract strike from callback_data, set as string result, fire event

### Task 2: SmartScheduler put signal job (`src/smart_scheduler.py`)

- Added `from .wheel_strategy import WheelStrategy, PutSignal` import
- Added `self.wheel_strategy` initialization in `__init__` (only when `bot.client` is available)
- Added `_job_put_signal_check()` method with guards: non-trading-day, no wheel_strategy, no signal, no telegram_bot
- Registered `"put_signal_check"` job at `CronTrigger(day_of_week="mon-fri", hour=10, minute=0)` with 300s misfire grace time
- Bridges sync→async via `run_async(telegram_bot.request_put_approval(...))`
- Full audit trail via `db.log_event()` for `put_signal_fired`, `put_approval_result`, `put_signal_check_error`

### Task 2: Tests (`tests/test_put_approval.py`)

18 tests across 4 classes:
- **TestPutOrderExecution** (3): preview+place called, cycle created + transitioned to SHORT_PUT, no DB write on API failure
- **TestPutApprovalFlow** (4): approved path calls execute, rejected path does not, timeout returns TIMEOUT, put event is separate from intraday event
- **TestPutCallbackRouting** (5): approve/reject/adjust/alt_reject/alt_strike routing verified
- **TestSchedulerPutJob** (6): non-trading-day skip, no-wheel-strategy skip, no-signal log, signal fires calls run_async, no-telegram skip, exception logged + notification sent

## Deviations from Plan

### Auto-fixed Issues

None — plan executed exactly as written.

**Minor implementation notes (within spec):**
- `_handle_put_adjust()` signature takes `data: str` (not just `query`) to extract the `callback_id` from the `put_adjust_` prefix, needed to build `put_alt_*` callback data that routes back correctly.
- Test assertion for `db.log_event` call_args used `c[0][0], c[0][1]` tuple extraction (3-arg calls) rather than 2-element tuple as initially drafted — corrected inline before commit.

## Known Stubs

None. All data flows are wired: `WheelStrategy.get_put_signal()` → `request_put_approval()` → `_execute_put_order()` → E*TRADE API + DB.

## Threat Surface Scan

All threat mitigations from the plan's threat model were applied:

| Threat | Applied |
|--------|---------|
| T-03-05 Elevation of Privilege | `_is_authorized()` at top of `_handle_callback()` covers all new `put_*` branches |
| T-03-06 Tampering (callback injection) | Only known prefixes handled; unknown fall through to existing handlers |
| T-03-07 Double execution | `create_wheel_cycle()` raises `ValueError` if active cycle exists |
| T-03-08 DoS (API failure) | `try/except` wraps preview/place; DB not written on failure |
| T-03-09 Info disclosure | Only `strike/delta/premium/dte/cycle_id/order_id` logged — no raw API response |
| T-03-10 Repudiation | `db.log_event()` records signal fired, approval result, and order placement |

No new threat surface introduced beyond what is in the plan's threat model.

## Self-Check

Files exist:
- `src/telegram/bot.py` — modified
- `src/smart_scheduler.py` — modified
- `tests/test_put_approval.py` — created

Commits exist:
- `a6334ee` feat(03-02): add put approval flow to TelegramBot
- `8f5d2ea` feat(03-02): add put signal job to SmartScheduler and tests

Test results: 234 passed, 0 failed (full suite including 18 new tests).

## Self-Check: PASSED
