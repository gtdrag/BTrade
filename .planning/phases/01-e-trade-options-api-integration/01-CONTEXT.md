# Phase 1: E*TRADE Options API Integration - Context

**Gathered:** 2026-03-23
**Status:** Ready for planning

<domain>
## Phase Boundary

Connect to E*TRADE options endpoints and validate data quality. Bot can fetch IBIT options chains with Greeks, place options orders (sell-to-open, buy-to-close), query options positions, and reject stale quotes. No strategy logic, no Telegram integration, no database persistence — those are later phases.

</domain>

<decisions>
## Implementation Decisions

### Options Chain Data Shape
- Claude's discretion on internal data structure (flat list vs grouped by expiry)
- IBIT-only — hardcode symbol like existing `get_ibit_quote()`, not generic
- Filter to 30-45 DTE expirations only — don't fetch full chain
- Return all five Greeks for every contract: delta, gamma, theta, vega, IV
- Include bid/ask/last, strike, expiration, open interest for each contract

### Options Order Flow
- Always use preview → place two-step flow (mirror existing equity pattern)
- Limit orders only — no market orders for options (bid-ask spreads too wide)
- Support all three order actions: sell-to-open, buy-to-close, position query
- Separate methods: `preview_options_order()` and `place_options_order()` — don't extend existing equity methods
- New `_build_options_order_request()` separate from `_build_order_request()`

### Quote Freshness
- Reject stale quotes (>60 seconds) and alert user via Telegram
- Hardcoded 60-second threshold (not configurable)
- Freshness validation built into chain fetch method — callers get clean data or error
- Options quotes only — don't modify existing equity quote flow

### Claude's Discretion
- Internal data structure choice for chain results
- Options-specific error handling and retry patterns
- Response schema validation approach
- How to extract timestamp from E*TRADE quote response for freshness check

</decisions>

<specifics>
## Specific Ideas

- Separate methods for options vs equity (don't pollute existing equity order flow)
- Follow existing patterns: `get_ibit_quote()` style for options chain, `preview_order()`/`place_order()` style for options orders
- Existing `_request()` method handles auth, retry, rate limiting — reuse it for all options calls

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- `ETradeClient._request()`: Authenticated request with retry, rate limiting, token refresh — use for all options endpoints
- `ETradeClient.get_quote()` / `get_ibit_quote()`: Pattern for market data fetching
- `ETradeClient.preview_order()` / `place_order()`: Pattern for two-step order flow with PreviewIds
- `MockETradeClient`: Existing mock with equity simulation — extend for options
- `create_etrade_client()` factory: Returns real or mock client based on dry_run flag

### Established Patterns
- OAuth 1.0a auth with `requests_oauthlib` — already working
- `_build_order_request()` builds order payload from params — create options equivalent
- Mock client tracks positions dict and cash — extend with options positions
- JSON response parsing with nested `.get()` chains (QuoteResponse → QuoteData)
- `get_et_now()` from utils for timestamps — use for freshness comparison

### Integration Points
- New methods added to `ETradeClient` class in `src/etrade_client.py`
- New mock methods added to `MockETradeClient` in same file
- E*TRADE API base: `https://api.etrade.com` (production) / `https://apisb.etrade.com` (sandbox)
- Options chain endpoint: `/v1/market/optionchains` (E*TRADE API)
- Options order type: `orderType: "OPTN"` instead of `"EQ"`

</code_context>

<mock_requirements>
## Paper Trading Mock Requirements

- Realistic Greeks simulation — delta varies by moneyness (ATM ~0.50, decreasing OTM), theta decays
- Simulate order fills with calculated premium from Greeks
- Track mock options positions with premium received and contract details
- Simulate basic assignment — if put expires ITM, convert to share position
- Mock only for Phase 1 testing — no E*TRADE sandbox reliance
- Extend existing `MockETradeClient` class, don't create separate mock

</mock_requirements>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 01-e-trade-options-api-integration*
*Context gathered: 2026-03-23*
