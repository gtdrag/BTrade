---
phase: 2
slug: options-database-state-management
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-07
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | pyproject.toml |
| **Quick run command** | `python -m pytest tests/test_wheel_state.py -v` |
| **Full suite command** | `python -m pytest tests/ -v` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/test_wheel_state.py -v`
- **After every plan wave:** Run `python -m pytest tests/ -v`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 02-01-01 | 01 | 1 | DB-02 | — | N/A | unit | `python -m pytest tests/test_wheel_state.py -k "TestWheelState" -v` | ❌ W0 | ⬜ pending |
| 02-01-02 | 01 | 1 | DB-01 | — | N/A | unit | `python -m pytest tests/test_wheel_state.py -k "TestDatabaseSchema" -v` | ❌ W0 | ⬜ pending |
| 02-02-01 | 02 | 2 | DB-01,DB-02,DB-03,DB-04 | — | N/A | unit | `python -m pytest tests/test_wheel_state.py -k "TestWheelCycle or TestCostBasis" -v` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_wheel_state.py` — stubs for DB-01, DB-02, DB-03, DB-04
- [ ] Test fixtures for Database with tmp_path isolation

*Existing infrastructure covers test runner — only test file needs creation.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| None | — | — | — |

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
