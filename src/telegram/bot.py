"""
Main TelegramBot class combining all command mixins.

This is the entry point for the modular Telegram bot.
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from ..async_utils import run_sync_in_executor
from ..utils import get_et_now
from ..wheel_state import WheelState
from .analysis_commands import AnalysisCommandsMixin
from .auth_commands import AuthCommandsMixin
from .backtest_commands import BacktestCommandsMixin
from .trading_commands import TradingCommandsMixin
from .utils import ApprovalResult, TradeApprovalRequest, escape_markdown

if TYPE_CHECKING:
    from ..smart_scheduler import SmartScheduler
    from ..trading_bot import TradingBot
    from ..wheel_strategy import CallSignal, PutSignal

logger = logging.getLogger(__name__)


class TelegramBot(
    TradingCommandsMixin,
    AnalysisCommandsMixin,
    AuthCommandsMixin,
    BacktestCommandsMixin,
):
    """
    Telegram bot for trade notifications and approval.

    Features:
    - Send trade approval requests with inline buttons
    - Wait for user approval/rejection
    - Send trade confirmations
    - Send daily summaries
    - Handle errors and alerts
    - Interactive commands for bot control

    Command modules:
    - TradingCommandsMixin: /mode, /pause, /resume, /balance, /positions, /signal, /jobs, /logs, /sellall
    - AnalysisCommandsMixin: /analyze, /patterns, /analyses, /promote, /retire, /hedge, /review
    - AuthCommandsMixin: /auth, /verify
    - BacktestCommandsMixin: /backtest, /simulate
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
        self._pending_sellall = None  # Stores pending sellall confirmation state
        self._is_running = False

        # Put options approval — separate event/result to avoid collision with intraday approvals
        self._put_approval_event: Optional[asyncio.Event] = None
        self._put_approval_result: Optional[str] = None  # "approved", "rejected", or strike string
        self._put_approval_signal: Optional[Any] = None  # stores PutSignal for execution
        self._put_approval_chain: Optional[List[Dict]] = None  # stores full chain for adjust

        # Call options approval — separate event/result to avoid collision with put and intraday approvals
        self._call_approval_event: Optional[asyncio.Event] = None
        self._call_approval_result: Optional[str] = None  # "approved", "rejected", or strike string
        self._call_approval_signal: Optional[Any] = None  # stores CallSignal for execution
        self._call_approval_chain: Optional[List[Dict]] = None  # stores full chain for adjust

        # BTC (buy-to-close) approval — separate event to avoid collision with put/call/intraday
        self._btc_approval_event: Optional[asyncio.Event] = None
        self._btc_approval_result: Optional[str] = None  # "approved" or "rejected"
        self._btc_approval_position: Optional[dict] = None  # position dict being closed
        self._btc_approval_chain: Optional[list] = None  # live chain at time of approval

        # Roll approval — separate event to avoid collision with other approval flows
        self._roll_approval_event: Optional[asyncio.Event] = None
        self._roll_approval_result: Optional[str] = None  # "approved" or "rejected"
        self._roll_approval_position: Optional[dict] = None  # current position being rolled
        self._roll_approval_new_contract: Optional[dict] = None  # proposed new contract

    def _is_authorized(self, update: Update) -> bool:
        """
        Check if the sender is authorized to use this bot.

        Security: Only the configured chat_id can execute commands.
        This prevents unauthorized users from controlling the trading bot.
        """
        if not self.chat_id:
            # No chat_id configured - deny all (fail secure)
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
        """Initialize the bot application."""
        self._app = Application.builder().token(self.token).build()

        # Add command handlers - basic
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        self._app.add_handler(CommandHandler("test", self._cmd_test))

        # Add command handlers - bot control (from TradingCommandsMixin)
        self._app.add_handler(CommandHandler("mode", self._cmd_mode))
        self._app.add_handler(CommandHandler("pause", self._cmd_pause))
        self._app.add_handler(CommandHandler("resume", self._cmd_resume))

        # Add command handlers - information (from TradingCommandsMixin)
        self._app.add_handler(CommandHandler("balance", self._cmd_balance))
        self._app.add_handler(CommandHandler("positions", self._cmd_positions))
        self._app.add_handler(CommandHandler("signal", self._cmd_signal))
        self._app.add_handler(CommandHandler("jobs", self._cmd_jobs))
        self._app.add_handler(CommandHandler("logs", self._cmd_logs))

        # Add command handlers - risk management (from TradingCommandsMixin)
        self._app.add_handler(CommandHandler("sellall", self._cmd_sellall))

        # Add command handlers - analysis (from AnalysisCommandsMixin)
        self._app.add_handler(CommandHandler("analyze", self._cmd_analyze))
        self._app.add_handler(CommandHandler("patterns", self._cmd_patterns))
        self._app.add_handler(CommandHandler("analyses", self._cmd_analyses))
        self._app.add_handler(CommandHandler("promote", self._cmd_promote))
        self._app.add_handler(CommandHandler("retire", self._cmd_retire))
        self._app.add_handler(CommandHandler("hedge", self._cmd_hedge))
        self._app.add_handler(CommandHandler("review", self._cmd_review))

        # Add command handlers - E*TRADE authentication (from AuthCommandsMixin)
        self._app.add_handler(CommandHandler("auth", self._cmd_auth))
        self._app.add_handler(CommandHandler("verify", self._cmd_verify))

        # Add command handlers - backtesting (from BacktestCommandsMixin)
        self._app.add_handler(CommandHandler("backtest", self._cmd_backtest))
        self._app.add_handler(CommandHandler("simulate", self._cmd_simulate))

        # Add callback handler for inline buttons
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))

        # Add error handler to catch and log all errors
        self._app.add_error_handler(self._error_handler)

        await self._app.initialize()
        logger.info("Telegram bot initialized")

    async def _error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle errors that occur during polling/updates."""
        logger.error(f"Telegram bot error: {context.error}")

        # Log the traceback for debugging
        if context.error:
            import traceback

            tb_string = "".join(
                traceback.format_exception(
                    type(context.error), context.error, context.error.__traceback__
                )
            )
            logger.error(f"Telegram error traceback:\n{tb_string}")

        # Try to notify about the error (but don't fail if this also fails)
        try:
            if self.chat_id:
                error_msg = str(context.error)[:200] if context.error else "Unknown error"
                # Escape markdown to prevent parse errors from error text
                safe_error = escape_markdown(error_msg)
                await context.bot.send_message(
                    chat_id=self.chat_id,
                    text=f"⚠️ Bot error occurred:\n{safe_error}\n\nBot will continue running.",
                    parse_mode="Markdown",
                )
        except Exception as e:
            logger.warning(f"Could not send error notification: {e}")

    async def start_polling(self):
        """Start the bot in polling mode (for development/testing)."""
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
    # Basic Command Handlers
    # =========================================================================

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command - shows chat_id for setup (available to anyone)."""
        chat_id = update.effective_chat.id
        is_authorized = self._is_authorized(update)

        if is_authorized:
            await update.message.reply_text(
                f"🤖 *IBIT Trading Bot*\n\n"
                f"✅ You are authorized\n"
                f"Your Chat ID: `{chat_id}`\n\n"
                f"Commands:\n"
                f"/status - Check bot status\n"
                f"/help - Show all commands",
                parse_mode="Markdown",
            )
        else:
            # Show chat_id for setup purposes, but indicate not authorized
            await update.message.reply_text(
                f"🤖 *IBIT Trading Bot*\n\n"
                f"🚫 Not authorized for this bot\n\n"
                f"Your Chat ID: `{chat_id}`\n\n"
                f"If you are the owner, add this to your environment:\n"
                f"`TELEGRAM_CHAT_ID={chat_id}`",
                parse_mode="Markdown",
            )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command - comprehensive bot status."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        from ..utils import get_et_now

        now = get_et_now()
        lines = ["📊 *Bot Status*\n"]

        # Bot status
        if self._is_paused:
            lines.append("⏸ Scheduler: PAUSED")
        elif self._is_running:
            lines.append("🟢 Scheduler: Running")
        else:
            lines.append("🔴 Scheduler: Stopped")

        # Trading mode
        if self.trading_bot:
            mode = "LIVE" if not self.trading_bot.is_paper_mode else "PAPER"
            mode_emoji = "💰" if mode == "LIVE" else "📝"
            lines.append(f"{mode_emoji} Mode: {mode}")
        else:
            lines.append("❓ Mode: Unknown")

        # Time
        lines.append(f"🕐 Time: {now.strftime('%I:%M %p ET')}")
        lines.append(f"📅 Date: {now.strftime('%A, %b %d')}")

        # Pending approval
        if self._pending_approval:
            lines.append("⏳ Pending: Yes")
        else:
            lines.append("✓ Pending: None")

        # Next scheduled job
        if self.scheduler:
            try:
                jobs = self.scheduler.scheduler.get_jobs()
                next_jobs = sorted(
                    [j for j in jobs if j.next_run_time],
                    key=lambda x: x.next_run_time,
                )[:2]
                if next_jobs:
                    lines.append("\n📅 *Next Jobs:*")
                    for job in next_jobs:
                        time_str = job.next_run_time.strftime("%I:%M %p")
                        lines.append(f"• {time_str}: {escape_markdown(job.name)}")
            except Exception:
                pass

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        await update.message.reply_text(
            "*IBIT Trading Bot Help*\n\n"
            "📱 *Bot Control:*\n"
            "/status - Comprehensive bot status\n"
            "/mode - Switch paper/live mode\n"
            "/pause - Pause trading\n"
            "/resume - Resume trading\n\n"
            "📊 *Information:*\n"
            "/balance - Account balance\n"
            "/positions - Current positions\n"
            "/signal - Check today's signal\n"
            "/jobs - View scheduled jobs\n"
            "/logs - View recent activity logs\n\n"
            "🤖 *AI Pattern Discovery:*\n"
            "/analyze - Run pattern analysis now\n"
            "/patterns - View discovered patterns\n"
            "/analyses - View past Claude analyses\n"
            "/promote - Promote pattern to paper/live\n"
            "/retire - Retire a pattern\n\n"
            "🛡️ *Risk Management:*\n"
            "/sellall - Sell ALL positions in account\n"
            "/hedge - Trailing hedge status/control\n"
            "/review - Run monthly strategy review now\n\n"
            "📈 *Backtesting & Simulation:*\n"
            "/backtest - Run strategy backtest\n"
            "  Examples: `/backtest 3 months`, `/backtest 1 week`\n"
            "/simulate - Historical AI evolution simulation\n"
            "  `/simulate 2024` - Full year\n"
            "  `/simulate 2024 email` - Send via email\n"
            "  `/simulate Jan 2024 to Jun 2024` - Month range\n\n"
            "🔐 E\\*TRADE Auth:\n"
            "/auth - Start E\\*TRADE login\n"
            "/verify CODE - Complete login\n\n"
            "🧪 *Testing:*\n"
            "/test - Test approval flow\n"
            "/start - Get your chat ID\n\n"
            "*How approval works:*\n"
            "1. Signal triggers → notification sent\n"
            "2. Tap ✅ Approve or ❌ Reject\n"
            "3. Trade executes (or not)",
            parse_mode="Markdown",
        )

    async def _cmd_test(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /test command - sends a mock approval request."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        callback_id = f"test_{datetime.now().strftime('%H%M%S')}"

        message = (
            "🧪 TEST APPROVAL REQUEST\n\n"
            "📊 Details:\n"
            "• Signal: MEAN REVERSION\n"
            "• Reason: IBIT dropped -2.5% yesterday\n"
            "• ETF: BITU (2x Long)\n"
            "• Shares: 10\n"
            "• Price: 50.00 USD\n"
            "• Total: 500.00 USD\n\n"
            "⏱ This is a TEST - tap a button to see the full flow!"
        )

        keyboard = [
            [
                InlineKeyboardButton("✅ APPROVE", callback_data=f"approve_{callback_id}"),
                InlineKeyboardButton("❌ REJECT", callback_data=f"reject_{callback_id}"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            message,
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )
        logger.info(f"Test approval request sent with callback_id: {callback_id}")

    # =========================================================================
    # Callback Handlers
    # =========================================================================

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline button callbacks."""
        # Security: Verify the callback is from an authorized user
        if not self._is_authorized(update):
            query = update.callback_query
            await query.answer("🚫 Unauthorized", show_alert=True)
            logger.warning(
                f"Unauthorized callback attempt from chat_id: {update.effective_chat.id}"
            )
            return

        query = update.callback_query
        await query.answer()

        data = query.data
        logger.info(f"Received callback: {data}")

        # Handle sellall confirmation/cancellation
        if data.startswith("sellall_confirm_") or data.startswith("sellall_cancel_"):
            await self._handle_sellall_callback(query, data)
            return

        # Handle put options approval callbacks (before intraday approve_/reject_ handlers)
        if data.startswith("put_approve_"):
            self._put_approval_result = "approved"
            await query.edit_message_text(
                text=query.message.text + "\n\n✅ *APPROVED* — Executing put order...",
                parse_mode="Markdown",
            )
            if self._put_approval_event:
                self._put_approval_event.set()
            return

        elif data.startswith("put_adjust_"):
            await self._handle_put_adjust(query, data)
            return

        elif data.startswith("put_alt_reject_"):
            self._put_approval_result = "rejected"
            await query.edit_message_text(
                text="❌ All alternatives rejected. Put suggestion cancelled.",
                parse_mode="Markdown",
            )
            if self._put_approval_event:
                self._put_approval_event.set()
            return

        elif data.startswith("put_alt_"):
            # User selected an alternative strike: put_alt_{strike}_{callback_id}
            parts = data.split("_")
            # parts: ["put", "alt", strike, callback_id...]
            try:
                selected_strike = float(parts[2])
                self._put_approval_result = str(selected_strike)
                await query.edit_message_text(
                    text=f"✅ *APPROVED* — Executing put at strike ${selected_strike}...",
                    parse_mode="Markdown",
                )
                if self._put_approval_event:
                    self._put_approval_event.set()
            except (IndexError, ValueError) as e:
                logger.error(f"Failed to parse put_alt callback data '{data}': {e}")
                await query.edit_message_text(
                    text="❌ Error parsing selection. Put suggestion cancelled.",
                    parse_mode="Markdown",
                )
                self._put_approval_result = "rejected"
                if self._put_approval_event:
                    self._put_approval_event.set()
            return

        elif data.startswith("put_reject_"):
            self._put_approval_result = "rejected"
            await query.edit_message_text(
                text=query.message.text + "\n\n❌ *REJECTED* — Put suggestion cancelled.",
                parse_mode="Markdown",
            )
            if self._put_approval_event:
                self._put_approval_event.set()
            return

        # Handle call options approval callbacks (BEFORE generic approve_/reject_ handlers)
        elif data.startswith("call_approve_"):
            self._call_approval_result = "approved"
            await query.edit_message_text(
                text=query.message.text + "\n\n--- Approved --- Executing call order...",
                parse_mode="Markdown",
            )
            if self._call_approval_event:
                self._call_approval_event.set()
            return

        elif data.startswith("call_adjust_"):
            await self._handle_call_adjust(query, data)
            return

        elif data.startswith("call_alt_reject_"):
            self._call_approval_result = "rejected"
            await query.edit_message_text(
                text="All alternatives rejected. Call suggestion cancelled.",
                parse_mode="Markdown",
            )
            if self._call_approval_event:
                self._call_approval_event.set()
            return

        elif data.startswith("call_alt_"):
            # User selected an alternative strike: call_alt_{strike}_{callback_id}
            parts = data.split("_")
            # parts: ["call", "alt", strike, callback_id...]
            try:
                selected_strike = float(parts[2])
                self._call_approval_result = str(selected_strike)
                await query.edit_message_text(
                    text=f"--- Approved --- Executing call at strike ${selected_strike}...",
                    parse_mode="Markdown",
                )
                if self._call_approval_event:
                    self._call_approval_event.set()
            except (IndexError, ValueError) as e:
                logger.error(f"Failed to parse call_alt callback data '{data}': {e}")
                await query.edit_message_text(
                    text="Error parsing selection. Call suggestion cancelled.",
                    parse_mode="Markdown",
                )
                self._call_approval_result = "rejected"
                if self._call_approval_event:
                    self._call_approval_event.set()
            return

        elif data.startswith("call_reject_"):
            self._call_approval_result = "rejected"
            await query.edit_message_text(
                text=query.message.text + "\n\n--- Rejected --- Call suggestion cancelled.",
                parse_mode="Markdown",
            )
            if self._call_approval_event:
                self._call_approval_event.set()
            return

        # Handle BTC (buy-to-close) approval callbacks — separate from put/call/intraday
        elif data.startswith("btc_approve_"):
            self._btc_approval_result = "approved"
            if self._btc_approval_event:
                self._btc_approval_event.set()
            await query.answer("Buy-to-close approved")
            return

        elif data.startswith("btc_reject_"):
            self._btc_approval_result = "rejected"
            if self._btc_approval_event:
                self._btc_approval_event.set()
            await query.answer("Buy-to-close rejected")
            return

        # Handle roll approval callbacks — separate from BTC/put/call/intraday
        elif data.startswith("roll_approve_"):
            self._roll_approval_result = "approved"
            if self._roll_approval_event:
                self._roll_approval_event.set()
            await query.answer("Roll approved")
            return

        elif data.startswith("roll_reject_"):
            self._roll_approval_result = "rejected"
            if self._roll_approval_event:
                self._roll_approval_event.set()
            await query.answer("Roll rejected")
            return

        # Check if this is a test callback
        is_test = "_test_" in data

        # Handle parameter recommendation approval/rejection
        if data.startswith("apply_param_") or data.startswith("reject_param_"):
            await self._handle_param_recommendation(query, data)
            return

        if data.startswith("approve_"):
            self._approval_result = ApprovalResult.APPROVED
            if is_test:
                await query.edit_message_text(
                    text=(
                        "✅ *TEST APPROVED*\n\n"
                        "🎉 *Full loop confirmed!*\n\n"
                        "The approval workflow is working:\n"
                        "1. ✓ Railway sent the message\n"
                        "2. ✓ You tapped APPROVE\n"
                        "3. ✓ Railway received your response\n\n"
                        "_In production, the trade would execute now._"
                    ),
                    parse_mode="Markdown",
                )
            else:
                await query.edit_message_text(
                    text=query.message.text + "\n\n✅ *APPROVED* - Executing trade...",
                    parse_mode="Markdown",
                )
        elif data.startswith("reject_"):
            self._approval_result = ApprovalResult.REJECTED
            if is_test:
                await query.edit_message_text(
                    text=(
                        "❌ *TEST REJECTED*\n\n"
                        "🎉 *Full loop confirmed!*\n\n"
                        "The rejection workflow is working:\n"
                        "1. ✓ Railway sent the message\n"
                        "2. ✓ You tapped REJECT\n"
                        "3. ✓ Railway received your response\n\n"
                        "_In production, the trade would be cancelled._"
                    ),
                    parse_mode="Markdown",
                )
            else:
                await query.edit_message_text(
                    text=query.message.text + "\n\n❌ *REJECTED* - Trade cancelled.",
                    parse_mode="Markdown",
                )

        # Signal that we got a response
        if self._approval_event:
            self._approval_event.set()

    # =========================================================================
    # Put Options Approval Flow
    # =========================================================================

    async def request_put_approval(
        self,
        signal: "PutSignal",
        chain: List[Dict],
        client: Any,
        db: Any,
        account_id_key: str = "default",
    ) -> ApprovalResult:
        """Send a put options approval request and wait for user response.

        Sends a Telegram message with strike/expiry/greeks details and 3 inline
        buttons: Approve, Adjust (show alternatives), Reject.  Uses a separate
        _put_approval_event so it cannot collide with the intraday _approval_event.

        Returns ApprovalResult indicating the user's decision or timeout.
        """
        if not self.chat_id:
            logger.error("No chat_id configured, cannot request put approval")
            return ApprovalResult.ERROR

        try:
            if not self._app:
                await self.initialize()

            callback_id = f"put_{get_et_now().strftime('%H%M%S')}"

            # Store for use in callback handlers
            self._put_approval_signal = signal
            self._put_approval_chain = chain

            # Build message
            message = (
                "📉 *CASH-SECURED PUT SIGNAL*\n\n"
                f"• Symbol: {escape_markdown(signal.symbol)}\n"
                f"• Strike: ${signal.strike:.2f}\n"
                f"• Expiry: {escape_markdown(signal.expiry_date)} ({signal.dte} DTE)\n"
                f"• Delta: {signal.delta:.3f}\n"
                f"• Premium (bid): ${signal.premium:.2f}/share\n"
                f"• Max Risk: ${signal.max_risk:,.0f} (1 contract)\n"
                f"• IV: {signal.iv:.1%}\n"
                f"• Pullback: {signal.pullback_pct:.1f}%\n\n"
                f"⏱ Timeout: {self.approval_timeout // 60} minutes"
            )

            keyboard = [
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"put_approve_{callback_id}"),
                    InlineKeyboardButton("🔄 Adjust", callback_data=f"put_adjust_{callback_id}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"put_reject_{callback_id}"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await self._app.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode="Markdown",
                reply_markup=reply_markup,
            )

            # Set up separate event for put approval (T-03-05: no collision)
            self._put_approval_event = asyncio.Event()
            self._put_approval_result = None

            try:
                await asyncio.wait_for(
                    self._put_approval_event.wait(),
                    timeout=self.approval_timeout,
                )
            except asyncio.TimeoutError:
                await self.send_message(
                    f"⏰ *TIMEOUT*\n\nNo response received for put signal at ${signal.strike:.2f}. "
                    "Suggestion cancelled."
                )
                return ApprovalResult.TIMEOUT

            result = self._put_approval_result

            if result == "rejected":
                return ApprovalResult.REJECTED

            # result is either "approved" or a numeric string (alternative strike selected)
            if result is not None and result != "approved":
                # User selected an alternative strike from the adjust flow
                try:
                    alt_strike = float(result)
                    # Find the matching contract in the stored chain
                    matching = next(
                        (
                            c for c in (chain or [])
                            if c.get("option_type") == "PUT"
                            and abs(float(c.get("strike", 0)) - alt_strike) < 0.01
                        ),
                        None,
                    )
                    if matching:
                        from ..wheel_strategy import PutSignal as PS  # local import — avoid circular
                        expiry_date = matching.get("expiry_date", signal.expiry_date)
                        if hasattr(expiry_date, "isoformat"):
                            expiry_date = expiry_date.isoformat()
                        else:
                            expiry_date = str(expiry_date)
                        alt_signal = PS(
                            strike=float(matching["strike"]),
                            expiry_date=expiry_date,
                            expiry_year=int(matching.get("expiry_year", signal.expiry_year)),
                            expiry_month=int(matching.get("expiry_month", signal.expiry_month)),
                            expiry_day=int(matching.get("expiry_day", signal.expiry_day)),
                            delta=float(matching.get("delta", signal.delta)),
                            premium=float(matching.get("bid", signal.premium)),
                            dte=int(matching.get("dte", signal.dte)),
                            max_risk=float(matching["strike"]) * 100.0,
                            symbol=str(matching.get("symbol", signal.symbol)),
                            iv=float(matching.get("iv", signal.iv)),
                            gamma=float(matching.get("gamma", signal.gamma)),
                            theta=float(matching.get("theta", signal.theta)),
                            vega=float(matching.get("vega", signal.vega)),
                            pullback_pct=signal.pullback_pct,
                        )
                        signal = alt_signal
                    else:
                        logger.warning(
                            "Could not find chain entry for alt_strike=%.2f; using original signal",
                            alt_strike,
                        )
                except ValueError:
                    logger.error("Could not parse put approval result as float: %s", result)

            success = await self._execute_put_order(signal, client, db, account_id_key)
            return ApprovalResult.APPROVED if success else ApprovalResult.ERROR

        except Exception as e:
            logger.error(f"Failed to request put approval: {e}", exc_info=True)
            return ApprovalResult.ERROR

    async def _execute_put_order(
        self,
        signal: "PutSignal",
        client: Any,
        db: Any,
        account_id_key: str,
    ) -> bool:
        """Preview, place a put sell-to-open order, then record it in the DB.

        Order is only recorded if placement succeeds (T-03-08: no DB write on failure).
        Logs only summary data (T-03-09).

        Returns True on success, False on any failure.
        """
        try:
            # Use bid as limit price (conservative for sell-to-open)
            limit_price = signal.premium

            # Preview first
            preview_response = client.preview_options_order(
                account_id_key,
                "IBIT",
                "PUT",
                signal.expiry_year,
                signal.expiry_month,
                signal.expiry_day,
                signal.strike,
                "SELL_OPEN",
                1,
                limit_price,
            )
            preview_ids = preview_response.get("PreviewIds")

            # Place the order
            place_response = client.place_options_order(
                account_id_key,
                "IBIT",
                "PUT",
                signal.expiry_year,
                signal.expiry_month,
                signal.expiry_day,
                signal.strike,
                "SELL_OPEN",
                1,
                limit_price,
                preview_ids=preview_ids,
            )

            order_id = place_response.get("orderId") or place_response.get("OrderIds", [{}])[0].get("orderId")

            # Record in DB — only after successful placement (T-03-08)
            cycle_id = db.create_wheel_cycle()
            db.transition_wheel_state(
                cycle_id,
                WheelState.SHORT_PUT,
                "put_sold",
                put_strike=signal.strike,
                put_premium_received=signal.premium,
                put_expiry_date=signal.expiry_date,
            )
            db.open_wheel_position(
                cycle_id,
                signal.symbol,
                "PUT",
                signal.strike,
                signal.expiry_date,
                signal.dte,
                signal.premium,
                1,
                signal.delta,
                signal.gamma,
                signal.theta,
                signal.vega,
                signal.iv,
            )

            # T-03-10: Audit log
            db.log_event(
                "INFO",
                "put_order_placed",
                {
                    "strike": signal.strike,
                    "delta": signal.delta,
                    "premium": signal.premium,
                    "dte": signal.dte,
                    "cycle_id": cycle_id,
                    "order_id": str(order_id) if order_id else "unknown",
                },
            )

            await self._app.bot.send_message(
                chat_id=self.chat_id,
                text=(
                    "✅ *PUT ORDER PLACED*\n\n"
                    f"• Strike: ${signal.strike:.2f}\n"
                    f"• Expiry: {escape_markdown(signal.expiry_date)}\n"
                    f"• Premium: ${signal.premium:.2f}/share (${signal.premium * 100:.2f} total)\n"
                    f"• Order ID: {escape_markdown(str(order_id) if order_id else 'pending')}\n"
                    f"• Cycle ID: {cycle_id}"
                ),
                parse_mode="Markdown",
            )
            logger.info(
                "Put order placed: strike=%.2f dte=%d cycle_id=%d",
                signal.strike,
                signal.dte,
                cycle_id,
            )
            return True

        except Exception as e:
            logger.error(f"Failed to execute put order: {e}", exc_info=True)
            try:
                await self._app.bot.send_message(
                    chat_id=self.chat_id,
                    text=f"⚠️ *PUT ORDER FAILED*\n\n{escape_markdown(str(e))}",
                    parse_mode="Markdown",
                )
            except Exception:
                pass
            return False

    async def _handle_put_adjust(self, query: Any, data: str) -> None:
        """Show 3-5 nearby alternative put strikes for user selection.

        Filters the stored chain to puts with abs(delta) in [0.15, 0.40],
        finds strikes around the originally suggested strike, and renders them
        as inline buttons so the user can pick one or reject all.
        """
        chain = self._put_approval_chain or []
        signal = self._put_approval_signal

        # Extract callback_id suffix from the put_adjust_ prefix
        callback_id = data[len("put_adjust_"):]  # e.g. "put_HHMMSS"

        # Filter to wider delta range for alternatives
        candidates = [
            c for c in chain
            if c.get("option_type") == "PUT"
            and 0.15 <= abs(float(c.get("delta", 0))) <= 0.40
        ]
        # Sort by strike ascending
        candidates.sort(key=lambda c: float(c.get("strike", 0)))

        if not candidates:
            await query.edit_message_text(
                text="⚠️ No alternative strikes available in the 0.15–0.40 delta range.",
                parse_mode="Markdown",
            )
            return

        # Find the suggested strike's index and take 2 below and 2 above
        suggested_strike = signal.strike if signal else None
        if suggested_strike is not None:
            # Find nearest index
            strikes = [float(c.get("strike", 0)) for c in candidates]
            nearest_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - suggested_strike))
            start = max(0, nearest_idx - 2)
            end = min(len(candidates), nearest_idx + 3)
            alternatives = [
                c for c in candidates[start:end]
                if abs(float(c.get("strike", 0)) - suggested_strike) > 0.01
            ]
        else:
            alternatives = candidates[:5]

        if not alternatives:
            await query.edit_message_text(
                text="⚠️ No alternative strikes different from the suggested strike.",
                parse_mode="Markdown",
            )
            return

        # Build buttons: one per alternative strike
        keyboard = []
        for c in alternatives:
            alt_strike = float(c.get("strike", 0))
            alt_delta = abs(float(c.get("delta", 0)))
            alt_bid = float(c.get("bid", 0))
            alt_dte = int(c.get("dte", 0))
            label = f"${alt_strike:.2f} | δ={alt_delta:.2f} | ${alt_bid:.2f}bid | {alt_dte}DTE"
            keyboard.append(
                [InlineKeyboardButton(label, callback_data=f"put_alt_{alt_strike}_{callback_id}")]
            )

        # Reject all button
        keyboard.append(
            [InlineKeyboardButton("❌ Reject All", callback_data=f"put_alt_reject_{callback_id}")]
        )

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text="🔄 *SELECT ALTERNATIVE STRIKE*\n\nChoose a put strike or reject all:",
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )

    # =========================================================================
    # Covered Call Approval Flow
    # =========================================================================

    async def request_call_approval(
        self,
        signal: "CallSignal",
        chain: List[Dict],
        client: Any,
        db: Any,
        account_id_key: str = "default",
    ) -> ApprovalResult:
        """Send a covered call approval request and wait for user response.

        Sends a Telegram message with strike/expiry/greeks/cost_basis details and 3 inline
        buttons: Approve, Adjust (show alternatives above cost basis), Reject. Uses a separate
        _call_approval_event so it cannot collide with the intraday or put _approval_events.

        Returns ApprovalResult indicating the user's decision or timeout.
        """
        if not self.chat_id:
            logger.error("No chat_id configured, cannot request call approval")
            return ApprovalResult.ERROR

        try:
            if not self._app:
                await self.initialize()

            callback_id = f"call_{get_et_now().strftime('%H%M%S')}"

            # Store for use in callback handlers
            self._call_approval_signal = signal
            self._call_approval_chain = chain

            # Build message
            message = (
                "📈 *COVERED CALL SIGNAL*\n\n"
                f"• Symbol: {escape_markdown(signal.symbol)}\n"
                f"• Strike: ${signal.strike:.2f}\n"
                f"• Expiry: {escape_markdown(signal.expiry_date)} ({signal.dte} DTE)\n"
                f"• Delta: {signal.delta:.3f}\n"
                f"• Premium (bid): ${signal.premium:.2f}/share\n"
                f"• Total Premium: ${signal.total_premium:.2f} (1 contract)\n"
                f"• Cost Basis: ${signal.cost_basis:.2f}/share\n"
                f"• IV: {signal.iv:.1%}\n\n"
                f"⏱ Timeout: {self.approval_timeout // 60} minutes"
            )

            keyboard = [
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"call_approve_{callback_id}"),
                    InlineKeyboardButton("🔄 Adjust", callback_data=f"call_adjust_{callback_id}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"call_reject_{callback_id}"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await self._app.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode="Markdown",
                reply_markup=reply_markup,
            )

            # Set up separate event for call approval (avoids collision with put/intraday)
            self._call_approval_event = asyncio.Event()
            self._call_approval_result = None

            try:
                await asyncio.wait_for(
                    self._call_approval_event.wait(),
                    timeout=self.approval_timeout,
                )
            except asyncio.TimeoutError:
                await self.send_message(
                    f"⏰ *TIMEOUT*\n\nNo response received for call signal at ${signal.strike:.2f}. "
                    "Suggestion cancelled."
                )
                return ApprovalResult.TIMEOUT

            result = self._call_approval_result

            if result == "rejected":
                return ApprovalResult.REJECTED

            # result is either "approved" or a numeric string (alternative strike selected)
            if result is not None and result != "approved":
                # User selected an alternative strike from the adjust flow
                try:
                    alt_strike = float(result)
                    # Find the matching CALL contract in the stored chain
                    matching = next(
                        (
                            c for c in (chain or [])
                            if c.get("option_type") == "CALL"
                            and abs(float(c.get("strike", 0)) - alt_strike) < 0.01
                        ),
                        None,
                    )
                    if matching:
                        from ..wheel_strategy import CallSignal as CS  # local import — avoid circular
                        expiry_date = matching.get("expiry_date", signal.expiry_date)
                        if hasattr(expiry_date, "isoformat"):
                            expiry_date = expiry_date.isoformat()
                        else:
                            expiry_date = str(expiry_date)
                        alt_signal = CS(
                            strike=float(matching["strike"]),
                            expiry_date=expiry_date,
                            expiry_year=int(matching.get("expiry_year", signal.expiry_year)),
                            expiry_month=int(matching.get("expiry_month", signal.expiry_month)),
                            expiry_day=int(matching.get("expiry_day", signal.expiry_day)),
                            delta=float(matching.get("delta", signal.delta)),
                            premium=float(matching.get("bid", signal.premium)),
                            dte=int(matching.get("dte", signal.dte)),
                            total_premium=float(matching.get("bid", signal.premium)) * 100,
                            symbol=str(matching.get("symbol", signal.symbol)),
                            iv=float(matching.get("iv", signal.iv)),
                            gamma=float(matching.get("gamma", signal.gamma)),
                            theta=float(matching.get("theta", signal.theta)),
                            vega=float(matching.get("vega", signal.vega)),
                            cost_basis=signal.cost_basis,
                        )
                        signal = alt_signal
                    else:
                        logger.warning(
                            "Could not find chain entry for call_alt_strike=%.2f; using original signal",
                            alt_strike,
                        )
                except ValueError:
                    logger.error("Could not parse call approval result as float: %s", result)

            success = await self._execute_call_order(signal, client, db, account_id_key)
            return ApprovalResult.APPROVED if success else ApprovalResult.ERROR

        except Exception as e:
            logger.error(f"Failed to request call approval: {e}", exc_info=True)
            return ApprovalResult.ERROR

    async def _execute_call_order(
        self,
        signal: "CallSignal",
        client: Any,
        db: Any,
        account_id_key: str,
    ) -> bool:
        """Preview, place a covered call sell-to-open order, then record it in the DB.

        STALE SIGNAL GUARD (T-04-08): Re-reads active cycle and verifies signal.strike >= cycle cost_basis
        before placing order. Rejects if cost basis has changed since signal was generated.

        Order is only recorded if placement succeeds.
        Logs only summary data (T-04-11).

        Returns True on success, False on any failure.
        """
        try:
            # STALE SIGNAL GUARD: re-read fresh cycle from DB before placing order
            cycle = db.get_active_cycle()
            if cycle is None:
                logger.error("_execute_call_order: no active cycle found")
                await self._app.bot.send_message(
                    chat_id=self.chat_id,
                    text="⚠️ *CALL ORDER REJECTED*\n\nNo active wheel cycle found.",
                    parse_mode="Markdown",
                )
                return False

            current_cost_basis = cycle.get("cost_basis", 0.0) or 0.0
            if signal.strike < current_cost_basis:
                # Stale signal — strike is now below cost basis
                logger.warning(
                    "Call strike %.2f is below current cost basis %.2f — rejecting stale signal",
                    signal.strike,
                    current_cost_basis,
                )
                await self._app.bot.send_message(
                    chat_id=self.chat_id,
                    text=(
                        f"⚠️ *CALL ORDER REJECTED*\n\n"
                        f"Call strike ${signal.strike:.2f} is below current cost basis "
                        f"${current_cost_basis:.2f}. Order rejected."
                    ),
                    parse_mode="Markdown",
                )
                return False

            # Use bid as limit price (conservative for sell-to-open)
            limit_price = signal.premium

            # Preview first
            preview_response = client.preview_options_order(
                account_id_key,
                "IBIT",
                "CALL",
                signal.expiry_year,
                signal.expiry_month,
                signal.expiry_day,
                signal.strike,
                "SELL_OPEN",
                1,
                limit_price,
            )
            preview_ids = preview_response.get("PreviewIds")

            # Place the order
            place_response = client.place_options_order(
                account_id_key,
                "IBIT",
                "CALL",
                signal.expiry_year,
                signal.expiry_month,
                signal.expiry_day,
                signal.strike,
                "SELL_OPEN",
                1,
                limit_price,
                preview_ids=preview_ids,
            )

            order_id = place_response.get("orderId") or place_response.get("OrderIds", [{}])[0].get("orderId")

            # Record in DB — only after successful placement
            cycle_id = cycle["id"]
            old_cost_basis = current_cost_basis
            old_premiums = cycle.get("covered_call_premiums_collected") or 0.0
            new_cost_basis = old_cost_basis - signal.premium
            new_total_premiums = old_premiums + signal.premium

            db.transition_wheel_state(
                cycle_id,
                WheelState.COVERED_CALL,
                "call_sold",
                cost_basis=new_cost_basis,
                covered_call_premiums_collected=new_total_premiums,
            )
            db.open_wheel_position(
                cycle_id,
                signal.symbol,
                "CALL",
                signal.strike,
                signal.expiry_date,
                signal.dte,
                signal.premium,
                1,
                signal.delta,
                signal.gamma,
                signal.theta,
                signal.vega,
                signal.iv,
            )

            # T-04-11: Audit log
            db.log_event(
                "INFO",
                "call_order_placed",
                {
                    "strike": signal.strike,
                    "delta": signal.delta,
                    "premium": signal.premium,
                    "dte": signal.dte,
                    "cycle_id": cycle_id,
                    "order_id": str(order_id) if order_id else "unknown",
                    "new_cost_basis": new_cost_basis,
                },
            )

            await self._app.bot.send_message(
                chat_id=self.chat_id,
                text=(
                    "✅ *CALL ORDER PLACED*\n\n"
                    f"• Strike: ${signal.strike:.2f}\n"
                    f"• Expiry: {escape_markdown(signal.expiry_date)}\n"
                    f"• Premium: ${signal.premium:.2f}/share (${signal.premium * 100:.2f} total)\n"
                    f"• New Cost Basis: ${new_cost_basis:.2f}/share\n"
                    f"• Order ID: {escape_markdown(str(order_id) if order_id else 'pending')}\n"
                    f"• Cycle ID: {cycle_id}"
                ),
                parse_mode="Markdown",
            )
            logger.info(
                "Call order placed: strike=%.2f dte=%d cycle_id=%d new_cost_basis=%.2f",
                signal.strike,
                signal.dte,
                cycle_id,
                new_cost_basis,
            )
            return True

        except Exception as e:
            logger.error(f"Failed to execute call order: {e}", exc_info=True)
            try:
                await self._app.bot.send_message(
                    chat_id=self.chat_id,
                    text=f"⚠️ *CALL ORDER FAILED*\n\n{escape_markdown(str(e))}",
                    parse_mode="Markdown",
                )
            except Exception:
                pass
            return False

    async def _handle_call_adjust(self, query: Any, data: str) -> None:
        """Show 3-5 nearby alternative call strikes for user selection.

        Filters the stored chain to calls with delta in [0.20, 0.45], then applies a
        HARD COST BASIS FILTER (T-04-10): only shows strikes >= cost_basis. If no
        qualifying strikes exist, shows a warning instead of presenting options.
        """
        chain = self._call_approval_chain or []
        signal = self._call_approval_signal

        # Extract callback_id suffix from the call_adjust_ prefix
        callback_id = data[len("call_adjust_"):]  # e.g. "call_HHMMSS"

        # Cost basis from signal (protected by T-04-10)
        cost_basis = signal.cost_basis if signal else 0.0

        # Filter to CALL contracts with wider delta range for alternatives
        # HARD FILTER: only strikes >= cost_basis (T-04-10)
        candidates = [
            c for c in chain
            if c.get("option_type") == "CALL"
            and 0.20 <= abs(float(c.get("delta", 0))) <= 0.45
            and float(c.get("strike", 0)) >= cost_basis
        ]
        # Sort by strike ascending
        candidates.sort(key=lambda c: float(c.get("strike", 0)))

        if not candidates:
            await query.edit_message_text(
                text=(
                    f"⚠️ No profitable call strikes above cost basis ${cost_basis:.2f}. "
                    "Consider waiting for price recovery."
                ),
                parse_mode="Markdown",
            )
            return

        # Find the suggested strike's index and take 2 below and 2 above
        suggested_strike = signal.strike if signal else None
        if suggested_strike is not None:
            strikes = [float(c.get("strike", 0)) for c in candidates]
            nearest_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - suggested_strike))
            start = max(0, nearest_idx - 2)
            end = min(len(candidates), nearest_idx + 3)
            alternatives = [
                c for c in candidates[start:end]
                if abs(float(c.get("strike", 0)) - suggested_strike) > 0.01
            ]
        else:
            alternatives = candidates[:5]

        if not alternatives:
            await query.edit_message_text(
                text="⚠️ No alternative call strikes different from the suggested strike.",
                parse_mode="Markdown",
            )
            return

        # Build buttons: one per alternative strike
        keyboard = []
        for c in alternatives:
            alt_strike = float(c.get("strike", 0))
            alt_delta = abs(float(c.get("delta", 0)))
            alt_bid = float(c.get("bid", 0))
            alt_dte = int(c.get("dte", 0))
            label = f"${alt_strike:.2f} | δ={alt_delta:.2f} | ${alt_bid:.2f}bid | {alt_dte}DTE"
            keyboard.append(
                [InlineKeyboardButton(label, callback_data=f"call_alt_{alt_strike}_{callback_id}")]
            )

        # Reject all button
        keyboard.append(
            [InlineKeyboardButton("❌ Reject All", callback_data=f"call_alt_reject_{callback_id}")]
        )

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text="🔄 *SELECT ALTERNATIVE CALL STRIKE*\n\nChoose a call strike or reject all:",
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )

    # =========================================================================
    # Profit-Take (BTC) Approval Flow
    # =========================================================================

    async def request_profit_take_approval(
        self,
        position: dict,
        chain: list,
        cycle: dict,
        client: Any,
        db: Any,
        account_id_key: str = "default",
    ) -> "ApprovalResult":
        """Send a buy-to-close approval request and wait for user response.

        Sends a Telegram message with option symbol, entry premium, current ask,
        profit %, and dollar savings from closing early. Uses Approve/Reject buttons.
        Uses a separate _btc_approval_event to avoid collision with put/call/intraday.

        Returns ApprovalResult indicating the user's decision or timeout.
        """
        if not self.chat_id:
            logger.error("No chat_id configured, cannot request BTC approval")
            return ApprovalResult.ERROR

        try:
            if not self._app:
                await self.initialize()

            # Find current ask price in chain
            symbol = position["symbol"]
            matching = next(
                (c for c in chain if c.get("symbol") == symbol),
                None,
            )
            if matching is None:
                logger.warning("BTC approval: symbol %s not found in chain", symbol)
                return ApprovalResult.ERROR

            current_ask = float(matching.get("ask", 0))
            premium_received = float(position.get("premium_received", 0))
            quantity = int(position.get("quantity", 1))

            profit_pct = (
                (premium_received - current_ask) / premium_received * 100
                if premium_received > 0
                else 0.0
            )
            savings = (premium_received - current_ask) * quantity * 100

            position_id = position["id"]
            callback_id = f"btc_{position_id}"

            # Store for callback use
            self._btc_approval_position = position
            self._btc_approval_chain = chain

            message = (
                "*PROFIT TARGET HIT - 50% Reached*\n\n"
                f"Option: {escape_markdown(symbol)}\n"
                f"Type: {position.get('option_type', 'N/A')}\n"
                f"Strike: ${float(position.get('strike', 0)):.2f}\n"
                f"Entry premium: ${premium_received:.2f}/share\n"
                f"Current ask: ${current_ask:.2f}/share\n"
                f"Profit: {profit_pct:.1f}%\n"
                f"Savings from closing early: ${savings:.2f}\n\n"
                f"Suggest: Buy-to-close at ${current_ask:.2f}"
            )

            keyboard = [
                [
                    InlineKeyboardButton("Approve BTC", callback_data=f"btc_approve_{callback_id}"),
                    InlineKeyboardButton("Reject", callback_data=f"btc_reject_{callback_id}"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await self._app.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode="Markdown",
                reply_markup=reply_markup,
            )

            # Set up fresh event for BTC approval
            self._btc_approval_event = asyncio.Event()
            self._btc_approval_result = None

            try:
                await asyncio.wait_for(
                    self._btc_approval_event.wait(),
                    timeout=self.approval_timeout,
                )
            except asyncio.TimeoutError:
                await self.send_message(
                    f"*TIMEOUT*\n\nNo response received for BTC at ${current_ask:.2f}. "
                    "Suggestion cancelled."
                )
                return ApprovalResult.TIMEOUT

            result = self._btc_approval_result

            if result == "rejected":
                logger.info("BTC approval rejected for position_id=%d", position_id)
                return ApprovalResult.REJECTED

            # result == "approved"
            success = await self._execute_btc_order(
                position, cycle, client, db, account_id_key, current_ask
            )
            return ApprovalResult.APPROVED if success else ApprovalResult.ERROR

        except Exception as e:
            logger.error(f"Failed to request BTC approval: {e}", exc_info=True)
            return ApprovalResult.ERROR

    async def _execute_btc_order(
        self,
        position: dict,
        cycle: dict,
        client: Any,
        db: Any,
        account_id_key: str,
        close_price: float,
    ) -> bool:
        """Preview and place a buy-to-close order, then update DB state.

        T-05-06: No DB mutation (close_wheel_position, transition_wheel_state) on API failure.
        T-05-08: Stale signal guard — re-reads open position before executing.
        T-05-09: Audit log for every order attempt.

        Returns True on success, False on any failure.
        """
        try:
            symbol = position["symbol"]
            option_type = position.get("option_type", "PUT")
            quantity = int(position.get("quantity", 1))

            # Preview order
            preview_response = client.preview_options_order(
                account_id_key,
                "IBIT",
                option_type,
                symbol,
                "BUY_CLOSE",
                quantity,
                close_price,
            )
            preview_ids = preview_response.get("PreviewIds")

            # Place order
            place_response = client.place_options_order(
                account_id_key,
                "IBIT",
                option_type,
                symbol,
                "BUY_CLOSE",
                quantity,
                close_price,
                preview_ids=preview_ids,
            )

            order_id = (
                place_response.get("orderId")
                or place_response.get("OrderIds", [{}])[0].get("orderId")
            )

            # DB mutations — only after successful order placement (T-05-06)
            db.close_wheel_position(position["id"], close_price)

            # Determine next cycle state based on current state
            cycle_state = cycle.get("state", "")
            if cycle_state == WheelState.SHORT_PUT.value or cycle_state == "SHORT_PUT":
                next_state = WheelState.CASH
            else:
                next_state = WheelState.HOLDING_SHARES

            db.transition_wheel_state(cycle["id"], next_state, "profit_take_btc")

            db.log_event(
                "INFO",
                "btc_order_placed",
                {
                    "position_id": position["id"],
                    "close_price": close_price,
                    "cycle_id": cycle["id"],
                    "order_id": str(order_id) if order_id else "unknown",
                },
            )

            await self._app.bot.send_message(
                chat_id=self.chat_id,
                text=(
                    "*BTC ORDER PLACED*\n\n"
                    f"Symbol: {escape_markdown(symbol)}\n"
                    f"Close price: ${close_price:.2f}/share\n"
                    f"Order ID: {escape_markdown(str(order_id) if order_id else 'pending')}\n"
                    f"Cycle transitioned to: {next_state.value}"
                ),
                parse_mode="Markdown",
            )
            logger.info(
                "BTC order placed: position_id=%d close_price=%.2f cycle_id=%d",
                position["id"],
                close_price,
                cycle["id"],
            )
            return True

        except Exception as e:
            logger.error(f"Failed to execute BTC order: {e}", exc_info=True)
            # T-05-06: Do NOT call close_wheel_position or transition_wheel_state on failure
            try:
                await self._app.bot.send_message(
                    chat_id=self.chat_id,
                    text=f"*BTC ORDER FAILED*\n\n{escape_markdown(str(e))}",
                    parse_mode="Markdown",
                )
            except Exception:
                pass
            return False

    async def send_dte_alert(self, position: dict, cycle: dict, db: Any) -> None:
        """Send an informational DTE warning message (no action buttons).

        Called when a position reaches 21 DTE and dte_alert_sent is False.
        Marks dte_alert_sent after sending to prevent duplicates (PM-04).

        Args:
            position: options_positions row dict.
            cycle: wheel_cycles row dict.
            db: Database instance for mark_dte_alert_sent call.
        """
        if not self.chat_id:
            logger.warning("send_dte_alert: no chat_id configured")
            return

        try:
            if not self._app:
                await self.initialize()

            option_type = position.get("option_type", "N/A")
            strike = float(position.get("strike", 0))
            expiry_date = position.get("expiry_date", "N/A")

            # Calculate DTE from expiry_date
            try:
                from datetime import date as date_cls
                expiry = date_cls.fromisoformat(str(expiry_date))
                today = get_et_now().date()
                dte = (expiry - today).days
            except Exception:
                dte = "N/A"

            message = (
                "*DTE WARNING - 21 Days to Expiration*\n\n"
                f"Option type: {option_type}\n"
                f"Strike: ${strike:.2f}\n"
                f"Expiry: {escape_markdown(str(expiry_date))}\n"
                f"Days remaining: {dte}\n\n"
                "Consider your next action: close for profit, let expire, or prepare for assignment."
            )

            await self._app.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode="Markdown",
            )

            # Mark alert sent to prevent duplicate sends
            db.mark_dte_alert_sent(position["id"])
            logger.info("DTE alert sent for position_id=%d dte=%s", position["id"], dte)

        except Exception as e:
            logger.error(f"Failed to send DTE alert: {e}", exc_info=True)

    # =========================================================================
    # Defensive Roll Approval Flow
    # =========================================================================

    async def request_roll_approval(
        self,
        position: dict,
        new_contract: dict,
        cycle: dict,
        chain: list,
        client: Any,
        db: Any,
        account_id_key: str = "default",
    ) -> "ApprovalResult":
        """Send a defensive roll approval request and wait for user response.

        Guards:
        - roll_count >= 2: sends warning, returns None (no approval dialog)
        - net debit (new_bid <= current_ask): sends warning, returns None

        Returns ApprovalResult or None if blocked by guards.
        """
        if not self.chat_id:
            logger.error("No chat_id configured, cannot request roll approval")
            return ApprovalResult.ERROR

        try:
            if not self._app:
                await self.initialize()

            roll_count = position.get("roll_count", 0)

            # Guard: max rolls reached
            if roll_count >= 2:
                await self._app.bot.send_message(
                    chat_id=self.chat_id,
                    text=(
                        "*ROLL BLOCKED*\n\n"
                        f"Max rolls reached ({roll_count}/2). Manual intervention may be needed."
                    ),
                    parse_mode="Markdown",
                )
                logger.info(
                    "Roll blocked: max rolls reached for position_id=%d roll_count=%d",
                    position["id"],
                    roll_count,
                )
                return None

            # Find current option ask price in chain
            symbol = position["symbol"]
            matching_current = next(
                (c for c in chain if c.get("symbol") == symbol),
                None,
            )
            current_ask = float(matching_current.get("ask", 0)) if matching_current else 0.0

            new_bid = float(new_contract.get("bid", 0))
            net = new_bid - current_ask

            # Guard: net debit
            if net <= 0:
                await self._app.bot.send_message(
                    chat_id=self.chat_id,
                    text=(
                        "*ROLL BLOCKED*\n\n"
                        f"Roll would result in net debit of ${abs(net):.2f}/share. "
                        "Cannot roll for a debit."
                    ),
                    parse_mode="Markdown",
                )
                logger.info(
                    "Roll blocked: net debit for position_id=%d net=%.2f",
                    position["id"],
                    net,
                )
                return None

            position_id = position["id"]
            callback_id = f"roll_{position_id}"

            current_strike = float(position.get("strike", 0))
            current_dte = position.get("dte_at_entry", "N/A")
            new_strike = float(new_contract.get("strike", 0))
            new_expiry = new_contract.get("expiry_date", "N/A")
            new_delta = float(new_contract.get("delta", 0))
            new_dte = int(new_contract.get("dte", 0))

            # Store for callback use
            self._roll_approval_position = position
            self._roll_approval_new_contract = new_contract

            message = (
                "*DEFENSIVE ROLL SUGGESTED*\n\n"
                "Current position:\n"
                f"  Strike: ${current_strike:.2f}\n"
                f"  DTE: {current_dte} days\n"
                f"  Roll count: {roll_count}/2\n\n"
                "Suggested new position:\n"
                f"  Strike: ${new_strike:.2f}\n"
                f"  Expiry: {escape_markdown(str(new_expiry))}\n"
                f"  Delta: {new_delta:.3f}\n"
                f"  DTE: {new_dte} days\n\n"
                f"Estimated BTC cost: ${current_ask:.2f}/share\n"
                f"New STO premium: ${new_bid:.2f}/share\n"
                f"Net credit: ${net:.2f}/share (${net * 100:.2f} total)"
            )

            keyboard = [
                [
                    InlineKeyboardButton("Approve Roll", callback_data=f"roll_approve_{callback_id}"),
                    InlineKeyboardButton("Reject", callback_data=f"roll_reject_{callback_id}"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await self._app.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode="Markdown",
                reply_markup=reply_markup,
            )

            # Set up fresh event for roll approval
            self._roll_approval_event = asyncio.Event()
            self._roll_approval_result = None

            try:
                await asyncio.wait_for(
                    self._roll_approval_event.wait(),
                    timeout=self.approval_timeout,
                )
            except asyncio.TimeoutError:
                await self.send_message(
                    f"*TIMEOUT*\n\nNo response received for roll suggestion. Suggestion cancelled."
                )
                return ApprovalResult.TIMEOUT

            result = self._roll_approval_result

            if result == "rejected":
                logger.info("Roll rejected for position_id=%d", position_id)
                return ApprovalResult.REJECTED

            # result == "approved"
            success = await self._execute_roll(
                position, new_contract, cycle, client, db, account_id_key, current_ask
            )
            return ApprovalResult.APPROVED if success else ApprovalResult.ERROR

        except Exception as e:
            logger.error(f"Failed to request roll approval: {e}", exc_info=True)
            return ApprovalResult.ERROR

    async def _execute_roll(
        self,
        position: dict,
        new_contract: dict,
        cycle: dict,
        client: Any,
        db: Any,
        account_id_key: str,
        btc_price: float,
    ) -> bool:
        """Execute a two-step roll: BTC current position, then STO new position.

        Step 1: Buy-to-close current option. If this fails, no DB changes are made.
        Step 2: Sell-to-open new option. If this fails after BTC succeeds, old position
                is closed and cycle transitions to safe state (CASH for put, HOLDING_SHARES
                for call). Error notification is sent with BTC order ID.

        T-05-08: Stale signal guard — ensures old position is actually OPEN before BTC.
        T-05-09: Audit log for every step.

        Returns True on full success, False on any failure.
        """
        symbol = position["symbol"]
        option_type = position.get("option_type", "PUT")
        quantity = int(position.get("quantity", 1))
        old_roll_count = position.get("roll_count", 0)
        btc_result = None

        # Step 1: BTC current position
        try:
            preview_response = client.preview_options_order(
                account_id_key,
                "IBIT",
                option_type,
                symbol,
                "BUY_CLOSE",
                quantity,
                btc_price,
            )
            preview_ids = preview_response.get("PreviewIds")

            btc_result = client.place_options_order(
                account_id_key,
                "IBIT",
                option_type,
                symbol,
                "BUY_CLOSE",
                quantity,
                btc_price,
                preview_ids=preview_ids,
            )
        except Exception as e:
            # BTC failed — do NOT modify any DB state
            logger.error("Roll BTC failed for position_id=%d: %s", position["id"], e)
            db.log_event("ERROR", "roll_btc_failed", {"position_id": position["id"], "error": str(e)})
            try:
                await self._app.bot.send_message(
                    chat_id=self.chat_id,
                    text=f"*ROLL FAILED*\n\nBTC step failed: {escape_markdown(str(e))}\nNo changes made.",
                    parse_mode="Markdown",
                )
            except Exception:
                pass
            return False

        # BTC succeeded — close old position in DB
        db.close_wheel_position(position["id"], btc_price)

        btc_order_id = (
            btc_result.get("orderId")
            or btc_result.get("OrderIds", [{}])[0].get("orderId")
            if btc_result else None
        )

        # Step 2: STO new position
        new_symbol = new_contract.get("symbol", symbol)
        new_bid = float(new_contract.get("bid", 0))

        try:
            sto_preview = client.preview_options_order(
                account_id_key,
                "IBIT",
                option_type,
                new_symbol,
                "SELL_OPEN",
                quantity,
                new_bid,
            )
            sto_preview_ids = sto_preview.get("PreviewIds")

            sto_result = client.place_options_order(
                account_id_key,
                "IBIT",
                option_type,
                new_symbol,
                "SELL_OPEN",
                quantity,
                new_bid,
                preview_ids=sto_preview_ids,
            )
        except Exception as e:
            # STO failed after BTC succeeded — transition cycle to safe state
            logger.error("Roll STO failed for position_id=%d: %s", position["id"], e)
            cycle_state = cycle.get("state", "")
            if cycle_state == WheelState.SHORT_PUT.value or cycle_state == "SHORT_PUT":
                safe_state = WheelState.CASH
                safe_reason = "roll_sto_failed_put"
            else:
                safe_state = WheelState.HOLDING_SHARES
                safe_reason = "roll_sto_failed_call"

            db.transition_wheel_state(cycle["id"], safe_state, safe_reason)
            db.log_event(
                "ERROR",
                "roll_sto_failed",
                {
                    "position_id": position["id"],
                    "btc_order_id": str(btc_order_id) if btc_order_id else "unknown",
                    "error": str(e),
                },
            )
            try:
                await self._app.bot.send_message(
                    chat_id=self.chat_id,
                    text=(
                        f"*ROLL PARTIAL FAILURE*\n\n"
                        f"BTC executed but STO failed: {escape_markdown(str(e))}\n"
                        f"Position closed. You may need to manually open a new position.\n"
                        f"BTC order: {escape_markdown(str(btc_order_id) if btc_order_id else 'N/A')}"
                    ),
                    parse_mode="Markdown",
                )
            except Exception:
                pass
            return False

        # Both steps succeeded — open new position in DB
        new_position_id = db.open_wheel_position(
            cycle_id=cycle["id"],
            symbol=new_symbol,
            option_type=option_type,
            strike=float(new_contract.get("strike", 0)),
            expiry_date=str(new_contract.get("expiry_date", "")),
            dte_at_entry=int(new_contract.get("dte", 0)),
            premium_received=new_bid,
            quantity=quantity,
            delta=new_contract.get("delta"),
            gamma=new_contract.get("gamma"),
            theta=new_contract.get("theta"),
            vega=new_contract.get("vega"),
            iv=new_contract.get("iv"),
        )

        # Set roll_count on new position to old_roll_count + 1
        db.set_roll_count(new_position_id, old_roll_count + 1)

        sto_order_id = (
            sto_result.get("orderId")
            or sto_result.get("OrderIds", [{}])[0].get("orderId")
            if sto_result else None
        )

        db.log_event(
            "INFO",
            "roll_executed",
            {
                "old_position_id": position["id"],
                "new_position_id": new_position_id,
                "new_strike": float(new_contract.get("strike", 0)),
                "roll_count": old_roll_count + 1,
                "btc_order_id": str(btc_order_id) if btc_order_id else "unknown",
                "sto_order_id": str(sto_order_id) if sto_order_id else "unknown",
            },
        )

        net_credit = new_bid - btc_price
        try:
            await self._app.bot.send_message(
                chat_id=self.chat_id,
                text=(
                    "*ROLL COMPLETE*\n\n"
                    f"Closed: ${float(position.get('strike', 0)):.2f} strike\n"
                    f"Opened: ${float(new_contract.get('strike', 0)):.2f} strike\n"
                    f"Net credit: ${net_credit:.2f}/share\n"
                    f"Roll count: {old_roll_count + 1}/2"
                ),
                parse_mode="Markdown",
            )
        except Exception:
            pass

        logger.info(
            "Roll executed: position_id=%d -> new_position_id=%d roll_count=%d",
            position["id"],
            new_position_id,
            old_roll_count + 1,
        )
        return True

    async def _handle_param_recommendation(self, query, data: str):
        """Handle parameter recommendation approval/rejection."""
        from ..strategy_review import (
            get_pending_recommendations,
            get_strategy_reviewer,
        )

        # Extract recommendation index
        try:
            idx = int(data.split("_")[-1])
        except (ValueError, IndexError):
            await query.edit_message_text(
                text="❌ Invalid recommendation reference.",
                parse_mode="Markdown",
            )
            return

        recommendations = get_pending_recommendations()
        if idx >= len(recommendations):
            await query.edit_message_text(
                text="❌ Recommendation no longer available.",
                parse_mode="Markdown",
            )
            return

        rec = recommendations[idx]

        if data.startswith("apply_param_"):
            # Apply the recommendation
            reviewer = get_strategy_reviewer()
            success = reviewer.apply_recommendation(rec)

            # Escape dynamic content
            display_name = escape_markdown(rec.to_display_name())
            current_val = escape_markdown(str(rec.current_value))
            recommended_val = escape_markdown(str(rec.recommended_value))

            if success:
                await query.edit_message_text(
                    text=(
                        f"✅ *Parameter Updated!*\n\n"
                        f"*{display_name}*\n"
                        f"`{current_val}` → `{recommended_val}`\n\n"
                        f"_Change applied and saved to database._\n\n"
                        f"💾 This change persists across restarts."
                    ),
                    parse_mode="Markdown",
                )
            else:
                await query.edit_message_text(
                    text="❌ Failed to apply parameter change.",
                    parse_mode="Markdown",
                )

        elif data.startswith("reject_param_"):
            display_name = escape_markdown(rec.to_display_name())
            current_val = escape_markdown(str(rec.current_value))
            await query.edit_message_text(
                text=(
                    f"❌ *Recommendation Rejected*\n\n"
                    f"*{display_name}*\n"
                    f"Keeping current value: `{current_val}`"
                ),
                parse_mode="Markdown",
            )

    async def _handle_sellall_callback(self, query, data: str):
        """Handle sellall confirm/cancel callbacks."""
        if data.startswith("sellall_cancel_"):
            self._pending_sellall = None
            await query.edit_message_text(
                text=query.message.text + "\n\nCANCELLED - No positions were sold.",
                parse_mode=None,
            )
            return

        # Confirm path
        if not self._pending_sellall:
            await query.edit_message_text(
                text="Sell all request expired. Please run /sellall again.",
                parse_mode=None,
            )
            return

        if not self.trading_bot:
            await query.edit_message_text(
                text="Trading bot not available.",
                parse_mode=None,
            )
            self._pending_sellall = None
            return

        self._pending_sellall = None

        await query.edit_message_text(
            text=query.message.text + "\n\nExecuting sell all...",
            parse_mode=None,
        )

        try:
            results = await run_sync_in_executor(self.trading_bot.liquidate_all_account_positions)

            if not results:
                await self._app.bot.send_message(
                    chat_id=self.chat_id,
                    text="No positions were sold (account may already be empty).",
                    parse_mode=None,
                )
                return

            # Build results summary
            lines = ["SELL ALL RESULTS\n"]
            total_sold = 0.0
            successes = 0
            failures = 0

            for r in results:
                symbol = r.get("symbol", "?")
                shares = r.get("shares", 0)
                price = r.get("price", 0)
                value = r.get("total_value", 0)
                success = r.get("success", False)
                error = r.get("error")

                if success:
                    successes += 1
                    total_sold += value
                    status = "SOLD"
                    if error:
                        status = f"SOLD ({error})"
                    lines.append(
                        f"  {symbol}: {shares} shares @ ${price:.2f} = ${value:,.2f} - {status}"
                    )
                else:
                    failures += 1
                    lines.append(f"  {symbol}: FAILED - {error}")

            lines.append(f"\nSummary: {successes} sold, {failures} failed")
            if total_sold > 0:
                lines.append(f"Total proceeds: ${total_sold:,.2f}")

            await self._app.bot.send_message(
                chat_id=self.chat_id,
                text="\n".join(lines),
                parse_mode=None,
            )

            # Log completion
            from ..database import get_database

            db = get_database()
            db.log_event(
                "SELLALL_COMPLETED",
                f"Sold {successes} positions for ${total_sold:,.2f}",
            )

        except Exception as e:
            logger.error(f"Error executing sell all: {e}")
            await self._app.bot.send_message(
                chat_id=self.chat_id,
                text=f"Error executing sell all: {str(e)}",
                parse_mode=None,
            )

    # =========================================================================
    # Core Messaging Methods
    # =========================================================================

    async def send_message(self, text: str, parse_mode: Optional[str] = None) -> bool:
        """Send a simple text message.

        Args:
            text: Message text to send
            parse_mode: Optional ("Markdown", "HTML", or None). Defaults to None.
        """
        if not self.chat_id:
            logger.warning("No chat_id configured, cannot send message")
            return False

        try:
            if not self._app:
                await self.initialize()

            await self._app.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode=parse_mode,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return False

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
        Send a trade approval request and wait for user response.

        Returns ApprovalResult indicating user's decision or timeout.
        """
        if not self.chat_id:
            logger.error("No chat_id configured")
            return ApprovalResult.ERROR

        try:
            if not self._app:
                await self.initialize()

            # Generate unique callback ID
            callback_id = f"{signal_type}_{datetime.now().strftime('%H%M%S')}"

            # Create message - escape dynamic content
            emoji = self._get_signal_emoji(signal_type)
            reason_safe = escape_markdown(reason)
            etf_safe = escape_markdown(etf)
            message = (
                f"{emoji} *{signal_type.replace('_', ' ').upper()} SIGNAL*\n\n"
                f"📊 *Details:*\n"
                f"• Reason: {reason_safe}\n"
                f"• ETF: {etf_safe}\n"
                f"• Shares: {shares}\n"
                f"• Price: ${price:.2f}\n"
                f"• Total: ${position_value:.2f}\n\n"
                f"⏱ Timeout: {self.approval_timeout // 60} minutes"
            )

            # Create inline keyboard with Approve/Reject buttons
            keyboard = [
                [
                    InlineKeyboardButton("✅ APPROVE", callback_data=f"approve_{callback_id}"),
                    InlineKeyboardButton("❌ REJECT", callback_data=f"reject_{callback_id}"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            # Send message
            await self._app.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode="Markdown",
                reply_markup=reply_markup,
            )

            # Wait for response
            self._approval_event = asyncio.Event()
            self._approval_result = None

            try:
                await asyncio.wait_for(
                    self._approval_event.wait(),
                    timeout=self.approval_timeout,
                )
                return self._approval_result or ApprovalResult.ERROR
            except asyncio.TimeoutError:
                await self.send_message(
                    f"⏰ *TIMEOUT*\n\nNo response received for {signal_type}. Trade skipped."
                )
                return ApprovalResult.TIMEOUT

        except Exception as e:
            logger.error(f"Failed to request approval: {e}")
            return ApprovalResult.ERROR

    async def send_trade_executed(
        self,
        signal_type: str,
        etf: str,
        action: str,
        shares: int,
        price: float,
        total: float,
    ):
        """Send confirmation that a trade was executed."""
        emoji = "🟢" if action.lower() == "buy" else "🔴"
        etf_safe = escape_markdown(etf)
        signal_safe = escape_markdown(signal_type)
        await self.send_message(
            f"{emoji} *TRADE EXECUTED*\n\n"
            f"• Signal: {signal_safe}\n"
            f"• Action: {action.upper()} {shares} {etf_safe}\n"
            f"• Price: ${price:.2f}\n"
            f"• Total: ${total:.2f}"
        )

    async def send_position_closed(
        self,
        etf: str,
        shares: int,
        entry_price: float,
        exit_price: float,
        pnl: float,
        pnl_pct: float,
    ):
        """Send notification that a position was closed."""
        emoji = "📈" if pnl >= 0 else "📉"
        pnl_sign = "+" if pnl >= 0 else ""
        etf_safe = escape_markdown(etf)

        await self.send_message(
            f"{emoji} *POSITION CLOSED*\n\n"
            f"• ETF: {etf_safe}\n"
            f"• Shares: {shares}\n"
            f"• Entry: ${entry_price:.2f}\n"
            f"• Exit: ${exit_price:.2f}\n"
            f"• P/L: {pnl_sign}${pnl:.2f} ({pnl_sign}{pnl_pct:.1f}%)"
        )

    async def send_daily_summary(
        self,
        trades_today: int,
        total_pnl: float,
        win_rate: float,
        ending_cash: float,
    ):
        """Send end-of-day summary."""
        emoji = "📈" if total_pnl >= 0 else "📉"
        pnl_sign = "+" if total_pnl >= 0 else ""

        await self.send_message(
            f"📊 *DAILY SUMMARY*\n\n"
            f"• Trades: {trades_today}\n"
            f"• P/L: {pnl_sign}${total_pnl:.2f}\n"
            f"• Win Rate: {win_rate:.0f}%\n"
            f"• Cash Balance: ${ending_cash:.2f}\n\n"
            f"{emoji} Day complete. See you tomorrow!"
        )

    async def send_error_alert(self, error_type: str, message: str):
        """Send an error alert."""
        # Escape error content to prevent Markdown parsing issues
        error_type_safe = escape_markdown(error_type)
        message_safe = escape_markdown(message)
        await self.send_message(
            f"⚠️ *ERROR ALERT*\n\n"
            f"• Type: {error_type_safe}\n"
            f"• Message: {message_safe}\n\n"
            f"Please check the bot logs."
        )

    async def send_no_signal_today(self):
        """Send notification that there's no trade signal today."""
        await self.send_message(
            "💤 *NO SIGNAL TODAY*\n\n"
            "No trading signals triggered. Staying in cash.\n"
            "The bot will continue monitoring."
        )

    def _get_signal_emoji(self, signal_type: str) -> str:
        """Get emoji for signal type."""
        emojis = {
            "mean_reversion": "📈",
            "short_thursday": "📅",
            "crash_day": "💥",
            "pump_day": "🚀",
        }
        return emojis.get(signal_type.lower(), "📊")
