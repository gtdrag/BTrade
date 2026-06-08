# Phase 2: Options Database & State Management - Context

**Gathered:** 2026-04-07
**Status:** Ready for planning

<domain>
## Phase Boundary

Persist options positions and wheel cycle states in SQLite with accurate cost basis tracking across assignments. New tables for options positions and wheel cycles. State machine for cycle transitions. No Telegram integration, no trade execution logic — those are later phases.

</domain>

<decisions>
## Implementation Decisions

### Wheel Cycle State Machine
- Python Enum states with transition validation: `WheelState(Enum)` with CASH, SHORT_PUT, HOLDING_SHARES, COVERED_CALL
- A `transition()` method validates legal state transitions and raises on invalid ones
- New `wheel_cycles` table in SQLite via `_init_db()` — follows existing database pattern
- Single cycle only (IBIT-only per requirements) — one active cycle at a time
- State transitions logged to existing `event_log` table with `event_type='wheel_transition'`

### Cost Basis Tracking
- Running calculation on the cycle record: `cost_basis = strike_price - put_premium_received - covered_call_premiums_collected`
- Updated on each premium event (put sold, call sold, position closed)
- No commissions tracking (E*TRADE commissions are $0 for options)
- P&L computed on read (derived from cycle data), not stored — avoids stale values
- Put expires worthless: full premium is profit, cycle returns to CASH, record `realized_pnl = premium_received`

### Options Position Storage
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

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/database.py` — Database singleton with `_init_db()` for schema, `get_database()` factory
- `src/database.py` — Existing tables: `trades`, `bot_state`, `event_log`
- `src/database.py` — `NumpyJSONEncoder` and `safe_json_dumps()` for serialization
- Phase 1 output: `ETradeClient.get_ibit_options_chain()` returns contract dicts with all 5 Greeks

### Established Patterns
- Schema in `_init_db()` with `CREATE TABLE IF NOT EXISTS`
- Singleton via `get_database()` factory
- Module-level `logger = logging.getLogger(__name__)`
- Dataclasses for structured data (`Signal`, `TodaySignal`, `BotConfig`, `StrategyConfig`)
- Enums with `.value` for string representation in logs/JSON

### Integration Points
- New tables created in `src/database.py` `_init_db()` method
- New query methods on `Database` class
- Phase 3+ will call these methods to record put sales, assignments, call sales

</code_context>

<specifics>
## Specific Ideas

- Follow existing `record_trade_entry()` / `record_trade_exit()` pattern for options position recording
- Use `get_et_now()` for all timestamps (per CLAUDE.md rules)
- Wheel cycle queries should return both current state and full history

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 02-options-database-state-management*
*Context gathered: 2026-04-07 via smart discuss*
