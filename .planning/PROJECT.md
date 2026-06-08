# BTrade

## What This Is

An automated Bitcoin ETF trading bot that executes options strategies on IBIT via E*TRADE, with Telegram-based trade approvals and notifications. Previously ran intraday directional strategies on leveraged ETFs (BITU/SBIT); now pivoting to a wheel strategy (cash-secured puts → covered calls) on IBIT.

## Core Value

Systematically generate income from IBIT options by running the wheel strategy with disciplined entry signals, automated position monitoring, and semi-automated execution via Telegram.

## Requirements

### Validated

<!-- Shipped and confirmed valuable. -->

- ✓ E*TRADE API integration (OAuth, quotes, order placement, position queries) — v0
- ✓ Telegram bot with command routing, trade approvals, notifications — v0
- ✓ Market data fallback chain (E*TRADE → Alpaca → Finnhub → Yahoo) — v0
- ✓ SQLite persistence (trades, bot state, event log, strategy params) — v0
- ✓ APScheduler-based job orchestration with market hours awareness — v0
- ✓ Paper/live trading mode toggle — v0
- ✓ Streamlit monitoring dashboard — v0
- ✓ Async/sync bridging for Telegram ↔ APScheduler — v0
- ✓ Error alerting via Telegram — v0

### Active

<!-- Current scope. Building toward these. -->

- [ ] Wheel strategy: cash-secured puts on IBIT with signal-based entry
- [ ] Wheel strategy: covered calls on assigned IBIT shares
- [ ] Full wheel cycle: put → assignment detection → call → called away → repeat
- [ ] E*TRADE options API integration (chains, options orders, Greeks)
- [ ] Strike/expiration selection algorithm (delta-based, configurable DTE)
- [ ] Telegram approval flow for options trades (suggest → approve/adjust)
- [ ] Position monitoring for assignment detection and option expiration
- [ ] Profit management: close at 50% profit, roll mechanics
- [ ] Transition: deprecate intraday BITU/SBIT strategies

### Out of Scope

<!-- Explicit boundaries. Includes reasoning to prevent re-adding. -->

- Multi-leg strategies (iron condors, spreads) — keep it simple, wheel only
- Multiple underlyings — IBIT only for v1
- Fully autonomous execution — semi-automated requires Telegram approval
- Real-time Greeks monitoring dashboard — not needed for basic wheel

## Context

- Existing codebase is a working intraday bot with 5 directional strategies on leveraged ETFs
- E*TRADE API already integrated for equities; options API needs to be added
- Telegram approval flow exists for equity trades; needs adaptation for options
- Current architecture is modular (mixin-based TradingBot) — options strategy can follow same pattern
- SQLite database needs new tables for options positions, assignment tracking
- Branch: `wheel-strategy` — dedicated development branch

## Constraints

- **Broker**: E*TRADE API — must use their options API capabilities and limitations
- **Underlying**: IBIT only — simplifies options chain lookups and position management
- **Approval**: All options trades require Telegram approval (no auto-execution for v1)
- **Expiration default**: 30-45 DTE — configurable parameter
- **Execution style**: Semi-automated — bot suggests, user confirms

## Current State

**v1.0 Wheel Strategy — SHIPPED 2026-04-09**

The bot now runs a complete IBIT options wheel strategy:
- Signal-based cash-secured put selling (>=2% pullback from 5-day high)
- Automatic assignment detection at 8:30 AM ET with covered call suggestions
- Full wheel cycle: CASH → SHORT_PUT → HOLDING_SHARES → COVERED_CALL → CASH
- Profit management: 50% profit-taking, defensive rolling (credit-only, 2-roll limit), 21 DTE alerts
- Telegram commands: `/wheel` (status), `/wheelmode on/off` (toggle), daily 4:30 PM summary
- Streamlit dashboard with wheel strategy section (positions, cycle state, premium tracker)
- Wheel mode gates all intraday BITU/SBIT strategies when active

**Tests:** 383 passing | **Requirements:** 31/31 complete | **Phases:** 6/6 complete

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Replace intraday with wheel | Wheel generates consistent income vs directional bets; better risk-adjusted returns | Shipped v1.0 |
| IBIT only (not leveraged ETFs) | More liquid options, standard margin, no leverage decay | Shipped v1.0 |
| Semi-automated via Telegram | Maintain human oversight for options trades which have more nuance | Shipped v1.0 |
| 30-45 DTE default | Best theta decay curve, well-studied timeframe, manageable frequency | Shipped v1.0 |
| Signal-based put entry | Avoid selling puts at unfavorable times; wait for pullbacks/elevated IV | Shipped v1.0 |

---
*Last updated: 2026-04-09 after milestone v1.0 completion*
