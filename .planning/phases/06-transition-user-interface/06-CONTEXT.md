# Phase 6: Transition & User Interface - Context

**Gathered:** 2026-04-09
**Status:** Ready for planning

<domain>
## Phase Boundary

Replace intraday strategies with wheel mode, add Telegram commands for wheel visibility and control, build Streamlit dashboard for options positions, and add daily position summary. This is the final phase — completes the v1.0 milestone.

</domain>

<decisions>
## Implementation Decisions

### Wheel Mode Toggle & Strategy Switching
- Persist wheel mode in `bot_state` table with key `wheel_mode_enabled`. Default: enabled.
- Check `wheel_mode_enabled` in SmartScheduler before running intraday jobs. If enabled, skip mean reversion, crash day, pump day, 10am dump.
- Don't force close existing intraday positions on toggle. Let them exit normally at 3:55 PM.
- Mutually exclusive: wheel on = intraday off, and vice versa.

### Telegram Commands & Daily Summary
- `/wheel` command shows: current cycle state, active positions with Greeks, cost basis, DTE, P&L, total premium. If no cycle: "No active wheel cycle."
- `/wheelmode on` and `/wheelmode off` subcommands to toggle. Confirmation message. Updates bot_state.
- Daily 4:30 PM ET summary: all active positions with DTE, P&L, max risk, total premium. New scheduler job. If no positions: "No active wheel positions today."
- Daily summary is wheel-only (no intraday recap).

### Streamlit Dashboard
- New "Wheel Strategy" section in existing `app.py`. Alongside existing dashboard content.
- 3 panels: Active Positions table (Greeks, DTE, P&L), Cycle State (current state + progress), Premium Tracker (total premium, history chart).
- Auto-refresh via `st.rerun()` every 60 seconds during market hours.
- Cycle history table: completed cycles with dates, premiums, P&L, annualized return. Uses `get_cycle_history()`.

### Claude's Discretion
- Exact Streamlit layout and styling
- Telegram message formatting for /wheel command
- How to determine "market hours" for auto-refresh
- Error handling for missing data in dashboard

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `app.py` — existing Streamlit dashboard (44K)
- `SmartScheduler.setup_jobs()` — job registration pattern
- `Database.get_active_cycle()` / `get_cycle_history()` / `get_cycle_positions()` — all queries exist
- `TelegramBot` — command handler registration pattern in `src/telegram/handlers.py`
- `Database.get_bot_state()` / `set_bot_state()` — bot_state CRUD exists

### Established Patterns
- Telegram command handlers registered in `src/telegram/handlers.py`
- Streamlit layout with `st.columns()`, `st.metric()`, `st.dataframe()`
- Bot state persisted in `bot_state` table
- `get_et_now()` for timestamps
- APScheduler `CronTrigger` for scheduled jobs

### Integration Points
- SmartScheduler: gate intraday jobs behind `wheel_mode_enabled` check
- Telegram: register `/wheel` and `/wheelmode` command handlers
- Streamlit: add wheel strategy section to `app.py`
- SmartScheduler: add 4:30 PM daily summary job

</code_context>

<specifics>
## Specific Ideas

- Keep intraday code intact — just gate it. Don't delete anything.
- Streamlit section should be visually distinct from intraday sections
- `/wheel` output should be scannable at a glance

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 06-transition-user-interface*
*Context gathered: 2026-04-09 via smart discuss*
