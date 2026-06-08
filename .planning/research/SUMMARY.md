# Project Research Summary

**Project:** BTrade - IBIT Wheel Strategy Implementation
**Domain:** Options trading wheel strategy on existing intraday equity trading bot
**Researched:** 2026-03-20
**Confidence:** HIGH

## Executive Summary

The wheel strategy can be cleanly integrated into the existing BTrade equity bot by extending the proven mixin-based architecture with three new components: OptionsChainMixin (strike/expiration selection), OptionsExecutionMixin (options order execution), and WheelCycleMixin (put → assignment → call coordination). The existing E*TRADE API provides comprehensive options support including Greeks, chains, and order placement, requiring only one new Python dependency (`blackscholes` for verification/fallback Greeks calculation).

The recommended approach is a gradual transition from intraday equity trading to the wheel strategy through five phases: (1) E*TRADE Options API integration with paper trading, (2) Basic wheel cycle implementation (CSP → assignment detection → covered call), (3) Profit management (50% close, rolling), (4) Edge case handling (corporate actions, early assignment, dividend timing), and (5) Deprecation of intraday strategies with user transition management. This phase structure is informed by dependency analysis showing that assignment detection must precede covered calls, and that profit management requires the full cycle working first.

Key risks center on assignment detection blindness (mitigate with daily position reconciliation), cost basis tracking errors in multi-cycle wheels (mitigate with dedicated database schema), and API response parsing fragility (mitigate with schema validation). The most critical finding is that E*TRADE provides Greeks directly in options chain responses, eliminating the need for complex Black-Scholes calculations and reducing implementation complexity significantly.

## Key Findings

### Recommended Stack

The stack additions are minimal due to E*TRADE's comprehensive options API support. Only one new dependency is required: `blackscholes>=0.2.0` for Greeks verification and fallback calculations. The library is actively maintained (Dec 2024 release), supports Python 3.10+, and provides all necessary Greeks (delta, gamma, theta, vega, rho) plus implied volatility calculations.

**Core technologies:**
- **blackscholes 0.2.0+**: Greeks calculation and verification — actively maintained, pure Python, no C dependencies, provides fallback when E*TRADE Greeks unavailable
- **E*TRADE Options API**: Options chains, Greeks, order preview/placement — already integrated for equities, extends with 4 new endpoints (optionchains, optionexpiredate, options preview/place)
- **scipy 1.11+**: Implied volatility optimization — already in stack, reuse for IV calculations via Brent's method

**What NOT to add:**
- py_vollib (discontinued 2017), mibian (discontinued 2016), QuantLib (overkill), custom OSI parser libraries (simple regex sufficient)

**Critical finding:** E*TRADE returns Greeks directly in options chain responses. No need for heavy calculation engines. This reduces complexity dramatically and means the bot can use broker-calculated Greeks as the source of truth.

### Expected Features

The wheel strategy has clear table stakes that users expect from any automated wheel implementation. Differentiation comes from intelligent entry timing and profit management rather than novel mechanics.

**Must have (table stakes):**
- Cash-secured put selling with delta-based strike selection (0.20-0.30 delta, 30-45 DTE)
- Assignment detection via position reconciliation
- Covered call writing post-assignment (0.25-0.35 delta)
- Position tracking across full cycle (put → shares → call → cash)
- Telegram approval flow for all options trades
- Expiration monitoring with alerts
- Cost basis tracking through assignments and premiums
- Paper mode for testing

**Should have (competitive):**
- 50% profit close rule (industry best practice for capital efficiency)
- Signal-based put entry (only enter during IBIT pullbacks or elevated IV)
- Defensive rolling when positions tested (max 2 rolls, credit-only)
- Cycle performance analytics (full-cycle returns, not trade-by-trade)
- IV rank filtering (skip trades when IV < 30th percentile)

**Defer (v2+):**
- Multi-underlying support (IBIT-only for v1 simplifies chain lookups)
- Multi-expiration tracking (prove single-cycle first)
- Real-time Greeks dashboard (static Greeks in approvals sufficient)
- Intraday options trading (wheel is weekly/monthly strategy)

**Anti-features (explicitly avoid):**
- Multi-leg spreads (iron condors, butterflies — adds complexity, different risk profile)
- Fully autonomous execution (options need human judgment for strikes/rolls)
- Dividend capture integration (IBIT doesn't pay meaningful dividends)

### Architecture Approach

The existing mixin-based TradingBot architecture extends cleanly with parallel options-specific mixins. The core pattern of Strategy → Execution → Broker/Persistence remains identical, with new mixins composing into TradingBot without modifying existing equity mixins. Assignment detection runs as a scheduled APScheduler job polling E*TRADE positions API daily at 8 AM ET.

**Major components:**
1. **OptionsChainMixin** (`src/trading_bot/options_chain_mixin.py`) — Fetches options chains from E*TRADE, selects strikes by delta targeting, retrieves Greeks, implements strike selection algorithm
2. **OptionsExecutionMixin** (`src/trading_bot/options_execution_mixin.py`) — Previews and places options orders, handles fills via polling, adapts Telegram approval flow for options (shows strike/expiration/Greeks), records trades to database
3. **WheelCycleMixin** (`src/trading_bot/wheel_cycle_mixin.py`) — Coordinates full wheel cycle state machine (CASH → SHORT_PUT → HOLDING_SHARES → COVERED_CALL), detects assignments via position diffing, triggers covered calls automatically post-assignment, monitors for profit-taking
4. **WheelStrategy** (`src/wheel_strategy.py`) — Generates OptionsSignal based on pullback detection, IV rank evaluation, cash sufficiency checks; configurable delta/DTE targets; signal-driven entry logic
5. **Database Schema Extensions** — New tables: `options_positions` (tracks option trades with Greeks, assignment status), `wheel_cycles` (links put → assignment → call with state machine), `equity_positions` (tracks shares acquired via assignment)

**Architecture pattern:** Parallel mixin hierarchy. Existing ExecutionMixin handles equity trades unchanged. New OptionsExecutionMixin handles options trades. Same approval flow, notification system, database logging patterns reused. Zero breaking changes to equity functionality.

**Key insight:** Assignment detection is position diffing, not event-based. E*TRADE API doesn't push assignment notifications. Bot must poll positions daily and compare to previous day's snapshot stored in database. This is reliable but means assignment detection latency of ~12 hours (assignment Friday 5:30 PM, detection Monday 8 AM).

### Critical Pitfalls

These are the top 5 pitfalls most likely to cause rewrites or financial losses:

1. **Assignment Detection Blindness** — Bot unaware when options assigned, state diverges from broker. Prevention: Daily position reconciliation at 8 AM comparing E*TRADE positions to database state, Telegram alerts on mismatch, store position snapshots with timestamps, detect short option qty changes and new equity positions.

2. **Pin Risk Uncertainty** — Options expire near strike price, unclear if assignment will occur until 5:30 PM ET Saturday deadline. Prevention: Conservative assumptions (if within $0.50 of strike, assume worst case), Monday morning reconciliation before new signals, no position decisions after Friday 5:30 PM.

3. **Cost Basis Tracking Errors** — Selling covered calls below adjusted cost basis locks in losses. Prevention: Dedicated `adjusted_cost_basis` DB column updated on assignment (strike - premium) and additional premiums, strike validation ensures CC strike >= adjusted basis, Telegram approval shows cost basis warnings.

4. **E*TRADE API Response Parsing Fragility** — Options responses have different structure than equity (OptionChainResponse > OptionPair > Call/Put), existing deep `.get()` chains break with IndexError/KeyError. Prevention: Separate options parsing module using pydantic dataclasses, full response logging on failure, integration tests with real E*TRADE response fixtures.

5. **Concurrent Signal Processing During Wheel** — Holding IBIT shares from assignment while intraday equity signals fire simultaneously. Prevention: Master trading mode flag (WHEEL vs INTRADAY, mutually exclusive), signal filters check mode before generating, graceful transition period closes all intraday positions before enabling wheel.

**Additional moderate pitfalls:** Stale quotes at market open (validate quote freshness <60s), Greeks divergence between E*TRADE and calculated (use broker Greeks as truth), cash requirement miscalculation for multiple puts (validate buying power pre-trade), rolling without strike validation (enforce roll cost <=25% original credit, new strike >=80% original).

## Implications for Roadmap

Based on research, suggested phase structure:

### Phase 1: E*TRADE Options API Foundation
**Rationale:** All downstream components depend on verified broker integration. Must confirm E*TRADE options endpoints work before building abstractions. Addresses Pitfall #4 (API parsing fragility) and Pitfall #6 (stale quotes).

**Delivers:**
- ETradeClient methods: `get_option_chains()`, `get_option_quote()`, `preview_options_order()`, `place_options_order()`
- Schema validation using dataclasses (OptionContract, OptionQuote)
- Quote freshness validation (<60s, check `quoteStatus` field)
- Integration tests against E*TRADE sandbox
- Mock implementations for paper trading

**Addresses (from FEATURES.md):**
- E*TRADE Options API Integration (table stakes)
- Options Order Preview (table stakes)
- Paper Mode for Options (table stakes)

**Avoids (from PITFALLS.md):**
- Pitfall #4: E*TRADE API Response Parsing Fragility (separate parsing module, pydantic validation)
- Pitfall #6: Stale Options Quotes at Market Open (freshness checks)
- Pitfall #15: Rate Limiting on Options Chain Queries (caching, backoff logic)

**Stack elements:** E*TRADE Options API endpoints, dataclasses for schema validation

**Research needs:** Standard patterns. E*TRADE API well-documented, skip `/gsd:research-phase`.

---

### Phase 2: Database Schema & Basic Wheel Cycle
**Rationale:** Database schema must exist before execution/strategy layers. Basic cycle (CSP → assignment → CC) is the MVP — prove the wheel works before adding profit management. Addresses Pitfall #1 (assignment detection), Pitfall #3 (cost basis tracking), Pitfall #14 (schema inadequacy).

**Delivers:**
- Database tables: `options_positions`, `wheel_cycles`, `equity_positions`
- Database methods: `record_options_trade_entry()`, `record_assignment()`, `start_wheel_cycle()`, `update_wheel_cycle_state()`
- WheelCycleMixin: `check_for_assignments()` (position diffing logic), `handle_assignment()` (auto-trigger CC)
- Assignment detection job (APScheduler, daily 8 AM ET)
- Cost basis tracking with `adjusted_cost_basis` column

**Addresses (from FEATURES.md):**
- Cash-Secured Put Selling (table stakes)
- Assignment Detection (table stakes)
- Covered Call Writing (table stakes)
- Position Tracking Across Cycle (table stakes)
- Cost Basis Tracking (table stakes)

**Avoids (from PITFALLS.md):**
- Pitfall #1: Assignment Detection Blindness (daily reconciliation, position diffing)
- Pitfall #2: Pin Risk Uncertainty (conservative assumptions, Monday reconciliation)
- Pitfall #3: Cost Basis Tracking Errors (adjusted_cost_basis schema, strike validation)
- Pitfall #14: Database Schema for Multi-State Wheel Cycle (wheel_cycles table with state machine)

**Stack elements:** SQLite schema extensions, APScheduler job additions

**Research needs:** Standard patterns. Database design well-established, skip `/gsd:research-phase`.

---

### Phase 3: Strike Selection & Execution
**Rationale:** With database in place, implement the "how to trade" logic. Strike selection is critical for wheel strategy effectiveness (delta targeting drives win rate). Extends existing Telegram approval flow for options.

**Delivers:**
- OptionsChainMixin: `get_option_chain()` with DTE filtering, `select_put_strike()` / `select_call_strike()` with delta targeting
- OptionsExecutionMixin: `execute_options_trade()`, `_wait_for_options_fill()`, Telegram approval adaptation
- WheelStrategy: `get_today_signal()` with pullback detection, IV percentile calc, cash sufficiency check
- WheelStrategyConfig: delta/DTE targets, entry thresholds, position limits
- SmartScheduler integration: `_wheel_signal_job()` at 9:35 AM

**Addresses (from FEATURES.md):**
- Strike Selection (Delta-Based) (table stakes)
- DTE Selection (table stakes)
- Telegram Approval Flow (Options) (table stakes)
- Signal-Based Put Entry (differentiator)

**Avoids (from PITFALLS.md):**
- Pitfall #7: Greeks Calculation Accuracy (use E*TRADE's Greeks, delta ranges not exact)
- Pitfall #10: Cash Requirement Miscalculation (pre-trade buying power check)
- Pitfall #11: Options Order Type Incompatibility (force DAY orders, not GTC)

**Stack elements:** blackscholes library (verification/fallback), E*TRADE Greeks from chain response

**Research needs:** Standard patterns. Delta-based strike selection well-documented, skip `/gsd:research-phase`.

---

### Phase 4: Profit Management & Edge Cases
**Rationale:** With basic cycle working, add profit optimization (50% close rule) and handle edge cases that can break the cycle (corporate actions, early assignment, dividends). This is where strategy moves from "functional" to "production-ready."

**Delivers:**
- Position monitoring job: `monitor_options_positions()` (every 30 min), 50% profit close logic
- Defensive rolling: `roll_options_position()` with validation (cost <=25% credit, strike >=80% original)
- Expiration monitoring: alerts at 21 DTE, auto-close suggestions
- Corporate actions detection (adjusted contract symbols)
- Dividend calendar integration (early assignment warnings for ITM calls near ex-div)
- After-hours monitoring job (6 PM daily for significant moves)

**Addresses (from FEATURES.md):**
- 50% Profit Close (differentiator — industry best practice)
- Rolling Options (Defensive) (differentiator)
- Expiration Monitoring (table stakes)

**Avoids (from PITFALLS.md):**
- Pitfall #9: Rolling Without Strike Validation (enforce roll criteria)
- Pitfall #4: Corporate Actions Breaking Options Chain (detect adjusted contracts, pause trading)
- Pitfall #12: Early Assignment on Dividend Ex-Date (calendar integration, pre-ex-div warnings)
- Pitfall #13: Overnight Position Monitoring Gap (after-hours job)

**Stack elements:** APScheduler jobs (30 min monitoring, 6 PM after-hours check)

**Research needs:** May need `/gsd:research-phase` for corporate actions API (Alpaca or E*TRADE notifications). Early assignment risk formulas need validation.

---

### Phase 5: Transition & Deprecation
**Rationale:** Final phase handles user psychology and system transition. Intraday equity strategies deprecated, wheel becomes primary. User education critical for overnight risk tolerance shift.

**Delivers:**
- `wheel_enabled` flag in BotConfig
- Signal filter: disable SmartStrategy when wheel_enabled=True
- Streamlit dashboard updates: options positions display, wheel cycle state
- Telegram commands: `/wheel` (cycle status), `/enable_wheel` (mode switch)
- Education flow: Telegram messages explaining risk profile shift
- Gradual scaling: start with 1 contract, increase after user comfort
- Daily position summary (4:30 PM): current positions, max risk, days to expiration

**Addresses (from FEATURES.md):**
- Telegram Position Summary (differentiator)
- Cycle Performance Analytics (differentiator)

**Avoids (from PITFALLS.md):**
- Pitfall #8: Concurrent Signal Processing (master mode flag, signal filters)
- Pitfall #16: Intraday to Multi-Day Psychology Transition (education, gradual scaling)

**Stack elements:** BotConfig extensions, Streamlit UI updates

**Research needs:** Standard patterns. UX/UI updates well-understood, skip `/gsd:research-phase`.

---

### Phase Ordering Rationale

- **API integration first (Phase 1)** because all execution depends on verified E*TRADE connectivity. Can't build abstractions without confirming broker integration works.
- **Database schema before logic (Phase 2)** because execution and strategy layers need persistence in place. Schema changes are risky post-launch.
- **Basic cycle before optimizations (Phase 2 → Phase 3 → Phase 4)** because profit management (rolling, 50% close) requires the full cycle working first. Can't optimize what doesn't exist.
- **Edge cases after core functionality (Phase 4)** because corporate actions and early assignment are rare events. Core cycle must work reliably first.
- **User transition last (Phase 5)** because wheel must be proven functional before deprecating existing intraday strategies. Allows fallback if issues discovered.

**Dependency chain:** Phase 1 (API) → Phase 2 (DB + assignment detection) → Phase 3 (execution + strategy) → Phase 4 (optimizations) → Phase 5 (transition). Each phase depends on previous phase being complete and validated.

**Pitfall mitigation by phase:** Phases 1-2 address foundational pitfalls (API parsing, assignment detection, cost basis). Phase 3 addresses execution pitfalls (Greeks, cash calc, order types). Phase 4 addresses edge cases (rolling, corporate actions). Phase 5 addresses integration pitfalls (concurrent signals, user psychology).

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 4 (Corporate Actions):** E*TRADE corporate actions API or Alpaca integration for adjusted contract detection. Need to confirm notification mechanisms and response formats.
- **Phase 4 (Early Assignment):** Extrinsic value vs dividend formulas need validation. May need `/gsd:research-phase` to gather broker-specific early assignment criteria.

Phases with standard patterns (skip research-phase):
- **Phase 1 (E*TRADE API):** Well-documented official API, sandbox available for testing
- **Phase 2 (Database Schema):** Standard SQL schema patterns, existing codebase has similar structure
- **Phase 3 (Strike Selection):** Delta-based selection is industry standard, extensively documented
- **Phase 5 (UI/UX):** Streamlit/Telegram extensions follow existing patterns

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | E*TRADE API verified via official docs. blackscholes actively maintained (Dec 2024). Only 1 new dependency. |
| Features | HIGH | Wheel strategy mechanics well-documented across multiple authoritative sources (Schwab, Option Alpha, QuantWheel). MVP feature set validated by community consensus. |
| Architecture | HIGH | Existing mixin pattern proven with 5 mixins. Adding 3 more follows identical composition pattern. E*TRADE integration works for equities, extends naturally to options. |
| Pitfalls | MEDIUM | Assignment detection approach validated by web research but not tested with E*TRADE specifically. Pin risk buffer ($0.50) is rule of thumb, not official threshold. Rate limiting (2 req/sec) mentioned but not in official E*TRADE docs. |

**Overall confidence:** HIGH

The research is comprehensive with official documentation for E*TRADE API, actively maintained libraries (blackscholes), and well-established wheel strategy patterns validated across multiple authoritative sources. The architecture leverages proven patterns from the existing codebase (mixin composition, signal-driven execution, database-as-truth). Uncertainty exists primarily around E*TRADE-specific operational details (assignment notification timing, rate limit thresholds, Greeks accuracy) which can be validated during Phase 1 sandbox testing.

### Gaps to Address

Areas where research was inconclusive or needs validation during implementation:

- **Assignment notification timing:** Research confirms assignment occurs Friday 5:30 PM but E*TRADE API documentation doesn't specify when positions API reflects assignment. Assumption is Saturday/Monday update. Validate during Phase 2 sandbox testing by letting test option expire ITM.

- **E*TRADE Greeks accuracy:** Research shows E*TRADE provides Greeks but doesn't quantify accuracy vs theoretical Black-Scholes. Plan: Log E*TRADE delta vs calculated delta for 1 week during Phase 3, measure divergence percentage. If >10% divergence, investigate model differences.

- **Rate limiting specifics:** Community sources mention 2 req/sec but official E*TRADE docs don't specify options chain endpoint limits. Plan: Implement caching (60s TTL) and backoff logic in Phase 1. Monitor for HTTP 429 responses during testing.

- **Early assignment triggers:** Research identifies dividend capture as trigger but doesn't provide precise extrinsic value threshold. Plan: Use conservative buffer (close ITM calls 3 days before ex-div) in Phase 4. Refine threshold based on empirical observations.

- **Corporate actions notification:** Unclear if E*TRADE pushes notifications for adjusted contracts or if bot must poll. Plan: Phase 4 research task to investigate E*TRADE Alert Service API or Alpaca corporate actions webhook as fallback.

- **Paper mode assignment simulation:** Existing paper trading mocks equity orders but needs extension for assignment mechanics (qty changes, share delivery). Plan: Design paper mode assignment logic during Phase 2 to mirror real broker behavior (put qty -1 → 0, shares 0 → 100).

## Sources

### Primary (HIGH confidence)
- [E*TRADE Developer Portal](https://developer.etrade.com/home) — API authentication, endpoints structure
- [E*TRADE Market API Documentation](https://apisb.etrade.com/docs/api/market/api-market-v1.html) — Options chains, Greeks, quotes, expiration dates
- [E*TRADE Order API Documentation](https://apisb.etrade.com/docs/api/order/api-order-v1.html) — Options order preview/placement, order types, status polling
- [E*TRADE Portfolio API Documentation](https://apisb.etrade.com/docs/api/account/api-portfolio-v1.html) — Position queries for assignment detection
- [blackscholes PyPI](https://pypi.org/project/blackscholes/) — Library specs, version history (Dec 2024 release)
- [blackscholes GitHub](https://github.com/CarloLepelaars/blackscholes) — Implementation details, API documentation
- [OSI Format Specification (Fidelity)](https://www.fidelity.com/webcontent/ap102701-quotes-content/16.10/shtml/osi.shtml) — Official Options Symbology Initiative format

### Secondary (MEDIUM confidence)
- [Three Things to Know About the Wheel Strategy | Charles Schwab](https://www.schwab.com/learn/story/three-things-to-know-about-wheel-strategy) — Wheel mechanics, delta targets
- [Wheel Strategy: Complete Options Income Guide | QuantWheel](https://quantwheel.com/learn/wheel-strategy/) — DTE selection, profit taking strategies
- [The Options Wheel Strategy | Option Alpha](https://optionalpha.com/blog/wheel-strategy) — Strike selection methodology, assignment handling
- [The Options Wheel Strategy (Python) | Alpaca Markets](https://alpaca.markets/learn/options-wheel-strategy) — Architecture patterns, code examples
- [GitHub: alpacahq/options-wheel](https://github.com/alpacahq/options-wheel) — Reference implementation, assignment detection
- [Strategic Early Exits: 50% Rule | Wheel Strategy Options](https://wheelstrategyoptions.com/blog/strategic-early-exits-maximizing-csp-profits-with-the-50-rule-in-wheel-options-trading/) — Profit taking best practices
- [Optimizing Wheel Strategy DTE and Delta | Wheel Strategy Options](https://wheelstrategyoptions.com/blog/optimizing-the-wheel-strategy-advanced-dte-delta-and-trade-exit-tactics/) — Parameter optimization
- [Delta Explained | QuantWheel](https://quantwheel.com/learn/options-delta-explained/) — Greeks interpretation
- [Everything About Options Assignment Risk | SteadyOptions](https://steadyoptions.com/articles/everything-you-need-to-know-about-options-assignment-risk-r738/) — Assignment timing, pin risk
- [Options Exercise and Assignment | Nasdaq](https://www.nasdaq.com/articles/automatic-exercise-after-hours-risk-and-other-options-expiration-issues-2010-11-18) — OCC auto-exercise rules
- [Pin Risk | ApexVol](https://apexvol.com/learn/pin-risk) — Pin risk mechanics, avoidance strategies
- [Understanding Special Exercises and Pin Risk | IBKR](https://www.interactivebrokers.com/campus/traders-insight/securities/options/understanding-special-exercises-and-pin-risk/) — Pin risk scenarios

### Tertiary (LOW confidence, needs validation)
- E*TRADE rate limiting (2 req/sec per module) — mentioned in community forums but not official docs, validate during testing
- Pin risk $0.50 buffer — rule of thumb from multiple sources, not official OCC guidance
- Greeks divergence magnitude (10% threshold) — inferred from QuantConnect forum discussion, needs empirical validation
- Early assignment extrinsic value threshold — qualitative guidance only, no precise formulas found

---
*Research completed: 2026-03-20*
*Ready for roadmap: yes*
