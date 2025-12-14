---
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
inputDocuments:
  - docs/STRATEGY_VALIDATION_REPORT.md
  - docs/PROFITABLE_STRATEGIES_REPORT.md
  - README.md
documentCounts:
  briefs: 0
  research: 0
  brainstorming: 0
  projectDocs: 3
workflowType: 'prd'
lastStep: 11
project_name: 'BTrade'
user_name: 'George'
date: '2025-12-14'
status: 'complete'
---

# Product Requirements Document - BTrade

**Author:** George
**Date:** 2025-12-13

## Executive Summary

BTrade is a Bitcoin ETF trading bot that executes a proven, backtested strategy with +361.8% returns (vs +35.5% IBIT buy-and-hold). Unlike typical trading bots built to be sold, BTrade is built to be used - designed for a trader who wants reliable automation they can trust with real money, not another dashboard to babysit.

**The Vision:** A personal trading tool that runs autonomously, executes high-probability setups with leverage, and stays in cash when there's no edge. If it proves valuable in live trading, it will be open-sourced for the Bitcoin/Crypto Twitter community with a donation model.

**The Core Problem:** The strategy is validated. The code works. What's missing is the **confidence layer** - the monitoring, alerting, persistence, and failsafes that let you turn this on with real money and sleep at night.

**Primary Goal:** Transform a working prototype into a production-ready tool the author can trust to trade real money unsupervised.

**Secondary Goal:** Open-source release to the crypto community after meeting the confidence threshold (see Success Criteria).

### What Makes This Special

1. **Proven strategy, not hype** - The original "10 AM dip" hypothesis was rigorously invalidated. What remains (Mean Reversion + Short Thursday) is backed by 10+ years of Bitcoin data and statistical analysis.

2. **Trust-first design** - Built for someone who wants to sleep at night. Comprehensive logging, alerting, and failsafes over flashy features.

3. **3-second dashboard** - Open the app, see a green checkmark or red X, know instantly if today's trade executed. One number (total P&L), one sentence (current status). No hunting through tabs.

4. **Honest positioning** - Active only 33% of trading days. Sits in cash when there's no edge. No FOMO-inducing "always be trading" mentality.

5. **Real automation** - Not alerts you have to act on. Actual trade execution at 9:35 AM, position closing at 3:55 PM.

6. **Built to use, not to sell** - No affiliate links, no premium tiers, no upsells. A tool that does one thing well.

### Success Criteria (Confidence Threshold)

Before going live with real money:
- **30 consecutive days** of paper trading with zero missed signals
- **Zero silent failures** - every error surfaced via notification
- **100% match** between expected and actual trades
- **Graceful recovery** from at least one simulated failure (app restart, API timeout)

Before open-source release:
- **90 days of live trading** with positive returns
- **First-run experience** tested with 3 non-technical users who successfully connect E*TRADE
- **Documentation** covering setup, configuration, and troubleshooting

## Project Classification

**Technical Type:** Web Application (Streamlit dashboard with backend automation)
**Domain:** Fintech (trading, investment, broker integration)
**Complexity:** High
**Project Context:** Brownfield - extending existing working system

**Fintech Complexity Implications:**
- Broker API reliability and error handling critical
- Position reconciliation required (bot state vs actual broker state)
- Regulatory considerations for distribution (disclaimers, no financial advice)
- Security requirements for API credentials and OAuth tokens

**Current State:**
- Working Streamlit dashboard with 4 tabs (Today, Trading, Backtest, Strategy)
- Paper and live trading modes implemented
- E*TRADE API integration complete
- Automated scheduling via APScheduler
- SQLite trade logging
- GitHub repository: https://github.com/gtdrag/BTrade

### Key Gaps to Address (Priority Order)

1. **Position reconciliation** - Bot must verify actual broker positions before every action. This is the nuclear risk - buying when you already own, or failing to sell.

2. **State persistence across restarts** - If machine reboots mid-trade, scheduler must recover gracefully and know current position state.

3. **Proactive alerting** - Push notifications (email/SMS) on trade execution, failures, and daily summary. No silent failures ever.

4. **Persistent paper trading history** - Trade log survives app restarts. Track "what would have happened" over 30+ days.

5. **3-second status dashboard** - Redesign "Today" tab: green/red status indicator, P&L number, one-sentence current state. Visible without scrolling.

6. **Remote kill switch** - Stop all trading via single action (API call, SMS command, or dashboard button) with <1 minute latency.

7. **First-run setup wizard** - Guided E*TRADE connection with "test your credentials" button. Reduce setup friction for open-source users.

### Core Requirements

**Testability is mandatory.** A trading bot cannot be manually tested daily. The system must support:
- Simulation mode replaying historical market conditions
- Chaos testing (API failures, timeouts, malformed responses)
- Time-travel testing (simulate specific dates/conditions on demand)

## Success Criteria

### User Success

**Primary User (George):**
- "It just works" - bot runs daily without intervention or crashes
- Every trade executes correctly and matches expected signal
- Notifications confirm actions so there's no uncertainty
- **Beats buy-and-hold by 10%+** over any 90-day period
- No "oh shit" moments from silent failures or unexpected behavior

**Open-Source Users (Crypto Twitter):**
- Successful E*TRADE connection on first attempt (setup wizard works)
- Understand within 3 minutes what the bot does and why
- "Aha moment": Seeing the backtest results and realizing this isn't hype

### Business Success

**This is not a revenue play.** Success means:
- Bot works reliably for the author's personal use
- If shared, at least 10 people actually run it (not just star the repo)
- One unsolicited "this made me money" message from the community
- Donations are a bonus, not a goal

### Technical Success

**Reliability:**
- Zero missed trades due to bot failure (API failures are acceptable if notified)
- 100% signal accuracy (calculated signal matches manual verification)
- Trade execution within 5 minutes of scheduled time
- All positions closed before market close every day

**Failure Tolerance:**
- Max acceptable loss from a bug: $500 during paper phase, $0 after going live
- Acceptable missed trades: 0 (must be notified of any failure)
- Latency tolerance: $0.50 slippage per share is acceptable

**Risk Management:**
- Configurable position sizing (25/50/75/100% of capital)
- Default conservative setting (50%) to limit drawdown risk
- Max drawdown visibility before trade execution

### Measurable Outcomes

| Metric | Target | Measurement |
|--------|--------|-------------|
| Paper trading reliability | 30 days, 0 missed signals | Automated logging |
| Live trading performance | Beat B&H by 10%+ | P&L tracking |
| Signal accuracy | 100% match | Manual spot-checks |
| Notification delivery | 100% | Delivery confirmations |
| Setup success rate | 80%+ first-try | User feedback |

## Product Scope

### MVP - Minimum Viable Product

**Must have for personal use:**
1. Position reconciliation with E*TRADE (verify before every action)
2. State persistence across restarts (recover mid-trade)
3. Proactive alerting (email/SMS on every trade and failure)
4. Persistent paper trading history (30+ days tracking)
5. 3-second status dashboard (green/red, P&L, current state)
6. Configurable position sizing (25/50/75/100%)

### Growth Features (Post-MVP)

**For open-source release:**
7. Remote kill switch (stop trading via API/SMS)
8. First-run setup wizard (guided E*TRADE connection)
9. Simulation mode (replay historical conditions)
10. Comprehensive documentation

### Vision (Future)

**If community demand exists:**
- Multi-broker support (beyond E*TRADE)
- Cloud-hosted option (no self-hosting required)
- Strategy customization (adjust thresholds)
- Community performance leaderboard
- **Distributable app** (Mac App Store / Windows Store / iOS / Android) - removes Python/technical setup entirely

**Note on Mobile Distribution:** App Store rules allow trading apps that connect to licensed brokers (E*TRADE). However, reliable background execution (9:35 AM buy, 3:55 PM sell) requires a cloud backend - mobile OS platforms aggressively kill background processes. Architecture would be: mobile dashboard + cloud scheduler + push notifications.

## User Journeys

### Journey 1: George - Morning Coffee Check (Happy Path)

George wakes up, makes coffee, and grabs his phone. He opens BTrade and sees a big green checkmark. Below it: **"+$127.40 today | BITX sold at 3:55 PM"**. He smiles, closes the app, and starts his day.

That's it. That's the whole interaction.

The magic happened while he slept. At 9:35 AM, BTrade detected that IBIT dropped 2.3% yesterday, bought 47 shares of BITX at $42.15, and sent him a push notification: "Bought BITX - Mean Reversion signal." At 3:55 PM, it sold at $44.86 and sent another notification: "Sold BITX +$127.40 (+6.4%)". George saw the notifications but didn't need to do anything.

On Thursday, he checks and sees a yellow indicator: **"No position | Cash"**. The bot calculated there was no signal today. That's fine. Cash days are part of the strategy.

One morning, he sees a red indicator: **"⚠️ Trade failed - E*TRADE API timeout"**. Below it: "Retry scheduled in 5 min. No position opened." He got an SMS about this at 9:36 AM. The bot recovered automatically and executed at 9:40 AM. By morning coffee, the issue was resolved and logged.

### Journey 2: George - The Nightmare That Didn't Happen (Error Recovery)

It's Thursday morning. George checks BTrade with his coffee and sees a red banner: **"⚠️ POSITION OVERRIDE: E*TRADE shows 100 BITX shares. Bot expected 0. TRADING HALTED."**

His stomach drops. But then he reads the details: *"Detected at 9:34 AM before market open. No trades executed. Position reconciliation caught mismatch."*

George logs into E*TRADE directly. He sees the 100 shares - he forgot he manually bought some BITX last week. He sells them manually, goes back to BTrade, clicks "Acknowledge & Resume." The bot verifies: **"E*TRADE position: 0 shares. ✓ Matched. Trading resumed."**

Crisis averted. The bot protected him from doubling down on an unknown position.

Later that month, a real loss happens. BTrade bought SBIT on Thursday, but Bitcoin pumped 4%. SBIT dropped 8%. George lost $340. But it was okay because:
- Position size was 50% of capital (his conservative setting)
- The notification showed: "Sold SBIT -$340. Monthly P&L still +$890."
- Overall performance: **+12.4% vs B&H +8.2%** over 60 days

One bad trade didn't ruin him. The loss was controlled, expected, and within tolerance.

### Journey 3: Jake from Crypto Twitter - New User Onboarding

Jake sees a tweet about BTrade beating buy-and-hold by 15%. He clicks the GitHub link, skeptical but curious.

**Path A - Manual Trading (Zero Setup):**
Jake doesn't want to give API access to a random project. He downloads the app, opens it, and sees today's signal: **"BUY BITX - IBIT dropped 2.4% yesterday"**. He buys BITX manually on his broker app, sells at 3:50 PM. After two weeks of manual trading (4 trades, 3 winners, up 6%), he trusts it.

**Path B - Automated Trading:**
After two weeks, Jake wants automation. He clicks "Enable Automation" and sees a setup wizard:
1. Create E*TRADE Developer Account (with screenshots)
2. Get API Keys (sandbox vs production)
3. Enter Credentials
4. Test Connection - **"✓ Connected to E*TRADE Sandbox"**
5. Start Paper Trading - mandatory 30 days before live

Jake completes setup in 20 minutes. Next morning: **"Paper trade executed: Bought 50 BITX @ $41.20"**

### Journey 4: George - Set and Forget (Long-term)

After 90 days of live trading, George's interaction is minimal:

- **Daily:** Glance at notification. Green? Move on.
- **Monthly:** Check P&L chart. "Up 8% vs B&H 3%." Close app.
- **Quarterly:** Transfer profits.
- **Yearly:** Export trade log for taxes.

No configuration tweaking. No log reviews. The strategy works or it doesn't.

### Journey Requirements Summary

| Journey | Key Requirements |
|---------|------------------|
| Morning Coffee Check | 3-second dashboard, push notifications, SMS alerts, auto-retry |
| Nightmare Prevention | Position reconciliation, trading halt on mismatch, configurable position sizing |
| New User Onboarding | Distributable app, manual mode, setup wizard, test connection, paper trading |
| Set and Forget | Trade log export, performance summaries, minimal required interaction |

## Domain-Specific Requirements

### Fintech Compliance & Regulatory Overview

BTrade operates in the fintech domain but with a narrow scope: personal automated trading of SEC-regulated ETFs (IBIT, BITX, SBIT) through E*TRADE's API. This is NOT a financial services business - it's a personal tool that may be open-sourced.

**What applies:** API security, basic disclaimers, tax reporting
**What doesn't apply:** KYC/AML, PCI DSS, banking regulations, money transmission laws

### Security Requirements

**API Credential Protection (Critical):**
- Never store credentials in plaintext or committed files
- Use OS-level secure storage (macOS Keychain / Windows Credential Manager)
- Environment variables as fallback for server deployments
- Credentials encrypted at rest if stored in config files
- No credentials in logs, error messages, or stack traces

**Session Security:**
- OAuth tokens stored securely, not in browser localStorage
- Token refresh handled automatically before expiration
- Session invalidation on suspicious activity (failed auth attempts)

**Access Control:**
- Single-user system (no multi-tenancy complexity)
- Optional PIN/password to open dashboard (for shared computers)

### Disclaimer Requirements

**Minimal but present:**
- Single disclaimer on first launch / first-run wizard: *"BTrade is not financial advice. Past performance does not guarantee future results. You are solely responsible for your trading decisions. Use at your own risk."*
- Footer link to full disclaimer in documentation
- No per-screen warnings or pop-ups

**Open-source LICENSE considerations:**
- MIT or Apache 2.0 license with liability disclaimer
- "AS IS" without warranty of any kind

### Tax Reporting Requirements

**Trade Log Export:**
- CSV format compatible with TurboTax, H&R Block, and major tax software
- Required fields: Date, Action (Buy/Sell), Symbol, Quantity, Price, Total, Gain/Loss
- Short-term capital gains flagging (all trades are <1 day hold)
- Cost basis tracking (FIFO method)
- Annual summary report (total gains, total losses, net)

**Format Example:**
```csv
Date,Action,Symbol,Quantity,Price,Proceeds,Cost Basis,Gain/Loss,Term
2025-01-15,SELL,BITX,50,44.86,2243.00,2107.50,135.50,Short
```

### Implementation Considerations

**Security vs. Usability Balance:**
- First-run: Guided credential setup with "Test Connection" before saving
- Credentials stored in OS keychain (secure) but only entered once (easy)
- No re-authentication required for daily use unless session expires
- Clear error messages without exposing sensitive data

**Compliance Maintenance:**
- Disclaimer text stored in single config file (easy to update if laws change)
- Tax export format versioned (can add fields for future requirements)

## Technical Architecture

### Current System (Brownfield)

```
┌─────────────────────────────────────────────────────────────┐
│                    Streamlit Dashboard                       │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────────┐          │
│  │  Today  │ │ Trading │ │Backtest │ │ Strategy │          │
│  └────┬────┘ └────┬────┘ └────┬────┘ └────┬─────┘          │
└───────┼──────────┼──────────┼──────────┼────────────────────┘
        │          │          │          │
        v          v          v          v
┌─────────────────────────────────────────────────────────────┐
│                     Python Backend                           │
│  ┌──────────────┐  ┌────────────────┐  ┌─────────────────┐  │
│  │SmartStrategy │  │  TradingBot    │  │ SmartScheduler  │  │
│  │(Signal Logic)│  │(Execution)     │  │(APScheduler)    │  │
│  └──────────────┘  └───────┬────────┘  └────────┬────────┘  │
│                            │                     │           │
│                            v                     v           │
│            ┌───────────────────────────────────────┐        │
│            │         E*TRADE Client               │        │
│            │  (OAuth + Order Execution)           │        │
│            └───────────────────────────────────────┘        │
│                            │                                 │
│  ┌─────────────┐          │          ┌──────────────────┐  │
│  │  Database   │◄─────────┴─────────►│  Notifications   │  │
│  │  (SQLite)   │                     │  (Email/Desktop) │  │
│  └─────────────┘                     └──────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Key Components

| Component | File | Purpose |
|-----------|------|---------|
| SmartStrategy | src/smart_strategy.py | Signal generation (Mean Reversion, Short Thursday) |
| TradingBot | src/trading_bot.py | Trade execution, position management |
| SmartScheduler | src/smart_scheduler.py | 9:35 AM buy, 3:55 PM sell automation |
| ETradeClient | src/etrade_client.py | E*TRADE OAuth and order execution |
| Database | src/database.py | Trade logging, event history |
| Notifications | src/notifications.py | Email/desktop alerts |

### Data Flow

1. **Signal Generation** (9:34 AM daily):
   - Fetch previous day IBIT close from Yahoo Finance
   - Calculate daily return percentage
   - Apply strategy rules (Mean Reversion threshold, Thursday detection)
   - Output: BUY BITX, BUY SBIT, or CASH

2. **Trade Execution** (9:35 AM if signal exists):
   - Get current ETF quote
   - Calculate position size based on available capital
   - Preview order via E*TRADE API
   - Execute market order
   - Log to database, send notification

3. **Position Close** (3:55 PM if position exists):
   - Check open positions
   - Execute market sell order
   - Calculate P&L
   - Log to database, send notification

## Functional Requirements

### FR1: Position Reconciliation (Priority: Critical)

**Description:** Before any trade action, verify bot's expected positions match actual E*TRADE positions.

**Acceptance Criteria:**
- [ ] Query E*TRADE positions API before morning signal execution
- [ ] Compare expected state (from database) with actual broker state
- [ ] If mismatch detected: halt trading, send alert, require manual acknowledgment
- [ ] Log all reconciliation checks with timestamps

**Rationale:** Prevents catastrophic errors like buying when already holding, or selling non-existent positions.

### FR2: State Persistence (Priority: Critical)

**Description:** Bot state survives app restarts and system reboots.

**Acceptance Criteria:**
- [ ] Persist current position state to SQLite on every trade
- [ ] On startup, load last known state from database
- [ ] Detect if trade was interrupted mid-execution
- [ ] Resume or alert based on interruption type

**Rationale:** Machine reboots happen. The bot must know if it's holding a position.

### FR3: Proactive Alerting (Priority: High)

**Description:** Push notifications for all trade events and failures.

**Acceptance Criteria:**
- [ ] Email notification on: trade executed, trade failed, position closed, daily summary
- [ ] SMS notification on: trade failed, position mismatch, API authentication expired
- [ ] Desktop notification on: all events
- [ ] No silent failures - every error surfaces to user

**Rationale:** User needs confidence that they'll know if something goes wrong.

### FR4: Paper Trading History (Priority: High)

**Description:** Track simulated trades across app restarts for 30+ days.

**Acceptance Criteria:**
- [ ] Paper trades logged to persistent database (not just session state)
- [ ] Running P&L calculation for paper portfolio
- [ ] Comparison view: "What would have happened" vs actual market
- [ ] 30-day history minimum before live trading enabled

**Rationale:** Validates strategy in current market conditions before risking real money.

### FR5: 3-Second Status Dashboard (Priority: High)

**Description:** At-a-glance status visible immediately on app open.

**Acceptance Criteria:**
- [ ] Large status indicator: Green (trade executed), Yellow (cash/no signal), Red (error)
- [ ] Primary metric: Today's P&L or current position value
- [ ] One-sentence status: "BITX sold at 3:55 PM +$127.40" or "No trade today - cash"
- [ ] Visible without scrolling on desktop and mobile browsers

**Rationale:** "Morning coffee check" user journey - see status in 3 seconds.

### FR6: Configurable Position Sizing (Priority: Medium)

**Description:** Allow users to limit exposure per trade.

**Acceptance Criteria:**
- [ ] Dropdown selection: 25%, 50%, 75%, 100% of available capital
- [ ] Default to conservative setting (50%)
- [ ] Show estimated max loss before trade execution
- [ ] Persist preference across sessions

**Rationale:** Risk management for users nervous about going all-in.

### FR7: Remote Kill Switch (Priority: Medium)

**Description:** Emergency stop for all trading activity.

**Acceptance Criteria:**
- [ ] Dashboard button: "Stop All Trading"
- [ ] Confirmation required to prevent accidental clicks
- [ ] Closes any open positions immediately
- [ ] Disables scheduler until manually re-enabled
- [ ] SMS command option (reply "STOP" to any notification)

**Rationale:** Peace of mind that you can halt everything instantly.

### FR8: First-Run Setup Wizard (Priority: Medium)

**Description:** Guided onboarding for E*TRADE connection.

**Acceptance Criteria:**
- [ ] Step-by-step flow with screenshots
- [ ] "Test Connection" button before saving credentials
- [ ] Sandbox vs Production toggle (default: sandbox)
- [ ] Paper trading auto-starts after successful connection
- [ ] Link to E*TRADE developer account creation

**Rationale:** Reduces friction for open-source users who aren't developers.

### FR9: Tax Export (Priority: Low)

**Description:** Export trade history for tax filing.

**Acceptance Criteria:**
- [ ] CSV download of all trades
- [ ] Fields: Date, Symbol, Action, Quantity, Price, Proceeds, Cost Basis, Gain/Loss, Term
- [ ] FIFO cost basis calculation
- [ ] Annual summary: total gains, total losses, net
- [ ] Compatible with TurboTax import format

**Rationale:** All gains are short-term capital gains; users need records for taxes.

## Non-Functional Requirements

### NFR1: Reliability

| Metric | Target | Measurement |
|--------|--------|-------------|
| Trade execution success | 99.9% | (Successful trades / attempted trades) |
| Scheduled job execution | 100% | All scheduled jobs run within 5 min window |
| Uptime during market hours | 99.5% | Monitored availability 9:30 AM - 4:00 PM ET |

### NFR2: Performance

| Metric | Target | Measurement |
|--------|--------|-------------|
| Dashboard load time | < 2 seconds | Time to first meaningful paint |
| Trade execution latency | < 30 seconds | Signal to order placed |
| Quote freshness | < 5 seconds | Age of displayed price data |

### NFR3: Security

| Requirement | Implementation |
|-------------|----------------|
| Credential storage | OS-level secure storage (macOS Keychain / Windows Credential Manager) |
| OAuth tokens | Encrypted at rest, auto-refresh before expiration |
| No credentials in logs | Sanitized logging, redact sensitive data |
| Session timeout | Re-authenticate after 24 hours of inactivity |

### NFR4: Testability

| Requirement | Implementation |
|-------------|----------------|
| Simulation mode | Replay historical dates with fake market data |
| Chaos testing | Inject API failures, timeouts, malformed responses |
| Time travel | Test specific dates/scenarios on demand |
| Paper trading | Full functionality without real money |

### NFR5: Maintainability

| Requirement | Implementation |
|-------------|----------------|
| Logging | Structured JSON logs with severity levels |
| Error tracking | All exceptions logged with stack traces |
| Database migrations | Versioned schema with upgrade path |
| Configuration | Environment variables + config file fallback |

## Epic Overview

### Epic 1: Core Reliability (MVP - Personal Use)
**Goal:** Trust the bot enough to run with real money unsupervised.

| Story | Priority | Estimate |
|-------|----------|----------|
| Implement position reconciliation with E*TRADE | Critical | |
| Add state persistence across restarts | Critical | |
| Build proactive alerting (email/SMS) | High | |
| Persist paper trading history to database | High | |
| Redesign Today tab as 3-second dashboard | High | |
| Add configurable position sizing | Medium | |

**Success Criteria:** 30 days of paper trading with zero missed signals.

### Epic 2: Open Source Readiness
**Goal:** Non-technical users can set up and use BTrade.

| Story | Priority | Estimate |
|-------|----------|----------|
| Build first-run setup wizard | Medium | |
| Add remote kill switch | Medium | |
| Implement simulation mode | Medium | |
| Write comprehensive documentation | Medium | |
| Add tax export functionality | Low | |

**Success Criteria:** 80%+ first-try setup success rate.

### Epic 3: Future Distribution (Vision)
**Goal:** Reach broader audience via app stores.

| Story | Priority | Estimate |
|-------|----------|----------|
| Evaluate cloud backend architecture | Future | |
| Design mobile-first dashboard | Future | |
| Multi-broker API abstraction | Future | |
| App Store submission process | Future | |

**Success Criteria:** Community demand validated before starting.

---

## Appendix: Strategy Reference

### Mean Reversion Signal
- **Trigger:** IBIT previous day return ≤ -2.0%
- **Action:** Buy BITX (2x leveraged long) at 9:35 AM
- **Exit:** Sell at 3:55 PM (same day)
- **Historical win rate:** ~63%

### Short Thursday Signal
- **Trigger:** Today is Thursday
- **Action:** Buy SBIT (2x inverse) at 9:35 AM
- **Exit:** Sell at 3:55 PM (same day)
- **Historical win rate:** ~55%

### Signal Priority
If both signals trigger on a Thursday after a big down day, Mean Reversion takes priority (higher win rate).

### ETF Details
| Symbol | Name | Leverage | Expense Ratio |
|--------|------|----------|---------------|
| IBIT | iShares Bitcoin Trust | 1x | 0.25% |
| BITX | 2x Bitcoin Strategy ETF | 2x Long | 1.85% |
| SBIT | ProShares UltraShort Bitcoin | 2x Short | 0.95% |
