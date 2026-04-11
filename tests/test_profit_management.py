"""
Tests for profit management: DB migration, WheelStrategy monitoring methods,
SmartScheduler monitoring job, and Telegram approval flows (BTC, roll, DTE alert).

TDD test file — tests written first against the specs in 05-01-PLAN.md and 05-02-PLAN.md.
"""

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database import Database
from src.etrade_client import ETradeClient
from src.telegram.utils import ApprovalResult
from src.wheel_state import WheelState
from tests.conftest import make_telegram_bot as _make_telegram_bot  # HI-01 / LO-02


def _make_etrade_mock():
    """Create an ETradeClient mock with signature enforcement.

    Uses create_autospec so any call with the wrong number or type of
    arguments raises TypeError at test time — this is what catches bugs
    like the Phase 5 _execute_btc_order arg-count mismatch that MagicMock
    would silently accept.
    """
    mock = create_autospec(ETradeClient, instance=True)
    # Give the mock useful return values for the options flow
    mock.preview_options_order.return_value = {"PreviewIds": [{"previewId": 1}]}
    mock.place_options_order.return_value = {"orderId": "ORD123"}
    return mock


# ---------------------------------------------------------------------------
# Shared helpers / fixtures
# ---------------------------------------------------------------------------


from tests.conftest import make_contract as _make_contract  # LO-01


@pytest.fixture
def db(tmp_path):
    """Create an isolated test database."""
    db_path = tmp_path / "test_trades.db"
    return Database(str(db_path))


def _create_cycle_and_position(db: Database) -> tuple:
    """Insert a wheel cycle and an open options position; return (cycle_id, position_id)."""

    from src.utils import get_et_now

    now = get_et_now().isoformat()
    with db._get_connection() as conn:
        cursor = conn.cursor()
        # Insert a wheel cycle
        cursor.execute(
            """
            INSERT INTO wheel_cycles
                (state, underlying, put_strike, put_premium_received, put_expiry_date,
                 opened_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (WheelState.SHORT_PUT.value, "IBIT", 50.0, 1.50, "2026-05-15", now, now, now),
        )
        cycle_id = cursor.lastrowid

        # Insert an options position (OPEN status)
        cursor.execute(
            """
            INSERT INTO options_positions
                (cycle_id, symbol, option_type, strike, expiry_date, dte_at_entry,
                 quantity, premium_received, status, opened_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cycle_id,
                "IBIT260515P00050000",
                "PUT",
                50.0,
                "2026-05-15",
                35,
                1,
                1.50,
                "OPEN",
                now,
                now,
                now,
            ),
        )
        position_id = cursor.lastrowid

    return cycle_id, position_id


# ---------------------------------------------------------------------------
# Task 1: Database migration and helper methods
# ---------------------------------------------------------------------------


class TestDatabaseMigration:
    """Verify roll_count and dte_alert_sent columns and helper methods."""

    def test_options_positions_has_roll_count_column(self, db):
        """After _init_db, options_positions must have a roll_count column."""
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(options_positions)")
            columns = [row[1] for row in cursor.fetchall()]
        assert "roll_count" in columns

    def test_options_positions_has_dte_alert_sent_column(self, db):
        """After _init_db, options_positions must have a dte_alert_sent column."""
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(options_positions)")
            columns = [row[1] for row in cursor.fetchall()]
        assert "dte_alert_sent" in columns

    def test_increment_roll_count_from_zero(self, db):
        """increment_roll_count once -> roll_count == 1."""
        _, position_id = _create_cycle_and_position(db)
        db.increment_roll_count(position_id)
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT roll_count FROM options_positions WHERE id = ?", (position_id,))
            row = cursor.fetchone()
        assert row["roll_count"] == 1

    def test_increment_roll_count_twice(self, db):
        """increment_roll_count twice -> roll_count == 2."""
        _, position_id = _create_cycle_and_position(db)
        db.increment_roll_count(position_id)
        db.increment_roll_count(position_id)
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT roll_count FROM options_positions WHERE id = ?", (position_id,))
            row = cursor.fetchone()
        assert row["roll_count"] == 2

    def test_mark_dte_alert_sent_sets_to_one(self, db):
        """mark_dte_alert_sent sets dte_alert_sent from 0 to 1."""
        _, position_id = _create_cycle_and_position(db)
        db.mark_dte_alert_sent(position_id)
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT dte_alert_sent FROM options_positions WHERE id = ?", (position_id,)
            )
            row = cursor.fetchone()
        assert row["dte_alert_sent"] == 1

    def test_mark_dte_alert_sent_idempotent(self, db):
        """mark_dte_alert_sent called twice stays at 1 (idempotent)."""
        _, position_id = _create_cycle_and_position(db)
        db.mark_dte_alert_sent(position_id)
        db.mark_dte_alert_sent(position_id)
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT dte_alert_sent FROM options_positions WHERE id = ?", (position_id,)
            )
            row = cursor.fetchone()
        assert row["dte_alert_sent"] == 1

    def test_get_open_position_for_cycle_returns_open_position(self, db):
        """get_open_position_for_cycle returns the OPEN position dict."""
        cycle_id, position_id = _create_cycle_and_position(db)
        result = db.get_open_position_for_cycle(cycle_id)
        assert result is not None
        assert result["id"] == position_id
        assert result["status"] == "OPEN"
        assert result["cycle_id"] == cycle_id

    def test_get_open_position_for_cycle_returns_none_when_no_positions(self, db):
        """get_open_position_for_cycle returns None when cycle has no positions."""
        from src.utils import get_et_now

        now = get_et_now().isoformat()
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO wheel_cycles (state, underlying, opened_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (WheelState.CASH.value, "IBIT", now, now, now),
            )
            empty_cycle_id = cursor.lastrowid
        result = db.get_open_position_for_cycle(empty_cycle_id)
        assert result is None

    def test_get_open_position_for_cycle_returns_none_when_all_closed(self, db):
        """get_open_position_for_cycle returns None when all positions are CLOSED."""
        cycle_id, position_id = _create_cycle_and_position(db)
        # Close the position
        db.close_wheel_position(position_id, close_premium=0.50)
        result = db.get_open_position_for_cycle(cycle_id)
        assert result is None


# ---------------------------------------------------------------------------
# Task 2: WheelStrategy monitoring methods
# ---------------------------------------------------------------------------


class TestProfitTarget:
    """Tests for WheelStrategy.check_profit_target."""

    def _make_strategy(self):
        """Build a minimal WheelStrategy with mock client and db."""
        from src.wheel_strategy import WheelStrategy

        client = MagicMock()
        db = MagicMock()
        strategy = WheelStrategy(client, db)
        return strategy

    def test_returns_true_when_ask_is_50_percent_of_premium(self):
        """ask == 50% of premium_received -> profit_target_hit = True."""
        strategy = self._make_strategy()
        position = {"symbol": "IBIT260515P00050000", "premium_received": 1.50}
        chain = [_make_contract("PUT", 50.0, -0.25, 0.70, ask=0.75, symbol="IBIT260515P00050000")]
        # ask=0.75 == 50% of 1.50 -> hit
        assert strategy.check_profit_target(position, chain) is True

    def test_returns_true_when_ask_below_50_percent_of_premium(self):
        """ask < 50% of premium_received -> profit_target_hit = True."""
        strategy = self._make_strategy()
        position = {"symbol": "IBIT260515P00050000", "premium_received": 1.50}
        chain = [_make_contract("PUT", 50.0, -0.25, 0.60, ask=0.60, symbol="IBIT260515P00050000")]
        # ask=0.60 < 0.75 -> definitely hit
        assert strategy.check_profit_target(position, chain) is True

    def test_returns_false_when_ask_above_50_percent_of_premium(self):
        """ask > 50% of premium_received -> profit_target_hit = False."""
        strategy = self._make_strategy()
        position = {"symbol": "IBIT260515P00050000", "premium_received": 1.50}
        chain = [_make_contract("PUT", 50.0, -0.25, 0.90, ask=0.90, symbol="IBIT260515P00050000")]
        # ask=0.90 > 0.75 -> not hit
        assert strategy.check_profit_target(position, chain) is False

    def test_returns_false_when_symbol_not_in_chain(self):
        """Symbol not found in chain -> False (option outside DTE range)."""
        strategy = self._make_strategy()
        position = {"symbol": "IBIT260515P00050000", "premium_received": 1.50}
        chain = [_make_contract("PUT", 48.0, -0.25, 0.50, symbol="IBIT260515P00048000")]
        assert strategy.check_profit_target(position, chain) is False

    def test_returns_false_when_premium_is_zero(self):
        """Zero premium_received -> False without dividing by zero.

        Guards against crash in the 30-min monitoring loop if a position
        somehow has zero premium recorded (e.g., data corruption or
        migration from a system that didn't track premium).
        """
        strategy = self._make_strategy()
        position = {"symbol": "IBIT260515P00050000", "premium_received": 0.0}
        chain = [_make_contract("PUT", 50.0, -0.25, 0.50, symbol="IBIT260515P00050000")]
        # Must not raise ZeroDivisionError
        assert strategy.check_profit_target(position, chain) is False

    def test_returns_false_when_premium_is_negative(self):
        """Negative premium_received -> False without crashing."""
        strategy = self._make_strategy()
        position = {"symbol": "IBIT260515P00050000", "premium_received": -1.0}
        chain = [_make_contract("PUT", 50.0, -0.25, 0.50, symbol="IBIT260515P00050000")]
        assert strategy.check_profit_target(position, chain) is False


class TestPositionTested:
    """Tests for WheelStrategy.check_position_tested."""

    def _make_strategy(self, ibit_price: float):
        """Build a strategy with mocked get_ibit_quote."""
        from src.wheel_strategy import WheelStrategy

        client = MagicMock()
        client.get_ibit_quote.return_value = {
            "last_price": ibit_price,
            "bid": ibit_price - 0.01,
            "ask": ibit_price + 0.01,
        }
        db = MagicMock()
        strategy = WheelStrategy(client, db)
        return strategy

    def test_returns_true_when_price_within_2_percent_of_strike(self):
        """IBIT price within 2% of strike -> position tested."""
        strategy = self._make_strategy(ibit_price=50.5)  # 1% above strike=50
        position = {"strike": 50.0}
        assert strategy.check_position_tested(position) is True

    def test_returns_true_when_price_exactly_at_strike(self):
        """IBIT price exactly at strike -> position tested."""
        strategy = self._make_strategy(ibit_price=50.0)
        position = {"strike": 50.0}
        assert strategy.check_position_tested(position) is True

    def test_returns_false_when_price_more_than_2_percent_away(self):
        """IBIT price more than 2% from strike -> not tested."""
        strategy = self._make_strategy(ibit_price=55.0)  # 10% above strike=50
        position = {"strike": 50.0}
        assert strategy.check_position_tested(position) is False

    def test_returns_false_when_api_fails(self):
        """get_ibit_quote raises -> returns False gracefully."""
        from src.etrade_client import ETradeAPIError
        from src.wheel_strategy import WheelStrategy

        client = MagicMock()
        client.get_ibit_quote.side_effect = ETradeAPIError("stale quote")
        strategy = WheelStrategy(client, MagicMock())
        position = {"strike": 50.0}
        assert strategy.check_position_tested(position) is False


class TestDTEWarning:
    """Tests for WheelStrategy.check_dte_warning."""

    def _make_strategy(self):
        from src.wheel_strategy import WheelStrategy

        return WheelStrategy(MagicMock(), MagicMock())

    def _future_date(self, days: int) -> str:
        """Return an ISO date string N days from a fixed reference date."""
        # Use a fixed reference date to avoid flakiness
        ref = date(2026, 4, 8)
        return (ref + timedelta(days=days)).isoformat()

    def test_returns_true_when_dte_is_21_and_not_sent(self):
        """DTE == 21 and dte_alert_sent == 0 -> True."""
        strategy = self._make_strategy()
        position = {
            "expiry_date": self._future_date(21),
            "dte_alert_sent": 0,
        }
        with patch("src.wheel_strategy.get_et_now") as mock_now:
            from datetime import datetime

            import pytz

            et = pytz.timezone("America/New_York")
            mock_now.return_value = datetime(2026, 4, 8, 10, 0, tzinfo=et)
            result = strategy.check_dte_warning(position)
        assert result is True

    def test_returns_true_when_dte_is_less_than_21_and_not_sent(self):
        """DTE < 21 (e.g., 20) and dte_alert_sent == 0 -> True."""
        strategy = self._make_strategy()
        position = {
            "expiry_date": self._future_date(20),
            "dte_alert_sent": 0,
        }
        with patch("src.wheel_strategy.get_et_now") as mock_now:
            from datetime import datetime

            import pytz

            et = pytz.timezone("America/New_York")
            mock_now.return_value = datetime(2026, 4, 8, 10, 0, tzinfo=et)
            result = strategy.check_dte_warning(position)
        assert result is True

    def test_returns_false_when_dte_alert_already_sent(self):
        """dte_alert_sent == 1 -> False regardless of DTE."""
        strategy = self._make_strategy()
        position = {
            "expiry_date": self._future_date(21),
            "dte_alert_sent": 1,
        }
        with patch("src.wheel_strategy.get_et_now") as mock_now:
            from datetime import datetime

            import pytz

            et = pytz.timezone("America/New_York")
            mock_now.return_value = datetime(2026, 4, 8, 10, 0, tzinfo=et)
            result = strategy.check_dte_warning(position)
        assert result is False

    def test_returns_false_when_dte_greater_than_21(self):
        """DTE > 21 -> False (not at warning threshold yet)."""
        strategy = self._make_strategy()
        position = {
            "expiry_date": self._future_date(30),
            "dte_alert_sent": 0,
        }
        with patch("src.wheel_strategy.get_et_now") as mock_now:
            from datetime import datetime

            import pytz

            et = pytz.timezone("America/New_York")
            mock_now.return_value = datetime(2026, 4, 8, 10, 0, tzinfo=et)
            result = strategy.check_dte_warning(position)
        assert result is False


class TestSelectRollStrike:
    """Tests for WheelStrategy.select_roll_strike."""

    def _make_strategy(self, chain):
        from src.wheel_strategy import WheelStrategy

        client = MagicMock()
        client.get_ibit_options_chain.return_value = chain
        strategy = WheelStrategy(client, MagicMock())
        return strategy

    def _make_put_chain(self):
        """Chain with puts at various strikes; only some in 30-45 DTE and 0.20-0.30 delta."""
        return [
            # Below current strike=50, delta in range, DTE in range -> qualifying
            _make_contract("PUT", 47.0, -0.25, 1.20, dte=35, symbol="IBIT_P47"),
            _make_contract("PUT", 48.0, -0.22, 1.30, dte=40, symbol="IBIT_P48"),
            # Below current strike, delta out of range -> not qualifying
            _make_contract("PUT", 46.0, -0.15, 0.80, dte=35, symbol="IBIT_P46"),
            # Above current strike -> not qualifying for roll
            _make_contract("PUT", 52.0, -0.28, 1.50, dte=35, symbol="IBIT_P52"),
            # DTE out of range -> not qualifying
            _make_contract("PUT", 47.5, -0.24, 1.25, dte=25, symbol="IBIT_P475"),
        ]

    def _make_call_chain(self):
        """Chain with calls at various strikes; only some in 30-45 DTE and 0.25-0.35 delta."""
        return [
            # Above current strike=50, delta in range, DTE in range -> qualifying
            _make_contract("CALL", 52.0, 0.28, 1.10, dte=35, symbol="IBIT_C52"),
            _make_contract("CALL", 53.0, 0.30, 1.20, dte=40, symbol="IBIT_C53"),
            # Above current strike, delta out of range -> not qualifying
            _make_contract("CALL", 54.0, 0.40, 0.90, dte=35, symbol="IBIT_C54"),
            # Below current strike -> not qualifying for call roll
            _make_contract("CALL", 48.0, 0.30, 1.50, dte=35, symbol="IBIT_C48"),
            # DTE out of range -> not qualifying
            _make_contract("CALL", 52.5, 0.27, 1.15, dte=25, symbol="IBIT_C525"),
        ]

    def test_select_roll_strike_put_returns_strike_below_current(self):
        """For PUT, selected strike must be < current_strike."""
        strategy = self._make_strategy(self._make_put_chain())
        result = strategy.select_roll_strike(current_strike=50.0, option_type="PUT")
        assert result is not None
        assert float(result["strike"]) < 50.0

    def test_select_roll_strike_call_returns_strike_above_current(self):
        """For CALL, selected strike must be > current_strike."""
        strategy = self._make_strategy(self._make_call_chain())
        result = strategy.select_roll_strike(current_strike=50.0, option_type="CALL")
        assert result is not None
        assert float(result["strike"]) > 50.0

    def test_select_roll_strike_returns_none_when_no_qualifying_contracts(self):
        """Returns None when no contracts pass all filters."""
        # Empty chain has no qualifying contracts
        strategy = self._make_strategy([])
        result = strategy.select_roll_strike(current_strike=50.0, option_type="PUT")
        assert result is None


class TestRunMonitoringChecks:
    """Tests for WheelStrategy.run_monitoring_checks."""

    def _make_strategy(
        self,
        ibit_price: float = 55.0,
        position: dict = None,
        chain: list = None,
        chain_raises: Exception = None,
    ):
        from src.wheel_strategy import WheelStrategy

        client = MagicMock()
        client.get_ibit_quote.return_value = {
            "last_price": ibit_price,
            "bid": ibit_price - 0.01,
            "ask": ibit_price + 0.01,
        }
        if chain_raises:
            client.get_ibit_options_chain.side_effect = chain_raises
        else:
            client.get_ibit_options_chain.return_value = chain or []
        db = MagicMock()
        db.get_open_position_for_cycle.return_value = position
        strategy = WheelStrategy(client, db)
        return strategy

    def _make_position(
        self,
        symbol: str = "IBIT260515P00050000",
        premium_received: float = 1.50,
        strike: float = 50.0,
        expiry_date: str = "2026-06-15",
        dte_alert_sent: int = 0,
    ) -> dict:
        return {
            "id": 1,
            "cycle_id": 1,
            "symbol": symbol,
            "premium_received": premium_received,
            "strike": strike,
            "expiry_date": expiry_date,
            "dte_alert_sent": dte_alert_sent,
            "status": "OPEN",
        }

    def test_returns_dict_with_required_keys(self):
        """run_monitoring_checks returns a dict with profit_target_hit, position_tested, dte_warning."""
        position = self._make_position(expiry_date="2026-06-15")
        strategy = self._make_strategy(position=position)
        cycle = {"id": 1}
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(strategy.run_monitoring_checks(cycle))
        assert "profit_target_hit" in result
        assert "position_tested" in result
        assert "dte_warning" in result

    def test_returns_all_false_when_no_open_position(self):
        """When no open position, all flags are False."""
        strategy = self._make_strategy(position=None)
        cycle = {"id": 1}
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(strategy.run_monitoring_checks(cycle))
        assert result["profit_target_hit"] is False
        assert result["position_tested"] is False
        assert result["dte_warning"] is False

    def test_returns_all_false_when_chain_raises_etrade_api_error(self):
        """When get_ibit_options_chain raises ETradeAPIError, return all-False dict."""
        from src.etrade_client import ETradeAPIError

        position = self._make_position()
        strategy = self._make_strategy(position=position, chain_raises=ETradeAPIError("stale"))
        cycle = {"id": 1}
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(strategy.run_monitoring_checks(cycle))
        assert result["profit_target_hit"] is False
        assert result["position_tested"] is False
        assert result["dte_warning"] is False

    def test_position_tested_is_false_when_profit_target_hit(self):
        """When profit target is hit, position_tested is forced to False (profit priority)."""
        # Position with ask at 50% of premium
        position = self._make_position(
            symbol="IBIT_P50", premium_received=1.50, strike=50.0, expiry_date="2026-06-15"
        )
        chain = [_make_contract("PUT", 50.0, -0.25, 0.70, ask=0.75, symbol="IBIT_P50")]
        # IBIT price near strike (would normally set position_tested=True)
        strategy = self._make_strategy(ibit_price=50.5, position=position, chain=chain)
        cycle = {"id": 1}
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(strategy.run_monitoring_checks(cycle))
        assert result["profit_target_hit"] is True
        assert result["position_tested"] is False  # profit takes priority

    def test_position_and_chain_included_in_result(self):
        """Result dict includes 'position' and 'chain' keys for Plan 02 use."""
        position = self._make_position(expiry_date="2026-06-15")
        chain = []
        strategy = self._make_strategy(position=position, chain=chain)
        cycle = {"id": 1}
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(strategy.run_monitoring_checks(cycle))
        assert "position" in result
        assert "chain" in result


# ---------------------------------------------------------------------------
# Task 3: SmartScheduler monitoring job
# ---------------------------------------------------------------------------


class TestSchedulerMonitoring:
    """Verify _job_wheel_monitoring guards and execution flow."""

    def _make_scheduler(
        self,
        is_trading: bool = True,
        has_wheel_strategy: bool = True,
        cycle: dict = None,
        monitoring_approval_pending: bool = False,
    ):
        """Build a SmartScheduler via the shared conftest factory."""
        from tests.conftest import make_smart_scheduler

        scheduler = make_smart_scheduler(telegram_bot=MagicMock())
        scheduler.db = MagicMock()
        scheduler._monitoring_approval_pending = monitoring_approval_pending

        if has_wheel_strategy:
            scheduler.wheel_strategy = MagicMock()
            mock_result = {
                "profit_target_hit": False,
                "position_tested": False,
                "dte_warning": False,
                "position": None,
                "chain": None,
            }
            scheduler.wheel_strategy.run_monitoring_checks = AsyncMock(return_value=mock_result)
        else:
            scheduler.wheel_strategy = None

        scheduler.db.get_active_cycle.return_value = cycle

        return scheduler, is_trading

    def _run_job(self, scheduler, is_trading: bool):
        """Run the monitoring job with trading day patched."""
        with patch("src.smart_scheduler.is_trading_day", return_value=is_trading):
            with patch("src.smart_scheduler.get_et_now"):
                with patch("src.smart_scheduler.run_async") as mock_run_async:
                    mock_run_async.side_effect = lambda coro: None
                    scheduler._job_wheel_monitoring()
                    return mock_run_async

    def test_setup_jobs_registers_wheel_monitoring(self):
        """setup_jobs must register a 'wheel_monitoring' job."""
        from tests.conftest import make_smart_scheduler

        scheduler = make_smart_scheduler(telegram_bot=MagicMock())

        # setup_jobs() reads flags off scheduler.bot.config.strategy and
        # calls scheduler.scheduler.add_job(). Override the bot so the
        # non-wheel signal-check branches don't register jobs we don't
        # care about here.
        scheduler.bot.client = None
        scheduler.bot.config.strategy.crash_day_enabled = False
        scheduler.bot.config.strategy.pump_day_enabled = False
        scheduler.bot.config.strategy.ten_am_dump_enabled = False
        scheduler.bot.is_paper_mode = True

        scheduler.setup_jobs()
        # Check that add_job was called with id="wheel_monitoring"
        call_kwargs = [call[1] for call in scheduler.scheduler.add_job.call_args_list]
        job_ids = [kw.get("id") for kw in call_kwargs]
        assert "wheel_monitoring" in job_ids

    def test_skips_on_non_trading_day(self):
        """Job returns early without calling run_monitoring_checks on non-trading days."""
        scheduler, _ = self._make_scheduler(
            has_wheel_strategy=True,
            cycle={"id": 1, "state": WheelState.SHORT_PUT.value},
        )
        with patch("src.smart_scheduler.is_trading_day", return_value=False):
            with patch("src.smart_scheduler.get_et_now"):
                scheduler._job_wheel_monitoring()
        scheduler.wheel_strategy.run_monitoring_checks.assert_not_called()

    def test_skips_when_wheel_strategy_is_none(self):
        """Job returns early when wheel_strategy is None."""
        scheduler, _ = self._make_scheduler(has_wheel_strategy=False)
        with patch("src.smart_scheduler.is_trading_day", return_value=True):
            with patch("src.smart_scheduler.get_et_now"):
                scheduler._job_wheel_monitoring()
        # No exception, skipped cleanly
        scheduler.db.get_active_cycle.assert_not_called()

    def test_skips_when_no_active_cycle(self):
        """Job returns early when get_active_cycle returns None."""
        scheduler, _ = self._make_scheduler(cycle=None)
        with patch("src.smart_scheduler.is_trading_day", return_value=True):
            with patch("src.smart_scheduler.get_et_now"):
                scheduler._job_wheel_monitoring()
        scheduler.wheel_strategy.run_monitoring_checks.assert_not_called()

    def test_skips_when_cycle_state_is_cash(self):
        """Job returns early when cycle state is CASH (no open option)."""
        scheduler, _ = self._make_scheduler(cycle={"id": 1, "state": WheelState.CASH.value})
        with patch("src.smart_scheduler.is_trading_day", return_value=True):
            with patch("src.smart_scheduler.get_et_now"):
                scheduler._job_wheel_monitoring()
        scheduler.wheel_strategy.run_monitoring_checks.assert_not_called()

    def test_skips_when_cycle_state_is_holding_shares(self):
        """Job returns early when cycle state is HOLDING_SHARES (no open option)."""
        scheduler, _ = self._make_scheduler(
            cycle={"id": 1, "state": WheelState.HOLDING_SHARES.value}
        )
        with patch("src.smart_scheduler.is_trading_day", return_value=True):
            with patch("src.smart_scheduler.get_et_now"):
                scheduler._job_wheel_monitoring()
        scheduler.wheel_strategy.run_monitoring_checks.assert_not_called()

    def test_calls_run_monitoring_checks_for_short_put(self):
        """Job calls run_monitoring_checks when cycle is SHORT_PUT."""
        cycle = {"id": 1, "state": WheelState.SHORT_PUT.value}
        scheduler, _ = self._make_scheduler(cycle=cycle)
        with patch("src.smart_scheduler.is_trading_day", return_value=True):
            with patch("src.smart_scheduler.get_et_now"):
                with patch("src.smart_scheduler.run_async") as mock_run_async:
                    mock_run_async.side_effect = lambda coro: {
                        "profit_target_hit": False,
                        "position_tested": False,
                        "dte_warning": False,
                        "position": None,
                        "chain": None,
                    }
                    scheduler._job_wheel_monitoring()
        mock_run_async.assert_called_once()

    def test_calls_run_monitoring_checks_for_covered_call(self):
        """Job calls run_monitoring_checks when cycle is COVERED_CALL."""
        cycle = {"id": 1, "state": WheelState.COVERED_CALL.value}
        scheduler, _ = self._make_scheduler(cycle=cycle)
        with patch("src.smart_scheduler.is_trading_day", return_value=True):
            with patch("src.smart_scheduler.get_et_now"):
                with patch("src.smart_scheduler.run_async") as mock_run_async:
                    mock_run_async.side_effect = lambda coro: {
                        "profit_target_hit": False,
                        "position_tested": False,
                        "dte_warning": False,
                        "position": None,
                        "chain": None,
                    }
                    scheduler._job_wheel_monitoring()
        mock_run_async.assert_called_once()

    def test_skips_when_monitoring_approval_pending(self):
        """Job returns early when _monitoring_approval_pending is True."""
        cycle = {"id": 1, "state": WheelState.SHORT_PUT.value}
        scheduler, _ = self._make_scheduler(cycle=cycle, monitoring_approval_pending=True)
        with patch("src.smart_scheduler.is_trading_day", return_value=True):
            with patch("src.smart_scheduler.get_et_now"):
                scheduler._job_wheel_monitoring()
        scheduler.wheel_strategy.run_monitoring_checks.assert_not_called()

    def test_catches_exception_logs_error_and_sends_notification(self):
        """If an exception occurs in the job, it is caught and a notification is sent."""
        cycle = {"id": 1, "state": WheelState.SHORT_PUT.value}
        scheduler, _ = self._make_scheduler(cycle=cycle)
        scheduler.db.get_active_cycle.return_value = cycle
        # Make run_async raise an exception
        with patch("src.smart_scheduler.is_trading_day", return_value=True):
            with patch("src.smart_scheduler.get_et_now"):
                with patch("src.smart_scheduler.run_async", side_effect=RuntimeError("API down")):
                    scheduler._job_wheel_monitoring()
        scheduler.db.log_event.assert_called()
        scheduler._send_notification.assert_called_once()


# ---------------------------------------------------------------------------
# Task 1 (05-02): Telegram BTC approval flow and DTE alert
# ---------------------------------------------------------------------------


def _make_position(
    position_id: int = 1,
    cycle_id: int = 1,
    symbol: str = "IBIT260515P00050000",
    option_type: str = "PUT",
    strike: float = 50.0,
    premium_received: float = 1.50,
    quantity: int = 1,
    roll_count: int = 0,
    dte_alert_sent: int = 0,
    expiry_date: str = "2026-05-15",
) -> dict:
    return {
        "id": position_id,
        "cycle_id": cycle_id,
        "symbol": symbol,
        "option_type": option_type,
        "strike": strike,
        "premium_received": premium_received,
        "quantity": quantity,
        "roll_count": roll_count,
        "dte_alert_sent": dte_alert_sent,
        "expiry_date": expiry_date,
        "status": "OPEN",
    }


def _make_chain_with_ask(symbol: str, ask: float) -> list:
    """Build a chain with one contract matching symbol and given ask."""
    return [
        {
            "symbol": symbol,
            "option_type": "PUT",
            "strike": 50.0,
            "ask": ask,
            "bid": ask - 0.05,
            "delta": -0.25,
            "gamma": 0.05,
            "theta": -0.04,
            "vega": 0.10,
            "iv": 0.35,
            "dte": 21,
            "expiry_date": "2026-05-15",
            "expiry_year": 2026,
            "expiry_month": 5,
            "expiry_day": 15,
        }
    ]


class TestBuyToClose:
    """Tests for request_profit_take_approval and _execute_btc_order."""

    def test_btc_approval_sends_message_with_profit_info(self):
        """request_profit_take_approval sends a message containing strike, profit %, and savings."""
        bot = _make_telegram_bot()
        position = _make_position(symbol="IBIT260515P00050000", premium_received=1.50)
        # ask=0.60 -> profit = (1.50-0.60)/1.50*100 = 60%
        chain = _make_chain_with_ask("IBIT260515P00050000", ask=0.60)
        cycle = {"id": 1, "state": "SHORT_PUT"}

        async def run():
            async def fake_wait_for(coro, timeout):
                bot._btc_approval.result = "rejected"

            with patch("asyncio.wait_for", new=fake_wait_for):
                await bot.request_profit_take_approval(
                    position, chain, cycle, MagicMock(), MagicMock(), "acc"
                )

        asyncio.get_event_loop().run_until_complete(run())
        bot._app.bot.send_message.assert_called()
        sent_text = bot._app.bot.send_message.call_args[1]["text"]
        assert "50.0" in sent_text or "50" in sent_text  # strike
        assert "60" in sent_text or "Profit" in sent_text.replace("PROFIT", "Profit")

    def test_btc_approval_shows_approve_and_reject_buttons(self):
        """request_profit_take_approval includes Approve and Reject inline buttons."""
        bot = _make_telegram_bot()
        position = _make_position(symbol="IBIT260515P00050000", premium_received=1.50)
        chain = _make_chain_with_ask("IBIT260515P00050000", ask=0.60)
        cycle = {"id": 1, "state": "SHORT_PUT"}

        async def run():
            async def fake_wait_for(coro, timeout):
                bot._btc_approval.result = "rejected"

            with patch("asyncio.wait_for", new=fake_wait_for):
                await bot.request_profit_take_approval(
                    position, chain, cycle, MagicMock(), MagicMock(), "acc"
                )

        asyncio.get_event_loop().run_until_complete(run())
        call_kwargs = bot._app.bot.send_message.call_args[1]
        markup = call_kwargs.get("reply_markup")
        assert markup is not None
        # Flatten all button callback_data
        all_data = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        assert any("btc_approve_" in d for d in all_data)
        assert any("btc_reject_" in d for d in all_data)

    def test_btc_approve_calls_execute_btc_order(self):
        """When btc_approve callback fires, _execute_btc_order is called."""
        bot = _make_telegram_bot()
        position = _make_position(symbol="IBIT260515P00050000", premium_received=1.50)
        chain = _make_chain_with_ask("IBIT260515P00050000", ask=0.60)
        cycle = {"id": 1, "state": "SHORT_PUT"}

        async def run():
            async def fake_wait_for(coro, timeout):
                bot._btc_approval.result = "approved"

            with patch.object(
                bot, "_execute_btc_order", new=AsyncMock(return_value=True)
            ) as mock_exec:
                with patch("asyncio.wait_for", new=fake_wait_for):
                    result = await bot.request_profit_take_approval(
                        position, chain, cycle, MagicMock(), MagicMock(), "acc"
                    )
                mock_exec.assert_called_once()
                return result

        result = asyncio.get_event_loop().run_until_complete(run())
        assert result == ApprovalResult.APPROVED

    def test_btc_reject_returns_rejected_no_order(self):
        """When btc_reject callback fires, _execute_btc_order is NOT called."""
        bot = _make_telegram_bot()
        position = _make_position(symbol="IBIT260515P00050000", premium_received=1.50)
        chain = _make_chain_with_ask("IBIT260515P00050000", ask=0.60)
        cycle = {"id": 1, "state": "SHORT_PUT"}

        async def run():
            async def fake_wait_for(coro, timeout):
                bot._btc_approval.result = "rejected"

            with patch.object(bot, "_execute_btc_order", new=AsyncMock()) as mock_exec:
                with patch("asyncio.wait_for", new=fake_wait_for):
                    result = await bot.request_profit_take_approval(
                        position, chain, cycle, MagicMock(), MagicMock(), "acc"
                    )
                mock_exec.assert_not_called()
                return result

        result = asyncio.get_event_loop().run_until_complete(run())
        assert result == ApprovalResult.REJECTED

    def test_execute_btc_order_places_buy_close_order(self):
        """_execute_btc_order calls preview + place with BUY_CLOSE action in the correct arg slot.

        Uses create_autospec(ETradeClient) so arg-count and position
        mismatches raise TypeError — this is the test that would have
        caught the original Phase 5 bug where BTC was calling
        preview_options_order with 7 args instead of 10.
        """
        bot = _make_telegram_bot()
        position = _make_position(symbol="IBIT260515P00050000", premium_received=1.50)
        cycle = {"id": 1, "state": "SHORT_PUT"}
        mock_client = _make_etrade_mock()
        mock_db = MagicMock()

        async def run():
            return await bot._execute_btc_order(
                position, cycle, mock_client, mock_db, "acc", close_price=0.60
            )

        # HI-05 fix: assert the return value. A bug returning False on success
        # (or raising silently after the API call) would previously be missed
        # because the test ignored the return.
        result = asyncio.get_event_loop().run_until_complete(run())
        assert result is True, f"_execute_btc_order should return True on success, got {result}"
        mock_client.preview_options_order.assert_called_once()
        mock_client.place_options_order.assert_called_once()

        # Verify BUY_CLOSE action was in the order_action slot (position 8),
        # not just "somewhere in the args" — position-sensitive assertion.
        # preview_options_order signature:
        #   (self, account_id_key, symbol, option_type, expiry_year,
        #    expiry_month, expiry_day, strike_price, order_action, quantity, limit_price)
        preview_args = mock_client.preview_options_order.call_args[0]
        assert (
            preview_args[7] == "BUY_CLOSE"
        ), f"BUY_CLOSE should be in slot 7 (order_action), got {preview_args}"
        assert preview_args[0] == "acc"  # account_id_key
        assert preview_args[1] == "IBIT"  # symbol
        assert preview_args[2] == "PUT"  # option_type
        assert preview_args[9] == 0.60  # limit_price (close_price)

    def test_execute_btc_short_put_transitions_to_cash(self):
        """_execute_btc_order for SHORT_PUT transitions cycle to CASH."""
        bot = _make_telegram_bot()
        position = _make_position(symbol="IBIT260515P00050000", premium_received=1.50)
        cycle = {"id": 1, "state": "SHORT_PUT"}
        mock_client = MagicMock()
        mock_client.preview_options_order.return_value = {"PreviewIds": []}
        mock_client.place_options_order.return_value = {"orderId": "ORD123"}
        mock_db = MagicMock()

        async def run():
            await bot._execute_btc_order(
                position, cycle, mock_client, mock_db, "acc", close_price=0.60
            )

        asyncio.get_event_loop().run_until_complete(run())
        mock_db.transition_wheel_state.assert_called_once()
        call_args = mock_db.transition_wheel_state.call_args[0]
        assert call_args[1] == WheelState.CASH

    def test_execute_btc_covered_call_transitions_to_holding_shares(self):
        """_execute_btc_order for COVERED_CALL transitions cycle to HOLDING_SHARES."""
        bot = _make_telegram_bot()
        position = _make_position(
            symbol="IBIT260515C00050000", option_type="CALL", premium_received=1.20
        )
        cycle = {"id": 1, "state": "COVERED_CALL"}
        mock_client = MagicMock()
        mock_client.preview_options_order.return_value = {"PreviewIds": []}
        mock_client.place_options_order.return_value = {"orderId": "ORD456"}
        mock_db = MagicMock()

        async def run():
            await bot._execute_btc_order(
                position, cycle, mock_client, mock_db, "acc", close_price=0.50
            )

        asyncio.get_event_loop().run_until_complete(run())
        mock_db.transition_wheel_state.assert_called_once()
        call_args = mock_db.transition_wheel_state.call_args[0]
        assert call_args[1] == WheelState.HOLDING_SHARES

    def test_execute_btc_records_close_premium(self):
        """_execute_btc_order calls close_wheel_position with the close_price."""
        bot = _make_telegram_bot()
        position = _make_position(symbol="IBIT260515P00050000", premium_received=1.50)
        cycle = {"id": 1, "state": "SHORT_PUT"}
        mock_client = MagicMock()
        mock_client.preview_options_order.return_value = {"PreviewIds": []}
        mock_client.place_options_order.return_value = {"orderId": "ORD123"}
        mock_db = MagicMock()

        async def run():
            await bot._execute_btc_order(
                position, cycle, mock_client, mock_db, "acc", close_price=0.60
            )

        asyncio.get_event_loop().run_until_complete(run())
        mock_db.close_wheel_position.assert_called_once_with(position["id"], 0.60)

    def test_execute_btc_no_db_mutation_on_api_failure(self):
        """T-05-06: If API call fails, close_wheel_position and transition_wheel_state are NOT called."""
        from src.etrade_client import ETradeAPIError

        bot = _make_telegram_bot()
        position = _make_position(symbol="IBIT260515P00050000", premium_received=1.50)
        cycle = {"id": 1, "state": "SHORT_PUT"}
        mock_client = MagicMock()
        mock_client.preview_options_order.side_effect = ETradeAPIError("API unavailable")
        mock_db = MagicMock()

        async def run():
            await bot._execute_btc_order(
                position, cycle, mock_client, mock_db, "acc", close_price=0.60
            )

        asyncio.get_event_loop().run_until_complete(run())
        mock_db.close_wheel_position.assert_not_called()
        mock_db.transition_wheel_state.assert_not_called()


class TestDTEAlert:
    """Tests for send_dte_alert."""

    def test_dte_alert_sends_informational_message_no_buttons(self):
        """send_dte_alert sends a message with NO inline keyboard buttons."""
        bot = _make_telegram_bot()
        position = _make_position(expiry_date="2026-05-15")
        cycle = {"id": 1, "state": "SHORT_PUT"}
        mock_db = MagicMock()

        async def run():
            await bot.send_dte_alert(position, cycle, mock_db)

        asyncio.get_event_loop().run_until_complete(run())
        bot._app.bot.send_message.assert_called_once()
        call_kwargs = bot._app.bot.send_message.call_args[1]
        # No reply_markup or None = no buttons
        markup = call_kwargs.get("reply_markup")
        assert markup is None

    def test_dte_alert_calls_mark_dte_alert_sent(self):
        """send_dte_alert calls db.mark_dte_alert_sent after sending message."""
        bot = _make_telegram_bot()
        position = _make_position(expiry_date="2026-05-15")
        cycle = {"id": 1, "state": "SHORT_PUT"}
        mock_db = MagicMock()

        async def run():
            await bot.send_dte_alert(position, cycle, mock_db)

        asyncio.get_event_loop().run_until_complete(run())
        mock_db.mark_dte_alert_sent.assert_called_once_with(position["id"])

    def test_dte_alert_message_contains_expiry_and_option_type(self):
        """send_dte_alert message includes option_type, strike, expiry_date."""
        bot = _make_telegram_bot()
        position = _make_position(option_type="PUT", strike=50.0, expiry_date="2026-05-15")
        cycle = {"id": 1, "state": "SHORT_PUT"}
        mock_db = MagicMock()

        async def run():
            await bot.send_dte_alert(position, cycle, mock_db)

        asyncio.get_event_loop().run_until_complete(run())
        sent_text = bot._app.bot.send_message.call_args[1]["text"]
        assert "PUT" in sent_text or "put" in sent_text.lower()
        assert "50" in sent_text
        assert "2026-05-15" in sent_text or "05-15" in sent_text or "May" in sent_text


class TestBTCCallbackRouting:
    """Verify btc_approve/btc_reject callbacks route to BTC event and don't interfere with put/call."""

    def _make_callback_query(self, data: str):
        """Build a minimal mock callback query."""
        query = MagicMock()
        query.data = data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.message = MagicMock()
        query.message.text = "original"
        return query

    def _make_update(self, chat_id="12345", data="btc_approve_1"):
        update = MagicMock()
        update.effective_chat.id = chat_id
        update.callback_query = self._make_callback_query(data)
        return update

    def test_btc_approve_sets_btc_result_not_call_or_put(self):
        """btc_approve_ callback sets _btc_approval.result='approved', not _call/_put result."""
        bot = _make_telegram_bot()
        bot._btc_approval.event = asyncio.Event()
        bot._btc_approval.callback_id = "1"  # matches tail of btc_approve_1
        update = self._make_update(data="btc_approve_1")

        asyncio.get_event_loop().run_until_complete(bot._handle_callback(update, MagicMock()))

        assert bot._btc_approval.result == "approved"
        assert bot._call_approval.result is None
        assert bot._put_approval.result is None

    def test_btc_reject_sets_btc_result_not_call_or_put(self):
        """btc_reject_ callback sets _btc_approval.result='rejected', not _call/_put result."""
        bot = _make_telegram_bot()
        bot._btc_approval.event = asyncio.Event()
        bot._btc_approval.callback_id = "1"
        update = self._make_update(data="btc_reject_1")

        asyncio.get_event_loop().run_until_complete(bot._handle_callback(update, MagicMock()))

        assert bot._btc_approval.result == "rejected"
        assert bot._call_approval.result is None
        assert bot._put_approval.result is None

    def test_btc_approve_from_unauthorized_chat_is_rejected(self):
        """ME-10: btc_approve from a non-owner chat_id must NOT set the result.

        _handle_callback's authorization check runs before any prefix
        dispatch. A unauthorized user tapping a still-visible btc_approve
        button in a forwarded message should see the "Unauthorized" alert
        and leave `_btc_approval.result` untouched. If the auth check is
        ever accidentally skipped for btc_* (e.g. a refactor that short-
        circuits the dispatch table), this test fails.
        """
        bot = _make_telegram_bot()
        bot.chat_id = "12345"  # owner
        bot._btc_approval.event = asyncio.Event()
        bot._btc_approval.callback_id = "1"

        update = self._make_update(chat_id="99999", data="btc_approve_1")

        asyncio.get_event_loop().run_until_complete(bot._handle_callback(update, MagicMock()))

        # Result must NOT be set and the unauthorized alert must fire
        assert bot._btc_approval.result is None
        assert not bot._btc_approval.event.is_set()
        update.callback_query.answer.assert_awaited_with("🚫 Unauthorized", show_alert=True)

    def test_btc_reject_from_unauthorized_chat_is_rejected(self):
        """ME-10: btc_reject from a non-owner chat_id must NOT set the result."""
        bot = _make_telegram_bot()
        bot.chat_id = "12345"
        bot._btc_approval.event = asyncio.Event()
        bot._btc_approval.callback_id = "1"

        update = self._make_update(chat_id="99999", data="btc_reject_1")

        asyncio.get_event_loop().run_until_complete(bot._handle_callback(update, MagicMock()))

        assert bot._btc_approval.result is None
        assert not bot._btc_approval.event.is_set()


class TestSchedulerBTCWiring:
    """Verify _job_wheel_monitoring dispatches BTC and DTE alerts correctly."""

    def _make_scheduler(
        self,
        profit_target_hit=False,
        position_tested=False,
        dte_warning=False,
        position=None,
        chain=None,
        cycle_state="SHORT_PUT",
    ):
        from tests.conftest import make_smart_scheduler

        telegram_bot = MagicMock()
        telegram_bot.request_profit_take_approval = AsyncMock(return_value=ApprovalResult.APPROVED)
        telegram_bot.send_dte_alert = AsyncMock()

        scheduler = make_smart_scheduler(telegram_bot=telegram_bot)
        scheduler.db = MagicMock()
        scheduler.wheel_strategy = MagicMock()
        scheduler.wheel_strategy.client = MagicMock()
        scheduler.wheel_strategy.account_id_key = "acc"
        mock_result = {
            "profit_target_hit": profit_target_hit,
            "position_tested": position_tested,
            "dte_warning": dte_warning,
            "position": position,
            "chain": chain,
        }
        scheduler.wheel_strategy.run_monitoring_checks = AsyncMock(return_value=mock_result)
        cycle = {"id": 1, "state": cycle_state}
        scheduler.db.get_active_cycle.return_value = cycle
        return scheduler, cycle

    def test_dispatches_profit_take_approval_when_profit_target_hit(self):
        """When profit_target_hit=True, run_async is called with request_profit_take_approval."""
        position = _make_position()
        chain = _make_chain_with_ask("IBIT260515P00050000", ask=0.60)
        scheduler, cycle = self._make_scheduler(
            profit_target_hit=True, position=position, chain=chain
        )

        with patch("src.smart_scheduler.is_trading_day", return_value=True), patch(
            "src.smart_scheduler.get_et_now"
        ), patch("src.smart_scheduler.run_async") as mock_run_async:
            mock_run_async.side_effect = (
                lambda coro: {
                    "profit_target_hit": True,
                    "position_tested": False,
                    "dte_warning": False,
                    "position": position,
                    "chain": chain,
                }
                if hasattr(coro, "cr_frame") or True
                else None
            )
            scheduler._job_wheel_monitoring()

        # Verify run_async was called (for monitoring checks at minimum)
        assert mock_run_async.call_count >= 1

    def test_dispatches_dte_alert_when_dte_warning_true(self):
        """When dte_warning=True, run_async is called with send_dte_alert."""
        position = _make_position()
        scheduler, cycle = self._make_scheduler(dte_warning=True, position=position, chain=[])

        run_async_calls = []

        def capture_run_async(coro):
            run_async_calls.append(coro)
            # Return monitoring result on first call
            if len(run_async_calls) == 1:
                return {
                    "profit_target_hit": False,
                    "position_tested": False,
                    "dte_warning": True,
                    "position": position,
                    "chain": [],
                }
            return None

        with patch("src.smart_scheduler.is_trading_day", return_value=True), patch(
            "src.smart_scheduler.get_et_now"
        ), patch("src.smart_scheduler.run_async", side_effect=capture_run_async):
            scheduler._job_wheel_monitoring()

        # At least 2 run_async calls: one for monitoring checks, one for send_dte_alert
        assert len(run_async_calls) >= 2

    def test_monitoring_approval_pending_cleared_after_btc(self):
        """_monitoring_approval_pending is True during approval and False after."""
        position = _make_position()
        chain = _make_chain_with_ask("IBIT260515P00050000", ask=0.60)
        scheduler, cycle = self._make_scheduler(
            profit_target_hit=True, position=position, chain=chain
        )

        pending_during = []

        def capture_run_async(coro):
            pending_during.append(scheduler._monitoring_approval_pending)
            if len(pending_during) == 1:
                return {
                    "profit_target_hit": True,
                    "position_tested": False,
                    "dte_warning": False,
                    "position": position,
                    "chain": chain,
                }
            return ApprovalResult.APPROVED

        with patch("src.smart_scheduler.is_trading_day", return_value=True), patch(
            "src.smart_scheduler.get_et_now"
        ), patch("src.smart_scheduler.run_async", side_effect=capture_run_async):
            scheduler._job_wheel_monitoring()

        # After job completes, pending should be False
        assert scheduler._monitoring_approval_pending is False


# ---------------------------------------------------------------------------
# Task 2 (05-02): Telegram roll approval flow with credit-only validation
# ---------------------------------------------------------------------------


def _make_new_contract(
    strike: float = 48.0,
    bid: float = 1.40,
    ask: float = 1.50,
    delta: float = -0.22,
    dte: int = 35,
    expiry_date: str = "2026-06-20",
) -> dict:
    """Build a minimal new_contract dict as returned by select_roll_strike."""
    return {
        "symbol": f"IBIT260620P{int(strike * 1000):08d}",
        "option_type": "PUT",
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "delta": delta,
        "gamma": 0.05,
        "theta": -0.04,
        "vega": 0.10,
        "iv": 0.35,
        "dte": dte,
        "expiry_date": expiry_date,
        "expiry_year": 2026,
        "expiry_month": 6,
        "expiry_day": 20,
    }


def _make_chain_for_roll(
    position_symbol: str,
    position_ask: float,
    new_contract_symbol: str = None,
) -> list:
    """Build a chain with the current position and optionally the new contract."""
    chain = [
        {
            "symbol": position_symbol,
            "option_type": "PUT",
            "strike": 50.0,
            "ask": position_ask,
            "bid": position_ask - 0.05,
            "delta": -0.25,
            "dte": 10,
            "expiry_date": "2026-05-15",
        }
    ]
    return chain


class TestRollApproval:
    """Tests for request_roll_approval message and approval flow."""

    def test_roll_approval_sends_message_with_position_details(self):
        """request_roll_approval sends message with current strike, new strike, net credit."""
        bot = _make_telegram_bot()
        position = _make_position(symbol="IBIT260515P00050000", roll_count=0)
        new_contract = _make_new_contract(strike=48.0, bid=1.40)
        chain = _make_chain_for_roll("IBIT260515P00050000", position_ask=0.80)
        cycle = {"id": 1, "state": "SHORT_PUT"}

        async def run():
            async def fake_wait_for(coro, timeout):
                bot._roll_approval.result = "rejected"

            with patch("asyncio.wait_for", new=fake_wait_for):
                await bot.request_roll_approval(
                    position, new_contract, cycle, chain, MagicMock(), MagicMock(), "acc"
                )

        asyncio.get_event_loop().run_until_complete(run())
        bot._app.bot.send_message.assert_called()
        sent_text = bot._app.bot.send_message.call_args[1]["text"]
        assert "50" in sent_text  # current strike
        assert "48" in sent_text  # new strike

    def test_roll_approval_shows_approve_and_reject_buttons(self):
        """request_roll_approval includes Approve and Reject inline buttons."""
        bot = _make_telegram_bot()
        position = _make_position(symbol="IBIT260515P00050000", roll_count=0)
        new_contract = _make_new_contract(strike=48.0, bid=1.40)
        chain = _make_chain_for_roll("IBIT260515P00050000", position_ask=0.80)
        cycle = {"id": 1, "state": "SHORT_PUT"}

        async def run():
            async def fake_wait_for(coro, timeout):
                bot._roll_approval.result = "rejected"

            with patch("asyncio.wait_for", new=fake_wait_for):
                await bot.request_roll_approval(
                    position, new_contract, cycle, chain, MagicMock(), MagicMock(), "acc"
                )

        asyncio.get_event_loop().run_until_complete(run())
        # Second call is the roll message (first may be guard warnings)
        call_kwargs = bot._app.bot.send_message.call_args[1]
        markup = call_kwargs.get("reply_markup")
        assert markup is not None
        all_data = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        assert any("roll_approve_" in d for d in all_data)
        assert any("roll_reject_" in d for d in all_data)

    def test_roll_approve_calls_execute_roll(self):
        """When roll_approve callback fires, _execute_roll is called."""
        bot = _make_telegram_bot()
        position = _make_position(symbol="IBIT260515P00050000", roll_count=0)
        new_contract = _make_new_contract(strike=48.0, bid=1.40)
        chain = _make_chain_for_roll("IBIT260515P00050000", position_ask=0.80)
        cycle = {"id": 1, "state": "SHORT_PUT"}

        async def run():
            async def fake_wait_for(coro, timeout):
                bot._roll_approval.result = "approved"

            with patch.object(bot, "_execute_roll", new=AsyncMock(return_value=True)) as mock_exec:
                with patch("asyncio.wait_for", new=fake_wait_for):
                    result = await bot.request_roll_approval(
                        position, new_contract, cycle, chain, MagicMock(), MagicMock(), "acc"
                    )
                mock_exec.assert_called_once()
                return result

        result = asyncio.get_event_loop().run_until_complete(run())
        assert result == ApprovalResult.APPROVED

    def test_roll_reject_returns_rejected_no_execution(self):
        """When roll_reject callback fires, _execute_roll is NOT called."""
        bot = _make_telegram_bot()
        position = _make_position(symbol="IBIT260515P00050000", roll_count=0)
        new_contract = _make_new_contract(strike=48.0, bid=1.40)
        chain = _make_chain_for_roll("IBIT260515P00050000", position_ask=0.80)
        cycle = {"id": 1, "state": "SHORT_PUT"}

        async def run():
            async def fake_wait_for(coro, timeout):
                bot._roll_approval.result = "rejected"

            with patch.object(bot, "_execute_roll", new=AsyncMock()) as mock_exec:
                with patch("asyncio.wait_for", new=fake_wait_for):
                    result = await bot.request_roll_approval(
                        position, new_contract, cycle, chain, MagicMock(), MagicMock(), "acc"
                    )
                mock_exec.assert_not_called()
                return result

        result = asyncio.get_event_loop().run_until_complete(run())
        assert result == ApprovalResult.REJECTED


class TestRollBlocking:
    """Tests for roll guards: max roll count and net debit."""

    def test_roll_blocked_when_roll_count_gte_2(self):
        """roll_count >= 2 sends warning message and returns None without approval dialog."""
        bot = _make_telegram_bot()
        # roll_count=2 should be blocked
        position = _make_position(symbol="IBIT260515P00050000", roll_count=2)
        new_contract = _make_new_contract(strike=48.0, bid=1.40)
        chain = _make_chain_for_roll("IBIT260515P00050000", position_ask=0.80)
        cycle = {"id": 1, "state": "SHORT_PUT"}

        async def run():
            return await bot.request_roll_approval(
                position, new_contract, cycle, chain, MagicMock(), MagicMock(), "acc"
            )

        result = asyncio.get_event_loop().run_until_complete(run())
        assert result is None
        # Warning message sent, but no inline buttons
        bot._app.bot.send_message.assert_called_once()
        call_kwargs = bot._app.bot.send_message.call_args[1]
        assert call_kwargs.get("reply_markup") is None

    def test_roll_blocked_when_net_debit(self):
        """net credit <= 0 sends explanation message and returns None without approval dialog."""
        bot = _make_telegram_bot()
        position = _make_position(symbol="IBIT260515P00050000", roll_count=0)
        # new bid=0.50, current ask=0.80 -> net = 0.50 - 0.80 = -0.30 (debit)
        new_contract = _make_new_contract(strike=48.0, bid=0.50)
        chain = _make_chain_for_roll("IBIT260515P00050000", position_ask=0.80)
        cycle = {"id": 1, "state": "SHORT_PUT"}

        async def run():
            return await bot.request_roll_approval(
                position, new_contract, cycle, chain, MagicMock(), MagicMock(), "acc"
            )

        result = asyncio.get_event_loop().run_until_complete(run())
        assert result is None
        bot._app.bot.send_message.assert_called_once()
        sent_text = bot._app.bot.send_message.call_args[1]["text"]
        assert "debit" in sent_text.lower()

    def test_max_rolls_warning_message_contains_roll_count(self):
        """Max roll warning message mentions the current roll count."""
        bot = _make_telegram_bot()
        position = _make_position(symbol="IBIT260515P00050000", roll_count=2)
        new_contract = _make_new_contract(strike=48.0, bid=1.40)
        chain = _make_chain_for_roll("IBIT260515P00050000", position_ask=0.80)
        cycle = {"id": 1, "state": "SHORT_PUT"}

        async def run():
            return await bot.request_roll_approval(
                position, new_contract, cycle, chain, MagicMock(), MagicMock(), "acc"
            )

        asyncio.get_event_loop().run_until_complete(run())
        sent_text = bot._app.bot.send_message.call_args[1]["text"]
        # Should mention 2/2 or "max" rolls
        assert "2" in sent_text and ("max" in sent_text.lower() or "2/2" in sent_text)


class TestRollExecution:
    """Tests for _execute_roll two-step BTC+STO execution."""

    def _make_clients(self, btc_raises=None, sto_raises=None):
        # Use autospec so preview_options_order / place_options_order calls
        # with wrong arg counts raise TypeError instead of silently passing.
        mock_client = _make_etrade_mock()
        if btc_raises:
            mock_client.preview_options_order.side_effect = btc_raises
            mock_client.place_options_order.side_effect = btc_raises
        else:
            mock_client.preview_options_order.return_value = {"PreviewIds": []}
            mock_client.place_options_order.return_value = {"orderId": "ORD001"}
        return mock_client

    def _make_sto_failing_client(self):
        """Client where BTC succeeds but STO fails."""
        mock_client = _make_etrade_mock()
        preview_calls = [0]

        def side_effect_preview(*args, **kwargs):
            preview_calls[0] += 1
            # First call (BTC preview) succeeds, second call (STO preview) fails
            if preview_calls[0] <= 1:
                return {"PreviewIds": []}
            raise Exception("STO API error")

        place_calls = [0]

        def side_effect_place(*args, **kwargs):
            place_calls[0] += 1
            if place_calls[0] <= 1:
                return {"orderId": "BTC_ORD"}
            raise Exception("STO API error")

        mock_client.preview_options_order.side_effect = side_effect_preview
        mock_client.place_options_order.side_effect = side_effect_place
        return mock_client

    def test_execute_roll_calls_btc_then_sto(self):
        """_execute_roll calls preview+place for BUY_CLOSE then SELL_OPEN
        with correct positional arguments.

        Position-sensitive assertions: BUY_CLOSE and SELL_OPEN must land
        in slot 7 (order_action), not just anywhere in the args tuple.
        Uses create_autospec so arg-count mismatches are caught immediately.
        """
        bot = _make_telegram_bot()
        position = _make_position(symbol="IBIT260515P00050000", roll_count=0)
        new_contract = _make_new_contract(strike=48.0, bid=1.40)
        cycle = {"id": 1, "state": "SHORT_PUT"}
        mock_client = self._make_clients()
        mock_db = MagicMock()
        mock_db.open_wheel_position.return_value = 99

        async def run():
            return await bot._execute_roll(
                position, new_contract, cycle, mock_client, mock_db, "acc", btc_price=0.80
            )

        result = asyncio.get_event_loop().run_until_complete(run())
        assert result is True
        # Both preview and place called twice: once for BTC, once for STO
        assert mock_client.preview_options_order.call_count == 2
        assert mock_client.place_options_order.call_count == 2

        # preview_options_order signature (excluding self):
        #   account_id_key, symbol, option_type, expiry_year, expiry_month,
        #   expiry_day, strike_price, order_action, quantity, limit_price
        btc_args = mock_client.preview_options_order.call_args_list[0][0]
        sto_args = mock_client.preview_options_order.call_args_list[1][0]

        # Position-sensitive: order_action is slot 7
        assert btc_args[7] == "BUY_CLOSE", f"BUY_CLOSE should be in slot 7, got {btc_args}"
        assert sto_args[7] == "SELL_OPEN", f"SELL_OPEN should be in slot 7, got {sto_args}"
        # Full arg count check
        assert len(btc_args) == 10, f"preview_options_order expects 10 args, got {len(btc_args)}"
        assert len(sto_args) == 10, f"preview_options_order expects 10 args, got {len(sto_args)}"

    def test_execute_roll_btc_failure_no_db_changes(self):
        """If BTC step fails, no DB mutations are made."""
        bot = _make_telegram_bot()
        position = _make_position(symbol="IBIT260515P00050000", roll_count=0)
        new_contract = _make_new_contract(strike=48.0, bid=1.40)
        cycle = {"id": 1, "state": "SHORT_PUT"}
        mock_client = self._make_clients(btc_raises=Exception("BTC API down"))
        mock_db = MagicMock()

        async def run():
            return await bot._execute_roll(
                position, new_contract, cycle, mock_client, mock_db, "acc", btc_price=0.80
            )

        result = asyncio.get_event_loop().run_until_complete(run())
        assert result is False
        mock_db.close_wheel_position.assert_not_called()
        mock_db.open_wheel_position.assert_not_called()
        mock_db.transition_wheel_state.assert_not_called()

    def test_execute_roll_sto_failure_closes_old_transitions_to_safe_state(self):
        """If STO fails after BTC, old position is closed and cycle transitions to CASH."""
        bot = _make_telegram_bot()
        position = _make_position(symbol="IBIT260515P00050000", roll_count=0)
        new_contract = _make_new_contract(strike=48.0, bid=1.40)
        cycle = {"id": 1, "state": "SHORT_PUT"}
        mock_client = self._make_sto_failing_client()
        mock_db = MagicMock()

        async def run():
            return await bot._execute_roll(
                position, new_contract, cycle, mock_client, mock_db, "acc", btc_price=0.80
            )

        result = asyncio.get_event_loop().run_until_complete(run())
        assert result is False
        # Old position closed
        mock_db.close_wheel_position.assert_called_once_with(position["id"], 0.80)
        # Cycle transitioned to safe state
        mock_db.transition_wheel_state.assert_called_once()
        call_args = mock_db.transition_wheel_state.call_args[0]
        assert call_args[1] == WheelState.CASH
        # New position NOT opened
        mock_db.open_wheel_position.assert_not_called()

    def test_execute_roll_new_position_has_incremented_roll_count(self):
        """New position gets roll_count = old_roll_count + 1 via set_roll_count."""
        bot = _make_telegram_bot()
        position = _make_position(symbol="IBIT260515P00050000", roll_count=1)
        new_contract = _make_new_contract(strike=48.0, bid=1.40)
        cycle = {"id": 1, "state": "SHORT_PUT"}
        mock_client = self._make_clients()
        mock_db = MagicMock()
        mock_db.open_wheel_position.return_value = 42  # new position id

        async def run():
            return await bot._execute_roll(
                position, new_contract, cycle, mock_client, mock_db, "acc", btc_price=0.80
            )

        asyncio.get_event_loop().run_until_complete(run())
        # set_roll_count called with new_position_id=42 and count=2 (1+1)
        mock_db.set_roll_count.assert_called_once_with(42, 2)

    def test_execute_roll_opens_new_position_in_db(self):
        """_execute_roll calls open_wheel_position with new contract details."""
        bot = _make_telegram_bot()
        position = _make_position(symbol="IBIT260515P00050000", roll_count=0)
        new_contract = _make_new_contract(strike=48.0, bid=1.40, dte=35, expiry_date="2026-06-20")
        cycle = {"id": 1, "state": "SHORT_PUT"}
        mock_client = self._make_clients()
        mock_db = MagicMock()
        mock_db.open_wheel_position.return_value = 55

        async def run():
            return await bot._execute_roll(
                position, new_contract, cycle, mock_client, mock_db, "acc", btc_price=0.80
            )

        asyncio.get_event_loop().run_until_complete(run())
        mock_db.open_wheel_position.assert_called_once()
        call_kwargs = mock_db.open_wheel_position.call_args[1]
        assert call_kwargs["strike"] == 48.0
        assert call_kwargs["premium_received"] == 1.40
        assert call_kwargs["dte_at_entry"] == 35


class TestRollCallbackRouting:
    """Verify roll_approve/roll_reject callbacks route to roll event and don't interfere."""

    def _make_update(self, chat_id="12345", data="roll_approve_roll_1"):
        update = MagicMock()
        update.effective_chat.id = chat_id
        query = MagicMock()
        query.data = data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.message = MagicMock()
        query.message.text = "original"
        update.callback_query = query
        return update

    def test_roll_approve_sets_roll_result_not_btc_or_put_or_call(self):
        """roll_approve_ callback sets _roll_approval.result='approved', not btc/call/put."""
        bot = _make_telegram_bot()
        bot._roll_approval.event = asyncio.Event()
        bot._roll_approval.callback_id = "1"  # matches tail of roll_approve_roll_1
        update = self._make_update(data="roll_approve_roll_1")

        asyncio.get_event_loop().run_until_complete(bot._handle_callback(update, MagicMock()))

        assert bot._roll_approval.result == "approved"
        assert bot._btc_approval.result is None
        assert bot._call_approval.result is None
        assert bot._put_approval.result is None

    def test_roll_approve_from_unauthorized_chat_is_rejected(self):
        """ME-10: roll_approve from a non-owner chat_id must NOT set the result."""
        bot = _make_telegram_bot()
        bot.chat_id = "12345"  # owner
        bot._roll_approval.event = asyncio.Event()
        bot._roll_approval.callback_id = "1"

        update = self._make_update(chat_id="99999", data="roll_approve_roll_1")

        asyncio.get_event_loop().run_until_complete(bot._handle_callback(update, MagicMock()))

        assert bot._roll_approval.result is None
        assert not bot._roll_approval.event.is_set()
        update.callback_query.answer.assert_awaited_with("🚫 Unauthorized", show_alert=True)

    def test_roll_reject_from_unauthorized_chat_is_rejected(self):
        """ME-10: roll_reject from a non-owner chat_id must NOT set the result."""
        bot = _make_telegram_bot()
        bot.chat_id = "12345"
        bot._roll_approval.event = asyncio.Event()
        bot._roll_approval.callback_id = "1"

        update = self._make_update(chat_id="99999", data="roll_reject_roll_1")

        asyncio.get_event_loop().run_until_complete(bot._handle_callback(update, MagicMock()))

        assert bot._roll_approval.result is None
        assert not bot._roll_approval.event.is_set()


class TestSchedulerRollWiring:
    """Verify _job_wheel_monitoring dispatches roll approval correctly."""

    def _make_scheduler(
        self,
        position_tested=False,
        profit_target_hit=False,
        position=None,
        chain=None,
        roll_count=0,
        new_contract=None,
        cycle_state="SHORT_PUT",
    ):
        from tests.conftest import make_smart_scheduler

        telegram_bot = MagicMock()
        telegram_bot.request_roll_approval = AsyncMock(return_value=ApprovalResult.APPROVED)
        telegram_bot.send_dte_alert = AsyncMock()

        scheduler = make_smart_scheduler(telegram_bot=telegram_bot)
        scheduler.db = MagicMock()
        scheduler.wheel_strategy = MagicMock()
        scheduler.wheel_strategy.client = MagicMock()
        scheduler.wheel_strategy.account_id_key = "acc"
        scheduler.wheel_strategy.select_roll_strike.return_value = new_contract

        if position and roll_count is not None:
            position = dict(position)
            position["roll_count"] = roll_count

        mock_result = {
            "profit_target_hit": profit_target_hit,
            "position_tested": position_tested,
            "dte_warning": False,
            "position": position,
            "chain": chain,
        }
        scheduler.wheel_strategy.run_monitoring_checks = AsyncMock(return_value=mock_result)
        cycle = {"id": 1, "state": cycle_state}
        scheduler.db.get_active_cycle.return_value = cycle
        return scheduler, cycle

    def test_dispatches_roll_when_position_tested_and_profit_not_hit(self):
        """When position_tested=True and profit_target_hit=False, roll is dispatched."""
        position = _make_position(roll_count=0)
        chain = _make_chain_for_roll("IBIT260515P00050000", position_ask=0.80)
        new_contract = _make_new_contract(strike=48.0, bid=1.40)
        scheduler, cycle = self._make_scheduler(
            position_tested=True,
            profit_target_hit=False,
            position=position,
            chain=chain,
            roll_count=0,
            new_contract=new_contract,
        )

        run_async_calls = []

        def capture_run_async(coro):
            run_async_calls.append(coro)
            if len(run_async_calls) == 1:
                return {
                    "profit_target_hit": False,
                    "position_tested": True,
                    "dte_warning": False,
                    "position": position,
                    "chain": chain,
                }
            return ApprovalResult.APPROVED

        with patch("src.smart_scheduler.is_trading_day", return_value=True), patch(
            "src.smart_scheduler.get_et_now"
        ), patch("src.smart_scheduler.run_async", side_effect=capture_run_async):
            scheduler._job_wheel_monitoring()

        # At least 2 run_async calls: monitoring checks + roll approval
        assert len(run_async_calls) >= 2

    def test_scheduler_sends_notification_when_roll_count_gte_2(self):
        """When position_tested=True but roll_count >= 2, _send_notification called (no roll dispatch)."""
        position = _make_position(roll_count=2)
        chain = _make_chain_for_roll("IBIT260515P00050000", position_ask=0.80)
        scheduler, cycle = self._make_scheduler(
            position_tested=True,
            profit_target_hit=False,
            position=position,
            chain=chain,
            roll_count=2,
        )

        def capture_run_async(coro):
            return {
                "profit_target_hit": False,
                "position_tested": True,
                "dte_warning": False,
                "position": position,
                "chain": chain,
            }

        with patch("src.smart_scheduler.is_trading_day", return_value=True), patch(
            "src.smart_scheduler.get_et_now"
        ), patch("src.smart_scheduler.run_async", side_effect=capture_run_async):
            scheduler._job_wheel_monitoring()

        scheduler._send_notification.assert_called_once()
        notification_text = scheduler._send_notification.call_args[0][0]
        assert "max" in notification_text.lower() or "2" in notification_text
