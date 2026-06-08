---
phase: 3
slug: cash-secured-put-cycle
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-08
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | pytest.ini / pyproject.toml |
| **Quick run command** | `python -m pytest tests/ -k "wheel or put or assignment" -x -q` |
| **Full suite command** | `python -m pytest tests/ -v` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/ -k "wheel or put or assignment" -x -q`
- **After each plan completion:** Run `python -m pytest tests/ -v`
- **Before verification:** Run full suite + manual Telegram interaction test

---

## Validation Architecture

### Signal Generation Tests
- WheelStrategy.get_put_signal() returns signal on >=2% pullback
- WheelStrategy.get_put_signal() returns None on <2% pullback
- WheelStrategy.get_put_signal() returns None when active cycle exists

### Strike Selection Tests
- Filter contracts to 0.20-0.30 abs(delta) range
- Select highest premium in delta range
- Handle empty chain (no contracts in range)
- Handle negative delta values correctly

### Telegram Approval Tests
- Approve flow places order via preview → place
- Reject flow cancels without placing order
- Adjust flow shows alternatives and allows re-selection
- Collateral validation blocks insufficient capital

### Assignment Detection Tests
- Detect assignment when put disappears and IBIT shares appear
- Detect OTM expiry when put disappears and no shares appear
- Idempotent: running detection twice doesn't double-record
- Correct cost basis calculation on assignment

### Integration Tests
- Full flow: signal → suggest → approve → order placement
- Full flow: expiry → assignment detection → cycle state transition
- Full flow: OTM expiry → position close → cycle back to CASH
