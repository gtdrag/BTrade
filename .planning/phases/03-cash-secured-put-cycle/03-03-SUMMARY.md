---
phase: 03-cash-secured-put-cycle
plan: "03"
subsystem: wheel-strategy
tags: [tdd, wheel-strategy, assignment-detection, options, scheduler, expiry]
dependency_graph:
  requires:
    - "03-01: WheelStrategy, PutSignal, WheelState, create_wheel_cycle"
    - "03-02: put approval flow, SHORT_PUT cycle records in DB with put_strike/put_premium_received/put_expiry_date"
    - "02-02: transition_wheel_state, close_wheel_position, get_cycle_positions, get_active_cycle, get_cycle_history"
    - "01-01: MockETradeClient.get_options_positions, get_account_positions"
  provides:
    - "WheelStrategy.detect_and_process_expiry() — assignment vs OTM expiry detection"
    - "SmartScheduler._job_assignment_detection() — 8:30 AM ET scheduler job"
    - "tests/test_assignment.py — 12 tests covering all detection paths"
  affects:
    - src/wheel_strategy.py
    - src/smart_scheduler.py
    - tests/test_assignment.py
tech_stack:
  added: []
  patterns:
    - tdd-red-green-no-refactor
    - state-machine-idempotency-guard
    - api-error-without-state-mutation
    - dual-condition-assignment-check
key_files:
  created:
    - tests/test_assignment.py
  modified:
    - src/wheel_strategy.py
    - src/smart_scheduler.py
decisions:
  - "Dual condition for assignment: put gone from options positions AND IBIT shares in equity (T-03-11 — neither alone is sufficient)"
  - "State guard first: return None unless cycle.state == SHORT_PUT (T-03-12 idempotency)"
  - "API errors do not mutate state: catch exceptions, log, return None to retry next day (T-03-13)"
  - "Audit trail via db.log_event for every outcome: put_assigned, put_expired_otm (T-03-14)"
  - "OTM: realized_pnl = premium_received * 100 (1 contract = 100 shares)"
  - "Assignment: cost_basis = put_strike - put_premium_received"
  - "Assignment notification includes covered call suggestion (ASGN-03 requirement)"
metrics:
  duration_minutes: 30
  completed_date: "2026-04-08"
  tasks_completed: 2
  tasks_total: 2
  files_modified: 3
---

# Phase 03 Plan 03: Assignment Detection Summary

**One-liner:** WheelStrategy.detect_and_process_expiry() reconciles live E*TRADE positions against DB to detect put assignment (IBIT shares appear) or OTM expiry (no shares), transitions state machine accordingly, and sends Telegram notification with covered call hint.

## What Was Built

### Task 1: TDD RED — test scaffold (`tests/test_assignment.py`)

12 failing tests across 3 classes, all failing with `AttributeError` before implementation:

**TestAssignmentDetection** (6 tests):
- `test_detects_assignment_when_shares_appear` — put gone + IBIT equity shares → "assigned"
- `test_detects_otm_expiry_when_no_shares` — put gone + no IBIT shares → "expired_otm"
- `test_skips_when_not_expired` — today <= expiry_date → None
- `test_skips_when_no_active_cycle` — empty DB → None
- `test_skips_when_cycle_not_short_put` — CASH state cycle → None
- `test_idempotent_already_holding_shares` — already HOLDING_SHARES → None

**TestAssignmentTransition** (3 tests):
- `test_assignment_transitions_to_holding_shares` — cycle state becomes HOLDING_SHARES
- `test_assignment_cost_basis` — cost_basis == 50.0 - 2.50 == 47.50
- `test_assignment_closes_position` — position status becomes CLOSED

**TestOTMExpiry** (3 tests):
- `test_otm_transitions_to_cash` — cycle state becomes CASH with closed_at set
- `test_otm_records_pnl` — realized_pnl == 2.50 * 100 == 250.0
- `test_otm_closes_position_zero_premium` — position close_premium == 0.0 and CLOSED

Each test uses an isolated `tmp_path`-based database (`Database(db_path=tmp_path / "test.db")`) to prevent the module-level `DEFAULT_DB_PATH` singleton from leaking between tests.

### Task 2: TDD GREEN — implementation

**`detect_and_process_expiry()` in `src/wheel_strategy.py`:**

Seven-step detection algorithm:
1. Get active cycle — return None if absent
2. State gate — return None if not SHORT_PUT (idempotency, T-03-12)
3. Get open positions — return None if no OPEN positions found
4. Expiry gate — return None if today <= expiry_date
5. Live options check (T-03-13: wrapped in try/except) — return None if put still in E*TRADE positions (settlement not complete)
6. Equity check (T-03-13) — look for IBIT shares with `securityType == "EQ"` in account positions
7. Branch on result:
   - **Assignment path**: `transition_wheel_state(HOLDING_SHARES)` with `cost_basis = strike - premium`, `close_wheel_position(0.0)`, `log_event("put_assigned")`, return `"assigned"`
   - **OTM expiry path**: `transition_wheel_state(CASH)` with `realized_pnl = premium * 100`, `close_wheel_position(0.0)`, `log_event("put_expired_otm")`, return `"expired_otm"`

**`_job_assignment_detection()` in `src/smart_scheduler.py`:**
- Trading day guard via `is_trading_day()`
- `wheel_strategy` initialization guard
- Delegates to `detect_and_process_expiry()`
- On `"assigned"`: reads cost_basis from active cycle, sends Telegram notification mentioning covered call as next step (ASGN-03)
- On `"expired_otm"`: reads realized_pnl from cycle history, sends Telegram notification with P&L
- Exception handler: logs + notifies without masking the error
- Registered in `setup_jobs()` as `CronTrigger(day_of_week="mon-fri", hour=8, minute=30)` with 600s misfire grace

## Deviations from Plan

None — plan executed exactly as written.

**Implementation notes (within spec):**
- Tests use `Database(db_path=tmp_path / "test.db")` directly instead of env var, because `DEFAULT_DB_PATH` is a module-level constant cached at import time — env var approach would share the same DB across tests.
- `get_account_positions` on `MockETradeClient` returns `{symbolDescription, quantity, ...}` format (no `Product` dict). Tests mock `get_account_positions` via `patch.object` to return the `Product`-keyed format that the real E*TRADE API returns and that `detect_and_process_expiry()` reads. This is correct and safe: the implementation reads the format returned by the real client.

## Known Stubs

None. All data flows are wired: live E*TRADE positions → detection logic → DB state transition → Telegram notification.

## Threat Surface Scan

All threat mitigations from the plan's threat model were applied:

| Threat | Applied |
|--------|---------|
| T-03-11 Tampering (false assignment) | Require BOTH: put gone from options AND IBIT shares in equity |
| T-03-12 Tampering (double-transition) | State gate: skip unless cycle.state == SHORT_PUT |
| T-03-13 DoS (API failure) | Both API calls wrapped in try/except; no state mutation on failure |
| T-03-14 Repudiation | db.log_event() records "put_assigned" and "put_expired_otm" with cycle_id and financial details |
| T-03-15 Info disclosure | Cost basis sent only to authorized Telegram user (existing _is_authorized() guard) |

No new threat surface introduced beyond the plan's threat model.

## Self-Check

Files exist:
- `src/wheel_strategy.py` — modified (detect_and_process_expiry added)
- `src/smart_scheduler.py` — modified (_job_assignment_detection added, job registered)
- `tests/test_assignment.py` — created (341 lines, 12 tests)

Commits exist:
- `5ece741` test(03-03): add failing tests for assignment detection, OTM expiry, idempotency
- `228a68f` feat(03-03): implement detect_and_process_expiry and wire assignment detection scheduler job

Test results: 246 passed, 0 failed (full suite including 12 new tests).

## Self-Check: PASSED
