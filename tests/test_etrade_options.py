"""
Tests for IBIT options chain fetching with quote freshness validation.

Tests verify:
- get_ibit_options_chain() returns contracts with all 5 Greeks
- Contracts are filtered to 30-45 DTE only
- Stale quotes (>60s) raise ETradeAPIError
- Fresh quotes pass without error
- Mock Greeks are realistic (ATM put delta near -0.50)
"""

import sys
from pathlib import Path

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.etrade_client import ETradeAPIError, MockETradeClient


@pytest.fixture
def mock_client():
    """Create mock E*TRADE client with IBIT price set."""
    client = MockETradeClient(initial_cash=100000)
    client.set_mock_price("IBIT", 50.0)
    return client


class TestOptionsChain:
    """Test options chain data shape and filtering."""

    def test_returns_list_of_contracts(self, mock_client):
        """get_ibit_options_chain() returns a non-empty list."""
        result = mock_client.get_ibit_options_chain()
        assert isinstance(result, list)
        assert len(result) > 0

    def test_contracts_have_all_five_greeks(self, mock_client):
        """Every contract dict has all five Greek keys with float values."""
        result = mock_client.get_ibit_options_chain()
        greek_keys = {"delta", "gamma", "theta", "vega", "iv"}
        for contract in result:
            for key in greek_keys:
                assert key in contract, f"Missing Greek key '{key}' in contract {contract.get('symbol')}"
                assert isinstance(contract[key], float), (
                    f"Greek '{key}' should be float, got {type(contract[key])}"
                )

    def test_contracts_have_market_data(self, mock_client):
        """Every contract dict has bid, ask, last, strike, and open_interest keys."""
        result = mock_client.get_ibit_options_chain()
        market_keys = {"bid", "ask", "last", "strike", "open_interest"}
        for contract in result:
            for key in market_keys:
                assert key in contract, f"Missing market key '{key}' in contract {contract.get('symbol')}"

    def test_contracts_have_expiry_info(self, mock_client):
        """Every contract dict has expiry_date, dte, option_type, and symbol keys."""
        result = mock_client.get_ibit_options_chain()
        expiry_keys = {"expiry_date", "dte", "option_type", "symbol"}
        for contract in result:
            for key in expiry_keys:
                assert key in contract, f"Missing expiry key '{key}' in contract {contract.get('symbol')}"

    def test_dte_filter_30_to_45(self, mock_client):
        """All returned contracts have 30 <= dte <= 45."""
        result = mock_client.get_ibit_options_chain()
        for contract in result:
            dte = contract["dte"]
            assert 30 <= dte <= 45, (
                f"Contract {contract.get('symbol')} has dte={dte} outside 30-45 range"
            )

    def test_includes_puts_and_calls(self, mock_client):
        """Returned contracts include both PUT and CALL option types."""
        result = mock_client.get_ibit_options_chain()
        option_types = {contract["option_type"] for contract in result}
        assert "PUT" in option_types, "No PUT contracts in chain"
        assert "CALL" in option_types, "No CALL contracts in chain"


class TestFreshness:
    """Test quote freshness validation."""

    def test_stale_quote_raises(self, mock_client):
        """When mock returns stale quotes, get_ibit_options_chain() raises ETradeAPIError."""
        mock_client._force_stale_quotes = True
        with pytest.raises(ETradeAPIError, match="Stale"):
            mock_client.get_ibit_options_chain()

    def test_fresh_quote_passes(self, mock_client):
        """When quotes are fresh, get_ibit_options_chain() returns contracts without error."""
        result = mock_client.get_ibit_options_chain()
        assert isinstance(result, list)
        assert len(result) > 0


class TestMockGreeks:
    """Test that mock Greeks are realistic."""

    def test_atm_put_delta_near_negative_half(self, mock_client):
        """ATM put (strike == spot) should have delta near -0.50."""
        result = mock_client.get_ibit_options_chain()
        # Find a PUT contract near ATM (strike within $1 of spot price $50)
        atm_puts = [
            c for c in result
            if c["option_type"] == "PUT" and abs(c["strike"] - 50.0) < 1.0
        ]
        assert len(atm_puts) > 0, "No ATM PUT contracts found near strike $50"
        for contract in atm_puts:
            assert contract["delta"] == pytest.approx(-0.50, abs=0.10), (
                f"ATM put delta {contract['delta']} is not near -0.50 (abs tolerance 0.10)"
            )

    def test_otm_put_delta_closer_to_zero(self, mock_client):
        """OTM put (strike < spot - $2) should have |delta| < 0.40."""
        result = mock_client.get_ibit_options_chain()
        # Find a PUT contract that is OTM (strike below $48)
        otm_puts = [
            c for c in result
            if c["option_type"] == "PUT" and c["strike"] < 48.0
        ]
        assert len(otm_puts) > 0, "No OTM PUT contracts found with strike < $48"
        for contract in otm_puts:
            assert abs(contract["delta"]) < 0.40, (
                f"OTM put delta {contract['delta']} should have |delta| < 0.40"
            )


class TestOptionsOrders:
    """Test options order builder, preview, and place methods."""

    def test_build_options_order_has_optn_type(self, mock_client):
        """_build_options_order_request() returns payload with orderType == 'OPTN'."""
        result = mock_client._build_options_order_request(
            symbol="IBIT",
            option_type="PUT",
            expiry_year=2026,
            expiry_month=5,
            expiry_day=15,
            strike_price=48.0,
            order_action="SELL_OPEN",
            quantity=1,
            limit_price=2.50,
            preview=True,
        )
        # The outer wrapper is PreviewOrderRequest or PlaceOrderRequest
        outer_key = "PreviewOrderRequest"
        assert result[outer_key]["orderType"] == "OPTN"

    def test_build_options_order_has_limit_price(self, mock_client):
        """Payload contains priceType == 'LIMIT' and limitPrice == provided value."""
        result = mock_client._build_options_order_request(
            symbol="IBIT",
            option_type="PUT",
            expiry_year=2026,
            expiry_month=5,
            expiry_day=15,
            strike_price=48.0,
            order_action="SELL_OPEN",
            quantity=1,
            limit_price=2.50,
            preview=True,
        )
        order = result["PreviewOrderRequest"]["Order"][0]
        assert order["priceType"] == "LIMIT"
        assert order["limitPrice"] == 2.50

    def test_build_options_order_has_product_fields(self, mock_client):
        """Product block contains securityType, callPut, strikePrice, expiry fields."""
        result = mock_client._build_options_order_request(
            symbol="IBIT",
            option_type="PUT",
            expiry_year=2026,
            expiry_month=5,
            expiry_day=15,
            strike_price=48.0,
            order_action="SELL_OPEN",
            quantity=1,
            limit_price=2.50,
            preview=True,
        )
        product = result["PreviewOrderRequest"]["Order"][0]["Instrument"][0]["Product"]
        assert product["securityType"] == "OPTN"
        assert product["callPut"] == "PUT"
        assert product["strikePrice"] == "48.0"
        assert product["expiryYear"] == "2026"
        assert product["expiryMonth"] == "5"
        assert product["expiryDay"] == "15"

    def test_build_options_order_preview_key(self, mock_client):
        """When preview=True, outer key is 'PreviewOrderRequest'."""
        result = mock_client._build_options_order_request(
            symbol="IBIT",
            option_type="PUT",
            expiry_year=2026,
            expiry_month=5,
            expiry_day=15,
            strike_price=48.0,
            order_action="SELL_OPEN",
            quantity=1,
            limit_price=2.50,
            preview=True,
        )
        assert "PreviewOrderRequest" in result
        assert "PlaceOrderRequest" not in result

    def test_build_options_order_place_key(self, mock_client):
        """When preview=False, outer key is 'PlaceOrderRequest'."""
        result = mock_client._build_options_order_request(
            symbol="IBIT",
            option_type="PUT",
            expiry_year=2026,
            expiry_month=5,
            expiry_day=15,
            strike_price=48.0,
            order_action="SELL_OPEN",
            quantity=1,
            limit_price=2.50,
            preview=False,
        )
        assert "PlaceOrderRequest" in result
        assert "PreviewOrderRequest" not in result

    def test_preview_returns_preview_ids(self, mock_client):
        """preview_options_order() returns dict containing 'PreviewIds' key."""
        result = mock_client.preview_options_order(
            "test_account", "IBIT", "PUT", 2026, 5, 15, 48.0, "SELL_OPEN", 1, 2.50
        )
        assert "PreviewIds" in result
        assert len(result["PreviewIds"]) > 0

    def test_place_sell_open_returns_order_id(self, mock_client):
        """place_options_order() with SELL_OPEN returns dict with 'OrderIds'."""
        result = mock_client.place_options_order(
            "test_account", "IBIT", "PUT", 2026, 5, 15, 48.0, "SELL_OPEN", 1, 2.50
        )
        assert "OrderIds" in result
        assert len(result["OrderIds"]) > 0

    def test_place_buy_close_returns_order_id(self, mock_client):
        """place_options_order() with BUY_CLOSE returns dict with 'OrderIds'."""
        # First open a position
        mock_client.place_options_order(
            "test_account", "IBIT", "PUT", 2026, 5, 15, 48.0, "SELL_OPEN", 1, 2.50
        )
        # Then close it
        result = mock_client.place_options_order(
            "test_account", "IBIT", "PUT", 2026, 5, 15, 48.0, "BUY_CLOSE", 1, 1.00
        )
        assert "OrderIds" in result
        assert len(result["OrderIds"]) > 0

    def test_sell_open_tracks_position(self, mock_client):
        """After SELL_OPEN, get_options_positions() returns list with the sold option."""
        mock_client.place_options_order(
            "test_account", "IBIT", "PUT", 2026, 5, 15, 48.0, "SELL_OPEN", 1, 2.50
        )
        positions = mock_client.get_options_positions("test_account")
        assert len(positions) == 1
        assert positions[0]["option_type"] == "PUT"
        assert positions[0]["strike"] == 48.0

    def test_buy_close_removes_position(self, mock_client):
        """After SELL_OPEN then BUY_CLOSE for same contract, get_options_positions returns empty list."""
        mock_client.place_options_order(
            "test_account", "IBIT", "PUT", 2026, 5, 15, 48.0, "SELL_OPEN", 1, 2.50
        )
        mock_client.place_options_order(
            "test_account", "IBIT", "PUT", 2026, 5, 15, 48.0, "BUY_CLOSE", 1, 1.00
        )
        positions = mock_client.get_options_positions("test_account")
        assert len(positions) == 0


class TestOptionsPositions:
    """Test get_options_positions() method."""

    def test_returns_list(self, mock_client):
        """get_options_positions() returns a list."""
        result = mock_client.get_options_positions("test_account")
        assert isinstance(result, list)

    def test_empty_when_no_options(self, mock_client):
        """Before any options orders, get_options_positions returns empty list."""
        assert len(mock_client.get_options_positions("test_account")) == 0

    def test_position_has_contract_details(self, mock_client):
        """After SELL_OPEN, returned position dict has required keys."""
        mock_client.place_options_order(
            "test_account", "IBIT", "PUT", 2026, 5, 15, 48.0, "SELL_OPEN", 1, 2.50
        )
        positions = mock_client.get_options_positions("test_account")
        assert len(positions) == 1
        pos = positions[0]
        required_keys = {"symbol", "option_type", "strike", "quantity", "position_type"}
        for key in required_keys:
            assert key in pos, f"Missing key '{key}' in position dict"
