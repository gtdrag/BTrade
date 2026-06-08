---
phase: 05-profit-management
verified: 2026-04-08T23:30:00Z
status: human_needed
score: 5/5 must-haves verified
overrides_applied: 0
human_verification:
  - test: "Run the bot on a trading day with a SHORT_PUT position at 50%+ profit and confirm Telegram message arrives with correct symbol, entry premium, ask, profit %, and Approve/Reject buttons"
    expected: "Telegram message 'PROFIT TARGET HIT - 50% Reached' with correct figures; Approve executes BTC order and transitions cycle to CASH; Reject dismisses without trade"
    why_human: "End-to-end Telegram delivery, inline button rendering, and live E*TRADE order placement cannot be verified programmatically without running the bot against live services"
  - test: "Simulate position_tested condition (IBIT within 2% of strike) and confirm roll suggestion Telegram message with guard behavior: roll blocked at roll_count >= 2 and at net debit"
    expected: "Roll approval message shows current/new strike, DTE, net credit; blocking conditions send informational warning without approval dialog"
    why_human: "Credit-only validation depends on live bid/ask spread from E*TRADE chain; real-time IBIT price proximity test cannot be triggered in a dry-run"
  - test: "With a position at 21 DTE, confirm DTE alert fires once and does not repeat on the next monitoring cycle"
    expected: "First run: Telegram 'DTE WARNING - 21 Days to Expiration' message sent and dte_alert_sent set to 1; second run: no duplicate message"
    why_human: "Requires a real or near-real position row with expiry_date 21 days out and a running scheduler to observe idempotency over two cycles"
---

# Phase 5: Profit Management Verification Report

**Phase Goal:** Bot optimizes wheel returns through 50% profit-taking, defensive rolling, and expiration monitoring
**Verified:** 2026-04-08T23:30:00Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (Roadmap Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|---------|
| 1 | Bot checks option P&L every 30 minutes during market hours | VERIFIED | `smart_scheduler.py:329-338`: `CronTrigger(hour="9-15", minute="0,30", day_of_week="mon-fri")` registers `wheel_monitoring` job |
| 2 | Bot sends Telegram buy-to-close suggestion when option reaches 50% profit | VERIFIED | `wheel_strategy.py:663-695`: `check_profit_target` uses ask price; `telegram/bot.py:1363-1474`: `request_profit_take_approval` sends formatted message with Approve/Reject buttons; `smart_scheduler.py:682-701`: wires monitoring result to BTC flow |
| 3 | Bot suggests defensive roll with new strike/expiration when position tested (price near strike) | VERIFIED | `wheel_strategy.py:697-723`: `check_position_tested` (2% threshold via `get_ibit_quote`); `telegram/bot.py:1639-1787`: `request_roll_approval` with `select_roll_strike` contract selection; `smart_scheduler.py:704-752`: `elif position_tested` branch |
| 4 | Bot prevents rolls that would result in net debit or exceed 2 rolls per position | VERIFIED | `telegram/bot.py:1668-1711`: `roll_count >= 2` guard sends blocking message; `net <= 0` guard sends debit explanation; both return `None` before opening approval dialog; `_execute_roll:1943`: `set_roll_count(new_position_id, old_roll_count + 1)` |
| 5 | Bot sends Telegram alert at 21 DTE warning of approaching expiration | VERIFIED | `wheel_strategy.py:725-740`: `check_dte_warning` (`dte <= 21` and `dte_alert_sent == 0`); `telegram/bot.py:1581-1634`: `send_dte_alert` sends informational message then calls `db.mark_dte_alert_sent`; `smart_scheduler.py:673-679`: DTE alert fires first, non-blocking |

**Score:** 5/5 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/database.py` | roll_count, dte_alert_sent migration + increment_roll_count, mark_dte_alert_sent, get_open_position_for_cycle, set_roll_count methods | VERIFIED | Lines 271-280: PRAGMA migration adds both columns; lines 1113-1175: all four helper methods present with proper SQL |
| `src/wheel_strategy.py` | check_profit_target, check_position_tested, check_dte_warning, select_roll_strike, run_monitoring_checks methods + profit_target_pct | VERIFIED | Lines 663-866: all five methods fully implemented; `profit_target_pct = 0.50` at line 133 |
| `src/smart_scheduler.py` | wheel_monitoring CronTrigger job every 30 min + _monitoring_approval_pending flag + _job_wheel_monitoring method | VERIFIED | Lines 328-338: job registration; line 69: guard flag; lines 607-757: full method with guard chain and Telegram wiring |
| `src/telegram/bot.py` | request_profit_take_approval, _execute_btc_order, send_dte_alert, request_roll_approval, _execute_roll methods | VERIFIED | Lines 1363, 1475, 1581, 1639, 1789: all five methods fully implemented |
| `tests/test_profit_management.py` | 71+ tests across 16 test classes covering all monitoring logic | VERIFIED | 1776 lines; 16 test classes (TestDatabaseMigration through TestSchedulerRollWiring); 71 tests all pass |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `src/smart_scheduler.py` | `src/wheel_strategy.py` | `run_async(self.wheel_strategy.run_monitoring_checks(cycle))` | WIRED | Line 648: direct call found; result dict consumed for all three action branches |
| `src/wheel_strategy.py` | `src/database.py` | `self.db.get_open_position_for_cycle()` | WIRED | Line 827: call found in `run_monitoring_checks`; returns position row used throughout |
| `src/wheel_strategy.py` | `src/etrade_client.py` | `self.client.get_ibit_options_chain()` | WIRED | Line 834: wrapped in `try/except ETradeAPIError`; chain passed to `check_profit_target` and returned for Plan 02 flows |
| `src/smart_scheduler.py` | `src/telegram/bot.py` | `run_async(telegram_bot.request_profit_take_approval(...))` | WIRED | Line 685-693: call with all required params (position, chain, cycle, client, db, account_id_key) |
| `src/smart_scheduler.py` | `src/telegram/bot.py` | `run_async(telegram_bot.request_roll_approval(...))` | WIRED | Line 735-744: call in `elif position_tested` branch with all required params |
| `src/telegram/bot.py` | `src/etrade_client.py` | `preview_options_order + place_options_order` | WIRED | `_execute_btc_order` lines 1498-1519; `_execute_roll` lines 1819-1879: both BTC and STO steps wired |
| `src/telegram/bot.py` | `src/database.py` | `close_wheel_position, transition_wheel_state, mark_dte_alert_sent, set_roll_count` | WIRED | Lines 1527, 1536 (BTC); lines 1855, 1900 (roll); line 1629 (DTE); line 1943 (roll count) |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `_job_wheel_monitoring` | `result` dict | `run_monitoring_checks(cycle)` → `get_open_position_for_cycle` + `get_ibit_options_chain()` | Yes — live DB query + live E*TRADE chain | FLOWING |
| `check_profit_target` | `profit_pct` | `chain` arg (live from `get_ibit_options_chain`) and `position["premium_received"]` (from DB) | Yes — real ask price from chain | FLOWING |
| `check_position_tested` | `distance_pct` | `get_ibit_quote()["last_price"]` | Yes — live E*TRADE quote | FLOWING |
| `request_profit_take_approval` | `current_ask`, `premium_received` | `chain` passed from scheduler (originally from E*TRADE) | Yes — flowing from live chain | FLOWING |
| `_execute_roll` | `old_roll_count` | `position["roll_count"]` from DB row | Yes — real DB value; `set_roll_count(new_position_id, old_roll_count + 1)` increments correctly | FLOWING |
| `send_dte_alert` | `dte` | `position["expiry_date"]` from DB; `get_et_now().date()` for today | Yes — computed from real DB expiry | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Test suite — 71 profit management tests | `python3 -m pytest tests/test_profit_management.py -x -q` | 71 passed, 14 warnings | PASS |
| Full regression suite — 352 tests | `python3 -m pytest tests/ -x -q` | 352 passed, 22 warnings | PASS |
| `check_profit_target` method present | `grep -n "def check_profit_target" src/wheel_strategy.py` | line 663 | PASS |
| `wheel_monitoring` CronTrigger registered | `grep -n "wheel_monitoring" src/smart_scheduler.py` | lines 329, 336 confirm registration | PASS |
| `_execute_roll` sets roll_count correctly | `grep -n "set_roll_count" src/telegram/bot.py` | line 1943: `set_roll_count(new_position_id, old_roll_count + 1)` | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|---------|
| PM-01 | 05-01-PLAN.md | Bot monitors open options positions every 30 minutes during market hours | SATISFIED | `smart_scheduler.py:328-338`: CronTrigger `hour="9-15" minute="0,30"`; guard chain correctly skips CASH/HOLDING_SHARES states |
| PM-02 | 05-02-PLAN.md | Bot suggests closing position at 50% profit via Telegram (buy-to-close) | SATISFIED | `check_profit_target` uses ask price; `request_profit_take_approval` sends formatted Telegram message with Approve/Reject; `_execute_btc_order` executes and mutates DB only on success |
| PM-03 | 05-02-PLAN.md | Bot suggests defensive roll when position is tested (approaching strike) with credit-only validation | SATISFIED | `check_position_tested` (2% threshold); `select_roll_strike` (30-45 DTE, delta range); `request_roll_approval` validates net credit > 0 before approval dialog |
| PM-04 | 05-02-PLAN.md | Bot monitors expiration approach and sends alerts at 21 DTE | SATISFIED | `check_dte_warning` (DTE <= 21 and `dte_alert_sent == 0`); `send_dte_alert` sends informational message then calls `mark_dte_alert_sent` — idempotent |
| PM-05 | 05-02-PLAN.md | Rolling is limited to max 2 rolls per position, enforcing credit-only (no debit rolls) | SATISFIED | `request_roll_approval` guards: `roll_count >= 2` → blocking message; `net <= 0` → debit warning; `_execute_roll:1943` propagates `old_roll_count + 1` to new position via `set_roll_count` |

All 5 PM requirements from REQUIREMENTS.md (PM-01 through PM-05) are accounted for and satisfied. No orphaned requirements.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `src/smart_scheduler.py` | 68, 612 | Comments referencing "Plan 02 will..." | Info | Residual doc comments from phased development — code is fully wired; comments are inaccurate descriptions, not stubs. No code path is missing. |

No blockers or warnings found. The "Plan 02 will..." comment at line 612 is in the docstring of `_job_wheel_monitoring` which describes the historical context of the phased build — the actual code below that docstring is fully wired with all Telegram flows in place. The comment at line 68 describes the intended lifecycle of `_monitoring_approval_pending` which is correctly implemented.

### Human Verification Required

**Automated checks cover all computation logic.** The following require a live environment:

#### 1. Buy-to-Close Approval End-to-End

**Test:** Start the bot with a SHORT_PUT position whose current ask is <= 50% of premium_received. Wait for the next 30-minute monitoring cycle.
**Expected:** Telegram message arrives: "PROFIT TARGET HIT - 50% Reached" with symbol, entry premium, current ask, profit %, and Approve/Reject buttons. Approve triggers BTC order and cycle transitions to CASH. Reject dismisses with no trade.
**Why human:** Live Telegram delivery, inline button rendering, and E*TRADE order placement cannot be verified programmatically without running services.

#### 2. Defensive Roll Guards

**Test:** Create a COVERED_CALL position with IBIT price within 2% of strike. Verify (a) roll suggestion fires, (b) roll is blocked when `roll_count >= 2`, (c) roll is blocked when the net credit from rolling is negative.
**Expected:** (a) Telegram message with current/new strike, DTE, delta, BTC cost, STO premium, net credit. (b) "ROLL BLOCKED - Max rolls reached (2/2)" message sent, no approval dialog. (c) "ROLL BLOCKED - Roll would result in net debit" message, no approval dialog.
**Why human:** Requires live E*TRADE bid/ask spreads for net credit calculation; real IBIT price proximity cannot be triggered in a dry-run.

#### 3. DTE Alert Idempotency

**Test:** With a position whose `expiry_date` is 21 days from today, trigger two consecutive monitoring cycles.
**Expected:** First cycle: "DTE WARNING - 21 Days to Expiration" Telegram message sent; `dte_alert_sent` set to 1 in DB. Second cycle: no duplicate message.
**Why human:** Requires a running scheduler, a real position row, and Telegram delivery to observe over two cycles.

### Gaps Summary

No gaps found. All 5 roadmap success criteria are verified through direct code inspection and passing test suite (352 tests). All PM-01 through PM-05 requirements have confirmed implementations. The three human verification items above are required for final sign-off on live behavior — they do not indicate missing functionality, only untestable Telegram/E*TRADE integration behavior.

---

_Verified: 2026-04-08T23:30:00Z_
_Verifier: Claude (gsd-verifier)_
