# Domain Pitfalls: Adding Wheel Strategy to Existing Equity Bot

**Domain:** Options trading (wheel strategy) on top of existing intraday equity bot
**Researched:** 2026-03-20
**Confidence:** MEDIUM (WebSearch + official docs, verified across multiple sources)

## Critical Pitfalls

Mistakes that cause rewrites, data corruption, or financial loss.

---

### Pitfall 1: Assignment Detection Blindness

**What goes wrong:** Bot is unaware when options are assigned. It continues tracking the short put/call position while the broker has actually delivered/removed shares from the account.

**Why it happens:**
- Assignment occurs after market close (between 4:00 PM ET and 5:30 PM ET exercise deadline)
- Broker APIs often don't report assignment as a discrete event
- Assignment decisions are discretionary - option holders have 90 minutes post-close to decide
- Partial assignments can occur (e.g., assigned on 3 of 5 contracts)

**Consequences:**
- Bot's position state diverges from actual broker positions
- Next day's signal logic uses wrong position data (thinks it has puts, actually has shares)
- Potential duplicate orders (selling more puts when shares were already assigned)
- Missing covered call opportunity window (unaware shares were assigned overnight)

**Prevention:**
1. **Position reconciliation at market open** - Query E*TRADE positions API and compare with local state
2. **Assignment detection logic** - Check for:
   - Short option contracts that disappeared overnight
   - New equity positions that appeared overnight
   - `PositionType` field changes in E*TRADE responses
3. **Event-driven architecture** - Store position state changes with timestamps, don't rely on in-memory state
4. **Telegram alert on reconciliation mismatch** - Notify user if local state doesn't match broker

**Detection:** Position count mismatch between local `_paper_positions` (or DB) and E*TRADE API response at market open.

**Phase to address:** Phase 2 (Assignment Detection & Covered Calls)

**Sources:**
- [SteadyOptions: Everything You Need to Know About Options Assignment Risk](https://steadyoptions.com/articles/everything-you-need-to-know-about-options-assignment-risk-r738/)
- [Nasdaq: Automatic Exercise, After-Hours Risk](https://www.nasdaq.com/articles/automatic-exercise-after-hours-risk-and-other-options-expiration-issues-2010-11-18)
- [Option Alpha: Order Handling](https://docs.optionalpha.com/technical-documentation/platform/order-handling)

---

### Pitfall 2: Pin Risk Uncertainty

**What goes wrong:** Options expire at or very close to strike price. Bot doesn't know if it will be assigned until after 5:30 PM ET deadline, but needs to make decisions about next week's positions.

**Why it happens:**
- OCC auto-exercises at $0.01 ITM, but uses official closing price
- After-hours movement (4:00-5:30 PM) can change ITM/OTM status
- Exercise is discretionary - holders may not exercise even if ITM due to commissions or risk
- Stock closes exactly at strike = maximum uncertainty

**Consequences:**
- Bot may sell new puts assuming assignment didn't happen, then get assigned and be double-exposed
- Missing Saturday morning (11:59 AM ET official expiration) means delayed detection
- Telegram approval requests may use stale position assumptions

**Prevention:**
1. **Conservative position assumption** - If position expires within $0.50 of strike, assume WORST CASE:
   - Short puts near strike → assume assignment (will have shares Monday)
   - Short calls near strike → assume called away (will be cash Monday)
2. **Expiration Friday 5:30 PM cutoff** - Don't make new position decisions after this time
3. **Monday morning reconciliation** - First job is position verification, before any new signals
4. **Configurable pin risk buffer** - `PIN_RISK_THRESHOLD = 0.50` (dollars from strike)

**Detection:** Position expires on Friday within PIN_RISK_THRESHOLD of strike price.

**Phase to address:** Phase 2 (Assignment Detection & Covered Calls) + Phase 3 (Expiration Handling)

**Sources:**
- [ApexVol: Pin Risk - Avoid Assignment & Costly Mistakes](https://apexvol.com/learn/pin-risk)
- [IBKR: Understanding Special Exercises and Pin Risk](https://www.interactivebrokers.com/campus/traders-insight/securities/options/understanding-special-exercises-and-pin-risk/)
- [StrikeWatch: Options Expiration OPEX Explained](https://www.strike-watch.com/lab/options-expiration-opex-assignment-pin-risk)

---

### Pitfall 3: Cost Basis Tracking Errors in Wheel Cycle

**What goes wrong:** After assignment, bot sells covered calls below the adjusted cost basis, locking in guaranteed losses when shares are called away.

**Why it happens:**
- Premium collected from original put reduces cost basis
- Multi-cycle wheels accumulate complex cost basis adjustments
- Bot tracks premium separately from equity cost basis
- Strike selection algorithm doesn't consider actual cost basis

**Example:**
```
1. Sell $50 put, collect $2 premium → Cost basis if assigned: $48
2. Get assigned at $50 → Paid $50, but effective cost: $48
3. Bot selects $47 strike for covered call (based on delta, ignores cost basis)
4. Shares called away at $47 → Realized loss: $1/share despite collecting premium
```

**Consequences:**
- Locking in losses while thinking you're profitable
- Monthly strategy review shows negative returns despite "successful" wheel cycles
- P&L calculations misleading

**Prevention:**
1. **Adjusted cost basis table** - New DB column: `adjusted_cost_basis` updated on:
   - Assignment (strike - premium collected)
   - Additional premium from covered calls (further reduces basis)
2. **Strike validation** - Covered call strike selection MUST be `>= adjusted_cost_basis`
3. **Telegram approval shows cost basis** - "Selling $47 call on shares with $48 basis = $1 locked loss"
4. **Separate P&L tracking** - Don't just sum premiums; track actual equity gains/losses

**Detection:** `covered_call_strike < adjusted_cost_basis` in strike selection logic.

**Phase to address:** Phase 2 (Assignment Detection & Covered Calls) - database schema design

**Sources:**
- [QuantWheel: Cash Secured Puts Biggest Mistakes](https://quantwheel.com/learn/cash-secured-puts-biggest-mistakes/)
- [Options Trading IQ: Biggest Mistakes When Rolling Options](https://optionstradingiq.com/the-biggest-mistakes-when-rolling-options/)
- [Rockwell Trading: Wheel Options Strategy 2026](https://www.rockwelltrading.com/what-is-the-wheel-options-strategy-how-it-works-why-its-boring-and-why-thats-the-point-2026/articles/coffee-with-markus/)

---

### Pitfall 4: Corporate Actions Breaking the Options Chain

**What goes wrong:** IBIT pays a special dividend or undergoes a stock split. Existing options contracts get adjusted with non-standard strikes and deliverables. Bot's strike selection logic breaks.

**Why it happens:**
- OCC adjusts existing options contracts for corporate actions
- Adjusted options get symbols like `IBIT1` (number suffix)
- Strike prices no longer align with standard chain (e.g., $47.38 instead of $47.50)
- New standard contracts continue listing alongside adjusted contracts

**Consequences:**
- Bot can't find matching strikes in options chain (expects $50, sees $48.23)
- Order placement fails with "invalid strike" error
- Existing positions can't be closed - symbol mismatch
- Greeks calculations wrong (using standard contract data for adjusted contract)

**Prevention:**
1. **Adjusted contract detection** - Check symbol for numeric suffix before trading
2. **Corporate actions webhook** - Subscribe to E*TRADE or use Alpaca's corporate actions endpoint
3. **Manual intervention flag** - If adjustment detected, pause automated trading, alert via Telegram
4. **Fallback to manual close** - For adjusted positions, require user to close via E*TRADE interface
5. **Post-adjustment resume** - Wait for adjusted contracts to expire, then resume with standard chain

**Detection:** Options chain returns symbols with format `{TICKER}{DIGIT}` or strikes don't match standard $0.50 increments.

**Phase to address:** Phase 4 (Edge Cases & Resilience)

**Sources:**
- [Robinhood: How Corporate Actions Affect Your Options](https://robinhood.com/us/en/support/articles/how-corporate-actions-affect-your-options/)
- [Fidelity: Options Contract Adjustments](https://www.fidelity.com/learning-center/investment-products/options/contract-adjustments)
- [OptionVisualizer: Corporate Actions & Options](https://www.optionvisualizer.com/documentation/education/corporate-actions)

---

### Pitfall 5: E*TRADE API Response Parsing Fragility for Options

**What goes wrong:** E*TRADE options API responses have different structure than equity responses. Existing parsing logic (deep `.get()` chains) breaks with IndexError or KeyError.

**Why it happens:**
- Options responses nest data differently: `OptionChainResponse > OptionPair > Call/Put`
- Existing code pattern: `order.get("OrderDetail", [{}])[0].get("Instrument")` assumes list always has [0]
- Options responses sometimes return empty `[]` for strikes with no open interest
- Different response structure for multi-leg vs single-leg orders (wheel uses single-leg)

**Consequences:**
- Bot crashes during options chain retrieval
- Order status checks fail with uncaught exceptions
- Greeks data extraction breaks
- Existing error handling catches too broadly (`except Exception`), masks root cause

**Prevention:**
1. **Separate options parsing module** - Don't reuse equity parsing logic
2. **Response schema validation** - Use pydantic or dataclasses:
```python
@dataclass
class OptionsChainResponse:
    option_pairs: List[OptionPair]

    @classmethod
    def from_etrade(cls, response: dict):
        # Explicit validation, fail fast with clear error
        if not response.get("OptionChainResponse"):
            raise ValueError(f"Invalid response structure: {response}")
```
3. **Defensive extraction with defaults** - Replace `[0]` with `.get(0)` equivalent or explicit length check
4. **Full response logging on failure** - Log entire response to DB for post-mortem
5. **Integration tests with real API responses** - Save actual E*TRADE responses as fixtures

**Detection:** `IndexError`, `KeyError`, or `TypeError` during options chain parsing in production.

**Phase to address:** Phase 1 (E*TRADE Options API Integration) - implement before live trading

**Sources:**
- [E*TRADE API Market Data Documentation](https://apisb.etrade.com/docs/api/market/api-market-v1.html)
- [GitHub: pyetrade option chains example](https://github.com/1rocketdude/pyetrade_option_chains)
- Existing codebase: `CONCERNS.md` - Fragile E*TRADE Response Parsing section

---

### Pitfall 6: Stale Options Quotes at Market Open

**What goes wrong:** Bot retrieves options chain at 9:30 AM but uses stale bid/ask from Friday 4:00 PM close. Places order at limit price based on stale quote, order never fills.

**Why it happens:**
- E*TRADE API returns delayed quotes unless market data agreement signed
- Options markets are less liquid at open - spreads widen significantly
- Bid/ask can shift 20-50% in first 15 minutes
- Quote timestamps (`askTime`, `bidTime`) not checked before using data

**Consequences:**
- Limit orders sit unfilled while market moves
- Missing entry window for signal-based put selling
- User frustration - sees opportunity but bot didn't execute

**Prevention:**
1. **Quote freshness check** - Validate `askTime` and `bidTime` within last 60 seconds:
```python
def is_quote_fresh(quote: dict, max_age_seconds: int = 60) -> bool:
    ask_time = datetime.fromtimestamp(quote['askTime'] / 1000)  # E*TRADE uses epoch ms
    return (datetime.now() - ask_time).total_seconds() < max_age_seconds
```
2. **Market data agreement verification** - Check `quoteStatus` field != "DELAYED"
3. **Retry with fresh quote** - If quote stale, sleep 30s and re-fetch before order placement
4. **Wider limit price buffer** - For options, use midpoint + 5% instead of equity's midpoint + 1%
5. **Telegram alert on stale quote** - "Quote is 3 minutes old, waiting for fresh data..."

**Detection:** `quoteStatus == "DELAYED"` or `(current_time - askTime) > 60 seconds`.

**Phase to address:** Phase 1 (E*TRADE Options API Integration)

**Sources:**
- [E*TRADE API Quote Documentation](https://apisb.etrade.com/docs/api/market/api-quote-v1.html)
- [CRAN: etrader package documentation](https://cran.r-project.org/web/packages/etrader/etrader.pdf)

---

### Pitfall 7: Greeks Calculation Accuracy and Model Dependency

**What goes wrong:** Bot selects strikes based on delta (e.g., "sell 0.30 delta put"), but E*TRADE's calculated delta differs from bot's calculation. Wrong strike selected.

**Why it happens:**
- Different brokers use different pricing models (Black-Scholes vs binomial vs proprietary)
- Implied volatility inputs differ (E*TRADE may use their own IV calc)
- Greeks are "local sensitivities" - accurate for small moves, not large
- Greeks change every tick - value at quote time != value at order execution time

**Consequences:**
- Selecting 0.30 delta put, actually getting 0.35 delta (more assignment risk)
- Backtests based on theoretical Greeks don't match live results
- Risk management wrong (gamma exposure higher than expected)

**Prevention:**
1. **Use broker's Greeks, don't calculate your own** - E*TRADE API returns Greeks in options chain
2. **Delta range instead of exact value** - Select "0.25-0.35 delta" not "0.30 delta"
3. **Greeks staleness check** - Same timestamp validation as quotes
4. **Separate Greeks validation phase** - Log E*TRADE delta vs calculated delta for 1 week, measure divergence
5. **Conservative bounds** - If targeting 0.30 delta, select closest strike <= 0.30 (not >= 0.30)

**Detection:** Significant divergence (>10%) between expected Greeks and actual position performance.

**Phase to address:** Phase 1 (E*TRADE Options API Integration) + Phase 3 (Strike Selection Algorithm)

**Sources:**
- [ChartMini: Options Greeks Delta Gamma Theta Vega Guide 2026](https://chartmini.com/blog/options-greeks-delta-gamma-theta-vega-guide-2026)
- [QuantConnect: More Accurate Option Greeks](https://www.quantconnect.com/forum/discussion/14454/more-accurate-option-greeks/)
- [Zerodha: Black-Scholes Greek Calculator](https://zerodha.com/varsity/chapter/greek-calculator/)

---

## Moderate Pitfalls

Significant issues that cause incorrect behavior but are recoverable.

---

### Pitfall 8: Concurrent Signal Processing During Wheel Cycles

**What goes wrong:** Bot is in middle of wheel cycle (holding shares, about to sell covered call) when intraday equity signal fires. Both systems try to trade simultaneously.

**Why it happens:**
- Existing intraday strategies (crash day, pump day) still running in scheduler
- Wheel strategy operates on multi-day timeframe
- No mutual exclusion between wheel state and intraday signals
- Same underlying asset (both trade IBIT exposure via different instruments)

**Consequences:**
- Holding 100 IBIT shares (from put assignment) + bot tries to buy BITU = over-leveraged
- Covered call selling conflicts with liquidation from intraday exit
- Position lock helps but doesn't prevent signal generation, just execution

**Prevention:**
1. **Master trading mode flag** - `WHEEL_MODE` vs `INTRADAY_MODE` (mutually exclusive)
2. **Signal filter by mode** - Intraday signals return early if `current_mode == WHEEL_MODE`
3. **Graceful transition period** - Don't enable wheel until all intraday positions closed
4. **Database state table** - `trading_mode` column with values: `INTRADAY`, `WHEEL_TRANSITION`, `WHEEL_ACTIVE`
5. **Telegram command for mode switch** - `/enable_wheel` closes intraday positions, pauses intraday jobs

**Detection:** Multiple signals generated on same day with different instrument types (options + equity).

**Phase to address:** Phase 5 (Deprecate Intraday Strategies) - explicitly handle transition

**Sources:**
- Existing codebase: `smart_scheduler.py` - multiple concurrent job methods
- Existing codebase: `CONCERNS.md` - Position Reversal Logic During Concurrent Jobs

---

### Pitfall 9: Rolling Options Without Strike Price Validation

**What goes wrong:** Bot attempts to "roll" a tested put by closing at a loss and opening a new put at lower strike. New strike is below user's acceptable threshold, locks in larger potential loss.

**Why it happens:**
- Rolling logic focuses on extending duration (DTE) without validating strike quality
- Market crashed 10% - bot mechanically rolls from $50 put to $45 put
- No cost-benefit analysis of "pay $X to roll vs just take assignment"

**Consequences:**
- Paying premium to extend a bad trade instead of taking assignment
- Accumulating losses via roll costs (>25% of original credit = danger zone)
- Getting assigned anyway but at a worse effective cost basis

**Prevention:**
1. **Roll validation criteria**:
   - New strike must be >= `MIN_ACCEPTABLE_STRIKE` (configurable, e.g., 80% of initial)
   - Roll cost must be <= 25% of original credit
   - New DTE must be <= 60 days (don't roll into 90 DTE)
2. **Cost-benefit comparison** - Present user with:
   - Option A: Take assignment, cost basis $X
   - Option B: Roll for $Y, new cost basis $Z if assigned next time
3. **No automatic rolling** - All rolls require Telegram approval (unlike closures which can be auto)
4. **Max rolls per position** - `MAX_ROLL_COUNT = 2` before forced assignment acceptance

**Detection:** Roll attempt with `new_strike < (original_strike * 0.80)` or `roll_cost > (original_credit * 0.25)`.

**Phase to address:** Phase 4 (Profit Management & Rolling)

**Sources:**
- [Options Trading IQ: Biggest Mistakes When Rolling Options](https://optionstradingiq.com/the-biggest-mistakes-when-rolling-options/)
- [Options Trading IQ: When To Roll Cash-Secured Puts](https://optionstradingiq.com/put-rolling-strategies/)
- [Market Rebellion: Rolling Options](https://marketrebellion.com/training/rolling-options/)

---

### Pitfall 10: Cash Requirement Miscalculation for Multiple Puts

**What goes wrong:** Bot sells 5 puts thinking it has cash to cover all assignments. Broker rejects 4th and 5th contracts due to insufficient buying power.

**Why it happens:**
- Cash-secured puts require 100% of strike value in cash reserves
- Bot calculates per-contract but doesn't validate total account capacity
- Existing equity positions tie up cash
- Margin vs cash account confusion (margin only requires 20-30%, cash requires 100%)

**Consequences:**
- Partial order fills (only 3 of 5 contracts sold)
- Position size smaller than intended
- Signal logic assumes 5 contracts but portfolio only has 3

**Prevention:**
1. **Pre-trade buying power check**:
```python
def validate_cash_secured_put(strike: float, quantity: int) -> bool:
    required_cash = strike * 100 * quantity  # 100 shares per contract
    buying_power = etrade.get_account_balance()['cashAvailableForInvestment']
    return buying_power >= required_cash
```
2. **Position sizing algorithm** - Calculate max contracts based on available cash:
```python
max_contracts = int(buying_power / (strike * 100))
actual_quantity = min(desired_quantity, max_contracts)
```
3. **Telegram alert on partial fill** - "Requested 5 contracts, only 3 filled due to cash limits"
4. **Separate tracking for equity vs options buying power** - Don't assume equity buying power = options buying power

**Detection:** Order response contains `executedQuantity < requestedQuantity`.

**Phase to address:** Phase 1 (E*TRADE Options API Integration) - order validation

**Sources:**
- [Nasdaq: Everything About Cash-Secured Puts](https://www.nasdaq.com/articles/everything-you-need-to-know-about-cash-secured-puts)
- [QuantWheel: Cash Secured Puts Biggest Mistakes](https://quantwheel.com/learn/cash-secured-puts-biggest-mistakes/)
- [Tastytrade: Cash-Secured Put](https://support.tastytrade.com/support/s/solutions/articles/43000435285)

---

### Pitfall 11: Options Order Type Incompatibility

**What goes wrong:** Bot uses `GTC` (Good Till Canceled) limit orders for options like it does for equity. Order stays open for days, gets filled at terrible price during volatility spike.

**Why it happens:**
- Equity order logic reused for options without understanding time decay
- Options lose value every day - yesterday's "good" limit price is today's overpay
- GTC makes sense for equity, dangerous for options (especially short-dated)

**Consequences:**
- Stale limit order fills at poor price
- Buying back puts at higher IV than when originally sold
- User confusion - "Why did we close this position?"

**Prevention:**
1. **Day orders only for options** - `time_in_force = "DAY"` (not GTC)
2. **Exception: Closing orders can use GTC** - With max duration of 3 days
3. **Order type restrictions**:
   - Options: `LIMIT` or `MARKET` only (no `STOP_LIMIT` complexity for v1)
   - Multi-leg: Not supported (wheel is single-leg only)
4. **Validation in order builder**:
```python
def build_options_order(action, quantity, limit_price):
    if time_in_force == "GTC":
        raise ValueError("Options orders must use DAY time-in-force")
```

**Detection:** Options order placed with `timeInForce: "GTC"`.

**Phase to address:** Phase 1 (E*TRADE Options API Integration)

**Sources:**
- [Bitget: Understanding Order Types GTC FOK IOC](https://www.bitget.com/support/articles/12560603818004)
- [FINRA: Time Parameters and Qualifiers on Stock Orders](https://www.finra.org/investors/insights/time-parameters-qualifiers-stock-orders)
- [TradersPost: Order Types for Algorithmic Trading](https://blog.traderspost.io/article/order-types-algorithmic-trading-guide)

---

### Pitfall 12: Early Assignment on Dividend Ex-Date

**What goes wrong:** IBIT announces a dividend. Bot is short ITM calls. Gets assigned early (day before ex-dividend date) unexpectedly.

**Why it happens:**
- Call holders exercise early to capture dividend
- Bot doesn't track dividend calendar
- Early assignment risk spikes when `dividend > extrinsic_value`
- Wheel strategy typically sells OTM calls, but market moves can make them ITM

**Consequences:**
- Shares called away earlier than expected
- Missing out on collecting dividend
- Next cycle's timing disrupted (expected shares for another week)

**Prevention:**
1. **Dividend calendar integration** - Query Alpaca or Finnhub for upcoming ex-dividend dates
2. **Pre-dividend assignment warning** - If `days_to_ex_div < 3` and `call_position == ITM`:
   - Telegram alert: "High early assignment risk due to dividend"
   - Option to close position early
3. **Extrinsic value monitoring** - If `extrinsic_value < dividend_amount`, expect assignment
4. **Position reconciliation after ex-date** - Always check if assigned on day after ex-dividend

**Detection:** `extrinsic_value < expected_dividend` and `position == short_call` and `days_to_ex_div <= 3`.

**Phase to address:** Phase 4 (Edge Cases & Resilience)

**Sources:**
- [Saxo: How to Avoid Assignment in Options Trading](https://www.home.saxo/content/articles/options/assignment-explained---02---how-to-avoid-assignment-04072025)
- [Charles Schwab: Risks of Options Assignment](https://www.schwab.com/learn/story/risks-options-assignment)

---

## Minor Pitfalls

Issues that cause inconvenience or confusion but don't break core functionality.

---

### Pitfall 13: Overnight Position Monitoring Gap

**What goes wrong:** Bot monitors positions during market hours (9:30 AM - 4:00 PM) but stops monitoring after close. Misses overnight news that affects Monday's strategy.

**Why it happens:**
- Existing scheduler jobs are market-hours only
- Options positions have weekend/overnight exposure
- No scheduled jobs for 4:00 PM - 9:30 AM period

**Consequences:**
- Delayed reaction to earnings announcements after hours
- Missing opportunity to close positions in extended hours trading
- User manually monitors outside bot hours

**Prevention:**
1. **After-hours monitoring job** - 6:00 PM daily:
   - Check if IBIT moved >3% after hours
   - Telegram notification if significant move
   - Suggest position adjustments for next day
2. **Weekend monitoring** - Saturday 10:00 AM:
   - Review expiration assignment outcomes
   - Reconcile positions post-expiration
3. **Configurable monitoring** - `AFTER_HOURS_MONITORING = True/False`

**Detection:** Significant overnight move (>3%) with no bot notification.

**Phase to address:** Phase 4 (Edge Cases & Resilience) - nice-to-have

**Sources:**
- [Eventus: Trade Surveillance After Hours](https://www.eventus.com/cat-article/trade-surveillance-after-hours-preparing-for-24-5-equities-trading/)
- [IBKR: Overnight Trading](https://www.interactivebrokers.com/en/trading/us-overnight-trading.php)

---

### Pitfall 14: Database Schema for Multi-State Wheel Cycle

**What goes wrong:** Existing `trades` table designed for single-entry/single-exit equity trades. Wheel cycle has multiple states (put sold → assigned → call sold → called away). Database schema doesn't capture full cycle.

**Why it happens:**
- Equity trade model: one row = one complete trade
- Wheel cycle: one cycle = 4+ separate trades (sell put, assignment event, sell call, call-away event)
- No parent-child relationship between cycle components

**Consequences:**
- Can't query "show all incomplete wheel cycles"
- P&L calculation requires manual joining of separate trades
- Monthly strategy review can't aggregate by wheel cycle
- Difficult to answer "How many wheels completed this month?"

**Prevention:**
1. **New table: `wheel_cycles`**:
```sql
CREATE TABLE wheel_cycles (
    cycle_id TEXT PRIMARY KEY,
    underlying TEXT,  -- "IBIT"
    state TEXT,  -- "PUT_OPEN", "ASSIGNED", "CALL_OPEN", "COMPLETED"
    initiated_at TIMESTAMP,
    completed_at TIMESTAMP,
    total_premium REAL,
    final_pnl REAL
);
```
2. **Foreign key in trades table**: `cycle_id` references `wheel_cycles.cycle_id`
3. **State machine tracking**:
   - `PUT_OPEN` → `ASSIGNED` → `CALL_OPEN` → `CALLED_AWAY` → `COMPLETED`
   - OR: `PUT_OPEN` → `CLOSED_PROFIT` (early close)
4. **Cycle analytics queries**:
```sql
-- Average cycle duration
SELECT AVG(julianday(completed_at) - julianday(initiated_at))
FROM wheel_cycles WHERE state = 'COMPLETED';
```

**Detection:** Unable to query full wheel cycle history from database.

**Phase to address:** Phase 1 (E*TRADE Options API Integration) - database design is foundational

**Sources:**
- [Vertabelo: Workflow Patterns for State Management](https://vertabelo.com/blog/the-workflow-pattern-part-1-using-workflow-patterns-to-manage-the-state-of-any-entity/)
- [GeeksforGeeks: Handling State and State Management](https://www.geeksforgeeks.org/handling-state-and-state-management-system-design/)

---

### Pitfall 15: E*TRADE Rate Limiting on Options Chain Queries

**What goes wrong:** Bot queries options chain multiple times per minute during strike selection. E*TRADE API throttles requests, order placement delayed.

**Why it happens:**
- E*TRADE has undocumented rate limits (2 requests/second per module)
- Options chain endpoint may have stricter limits than equity quotes
- Strike selection algorithm iterates through multiple expirations
- No local caching of options chain data

**Consequences:**
- API returns HTTP 429 (Too Many Requests)
- Order placement delayed by 30-60 seconds
- Missing entry window for time-sensitive signals

**Prevention:**
1. **Cache options chain data** - Valid for 60 seconds:
```python
@lru_cache(maxsize=10)
def get_options_chain_cached(symbol, expiration):
    # Cache key includes timestamp rounded to minute
    return etrade.get_options_chain(symbol, expiration)
```
2. **Batch expiration queries** - Fetch multiple expirations in one call
3. **Rate limit detection and backoff**:
```python
if response.status_code == 429:
    retry_after = int(response.headers.get('Retry-After', 60))
    await asyncio.sleep(retry_after)
```
4. **Pre-fetch chains before market open** - 9:25 AM job fetches all needed chains
5. **Telegram alert on rate limit** - "E*TRADE throttling detected, retrying..."

**Detection:** HTTP 429 response or `RateLimitExceeded` error from E*TRADE API.

**Phase to address:** Phase 1 (E*TRADE Options API Integration)

**Sources:**
- [E*TRADE API FAQ](https://developer.etrade.com/support/frequently-asked-questions)
- [E*TRADE API Documentation](https://developer.etrade.com/getting-started)

---

### Pitfall 16: Intraday to Multi-Day Psychology Transition

**What goes wrong:** User is accustomed to intraday equity bot closing all positions by 3:55 PM. Wheel strategy holds positions for weeks. User gets anxious about overnight risk.

**Why it happens:**
- Fundamental strategy shift: intraday (zero overnight risk) → wheel (persistent exposure)
- No mental preparation for holding through volatility
- Different risk profile requires different emotional tolerance

**Consequences:**
- User manually overrides bot to close positions early
- Breaking wheel cycle discipline (closing puts at small profit instead of waiting for assignment)
- Strategy underperformance due to human interference

**Prevention:**
1. **Education phase before launch**:
   - Telegram message explaining wheel strategy risk profile
   - "You will hold positions overnight and over weekends"
   - Comparison: "Intraday had 0 overnight exposure. Wheel has 24/7 exposure."
2. **Gradual transition**:
   - Start with 1 contract only for first month
   - Scale up after user comfort level increases
3. **Daily position summary** - 4:30 PM message:
   - Current positions
   - Max risk (if assigned)
   - Days until expiration
4. **Configurable risk limits** - User sets `MAX_POSITION_SIZE`, `MAX_OVERNIGHT_VALUE`

**Detection:** Frequent manual position closures during wheel cycles.

**Phase to address:** Phase 5 (Deprecate Intraday Strategies) - transition management

**Sources:**
- [TradingSim: Day Trading vs Swing Trading](https://www.tradingsim.com/blog/day-trading-vs-swing-trading-which-strategy-suits-you-best)
- [IBKR: Basic Guide to Trade Options Intraday](https://www.interactivebrokers.com/campus/ibkr-quant-news/basic-guide-to-trade-options-intraday-strategies-and-risk-management/)

---

## Phase-Specific Warnings

| Phase | Likely Pitfall | Mitigation |
|-------|---------------|------------|
| **Phase 1: E*TRADE Options API** | API response parsing fragility (Pitfall 5) | Build separate parsing module, use schema validation, save real responses as test fixtures |
| **Phase 1: Options API** | Stale quotes at market open (Pitfall 6) | Implement quote freshness validation, check `quoteStatus` field, verify market data agreement |
| **Phase 1: Options API** | Rate limiting during chain queries (Pitfall 15) | Cache options chain for 60s, implement backoff, pre-fetch at 9:25 AM |
| **Phase 2: Assignment Detection** | Assignment detection blindness (Pitfall 1) | Position reconciliation at market open, compare E*TRADE positions with local state, Telegram alerts on mismatch |
| **Phase 2: Assignment Detection** | Pin risk uncertainty (Pitfall 2) | Conservative assumptions near strike, $0.50 buffer, Monday morning reconciliation |
| **Phase 2: Covered Calls** | Cost basis tracking errors (Pitfall 3) | New DB table for adjusted cost basis, validate strike >= basis, show basis in Telegram approvals |
| **Phase 2: Covered Calls** | Database schema inadequacy (Pitfall 14) | Design `wheel_cycles` table with state machine, foreign keys to trades |
| **Phase 3: Strike Selection** | Greeks calculation divergence (Pitfall 7) | Use broker's Greeks not calculated, accept delta ranges not exact values, validate for 1 week |
| **Phase 3: Strike Selection** | Cash requirement miscalculation (Pitfall 10) | Pre-trade buying power check, position sizing based on available cash |
| **Phase 3: Order Placement** | Options order type incompatibility (Pitfall 11) | Force `DAY` orders for options (not GTC), validate in order builder |
| **Phase 4: Profit Management** | Rolling without strike validation (Pitfall 9) | Roll cost <= 25% original credit, new strike >= 80% original, Telegram approval required |
| **Phase 4: Edge Cases** | Corporate actions breaking chain (Pitfall 4) | Detect adjusted contracts, pause trading, manual close for adjusted positions |
| **Phase 4: Edge Cases** | Early assignment on dividend (Pitfall 12) | Integrate dividend calendar, warn 3 days before ex-div, close ITM calls early |
| **Phase 4: Edge Cases** | Overnight monitoring gap (Pitfall 13) | After-hours job at 6 PM, weekend reconciliation Saturday 10 AM |
| **Phase 5: Transition** | Concurrent signal processing (Pitfall 8) | Master mode flag (WHEEL vs INTRADAY), filter signals by mode, graceful transition period |
| **Phase 5: Transition** | Psychology transition (Pitfall 16) | Education messages, start with 1 contract, daily position summaries, gradual scaling |

---

## Integration-Specific Warnings

These pitfalls arise from integrating options with existing equity infrastructure:

### Existing Position Lock Insufficient for Options
- **Problem:** `_position_lock` RLock prevents concurrent equity trades but doesn't account for options + equity interaction
- **Scenario:** Short put assigned (now holding shares) while intraday job tries to buy BITU
- **Solution:** Expand lock scope to cover "any IBIT exposure" (equity OR options)

### Paper Mode Position Tracking Divergence
- **Problem:** Existing `_paper_positions` dict tracks equity, but options have complex state (assigned shares vs contracts)
- **Scenario:** Paper mode doesn't simulate assignment correctly
- **Solution:** Separate `_paper_options_positions` with assignment simulation logic

### Event Logging Assumes Single Instrument Type
- **Problem:** `log_event()` designed for equity trades, doesn't capture options-specific fields (strike, expiration, Greeks)
- **Solution:** Add `event_metadata` JSON column to `event_log` table for structured options data

### Telegram Approval Message Format
- **Problem:** Existing format shows "Buy 100 BITU at $X" - options need different format
- **Solution:** Template system: equity template vs options template with strike/expiration/Greeks

### Daily Trade Flag Reset Logic
- **Problem:** `_trades_today` dict keys are signal names (e.g., "crash_day") - wheel signals have different lifecycle
- **Solution:** Separate `_options_positions_today` tracking by cycle_id not signal name

---

## Sources Summary

**HIGH Confidence:**
- E*TRADE official API documentation (Market, Quote endpoints)
- OCC options assignment rules (authoritative source)

**MEDIUM Confidence:**
- Multiple broker support articles (Schwab, Fidelity, IBKR, Robinhood) - verified across sources
- Options education sites (Option Alpha, SteadyOptions, Options Trading IQ) - industry standard guidance
- Exchange documentation (FINRA, Nasdaq) - regulatory requirements

**LOW Confidence (Flagged for Validation):**
- E*TRADE undocumented rate limits (2 req/sec) - mentioned but not in official docs
- Greeks calculation divergence magnitude - needs empirical testing with live E*TRADE data
- Pin risk $0.50 buffer - rule of thumb, not official threshold

---

*Research completed: 2026-03-20*
*Total sources reviewed: 50+*
*Confidence level: MEDIUM (WebSearch verified with official documentation where available)*
