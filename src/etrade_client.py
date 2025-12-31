"""
E*TRADE API Client for IBIT Dip Bot.
Handles OAuth authentication, quotes, orders, and account management.
"""

import json
import logging
import os
import time
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from requests_oauthlib import OAuth1Session

from .utils import get_et_now

logger = logging.getLogger(__name__)


# E*TRADE API endpoints
ETRADE_SANDBOX_BASE = "https://apisb.etrade.com"
ETRADE_PRODUCTION_BASE = "https://api.etrade.com"

OAUTH_REQUEST_TOKEN = "/oauth/request_token"
OAUTH_AUTHORIZE = "https://us.etrade.com/e/t/etws/authorize"
OAUTH_ACCESS_TOKEN = "/oauth/access_token"
OAUTH_RENEW_TOKEN = "/oauth/renew_access_token"
OAUTH_REVOKE_TOKEN = "/oauth/revoke_access_token"


class ETradeAuthError(Exception):
    """Authentication error with E*TRADE API."""

    pass


class ETradeAPIError(Exception):
    """API error from E*TRADE."""

    pass


class ETradeClient:
    """
    E*TRADE API client with OAuth 1.0a authentication.

    Handles:
    - OAuth authentication flow
    - Token refresh/renewal
    - Quote fetching
    - Order placement (preview and execute)
    - Account balance/position queries
    """

    def __init__(
        self,
        consumer_key: str,
        consumer_secret: str,
        sandbox: bool = False,
        token_file: Optional[Path] = None,
    ):
        """
        Initialize E*TRADE client.

        Args:
            consumer_key: E*TRADE API consumer key
            consumer_secret: E*TRADE API consumer secret
            sandbox: Use sandbox environment (for testing)
            token_file: Path to store OAuth tokens
        """
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.sandbox = sandbox
        self.base_url = ETRADE_SANDBOX_BASE if sandbox else ETRADE_PRODUCTION_BASE
        self.token_file = token_file or Path(__file__).parent.parent / ".etrade_tokens.json"

        self.access_token: Optional[str] = None
        self.access_token_secret: Optional[str] = None
        self.session: Optional[OAuth1Session] = None

        self._load_tokens()

    def _load_tokens(self):
        """Load saved tokens from file."""
        if self.token_file.exists():
            try:
                with open(self.token_file) as f:
                    tokens = json.load(f)
                    self.access_token = tokens.get("access_token")
                    self.access_token_secret = tokens.get("access_token_secret")
                    if self.access_token and self.access_token_secret:
                        self._create_session()
                        logger.info("Loaded saved OAuth tokens")
            except Exception as e:
                logger.warning(f"Failed to load tokens: {e}")

    def _save_tokens(self):
        """Save tokens to file."""
        try:
            with open(self.token_file, "w") as f:
                json.dump(
                    {
                        "access_token": self.access_token,
                        "access_token_secret": self.access_token_secret,
                        "saved_at": get_et_now().isoformat(),
                    },
                    f,
                )
            # Secure the file
            os.chmod(self.token_file, 0o600)
            logger.info("Saved OAuth tokens")
        except Exception as e:
            logger.warning(f"Failed to save tokens: {e}")

    def _create_session(self):
        """Create OAuth session with current tokens."""
        self.session = OAuth1Session(
            self.consumer_key,
            client_secret=self.consumer_secret,
            resource_owner_key=self.access_token,
            resource_owner_secret=self.access_token_secret,
        )

    def is_authenticated(self) -> bool:
        """Check if we have valid tokens that actually work."""
        if self.access_token is None or self.access_token_secret is None:
            return False

        # Actually test the connection by trying to list accounts
        try:
            self._create_session()
            url = f"{self.base_url}/v1/accounts/list"
            response = self.session.get(url)
            if response.status_code == 200:
                return True
            elif response.status_code == 401:
                logger.warning("E*TRADE token expired or invalid")
                return False
            else:
                logger.warning(f"E*TRADE auth check failed: {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"E*TRADE auth check error: {e}")
            return False

    def ensure_authenticated(self) -> bool:
        """
        Ensure we have a valid, freshly-renewed token before starting order sequences.

        Call this before preview+place order sequences to avoid token expiry mid-order.
        This proactively renews the token if authenticated, preventing 401s during orders.

        Returns:
            True if authenticated (and token renewed), False otherwise
        """
        if not self.is_authenticated():
            return False

        # Proactively renew token to ensure it's fresh for the order sequence
        # E*TRADE tokens can expire at any time, so renewing before orders is safer
        try:
            self.renew_token()
            return True
        except Exception as e:
            logger.warning(f"Token renewal during ensure_authenticated failed: {e}")
            # Still return True if authenticated - renewal failure isn't fatal
            return self.is_authenticated()

    def authenticate(self, callback_url: str = "oob") -> bool:
        """
        Perform OAuth authentication flow.

        For command-line apps, use callback_url="oob" (out-of-band).
        User will need to visit the authorization URL and enter the verifier code.

        Returns:
            True if authentication successful
        """
        try:
            # Step 1: Get request token
            oauth = OAuth1Session(
                self.consumer_key, client_secret=self.consumer_secret, callback_uri=callback_url
            )

            request_token_url = f"{self.base_url}{OAUTH_REQUEST_TOKEN}"
            response = oauth.fetch_request_token(request_token_url)

            resource_owner_key = response.get("oauth_token")
            resource_owner_secret = response.get("oauth_token_secret")

            if not resource_owner_key:
                raise ETradeAuthError("Failed to get request token")

            # Step 2: Direct user to authorization URL
            auth_url = f"{OAUTH_AUTHORIZE}?key={self.consumer_key}&token={resource_owner_key}"

            print("\n" + "=" * 60)
            print("E*TRADE Authorization Required")
            print("=" * 60)
            print(f"\n1. Open this URL in your browser:\n\n{auth_url}\n")
            print("2. Log in to E*TRADE and authorize the application")
            print("3. Copy the verification code shown")

            # Try to open browser automatically
            try:
                webbrowser.open(auth_url)
                print("\n(Browser should open automatically)")
            except Exception:
                pass

            # Step 3: Get verifier code from user
            verifier = input("\nEnter the verification code: ").strip()

            if not verifier:
                raise ETradeAuthError("No verification code provided")

            # Step 4: Exchange for access token
            oauth = OAuth1Session(
                self.consumer_key,
                client_secret=self.consumer_secret,
                resource_owner_key=resource_owner_key,
                resource_owner_secret=resource_owner_secret,
                verifier=verifier,
            )

            access_token_url = f"{self.base_url}{OAUTH_ACCESS_TOKEN}"
            access_tokens = oauth.fetch_access_token(access_token_url)

            self.access_token = access_tokens.get("oauth_token")
            self.access_token_secret = access_tokens.get("oauth_token_secret")

            if not self.access_token:
                raise ETradeAuthError("Failed to get access token")

            self._create_session()
            self._save_tokens()

            print("\nAuthentication successful!")
            logger.info("E*TRADE authentication completed successfully")
            return True

        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            raise ETradeAuthError(f"Authentication failed: {e}")

    def get_authorization_url(self, callback_url: str = "oob") -> tuple:
        """
        Get authorization URL for OAuth flow (step 1).

        Returns:
            Tuple of (auth_url, request_token_dict)
        """
        oauth = OAuth1Session(
            self.consumer_key, client_secret=self.consumer_secret, callback_uri=callback_url
        )

        request_token_url = f"{self.base_url}{OAUTH_REQUEST_TOKEN}"
        response = oauth.fetch_request_token(request_token_url)

        resource_owner_key = response.get("oauth_token")
        resource_owner_secret = response.get("oauth_token_secret")

        if not resource_owner_key:
            raise ETradeAuthError("Failed to get request token")

        auth_url = f"{OAUTH_AUTHORIZE}?key={self.consumer_key}&token={resource_owner_key}"

        return auth_url, {
            "oauth_token": resource_owner_key,
            "oauth_token_secret": resource_owner_secret,
        }

    def complete_authorization(self, verifier: str, request_token: dict) -> bool:
        """
        Complete OAuth flow with verifier code (step 2).

        Args:
            verifier: The 5-character code from E*TRADE
            request_token: Dict with oauth_token and oauth_token_secret from get_authorization_url

        Returns:
            True if successful
        """
        oauth = OAuth1Session(
            self.consumer_key,
            client_secret=self.consumer_secret,
            resource_owner_key=request_token["oauth_token"],
            resource_owner_secret=request_token["oauth_token_secret"],
            verifier=verifier,
        )

        access_token_url = f"{self.base_url}{OAUTH_ACCESS_TOKEN}"
        access_tokens = oauth.fetch_access_token(access_token_url)

        self.access_token = access_tokens.get("oauth_token")
        self.access_token_secret = access_tokens.get("oauth_token_secret")

        if not self.access_token:
            raise ETradeAuthError("Failed to get access token")

        self._create_session()
        self._save_tokens()

        logger.info("E*TRADE authentication completed successfully")
        return True

    def renew_token(self) -> bool:
        """
        Renew access token (must be done daily for production).
        Tokens expire at midnight ET if not renewed.

        Note: E*TRADE's renew endpoint returns new oauth_token and oauth_token_secret
        that must be parsed and stored for subsequent requests.
        """
        if not self.is_authenticated():
            raise ETradeAuthError("Not authenticated")

        try:
            url = f"{self.base_url}{OAUTH_RENEW_TOKEN}"
            response = self.session.get(url)

            if response.status_code == 200:
                # Parse new tokens from response (format: oauth_token=XXX&oauth_token_secret=YYY)
                from urllib.parse import parse_qs

                token_data = parse_qs(response.text)

                new_token = token_data.get("oauth_token", [None])[0]
                new_secret = token_data.get("oauth_token_secret", [None])[0]

                if new_token and new_secret:
                    self.access_token = new_token
                    self.access_token_secret = new_secret
                    self._create_session()
                    self._save_tokens()
                    logger.info("Access token renewed and saved successfully")
                else:
                    # Some E*TRADE responses don't include new tokens (token extended, not replaced)
                    logger.info("Access token renewed (token extended, no new credentials)")

                return True
            else:
                logger.warning(f"Token renewal failed: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            logger.error(f"Token renewal error: {e}")
            return False

    def revoke_token(self):
        """Revoke current access token."""
        if not self.is_authenticated():
            return

        try:
            url = f"{self.base_url}{OAUTH_REVOKE_TOKEN}"
            self.session.get(url)
            logger.info("Access token revoked")
        except Exception as e:
            logger.warning(f"Token revocation error: {e}")
        finally:
            self.access_token = None
            self.access_token_secret = None
            self.session = None
            if self.token_file.exists():
                self.token_file.unlink()

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
        retry_count: int = 3,
    ) -> Dict[str, Any]:
        """
        Make authenticated API request with retry logic.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path
            params: Query parameters
            json_data: JSON body for POST requests
            retry_count: Number of retries on failure

        Returns:
            JSON response as dict
        """
        if not self.is_authenticated():
            raise ETradeAuthError("Not authenticated. Call authenticate() first.")

        url = f"{self.base_url}{endpoint}"
        headers = {"Accept": "application/json"}

        if json_data:
            headers["Content-Type"] = "application/json"

        for attempt in range(retry_count):
            try:
                if method.upper() == "GET":
                    response = self.session.get(url, params=params, headers=headers)
                elif method.upper() == "POST":
                    response = self.session.post(
                        url, params=params, json=json_data, headers=headers
                    )
                elif method.upper() == "PUT":
                    response = self.session.put(url, params=params, json=json_data, headers=headers)
                elif method.upper() == "DELETE":
                    response = self.session.delete(url, params=params, headers=headers)
                else:
                    raise ValueError(f"Unsupported method: {method}")

                # Handle rate limiting
                if response.status_code == 429:
                    wait_time = int(response.headers.get("Retry-After", 60))
                    logger.warning(f"Rate limited. Waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue

                # Handle auth errors
                if response.status_code == 401:
                    logger.warning("Token expired, attempting renewal...")
                    if self.renew_token():
                        continue
                    raise ETradeAuthError("Token expired and renewal failed")

                # Success
                if response.status_code in (200, 201):
                    return response.json()

                # Other errors
                logger.error(f"API error {response.status_code}: {response.text}")
                raise ETradeAPIError(f"API error {response.status_code}: {response.text}")

            except (requests.RequestException, ConnectionError) as e:
                logger.warning(f"Request failed (attempt {attempt + 1}): {e}")
                if attempt < retry_count - 1:
                    time.sleep(2**attempt)  # Exponential backoff
                else:
                    raise ETradeAPIError(f"Request failed after {retry_count} attempts: {e}")

        raise ETradeAPIError("Max retries exceeded")

    # ==================== Account Methods ====================

    def list_accounts(self) -> List[Dict[str, Any]]:
        """Get list of accounts."""
        response = self._request("GET", "/v1/accounts/list")
        accounts = response.get("AccountListResponse", {}).get("Accounts", {}).get("Account", [])
        return accounts if isinstance(accounts, list) else [accounts]

    def get_account_balance(self, account_id_key: str) -> Dict[str, Any]:
        """
        Get account balance including cash available for trading.

        Args:
            account_id_key: The accountIdKey from list_accounts()
        """
        if not account_id_key:
            logger.error("get_account_balance: No account_id_key provided")
            raise ETradeAPIError("account_id_key is required")

        params = {"instType": "BROKERAGE", "realTimeNAV": "true"}
        response = self._request("GET", f"/v1/accounts/{account_id_key}/balance", params=params)
        balance = response.get("BalanceResponse", {})

        if not balance:
            logger.warning(
                f"get_account_balance: No BalanceResponse in response. Keys: {list(response.keys())}"
            )

        return balance

    def get_account_positions(self, account_id_key: str) -> List[Dict[str, Any]]:
        """
        Get current positions in account.

        Args:
            account_id_key: The accountIdKey from list_accounts()
        """
        logger.info(f"get_account_positions: Querying account {account_id_key}")
        response = self._request("GET", f"/v1/accounts/{account_id_key}/portfolio")
        logger.info(
            f"get_account_positions: Raw response keys: {response.keys() if response else 'None'}"
        )
        portfolio = response.get("PortfolioResponse", {}).get("AccountPortfolio", [])
        logger.info(
            f"get_account_positions: Portfolio has {len(portfolio) if portfolio else 0} accounts"
        )

        if not portfolio:
            logger.warning(
                f"get_account_positions: No portfolio data returned for account {account_id_key}"
            )
            return []

        positions = []
        for account in portfolio if isinstance(portfolio, list) else [portfolio]:
            account_positions = account.get("Position", [])
            if isinstance(account_positions, dict):
                account_positions = [account_positions]
            positions.extend(account_positions)

        logger.info(f"get_account_positions: Found {len(positions)} positions")
        for pos in positions:
            symbol = pos.get("Product", {}).get("symbol", pos.get("symbolDescription", "?"))
            qty = pos.get("quantity", 0)
            logger.info(f"get_account_positions: Position - {symbol}: {qty} shares")

        return positions

    def get_cash_available(self, account_id_key: str) -> float:
        """Get cash available for trading in IRA account."""
        balance = self.get_account_balance(account_id_key)

        # Validate we got a proper response
        if not balance:
            logger.error("get_cash_available: Empty balance response from E*TRADE API")
            raise ETradeAPIError("Empty balance response - check authentication")

        # For IRA accounts, look at cashAvailableForInvestment
        computed = balance.get("Computed", {})
        if not computed:
            logger.warning(
                f"get_cash_available: No 'Computed' field in balance response. Keys: {list(balance.keys())}"
            )

        cash = computed.get("cashAvailableForInvestment", 0)

        # Fallback to other cash fields
        if not cash:
            cash = computed.get("cashBuyingPower", 0)
        if not cash:
            cash = computed.get("settledCashForInvestment", 0)

        # Log warning if cash is exactly 0 (suspicious)
        if cash == 0:
            logger.warning(
                f"get_cash_available: Cash is $0.00 - this may indicate API issue. "
                f"Balance keys: {list(balance.keys())}, Computed keys: {list(computed.keys())}"
            )

        return float(cash)

    # ==================== Quote Methods ====================

    def get_quote(self, symbol: str) -> Dict[str, Any]:
        """
        Get real-time quote for a symbol.

        Args:
            symbol: Stock/ETF symbol (e.g., "IBIT")

        Returns:
            Quote data including lastTrade, open, high, low, etc.
        """
        response = self._request("GET", f"/v1/market/quote/{symbol}")
        quote_data = response.get("QuoteResponse", {}).get("QuoteData", [])

        if not quote_data:
            raise ETradeAPIError(f"No quote data for {symbol}")

        quote = quote_data[0] if isinstance(quote_data, list) else quote_data
        return quote

    def get_ibit_quote(self) -> Dict[str, float]:
        """
        Get IBIT quote with key price fields.

        Returns:
            Dict with: last_price, open_price, bid, ask, volume
        """
        quote = self.get_quote("IBIT")
        all_data = quote.get("All", {})
        _intraday = quote.get("Intraday", {})  # noqa: F841 - reserved for future use

        return {
            "last_price": float(all_data.get("lastTrade", 0)),
            "open_price": float(all_data.get("open", 0)),
            "bid": float(all_data.get("bid", 0)),
            "ask": float(all_data.get("ask", 0)),
            "high": float(all_data.get("high", 0)),
            "low": float(all_data.get("low", 0)),
            "volume": int(all_data.get("totalVolume", 0)),
            "change_pct": float(all_data.get("changeClose", 0)),
        }

    # ==================== Order Methods ====================

    def preview_order(
        self,
        account_id_key: str,
        symbol: str,
        action: str,  # "BUY" or "SELL"
        quantity: int,
        order_type: str = "MARKET",  # "MARKET" or "LIMIT"
        limit_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Preview an order before placing it.

        Returns preview with estimated cost/proceeds.
        """
        order_data = self._build_order_request(
            symbol, action, quantity, order_type, limit_price, preview=True
        )

        response = self._request(
            "POST", f"/v1/accounts/{account_id_key}/orders/preview", json_data=order_data
        )

        return response.get("PreviewOrderResponse", {})

    def place_order(
        self,
        account_id_key: str,
        symbol: str,
        action: str,  # "BUY" or "SELL"
        quantity: int,
        order_type: str = "MARKET",
        limit_price: Optional[float] = None,
        preview_ids: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """
        Place an order.

        Args:
            account_id_key: Account to trade in
            symbol: Stock/ETF symbol
            action: "BUY" or "SELL"
            quantity: Number of shares
            order_type: "MARKET" or "LIMIT"
            limit_price: Required for limit orders
            preview_ids: PreviewIds from preview_order (recommended)

        Returns:
            Order response with order ID and status
        """
        order_data = self._build_order_request(
            symbol, action, quantity, order_type, limit_price, preview=False
        )

        # Add preview IDs if provided (required for production)
        if preview_ids:
            order_data["PlaceOrderRequest"]["PreviewIds"] = preview_ids

        response = self._request(
            "POST", f"/v1/accounts/{account_id_key}/orders/place", json_data=order_data
        )

        return response.get("PlaceOrderResponse", {})

    def _build_order_request(
        self,
        symbol: str,
        action: str,
        quantity: int,
        order_type: str,
        limit_price: Optional[float],
        preview: bool,
    ) -> Dict[str, Any]:
        """Build order request payload."""
        order = {
            "allOrNone": "false",
            "priceType": order_type,
            "orderTerm": "GOOD_FOR_DAY",
            "marketSession": "REGULAR",
            "Instrument": [
                {
                    "Product": {"securityType": "EQ", "symbol": symbol},
                    "orderAction": action,
                    "quantityType": "QUANTITY",
                    "quantity": quantity,
                }
            ],
        }

        if order_type == "LIMIT" and limit_price:
            order["limitPrice"] = limit_price

        key = "PreviewOrderRequest" if preview else "PlaceOrderRequest"
        return {
            key: {
                "orderType": "EQ",
                "clientOrderId": f"IBIT_{get_et_now().strftime('%Y%m%d%H%M%S')}",
                "Order": [order],
            }
        }

    def get_order_status(self, account_id_key: str, order_id: str) -> Dict[str, Any]:
        """Get status of a specific order."""
        response = self._request("GET", f"/v1/accounts/{account_id_key}/orders/{order_id}")
        return response.get("OrdersResponse", {})

    def cancel_order(self, account_id_key: str, order_id: str) -> bool:
        """Cancel an open order."""
        try:
            self._request(
                "PUT",
                f"/v1/accounts/{account_id_key}/orders/cancel",
                json_data={"CancelOrderRequest": {"orderId": order_id}},
            )
            return True
        except ETradeAPIError:
            return False


class MockETradeClient:
    """
    Mock E*TRADE client for dry-run/paper trading mode.
    Simulates API responses without making real trades.
    """

    def __init__(self, initial_cash: float = 100000.0):
        """Initialize mock client with starting cash."""
        self.cash = initial_cash
        self.positions: Dict[str, Dict] = {}
        self.orders: List[Dict] = []
        self._mock_prices: Dict[str, float] = {"IBIT": 50.0}

    def is_authenticated(self) -> bool:
        return True

    def authenticate(self, callback_url: str = "oob") -> bool:
        logger.info("Mock authentication (dry-run mode)")
        return True

    def renew_token(self) -> bool:
        return True

    def set_mock_price(self, symbol: str, price: float):
        """Set mock price for testing."""
        self._mock_prices[symbol] = price

    def list_accounts(self) -> List[Dict[str, Any]]:
        return [
            {
                "accountId": "MOCK_IRA_001",
                "accountIdKey": "mock_key_001",
                "accountDesc": "Mock IRA Account",
                "accountType": "IRA",
            }
        ]

    def get_cash_available(self, account_id_key: str) -> float:
        return self.cash

    def get_account_positions(self, account_id_key: str) -> List[Dict[str, Any]]:
        positions = []
        for symbol, data in self.positions.items():
            positions.append(
                {
                    "symbolDescription": symbol,
                    "quantity": data["quantity"],
                    "costPerShare": data["cost_basis"],
                    "marketValue": data["quantity"] * self._mock_prices.get(symbol, 0),
                }
            )
        return positions

    def get_quote(self, symbol: str) -> Dict[str, Any]:
        price = self._mock_prices.get(symbol, 50.0)
        return {
            "All": {
                "lastTrade": price,
                "open": price * 0.995,  # Simulate slight gap
                "bid": price - 0.01,
                "ask": price + 0.01,
                "high": price * 1.01,
                "low": price * 0.99,
                "totalVolume": 1000000,
            }
        }

    def get_ibit_quote(self) -> Dict[str, float]:
        price = self._mock_prices.get("IBIT", 50.0)
        return {
            "last_price": price,
            "open_price": price * 1.005,  # Open slightly higher to allow dip
            "bid": price - 0.01,
            "ask": price + 0.01,
            "high": price * 1.01,
            "low": price * 0.99,
            "volume": 1000000,
            "change_pct": -0.5,
        }

    def preview_order(
        self,
        account_id_key: str,
        symbol: str,
        action: str,
        quantity: int,
        order_type: str = "MARKET",
        limit_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        price = limit_price or self._mock_prices.get(symbol, 50.0)
        total = price * quantity

        return {
            "PreviewIds": [{"previewId": "mock_preview_001"}],
            "Order": [
                {"estimatedTotalAmount": total if action == "BUY" else 0, "estimatedCommission": 0}
            ],
        }

    def place_order(
        self,
        account_id_key: str,
        symbol: str,
        action: str,
        quantity: int,
        order_type: str = "MARKET",
        limit_price: Optional[float] = None,
        preview_ids: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        price = limit_price or self._mock_prices.get(symbol, 50.0)
        total = price * quantity

        order_id = f"MOCK_{len(self.orders) + 1:06d}"

        if action == "BUY":
            if total > self.cash:
                raise ETradeAPIError("Insufficient funds")
            self.cash -= total
            if symbol in self.positions:
                old_qty = self.positions[symbol]["quantity"]
                old_cost = self.positions[symbol]["cost_basis"]
                new_qty = old_qty + quantity
                self.positions[symbol] = {
                    "quantity": new_qty,
                    "cost_basis": ((old_qty * old_cost) + total) / new_qty,
                }
            else:
                self.positions[symbol] = {"quantity": quantity, "cost_basis": price}
        else:  # SELL
            if symbol not in self.positions or self.positions[symbol]["quantity"] < quantity:
                raise ETradeAPIError("Insufficient shares")
            self.cash += total
            self.positions[symbol]["quantity"] -= quantity
            if self.positions[symbol]["quantity"] == 0:
                del self.positions[symbol]

        order = {
            "orderId": order_id,
            "symbol": symbol,
            "action": action,
            "quantity": quantity,
            "price": price,
            "status": "EXECUTED",
            "timestamp": get_et_now().isoformat(),
        }
        self.orders.append(order)

        logger.info(f"Mock order executed: {action} {quantity} {symbol} @ ${price:.2f}")

        return {
            "OrderIds": [{"orderId": order_id}],
            "Order": [{"orderId": order_id, "status": "EXECUTED"}],
        }


def create_etrade_client(
    consumer_key: Optional[str] = None,
    consumer_secret: Optional[str] = None,
    sandbox: bool = False,
    dry_run: bool = False,
) -> ETradeClient:
    """
    Factory function to create appropriate E*TRADE client.

    Args:
        consumer_key: API key (or from ETRADE_CONSUMER_KEY env var)
        consumer_secret: API secret (or from ETRADE_CONSUMER_SECRET env var)
        sandbox: Use sandbox environment
        dry_run: Use mock client for paper trading

    Returns:
        ETradeClient or MockETradeClient instance
    """
    if dry_run:
        logger.info("Creating mock E*TRADE client (dry-run mode)")
        return MockETradeClient()

    key = consumer_key or os.environ.get("ETRADE_CONSUMER_KEY")
    secret = consumer_secret or os.environ.get("ETRADE_CONSUMER_SECRET")

    if not key or not secret:
        raise ValueError(
            "E*TRADE credentials required. Set ETRADE_CONSUMER_KEY and "
            "ETRADE_CONSUMER_SECRET environment variables or pass directly."
        )

    return ETradeClient(key, secret, sandbox=sandbox)
