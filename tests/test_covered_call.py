"""
Tests for covered call signal generation, strike selection, and call-away/OTM detection.

TDD RED phase -- all tests are written first against spec.
Implementation lives in src/wheel_strategy.py.

Covers:
- get_call_signal(): returns CallSignal when HOLDING_SHARES and profitable strikes exist
- get_call_signal(): returns None in wrong states or no cycle
- select_call_strike(): cost-basis hard filter (T-04-01)
- select_call_strike(): delta range filter (0.25-0.35)
- detect_and_process_expiry() COVERED_CALL branch:
    - "called_away" when call gone AND shares gone (T-04-02 dual condition)
    - "call_expired_otm" when call gone but shares remain
    - None when not expired or call still live
"""

import sys
from datetime import date
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database import Database
from src.etrade_client import MockETradeClient
from src.wheel_state import WheelState
from src.wheel_strategy import CallSignal, WheelStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_contract(
    option_type: str,
    strike: float,
    delta: float,
    bid: float,
    expiry_year: int = 2026,
    expiry_month: int = 5,
    expiry_day: int = 15,
    dte: int = 35,
    symbol: Optional[str] = None,
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


def _setup_holding_shares_cycle(db: Database) -> int:
    """Create a cycle in HOLDING_SHARES state with cost_basis=47.50.

    Steps:
    1. CASH -> SHORT_PUT (put_strike=50.0, put_premium_received=2.50)
    2. SHORT_PUT -> HOLDING_SHARES (shares_held=100, cost_basis=47.50)

    Returns cycle_id.
    """
    cycle_id = db.create_wheel_cycle()

    db.transition_wheel_state(
        cycle_id,
        WheelState.SHORT_PUT,
        "put_sold",
        put_strike=50.0,
        put_premium_received=2.50,
        put_expiry_date="2026-04-03",
    )

    # Open the put position (required for the cycle to have a full history)
    db.open_wheel_position(
        cycle_id=cycle_id,
        symbol="IBIT260403P00050000",
        option_type="PUT",
        strike=50.0,
        expiry_date="2026-04-03",
        dte_at_entry=30,
        premium_received=2.50,
        quantity=1,
        delta=-0.25,
        gamma=0.05,
        theta=-0.10,
        vega=0.15,
        iv=0.35,
    )

    db.transition_wheel_state(
        cycle_id,
        WheelState.HOLDING_SHARES,
        "put_assigned",
        shares_held=100,
        cost_basis=47.50,
    )

    return cycle_id


def _setup_covered_call_cycle(db: Database) -> tuple:
    """Create a cycle in COVERED_CALL state, with an open CALL position.

    Extends _setup_holding_shares_cycle() to COVERED_CALL, then opens a CALL
    position with symbol="IBIT260515C00050000", strike=50.0, expiry 2026-05-15.

    Reflects 1.50 call premium reducing basis from 47.50 to 46.0.

    Returns (cycle_id, call_position_id).
    """
    cycle_id = _setup_holding_shares_cycle(db)

    db.transition_wheel_state(
        cycle_id,
        WheelState.COVERED_CALL,
        "call_sold",
        covered_call_premiums_collected=1.50,
        cost_basis=46.0,
    )

    call_position_id = db.open_wheel_position(
        cycle_id=cycle_id,
        symbol="IBIT260515C00050000",
        option_type="CALL",
        strike=50.0,
        expiry_date="2026-05-15",
        dte_at_entry=35,
        premium_received=1.50,
        quantity=1,
        delta=0.28,
        gamma=0.05,
        theta=-0.04,
        vega=0.10,
        iv=0.35,
    )

    return cycle_id, call_position_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    """Create an isolated test database (each test gets a unique tmp file)."""
    return Database(db_path=tmp_path / "test.db")


@pytest.fixture
def mock_client():
    """Create a MockETradeClient."""
    return MockETradeClient()


# ---------------------------------------------------------------------------
# TestCallSignal -- get_call_signal() behavior
# ---------------------------------------------------------------------------


class TestCallSignal:
    """Tests for get_call_signal(): state gates, strike selection, return type."""

    def test_call_signal_when_holding_shares(self, db, mock_client):
        """Returns CallSignal when cycle is HOLDING_SHARES and profitable strikes exist."""
        _setup_holding_shares_cycle(db)  # cost_basis=47.50

        # Chain with a CALL in the 0.25-0.35 delta range above cost basis
        call_contract = _make_contract("CALL", strike=48.0, delta=0.28, bid=1.20)
        with patch.object(mock_client, "get_ibit_options_chain", return_value=[call_contract]):
            strategy = WheelStrategy(mock_client, db)
            signal = strategy.get_call_signal()

        assert signal is not None
        assert isinstance(signal, CallSignal)
        assert signal.strike >= 47.50  # must be at or above cost basis
        assert 0.25 <= signal.delta <= 0.35

    def test_no_signal_when_no_active_cycle(self, db, mock_client):
        """Returns None when there is no active cycle."""
        call_contract = _make_contract("CALL", strike=48.0, delta=0.28, bid=1.20)
        with patch.object(mock_client, "get_ibit_options_chain", return_value=[call_contract]):
            strategy = WheelStrategy(mock_client, db)
            signal = strategy.get_call_signal()

        assert signal is None

    def test_no_signal_wrong_state_short_put(self, db, mock_client):
        """Returns None when cycle is in SHORT_PUT state."""
        cycle_id = db.create_wheel_cycle()
        db.transition_wheel_state(
            cycle_id,
            WheelState.SHORT_PUT,
            "put_sold",
            put_strike=50.0,
            put_premium_received=2.50,
        )

        call_contract = _make_contract("CALL", strike=48.0, delta=0.28, bid=1.20)
        with patch.object(mock_client, "get_ibit_options_chain", return_value=[call_contract]):
            strategy = WheelStrategy(mock_client, db)
            signal = strategy.get_call_signal()

        assert signal is None

    def test_no_signal_cash_state(self, db, mock_client):
        """Returns None when cycle is in CASH state (freshly created, not yet transitioned)."""
        # CASH is the initial state; no further transitions needed
        db.create_wheel_cycle()

        call_contract = _make_contract("CALL", strike=48.0, delta=0.28, bid=1.20)
        with patch.object(mock_client, "get_ibit_options_chain", return_value=[call_contract]):
            strategy = WheelStrategy(mock_client, db)
            signal = strategy.get_call_signal()

        assert signal is None

    def test_no_strikes_above_cost_basis(self, db, mock_client):
        """Returns None when cost_basis=60.0 and all chain strikes are below 60."""
        cycle_id = db.create_wheel_cycle()
        db.transition_wheel_state(
            cycle_id,
            WheelState.SHORT_PUT,
            "put_sold",
            put_strike=65.0,
            put_premium_received=2.50,
        )
        db.transition_wheel_state(
            cycle_id,
            WheelState.HOLDING_SHARES,
            "put_assigned",
            shares_held=100,
            cost_basis=60.0,
        )

        # All CALL strikes are below cost_basis=60.0
        chain = [
            _make_contract("CALL", strike=55.0, delta=0.28, bid=1.20),
            _make_contract("CALL", strike=58.0, delta=0.30, bid=1.10),
        ]
        with patch.object(mock_client, "get_ibit_options_chain", return_value=chain):
            strategy = WheelStrategy(mock_client, db)
            signal = strategy.get_call_signal()

        assert signal is None


# ---------------------------------------------------------------------------
# TestCallStrikeSelection -- select_call_strike() filtering
# ---------------------------------------------------------------------------


class TestCallStrikeSelection:
    """Tests for select_call_strike(): delta filter, cost basis filter, pick highest bid."""

    def test_selects_highest_bid_in_delta_range(self, db, mock_client):
        """Picks the CALL with the highest bid among those in 0.25-0.35 delta range."""
        chain = [
            _make_contract("CALL", strike=48.0, delta=0.28, bid=1.00),
            _make_contract("CALL", strike=49.0, delta=0.30, bid=1.50),  # highest bid
            _make_contract("CALL", strike=50.0, delta=0.25, bid=0.80),
        ]
        with patch.object(mock_client, "get_ibit_options_chain", return_value=chain):
            strategy = WheelStrategy(mock_client, db)
            result = strategy.select_call_strike(cost_basis=46.0)

        assert result is not None
        assert result["strike"] == 49.0
        assert result["bid"] == 1.50

    def test_cost_basis_filter(self, db, mock_client):
        """Only returns strikes at or above cost_basis=49.0."""
        chain = [
            _make_contract("CALL", strike=46.0, delta=0.30, bid=2.00),  # below cost basis
            _make_contract("CALL", strike=48.0, delta=0.28, bid=1.50),  # below cost basis
            _make_contract("CALL", strike=50.0, delta=0.27, bid=1.00),  # at or above -- selected
        ]
        with patch.object(mock_client, "get_ibit_options_chain", return_value=chain):
            strategy = WheelStrategy(mock_client, db)
            result = strategy.select_call_strike(cost_basis=49.0)

        assert result is not None
        assert float(result["strike"]) >= 49.0
        assert result["strike"] == 50.0

    def test_no_calls_in_delta_range(self, db, mock_client):
        """Returns None when all CALL contracts have delta outside 0.25-0.35."""
        chain = [
            _make_contract("CALL", strike=48.0, delta=0.10, bid=0.50),  # too low delta
            _make_contract("CALL", strike=50.0, delta=0.50, bid=2.00),  # too high delta
        ]
        with patch.object(mock_client, "get_ibit_options_chain", return_value=chain):
            strategy = WheelStrategy(mock_client, db)
            result = strategy.select_call_strike(cost_basis=45.0)

        assert result is None

    def test_filters_put_contracts(self, db, mock_client):
        """Ignores PUT contracts even if they fall in the delta range."""
        chain = [
            # PUT in delta range -- should be ignored (delta is negative for puts)
            _make_contract("PUT", strike=48.0, delta=-0.28, bid=1.50),
            # CALL in delta range -- should be selected
            _make_contract("CALL", strike=49.0, delta=0.28, bid=0.90),
        ]
        with patch.object(mock_client, "get_ibit_options_chain", return_value=chain):
            strategy = WheelStrategy(mock_client, db)
            result = strategy.select_call_strike(cost_basis=45.0)

        assert result is not None
        assert result["option_type"] == "CALL"
        assert result["strike"] == 49.0


# ---------------------------------------------------------------------------
# TestCallExpiry -- detect_and_process_expiry() COVERED_CALL branch
# ---------------------------------------------------------------------------


class TestCallExpiry:
    """Tests for the COVERED_CALL branch of detect_and_process_expiry()."""

    def test_called_away(self, db, mock_client):
        """Returns 'called_away' when call is gone from options AND IBIT shares are gone."""
        cycle_id, call_pos_id = _setup_covered_call_cycle(db)

        # Simulate: past expiry date, call gone from live options, shares gone from equity
        with (
            patch("src.wheel_strategy.get_et_now") as mock_now,
            patch.object(mock_client, "get_options_positions", return_value=[]),
            patch.object(mock_client, "get_account_positions", return_value=[]),
        ):
            # Today is after the call expiry (2026-05-15)
            mock_now.return_value = MagicMock()
            mock_now.return_value.date.return_value = date(2026, 5, 16)

            strategy = WheelStrategy(mock_client, db)
            result = strategy.detect_and_process_expiry()

        assert result == "called_away"

    def test_call_expired_otm(self, db, mock_client):
        """Returns 'call_expired_otm' when call is gone but IBIT shares remain."""
        cycle_id, call_pos_id = _setup_covered_call_cycle(db)

        ibit_equity = [{"Product": {"symbol": "IBIT", "securityType": "EQ"}, "quantity": 100}]

        with (
            patch("src.wheel_strategy.get_et_now") as mock_now,
            patch.object(mock_client, "get_options_positions", return_value=[]),
            patch.object(mock_client, "get_account_positions", return_value=ibit_equity),
        ):
            mock_now.return_value = MagicMock()
            mock_now.return_value.date.return_value = date(2026, 5, 16)

            strategy = WheelStrategy(mock_client, db)
            result = strategy.detect_and_process_expiry()

        assert result == "call_expired_otm"

    def test_skips_when_call_not_expired(self, db, mock_client):
        """Returns None when today <= call expiry_date (call not yet expired)."""
        _setup_covered_call_cycle(db)

        with (
            patch("src.wheel_strategy.get_et_now") as mock_now,
        ):
            # Today is BEFORE the call expiry (2026-05-15)
            mock_now.return_value = MagicMock()
            mock_now.return_value.date.return_value = date(2026, 5, 14)

            strategy = WheelStrategy(mock_client, db)
            result = strategy.detect_and_process_expiry()

        assert result is None

    def test_skips_when_call_still_live(self, db, mock_client):
        """Returns None when call symbol still appears in live E*TRADE options positions."""
        _setup_covered_call_cycle(db)

        # Call is still listed as live in E*TRADE
        live_call = [{"symbol": "IBIT260515C00050000", "quantity": -1}]

        with (
            patch("src.wheel_strategy.get_et_now") as mock_now,
            patch.object(mock_client, "get_options_positions", return_value=live_call),
        ):
            # Past expiry but settlement not complete
            mock_now.return_value = MagicMock()
            mock_now.return_value.date.return_value = date(2026, 5, 16)

            strategy = WheelStrategy(mock_client, db)
            result = strategy.detect_and_process_expiry()

        assert result is None

    def test_cycle_closes_to_cash_on_callaway(self, db, mock_client):
        """After called_away, cycle transitions to CASH and realized_pnl is recorded."""
        cycle_id, call_pos_id = _setup_covered_call_cycle(db)

        with (
            patch("src.wheel_strategy.get_et_now") as mock_now,
            patch.object(mock_client, "get_options_positions", return_value=[]),
            patch.object(mock_client, "get_account_positions", return_value=[]),
        ):
            mock_now.return_value = MagicMock()
            mock_now.return_value.date.return_value = date(2026, 5, 16)

            strategy = WheelStrategy(mock_client, db)
            strategy.detect_and_process_expiry()

        # Cycle must be closed (CASH state, closed_at set)
        history = db.get_cycle_history()
        assert len(history) == 1
        closed_cycle = history[0]
        assert closed_cycle["state"] == "CASH"
        assert closed_cycle["closed_at"] is not None

        # realized_pnl must be set (not None or zero)
        assert closed_cycle["realized_pnl"] is not None

    def test_otm_returns_to_holding(self, db, mock_client):
        """After call_expired_otm, cycle transitions back to HOLDING_SHARES."""
        cycle_id, call_pos_id = _setup_covered_call_cycle(db)

        ibit_equity = [{"Product": {"symbol": "IBIT", "securityType": "EQ"}, "quantity": 100}]

        with (
            patch("src.wheel_strategy.get_et_now") as mock_now,
            patch.object(mock_client, "get_options_positions", return_value=[]),
            patch.object(mock_client, "get_account_positions", return_value=ibit_equity),
        ):
            mock_now.return_value = MagicMock()
            mock_now.return_value.date.return_value = date(2026, 5, 16)

            strategy = WheelStrategy(mock_client, db)
            strategy.detect_and_process_expiry()

        # Cycle must be active and in HOLDING_SHARES state
        active_cycle = db.get_active_cycle()
        assert active_cycle is not None
        assert active_cycle["state"] == "HOLDING_SHARES"
        assert active_cycle["closed_at"] is None
