"""
Tests for TR-01: wheel_mode_enabled database column and intraday job gating.

When wheel_mode_enabled=1 in bot_state, all intraday jobs skip execution.
When wheel_mode_enabled=0, intraday jobs run normally.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database import Database


class TestWheelModeDatabase:
    """Tests for wheel_mode_enabled column in bot_state."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create a fresh test database."""
        db_path = tmp_path / "test.db"
        return Database(db_path)

    def test_wheel_mode_enabled_default_on(self, db):
        """Fresh DB has wheel_mode_enabled=1 in bot_state."""
        state = db.get_bot_state()
        assert state.get("wheel_mode_enabled") == 1

    def test_wheel_mode_toggle(self, db):
        """update_bot_state(wheel_mode_enabled=0) persists and reads back correctly."""
        # Disable wheel mode
        db.update_bot_state(wheel_mode_enabled=0)
        state = db.get_bot_state()
        assert state.get("wheel_mode_enabled") == 0

        # Re-enable wheel mode
        db.update_bot_state(wheel_mode_enabled=1)
        state = db.get_bot_state()
        assert state.get("wheel_mode_enabled") == 1


class TestWheelModeSchedulerGating:
    """Tests for wheel mode gate in SmartScheduler intraday jobs."""

    def _make_scheduler(self, wheel_mode_enabled):
        """Build a minimal SmartScheduler mock for testing job gating."""
        from src.smart_scheduler import SmartScheduler

        scheduler = object.__new__(SmartScheduler)

        # Mock db
        scheduler.db = MagicMock()
        scheduler.db.get_bot_state.return_value = {"wheel_mode_enabled": wheel_mode_enabled}

        # Mock bot
        scheduler.bot = MagicMock()
        scheduler.bot.strategy = MagicMock()
        scheduler.bot._position_lock = MagicMock()
        scheduler.bot._position_lock.__enter__ = MagicMock(return_value=None)
        scheduler.bot._position_lock.__exit__ = MagicMock(return_value=False)
        scheduler.bot.is_paper_mode = True
        scheduler.bot._paper_positions = {}

        # Mock telegram_bot
        scheduler.telegram_bot = MagicMock()

        # Mock helpers
        scheduler._last_result = None
        scheduler._error_count = 0
        scheduler._log_signal_check = MagicMock()
        scheduler._send_notification = MagicMock()
        scheduler._send_error_notification = MagicMock()

        return scheduler

    @patch("src.smart_scheduler.is_trading_day", return_value=True)
    @patch("src.smart_scheduler.get_et_now")
    def test_intraday_gated_when_wheel_mode_on(self, mock_now, mock_trading_day):
        """When wheel_mode_enabled=1, _job_morning_signal returns early without calling get_today_signal."""
        from datetime import datetime
        mock_now.return_value = MagicMock()

        scheduler = self._make_scheduler(wheel_mode_enabled=1)
        scheduler._job_morning_signal()

        # Core intraday logic should NOT be reached
        scheduler.bot.get_today_signal.assert_not_called()
        scheduler.bot.execute_signal.assert_not_called()

    @patch("src.smart_scheduler.is_trading_day", return_value=True)
    @patch("src.smart_scheduler.get_et_now")
    def test_intraday_runs_when_wheel_mode_off(self, mock_now, mock_trading_day):
        """When wheel_mode_enabled=0, _job_morning_signal proceeds to call get_today_signal."""
        mock_now.return_value = MagicMock()

        scheduler = self._make_scheduler(wheel_mode_enabled=0)
        # Make get_today_signal return a mock signal so execution doesn't blow up
        from src.smart_strategy import Signal
        mock_signal = MagicMock()
        mock_signal.signal = Signal.CASH
        scheduler.bot.get_today_signal.return_value = mock_signal

        execute_result = MagicMock()
        execute_result.success = True
        execute_result.signal = Signal.CASH
        scheduler.bot.execute_signal.return_value = execute_result

        scheduler._job_morning_signal()

        # Core intraday logic SHOULD be reached
        scheduler.bot.get_today_signal.assert_called_once()

    @pytest.mark.parametrize("method_name,setup_attr,setup_value", [
        ("_job_morning_signal", None, None),
        ("_job_crash_day_check", None, None),
        ("_job_pump_day_check", None, None),
        ("_job_ten_am_dump_exit", "_ten_am_dump_position_open", True),
    ])
    @patch("src.smart_scheduler.is_trading_day", return_value=True)
    @patch("src.smart_scheduler.get_et_now")
    def test_all_four_intraday_jobs_gated(
        self, mock_now, mock_trading_day, method_name, setup_attr, setup_value
    ):
        """All 4 intraday jobs skip their core logic when wheel_mode_enabled=1."""
        mock_now.return_value = MagicMock()

        scheduler = self._make_scheduler(wheel_mode_enabled=1)

        if setup_attr:
            setattr(scheduler.bot.strategy, setup_attr, setup_value)

        job = getattr(scheduler, method_name)
        job()

        # None of the jobs should call bot methods that touch signal logic or positions
        scheduler.bot.get_today_signal.assert_not_called()
        scheduler.bot.strategy.get_today_signal.assert_not_called()
        scheduler.bot.execute_signal.assert_not_called()
        scheduler.bot.close_position.assert_not_called()

    @patch("src.smart_scheduler.is_trading_day", return_value=True)
    @patch("src.smart_scheduler.get_et_now")
    def test_daily_summary_gated(self, mock_now, mock_trading_day):
        """When wheel_mode_enabled=1, _job_daily_summary does not send intraday summary."""
        mock_now.return_value = MagicMock()
        mock_now.return_value.strftime.return_value = "2026-04-08"

        scheduler = self._make_scheduler(wheel_mode_enabled=1)
        # Also wire up db.get_events to return empty to avoid attribute errors if gate fails
        scheduler.db.get_events.return_value = []

        scheduler._job_daily_summary()

        # telegram_bot.send_daily_summary should NOT be called
        scheduler.telegram_bot.send_daily_summary.assert_not_called()
