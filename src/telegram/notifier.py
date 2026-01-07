"""
Synchronous wrapper for TelegramBot.

Use this in the trading bot where we're not running an async event loop.
"""

import asyncio
import logging
import os
from typing import Optional

from .bot import TelegramBot
from .utils import ApprovalResult

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """
    Synchronous wrapper for TelegramBot.

    Use this in the trading bot where we're not running an async event loop.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,
        approval_timeout_minutes: int = 10,
    ):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        self.approval_timeout = approval_timeout_minutes

        if not self.token:
            logger.warning("TELEGRAM_BOT_TOKEN not set - notifications disabled")

    def _run_async(self, coro):
        """Run an async coroutine synchronously.

        Handles all event loop states properly:
        - If running loop exists and open: use run_coroutine_threadsafe
        - If running loop is closed: create isolated new loop
        - If no running loop: create isolated new loop

        Uses new_event_loop() instead of asyncio.run() to avoid corrupting
        global event loop policy state.
        """
        try:
            # Check if there's already a running event loop
            loop = asyncio.get_running_loop()

            # Check if the loop is closed (can happen after errors)
            if loop.is_closed():
                logger.warning("_run_async: Running loop is closed, creating new one")
                return self._run_in_isolated_loop(coro)

            # Running loop exists and is open - schedule on it and wait for result
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            return future.result(timeout=30)
        except RuntimeError as e:
            # No running loop - create an isolated loop
            if "no running" in str(e).lower() or "no current" in str(e).lower():
                return self._run_in_isolated_loop(coro)
            # Some other RuntimeError
            logger.error(f"_run_async: Unexpected error: {e}")
            raise

    def _run_in_isolated_loop(self, coro):
        """Run coroutine in isolated loop without affecting global policy."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def send_message(self, text: str) -> bool:
        """Send a simple message."""
        if not self.token or not self.chat_id:
            return False

        async def _send():
            bot = TelegramBot(self.token, self.chat_id, self.approval_timeout)
            await bot.initialize()
            result = await bot.send_message(text)
            return result

        try:
            return self._run_async(_send())
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return False

    def request_approval(
        self,
        signal_type: str,
        etf: str,
        reason: str,
        shares: int,
        price: float,
        position_value: float,
    ) -> ApprovalResult:
        """Request trade approval (blocking)."""
        if not self.token or not self.chat_id:
            logger.warning("Telegram not configured - auto-approving")
            return ApprovalResult.APPROVED

        async def _request():
            bot = TelegramBot(self.token, self.chat_id, self.approval_timeout)
            await bot.initialize()
            await bot.start_polling()

            try:
                result = await bot.request_trade_approval(
                    signal_type, etf, reason, shares, price, position_value
                )
                return result
            finally:
                await bot.stop()

        try:
            return self._run_async(_request())
        except Exception as e:
            logger.error(f"Failed to request approval: {e}")
            return ApprovalResult.ERROR

    def notify_trade_executed(
        self,
        signal_type: str,
        etf: str,
        action: str,
        shares: int,
        price: float,
        total: float,
    ):
        """Notify that a trade was executed."""
        if not self.token or not self.chat_id:
            return

        async def _notify():
            bot = TelegramBot(self.token, self.chat_id)
            await bot.initialize()
            await bot.send_trade_executed(signal_type, etf, action, shares, price, total)

        try:
            self._run_async(_notify())
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")

    def notify_error(self, error_type: str, message: str):
        """Send an error alert."""
        if not self.token or not self.chat_id:
            return

        async def _notify():
            bot = TelegramBot(self.token, self.chat_id)
            await bot.initialize()
            await bot.send_error_alert(error_type, message)

        try:
            self._run_async(_notify())
        except Exception as e:
            logger.error(f"Failed to send error alert: {e}")
