---
phase: 05-profit-management
plan: "01"
subsystem: profit-management
tags: [wheel-strategy, monitoring, scheduler, database, tdd]
dependency_graph:
  requires:
    - 04-02  # covered call approval flow (provides COVERED_CALL state)
    - 02-02  # wheel CRUD and cost basis (provides options_positions table)
  provides:
    - roll_count and dte_alert_sent DB columns
    - WheelStrategy monitoring methods (check_profit_target, check_position_tested, check_dte_warning, select_roll_strike, run_monitoring_checks)
    - SmartScheduler wheel_monitoring CronTrigger job
  affects:
    - 05-02  # Plan 02 wires Telegram notification flows on top of this plan's results dict
tech_stack:
  added: []
  patterns:
    - Backward-compatible ALTER TABLE migration (PRAGMA table_info guard)
    - Async coroutine method on sync strategy class (run_monitoring_checks is async for Plan 02 compatibility)
    - run_async() bridge in SmartScheduler for async coroutine dispatch
    - Priority ordering in monitoring checks (profit target overrides position tested)
key_files:
  created:
    - tests/test_profit_management.py
  modified:
    - src/database.py
    - src/wheel_strategy.py
    - src/smart_scheduler.py
    - tests/test_wheel_state.py
decisions:
  - "profit_target uses ask price (conservative, T-05-02): prevents premature buy-to-close triggers from stale/wide spreads"
  - "run_monitoring_checks is async to match Plan 02's Telegram await pattern; called via run_async() in scheduler"
  - "profit_target_hit takes priority over position_tested: if profit hit, tested=False (per RESEARCH.md Open Questions #3)"
  - "ETradeAPIError guard in run_monitoring_checks returns all-False dict (T-05-05): stale quote never triggers false action"
  - "wheel_monitoring job uses CronTrigger hour=9-15 minute=0,30 (T-05-01 DoS: early-exit guards before any API call)"
metrics:
  duration_minutes: 35
  completed_date: "2026-04-08"
  tasks_completed: 3
  tasks_total: 3
  files_modified: 5
---

# Phase 05 Plan 01: Profit Management Monitoring Foundation Summary

**One-liner:** 30-minute options monitoring job with DB migration for roll_count/dte_alert_sent, five WheelStrategy check methods (profit target via ask price, position tested, DTE warning, roll strike selection, orchestration), and SmartScheduler CronTrigger job with full guard chain.

## What Was Built

### Task 1: Database migration and helper methods (commit `06d2ced`)

Added two columns to `options_positions` via backward-compatible `PRAGMA table_info` migration:

- `roll_count INTEGER DEFAULT 0` — tracks how many times a position has been rolled
- `dte_alert_sent INTEGER DEFAULT 0` — prevents repeated 21-DTE alerts

Added three new methods to `Database`:
- `increment_roll_count(position_id)` — atomic `roll_count + 1` update
- `mark_dte_alert_sent(position_id)` — idempotent, sets to 1 regardless of current value
- `get_open_position_for_cycle(cycle_id)` — returns latest OPEN position row or None

### Task 2: WheelStrategy monitoring methods (commit `0a19cd6`)

Added `profit_target_pct = 0.50` instance variable and five new methods to `WheelStrategy`:

- `check_profit_target(position, chain)` — returns True when ask <= 50% of premium_received (uses ask price per T-05-02)
- `check_position_tested(position)` — returns True when IBIT within 2% of strike via `get_ibit_quote()`, False on API failure
- `check_dte_warning(position)` — returns True when DTE <= 21 and `dte_alert_sent == 0`
- `select_roll_strike(current_strike, option_type)` — filters further-OTM contracts with 30-45 DTE and appropriate delta range, returns highest-bid qualifying contract
- `run_monitoring_checks(cycle)` — async orchestration: fetches chain (ETradeAPIError guard per T-05-05), evaluates all three checks with profit-target priority

### Task 3: SmartScheduler 30-minute monitoring job (commit `dcd72f1`)

Added to `SmartScheduler`:
- `_monitoring_approval_pending = False` flag to prevent overlapping approval requests
- `wheel_monitoring` CronTrigger job registered in `setup_jobs()` at `hour="9-15" minute="0,30" day_of_week="mon-fri"`
- `_job_wheel_monitoring()` method with guard chain: non-trading day → no strategy → approval pending → no active cycle → wrong state (CASH/HOLDING_SHARES). Calls `run_async(wheel_strategy.run_monitoring_checks(cycle))`, logs boolean flags only (T-05-03), and catches all exceptions with Telegram alert.

Also fixed `tests/test_wheel_state.py::TestDatabaseSchema::test_options_positions_has_all_columns` to include the two new columns in the expected set (Rule 1 auto-fix).

## Test Results

- `tests/test_profit_management.py`: 39 tests — all pass
  - `TestDatabaseMigration`: 9 tests (column existence, increment, idempotency, get_open queries)
  - `TestProfitTarget`: 4 tests
  - `TestPositionTested`: 4 tests
  - `TestDTEWarning`: 4 tests
  - `TestSelectRollStrike`: 3 tests
  - `TestRunMonitoringChecks`: 5 tests
  - `TestSchedulerMonitoring`: 10 tests
- Full suite: 320 passed, 0 failures

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated schema test to include new columns**
- **Found during:** Task 3 full-suite run
- **Issue:** `tests/test_wheel_state.py::TestDatabaseSchema::test_options_positions_has_all_columns` asserted a fixed set of 20 columns; failed after migration added `roll_count` and `dte_alert_sent`
- **Fix:** Added both columns to the expected set with a Phase 5 comment
- **Files modified:** `tests/test_wheel_state.py`
- **Commit:** `dcd72f1`

## Threat Surface Scan

All changes covered by the plan's `<threat_model>`:

| Threat | Disposition | Applied |
|--------|-------------|---------|
| T-05-01 | mitigate | Guard chain in `_job_wheel_monitoring` skips API before any state check |
| T-05-02 | mitigate | `check_profit_target` uses `ask` (not bid) for conservative cost check |
| T-05-03 | mitigate | Event log records only boolean flags — no premium amounts or raw chain |
| T-05-04 | accept | ALTER TABLE ADD COLUMN is non-destructive; existing rows default to 0 |
| T-05-05 | mitigate | `run_monitoring_checks` wraps `get_ibit_options_chain` in `except ETradeAPIError` |

No new unplanned trust boundaries introduced.

## Known Stubs

None. The monitoring computation layer is fully wired. Telegram notification flows for `profit_target_hit`, `position_tested`, and `dte_warning` are intentionally deferred to Plan 02 (documented in `_job_wheel_monitoring` comments).

## Self-Check

Checking committed files and artifacts...

| Item | Status |
|------|--------|
| src/database.py | FOUND |
| src/wheel_strategy.py | FOUND |
| src/smart_scheduler.py | FOUND |
| tests/test_profit_management.py | FOUND |
| commit 06d2ced | FOUND |
| commit 0a19cd6 | FOUND |
| commit dcd72f1 | FOUND |

## Self-Check: PASSED
