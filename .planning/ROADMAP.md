# Roadmap: BTrade Wheel Strategy

## Overview

Transform BTrade from an intraday directional trading bot into a systematic IBIT options income generator using the wheel strategy. The journey starts with E*TRADE options API integration, builds the database foundation for tracking complex multi-state positions, implements the full wheel cycle (cash-secured puts → assignment detection → covered calls), adds profit optimization through 50% profit-taking and defensive rolling, and culminates in user transition from intraday strategies to the wheel with full visibility and control.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: E*TRADE Options API Integration** - Connect to E*TRADE options endpoints and validate data quality
- [ ] **Phase 2: Options Database & State Management** - Persist options positions and wheel cycle states with accurate cost basis
- [ ] **Phase 3: Cash-Secured Put Cycle** - Sell signal-based puts and detect assignments
- [ ] **Phase 4: Covered Call Cycle** - Complete full wheel cycle by writing covered calls after assignment
- [ ] **Phase 5: Profit Management** - Optimize returns through profit-taking and defensive rolling
- [ ] **Phase 6: Transition & User Interface** - Replace intraday strategies with wheel and enable user control

## Phase Details

### Phase 1: E*TRADE Options API Integration
**Goal**: Bot can reliably fetch IBIT options chains with Greeks and place options orders via E*TRADE
**Depends on**: Nothing (first phase)
**Requirements**: API-01, API-02, API-03, API-04
**Success Criteria** (what must be TRUE):
  1. Bot retrieves IBIT options chains with delta, gamma, theta, vega, and IV from E*TRADE
  2. Bot places a sell-to-open put order in paper mode and receives confirmation number
  3. Bot queries current options positions from E*TRADE portfolio API showing contract details
  4. Bot rejects stale quotes (older than 60 seconds) before suggesting trades
  5. All E*TRADE options responses are validated against expected schema without errors
**Plans:** 2 plans

Plans:
- [x] 01-01-PLAN.md — Options chain fetch with freshness validation and mock Greeks (API-01, API-04)
- [x] 01-02-PLAN.md — Options order flow (preview/place) and positions query (API-02, API-03)

### Phase 2: Options Database & State Management
**Goal**: Options positions and wheel cycles are persistently tracked with accurate cost basis across assignments
**Depends on**: Phase 1
**Requirements**: DB-01, DB-02, DB-03, DB-04
**Success Criteria** (what must be TRUE):
  1. Sold options are stored in database with entry Greeks, premium received, and expiration date
  2. Wheel cycle state machine transitions correctly (CASH → SHORT_PUT → HOLDING_SHARES → COVERED_CALL → CASH)
  3. Assigned shares appear in database with cost basis calculated as strike price minus premium received
  4. When covered call premium is collected, adjusted cost basis decreases correctly in database
  5. Database queries return accurate cycle status and P&L at any point in the wheel
**Plans:** 2 plans

Plans:
- [x] 02-01-PLAN.md — WheelState enum, transition validation, dataclasses, and database schema (DB-01, DB-02)
- [ ] 02-02-PLAN.md — Wheel cycle and options position CRUD methods with cost basis tracking (DB-01, DB-02, DB-03, DB-04)

### Phase 3: Cash-Secured Put Cycle
**Goal**: Bot can sell cash-secured puts with signal-based entry and automatically detect option assignments
**Depends on**: Phase 2
**Requirements**: CSP-01, CSP-02, CSP-03, CSP-04, CSP-05, CSP-06, ASGN-01, ASGN-02, ASGN-03
**Success Criteria** (what must be TRUE):
  1. Bot sends Telegram message suggesting a put trade only when IBIT pullback signal fires
  2. Suggested put strike is within 0.20-0.30 delta range and expiration is 30-45 days out
  3. User can approve, adjust strike/expiration, or reject the suggested put via Telegram buttons
  4. Bot validates sufficient cash collateral (strike price × 100) before allowing put execution
  5. Bot detects assignment by 9 AM ET the Monday after expiration and sends Telegram notification
  6. Assignment notification shows shares acquired and adjusted cost basis
**Plans:** 3 plans

Plans:
- [x] 03-01-PLAN.md — WheelStrategy class with signal generation, strike selection, and cash validation (CSP-01, CSP-02, CSP-03, CSP-06)
- [x] 03-02-PLAN.md — Telegram put approval flow and scheduler put signal job (CSP-04, CSP-05)
- [x] 03-03-PLAN.md — Assignment detection, OTM expiry handling, and scheduler wiring (ASGN-01, ASGN-02, ASGN-03)

### Phase 4: Covered Call Cycle
**Goal**: Bot completes full wheel cycle by automatically suggesting covered calls after put assignment
**Depends on**: Phase 3
**Requirements**: CC-01, CC-02, CC-03, CC-04
**Success Criteria** (what must be TRUE):
  1. Within 1 hour of detecting assignment, bot sends Telegram message suggesting covered call parameters
  2. Suggested call strike is 0.25-0.35 delta and at or above adjusted cost basis
  3. Bot prevents user from approving call strikes below adjusted cost basis with warning message
  4. When shares are called away, wheel cycle state returns to CASH and full-cycle P&L is recorded
  5. User can see complete cycle history in database (put entry → assignment → call entry → call away → profit)
**Plans:** 2 plans

Plans:
- [x] 04-01-PLAN.md — CallSignal dataclass, call strike selection with cost basis protection, and call-away/OTM detection (CC-01, CC-03, CC-04)
- [x] 04-02-PLAN.md — Telegram call approval flow, callback routing, and scheduler wiring for call suggestions (CC-02, CC-03, CC-04)

### Phase 5: Profit Management
**Goal**: Bot optimizes wheel returns through 50% profit-taking, defensive rolling, and expiration monitoring
**Depends on**: Phase 4
**Requirements**: PM-01, PM-02, PM-03, PM-04, PM-05
**Success Criteria** (what must be TRUE):
  1. Bot checks option P&L every 30 minutes during market hours
  2. Bot sends Telegram buy-to-close suggestion when option reaches 50% profit
  3. Bot suggests defensive roll with new strike/expiration when position tested (price near strike)
  4. Bot prevents rolls that would result in net debit or exceed 2 rolls per position
  5. Bot sends Telegram alert at 21 DTE warning of approaching expiration
**Plans:** 2 plans

Plans:
- [x] 05-01-PLAN.md — DB migration, monitoring methods, scheduler 30-min job (PM-01)
- [x] 05-02-PLAN.md — Telegram BTC/roll approval flows and DTE alerts (PM-02, PM-03, PM-04, PM-05)

### Phase 6: Transition & User Interface
**Goal**: Wheel strategy replaces intraday strategies with full user visibility, control, and daily position reporting
**Depends on**: Phase 5
**Requirements**: TR-01, TR-02, TR-03, TR-04, TR-05
**Success Criteria** (what must be TRUE):
  1. When wheel mode is enabled, intraday BITU/SBIT strategies do not fire signals
  2. User can send `/wheel` in Telegram and see current cycle state, positions, cost basis, and DTE
  3. User can toggle wheel mode on/off via Telegram command
  4. Streamlit dashboard displays options positions with Greeks, wheel cycle state, and total premium collected
  5. User receives Telegram summary at 4:30 PM ET daily showing positions, max risk, and days to expiration
**Plans:** 3 plans

Plans:
- [x] 06-01-PLAN.md — DB migration for wheel_mode_enabled and intraday job gating (TR-01)
- [x] 06-02-PLAN.md — Telegram /wheel, /wheelmode commands and 4:30 PM daily summary job (TR-02, TR-03, TR-05)
- [x] 06-03-PLAN.md — Streamlit wheel strategy dashboard section with auto-refresh (TR-04)

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. E*TRADE Options API Integration | 0/2 | Planning complete | - |
| 2. Options Database & State Management | 0/2 | Planning complete | - |
| 3. Cash-Secured Put Cycle | 0/3 | Planning complete | - |
| 4. Covered Call Cycle | 0/2 | Planning complete | - |
| 5. Profit Management | 0/2 | Planning complete | - |
| 6. Transition & User Interface | 0/3 | Planning complete | - |
