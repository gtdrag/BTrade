# Technology Stack - Wheel Strategy Options

**Project:** BTrade - IBIT Wheel Strategy
**Researched:** 2026-03-20

## Stack Additions for Options Trading

This document covers ONLY the new stack requirements for adding wheel strategy options trading to the existing intraday trading bot. The existing stack (Python 3.8+, APScheduler, Streamlit, requests-oauthlib, E*TRADE API for equities) remains unchanged.

---

## Core Additions

### Options Greeks Calculation

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| **blackscholes** | 0.2.0+ | Greeks calculation (delta, gamma, theta, vega, rho), implied volatility, pricing | RECOMMENDED - Active maintenance (Dec 2024 release), Python 3.10+ support, up to 3rd order Greeks, MIT license, pure Python |
| ~~py_vollib~~ | ~~1.0.1~~ | ~~Greeks and IV calculation~~ | AVOID - Last updated 2017, discontinued, outdated |
| ~~mibian~~ | ~~0.1.3~~ | ~~Greeks via Black-Scholes/Merton~~ | AVOID - Last updated 2016, Python compatibility concerns |
| scipy | 1.11+ | Implied volatility optimization (Brent's method, Newton-Raphson) | ALREADY IN STACK - Use for IV calculation alongside blackscholes |

**Rationale:**
- `blackscholes` is the only actively maintained, modern library (2024 release) with Python 3.10+ support
- Provides all Greeks needed: delta (strike selection), gamma (risk), theta (time decay), vega (IV sensitivity)
- Supports Black-Scholes-Merton (for stocks with dividends) and Black-76 (for futures-style)
- Pure Python implementation, no C/SWIG dependencies
- `scipy` already in stack for optimization, use for IV calculations via `minimize_scalar` with Brent's method

**Installation:**
```bash
pip install blackscholes>=0.2.0
```

### E*TRADE Options API Integration

| Component | Endpoint/Feature | Status | Notes |
|-----------|------------------|--------|-------|
| **Options Chain** | `GET /v1/market/optionchains` | ✓ Documented | Returns strikes, Greeks, bid/ask, volume, OI |
| **Options Expiration** | `GET /v1/market/optionexpiredate` | ✓ Documented | List expiry dates for underlying |
| **Options Orders** | `POST /v1/accounts/{accountIdKey}/orders/preview` | ✓ Documented | Preview options orders (orderType: "OPTN") |
| **Options Orders** | `POST /v1/accounts/{accountIdKey}/orders/place` | ✓ Documented | Place options orders with OSI key |
| **Position Tracking** | `GET /v1/accounts/{accountIdKey}/portfolio` | ✓ Existing | Already in etrade_client.py, returns options positions |

**Key Findings:**
- E*TRADE returns **Greeks directly in options chain API** (delta, gamma, theta, vega, rho, IV) - no need to calculate from scratch
- Options orders use **OSI (Options Symbology Initiative) format**: `{symbol}{YYMMDD}{C|P}{strike*1000}` (e.g., `IBIT--260417C00050000`)
- Order structure requires: `orderType: "OPTN"`, `securityType: "OPTN"`, `callPut: "CALL"|"PUT"`, `expiryYear/Month/Day`, `strikePrice`, `orderAction: "BUY_OPEN"|"SELL_OPEN"|"BUY_CLOSE"|"SELL_CLOSE"`
- Preview/place flow identical to equities: preview returns `previewId` (3min expiry), place requires `previewId`
- Position API already returns options positions with product details, quantity, cost basis

**No changes needed to existing `etrade_client.py` OAuth/session management** - add methods only.

---

## Supporting Libraries

### Data Processing (Already in Stack)

| Library | Current Version | New Use for Options |
|---------|----------------|---------------------|
| **pandas** | 2.0+ | Parse options chain responses, filter by DTE/delta/strike |
| **numpy** | 1.24+ | Greeks calculations, option chain filtering |

**Rationale:** No new dependencies needed. Existing pandas/numpy handle options chain parsing (convert JSON to DataFrame, filter strikes by delta/DTE).

### Date/Time Handling (Already in Stack)

| Component | Use for Options |
|-----------|----------------|
| **datetime (stdlib)** | Calculate DTE (days to expiration): `(expiry_date - today).days` |
| **pytz or zoneinfo** | Market hours (9:30-16:00 ET), expiration (16:00 ET on expiry Friday) |

**Rationale:** Standard datetime arithmetic for DTE calculation. Existing `get_et_now()` utility already handles ET timezone.

---

## What NOT to Add

| Library/Tool | Why Avoid |
|--------------|-----------|
| **py_vollib** | Discontinued since 2017, use `blackscholes` instead |
| **mibian** | Discontinued since 2016, Python 3.10+ compatibility unknown |
| **py-vollib-vectorized** | Last update 2018, depends on outdated py_vollib |
| **yfinance** | Already in stack for fallback data, but E*TRADE options API superior (real-time Greeks, tighter spreads) |
| **QuantLib** | Overkill for basic wheel strategy, C++ dependency, complex installation |
| **optionlab** | Strategy evaluation library, not needed for simple wheel (we control logic) |
| **Custom OSI parser library** | OSI format is simple, regex or string slicing sufficient (see below) |

**Rationale:** Avoid unmaintained libraries, unnecessary dependencies, and over-engineering. E*TRADE API provides Greeks directly, so no need for heavy calculation engines.

---

## Integration Points with Existing Code

### 1. E*TRADE Client Extensions (`src/etrade_client.py`)

Add these methods to `ETradeClient` class:

```python
def get_option_expirations(self, symbol: str) -> List[str]:
    """GET /v1/market/optionexpiredate?symbol={symbol}"""

def get_option_chain(
    self,
    symbol: str,
    expiry_date: str,  # "YYYY-MM-DD"
    option_type: str = "PUT",  # "PUT", "CALL", or "CALLPUT"
    strike_near: Optional[float] = None,
    num_strikes: int = 10
) -> Dict[str, Any]:
    """GET /v1/market/optionchains - returns Greeks, bid/ask, volume, OI"""

def preview_option_order(
    self,
    account_id_key: str,
    symbol: str,
    call_put: str,  # "CALL" or "PUT"
    expiry_year: int,
    expiry_month: int,
    expiry_day: int,
    strike_price: float,
    action: str,  # "BUY_OPEN", "SELL_OPEN", "BUY_CLOSE", "SELL_CLOSE"
    quantity: int,
    order_type: str = "LIMIT",
    limit_price: Optional[float] = None
) -> Dict[str, Any]:
    """Preview options order"""

def place_option_order(
    self,
    account_id_key: str,
    symbol: str,
    call_put: str,
    expiry_year: int,
    expiry_month: int,
    expiry_day: int,
    strike_price: float,
    action: str,
    quantity: int,
    order_type: str = "LIMIT",
    limit_price: Optional[float] = None,
    preview_ids: Optional[List[Dict]] = None
) -> Dict[str, Any]:
    """Place options order"""
```

**Mock implementations** for `MockETradeClient` needed for paper trading.

### 2. OSI Symbol Handling

**Simple implementation** (no library needed):

```python
def build_osi_key(symbol: str, expiry_date: datetime.date, call_put: str, strike: float) -> str:
    """
    Build OSI key: IBIT--260417C00050000
    Format: {symbol:6}{YYMMDD}{C|P}{strike*1000:08d}
    """
    symbol_padded = f"{symbol:<6}"  # Left-align, pad to 6 chars
    date_str = expiry_date.strftime("%y%m%d")
    cp = "C" if call_put.upper() == "CALL" else "P"
    strike_int = int(strike * 1000)
    return f"{symbol_padded}{date_str}{cp}{strike_int:08d}"

def parse_osi_key(osi_key: str) -> Dict[str, Any]:
    """Parse OSI key to components"""
    return {
        "symbol": osi_key[:6].strip(),
        "expiry": datetime.strptime(osi_key[6:12], "%y%m%d").date(),
        "call_put": "CALL" if osi_key[12] == "C" else "PUT",
        "strike": int(osi_key[13:]) / 1000.0
    }
```

### 3. Greeks Calculation (Verification/Fallback)

E*TRADE provides Greeks, but `blackscholes` useful for:
- **Verification** of E*TRADE Greeks (sanity check)
- **"What-if" scenarios** (e.g., "What's theta if IV drops 5%?")
- **Strike selection logic** (e.g., "Find strike with delta ~0.30")

```python
from blackscholes import BlackScholes

def calculate_greeks(
    spot_price: float,
    strike: float,
    time_to_expiry: float,  # Years (DTE / 365)
    risk_free_rate: float,  # e.g., 0.05 for 5%
    volatility: float,  # IV as decimal (e.g., 0.25 for 25%)
    option_type: str  # "call" or "put"
) -> Dict[str, float]:
    """Calculate Greeks using Black-Scholes"""
    bs = BlackScholes(
        S=spot_price,
        K=strike,
        T=time_to_expiry,
        r=risk_free_rate,
        sigma=volatility
    )

    if option_type.lower() == "call":
        return {
            "price": bs.call_price,
            "delta": bs.call_delta,
            "gamma": bs.gamma,  # Same for calls/puts
            "theta": bs.call_theta,
            "vega": bs.vega,  # Same for calls/puts
            "rho": bs.call_rho
        }
    else:
        return {
            "price": bs.put_price,
            "delta": bs.put_delta,
            "gamma": bs.gamma,
            "theta": bs.put_theta,
            "vega": bs.vega,
            "rho": bs.put_rho
        }
```

### 4. Database Extensions

**New tables needed** (add to `src/database.py`):

```sql
-- Options positions (separate from equity trades)
CREATE TABLE IF NOT EXISTS options_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,  -- "IBIT"
    option_type TEXT NOT NULL,  -- "PUT" or "CALL"
    strike REAL NOT NULL,
    expiry_date TEXT NOT NULL,  -- ISO format "YYYY-MM-DD"
    quantity INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    entry_time TEXT NOT NULL,
    osi_key TEXT,  -- E*TRADE OSI symbol
    status TEXT DEFAULT 'OPEN',  -- "OPEN", "CLOSED", "ASSIGNED", "EXPIRED"
    exit_price REAL,
    exit_time TEXT,
    profit_loss REAL,
    strategy TEXT,  -- "CSP" (cash-secured put), "CC" (covered call)
    notes TEXT
);

-- Assignment tracking (for wheel strategy)
CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    option_position_id INTEGER NOT NULL,
    assignment_date TEXT NOT NULL,
    shares_acquired INTEGER NOT NULL,  -- 100 per contract
    cost_basis REAL NOT NULL,  -- Strike price paid
    next_action TEXT,  -- "SELL_CC" (sell covered call next)
    FOREIGN KEY (option_position_id) REFERENCES options_positions(id)
);

-- Wheel cycle tracking
CREATE TABLE IF NOT EXISTS wheel_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    put_option_id INTEGER,  -- FK to options_positions
    assignment_id INTEGER,  -- FK to assignments
    call_option_id INTEGER,  -- FK to options_positions
    completed_at TEXT,
    total_profit REAL,
    status TEXT DEFAULT 'IN_PROGRESS',  -- "IN_PROGRESS", "COMPLETED", "ABANDONED"
    FOREIGN KEY (put_option_id) REFERENCES options_positions(id),
    FOREIGN KEY (assignment_id) REFERENCES assignments(id),
    FOREIGN KEY (call_option_id) REFERENCES options_positions(id)
);
```

---

## Alternative Considerations

### If E*TRADE Greeks Prove Unreliable

**Fallback hierarchy:**
1. **E*TRADE API Greeks** (primary, real-time)
2. **blackscholes library** (verification, calculated from IV)
3. **scipy optimization** (IV calculation from market price if E*TRADE IV wrong)

**Implementation:**
```python
# Verify E*TRADE Greeks against calculated Greeks
etrade_greeks = option_chain_data["OptionGreeks"]
calculated_greeks = calculate_greeks(...)

# Flag if delta differs >10%
if abs(etrade_greeks["delta"] - calculated_greeks["delta"]) > 0.1:
    logger.warning(f"E*TRADE delta discrepancy: {etrade_greeks['delta']} vs {calculated_greeks['delta']}")
    # Use calculated Greeks or alert user
```

### If OSI Symbol Parsing Becomes Complex

E*TRADE API returns OSI keys in option chain responses. If parsing proves buggy, validate against E*TRADE's product structure:

```python
# E*TRADE returns both OSI key AND product structure
{
    "osiKey": "IBIT--260417C00050000",
    "Product": {
        "symbol": "IBIT",
        "securityType": "OPTN",
        "callPut": "CALL",
        "expiryYear": 2026,
        "expiryMonth": 4,
        "expiryDay": 17,
        "strikePrice": 50.0
    }
}

# Prefer Product structure for parsing, use osiKey only for order placement
```

---

## Dependencies Summary

**Additions to `requirements.txt`:**
```txt
# Options Greeks calculation
blackscholes>=0.2.0
```

**No changes needed:**
- `scipy>=1.11.0` (already in stack for optimization)
- `pandas>=2.0.0` (already in stack for data processing)
- `numpy>=1.24.0` (already in stack for numerical ops)
- `requests>=2.31.0` (already in stack for E*TRADE API)
- `requests-oauthlib>=1.3.0` (already in stack for E*TRADE OAuth)

**Total new dependencies: 1** (`blackscholes`)

---

## Platform Requirements

**No changes to existing platform requirements:**
- Python 3.10+ (required for `blackscholes` 0.2.0)
- Existing E*TRADE API access (options trading must be enabled on account)
- No additional memory/disk requirements (options data minimal)

**E*TRADE Account Requirements:**
- Options trading approval (Level 2+ for cash-secured puts, covered calls)
- Sufficient cash for cash-secured puts ($IBIT_price * 100 per contract)

---

## Build & Start Commands

**No changes needed** - existing commands work:

```bash
# Install new dependency
pip install -r requirements.txt

# Run bot (paper mode tests options logic with MockETradeClient)
python run_bot.py

# Run bot (live mode requires E*TRADE options approval)
python run_bot.py --live
```

---

## Security Notes

**No new security concerns:**
- E*TRADE OAuth unchanged (same tokens for equities and options)
- `blackscholes` is pure Python, no binary dependencies
- Options orders use same preview/place flow (user approval via Telegram)

---

## Confidence Assessment

| Area | Confidence | Source | Notes |
|------|------------|--------|-------|
| E*TRADE Options API | HIGH | Official docs, WebFetch of api docs | Endpoints documented, Greeks included in responses |
| `blackscholes` library | HIGH | PyPI, GitHub (Dec 2024 release) | Actively maintained, Python 3.10+ support, MIT license |
| OSI symbol format | HIGH | E*TRADE docs, Fidelity OSI spec | Well-documented standard, simple parsing |
| Greeks calculation | MEDIUM | Multiple sources, community implementations | E*TRADE provides Greeks, `blackscholes` for verification |
| Assignment detection | MEDIUM | E*TRADE Portfolio API | Position API returns options, detect assignment by qty change |
| Deprecated libraries | HIGH | PyPI release dates, Snyk/Libraries.io | py_vollib (2017), mibian (2016) clearly outdated |

---

## Open Questions / Research Gaps

1. **E*TRADE Greeks accuracy** - How reliable are E*TRADE's Greeks vs calculated? (Test in paper mode first)
2. **Assignment notification timing** - Does E*TRADE API push assignment events, or poll Portfolio API? (Check streaming API docs)
3. **Early assignment risk** - Does E*TRADE flag American options at risk of early assignment? (Likely need manual logic)
4. **Dividend handling** - IBIT pays dividends quarterly - does E*TRADE adjust Greeks for ex-div dates? (Use Black-Scholes-Merton if not)
5. **IV surface** - Does E*TRADE provide IV skew across strikes, or single IV per chain? (Check options chain response structure)

**Resolution:** Test in sandbox/paper mode, add phase-specific research if needed.

---

## Sources

### E*TRADE API Documentation
- [E*TRADE Developer Portal](https://developer.etrade.com/home)
- [Market API Documentation](https://apisb.etrade.com/docs/api/market/api-market-v1.html)
- [Order API Documentation](https://apisb.etrade.com/docs/api/order/api-order-v1.html)
- [Portfolio API Documentation](https://apisb.etrade.com/docs/api/account/api-portfolio-v1.html)

### Python Libraries
- [blackscholes PyPI](https://pypi.org/project/blackscholes/)
- [blackscholes GitHub](https://github.com/CarloLepelaars/blackscholes)
- [py_vollib PyPI](https://pypi.org/project/py_vollib/) (avoided - outdated)
- [mibian PyPI](https://pypi.org/project/mibian/) (avoided - outdated)

### Options Concepts
- [Options Greeks](https://www.britannica.com/money/option-greeks-delta-theta-gamma-vega)
- [OSI Format Specification](https://www.fidelity.com/webcontent/ap102701-quotes-content/16.10/shtml/osi.shtml)
- [Wheel Strategy Overview](https://alpaca.markets/learn/options-wheel-strategy)
- [Implied Volatility Calculation with SciPy](https://medium.com/@polanitzer/implied-volatility-in-python-compute-the-volatilities-implied-by-option-prices-observed-in-the-e2085c184270)

### Community Resources
- [Computing Option Greeks with Python](https://www.codearmo.com/python-tutorial/options-trading-greeks-black-scholes)
- [Black-Scholes Model Python Guide](https://www.codearmo.com/python-tutorial/options-trading-black-scholes-model)
- [OptionLab - Strategy Evaluation Library](https://github.com/rgaveiga/optionlab)

---

*Stack research completed: 2026-03-20*
*Research confidence: HIGH (E*TRADE API documented, blackscholes actively maintained)*
*Critical finding: E*TRADE returns Greeks directly - minimal stack additions needed*
