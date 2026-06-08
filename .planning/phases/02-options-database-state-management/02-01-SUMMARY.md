---
phase: 02-options-database-state-management
plan: 01
subsystem: database-state
tags: [wheel-strategy, state-machine, sqlite, dataclasses, tdd]
dependency_graph:
  requires: []
  provides: [WheelState, transition, WheelCycle, OptionsPosition, wheel_cycles_table, options_positions_table]
  affects: [src/database.py]
tech_stack:
  added: []
  patterns: [Enum state machine, dataclass return types, CREATE TABLE IF NOT EXISTS, TDD red-green]
key_files:
  created:
    - src/wheel_state.py
    - tests/test_wheel_state.py
  modified:
    - src/database.py
decisions:
  - "Used string values for WheelState enum (e.g., CASH='CASH') for direct SQLite storage without serialization layer"
  - "VALID_TRANSITIONS exported as module-level dict; all state changes must go through transition() for T-02-01 mitigation"
  - "Individual REAL columns for Greeks (delta, gamma, theta, vega, iv) — not JSON blob — for queryability in Phase 5 monitoring"
  - "Foreign key on options_positions.cycle_id is declared but not PRAGMA-enforced — application-level enforcement preserves backward compatibility"
  - "TestDatabaseSchema included in Task 1 RED commit to ensure schema tests fail correctly before Task 2"
metrics:
  duration_minutes: 25
  completed_date: "2026-04-07"
  tasks_completed: 2
  tasks_total: 2
  files_created: 2
  files_modified: 1
  tests_added: 33
  tests_total_after: 174
---

# Phase 02 Plan 01: Wheel State Machine and Database Schema Summary

**One-liner:** WheelState enum with 4-state transition validation, WheelCycle/OptionsPosition dataclasses, and two new SQLite tables (wheel_cycles 14 cols, options_positions 20 cols with individual Greek columns).

## What Was Built

### src/wheel_state.py (new)
- `WheelState(Enum)` with CASH, SHORT_PUT, HOLDING_SHARES, COVERED_CALL — string values for SQLite storage
- `VALID_TRANSITIONS` dict: CASH->{SHORT_PUT}, SHORT_PUT->{CASH, HOLDING_SHARES}, HOLDING_SHARES->{COVERED_CALL}, COVERED_CALL->{HOLDING_SHARES, CASH}
- `transition(current, next_state)` — validates against VALID_TRANSITIONS, raises ValueError with descriptive message on invalid transitions
- `WheelCycle` dataclass — 14 fields tracking full cycle lifecycle (state, put details, shares, cost basis, premiums, P&L)
- `OptionsPosition` dataclass — 20 fields for individual options contracts (Greeks, premium, status, open/close data)

### src/database.py (modified)
- `wheel_cycles` table — 14 columns in `_init_db()` using `CREATE TABLE IF NOT EXISTS`
- `options_positions` table — 20 columns with foreign key reference to wheel_cycles, individual REAL columns for each Greek

### tests/test_wheel_state.py (new)
- `TestWheelStateTransitions` — 19 tests covering all 6 valid transitions, all 4 invalid transitions, enum values, VALID_TRANSITIONS structure
- `TestDataclasses` — 6 tests for WheelCycle and OptionsPosition instantiation, Optional field handling, complete field sets
- `TestDatabaseSchema` — 8 tests verifying wheel_cycles and options_positions table existence, all column names, correct SQLite types, idempotent init, backward compatibility

## Commits

| Hash | Type | Description |
|------|------|-------------|
| b260d85 | test | add failing tests for wheel state machine and dataclasses (RED phase) |
| 5e3a691 | feat | implement wheel state machine and dataclasses (GREEN phase) |
| 1b6032b | feat | add wheel_cycles and options_positions tables to database schema |

## Test Results

```
174 passed, 1 warning in 5.61s
```

- tests/test_wheel_state.py: 33 passed (all new)
- tests/test_database.py: 10 passed (no regressions)
- Full suite: 174 passed (0 failures)

## Deviations from Plan

None — plan executed exactly as written.

The TestDatabaseSchema class was included in the RED commit (Task 1) as specified in the plan's action for Task 2. The tests correctly failed on the RED commit (no `src/wheel_state.py` yet), then passed after both GREEN commits.

## Requirements Satisfied

| Req ID | Status | Evidence |
|--------|--------|---------|
| DB-01 | Complete | options_positions table has all Greek columns (delta, gamma, theta, vega, iv) as REAL, plus premium_received, status, contract details |
| DB-02 | Complete | WheelState enum with 4 states, VALID_TRANSITIONS dict, transition() raises ValueError on invalid moves, wheel_cycles table in SQLite |

## Known Stubs

None — this plan is schema and state machine only. No data-reading or rendering code exists yet.

## Threat Flags

None — plan introduces no new network endpoints, auth paths, or file access patterns. Changes are pure SQLite DDL and Python stdlib code (internal trust boundary only).

## Self-Check: PASSED

- `src/wheel_state.py` exists: FOUND
- `tests/test_wheel_state.py` exists: FOUND
- `src/database.py` modified with wheel_cycles and options_positions: FOUND
- Commits b260d85, 5e3a691, 1b6032b: FOUND (git log verified)
- All 174 tests pass: VERIFIED
