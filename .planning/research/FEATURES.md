# Feature Landscape: Wheel Strategy for IBIT Options

**Domain:** Options trading bot (wheel strategy)
**Researched:** 2026-03-20
**Confidence:** MEDIUM (verified with multiple sources, some E*TRADE-specific details need validation)

## Table Stakes

Features users expect from a wheel strategy bot. Missing = strategy feels incomplete or unmanageable.

| Feature | Why Expected | Complexity | Dependencies | Notes |
|---------|--------------|------------|--------------|-------|
| **Cash-Secured Put Selling** | Core Phase 1 of wheel - primary entry mechanism | Medium | E*TRADE options API, options chain data, cash balance checking | Sell OTM puts at 0.20-0.30 delta, collect premium |
| **Assignment Detection** | Must know when puts assigned to transition to Phase 2 | Medium | E*TRADE position API, weekend/Monday polling | Assignment happens Friday evening, shares appear Monday AM |
| **Covered Call Writing** | Core Phase 2 of wheel - primary income on owned shares | Medium | E*TRADE options API, position verification | Sell OTM calls at 0.25-0.35 delta on assigned shares |
| **Strike Selection (Delta-Based)** | Industry standard for wheel is delta targeting | Low | Options chain with Greeks (delta) | Target 0.20-0.30 delta for puts, 0.25-0.35 for calls |
| **DTE Selection** | 30-45 DTE is optimal theta decay window | Low | Options chain filtering | Configurable param, default 30-45 days |
| **Position Tracking Across Cycle** | Track full cycle: put → assignment → call → called away | High | Database schema changes, cycle state machine | Need unique cycle IDs, cost basis tracking, premium accumulation |
| **Telegram Approval Flow (Options)** | User must approve options trades before execution | Medium | Extend existing approval system | Show strike/exp/premium/delta/Greeks in approval message |
| **Expiration Monitoring** | Alert user before expiration, handle auto-close logic | Medium | Scheduler jobs, 21 DTE tracking | Exit/roll at 21 DTE or earlier based on P&L |
| **Call Assignment Detection** | Detect when shares called away to restart cycle | Low | E*TRADE position API | Shares removed = called away, return to Phase 1 |
| **Paper Mode for Options** | Test wheel logic without real options trades | Medium | Extend existing paper trading | Simulate assignment, Greeks, expiration |
| **Options Order Preview** | Show cost/margin before placement (E*TRADE requirement) | Low | E*TRADE preview API | Already exists for equities, adapt for options |
| **Cost Basis Tracking** | Track adjusted cost basis through assignments/premiums | Medium | Database updates, P&L calculation | Shares acquired at strike - premium collected |

## Differentiators

Features that elevate the bot beyond basic wheel execution. Not expected, but add significant value.

| Feature | Value Proposition | Complexity | Dependencies | Notes |
|---------|-------------------|------------|--------------|-------|
| **50% Profit Close** | Capture gains early, redeploy capital faster | Medium | P&L monitoring per position, auto-close logic | Close at 50-75% profit with 21+ DTE remaining (industry best practice) |
| **Signal-Based Put Entry** | Enter wheel only at favorable times (pullbacks, elevated IV) | Low | Extend existing signal system | Avoid selling puts during rallies or low IV |
| **Rolling Options (Defensive)** | Extend positions to avoid assignment or improve strikes | High | Multi-leg order support, credit verification | Roll out/up/down for net credit only; limit to 2 rolls per position |
| **IV Rank Filtering** | Only sell options when IV is elevated (better premiums) | Low | IV historical data, percentile calculation | Skip trades when IV < 30th percentile |
| **Real-Time Greeks Display** | Show delta/theta/vega in position monitoring | Low | E*TRADE or data provider Greeks | Helps manual decision-making |
| **Cycle Performance Analytics** | Show full-cycle returns (put premium + call premium + capital gain) | Medium | Database aggregation queries | More useful than trade-by-trade P&L |
| **Telegram Position Summary** | `/positions` command shows wheel state for each cycle | Low | Extend existing command handlers | "Cycle #3: In shares, 15 DTE on CC at $52 strike" |
| **Multi-Expiration Tracking** | Handle multiple put/call positions at different expirations | High | Enhanced position tracking, scheduler complexity | Allows scaling capital across multiple cycles |
| **Auto-Roll Suggestions** | Bot suggests roll parameters when position at risk | Medium | Decision logic, approval flow | "Roll $50 put to $48 3/28 for $0.30 credit?" |
| **Premium Income Dashboard** | Streamlit page showing total premiums collected, annualized return | Low | Database queries, visualization | Strong feedback loop for strategy validation |

## Anti-Features

Features to explicitly NOT build. Avoid complexity, maintain wheel purity.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Multi-Leg Spreads (Iron Condors, Butterflies)** | Adds massive complexity, different risk profile than wheel, harder to manage | Stick to single-leg CSP and CC only |
| **Multi-Underlying Support (v1)** | IBIT-only simplifies options chain lookups, position tracking, reduces cognitive load | Future enhancement after wheel proven on IBIT |
| **Fully Autonomous Execution** | Options have more nuance than equities; require human judgment for strikes, rolls, early assignment risk | Keep Telegram approval for all options trades |
| **Real-Time Greeks Dashboard** | Over-engineering; not needed for basic wheel which is a slow strategy (weeks not hours) | Show Greeks in approval message, don't build live monitoring |
| **Complex Assignment Handling (Early Exercise)** | Early assignment is rare on cash-secured puts; adds complexity for edge case | Handle standard expiration assignment only; alert user if early assignment detected |
| **Intraday Options Trading** | Wheel is a weekly/monthly strategy; intraday options = different strategy entirely | Keep wheel separate from deprecated intraday equity strategies |
| **Dividend Capture Integration** | IBIT doesn't pay dividends; irrelevant for this underlying | Skip dividend tracking |
| **Earnings-Based Timing** | ETFs don't have earnings; corporate earnings-based wheel logic doesn't apply | Use IV rank and technical signals instead |
| **Options Spreads for Risk Reduction** | Wheel relies on cash-secured risk; spreads fundamentally change the strategy | Accept assignment risk as part of strategy |
| **Auto-Exercise ITM Calls** | Broker handles exercise automatically; no action needed | Let E*TRADE auto-exercise ITM options at expiration |

## Feature Dependencies

```
Cash-Secured Put Selling
  ↓
Assignment Detection
  ↓
Covered Call Writing
  ↓
Call Assignment Detection
  ↓
[Return to Cash-Secured Put Selling]

Position Tracking Across Cycle
  ↑ (tracks all phases)

Strike Selection (Delta-Based) ← required by → Cash-Secured Put Selling, Covered Call Writing
DTE Selection ← required by → Cash-Secured Put Selling, Covered Call Writing
Telegram Approval Flow ← required by → All trade execution
Expiration Monitoring ← enables → 50% Profit Close, Rolling Options

Signal-Based Put Entry ← uses → Existing signal system (mean reversion, IV rank)
Rolling Options ← requires → Expiration Monitoring, Multi-leg order support
50% Profit Close ← requires → P&L monitoring, Expiration Monitoring
```

## MVP Recommendation

**Phase 1: Basic Wheel Cycle (Minimum Viable Wheel)**

Prioritize (in order):
1. **E*TRADE Options API Integration** - Can't trade without it; foundation for everything
2. **Cash-Secured Put Selling** - Entry point into wheel
3. **Assignment Detection** - Transition mechanism to Phase 2
4. **Covered Call Writing** - Income generation on owned shares
5. **Position Tracking Across Cycle** - Know where you are in the wheel
6. **Strike/DTE Selection** - Delta-based (0.20-0.30 puts, 0.25-0.35 calls), 30-45 DTE
7. **Telegram Approval Flow (Options)** - Safe execution with human oversight
8. **Expiration Monitoring** - Don't let positions expire unmanaged

**Phase 2: Profit Management**

9. **50% Profit Close** - Capital efficiency, proven best practice
10. **Rolling Options (Defensive)** - Handle positions at risk
11. **Signal-Based Put Entry** - Improve entry timing

**Phase 3: Analytics & UX**

12. **Cycle Performance Analytics** - Understand full-cycle returns
13. **Premium Income Dashboard** - Visual feedback
14. **Telegram Position Summary** - Quick status checks

**Defer to Future (Post-v1):**
- Multi-expiration tracking - Add after single-cycle proven
- IV Rank Filtering - Nice-to-have, not essential for v1
- Auto-Roll Suggestions - Manual rolls acceptable initially
- Real-Time Greeks Display - Static Greeks in approvals sufficient

## Complexity Assessment

| Feature Category | Overall Complexity | Key Challenges |
|------------------|-------------------|----------------|
| **E*TRADE Options API** | High | New API surface, OAuth, options-specific endpoints, Greeks data |
| **Basic Wheel (Put → Assignment → Call)** | Medium | State machine logic, assignment timing (weekend), cost basis math |
| **Position Tracking** | High | Database schema changes, cycle lifecycle, premium accumulation, adjusted cost basis |
| **Telegram Approval (Options)** | Low | Extend existing pattern, different message format |
| **Strike/DTE Selection** | Low | Filter options chain by delta/DTE criteria |
| **50% Profit Close** | Medium | P&L monitoring, auto-close with approval |
| **Rolling Options** | High | Multi-leg orders, credit verification, decision logic |
| **Signal-Based Entry** | Low | Reuse existing signal infrastructure |

## Dependencies on Existing Infrastructure

| Existing Component | How Wheel Leverages It | Modifications Needed |
|--------------------|------------------------|----------------------|
| **E*TRADE Client** (`src/etrade_client.py`) | OAuth, order placement foundation | Add options-specific methods: `get_options_chain()`, `place_options_order()`, `preview_options_order()`, `get_options_positions()` |
| **Telegram Bot** (`src/telegram/`) | Approval flow, notifications, commands | Extend approval messages for options (show Greeks, expiration), add `/positions` for wheel cycles |
| **Database** (`src/database.py`) | Persistence layer | New tables: `options_positions`, `wheel_cycles`, `options_trades`; new columns in `bot_state` for wheel config |
| **Scheduler** (`src/smart_scheduler.py`) | Job orchestration | New jobs: assignment check (Monday AM), expiration check (daily), 50% profit check (hourly during market) |
| **Paper Trading** (`BotConfig.mode`) | Testing without real trades | Extend to simulate options Greeks, assignment, expiration |
| **Data Providers** (`src/data_providers.py`) | Market data fallback chain | Add options chain data, IV data (may need new provider for Greeks) |
| **Error Alerting** (`src/error_alerting.py`) | Telegram error notifications | Reuse as-is for options errors |

## Data Requirements

| Data Type | Source | Frequency | Purpose |
|-----------|--------|-----------|---------|
| **Options Chain** | E*TRADE API | On-demand (trade setup) | Strike/expiration selection, delta lookup |
| **Options Greeks** | E*TRADE API or data provider | On-demand (trade setup, monitoring) | Delta for strike selection, theta for decay monitoring |
| **Options Positions** | E*TRADE API | Daily (assignment check), hourly (P&L monitoring) | Detect assignment, calculate P&L, expiration tracking |
| **IV Rank (optional)** | Data provider (Alpaca, Finnhub) | Daily | Signal-based entry filtering |
| **IBIT Price** | Existing data providers | Real-time during market hours | P&L calculation, strike selection context |

## Known Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| **E*TRADE Options API Limits Unknown** | May hit rate limits or have restricted options capabilities | Research E*TRADE developer docs thoroughly; contact support if needed; implement rate limiting |
| **Assignment Detection Delay** | Assignment happens Friday evening, may not detect until Monday market open | Schedule Monday pre-market check; alert user if assignment detected late |
| **Greeks Data Availability** | E*TRADE may not provide live Greeks or may be delayed | Fallback to Black-Scholes approximation if needed; accept staleness for delta targeting |
| **Early Assignment (Edge Case)** | Put holder can exercise early (rare but possible) | Alert user if detected; manual handling acceptable for v1 |
| **Rolling Complexity** | Multi-leg orders (buy to close + sell to open) may fail partially | Use E*TRADE spread order if supported; otherwise execute sequentially with rollback logic |
| **Cost Basis Calculation Errors** | Complex math across assignments and premiums | Thorough unit tests; validate against E*TRADE's reported cost basis |
| **IBIT Liquidity Changes** | If IBIT options become illiquid, wide spreads hurt strategy | Monitor bid-ask spreads; alert user if spread > threshold (e.g., 5% of premium) |

## Sources

### Wheel Strategy Mechanics
- [Three Things to Know About the Wheel Strategy | Charles Schwab](https://www.schwab.com/learn/story/three-things-to-know-about-wheel-strategy)
- [Wheel Strategy: Complete Options Income Guide [2026]](https://quantwheel.com/learn/wheel-strategy/)
- [What Is the Wheel Options Strategy? (2026) | Rockwell Trading](https://www.rockwelltrading.com/what-is-the-wheel-options-strategy-how-it-works-why-its-boring-and-why-thats-the-point-2026/articles/coffee-with-markus/)
- [The Options Wheel Strategy (How to Trade in Python) | Alpaca Markets](https://alpaca.markets/learn/options-wheel-strategy)
- [How to Trade the Options Wheel Strategy | Option Alpha](https://optionalpha.com/blog/wheel-strategy)

### Assignment Detection & Management
- [Wheel Options Strategy: Complete Guide | Options Cafe](https://options.cafe/blog/wheel-options-strategy-complete-guide/)
- [Advanced Wheel Strategy Adjustments | Wheel Strategy Options](https://wheelstrategyoptions.com/blog/advanced-wheel-strategy-adjustments-mastering-the-art-of-dynamic-risk-management/)
- [Wheel Strategy Best DTE | Days to Expiry](https://www.daystoexpiry.com/blog/wheel-options-trading-strategy-complete-dte-playbook)
- [Option Wheel Strategy in High Volatility Market (2026) | MoneySense AI](https://moneysense.ai/blog/ai-news/option-wheel-strategy-high-volatility-market-2026)

### 50% Profit Close & Rolling
- [Strategic Early Exits: 50% Rule in Wheel Trading | Wheel Strategy Options](https://wheelstrategyoptions.com/blog/strategic-early-exits-maximizing-csp-profits-with-the-50-rule-in-wheel-options-trading/)
- [The Wheel Strategy: How It Works | Ryan OConnell, CFA](https://ryanoconnellfinance.com/wheel-strategy/)
- [Complete Guide to Wheel Options Trading | InsiderFinance](https://www.insiderfinance.io/resources/complete-guide-to-wheel-options-trading-strategy)
- [Rolling Options: How to Save a Losing Trade | Sam Kling (Medium)](https://samkling.medium.com/rolling-options-how-to-save-a-losing-options-trade-wheel-options-strategies-424c0f4999e)

### Strike/DTE Selection
- [Delta Explained: The Essential Options Greek [2026]](https://quantwheel.com/learn/options-delta-explained/)
- [Optimizing The Wheel Strategy: DTE, Delta, Trade Exit | Wheel Strategy Options](https://wheelstrategyoptions.com/blog/optimizing-the-wheel-strategy-advanced-dte-delta-and-trade-exit-tactics/)
- [Wheel Strategy Best DTE Guide | Days to Expiry](https://www.daystoexpiry.com/blog/wheel-strategy-best-dte-optimizing-days-to-expiration-for-puts-and-calls)
- [Options Delta Explained | The Wheel Strategy](https://thewheelstrategy.com/delta/)

### Position Tracking & Automation
- [GitHub - alpacahq/options-wheel | Alpaca HQ](https://github.com/alpacahq/options-wheel)
- [Optimizing Wheel Strategy Trade Tracking | Wheel Strategy Options](https://wheelstrategyoptions.com/blog/optimizing-wheel-strategy-the-imperative-of-meticulous-trade-tracking-for-superior-returns/)
- [Options Expiration Explained: For Wheel traders [2026]](https://quantwheel.com/learn/options-expiration/)
- [Wheel Strategy Real-Time Alerts | Options Cafe](https://options.cafe/blog/wheel-strategy-real-time-alerts-now-available-full-transparency/)

### E*TRADE API & Assignment
- [E*TRADE API Documentation | Developer Portal](https://developer.etrade.com/getting-started)
- [E*TRADE API Alert Service](https://apisb.etrade.com/docs/api/user/api-alert-v1.html)

### Options Expiration & Monitoring
- [Options Expiration: Complete Guide | QuantWheel](https://quantwheel.com/learn/options-expiration/)
- [Automatically Exit Options Before Expiration | Option Alpha](https://optionalpha.com/blog/automatically-exit-your-option-trades-before-expiration)
- [Exit Options Automated Position Management | Option Alpha](https://optionalpha.com/help/exit-options)

### IBIT Options Liquidity
- [IBIT Options Chain | Barchart](https://www.barchart.com/etfs-funds/quotes/IBIT/options)
- [iShares Bitcoin Trust ETF (IBIT) Options | Yahoo Finance](https://finance.yahoo.com/quote/IBIT/options/)
- [3 Best Bitcoin ETF Picks for 2026 | Nasdaq](https://www.nasdaq.com/articles/3-best-bitcoin-etf-picks-2026)
- [IBIT ETF Analysis (2026) | Bitget Academy](https://www.bitget.com/academy/ibit-etf-bitcoin)
