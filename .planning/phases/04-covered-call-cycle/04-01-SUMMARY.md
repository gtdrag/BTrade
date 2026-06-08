---
phase: "04"
plan: "01"
subsystem: wheel-strategy
tags: [tdd, covered-call, options, signal-generation, state-machine]
dependency_graph:
  requires: []
  provides: [CallSignal, get_call_signal, select_call_strike, detect_and_process_expiry-covered-call-branch]
  affects: [src/wheel_strategy.py]
tech_stack:
  added: []
  patterns: [dataclass, cost-basis-hard-filter, dual-condition-detection, try-except-api-wrap]
key_files:
  created:
    - tests/test_covered_call.py
  modified:
    - src/wheel_strategy.py
decisions:
  - "select_call_strike uses hard filter strike >= cost_basis (T-04-01) before any delta filtering"
  - "detect_and_process_expiry now branches on state value instead of early-return gate"
  - "Full-cycle P&L on call-away: put_premium + call_premiums + (call_strike - original_cost_basis)*100"
  - "call_expired_otm transitions back to HOLDING_SHARES, keeping cycle open for next call"
metrics:
  duration_minutes: 8
  completed_date: "2026-04-08"
  tasks_completed: 2
  files_changed: 2
---

# Phase 04 Plan 01: Covered Call Signal and Call-Away Detection Summary

**One-liner:** CallSignal dataclass with cost-basis-protected strike selection (delta 0.25-0.35, strike >= cost_basis) and dual-condition call-away detection (call gone AND shares gone) extending the WheelStrategy expiry state machine.

## What Was Built

Covered call decision engine added to `src/wheel_strategy.py`:

- **`CallSignal` dataclass**: mirrors `PutSignal` with `total_premium` (premium * 100) and `cost_basis` fields for downstream display and re-validation.
- **`select_call_strike(cost_basis)`**: fetches options chain, filters to CALL contracts in `0.25-0.35` abs(delta) range with strike >= cost_basis (T-04-01 hard filter), returns highest-bid candidate.
- **`get_call_signal()`**: three-gate check — state must be `HOLDING_SHARES`, strike selection must find a candidate, then builds and returns `CallSignal`. Logs summary only (T-04-06).
- **`detect_and_process_expiry()` COVERED_CALL branch**: replaces single early-return state gate with branching logic. COVERED_CALL path detects expiry (today > call expiry date), confirms call settlement (call gone from live options), then uses dual condition for outcome:
  - **called_away**: call gone AND IBIT shares gone from equity -> transitions to CASH with full-cycle P&L
  - **call_expired_otm**: call gone, shares remain -> transitions back to HOLDING_SHARES

## Commits

| Task | Commit | Message |
|------|--------|---------|
| Task 1 (RED) | 35d2cba | test(04-01): add failing tests for call signal, strike selection, call-away detection |
| Task 2 (GREEN) | f93faec | feat(04-01): implement CallSignal, call strike selection, and call-away detection |

## Test Results

- New tests: 15 (all in `tests/test_covered_call.py`)
- Full suite: 261 passed, 0 failures (baseline was 246)

## Threat Mitigations Applied

| Threat ID | Mitigation | Location |
|-----------|-----------|----------|
| T-04-01 | Hard filter `float(c["strike"]) >= cost_basis` before returning any candidate | `select_call_strike()` |
| T-04-02 | Dual condition: call gone from options AND IBIT shares gone from equity | `detect_and_process_expiry()` COVERED_CALL branch |
| T-04-03 | State gate: only COVERED_CALL state enters the new branch; HOLDING_SHARES/CASH skip | `detect_and_process_expiry()` branching |
| T-04-04 | `get_options_positions` and `get_account_positions` wrapped in try/except; state NOT mutated on error | `detect_and_process_expiry()` |
| T-04-05 | Every outcome (called_away, call_expired_otm) logged via `db.log_event()` | `detect_and_process_expiry()` |
| T-04-06 | Only strike, delta, cost_basis logged for call signal — never raw chain or balance data | `get_call_signal()` |

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — all methods are fully implemented with real logic.

## Self-Check: PASSED

- `tests/test_covered_call.py` exists: FOUND
- `src/wheel_strategy.py` contains `class CallSignal`: FOUND
- `src/wheel_strategy.py` contains `def get_call_signal`: FOUND
- `src/wheel_strategy.py` contains `def select_call_strike`: FOUND
- Commit 35d2cba exists: FOUND
- Commit f93faec exists: FOUND
- 261 tests pass, 0 failures: VERIFIED
