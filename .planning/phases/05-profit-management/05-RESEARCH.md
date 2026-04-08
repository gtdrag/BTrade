# Phase 05: Profit Management - Research

**Researched:** 2026-04-08
**Domain:** Wheel strategy profit management — P&L monitoring, profit-taking, defensive rolling, DTE alerts
**Confidence:** HIGH (all findings based on direct codebase inspection)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Profit-Taking Mechanics**
- New scheduler job every 30 min during market hours (9:30-4:00 ET) to check option P&L. If option dropped to 50% of entry premium, trigger buy-to-close suggestion.
- Buy-to-close suggestion via Telegram message with current P&L + Approve/Reject buttons. Show: option symbol, entry premium, current ask, profit %, savings from closing early.
- After buy-to-close: if put (SHORT_PUT), transition to CASH. If covered call (COVERED_CALL), transition to HOLDING_SHARES. Premium profit recorded.
- `profit_target_pct` configurable on WheelStrategy, default 0.50. No Telegram config for v1.

**Defensive Rolling**
- "Position tested" = IBIT price within 2% of strike. Check during same 30-min monitoring job.
- Roll suggestion via Telegram: current position details + suggested new strike/expiry + Approve/Reject. Show: current strike, DTE, new strike (further OTM), new expiry (30-45 DTE), net credit/debit.
- Execute roll as two-step: buy-to-close current + sell-to-open new. Sequential execution. Record both legs.
- Track `roll_count` on options_positions record. Block roll suggestions when count >= 2. Warn "max rolls reached" at limit.
- Net-debit roll prevention: track cumulative roll cost. Block rolls where debit exceeds remaining premium.

**Expiration Monitoring**
- DTE warning once at 21 DTE via Telegram notification. Informational only, no action buttons. Flag to prevent duplicate alerts.
- Alert shows: option type, strike, expiry, DTE, current P&L %. Suggest "consider closing for profit" if profitable, "prepare for assignment/expiry" if not.
- No action at expiration — Phase 3's `detect_and_process_expiry()` handles outcomes.
- Runs as part of the 30-min monitoring job.

### Claude's Discretion
- Internal method structure for monitoring job
- How to track DTE alert sent flag (DB column or in-memory)
- How to calculate current option price (bid vs mark vs ask)
- Roll strike selection algorithm details

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| PM-01 | Bot monitors open options positions every 30 minutes during market hours | `CronTrigger` with `minute="0,30"` pattern in `SmartScheduler.setup_jobs()`; only when active position exists |
| PM-02 | Bot suggests closing position at 50% profit via Telegram (buy-to-close) | `request_put_approval` / `request_call_approval` patterns reused for `request_profit_take_approval`; existing `_btc_approval_event` pattern |
| PM-03 | Bot suggests defensive roll when position is tested (approaching strike) with credit-only validation | Two-step order: `preview_options_order` + `place_options_order` for BTC leg then STO leg; new `roll_count` and `cumulative_roll_cost` DB columns |
| PM-04 | Bot monitors expiration approach and sends alerts at 21 DTE | `dte_alert_sent` column on `options_positions`; informational-only send_message (no buttons) |
| PM-05 | Rolling is limited to max 2 rolls per position, enforcing credit-only (no debit rolls) | `roll_count` column gate + cumulative cost check before sending roll suggestion |
</phase_requirements>

---

## Summary

Phase 5 adds a 30-minute intraday monitoring job to `SmartScheduler` that checks active options positions for three conditions: 50% profit target hit (trigger buy-to-close via Telegram), position tested (IBIT price within 2% of strike, trigger roll suggestion), and 21 DTE warning (one-time informational Telegram alert). All logic is added as new methods on `WheelStrategy`, with two new Telegram approval flows in `TelegramBot`. Two new columns are needed on `options_positions`: `roll_count INTEGER DEFAULT 0` and `dte_alert_sent INTEGER DEFAULT 0`.

The codebase already provides all the building blocks: `CronTrigger`-based scheduled jobs in `SmartScheduler`, the `request_put_approval` / `request_call_approval` Telegram approval pattern with separate `asyncio.Event` instances per flow type, `preview_options_order` / `place_options_order` on `ETradeClient`, and the DB migration pattern using `ALTER TABLE ... ADD COLUMN`. The challenge is the two-step roll execution (BTC then STO are separate API calls, each needing preview + place) and ensuring the roll credit validation uses cumulative cost across all rolls on that position.

**Primary recommendation:** Add the monitoring job to `SmartScheduler`, three methods to `WheelStrategy` (check P&L, check tested, check DTE), two new Telegram approval flows (`request_profit_take_approval`, `request_roll_approval`), and migrate `options_positions` with `roll_count` + `dte_alert_sent` columns.

---

## Standard Stack

### Core (Already Installed)

| Library | Version | Purpose | Source |
|---------|---------|---------|--------|
| APScheduler | in use | 30-min CronTrigger job | [VERIFIED: src/smart_scheduler.py] |
| python-telegram-bot | in use | Telegram approval flows | [VERIFIED: src/telegram/bot.py] |
| SQLite3 | stdlib | DB migrations for new columns | [VERIFIED: src/database.py] |
| yfinance | in use | IBIT spot price for "tested" check | [VERIFIED: src/wheel_strategy.py] |

No new libraries are required. All dependencies for Phase 5 are already installed. [VERIFIED: codebase inspection]

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| yfinance spot price for tested check | `client.get_ibit_quote()` | E*TRADE quote is more reliable during market hours; either works — see Open Questions |

---

## Architecture Patterns

### Existing Scheduling Pattern (CronTrigger every 30 min)

The scheduler uses `CronTrigger` from APScheduler. Existing 15-min jobs use `minute="0,15,30,45"`. For 30-min intervals use `minute="0,30"`. [VERIFIED: src/smart_scheduler.py lines 148-224]

```python
# Source: src/smart_scheduler.py (existing pattern, adapted)
self.scheduler.add_job(
    self._job_wheel_monitoring,
    CronTrigger(
        day_of_week="mon-fri",
        hour="9-15",
        minute="0,30",
        timezone=ET,
    ),
    id="wheel_monitoring",
    name="Wheel Options Monitoring",
    misfire_grace_time=120,
)
```

**Note:** 9:30 AM (hour=9, minute=30) and 3:30 PM (hour=15, minute=30) are included with `hour="9-15"` plus `minute="0,30"`. The 4:00 PM slot is excluded because the range stops at hour 15. Context decision says "9:30-4:00 ET" — include `hour="9-16"` with a check for minutes, or use `"9-15"` and accept 3:30 PM as last run. Recommend `hour="9-15"` + `minute="0,30"` (last run at 3:30 PM is acceptable given positions close at 3:55 via the existing job).

### Efficiency Gate Pattern (Skip API Calls When No Position)

```python
# Source: src/smart_scheduler.py _job_put_signal_check pattern
def _job_wheel_monitoring(self) -> None:
    now = get_et_now()
    if not is_trading_day(now.date()):
        return
    if not self.wheel_strategy:
        return

    # Efficiency gate — only hit E*TRADE when there is an active position
    cycle = self.db.get_active_cycle()
    if cycle is None:
        logger.debug("wheel_monitoring: no active cycle, skipping")
        return
    state = cycle["state"]
    if state not in (WheelState.SHORT_PUT.value, WheelState.COVERED_CALL.value):
        logger.debug("wheel_monitoring: cycle state=%s has no open option, skipping", state)
        return

    # Delegate to WheelStrategy monitoring methods
    result = run_async(self.wheel_strategy.run_monitoring_checks(cycle))
    ...
```

[VERIFIED: pattern from src/smart_scheduler.py, adapted]

### Telegram Approval Pattern (Separate asyncio.Event Per Flow Type)

Each approval flow uses its own `asyncio.Event` instance to prevent collisions between concurrent approval requests (put approval, call approval, and the two new ones for profit-take and roll). [VERIFIED: src/telegram/bot.py lines 85-100]

The pattern:

1. Add instance vars in `__init__`: `_btc_approval_event`, `_btc_approval_result`, `_roll_approval_event`, `_roll_approval_result`
2. Add `async def request_profit_take_approval(...)` method that sends message + Approve/Reject buttons and waits on its event
3. Add `async def request_roll_approval(...)` method similarly
4. Extend `_handle_callback()` with `elif data.startswith("btc_approve_"):` and `elif data.startswith("roll_approve_"):` branches

The existing put and call approval methods follow exactly this structure — they are the direct template. [VERIFIED: src/telegram/bot.py lines 602-731, 938-1071]

### Two-Step Roll Execution

The roll is NOT a spread order. It is two sequential orders: [VERIFIED: CONTEXT.md decision]

```
Step 1: BTC current position
  preview_options_order(..., "BUY_CLOSE", ...)
  place_options_order(..., "BUY_CLOSE", ...)

Step 2: STO new position (only if Step 1 succeeds)
  preview_options_order(..., "SELL_OPEN", ...)
  place_options_order(..., "SELL_OPEN", ...)
```

On Step 1 success + Step 2 failure: position is closed but new one not opened. Must handle this state: log error, notify user, leave cycle in CASH or HOLDING_SHARES (depending on option type). Do NOT attempt to re-open automatically.

### Database Migration Pattern

Existing pattern in `database.py` `_init_db()` uses `ALTER TABLE ... ADD COLUMN`: [VERIFIED: src/database.py lines 151-155, 216-220]

```python
# Source: src/database.py _init_db() (existing migration pattern)
cursor.execute("PRAGMA table_info(options_positions)")
columns = [row[1] for row in cursor.fetchall()]
if "roll_count" not in columns:
    cursor.execute(
        "ALTER TABLE options_positions ADD COLUMN roll_count INTEGER DEFAULT 0"
    )
if "dte_alert_sent" not in columns:
    cursor.execute(
        "ALTER TABLE options_positions ADD COLUMN dte_alert_sent INTEGER DEFAULT 0"
    )
```

This runs on every startup and is idempotent. [VERIFIED: pattern from src/database.py]

### P&L Calculation: What Price to Use

The chain from `get_ibit_options_chain()` returns `bid`, `ask`, and `last` for each contract. [VERIFIED: src/etrade_client.py lines 662-681]

For 50% profit check:
- Entry premium is stored as `premium_received` in `options_positions` (per-share, what was collected when selling)
- Current cost to buy back = **ask** price (conservative: it costs more to buy than bid)
- Profit % = `(premium_received - current_ask) / premium_received`
- 50% profit target: `current_ask <= premium_received * (1 - profit_target_pct)` = `current_ask <= premium_received * 0.50`

Using `ask` is the correct convention for buy-to-close (you buy at the ask). Using `mark` (mid) is an alternative that is less conservative. The CONTEXT.md leaves this to Claude's Discretion — recommend **ask** for conservatism.

### "Position Tested" Check

"IBIT price within 2% of strike" means: `abs(ibit_price - strike) / strike <= 0.02` [ASSUMED: standard options convention, not specified in code]

Use `yfinance` for spot price (same pattern as `get_put_signal()`), or use `client.get_ibit_quote()["last_price"]`. The client route avoids a second external dependency and is already used in Phase 3 execution. Recommend `client.get_ibit_quote()` for the monitoring job since the E*TRADE connection is already alive. [VERIFIED: src/etrade_client.py line 995]

### Roll Strike Selection

The CONTEXT.md says "further OTM, 30-45 DTE" — Claude's Discretion for the algorithm. Recommended approach:

For **put roll**: select a put with lower strike than current (more OTM for a put) using existing `select_put_strike()` logic, but filtered to strikes below current strike. [ASSUMED: standard wheel rolling convention]

For **call roll**: select a call with higher strike than current (more OTM for a call) using existing `select_call_strike()` logic filtered above current. [ASSUMED: standard wheel rolling convention]

Net credit validation: `new_sto_premium > btc_cost`. Cumulative check: `sum of all btc_costs on position + new_btc_cost <= sum of all premiums received on position`. [ASSUMED: interpretation of "cumulative roll cost"]

### Anti-Patterns to Avoid

- **Parallel approval requests:** Never trigger profit-take and roll suggestions simultaneously for the same position — they would both wait on different events and could confuse the user. The monitoring job should: check profit target first; if triggered, skip the tested/roll check for this cycle. [VERIFIED: pattern from existing multi-event architecture]
- **State mutation on API failure:** Like Phases 3 and 4, wrap all E*TRADE calls in try/except and return/log on failure WITHOUT mutating DB state. [VERIFIED: threat mitigations T-03-13, T-04-04 in src/wheel_strategy.py]
- **Calling get_ibit_options_chain() when no position:** The function makes a real API call and enforces quote freshness. Only call when there is an active SHORT_PUT or COVERED_CALL position.
- **DTE alert duplication:** The `dte_alert_sent` flag must be persisted in DB (not in-memory) because the process can restart. In-memory would re-send the alert on every restart. [ASSUMED: derived from process restart risk]

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| 30-min recurring job | Custom threading.Timer loop | APScheduler CronTrigger | Handles misfire, job IDs, timezone-aware |
| Current option price | Calculate from Greeks | `get_ibit_options_chain()` ask field | Already implemented, freshness-checked |
| Inline keyboard buttons | Manual JSON construction | `InlineKeyboardMarkup` + `InlineKeyboardButton` | Existing pattern throughout bot.py |
| State-machine transition | Direct SQL UPDATE on state column | `db.transition_wheel_state()` | Validates transitions, logs audit trail |
| DB schema migration | Dropping/recreating tables | `ALTER TABLE ... ADD COLUMN` | Backward-compatible, non-destructive |
| Roll strike selection | Options pricing model | Filter existing chain by type + strike constraint | Chain already fetched, no extra API call needed |

**Key insight:** The chain already returns all data needed for P&L calculation (`bid`, `ask`, `last`, `delta`, `strike`, `dte`). Re-use `get_ibit_options_chain()` for both the current premium lookup AND finding a suitable roll strike — one API call serves both purposes in the monitoring job.

---

## Common Pitfalls

### Pitfall 1: Using Wrong Price for P&L vs. Roll Selection
**What goes wrong:** Using `bid` for buy-to-close cost (understates actual execution cost) or using `last` (may be stale if option hasn't traded recently).
**Why it happens:** Chain has three price fields; documentation/convention is ambiguous.
**How to avoid:** Use `ask` for buy-to-close cost estimation. Show `ask` in Telegram message. [ASSUMED: standard market convention]
**Warning signs:** Profit target appears hit but order fills at worse price.

### Pitfall 2: DTE Alert Re-sending After Restart
**What goes wrong:** `dte_alert_sent` stored only in instance variable. Bot restarts, variable resets to False, alert fires again.
**Why it happens:** Not persisted to DB.
**How to avoid:** Add `dte_alert_sent INTEGER DEFAULT 0` to `options_positions` table, set to 1 after sending, check before sending.

### Pitfall 3: Roll Step 2 Failure Leaves Naked Position
**What goes wrong:** BTC executes (position closed), STO fails (network error, stale quote). Account now has no option position but DB still shows cycle in SHORT_PUT or COVERED_CALL state.
**Why it happens:** Two separate API calls without atomic rollback.
**How to avoid:** On STO failure: close the DB position record (status='CLOSED'), transition cycle state appropriately (SHORT_PUT -> CASH, COVERED_CALL -> HOLDING_SHARES), and send error Telegram alert with manual instructions. Log both the BTC order ID and the STO failure details for audit.
**Warning signs:** DB cycle state is SHORT_PUT but no option position exists in E*TRADE.

### Pitfall 4: Monitoring Job Triggering While Approval Is Pending
**What goes wrong:** Monitoring job fires every 30 minutes. First run sends profit-take approval. Before user responds, next job run fires and sends another approval.
**Why it happens:** No "approval in progress" guard on the monitoring job.
**How to avoid:** Add an `_monitoring_approval_pending` flag that is set when an approval message is sent and cleared when the event fires (approved or rejected). Skip monitoring check if flag is set.

### Pitfall 5: Roll Count Tracking on Wrong Entity
**What goes wrong:** `roll_count` tracked on wheel cycle instead of options_positions record. A cycle with multiple calls would share one roll counter.
**Why it happens:** Ambiguity about "per position" vs "per cycle."
**How to avoid:** Per CONTEXT.md: "2-roll limit is per position, not per cycle." Track `roll_count` on `options_positions` row for the current open option. When a roll closes the current position and opens a new one, the new position starts with `roll_count = old_position.roll_count + 1` so history is carried forward.

### Pitfall 6: get_ibit_options_chain() Fails With ETradeAPIError (Stale Quotes)
**What goes wrong:** Market is illiquid, quotes go stale >60s. Monitoring job raises `ETradeAPIError` and crashes the job.
**Why it happens:** `get_ibit_options_chain()` raises `ETradeAPIError` on stale quotes (T-01-02). [VERIFIED: src/etrade_client.py lines 637-641]
**How to avoid:** Wrap `get_ibit_options_chain()` in try/except `ETradeAPIError`. Log warning and skip monitoring for this cycle. Do not crash job.

---

## Code Examples

### Finding Current Option Price in the Chain

```python
# Source: src/etrade_client.py chain shape (verified)
def _find_current_option_price(self, symbol: str, chain: list) -> float | None:
    """Find the ask price for an active position symbol in a live chain."""
    for contract in chain:
        if contract["symbol"] == symbol:
            return contract["ask"]  # Conservative: cost to buy back
    return None  # Option no longer in 30-45 DTE range (expired or rolled past range)
```

### 50% Profit Target Check

```python
# Based on: src/database.py options_positions schema (verified)
def check_profit_target(
    self, position: dict, chain: list, profit_target_pct: float = 0.50
) -> bool:
    """Return True if option has reached profit target."""
    entry_premium = float(position["premium_received"])
    current_ask = self._find_current_option_price(position["symbol"], chain)
    if current_ask is None:
        return False  # Can't determine price — skip
    profit_pct = (entry_premium - current_ask) / entry_premium
    return profit_pct >= profit_target_pct
```

### DTE Calculation

```python
# Pattern: src/wheel_strategy.py detect_and_process_expiry (verified)
from datetime import date
from src.utils import get_et_now

def calculate_dte(expiry_date_str: str) -> int:
    expiry = date.fromisoformat(expiry_date_str)
    today = get_et_now().date()
    return (expiry - today).days
```

### DB Methods Needed for Phase 5

New DB methods to add to `Database`:

```python
# Schema migration (in _init_db)
def _add_phase5_columns(self, cursor):
    cursor.execute("PRAGMA table_info(options_positions)")
    cols = [r[1] for r in cursor.fetchall()]
    if "roll_count" not in cols:
        cursor.execute(
            "ALTER TABLE options_positions ADD COLUMN roll_count INTEGER DEFAULT 0"
        )
    if "dte_alert_sent" not in cols:
        cursor.execute(
            "ALTER TABLE options_positions ADD COLUMN dte_alert_sent INTEGER DEFAULT 0"
        )

# Update roll_count on a position
def increment_roll_count(self, position_id: int) -> None: ...

# Mark DTE alert sent
def mark_dte_alert_sent(self, position_id: int) -> None: ...
```

### Scheduler Job Pattern (30-min)

```python
# Source: adapted from src/smart_scheduler.py CronTrigger pattern (verified)
self.scheduler.add_job(
    self._job_wheel_monitoring,
    CronTrigger(
        day_of_week="mon-fri",
        hour="9-15",
        minute="0,30",
        timezone=ET,
    ),
    id="wheel_monitoring",
    name="Wheel Options Monitoring",
    misfire_grace_time=120,
)
```

---

## State of the Art

| Old Approach | Current Approach | Impact |
|--------------|-----------------|--------|
| No monitoring job | 30-min interval check | Requires scheduler addition only, no polling loop |
| No roll tracking | `roll_count` column | Need DB migration; backward-compatible |
| No DTE alerting | `dte_alert_sent` flag | Need DB migration; backward-compatible |

---

## Runtime State Inventory

This is not a rename/refactor phase. Step 2.5 SKIPPED.

---

## Environment Availability

All dependencies already available — phase is purely code/logic additions on top of existing stack.

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| APScheduler | 30-min job | Yes | in use | — |
| python-telegram-bot | Approval flows | Yes | in use | — |
| yfinance or E*TRADE client | Spot price for "tested" check | Yes | in use | — |
| SQLite3 | DB migration | Yes | stdlib | — |

No missing dependencies. [VERIFIED: codebase inspection]

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.4.2 with asyncio mode=auto |
| Config file | pyproject.toml |
| Quick run command | `python3 -m pytest tests/ -k "profit" -x` |
| Full suite command | `python3 -m pytest tests/ -v` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| PM-01 | Monitoring job skips on non-trading day | unit | `python3 -m pytest tests/test_profit_management.py::TestMonitoringJob::test_skips_non_trading_day -x` | No — Wave 0 |
| PM-01 | Monitoring job skips when no active cycle | unit | `python3 -m pytest tests/test_profit_management.py::TestMonitoringJob::test_skips_no_active_cycle -x` | No — Wave 0 |
| PM-01 | Monitoring job skips when state is CASH or HOLDING_SHARES | unit | `python3 -m pytest tests/test_profit_management.py::TestMonitoringJob::test_skips_wrong_state -x` | No — Wave 0 |
| PM-02 | check_profit_target returns True at 50% gain | unit | `python3 -m pytest tests/test_profit_management.py::TestProfitTarget::test_triggers_at_50pct -x` | No — Wave 0 |
| PM-02 | check_profit_target returns False below threshold | unit | `python3 -m pytest tests/test_profit_management.py::TestProfitTarget::test_no_trigger_below_threshold -x` | No — Wave 0 |
| PM-02 | BTC suggestion sent to Telegram when profit target hit | unit | `python3 -m pytest tests/test_profit_management.py::TestBuyToClose::test_approval_sent -x` | No — Wave 0 |
| PM-02 | After approved BTC for SHORT_PUT, cycle transitions to CASH | unit | `python3 -m pytest tests/test_profit_management.py::TestBuyToClose::test_put_btc_transitions_to_cash -x` | No — Wave 0 |
| PM-02 | After approved BTC for COVERED_CALL, cycle transitions to HOLDING_SHARES | unit | `python3 -m pytest tests/test_profit_management.py::TestBuyToClose::test_call_btc_transitions_to_holding -x` | No — Wave 0 |
| PM-03 | check_position_tested returns True when IBIT within 2% of strike | unit | `python3 -m pytest tests/test_profit_management.py::TestPositionTested::test_triggered_within_2pct -x` | No — Wave 0 |
| PM-03 | Roll suggestion sent when tested and roll_count < 2 | unit | `python3 -m pytest tests/test_profit_management.py::TestRoll::test_suggestion_sent_when_under_limit -x` | No — Wave 0 |
| PM-03 | Roll blocked when net debit (new sto < btc cost) | unit | `python3 -m pytest tests/test_profit_management.py::TestRoll::test_blocked_net_debit -x` | No — Wave 0 |
| PM-03 | Roll executes BTC then STO sequentially | unit | `python3 -m pytest tests/test_profit_management.py::TestRoll::test_two_step_execution_order -x` | No — Wave 0 |
| PM-04 | DTE alert sent when DTE reaches 21 | unit | `python3 -m pytest tests/test_profit_management.py::TestDTEAlert::test_alert_at_21_dte -x` | No — Wave 0 |
| PM-04 | DTE alert NOT re-sent when dte_alert_sent=1 | unit | `python3 -m pytest tests/test_profit_management.py::TestDTEAlert::test_no_duplicate_alert -x` | No — Wave 0 |
| PM-05 | Roll blocked when roll_count >= 2 | unit | `python3 -m pytest tests/test_profit_management.py::TestRoll::test_blocked_at_max_rolls -x` | No — Wave 0 |
| PM-05 | "max rolls reached" warning sent on 3rd attempt | unit | `python3 -m pytest tests/test_profit_management.py::TestRoll::test_max_rolls_warning_message -x` | No — Wave 0 |
| PM-05 | roll_count increments after successful roll | unit | `python3 -m pytest tests/test_profit_management.py::TestRoll::test_roll_count_increments -x` | No — Wave 0 |

### Sampling Rate
- **Per task commit:** `python3 -m pytest tests/test_profit_management.py -x`
- **Per wave merge:** `python3 -m pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/test_profit_management.py` — covers all PM-01 through PM-05 requirements (new file)

*(No existing test infrastructure for Phase 5 features — all tests are new.)*

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | Bot already enforces `_is_authorized()` check on all callbacks |
| V3 Session Management | No | Stateless Telegram callback flow |
| V4 Access Control | Yes | Same `_is_authorized()` guard must apply to `btc_approve_*` and `roll_approve_*` callbacks |
| V5 Input Validation | Yes | Roll strike from chain lookup only (no user-supplied numeric input to parse as strike) |
| V6 Cryptography | No | No new crypto operations |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Unauthorized callback injection (malicious Telegram user sends btc_approve callback) | Tampering | `_is_authorized()` check at top of `_handle_callback()` — already enforced for all callbacks [VERIFIED: bot.py line 408] |
| Stale approval approval after timeout | Tampering | `asyncio.Event` fires once; second set() is a no-op. Event is reset per request. |
| Roll executes on wrong position (race condition) | Tampering | Re-read active cycle from DB before executing roll orders (stale signal guard — matches T-04-08 pattern in Phase 4) |
| Profit-take fires on already-closed position | Spoofing | Check `position["status"] == "OPEN"` before triggering suggestion |

---

## Open Questions

1. **IBIT spot price source for "position tested" check**
   - What we know: yfinance is used in `get_put_signal()` for historical price; `client.get_ibit_quote()` returns real-time quote from E*TRADE.
   - What's unclear: During the monitoring job, E*TRADE connection should be live. But if the client is in paper mode (`MockETradeClient`), `get_ibit_quote()` returns a mock price.
   - Recommendation: Use `client.get_ibit_quote()["last_price"]` in the monitoring job — it works in both live and paper (mock) modes and avoids an extra yfinance call.

2. **Mark price vs. ask for profit-take display**
   - What we know: Context says show "current ask" in Telegram message. The chain has `bid`, `ask`, and `last`.
   - What's unclear: Whether the display and the trigger calculation should both use `ask`, or if mark (mid) is better for display.
   - Recommendation: Use `ask` for both trigger calculation and display. It is the most conservative (realistic) estimate of what you'd pay to buy back. Note: this means users may see the trigger fire slightly before the "intuitive" 50% mark.

3. **Priority when both profit target AND position tested are true simultaneously**
   - What we know: Context doesn't specify priority.
   - What's unclear: If profit is at 55% AND IBIT is within 2% of strike — should bot suggest BTC or roll?
   - Recommendation: Check profit target first; if triggered, send BTC suggestion and skip roll check for this monitoring cycle. Profit-taking is the cleaner outcome and the roll check becomes moot if position closes.

4. **Carry roll_count across roll chain**
   - What we know: Context says "2-roll limit is per position, not per cycle." A roll closes the current position and opens a new one.
   - What's unclear: Does the new position's `roll_count` start at 0 or inherit from the closed position?
   - Recommendation: New position's `roll_count = old_position.roll_count + 1`. This way the limit is truly cumulative across the roll chain on that "logical" option leg.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Use `ask` price for buy-to-close cost estimation | Architecture Patterns, Pitfall 1 | Minor: could use `mark` (mid) instead; affects when trigger fires |
| A2 | "Position tested" = `abs(ibit_price - strike) / strike <= 0.02` | Architecture Patterns | Low: standard convention; could be absolute dollar distance instead |
| A3 | For put roll: select put with lower strike; for call roll: select call with higher strike | Architecture Patterns | Low: standard "rolling further OTM" convention |
| A4 | New position's `roll_count` = `old_position.roll_count + 1` | Open Questions #4 | Medium: if wrong, counter resets and 2-roll limit is bypassed |
| A5 | `dte_alert_sent` must be DB-persisted (not in-memory) to survive restarts | Pitfall 2 | High if wrong: alert fires on every restart until DTE passes |
| A6 | Profit-take suggestion takes priority over roll suggestion in same monitoring cycle | Open Questions #3 | Low: worst case is confusing dual messages; conservative choice is profit-take first |

---

## Sources

### Primary (HIGH confidence — codebase inspection)
- `src/smart_scheduler.py` — CronTrigger patterns, setup_jobs(), job structure, run_async bridge
- `src/telegram/bot.py` — approval flow pattern, _handle_callback routing, event isolation, initialize()
- `src/wheel_strategy.py` — WheelStrategy class, existing method structure, yfinance pattern
- `src/database.py` — DB migration pattern (ALTER TABLE), options_positions schema, wheel CRUD methods
- `src/etrade_client.py` — get_ibit_options_chain() chain shape (bid/ask/last/symbol fields), preview_options_order/place_options_order signatures
- `src/wheel_state.py` — WheelState enum, VALID_TRANSITIONS, transition() validation

### Secondary (MEDIUM confidence)
- `.planning/phases/05-profit-management/05-CONTEXT.md` — locked decisions on profit target, roll mechanics, DTE alerting
- `.planning/REQUIREMENTS.md` — PM-01 through PM-05 requirement descriptions
- Existing test files (test_put_approval.py, test_covered_call.py, test_assignment.py) — established test patterns

### Tertiary (LOW confidence)
- None — all claims are verified from codebase or documented decisions.

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries already installed, verified from imports
- Architecture patterns: HIGH — directly read from existing implementation files
- Pitfalls: HIGH (structural), MEDIUM (A2/A3 market convention assumptions)
- Test map: HIGH — test file names and class structure follow existing project patterns

**Research date:** 2026-04-08
**Valid until:** 2026-05-08 (stable codebase, 30-day window)
