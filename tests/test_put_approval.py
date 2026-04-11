"""
Tests for Telegram put approval flow and SmartScheduler put signal job.

Covers:
1. Approval flow calls preview + place + DB methods
2. Reject flow does NOT call preview/place
3. Callback routing for each prefix
4. Scheduler job skips on non-trading day
5. Scheduler job skips when no signal
6. Scheduler job calls request_put_approval when signal fires
7. Separate _put_approval.event (not shared with _approval_event)
8. _execute_put_order creates cycle and transitions to SHORT_PUT
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database import Database
from src.etrade_client import ETradeClient
from src.telegram.utils import ApprovalResult
from src.wheel_strategy import PutSignal


def _make_db_mock() -> MagicMock:
    """Create a Database mock with signature enforcement (CR-02).

    create_autospec inspects Database and makes every method signature-
    checked, so a call with the wrong number or type of arguments raises
    TypeError at test time — this is what catches the class of bugs where
    production code drifts away from a method signature but MagicMock
    silently accepts the mismatch.
    """
    return create_autospec(Database, instance=True)


def _make_client_mock() -> MagicMock:
    """Create an ETradeClient mock with signature enforcement (CR-03).

    Gives useful default return values for the options flow so tests
    don't have to set them up when they only care about call arguments.
    """
    mock = create_autospec(ETradeClient, instance=True)
    mock.preview_options_order.return_value = {"PreviewIds": [{"previewId": 1}]}
    mock.place_options_order.return_value = {"orderId": "ORD123"}
    return mock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_put_signal(strike: float = 48.0) -> PutSignal:
    """Build a minimal PutSignal for testing."""
    return PutSignal(
        strike=strike,
        expiry_date="2026-05-15",
        expiry_year=2026,
        expiry_month=5,
        expiry_day=15,
        delta=-0.25,
        premium=1.50,
        dte=37,
        max_risk=strike * 100,
        symbol=f"IBIT260515P{int(strike * 1000):08d}",
        iv=0.35,
        gamma=0.05,
        theta=-0.04,
        vega=0.10,
        pullback_pct=-2.5,
    )


def _make_chain(strikes=(46.0, 47.0, 48.0, 49.0, 50.0)) -> list:
    """Build a minimal options chain with PUT contracts at given strikes."""
    contracts = []
    for s in strikes:
        delta = -(0.20 + (50.0 - s) * 0.02)  # rough approximation
        contracts.append(
            {
                "symbol": f"IBIT260515P{int(s * 1000):08d}",
                "option_type": "PUT",
                "strike": s,
                "delta": delta,
                "bid": 1.20 + (50.0 - s) * 0.15,
                "ask": 1.30 + (50.0 - s) * 0.15,
                "iv": 0.35,
                "gamma": 0.05,
                "theta": -0.04,
                "vega": 0.10,
                "dte": 37,
                "expiry_date": "2026-05-15",
                "expiry_year": 2026,
                "expiry_month": 5,
                "expiry_day": 15,
            }
        )
    return contracts


def _make_telegram_bot():
    """Create a minimal TelegramBot-like mock with required instance vars."""
    from src.telegram.bot import TelegramBot

    # Patch dependencies so __init__ doesn't fail without real tokens
    with patch("src.telegram.bot.AnalysisCommandsMixin"), patch(
        "src.telegram.bot.AuthCommandsMixin"
    ), patch("src.telegram.bot.BacktestCommandsMixin"), patch(
        "src.telegram.bot.TradingCommandsMixin"
    ):
        bot = TelegramBot.__new__(TelegramBot)

    # Manually set required instance vars
    bot.token = "fake_token"
    bot.chat_id = "12345"
    bot.approval_timeout = 600
    bot.scheduler = None
    bot.trading_bot = None
    bot._is_paused = False
    bot._pending_auth_request = None
    bot._app = MagicMock()
    bot._pending_approval = None
    bot._approval_event = None
    bot._approval_result = None
    bot._pending_sellall = None
    bot._is_running = False
    from src.telegram.approval_flow import ApprovalFlow

    bot._put_approval = ApprovalFlow()
    bot._call_approval = ApprovalFlow()
    bot._btc_approval = ApprovalFlow()
    bot._roll_approval = ApprovalFlow()
    return bot


# ---------------------------------------------------------------------------
# Test 1: _execute_put_order calls preview, place, create_wheel_cycle, transition, open
# ---------------------------------------------------------------------------


class TestPutOrderExecution:
    """Verify _execute_put_order invokes all expected E*TRADE and DB methods."""

    def test_execute_put_order_calls_preview_and_place(self):
        """On success: preview_options_order and place_options_order are both called."""
        bot = _make_telegram_bot()
        signal = _make_put_signal(strike=48.0)

        mock_client = _make_client_mock()
        mock_db = _make_db_mock()
        # open_short_put_cycle returns (cycle_id, position_id) atomically
        mock_db.open_short_put_cycle.return_value = (7, 42)

        bot._app.bot.send_message = AsyncMock()

        result = asyncio.get_event_loop().run_until_complete(
            bot._execute_put_order(signal, mock_client, mock_db, "acc-key")
        )

        assert result is True
        mock_client.preview_options_order.assert_called_once_with(
            "acc-key", "IBIT", "PUT", 2026, 5, 15, 48.0, "SELL_OPEN", 1, 1.50
        )
        mock_client.place_options_order.assert_called_once_with(
            "acc-key",
            "IBIT",
            "PUT",
            2026,
            5,
            15,
            48.0,
            "SELL_OPEN",
            1,
            1.50,
            preview_ids=[{"previewId": 1}],
        )

    def test_execute_put_order_creates_cycle_atomically(self):
        """On success: open_short_put_cycle called once atomically with all fields."""
        bot = _make_telegram_bot()
        signal = _make_put_signal(strike=48.0)

        mock_client = _make_client_mock()
        mock_client.preview_options_order.return_value = {"PreviewIds": None}
        mock_client.place_options_order.return_value = {"orderId": "ORD999"}

        mock_db = _make_db_mock()
        mock_db.open_short_put_cycle.return_value = (5, 42)

        bot._app.bot.send_message = AsyncMock()

        asyncio.get_event_loop().run_until_complete(
            bot._execute_put_order(signal, mock_client, mock_db, "acc-key")
        )

        # Single atomic call replaces create_wheel_cycle + transition + open_wheel_position
        mock_db.open_short_put_cycle.assert_called_once_with(
            symbol=signal.symbol,
            strike=48.0,
            premium_received=1.50,
            expiry_date="2026-05-15",
            dte_at_entry=37,
            delta=signal.delta,
            gamma=signal.gamma,
            theta=signal.theta,
            vega=signal.vega,
            iv=signal.iv,
            quantity=1,
        )
        # Old non-atomic methods should NOT be called anymore
        mock_db.create_wheel_cycle.assert_not_called()
        mock_db.transition_wheel_state.assert_not_called()
        mock_db.open_wheel_position.assert_not_called()

    def test_execute_put_order_does_not_create_cycle_on_failure(self):
        """On E*TRADE API failure: open_short_put_cycle must NOT be called."""
        from src.etrade_client import ETradeAPIError

        bot = _make_telegram_bot()
        signal = _make_put_signal()

        mock_client = _make_client_mock()
        mock_client.preview_options_order.side_effect = ETradeAPIError("API error")

        mock_db = _make_db_mock()
        bot._app.bot.send_message = AsyncMock()

        result = asyncio.get_event_loop().run_until_complete(
            bot._execute_put_order(signal, mock_client, mock_db, "acc-key")
        )

        assert result is False
        mock_db.open_short_put_cycle.assert_not_called()
        mock_db.create_wheel_cycle.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: request_put_approval — approve path calls _execute_put_order
# ---------------------------------------------------------------------------


class TestPutApprovalFlow:
    """Verify request_put_approval orchestrates message, event, and execution correctly."""

    def test_approval_approved_calls_execute_put_order(self):
        """When user taps Approve, _execute_put_order is called and APPROVED is returned."""
        bot = _make_telegram_bot()
        signal = _make_put_signal()
        chain = _make_chain()

        mock_client = _make_client_mock()
        mock_db = _make_db_mock()
        bot._app.bot.send_message = AsyncMock()

        async def run():
            # Simulate user tapping Approve mid-wait
            async def fake_wait_for(coro, timeout):
                # Set result and mark event done
                bot._put_approval.result = "approved"

            with patch.object(
                bot, "_execute_put_order", new=AsyncMock(return_value=True)
            ) as mock_exec:
                with patch("asyncio.wait_for", new=fake_wait_for):
                    result = await bot.request_put_approval(
                        signal, chain, mock_client, mock_db, "acc"
                    )
                mock_exec.assert_called_once()
                return result

        result = asyncio.get_event_loop().run_until_complete(run())
        assert result == ApprovalResult.APPROVED

    def test_approval_rejected_does_not_call_execute(self):
        """When user taps Reject, _execute_put_order is NOT called and REJECTED is returned."""
        bot = _make_telegram_bot()
        signal = _make_put_signal()
        chain = _make_chain()

        mock_client = _make_client_mock()
        mock_db = _make_db_mock()
        bot._app.bot.send_message = AsyncMock()

        async def run():
            async def fake_wait_for(coro, timeout):
                bot._put_approval.result = "rejected"

            with patch.object(
                bot, "_execute_put_order", new=AsyncMock(return_value=False)
            ) as mock_exec:
                with patch("asyncio.wait_for", new=fake_wait_for):
                    result = await bot.request_put_approval(
                        signal, chain, mock_client, mock_db, "acc"
                    )
                mock_exec.assert_not_called()
                return result

        result = asyncio.get_event_loop().run_until_complete(run())
        assert result == ApprovalResult.REJECTED

    def test_approval_timeout_returns_timeout(self):
        """When asyncio.TimeoutError fires, TIMEOUT is returned and no order placed."""
        bot = _make_telegram_bot()
        signal = _make_put_signal()
        chain = _make_chain()

        mock_client = _make_client_mock()
        mock_db = _make_db_mock()
        bot._app.bot.send_message = AsyncMock()

        async def run():
            async def fake_wait_for(coro, timeout):
                raise asyncio.TimeoutError()

            with patch.object(bot, "_execute_put_order", new=AsyncMock()) as mock_exec:
                with patch("asyncio.wait_for", new=fake_wait_for):
                    with patch.object(bot, "send_message", new=AsyncMock()):
                        result = await bot.request_put_approval(
                            signal, chain, mock_client, mock_db, "acc"
                        )
                mock_exec.assert_not_called()
                return result

        result = asyncio.get_event_loop().run_until_complete(run())
        assert result == ApprovalResult.TIMEOUT

    def test_put_approval_event_is_separate_from_intraday_event(self):
        """_put_approval.event must be a different object from _approval_event."""
        bot = _make_telegram_bot()
        signal = _make_put_signal()
        chain = _make_chain()

        # Pre-set the intraday approval event
        intraday_event = asyncio.Event()
        bot._approval_event = intraday_event

        mock_client = _make_client_mock()
        mock_db = _make_db_mock()
        bot._app.bot.send_message = AsyncMock()

        async def run():
            async def fake_wait_for(coro, timeout):
                bot._put_approval.result = "rejected"

            with patch("asyncio.wait_for", new=fake_wait_for):
                with patch.object(bot, "send_message", new=AsyncMock()):
                    await bot.request_put_approval(signal, chain, mock_client, mock_db, "acc")

            # _put_approval.event was set (a new Event, different from intraday)
            assert bot._put_approval.event is not None
            assert bot._put_approval.event is not intraday_event
            # Intraday event remains untouched
            assert not intraday_event.is_set()

        asyncio.get_event_loop().run_until_complete(run())


# ---------------------------------------------------------------------------
# Test 3: Callback routing
# ---------------------------------------------------------------------------


class TestPutCallbackRouting:
    """Verify _handle_callback routes put_* prefixes correctly."""

    def _make_callback_update(self, data: str):
        """Build a minimal Update mock with callback_query."""
        update = MagicMock()
        update.effective_chat.id = "12345"
        query = MagicMock()
        query.data = data
        query.message.text = "ORIGINAL"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update.callback_query = query
        return update, query

    def test_put_approve_sets_result_approved(self):
        """put_approve_ callback sets _put_approval.result='approved' and fires event."""
        bot = _make_telegram_bot()
        event = asyncio.Event()
        bot._put_approval.event = event
        # Simulate an active approval flow — callback_id tail must match the
        # trailing token of the callback_data for _is_stale_callback to pass.
        bot._put_approval.callback_id = "103000"

        update, query = self._make_callback_update("put_approve_put_103000")

        with patch.object(bot, "_is_authorized", return_value=True):
            asyncio.get_event_loop().run_until_complete(bot._handle_callback(update, MagicMock()))

        assert bot._put_approval.result == "approved"
        assert event.is_set()

    def test_put_reject_sets_result_rejected(self):
        """put_reject_ callback sets _put_approval.result='rejected' and fires event."""
        bot = _make_telegram_bot()
        event = asyncio.Event()
        bot._put_approval.event = event
        bot._put_approval.callback_id = "103000"

        update, query = self._make_callback_update("put_reject_put_103000")

        with patch.object(bot, "_is_authorized", return_value=True):
            asyncio.get_event_loop().run_until_complete(bot._handle_callback(update, MagicMock()))

        assert bot._put_approval.result == "rejected"
        assert event.is_set()

    def test_put_adjust_calls_handle_put_adjust(self):
        """put_adjust_ callback delegates to _handle_put_adjust()."""
        bot = _make_telegram_bot()
        bot._put_approval.chain = _make_chain()
        bot._put_approval.signal = _make_put_signal()
        bot._put_approval.callback_id = "103000"

        update, query = self._make_callback_update("put_adjust_put_103000")
        query.edit_message_text = AsyncMock()

        with patch.object(bot, "_is_authorized", return_value=True):
            with patch.object(bot, "_handle_put_adjust", new=AsyncMock()) as mock_adjust:
                asyncio.get_event_loop().run_until_complete(
                    bot._handle_callback(update, MagicMock())
                )
                mock_adjust.assert_called_once()

    def test_put_alt_reject_sets_rejected(self):
        """put_alt_reject_ callback sets _put_approval.result='rejected' and fires event."""
        bot = _make_telegram_bot()
        event = asyncio.Event()
        bot._put_approval.event = event
        bot._put_approval.callback_id = "103000"

        update, query = self._make_callback_update("put_alt_reject_put_103000")

        with patch.object(bot, "_is_authorized", return_value=True):
            asyncio.get_event_loop().run_until_complete(bot._handle_callback(update, MagicMock()))

        assert bot._put_approval.result == "rejected"
        assert event.is_set()

    def test_put_alt_sets_strike_result(self):
        """put_alt_ callback extracts strike and sets it as _put_approval.result."""
        bot = _make_telegram_bot()
        event = asyncio.Event()
        bot._put_approval.event = event
        bot._put_approval.callback_id = "103000"

        update, query = self._make_callback_update("put_alt_47.0_put_103000")

        with patch.object(bot, "_is_authorized", return_value=True):
            asyncio.get_event_loop().run_until_complete(bot._handle_callback(update, MagicMock()))

        assert bot._put_approval.result == "47.0"
        assert event.is_set()

    def test_stale_put_approve_is_ignored(self):
        """A callback with a different callback_id tail is treated as stale."""
        bot = _make_telegram_bot()
        event = asyncio.Event()
        bot._put_approval.event = event
        # Current valid callback_id is "999999" — the callback tries "103000"
        bot._put_approval.callback_id = "999999"

        update, query = self._make_callback_update("put_approve_put_103000")

        with patch.object(bot, "_is_authorized", return_value=True):
            asyncio.get_event_loop().run_until_complete(bot._handle_callback(update, MagicMock()))

        assert bot._put_approval.result is None
        assert not event.is_set()

    def test_put_callback_with_no_pending_approval_is_ignored(self):
        """A callback that arrives with no pending approval is treated as stale."""
        bot = _make_telegram_bot()
        event = asyncio.Event()
        bot._put_approval.event = event
        bot._put_approval.callback_id = None  # no pending approval

        update, query = self._make_callback_update("put_approve_put_103000")

        with patch.object(bot, "_is_authorized", return_value=True):
            asyncio.get_event_loop().run_until_complete(bot._handle_callback(update, MagicMock()))

        assert bot._put_approval.result is None
        assert not event.is_set()


# ---------------------------------------------------------------------------
# Test 4: SmartScheduler._job_put_signal_check
# ---------------------------------------------------------------------------


class TestSchedulerPutJob:
    """Verify _job_put_signal_check guards and execution flow."""

    def _make_scheduler(self, has_signal: bool = True, has_telegram: bool = True):
        """Build a minimal SmartScheduler mock."""
        from src.smart_scheduler import SmartScheduler

        scheduler = SmartScheduler.__new__(SmartScheduler)
        scheduler.db = MagicMock()
        scheduler.telegram_bot = MagicMock() if has_telegram else None

        mock_signal = _make_put_signal() if has_signal else None
        mock_client = MagicMock()
        mock_client.get_ibit_options_chain.return_value = _make_chain()

        scheduler.wheel_strategy = MagicMock()
        scheduler.wheel_strategy.get_put_signal.return_value = mock_signal
        scheduler.wheel_strategy.client = mock_client
        scheduler.wheel_strategy.account_id_key = "default"

        scheduler._send_notification = MagicMock()
        return scheduler

    def test_skips_on_non_trading_day(self):
        """Job returns early without calling get_put_signal on weekends/holidays."""
        scheduler = self._make_scheduler()

        with patch("src.smart_scheduler.is_trading_day", return_value=False):
            scheduler._job_put_signal_check()

        scheduler.wheel_strategy.get_put_signal.assert_not_called()

    def test_skips_when_no_wheel_strategy(self):
        """Job returns early when wheel_strategy is None."""
        scheduler = self._make_scheduler()
        scheduler.wheel_strategy = None

        with patch("src.smart_scheduler.is_trading_day", return_value=True):
            scheduler._job_put_signal_check()

        # No exception, just logged and returned
        scheduler.db.log_event.assert_not_called()

    def test_logs_no_signal_when_strategy_returns_none(self):
        """When get_put_signal() returns None, logs no_signal and does NOT call Telegram."""
        scheduler = self._make_scheduler(has_signal=False)

        with patch("src.smart_scheduler.is_trading_day", return_value=True):
            scheduler._job_put_signal_check()

        scheduler.db.log_event.assert_called_once_with(
            "INFO", "put_signal_check", {"result": "no_signal"}
        )
        scheduler.telegram_bot.request_put_approval.assert_not_called()

    def test_calls_request_put_approval_when_signal_fires(self):
        """When signal fires, run_async(request_put_approval(...)) is invoked."""
        scheduler = self._make_scheduler(has_signal=True, has_telegram=True)

        mock_result = ApprovalResult.APPROVED
        with patch("src.smart_scheduler.is_trading_day", return_value=True):
            with patch("src.smart_scheduler.run_async", return_value=mock_result) as mock_run_async:
                scheduler._job_put_signal_check()

        mock_run_async.assert_called_once()
        # Confirm audit log was written for the approval result
        # call_args are (args, kwargs); args[0] and args[1] are event_type and event_name
        logged_events = [(c[0][0], c[0][1]) for c in scheduler.db.log_event.call_args_list]
        assert ("INFO", "put_signal_fired") in logged_events
        assert ("INFO", "put_approval_result") in logged_events

    def test_skips_approval_when_no_telegram_bot(self):
        """When telegram_bot is None and signal fires, job logs and returns without crashing."""
        scheduler = self._make_scheduler(has_signal=True, has_telegram=False)

        with patch("src.smart_scheduler.is_trading_day", return_value=True):
            with patch("src.smart_scheduler.run_async") as mock_run_async:
                scheduler._job_put_signal_check()

        mock_run_async.assert_not_called()

    def test_exception_logged_and_notification_sent(self):
        """If get_put_signal raises, error is logged and notification is sent."""
        scheduler = self._make_scheduler()
        scheduler.wheel_strategy.get_put_signal.side_effect = RuntimeError("chain unavailable")

        with patch("src.smart_scheduler.is_trading_day", return_value=True):
            scheduler._job_put_signal_check()  # must not propagate

        scheduler.db.log_event.assert_called_with(
            "ERROR", "put_signal_check_error", {"error": "chain unavailable"}
        )
        scheduler._send_notification.assert_called_once()
