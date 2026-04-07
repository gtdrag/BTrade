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
