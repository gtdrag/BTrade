---
phase: 06-transition-user-interface
plan: "01"
subsystem: database-scheduler
tags: [wheel-mode, gating, migration, tdd, tr-01]
dependency_graph:
  requires: []
  provides: [wheel_mode_enabled-column, intraday-job-gating]
  affects: [src/smart_scheduler.py, src/database.py]
tech_stack:
  added: []
  patterns: [guard-clause, ALTER-TABLE-migration, safe-default]
key_files:
  created:
    - tests/test_wheel_ui.py
  modified:
    - src/database.py
    - src/smart_scheduler.py
decisions:
  - "Safe default wheel_mode_enabled=1: missing value defaults to wheel mode ON so existing DBs disable intraday on upgrade without explicit config"
  - "Guard placed after is_trading_day check but before any market data fetch or signal logic, minimizing unnecessary DB reads on non-trading days"
  - "Five separate guards (one per method) rather than a shared helper, keeping each method self-contained and independently testable"
metrics:
  duration_minutes: 12
  completed_date: "2026-04-09"
  tasks_completed: 1
  tasks_total: 1
  files_changed: 3
---

# Phase 06 Plan 01: Wheel Mode DB Column and Intraday Job Gating Summary

**One-liner:** Added `wheel_mode_enabled INTEGER DEFAULT 1` column to `bot_state` with backward-compatible ALTER TABLE migration, and gated all 5 intraday scheduler jobs behind a guard clause so BITU/SBIT strategies do not fire when wheel mode is active.

## What Was Built

### Database migration (`src/database.py`)

- Added `wheel_mode_enabled INTEGER DEFAULT 1` to the `CREATE TABLE IF NOT EXISTS bot_state` DDL.
- Added an ALTER TABLE migration block immediately after the existing `trading_mode` migration: if `wheel_mode_enabled` is not in the columns list, `ALTER TABLE bot_state ADD COLUMN wheel_mode_enabled INTEGER DEFAULT 1` is executed. Existing databases gain the column on next startup, defaulting to `1` (wheel mode on).

### Intraday job gating (`src/smart_scheduler.py`)

Added a wheel mode guard to each of the 5 affected methods. The guard is placed after the `is_trading_day` check and before any market data fetch or signal call. Pattern used:

```python
# TR-01: skip intraday when wheel mode is active
wheel_mode = self.db.get_bot_state().get("wheel_mode_enabled", 1)
if wheel_mode:
    logger.info("Wheel mode enabled - skipping {job_name}")
    return
```

Methods gated:
1. `_job_morning_signal`
2. `_job_crash_day_check`
3. `_job_pump_day_check`
4. `_job_ten_am_dump_exit`
5. `_job_daily_summary`

No existing intraday code was deleted or modified — only early-return guards were added.

### Tests (`tests/test_wheel_ui.py`)

6 tests covering TR-01 requirements:
- `test_wheel_mode_enabled_default_on`: fresh DB returns `wheel_mode_enabled=1`
- `test_wheel_mode_toggle`: `update_bot_state` persists 0 and 1 correctly
- `test_intraday_gated_when_wheel_mode_on`: `_job_morning_signal` does not call `get_today_signal` when wheel mode is on
- `test_intraday_runs_when_wheel_mode_off`: `_job_morning_signal` calls `get_today_signal` when wheel mode is off
- `test_all_four_intraday_jobs_gated`: parametrized over all 4 intraday jobs, verifies none call signal/position logic when wheel mode is on
- `test_daily_summary_gated`: `_job_daily_summary` does not call `send_daily_summary` when wheel mode is on

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Safe default `wheel_mode_enabled=1` | On upgrade, existing DBs default to wheel mode on — disables intraday rather than enabling it unexpectedly. Matches T-06-02 threat disposition. |
| Guard after `is_trading_day`, before signal logic | Non-trading days still short-circuit early; DB is only read on trading days, minimizing overhead. |
| Five separate guards per method | Keeps each method independently readable and testable; avoids shared mutable state. |

## Commits

| Hash | Type | Description |
|------|------|-------------|
| 96718c2 | test | Add failing tests for wheel_mode_enabled DB column and intraday job gating (RED) |
| 7b02ec6 | feat | Add wheel_mode_enabled DB column and intraday job gating (GREEN) |

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — all behavior is fully wired. The `wheel_mode_enabled` flag reads from and writes to SQLite; no mock or placeholder data flows to any consumer.

## Threat Flags

No new network endpoints, auth paths, file access patterns, or schema changes at additional trust boundaries beyond those documented in the plan's threat model.

## Self-Check

- [x] `tests/test_wheel_ui.py` exists and contains 6 test functions
- [x] `src/database.py` contains `wheel_mode_enabled INTEGER DEFAULT 1` in both CREATE TABLE and ALTER TABLE blocks
- [x] `src/smart_scheduler.py` contains `wheel_mode_enabled` in 5 locations (one per gated method)
- [x] All 9 tests pass (`python3 -m pytest tests/test_wheel_ui.py -v` — 9 passed)
- [x] Full suite passes (`python3 -m pytest tests/` — 361 passed, 0 failed)
- [x] Commits 96718c2 and 7b02ec6 exist in git log

## Self-Check: PASSED
