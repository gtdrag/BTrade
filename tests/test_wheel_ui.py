"""
Tests for TR-01: wheel_mode_enabled database column and intraday job gating.

When wheel_mode_enabled=1 in bot_state, all intraday jobs skip execution.
When wheel_mode_enabled=0, intraday jobs run normally.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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

    def test_wheel_mode_toggle_idempotent(self, db):
        """LO-05: setting wheel_mode to its current value is a no-op and does not raise."""
        # Fresh DB starts with wheel_mode_enabled=1. Re-setting to 1 should be
        # idempotent (this guards against future DB changes that might reject
        # no-op updates or emit unintended side-effects).
        db.update_bot_state(wheel_mode_enabled=1)
        db.update_bot_state(wheel_mode_enabled=1)
        assert db.get_bot_state().get("wheel_mode_enabled") == 1

        db.update_bot_state(wheel_mode_enabled=0)
        db.update_bot_state(wheel_mode_enabled=0)
        assert db.get_bot_state().get("wheel_mode_enabled") == 0


class TestWheelModeSchedulerGating:
    """Tests for wheel mode gate in SmartScheduler intraday jobs."""

    def _make_scheduler(self, wheel_mode_enabled):
        """Build a SmartScheduler via the shared conftest factory (HI-07)."""
        from tests.conftest import make_smart_scheduler

        scheduler = make_smart_scheduler(telegram_bot=MagicMock())
        scheduler.db = MagicMock()
        scheduler.db.get_bot_state.return_value = {"wheel_mode_enabled": wheel_mode_enabled}

        # The gating tests need scheduler.bot to be a plain MagicMock the
        # tests can poke at with assert_not_called — the factory's mock_bot
        # is already a MagicMock but we replace it for the position-lock
        # context manager setup.
        scheduler.bot = MagicMock()
        scheduler.bot.strategy = MagicMock()
        scheduler.bot._position_lock = MagicMock()
        scheduler.bot._position_lock.__enter__ = MagicMock(return_value=None)
        scheduler.bot._position_lock.__exit__ = MagicMock(return_value=False)
        scheduler.bot.is_paper_mode = True
        scheduler.bot._paper_positions = {}

        scheduler._log_signal_check = MagicMock()
        scheduler._send_error_notification = MagicMock()

        return scheduler

    @patch("src.smart_scheduler.is_trading_day", return_value=True)
    @patch("src.smart_scheduler.get_et_now")
    def test_intraday_gated_when_wheel_mode_on(self, mock_now, mock_trading_day):
        """When wheel_mode_enabled=1, _job_morning_signal returns early without calling get_today_signal."""
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

    @pytest.mark.parametrize(
        "method_name,setup_attr,setup_value",
        [
            ("_job_morning_signal", None, None),
            ("_job_crash_day_check", None, None),
            ("_job_pump_day_check", None, None),
            ("_job_ten_am_dump_exit", "_ten_am_dump_position_open", True),
        ],
    )
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


# ---------------------------------------------------------------------------
# Task 1 tests: WheelCommandsMixin (/wheel and /wheelmode)
# ---------------------------------------------------------------------------


def _make_update(authorized=True):
    """Build a mock Telegram Update with reply_text as AsyncMock."""
    update = MagicMock()
    update.effective_chat.id = 12345
    update.message.reply_text = AsyncMock()
    return update


def _make_telegram_bot(wheel_mode_enabled=0, active_cycle=None, positions=None):
    """Build a minimal TelegramBot-like object with WheelCommandsMixin attached."""
    from src.telegram.wheel_commands import WheelCommandsMixin

    class FakeBot(WheelCommandsMixin):
        def _is_authorized(self, update):
            return getattr(self, "_auth_result", True)

        async def _send_unauthorized_response(self, update):
            await update.message.reply_text("Unauthorized")

    bot = FakeBot()
    bot._auth_result = True
    return bot


class TestWheelCommand:
    """Tests for /wheel Telegram command (TR-02)."""

    @pytest.mark.asyncio
    @patch("src.telegram.wheel_commands.get_database")
    async def test_wheel_cmd_no_cycle(self, mock_get_db):
        """_cmd_wheel with no active cycle replies with 'No active wheel cycle.'"""
        mock_db = MagicMock()
        mock_db.get_active_cycle.return_value = None
        mock_get_db.return_value = mock_db

        bot = _make_telegram_bot()
        update = _make_update()
        ctx = MagicMock()

        await bot._cmd_wheel(update, ctx)

        update.message.reply_text.assert_called_once()
        call_args = update.message.reply_text.call_args[0][0]
        assert "No active wheel cycle." in call_args

    @pytest.mark.asyncio
    @patch("src.telegram.wheel_commands.get_et_now")
    @patch("src.telegram.wheel_commands.get_database")
    async def test_wheel_cmd_active_cycle(self, mock_get_db, mock_now):
        """_cmd_wheel with active cycle shows state, cost basis, DTE, premium."""
        from datetime import date

        mock_db = MagicMock()
        mock_db.get_active_cycle.return_value = {
            "id": 1,
            "state": "SHORT_PUT",
            "cost_basis": 52.50,
            "put_premium_received": 1.20,
            "covered_call_premiums_collected": 0.0,
        }
        mock_db.get_cycle_positions.return_value = [
            {
                "status": "OPEN",
                "option_type": "PUT",
                "strike": 53.0,
                "expiry_date": "2026-05-15",
                "premium_received": 1.20,
                "delta": -0.25,
                "current_value": None,
            }
        ]
        mock_get_db.return_value = mock_db
        # Return a fixed "today" so DTE is deterministic
        mock_now.return_value = MagicMock()
        mock_now.return_value.date.return_value = date(2026, 4, 8)

        bot = _make_telegram_bot()
        update = _make_update()
        ctx = MagicMock()

        await bot._cmd_wheel(update, ctx)

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "SHORT_PUT" in text
        assert "$52.50" in text
        assert "DTE" in text
        assert "$1.20" in text

    @pytest.mark.asyncio
    @patch("src.telegram.wheel_commands.get_database")
    async def test_wheel_cmd_unauthorized(self, mock_get_db):
        """Unauthorized user is rejected before any DB access."""
        bot = _make_telegram_bot()
        bot._auth_result = False
        update = _make_update()
        ctx = MagicMock()

        await bot._cmd_wheel(update, ctx)

        # DB should NOT be touched
        mock_get_db.assert_not_called()
        # Unauthorized response was sent
        update.message.reply_text.assert_called_once()
        assert "Unauthorized" in update.message.reply_text.call_args[0][0]


class TestWheelModeCommand:
    """Tests for /wheelmode Telegram command (TR-03)."""

    @pytest.mark.asyncio
    @patch("src.telegram.wheel_commands.get_database")
    async def test_wheelmode_on(self, mock_get_db):
        """/wheelmode on calls update_bot_state(wheel_mode_enabled=1) and replies ENABLED."""
        mock_db = MagicMock()
        mock_db.get_bot_state.return_value = {"wheel_mode_enabled": 0}
        mock_get_db.return_value = mock_db

        bot = _make_telegram_bot()
        update = _make_update()
        ctx = MagicMock()
        ctx.args = ["on"]

        await bot._cmd_wheelmode(update, ctx)

        mock_db.update_bot_state.assert_called_once_with(wheel_mode_enabled=1)
        text = update.message.reply_text.call_args[0][0]
        assert "ENABLED" in text

    @pytest.mark.asyncio
    @patch("src.telegram.wheel_commands.get_database")
    async def test_wheelmode_off(self, mock_get_db):
        """/wheelmode off calls update_bot_state(wheel_mode_enabled=0) and replies DISABLED."""
        mock_db = MagicMock()
        mock_db.get_bot_state.return_value = {"wheel_mode_enabled": 1}
        mock_get_db.return_value = mock_db

        bot = _make_telegram_bot()
        update = _make_update()
        ctx = MagicMock()
        ctx.args = ["off"]

        await bot._cmd_wheelmode(update, ctx)

        mock_db.update_bot_state.assert_called_once_with(wheel_mode_enabled=0)
        text = update.message.reply_text.call_args[0][0]
        assert "DISABLED" in text

    @pytest.mark.asyncio
    @patch("src.telegram.wheel_commands.get_database")
    async def test_wheelmode_no_args(self, mock_get_db):
        """/wheelmode with no args shows current status (ON or OFF)."""
        mock_db = MagicMock()
        mock_db.get_bot_state.return_value = {"wheel_mode_enabled": 1}
        mock_get_db.return_value = mock_db

        bot = _make_telegram_bot()
        update = _make_update()
        ctx = MagicMock()
        ctx.args = []

        await bot._cmd_wheelmode(update, ctx)

        # Should NOT update DB
        mock_db.update_bot_state.assert_not_called()
        text = update.message.reply_text.call_args[0][0]
        # Either "ON" or "OFF" should appear in status
        assert "ON" in text or "OFF" in text

    @pytest.mark.asyncio
    @patch("src.telegram.wheel_commands.get_database")
    async def test_wheelmode_invalid_arg(self, mock_get_db):
        """/wheelmode with invalid arg replies with 'Usage:'."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        bot = _make_telegram_bot()
        update = _make_update()
        ctx = MagicMock()
        ctx.args = ["banana"]

        await bot._cmd_wheelmode(update, ctx)

        mock_db.update_bot_state.assert_not_called()
        text = update.message.reply_text.call_args[0][0]
        assert "Usage:" in text

    @pytest.mark.asyncio
    @patch("src.telegram.wheel_commands.get_database")
    async def test_wheelmode_unauthorized(self, mock_get_db):
        """Unauthorized user is rejected for /wheelmode."""
        bot = _make_telegram_bot()
        bot._auth_result = False
        update = _make_update()
        ctx = MagicMock()
        ctx.args = ["on"]

        await bot._cmd_wheelmode(update, ctx)

        mock_get_db.assert_not_called()
        assert "Unauthorized" in update.message.reply_text.call_args[0][0]


# ---------------------------------------------------------------------------
# Task 2 tests: _job_wheel_daily_summary in SmartScheduler (TR-05)
# ---------------------------------------------------------------------------


def _make_scheduler_for_wheel():
    """Build a SmartScheduler via the shared conftest factory (HI-07)."""
    from tests.conftest import make_smart_scheduler

    scheduler = make_smart_scheduler()
    scheduler.db = MagicMock()
    return scheduler


class TestWheelDailySummary:
    """Tests for _job_wheel_daily_summary in SmartScheduler (TR-05)."""

    @patch("src.smart_scheduler.is_trading_day", return_value=False)
    @patch("src.smart_scheduler.get_et_now")
    def test_wheel_summary_skips_non_trading_day(self, mock_now, mock_trading_day):
        """_job_wheel_daily_summary skips on non-trading days."""
        mock_now.return_value = MagicMock()
        mock_now.return_value.date.return_value = MagicMock()

        scheduler = _make_scheduler_for_wheel()
        scheduler._job_wheel_daily_summary()

        scheduler._send_notification.assert_not_called()

    @patch("src.smart_scheduler.is_trading_day", return_value=True)
    @patch("src.smart_scheduler.get_et_now")
    def test_wheel_summary_no_cycle(self, mock_now, mock_trading_day):
        """_job_wheel_daily_summary sends 'No active wheel positions today.' when no cycle."""
        mock_now.return_value = MagicMock()
        mock_now.return_value.date.return_value = MagicMock()

        scheduler = _make_scheduler_for_wheel()
        scheduler.db.get_active_cycle.return_value = None
        scheduler._job_wheel_daily_summary()

        scheduler._send_notification.assert_called_once()
        msg = scheduler._send_notification.call_args[0][0]
        assert "No active wheel positions today." in msg

    @patch("src.smart_scheduler.is_trading_day", return_value=True)
    @patch("src.smart_scheduler.get_et_now")
    def test_wheel_summary_with_cycle(self, mock_now, mock_trading_day):
        """_job_wheel_daily_summary sends positions, DTE, max risk, and premium when cycle exists."""
        from datetime import date

        mock_now.return_value = MagicMock()
        mock_now.return_value.date.return_value = date(2026, 4, 8)

        scheduler = _make_scheduler_for_wheel()
        scheduler.db.get_active_cycle.return_value = {
            "id": 1,
            "state": "SHORT_PUT",
            "cost_basis": 50.0,
            "put_premium_received": 1.50,
            "covered_call_premiums_collected": 0.0,
        }
        scheduler.db.get_cycle_positions.return_value = [
            {
                "status": "OPEN",
                "option_type": "PUT",
                "strike": 50.0,
                "expiry_date": "2026-05-08",
                "premium_received": 1.50,
                "delta": -0.30,
            }
        ]

        scheduler._job_wheel_daily_summary()

        scheduler._send_notification.assert_called_once()
        msg = scheduler._send_notification.call_args[0][0]
        # Should include position details, DTE, max risk, premium
        assert "PUT" in msg
        assert "DTE" in msg
        # ME-07 fix: assert the max risk label + value together so an
        # unrelated "5000" in the message (e.g. an order ID) doesn't pass
        # the check. The render format is "max risk: $5,000".
        assert "max risk: $5,000" in msg, f"Expected 'max risk: $5,000' in message, got: {msg}"
        assert "premium: $1.50" in msg, f"Expected 'premium: $1.50' in message, got: {msg}"

    @patch("src.smart_scheduler.is_trading_day", return_value=True)
    @patch("src.smart_scheduler.get_et_now")
    def test_wheel_summary_job_registered(self, mock_now, mock_trading_day):
        """setup_jobs() registers 'wheel_daily_summary' job with CronTrigger at 16:30."""
        from tests.conftest import make_smart_scheduler

        scheduler = make_smart_scheduler()

        # Capture jobs registered via scheduler.scheduler.add_job(...)
        added_jobs = {}

        def mock_add_job(func, trigger, id, name, misfire_grace_time=600):
            added_jobs[id] = {"func": func, "name": name}

        scheduler.scheduler.add_job.side_effect = mock_add_job

        # Disable the non-wheel branches of setup_jobs so we only see
        # wheel-specific job registrations.
        scheduler.bot.config.strategy.crash_day_enabled = False
        scheduler.bot.config.strategy.pump_day_enabled = False
        scheduler.bot.config.strategy.ten_am_dump_enabled = False
        scheduler.bot.is_paper_mode = True
        scheduler.bot.client = None

        scheduler.setup_jobs()

        assert (
            "wheel_daily_summary" in added_jobs
        ), f"wheel_daily_summary job not registered. Jobs found: {list(added_jobs.keys())}"


# ---------------------------------------------------------------------------
# Task 1 tests (plan 06-03): render_wheel_section dashboard helpers (TR-04)
# ---------------------------------------------------------------------------


class TestDashboardNoCycle:
    """test_dashboard_no_cycle: _build_wheel_positions_df returns empty when no positions."""

    def test_dashboard_no_cycle_empty_positions(self):
        """_build_wheel_positions_df with None/empty list returns empty DataFrame."""
        from datetime import date

        import pandas as pd

        from app import _build_wheel_positions_df

        result = _build_wheel_positions_df([], date.today())
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_dashboard_no_cycle_none_positions(self):
        """_build_wheel_positions_df with empty positions (no OPEN status) returns empty DataFrame."""
        from datetime import date

        import pandas as pd

        from app import _build_wheel_positions_df

        # All positions are CLOSED — should produce empty DataFrame
        positions = [
            {
                "status": "CLOSED",
                "option_type": "PUT",
                "strike": 50.0,
                "expiry_date": "2026-05-15",
                "premium_received": 1.20,
                "delta": -0.25,
                "theta": -0.05,
            }
        ]
        result = _build_wheel_positions_df(positions, date.today())
        assert isinstance(result, pd.DataFrame)
        assert result.empty


class TestDashboardWithCycle:
    """test_dashboard_with_cycle: _build_wheel_positions_df with open positions returns correct DataFrame."""

    def test_build_wheel_positions_df_columns(self):
        """_build_wheel_positions_df returns DataFrame with expected columns."""
        from datetime import date

        import pandas as pd

        from app import _build_wheel_positions_df

        positions = [
            {
                "status": "OPEN",
                "option_type": "PUT",
                "strike": 53.0,
                "expiry_date": "2026-05-15",
                "premium_received": 1.20,
                "delta": -0.25,
                "theta": -0.04,
            }
        ]
        result = _build_wheel_positions_df(positions, date(2026, 4, 8))
        assert isinstance(result, pd.DataFrame)
        assert not result.empty
        for col in ["Type", "Strike", "Expiry", "DTE", "Delta", "Theta", "Premium"]:
            assert col in result.columns, f"Column '{col}' missing from DataFrame"

    def test_build_wheel_positions_df_dte_calculation(self):
        """DTE is calculated correctly as (expiry - now_date).days."""
        from datetime import date

        from app import _build_wheel_positions_df

        today = date(2026, 4, 8)
        positions = [
            {
                "status": "OPEN",
                "option_type": "PUT",
                "strike": 50.0,
                "expiry_date": "2026-05-15",
                "premium_received": 2.00,
                "delta": -0.30,
                "theta": -0.06,
            }
        ]
        df = _build_wheel_positions_df(positions, today)
        from datetime import date as date_cls

        expected_dte = (date_cls(2026, 5, 15) - today).days
        assert df.iloc[0]["DTE"] == expected_dte


class TestBuildCycleHistoryDf:
    """test_build_cycle_history_df: DataFrame with correct columns including annualized return."""

    def test_build_cycle_history_df_empty(self):
        """_build_cycle_history_df with empty list returns empty DataFrame."""
        import pandas as pd

        from app import _build_cycle_history_df

        result = _build_cycle_history_df([])
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_build_cycle_history_df_columns(self):
        """_build_cycle_history_df returns DataFrame with correct columns."""
        import pandas as pd

        from app import _build_cycle_history_df

        cycles = [
            {
                "id": 1,
                "state": "CALLED_AWAY",
                "opened_at": "2026-03-01T10:00:00",
                "closed_at": "2026-04-01T10:00:00",
                "put_premium_received": 1.50,
                "covered_call_premiums_collected": 0.75,
                "realized_pnl": 225.0,
                "cost_basis": 50.0,
            }
        ]
        df = _build_cycle_history_df(cycles)
        assert isinstance(df, pd.DataFrame)
        assert not df.empty
        expected_cols = [
            "State",
            "Start",
            "End",
            "Put Premium",
            "CC Premium",
            "Total P&L",
            "Annualized %",
        ]
        for col in expected_cols:
            assert col in df.columns, f"Column '{col}' missing from DataFrame"

    def test_build_cycle_history_df_annualized_return(self):
        """_build_cycle_history_df computes Annualized % as a numeric or formatted value."""
        from app import _build_cycle_history_df

        cycles = [
            {
                "id": 1,
                "state": "CALLED_AWAY",
                "opened_at": "2026-03-01T10:00:00",
                "closed_at": "2026-04-01T10:00:00",
                "put_premium_received": 1.50,
                "covered_call_premiums_collected": 0.75,
                "realized_pnl": 225.0,
                "cost_basis": 50.0,
            }
        ]
        df = _build_cycle_history_df(cycles)

        # ME-06 fix: assert the exact computed value, not just non-None.
        # Cycle: 31 days, $225 P&L, cost_basis 50.0 → capital at risk $5000.
        # total_prem = (1.50 + 0.75) * 100 = $225
        # annualized = 225 / 5000 * (365 / 31) * 100 = 52.983...%
        # The formatter rounds to 1 decimal.
        expected = f"{(225.0 / 5000.0) * (365 / 31) * 100:.1f}%"
        assert (
            df.iloc[0]["Annualized %"] == expected
        ), f"Expected Annualized % = {expected}, got {df.iloc[0]['Annualized %']}"


class TestCalcTotalPremium:
    """test_calc_total_premium: total premium calculation."""

    def test_calc_total_premium_basic(self):
        """_calc_total_premium computes (put_premium + cc_premiums) * 100 correctly."""
        from app import _calc_total_premium

        cycle = {
            "put_premium_received": 1.5,
            "covered_call_premiums_collected": 0.75,
        }
        result = _calc_total_premium(cycle)
        assert result == pytest.approx(225.0)

    def test_calc_total_premium_zero_cc(self):
        """_calc_total_premium handles zero covered call premiums."""
        from app import _calc_total_premium

        cycle = {
            "put_premium_received": 2.0,
            "covered_call_premiums_collected": 0.0,
        }
        result = _calc_total_premium(cycle)
        assert result == pytest.approx(200.0)

    def test_calc_total_premium_none_values(self):
        """_calc_total_premium handles None values gracefully."""
        from app import _calc_total_premium

        cycle = {
            "put_premium_received": None,
            "covered_call_premiums_collected": None,
        }
        result = _calc_total_premium(cycle)
        assert result == pytest.approx(0.0)
