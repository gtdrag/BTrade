"""
Tests for WheelStrategy: put signal generation, strike selection, cash validation.

TDD test file — all tests are written first against a spec.
Implementation lives in src/wheel_strategy.py.
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.wheel_strategy import PutSignal, WheelStrategy


def _make_mock_history(high_values, close_values):
    """Build a DataFrame mimicking yf.Ticker().history() output."""
    return pd.DataFrame({
        "High": high_values,
        "Close": close_values,
    })


def _make_contract(
    option_type: str,
    strike: float,
    delta: float,
    bid: float,
    expiry_year: int = 2026,
    expiry_month: int = 5,
    expiry_day: int = 15,
    dte: int = 35,
    symbol: str | None = None,
    iv: float = 0.35,
    gamma: float = 0.05,
    theta: float = -0.04,
    vega: float = 0.10,
) -> dict:
    """Build a minimal contract dict matching get_ibit_options_chain() shape."""
    return {
        "symbol": symbol or f"IBIT260515{option_type[0]}{int(strike * 1000):08d}",
        "option_type": option_type,
        "strike": strike,
        "expiry_year": expiry_year,
        "expiry_month": expiry_month,
        "expiry_day": expiry_day,
        "expiry_date": f"{expiry_year}-{expiry_month:02d}-{expiry_day:02d}",
        "dte": dte,
        "bid": bid,
        "ask": bid + 0.10,
        "last": bid + 0.05,
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega": vega,
        "iv": iv,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    """Create an isolated test database."""
    from src.database import Database
    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    return Database(tmp_path / "test.db")


@pytest.fixture
def mock_client():
    """Return a MockETradeClient with $100k cash and IBIT spot = 50.0."""
    from src.etrade_client import MockETradeClient
    client = MockETradeClient(initial_cash=100_000.0)
    client.set_mock_price("IBIT", 50.0)
    return client


@pytest.fixture
def strategy(mock_client, db):
    """Return a WheelStrategy wired to mock client and isolated database."""
    return WheelStrategy(client=mock_client, db=db, account_id_key="mock_key_001")


# ---------------------------------------------------------------------------
# TestPutSignal — tests for WheelStrategy.get_put_signal()
# ---------------------------------------------------------------------------

class TestPutSignal:
    """Tests for WheelStrategy.get_put_signal()."""

    def test_signal_fires_on_pullback(self, strategy):
        """get_put_signal() returns PutSignal when IBIT is >=2% below 5-day high."""
        # 5-day high=50.0, current close=48.5 => -3% pullback => should fire
        history_df = _make_mock_history(
            high_values=[50.0, 49.5, 49.8, 50.0, 49.2],
            close_values=[49.5, 49.0, 49.6, 49.8, 48.5],
        )
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.history.return_value = history_df
            result = strategy.get_put_signal()
        assert result is not None, "Expected PutSignal on >=2% pullback, got None"
        assert isinstance(result, PutSignal)

    def test_no_signal_without_pullback(self, strategy):
        """get_put_signal() returns None when IBIT is only 1% below 5-day high."""
        # 5-day high=50.0, current close=49.5 => -1% pullback => should NOT fire
        history_df = _make_mock_history(
            high_values=[50.0, 49.5, 49.8, 50.0, 49.2],
            close_values=[49.5, 49.0, 49.6, 49.8, 49.5],
        )
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.history.return_value = history_df
            result = strategy.get_put_signal()
        assert result is None, "Expected None for <2% pullback"

    def test_no_signal_when_short_put_active(self, strategy, db):
        """get_put_signal() returns None when active cycle is SHORT_PUT."""
        from src.wheel_state import WheelState
        cycle_id = db.create_wheel_cycle()
        db.transition_wheel_state(cycle_id, WheelState.SHORT_PUT, reason="sold put")

        # No yfinance mock needed — state check happens first
        result = strategy.get_put_signal()
        assert result is None, "Expected None when cycle state is SHORT_PUT"

    def test_no_signal_when_holding_shares(self, strategy, db):
        """get_put_signal() returns None when active cycle is HOLDING_SHARES."""
        from src.wheel_state import WheelState
        cycle_id = db.create_wheel_cycle()
        db.transition_wheel_state(cycle_id, WheelState.SHORT_PUT, reason="sold put",
                                   put_strike=50.0, put_premium_received=2.0)
        db.transition_wheel_state(cycle_id, WheelState.HOLDING_SHARES, reason="assigned",
                                   shares_held=100, cost_basis=48.0)

        result = strategy.get_put_signal()
        assert result is None, "Expected None when cycle state is HOLDING_SHARES"

    def test_signal_fires_when_cycle_cash(self, strategy, db):
        """get_put_signal() returns PutSignal when active cycle is in CASH state."""
        # Create cycle in CASH state (default)
        db.create_wheel_cycle()

        history_df = _make_mock_history(
            high_values=[50.0, 49.5, 49.8, 50.0, 49.2],
            close_values=[49.5, 49.0, 49.6, 49.8, 48.5],
        )
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.history.return_value = history_df
            result = strategy.get_put_signal()
        assert result is not None, "Expected PutSignal when cycle is CASH"
        assert isinstance(result, PutSignal)

    def test_signal_fires_when_no_cycle(self, strategy, db):
        """get_put_signal() returns PutSignal when no active cycle exists (fresh start)."""
        # Empty DB, no cycle
        assert db.get_active_cycle() is None

        history_df = _make_mock_history(
            high_values=[50.0, 49.5, 49.8, 50.0, 49.2],
            close_values=[49.5, 49.0, 49.6, 49.8, 48.5],
        )
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.history.return_value = history_df
            result = strategy.get_put_signal()
        assert result is not None, "Expected PutSignal when no cycle exists"
        assert isinstance(result, PutSignal)

    def test_put_signal_has_correct_fields(self, strategy):
        """Returned PutSignal contains expected fields with correct types."""
        history_df = _make_mock_history(
            high_values=[50.0, 49.5, 49.8, 50.0, 49.2],
            close_values=[49.5, 49.0, 49.6, 49.8, 48.5],
        )
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.history.return_value = history_df
            result = strategy.get_put_signal()
        assert result is not None
        assert isinstance(result.strike, float)
        assert isinstance(result.premium, float)
        assert isinstance(result.delta, float)
        assert isinstance(result.dte, int)
        assert isinstance(result.max_risk, float)
        assert result.max_risk == result.strike * 100
        assert result.pullback_pct < 0, "pullback_pct should be negative"


# ---------------------------------------------------------------------------
# TestStrikeSelection — tests for WheelStrategy.select_put_strike()
# ---------------------------------------------------------------------------

class TestStrikeSelection:
    """Tests for WheelStrategy.select_put_strike()."""

    def test_selects_highest_bid_in_delta_range(self, strategy, mock_client):
        """select_put_strike() returns the put with highest bid where 0.20 <= abs(delta) <= 0.30."""
        chain = [
            _make_contract("PUT", 47.0, delta=-0.15, bid=0.30),  # out of range
            _make_contract("PUT", 48.0, delta=-0.22, bid=1.50),  # in range
            _make_contract("PUT", 48.5, delta=-0.28, bid=2.00),  # in range — highest bid
            _make_contract("PUT", 49.0, delta=-0.35, bid=2.50),  # out of range
        ]
        with patch.object(mock_client, "get_ibit_options_chain", return_value=chain):
            result = strategy.select_put_strike()
        assert result is not None
        assert result["strike"] == 48.5
        assert result["delta"] == -0.28

    def test_handles_negative_delta(self, strategy, mock_client):
        """select_put_strike() correctly handles negative put deltas via abs()."""
        # All put deltas are negative; filter uses abs(delta)
        chain = [
            _make_contract("PUT", 48.0, delta=-0.25, bid=1.80),  # abs(-0.25)=0.25 -> in range
        ]
        with patch.object(mock_client, "get_ibit_options_chain", return_value=chain):
            result = strategy.select_put_strike()
        assert result is not None
        assert abs(result["delta"]) == pytest.approx(0.25)

    def test_returns_none_when_no_contracts_in_range(self, strategy, mock_client):
        """select_put_strike() returns None when no puts have abs(delta) in 0.20-0.30."""
        chain = [
            _make_contract("PUT", 47.0, delta=-0.10, bid=0.50),  # below range
            _make_contract("PUT", 50.0, delta=-0.55, bid=3.00),  # above range
        ]
        with patch.object(mock_client, "get_ibit_options_chain", return_value=chain):
            result = strategy.select_put_strike()
        assert result is None

    def test_filters_only_puts(self, strategy, mock_client):
        """select_put_strike() ignores CALL contracts even if their delta is in range."""
        chain = [
            # CALL with delta in 0.20-0.30 range — should be ignored
            _make_contract("CALL", 52.0, delta=0.25, bid=3.00),
            # PUT out of range
            _make_contract("PUT", 47.0, delta=-0.10, bid=0.50),
        ]
        with patch.object(mock_client, "get_ibit_options_chain", return_value=chain):
            result = strategy.select_put_strike()
        assert result is None, "Should not select a CALL contract"

    def test_returns_none_on_empty_chain(self, strategy, mock_client):
        """select_put_strike() returns None when chain is empty."""
        with patch.object(mock_client, "get_ibit_options_chain", return_value=[]):
            result = strategy.select_put_strike()
        assert result is None

    def test_picks_highest_bid_among_multiple_in_range(self, strategy, mock_client):
        """select_put_strike() picks the contract with the highest bid in the delta range."""
        chain = [
            _make_contract("PUT", 48.0, delta=-0.20, bid=1.00),
            _make_contract("PUT", 48.5, delta=-0.25, bid=1.80),  # highest
            _make_contract("PUT", 49.0, delta=-0.30, bid=1.50),
        ]
        with patch.object(mock_client, "get_ibit_options_chain", return_value=chain):
            result = strategy.select_put_strike()
        assert result is not None
        assert result["bid"] == pytest.approx(1.80)


# ---------------------------------------------------------------------------
# TestCashValidation — tests for cash collateral gate in get_put_signal()
# ---------------------------------------------------------------------------

class TestCashValidation:
    """Tests for cash collateral validation inside get_put_signal()."""

    def _history_with_3pct_pullback(self):
        """Helper: return a DataFrame causing a >=2% pullback signal."""
        return _make_mock_history(
            high_values=[50.0, 49.5, 49.8, 50.0, 49.2],
            close_values=[49.5, 49.0, 49.6, 49.8, 48.5],
        )

    def test_signal_none_when_insufficient_cash(self, db, mock_client):
        """get_put_signal() returns None when cash < strike * 100."""
        from src.etrade_client import MockETradeClient
        # Need a strike around 48.0, so require $4800. Set cash to $3000.
        poor_client = MockETradeClient(initial_cash=3_000.0)
        poor_client.set_mock_price("IBIT", 50.0)
        strategy = WheelStrategy(client=poor_client, db=db, account_id_key="mock_key_001")

        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.history.return_value = self._history_with_3pct_pullback()
            result = strategy.get_put_signal()
        assert result is None, "Expected None when cash < strike * 100"

    def test_signal_fires_when_sufficient_cash(self, db, mock_client):
        """get_put_signal() returns PutSignal when cash >= strike * 100."""
        from src.etrade_client import MockETradeClient
        # Rich client has $5000, strike ~ 48.0 => requires $4800 => sufficient
        rich_client = MockETradeClient(initial_cash=5_000.0)
        rich_client.set_mock_price("IBIT", 50.0)
        strategy = WheelStrategy(client=rich_client, db=db, account_id_key="mock_key_001")

        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.history.return_value = self._history_with_3pct_pullback()
            result = strategy.get_put_signal()
        assert result is not None, "Expected PutSignal when cash >= strike * 100"

    def test_max_risk_equals_strike_times_100(self, db, mock_client):
        """PutSignal.max_risk is always strike * 100 (1 contract = 100 shares)."""
        strategy = WheelStrategy(client=mock_client, db=db, account_id_key="mock_key_001")

        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.history.return_value = self._history_with_3pct_pullback()
            result = strategy.get_put_signal()
        assert result is not None
        assert result.max_risk == pytest.approx(result.strike * 100)
