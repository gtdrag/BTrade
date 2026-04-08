"""
Tests for profit management: DB migration, WheelStrategy monitoring methods,
and SmartScheduler monitoring job.

TDD test file — tests written first against the spec in 05-01-PLAN.md.
"""

import os
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database import Database
from src.wheel_state import WheelState


# ---------------------------------------------------------------------------
# Shared helpers / fixtures
# ---------------------------------------------------------------------------

def _make_contract(
    option_type: str,
    strike: float,
    delta: float,
    bid: float,
    ask: float = None,
    dte: int = 35,
    symbol: str = None,
    iv: float = 0.35,
    gamma: float = 0.05,
    theta: float = -0.04,
    vega: float = 0.10,
    expiry_year: int = 2026,
    expiry_month: int = 5,
    expiry_day: int = 15,
) -> dict:
    """Build a minimal contract dict matching get_ibit_options_chain() shape."""
    if ask is None:
        ask = bid + 0.10
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
        "ask": ask,
        "last": bid + 0.05,
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega": vega,
        "iv": iv,
    }


@pytest.fixture
def db(tmp_path):
    """Create an isolated test database."""
    db_path = tmp_path / "test_trades.db"
    return Database(str(db_path))


def _create_cycle_and_position(db: Database) -> tuple:
    """Insert a wheel cycle and an open options position; return (cycle_id, position_id)."""
    from src.utils import get_et_now
    import sqlite3

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
            cursor.execute("SELECT dte_alert_sent FROM options_positions WHERE id = ?", (position_id,))
            row = cursor.fetchone()
        assert row["dte_alert_sent"] == 1

    def test_mark_dte_alert_sent_idempotent(self, db):
        """mark_dte_alert_sent called twice stays at 1 (idempotent)."""
        _, position_id = _create_cycle_and_position(db)
        db.mark_dte_alert_sent(position_id)
        db.mark_dte_alert_sent(position_id)
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT dte_alert_sent FROM options_positions WHERE id = ?", (position_id,))
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


class TestPositionTested:
    """Tests for WheelStrategy.check_position_tested."""

    def _make_strategy(self, ibit_price: float):
        """Build a strategy with mocked get_ibit_quote."""
        from src.wheel_strategy import WheelStrategy
        client = MagicMock()
        client.get_ibit_quote.return_value = {"last_price": ibit_price, "bid": ibit_price - 0.01, "ask": ibit_price + 0.01}
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
        from src.wheel_strategy import WheelStrategy
        from src.etrade_client import ETradeAPIError
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
        client.get_ibit_quote.return_value = {"last_price": ibit_price, "bid": ibit_price - 0.01, "ask": ibit_price + 0.01}
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
        result = asyncio.get_event_loop().run_until_complete(
            strategy.run_monitoring_checks(cycle)
        )
        assert "profit_target_hit" in result
        assert "position_tested" in result
        assert "dte_warning" in result

    def test_returns_all_false_when_no_open_position(self):
        """When no open position, all flags are False."""
        strategy = self._make_strategy(position=None)
        cycle = {"id": 1}
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            strategy.run_monitoring_checks(cycle)
        )
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
        result = asyncio.get_event_loop().run_until_complete(
            strategy.run_monitoring_checks(cycle)
        )
        assert result["profit_target_hit"] is False
        assert result["position_tested"] is False
        assert result["dte_warning"] is False

    def test_position_tested_is_false_when_profit_target_hit(self):
        """When profit target is hit, position_tested is forced to False (profit priority)."""
        # Position with ask at 50% of premium
        position = self._make_position(symbol="IBIT_P50", premium_received=1.50, strike=50.0, expiry_date="2026-06-15")
        chain = [_make_contract("PUT", 50.0, -0.25, 0.70, ask=0.75, symbol="IBIT_P50")]
        # IBIT price near strike (would normally set position_tested=True)
        strategy = self._make_strategy(ibit_price=50.5, position=position, chain=chain)
        cycle = {"id": 1}
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            strategy.run_monitoring_checks(cycle)
        )
        assert result["profit_target_hit"] is True
        assert result["position_tested"] is False  # profit takes priority

    def test_position_and_chain_included_in_result(self):
        """Result dict includes 'position' and 'chain' keys for Plan 02 use."""
        position = self._make_position(expiry_date="2026-06-15")
        chain = []
        strategy = self._make_strategy(position=position, chain=chain)
        cycle = {"id": 1}
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            strategy.run_monitoring_checks(cycle)
        )
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
        """Build a minimal SmartScheduler mock for monitoring tests."""
        from src.smart_scheduler import SmartScheduler

        scheduler = SmartScheduler.__new__(SmartScheduler)
        scheduler.db = MagicMock()
        scheduler.telegram_bot = MagicMock()
        scheduler._send_notification = MagicMock()
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
        from src.smart_scheduler import SmartScheduler

        scheduler = SmartScheduler.__new__(SmartScheduler)
        scheduler.db = MagicMock()
        scheduler.telegram_bot = MagicMock()
        scheduler._send_notification = MagicMock()
        scheduler._monitoring_approval_pending = False
        scheduler.wheel_strategy = None

        # Mock bot and scheduler objects
        mock_bot = MagicMock()
        mock_bot.client = None
        mock_bot.config.strategy.crash_day_enabled = False
        mock_bot.config.strategy.pump_day_enabled = False
        mock_bot.config.strategy.ten_am_dump_enabled = False
        mock_bot.is_paper_mode = True
        scheduler.bot = mock_bot
        scheduler.scheduler = MagicMock()

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
        scheduler, _ = self._make_scheduler(cycle={"id": 1, "state": WheelState.HOLDING_SHARES.value})
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
