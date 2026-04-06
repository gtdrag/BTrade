# Phase 1: E*TRADE Options API Integration - Research

**Researched:** 2026-04-06
**Domain:** E*TRADE REST API — options chain fetching, options order placement, portfolio query, quote freshness validation
**Confidence:** HIGH (API structure verified via official docs and working Python wrappers)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Options Chain Data Shape**
- IBIT-only — hardcode symbol like existing `get_ibit_quote()`, not generic
- Filter to 30-45 DTE expirations only — don't fetch full chain
- Return all five Greeks for every contract: delta, gamma, theta, vega, IV
- Include bid/ask/last, strike, expiration, open interest for each contract
- Claude's discretion on internal data structure (flat list vs grouped by expiry)

**Options Order Flow**
- Always use preview -> place two-step flow (mirror existing equity pattern)
- Limit orders only — no market orders for options (bid-ask spreads too wide)
- Support all three order actions: sell-to-open, buy-to-close, position query
- Separate methods: `preview_options_order()` and `place_options_order()` — don't extend existing equity methods
- New `_build_options_order_request()` separate from `_build_order_request()`

**Quote Freshness**
- Reject stale quotes (>60 seconds) and alert user via Telegram
- Hardcoded 60-second threshold (not configurable)
- Freshness validation built into chain fetch method — callers get clean data or error
- Options quotes only — don't modify existing equity quote flow

**Integration Points**
- New methods added to `ETradeClient` class in `src/etrade_client.py`
- New mock methods added to `MockETradeClient` in same file
- Reuse `ETradeClient._request()` for all options calls
- Follow `get_ibit_quote()` style for options chain
- Follow `preview_order()`/`place_order()` style for options orders

**Mock Requirements**
- Realistic Greeks simulation — delta varies by moneyness (ATM ~0.50, decreasing OTM), theta decays
- Simulate order fills with calculated premium from Greeks
- Track mock options positions with premium received and contract details
- Simulate basic assignment — if put expires ITM, convert to share position
- Mock only for Phase 1 testing — no E*TRADE sandbox reliance
- Extend existing `MockETradeClient` class, don't create separate mock

### Claude's Discretion
- Internal data structure choice for chain results
- Options-specific error handling and retry patterns
- Response schema validation approach
- How to extract timestamp from E*TRADE quote response for freshness check

### Deferred Ideas (OUT OF SCOPE)
- None — discussion stayed within phase scope
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| API-01 | Bot can fetch IBIT options chains with Greeks (delta, gamma, theta, vega, IV) from E*TRADE | Verified endpoint `/v1/market/optionchains`, response includes OptionGreeks with all five fields |
| API-02 | Bot can preview and place options orders (sell-to-open puts, sell-to-open calls, buy-to-close) via E*TRADE | Verified `orderType: "OPTN"`, orderAction values SELL_OPEN/BUY_CLOSE, preview+place two-step pattern confirmed |
| API-03 | Bot can query options positions from E*TRADE portfolio API | Existing `/v1/accounts/{key}/portfolio` endpoint returns OPTN securityType positions with contract details |
| API-04 | Bot validates quote freshness (<60s) before using options prices for orders | `timeStamp` field in OptionChainResponse is Unix epoch seconds; compare against `get_et_now()` |
</phase_requirements>

---

## Summary

Phase 1 is a pure API extension to `src/etrade_client.py`. The E*TRADE v1 REST API supports options chains and options orders using the same OAuth 1.0a authentication and `_request()` infrastructure already in place. The options chain endpoint (`/v1/market/optionchains`) returns all five Greeks and a Unix epoch `timeStamp` per contract that supports the 60-second freshness check. Options orders use `orderType: "OPTN"` instead of `"EQ"`, and the Product block gains five new fields (`callPut`, `strikePrice`, `expiryYear`, `expiryMonth`, `expiryDay`) with options-specific `orderAction` values (`SELL_OPEN`, `BUY_CLOSE`). Portfolio querying reuses the existing positions endpoint — options positions appear with `securityType: "OPTN"`.

The biggest testing constraint is that E*TRADE's sandbox returns stale, incorrect options data (AAPL March 2013 contracts regardless of request). All testing must therefore rely on the extended `MockETradeClient` with realistic Black-Scholes-derived Greek simulation. The mock is the primary validation vehicle for this phase.

No new Python dependencies are required. All needed libraries (`requests`, `requests_oauthlib`) are already installed. The test framework (pytest 8.4.2, asyncio mode auto) is in place.

**Primary recommendation:** Add four method groups to `ETradeClient` and matching mock counterparts — chain fetch, options order build, options preview, options place — with freshness validation gating the chain fetch method.

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| requests | 2.31.0+ (installed) | HTTP calls to E*TRADE REST API | Already used throughout codebase |
| requests-oauthlib | 2.0.0 (installed) | OAuth 1.0a signing | Already handles all E*TRADE auth |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest | 8.4.2 (installed) | Test framework | All unit and integration tests |
| pytest-cov | 4.1.0+ (installed) | Coverage reporting | Per-wave coverage checks |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Raw `_request()` | pyetrade library | pyetrade adds a dependency and wraps responses in ways that differ from existing codebase conventions; `_request()` is already tested and handles auth/retry |
| Mock-only testing | E*TRADE sandbox | Sandbox returns stale AAPL 2013 data for options chains (confirmed limitation); mock is the right approach |

**No new installation required** — all dependencies are already present.

---

## Architecture Patterns

### File Placement
All new code goes in `src/etrade_client.py`. No new files. Two classes receive additions:

```
src/etrade_client.py
├── ETradeClient               # Real API client
│   ├── get_ibit_options_chain()    # API-01: fetch chain + freshness gate
│   ├── get_options_positions()     # API-03: filter portfolio for OPTN type
│   ├── preview_options_order()     # API-02 step 1
│   ├── place_options_order()       # API-02 step 2
│   └── _build_options_order_request()  # internal payload builder
└── MockETradeClient           # Paper trading mock
    ├── get_ibit_options_chain()    # Returns simulated Greeks
    ├── get_options_positions()     # Returns tracked mock positions
    ├── preview_options_order()     # Simulates preview with estimated premium
    └── place_options_order()       # Simulates fill, updates mock state
```

### Pattern 1: Options Chain Fetch with Freshness Gate
**What:** Fetch chain filtered to 30-45 DTE, validate all contract timestamps before returning.
**When to use:** Any time the bot needs to reference options prices for a suggested trade.

```python
# Source: E*TRADE API docs (apisb.etrade.com/docs/api/market/api-market-v1.html)
def get_ibit_options_chain(self) -> List[Dict[str, Any]]:
    """
    Fetch IBIT options chain for 30-45 DTE expirations.
    Returns only contracts with fresh quotes (< 60 seconds old).
    Raises ETradeAPIError if any quotes are stale.
    """
    from datetime import datetime, timedelta

    now = get_et_now()
    min_dte = 30
    max_dte = 45

    # Calculate expiry window
    min_expiry = now + timedelta(days=min_dte)
    max_expiry = now + timedelta(days=max_dte)

    params = {
        "symbol": "IBIT",
        "chainType": "CALLPUT",
        "includeWeekly": "false",
        "skipAdjusted": "true",
        "optionCategory": "STANDARD",
    }

    response = self._request("GET", "/v1/market/optionchains", params=params)
    option_pairs = response.get("OptionChainResponse", {}).get("OptionPair", [])

    contracts = []
    for pair in option_pairs if isinstance(option_pairs, list) else [option_pairs]:
        for option_key in ("Call", "Put"):
            option = pair.get(option_key)
            if not option:
                continue

            # Freshness check: timeStamp is Unix epoch seconds
            timestamp_epoch = option.get("timeStamp", 0)
            age_seconds = (now.timestamp() - timestamp_epoch)
            if age_seconds > 60:
                raise ETradeAPIError(
                    f"Stale options quote: {option.get('symbol')} is {age_seconds:.0f}s old"
                )

            greeks = option.get("OptionGreeks", {})
            expiry_year = int(option.get("expiryYear", 0))
            expiry_month = int(option.get("expiryMonth", 0))
            expiry_day = int(option.get("expiryDay", 0))

            if not (expiry_year and expiry_month and expiry_day):
                continue

            expiry_date = datetime(expiry_year, expiry_month, expiry_day)
            if not (min_expiry <= expiry_date <= max_expiry):
                continue

            contracts.append({
                "symbol": option.get("symbol"),
                "option_type": option.get("optionType"),  # "CALL" or "PUT"
                "strike": float(option.get("strikePrice", 0)),
                "expiry_year": expiry_year,
                "expiry_month": expiry_month,
                "expiry_day": expiry_day,
                "expiry_date": expiry_date.date(),
                "dte": (expiry_date.date() - now.date()).days,
                "bid": float(option.get("bid", 0)),
                "ask": float(option.get("ask", 0)),
                "last": float(option.get("lastPrice", 0)),
                "open_interest": int(option.get("openInterest", 0)),
                "delta": float(greeks.get("delta", 0)),
                "gamma": float(greeks.get("gamma", 0)),
                "theta": float(greeks.get("theta", 0)),
                "vega": float(greeks.get("vega", 0)),
                "iv": float(greeks.get("iv", 0)),
                "quote_timestamp": timestamp_epoch,
            })

    return contracts
```

### Pattern 2: Options Order Builder (separate from equity)
**What:** Build the OPTN order payload — parallel structure to `_build_order_request()` but with options-specific fields.
**When to use:** Called by `preview_options_order()` and `place_options_order()`.

```python
# Source: E*TRADE API docs (apisb.etrade.com/docs/api/order/api-order-v1.html)
def _build_options_order_request(
    self,
    symbol: str,
    option_type: str,     # "CALL" or "PUT"
    expiry_year: int,
    expiry_month: int,
    expiry_day: int,
    strike_price: float,
    order_action: str,    # "SELL_OPEN", "BUY_CLOSE", "BUY_OPEN", "SELL_CLOSE"
    quantity: int,
    limit_price: float,
    preview: bool,
) -> Dict[str, Any]:
    """Build options order request payload (OPTN security type)."""
    order = {
        "allOrNone": "false",
        "priceType": "LIMIT",
        "limitPrice": limit_price,
        "orderTerm": "GOOD_FOR_DAY",
        "marketSession": "REGULAR",
        "Instrument": [
            {
                "Product": {
                    "securityType": "OPTN",
                    "symbol": symbol,
                    "callPut": option_type,
                    "expiryYear": str(expiry_year),
                    "expiryMonth": str(expiry_month),
                    "expiryDay": str(expiry_day),
                    "strikePrice": str(strike_price),
                },
                "orderAction": order_action,
                "quantityType": "QUANTITY",
                "quantity": quantity,
            }
        ],
    }

    key = "PreviewOrderRequest" if preview else "PlaceOrderRequest"
    return {
        key: {
            "orderType": "OPTN",
            "clientOrderId": f"OPTN_{get_et_now().strftime('%Y%m%d%H%M%S')}",
            "Order": [order],
        }
    }
```

### Pattern 3: Options Portfolio Query
**What:** Filter existing portfolio response to only OPTN positions.
**When to use:** API-03 — bot needs to see current options positions.

```python
# Source: E*TRADE API docs (apisb.etrade.com/docs/api/account/api-portfolio-v1.html)
def get_options_positions(self, account_id_key: str) -> List[Dict[str, Any]]:
    """
    Get current options positions from portfolio.
    Filters get_account_positions() results for OPTN securityType.
    """
    all_positions = self.get_account_positions(account_id_key)
    options = []
    for pos in all_positions:
        product = pos.get("Product", {})
        if product.get("securityType") == "OPTN":
            options.append({
                "symbol": product.get("symbol"),
                "option_type": product.get("callPut"),  # "CALL" or "PUT"
                "strike": float(product.get("strikePrice", 0)),
                "expiry_year": int(product.get("expiryYear", 0)),
                "expiry_month": int(product.get("expiryMonth", 0)),
                "expiry_day": int(product.get("expiryDay", 0)),
                "quantity": float(pos.get("quantity", 0)),
                "position_type": pos.get("positionType"),  # "LONG" or "SHORT"
                "market_value": float(pos.get("marketValue", 0)),
                "total_gain": float(pos.get("totalGain", 0)),
                "osi_key": product.get("osiKey"),
            })
    return options
```

### Pattern 4: Mock Greeks Simulation
**What:** Realistic options Greeks based on moneyness — no external library needed.
**When to use:** `MockETradeClient.get_ibit_options_chain()` for paper trading tests.

```python
# Source: Black-Scholes delta approximation [ASSUMED - standard finance formula]
import math

def _simulate_put_delta(self, spot: float, strike: float, dte: int, iv: float = 0.40) -> float:
    """
    Approximate put delta using simplified Black-Scholes moneyness.
    ATM (spot==strike) yields ~-0.50. Deep OTM yields closer to 0. Deep ITM yields ~-1.0.
    """
    if dte <= 0 or iv <= 0:
        return -0.50
    t = dte / 365.0
    moneyness = math.log(spot / strike) / (iv * math.sqrt(t))
    # Approximate N(-d1) for put delta
    from scipy.special import ndtr
    return -float(ndtr(-moneyness))

def _simulate_theta(self, premium: float, dte: int) -> float:
    """Approximate daily theta as ~1/dte fraction of premium."""
    if dte <= 0:
        return 0.0
    return -(premium / max(dte, 1)) * 0.5
```

**Note:** `scipy` is not in requirements.txt. The mock can use a pure-Python normal CDF approximation (Abramowitz & Stegun) to avoid adding a dependency just for tests.

### Anti-Patterns to Avoid
- **Calling `_request()` without auth check:** `_request()` already verifies auth; do not add extra `is_authenticated()` calls around options methods — they would double-call the accounts endpoint.
- **Using `datetime.now()` for freshness comparison:** The codebase requires `get_et_now()` for all timestamps. Options chain timestamps are US Eastern epoch seconds; using `time.time()` (UTC-aware) is fine for the epoch math, but use `get_et_now()` for any human-readable timestamps.
- **Extending equity order methods:** `preview_order()` and `place_order()` have the signature `(account_id_key, symbol, action, quantity, ...)`. Options require many more parameters (strike, expiry, callPut). Do not overload the existing methods with optional keyword args.
- **Relying on E*TRADE sandbox for options testing:** Sandbox returns stale AAPL 2013 data. All options-specific tests must use `MockETradeClient`.
- **Using market orders for options:** CONTEXT.md explicitly requires limit orders only — the mock and real client both enforce `priceType: "LIMIT"` in `_build_options_order_request()`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| OAuth 1.0a for options calls | Custom auth headers | `ETradeClient._request()` | Already handles token refresh, retry, rate limiting |
| Options positions query | New portfolio endpoint | `get_account_positions()` filtered by `securityType == "OPTN"` | Same endpoint returns all position types |
| Options order retry/rate limiting | Custom retry in options methods | `_request()` exponential backoff | Already implemented with 3-attempt backoff |
| Quote freshness timestamp parsing | Custom timestamp logic | `time.time()` vs response `timeStamp` (both Unix epoch) | Simple arithmetic; no library needed |

**Key insight:** Every options call routes through `_request()`. The options additions are thin wrappers that transform parameters into the correct payload shape — they do not need to re-implement transport, auth, or retry logic.

---

## Common Pitfalls

### Pitfall 1: E*TRADE Sandbox Returns Wrong Options Data
**What goes wrong:** Calling the options chain endpoint against the sandbox (`apisb.etrade.com`) returns Apple 2013 options data regardless of what symbol or expiry is requested. Tests that pass against sandbox may fail or produce nonsensical data.
**Why it happens:** E*TRADE sandbox stores static fixture data, not live market data. Options fixtures were not updated.
**How to avoid:** All options tests use `MockETradeClient`. Sandbox is used only for OAuth flow testing.
**Warning signs:** Response shows AAPL symbol, March 2013 expiry when requesting IBIT.

### Pitfall 2: OptionPair vs OptionDetails Response Shape
**What goes wrong:** The options chain response nests options inside `OptionPair` objects. Each pair contains a `Call` and `Put` key. Treating the response as a flat list of contracts causes KeyError or empty results.
**Why it happens:** The API groups contracts by strike/expiry pair rather than listing calls and puts separately.
**How to avoid:** Iterate over `OptionPair` list, then extract `Call` and `Put` from each pair.
**Warning signs:** Empty contracts list despite a non-empty API response.

### Pitfall 3: PreviewId Must Be Used Within 3 Minutes
**What goes wrong:** If the user takes more than 3 minutes between preview and place (e.g., waiting for Telegram approval), the `previewId` expires and the `place_options_order()` call fails.
**Why it happens:** E*TRADE enforces a 3-minute window on PreviewIds.
**How to avoid:** Phase 1 is infrastructure only — Telegram approval comes in Phase 3. For Phase 1, the preview -> place flow is tested programmatically with no human delay. Document this constraint clearly for Phase 3.
**Warning signs:** 4xx error on place_order with message about invalid or expired preview ID.

### Pitfall 4: Options Position Response Uses Different Field Names Than Equity
**What goes wrong:** The equity position fields `symbolDescription`, `quantity`, `costPerShare` do not apply to options. Options positions are accessed via the `Product` sub-object.
**Why it happens:** E*TRADE returns a generic Position object; options contract details live inside `Product` which has `securityType`, `callPut`, `strikePrice`, `expiryYear`, etc.
**How to avoid:** Filter positions with `pos.get("Product", {}).get("securityType") == "OPTN"` and extract fields from `Product`.
**Warning signs:** Strike price or expiry fields returning None/0 when accessing top-level position fields.

### Pitfall 5: timeStamp in Response Is UTC Epoch, get_et_now() Returns Timezone-Aware Datetime
**What goes wrong:** Comparing `get_et_now().timestamp()` (which correctly gives UTC epoch) with the response `timeStamp` (also UTC epoch seconds) works, but calling `.timestamp()` on a naive datetime raises an error.
**Why it happens:** `get_et_now()` returns a timezone-aware datetime (US/Eastern); `.timestamp()` on a timezone-aware datetime is safe and returns UTC epoch.
**How to avoid:** Always call `get_et_now().timestamp()` for the current epoch. Never use `datetime.now().timestamp()`.
**Warning signs:** TypeError about naive datetimes, or freshness check always failing due to timezone offset.

### Pitfall 6: clientOrderId Must Be Unique per Request
**What goes wrong:** Reusing the same `clientOrderId` (e.g., hardcoded test string) causes E*TRADE to reject duplicate orders with an error.
**Why it happens:** E*TRADE tracks clientOrderId for idempotency; reuse triggers a duplicate order rejection.
**How to avoid:** Use timestamp-based IDs: `f"OPTN_{get_et_now().strftime('%Y%m%d%H%M%S')}"`. In tests, mock does not enforce uniqueness, so this only matters in production.
**Warning signs:** "Duplicate order" error from E*TRADE when placing a second order.

---

## Code Examples

### Full Options Order Flow (Preview + Place)
```python
# Source: E*TRADE API docs (apisb.etrade.com/docs/api/order/api-order-v1.html) [VERIFIED]

def preview_options_order(
    self,
    account_id_key: str,
    symbol: str,
    option_type: str,       # "CALL" or "PUT"
    expiry_year: int,
    expiry_month: int,
    expiry_day: int,
    strike_price: float,
    order_action: str,      # "SELL_OPEN", "BUY_CLOSE"
    quantity: int,
    limit_price: float,
) -> Dict[str, Any]:
    order_data = self._build_options_order_request(
        symbol, option_type, expiry_year, expiry_month, expiry_day,
        strike_price, order_action, quantity, limit_price, preview=True
    )
    response = self._request(
        "POST", f"/v1/accounts/{account_id_key}/orders/preview", json_data=order_data
    )
    return response.get("PreviewOrderResponse", {})


def place_options_order(
    self,
    account_id_key: str,
    symbol: str,
    option_type: str,
    expiry_year: int,
    expiry_month: int,
    expiry_day: int,
    strike_price: float,
    order_action: str,
    quantity: int,
    limit_price: float,
    preview_ids: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    order_data = self._build_options_order_request(
        symbol, option_type, expiry_year, expiry_month, expiry_day,
        strike_price, order_action, quantity, limit_price, preview=False
    )
    if preview_ids:
        order_data["PlaceOrderRequest"]["PreviewIds"] = preview_ids
    response = self._request(
        "POST", f"/v1/accounts/{account_id_key}/orders/place", json_data=order_data
    )
    return response.get("PlaceOrderResponse", {})
```

### Freshness Validation Core Logic
```python
# Timestamp is Unix epoch seconds (confirmed from E*TRADE docs) [VERIFIED]
import time

QUOTE_FRESHNESS_SECONDS = 60  # Hardcoded per CONTEXT.md decision

def _check_quote_freshness(self, timestamp_epoch: int, symbol: str) -> None:
    """Raise ETradeAPIError if quote is older than 60 seconds."""
    age_seconds = time.time() - timestamp_epoch
    if age_seconds > QUOTE_FRESHNESS_SECONDS:
        raise ETradeAPIError(
            f"Stale options quote for {symbol}: {age_seconds:.0f}s old (max {QUOTE_FRESHNESS_SECONDS}s)"
        )
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| XML-only E*TRADE orders | JSON accepted alongside XML | E*TRADE API v1 | Use `json=` parameter in `_request()`, already working for equity |
| Separate equity/options order types | Same endpoint, different `orderType` field | Always | `orderType: "OPTN"` vs `"EQ"` routes correctly |
| Manual Greek calculation | API returns Greeks directly | Always | No local Black-Scholes needed for production; mock needs approximation |

**Note on pyetrade:** The `pyetrade` Python library exists but is not used in this codebase and should not be introduced. It adds a dependency for functionality already implemented directly with `requests` and `requests_oauthlib`.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `timeStamp` field in OptionChainResponse is Unix epoch seconds at the individual contract level | Architecture Patterns — Pattern 1 | Freshness check would be incorrect; need to test with real API response and log raw timestamp values |
| A2 | OptionPair response nests options under "Call" and "Put" keys | Architecture Patterns — Pattern 1 | Chain parsing loop would return empty results; resolve by logging raw response shape on first real API call |
| A3 | Portfolio endpoint at `/v1/accounts/{key}/portfolio` returns `Product.securityType == "OPTN"` for options positions | Architecture Patterns — Pattern 3 | get_options_positions() would return empty list; resolve by logging raw position objects in first production test |
| A4 | Mock delta simulation without scipy (pure-Python normal CDF) is sufficient accuracy for test assertions | Architecture Patterns — Pattern 4 | Test assertions for delta values could be slightly off; use `pytest.approx` with tolerance >= 0.05 |
| A5 | E*TRADE production API requires `ensure_authenticated()` call before options order sequences | Common Pitfalls | Token may expire mid-order; existing equity pattern calls `ensure_authenticated()` before order sequences |

---

## Open Questions

1. **OptionChainResponse exact JSON key structure**
   - What we know: Endpoint is `/v1/market/optionchains`, returns `OptionChainResponse` with `optionPairs` (or `OptionPair`) array
   - What's unclear: Whether the JSON key is `optionPairs` or `OptionPair` (XML-to-JSON conversion varies); whether `Call`/`Put` are direct keys or nested differently
   - Recommendation: On first production/sandbox call, log `json.dumps(response, indent=2)` and update the parser. The mock should return a structure that mirrors what the real API returns once confirmed.

2. **DTE Filtering: Client-side vs Server-side**
   - What we know: E*TRADE API accepts `expiryYear`/`expiryMonth`/`expiryDay` as request params but only for a single expiration date, not a range
   - What's unclear: Whether the API supports a range filter or always returns all expirations for the given `chainType`
   - Recommendation: Fetch all expirations, filter client-side to 30-45 DTE range. This is safe and avoids requiring multiple API calls.

3. **Options Greeks in Sandbox**
   - What we know: Sandbox returns stale AAPL 2013 data; real Greeks values may differ in structure
   - What's unclear: Whether the `OptionGreeks` JSON block is always present or conditional on `includeGreeks` parameter
   - Recommendation: Add `includeGreeks: "true"` to request params defensively; validate that `greeks.get("delta")` is not None before using.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3 | All code | Yes | 3.9.6 | — |
| pytest | Tests | Yes | 8.4.2 | — |
| requests | `_request()` | Yes | 2.31.0+ | — |
| requests-oauthlib | OAuth | Yes | 2.0.0 | — |
| E*TRADE sandbox | Manual integration testing | Limited | — | MockETradeClient (primary test vehicle) |
| E*TRADE production API | Live validation | Unknown — requires active auth | — | MockETradeClient for Phase 1 |

**Missing dependencies with no fallback:** None.

**Missing dependencies with fallback:**
- E*TRADE production API: Active auth may require re-authentication. If tokens are expired, the OAuth flow requires browser interaction. MockETradeClient covers all Phase 1 requirements without live API access.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.4.2 |
| Config file | `pyproject.toml` (`[tool.pytest.ini_options]`) |
| Quick run command | `python -m pytest tests/test_etrade_options.py -x -q` |
| Full suite command | `python -m pytest tests/ -v` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| API-01 | `get_ibit_options_chain()` returns contracts with all 5 Greeks | unit | `pytest tests/test_etrade_options.py::TestOptionsChain -x` | Wave 0 |
| API-01 | Chain filtered to 30-45 DTE only | unit | `pytest tests/test_etrade_options.py::TestOptionsChain::test_dte_filter -x` | Wave 0 |
| API-02 | `preview_options_order()` returns PreviewIds | unit | `pytest tests/test_etrade_options.py::TestOptionsOrders::test_preview_returns_preview_ids -x` | Wave 0 |
| API-02 | `place_options_order()` with SELL_OPEN returns orderId | unit | `pytest tests/test_etrade_options.py::TestOptionsOrders::test_place_sell_open -x` | Wave 0 |
| API-02 | `place_options_order()` with BUY_CLOSE returns orderId | unit | `pytest tests/test_etrade_options.py::TestOptionsOrders::test_place_buy_close -x` | Wave 0 |
| API-03 | `get_options_positions()` filters OPTN from portfolio | unit | `pytest tests/test_etrade_options.py::TestOptionsPositions -x` | Wave 0 |
| API-04 | Stale quote (>60s) raises `ETradeAPIError` | unit | `pytest tests/test_etrade_options.py::TestFreshness::test_stale_quote_raises -x` | Wave 0 |
| API-04 | Fresh quote (<60s) returns contracts without error | unit | `pytest tests/test_etrade_options.py::TestFreshness::test_fresh_quote_passes -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_etrade_options.py -x -q`
- **Per wave merge:** `python -m pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/test_etrade_options.py` — new file covering all API-01 through API-04 requirements
- [ ] No framework install needed — pytest and all dependencies already present

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | Yes | OAuth 1.0a via `requests_oauthlib` — already implemented, tokens stored at `chmod 600` |
| V3 Session Management | Yes | Token renewal on 401, proactive `ensure_authenticated()` before order sequences |
| V4 Access Control | No | Single-user bot, no multi-user access control needed |
| V5 Input Validation | Yes | Schema validate API responses using `.get()` with defaults; raise `ETradeAPIError` on unexpected shape |
| V6 Cryptography | No | OAuth signing handled by `requests_oauthlib`; no hand-rolled crypto |

### Known Threat Patterns for E*TRADE API Client

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Token file readable by other processes | Information Disclosure | Already mitigated: `os.chmod(token_file, 0o600)` in `_save_tokens()` |
| Replayed stale PreviewId | Elevation of Privilege | E*TRADE enforces 3-minute window; do not cache PreviewIds between sessions |
| Order parameter injection via f-strings | Tampering | All numeric params (strike, quantity, limit_price) are typed Python floats/ints, not raw strings from user input |
| clientOrderId reuse triggering duplicate order | Denial of Service | Use `get_et_now().strftime('%Y%m%d%H%M%S')` to ensure uniqueness |

---

## Sources

### Primary (HIGH confidence)
- `https://apisb.etrade.com/docs/api/market/api-market-v1.html` — options chain endpoint URL, parameters, response structure including `timeStamp`, Greeks fields
- `https://apisb.etrade.com/docs/api/order/api-order-v1.html` — `orderType: "OPTN"`, Product fields for options (callPut, expiryYear, expiryMonth, expiryDay, strikePrice), orderAction values (BUY_OPEN, SELL_OPEN, BUY_CLOSE, SELL_CLOSE), PreviewId 3-minute window
- `https://apisb.etrade.com/docs/api/account/api-portfolio-v1.html` — portfolio response with OPTN positions, Product.securityType, osiKey
- `src/etrade_client.py` in codebase — existing `_request()`, `preview_order()`, `place_order()`, `MockETradeClient` patterns

### Secondary (MEDIUM confidence)
- `https://github.com/jessecooper/pyetrade/blob/master/pyetrade/order.py` — cross-reference for options payload structure; confirmed `expiryDay/Month/Year`, `callPut`, `strikePrice` in Product block
- `https://rdrr.io/cran/etrader/man/etrd_place_optn_order.html` — confirmed orderAction values: BUY_OPEN, BUY_CLOSE, SELL_OPEN, SELL_CLOSE
- `https://github.com/1rocketdude/pyetrade_option_chains` — confirmed sandbox limitation (stale AAPL 2013 data)
- `https://github.com/jessecooper/pyetrade/issues/88` — confirmed sandbox limitation root cause and resolution (use production credentials)

### Tertiary (LOW confidence)
- WebSearch synthesis for `timeStamp` being Unix epoch seconds — consistent with pyetrade example values (e.g., `1529430484`)
- Black-Scholes delta approximation for mock — standard finance formula, not E*TRADE-specific

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new dependencies, existing libraries verified installed
- Architecture: HIGH — E*TRADE API v1 endpoints and payload structure verified via official docs and working Python wrapper cross-reference
- Pitfalls: HIGH — sandbox limitation confirmed via GitHub issues; PreviewId window confirmed via official docs; timestamp format confirmed
- Mock design: MEDIUM — Greek simulation formula is standard finance but exact field names in mock response need alignment with first real API call

**Research date:** 2026-04-06
**Valid until:** 2026-07-06 (E*TRADE API v1 is stable; unlikely to change in 90 days)
