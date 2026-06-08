---
phase: 03-cash-secured-put-cycle
plan: "01"
subsystem: wheel-strategy
tags: [tdd, wheel-strategy, signal-generation, options, put-signal]
dependency_graph:
  requires:
    - "02-02: wheel_cycles DB schema (create_wheel_cycle, get_active_cycle, transition_wheel_state)"
    - "src/etrade_client.py: MockETradeClient.get_ibit_options_chain, get_cash_available"
  provides:
    - "WheelStrategy.get_put_signal() — cash-secured put entry signal"
    - "WheelStrategy.select_put_strike() — delta-range strike selection"
    - "PutSignal dataclass — typed result for Telegram approval flow"
  affects:
    - "03-02: Telegram approval flow will consume PutSignal"
    - "03-03: Order execution will consume PutSignal.strike, PutSignal.symbol"
tech_stack:
  added:
    - "yfinance: 5-day high lookups for IBIT pullback signal"
  patterns:
    - "TDD (RED -> GREEN): failing tests committed before implementation"
    - "State machine gate: db.get_active_cycle() checked before any API calls"
    - "Delta range filtering: abs(delta) in [0.20, 0.30] for OTM puts"
key_files:
  created:
    - src/wheel_strategy.py
    - tests/test_wheel_strategy.py
  modified: []
decisions:
  - "Use Close.iloc[-1] from the same history() call as current price proxy (avoids second API round-trip)"
  - "State check is first gate (T-03-04): early return before chain/price lookups — enforces state machine"
  - "select_put_strike() returns the raw contract dict (not PutSignal) to keep gate logic clean"
  - "expiry_date serialized to string via isoformat() to handle both date objects and strings from MockETradeClient"
metrics:
  duration: "~12 minutes"
  completed_date: "2026-04-08"
  tasks_completed: 2
  files_created: 2
  tests_added: 16
  tests_total: 216
---

# Phase 03 Plan 01: WheelStrategy Signal Generation Summary

**One-liner:** WheelStrategy class with get_put_signal() that fires on IBIT >=2% pullback from 5-day high, selects highest-bid put in 0.20-0.30 abs(delta) range, and gates on cash collateral >= strike * 100.

## What Was Built

`src/wheel_strategy.py` — decision engine for the wheel strategy's cash-secured put leg.

**PutSignal dataclass** with 15 fields: strike, expiry_date, expiry_year/month/day, delta, premium (bid), dte, max_risk (strike*100), symbol, iv, gamma, theta, vega, pullback_pct.

**WheelStrategy class** with configurable thresholds (pullback_threshold=-2.0, delta_min=0.20, delta_max=0.30).

**Four-gate signal flow in get_put_signal():**

1. **State gate** (T-03-04 mitigation): checks db.get_active_cycle() first. Returns None if cycle is SHORT_PUT or HOLDING_SHARES — prevents stacking positions.
2. **Price gate**: fetches IBIT 5-day history via yfinance, computes pullback_pct = (close - 5d_high) / 5d_high * 100. Returns None if pullback_pct > -2.0.
3. **Strike gate**: calls select_put_strike() which fetches the chain and picks the highest-bid PUT in 0.20-0.30 abs(delta) range.
4. **Cash gate**: calls client.get_cash_available() and returns None if cash < strike * 100.

**select_put_strike():** Filters chain to PUTs, then to delta range, then returns max by bid.

## Threat Mitigations Applied

| Threat | Mitigation |
|--------|-----------|
| T-03-01: Info disclosure via logs | Logs only pullback_pct, strike, delta, cash — never raw chain or account balance |
| T-03-03: yfinance API failure | Wrapped in try/except; returns None with warning log on any exception or empty history |
| T-03-04: State bypass | State check is the very first gate — no API calls made for blocking states |

T-03-02 (yfinance tampering) accepted per plan — no financial damage possible without Telegram approval.

## Test Coverage

16 test methods across 3 classes in `tests/test_wheel_strategy.py` (319 lines):

| Class | Tests | Covers |
|-------|-------|--------|
| TestPutSignal | 7 | pullback detection, state blocking (SHORT_PUT, HOLDING_SHARES, CASH), no-cycle case, field types |
| TestStrikeSelection | 6 | highest-bid selection, negative delta handling, no-range case, CALL filtering, empty chain |
| TestCashValidation | 3 | insufficient cash (3k vs 4.8k), sufficient cash (5k), max_risk formula |

Full suite: 216/216 passing, 0 regressions.

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| Task 1 (TDD RED) | ecb6837 | test(03-01): 16 failing tests across 3 classes |
| Task 2 (TDD GREEN) | 26678aa | feat(03-01): WheelStrategy + PutSignal implementation |

## Deviations from Plan

**1. [Rule 1 - Bug] Fixed Python 3.9 type annotation syntax**
- **Found during:** Task 1 (test collection)
- **Issue:** `str | None` union syntax requires Python 3.10+; runtime is Python 3.9.6
- **Fix:** Added `from typing import Optional` import; replaced `str | None` with `Optional[str]`
- **Files modified:** tests/test_wheel_strategy.py
- **Commit:** 26678aa (bundled with GREEN commit)

**2. [Rule 2 - Missing critical] expiry_date serialization**
- **Found during:** Task 2 (implementation)
- **Issue:** MockETradeClient returns expiry_date as a Python `date` object, but real E*TRADE API returns strings. PutSignal.expiry_date must be a consistent string.
- **Fix:** Added `hasattr(expiry_date, "isoformat")` check before building PutSignal; calls `.isoformat()` on date objects, `str()` on strings.
- **Files modified:** src/wheel_strategy.py

## Known Stubs

None. All fields in PutSignal are populated from real (or mock) data sources. No hardcoded placeholders.

## Threat Flags

None. No new network endpoints, auth paths, file access patterns, or schema changes introduced. WheelStrategy is a read-only signal generator — it calls existing client/db methods and returns a dataclass.

## Self-Check: PASSED

- src/wheel_strategy.py: FOUND
- tests/test_wheel_strategy.py: FOUND
- commit ecb6837: FOUND
- commit 26678aa: FOUND
- 16 tests in wheel_strategy: FOUND
- Full suite 216 passing: VERIFIED
