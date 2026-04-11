"""
Tests for assignment detection: detect_and_process_expiry() on WheelStrategy.

Covers:
- Assignment detection when put disappears and IBIT shares appear (T-03-11)
- OTM expiry detection when put disappears and no IBIT shares (T-03-11)
- Idempotency: skip if already HOLDING_SHARES or CASH (T-03-12)
- Early exit when not expired yet
- Early exit when no active cycle
- Early exit when cycle is not SHORT_PUT

All tests use TDD RED pattern: detect_and_process_expiry() does not exist yet,
so all tests should fail with AttributeError before implementation.
"""

import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.etrade_client import MockETradeClient
from src.wheel_state import WheelState
from src.wheel_strategy import WheelStrategy

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    """Create an isolated test database (each test gets a unique tmp file)."""
    from src.database import Database

    return Database(db_path=tmp_path / "test.db")


@pytest.fixture
def mock_client():
    """Create a MockETradeClient."""
    return MockETradeClient()


@pytest.fixture
def setup_short_put_cycle(db):
    """
    Create a wheel cycle in SHORT_PUT state with a past expiry date.

    - put_strike=50.0
    - put_premium_received=2.50
    - put_expiry_date="2026-04-03" (past date)
    - Opens an options position with symbol="IBIT260403P00050000"

    Returns (cycle_id, position_id).
    """
    # Create a cycle (starts in CASH)
    cycle_id = db.create_wheel_cycle()

    # Transition CASH -> SHORT_PUT with put details
    db.transition_wheel_state(
        cycle_id,
        WheelState.SHORT_PUT,
        "put_sold",
        put_strike=50.0,
        put_premium_received=2.50,
        put_expiry_date="2026-04-03",
    )

    # Open the options position
    position_id = db.open_wheel_position(
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

    return cycle_id, position_id


# ---------------------------------------------------------------------------
# TestAssignmentDetection - 6 tests
# ---------------------------------------------------------------------------


class TestAssignmentDetection:
    """Tests for detect_and_process_expiry() detection logic."""

    def test_detects_assignment_when_shares_appear(self, db, mock_client, setup_short_put_cycle):
        """
        When cycle is SHORT_PUT, expiry has passed, put is gone from live positions,
        and IBIT shares appear in equity positions -> returns "assigned".
        """
        cycle_id, position_id = setup_short_put_cycle
        strategy = WheelStrategy(client=mock_client, db=db, account_id_key="test_key")

        # Clear mock's options positions (put has disappeared from E*TRADE)
        mock_client._options_positions = {}

        # Mock get_account_positions to return IBIT equity shares
        ibit_equity_positions = [
            {
                "Product": {"symbol": "IBIT", "securityType": "EQ"},
                "quantity": 100,
                "positionType": "LONG",
            }
        ]

        # Patch get_et_now to return a date after expiry (2026-04-07 > 2026-04-03)
        mock_now = MagicMock()
        mock_now.date.return_value = date(2026, 4, 7)

        with patch("src.wheel_strategy.get_et_now", return_value=mock_now):
            with patch.object(
                mock_client, "get_account_positions", return_value=ibit_equity_positions
            ) as mock_get_positions:
                result = strategy.detect_and_process_expiry()

        assert result == "assigned"
        # HI-06 fix: verify the positions query used the strategy's account_id_key
        mock_get_positions.assert_called_with("test_key")

    def test_detects_otm_expiry_when_no_shares(self, db, mock_client, setup_short_put_cycle):
        """
        When cycle is SHORT_PUT, expiry has passed, put is gone from live positions,
        and no IBIT shares -> returns "expired_otm".
        """
        cycle_id, position_id = setup_short_put_cycle
        strategy = WheelStrategy(client=mock_client, db=db, account_id_key="test_key")

        # Clear mock's options positions (put has disappeared)
        mock_client._options_positions = {}

        # Mock get_account_positions to return empty list (no IBIT shares)
        mock_now = MagicMock()
        mock_now.date.return_value = date(2026, 4, 7)

        with patch("src.wheel_strategy.get_et_now", return_value=mock_now):
            with patch.object(mock_client, "get_account_positions", return_value=[]):
                result = strategy.detect_and_process_expiry()

        assert result == "expired_otm"

    def test_skips_when_not_expired(self, db, mock_client, setup_short_put_cycle):
        """
        When today <= expiry_date, returns None (not expired yet).
        The setup fixture uses expiry_date="2026-04-03"; we mock today as 2026-04-02.
        """
        cycle_id, position_id = setup_short_put_cycle
        strategy = WheelStrategy(client=mock_client, db=db, account_id_key="test_key")

        # Patch get_et_now to return a date BEFORE expiry
        mock_now = MagicMock()
        mock_now.date.return_value = date(2026, 4, 2)  # before 2026-04-03

        with patch("src.wheel_strategy.get_et_now", return_value=mock_now):
            result = strategy.detect_and_process_expiry()

        assert result is None

    def test_skips_when_no_active_cycle(self, db, mock_client):
        """When get_active_cycle() returns None, returns None."""
        strategy = WheelStrategy(client=mock_client, db=db, account_id_key="test_key")
        result = strategy.detect_and_process_expiry()
        assert result is None

    def test_skips_when_cycle_not_short_put(self, db, mock_client):
        """When cycle state is CASH (no put sold yet), returns None."""
        # Create a cycle in CASH state (initial state, no put sold)
        db.create_wheel_cycle()
        # Cycle is in CASH state now — should skip

        strategy = WheelStrategy(client=mock_client, db=db, account_id_key="test_key")
        result = strategy.detect_and_process_expiry()

        assert result is None

    def test_idempotent_already_holding_shares(self, db, mock_client, setup_short_put_cycle):
        """
        When cycle state is already HOLDING_SHARES (prior detection ran),
        returns None (idempotency guarantee - T-03-12).
        """
        cycle_id, position_id = setup_short_put_cycle

        # Transition to HOLDING_SHARES (simulating prior detection run)
        db.transition_wheel_state(
            cycle_id,
            WheelState.HOLDING_SHARES,
            "put_assigned",
            shares_held=100,
            cost_basis=47.50,
        )

        strategy = WheelStrategy(client=mock_client, db=db, account_id_key="test_key")
        result = strategy.detect_and_process_expiry()

        assert result is None


# ---------------------------------------------------------------------------
# TestAssignmentTransition - 3 tests
# ---------------------------------------------------------------------------


class TestAssignmentTransition:
    """Tests for state transitions after assignment detection."""

    def test_assignment_transitions_to_holding_shares(self, db, mock_client, setup_short_put_cycle):
        """After assignment, cycle state is HOLDING_SHARES."""
        cycle_id, position_id = setup_short_put_cycle
        strategy = WheelStrategy(client=mock_client, db=db, account_id_key="test_key")

        mock_client._options_positions = {}
        ibit_equity_positions = [
            {"Product": {"symbol": "IBIT", "securityType": "EQ"}, "quantity": 100}
        ]

        mock_now = MagicMock()
        mock_now.date.return_value = date(2026, 4, 7)

        with patch("src.wheel_strategy.get_et_now", return_value=mock_now):
            with patch.object(
                mock_client, "get_account_positions", return_value=ibit_equity_positions
            ):
                strategy.detect_and_process_expiry()

        cycle = db.get_active_cycle()
        assert cycle["state"] == "HOLDING_SHARES"

    def test_assignment_cost_basis(self, db, mock_client, setup_short_put_cycle):
        """After assignment, cost_basis == put_strike - put_premium_received == 50.0 - 2.50 == 47.50."""
        cycle_id, position_id = setup_short_put_cycle
        strategy = WheelStrategy(client=mock_client, db=db, account_id_key="test_key")

        mock_client._options_positions = {}
        ibit_equity_positions = [
            {"Product": {"symbol": "IBIT", "securityType": "EQ"}, "quantity": 100}
        ]

        mock_now = MagicMock()
        mock_now.date.return_value = date(2026, 4, 7)

        with patch("src.wheel_strategy.get_et_now", return_value=mock_now):
            with patch.object(
                mock_client, "get_account_positions", return_value=ibit_equity_positions
            ):
                strategy.detect_and_process_expiry()

        cycle = db.get_active_cycle()
        assert cycle["cost_basis"] == pytest.approx(47.50)

    def test_assignment_closes_position(self, db, mock_client, setup_short_put_cycle):
        """After assignment, position status is CLOSED."""
        cycle_id, position_id = setup_short_put_cycle
        strategy = WheelStrategy(client=mock_client, db=db, account_id_key="test_key")

        mock_client._options_positions = {}
        ibit_equity_positions = [
            {"Product": {"symbol": "IBIT", "securityType": "EQ"}, "quantity": 100}
        ]

        mock_now = MagicMock()
        mock_now.date.return_value = date(2026, 4, 7)

        with patch("src.wheel_strategy.get_et_now", return_value=mock_now):
            with patch.object(
                mock_client, "get_account_positions", return_value=ibit_equity_positions
            ):
                strategy.detect_and_process_expiry()

        positions = db.get_cycle_positions(cycle_id)
        assert len(positions) == 1
        assert positions[0]["status"] == "CLOSED"


# ---------------------------------------------------------------------------
# TestOTMExpiry - 3 tests
# ---------------------------------------------------------------------------


class TestOTMExpiry:
    """Tests for OTM expiry path (put expires worthless)."""

    def test_otm_transitions_to_cash(self, db, mock_client, setup_short_put_cycle):
        """After OTM expiry, cycle state is CASH and closed_at is not None."""
        cycle_id, position_id = setup_short_put_cycle
        strategy = WheelStrategy(client=mock_client, db=db, account_id_key="test_key")

        mock_client._options_positions = {}

        mock_now = MagicMock()
        mock_now.date.return_value = date(2026, 4, 7)

        with patch("src.wheel_strategy.get_et_now", return_value=mock_now):
            with patch.object(mock_client, "get_account_positions", return_value=[]):
                strategy.detect_and_process_expiry()

        # After OTM expiry, cycle is CASH (closed)
        history = db.get_cycle_history(limit=1)
        assert len(history) == 1
        assert history[0]["state"] == "CASH"
        assert history[0]["closed_at"] is not None

    def test_otm_records_pnl(self, db, mock_client, setup_short_put_cycle):
        """After OTM expiry, realized_pnl == premium_received * 100 == 2.50 * 100 == 250.0."""
        cycle_id, position_id = setup_short_put_cycle
        strategy = WheelStrategy(client=mock_client, db=db, account_id_key="test_key")

        mock_client._options_positions = {}

        mock_now = MagicMock()
        mock_now.date.return_value = date(2026, 4, 7)

        with patch("src.wheel_strategy.get_et_now", return_value=mock_now):
            with patch.object(mock_client, "get_account_positions", return_value=[]):
                strategy.detect_and_process_expiry()

        history = db.get_cycle_history(limit=1)
        assert history[0]["realized_pnl"] == pytest.approx(250.0)

    def test_otm_closes_position_zero_premium(self, db, mock_client, setup_short_put_cycle):
        """After OTM expiry, position close_premium == 0.0 and status == CLOSED."""
        cycle_id, position_id = setup_short_put_cycle
        strategy = WheelStrategy(client=mock_client, db=db, account_id_key="test_key")

        mock_client._options_positions = {}

        mock_now = MagicMock()
        mock_now.date.return_value = date(2026, 4, 7)

        with patch("src.wheel_strategy.get_et_now", return_value=mock_now):
            with patch.object(mock_client, "get_account_positions", return_value=[]):
                strategy.detect_and_process_expiry()

        positions = db.get_cycle_positions(cycle_id)
        assert len(positions) == 1
        assert positions[0]["status"] == "CLOSED"
        assert positions[0]["close_premium"] == pytest.approx(0.0)
