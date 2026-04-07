---
phase: 02-options-database-state-management
plan: "02"
subsystem: database
tags: [database, wheel-strategy, crud, state-machine, tdd]
dependency_graph:
  requires:
    - 02-01  # WheelState enum, transition(), WheelCycle, OptionsPosition dataclasses, schema
  provides:
    - Wheel cycle and options position CRUD API (8 methods on Database)
    - Cost basis tracking for assignment and covered call premium reduction
    - State-validated transitions with audit log
    - On-read P&L computation
  affects:
    - Phase 3 (cash-secured puts) — will call create_wheel_cycle, open_wheel_position, transition_wheel_state
    - Phase 4 (covered calls) — will call transition_wheel_state with cost_basis updates, compute_cycle_pnl
tech_stack:
  added: []
  patterns:
    - TDD (RED/GREEN) with pytest and tmp_path fixtures
    - Parameterized SQL with contextmanager connection pattern
    - State machine validation before every SQL write
    - P&L computed on read (not stored)
key_files:
  created:
    - path: tests/test_wheel_state.py
      note: "4 new test classes appended (357 lines added): TestWheelCycleCRUD, TestTransitionWheelState, TestOptionsPositions, TestCostBasis"
  modified:
    - path: src/database.py
      note: "Added 8 wheel strategy methods plus WheelState/validate_transition import (296 lines added)"
decisions:
  - "P&L computed on read in compute_cycle_pnl() — not stored in DB; avoids stale data"
  - "transition_wheel_state() takes **updates kwargs — caller provides cost_basis on assignment, not computed internally; keeps method generic"
  - "Single active cycle enforced at application level via create_wheel_cycle() pre-check"
  - "Positions never deleted — close_wheel_position() sets status=CLOSED for audit trail"
  - "SQL keys in transition_wheel_state come from controlled caller code; values use parameterized queries (T-02-06)"
metrics:
  duration_minutes: 25
  completed_date: "2026-04-06"
  tasks_completed: 1
  files_modified: 2
---

# Phase 02 Plan 02: Wheel Strategy Database CRUD Summary

**One-liner:** 8 Database methods implementing the full wheel lifecycle API — state-validated transitions, individual Greek columns, cost basis math, and on-read P&L computation using SQLite parameterized queries.

## What Was Built

Added a complete `# Wheel Strategy Operations` section to `src/database.py` with 8 methods that form the API for Phase 3 (cash-secured puts) and Phase 4 (covered calls):

| Method | Purpose |
|--------|---------|
| `create_wheel_cycle()` | Creates a new CASH cycle; enforces single-active invariant |
| `get_active_cycle()` | Returns the unclosed cycle dict or None |
| `get_cycle_history(limit)` | Returns all cycles (including closed), ordered by id DESC |
| `transition_wheel_state(cycle_id, next_state, reason, **updates)` | Validates via state machine, updates DB, logs event |
| `open_wheel_position(cycle_id, ...)` | Records an options position with 5 individual Greek columns |
| `close_wheel_position(position_id, close_premium)` | Marks CLOSED, never deletes |
| `get_cycle_positions(cycle_id)` | Returns all positions for a cycle |
| `compute_cycle_pnl(cycle, current_price)` | Computes unrealized/realized P&L on read, no DB write |

## TDD Execution

**RED commit:** `8e9fec4` — 26 new tests across 4 classes; 8 failures + 18 errors (all `AttributeError: 'Database' has no attribute`); 33 existing tests still passed.

**GREEN commit:** `b2e9783` — all 59 wheel tests pass; full suite 200/200 green.

## Security Mitigations Applied (from threat model)

| Threat | Mitigation Applied |
|--------|--------------------|
| T-02-05: State bypass | All transitions go through `validate_transition()` before SQL UPDATE |
| T-02-06: SQL injection via **updates | Field names from controlled code; values via parameterized `?` |
| T-02-07: Invalid option_type | `open_wheel_position()` validates `in ("PUT", "CALL")` before INSERT |
| T-02-08: Multiple active cycles | `create_wheel_cycle()` SELECT-before-INSERT guard raises ValueError |
| T-02-09: Cost basis sign error | Tests explicitly verify $50 strike - $2.50 premium = $47.50; covered call reduces further |

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — all 8 methods are fully implemented and wired to SQLite. No placeholder values or hardcoded returns.

## Self-Check: PASSED
