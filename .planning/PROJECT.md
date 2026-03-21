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

## Current Milestone: v1.0 Wheel Strategy

**Goal:** Replace intraday directional strategies with a full wheel strategy on IBIT options, executed semi-automatically via Telegram approvals.

**Target features:**
- E*TRADE options API integration (chains, orders, Greeks)
- Signal-based cash-secured put selling with Telegram approval
- Assignment detection and automatic covered call suggestions
- Full wheel cycle management (put → shares → call → repeat)
- Configurable strike selection (delta, % OTM, DTE)
- Profit management (50% profit close, rolling)
- Deprecate/disable intraday BITU/SBIT strategies

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Replace intraday with wheel | Wheel generates consistent income vs directional bets; better risk-adjusted returns | — Pending |
| IBIT only (not leveraged ETFs) | More liquid options, standard margin, no leverage decay | — Pending |
| Semi-automated via Telegram | Maintain human oversight for options trades which have more nuance | — Pending |
| 30-45 DTE default | Best theta decay curve, well-studied timeframe, manageable frequency | — Pending |
| Signal-based put entry | Avoid selling puts at unfavorable times; wait for pullbacks/elevated IV | — Pending |

---
*Last updated: 2026-03-18 after milestone v1.0 initialization*
