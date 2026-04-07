---
phase: 01-e-trade-options-api-integration
reviewed: 2026-04-07T21:59:47Z
depth: standard
files_reviewed: 2
files_reviewed_list:
  - src/etrade_client.py
  - tests/test_etrade_options.py
findings:
  critical: 0
  warning: 5
  info: 3
  total: 8
status: issues_found
---

# Phase 01: Code Review Report

**Reviewed:** 2026-04-07T21:59:47Z
**Depth:** standard
**Files Reviewed:** 2
**Status:** issues_found

## Summary

Reviewed the new E*TRADE options chain and options order integration added to `src/etrade_client.py`, plus the accompanying test suite in `tests/test_etrade_options.py`. The overall structure is solid: the options chain method, the order builder, preview/place methods, and the mock client all follow the existing patterns in the codebase.

Five warnings were found. The most impactful is the stale-quote check firing before DTE filtering, which will cause false `ETradeAPIError` exceptions in production when the E*TRADE API returns any stale quote for a contract outside the 30-45 DTE window (e.g., near-expiry or far-dated contracts). There is also a mock/real API contract mismatch on `osi_key`, a `BUY_CLOSE` quantity bug in the mock, a falsy-zero cash fallback issue, and a silent empty-response path in the options chain parser.

No critical security issues were found.

---

## Warnings

### WR-01: Stale-quote check fires before DTE filter — false errors on out-of-range contracts

**File:** `src/etrade_client.py:628-641`

**Issue:** The freshness check on line 637 runs inside the outer `for pair in option_pairs` loop, *before* the DTE filter on line 655. E*TRADE returns the full options chain — all expirations — and the code then filters to 30-45 DTE. Any contract outside that window (e.g., a weekly with 2 DTE, or a LEAPS with 180 DTE) that happens to have a stale quote will trigger `ETradeAPIError` even though that contract would have been discarded by the DTE filter. In production, it is common for far-dated and near-expiry contracts to have stale quotes during low-volume periods.

**Fix:** Move the freshness check inside the DTE filter block, after the expiry date is parsed and confirmed to be in range:

```python
# After DTE filter (line 655), before appending to contracts:
if not (min_naive <= expiry_date <= max_naive):
    continue

# Freshness check — only validate quotes for contracts we will actually use
timestamp_epoch = option.get("timeStamp", 0)
age_seconds = time.time() - timestamp_epoch
if age_seconds > QUOTE_FRESHNESS_SECONDS:
    raise ETradeAPIError(
        f"Stale options quote for {option.get('symbol', 'unknown')}: "
        f"{age_seconds:.0f}s old (max {QUOTE_FRESHNESS_SECONDS}s)"
    )
```

Also remove the freshness block from its current position at lines 635-641.

---

### WR-02: `is_authenticated()` makes a live HTTP call on every `_request()` invocation

**File:** `src/etrade_client.py:395`

**Issue:** `_request()` calls `self.is_authenticated()` before every API call (line 395). `is_authenticated()` makes a real HTTP GET to `/v1/accounts/list` (lines 138-147). For a preview+place options order sequence, this results in two extra round-trips to `accounts/list` (one per `_request` call) in addition to the actual order calls. More critically, `renew_token()` also calls `is_authenticated()` at line 321, and `ensure_authenticated()` calls `is_authenticated()` (line 162) followed by `renew_token()` (line 168) — so a single `ensure_authenticated()` call makes at least two live HTTP calls to `accounts/list` before any order is placed. Under rate limiting or slow network conditions this is a reliability hazard.

**Fix:** Cache the authentication state with a short TTL (e.g., 30 seconds), or change `_request()` to check for a non-None session rather than making a live network call:

```python
def _is_session_ready(self) -> bool:
    """Fast in-memory check — session exists with tokens."""
    return (
        self.access_token is not None
        and self.access_token_secret is not None
        and self.session is not None
    )
```

Use `_is_session_ready()` in `_request()` and reserve the live `is_authenticated()` for pre-order validation only.

---

### WR-03: Falsy-zero check on cash treats legitimate $0.00 as missing

**File:** `src/etrade_client.py:537-540`

**Issue:** Lines 537 and 539 use `if not cash:` to fall through to backup cash fields. This treats a legitimate zero balance in `cashAvailableForInvestment` as an absent/missing value and falls through to `cashBuyingPower`, then `settledCashForInvestment`. A fully-invested account with $0 available for immediate investment but $500 in settled cash would incorrectly report `$500` as available, potentially triggering an options order that the account cannot actually fund immediately.

**Fix:** Use an explicit `None`/missing check:

```python
cash = computed.get("cashAvailableForInvestment")
if cash is None:
    cash = computed.get("cashBuyingPower")
if cash is None:
    cash = computed.get("settledCashForInvestment", 0)
return float(cash)
```

---

### WR-04: `MockETradeClient.place_options_order` ignores quantity on `BUY_CLOSE`

**File:** `src/etrade_client.py:1288-1293`

**Issue:** When `order_action == "BUY_CLOSE"`, the mock checks that `pos_key` exists in `_options_positions` but unconditionally deletes the entire position on line 1293 regardless of `quantity`. If a user opens 5 contracts (`SELL_OPEN`, qty=5) and then closes 2 (`BUY_CLOSE`, qty=2), the mock removes the full 5-contract position. This makes the mock diverge from real brokerage behavior and can mask bugs in callers that partially close positions.

**Fix:**

```python
elif order_action == "BUY_CLOSE":
    cost = limit_price * quantity * 100
    if pos_key not in self._options_positions:
        raise ETradeAPIError("No position to close")
    existing_qty = self._options_positions[pos_key]["quantity"]
    if quantity > existing_qty:
        raise ETradeAPIError(f"Cannot close {quantity} contracts; only {existing_qty} open")
    self.cash -= cost
    remaining = existing_qty - quantity
    if remaining == 0:
        del self._options_positions[pos_key]
    else:
        self._options_positions[pos_key]["quantity"] = remaining
```

---

### WR-05: `MockETradeClient.get_options_positions` omits `osi_key` — API contract mismatch

**File:** `src/etrade_client.py:1316-1332`

**Issue:** `ETradeClient.get_options_positions` returns dicts that include `"osi_key"` (line 803). `MockETradeClient.get_options_positions` (line 1316-1332) does not include `"osi_key"` in its returned dicts. Any downstream code that reads `osi_key` from positions and is tested against the mock will not catch a `KeyError` or missing-key bug. The test at line 298-300 does not check for `osi_key`, so the mismatch goes undetected.

**Fix:** Add `osi_key` to the mock's position tracking and return it:

In `place_options_order`, add to the position dict stored in `_options_positions`:
```python
"osi_key": f"{symbol}{expiry_date_str}{option_type[0]}{int(strike_price * 1000):08d}",
```

In `get_options_positions`, include in the returned dict:
```python
"osi_key": pos.get("osi_key"),
```

---

## Info

### IN-01: `_force_stale_quotes` not declared in `MockETradeClient.__init__`

**File:** `src/etrade_client.py:1057`

**Issue:** The `_force_stale_quotes` attribute is accessed via `getattr(self, "_force_stale_quotes", False)` but is never initialized in `__init__`. Tests set it directly with `mock_client._force_stale_quotes = True`. This is functional but inconsistent with how other mock state (`_mock_prices`, `_options_positions`) is initialized.

**Fix:** Add to `__init__`:
```python
self._force_stale_quotes: bool = False
```

---

### IN-02: Silent empty response when `OptionChainResponse` is absent

**File:** `src/etrade_client.py:620-625`

**Issue:** If the API response is missing `OptionChainResponse` or `OptionPair`, the chain of `.get()` calls returns `[]`, the loop body never executes, and `get_ibit_options_chain()` returns an empty list with only an info-level log message. Callers have no way to distinguish "API returned zero contracts" from "API returned a malformed/unexpected response." This could lead to a no-trade day when the connection is actually broken.

**Fix:** Add a guard after parsing `option_pairs`:
```python
if not option_pairs:
    logger.warning(
        "get_ibit_options_chain: OptionPair list is empty — "
        "API may have returned a malformed response. Raw keys: %s",
        list(response.get("OptionChainResponse", {}).keys()),
    )
```

---

### IN-03: `_build_order_request` passes `order_type` as `priceType` without validation

**File:** `src/etrade_client.py:884`

**Issue:** The `order_type` parameter is placed directly into the E*TRADE payload as `"priceType": order_type`. No validation is done against the accepted values (`MARKET`, `LIMIT`). An unsupported value produces a payload that E*TRADE rejects with an opaque API error rather than a clear `ValueError` at the call site.

**Fix:**
```python
VALID_ORDER_TYPES = {"MARKET", "LIMIT"}
if order_type not in VALID_ORDER_TYPES:
    raise ValueError(f"Invalid order_type '{order_type}'. Must be one of: {VALID_ORDER_TYPES}")
```

---

_Reviewed: 2026-04-07T21:59:47Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
