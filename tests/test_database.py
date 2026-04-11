"""
Unit tests for IBIT Dip Bot database module.
"""

import sys
from datetime import date, datetime
from pathlib import Path

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database import Database


class TestDatabase:
    """Test Database class."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create test database."""
        db_path = tmp_path / "test_trades.db"
        return Database(db_path)

    def test_database_initialization(self, db):
        """Test database initializes with tables."""
        # If we get here without error, tables were created
        state = db.get_bot_state()
        assert state is not None
        assert "is_paused" in state

    def test_record_trade_entry(self, db):
        """Test recording a trade entry."""
        trade_id = db.record_trade_entry(
            date=date(2024, 6, 15),
            day_of_week="Tuesday",
            open_price=50.0,
            entry_price=49.5,
            dip_percentage=1.0,
            shares=100,
            is_dry_run=True,
            notes="Test trade",
        )

        assert trade_id > 0

        # Verify trade was recorded
        open_trade = db.get_open_trade()
        assert open_trade is not None
        assert open_trade["id"] == trade_id
        assert open_trade["shares"] == 100
        assert open_trade["status"] == "open"

    def test_record_trade_exit(self, db):
        """Test recording a trade exit."""
        # First create entry
        trade_id = db.record_trade_entry(
            date=date(2024, 6, 15),
            day_of_week="Tuesday",
            open_price=50.0,
            entry_price=49.5,
            dip_percentage=1.0,
            shares=100,
        )

        # Then record exit
        db.record_trade_exit(
            trade_id=trade_id, exit_price=50.5, dollar_pnl=100.0, percentage_pnl=2.02
        )

        # Verify no open trade
        open_trade = db.get_open_trade()
        assert open_trade is None

        # Verify trade history
        trades = db.get_trade_history(limit=10)
        assert len(trades) == 1
        assert trades[0]["status"] == "closed"
        assert trades[0]["dollar_pnl"] == 100.0

    def test_get_trade_statistics(self, db):
        """Test getting trade statistics."""
        # Create some trades
        for i in range(5):
            trade_id = db.record_trade_entry(
                date=date(2024, 6, 15 + i),
                day_of_week="Tuesday",
                open_price=50.0,
                entry_price=49.5,
                dip_percentage=1.0,
                shares=100,
            )

            # Alternate wins and losses
            pnl = 50.0 if i % 2 == 0 else -25.0
            pct_pnl = 1.0 if i % 2 == 0 else -0.5

            db.record_trade_exit(
                trade_id=trade_id,
                exit_price=50.0 + (pnl / 100),
                dollar_pnl=pnl,
                percentage_pnl=pct_pnl,
            )

        stats = db.get_trade_statistics()

        assert stats["total_trades"] == 5
        assert stats["winning_trades"] == 3
        assert stats["losing_trades"] == 2
        assert stats["win_rate"] == 60.0

    def test_bot_state_operations(self, db):
        """Test bot state operations."""
        # Initial state
        state = db.get_bot_state()
        assert state["is_paused"] == 0

        # Update state
        db.update_bot_state(is_paused=1)

        state = db.get_bot_state()
        assert state["is_paused"] == 1

        # Set paused with until date
        future_date = datetime(2024, 6, 20, 9, 30)
        db.set_paused(True, future_date)

        state = db.get_bot_state()
        assert state["is_paused"] == 1
        assert state["pause_until"] == future_date.isoformat()

    def test_position_tracking(self, db):
        """Test position tracking in bot state."""
        # Set position
        db.set_position(100, 49.5, date(2024, 6, 15))

        state = db.get_bot_state()
        assert state["current_position_shares"] == 100
        assert state["current_position_entry_price"] == 49.5

        # Clear position
        db.clear_position()

        state = db.get_bot_state()
        assert state["current_position_shares"] == 0
        assert state["current_position_entry_price"] is None

    def test_daily_price_operations(self, db):
        """Test daily price storage and retrieval."""
        test_date = date(2024, 6, 15)
        test_price = 50.25

        db.store_open_price(test_date, test_price)

        # Retrieve price
        price = db.get_open_price(test_date)
        assert price == test_price

        # Non-existent date
        price = db.get_open_price(date(2024, 1, 1))
        assert price is None

    def test_logging_operations(self, db):
        """Test logging operations."""
        # Log some events
        db.log_event("INFO", "Test event 1", {"key": "value1"})
        db.log_event("WARNING", "Test event 2", {"key": "value2"})
        db.log_event("ERROR", "Test event 3")

        # Get all logs
        logs = db.get_logs(limit=10)
        assert len(logs) == 3

        # Get by level
        error_logs = db.get_logs(level="ERROR")
        assert len(error_logs) == 1
        assert error_logs[0]["event"] == "Test event 3"

    def test_equity_curve(self, db):
        """Test equity curve generation."""
        # Create trades with cumulative P&L
        cumulative = 0
        for i in range(5):
            trade_id = db.record_trade_entry(
                date=date(2024, 6, 15 + i),
                day_of_week="Tuesday",
                open_price=50.0,
                entry_price=49.5,
                dip_percentage=1.0,
                shares=100,
            )

            pnl = 50.0 * (i + 1)  # Increasing P&L
            cumulative += pnl

            db.record_trade_exit(
                trade_id=trade_id,
                exit_price=50.0 + (pnl / 100),
                dollar_pnl=pnl,
                percentage_pnl=pnl / 49.5,
            )

        curve = db.get_equity_curve()

        assert len(curve) == 5
        assert curve[-1]["cumulative_pnl"] == sum(50.0 * (i + 1) for i in range(5))


class TestDatabaseConcurrency:
    """Test database concurrency handling."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create test database."""
        db_path = tmp_path / "test_concurrent.db"
        return Database(db_path)

    def test_multiple_connections(self, db, tmp_path):
        """Test that multiple database instances work correctly."""
        db_path = tmp_path / "test_concurrent.db"

        # Create second connection
        db2 = Database(db_path)

        # Write from first connection
        db.log_event("INFO", "From db1")

        # Read from second connection
        logs = db2.get_logs()
        assert len(logs) == 1
        assert logs[0]["event"] == "From db1"

    def test_concurrent_writes_from_multiple_threads(self, tmp_path):
        """ME-08: actual concurrent writes from threads must not deadlock or
        corrupt data.

        The previous test_multiple_connections was sequential — it wrote from
        one Database, then read from another. It did NOT exercise the path
        where two threads contend on the same SQLite file and one of them
        hits "database is locked". This test spawns N threads and has each
        one insert M log events. After all threads finish, every event must
        be present in the database exactly once and all threads must have
        finished without raising.
        """
        import threading
        from concurrent.futures import ThreadPoolExecutor, as_completed

        db_path = tmp_path / "concurrent_writes.db"

        # Pre-create the schema from the main thread; each writer thread
        # will open its own Database instance against the same path.
        Database(db_path)

        thread_count = 8
        events_per_thread = 25
        errors: list[BaseException] = []
        errors_lock = threading.Lock()

        def writer(thread_id: int) -> None:
            try:
                writer_db = Database(db_path)
                for i in range(events_per_thread):
                    writer_db.log_event(
                        "INFO",
                        f"t{thread_id}_e{i}",
                        {"thread_id": thread_id, "seq": i},
                    )
            except BaseException as exc:
                with errors_lock:
                    errors.append(exc)

        with ThreadPoolExecutor(max_workers=thread_count) as executor:
            futures = [executor.submit(writer, tid) for tid in range(thread_count)]
            for future in as_completed(futures):
                future.result()  # re-raises any per-thread exception

        assert not errors, f"Writer threads raised: {errors}"

        # Verify no data corruption: every expected event is present once.
        reader = Database(db_path)
        logs = reader.get_logs(limit=thread_count * events_per_thread + 10)
        events = {log["event"] for log in logs}
        expected = {f"t{tid}_e{i}" for tid in range(thread_count) for i in range(events_per_thread)}
        missing = expected - events
        assert not missing, f"Missing {len(missing)} events after concurrent writes: {missing}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
