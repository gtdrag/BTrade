---
phase: 4
slug: covered-call-cycle
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-08
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | pyproject.toml |
| **Quick run command** | `python3 -m pytest tests/ -k "call or covered or cycle" -x -q` |
| **Full suite command** | `python3 -m pytest tests/ -v` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python3 -m pytest tests/ -k "call or covered or cycle" -x -q`
- **After each plan completion:** Run `python3 -m pytest tests/ -v`
- **Before verification:** Run full suite + manual Telegram interaction test

---

## Validation Architecture

### Call Signal Generation Tests
- WheelStrategy.get_call_signal() returns signal when cycle is HOLDING_SHARES
- WheelStrategy.get_call_signal() returns None when no active cycle
- WheelStrategy.get_call_signal() returns None when cycle not in HOLDING_SHARES
- select_call_strike() filters to 0.25-0.35 delta range
- select_call_strike() only returns strikes at or above cost basis
- select_call_strike() returns None when no strikes above cost basis

### Telegram Approval Tests
- Approve flow places sell-to-open call order
- Reject flow cancels without placing order
- Adjust flow shows alternatives above cost basis
- Cost basis protection: strikes below cost basis are filtered out
- Separate _call_approval_event (no collision with put/intraday)

### Call-Away Detection Tests
- Detect call-away when call disappears and IBIT shares disappear
- Detect OTM call expiry when call disappears but shares remain
- On call-away: transition to CASH, record full-cycle P&L
- On OTM expiry: stay HOLDING_SHARES, suggest new call
- Idempotent detection (no double-recording)

### Integration Tests
- Full cycle: assignment → call suggestion → approve → call-away → CASH
- Full cycle P&L calculation matches expected formula
- Cycle history queryable from database
