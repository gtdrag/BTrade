---
phase: 06-transition-user-interface
plan: 02
subsystem: telegram-ui
tags: [telegram, wheel-strategy, scheduler, commands]
dependency_graph:
  requires:
    - 06-01  # wheel_mode_enabled DB column and intraday gating
  provides:
    - WheelCommandsMixin with /wheel and /wheelmode handlers
    - Daily wheel position summary job at 4:30 PM ET
  affects:
    - src/telegram/bot.py
    - src/smart_scheduler.py
tech_stack:
  added: []
  patterns:
    - Telegram command mixin pattern (WheelCommandsMixin follows TradingCommandsMixin)
    - Module-level import for patchability in tests (get_database, get_et_now at top of wheel_commands.py)
key_files:
  created:
    - src/telegram/wheel_commands.py
  modified:
    - src/telegram/bot.py
    - src/telegram/__init__.py
    - src/smart_scheduler.py
    - tests/test_wheel_ui.py
decisions:
  - Import get_database and get_et_now at module level in wheel_commands.py (not inside methods) to make them patchable via unittest.mock.patch
  - WheelCommandsMixin placed last in TelegramBot MRO to avoid shadowing existing mixins
metrics:
  duration: ~20 minutes
  completed: 2026-04-08
  tasks_completed: 2
  files_modified: 5
---

# Phase 06 Plan 02: Wheel Telegram UI Summary

**One-liner:** Telegram /wheel and /wheelmode commands plus 4:30 PM wheel-only daily summary job via WheelCommandsMixin.

## What Was Built

### Task 1: WheelCommandsMixin (TR-02, TR-03)

New file `src/telegram/wheel_commands.py` provides two commands:

**`/wheel`** — shows the current active wheel cycle or "No active wheel cycle." when none exists. For an active cycle, displays: state name, cost basis per share, total premium collected (put + covered call premiums * 100 shares), and for each open options position: type, strike, expiry, DTE, delta (labeled as entry value), P&L if current_value available, and premium received.

**`/wheelmode`** — toggles the `wheel_mode_enabled` flag in the database:
- `/wheelmode on` → sets `wheel_mode_enabled=1`, replies with ENABLED confirmation
- `/wheelmode off` → sets `wheel_mode_enabled=0`, replies with DISABLED confirmation
- `/wheelmode` (no args) → shows current ON/OFF status without modifying DB
- `/wheelmode <invalid>` → shows Usage: message without modifying DB

Both handlers call `_is_authorized(update)` before any DB access. The no-args guard prevents IndexError (T-06-05). Arg validation restricts DB writes to "on"/"off" only (T-06-04).

`WheelCommandsMixin` is integrated into `TelegramBot` class hierarchy and both handlers are registered in `initialize()`. `WheelCommandsMixin` is exported from `src/telegram/__init__.py`.

### Task 2: Daily Wheel Position Summary (TR-05)

New method `_job_wheel_daily_summary` in `SmartScheduler`:
- Skips on non-trading days (returns early)
- Sends "No active wheel positions today." when no active cycle
- Sends formatted summary with cycle state, cost basis, total premium, and per-position details (type, strike, DTE, max risk = strike * 100, premium received)
- Registered in `setup_jobs()` with `CronTrigger(day_of_week="mon-fri", hour=16, minute=30)` — 30 minutes after the existing 4:00 PM intraday summary
- Exception-safe with logger.error and `_error_count` increment

## Tests

21 tests in `tests/test_wheel_ui.py` (9 from Plan 01 + 12 new):

**TestWheelCommand (3 tests):**
- `test_wheel_cmd_no_cycle` — no active cycle returns "No active wheel cycle."
- `test_wheel_cmd_active_cycle` — active cycle reply contains state, cost basis, DTE, premium
- `test_wheel_cmd_unauthorized` — unauthorized user rejected, DB not accessed

**TestWheelModeCommand (4 tests):**
- `test_wheelmode_on` — updates DB with wheel_mode_enabled=1, reply contains "ENABLED"
- `test_wheelmode_off` — updates DB with wheel_mode_enabled=0, reply contains "DISABLED"
- `test_wheelmode_no_args` — shows current status, DB not updated
- `test_wheelmode_invalid_arg` — reply contains "Usage:", DB not updated
- `test_wheelmode_unauthorized` — rejected before DB access

**TestWheelDailySummary (4 tests):**
- `test_wheel_summary_skips_non_trading_day` — _send_notification not called
- `test_wheel_summary_no_cycle` — sends "No active wheel positions today."
- `test_wheel_summary_with_cycle` — message contains PUT, DTE, max_risk, premium
- `test_wheel_summary_job_registered` — "wheel_daily_summary" id found in setup_jobs()

Full suite: 373 tests pass, 0 failures.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Module-level imports required for mock.patch patchability**
- **Found during:** Task 1 GREEN phase — `test_wheel_cmd_no_cycle` failed with `AttributeError: module 'src.telegram.wheel_commands' does not have the attribute 'get_database'`
- **Issue:** Plan code sketch used lazy inline imports (`from ..database import get_database` inside method body). `unittest.mock.patch("src.telegram.wheel_commands.get_database")` requires the name to be bound at module level.
- **Fix:** Moved `from ..database import get_database` and `from ..utils import get_et_now` to module-level imports in `wheel_commands.py`. Removed duplicate inline imports from method bodies.
- **Files modified:** `src/telegram/wheel_commands.py`
- **Commit:** 186e94e

None other — plan executed as written after this fix.

## Threat Mitigations Applied

All T-06-0x mitigations from the plan's threat register were implemented:

| Threat | Mitigation | Location |
|--------|-----------|----------|
| T-06-03 (EoP) | `_is_authorized(update)` at top of both handlers | wheel_commands.py lines 30, 92 |
| T-06-04 (Tampering) | `arg not in ("on", "off")` guard before DB write | wheel_commands.py line 108 |
| T-06-05 (DoS) | `if not args:` guard before `args[0]` | wheel_commands.py line 100 |
| T-06-06 (Info Disclosure) | Accepted — data only visible to authorized chat_id | N/A |

## Known Stubs

None — all data is live from `get_active_cycle()` and `get_cycle_positions()`. Greeks shown are labeled as entry values (per Research pitfall 6).

## Threat Flags

None — no new network endpoints, auth paths, or trust boundaries introduced beyond what the plan's threat model covers.

## Self-Check: PASSED

- `src/telegram/wheel_commands.py` exists: FOUND
- `src/telegram/bot.py` contains WheelCommandsMixin: FOUND (line 28 import, line 44 hierarchy)
- `src/smart_scheduler.py` contains `_job_wheel_daily_summary`: FOUND (line 1493 def, line 252 registration)
- Commits exist: b8b1ef7 (tests), 186e94e (Task 1), 0b21fb9 (Task 2) — all confirmed in git log
- 373/373 tests pass
