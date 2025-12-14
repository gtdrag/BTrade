---
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8]
inputDocuments:
  - docs/prd.md
workflowType: 'architecture'
lastStep: 8
status: 'complete'
completedAt: '2025-12-14'
project_name: 'BTrade'
user_name: 'George'
date: '2025-12-14'
---

# Architecture Decision Document

_This document builds collaboratively through step-by-step discovery. Sections are appended as we work through each architectural decision together._

## Project Context Analysis

### Requirements Overview

**Functional Requirements:**
9 FRs identified, prioritized as Critical (2), High (3), Medium (3), Low (1):
- Position reconciliation and state persistence are nuclear-risk preventers
- Alerting ensures "no silent failures" promise
- Dashboard redesign delivers "3-second status check" UX goal

**Non-Functional Requirements:**
5 NFR areas with quantified targets:
- Reliability: 99.9% trade execution, 100% scheduled jobs within 5-min window
- Performance: <2s dashboard load, <30s trade execution latency
- Security: OS keychain storage, encrypted OAuth, sanitized logs
- Testability: Simulation mode, chaos testing, time-travel testing
- Maintainability: Structured JSON logs, versioned migrations

**Scale & Complexity:**
- Primary domain: Fintech trading automation
- Complexity level: Medium-High
- Estimated architectural components: 8-10 (strategy, bot, scheduler, broker client, database, notifications, dashboard, config, reconciliation, state manager)

### Technical Constraints & Dependencies

**Existing Brownfield Stack:**
- Streamlit (dashboard) - constrains frontend architecture
- Python (backend) - language choice locked
- APScheduler (job scheduling) - current implementation
- SQLite (persistence) - lightweight, single-file database
- E*TRADE API (broker) - external dependency with OAuth complexity
- yfinance (market data) - free tier, rate limits possible

**External Dependencies:**
- E*TRADE API availability and rate limits
- Market data providers (yfinance reliability)
- Email/SMS delivery services (for notifications)

### Cross-Cutting Concerns Identified

| Concern | Affected Components | Architectural Implication |
|---------|---------------------|---------------------------|
| Error handling | All | Unified error taxonomy, retry policies, escalation paths |
| Logging | All | Structured logging, correlation IDs, audit trail |
| Notifications | Bot, Scheduler, Reconciliation | Central notification hub, channel routing |
| Auth state | Bot, Scheduler, Dashboard | Token refresh, session management, graceful degradation |
| Time zones | Strategy, Scheduler | Consistent ET handling, DST awareness |
| State consistency | Bot, DB, Broker | Transaction boundaries, reconciliation logic |

## Starter Template Evaluation

### Primary Technology Domain

**Brownfield Python Application** - Existing codebase with Streamlit dashboard and Python backend.

### Existing Stack Assessment

**Current Technologies (Keep):**
- Python 3.x - Backend language
- Streamlit - Dashboard framework
- APScheduler - Job scheduling
- SQLite - Persistent storage
- yfinance - Market data API
- E*TRADE API - Broker integration

**Stack Strengths:**
- Simple, focused architecture appropriate for single-user tool
- Python ecosystem provides all needed libraries
- Streamlit enables rapid dashboard iteration
- SQLite is portable and zero-config

**Stack Gaps Identified:**
- No OS-level secure credential storage
- No SMS notification capability
- No structured logging with correlation IDs
- No explicit state persistence for crash recovery
- No position reconciliation layer

### Enhancement Plan

**Add: Secure Credential Storage**
- Package: `keyring` (cross-platform OS keychain access)
- Supports macOS Keychain, Windows Credential Manager, Linux SecretService

**Add: SMS Notifications**
- Options: Twilio (reliable, simple API), Pushover (cheaper, mobile-focused)
- Recommendation: Twilio for critical trading alerts

**Add: Structured Logging**
- Package: `structlog` with JSON output
- Enables correlation IDs, audit trails, log aggregation

**Add: Position State Manager**
- Custom module wrapping SQLite
- Tracks expected positions, reconciles with broker
- Handles crash recovery and interrupted trades

### Initialization Command

```bash
# No starter command needed - brownfield project
# Enhancement dependencies to add:
pip install keyring structlog twilio
```

**Note:** First implementation story should add these dependencies and create the new modules.

## Core Architectural Decisions

### Decision Priority Analysis

**Critical Decisions (Block Implementation):**
- Position state schema design - Required for FR1 (Position Reconciliation)
- Error taxonomy - Required for FR3 (Proactive Alerting)
- OAuth token management - Required for reliable E*TRADE integration

**Important Decisions (Shape Architecture):**
- Dashboard component organization - Affects maintainability
- Retry policies - Affects reliability targets
- Logging strategy - Affects debugging and audit trail

**Deferred Decisions (Post-MVP):**
- Standalone app packaging (PyInstaller/Briefcase)
- Cloud deployment options
- Multi-broker abstraction

### Data Architecture

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Database** | SQLite (existing) | Appropriate for single-user, portable, zero-config |
| **Migration approach** | In-code version check | Simple for single-user app; check schema version on startup, migrate if needed |
| **ORM** | None (raw SQL) | Keep it simple; SQLite queries are straightforward |

**New Tables Required:**

```sql
-- Bot state for crash recovery
CREATE TABLE bot_state (
    id INTEGER PRIMARY KEY,
    position_symbol TEXT,
    position_shares INTEGER,
    position_entry_price REAL,
    position_entry_time TEXT,
    expected_action TEXT,  -- 'HOLDING', 'PENDING_BUY', 'PENDING_SELL'
    last_updated TEXT
);

-- Reconciliation audit log
CREATE TABLE reconciliation_log (
    id INTEGER PRIMARY KEY,
    timestamp TEXT,
    expected_position TEXT,
    broker_position TEXT,
    match BOOLEAN,
    action_taken TEXT
);

-- Persistent trade history (paper + live)
CREATE TABLE trade_history (
    id INTEGER PRIMARY KEY,
    date TEXT,
    mode TEXT,  -- 'paper' or 'live'
    signal TEXT,
    etf TEXT,
    action TEXT,
    shares INTEGER,
    entry_price REAL,
    exit_price REAL,
    pnl REAL,
    order_id TEXT
);
```

### Authentication & Security

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Credential storage** | `keyring` package | OS-level secure storage (macOS Keychain, Windows Credential Manager) |
| **Fallback storage** | Encrypted file | For environments without keychain access |
| **OAuth refresh** | Proactive + fallback | Refresh at 8 AM daily; retry on auth failure |
| **Token handling** | Never log, never plaintext | Security-first approach |

**Credential Storage Pattern:**
```python
import keyring

SERVICE_NAME = "btrade"

def store_credential(key: str, value: str):
    keyring.set_password(SERVICE_NAME, key, value)

def get_credential(key: str) -> str:
    return keyring.get_password(SERVICE_NAME, key)
```

### Error Handling & Communication

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Error taxonomy** | 3-tier (Recoverable/Fatal/PositionMismatch) | Clear escalation paths |
| **Retry policy** | 3 retries, exponential backoff | Balance reliability vs timeout |
| **Max retry window** | 5 minutes | Avoid missing trading window |
| **Notification routing** | All errors surface to user | "No silent failures" requirement |

**Error Classes:**
```python
class RecoverableError(Exception):
    """Retry with backoff - API timeout, rate limit"""
    pass

class FatalError(Exception):
    """Halt trading, notify immediately - auth failure, broker error"""
    pass

class PositionMismatchError(FatalError):
    """Special handling - requires manual acknowledgment"""
    pass
```

**Retry Configuration:**
- Initial delay: 1 second
- Backoff multiplier: 2x
- Max retries: 3
- Max total time: 5 minutes
- On exhaustion: Escalate to FatalError, send SMS

### Frontend Architecture

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Framework** | Streamlit (existing) | Works well, fast iteration |
| **Organization** | Component modules | Separate concerns, improve maintainability |
| **UI state** | `st.session_state` | Streamlit's built-in state management |
| **Persistent state** | SQLite via state manager | Clear boundary between UI and data |

**Component Structure:**
```
app.py                    # Main entry, tab routing
src/
  components/
    status_dashboard.py   # 3-second status (green/yellow/red)
    trading_controls.py   # Execute/close buttons
    backtest_view.py      # Backtest interface
    settings_panel.py     # Configuration UI
```

**State Boundaries:**
- `st.session_state` → UI-only (tab state, form inputs, temp data)
- SQLite → Persistent (positions, trades, settings, audit log)
- Never store position state in session_state

### Infrastructure & Deployment

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **MVP deployment** | Local macOS execution | Single-user tool, no server needed |
| **Launch method** | `streamlit run app.py` | Simple, familiar |
| **Auto-start** | Optional LaunchAgent | For always-on operation |
| **Logging output** | JSON to file | Structured, parseable |
| **Log retention** | 30 days, rotation | Balance history vs disk space |

**Logging Configuration:**
```python
import structlog

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ],
    logger_factory=structlog.WriteLoggerFactory(
        file=open("logs/btrade.log", "a")
    )
)
```

### Decision Impact Analysis

**Implementation Sequence:**
1. Add new dependencies (keyring, structlog, twilio)
2. Create database schema migrations
3. Implement state manager with reconciliation
4. Add structured logging throughout
5. Enhance notifications with SMS
6. Refactor dashboard to component structure
7. Implement 3-second status dashboard

**Cross-Component Dependencies:**
- State manager ← required by scheduler, trading bot, dashboard
- Structured logging ← used by all components
- Credential storage ← required by E*TRADE client
- Error taxonomy ← used by all components, drives notification routing

## Implementation Patterns & Consistency Rules

### Pattern Categories Defined

**Critical Conflict Points Identified:** 7 areas where implementation could vary

| Category | Conflict Area | Resolution |
|----------|---------------|------------|
| Naming | Table/column naming | snake_case always |
| Naming | Function/variable naming | PEP8 (snake_case) |
| Structure | Test file location | Separate `tests/` directory |
| Structure | New module organization | All modules in `src/` |
| Format | Log message structure | JSON via structlog |
| Process | Error handling | 3-tier taxonomy with specific catches |
| Process | State updates | Persist BEFORE external actions |

### Naming Patterns

**Database Naming Conventions:**
```sql
-- Tables: snake_case, plural
bot_state, trade_history, reconciliation_log

-- Columns: snake_case
position_symbol, entry_price, last_updated

-- Indexes: idx_{table}_{column}
idx_trade_history_date
```

**Python Naming Conventions (PEP8):**
```python
# Functions/variables: snake_case
def get_today_signal():
    current_price = 42.50

# Classes: PascalCase
class TradingBot:
    pass

# Constants: UPPER_SNAKE_CASE
MAX_RETRIES = 3
DEFAULT_TIMEOUT = 30

# Private methods/attributes: leading underscore
def _internal_helper():
    pass
```

**File Naming Conventions:**
```
# Modules: snake_case.py
smart_strategy.py
trading_bot.py
state_manager.py

# Test files: test_{module}.py
test_smart_strategy.py
test_trading_bot.py
```

### Structure Patterns

**Project Organization:**
```
ibit/
├── app.py                    # Streamlit entry point
├── src/
│   ├── __init__.py
│   ├── smart_strategy.py     # Signal logic
│   ├── trading_bot.py        # Trade execution
│   ├── smart_scheduler.py    # APScheduler jobs
│   ├── etrade_client.py      # Broker API
│   ├── database.py           # SQLite access
│   ├── notifications.py      # Email/desktop/SMS
│   ├── utils.py              # Helpers
│   ├── errors.py             # Error taxonomy (NEW)
│   ├── state_manager.py      # Position state (NEW)
│   ├── credentials.py        # Keychain access (NEW)
│   └── components/           # Streamlit UI (NEW)
│       ├── status_dashboard.py
│       ├── trading_controls.py
│       └── settings_panel.py
├── tests/                    # All tests here
│   ├── __init__.py
│   ├── test_smart_strategy.py
│   ├── test_trading_bot.py
│   └── test_state_manager.py
├── logs/                     # Log files (gitignored)
├── data/                     # SQLite database (gitignored)
└── docs/                     # Documentation
```

**File Structure Rules:**
- New Python modules → `src/`
- New UI components → `src/components/`
- New tests → `tests/test_{module}.py`
- Configuration → project root or `config/`
- Documentation → `docs/`

### Format Patterns

**Log Format (JSON via structlog):**
```python
{
    "timestamp": "2025-01-15T09:35:00-05:00",
    "level": "info",
    "event": "trade_executed",
    "correlation_id": "abc123",
    "data": {
        "signal": "mean_reversion",
        "etf": "BITX",
        "shares": 47,
        "price": 42.15
    }
}
```

**Log Event Naming Convention:**
```python
# Format: {action}_{object} or {object}_{state}
"trade_executed"
"trade_failed"
"position_reconciled"
"position_mismatch_detected"
"token_refreshed"
"token_expired"
"scheduler_started"
"scheduler_stopped"
```

**Date/Time Format:**
```python
# Internal: Always ISO 8601 with timezone
from datetime import datetime
from .utils import ET

timestamp = datetime.now(ET).isoformat()
# Result: "2025-01-15T09:35:00-05:00"

# Database storage: ISO string
# API/logs: ISO string
# Display: Format for readability
display_time = dt.strftime("%I:%M %p ET")  # "9:35 AM ET"
```

### Communication Patterns

**Notification Event Structure:**
```python
# All notifications use consistent structure
notification = {
    "type": "trade",  # trade, error, info, daily_summary
    "severity": "info",  # info, warning, error, critical
    "title": "Trade Executed",
    "message": "Bought 47 BITX @ $42.15",
    "data": {...},
    "timestamp": "2025-01-15T09:35:00-05:00"
}
```

**Channel Routing:**
- `info` → Desktop notification only
- `warning` → Desktop + Email
- `error` → Desktop + Email + SMS
- `critical` → Desktop + Email + SMS (immediate)

### Process Patterns

**Error Handling Pattern:**
```python
# ALWAYS catch specific exceptions, ordered by specificity
try:
    result = self.client.place_order(...)
except ETradeAuthError as e:
    # Fatal - halt and notify
    logger.error("auth_failed", error=str(e))
    raise FatalError(f"Authentication failed: {e}")
except ETradeAPIError as e:
    # Recoverable - will retry
    logger.warning("api_error", error=str(e))
    raise RecoverableError(f"API error: {e}")
except Exception as e:
    # Unknown - treat as fatal, log full traceback
    logger.exception("unexpected_error")
    raise FatalError(f"Unexpected: {e}")
```

**Retry Pattern (using tenacity):**
```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(RecoverableError),
    before_sleep=lambda retry_state: logger.info(
        "retry_scheduled",
        attempt=retry_state.attempt_number,
        wait=retry_state.next_action.sleep
    )
)
def execute_with_retry(self):
    ...
```

**State Persistence Pattern:**
```python
# CRITICAL: Always persist intent BEFORE external action
def execute_buy(self, etf: str, shares: int):
    # 1. Record intent (crash here = we know we were trying to buy)
    self.state_manager.set_pending_buy(etf, shares)

    # 2. Execute external action
    result = self.client.place_order(etf, shares, "BUY")

    # 3. Confirm completion (crash here = we check order status on restart)
    self.state_manager.confirm_buy(result.order_id, result.fill_price)

    return result
```

### Enforcement Guidelines

**All Code Changes MUST:**
1. Follow PEP8 naming conventions (enforced by ruff/flake8)
2. Use structured logging via `structlog` (never `print()`)
3. Persist state BEFORE external actions (state-action-confirm pattern)
4. Catch specific exceptions, never bare `except:`
5. Include context in all log entries
6. Use Eastern Time (`ET`) for all market-related timestamps
7. Add tests for new functionality in `tests/`

**Pattern Verification:**
- Pre-commit: `ruff check` for linting
- Pre-commit: `ruff format` for formatting
- CI: Run test suite
- Review: Check state persistence pattern in trading code

### Pattern Examples

**Good Example:**
```python
def execute_signal(self) -> TradeResult:
    signal = self.strategy.get_today_signal()

    if signal.signal == Signal.CASH:
        logger.info("no_signal_today", reason=signal.reason)
        return TradeResult(success=True, signal=Signal.CASH)

    try:
        # Persist intent
        self.state_manager.set_pending_buy(signal.etf, shares)
        logger.info("order_submitting", etf=signal.etf, shares=shares)

        # Execute
        result = self._execute_with_retry(signal.etf, shares)

        # Confirm
        self.state_manager.confirm_buy(result.order_id)
        logger.info("order_filled", order_id=result.order_id, price=result.price)

        return result

    except RecoverableError as e:
        logger.error("order_failed_recoverable", error=str(e))
        self.notifications.send_error(f"Trade failed: {e}")
        raise
    except FatalError as e:
        logger.critical("order_failed_fatal", error=str(e))
        self.notifications.send_critical(f"TRADING HALTED: {e}")
        self.state_manager.set_halted(str(e))
        raise
```

**Anti-Patterns to Avoid:**
```python
# BAD: print instead of logging
print(f"Buying {shares} shares")

# BAD: bare except
except:
    pass

# BAD: external action without state persistence
result = self.client.place_order(...)  # If we crash, we don't know what happened

# BAD: camelCase in Python
def getUserData():  # Should be get_user_data()

# BAD: hardcoded timezone
datetime.now()  # Should be datetime.now(ET)
```

## Project Structure & Boundaries

### Requirements to Structure Mapping

| Requirement | Module/Files |
|-------------|--------------|
| **FR1: Position Reconciliation** | `src/state_manager.py`, `src/reconciliation.py` |
| **FR2: State Persistence** | `src/state_manager.py`, `src/database.py` |
| **FR3: Proactive Alerting** | `src/notifications.py` (enhanced with SMS) |
| **FR4: Paper Trading History** | `src/database.py` (trade_history table) |
| **FR5: 3-Second Dashboard** | `src/components/status_dashboard.py` |
| **FR6: Position Sizing** | `src/trading_bot.py`, `src/config.py` |
| **FR7: Kill Switch** | `src/components/settings_panel.py`, `src/state_manager.py` |
| **FR8: Setup Wizard** | `src/components/setup_wizard.py` (future) |
| **FR9: Tax Export** | `src/export.py` (future) |

### Complete Project Directory Structure

```
ibit/
├── README.md                          # Project overview, quick start
├── requirements.txt                   # Python dependencies
├── pyproject.toml                     # Project metadata, tool config
├── .env.example                       # Environment template
├── .gitignore                         # Git ignores
├── .pre-commit-config.yaml           # Pre-commit hooks (ruff)
│
├── app.py                            # Streamlit entry point
│
├── src/
│   ├── __init__.py
│   │
│   │   # === CORE STRATEGY (existing) ===
│   ├── smart_strategy.py             # Signal generation logic
│   ├── utils.py                      # Helpers, timezone handling
│   │
│   │   # === TRADING EXECUTION (existing + enhanced) ===
│   ├── trading_bot.py                # Trade execution orchestration
│   ├── etrade_client.py              # E*TRADE API wrapper
│   ├── smart_scheduler.py            # APScheduler job management
│   │
│   │   # === DATA LAYER (existing + enhanced) ===
│   ├── database.py                   # SQLite access, schema migrations
│   │
│   │   # === NEW: RELIABILITY LAYER ===
│   ├── state_manager.py              # Position state, crash recovery
│   ├── reconciliation.py             # Broker position verification
│   ├── errors.py                     # Error taxonomy (Recoverable/Fatal)
│   │
│   │   # === NEW: SECURITY LAYER ===
│   ├── credentials.py                # Keychain access via keyring
│   │
│   │   # === NOTIFICATIONS (enhanced) ===
│   ├── notifications.py              # Email, desktop, SMS via Twilio
│   │
│   │   # === NEW: CONFIGURATION ===
│   ├── config.py                     # Settings, position sizing, thresholds
│   │
│   │   # === NEW: LOGGING ===
│   ├── logging_config.py             # structlog configuration
│   │
│   │   # === NEW: UI COMPONENTS ===
│   └── components/
│       ├── __init__.py
│       ├── status_dashboard.py       # 3-second status (green/yellow/red)
│       ├── trading_controls.py       # Execute/close buttons
│       ├── backtest_view.py          # Backtest interface
│       ├── settings_panel.py         # Configuration + kill switch
│       └── trade_history.py          # Paper/live trade log view
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py                   # Pytest fixtures
│   │
│   │   # === UNIT TESTS ===
│   ├── test_smart_strategy.py        # Signal logic tests
│   ├── test_trading_bot.py           # Execution tests
│   ├── test_state_manager.py         # State persistence tests
│   ├── test_reconciliation.py        # Position matching tests
│   ├── test_notifications.py         # Notification routing tests
│   │
│   │   # === INTEGRATION TESTS ===
│   ├── test_scheduler_integration.py # End-to-end scheduler tests
│   └── test_etrade_mock.py           # E*TRADE mock tests
│
├── data/                             # SQLite database (gitignored)
│   └── btrade.db
│
├── logs/                             # Log files (gitignored)
│   └── btrade.log
│
├── docs/
│   ├── prd.md                        # Product requirements
│   ├── architecture.md               # This document
│   ├── STRATEGY_VALIDATION_REPORT.md # Backtest analysis
│   └── PROFITABLE_STRATEGIES_REPORT.md
│
└── analysis/                         # Research scripts (existing)
    └── *.py
```

### Architectural Boundaries

**Application Layer Boundaries:**

```
┌─────────────────────────────────────────────────────────────────┐
│                         PRESENTATION                             │
│  app.py + src/components/*                                       │
│  - Streamlit UI only                                             │
│  - Calls business layer, never data layer directly               │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                          BUSINESS                                │
│  smart_strategy.py | trading_bot.py | smart_scheduler.py        │
│  - All trading logic lives here                                  │
│  - Orchestrates data access and external APIs                    │
└─────────────────────────────────────────────────────────────────┘
                              │
           ┌──────────────────┼──────────────────┐
           ▼                  ▼                  ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│   DATA ACCESS   │  │   EXTERNAL API  │  │  NOTIFICATIONS  │
│   database.py   │  │  etrade_client  │  │  notifications  │
│ state_manager.py│  │   yfinance      │  │  (email/SMS)    │
└─────────────────┘  └─────────────────┘  └─────────────────┘
```

**Data Flow:**

```
Signal Generation:
  yfinance → smart_strategy.py → TodaySignal

Trade Execution:
  TodaySignal → state_manager (persist) → etrade_client (execute)
             → state_manager (confirm) → notifications (alert)
             → database (log)

Position Reconciliation:
  etrade_client (positions) → reconciliation.py (compare)
                           → state_manager (expected)
                           → mismatch? → notifications (critical)
```

### Integration Points

**Internal Communication:**

| From | To | Method |
|------|-----|--------|
| `app.py` | `trading_bot` | Direct function calls |
| `trading_bot` | `state_manager` | Direct function calls |
| `trading_bot` | `etrade_client` | Direct function calls |
| `scheduler` | `trading_bot` | Scheduled job invocation |
| `*` | `notifications` | Direct function calls |
| `*` | `database` | Via state_manager or direct |

**External Integrations:**

| Service | Module | Purpose |
|---------|--------|---------|
| E*TRADE API | `etrade_client.py` | Trading, positions, account |
| yfinance | `smart_strategy.py` | Historical prices, quotes |
| Twilio | `notifications.py` | SMS alerts |
| SMTP | `notifications.py` | Email alerts |
| macOS Keychain | `credentials.py` | Secure credential storage |

### File Organization Patterns

**Configuration Files:**
```
.env.example          # Template (committed)
.env                  # Actual secrets (gitignored)
pyproject.toml        # Tool configuration (ruff, pytest)
.pre-commit-config.yaml  # Git hooks
```

**Source Organization:**
- Core modules at `src/` root level
- UI components in `src/components/`
- One module per concern (single responsibility)
- Cross-cutting modules (`errors.py`, `logging_config.py`) at root

**Test Organization:**
- Mirror source structure: `tests/test_{module}.py`
- Shared fixtures in `conftest.py`
- Integration tests named `test_*_integration.py`

### Module Dependency Graph

```
                    ┌─────────────┐
                    │   app.py    │
                    └──────┬──────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
   ┌────────────┐   ┌────────────┐   ┌────────────┐
   │ components │   │trading_bot │   │  scheduler │
   └────────────┘   └─────┬──────┘   └─────┬──────┘
                          │                │
          ┌───────────────┼───────┬────────┘
          ▼               ▼       ▼
   ┌────────────┐  ┌────────────┐ ┌────────────────┐
   │smart_strat │  │state_mgr   │ │ notifications  │
   └──────┬─────┘  └─────┬──────┘ └────────────────┘
          │              │
          ▼              ▼
   ┌────────────┐  ┌────────────┐
   │ yfinance   │  │ database   │
   └────────────┘  └────────────┘

   ┌────────────┐  ┌────────────┐
   │etrade_clnt │  │credentials │
   └────────────┘  └────────────┘
```

## Architecture Validation Results

### Coherence Validation ✅

**Decision Compatibility:**
All technology choices verified compatible:
- Python + Streamlit: Native integration
- SQLite + keyring: Both work on macOS/Windows
- APScheduler + Streamlit: Background scheduler runs independently
- structlog + JSON: Perfect match for structured logging
- tenacity + error taxonomy: Retry decorator works with error classes

**Pattern Consistency:**
- Naming: PEP8 throughout (snake_case functions, PascalCase classes)
- Structure: All patterns align with Python project conventions
- Error handling: 3-tier taxonomy consistently applied
- Logging: JSON format with structlog everywhere

**Structure Alignment:**
- Project structure supports all modules
- Clear separation: presentation → business → data layers
- Test structure mirrors source structure

### Requirements Coverage Validation ✅

**Functional Requirements Coverage:**

| Requirement | Architectural Support | Status |
|-------------|----------------------|--------|
| FR1: Position Reconciliation | `state_manager.py` + `reconciliation.py` | ✅ |
| FR2: State Persistence | SQLite tables + state_manager | ✅ |
| FR3: Proactive Alerting | notifications.py + channel routing | ✅ |
| FR4: Paper Trading History | trade_history table + database.py | ✅ |
| FR5: 3-Second Dashboard | status_dashboard.py component | ✅ |
| FR6: Position Sizing | config.py + trading_bot.py | ✅ |
| FR7: Kill Switch | settings_panel.py + state_manager | ✅ |
| FR8: Setup Wizard | Deferred to post-MVP | ⏳ |
| FR9: Tax Export | Deferred to post-MVP | ⏳ |

**Non-Functional Requirements Coverage:**

| NFR | Architectural Support | Status |
|-----|----------------------|--------|
| Reliability (99.9%) | Retry patterns, state persistence, reconciliation | ✅ |
| Performance (<2s load) | Streamlit + local SQLite | ✅ |
| Security | keyring, encrypted storage, sanitized logs | ✅ |
| Testability | Test structure, mock patterns defined | ✅ |
| Maintainability | structlog, modular design, PEP8 | ✅ |

### Implementation Readiness Validation ✅

**Decision Completeness:**
- All critical decisions documented with rationale
- Technology versions can be pinned in requirements.txt
- Code examples provided for major patterns
- Anti-patterns explicitly called out

**Structure Completeness:**
- Complete directory tree with all files
- New modules clearly marked (NEW tag)
- Layer boundaries diagrammed
- Data flow documented

**Pattern Completeness:**
- State-action-confirm pattern for trading
- Error taxonomy with escalation paths
- Notification routing by severity
- Log event naming convention

### Gap Analysis Results

**Critical Gaps:** None

**Minor Gaps (Non-blocking):**

| Gap | Priority | Resolution |
|-----|----------|------------|
| Pre-commit hook config | Low | Add in first implementation story |
| pyproject.toml | Low | Add in first implementation story |
| tenacity dependency | Low | Add to requirements.txt |

**Deferred Items (By Design):**
- FR8: Setup Wizard → Post-MVP
- FR9: Tax Export → Post-MVP
- Standalone app packaging → Vision phase

### Architecture Completeness Checklist

**✅ Requirements Analysis**
- [x] Project context thoroughly analyzed
- [x] Scale and complexity assessed (Medium-High)
- [x] Technical constraints identified (Brownfield, Streamlit, E*TRADE)
- [x] Cross-cutting concerns mapped (6 identified)

**✅ Architectural Decisions**
- [x] Critical decisions documented (Data, Auth, Error Handling)
- [x] Technology stack fully specified (existing + 4 new packages)
- [x] Integration patterns defined (internal + external)
- [x] Performance considerations addressed (<30s trade execution)

**✅ Implementation Patterns**
- [x] Naming conventions established (PEP8)
- [x] Structure patterns defined (layers, modules)
- [x] Communication patterns specified (function calls, notifications)
- [x] Process patterns documented (state-action-confirm, retry)

**✅ Project Structure**
- [x] Complete directory structure defined
- [x] Component boundaries established (3 layers)
- [x] Integration points mapped (5 external services)
- [x] Requirements to structure mapping complete (9 FRs → modules)

### Architecture Readiness Assessment

**Overall Status:** READY FOR IMPLEMENTATION

**Confidence Level:** HIGH

**Key Strengths:**
1. Clear 3-layer architecture with defined boundaries
2. State persistence pattern prevents data loss on crashes
3. Comprehensive error taxonomy with automatic escalation
4. Explicit patterns prevent AI agent conflicts
5. Brownfield approach preserves working code

**Areas for Future Enhancement:**
1. Add simulation mode for backtesting specific dates
2. Consider cloud deployment architecture if demand exists
3. Multi-broker abstraction for future expansion

### Implementation Handoff

**AI Agent Guidelines:**
- Follow all architectural decisions exactly as documented
- Use implementation patterns consistently across all components
- Respect project structure and boundaries
- Refer to this document for all architectural questions
- Use state-action-confirm pattern for ALL trading operations

**First Implementation Priority:**
```bash
# 1. Add new dependencies
pip install keyring structlog tenacity twilio

# 2. Create new module files
touch src/errors.py
touch src/state_manager.py
touch src/reconciliation.py
touch src/credentials.py
touch src/config.py
touch src/logging_config.py
mkdir -p src/components
touch src/components/__init__.py
touch src/components/status_dashboard.py

# 3. Update database schema
# Add bot_state, reconciliation_log, trade_history tables
```

**Implementation Sequence:**
1. Add dependencies and create module stubs
2. Implement error taxonomy (`errors.py`)
3. Implement structured logging (`logging_config.py`)
4. Implement state manager with new tables (`state_manager.py`)
5. Implement position reconciliation (`reconciliation.py`)
6. Enhance notifications with SMS (`notifications.py`)
7. Build 3-second dashboard (`status_dashboard.py`)
8. Integrate all components and test

## Architecture Completion Summary

### Workflow Completion

**Architecture Decision Workflow:** COMPLETED ✅
**Total Steps Completed:** 8
**Date Completed:** 2025-12-14
**Document Location:** docs/architecture.md

### Final Architecture Deliverables

**Complete Architecture Document**
- All architectural decisions documented with specific rationale
- Implementation patterns ensuring AI agent consistency
- Complete project structure with all files and directories
- Requirements to architecture mapping (9 FRs → specific modules)
- Validation confirming coherence and completeness

**Implementation Ready Foundation**
- 25+ architectural decisions made
- 7 implementation pattern categories defined
- 10 new modules/components specified
- 9 functional requirements fully supported

**AI Agent Implementation Guide**
- Technology stack: Python, Streamlit, SQLite, APScheduler + keyring, structlog, tenacity, Twilio
- Consistency rules that prevent implementation conflicts
- Project structure with clear 3-layer boundaries
- Integration patterns and communication standards

### Quality Assurance Checklist

**✅ Architecture Coherence**
- [x] All decisions work together without conflicts
- [x] Technology choices are compatible
- [x] Patterns support the architectural decisions
- [x] Structure aligns with all choices

**✅ Requirements Coverage**
- [x] All MVP functional requirements supported (FR1-FR7)
- [x] All non-functional requirements addressed
- [x] Cross-cutting concerns handled (error handling, logging, notifications)
- [x] Integration points defined (E*TRADE, Twilio, yfinance)

**✅ Implementation Readiness**
- [x] Decisions are specific and actionable
- [x] Patterns prevent agent conflicts
- [x] Structure is complete and unambiguous
- [x] Code examples provided for clarity

### Project Success Factors

**Clear Decision Framework**
Every technology choice was made collaboratively with clear rationale, ensuring all stakeholders understand the architectural direction.

**Consistency Guarantee**
Implementation patterns and rules ensure that multiple AI agents will produce compatible, consistent code that works together seamlessly.

**Complete Coverage**
All project requirements are architecturally supported, with clear mapping from business needs to technical implementation.

**Brownfield Approach**
Preserves working existing code while adding the "confidence layer" (state persistence, reconciliation, alerting) needed for production use.

---

**Architecture Status:** READY FOR IMPLEMENTATION ✅

**Next Phase:** Begin implementation using the architectural decisions and patterns documented herein.

**Document Maintenance:** Update this architecture when major technical decisions are made during implementation.
