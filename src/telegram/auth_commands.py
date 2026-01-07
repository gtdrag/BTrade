"""
Authentication command handlers for Telegram bot.

Commands in this module:
- E*TRADE OAuth: /auth, /verify
"""

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from ..async_utils import run_sync_in_executor

if TYPE_CHECKING:
    from .bot import TelegramBot

logger = logging.getLogger(__name__)


class AuthCommandsMixin:
    """
    Mixin class providing authentication-related Telegram commands.

    This class requires the following attributes from the base class:
    - _is_authorized(update) -> bool
    - _send_unauthorized_response(update) -> None
    - trading_bot: Optional[TradingBot]
    - _pending_auth_request: Optional[dict]
    """

    # =========================================================================
    # E*TRADE Authentication Commands
    # =========================================================================

    async def _cmd_auth(self: "TelegramBot", update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /auth command - start E*TRADE OAuth flow."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        if not self.trading_bot:
            await update.message.reply_text("❌ Trading bot not available.")
            return

        # Check if already authenticated
        if self.trading_bot.client and self.trading_bot.client.is_authenticated():
            await update.message.reply_text(
                "✅ *Already Authenticated*\n\n"
                "E*TRADE is already connected.\n"
                "Use /mode live to switch to live trading.",
                parse_mode="Markdown",
            )
            return

        # Check if we have a real E*TRADE client (not mock)
        if self.trading_bot.is_paper_mode:
            # In paper mode, we might not have a real client
            # Check if credentials are configured
            import os

            consumer_key = os.environ.get("ETRADE_CONSUMER_KEY")
            consumer_secret = os.environ.get("ETRADE_CONSUMER_SECRET")

            if not consumer_key or not consumer_secret:
                await update.message.reply_text(
                    "❌ E*TRADE Not Configured\n\n"
                    "Missing API credentials.\n"
                    "Set ETRADE_CONSUMER_KEY and ETRADE_CONSUMER_SECRET\n"
                    "in Railway environment variables."
                )
                return

            # Create client AND get auth URL in thread to avoid event loop conflicts
            # ETradeClient constructor does file I/O and creates OAuth sessions
            from ..etrade_client import ETradeClient

            def create_client_and_get_auth():
                """Create client and get auth URL - must run in thread."""
                client = ETradeClient(consumer_key, consumer_secret)
                auth_url, req_token = client.get_authorization_url()
                return client, auth_url, req_token

            try:
                temp_client, auth_url, request_token = await run_sync_in_executor(
                    create_client_and_get_auth
                )
            except Exception as e:
                logger.error(f"Failed to create client or get auth URL: {e}")
                await update.message.reply_text(f"❌ Failed to connect to E*TRADE: {e}")
                return
        else:
            temp_client = self.trading_bot.client

            if not temp_client:
                await update.message.reply_text("❌ E*TRADE client not available.")
                return

            try:
                # Get authorization URL - run in thread to avoid event loop conflicts
                auth_url, request_token = await run_sync_in_executor(
                    temp_client.get_authorization_url
                )
            except Exception as e:
                logger.error(f"Failed to get auth URL: {e}")
                await update.message.reply_text(
                    f"❌ Authorization Failed\n\nCould not connect to E*TRADE:\n{e}"
                )
                return

        # Store request token for /verify command
        self._pending_auth_request = {
            "request_token": request_token,
            "client": temp_client,
            "timestamp": datetime.now(),
        }

        await update.message.reply_text(
            "🔐 E*TRADE Authorization\n\n"
            "Step 1: Tap the link below to open E*TRADE:\n\n"
            f"{auth_url}\n\n"
            "Step 2: Log in and click 'Authorize'\n\n"
            "Step 3: Copy the verification code shown\n\n"
            "Step 4: Send: /verify YOUR_CODE\n\n"
            "⏱ Link expires in 5 minutes.",
            disable_web_page_preview=True,
        )
        logger.info("E*TRADE auth URL sent to user")

    async def _cmd_verify(self: "TelegramBot", update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /verify command - complete E*TRADE OAuth with verifier code."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        args = context.args

        if not args:
            await update.message.reply_text(
                "❌ *Missing Code*\n\n"
                "Usage: `/verify YOUR_CODE`\n\n"
                "Enter the 5-character code from E*TRADE.",
                parse_mode="Markdown",
            )
            return

        verifier = args[0].strip().upper()

        # Check if we have a pending auth request
        if not self._pending_auth_request:
            await update.message.reply_text(
                "❌ *No Pending Authorization*\n\n"
                "Run /auth first to start the authorization process.",
                parse_mode="Markdown",
            )
            return

        # Check if request hasn't expired (5 min timeout)
        from datetime import timedelta

        age = datetime.now() - self._pending_auth_request["timestamp"]
        if age > timedelta(minutes=5):
            self._pending_auth_request = None
            await update.message.reply_text(
                "❌ *Authorization Expired*\n\n"
                "The authorization request timed out.\n"
                "Run /auth again to start over.",
                parse_mode="Markdown",
            )
            return

        try:
            client = self._pending_auth_request["client"]
            request_token = self._pending_auth_request["request_token"]

            # Complete authorization - run in thread to avoid event loop conflicts
            success = await run_sync_in_executor(
                client.complete_authorization, verifier, request_token
            )

            if success:
                # Update the trading bot's client
                if self.trading_bot:
                    self.trading_bot.client = client

                self._pending_auth_request = None

                await update.message.reply_text(
                    "✅ E*TRADE Connected!\n\n"
                    "Authentication successful.\n\n"
                    "You can now:\n"
                    "• Use /mode live to switch to live trading\n"
                    "• Use /balance to check your account\n\n"
                    "⚠️ Tokens auto-renew daily at 8 AM ET."
                )
                logger.info("E*TRADE authentication completed via Telegram")
            else:
                await update.message.reply_text(
                    "❌ Verification Failed\n\n"
                    "Could not complete authorization.\n"
                    "Please try /auth again."
                )

        except Exception as e:
            logger.error(f"Verification failed: {e}")
            self._pending_auth_request = None
            # Don't use Markdown - error messages may contain special chars
            await update.message.reply_text(
                f"❌ Verification Failed\n\nError: {e}\n\nPlease try /auth again."
            )
