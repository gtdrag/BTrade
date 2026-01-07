"""
TelegramBot base class with core functionality.

This module contains:
- TelegramBot class initialization and lifecycle
- Core messaging methods (send_message, send_error_alert, etc.)
- Approval request handling
- Basic command handlers (/start, /help, /status, /test)

Command handlers for specific domains are in separate modules:
- trading_commands.py - Balance, positions, signal, mode, pause, resume
- analysis_commands.py - Analyze, patterns, analyses, promote, retire, review
- auth_commands.py - Auth, verify
- backtest_commands.py - Backtest, simulate
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from .utils import ApprovalResult, TradeApprovalRequest, escape_markdown

if TYPE_CHECKING:
    from ..smart_scheduler import SmartScheduler
    from ..trading_bot import TradingBot

logger = logging.getLogger(__name__)


class TelegramBotBase:
    """
    Base Telegram bot with core functionality.

    This class provides:
    - Bot initialization and lifecycle management
    - Core messaging methods
    - Approval workflow
    - Basic commands (/start, /help, /status, /test)

    Subclass this and add domain-specific command handlers.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,
        approval_timeout_minutes: int = 10,
        scheduler: Optional["SmartScheduler"] = None,
        trading_bot: Optional["TradingBot"] = None,
    ):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        self.approval_timeout = approval_timeout_minutes * 60  # Convert to seconds

        # References for interactive commands
        self.scheduler = scheduler
        self.trading_bot = trading_bot
        self._is_paused = False
        self._pending_auth_request = None  # Stores request token during OAuth flow

        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN not set")

        self._app: Optional[Application] = None
        self._pending_approval: Optional[TradeApprovalRequest] = None
        self._approval_event: Optional[asyncio.Event] = None
        self._approval_result: Optional[ApprovalResult] = None
        self._is_running = False

    def _is_authorized(self, update: Update) -> bool:
        """
        Check if the sender is authorized to use this bot.

        Security: Only the configured chat_id can execute commands.
        This prevents unauthorized users from controlling the trading bot.
        """
        if not self.chat_id:
            logger.warning("Authorization check failed: No chat_id configured")
            return False

        sender_chat_id = str(update.effective_chat.id)
        authorized = sender_chat_id == str(self.chat_id)

        if not authorized:
            logger.warning(
                f"Unauthorized access attempt from chat_id: {sender_chat_id} "
                f"(expected: {self.chat_id})"
            )

        return authorized

    async def _send_unauthorized_response(self, update: Update):
        """Send a response to unauthorized users."""
        await update.message.reply_text(
            "🚫 Unauthorized\n\n"
            "You are not authorized to control this bot.\n"
            "This incident has been logged."
        )

    async def initialize(self):
        """Initialize the bot application. Override to add more handlers."""
        self._app = Application.builder().token(self.token).build()

        # Add basic command handlers
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("test", self._cmd_test))

        # Add callback handler for inline buttons
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))

        # Add error handler
        self._app.add_error_handler(self._error_handler)

        await self._app.initialize()
        logger.info("Telegram bot initialized")

    def _register_command(self, command: str, handler):
        """Register a command handler. Call after initialize()."""
        if self._app:
            self._app.add_handler(CommandHandler(command, handler))

    async def _error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle errors that occur during polling/updates."""
        logger.error(f"Telegram bot error: {context.error}")

        if context.error:
            import traceback

            tb_string = "".join(
                traceback.format_exception(
                    type(context.error), context.error, context.error.__traceback__
                )
            )
            logger.error(f"Telegram error traceback:\n{tb_string}")

        try:
            if self.chat_id:
                error_msg = str(context.error)[:200] if context.error else "Unknown error"
                safe_error = escape_markdown(error_msg)
                await context.bot.send_message(
                    chat_id=self.chat_id,
                    text=f"⚠️ Bot error occurred:\n{safe_error}\n\nBot will continue running.",
                    parse_mode="Markdown",
                )
        except Exception as e:
            logger.warning(f"Could not send error notification: {e}")

    async def start_polling(self):
        """Start the bot in polling mode."""
        if not self._app:
            await self.initialize()

        self._is_running = True
        await self._app.start()
        await self._app.updater.start_polling()
        logger.info("Telegram bot polling started")

    async def stop(self):
        """Stop the bot."""
        if self._app and self._is_running:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            self._is_running = False
            logger.info("Telegram bot stopped")

    # =========================================================================
    # Core Messaging Methods
    # =========================================================================

    async def send_message(self, text: str, parse_mode: Optional[str] = None) -> bool:
        """Send a message to the configured chat.

        Args:
            text: Message text to send
            parse_mode: Optional parse mode ("Markdown", "HTML", or None for plain text).
                        Defaults to None to avoid markdown parsing errors with dynamic content.
        """
        if not self._app or not self.chat_id:
            return False

        try:
            await self._app.bot.send_message(chat_id=self.chat_id, text=text, parse_mode=parse_mode)
            return True
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return False

    async def send_error_alert(self, error_type: str, message: str):
        """Send an error alert."""
        safe_message = escape_markdown(message)
        await self.send_message(f"🚨 *{error_type}*\n\n{safe_message}")

    async def send_trade_executed(
        self,
        signal_type: str,
        etf: str,
        action: str,
        shares: int,
        price: float,
        total: float,
    ):
        """Send a trade execution notification."""
        await self.send_message(
            f"✅ *Trade Executed*\n\n"
            f"Signal: {signal_type}\n"
            f"Action: {action} {shares} {etf}\n"
            f"Price: ${price:.2f}\n"
            f"Total: ${total:.2f}"
        )

    # =========================================================================
    # Approval Workflow
    # =========================================================================

    async def request_trade_approval(
        self,
        signal_type: str,
        etf: str,
        reason: str,
        shares: int,
        price: float,
        position_value: float,
    ) -> ApprovalResult:
        """
        Request trade approval via inline buttons.

        Returns ApprovalResult after user responds or timeout.
        """
        self._pending_approval = TradeApprovalRequest(
            signal_type=signal_type,
            etf=etf,
            reason=reason,
            shares=shares,
            price=price,
            position_value=position_value,
            timestamp=datetime.now(),
        )

        self._approval_event = asyncio.Event()
        self._approval_result = None

        keyboard = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_{signal_type}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_{signal_type}"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        safe_reason = escape_markdown(reason)
        message = (
            f"🔔 *Trade Approval Required*\n\n"
            f"Signal: {signal_type}\n"
            f"ETF: {etf}\n"
            f"Shares: {shares}\n"
            f"Price: ${price:.2f}\n"
            f"Total: ${position_value:.2f}\n\n"
            f"Reason: {safe_reason}\n\n"
            f"_Waiting for your response..._"
        )

        try:
            await self._app.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                reply_markup=reply_markup,
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Failed to send approval request: {e}")
            return ApprovalResult.ERROR

        # Wait for response with timeout
        try:
            await asyncio.wait_for(self._approval_event.wait(), timeout=self.approval_timeout)
            return self._approval_result or ApprovalResult.ERROR
        except asyncio.TimeoutError:
            logger.warning("Trade approval timed out")
            return ApprovalResult.TIMEOUT
        finally:
            self._pending_approval = None
            self._approval_event = None

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline button callbacks."""
        if not self._is_authorized(update):
            query = update.callback_query
            await query.answer("🚫 Unauthorized", show_alert=True)
            return

        query = update.callback_query
        await query.answer()

        data = query.data
        logger.info(f"Received callback: {data}")

        # Handle approval callbacks
        if data.startswith("approve_"):
            self._approval_result = ApprovalResult.APPROVED
            await query.edit_message_text(
                text=query.message.text + "\n\n✅ *APPROVED* - Executing trade...",
                parse_mode="Markdown",
            )
            if self._approval_event:
                self._approval_event.set()

        elif data.startswith("reject_"):
            self._approval_result = ApprovalResult.REJECTED
            await query.edit_message_text(
                text=query.message.text + "\n\n❌ *REJECTED* - Trade cancelled",
                parse_mode="Markdown",
            )
            if self._approval_event:
                self._approval_event.set()

    # =========================================================================
    # Basic Command Handlers
    # =========================================================================

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command - shows chat_id for setup."""
        chat_id = update.effective_chat.id

        if self._is_authorized(update):
            await update.message.reply_text(
                f"🤖 *IBIT Trading Bot*\n\n"
                f"✅ Authorized\n"
                f"Your chat ID: `{chat_id}`\n\n"
                f"Use /help for available commands.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                f"🤖 *IBIT Trading Bot*\n\n"
                f"Your chat ID: `{chat_id}`\n\n"
                f"Add this to TELEGRAM_CHAT_ID to authorize.",
                parse_mode="Markdown",
            )

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        await update.message.reply_text(
            "*IBIT Trading Bot Commands*\n\n"
            "*Basic:*\n"
            "/status - Bot status\n"
            "/help - This help\n"
            "/test - Send test message\n\n"
            "*Trading:*\n"
            "/balance - Account balance\n"
            "/positions - Open positions\n"
            "/signal - Current signal\n"
            "/mode [paper|live] - Switch mode\n"
            "/pause - Pause trading\n"
            "/resume - Resume trading\n\n"
            "*Analysis:*\n"
            "/analyze - Run analysis\n"
            "/patterns - View patterns\n"
            "/review - Strategy review\n"
            "/backtest - Run backtest\n\n"
            "*System:*\n"
            "/jobs - Scheduled jobs\n"
            "/logs [n] - Recent logs\n"
            "/auth - E*TRADE auth\n",
            parse_mode="Markdown",
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        from ..utils import get_et_now

        now = get_et_now()
        mode = "UNKNOWN"
        if self.trading_bot:
            mode = "LIVE" if not self.trading_bot.is_paper_mode else "PAPER"

        paused = "⏸ PAUSED" if self._is_paused else "▶️ Running"

        lines = [
            "*Bot Status*\n",
            f"Mode: {mode}",
            f"Status: {paused}",
            f"Time: {now.strftime('%I:%M %p ET')}",
            f"Date: {now.strftime('%A, %b %d')}",
        ]

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _cmd_test(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /test command - send a test approval request."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        keyboard = [
            [
                InlineKeyboardButton("✅ Approve", callback_data="approve_test_signal"),
                InlineKeyboardButton("❌ Reject", callback_data="reject_test_signal"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            "🧪 *Test Trade Approval*\n\n"
            "Signal: TEST\n"
            "ETF: SBIT\n"
            "Shares: 100\n"
            "Price: $50.00\n"
            "Total: $5,000.00\n\n"
            "_This is a test - no real trade will execute._",
            reply_markup=reply_markup,
            parse_mode="Markdown",
        )
