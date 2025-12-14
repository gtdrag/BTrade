---
stepsCompleted: [1, 2, 3]
inputDocuments:
  - docs/prd.md
  - docs/architecture.md
workflowType: 'create-epics-stories'
lastStep: 3
project_name: 'BTrade'
user_name: 'George'
date: '2025-12-14'
epicsApproved: true
storiesGenerated: true
totalEpics: 5
totalStories: 34
---

# BTrade - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for BTrade, decomposing the requirements from the PRD and Architecture into implementable stories. BTrade is a Bitcoin ETF trading bot that executes a proven strategy (+361.8% returns vs +35.5% buy-and-hold) with the "confidence layer" needed for real money trading.

## Requirements Inventory

### Functional Requirements

**FR1: Position Reconciliation** (Priority: Critical)
Before any trade action, verify bot's expected positions match actual E*TRADE positions. Prevents catastrophic errors like double-buying or selling non-existent positions.

**FR2: State Persistence** (Priority: Critical)
Bot state survives app restarts and system reboots. Persist current position state to SQLite on every trade. On startup, load last known state and detect/resume interrupted trades.

**FR3: Proactive Alerting** (Priority: High)
Push notifications for all trade events and failures. Email on trade executed/failed/closed/daily summary. SMS on failures, position mismatch, auth expired. Desktop notification on all events. No silent failures.

**FR4: Paper Trading History** (Priority: High)
Track simulated trades across app restarts for 30+ days. Paper trades logged to persistent database. Running P&L calculation. Comparison view: "What would have happened" vs actual market. 30-day minimum before live trading enabled.

**FR5: 3-Second Status Dashboard** (Priority: High)
At-a-glance status visible immediately on app open. Large status indicator: Green (trade executed), Yellow (cash/no signal), Red (error). Primary metric: Today's P&L or current position value. One-sentence status. Visible without scrolling.

**FR6: Configurable Position Sizing** (Priority: Medium)
Allow users to limit exposure per trade. Dropdown: 25%, 50%, 75%, 100% of available capital. Default to conservative (50%). Show estimated max loss before execution. Persist preference.

**FR7: Remote Kill Switch** (Priority: Medium)
Emergency stop for all trading activity. Dashboard button with confirmation. Closes any open positions immediately. Disables scheduler until manually re-enabled.

**FR8: First-Run Setup Wizard** (Priority: Medium)
Guided onboarding for E*TRADE connection. Step-by-step flow with screenshots. "Test Connection" button before saving. Sandbox vs Production toggle.

**FR9: Tax Export** (Priority: Low)
Export trade history for tax filing. CSV download with fields: Date, Symbol, Action, Quantity, Price, Proceeds, Cost Basis, Gain/Loss, Term. FIFO cost basis calculation. Annual summary.

### Non-Functional Requirements

**NFR1: Reliability**
- Trade execution success: 99.9%
- Scheduled job execution: 100% (within 5 min window)
- Uptime during market hours: 99.5% (9:30 AM - 4:00 PM ET)

**NFR2: Performance**
- Dashboard load time: < 2 seconds
- Trade execution latency: < 30 seconds
- Quote freshness: < 5 seconds

**NFR3: Security**
- Credential storage: OS-level secure storage (macOS Keychain / Windows Credential Manager)
- OAuth tokens: Encrypted at rest, auto-refresh before expiration
- No credentials in logs
- Session timeout: Re-authenticate after 24 hours inactivity

**NFR4: Testability**
- Simulation mode: Replay historical dates with fake market data
- Chaos testing: Inject API failures, timeouts, malformed responses
- Time travel: Test specific dates/scenarios on demand

**NFR5: Maintainability**
- Logging: Structured JSON logs with severity levels via structlog
- Error tracking: All exceptions logged with stack traces
- Database migrations: Versioned schema with upgrade path
- Configuration: Environment variables + config file fallback

### Additional Requirements from Architecture

**New Dependencies:**
- `keyring` - OS-level secure credential storage
- `structlog` - Structured JSON logging
- `tenacity` - Retry logic with exponential backoff
- `twilio` - SMS notifications

**Database Schema (New Tables):**
- `bot_state` - Position state for crash recovery (position_symbol, shares, entry_price, expected_action, last_updated)
- `reconciliation_log` - Audit log for position verification (expected_position, broker_position, match, action_taken)
- `trade_history` - Persistent paper + live trades (mode, signal, etf, action, shares, prices, pnl, order_id)

**Error Taxonomy:**
- `RecoverableError` - Retry with backoff (API timeout, rate limit)
- `FatalError` - Halt trading, notify immediately (auth failure, broker error)
- `PositionMismatchError` - Special handling, requires manual acknowledgment

**Implementation Patterns:**
- State-Action-Confirm: Persist intent BEFORE external action, confirm after completion
- Retry: 3 attempts, exponential backoff, max 5 min window
- Notification routing: info→desktop, warning→desktop+email, error/critical→desktop+email+SMS
- Logging: JSON via structlog with correlation IDs

**New Modules Required:**
- `src/errors.py` - Error taxonomy
- `src/state_manager.py` - Position state and crash recovery
- `src/reconciliation.py` - Broker position verification
- `src/credentials.py` - Keychain access via keyring
- `src/config.py` - Settings and position sizing
- `src/logging_config.py` - structlog configuration
- `src/components/` - Streamlit UI components

## Epic List

### Epic 1: Reliability Foundation
**Goal:** Establish the infrastructure that makes the bot trustworthy for real money trading.

**User Value:** The bot has robust error handling, state persistence, and crash recovery. If your computer reboots mid-trade, you won't lose track of your position or have corrupted data.

**FRs covered:** FR2 (State Persistence), Enables FR1, FR3, FR4

**Implementation Notes:**
- Adds new dependencies (keyring, structlog, tenacity)
- Creates new database schema (bot_state, reconciliation_log, trade_history tables)
- Implements error taxonomy (RecoverableError, FatalError, PositionMismatchError)
- Sets up structured logging with correlation IDs
- Implements state_manager.py with state-action-confirm pattern

---

### Epic 2: Position Safety
**Goal:** Verify broker positions before trades and alert on all events.

**User Value:** The bot verifies your actual E*TRADE positions before every trade action. It won't accidentally double-buy shares you already own or sell positions that don't exist. Every trade event and failure surfaces via notification - no silent failures ever.

**FRs covered:** FR1 (Position Reconciliation), FR3 (Proactive Alerting)

**Implementation Notes:**
- Implements reconciliation.py with pre-trade verification
- Adds mismatch detection with trading halt
- Requires manual acknowledgment to resume after mismatch
- Adds Twilio SMS integration
- Implements notification routing by severity (info→desktop, warning→email, error→SMS)
- Daily summary notifications

---

### Epic 3: Visibility & Tracking
**Goal:** Instant status visibility and persistent trade history.

**User Value:** Open the app and see green/yellow/red status in 3 seconds - no hunting through tabs. Track 30+ days of paper trades to validate the strategy before risking real money. See running P&L and compare "what would have happened" vs actual market.

**FRs covered:** FR4 (Paper Trading History), FR5 (3-Second Status Dashboard)

**Implementation Notes:**
- Creates status_dashboard.py component
- Redesigns "Today" tab for instant visibility
- Migrates paper trades to persistent database
- Adds running P&L calculation
- Trade history comparison view

---

### Epic 4: Risk Controls
**Goal:** Position sizing and emergency stop capability.

**User Value:** Control how much of your capital is at risk per trade (25/50/75/100%). See estimated max loss before execution. Have a panic button to halt all trading instantly and close positions.

**FRs covered:** FR6 (Position Sizing), FR7 (Kill Switch)

**Implementation Notes:**
- Adds position sizing dropdown in settings
- Default to conservative 50%
- Shows max loss estimate before trade
- Kill switch button with confirmation
- Closes positions and disables scheduler on activation
- Preference persistence

---

### Epic 5: Onboarding & Compliance
**Goal:** Easy setup for new users and tax export functionality.

**User Value:** Non-technical users can connect E*TRADE via guided wizard without developer knowledge. Export trade history in TurboTax-compatible format for tax filing.

**FRs covered:** FR8 (Setup Wizard), FR9 (Tax Export)

**Implementation Notes:**
- Step-by-step wizard with screenshots
- "Test Connection" before saving credentials
- Sandbox vs Production toggle
- CSV export with FIFO cost basis
- Annual summary report
- All short-term capital gains flagging

---

## FR Coverage Map

| FR | Epic | Description |
|----|------|-------------|
| FR1: Position Reconciliation | Epic 2 | Verify broker positions match bot state before every trade |
| FR2: State Persistence | Epic 1 | Bot state survives restarts, crash recovery |
| FR3: Proactive Alerting | Epic 2 | Email/SMS/desktop notifications for all events |
| FR4: Paper Trading History | Epic 3 | Persistent 30+ day paper trade tracking |
| FR5: 3-Second Dashboard | Epic 3 | Green/yellow/red status indicator, instant visibility |
| FR6: Position Sizing | Epic 4 | 25/50/75/100% capital allocation choice |
| FR7: Kill Switch | Epic 4 | Emergency stop, close positions, halt trading |
| FR8: Setup Wizard | Epic 5 | Guided E*TRADE connection for non-technical users |
| FR9: Tax Export | Epic 5 | TurboTax-compatible CSV with FIFO cost basis |

## NFR Integration

NFRs are addressed across all epics:
- **NFR1 (Reliability):** Epic 1 (error handling, retries) + Epic 2 (reconciliation)
- **NFR2 (Performance):** Epic 3 (dashboard < 2s load)
- **NFR3 (Security):** Epic 1 (credentials.py via keyring) + Epic 5 (secure credential flow)
- **NFR4 (Testability):** Built into Epic 1 (mock-friendly patterns, structured logging)
- **NFR5 (Maintainability):** Epic 1 (structlog, migrations, config patterns)

---

## Epic 1: Reliability Foundation

**Goal:** Establish the infrastructure that makes the bot trustworthy for real money trading.

**User Value:** The bot has robust error handling, state persistence, and crash recovery. If your computer reboots mid-trade, you won't lose track of your position or have corrupted data.

**FRs covered:** FR2 (State Persistence), Enables FR1, FR3, FR4

---

### Story 1.1: Add Core Dependencies and Project Configuration

As a **developer**,
I want **the project to have all required dependencies and proper tooling configuration**,
So that **I can build reliability features with consistent code quality**.

**Acceptance Criteria:**

**Given** the project has existing requirements.txt
**When** I add the new dependencies
**Then** requirements.txt includes keyring, structlog, tenacity, and twilio
**And** all packages install successfully with `pip install -r requirements.txt`

**Given** the project needs consistent code formatting
**When** I create pyproject.toml
**Then** it includes ruff configuration for linting and formatting
**And** it includes pytest configuration
**And** `ruff check .` runs without configuration errors

**Given** the project needs pre-commit hooks
**When** I create .pre-commit-config.yaml
**Then** it includes ruff hooks for check and format
**And** `pre-commit install` succeeds

---

### Story 1.2: Implement Error Taxonomy

As a **developer**,
I want **a clear error classification system**,
So that **different error types trigger appropriate responses (retry vs halt vs notify)**.

**Acceptance Criteria:**

**Given** I need to categorize errors by severity
**When** I create src/errors.py
**Then** it defines RecoverableError(Exception) for transient failures
**And** it defines FatalError(Exception) for unrecoverable failures
**And** it defines PositionMismatchError(FatalError) for position discrepancies

**Given** a RecoverableError occurs
**When** the error is caught
**Then** it should be eligible for retry with backoff

**Given** a FatalError occurs
**When** the error is caught
**Then** trading should halt and user should be notified immediately

**Given** a PositionMismatchError occurs
**When** the error is caught
**Then** trading should halt until manual acknowledgment
**And** SMS notification should be sent

---

### Story 1.3: Configure Structured Logging

As a **developer**,
I want **structured JSON logging with correlation IDs**,
So that **I can trace issues across components and create audit trails**.

**Acceptance Criteria:**

**Given** the project needs structured logging
**When** I create src/logging_config.py
**Then** it configures structlog with JSON output format
**And** it includes timestamp in ISO 8601 format with timezone
**And** it includes log level (info, warning, error, critical)

**Given** a log entry is created
**When** the entry is written
**Then** it outputs valid JSON to logs/btrade.log
**And** logs directory is created if it doesn't exist

**Given** logs accumulate over time
**When** log rotation is configured
**Then** logs older than 30 days are removed
**And** current log file doesn't exceed reasonable size

**Given** I need to trace related operations
**When** I use the logger
**Then** I can include correlation_id in log entries
**And** I can include structured data (dicts) in entries

---

### Story 1.4: Create Reliability Database Schema

As a **developer**,
I want **database tables for state persistence, reconciliation, and trade history**,
So that **the bot can recover from crashes and maintain audit trails**.

**Acceptance Criteria:**

**Given** the bot needs crash recovery state
**When** I create the bot_state table
**Then** it has columns: id, position_symbol, position_shares, position_entry_price, position_entry_time, expected_action, last_updated
**And** expected_action accepts values: 'IDLE', 'PENDING_BUY', 'HOLDING', 'PENDING_SELL'

**Given** the bot needs reconciliation audit trail
**When** I create the reconciliation_log table
**Then** it has columns: id, timestamp, expected_position, broker_position, match (boolean), action_taken
**And** entries are append-only (never modified)

**Given** paper and live trades need persistent history
**When** I create the trade_history table
**Then** it has columns: id, date, mode ('paper'/'live'), signal, etf, action, shares, entry_price, exit_price, pnl, order_id
**And** it can store 30+ days of trade history

**Given** the database schema may evolve
**When** I implement schema versioning
**Then** database.py checks schema version on startup
**And** migrations run automatically if version mismatch
**And** schema version is stored in a metadata table

---

### Story 1.5: Implement State Manager Core

As a **developer**,
I want **a state manager module that tracks position state reliably**,
So that **the bot knows its expected state and can recover from crashes**.

**Acceptance Criteria:**

**Given** I need to track position state
**When** I create src/state_manager.py
**Then** it provides methods: get_state(), set_pending_buy(), confirm_buy(), set_pending_sell(), confirm_sell(), clear_position()

**Given** the bot is starting up
**When** I call get_state()
**Then** it returns the last persisted state from bot_state table
**And** it returns None if no state exists

**Given** the bot is about to place a buy order
**When** I call set_pending_buy(etf, shares, price)
**Then** it persists expected_action='PENDING_BUY' to database BEFORE returning
**And** it stores the position details (symbol, shares, price, timestamp)

**Given** a buy order has been filled
**When** I call confirm_buy(order_id, fill_price)
**Then** it updates expected_action='HOLDING'
**And** it updates the entry price with actual fill price
**And** it logs the confirmation

**Given** the bot crashed during a pending buy
**When** the bot restarts and calls get_state()
**Then** it returns state with expected_action='PENDING_BUY'
**And** the bot can decide to verify order status or alert user

---

### Story 1.6: Integrate State Persistence into Trading Bot

As a **trader (George)**,
I want **the trading bot to persist state before and after every trade action**,
So that **if my computer crashes mid-trade, the bot knows where it left off**.

**Acceptance Criteria:**

**Given** the trading bot is executing a buy signal
**When** the bot starts the trade
**Then** it calls state_manager.set_pending_buy() BEFORE placing the order
**And** the state is persisted to database before any API call

**Given** a buy order has been successfully filled
**When** the bot receives confirmation
**Then** it calls state_manager.confirm_buy() immediately
**And** the HOLDING state is persisted before any other action

**Given** the bot is closing a position
**When** the bot starts the sell
**Then** it calls state_manager.set_pending_sell() BEFORE placing the order
**And** after confirmation, it calls state_manager.clear_position()

**Given** the bot is starting up
**When** initialization occurs
**Then** it checks state_manager.get_state()
**And** if expected_action is 'PENDING_BUY' or 'PENDING_SELL', it logs a warning
**And** it does not automatically trade until state is resolved

**Given** structured logging is configured
**When** trade operations occur
**Then** all state transitions are logged with structlog
**And** logs include correlation_id linking related operations

---

## Epic 2: Position Safety

**Goal:** Verify broker positions before trades and alert on all events.

**User Value:** The bot verifies your actual E*TRADE positions before every trade action. It won't accidentally double-buy shares you already own or sell positions that don't exist. Every trade event and failure surfaces via notification - no silent failures ever.

**FRs covered:** FR1 (Position Reconciliation), FR3 (Proactive Alerting)

---

### Story 2.1: Implement Position Reconciliation Core

As a **trader (George)**,
I want **the bot to query my actual E*TRADE positions and compare them to expected state**,
So that **I never accidentally double-buy or sell positions I don't own**.

**Acceptance Criteria:**

**Given** I need to verify broker positions
**When** I create src/reconciliation.py
**Then** it provides a reconcile() function that returns match status
**And** it queries E*TRADE positions API for BITX and SBIT holdings

**Given** the bot expects 0 shares and E*TRADE shows 0 shares
**When** reconcile() is called
**Then** it returns match=True
**And** logs "position_reconciled" with details

**Given** the bot expects 50 BITX shares and E*TRADE shows 50 BITX shares
**When** reconcile() is called
**Then** it returns match=True
**And** the position details match (symbol, quantity)

**Given** the bot expects 0 shares but E*TRADE shows 100 BITX shares
**When** reconcile() is called
**Then** it returns match=False
**And** it raises PositionMismatchError with details of expected vs actual
**And** logs "position_mismatch_detected" at ERROR level

**Given** a reconciliation check occurs
**When** the check completes
**Then** it logs to reconciliation_log table (expected, actual, match, action_taken)

---

### Story 2.2: Integrate Pre-Trade Reconciliation Check

As a **trader (George)**,
I want **the bot to verify positions BEFORE every trade action**,
So that **trades only execute when state matches reality**.

**Acceptance Criteria:**

**Given** the scheduler triggers the morning signal job
**When** a trade signal exists (BITX or SBIT)
**Then** reconcile() is called BEFORE any order is placed
**And** trading only proceeds if match=True

**Given** reconcile() returns match=False before a buy
**When** the mismatch is detected
**Then** trading is halted immediately
**And** PositionMismatchError is raised
**And** no order is placed

**Given** the scheduler triggers the close positions job
**When** positions need to be closed
**Then** reconcile() is called BEFORE selling
**And** the expected shares match what we're about to sell

**Given** E*TRADE API is unavailable
**When** reconcile() cannot fetch positions
**Then** it raises RecoverableError
**And** retry logic attempts 3 times with backoff
**And** if all retries fail, it escalates to FatalError

---

### Story 2.3: Implement Mismatch Halt and Acknowledgment

As a **trader (George)**,
I want **trading to halt on position mismatch until I manually acknowledge**,
So that **I review and resolve discrepancies before the bot continues**.

**Acceptance Criteria:**

**Given** a PositionMismatchError has been raised
**When** the error is caught
**Then** state_manager sets state to 'HALTED'
**And** the halt reason is persisted (expected vs actual details)
**And** scheduler stops processing new trade jobs

**Given** the bot is in HALTED state
**When** I open the dashboard
**Then** I see a red banner with mismatch details
**And** I see an "Acknowledge & Resume" button
**And** I see the expected vs actual position comparison

**Given** I click "Acknowledge & Resume"
**When** the action is confirmed
**Then** reconcile() is called again to verify current state
**And** if match=True, state changes from HALTED to IDLE
**And** scheduler resumes normal operation
**And** an acknowledgment is logged with timestamp

**Given** I click "Acknowledge & Resume" but mismatch still exists
**When** reconcile() returns match=False
**Then** the halt continues
**And** user sees updated mismatch details
**And** a message indicates manual broker action may be needed

---

### Story 2.4: Add Twilio SMS Integration

As a **trader (George)**,
I want **SMS notifications for critical events**,
So that **I'm immediately alerted to failures even when away from my computer**.

**Acceptance Criteria:**

**Given** I need SMS capability
**When** I configure Twilio in notifications.py
**Then** it reads Twilio credentials from environment variables (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, TWILIO_TO_NUMBER)
**And** credentials can also come from keyring

**Given** valid Twilio credentials exist
**When** I call send_sms(message)
**Then** an SMS is sent to the configured number
**And** the Twilio response is logged
**And** delivery status is tracked

**Given** Twilio credentials are missing or invalid
**When** send_sms() is called
**Then** it logs a warning but does not crash
**And** other notification channels (email, desktop) still work

**Given** SMS sending fails (network, Twilio outage)
**When** the error occurs
**Then** it logs the failure
**And** retries once after 30 seconds
**And** continues with other notifications regardless of SMS status

---

### Story 2.5: Implement Notification Routing by Severity

As a **trader (George)**,
I want **notifications routed to appropriate channels based on severity**,
So that **critical issues reach me via SMS while routine updates stay on desktop**.

**Acceptance Criteria:**

**Given** a notification with severity='info' (trade executed successfully)
**When** the notification is sent
**Then** only desktop notification is triggered
**And** email and SMS are NOT sent

**Given** a notification with severity='warning' (retry in progress)
**When** the notification is sent
**Then** desktop AND email notifications are triggered
**And** SMS is NOT sent

**Given** a notification with severity='error' (trade failed after retries)
**When** the notification is sent
**Then** desktop, email, AND SMS notifications are triggered

**Given** a notification with severity='critical' (position mismatch)
**When** the notification is sent
**Then** desktop, email, AND SMS notifications are triggered immediately
**And** SMS includes urgency indicator

**Given** I want to test notification channels
**When** I use a test_notifications() function
**Then** a test message is sent to all configured channels
**And** I can verify each channel works

---

### Story 2.6: Implement Trade Event Notifications

As a **trader (George)**,
I want **notifications for all trade events**,
So that **I know when trades execute, fail, or positions close without checking the dashboard**.

**Acceptance Criteria:**

**Given** a trade signal is executed successfully
**When** the order is filled
**Then** I receive a notification: "Bought {shares} {ETF} @ ${price} - {signal_type}"
**And** severity is 'info' (desktop only)

**Given** a position is closed successfully
**When** the sell order is filled
**Then** I receive a notification: "Sold {shares} {ETF} @ ${price} | P&L: ${pnl}"
**And** severity is 'info' (desktop only)

**Given** no trade signal exists today
**When** the morning check completes
**Then** I receive a notification: "No trade today - Cash"
**And** severity is 'info' (desktop only)

**Given** a trade fails after all retries
**When** retries are exhausted
**Then** I receive a notification: "Trade FAILED: {error_message}"
**And** severity is 'error' (desktop + email + SMS)

**Given** a position mismatch is detected
**When** trading is halted
**Then** I receive a notification: "POSITION MISMATCH - Trading halted. Expected: {x}, Actual: {y}"
**And** severity is 'critical' (desktop + email + SMS)

---

### Story 2.7: Implement Daily Summary Notification

As a **trader (George)**,
I want **a daily summary notification after market close**,
So that **I have a single recap of the day's activity**.

**Acceptance Criteria:**

**Given** market close time is 4:00 PM ET
**When** 4:05 PM ET arrives
**Then** a daily summary notification is generated

**Given** a trade was executed today
**When** the summary is generated
**Then** it includes: signal type, ETF, entry price, exit price, P&L, percentage return
**And** it includes cumulative P&L for the month

**Given** no trade was executed today (cash day)
**When** the summary is generated
**Then** it states: "No trade today - Cash position"
**And** it includes cumulative P&L for the month

**Given** the daily summary is ready
**When** it is sent
**Then** it goes to desktop AND email (not SMS for routine summary)
**And** severity is 'info'

---

## Epic 3: Visibility & Tracking

**Goal:** Instant status visibility and persistent trade history.

**User Value:** Open the app and see green/yellow/red status in 3 seconds - no hunting through tabs. Track 30+ days of paper trades to validate the strategy before risking real money. See running P&L and compare "what would have happened" vs actual market.

**FRs covered:** FR4 (Paper Trading History), FR5 (3-Second Status Dashboard)

---

### Story 3.1: Create 3-Second Status Dashboard Component

As a **trader (George)**,
I want **a redesigned Today tab with instant status visibility**,
So that **I can see green/yellow/red status in 3 seconds without scrolling**.

**Acceptance Criteria:**

**Given** I open the BTrade dashboard
**When** the Today tab loads
**Then** I see a large status indicator at the top (visible without scrolling)
**And** the indicator is Green (trade executed), Yellow (cash/no signal), or Red (error/halted)

**Given** a trade was executed today
**When** I view the status
**Then** I see a GREEN indicator
**And** I see primary metric: "Today's P&L: +$127.40"
**And** I see one-sentence status: "BITX sold at 3:55 PM +6.4%"

**Given** no trade signal exists today
**When** I view the status
**Then** I see a YELLOW indicator
**And** I see primary metric: "No trade today"
**And** I see one-sentence status: "Cash position - no signal"

**Given** an error has occurred
**When** I view the status
**Then** I see a RED indicator
**And** I see primary metric: "Action Required"
**And** I see one-sentence status with error summary

**Given** the dashboard is viewed on mobile browser
**When** the page loads
**Then** the status indicator and primary metric are visible without scrolling

---

### Story 3.2: Implement Status Dashboard Logic

As a **developer**,
I want **the status dashboard to fetch and display current state correctly**,
So that **the visual indicators accurately reflect bot status**.

**Acceptance Criteria:**

**Given** I create src/components/status_dashboard.py
**When** it is imported
**Then** it provides a render_status_dashboard() function for Streamlit

**Given** the bot has no position and no error
**When** get_current_status() is called
**Then** it returns status='cash', indicator='yellow', message='No trade today'

**Given** the bot executed a trade and closed it
**When** get_current_status() is called
**Then** it returns status='traded', indicator='green', pnl=calculated_value
**And** message includes ETF, action, and P&L

**Given** the bot is in HALTED state
**When** get_current_status() is called
**Then** it returns status='halted', indicator='red', message=halt_reason

**Given** a trade failed
**When** get_current_status() is called
**Then** it returns status='error', indicator='red', message=error_details

**Given** the bot currently holds a position (market hours)
**When** get_current_status() is called
**Then** it returns status='holding', indicator='green'
**And** message shows current position and unrealized P&L

---

### Story 3.3: Migrate Paper Trades to Persistent Database

As a **trader (George)**,
I want **paper trades stored in the database instead of session state**,
So that **I can track paper trading performance across app restarts**.

**Acceptance Criteria:**

**Given** paper trading mode is active
**When** a paper trade is executed
**Then** it is inserted into trade_history table with mode='paper'
**And** all fields are populated (date, signal, etf, shares, entry_price)

**Given** a paper position is closed
**When** the sell is simulated
**Then** the trade_history record is updated with exit_price and pnl
**And** the trade is marked complete

**Given** the app restarts during paper trading
**When** paper mode resumes
**Then** previous paper trades are loaded from trade_history
**And** cumulative P&L is correctly calculated from history

**Given** paper trades exist in the old session-based format
**When** the app starts with migration enabled
**Then** any existing paper trade data is migrated to database
**And** session-based storage is no longer used for trades

---

### Story 3.4: Implement Paper Trading History View

As a **trader (George)**,
I want **to see my paper trading history with running P&L**,
So that **I can validate the strategy over 30+ days before risking real money**.

**Acceptance Criteria:**

**Given** paper trades exist in the database
**When** I view the paper trading history
**Then** I see a table with columns: Date, Signal, ETF, Entry, Exit, Return%, P&L
**And** trades are sorted by date (newest first)

**Given** I have 30+ days of paper trading
**When** I view the summary
**Then** I see total trades count
**And** I see win rate percentage
**And** I see cumulative P&L in dollars
**And** I see cumulative return percentage

**Given** paper trades exist
**When** I view the history
**Then** each trade row shows return colored (green positive, red negative)
**And** the cumulative P&L chart is visible

**Given** no paper trades exist
**When** I view the history
**Then** I see a message: "No paper trades yet. Paper trading begins at next signal."

---

### Story 3.5: Add Paper Trading "Days Until Live" Counter

As a **trader (George)**,
I want **to see how many paper trading days remain before I can trade live**,
So that **I know when the 30-day validation period is complete**.

**Acceptance Criteria:**

**Given** paper trading has been active for N days (N < 30)
**When** I view the paper trading summary
**Then** I see: "Paper trading: {N}/30 days | {30-N} days until live trading eligible"

**Given** paper trading has been active for 30+ days
**When** I view the paper trading summary
**Then** I see: "Paper trading: 30-day validation complete!"
**And** I see a summary of performance during the 30 days
**And** I see an option to enable live trading

**Given** paper trading had zero missed signals in 30 days
**When** the milestone is reached
**Then** a notification is sent: "30-day paper trading complete! Zero missed signals. Ready for live trading."

**Given** missed signals occurred during paper trading
**When** the 30-day mark is reached
**Then** the count shows: "Paper trading: 30 days | {X} missed signals"
**And** a warning suggests extending paper trading

---

### Story 3.6: Integrate Status Dashboard into Main App

As a **trader (George)**,
I want **the new status dashboard to replace the current Today tab design**,
So that **the 3-second status check is my primary view**.

**Acceptance Criteria:**

**Given** the app opens
**When** the Today tab is displayed (default)
**Then** the new status_dashboard component renders
**And** the old Today tab content is moved below the status indicator

**Given** the status dashboard is displayed
**When** I scroll down
**Then** I see additional details: current signal info, previous day return, ETF quote

**Given** I'm on a different tab (Trading, Backtest, Strategy)
**When** I click on Today tab
**Then** the status dashboard loads within 2 seconds (NFR2)

**Given** the bot is in HALTED state
**When** I view the status dashboard
**Then** the "Acknowledge & Resume" button is prominently displayed
**And** clicking it triggers the reconciliation check

---

## Epic 4: Risk Controls

**Goal:** Position sizing and emergency stop capability.

**User Value:** Control how much of your capital is at risk per trade (25/50/75/100%). See estimated max loss before execution. Have a panic button to halt all trading instantly and close positions.

**FRs covered:** FR6 (Position Sizing), FR7 (Kill Switch)

---

### Story 4.1: Implement Configuration Module

As a **developer**,
I want **a centralized configuration module for bot settings**,
So that **settings are persisted and easily accessible across components**.

**Acceptance Criteria:**

**Given** I need to manage bot settings
**When** I create src/config.py
**Then** it provides Config class with settings management
**And** settings are persisted to database (not just session state)

**Given** a setting needs to be read
**When** I call config.get('position_size')
**Then** it returns the persisted value from database
**And** if no value exists, it returns the default

**Given** a setting needs to be updated
**When** I call config.set('position_size', 50)
**Then** the value is immediately persisted to database
**And** subsequent reads return the new value

**Given** the database has no settings
**When** the app starts
**Then** default settings are initialized (position_size=50, etc.)

---

### Story 4.2: Add Position Sizing Configuration

As a **trader (George)**,
I want **to configure what percentage of my capital to use per trade**,
So that **I can limit my exposure and manage risk**.

**Acceptance Criteria:**

**Given** I access the settings panel
**When** I view position sizing options
**Then** I see a dropdown with: 25%, 50% (default), 75%, 100%
**And** the current setting is highlighted

**Given** I select a new position size (e.g., 25%)
**When** I confirm the change
**Then** the setting is persisted via config.py
**And** a message confirms: "Position size updated to 25%"

**Given** position size is set to 50%
**When** a trade signal triggers
**Then** the bot uses 50% of available capital for the order
**And** remaining capital stays as cash

**Given** I have $10,000 available and position size is 50%
**When** BITX is $40/share
**Then** the bot calculates: floor($5,000 / $40) = 125 shares
**And** actual order uses $5,000 maximum

---

### Story 4.3: Display Max Loss Estimate Before Trade

As a **trader (George)**,
I want **to see estimated maximum loss before a trade executes**,
So that **I understand my risk exposure**.

**Acceptance Criteria:**

**Given** a trade signal exists
**When** I view the Trading tab
**Then** I see: "Estimated max loss: ${amount} ({percent}% of position)"
**And** the calculation uses historical worst-day performance

**Given** mean reversion signal for BITX
**When** max loss is calculated
**Then** it uses: position_value × worst_historical_daily_return
**And** for BITX (2x leverage), worst case is approximately -15%

**Given** short Thursday signal for SBIT
**When** max loss is calculated
**Then** it uses position_value × worst_historical_daily_return
**And** for SBIT (2x inverse), worst case is approximately -20%

**Given** position size is 50% and capital is $10,000
**When** max loss is calculated for BITX
**Then** it shows: "$750 (15% of $5,000 position)"
**And** this is displayed BEFORE trade execution

---

### Story 4.4: Implement Kill Switch Core

As a **trader (George)**,
I want **an emergency stop button that halts all trading immediately**,
So that **I can stop the bot instantly if something goes wrong**.

**Acceptance Criteria:**

**Given** I access the settings panel or trading controls
**When** I view kill switch option
**Then** I see a prominent "STOP ALL TRADING" button
**And** the button is styled in red/warning colors

**Given** I click "STOP ALL TRADING"
**When** the action is triggered
**Then** a confirmation dialog appears: "Are you sure? This will close all positions and halt trading."

**Given** I confirm the kill switch activation
**When** the action executes
**Then** state_manager sets state to 'KILLED'
**And** the scheduler stops all pending jobs
**And** a notification is sent (severity='critical'): "Kill switch activated. Trading halted."

**Given** the kill switch is activated
**When** any scheduled job tries to run
**Then** it checks state first
**And** returns early without trading if state is 'KILLED'

---

### Story 4.5: Implement Position Closing on Kill Switch

As a **trader (George)**,
I want **the kill switch to close any open positions immediately**,
So that **I'm not left holding positions after halting the bot**.

**Acceptance Criteria:**

**Given** the kill switch is activated
**When** there is an open position
**Then** a market sell order is placed immediately
**And** the close attempt is logged

**Given** the position close succeeds
**When** the sell order fills
**Then** state_manager clears the position
**And** notification includes: "Position closed: Sold {shares} {ETF} @ ${price}"

**Given** the position close fails (API error)
**When** the sell order fails
**Then** the error is logged
**And** notification includes: "WARNING: Failed to close position. Manual action required."
**And** state remains 'KILLED' (trading still halted)

**Given** there is no open position
**When** kill switch is activated
**Then** it simply halts the scheduler
**And** notification confirms: "Trading halted. No positions to close."

---

### Story 4.6: Implement Kill Switch Resume

As a **trader (George)**,
I want **to resume trading after a kill switch activation**,
So that **the bot can continue after I've addressed the issue**.

**Acceptance Criteria:**

**Given** the bot is in 'KILLED' state
**When** I view the dashboard
**Then** I see a message: "Trading halted via kill switch"
**And** I see a "Resume Trading" button

**Given** I click "Resume Trading"
**When** the action is triggered
**Then** a confirmation appears: "Resume automated trading?"
**And** it warns that positions may be opened at next signal

**Given** I confirm resume
**When** the action executes
**Then** reconcile() is called to verify broker state
**And** if match=True, state changes from 'KILLED' to 'IDLE'
**And** scheduler resumes normal operation
**And** notification confirms: "Trading resumed"

**Given** resume is attempted but positions don't match
**When** reconcile() returns match=False
**Then** resume fails
**And** state changes to 'HALTED' (requires acknowledgment)
**And** message indicates mismatch detected during resume

---

## Epic 5: Onboarding & Compliance

**Goal:** Easy setup for new users and tax export functionality.

**User Value:** Non-technical users can connect E*TRADE via guided wizard without developer knowledge. Export trade history in TurboTax-compatible format for tax filing.

**FRs covered:** FR8 (Setup Wizard), FR9 (Tax Export)

---

### Story 5.1: Implement Secure Credential Storage

As a **trader (George)**,
I want **my E*TRADE API credentials stored securely in OS keychain**,
So that **my credentials are protected and not stored in plaintext files**.

**Acceptance Criteria:**

**Given** I need secure credential storage
**When** I create src/credentials.py
**Then** it uses the `keyring` package for OS-level secure storage
**And** it provides store_credential() and get_credential() functions

**Given** I store an API key
**When** I call store_credential('etrade_api_key', 'my-key')
**Then** it is stored in macOS Keychain (or Windows Credential Manager)
**And** the credential is NOT stored in any file

**Given** I need to retrieve a credential
**When** I call get_credential('etrade_api_key')
**Then** it returns the stored value from keychain
**And** returns None if the credential doesn't exist

**Given** the keychain is unavailable (headless server)
**When** credential storage is attempted
**Then** it falls back to encrypted file storage
**And** logs a warning about reduced security

**Given** credentials are logged accidentally
**When** any log entry is created
**Then** credential values are redacted/masked
**And** only "***REDACTED***" appears in logs

---

### Story 5.2: Create First-Run Setup Wizard - API Keys

As a **new user (Jake from Crypto Twitter)**,
I want **a guided wizard for entering E*TRADE API credentials**,
So that **I can set up the bot without reading technical documentation**.

**Acceptance Criteria:**

**Given** the app detects no stored credentials
**When** the app starts
**Then** the setup wizard launches automatically
**And** a welcome message explains what BTrade does

**Given** the wizard is on Step 1 (API Keys)
**When** I view the step
**Then** I see a link to E*TRADE Developer Account page
**And** I see instructions with screenshots for creating an app
**And** I see input fields for Consumer Key and Consumer Secret

**Given** I enter my API credentials
**When** I click "Save Credentials"
**Then** credentials are stored via credentials.py (keychain)
**And** credentials are never logged or displayed after entry

**Given** I leave credential fields empty
**When** I try to proceed
**Then** validation prevents continuing
**And** a message indicates required fields

---

### Story 5.3: Create First-Run Setup Wizard - OAuth Connection

As a **new user (Jake)**,
I want **to complete OAuth authorization with E*TRADE**,
So that **the bot can access my account**.

**Acceptance Criteria:**

**Given** API credentials are saved
**When** the wizard proceeds to Step 2 (OAuth)
**Then** I see a "Connect to E*TRADE" button
**And** I see explanation of what permissions are requested

**Given** I click "Connect to E*TRADE"
**When** OAuth flow begins
**Then** a browser window opens to E*TRADE login
**And** after login, I receive an authorization code
**And** I can paste the code back into the wizard

**Given** I enter the authorization code
**When** I click "Verify"
**Then** the app exchanges code for access/refresh tokens
**And** tokens are stored securely in keychain
**And** a success message confirms connection

**Given** OAuth verification fails
**When** the error occurs
**Then** a clear error message is displayed
**And** suggestions for common issues are shown
**And** I can retry the OAuth flow

---

### Story 5.4: Create First-Run Setup Wizard - Test Connection

As a **new user (Jake)**,
I want **to test that my E*TRADE connection works**,
So that **I know the bot can access my account before trading**.

**Acceptance Criteria:**

**Given** OAuth connection is successful
**When** the wizard proceeds to Step 3 (Test)
**Then** I see a "Test Connection" button
**And** I see explanation of what the test does

**Given** I click "Test Connection"
**When** the test runs
**Then** it fetches account info from E*TRADE API
**And** it displays: Account name, Account type, Cash balance
**And** a green checkmark confirms success

**Given** the test succeeds
**When** I view the results
**Then** I see: "✓ Connected to E*TRADE"
**And** I see a toggle: "Environment: Sandbox / Production"
**And** Sandbox is selected by default for safety

**Given** the test fails
**When** the error occurs
**Then** a clear error message is displayed
**And** I can go back and re-enter credentials
**And** common issues are listed (expired token, wrong environment)

---

### Story 5.5: Create First-Run Setup Wizard - Completion

As a **new user (Jake)**,
I want **the setup wizard to complete and start paper trading**,
So that **I can begin validating the strategy immediately**.

**Acceptance Criteria:**

**Given** connection test is successful
**When** the wizard proceeds to Step 4 (Complete)
**Then** I see a summary of configuration
**And** I see the disclaimer: "BTrade is not financial advice..."
**And** I see a checkbox to acknowledge the disclaimer

**Given** I acknowledge the disclaimer
**When** I click "Start Paper Trading"
**Then** wizard closes and main dashboard appears
**And** paper trading mode is enabled
**And** a message confirms: "Setup complete! Paper trading will begin at next signal."

**Given** setup is complete
**When** I return to the app later
**Then** the wizard does not appear again
**And** credentials are loaded from keychain automatically

**Given** I want to reconfigure credentials later
**When** I access Settings > Reconfigure E*TRADE
**Then** the wizard reopens for credential update

---

### Story 5.6: Implement Tax Export - CSV Generation

As a **trader (George)**,
I want **to export my trade history as a CSV file**,
So that **I can import it into tax software for filing**.

**Acceptance Criteria:**

**Given** trade history exists in the database
**When** I access the export function
**Then** I see options: Export Year (dropdown), Include (Paper/Live/Both)

**Given** I select a year and click "Export CSV"
**When** the export runs
**Then** a CSV file is downloaded: btrade_trades_{year}.csv
**And** the file includes headers: Date, Action, Symbol, Quantity, Price, Proceeds, Cost Basis, Gain/Loss, Term

**Given** trades exist for the selected year
**When** the CSV is generated
**Then** each row represents one complete trade (buy+sell)
**And** Date is the sell date
**And** Proceeds = sell_price × quantity
**And** Cost Basis = buy_price × quantity
**And** Gain/Loss = Proceeds - Cost Basis
**And** Term = "Short" (all trades are intraday)

**Given** no trades exist for the selected year
**When** export is attempted
**Then** a message indicates: "No trades found for {year}"

---

### Story 5.7: Implement Tax Export - FIFO Cost Basis

As a **trader (George)**,
I want **cost basis calculated using FIFO method**,
So that **my tax reporting is accurate and IRS-compliant**.

**Acceptance Criteria:**

**Given** all trades are intraday (buy and sell same day)
**When** cost basis is calculated
**Then** it uses the exact entry price for that day's trade
**And** FIFO is trivially correct (one buy matches one sell)

**Given** the export is generated
**When** I review the data
**Then** each trade shows the actual entry price paid
**And** gain/loss matches: exit_price - entry_price per share

**Given** future edge case of multiple lots
**When** cost basis calculation runs
**Then** it uses FIFO (First In, First Out) ordering
**And** oldest shares are sold first

---

### Story 5.8: Implement Tax Export - Annual Summary

As a **trader (George)**,
I want **an annual summary with total gains and losses**,
So that **I have a quick overview for tax purposes**.

**Acceptance Criteria:**

**Given** I export trades for a year
**When** the export completes
**Then** a summary section is included at the top of the file
**And** it shows: Total Trades, Winning Trades, Losing Trades

**Given** the annual summary is generated
**When** I review it
**Then** I see: Total Short-Term Gains: ${amount}
**And** I see: Total Short-Term Losses: ${amount}
**And** I see: Net Short-Term Gain/Loss: ${amount}

**Given** I need to report to the IRS
**When** I use the export
**Then** the format is compatible with TurboTax import
**And** all amounts match Schedule D requirements
**And** "Short-Term" is clearly indicated (all trades < 1 day)

---

### Story 5.9: Add Export to Dashboard

As a **trader (George)**,
I want **tax export accessible from the dashboard**,
So that **I can easily export when tax season arrives**.

**Acceptance Criteria:**

**Given** I'm on the Trading or Strategy tab
**When** I look for export functionality
**Then** I see a "Tax Export" section or button

**Given** I click "Tax Export"
**When** the export dialog opens
**Then** I see year selection defaulting to current year
**And** I see mode filter: Paper, Live, or Both
**And** I see a "Download CSV" button

**Given** it's December or later
**When** I access Tax Export
**Then** current year is pre-selected
**And** a message reminds: "Exports available for years with completed trades"

**Given** I have both paper and live trades
**When** I select "Live Only"
**Then** only live trades are included in the export
**And** paper trades are excluded
