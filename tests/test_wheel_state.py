"""
Tests for wheel strategy state machine, dataclasses, and database schema.
"""

import sys
from datetime import datetime
from pathlib import Path

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.wheel_state import (
    WheelState,
    WheelCycle,
    OptionsPosition,
    VALID_TRANSITIONS,
    transition,
)


class TestWheelStateTransitions:
    """Tests for WheelState enum and transition() function."""

    def test_wheel_state_has_exactly_four_members(self):
        """WheelState enum must have exactly 4 members."""
        members = list(WheelState)
        assert len(members) == 4

    def test_wheel_state_member_names(self):
        """WheelState has CASH, SHORT_PUT, HOLDING_SHARES, COVERED_CALL."""
        names = {s.name for s in WheelState}
        assert names == {"CASH", "SHORT_PUT", "HOLDING_SHARES", "COVERED_CALL"}

    def test_wheel_state_string_values(self):
        """WheelState values are strings matching state names (for SQLite storage)."""
        assert WheelState.CASH.value == "CASH"
        assert WheelState.SHORT_PUT.value == "SHORT_PUT"
        assert WheelState.HOLDING_SHARES.value == "HOLDING_SHARES"
        assert WheelState.COVERED_CALL.value == "COVERED_CALL"

    def test_valid_transitions_exported(self):
        """VALID_TRANSITIONS dict is exported and has 4 keys."""
        assert isinstance(VALID_TRANSITIONS, dict)
        assert len(VALID_TRANSITIONS) == 4

    def test_transition_cash_to_short_put(self):
        """CASH -> SHORT_PUT is valid (sell a put)."""
        result = transition(WheelState.CASH, WheelState.SHORT_PUT)
        assert result == WheelState.SHORT_PUT

    def test_transition_short_put_to_cash(self):
        """SHORT_PUT -> CASH is valid (put expires worthless)."""
        result = transition(WheelState.SHORT_PUT, WheelState.CASH)
        assert result == WheelState.CASH

    def test_transition_short_put_to_holding_shares(self):
        """SHORT_PUT -> HOLDING_SHARES is valid (assigned)."""
        result = transition(WheelState.SHORT_PUT, WheelState.HOLDING_SHARES)
        assert result == WheelState.HOLDING_SHARES

    def test_transition_holding_shares_to_covered_call(self):
        """HOLDING_SHARES -> COVERED_CALL is valid (sell a call)."""
        result = transition(WheelState.HOLDING_SHARES, WheelState.COVERED_CALL)
        assert result == WheelState.COVERED_CALL

    def test_transition_covered_call_to_holding_shares(self):
        """COVERED_CALL -> HOLDING_SHARES is valid (call expires worthless)."""
        result = transition(WheelState.COVERED_CALL, WheelState.HOLDING_SHARES)
        assert result == WheelState.HOLDING_SHARES

    def test_transition_covered_call_to_cash(self):
        """COVERED_CALL -> CASH is valid (shares called away)."""
        result = transition(WheelState.COVERED_CALL, WheelState.CASH)
        assert result == WheelState.CASH

    def test_invalid_transition_cash_to_holding_shares(self):
        """CASH -> HOLDING_SHARES is invalid (must go through SHORT_PUT first)."""
        with pytest.raises(ValueError):
            transition(WheelState.CASH, WheelState.HOLDING_SHARES)

    def test_invalid_transition_cash_to_covered_call(self):
        """CASH -> COVERED_CALL is invalid (must go through SHORT_PUT and HOLDING_SHARES)."""
        with pytest.raises(ValueError):
            transition(WheelState.CASH, WheelState.COVERED_CALL)

    def test_invalid_transition_short_put_to_covered_call(self):
        """SHORT_PUT -> COVERED_CALL is invalid (must go through HOLDING_SHARES)."""
        with pytest.raises(ValueError):
            transition(WheelState.SHORT_PUT, WheelState.COVERED_CALL)

    def test_invalid_transition_holding_shares_to_cash(self):
        """HOLDING_SHARES -> CASH is invalid (must go through COVERED_CALL)."""
        with pytest.raises(ValueError):
            transition(WheelState.HOLDING_SHARES, WheelState.CASH)

    def test_invalid_transition_error_message_is_descriptive(self):
        """ValueError message includes from/to state names."""
        with pytest.raises(ValueError) as exc_info:
            transition(WheelState.CASH, WheelState.HOLDING_SHARES)
        message = str(exc_info.value)
        assert "CASH" in message
        assert "HOLDING_SHARES" in message

    def test_valid_transitions_cash_only_allows_short_put(self):
        """CASH can only go to SHORT_PUT."""
        assert VALID_TRANSITIONS[WheelState.CASH] == {WheelState.SHORT_PUT}

    def test_valid_transitions_short_put_allows_cash_and_holding(self):
        """SHORT_PUT can go to CASH or HOLDING_SHARES."""
        assert VALID_TRANSITIONS[WheelState.SHORT_PUT] == {
            WheelState.CASH,
            WheelState.HOLDING_SHARES,
        }

    def test_valid_transitions_holding_shares_only_allows_covered_call(self):
        """HOLDING_SHARES can only go to COVERED_CALL."""
        assert VALID_TRANSITIONS[WheelState.HOLDING_SHARES] == {WheelState.COVERED_CALL}

    def test_valid_transitions_covered_call_allows_holding_and_cash(self):
        """COVERED_CALL can go to HOLDING_SHARES or CASH."""
        assert VALID_TRANSITIONS[WheelState.COVERED_CALL] == {
            WheelState.HOLDING_SHARES,
            WheelState.CASH,
        }


class TestDataclasses:
    """Tests for WheelCycle and OptionsPosition dataclasses."""

    def test_wheel_cycle_can_be_instantiated_with_all_fields(self):
        """WheelCycle can be created with all fields."""
        cycle = WheelCycle(
            id=1,
            state=WheelState.CASH,
            underlying="IBIT",
            put_strike=48.0,
            put_premium_received=1.50,
            put_expiry_date="2026-05-16",
            shares_held=0,
            cost_basis=0.0,
            covered_call_premiums_collected=0.0,
            realized_pnl=None,
            opened_at="2026-04-06T09:35:00",
            closed_at=None,
            created_at="2026-04-06T09:35:00",
            updated_at="2026-04-06T09:35:00",
        )
        assert cycle.id == 1
        assert cycle.state == WheelState.CASH
        assert cycle.underlying == "IBIT"
        assert cycle.put_strike == 48.0

    def test_wheel_cycle_optional_fields_accept_none(self):
        """WheelCycle Optional fields can be None."""
        cycle = WheelCycle(
            id=1,
            state=WheelState.CASH,
            underlying="IBIT",
            put_strike=None,
            put_premium_received=0.0,
            put_expiry_date=None,
            shares_held=0,
            cost_basis=0.0,
            covered_call_premiums_collected=0.0,
            realized_pnl=None,
            opened_at="2026-04-06T09:35:00",
            closed_at=None,
            created_at="2026-04-06T09:35:00",
            updated_at="2026-04-06T09:35:00",
        )
        assert cycle.put_strike is None
        assert cycle.put_expiry_date is None
        assert cycle.realized_pnl is None
        assert cycle.closed_at is None

    def test_wheel_cycle_has_all_required_fields(self):
        """WheelCycle has all 14 expected fields."""
        expected_fields = {
            "id",
            "state",
            "underlying",
            "put_strike",
            "put_premium_received",
            "put_expiry_date",
            "shares_held",
            "cost_basis",
            "covered_call_premiums_collected",
            "realized_pnl",
            "opened_at",
            "closed_at",
            "created_at",
            "updated_at",
        }
        cycle = WheelCycle(
            id=1,
            state=WheelState.CASH,
            underlying="IBIT",
            put_strike=None,
            put_premium_received=0.0,
            put_expiry_date=None,
            shares_held=0,
            cost_basis=0.0,
            covered_call_premiums_collected=0.0,
            realized_pnl=None,
            opened_at="2026-04-06T09:35:00",
            closed_at=None,
            created_at="2026-04-06T09:35:00",
            updated_at="2026-04-06T09:35:00",
        )
        actual_fields = set(cycle.__dataclass_fields__.keys())
        assert actual_fields == expected_fields

    def test_options_position_can_be_instantiated_with_all_fields(self):
        """OptionsPosition can be created with all fields."""
        pos = OptionsPosition(
            id=1,
            cycle_id=1,
            symbol="IBIT260515P00048000",
            option_type="PUT",
            strike=48.0,
            expiry_date="2026-05-15",
            dte_at_entry=39,
            quantity=1,
            premium_received=1.50,
            delta=-0.30,
            gamma=0.05,
            theta=-0.04,
            vega=0.10,
            iv=0.35,
            status="OPEN",
            close_premium=None,
            opened_at="2026-04-06T09:35:00",
            closed_at=None,
            created_at="2026-04-06T09:35:00",
            updated_at="2026-04-06T09:35:00",
        )
        assert pos.id == 1
        assert pos.cycle_id == 1
        assert pos.symbol == "IBIT260515P00048000"
        assert pos.option_type == "PUT"
        assert pos.strike == 48.0
        assert pos.delta == -0.30

    def test_options_position_optional_fields_accept_none(self):
        """OptionsPosition Optional fields (Greeks, close_premium, closed_at) can be None."""
        pos = OptionsPosition(
            id=1,
            cycle_id=1,
            symbol="IBIT260515P00048000",
            option_type="PUT",
            strike=48.0,
            expiry_date="2026-05-15",
            dte_at_entry=39,
            quantity=1,
            premium_received=1.50,
            delta=None,
            gamma=None,
            theta=None,
            vega=None,
            iv=None,
            status="OPEN",
            close_premium=None,
            opened_at="2026-04-06T09:35:00",
            closed_at=None,
            created_at="2026-04-06T09:35:00",
            updated_at="2026-04-06T09:35:00",
        )
        assert pos.delta is None
        assert pos.gamma is None
        assert pos.theta is None
        assert pos.vega is None
        assert pos.iv is None
        assert pos.close_premium is None
        assert pos.closed_at is None

    def test_options_position_has_all_required_fields(self):
        """OptionsPosition has all 20 expected fields."""
        expected_fields = {
            "id",
            "cycle_id",
            "symbol",
            "option_type",
            "strike",
            "expiry_date",
            "dte_at_entry",
            "quantity",
            "premium_received",
            "delta",
            "gamma",
            "theta",
            "vega",
            "iv",
            "status",
            "close_premium",
            "opened_at",
            "closed_at",
            "created_at",
            "updated_at",
        }
        pos = OptionsPosition(
            id=1,
            cycle_id=1,
            symbol="IBIT260515P00048000",
            option_type="PUT",
            strike=48.0,
            expiry_date="2026-05-15",
            dte_at_entry=39,
            quantity=1,
            premium_received=1.50,
            delta=None,
            gamma=None,
            theta=None,
            vega=None,
            iv=None,
            status="OPEN",
            close_premium=None,
            opened_at="2026-04-06T09:35:00",
            closed_at=None,
            created_at="2026-04-06T09:35:00",
            updated_at="2026-04-06T09:35:00",
        )
        actual_fields = set(pos.__dataclass_fields__.keys())
        assert actual_fields == expected_fields


class TestDatabaseSchema:
    """Tests for wheel_cycles and options_positions tables in the database schema."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create test database."""
        from src.database import Database

        db_path = tmp_path / "test_trades.db"
        return Database(db_path)

    def _get_table_columns(self, db, table_name):
        """Helper: get column info for a table via PRAGMA."""
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"PRAGMA table_info({table_name})")
            rows = cursor.fetchall()
            return {row[1]: row[2] for row in rows}  # name -> type

    def test_wheel_cycles_table_exists(self, db):
        """wheel_cycles table is created by Database._init_db()."""
        columns = self._get_table_columns(db, "wheel_cycles")
        assert len(columns) > 0, "wheel_cycles table does not exist"

    def test_wheel_cycles_has_all_columns(self, db):
        """wheel_cycles table has all 14 expected columns."""
        columns = self._get_table_columns(db, "wheel_cycles")
        expected_columns = {
            "id",
            "state",
            "underlying",
            "put_strike",
            "put_premium_received",
            "put_expiry_date",
            "shares_held",
            "cost_basis",
            "covered_call_premiums_collected",
            "realized_pnl",
            "opened_at",
            "closed_at",
            "created_at",
            "updated_at",
        }
        assert set(columns.keys()) == expected_columns

    def test_wheel_cycles_column_types(self, db):
        """wheel_cycles columns have correct SQLite types."""
        columns = self._get_table_columns(db, "wheel_cycles")
        assert columns["id"] == "INTEGER"
        assert columns["state"] == "TEXT"
        assert columns["underlying"] == "TEXT"
        assert columns["put_strike"] == "REAL"
        assert columns["put_premium_received"] == "REAL"
        assert columns["put_expiry_date"] == "TEXT"
        assert columns["shares_held"] == "INTEGER"
        assert columns["cost_basis"] == "REAL"
        assert columns["covered_call_premiums_collected"] == "REAL"
        assert columns["realized_pnl"] == "REAL"
        assert columns["opened_at"] == "TEXT"
        assert columns["closed_at"] == "TEXT"
        assert columns["created_at"] == "TEXT"
        assert columns["updated_at"] == "TEXT"

    def test_options_positions_table_exists(self, db):
        """options_positions table is created by Database._init_db()."""
        columns = self._get_table_columns(db, "options_positions")
        assert len(columns) > 0, "options_positions table does not exist"

    def test_options_positions_has_all_columns(self, db):
        """options_positions table has all 20 expected columns."""
        columns = self._get_table_columns(db, "options_positions")
        expected_columns = {
            "id",
            "cycle_id",
            "symbol",
            "option_type",
            "strike",
            "expiry_date",
            "dte_at_entry",
            "quantity",
            "premium_received",
            "delta",
            "gamma",
            "theta",
            "vega",
            "iv",
            "status",
            "close_premium",
            "opened_at",
            "closed_at",
            "created_at",
            "updated_at",
        }
        assert set(columns.keys()) == expected_columns

    def test_options_positions_column_types(self, db):
        """options_positions columns have correct SQLite types."""
        columns = self._get_table_columns(db, "options_positions")
        assert columns["id"] == "INTEGER"
        assert columns["cycle_id"] == "INTEGER"
        assert columns["symbol"] == "TEXT"
        assert columns["option_type"] == "TEXT"
        assert columns["strike"] == "REAL"
        assert columns["expiry_date"] == "TEXT"
        assert columns["dte_at_entry"] == "INTEGER"
        assert columns["quantity"] == "INTEGER"
        assert columns["premium_received"] == "REAL"
        assert columns["delta"] == "REAL"
        assert columns["gamma"] == "REAL"
        assert columns["theta"] == "REAL"
        assert columns["vega"] == "REAL"
        assert columns["iv"] == "REAL"
        assert columns["status"] == "TEXT"
        assert columns["close_premium"] == "REAL"
        assert columns["opened_at"] == "TEXT"
        assert columns["closed_at"] == "TEXT"
        assert columns["created_at"] == "TEXT"
        assert columns["updated_at"] == "TEXT"

    def test_idempotent_init(self, tmp_path):
        """Creating Database twice on same path does not error (CREATE TABLE IF NOT EXISTS)."""
        from src.database import Database

        db_path = tmp_path / "test_trades.db"
        db1 = Database(db_path)
        db2 = Database(db_path)  # Should not raise
        # Both should have same tables
        columns1 = self._get_table_columns(db1, "wheel_cycles")
        columns2 = self._get_table_columns(db2, "wheel_cycles")
        assert set(columns1.keys()) == set(columns2.keys())

    def test_backward_compatible_existing_tables_still_work(self, db):
        """Existing tables (trades, bot_state, logs, etc.) still work after adding new tables."""
        # bot_state should still exist and be initialized
        state = db.get_bot_state()
        assert state is not None
        assert "is_paused" in state


# ==================== Plan 02-02: CRUD Methods ====================


class TestWheelCycleCRUD:
    """Tests for create_wheel_cycle, get_active_cycle, get_cycle_history."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create test database."""
        from src.database import Database

        db_path = tmp_path / "test_trades.db"
        return Database(db_path)

    def test_create_wheel_cycle_returns_positive_int(self, db):
        """create_wheel_cycle() returns an integer cycle_id > 0."""
        cycle_id = db.create_wheel_cycle()
        assert isinstance(cycle_id, int)
        assert cycle_id > 0

    def test_create_wheel_cycle_sets_defaults(self, db):
        """create_wheel_cycle() sets state='CASH', underlying='IBIT', and opened_at."""
        cycle_id = db.create_wheel_cycle()
        cycle = db.get_active_cycle()
        assert cycle is not None
        assert cycle["state"] == "CASH"
        assert cycle["underlying"] == "IBIT"
        assert cycle["opened_at"] is not None

    def test_create_wheel_cycle_raises_when_active_exists(self, db):
        """create_wheel_cycle() raises ValueError if an active cycle already exists."""
        db.create_wheel_cycle()
        with pytest.raises(ValueError, match="Active cycle already exists"):
            db.create_wheel_cycle()

    def test_get_active_cycle_returns_none_when_no_cycle(self, db):
        """get_active_cycle() returns None when no active cycle exists."""
        result = db.get_active_cycle()
        assert result is None

    def test_get_active_cycle_returns_dict_with_all_columns(self, db):
        """get_active_cycle() returns dict with all wheel_cycles columns."""
        db.create_wheel_cycle()
        cycle = db.get_active_cycle()
        assert cycle is not None
        expected_keys = {
            "id", "state", "underlying", "put_strike", "put_premium_received",
            "put_expiry_date", "shares_held", "cost_basis",
            "covered_call_premiums_collected", "realized_pnl",
            "opened_at", "closed_at", "created_at", "updated_at",
        }
        assert set(cycle.keys()) == expected_keys

    def test_get_active_cycle_returns_unclosed_cycle(self, db):
        """get_active_cycle() returns a cycle where closed_at IS NULL."""
        db.create_wheel_cycle()
        cycle = db.get_active_cycle()
        assert cycle is not None
        assert cycle["closed_at"] is None

    def test_get_cycle_history_returns_all_cycles_ordered_desc(self, db):
        """get_cycle_history() returns all cycles (including closed) ordered by id DESC."""
        # Create and close a cycle
        cycle_id = db.create_wheel_cycle()
        from src.wheel_state import WheelState
        db.transition_wheel_state(cycle_id, WheelState.SHORT_PUT, reason="sold put")
        db.transition_wheel_state(cycle_id, WheelState.CASH, reason="put expired", realized_pnl=150.0)
        # Create a second cycle
        db.create_wheel_cycle()
        history = db.get_cycle_history()
        assert isinstance(history, list)
        assert len(history) == 2
        # Most recent should be first (id DESC)
        assert history[0]["id"] > history[1]["id"]


class TestTransitionWheelState:
    """Tests for transition_wheel_state() database method."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create test database."""
        from src.database import Database

        db_path = tmp_path / "test_trades.db"
        return Database(db_path)

    @pytest.fixture
    def active_cycle(self, db):
        """Create an active cycle and return its id."""
        return db.create_wheel_cycle()

    def test_transition_updates_state_column(self, db, active_cycle):
        """transition_wheel_state() updates the state column in wheel_cycles."""
        from src.wheel_state import WheelState
        db.transition_wheel_state(active_cycle, WheelState.SHORT_PUT, reason="sold put")
        cycle = db.get_active_cycle()
        assert cycle["state"] == "SHORT_PUT"

    def test_invalid_transition_raises_value_error(self, db, active_cycle):
        """transition_wheel_state() raises ValueError for invalid transitions."""
        from src.wheel_state import WheelState
        with pytest.raises(ValueError):
            # CASH -> HOLDING_SHARES is invalid
            db.transition_wheel_state(active_cycle, WheelState.HOLDING_SHARES, reason="bad")

    def test_wrong_cycle_id_raises_value_error(self, db, active_cycle):
        """transition_wheel_state() raises ValueError if cycle_id doesn't match active cycle."""
        from src.wheel_state import WheelState
        with pytest.raises(ValueError):
            db.transition_wheel_state(99999, WheelState.SHORT_PUT, reason="wrong id")

    def test_transition_to_cash_sets_closed_at(self, db, active_cycle):
        """transition_wheel_state to CASH sets closed_at timestamp."""
        from src.wheel_state import WheelState
        db.transition_wheel_state(active_cycle, WheelState.SHORT_PUT, reason="sold put")
        db.transition_wheel_state(active_cycle, WheelState.CASH, reason="expired")
        # Get cycle from history (it's now closed)
        history = db.get_cycle_history(limit=1)
        assert history[0]["closed_at"] is not None

    def test_transition_logs_wheel_transition_event(self, db, active_cycle):
        """transition_wheel_state() logs to logs table with event='wheel_transition'."""
        from src.wheel_state import WheelState
        db.transition_wheel_state(active_cycle, WheelState.SHORT_PUT, reason="sold put")
        logs = db.get_logs(limit=10)
        wheel_logs = [l for l in logs if l["event"] == "wheel_transition"]
        assert len(wheel_logs) >= 1

    def test_transition_accepts_kwargs_to_update_additional_fields(self, db, active_cycle):
        """transition_wheel_state accepts **updates to set extra fields like put_strike."""
        from src.wheel_state import WheelState
        db.transition_wheel_state(
            active_cycle,
            WheelState.SHORT_PUT,
            reason="sold put",
            put_strike=48.0,
            put_premium_received=2.50,
        )
        cycle = db.get_active_cycle()
        assert cycle["put_strike"] == 48.0
        assert cycle["put_premium_received"] == 2.50


class TestOptionsPositions:
    """Tests for open_wheel_position, close_wheel_position, get_cycle_positions."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create test database."""
        from src.database import Database

        db_path = tmp_path / "test_trades.db"
        return Database(db_path)

    @pytest.fixture
    def cycle_id(self, db):
        """Create an active cycle and return its id."""
        return db.create_wheel_cycle()

    def _open_put(self, db, cycle_id, **kwargs):
        """Helper to open a put position with defaults."""
        defaults = dict(
            cycle_id=cycle_id,
            symbol="IBIT260515P00048000",
            option_type="PUT",
            strike=48.0,
            expiry_date="2026-05-15",
            dte_at_entry=39,
            premium_received=1.50,
        )
        defaults.update(kwargs)
        return db.open_wheel_position(**defaults)

    def test_open_wheel_position_returns_positive_int(self, db, cycle_id):
        """open_wheel_position() returns an integer position_id > 0."""
        pos_id = self._open_put(db, cycle_id)
        assert isinstance(pos_id, int)
        assert pos_id > 0

    def test_open_wheel_position_stores_greeks_individually(self, db, cycle_id):
        """open_wheel_position() stores Greeks as individual columns, not JSON."""
        pos_id = db.open_wheel_position(
            cycle_id=cycle_id,
            symbol="IBIT260515P00048000",
            option_type="PUT",
            strike=48.0,
            expiry_date="2026-05-15",
            dte_at_entry=39,
            premium_received=1.50,
            delta=-0.30,
            gamma=0.05,
            theta=-0.04,
            vega=0.10,
            iv=0.35,
        )
        positions = db.get_cycle_positions(cycle_id)
        pos = positions[0]
        assert pos["delta"] == -0.30
        assert pos["gamma"] == 0.05
        assert pos["theta"] == -0.04
        assert pos["vega"] == 0.10
        assert pos["iv"] == 0.35

    def test_open_wheel_position_validates_option_type(self, db, cycle_id):
        """open_wheel_position() raises ValueError for invalid option_type."""
        with pytest.raises(ValueError, match="option_type"):
            db.open_wheel_position(
                cycle_id=cycle_id,
                symbol="IBIT260515X00048000",
                option_type="STRADDLE",
                strike=48.0,
                expiry_date="2026-05-15",
                dte_at_entry=39,
                premium_received=1.50,
            )

    def test_open_wheel_position_validates_cycle_id_exists(self, db):
        """open_wheel_position() raises ValueError if cycle_id doesn't exist."""
        with pytest.raises(ValueError, match="cycle_id"):
            db.open_wheel_position(
                cycle_id=99999,
                symbol="IBIT260515P00048000",
                option_type="PUT",
                strike=48.0,
                expiry_date="2026-05-15",
                dte_at_entry=39,
                premium_received=1.50,
            )

    def test_get_cycle_positions_returns_list(self, db, cycle_id):
        """get_cycle_positions() returns a list of dicts for all positions in cycle."""
        self._open_put(db, cycle_id)
        self._open_put(db, cycle_id, symbol="IBIT260615C00052000", option_type="CALL")
        positions = db.get_cycle_positions(cycle_id)
        assert isinstance(positions, list)
        assert len(positions) == 2

    def test_close_wheel_position_sets_status_and_timestamp(self, db, cycle_id):
        """close_wheel_position() sets status='CLOSED' and closed_at timestamp."""
        pos_id = self._open_put(db, cycle_id)
        db.close_wheel_position(pos_id, close_premium=0.05)
        positions = db.get_cycle_positions(cycle_id)
        pos = positions[0]
        assert pos["status"] == "CLOSED"
        assert pos["closed_at"] is not None
        assert pos["close_premium"] == 0.05

    def test_close_wheel_position_does_not_delete_row(self, db, cycle_id):
        """close_wheel_position does not delete the row (positions are never deleted)."""
        pos_id = self._open_put(db, cycle_id)
        db.close_wheel_position(pos_id, close_premium=0.05)
        positions = db.get_cycle_positions(cycle_id)
        # Row still exists
        assert len(positions) == 1
        assert positions[0]["id"] == pos_id


class TestCostBasis:
    """Tests for cost basis calculation and P&L computation."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create test database."""
        from src.database import Database

        db_path = tmp_path / "test_trades.db"
        return Database(db_path)

    @pytest.fixture
    def cycle_id(self, db):
        """Create an active cycle and return its id."""
        return db.create_wheel_cycle()

    def test_assignment_sets_cost_basis_strike_minus_premium(self, db, cycle_id):
        """After assignment, cost_basis = put_strike - put_premium_received (per share)."""
        from src.wheel_state import WheelState
        db.transition_wheel_state(cycle_id, WheelState.SHORT_PUT, reason="sold put",
                                   put_strike=50.0, put_premium_received=2.50)
        # Caller provides cost_basis on assignment
        cost_basis = 50.0 - 2.50  # = 47.50
        db.transition_wheel_state(cycle_id, WheelState.HOLDING_SHARES, reason="assigned",
                                   shares_held=100, cost_basis=cost_basis)
        cycle = db.get_active_cycle()
        assert cycle["cost_basis"] == pytest.approx(47.50)

    def test_covered_call_premium_reduces_cost_basis(self, db, cycle_id):
        """After collecting covered call premium, cost_basis decreases."""
        from src.wheel_state import WheelState
        db.transition_wheel_state(cycle_id, WheelState.SHORT_PUT, reason="sold put",
                                   put_strike=50.0, put_premium_received=2.50)
        db.transition_wheel_state(cycle_id, WheelState.HOLDING_SHARES, reason="assigned",
                                   shares_held=100, cost_basis=47.50)
        # Record covered call premium reduces cost_basis
        new_cost_basis = 47.50 - 1.50  # = 46.00
        new_cc_premiums = 1.50
        db.transition_wheel_state(cycle_id, WheelState.COVERED_CALL, reason="sold call",
                                   cost_basis=new_cost_basis,
                                   covered_call_premiums_collected=new_cc_premiums)
        cycle = db.get_active_cycle()
        assert cycle["cost_basis"] == pytest.approx(46.00)

    def test_second_covered_call_accumulates_premiums(self, db, cycle_id):
        """After second covered call, cost_basis decreases further and premiums accumulate."""
        from src.wheel_state import WheelState
        db.transition_wheel_state(cycle_id, WheelState.SHORT_PUT, reason="sold put",
                                   put_strike=50.0, put_premium_received=2.50)
        db.transition_wheel_state(cycle_id, WheelState.HOLDING_SHARES, reason="assigned",
                                   shares_held=100, cost_basis=47.50)
        db.transition_wheel_state(cycle_id, WheelState.COVERED_CALL, reason="sold call 1",
                                   cost_basis=46.00, covered_call_premiums_collected=1.50)
        # Call expires, go back to HOLDING_SHARES, then sell another call
        db.transition_wheel_state(cycle_id, WheelState.HOLDING_SHARES, reason="call expired")
        db.transition_wheel_state(cycle_id, WheelState.COVERED_CALL, reason="sold call 2",
                                   cost_basis=45.00, covered_call_premiums_collected=2.50)
        cycle = db.get_active_cycle()
        assert cycle["cost_basis"] == pytest.approx(45.00)
        assert cycle["covered_call_premiums_collected"] == pytest.approx(2.50)

    def test_compute_cycle_pnl_holding_shares(self, db, cycle_id):
        """compute_cycle_pnl with HOLDING_SHARES returns unrealized_pnl = (price - cost_basis) * shares."""
        from src.wheel_state import WheelState
        db.transition_wheel_state(cycle_id, WheelState.SHORT_PUT, reason="sold put",
                                   put_strike=50.0, put_premium_received=2.50)
        db.transition_wheel_state(cycle_id, WheelState.HOLDING_SHARES, reason="assigned",
                                   shares_held=100, cost_basis=47.50)
        cycle = db.get_active_cycle()
        pnl = db.compute_cycle_pnl(cycle, current_price=50.0)
        assert pnl["unrealized_pnl"] == pytest.approx(250.0)  # (50.0 - 47.50) * 100

    def test_compute_cycle_pnl_cash_state(self, db, cycle_id):
        """compute_cycle_pnl with CASH state returns unrealized_pnl=0 and realized_pnl from cycle."""
        from src.wheel_state import WheelState
        db.transition_wheel_state(cycle_id, WheelState.SHORT_PUT, reason="sold put",
                                   put_premium_received=2.50)
        db.transition_wheel_state(cycle_id, WheelState.CASH, reason="put expired",
                                   realized_pnl=250.0)
        history = db.get_cycle_history(limit=1)
        cycle = history[0]
        pnl = db.compute_cycle_pnl(cycle, current_price=0.0)
        assert pnl["unrealized_pnl"] == pytest.approx(0.0)
        assert pnl["realized_pnl"] == pytest.approx(250.0)

    def test_put_expires_worthless_realized_pnl(self, db, cycle_id):
        """Put expires worthless: cycle transitions to CASH with realized_pnl = premium * 100."""
        from src.wheel_state import WheelState
        db.transition_wheel_state(cycle_id, WheelState.SHORT_PUT, reason="sold put",
                                   put_premium_received=2.50)
        # Caller computes realized_pnl and passes it via **updates
        realized = 2.50 * 100  # = 250.0
        db.transition_wheel_state(cycle_id, WheelState.CASH, reason="put expired worthless",
                                   realized_pnl=realized)
        history = db.get_cycle_history(limit=1)
        cycle = history[0]
        assert cycle["realized_pnl"] == pytest.approx(250.0)
