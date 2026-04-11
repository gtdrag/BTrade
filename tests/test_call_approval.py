"""
Tests for Telegram covered call approval flow, callback routing, and scheduler wiring.

Covers:
1. Call approval flow: approve/reject/timeout/separate event isolation
2. _execute_call_order: places sell-to-open CALL, transitions DB, re-validates cost basis
3. _handle_call_adjust: shows alternatives filtered above cost basis
4. Callback routing: call_* prefixes dispatched before generic approve_/reject_
5. Scheduler wiring: call suggestion after assignment detection and OTM call expiry
6. Full-cycle summary (called_away): P&L + annualized return
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
from src.wheel_state import WheelState
from src.wheel_strategy import CallSignal
from tests.conftest import make_telegram_bot as _make_telegram_bot  # HI-01 / LO-02


def _make_db_mock() -> MagicMock:
    """Create a Database mock with signature enforcement (CR-02)."""
    return create_autospec(Database, instance=True)


def _make_client_mock() -> MagicMock:
    """Create an ETradeClient mock with signature enforcement (CR-03)."""
    mock = create_autospec(ETradeClient, instance=True)
    mock.preview_options_order.return_value = {"PreviewIds": [{"previewId": 1}]}
    mock.place_options_order.return_value = {"orderId": "ORD123"}
    return mock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_call_signal(strike: float = 50.0, cost_basis: float = 47.50) -> CallSignal:
    """Build a minimal CallSignal for testing."""
    return CallSignal(
        strike=strike,
        expiry_date="2026-05-15",
        expiry_year=2026,
        expiry_month=5,
        expiry_day=15,
        delta=0.28,
        premium=1.80,
        dte=37,
        total_premium=180.0,
        symbol=f"IBIT260515C{int(strike * 1000):08d}",
        iv=0.38,
        gamma=0.05,
        theta=-0.04,
        vega=0.12,
        cost_basis=cost_basis,
    )


def _make_call_chain(strikes=(48.0, 50.0, 52.0, 54.0, 56.0), cost_basis=47.50) -> list:
    """Build a minimal options chain with CALL contracts at given strikes."""
    contracts = []
    for s in strikes:
        delta = 0.20 + (60.0 - s) * 0.02  # rough approximation, positive deltas for calls
        contracts.append(
            {
                "symbol": f"IBIT260515C{int(s * 1000):08d}",
                "option_type": "CALL",
                "strike": s,
                "delta": delta,
                "bid": 1.50 + (60.0 - s) * 0.10,
                "ask": 1.60 + (60.0 - s) * 0.10,
                "iv": 0.38,
                "gamma": 0.05,
                "theta": -0.04,
                "vega": 0.12,
                "dte": 37,
                "expiry_date": "2026-05-15",
                "expiry_year": 2026,
                "expiry_month": 5,
                "expiry_day": 15,
            }
        )
    return contracts


# ---------------------------------------------------------------------------
# Test 1: TestCallApprovalFlow — approve / reject / timeout / event isolation
# ---------------------------------------------------------------------------


class TestCallApprovalFlow:
    """Verify request_call_approval orchestrates message, event, and execution correctly."""

    def test_approve_places_call_order(self):
        """When user taps Approve, _execute_call_order is called and APPROVED is returned."""
        bot = _make_telegram_bot()
        signal = _make_call_signal()
        chain = _make_call_chain()

        mock_client = _make_client_mock()
        mock_db = _make_db_mock()
        bot._app.bot.send_message = AsyncMock()

        async def run():
            async def fake_wait_for(coro, timeout):
                bot._call_approval.result = "approved"

            with patch.object(
                bot, "_execute_call_order", new=AsyncMock(return_value=True)
            ) as mock_exec:
                with patch("asyncio.wait_for", new=fake_wait_for):
                    result = await bot.request_call_approval(
                        signal, chain, mock_client, mock_db, "acc"
                    )
                mock_exec.assert_called_once()
                return result

        result = asyncio.get_event_loop().run_until_complete(run())
        assert result == ApprovalResult.APPROVED

    def test_reject_cancels_without_order(self):
        """When user taps Reject, _execute_call_order is NOT called and REJECTED is returned."""
        bot = _make_telegram_bot()
        signal = _make_call_signal()
        chain = _make_call_chain()

        mock_client = _make_client_mock()
        mock_db = _make_db_mock()
        bot._app.bot.send_message = AsyncMock()

        async def run():
            async def fake_wait_for(coro, timeout):
                bot._call_approval.result = "rejected"

            with patch.object(
                bot, "_execute_call_order", new=AsyncMock(return_value=False)
            ) as mock_exec:
                with patch("asyncio.wait_for", new=fake_wait_for):
                    result = await bot.request_call_approval(
                        signal, chain, mock_client, mock_db, "acc"
                    )
                mock_exec.assert_not_called()
                return result

        result = asyncio.get_event_loop().run_until_complete(run())
        assert result == ApprovalResult.REJECTED

    def test_timeout_sends_message(self):
        """When asyncio.TimeoutError fires, TIMEOUT is returned and no order placed."""
        bot = _make_telegram_bot()
        signal = _make_call_signal()
        chain = _make_call_chain()

        mock_client = _make_client_mock()
        mock_db = _make_db_mock()
        bot._app.bot.send_message = AsyncMock()

        async def run():
            async def fake_wait_for(coro, timeout):
                raise asyncio.TimeoutError()

            with patch.object(bot, "_execute_call_order", new=AsyncMock()) as mock_exec:
                with patch("asyncio.wait_for", new=fake_wait_for):
                    with patch.object(bot, "send_message", new=AsyncMock()):
                        result = await bot.request_call_approval(
                            signal, chain, mock_client, mock_db, "acc"
                        )
                mock_exec.assert_not_called()
                return result

        result = asyncio.get_event_loop().run_until_complete(run())
        assert result == ApprovalResult.TIMEOUT

    def test_separate_call_approval_event(self):
        """_call_approval.event must be a different object from _put_approval.event and _approval_event."""
        bot = _make_telegram_bot()
        signal = _make_call_signal()
        chain = _make_call_chain()

        # Pre-set the intraday and put approval events
        intraday_event = asyncio.Event()
        put_event = asyncio.Event()
        bot._approval_event = intraday_event
        bot._put_approval.event = put_event

        mock_client = _make_client_mock()
        mock_db = _make_db_mock()
        bot._app.bot.send_message = AsyncMock()

        async def run():
            async def fake_wait_for(coro, timeout):
                bot._call_approval.result = "rejected"

            with patch("asyncio.wait_for", new=fake_wait_for):
                with patch.object(bot, "send_message", new=AsyncMock()):
                    await bot.request_call_approval(signal, chain, mock_client, mock_db, "acc")

            # _call_approval.event was created (a new Event, different from intraday and put)
            assert bot._call_approval.event is not None
            assert bot._call_approval.event is not intraday_event
            assert bot._call_approval.event is not put_event
            # Intraday and put events remain untouched
            assert not intraday_event.is_set()
            assert not put_event.is_set()

        asyncio.get_event_loop().run_until_complete(run())


# ---------------------------------------------------------------------------
# Test 2: TestExecuteCallOrder
# ---------------------------------------------------------------------------


class TestExecuteCallOrder:
    """Verify _execute_call_order invokes all expected E*TRADE and DB methods."""

    def _make_active_cycle(self, cost_basis=47.50, state="HOLDING_SHARES"):
        """Return a minimal cycle dict simulating get_active_cycle()."""
        return {
            "id": 7,
            "state": state,
            "cost_basis": cost_basis,
            "put_strike": 48.0,
            "put_premium_received": 1.50,
            "covered_call_premiums_collected": 0.0,
            "shares_held": 100,
        }

    def test_places_sell_to_open_call(self):
        """On success: preview_options_order + place_options_order with CALL, SELL_OPEN."""
        bot = _make_telegram_bot()
        signal = _make_call_signal(strike=50.0, cost_basis=47.50)

        mock_client = _make_client_mock()
        mock_client.place_options_order.return_value = {"orderId": "ORD456"}

        mock_db = _make_db_mock()
        mock_db.get_active_cycle.return_value = self._make_active_cycle()
        bot._app.bot.send_message = AsyncMock()

        result = asyncio.get_event_loop().run_until_complete(
            bot._execute_call_order(signal, mock_client, mock_db, "acc-key")
        )

        assert result is True
        mock_client.preview_options_order.assert_called_once_with(
            "acc-key", "IBIT", "CALL", 2026, 5, 15, 50.0, "SELL_OPEN", 1, 1.80
        )
        mock_client.place_options_order.assert_called_once_with(
            "acc-key",
            "IBIT",
            "CALL",
            2026,
            5,
            15,
            50.0,
            "SELL_OPEN",
            1,
            1.80,
            preview_ids=[{"previewId": 1}],
        )

    def test_transitions_to_covered_call(self):
        """On success: transition_wheel_state called with COVERED_CALL, reduced cost_basis."""
        bot = _make_telegram_bot()
        signal = _make_call_signal(strike=50.0, cost_basis=47.50)
        # premium = 1.80, so new_cost_basis = 47.50 - 1.80 = 45.70

        mock_client = _make_client_mock()
        mock_client.preview_options_order.return_value = {"PreviewIds": None}
        mock_client.place_options_order.return_value = {"orderId": "ORD789"}

        mock_db = _make_db_mock()
        mock_db.get_active_cycle.return_value = self._make_active_cycle(cost_basis=47.50)
        bot._app.bot.send_message = AsyncMock()

        asyncio.get_event_loop().run_until_complete(
            bot._execute_call_order(signal, mock_client, mock_db, "acc-key")
        )

        mock_db.transition_wheel_state.assert_called_once_with(
            7,
            WheelState.COVERED_CALL,
            "call_sold",
            cost_basis=47.50 - 1.80,
            covered_call_premiums_collected=0.0 + 1.80,
        )

    def test_opens_wheel_position_for_call(self):
        """On success: open_wheel_position called with option_type='CALL'."""
        bot = _make_telegram_bot()
        signal = _make_call_signal(strike=50.0, cost_basis=47.50)

        mock_client = _make_client_mock()
        mock_client.preview_options_order.return_value = {"PreviewIds": None}
        mock_client.place_options_order.return_value = {"orderId": "ORD999"}

        mock_db = _make_db_mock()
        mock_db.get_active_cycle.return_value = self._make_active_cycle()
        bot._app.bot.send_message = AsyncMock()

        asyncio.get_event_loop().run_until_complete(
            bot._execute_call_order(signal, mock_client, mock_db, "acc-key")
        )

        # HI-02 fix: assert with keyword arguments to match production call style
        # and to be resilient against additional optional keyword parameters.
        mock_db.open_wheel_position.assert_called_once_with(
            cycle_id=7,
            symbol=signal.symbol,
            option_type="CALL",
            strike=50.0,
            expiry_date="2026-05-15",
            dte_at_entry=37,
            premium_received=1.80,
            quantity=1,
            delta=signal.delta,
            gamma=signal.gamma,
            theta=signal.theta,
            vega=signal.vega,
            iv=signal.iv,
        )

    def test_rejects_below_cost_basis(self):
        """If signal.strike < cycle.cost_basis at execution time (stale), order NOT placed."""
        bot = _make_telegram_bot()
        # strike=50.0 but cost_basis=52.0 — stale signal, strike is now below basis
        signal = _make_call_signal(strike=50.0, cost_basis=47.50)

        mock_client = _make_client_mock()
        mock_db = _make_db_mock()
        # Simulate DB returning a cycle where cost_basis is now 52.0 (higher than strike)
        mock_db.get_active_cycle.return_value = self._make_active_cycle(cost_basis=52.0)
        bot._app.bot.send_message = AsyncMock()

        result = asyncio.get_event_loop().run_until_complete(
            bot._execute_call_order(signal, mock_client, mock_db, "acc-key")
        )

        assert result is False
        mock_client.preview_options_order.assert_not_called()
        mock_client.place_options_order.assert_not_called()
        mock_db.transition_wheel_state.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3: TestCallAdjust
# ---------------------------------------------------------------------------


class TestCallAdjust:
    """Verify _handle_call_adjust filters alternatives above cost basis."""

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

    def test_shows_alternatives_above_cost_basis(self):
        """_handle_call_adjust only shows strikes >= cost_basis."""
        bot = _make_telegram_bot()
        # cost_basis = 49.0, so only strikes >= 49.0 should appear
        signal = _make_call_signal(strike=50.0, cost_basis=49.0)
        # chain has strikes: 46, 48, 50, 52, 54 — only 50, 52, 54 should be shown
        chain = _make_call_chain(strikes=(46.0, 48.0, 50.0, 52.0, 54.0), cost_basis=49.0)

        bot._call_approval.signal = signal
        bot._call_approval.chain = chain

        _, query = self._make_callback_update("call_adjust_call_103000")

        asyncio.get_event_loop().run_until_complete(
            bot._handle_call_adjust(query, "call_adjust_call_103000")
        )

        # HI-04 fix: unconditional assertions — if reply_markup is missing
        # (a bug), the test should fail loudly instead of silently passing.
        query.edit_message_text.assert_called_once()
        call_args = query.edit_message_text.call_args
        reply_markup = call_args.kwargs.get("reply_markup") or (
            call_args.args[1] if len(call_args.args) > 1 else None
        )
        assert reply_markup is not None, "Expected reply_markup with alternative strikes"

        button_data = [btn.callback_data for row in reply_markup.inline_keyboard for btn in row]
        # Must have at least one alt-strike button (not just the reject button)
        alt_buttons = [bd for bd in button_data if "call_alt_" in bd and "reject" not in bd]
        assert alt_buttons, "Expected at least one call_alt_ button above cost basis"

        for bd in alt_buttons:
            parts = bd.split("_")
            # Let parse errors crash the test — callback format drift is a bug
            strike_val = float(parts[2])
            assert strike_val >= 49.0, f"Strike {strike_val} is below cost_basis 49.0"

    def test_no_alternatives_sends_warning(self):
        """If no strikes above cost_basis in chain, sends warning message."""
        bot = _make_telegram_bot()
        # cost_basis = 60.0 — all strikes in chain are below it
        signal = _make_call_signal(strike=50.0, cost_basis=60.0)
        chain = _make_call_chain(strikes=(46.0, 48.0, 50.0, 52.0), cost_basis=60.0)

        bot._call_approval.signal = signal
        bot._call_approval.chain = chain

        _, query = self._make_callback_update("call_adjust_call_103000")

        asyncio.get_event_loop().run_until_complete(
            bot._handle_call_adjust(query, "call_adjust_call_103000")
        )

        query.edit_message_text.assert_called_once()
        call_args = query.edit_message_text.call_args
        text = call_args.kwargs.get("text") or (call_args.args[0] if call_args.args else "")
        # HI-08 fix: removed overly broad "above" alternative — any message
        # containing the word "above" would have matched. Require a specific
        # phrase indicating no profitable strikes.
        lowered = text.lower()
        assert (
            "no profitable" in lowered or "cost basis" in lowered
        ), f"Expected 'no profitable' or 'cost basis' in warning message, got: {text}"


# ---------------------------------------------------------------------------
# Test 4: TestCallbackRouting
# ---------------------------------------------------------------------------


class TestCallbackRouting:
    """Verify _handle_callback routes call_* prefixes correctly."""

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

    def test_call_approve_prefix_dispatched(self):
        """call_approve_ callback sets _call_approval.result='approved' and fires event."""
        bot = _make_telegram_bot()
        event = asyncio.Event()
        bot._call_approval.event = event
        bot._call_approval.callback_id = "103000"

        update, query = self._make_callback_update("call_approve_call_103000")

        with patch.object(bot, "_is_authorized", return_value=True):
            asyncio.get_event_loop().run_until_complete(bot._handle_callback(update, MagicMock()))

        assert bot._call_approval.result == "approved"
        assert event.is_set()

    def test_call_reject_prefix_dispatched(self):
        """call_reject_ callback sets _call_approval.result='rejected' and fires event."""
        bot = _make_telegram_bot()
        event = asyncio.Event()
        bot._call_approval.event = event
        bot._call_approval.callback_id = "103000"

        update, query = self._make_callback_update("call_reject_call_103000")

        with patch.object(bot, "_is_authorized", return_value=True):
            asyncio.get_event_loop().run_until_complete(bot._handle_callback(update, MagicMock()))

        assert bot._call_approval.result == "rejected"
        assert event.is_set()

    def test_call_prefix_before_intraday(self):
        """call_reject_ does NOT trigger _approval_result (intraday); only _call_approval.result."""
        bot = _make_telegram_bot()
        event = asyncio.Event()
        bot._call_approval.event = event
        bot._call_approval.callback_id = "103000"
        bot._approval_event = asyncio.Event()  # intraday event
        bot._approval_result = None  # ensure it starts as None

        update, query = self._make_callback_update("call_reject_call_103000")

        with patch.object(bot, "_is_authorized", return_value=True):
            asyncio.get_event_loop().run_until_complete(bot._handle_callback(update, MagicMock()))

        # call_* must set _call_approval.result
        assert bot._call_approval.result == "rejected"
        # _approval_result (intraday) must NOT be set by call_reject_
        assert bot._approval_result is None

    def test_call_adjust_delegates_to_handle_call_adjust(self):
        """call_adjust_ callback delegates to _handle_call_adjust()."""
        bot = _make_telegram_bot()
        bot._call_approval.chain = _make_call_chain()
        bot._call_approval.signal = _make_call_signal()
        bot._call_approval.callback_id = "103000"

        update, query = self._make_callback_update("call_adjust_call_103000")
        query.edit_message_text = AsyncMock()

        with patch.object(bot, "_is_authorized", return_value=True):
            with patch.object(bot, "_handle_call_adjust", new=AsyncMock()) as mock_adjust:
                asyncio.get_event_loop().run_until_complete(
                    bot._handle_callback(update, MagicMock())
                )
                mock_adjust.assert_called_once()

    def test_call_alt_sets_strike_result(self):
        """call_alt_ callback extracts strike and sets it as _call_approval.result."""
        bot = _make_telegram_bot()
        event = asyncio.Event()
        bot._call_approval.event = event
        bot._call_approval.callback_id = "103000"

        update, query = self._make_callback_update("call_alt_52.0_call_103000")

        with patch.object(bot, "_is_authorized", return_value=True):
            asyncio.get_event_loop().run_until_complete(bot._handle_callback(update, MagicMock()))

        assert bot._call_approval.result == "52.0"
        assert event.is_set()

    def test_call_alt_reject_sets_rejected(self):
        """call_alt_reject_ callback sets _call_approval.result='rejected' and fires event."""
        bot = _make_telegram_bot()
        event = asyncio.Event()
        bot._call_approval.event = event
        bot._call_approval.callback_id = "103000"

        update, query = self._make_callback_update("call_alt_reject_call_103000")

        with patch.object(bot, "_is_authorized", return_value=True):
            asyncio.get_event_loop().run_until_complete(bot._handle_callback(update, MagicMock()))

        assert bot._call_approval.result == "rejected"
        assert event.is_set()


# ---------------------------------------------------------------------------
# Test 5: TestSchedulerCallSuggestion
# ---------------------------------------------------------------------------


class TestSchedulerCallSuggestion:
    """Verify _job_assignment_detection wiring for call suggestions."""

    def _make_scheduler(self, expiry_result="assigned", call_signal_result=None, has_telegram=True):
        """Build a SmartScheduler via the shared conftest factory."""
        from tests.conftest import make_smart_scheduler

        scheduler = make_smart_scheduler(
            telegram_bot=MagicMock() if has_telegram else None,
        )
        scheduler.db = MagicMock()

        scheduler.wheel_strategy = MagicMock()
        scheduler.wheel_strategy.detect_and_process_expiry.return_value = expiry_result
        scheduler.wheel_strategy.get_call_signal.return_value = call_signal_result

        # Mock active cycle for cost_basis/strike reads
        scheduler.wheel_strategy.db = MagicMock()
        scheduler.wheel_strategy.db.get_active_cycle.return_value = {
            "id": 7,
            "state": "HOLDING_SHARES",
            "cost_basis": 47.50,
            "put_strike": 48.0,
        }

        mock_client = MagicMock()
        mock_client.get_ibit_options_chain.return_value = _make_call_chain()
        scheduler.wheel_strategy.client = mock_client
        scheduler.wheel_strategy.account_id_key = "default"

        return scheduler

    def test_suggests_call_after_assignment(self):
        """detect_and_process_expiry returns 'assigned' -> get_call_signal called -> request_call_approval called."""
        call_signal = _make_call_signal()
        scheduler = self._make_scheduler(expiry_result="assigned", call_signal_result=call_signal)

        with patch("src.smart_scheduler.is_trading_day", return_value=True):
            with patch("src.smart_scheduler.run_async") as mock_run_async:
                scheduler._job_assignment_detection()

        scheduler.wheel_strategy.get_call_signal.assert_called_once()
        mock_run_async.assert_called_once()
        # run_async was called with request_call_approval coroutine
        call_arg = mock_run_async.call_args[0][0]
        # The coroutine should come from telegram_bot.request_call_approval
        assert call_arg is not None

    def test_suggests_call_after_otm_expiry(self):
        """detect_and_process_expiry returns 'call_expired_otm' -> get_call_signal called -> request_call_approval called."""
        call_signal = _make_call_signal()
        scheduler = self._make_scheduler(
            expiry_result="call_expired_otm", call_signal_result=call_signal
        )
        # For call_expired_otm, cycle is still active
        scheduler.wheel_strategy.db.get_active_cycle.return_value = {
            "id": 7,
            "state": "HOLDING_SHARES",
            "cost_basis": 45.70,
            "put_strike": 48.0,
        }

        with patch("src.smart_scheduler.is_trading_day", return_value=True):
            with patch("src.smart_scheduler.run_async") as mock_run_async:
                scheduler._job_assignment_detection()

        scheduler.wheel_strategy.get_call_signal.assert_called_once()
        mock_run_async.assert_called_once()

    def test_no_profitable_strikes_sends_warning(self):
        """get_call_signal returns None -> sends 'no profitable strikes' warning, no run_async."""
        scheduler = self._make_scheduler(expiry_result="assigned", call_signal_result=None)

        with patch("src.smart_scheduler.is_trading_day", return_value=True):
            with patch("src.smart_scheduler.run_async") as mock_run_async:
                scheduler._job_assignment_detection()

        scheduler._send_notification.assert_called_once()
        notification_msg = scheduler._send_notification.call_args[0][0]
        assert (
            "no profitable" in notification_msg.lower() or "cost basis" in notification_msg.lower()
        )
        mock_run_async.assert_not_called()

    def test_called_away_sends_summary(self):
        """detect_and_process_expiry returns 'called_away' -> sends full-cycle summary with P&L, annualized return."""
        scheduler = self._make_scheduler(expiry_result="called_away")
        # Provide cycle history for the summary
        scheduler.db.get_cycle_history.return_value = [
            {
                "id": 7,
                "state": "CASH",
                "put_strike": 48.0,
                "put_premium_received": 1.50,
                "covered_call_premiums_collected": 1.80,
                "realized_pnl": 330.0,
                "opened_at": "2026-04-01T09:35:00",
                "closed_at": "2026-05-15T16:00:00",
            }
        ]

        with patch("src.smart_scheduler.is_trading_day", return_value=True):
            with patch("src.smart_scheduler.run_async") as mock_run_async:
                scheduler._job_assignment_detection()

        scheduler._send_notification.assert_called_once()
        notification_msg = scheduler._send_notification.call_args[0][0]

        # HI-03 fix: assert the actual computed values, not substring-only.
        # Expected P&L = 330.0 (from mocked cycle history)
        assert (
            "$330.00" in notification_msg
        ), f"Expected '$330.00' in summary, got: {notification_msg}"

        # Expected annualized return from the mocked 44-day cycle
        # (2026-04-01 to 2026-05-15 = 44 days):
        #   capital_at_risk = 48.0 * 100 = 4800
        #   return_pct = 330 / 4800 * (365 / 44) * 100 = ~57.0%
        # Accept any annualized value that matches the math to 1 decimal.
        import re

        annualized_match = re.search(r"Annualized return: (-?\d+\.\d+)%", notification_msg)
        assert (
            annualized_match is not None
        ), f"Expected 'Annualized return: X.X%' line, got: {notification_msg}"
        actual_annualized = float(annualized_match.group(1))
        expected_annualized = (330.0 / 4800.0) * (365 / 44) * 100
        assert abs(actual_annualized - expected_annualized) < 0.2, (
            f"Annualized return {actual_annualized}% doesn't match "
            f"expected ~{expected_annualized:.1f}%"
        )

        # Should NOT call run_async (no call suggestion on called_away)
        mock_run_async.assert_not_called()
