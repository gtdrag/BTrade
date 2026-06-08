# Phase 3: Cash-Secured Put Cycle - Research

**Researched:** 2026-04-08
**Domain:** Options signal generation, E*TRADE options execution, Telegram multi-step approval, APScheduler job registration, assignment detection via position polling
**Confidence:** HIGH — all findings verified directly from codebase; no external library additions required

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Signal Integration & Put Entry Logic**
- New `WheelStrategy` class separate from `SmartStrategy` — own `get_put_signal()` method. SmartStrategy handles intraday equity; WheelStrategy handles options. Clean separation per mixin pattern.
- IBIT pullback signal defined as: IBIT drops >=2% from recent high (5-day). Uses AlpacaProvider/MarketDataManager already built.
- Bot checks for put entry signals once daily at 10:00 AM ET — after morning volatility settles. New APScheduler job in SmartScheduler.
- Put signals do NOT fire when active wheel cycle is in SHORT_PUT or HOLDING_SHARES state. Only signal when cycle is CASH or no cycle exists. Prevents stacking.

**Strike Selection & Telegram Presentation**
- Filter chain contracts to 0.20-0.30 delta range, pick highest premium within range. Uses Phase 1's `get_ibit_options_chain()` which returns delta.
- Single Telegram message with key details + 3 inline buttons (Approve / Adjust / Reject). Shows: symbol, strike, expiration, delta, premium, max risk (strike x 100), DTE.
- "Adjust" shows 3-5 nearby alternatives (+-2 strikes from suggested), each with delta/premium/DTE. User taps one to approve, or rejects all. Single round of adjustment.
- Cash collateral validated before sending suggestion: paper/live capital >= strike x 100. If insufficient, don't suggest — log reason.

**Assignment Detection & Notification**
- Poll E*TRADE positions API via new scheduled job. Compare portfolio positions against open put records in `options_positions` table. If shares appear and put disappears, assignment occurred.
- Assignment detection runs at 8:30 AM ET on weekdays. Assignments processed overnight; check early before market opens.
- On assignment: auto-transition cycle to HOLDING_SHARES, record in DB, send Telegram notification showing shares acquired (100), cost basis (strike - premium), original put details.
- On OTM expiry (no assignment): auto-close position and cycle, record full premium as profit. Transition cycle back to CASH, close position in DB. Telegram notification with P&L.

### Claude's Discretion
- Internal structure of WheelStrategy class
- How to integrate WheelStrategy with SmartScheduler job registration
- Telegram message formatting details
- Error handling for E*TRADE API failures during assignment check
- How to determine if put expired (check DTE/expiration date vs current date)

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| CSP-01 | Bot identifies favorable put entry conditions (IBIT pullback, elevated IV, near support) | `MarketDataManager.get_quote()` provides current price; 5-day high computed from Alpaca history. Signal fires when current < 5-day high * 0.98 |
| CSP-02 | Bot selects put strike using delta targeting (0.20-0.30 delta, configurable) | `get_ibit_options_chain()` returns all contracts with `delta` field; filter `option_type == "PUT"` and `0.20 <= abs(delta) <= 0.30`, then pick max `bid` |
| CSP-03 | Bot selects expiration in 30-45 DTE range (configurable parameter) | `get_ibit_options_chain()` already pre-filters to 30-45 DTE at the API level; no additional filtering needed |
| CSP-04 | Bot sends Telegram message with suggested put trade (strike, expiration, premium, Greeks) for user approval | Extend existing `request_trade_approval()` pattern; new method `request_put_approval()` with 3 buttons |
| CSP-05 | User can approve, adjust, or reject suggested put trade via Telegram | New callback prefix `put_approve_`, `put_adjust_`, `put_reject_` handled in `_handle_callback()` dispatcher |
| CSP-06 | Bot validates sufficient cash to secure the put before suggesting trade (strike × 100 shares) | `ETradeClient.get_cash_available()` exists; compare against `strike * 100` |
| ASGN-01 | Bot detects option assignment by comparing E*TRADE positions to database state daily (8 AM ET) | `get_options_positions()` returns live positions; `get_cycle_positions()` returns DB positions; reconcile by symbol/strike/expiry |
| ASGN-02 | Bot sends Telegram notification when assignment is detected with details (shares acquired, cost basis) | `send_message()` with formatted assignment details; `transition_wheel_state()` to HOLDING_SHARES |
| ASGN-03 | Bot automatically suggests covered call parameters after assignment detection | Out of scope for Phase 3 per REQUIREMENTS.md traceability (CC-01 is Phase 4). Detection only for Phase 3 |
</phase_requirements>

---

## Summary

Phase 3 builds the first live-trading component of the wheel strategy: a signal-driven cash-secured put entry flow and daily assignment detection. The codebase from Phases 1 and 2 provides all infrastructure needed — the E*TRADE client, Telegram approval framework, database CRUD methods, and state machine are fully implemented and passing tests (200 tests collected, 92 passing for the relevant modules).

The phase creates one new file (`src/wheel_strategy.py`), adds two APScheduler jobs to `SmartScheduler`, extends the Telegram callback dispatcher for a new 3-button approval flow, and wires up an assignment detection reconciler. No new Python packages are required.

The critical implementation detail is the **multi-step Telegram flow**: the existing `request_trade_approval()` supports only Approve/Reject. The Adjust path requires a second message with alternative strike buttons, which means a new async method on `TelegramBot` and new callback data prefixes registered in `_handle_callback()`.

**Primary recommendation:** Build `WheelStrategy` as a standalone class (not a mixin) in `src/wheel_strategy.py`, injected with `MarketDataManager`, `ETradeClient`, and `Database` instances. Keep the Telegram interaction logic on `TelegramBot` via new methods. Wire scheduling in `SmartScheduler.setup_jobs()` following the existing `CronTrigger` pattern.

---

## Standard Stack

### Core (all already installed)
| Library | Version | Purpose | Source |
|---------|---------|---------|--------|
| `apscheduler` | installed | CronTrigger jobs for signal check and assignment detection | [VERIFIED: pyproject.toml pattern in codebase] |
| `python-telegram-bot` | installed | InlineKeyboardButton, CallbackQueryHandler, asyncio.Event | [VERIFIED: src/telegram/bot.py imports] |
| `sqlite3` | stdlib | Database persistence via `Database` singleton | [VERIFIED: src/database.py] |
| `zoneinfo` | stdlib | ET timezone via `get_et_now()` | [VERIFIED: src/utils.py] |

### No New Packages Required
All capabilities needed for Phase 3 are provided by existing Phase 1 and Phase 2 code. The planner should NOT add any new pip dependencies.

---

## Architecture Patterns

### Recommended Project Structure for Phase 3
```
src/
├── wheel_strategy.py          # NEW: WheelStrategy class (standalone, not mixin)
├── smart_scheduler.py         # MODIFY: add 2 new job methods + job registration
├── telegram/
│   ├── bot.py                 # MODIFY: add put approval methods + callback routing
│   └── utils.py               # MODIFY: add PutApprovalRequest dataclass
└── tests/
    └── test_wheel_strategy.py # NEW: signal, strike selection, cash validation tests
    └── test_assignment.py     # NEW: assignment detection reconciliation tests
```

### Pattern 1: WheelStrategy Class
**What:** Standalone class injected with dependencies (not a mixin). Encapsulates signal logic.
**When to use:** Strategy logic that needs market data, DB access, and E*TRADE — but is NOT part of the TradingBot mixin hierarchy.
**Example structure:**
```python
# Source: mirrors SmartStrategy pattern in src/smart_strategy.py
class WheelStrategy:
    def __init__(self, data_manager: MarketDataManager, client, db: Database):
        self.data_manager = data_manager
        self.client = client
        self.db = db

    def get_put_signal(self) -> Optional[PutSignal]:
        """Returns PutSignal if pullback conditions met, None otherwise."""
        cycle = self.db.get_active_cycle()
        if cycle and cycle["state"] in ("SHORT_PUT", "HOLDING_SHARES"):
            return None  # Already in position — no new puts
        # ... fetch quote, check 5-day high, validate cash, select strike
```

### Pattern 2: APScheduler Job Registration
**What:** Add jobs to `SmartScheduler.setup_jobs()` using `CronTrigger`.
**When to use:** Any recurring task tied to market schedule.
**Example:**
```python
# Source: src/smart_scheduler.py setup_jobs() pattern [VERIFIED]
# Put signal check — 10:00 AM ET daily
self.scheduler.add_job(
    self._job_put_signal_check,
    CronTrigger(day_of_week="mon-fri", hour=10, minute=0, timezone=ET),
    id="put_signal_check",
    name="Wheel Put Signal Check",
    misfire_grace_time=300,
)

# Assignment detection — 8:30 AM ET daily
self.scheduler.add_job(
    self._job_assignment_detection,
    CronTrigger(day_of_week="mon-fri", hour=8, minute=30, timezone=ET),
    id="assignment_detection",
    name="Assignment Detection",
    misfire_grace_time=600,
)
```
**Critical:** `SmartScheduler` receives `WheelStrategy` as a constructor injection or the job methods call `get_database()` / create a strategy instance inline.

### Pattern 3: Telegram Multi-Step Approval (NEW for Phase 3)
**What:** The existing `request_trade_approval()` handles only Approve/Reject. The Adjust path requires a second Telegram message.
**The flow:**
1. Bot sends Suggestion Message (Approve / Adjust / Reject buttons)
2. User taps Adjust → Bot edits original message OR sends new message with 3-5 alternative strikes, each as its own button
3. User taps one alternative → that becomes approved; or taps Reject All
**Callback prefix convention (must not collide):**
```python
# Primary suggestion
f"put_approve_{callback_id}"   # User approved suggested strike
f"put_adjust_{callback_id}"    # User wants alternatives
f"put_reject_{callback_id}"    # User rejected entirely

# Adjustment alternatives (sent in second message)
f"put_alt_{strike}_{callback_id}"   # User picked alternative
f"put_alt_reject_{callback_id}"     # User rejected all alternatives
```
**Important:** `_handle_callback()` in `bot.py` is the single dispatcher. New prefixes must be added there as elif branches. The existing `_approval_event` / `_approval_result` pattern uses a single asyncio.Event — for options approval, use a separate event to avoid collision with concurrent intraday approvals.

### Pattern 4: Assignment Detection Reconciliation
**What:** Compare live E*TRADE options positions against open DB positions to detect expired or assigned puts.
**Algorithm:**
```python
# Source: adapted from get_options_positions() and get_cycle_positions() [VERIFIED]
def detect_assignment(db: Database, client, account_id_key: str) -> Optional[str]:
    cycle = db.get_active_cycle()
    if not cycle or cycle["state"] != "SHORT_PUT":
        return None  # Not in a position to assign

    open_db_positions = [p for p in db.get_cycle_positions(cycle["id"])
                         if p["status"] == "OPEN"]
    if not open_db_positions:
        return None

    put_pos = open_db_positions[0]  # One put per cycle in Phase 3

    # Check if put expired (OTM path: expiration date has passed)
    from datetime import date
    expiry = date.fromisoformat(put_pos["expiry_date"])
    today = get_et_now().date()

    if today > expiry:
        # Expiration has passed — check if assigned via position API
        live_options = client.get_options_positions(account_id_key)
        live_symbols = {p["symbol"] for p in live_options}

        if put_pos["symbol"] not in live_symbols:
            # Option is gone — could be assigned OR expired worthless
            # Check for IBIT shares in equity positions
            # If IBIT shares appeared → assignment
            # If no IBIT shares → expired worthless
            ...
```
**Idempotency:** Check `cycle["state"]` before acting. If already `HOLDING_SHARES`, skip. If already `CASH` (OTM close applied), skip.

### Pattern 5: Cash Collateral Validation
**What:** Before sending put suggestion, check that available cash covers the full collateral.
**Example:**
```python
# Source: get_cash_available() in src/etrade_client.py [VERIFIED]
cash = client.get_cash_available(account_id_key)
required = suggested_contract["strike"] * 100  # 1 contract = 100 shares
if cash < required:
    logger.info(f"Insufficient cash: ${cash:.2f} < ${required:.2f} required")
    db.log_event("INFO", "put_signal_skipped",
                 {"reason": "insufficient_cash", "cash": cash, "required": required})
    return  # Do not send suggestion
```

### Pattern 6: OTM Expiry Detection (Discretion Area)
**What:** A put expires worthless when: (a) today > expiry_date AND (b) the option symbol is no longer in E*TRADE positions AND (c) no IBIT shares appeared.
**Implementation approach:**
- Check expiry_date from `options_positions` DB row against `get_et_now().date()`
- Options settle overnight after Friday expiration; Monday 8:30 AM is the right time
- For weekly options (which IBIT has), expiry is always a Friday
- The `get_ibit_options_chain()` fetches 30-45 DTE — these are monthly expirations, not weekly, but IBIT may have both. The options chain already filters DTE correctly at entry time.

### Anti-Patterns to Avoid
- **Separate asyncio.Event for options approval:** The existing `_approval_event` is a single instance variable. Concurrent intraday and options approvals would collide. Use a dedicated `_put_approval_event` and `_put_approval_result`.
- **Calling `get_active_wheel_cycle()`:** The CONTEXT.md lists this as the DB method name, but the actual implementation is `get_active_cycle()`. Use `db.get_active_cycle()` — verified in `src/database.py:874`.
- **Delta sign confusion:** Put deltas from E*TRADE are NEGATIVE (e.g., -0.25). The filter must use `abs(delta)` in range 0.20-0.30, or equivalently `-0.30 <= delta <= -0.20`.
- **Stale options chain during assignment detection:** Do NOT call `get_ibit_options_chain()` during assignment detection — that method raises `ETradeAPIError` for stale quotes and only returns 30-45 DTE contracts. Use `get_options_positions()` instead.
- **Missing `is_trading_day()` check:** Every scheduled job must guard with `if not is_trading_day(now.date()): return` — verified pattern in all existing jobs.
- **Direct state column UPDATE:** Never `UPDATE wheel_cycles SET state = ...` directly. Always use `db.transition_wheel_state()` which enforces the state machine.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| State machine transitions | Custom if/elif chain | `db.transition_wheel_state()` + `wheel_state.transition()` | Already enforces VALID_TRANSITIONS with ValueError, logs event |
| Options position persistence | Custom INSERT | `db.open_wheel_position()` + `db.close_wheel_position()` | Greeks stored individually per DB-01, audit trail preserved |
| Black-Scholes delta estimation | Hand-rolled BS | `get_ibit_options_chain()` delta field | E*TRADE provides real-time Greeks; mock client uses accurate B-S approximation |
| Cash balance check | Direct DB lookup | `client.get_cash_available(account_id_key)` | Handles IRA vs brokerage field names, fallback chain, zero-cash warning |
| Telegram async/sync bridge | Thread management | `run_async()` from `src/utils.py` | Thread-safe bridge for calling async Telegram from sync APScheduler jobs |
| P&L calculation | Custom math | `db.compute_cycle_pnl(cycle, current_price)` | Handles unrealized vs realized, state-aware |

**Key insight:** Phase 1 and 2 built a complete foundation. Phase 3's value is *wiring* existing components into a workflow, not building new primitives.

---

## Common Pitfalls

### Pitfall 1: Delta Sign Direction
**What goes wrong:** Filtering put contracts with `0.20 <= delta <= 0.30` matches nothing because put deltas are negative (-0.25, not 0.25).
**Why it happens:** The E*TRADE API returns negative deltas for puts; the mock client's `_simulate_greeks()` returns `self._normal_cdf(d1) - 1.0` which is negative for OTM puts.
**How to avoid:** Filter with `abs(contract["delta"])` or `-0.30 <= contract["delta"] <= -0.20`. Confirm against `mock_client.get_ibit_options_chain()` in tests.
**Warning signs:** Strike selection returns empty list even when chain has contracts.

### Pitfall 2: Telegram Single-Event Collision
**What goes wrong:** Options approval uses the shared `_approval_event` on TelegramBot. If an intraday signal fires at 9:35 AM while an options approval is pending from 10:00 AM, the events race.
**Why it happens:** `request_trade_approval()` sets `self._approval_event` and `self._approval_result` as instance variables — one per bot.
**How to avoid:** Add separate `_put_approval_event` and `_put_approval_result` instance variables in `TelegramBot.__init__`. The new `request_put_approval()` method uses these instead.
**Warning signs:** User approves put, intraday trade executes instead.

### Pitfall 3: Assignment Detection on Non-Expiry Days
**What goes wrong:** Detection job runs daily at 8:30 AM. On days when the put has not expired, the job polls E*TRADE unnecessarily and logs false negatives.
**Why it happens:** No expiry date guard in the detection logic.
**How to avoid:** Early return if `today <= expiry_date` (put hasn't expired yet). Only check E*TRADE positions when the expiry date has passed.
**Warning signs:** Excessive E*TRADE API calls; "option not found" logged on non-expiry days.

### Pitfall 4: `get_active_wheel_cycle()` vs `get_active_cycle()`
**What goes wrong:** CONTEXT.md references `Database.get_active_wheel_cycle()`, but the actual method is `Database.get_active_cycle()` (verified in `src/database.py:874`).
**Why it happens:** Method was renamed or documented with a different name during Phase 2.
**How to avoid:** Use `db.get_active_cycle()` in all Phase 3 code.
**Warning signs:** `AttributeError: 'Database' object has no attribute 'get_active_wheel_cycle'`

### Pitfall 5: OTM vs ITM Assignment Disambiguation
**What goes wrong:** When a put disappears from E*TRADE positions, the code incorrectly assumes assignment even if it expired worthless.
**Why it happens:** Both OTM expiry and assignment cause the option symbol to disappear from the portfolio.
**How to avoid:** After confirming the option is gone, check E*TRADE equity positions for IBIT shares. If 100 shares of IBIT appeared → assignment. If no IBIT shares → expired worthless.
**Warning signs:** Cycle transitions to HOLDING_SHARES when no shares were actually received.

### Pitfall 6: Limit Price for Options Orders
**What goes wrong:** `place_options_order()` requires a `limit_price`. Using `bid` undercuts fills; using `ask` overpays.
**Why it happens:** `_build_options_order_request()` uses `"priceType": "LIMIT"` exclusively.
**How to avoid:** For sell-to-open puts, use midpoint: `limit_price = (contract["bid"] + contract["ask"]) / 2`. Round to 2 decimal places. This is standard practice for options limit orders.
**Warning signs:** Order never fills (limit too low); overpaid premium (limit too high).

### Pitfall 7: Paper Mode Cash Collateral Check
**What goes wrong:** In paper mode, `MockETradeClient.get_cash_available()` returns `self.cash` (initial cash, default 100000). The check always passes.
**Why it happens:** Mock doesn't deduct collateral when puts are "sold."
**How to avoid:** This is intentional for paper mode — no action needed. Document that the cash check is best-effort in paper mode. For live mode it uses real API data.

---

## Code Examples

Verified patterns from existing codebase:

### Get IBIT 5-Day High for Pullback Signal
```python
# Source: AlpacaProvider.get_quote() returns current price [VERIFIED: src/data_providers.py]
# For 5-day historical high, use yfinance fallback (Yahoo) or Alpaca bars
import yfinance as yf
ticker = yf.Ticker("IBIT")
hist = ticker.history(period="5d")
five_day_high = hist["High"].max()  # [ASSUMED] — yfinance API; verify period parameter
current_price = data_manager.get_quote("IBIT").current_price
pullback_pct = (current_price - five_day_high) / five_day_high * 100
signal_fires = pullback_pct <= -2.0
```

### Filter Chain for Delta Target
```python
# Source: get_ibit_options_chain() returns delta field [VERIFIED: src/etrade_client.py:675]
chain = client.get_ibit_options_chain()
puts = [c for c in chain if c["option_type"] == "PUT"]
# Delta for puts is negative — use abs()
target_puts = [
    c for c in puts
    if 0.20 <= abs(c["delta"]) <= 0.30
]
if not target_puts:
    return None  # No contracts in target range
# Pick highest bid (most premium) within range
best_put = max(target_puts, key=lambda c: c["bid"])
```

### Sell-to-Open Put (Two-Step Preview+Place)
```python
# Source: preview_options_order() / place_options_order() [VERIFIED: src/etrade_client.py:736,759]
preview = client.preview_options_order(
    account_id_key=account_id_key,
    symbol="IBIT",
    option_type="PUT",
    expiry_year=contract["expiry_year"],
    expiry_month=contract["expiry_month"],
    expiry_day=contract["expiry_day"],
    strike_price=contract["strike"],
    order_action="SELL_OPEN",
    quantity=1,
    limit_price=round((contract["bid"] + contract["ask"]) / 2, 2),
)
preview_ids = preview.get("PreviewIds")
result = client.place_options_order(
    account_id_key=account_id_key,
    symbol="IBIT",
    option_type="PUT",
    expiry_year=contract["expiry_year"],
    expiry_month=contract["expiry_month"],
    expiry_day=contract["expiry_day"],
    strike_price=contract["strike"],
    order_action="SELL_OPEN",
    quantity=1,
    limit_price=round((contract["bid"] + contract["ask"]) / 2, 2),
    preview_ids=preview_ids,
)
```

### DB: Open Position After Execution
```python
# Source: open_wheel_position() [VERIFIED: src/database.py:980]
position_id = db.open_wheel_position(
    cycle_id=cycle_id,
    symbol=contract["symbol"],
    option_type="PUT",
    strike=contract["strike"],
    expiry_date=contract["expiry_date"].isoformat(),
    dte_at_entry=contract["dte"],
    premium_received=contract["bid"],  # actual fill price from order response
    quantity=1,
    delta=contract["delta"],
    gamma=contract["gamma"],
    theta=contract["theta"],
    vega=contract["vega"],
    iv=contract["iv"],
)
```

### DB: Transition to SHORT_PUT After Execution
```python
# Source: transition_wheel_state() [VERIFIED: src/database.py:905]
db.transition_wheel_state(
    cycle_id=cycle_id,
    next_state=WheelState.SHORT_PUT,
    reason="put_sold",
    put_strike=contract["strike"],
    put_premium_received=contract["bid"],
    put_expiry_date=contract["expiry_date"].isoformat(),
)
```

### DB: Transition to HOLDING_SHARES on Assignment
```python
# Source: transition_wheel_state() kwargs pattern [VERIFIED: src/database.py:956]
cost_basis = cycle["put_strike"] - cycle["put_premium_received"]
db.transition_wheel_state(
    cycle_id=cycle["id"],
    next_state=WheelState.HOLDING_SHARES,
    reason="put_assigned",
    shares_held=100,
    cost_basis=cost_basis,
)
```

### DB: Cycle Close on OTM Expiry
```python
# Source: transition_wheel_state() with CASH transition [VERIFIED: src/database.py:953]
# CASH transition auto-sets closed_at
db.transition_wheel_state(
    cycle_id=cycle["id"],
    next_state=WheelState.CASH,
    reason="put_expired_otm",
    realized_pnl=put_position["premium_received"] * 100,  # full premium kept
)
db.close_wheel_position(
    position_id=put_position["id"],
    close_premium=0.0,  # expired worthless — no cost to close
)
```

### APScheduler Job Registration Pattern
```python
# Source: setup_jobs() [VERIFIED: src/smart_scheduler.py:125]
self.scheduler.add_job(
    self._job_put_signal_check,
    CronTrigger(day_of_week="mon-fri", hour=10, minute=0, timezone=ET),
    id="put_signal_check",
    name="Wheel Put Signal Check",
    misfire_grace_time=300,
)
```

### Telegram 3-Button Options Approval
```python
# Source: adapted from request_trade_approval() [VERIFIED: src/telegram/bot.py:658]
callback_id = f"put_{get_et_now().strftime('%H%M%S')}"
keyboard = [
    [
        InlineKeyboardButton("Approve", callback_data=f"put_approve_{callback_id}"),
        InlineKeyboardButton("Adjust", callback_data=f"put_adjust_{callback_id}"),
        InlineKeyboardButton("Reject", callback_data=f"put_reject_{callback_id}"),
    ]
]
```

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | yfinance `ticker.history(period="5d")` returns 5 days of OHLC data suitable for 5-day high calculation | Code Examples: 5-Day High | Need to use `period="5d"` correctly; may need `period="1mo"` and slice. Impact: signal fires incorrectly |
| A2 | IBIT options expiring in the 30-45 DTE window are always monthly expirations (3rd Friday of month) | Common Pitfalls: OTM detection | If IBIT has weekly options in that window, expiry detection logic must handle any Friday, not just monthly |
| A3 | E*TRADE `get_options_positions()` returns empty list (not error) when no options held | Assignment Detection pattern | If it raises on empty portfolio, detection job needs try/except |
| A4 | `MockETradeClient.get_options_positions()` currently returns hardcoded mock data (1 tracked position) | Code Examples | Tests for assignment detection in paper mode may need `_options_positions` to be manipulable |

---

## Open Questions

1. **5-day high data source**
   - What we know: `MarketDataManager.get_quote()` provides current price only (single quote). Alpaca's bars API provides historical OHLC.
   - What's unclear: Does `AlpacaProvider` expose a `get_bars()` or historical method? The current `data_providers.py` only defines `get_quote()` and `get_quotes()`.
   - Recommendation: Use `yfinance` (already a dependency, used in `YahooProvider`) for the 5-day historical bars. `yf.Ticker("IBIT").history(period="5d")["High"].max()` is the simplest path. Verify AlpacaProvider bars capability before adding yfinance call.

2. **WheelStrategy constructor injection vs SmartScheduler ownership**
   - What we know: `SmartScheduler.__init__` takes `bot: TradingBot` and optionally `telegram_bot`. WheelStrategy needs `MarketDataManager`, `ETradeClient`, `Database`.
   - What's unclear: Should `SmartScheduler` instantiate `WheelStrategy` internally, or receive it as an injection?
   - Recommendation (Claude's discretion): `SmartScheduler` constructs `WheelStrategy` internally in `__init__`, pulling `MarketDataManager` from `bot.data_manager` (verify attribute exists) and `client` from `bot.client`. This keeps scheduler as the composition root.

3. **Adjust flow: edit original message or send new message**
   - What we know: `query.edit_message_text()` is used in the existing callback handler. New alternatives can be sent as a separate message.
   - What's unclear: User experience preference — editing the original vs a new message.
   - Recommendation (Claude's discretion): Edit the original message with the alternatives list. Cleaner UX; avoids multiple messages cluttering the chat. Use `query.edit_message_text()` with new keyboard containing 3-5 alternative buttons.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.9+ | All code | Yes | 3.9.6 | None needed |
| pytest | Test suite | Yes | 8.4.2 | None |
| python-telegram-bot | Telegram approval | Yes | installed | None |
| apscheduler | Job scheduling | Yes | installed | None |
| yfinance | 5-day historical high | Yes | installed (YahooProvider) | Alpaca bars API |
| E*TRADE API (live) | Live trading | Conditional | Requires auth | MockETradeClient for paper mode |

All runtime dependencies are available. Live E*TRADE authentication required only for live mode; MockETradeClient used in paper mode and tests.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.4.2 |
| Config file | pyproject.toml |
| Quick run command | `python3 -m pytest tests/test_wheel_strategy.py tests/test_assignment.py -x` |
| Full suite command | `python3 -m pytest tests/ -v` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CSP-01 | Signal fires when IBIT drops >= 2% from 5-day high | unit | `python3 -m pytest tests/test_wheel_strategy.py::TestPutSignal -x` | No — Wave 0 |
| CSP-01 | Signal does NOT fire when cycle is SHORT_PUT or HOLDING_SHARES | unit | `python3 -m pytest tests/test_wheel_strategy.py::TestPutSignalBlocked -x` | No — Wave 0 |
| CSP-02 | Strike selection picks highest bid in 0.20-0.30 abs(delta) range | unit | `python3 -m pytest tests/test_wheel_strategy.py::TestStrikeSelection -x` | No — Wave 0 |
| CSP-02 | Delta sign: abs(delta) filter works correctly for negative put deltas | unit | `python3 -m pytest tests/test_wheel_strategy.py::TestDeltaFilter -x` | No — Wave 0 |
| CSP-03 | Chain already filtered to 30-45 DTE by get_ibit_options_chain() | unit | `python3 -m pytest tests/test_etrade_options.py::TestOptionsChain -x` | Yes |
| CSP-06 | Cash validation: returns None when cash < strike * 100 | unit | `python3 -m pytest tests/test_wheel_strategy.py::TestCashValidation -x` | No — Wave 0 |
| ASGN-01 | Assignment detected when put disappears AND IBIT shares appear | unit | `python3 -m pytest tests/test_assignment.py::TestAssignmentDetection -x` | No — Wave 0 |
| ASGN-01 | OTM expiry: put disappears AND no IBIT shares → expired worthless | unit | `python3 -m pytest tests/test_assignment.py::TestOTMExpiry -x` | No — Wave 0 |
| ASGN-01 | Idempotency: running detection twice does not double-record | unit | `python3 -m pytest tests/test_assignment.py::TestIdempotency -x` | No — Wave 0 |
| ASGN-02 | HOLDING_SHARES transition sets cost_basis = strike - premium | unit | `python3 -m pytest tests/test_wheel_state.py::TestCostBasis -x` | Yes |

### Sampling Rate
- **Per task commit:** `python3 -m pytest tests/test_wheel_strategy.py tests/test_assignment.py -x`
- **Per wave merge:** `python3 -m pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/test_wheel_strategy.py` — covers CSP-01, CSP-02, CSP-06 (signal, strike, cash validation)
- [ ] `tests/test_assignment.py` — covers ASGN-01 (detection, OTM expiry, idempotency)
- [ ] No new framework install needed (pytest already configured)

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | E*TRADE auth already in ETradeClient |
| V3 Session Management | No | OAuth token management already in Phase 1 |
| V4 Access Control | Yes | Telegram `_is_authorized()` already checks `chat_id`; new callback prefixes must pass through same check |
| V5 Input Validation | Yes | Strike/expiry from E*TRADE API (trusted), but user-tapped callback_data is validated by prefix matching only |
| V6 Cryptography | No | No new crypto required |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Unauthorized callback tap | Elevation of Privilege | `_is_authorized()` check in `_handle_callback()` — already enforced for all callbacks |
| Callback data injection | Tampering | Parse only known prefixes (`put_approve_`, `put_adjust_`, `put_reject_`, `put_alt_`) — reject unknown formats |
| Double-execution of put order | Tampering | Check cycle state is CASH before executing; `transition_wheel_state()` raises ValueError on invalid transitions |
| Stale options quote used for order | Information Disclosure | `get_ibit_options_chain()` already raises `ETradeAPIError` for quotes >60s old (QUOTE_FRESHNESS_SECONDS) |

---

## Sources

### Primary (HIGH confidence — verified from codebase)
- `src/etrade_client.py` — `get_ibit_options_chain()`, `preview_options_order()`, `place_options_order()`, `get_options_positions()`, `get_cash_available()`, `MockETradeClient`
- `src/database.py` — `get_active_cycle()`, `transition_wheel_state()`, `open_wheel_position()`, `close_wheel_position()`, `get_cycle_positions()`, `compute_cycle_pnl()`
- `src/wheel_state.py` — `WheelState`, `VALID_TRANSITIONS`, `transition()`, `WheelCycle`, `OptionsPosition`
- `src/telegram/bot.py` — `request_trade_approval()`, `_handle_callback()`, `InlineKeyboardButton`, `_approval_event` pattern
- `src/smart_scheduler.py` — `setup_jobs()`, `CronTrigger` pattern, `is_trading_day()` guard pattern
- `src/utils.py` — `get_et_now()`, `ET`, `is_trading_day()`, `run_async()`
- `tests/test_wheel_state.py` — 59 passing tests confirming DB CRUD, transitions, cost basis
- `tests/test_etrade_options.py` — 35 passing tests confirming options chain and order methods

### Tertiary (LOW confidence — assumed from training knowledge)
- yfinance `ticker.history(period="5d")` API shape [ASSUMED — A1]
- IBIT options expiry schedule (monthly vs weekly) [ASSUMED — A2]

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new packages; all verified in codebase
- Architecture: HIGH — all patterns directly lifted from existing working code
- Pitfalls: HIGH — delta sign, event collision, method name discrepancy all verified by reading actual implementation
- Open questions: MEDIUM — yfinance API shape is assumed, method injection path requires verifying `bot.data_manager` attribute

**Research date:** 2026-04-08
**Valid until:** 2026-05-08 (stable codebase; no external API changes expected)
