---
phase: 01-e-trade-options-api-integration
plan: "02"
subsystem: etrade-client
tags: [options, api, tdd, mock, etrade]
dependency_graph:
  requires: [01-01]
  provides: [options-order-lifecycle, options-positions-query]
  affects: [src/etrade_client.py, tests/test_etrade_options.py]
tech_stack:
  added: []
  patterns: [options-limit-order-only, tdd-red-green, mock-position-tracking]
key_files:
  created:
    - tests/test_etrade_options.py (extended — TestOptionsOrders + TestOptionsPositions classes)
  modified:
    - src/etrade_client.py (options order methods on ETradeClient and MockETradeClient)
decisions:
  - "Identical _build_options_order_request() implementation in both ETradeClient and MockETradeClient — ensures payload shape is always consistent regardless of trading mode"
  - "SELL_OPEN credits cash immediately (premium_received = limit_price * quantity * 100) — correct for short option accounting"
  - "BUY_CLOSE raises ETradeAPIError if position key not found — prevents closing non-existent positions"
  - "MockETradeClient._options_positions keyed by symbol_type_strike_expiry — ensures same contract from SELL_OPEN can be matched in BUY_CLOSE"
metrics:
  duration: "2 minutes"
  completed_date: "2026-04-06"
  tasks_completed: 2
  files_modified: 2
requirements: [API-02, API-03]
---

# Phase 01 Plan 02: Options Order and Position Methods Summary

**One-liner:** Options order lifecycle (preview + SELL_OPEN/BUY_CLOSE) and positions query added to both ETradeClient and MockETradeClient using OPTN/LIMIT-only payload builder.

## What Was Built

Four method groups added to `src/etrade_client.py` on both `ETradeClient` and `MockETradeClient`:

1. `_build_options_order_request()` — Constructs the OPTN order payload with LIMIT priceType, Product block (securityType=OPTN, callPut, strikePrice, expiryYear/Month/Day), timestamp-based clientOrderId. Identical implementation in both classes.

2. `preview_options_order()` — ETradeClient: POST `/v1/accounts/{key}/orders/preview`, returns PreviewOrderResponse. Mock: returns `{"PreviewIds": [...], "Order": [...]}` with estimated premium (limit_price * quantity * 100).

3. `place_options_order()` — ETradeClient: POST `/v1/accounts/{key}/orders/place` with optional PreviewIds attachment. Mock: SELL_OPEN credits cash and adds to `_options_positions` dict; BUY_CLOSE debits cash and removes from dict; raises `ETradeAPIError` if BUY_CLOSE has no matching position.

4. `get_options_positions()` — ETradeClient: calls `get_account_positions()` and filters for `Product.securityType == "OPTN"`, returning normalized contract dicts. Mock: returns list from `_options_positions` values.

13 new tests added across `TestOptionsOrders` and `TestOptionsPositions` classes.

## Tasks

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add tests for options orders and positions (RED) | 10f0719 | tests/test_etrade_options.py |
| 2 | Implement options order and position methods on both clients (GREEN) | 07080d4 | src/etrade_client.py |

## Verification Results

- `python3 -m pytest tests/test_etrade_options.py -v` — 23/23 passed
- `python3 -m pytest tests/ -x -q` — 141/141 passed, no regressions
- `grep -n "def preview_options_order" src/etrade_client.py` — lines 736, 1235 (both classes)
- `grep -n "def place_options_order" src/etrade_client.py` — lines 759, 1255 (both classes)
- `grep -n "def get_options_positions" src/etrade_client.py` — lines 785, 1316 (both classes)
- `grep -n "def _build_options_order_request" src/etrade_client.py` — lines 689, 1188 (both classes)
- `grep -n '"orderType": "OPTN"'` — found in both _build_options_order_request implementations
- `grep -n "_options_positions"` — initialized in __init__, set in SELL_OPEN, deleted in BUY_CLOSE

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Identical payload builder in both classes | Plan spec required same implementation — ensures payload shape for MockETradeClient tests validates the real ETradeClient payload exactly |
| Position key format: `{symbol}_{type}_{strike}_{YYYYMMDD}` | Unique per contract; enables exact SELL_OPEN → BUY_CLOSE matching without ambiguity |
| Cash accounting on SELL_OPEN: credit immediately | Standard options accounting — premium received upfront when selling |
| ETradeAPIError on BUY_CLOSE with no position | Prevents silent no-op; matches real API behavior of rejecting close on non-existent position |

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — all methods are fully wired. MockETradeClient `get_options_positions()` returns `market_value` as `limit_price * quantity * 100` (opening premium, not current market value) and `total_gain` as 0.0. This is intentional for a mock — no live pricing. Documented in method docstring context.

## Threat Flags

No new security surface beyond the plan's threat model. All four methods route through the existing `_request()` infrastructure (which handles auth, retry, rate limiting). No new network endpoints introduced beyond what was planned.

## Self-Check: PASSED

- `/Users/georgedrag/APP_PROJECTS/ibit/.claude/worktrees/agent-af8b2de4/src/etrade_client.py` — exists, contains all four methods in both classes
- `/Users/georgedrag/APP_PROJECTS/ibit/.claude/worktrees/agent-af8b2de4/tests/test_etrade_options.py` — exists, contains TestOptionsOrders and TestOptionsPositions
- Commit 10f0719 — TDD RED test commit
- Commit 07080d4 — GREEN implementation commit
- 141 tests pass, 0 failures
