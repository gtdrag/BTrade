# Architecture Patterns: Wheel Strategy Integration

**Domain:** Options trading wheel strategy on existing equity trading bot
**Researched:** 2026-03-20
**Confidence:** HIGH (E*TRADE API verified, existing patterns analyzed, wheel strategy standards researched)

## Executive Summary

The wheel strategy can integrate cleanly with the existing mixin-based TradingBot architecture by following established patterns. The core challenge is extending equity-focused abstractions (position tracking, order execution, signal generation) to handle options-specific concerns (strike selection, assignment detection, Greeks monitoring, multi-leg cycles).

**Key finding:** The existing ExecutionMixin → E*TRADE flow works for options with minimal changes. The major new components are: (1) OptionsChainMixin for strike/expiration selection, (2) OptionsExecutionMixin for options order preview/placement, (3) WheelStrategyMixin for cycle management (put → assignment → call → repeat), and (4) database schema extensions for tracking options positions with assignment state.

**Architecture approach:** Parallel mixin hierarchy. Keep existing equity mixins untouched. Create new options-specific mixins that TradingBot composes. Strategy layer remains signal-driven but generates OptionsSignal instead of TodaySignal. Assignment detection runs as scheduled APScheduler job querying E*TRADE positions API.

## Recommended Architecture

### Component Boundaries

**Layered separation (maintain existing pattern):**

```
Strategy Layer (new: WheelStrategy)
      ↓
Execution Layer (new: OptionsExecutionMixin)
      ↓
Options Data Layer (new: OptionsChainMixin + E*TRADE options endpoints)
      ↓
Broker/Persistence (extend: ETradeClient options methods + Database schema)
```

**Mixin composition (TradingBot with options extensions):**

```python
class TradingBot(
    # Existing equity mixins (unchanged)
    OrdersMixin,
    PositionsMixin,
    ExecutionMixin,
    HedgeMixin,
    NotificationsMixin,

    # New options mixins (parallel hierarchy)
    OptionsChainMixin,      # Strike/exp selection, Greeks fetch
    OptionsExecutionMixin,  # Options order preview/place
    WheelCycleMixin,        # Full wheel cycle coordination
):
    """
    Mixin dependencies:
    - OptionsChainMixin requires: client (E*TRADE), data_manager, config
    - OptionsExecutionMixin requires: client, telegram, db, OptionsChainMixin
    - WheelCycleMixin requires: OptionsExecutionMixin, db, strategy (WheelStrategy)
    """
```

**Why this structure:**
- Maintains backward compatibility (equity mixins untouched)
- Each mixin has single responsibility
- Reuses existing patterns (approval flow, notification, database logging)
- Options functionality is additive, not invasive

### Data Flow: Cash-Secured Put Entry

```
1. APScheduler job fires (9:35 AM or signal-based time)
2. SmartScheduler calls WheelCycleMixin.execute_wheel_signal()
3. WheelStrategy.get_today_signal() evaluates:
   - Is IBIT at favorable entry? (pullback, elevated IV)
   - Do we have cash to sell puts?
   - Returns OptionsSignal(action=SELL_PUT, underlying=IBIT, target_delta=-0.28, dte=30-45)
4. WheelCycleMixin receives signal:
   a. Calls OptionsChainMixin.get_option_chain(underlying=IBIT, expiration_days=30-45)
   b. Selects strike with delta closest to -0.28 (cash-secured put)
   c. Calls OptionsExecutionMixin.execute_options_trade():
      - Fetches quote for selected option symbol
      - Calculates position size (contracts = cash_available / (strike * 100))
      - Requests Telegram approval with option details
      - If approved: preview_options_order() → place_options_order()
      - Polls for order fill (same pattern as equity orders)
      - Records to database (options_positions table)
   d. Logs signal check to database (event_log)
5. Telegram sends confirmation with option symbol, strike, premium collected, DTE
```

**Key difference from equity flow:** Option symbol is dynamic (selected at runtime from chain), not static like "BITU". Strike selection algorithm runs before execution.

### Data Flow: Assignment Detection → Covered Call

```
1. APScheduler job fires (daily at 8:00 AM ET, before market open)
2. SmartScheduler calls WheelCycleMixin.check_for_assignments()
3. Query E*TRADE positions API (client.get_account_positions())
4. Compare positions vs yesterday's snapshot (from database):
   - If short put position qty changed from -1 to 0: Assignment likely
   - If new IBIT equity position appeared (100 shares): Confirms assignment
5. WheelCycleMixin.handle_assignment():
   a. Update database: options_positions.status = 'assigned'
   b. Log assignment event (event_log)
   c. Generate OptionsSignal(action=SELL_CALL, underlying=IBIT, shares=100, target_delta=0.30, dte=30-45)
   d. Immediately execute covered call (same flow as put entry, different option type)
6. Telegram notification: "Put assigned! Acquired 100 IBIT @ $strike. Selling covered call..."
```

**Key insight:** Assignment detection is position diffing, not event-based. E*TRADE API doesn't push assignment notifications. Must poll positions daily and compare to database snapshot.

### Data Flow: Position Monitoring & Early Exit

```
1. APScheduler job fires (every 30 min during market hours)
2. WheelCycleMixin.monitor_options_positions():
   a. Query open options positions from database
   b. For each position:
      - Fetch current option quote (E*TRADE or Alpaca)
      - Calculate unrealized P&L vs entry premium
      - Check if profit target hit (e.g., 50% of max profit)
   c. If profit target hit:
      - Generate OptionsSignal(action=CLOSE, option_symbol=...)
      - Execute buy-to-close order (reverse of sell-to-open)
      - Update database: options_positions.status = 'closed', exit_price, exit_time, pnl
3. Telegram notification: "Closed IBIT put @ 50% profit ($X premium)"
```

**Position management:** Similar to equity hedge monitoring (HedgeMixin), but triggers on premium decay (theta profit) instead of price reversal.

## New Components

### 1. OptionsChainMixin

**Purpose:** Fetch options chains, select strikes/expirations, retrieve Greeks

**Location:** `src/trading_bot/options_chain_mixin.py`

**Dependencies:**
- `client: ETradeClient` (for options chain API)
- `config: BotConfig` (for default DTE, delta targets)

**Key methods:**
```python
def get_option_chain(symbol: str, expiration_days: Tuple[int, int]) -> List[OptionContract]
def select_put_strike(chain: List[OptionContract], target_delta: float) -> OptionContract
def select_call_strike(chain: List[OptionContract], target_delta: float, current_price: float) -> OptionContract
def get_option_quote(option_symbol: str) -> OptionQuote  # Returns bid/ask/Greeks
```

**Data structures:**
```python
@dataclass
class OptionContract:
    symbol: str              # IBIT250418P00040000 (OCC format)
    underlying: str          # IBIT
    option_type: str         # PUT or CALL
    strike: float            # 40.00
    expiration: date         # 2025-04-18
    delta: Optional[float]   # -0.28 (for puts) or 0.30 (for calls)
    gamma: Optional[float]
    theta: Optional[float]
    vega: Optional[float]
    iv: Optional[float]      # Implied volatility
    bid: float
    ask: float
    last: float
    volume: int
    open_interest: int

@dataclass
class OptionQuote:
    symbol: str
    bid: float
    ask: float
    last: float
    bid_size: int
    ask_size: int
    delta: Optional[float]
    gamma: Optional[float]
    theta: Optional[float]
    vega: Optional[float]
    iv: Optional[float]
    timestamp: datetime
```

**E*TRADE integration:**
- Endpoint: `GET /v1/market/optionchains?symbol=IBIT`
- Query params: `expiryYear`, `expiryMonth`, `expiryDay`, `strikePriceNear`, `noOfStrikes`, `optionCategory=STANDARD`, `chainType=PUT|CALL|CALLPUT`
- Response includes Greeks (delta, gamma, theta, vega, rho, IV) per strike
- Filter to target DTE range (30-45 days), then select by delta

**Strike selection algorithm:**
```python
def select_put_strike(chain: List[OptionContract], target_delta: float = -0.28) -> OptionContract:
    """
    Select cash-secured put strike closest to target delta.

    Target delta -0.28 means ~28% probability of assignment.
    Filters to standard options, excludes weeklies if configured.
    """
    candidates = [opt for opt in chain if opt.option_type == "PUT" and opt.delta]
    # Sort by distance from target delta
    candidates.sort(key=lambda x: abs(x.delta - target_delta))
    return candidates[0] if candidates else None
```

### 2. OptionsExecutionMixin

**Purpose:** Preview and place options orders, handle fills, record trades

**Location:** `src/trading_bot/options_execution_mixin.py`

**Dependencies:**
- `client: ETradeClient`
- `telegram: TelegramNotifier`
- `db: Database`
- `OptionsChainMixin` (for quote fetching)

**Key methods:**
```python
def execute_options_trade(
    option_contract: OptionContract,
    action: str,  # SELL_TO_OPEN, BUY_TO_CLOSE, BUY_TO_OPEN, SELL_TO_CLOSE
    quantity: int,
    skip_approval: bool = False
) -> OptionsTradeResult

def preview_options_order(...) -> Dict  # Calls E*TRADE preview API
def place_options_order(...) -> Dict    # Calls E*TRADE place API
def _wait_for_options_fill(order_id: str) -> Optional[Dict]  # Poll for execution
```

**Data structures:**
```python
@dataclass
class OptionsTradeResult:
    success: bool
    option_symbol: str
    underlying: str
    action: str              # SELL_TO_OPEN, BUY_TO_CLOSE, etc.
    strike: float
    expiration: date
    option_type: str         # PUT or CALL
    quantity: int            # Number of contracts
    entry_premium: float     # Premium per contract
    total_premium: float     # entry_premium * quantity * 100
    order_id: Optional[str]
    error: Optional[str]
    is_paper: bool
    filled_at: Optional[datetime]
```

**E*TRADE integration:**
- Options orders use same preview/place flow as equities
- Endpoint: `POST /v1/accounts/{accountIdKey}/orders/place`
- Order payload differences:
  ```json
  {
    "orderType": "OPTN",  // Not "EQ"
    "Instrument": {
      "Product": {
        "securityType": "OPTN",
        "symbol": "IBIT",
        "callPut": "PUT",
        "expiryYear": 2025,
        "expiryMonth": 4,
        "expiryDay": 18,
        "strikePrice": 40
      },
      "orderAction": "SELL_SHORT",  // For sell-to-open puts
      "quantityType": "QUANTITY",
      "quantity": 1
    },
    "priceType": "LIMIT",
    "limitPrice": 1.50  // Credit received
  }
  ```

**Approval flow (reuse existing pattern):**
```python
def _request_telegram_approval_options(option_contract, action, quantity, premium):
    """
    Similar to equity approval but shows:
    - Option symbol (IBIT250418P00040000)
    - Strike/expiration in readable format (Apr 18 2025 $40 PUT)
    - Premium collected (credit) or paid (debit)
    - Greeks (delta, theta)
    - Collateral required (strike * 100 * quantity for CSP)
    """
    approval = self.telegram.request_approval(
        signal_type=f"{action} {option_contract.option_type}",
        etf=option_contract.underlying,
        reason=f"Strike ${option_contract.strike} Δ{option_contract.delta:.2f} {option_contract.expiration}",
        shares=quantity,  # Contracts
        price=premium,
        position_value=premium * quantity * 100  # Total credit/debit
    )
    # Same ApprovalResult handling as equity trades
```

### 3. WheelCycleMixin

**Purpose:** Coordinate full wheel cycle (put → assignment → call → repeat)

**Location:** `src/trading_bot/wheel_cycle_mixin.py`

**Dependencies:**
- `OptionsExecutionMixin`
- `db: Database`
- `strategy: WheelStrategy`
- `telegram: TelegramNotifier`

**Key methods:**
```python
def execute_wheel_signal(signal: OptionsSignal) -> OptionsTradeResult
def check_for_assignments() -> List[AssignmentEvent]
def handle_assignment(assignment: AssignmentEvent) -> OptionsTradeResult
def monitor_options_positions() -> List[OptionsTradeResult]  # For profit taking
def close_options_position(position_id: int, reason: str) -> OptionsTradeResult
```

**State machine (wheel cycle):**
```
State 1: CASH
  ↓ (signal: SELL_PUT, delta=-0.28, DTE=30-45)
State 2: SHORT_PUT
  ↓ (if assigned: put goes to 0 qty, shares appear)
State 3: HOLDING_SHARES
  ↓ (auto-trigger: SELL_CALL, delta=0.30, DTE=30-45)
State 4: COVERED_CALL
  ↓ (if called away: shares go to 0, call expires worthless)
State 1: CASH (repeat)

Early exits:
- SHORT_PUT → CASH (close at 50% profit)
- COVERED_CALL → HOLDING_SHARES (close call early, keep shares)
```

**Assignment detection logic:**
```python
def check_for_assignments() -> List[AssignmentEvent]:
    """
    Compare current positions vs yesterday's snapshot.

    Assignment indicators:
    1. Short put position qty = 0 (was -1 yesterday)
    2. New equity position in same underlying (100 shares)
    3. E*TRADE transaction history shows "Assignment" event
    """
    current_positions = self.client.get_account_positions(...)
    yesterday_positions = self.db.get_options_positions(status='open')

    assignments = []
    for yesterday_pos in yesterday_positions:
        if yesterday_pos.option_type == 'PUT' and yesterday_pos.action == 'SELL_TO_OPEN':
            # Check if this put position closed
            current_opt = find_position(current_positions, yesterday_pos.option_symbol)
            current_equity = find_position(current_positions, yesterday_pos.underlying)

            if not current_opt and current_equity and current_equity.shares == 100:
                # Assignment detected
                assignments.append(AssignmentEvent(
                    option_position_id=yesterday_pos.id,
                    underlying=yesterday_pos.underlying,
                    strike=yesterday_pos.strike,
                    shares_acquired=100,
                    detected_at=get_et_now()
                ))

    return assignments
```

**Data structures:**
```python
@dataclass
class AssignmentEvent:
    option_position_id: int  # Links to options_positions.id
    underlying: str          # IBIT
    strike: float            # Effective purchase price
    shares_acquired: int     # Always 100 per contract
    detected_at: datetime

@dataclass
class OptionsSignal:
    action: str              # SELL_PUT, SELL_CALL, CLOSE
    underlying: str          # IBIT
    target_delta: float      # -0.28 for puts, 0.30 for calls
    dte_range: Tuple[int, int]  # (30, 45)
    reason: str              # Human-readable reason
    contracts: int           # Quantity
    shares: Optional[int]    # Only set for covered calls (100)
```

### 4. WheelStrategy

**Purpose:** Generate wheel signals based on market conditions

**Location:** `src/wheel_strategy.py`

**Pattern:** Similar to `SmartStrategy` but evaluates options entry conditions

**Key methods:**
```python
def get_today_signal() -> OptionsSignal
def should_sell_put() -> bool
def should_sell_call(shares: int) -> bool
def get_put_parameters() -> Dict  # Returns target_delta, dte_range
def get_call_parameters(current_price: float, cost_basis: float) -> Dict
```

**Signal generation logic:**
```python
def get_today_signal() -> OptionsSignal:
    """
    Evaluate conditions for wheel entry.

    Signal-based put selling (not always active):
    - IBIT pulled back ≥2% from recent high
    - IV percentile > 30 (elevated premium)
    - No existing short put position
    - Sufficient cash for assignment (strike * 100)

    Covered call (always active when holding shares):
    - Holding 100 IBIT shares
    - No existing covered call
    - OTM target delta 0.30 (30% probability of being called)
    """
    # Check if we have shares (from assignment)
    positions = self.db.get_equity_positions(symbol='IBIT')
    if positions and positions[0].shares >= 100:
        return OptionsSignal(
            action='SELL_CALL',
            underlying='IBIT',
            target_delta=0.30,
            dte_range=(30, 45),
            reason='Holding shares from assignment',
            contracts=1,
            shares=100
        )

    # Check put entry conditions
    if self._should_sell_put():
        return OptionsSignal(
            action='SELL_PUT',
            underlying='IBIT',
            target_delta=-0.28,
            dte_range=(30, 45),
            reason=self._get_put_entry_reason(),
            contracts=1
        )

    return OptionsSignal(action='CASH', underlying='IBIT', reason='No favorable entry')
```

**Configuration:**
```python
@dataclass
class WheelStrategyConfig:
    # Strike selection
    put_delta_target: float = -0.28   # ~28% assignment probability
    call_delta_target: float = 0.30   # ~30% called probability

    # Expiration
    dte_min: int = 30                 # Minimum days to expiration
    dte_max: int = 45                 # Maximum days to expiration

    # Entry signals for puts
    pullback_threshold: float = -2.0  # Enter puts after ≥2% pullback
    iv_percentile_min: float = 30     # Only sell puts when IV > 30th percentile

    # Profit management
    profit_take_pct: float = 50       # Close at 50% of max profit

    # Position limits
    max_cash_allocation: float = 0.25 # Use max 25% of cash per put
```

## Modified Components

### 1. ETradeClient (extend)

**New methods to add:**
```python
def get_option_chains(
    symbol: str,
    expiry_year: Optional[int] = None,
    expiry_month: Optional[int] = None,
    option_type: str = "CALLPUT"
) -> Dict

def get_option_quote(option_symbol: str) -> Dict

def preview_options_order(
    account_id_key: str,
    symbol: str,  # Underlying
    call_put: str,
    expiry_year: int,
    expiry_month: int,
    expiry_day: int,
    strike_price: float,
    action: str,  # SELL_SHORT, BUY, SELL, BUY_TO_COVER
    quantity: int,
    order_type: str = "LIMIT",
    limit_price: Optional[float] = None
) -> Dict

def place_options_order(
    account_id_key: str,
    preview_ids: List[int],
    **kwargs  # Same params as preview
) -> Dict
```

**Implementation pattern (same as equity methods):**
```python
def get_option_chains(self, symbol: str, **kwargs) -> Dict:
    """Fetch options chains from E*TRADE."""
    if not self.session:
        raise ETradeAuthError("Not authenticated")

    url = f"{self.base_url}/v1/market/optionchains"
    params = {"symbol": symbol, **kwargs}

    response = self.session.get(url, params=params)
    response.raise_for_status()
    return response.json()
```

### 2. Database Schema (extend)

**New tables:**

```sql
-- Options positions (similar to trades table but options-specific)
CREATE TABLE IF NOT EXISTS options_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Option identification
    option_symbol TEXT NOT NULL,           -- IBIT250418P00040000 (OCC format)
    underlying TEXT NOT NULL,              -- IBIT
    option_type TEXT NOT NULL,             -- PUT or CALL
    strike REAL NOT NULL,                  -- 40.00
    expiration TEXT NOT NULL,              -- 2025-04-18 (ISO date)

    -- Trade details
    action TEXT NOT NULL,                  -- SELL_TO_OPEN, BUY_TO_CLOSE, etc.
    quantity INTEGER NOT NULL,             -- Number of contracts
    entry_premium REAL NOT NULL,           -- Premium per contract
    entry_time TEXT NOT NULL,
    exit_premium REAL,
    exit_time TEXT,

    -- Greeks at entry
    entry_delta REAL,
    entry_theta REAL,
    entry_iv REAL,

    -- Status tracking
    status TEXT NOT NULL DEFAULT 'open',   -- open, closed, assigned, expired
    assignment_date TEXT,                  -- When assignment occurred

    -- P&L
    pnl REAL,                              -- Total profit/loss
    pnl_pct REAL,                          -- % return on collateral

    -- Metadata
    is_paper INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Wheel cycle tracking (links puts → shares → calls)
CREATE TABLE IF NOT EXISTS wheel_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Cycle identification
    underlying TEXT NOT NULL,              -- IBIT
    cycle_start TEXT NOT NULL,             -- When put was opened
    cycle_end TEXT,                        -- When shares sold (call exercised)

    -- Linked positions
    put_position_id INTEGER,               -- FK to options_positions
    equity_position_id INTEGER,            -- FK to equity_positions (new table)
    call_position_id INTEGER,              -- FK to options_positions

    -- Cycle state
    status TEXT NOT NULL DEFAULT 'active', -- active, completed, closed_early
    current_state TEXT NOT NULL,           -- cash, short_put, holding_shares, covered_call

    -- Performance
    total_premium_collected REAL,          -- Sum of put + call premiums
    cost_basis REAL,                       -- Strike price of put (if assigned)
    shares_acquired INTEGER,               -- 100 if assigned, 0 if put closed
    final_pnl REAL,                        -- Total cycle P&L

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Equity positions (for tracking shares from assignment)
CREATE TABLE IF NOT EXISTS equity_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    symbol TEXT NOT NULL,                  -- IBIT
    shares INTEGER NOT NULL,
    entry_price REAL NOT NULL,             -- Cost basis per share
    entry_time TEXT NOT NULL,
    exit_price REAL,
    exit_time TEXT,

    -- Link to wheel cycle
    wheel_cycle_id INTEGER,                -- FK to wheel_cycles
    acquired_via TEXT,                     -- 'assignment', 'purchase', 'other'

    status TEXT NOT NULL DEFAULT 'open',   -- open, closed
    pnl REAL,
    pnl_pct REAL,

    is_paper INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

**New methods in Database class:**
```python
def record_options_trade_entry(...) -> int
def record_options_trade_exit(position_id: int, ...) -> None
def record_assignment(position_id: int, shares: int) -> int  # Returns equity_position_id
def get_open_options_positions(underlying: Optional[str] = None) -> List[Dict]
def get_options_position(position_id: int) -> Optional[Dict]
def start_wheel_cycle(underlying: str, put_position_id: int) -> int
def update_wheel_cycle_state(cycle_id: int, new_state: str) -> None
def complete_wheel_cycle(cycle_id: int, final_pnl: float) -> None
```

### 3. SmartScheduler (extend)

**New scheduled jobs:**
```python
def setup_jobs(self):
    # Existing equity jobs
    self.scheduler.add_job(...)

    # New options jobs
    if self.config.wheel_enabled:
        # Daily assignment check (before market open)
        self.scheduler.add_job(
            func=self._check_assignments_job,
            trigger='cron',
            day_of_week='mon-fri',
            hour=8,
            minute=0,
            timezone='America/New_York'
        )

        # Options position monitoring (profit taking)
        self.scheduler.add_job(
            func=self._monitor_options_job,
            trigger='cron',
            day_of_week='mon-fri',
            hour='10-15',
            minute='*/30',  # Every 30 min during market hours
            timezone='America/New_York'
        )

        # Wheel signal check (9:35 AM entry time)
        self.scheduler.add_job(
            func=self._wheel_signal_job,
            trigger='cron',
            day_of_week='mon-fri',
            hour=9,
            minute=35,
            timezone='America/New_York'
        )
```

## Architecture Patterns to Follow

### 1. Mixin Dependency Injection

**Pattern:** TradingBot __init__ receives all dependencies, mixins access via self.*

**Example:**
```python
class TradingBot(OptionsChainMixin, OptionsExecutionMixin, WheelCycleMixin, ...):
    def __init__(
        self,
        config: BotConfig,
        client: ETradeClient,
        db: Database,
        telegram: TelegramNotifier,
        wheel_strategy: Optional[WheelStrategy] = None,
        ...
    ):
        self.config = config
        self.client = client
        self.db = db
        self.telegram = telegram
        self.wheel_strategy = wheel_strategy or WheelStrategy(config.wheel_strategy)
        # All mixins now have access to these attributes
```

**Why:** Ensures all mixins have access to shared resources without circular imports. Matches existing pattern (see TradingBot core.py line 61-96).

### 2. Signal-Driven Execution

**Pattern:** Strategy generates signal, Scheduler orchestrates, Execution executes

**Example:**
```python
# In SmartScheduler
def _wheel_signal_job(self):
    signal = self.trading_bot.wheel_strategy.get_today_signal()
    if signal.action != 'CASH':
        result = self.trading_bot.execute_wheel_signal(signal)
        self.db.log_event('WHEEL_SIGNAL', signal.action, {...})
```

**Why:** Same pattern as equity trades (see smart_scheduler.py). Separation of concerns: strategy decides what, execution handles how.

### 3. Database as Audit Trail

**Pattern:** Log all decisions, approvals, and state changes

**Example:**
```python
# Before executing options trade
self.db.log_event(
    'OPTIONS_APPROVAL_REQUEST',
    f'Requesting approval for {action} {option_type}',
    {
        'option_symbol': contract.symbol,
        'strike': contract.strike,
        'expiration': contract.expiration.isoformat(),
        'delta': contract.delta,
        'premium': premium,
        'timestamp': get_et_now().isoformat()
    }
)

# After trade execution
self.db.record_options_trade_entry(...)
```

**Why:** Matches existing pattern (see database.py log_event, execution_mixin.py line 288-300). Critical for debugging and compliance.

### 4. Telegram Approval Gating

**Pattern:** All trades require approval unless skip_approval=True

**Example:**
```python
def execute_options_trade(contract, action, quantity, skip_approval=False):
    if self.config.approval_mode == ApprovalMode.REQUIRED and not skip_approval:
        approval = self._request_telegram_approval_options(...)
        if approval != ApprovalResult.APPROVED:
            return OptionsTradeResult(success=False, error='Not approved')
    # Proceed with execution
```

**Why:** Safety-critical for real money. Reuses existing approval flow (see execution_mixin.py line 206-273).

### 5. Thread-Safe Position Management

**Pattern:** Use _position_lock (RLock) for position state changes

**Example:**
```python
def handle_assignment(self, assignment: AssignmentEvent):
    with self._position_lock:
        # Update options position status
        self.db.update_options_position(
            assignment.option_position_id,
            status='assigned',
            assignment_date=assignment.detected_at.isoformat()
        )
        # Create equity position
        equity_id = self.db.record_assignment(...)
        # Update wheel cycle state
        self.db.update_wheel_cycle_state(...)
```

**Why:** Prevents race conditions from concurrent APScheduler jobs (see positions_mixin.py line 72-95).

## Anti-Patterns to Avoid

### Anti-Pattern 1: Tight Coupling to E*TRADE

**What:** Hardcoding E*TRADE-specific logic in strategy or mixin methods

**Why bad:** Makes testing difficult, locks to single broker

**Instead:** Abstract broker calls behind interface
```python
# Bad
def get_option_chains(self):
    return self.client.session.get(f"{ETRADE_URL}/optionchains?...")

# Good
def get_option_chains(self):
    return self.client.get_option_chains(...)  # Client abstracts endpoint
```

**Prevention:** Keep E*TRADE specifics in ETradeClient class only. Mixins call client methods, not raw API.

### Anti-Pattern 2: Synchronous Broker Calls in Async Context

**What:** Calling blocking E*TRADE methods from async Telegram handlers

**Why bad:** Deadlocks, poor responsiveness

**Instead:** Use async bridging utilities
```python
# In Telegram command handler (async context)
async def handle_wheel_command(update, context):
    # Bridge to sync E*TRADE call
    signal = await run_sync_in_executor(self.bot.wheel_strategy.get_today_signal)
```

**Prevention:** Use run_async_from_sync / run_sync_in_executor (see async_utils.py).

### Anti-Pattern 3: Position State in Memory Only

**What:** Tracking wheel cycle state in class attributes without database persistence

**Why bad:** State lost on restart, no audit trail

**Instead:** Database as source of truth
```python
# Bad
self._current_wheel_state = 'short_put'  # Lost on restart

# Good
self.db.update_wheel_cycle_state(cycle_id, 'short_put')
wheel_cycle = self.db.get_active_wheel_cycle('IBIT')
current_state = wheel_cycle['current_state']
```

**Prevention:** Follow existing pattern (see database.py for persistent state).

### Anti-Pattern 4: Assignment Detection via Transaction Polling

**What:** Querying E*TRADE transaction history for "Assignment" events

**Why bad:** Transactions API is slow, not real-time, complex parsing

**Instead:** Position diffing (compare yesterday vs today)
```python
# Simpler, more reliable
current_positions = self.client.get_account_positions(...)
yesterday_positions = self.db.get_options_positions(status='open')
# Detect changes in position quantities
```

**Prevention:** Positions API is authoritative. Transactions API is for audit only.

### Anti-Pattern 5: Greeks Calculation Client-Side

**What:** Implementing Black-Scholes to calculate Greeks locally

**Why bad:** Complex, error-prone, E*TRADE already provides Greeks

**Instead:** Use broker-provided Greeks
```python
chain = self.client.get_option_chains('IBIT', ...)
# E*TRADE response includes delta, gamma, theta, vega per strike
for option in chain['OptionPair']:
    delta = option['Call']['delta']  # Use provided value
```

**Prevention:** E*TRADE options chain includes Greeks (verified via API docs). Don't reinvent.

## Scalability Considerations

| Concern | At 1 Wheel Cycle | At 5 Concurrent Cycles | At 10+ Cycles |
|---------|------------------|------------------------|---------------|
| **Assignment detection** | Position diff once daily (trivial) | Position diff scans all open puts (still fast) | May need indexed queries on options_positions.status |
| **Options chain fetches** | 1 fetch per signal (cached for 5 min) | 5 fetches at 9:35 AM (stagger by 1 sec?) | Rate limit concern - fetch serially, not parallel |
| **Database writes** | ~10 writes/day (entry, monitoring, exit) | ~50 writes/day | WAL mode handles concurrency, no issue |
| **Telegram approval latency** | User responds in ~30 sec | 5 approvals sequentially = 2.5 min | Batch approvals in single message? |

**Key insight:** Wheel strategy is low-frequency (1 trade every few days). Scalability not a concern until managing 20+ concurrent cycles across multiple underlyings. IBIT-only v1 handles 1-2 cycles max.

**Rate limiting (E*TRADE):**
- Options chains: Cache for 5 minutes (quotes don't change rapidly for 30-45 DTE options)
- Positions: Cache for 1 minute (assignment detection isn't time-critical)
- Order placement: No caching (real-time)

## Build Order (Dependency-Aware)

### Phase 1: E*TRADE Options API Foundation (Week 1)

**Goal:** Extend ETradeClient with options methods, verify connectivity

**Tasks:**
1. Add `get_option_chains()` method to ETradeClient
2. Add `preview_options_order()` and `place_options_order()` methods
3. Add `get_option_quote()` method
4. Write integration tests against E*TRADE sandbox
5. Document OCC option symbol format parsing

**Validation:** Can fetch IBIT options chain, preview/place test orders in sandbox

**Why first:** All downstream components depend on E*TRADE options API. Must verify broker integration works before building abstractions.

### Phase 2: Database Schema & Data Models (Week 1)

**Goal:** Create options-specific tables, test persistence

**Tasks:**
1. Add `options_positions` table (migration script)
2. Add `wheel_cycles` table
3. Add `equity_positions` table
4. Implement database methods (record_options_trade_entry, etc.)
5. Write unit tests for CRUD operations

**Validation:** Can persist and query options positions with assignment tracking

**Why second:** Execution and strategy layers need database schema in place. Low risk to add tables early.

### Phase 3: OptionsChainMixin (Week 2)

**Goal:** Strike/expiration selection logic with delta targets

**Tasks:**
1. Implement `get_option_chain()` with DTE filtering
2. Implement `select_put_strike()` with delta targeting
3. Implement `select_call_strike()` with ATM/OTM logic
4. Add configuration for delta targets (WheelStrategyConfig)
5. Write unit tests with mocked E*TRADE responses

**Validation:** Given target delta -0.28, selects appropriate strike from chain

**Why third:** Execution layer (next phase) depends on strike selection. No point executing trades if we can't select strikes.

### Phase 4: OptionsExecutionMixin (Week 2-3)

**Goal:** Preview, place, and monitor options orders

**Tasks:**
1. Implement `execute_options_trade()` with approval flow
2. Implement `_wait_for_options_fill()` (poll for execution)
3. Adapt Telegram approval to show options details (strike, Greeks)
4. Add paper mode simulation for options trades
5. Write integration tests with E*TRADE sandbox

**Validation:** Can sell cash-secured put via Telegram approval, confirm fill

**Why fourth:** Core execution primitive. Once working, can manually trigger trades before automating strategy.

### Phase 5: WheelStrategy Signal Generation (Week 3)

**Goal:** Decide when to enter puts/calls based on market conditions

**Tasks:**
1. Implement `get_today_signal()` with pullback detection
2. Add IV percentile calculation (vs historical IV)
3. Implement cash sufficiency check (strike * 100 available)
4. Add configuration for entry thresholds
5. Write unit tests with mocked market data

**Validation:** Returns SELL_PUT signal after 2% pullback with elevated IV

**Why fifth:** Strategy logic is independent of execution. Can develop/test separately.

### Phase 6: WheelCycleMixin Coordination (Week 4)

**Goal:** Link put → assignment → call in full cycle

**Tasks:**
1. Implement `check_for_assignments()` with position diffing
2. Implement `handle_assignment()` to auto-trigger covered call
3. Implement `monitor_options_positions()` for profit taking
4. Add wheel cycle state machine logic
5. Write end-to-end tests (put → assignment → call)

**Validation:** Simulated assignment triggers covered call automatically

**Why sixth:** Requires all previous components. Orchestrates full workflow.

### Phase 7: Scheduler Integration (Week 4)

**Goal:** Automate daily jobs for assignment checks and signal execution

**Tasks:**
1. Add `_check_assignments_job()` to SmartScheduler (8 AM daily)
2. Add `_wheel_signal_job()` to SmartScheduler (9:35 AM)
3. Add `_monitor_options_job()` (every 30 min during market hours)
4. Test scheduler timing in development
5. Add Telegram notifications for each job

**Validation:** Bot checks for assignments every morning, executes signals at 9:35

**Why seventh:** Final integration point. Requires all mixins and strategy working.

### Phase 8: Transition & Deprecation (Week 5)

**Goal:** Disable intraday equity strategies, enable wheel as primary

**Tasks:**
1. Add `wheel_enabled` flag to BotConfig
2. Disable SmartStrategy signals when wheel_enabled=True
3. Update Streamlit dashboard to show options positions
4. Add `/wheel` Telegram command for manual cycle status
5. Migrate equity capital allocation to options collateral calculation

**Validation:** Bot runs wheel-only mode, no BITU/SBIT trades

**Why last:** Ensures smooth transition. Keep equity strategies as fallback until wheel proven.

## Integration Points with Existing Code

### 1. TradingBot Composition

**Existing:**
```python
class TradingBot(OrdersMixin, PositionsMixin, ExecutionMixin, HedgeMixin, NotificationsMixin):
    def __init__(self, config, client, ...):
        self.client = client
        self.strategy = SmartStrategy(config.strategy)
```

**Modified:**
```python
class TradingBot(
    # Existing equity mixins (unchanged)
    OrdersMixin, PositionsMixin, ExecutionMixin, HedgeMixin, NotificationsMixin,
    # New options mixins (additive)
    OptionsChainMixin, OptionsExecutionMixin, WheelCycleMixin
):
    def __init__(self, config, client, ...):
        self.client = client
        # Existing strategy (keep for transition period)
        self.strategy = SmartStrategy(config.strategy)
        # New wheel strategy (optional, controlled by config flag)
        if config.wheel_enabled:
            self.wheel_strategy = WheelStrategy(config.wheel_strategy)
```

**Impact:** Zero breaking changes. Existing methods unaffected. New mixins add methods, don't override.

### 2. SmartScheduler Job Routing

**Existing:**
```python
def _morning_entry_job(self):
    signal = self.trading_bot.get_today_signal()  # SmartStrategy
    if signal.signal != Signal.CASH:
        self.trading_bot.execute_signal(signal)  # ExecutionMixin
```

**Modified:**
```python
def _morning_entry_job(self):
    # Route based on config flag
    if self.config.wheel_enabled:
        self._execute_wheel_signal()
    else:
        self._execute_equity_signal()

def _execute_wheel_signal(self):
    signal = self.trading_bot.wheel_strategy.get_today_signal()
    if signal.action != 'CASH':
        self.trading_bot.execute_wheel_signal(signal)  # WheelCycleMixin

def _execute_equity_signal(self):
    signal = self.trading_bot.get_today_signal()  # SmartStrategy
    if signal.signal != Signal.CASH:
        self.trading_bot.execute_signal(signal)  # ExecutionMixin
```

**Impact:** Minimal. Add conditional routing. Both paths use same approval/notification flow.

### 3. Database Singleton Pattern

**Existing:**
```python
# In TradingBot
self.db = db or get_database()

# In mixins
self.db.record_trade_entry(...)
```

**Modified:**
```python
# In TradingBot (unchanged)
self.db = db or get_database()

# In OptionsExecutionMixin (same pattern)
self.db.record_options_trade_entry(...)
```

**Impact:** None. Database singleton works identically for options tables. Just new methods, same instance.

### 4. Telegram Approval Flow

**Existing:**
```python
# In ExecutionMixin
approval = self.telegram.request_approval(
    signal_type='MEAN_REVERSION',
    etf='BITU',
    reason='IBIT dropped -2.1%',
    shares=100,
    price=25.50,
    position_value=2550.00
)
```

**Modified (options):**
```python
# In OptionsExecutionMixin (reuse same method, different params)
approval = self.telegram.request_approval(
    signal_type='SELL_PUT',
    etf='IBIT',  # Underlying
    reason='Strike $40 Δ-0.28 Apr 18 2025',
    shares=1,  # Contracts
    price=1.50,  # Premium per contract
    position_value=150.00  # Total credit (1 contract * $1.50 * 100)
)
```

**Impact:** Reuse existing method. Telegram message formatting adapts based on signal_type. No code changes needed in telegram_bot.py.

### 5. Market Data Fallback Chain

**Existing:**
```python
# In MarketDataManager
quote = self.data_manager.get_quote('IBIT')  # E*TRADE → Alpaca → Finnhub → Yahoo
```

**Modified (options):**
```python
# For underlying quotes (same as equity)
underlying_quote = self.data_manager.get_quote('IBIT')

# For options quotes (E*TRADE only - no fallback providers support options)
option_quote = self.client.get_option_quote('IBIT250418P00040000')
```

**Impact:** Options quotes rely solely on E*TRADE. No fallback available (Alpaca/Yahoo don't provide options Greeks). Acceptable risk for v1.

## Confidence Assessment

| Area | Confidence | Evidence |
|------|------------|----------|
| **E*TRADE options API** | HIGH | Official API docs verified. Endpoints for chains, Greeks, order placement confirmed. Sandbox available for testing. |
| **Mixin architecture integration** | HIGH | Existing TradingBot uses 5 mixins successfully. Pattern proven, well-documented. Adding 3 more follows same pattern. |
| **Database schema** | HIGH | Existing schema handles equities with similar structure (entry/exit, P&L, status). Options tables mirror equity patterns. |
| **Assignment detection** | MEDIUM | Position diffing approach validated by web research. E*TRADE positions API confirmed. No official assignment notification API (must poll). |
| **Strike selection algorithm** | HIGH | Delta-based selection is industry standard. E*TRADE provides Greeks in chain response. Algorithm straightforward (find closest delta). |
| **Wheel cycle coordination** | MEDIUM | State machine logic validated by wheel strategy guides. Complexity in edge cases (early assignment, corporate actions). |
| **Telegram approval adaptation** | HIGH | Existing approval flow handles arbitrary signal types. Options just different params. No architectural change needed. |

**Overall confidence:** HIGH for foundation (E*TRADE integration, mixin architecture, database). MEDIUM for operational complexity (assignment detection timing, cycle edge cases).

**Risks mitigated:**
- E*TRADE sandbox testing before live (Phase 1 validation)
- Paper mode for options (same pattern as equity paper mode)
- Telegram approval required (safety gate)
- Database audit trail (all actions logged)

**Remaining uncertainties:**
- Assignment timing (detected next morning, not instantly) - Acceptable for wheel strategy (not time-sensitive)
- Early assignment on ITM options before expiration - Edge case, handle with monitoring job
- Corporate actions (splits, dividends) affecting option symbols - Out of scope for v1, monitor via E*TRADE notifications

## Sources

- [E*TRADE API Market Documentation](https://apisb.etrade.com/docs/api/market/api-market-v1.html) - Options chains, Greeks, expiration dates endpoints
- [E*TRADE API Order Documentation](https://apisb.etrade.com/docs/api/order/api-order-v1.html) - Options order preview and placement
- [E*TRADE Portfolio API](https://apisb.etrade.com/docs/api/account/api-portfolio-v1.html) - Position queries for assignment detection
- [Wheel Strategy Guide 2026](https://quantwheel.com/learn/wheel-strategy/) - Architecture patterns, DTE/delta targets
- [Options Delta Explained](https://quantwheel.com/learn/options-delta-explained/) - Strike selection methodology
- [Optimizing Wheel Strategy DTE and Delta](https://wheelstrategyoptions.com/blog/optimizing-the-wheel-strategy-advanced-dte-delta-and-trade-exit-tactics/) - Profit taking, position management
- [Alpaca Options Wheel Algorithm](https://alpaca.markets/learn/options-wheel-strategy) - Python implementation patterns
- [Alpaca Options Trading API](https://docs.alpaca.markets/docs/options-trading) - Exercise/assignment event handling
- [GitHub: Alpaca Options Wheel Template](https://github.com/alpacahq/options-wheel) - Reference architecture
- [AI Options Trading Bot 2026](https://www.jenova.ai/en/resources/ai-options-trading-bot) - Multi-leg strategy architecture
- [Architecting Trading Bots in Python 2026](https://medium.com/@writeronepagecode/architecting-a-robust-trading-bot-in-python-782d850888a7) - Position management patterns
- [Option Assignment Understanding](https://optionalpha.com/learn/option-assignment) - Assignment detection indicators
- [Charles Schwab Options Assignment Guide](https://www.schwab.com/learn/story/options-exercise-assignment-and-more-beginners-guide) - Assignment mechanics
- [Cash Secured Put vs Covered Call](https://optionsamurai.com/blog/cash-secured-put-vs-covered-call/) - Wheel strategy fundamentals
