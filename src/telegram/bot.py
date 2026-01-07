"""
Main TelegramBot class combining all command mixins.

This is the entry point for the modular Telegram bot.
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from .analysis_commands import AnalysisCommandsMixin
from .auth_commands import AuthCommandsMixin
from .backtest_commands import BacktestCommandsMixin
from .trading_commands import TradingCommandsMixin
from .utils import ApprovalResult, TradeApprovalRequest, escape_markdown

if TYPE_CHECKING:
    from ..smart_scheduler import SmartScheduler
    from ..trading_bot import TradingBot

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
    - TradingCommandsMixin: /mode, /pause, /resume, /balance, /positions, /signal, /jobs, /logs
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
        self._is_running = False

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
