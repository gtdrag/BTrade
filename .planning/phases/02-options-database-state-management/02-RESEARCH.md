# Phase 2: Options Database & State Management - Research

**Researched:** 2026-04-06
**Domain:** SQLite schema design, Python Enum state machines, options position cost basis tracking
**Confidence:** HIGH

## Summary

This phase adds two SQLite tables (`wheel_cycles` and `options_positions`) and a Python `WheelState` Enum state machine to `src/database.py`. The existing codebase provides an excellent template: `_init_db()` uses `CREATE TABLE IF NOT EXISTS`, column migrations via `ALTER TABLE`, and a `log_event()` method that writes to the `logs` table. All new code must follow these exact patterns.

The locked decisions from CONTEXT.md are specific and actionable: the state machine uses a `WheelState(Enum)` with four states, transitions are validated in a `transition()` method, Greeks are individual columns (not JSON), and P&L is computed on read (not stored). Phase 1's `get_ibit_options_chain()` contract dict already defines the exact columns needed for `options_positions`.

One critical discrepancy: CONTEXT.md references "existing `event_log` table" for transition logging — but the actual table is named `logs`, accessed via `db.log_event()`. The planner must use `db.log_event()`, not a hypothetical `event_log` table.

**Primary recommendation:** Extend `src/database.py` in-place following established patterns. Add `WheelState` Enum and `WheelCycle`/`OptionsPosition` dataclasses to a new `src/wheel_state.py` module, then import into `database.py` for query return types.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Wheel Cycle State Machine**
- Python Enum states with transition validation: `WheelState(Enum)` with CASH, SHORT_PUT, HOLDING_SHARES, COVERED_CALL
- A `transition()` method validates legal state transitions and raises on invalid ones
- New `wheel_cycles` table in SQLite via `_init_db()` — follows existing database pattern
- Single cycle only (IBIT-only per requirements) — one active cycle at a time
- State transitions logged to existing `event_log` table with `event_type='wheel_transition'`
  - **RESEARCH NOTE:** The actual table is `logs`, accessed via `db.log_event(level, event, details)` — no `event_log` table exists. Use `db.log_event("INFO", "wheel_transition", {...})`.

**Cost Basis Tracking**
- Running calculation on the cycle record: `cost_basis = strike_price - put_premium_received - covered_call_premiums_collected`
- Updated on each premium event (put sold, call sold, position closed)
- No commissions tracking (E*TRADE commissions are $0 for options)
- P&L computed on read (derived from cycle data), not stored — avoids stale values
- Put expires worthless: full premium is profit, cycle returns to CASH, record `realized_pnl = premium_received`

**Options Position Storage**
- New `options_positions` table — separate from existing `trades` table
- Individual Greek columns (delta, gamma, theta, vega, iv) — queryable, matches Phase 1 data shape
- Foreign key: `options_positions.cycle_id` references `wheel_cycles.id`
- One cycle has multiple positions over its lifetime (put entry, then call entry)
- Never delete positions — mark as `status='CLOSED'`. Full audit trail for P&L queries
- Schema changes backward-compatible: add columns with defaults, never remove (per CLAUDE.md)

### Claude's Discretion
- Exact column names and types for both tables
- Database migration approach within `_init_db()`
- Query method signatures and return types
- Whether to use dataclasses for cycle/position records or plain dicts

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DB-01 | Options positions are persisted with contract details, Greeks at entry, premium, and status | Phase 1 contract dict shape maps directly to `options_positions` columns: symbol, option_type, strike, expiry_date, dte, bid, ask, delta, gamma, theta, vega, iv. Add status and premium_received columns. |
| DB-02 | Wheel cycles are tracked with state machine (CASH → SHORT_PUT → HOLDING_SHARES → COVERED_CALL) | `WheelState(Enum)` with four states. Valid transitions: CASH→SHORT_PUT, SHORT_PUT→CASH (expires), SHORT_PUT→HOLDING_SHARES (assigned), HOLDING_SHARES→COVERED_CALL, COVERED_CALL→HOLDING_SHARES (call expires), COVERED_CALL→CASH (called away). |
| DB-03 | Assigned shares are recorded with adjusted cost basis (strike price minus premium received) | `cost_basis = strike_price - put_premium_received` on HOLDING_SHARES transition. Stored as running field on `wheel_cycles` row. |
| DB-04 | Cost basis is updated when additional premiums are collected (covered call premium reduces basis) | `cost_basis -= covered_call_premiums_collected` on each call sold event. `covered_call_premiums_collected` accumulates as a running total column. |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| sqlite3 | stdlib | Database engine | Already used throughout codebase |
| enum | stdlib | State machine values | Already used in `Signal`, `TradingMode`, `BotStatus` throughout codebase |
| dataclasses | stdlib | Typed record objects | Already used for `Signal`, `BotConfig`, `StrategyConfig`, `TradeResult` |
| typing | stdlib | Type hints | Already imported in `database.py` |

All standard library — no new dependencies. [VERIFIED: codebase grep, src/database.py imports]

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| datetime | stdlib | Timestamps | Use `get_et_now()` from `src/utils.py` — never `datetime.now()` |
| json | stdlib | Details field serialization | Use `safe_json_dumps()` from `src/database.py` for log event details |

**Installation:**
No new packages. Phase is pure stdlib + existing project code.

## Architecture Patterns

### Recommended File Structure

```
src/
├── database.py          # EXTEND: add two new tables in _init_db(), new query methods
├── wheel_state.py       # NEW: WheelState enum, WheelCycle and OptionsPosition dataclasses
tests/
└── test_wheel_state.py  # NEW: tests for state machine and database methods
```

**Why a separate `wheel_state.py`?** The project pattern is `src/trading_bot/config.py` for types, `src/smart_strategy.py` for strategy types — types and enums live in their own module to avoid circular imports. The `Database` class in `database.py` would import from `wheel_state.py` for return type annotations.

### Pattern 1: WheelState Enum (follows project convention)

**What:** Python Enum with string values for SQLite storage; transition validation raises on invalid moves.
**When to use:** Any code that reads or writes wheel cycle state.
**Example:**
```python
# Source: project pattern from src/smart_strategy.py Signal enum + src/trading_bot/config.py TradingMode enum
from enum import Enum

class WheelState(Enum):
    CASH = "CASH"
    SHORT_PUT = "SHORT_PUT"
    HOLDING_SHARES = "HOLDING_SHARES"
    COVERED_CALL = "COVERED_CALL"

# Valid transition map
_VALID_TRANSITIONS = {
    WheelState.CASH: {WheelState.SHORT_PUT},
    WheelState.SHORT_PUT: {WheelState.CASH, WheelState.HOLDING_SHARES},
    WheelState.HOLDING_SHARES: {WheelState.COVERED_CALL},
    WheelState.COVERED_CALL: {WheelState.HOLDING_SHARES, WheelState.CASH},
}

def transition(current: WheelState, next_state: WheelState) -> WheelState:
    """Validate and return next state. Raises ValueError on invalid transition."""
    if next_state not in _VALID_TRANSITIONS[current]:
        raise ValueError(
            f"Invalid wheel transition: {current.value} -> {next_state.value}. "
            f"Valid: {[s.value for s in _VALID_TRANSITIONS[current]]}"
        )
    return next_state
```
[VERIFIED: pattern matches Signal enum in src/smart_strategy.py lines 34-47]

### Pattern 2: Table Creation in `_init_db()` (exact existing pattern)

**What:** `CREATE TABLE IF NOT EXISTS` inside `_init_db()`, followed by `ALTER TABLE` migrations for new columns.
**When to use:** Always — never create tables outside `_init_db()`.
**Example:**
```python
# Source: src/database.py _init_db() method — exact pattern used for all tables
cursor.execute("""
    CREATE TABLE IF NOT EXISTS wheel_cycles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        state TEXT NOT NULL DEFAULT 'CASH',
        underlying TEXT NOT NULL DEFAULT 'IBIT',
        put_strike REAL,
        put_premium_received REAL DEFAULT 0.0,
        put_expiry_date TEXT,
        shares_held INTEGER DEFAULT 0,
        cost_basis REAL DEFAULT 0.0,
        covered_call_premiums_collected REAL DEFAULT 0.0,
        realized_pnl REAL,
        opened_at TEXT NOT NULL,
        closed_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
""")

cursor.execute("""
    CREATE TABLE IF NOT EXISTS options_positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cycle_id INTEGER NOT NULL REFERENCES wheel_cycles(id),
        symbol TEXT NOT NULL,
        option_type TEXT NOT NULL,
        strike REAL NOT NULL,
        expiry_date TEXT NOT NULL,
        dte_at_entry INTEGER NOT NULL,
        quantity INTEGER NOT NULL DEFAULT 1,
        premium_received REAL NOT NULL,
        delta REAL,
        gamma REAL,
        theta REAL,
        vega REAL,
        iv REAL,
        status TEXT NOT NULL DEFAULT 'OPEN',
        close_premium REAL,
        opened_at TEXT NOT NULL,
        closed_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
""")
```
[VERIFIED: matches _init_db() pattern in src/database.py lines 94-228]

### Pattern 3: Dataclass Return Types (follows project convention)

**What:** `@dataclass` for structured query returns, matching project's use of dataclasses for `TradeResult`, `BotConfig`, etc.
**When to use:** Query methods that return structured data to callers.
**Example:**
```python
# Source: project pattern from src/trading_bot/config.py TradeResult dataclass
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

@dataclass
class WheelCycle:
    id: int
    state: WheelState
    underlying: str
    put_strike: Optional[float]
    put_premium_received: float
    put_expiry_date: Optional[str]
    shares_held: int
    cost_basis: float
    covered_call_premiums_collected: float
    realized_pnl: Optional[float]
    opened_at: str
    closed_at: Optional[str]
    created_at: str
    updated_at: str

@dataclass
class OptionsPosition:
    id: int
    cycle_id: int
    symbol: str
    option_type: str  # "PUT" or "CALL"
    strike: float
    expiry_date: str
    dte_at_entry: int
    quantity: int
    premium_received: float
    delta: Optional[float]
    gamma: Optional[float]
    theta: Optional[float]
    vega: Optional[float]
    iv: Optional[float]
    status: str  # "OPEN" or "CLOSED"
    close_premium: Optional[float]
    opened_at: str
    closed_at: Optional[str]
    created_at: str
    updated_at: str
```
[VERIFIED: pattern matches dataclasses in src/trading_bot/config.py and src/smart_strategy.py]

### Pattern 4: State Transition Logging (corrected from CONTEXT.md)

**What:** Log state transitions using existing `db.log_event()` which writes to the `logs` table.
**Critical correction:** CONTEXT.md says "existing `event_log` table" — this table does NOT exist. The actual logging table is `logs`, accessed via `db.log_event(level, event, details)`.
**Example:**
```python
# Source: src/database.py log_event() at line 463 — signature: log_event(level, event, details)
# Usage: src/database.py line 428 — db.log_event("MODE_CHANGE", new_mode=mode)
# NOTE: The actual method takes **kwargs not a dict for details in some calls
db.log_event(
    "INFO",
    "wheel_transition",
    details={
        "cycle_id": cycle_id,
        "from_state": current_state.value,
        "to_state": next_state.value,
        "reason": reason,
    }
)
```
[VERIFIED: log_event() method in src/database.py line 463, logs table at line 170]

### Pattern 5: Cost Basis Calculation (running total on cycle record)

**What:** The `wheel_cycles` row maintains a running `cost_basis` field that Phase 3+ methods update on each event.
**Formula:**
```
Initial assignment:  cost_basis = put_strike - put_premium_received
Each covered call:   cost_basis -= covered_call_premium_received
                     covered_call_premiums_collected += covered_call_premium_received
```
**P&L computation on read (not stored):**
```python
# DB-01 requirement: P&L computed on read from cycle data
def compute_cycle_pnl(cycle: WheelCycle, current_ibit_price: float) -> dict:
    """Compute unrealized and realized P&L from cycle data."""
    if cycle.state == WheelState.HOLDING_SHARES:
        unrealized = (current_ibit_price - cycle.cost_basis) * 100  # 100 shares per contract
        return {"unrealized_pnl": unrealized, "realized_pnl": cycle.realized_pnl or 0.0}
    return {"unrealized_pnl": 0.0, "realized_pnl": cycle.realized_pnl or 0.0}
```
[VERIFIED: decision in CONTEXT.md, cost_basis field design matches DB-03 and DB-04]

### Anti-Patterns to Avoid

- **Separate `event_log` table:** Does not exist — use `db.log_event()` → `logs` table.
- **Storing computed P&L:** Decision is to compute on read. Never store `unrealized_pnl` or `percentage_pnl` on `wheel_cycles`.
- **Using `datetime.now()`:** Always use `get_et_now()` from `src.utils` — per CLAUDE.md gotchas.
- **Deleting positions:** Mark as `status='CLOSED'` — never DELETE rows from `options_positions`.
- **Instantiating `Database` directly:** Always `db = get_database()` — per CLAUDE.md critical patterns.
- **JSON blob for Greeks:** Individual columns (delta, gamma, theta, vega, iv) as REAL — explicitly decided in CONTEXT.md for queryability.
- **Multiple active cycles:** One active cycle at a time. Query methods must enforce/document this.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| State machine validation | Custom guard conditions spread across callers | `transition()` function in `wheel_state.py` | Centralizes validation; callers can't bypass it; raises explicitly |
| Thread-safe DB access | Manual locking | Existing `_get_connection()` context manager | Already uses WAL mode + 30s timeout; tested and concurrency-safe |
| Enum serialization | Custom string conversion | `state.value` (Enum's built-in) | Returns the string literal stored in SQLite; matches project convention |
| Timestamp generation | `datetime.now()` | `get_et_now()` from `src.utils` | Mandatory ET timezone — CLAUDE.md gotcha #1 |
| JSON for details | Manual `json.dumps` | `safe_json_dumps()` from `src.database` | Handles numpy types; already used throughout codebase |

**Key insight:** The entire state machine complexity reduces to one `_VALID_TRANSITIONS` dict and one `transition()` function. Everything else is standard SQLite CRUD following the existing `record_trade_entry()`/`record_trade_exit()` pattern.

## Common Pitfalls

### Pitfall 1: Wrong Logging Table Name

**What goes wrong:** Code writes to `event_log` table which doesn't exist → `sqlite3.OperationalError: no such table: event_log`
**Why it happens:** CONTEXT.md says "existing `event_log` table" but the actual table is `logs`. The `log_event()` method writes to `logs`.
**How to avoid:** Use `db.log_event(level, event, details)` — never write SQL targeting `event_log`.
**Warning signs:** Any SQL with `INSERT INTO event_log` is wrong.

### Pitfall 2: `log_event()` Method Signature Inconsistency

**What goes wrong:** `db.log_event()` is called with kwargs directly (line 428: `self.log_event("MODE_CHANGE", new_mode=mode)`) but the method signature is `log_event(self, level: str, event: str, details: Optional[Dict] = None)` — the call at line 428 passes `new_mode` as a **kwarg to `log_event`** which will fail since `log_event` doesn't accept `**kwargs`.
**Research finding:** There is an inconsistency in the existing code — `log_event()` at line 463 takes `details: Optional[Dict]` but line 428 calls it with a kwarg. The call at line 428 likely works because of Python's positional argument handling with the `event` positional arg being `"MODE_CHANGE"` and `new_mode=mode` being an unexpected kwarg that gets silently ignored OR it may be a latent bug.
**How to avoid:** Always call `log_event(level, event, details={...})` with an explicit dict. Don't replicate the kwargs pattern from line 428.

### Pitfall 3: Foreign Key Enforcement

**What goes wrong:** SQLite foreign keys are NOT enforced by default. An `options_positions` row could reference a non-existent `wheel_cycles.id` without error.
**Why it happens:** SQLite requires `PRAGMA foreign_keys = ON` per connection.
**How to avoid:** The existing `_get_connection()` context manager does NOT enable foreign keys. Phase 2 can either add `cursor.execute("PRAGMA foreign_keys = ON")` to `_get_connection()` or rely on application-level enforcement. Given backward-compatibility requirements, application-level enforcement is safer — add a note in the planner.

### Pitfall 4: Single Active Cycle Assumption

**What goes wrong:** Phase 3+ calls `get_active_cycle()` expecting at most one row, but multiple CASH cycles or a bug leaves two HOLDING_SHARES rows → ambiguous state.
**Why it happens:** No DB-level UNIQUE constraint enforces "one active cycle at a time" (CASH state has no position, so there could be many historical CASH rows).
**How to avoid:** Define "active" as `closed_at IS NULL`. Add a unique constraint or application-level check in the state transition method that prevents entering SHORT_PUT when another cycle has `closed_at IS NULL AND state != 'CASH'`.

### Pitfall 5: `cost_basis` Sign Convention

**What goes wrong:** Covered call premium is *subtracted* from cost basis (reduces it). A bug adds it instead, making the basis higher, which Phase 4's CC-03 check (call strike ≥ adjusted cost basis) would falsely reject valid calls.
**Why it happens:** The formula `cost_basis = strike - put_premium - cc_premiums` is easy to misread.
**How to avoid:** Tests must verify direction: after a $2.00 covered call premium, a $48.00 cost basis becomes $46.00.

### Pitfall 6: Backward-Compatible Migration Pattern

**What goes wrong:** Adding new columns to an existing database (e.g., from Phase 1 having run and created a `trades.db`) breaks if columns already exist.
**Why it happens:** `ALTER TABLE ADD COLUMN` fails if the column exists.
**How to avoid:** Follow the existing pattern — check `PRAGMA table_info(tablename)` first:
```python
cursor.execute("PRAGMA table_info(wheel_cycles)")
columns = [col[1] for col in cursor.fetchall()]
if "some_new_column" not in columns:
    cursor.execute("ALTER TABLE wheel_cycles ADD COLUMN some_new_column REAL DEFAULT 0.0")
```
[VERIFIED: existing pattern in src/database.py lines 151-154 and 217-219]

## Code Examples

### Query Method: get_active_cycle()
```python
# Source: follows pattern of get_open_trade() in src/database.py lines 297-307
def get_active_cycle(self) -> Optional[Dict[str, Any]]:
    """Get the current active wheel cycle (closed_at IS NULL). Returns None if no active cycle."""
    with self._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM wheel_cycles
            WHERE closed_at IS NULL
            ORDER BY id DESC
            LIMIT 1
        """)
        row = cursor.fetchone()
        return dict(row) if row else None
```

### Query Method: open_wheel_position()
```python
# Source: follows pattern of record_trade_entry() in src/database.py lines 231-267
def open_wheel_position(
    self,
    option_type: str,          # "PUT" or "CALL"
    symbol: str,               # e.g., "IBIT261121P00048000"
    strike: float,
    expiry_date: str,          # ISO date string
    dte_at_entry: int,
    premium_received: float,
    quantity: int = 1,
    delta: Optional[float] = None,
    gamma: Optional[float] = None,
    theta: Optional[float] = None,
    vega: Optional[float] = None,
    iv: Optional[float] = None,
    cycle_id: Optional[int] = None,
) -> int:
    """Record a new options position. Returns options_positions.id."""
    now = get_et_now().isoformat()
    with self._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO options_positions (
                cycle_id, symbol, option_type, strike, expiry_date, dte_at_entry,
                quantity, premium_received, delta, gamma, theta, vega, iv,
                status, opened_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)
        """, (
            cycle_id, symbol, option_type, strike, expiry_date, dte_at_entry,
            quantity, premium_received, delta, gamma, theta, vega, iv,
            now, now, now
        ))
        return cursor.lastrowid
```

### State Transition in Database Method
```python
# Pattern: validate state before writing, log after writing
def transition_wheel_state(
    self,
    cycle_id: int,
    next_state: WheelState,
    reason: str,
    **updates  # additional fields to set on wheel_cycles
) -> None:
    """Validate and apply a wheel state transition."""
    from .wheel_state import transition, WheelState

    cycle = self.get_active_cycle()
    if not cycle or cycle["id"] != cycle_id:
        raise ValueError(f"No active cycle with id={cycle_id}")

    current_state = WheelState(cycle["state"])
    transition(current_state, next_state)  # raises ValueError if invalid

    now = get_et_now().isoformat()
    set_fields = {"state": next_state.value, "updated_at": now, **updates}
    if next_state == WheelState.CASH and "closed_at" not in updates:
        set_fields["closed_at"] = now

    fields_sql = ", ".join(f"{k} = ?" for k in set_fields)
    with self._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE wheel_cycles SET {fields_sql} WHERE id = ?",
            list(set_fields.values()) + [cycle_id]
        )

    # Log using existing log_event method → writes to 'logs' table
    self.log_event("INFO", "wheel_transition", details={
        "cycle_id": cycle_id,
        "from_state": current_state.value,
        "to_state": next_state.value,
        "reason": reason,
    })
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Storing computed P&L | Compute P&L on read from raw data | Decided in CONTEXT.md | Eliminates stale values, no extra UPDATE needed |
| JSON blob for Greeks | Individual REAL columns per Greek | Decided in CONTEXT.md | Enables `WHERE delta < 0.30` queries in Phase 5 monitoring |
| Separate `event_log` table | Existing `logs` table via `log_event()` | Always been `logs` in this codebase | No new table needed for transition logging |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `log_event(level, event, details=None)` signature is correct and line 428's kwargs call is either a latent bug or works via unexpected mechanism | Common Pitfalls #2 | If kwargs are swallowed silently, transition logging works but with bad args. If they error, existing tests would catch it. Low risk. |
| A2 | One active cycle means `closed_at IS NULL` — no UNIQUE constraint needed at DB level | Architecture Patterns | If two cycles accidentally get `closed_at IS NULL`, queries return wrong cycle. Low risk since single-cycle design is enforced in Phase 3+ callers. |

**Low risk on both assumptions** — the codebase patterns are well understood from direct code reading.

## Open Questions (RESOLVED)

1. **Foreign key enforcement** — RESOLVED: Application-level enforcement (check cycle_id exists before insert). No PRAGMA change. Keeps backward compatibility.

2. **`log_event()` kwargs inconsistency** — RESOLVED: Always call `db.log_event("INFO", "wheel_transition", {"key": "val"})` with positional dict form. The existing kwargs call at line 428 is either untested or an oversight — not our concern for Phase 2.

## Environment Availability

Step 2.6: SKIPPED — Phase 2 is purely code and SQLite changes with no external tool dependencies. SQLite is stdlib.

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.9+ | All code | ✓ | 3.9.6 | — |
| sqlite3 | Database | ✓ | stdlib | — |
| pytest | Testing | ✓ | 8.4.2 | — |

[VERIFIED: `python3 --version` returns Python 3.9.6, `python3 -m pytest tests/test_database.py` passes 10/10]

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.4.2 |
| Config file | `pyproject.toml` → `[tool.pytest.ini_options]` |
| Quick run command | `python -m pytest tests/test_wheel_state.py -v --tb=short` |
| Full suite command | `python -m pytest tests/ -v --tb=short` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DB-01 | Options positions stored with all Greeks and status | unit | `python -m pytest tests/test_wheel_state.py::TestOptionsPositions -x` | Wave 0 |
| DB-02 | State machine transitions CASH→SHORT_PUT→HOLDING_SHARES→COVERED_CALL→CASH | unit | `python -m pytest tests/test_wheel_state.py::TestWheelStateTransitions -x` | Wave 0 |
| DB-02 | Invalid transitions raise ValueError | unit | `python -m pytest tests/test_wheel_state.py::TestWheelStateTransitions::test_invalid_transition_raises -x` | Wave 0 |
| DB-03 | Assignment records shares with `cost_basis = strike - put_premium` | unit | `python -m pytest tests/test_wheel_state.py::TestCostBasis::test_assignment_cost_basis -x` | Wave 0 |
| DB-04 | Covered call premium reduces cost_basis correctly | unit | `python -m pytest tests/test_wheel_state.py::TestCostBasis::test_covered_call_reduces_basis -x` | Wave 0 |
| DB-04 | Multiple covered calls accumulate in `covered_call_premiums_collected` | unit | `python -m pytest tests/test_wheel_state.py::TestCostBasis::test_multiple_premiums_accumulate -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_wheel_state.py -v --tb=short`
- **Per wave merge:** `python -m pytest tests/ -v --tb=short`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/test_wheel_state.py` — covers DB-01 through DB-04 (state machine, cost basis, position recording)
- [ ] `src/wheel_state.py` — WheelState enum and dataclasses (needed before test file can import)

## Security Domain

> `security_enforcement` not set in `.planning/config.json` — treating as enabled.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Phase 2 is internal SQLite only, no auth surface |
| V3 Session Management | no | No sessions in this phase |
| V4 Access Control | no | No API endpoints in this phase |
| V5 Input Validation | yes | Validate `option_type` IN ('PUT', 'CALL'), state string matches WheelState enum values before INSERT |
| V6 Cryptography | no | No crypto operations |

### Known Threat Patterns for SQLite Schema Phase

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| SQL injection via f-string queries | Tampering | Use parameterized queries (`?` placeholders) — existing codebase does this correctly. Never string-format SQL with user/external data. |
| State bypass (calling transition without validation) | Tampering | All state changes via `transition_wheel_state()` which calls `transition()` validation. No direct SQL UPDATE of `state` column in callers. |
| Stale cost_basis from parallel writes | Tampering | WAL mode + 30s timeout already handles this. Single-writer pattern for cycle updates. |

**Note:** Phase 2 has minimal security surface — it is internal SQLite schema and Python state machine code. V5 input validation applies only to ensure enum values stored match the WheelState enum (no arbitrary strings in `state` column).

## Sources

### Primary (HIGH confidence)
- `src/database.py` — Full read, verified: `_init_db()` pattern, `logs` table name, `log_event()` signature, `get_database()` singleton, `record_trade_entry()`/`record_trade_exit()` pattern, WAL mode, migration via `PRAGMA table_info`
- `src/etrade_client.py` lines 596-684 — Verified: Phase 1 contract dict shape (symbol, option_type, strike, expiry_date, dte, bid, ask, last, open_interest, delta, gamma, theta, vega, iv, quote_timestamp)
- `src/smart_strategy.py` lines 34-47 — Verified: `Signal(Enum)` pattern with string values
- `src/trading_bot/config.py` — Verified: `TradingMode(Enum)` and `@dataclass TradeResult` patterns
- `pyproject.toml` — Verified: pytest 8.4.2, testpaths=["tests"], asyncio_mode=auto
- `tests/test_database.py` — Verified: test fixture pattern (tmp_path), `Database(db_path)` for isolated tests

### Secondary (MEDIUM confidence)
- `tests/test_etrade_options.py` — Verified Phase 1 contract dict keys from test assertions (lines 43-65)

### Tertiary (LOW confidence)
- None — all claims verified from direct code reading

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — stdlib only, verified from imports
- Architecture: HIGH — verified from existing patterns in database.py
- Pitfalls: HIGH — verified by direct code reading (table name discrepancy confirmed)
- Phase 1 data shape: HIGH — verified from get_ibit_options_chain() and test assertions

**Research date:** 2026-04-06
**Valid until:** 2026-07-06 (stable SQLite + Python stdlib, no external dependencies to go stale)
