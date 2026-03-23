# Requirements: BTrade Wheel Strategy

**Defined:** 2026-03-18
**Core Value:** Systematically generate income from IBIT options by running the wheel strategy with disciplined entry signals and semi-automated execution

## v1.0 Requirements

Requirements for wheel strategy milestone. Each maps to roadmap phases.

### E*TRADE Options API

- [ ] **API-01**: Bot can fetch IBIT options chains with Greeks (delta, gamma, theta, vega, IV) from E*TRADE
- [ ] **API-02**: Bot can preview and place options orders (sell-to-open puts, sell-to-open calls, buy-to-close) via E*TRADE
- [ ] **API-03**: Bot can query options positions from E*TRADE portfolio API
- [ ] **API-04**: Bot validates quote freshness (<60s) before using options prices for orders

### Database & State

- [ ] **DB-01**: Options positions are persisted with contract details, Greeks at entry, premium, and status
- [ ] **DB-02**: Wheel cycles are tracked with state machine (CASH → SHORT_PUT → HOLDING_SHARES → COVERED_CALL)
- [ ] **DB-03**: Assigned shares are recorded with adjusted cost basis (strike price minus premium received)
- [ ] **DB-04**: Cost basis is updated when additional premiums are collected (covered call premium reduces basis)

### Cash-Secured Puts

- [ ] **CSP-01**: Bot identifies favorable put entry conditions (IBIT pullback, elevated IV, near support)
- [ ] **CSP-02**: Bot selects put strike using delta targeting (0.20-0.30 delta, configurable)
- [ ] **CSP-03**: Bot selects expiration in 30-45 DTE range (configurable parameter)
- [ ] **CSP-04**: Bot sends Telegram message with suggested put trade (strike, expiration, premium, Greeks) for user approval
- [ ] **CSP-05**: User can approve, adjust, or reject suggested put trade via Telegram
- [ ] **CSP-06**: Bot validates sufficient cash to secure the put before suggesting trade (strike × 100 shares)

### Assignment Detection

- [ ] **ASGN-01**: Bot detects option assignment by comparing E*TRADE positions to database state daily (8 AM ET)
- [ ] **ASGN-02**: Bot sends Telegram notification when assignment is detected with details (shares acquired, cost basis)
- [ ] **ASGN-03**: Bot automatically suggests covered call parameters after assignment detection

### Covered Calls

- [ ] **CC-01**: Bot selects call strike using delta targeting (0.25-0.35 delta, configurable) at or above adjusted cost basis
- [ ] **CC-02**: Bot sends Telegram message with suggested covered call (strike, expiration, premium, Greeks) for user approval
- [ ] **CC-03**: Bot validates call strike is at or above adjusted cost basis to prevent locking in losses
- [ ] **CC-04**: When shares are called away, bot updates wheel cycle state back to CASH and records full-cycle P&L

### Profit Management

- [ ] **PM-01**: Bot monitors open options positions every 30 minutes during market hours
- [ ] **PM-02**: Bot suggests closing position at 50% profit via Telegram (buy-to-close)
- [ ] **PM-03**: Bot suggests defensive roll when position is tested (approaching strike) with credit-only validation
- [ ] **PM-04**: Bot monitors expiration approach and sends alerts at 21 DTE
- [ ] **PM-05**: Rolling is limited to max 2 rolls per position, enforcing credit-only (no debit rolls)

### Transition

- [ ] **TR-01**: Bot has a `wheel_enabled` mode flag that disables intraday BITU/SBIT strategies when active
- [ ] **TR-02**: Telegram command `/wheel` shows current wheel cycle status (state, positions, cost basis, DTE)
- [ ] **TR-03**: Telegram command to enable/disable wheel mode
- [ ] **TR-04**: Streamlit dashboard displays options positions, wheel cycle state, and premium collected
- [ ] **TR-05**: Bot sends daily position summary at 4:30 PM ET (positions, max risk, days to expiration)

## v2 Requirements

Deferred to future milestone. Tracked but not in current roadmap.

### Analytics

- **ANLYT-01**: Cycle performance analytics showing full-cycle returns (not just trade-by-trade)
- **ANLYT-02**: Premium income tracking over time with annualized yield calculation
- **ANLYT-03**: Win rate and average premium by delta/DTE combination

### Signal Enhancement

- **SIG-01**: IV rank filtering — only sell options when IV rank > 30th percentile
- **SIG-02**: Technical support/resistance levels for strike selection guidance
- **SIG-03**: Historical backtesting of wheel parameters on IBIT

### Edge Cases

- **EDGE-01**: Corporate actions detection for adjusted IBIT contracts
- **EDGE-02**: Early assignment warning system (deep ITM near ex-dividend)
- **EDGE-03**: After-hours significant move monitoring

## Out of Scope

| Feature | Reason |
|---------|--------|
| Multi-leg strategies (iron condors, spreads) | Different risk profile, adds complexity, wheel only for v1 |
| Multiple underlyings | IBIT only simplifies chain lookups and position management |
| Fully autonomous execution | Options trades have more nuance, require human judgment |
| Real-time Greeks dashboard | Static Greeks in Telegram approval messages sufficient |
| Intraday options trading | Wheel is a weekly/monthly strategy, not intraday |
| Mobile app | Telegram provides mobile interface |
| IV smile/skew analysis | Overkill for single-underlying wheel strategy |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| API-01 | Phase 1 | Pending |
| API-02 | Phase 1 | Pending |
| API-03 | Phase 1 | Pending |
| API-04 | Phase 1 | Pending |
| DB-01 | Phase 2 | Pending |
| DB-02 | Phase 2 | Pending |
| DB-03 | Phase 2 | Pending |
| DB-04 | Phase 2 | Pending |
| CSP-01 | Phase 3 | Pending |
| CSP-02 | Phase 3 | Pending |
| CSP-03 | Phase 3 | Pending |
| CSP-04 | Phase 3 | Pending |
| CSP-05 | Phase 3 | Pending |
| CSP-06 | Phase 3 | Pending |
| ASGN-01 | Phase 3 | Pending |
| ASGN-02 | Phase 3 | Pending |
| ASGN-03 | Phase 3 | Pending |
| CC-01 | Phase 4 | Pending |
| CC-02 | Phase 4 | Pending |
| CC-03 | Phase 4 | Pending |
| CC-04 | Phase 4 | Pending |
| PM-01 | Phase 5 | Pending |
| PM-02 | Phase 5 | Pending |
| PM-03 | Phase 5 | Pending |
| PM-04 | Phase 5 | Pending |
| PM-05 | Phase 5 | Pending |
| TR-01 | Phase 6 | Pending |
| TR-02 | Phase 6 | Pending |
| TR-03 | Phase 6 | Pending |
| TR-04 | Phase 6 | Pending |
| TR-05 | Phase 6 | Pending |

**Coverage:**
- v1 requirements: 31 total
- Mapped to phases: 31
- Unmapped: 0 ✓

---
*Requirements defined: 2026-03-18*
*Last updated: 2026-03-23 after roadmap creation*
