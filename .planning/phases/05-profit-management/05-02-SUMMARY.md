---
phase: 05-profit-management
plan: "02"
subsystem: profit-management
tags: [telegram, wheel-strategy, options, profit-management, buy-to-close, roll]
dependency_graph:
  requires: ["05-01"]
  provides: ["PM-02", "PM-03", "PM-04", "PM-05"]
  affects: ["src/telegram/bot.py", "src/smart_scheduler.py", "src/database.py"]
tech_stack:
  added: []
  patterns:
    - "Separate asyncio.Event per approval flow (BTC, roll) — same pattern as put/call approvals"
    - "Two-step roll execution with partial-failure handling and safe-state transitions"
    - "Credit-only roll validation before user dialog — no debit rolls permitted"
key_files:
  created:
    - tests/test_profit_management.py (Task 1+2 test classes appended to existing file)
  modified:
    - src/telegram/bot.py
    - src/smart_scheduler.py
    - src/database.py
decisions:
  - "send_dte_alert takes db as explicit parameter (not stored on bot) for testability"
  - "roll_blocked guards (max count, net debit) return None early before creating asyncio.Event"
  - "STO failure after BTC transitions cycle to CASH (put) or HOLDING_SHARES (call) for safety"
  - "set_roll_count DB method added instead of calling increment_roll_count N times"
metrics:
  duration_minutes: 35
  completed_date: "2026-04-08T22:51:24Z"
  tasks_completed: 2
  tasks_total: 2
  files_modified: 4
---

# Phase 5 Plan 02: Telegram Profit Management Flows Summary

**One-liner:** Telegram BTC and roll approval flows wired to monitoring job — buy-to-close at 50% profit, two-step credit-only defensive rolling with partial-failure handling, and informational 21-DTE alerts.

## What Was Built

### Task 1: BTC approval flow and DTE alert (17 tests)

**`TelegramBot.request_profit_take_approval`** — sends formatted profit-take suggestion showing option symbol, entry premium, current ask, profit %, and dollar savings from early close. Uses `btc_approve_` / `btc_reject_` inline buttons with a separate `_btc_approval_event` (no collision with put/call/intraday events).

**`TelegramBot._execute_btc_order`** — T-05-06 compliant: preview + place `BUY_CLOSE` order; on success calls `close_wheel_position` and `transition_wheel_state` (SHORT_PUT→CASH, COVERED_CALL→HOLDING_SHARES); on any API failure skips all DB mutations and sends error notification.

**`TelegramBot.send_dte_alert`** — informational message at 21 DTE with no action buttons; calls `db.mark_dte_alert_sent` after sending to prevent duplicates (PM-04).

**`SmartScheduler._job_wheel_monitoring` wiring** — DTE alert fires first (informational, non-blocking); profit-take approval blocks with `_monitoring_approval_pending=True` (cleared in `finally`); roll dispatch is `elif` branch (profit takes priority).

### Task 2: Roll approval flow with credit-only validation (15 tests)

**`TelegramBot.request_roll_approval`** — two guards before dialog:
1. `roll_count >= 2`: sends "Max rolls reached (N/2)" warning, returns `None`
2. `net credit <= 0` (new_bid - current_ask ≤ 0): sends debit explanation, returns `None`

Passes when guards clear: sends formatted message with current/new strike, DTE, delta, BTC cost, STO premium, net credit. Uses `roll_approve_` / `roll_reject_` buttons with separate `_roll_approval_event`.

**`TelegramBot._execute_roll`** — two-step execution:
- Step 1 BTC: if fails → no DB mutations, error notification, return False
- Step 1 success → `close_wheel_position(old_position, btc_price)`
- Step 2 STO: if fails → `transition_wheel_state(cycle, CASH|HOLDING_SHARES, roll_sto_failed_*)`, error notification with BTC order ID, return False
- Step 2 success → `open_wheel_position(new_contract)`, `set_roll_count(new_position_id, old_count + 1)`, audit log

**`Database.set_roll_count`** — single SQL `UPDATE` to set `roll_count` to an exact value; avoids calling `increment_roll_count` N times for roll chains.

**Scheduler roll wiring** — `elif position_tested` branch (only fires when `profit_target_hit` is False); checks `roll_count < 2` before Telegram dispatch; sends `_send_notification` when max rolls reached; logs `roll_blocked_max_rolls` event.

## Callback Routing (T-05-07)

All four new callbacks (`btc_approve_`, `btc_reject_`, `roll_approve_`, `roll_reject_`) are inserted in `_handle_callback` AFTER `call_reject_` handlers and BEFORE `apply_param_` handlers. All pass through the existing `_is_authorized()` check at the top of `_handle_callback` — no new auth surface.

## Threat Mitigations Applied

| Threat ID | Status | How |
|-----------|--------|-----|
| T-05-06 | Mitigated | `try/except` wraps preview+place in `_execute_btc_order`; DB writes only after success |
| T-05-07 | Mitigated | All callbacks route through existing `_is_authorized()` in `_handle_callback` |
| T-05-08 | Mitigated | STO failure transitions cycle to safe state with manual instructions |
| T-05-09 | Mitigated | `db.log_event` for every order attempt, result, and failure |
| T-05-10 | Mitigated | Only summary financial data sent to authorized chat_id |
| T-05-11 | Mitigated | `_monitoring_approval_pending` set True before wait, cleared in `finally` |
| T-05-12 | Mitigated | Net credit check performed before approval dialog |

## Test Results

| Class | Tests | Result |
|-------|-------|--------|
| TestBuyToClose | 9 | PASS |
| TestDTEAlert | 3 | PASS |
| TestBTCCallbackRouting | 2 | PASS |
| TestSchedulerBTCWiring | 3 | PASS |
| TestRollApproval | 4 | PASS |
| TestRollBlocking | 3 | PASS |
| TestRollExecution | 5 | PASS |
| TestRollCallbackRouting | 1 | PASS |
| TestSchedulerRollWiring | 2 | PASS |
| **Total new** | **32** | **PASS** |
| Full suite | 352 | PASS (no regressions) |

## Commits

| Hash | Description |
|------|-------------|
| `8b7ed41` | feat(05-02): implement BTC approval flow, DTE alert, and roll scaffolding |
| `36743d4` | test(05-02): add failing then passing tests for roll approval flow |

## Deviations from Plan

**1. [Rule 2 - Missing functionality] `send_dte_alert` takes `db` as explicit parameter**
- **Found during:** Task 1 implementation
- **Issue:** Plan spec showed `send_dte_alert(position, cycle)` with no `db` parameter, but the method needs to call `db.mark_dte_alert_sent`. Without `db`, the method could not fulfill its core responsibility.
- **Fix:** Added `db` as third parameter. Scheduler passes `self.db`; tests pass mock db.
- **Files modified:** `src/telegram/bot.py`, `src/smart_scheduler.py`, `tests/test_profit_management.py`

No other deviations — plan executed as specified.

## Known Stubs

None. All methods are fully wired with real logic.

## Self-Check

- [x] `src/telegram/bot.py` — `request_profit_take_approval`, `_execute_btc_order`, `send_dte_alert`, `request_roll_approval`, `_execute_roll` all present
- [x] `src/smart_scheduler.py` — all three flows wired in `_job_wheel_monitoring`
- [x] `src/database.py` — `set_roll_count` present
- [x] `tests/test_profit_management.py` — 32 new test cases, 71 total in file
- [x] Commits `8b7ed41` and `36743d4` exist on branch `wheel-strategy`
- [x] Full suite: 352 passed, 0 failed
