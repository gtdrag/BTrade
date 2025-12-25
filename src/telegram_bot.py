"""
Telegram Bot for Trade Notifications and Approval.

Provides mobile notifications and human-in-the-loop trade approval
via Telegram. Sends trade signals with Approve/Reject buttons and
waits for user response before executing trades.
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

logger = logging.getLogger(__name__)


class ApprovalResult(Enum):
    """Result of trade approval request."""

    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass
class TradeApprovalRequest:
    """Pending trade approval request."""

    signal_type: str
    etf: str
    reason: str
    position_size: float
    shares: int
    price: float
    timestamp: datetime
    callback_id: str


class TelegramBot:
    """
    Telegram bot for trade notifications and approval.

    Features:
    - Send trade approval requests with inline buttons
    - Wait for user approval/rejection
    - Send trade confirmations
    - Send daily summaries
    - Handle errors and alerts
    - Interactive commands for bot control
    """

    def __init__(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,
        approval_timeout_minutes: int = 10,
        scheduler=None,
        trading_bot=None,
    ):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        self.approval_timeout = approval_timeout_minutes * 60  # Convert to seconds

        # References for interactive commands
        self.scheduler = scheduler
        self.trading_bot = trading_bot
        self._is_paused = False

        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN not set")

        self._app: Optional[Application] = None
        self._pending_approval: Optional[TradeApprovalRequest] = None
        self._approval_event: Optional[asyncio.Event] = None
        self._approval_result: Optional[ApprovalResult] = None
        self._is_running = False

    async def initialize(self):
        """Initialize the bot application."""
        self._app = Application.builder().token(self.token).build()

        # Add command handlers - basic
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        self._app.add_handler(CommandHandler("test", self._cmd_test))

        # Add command handlers - bot control
        self._app.add_handler(CommandHandler("mode", self._cmd_mode))
        self._app.add_handler(CommandHandler("pause", self._cmd_pause))
        self._app.add_handler(CommandHandler("resume", self._cmd_resume))

        # Add command handlers - information
        self._app.add_handler(CommandHandler("balance", self._cmd_balance))
        self._app.add_handler(CommandHandler("positions", self._cmd_positions))
        self._app.add_handler(CommandHandler("signal", self._cmd_signal))
        self._app.add_handler(CommandHandler("jobs", self._cmd_jobs))

        # Add callback handler for inline buttons
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))

        await self._app.initialize()
        logger.info("Telegram bot initialized")

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

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        chat_id = update.effective_chat.id
        await update.message.reply_text(
            f"🤖 *IBIT Trading Bot*\n\n"
            f"Your Chat ID: `{chat_id}`\n\n"
            f"Add this to your `.env` file:\n"
            f"`TELEGRAM_CHAT_ID={chat_id}`\n\n"
            f"Commands:\n"
            f"/status - Check bot status\n"
            f"/help - Show help",
            parse_mode="Markdown",
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command - comprehensive bot status."""
        from .utils import get_et_now

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
                        lines.append(f"• {time_str}: {job.name}")
            except Exception:
                pass

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command."""
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
            "/jobs - View scheduled jobs\n\n"
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

    # ========== Bot Control Commands ==========

    async def _cmd_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /mode command - switch between paper and live mode."""
        args = context.args

        if not self.trading_bot:
            await update.message.reply_text("❌ Trading bot not available. Cannot switch modes.")
            return

        current_mode = "paper" if self.trading_bot.is_paper_mode else "live"

        if not args:
            # Show current mode
            mode_emoji = "📝" if current_mode == "paper" else "💰"
            await update.message.reply_text(
                f"{mode_emoji} *Current Mode: {current_mode.upper()}*\n\n"
                "To switch modes:\n"
                "/mode paper - Switch to paper trading\n"
                "/mode live - Switch to live trading",
                parse_mode="Markdown",
            )
            return

        new_mode = args[0].lower()
        if new_mode not in ["paper", "live"]:
            await update.message.reply_text("❌ Invalid mode. Use 'paper' or 'live'.")
            return

        if new_mode == current_mode:
            await update.message.reply_text(f"Already in {current_mode.upper()} mode.")
            return

        # Switch mode
        if new_mode == "live":
            # Check if E*TRADE is authenticated
            if not self.trading_bot.client or not self.trading_bot.client.is_authenticated():
                await update.message.reply_text(
                    "❌ Cannot switch to LIVE mode.\n\n"
                    "E*TRADE is not authenticated. "
                    "Please complete OAuth setup first."
                )
                return

            self.trading_bot.is_paper_mode = False
            await update.message.reply_text(
                "💰 *Switched to LIVE MODE*\n\n"
                "⚠️ Real money trades will be executed!\n"
                "All trades require your approval.",
                parse_mode="Markdown",
            )
        else:
            self.trading_bot.is_paper_mode = True
            await update.message.reply_text(
                "📝 *Switched to PAPER MODE*\n\n" "Simulated trades only. No real money at risk.",
                parse_mode="Markdown",
            )

        logger.info(f"Mode switched to {new_mode.upper()}")

    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /pause command - pause the scheduler."""
        if self._is_paused:
            await update.message.reply_text("⏸ Already paused.")
            return

        if self.scheduler:
            self.scheduler.scheduler.pause()
            self._is_paused = True
            await update.message.reply_text(
                "⏸ *Scheduler PAUSED*\n\n"
                "No trades will be executed until resumed.\n"
                "Use /resume to continue.",
                parse_mode="Markdown",
            )
            logger.info("Scheduler paused via Telegram")
        else:
            await update.message.reply_text("❌ Scheduler not available.")

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /resume command - resume the scheduler."""
        if not self._is_paused:
            await update.message.reply_text("▶️ Already running.")
            return

        if self.scheduler:
            self.scheduler.scheduler.resume()
            self._is_paused = False
            await update.message.reply_text(
                "▶️ *Scheduler RESUMED*\n\n" "Trading operations are active again.",
                parse_mode="Markdown",
            )
            logger.info("Scheduler resumed via Telegram")
        else:
            await update.message.reply_text("❌ Scheduler not available.")

    # ========== Information Commands ==========

    async def _cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /balance command - show account balance."""
        if not self.trading_bot:
            await update.message.reply_text("❌ Trading bot not available.")
            return

        try:
            portfolio = self.trading_bot.get_portfolio_value()
            cash = portfolio.get("cash", 0)
            total_value = portfolio.get("total_value", cash)
            positions_value = total_value - cash

            mode = "PAPER" if self.trading_bot.is_paper_mode else "LIVE"
            mode_emoji = "📝" if mode == "PAPER" else "💰"

            lines = [
                f"{mode_emoji} *Account Balance ({mode})*\n",
                f"💵 Cash: ${cash:,.2f}",
                f"📊 Positions: ${positions_value:,.2f}",
                f"💼 Total: ${total_value:,.2f}",
            ]

            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ Error fetching balance: {e}")

    async def _cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /positions command - show current positions."""
        if not self.trading_bot:
            await update.message.reply_text("❌ Trading bot not available.")
            return

        try:
            portfolio = self.trading_bot.get_portfolio_value()
            positions = portfolio.get("positions", [])

            if not positions:
                await update.message.reply_text(
                    "📭 *No Open Positions*\n\n" "Currently 100% in cash.",
                    parse_mode="Markdown",
                )
                return

            mode = "PAPER" if self.trading_bot.is_paper_mode else "LIVE"
            lines = [f"📊 *Open Positions ({mode})*\n"]

            for pos in positions:
                symbol = pos.get("symbol", "?")
                shares = pos.get("shares", 0)
                entry = pos.get("entry_price", 0)
                current = pos.get("current_price", 0)
                pnl = pos.get("unrealized_pnl", 0)
                pnl_pct = pos.get("unrealized_pnl_pct", 0)

                emoji = "📈" if pnl >= 0 else "📉"
                sign = "+" if pnl >= 0 else ""

                lines.append(f"\n{emoji} *{symbol}*")
                lines.append(f"• Shares: {shares}")
                lines.append(f"• Entry: ${entry:.2f}")
                lines.append(f"• Current: ${current:.2f}")
                lines.append(f"• P/L: {sign}${pnl:.2f} ({sign}{pnl_pct:.1f}%)")

            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ Error fetching positions: {e}")

    async def _cmd_signal(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /signal command - check today's signal."""
        if not self.trading_bot:
            await update.message.reply_text("❌ Trading bot not available.")
            return

        try:
            from .utils import get_et_now, is_trading_day

            now = get_et_now()
            day_name = now.strftime("%A")

            if not is_trading_day(now.date()):
                await update.message.reply_text(
                    f"📅 *Market Closed*\n\n" f"Today is {day_name}. No trading.",
                    parse_mode="Markdown",
                )
                return

            signal = self.trading_bot.strategy.get_today_signal()
            signal_name = signal.signal.value.upper().replace("_", " ")

            if signal.signal.value == "cash":
                await update.message.reply_text(
                    f"📭 *No Signal Today*\n\n"
                    f"Day: {day_name}\n"
                    f"Reason: {signal.reason or 'No qualifying conditions'}\n\n"
                    "Staying in cash.",
                    parse_mode="Markdown",
                )
            else:
                emoji = self._get_signal_emoji(signal.signal.value)
                etf = signal.etf if hasattr(signal, "etf") else "TBD"

                await update.message.reply_text(
                    f"{emoji} *Signal: {signal_name}*\n\n"
                    f"Day: {day_name}\n"
                    f"ETF: {etf}\n"
                    f"Reason: {signal.reason or 'Conditions met'}",
                    parse_mode="Markdown",
                )
        except Exception as e:
            await update.message.reply_text(f"❌ Error checking signal: {e}")

    async def _cmd_jobs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /jobs command - list scheduled jobs."""
        if not self.scheduler:
            await update.message.reply_text("❌ Scheduler not available.")
            return

        try:
            jobs = self.scheduler.scheduler.get_jobs()
            if not jobs:
                await update.message.reply_text("📅 No jobs scheduled.")
                return

            # Sort by next run time
            sorted_jobs = sorted(
                [j for j in jobs if j.next_run_time],
                key=lambda x: x.next_run_time,
            )

            lines = ["📅 *Scheduled Jobs*\n"]
            for job in sorted_jobs:
                time_str = job.next_run_time.strftime("%I:%M %p")
                date_str = job.next_run_time.strftime("%b %d")
                lines.append(f"• {time_str} ({date_str}): {job.name}")

            if self._is_paused:
                lines.append("\n⏸ _Scheduler is PAUSED_")

            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ Error fetching jobs: {e}")

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline button callbacks."""
        query = update.callback_query
        await query.answer()

        data = query.data
        logger.info(f"Received callback: {data}")

        # Check if this is a test callback
        is_test = "_test_" in data

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

    async def send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        """Send a simple text message."""
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

            # Create message
            emoji = self._get_signal_emoji(signal_type)
            message = (
                f"{emoji} *{signal_type.replace('_', ' ').upper()} SIGNAL*\n\n"
                f"📊 *Details:*\n"
                f"• Reason: {reason}\n"
                f"• ETF: {etf}\n"
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
        await self.send_message(
            f"{emoji} *TRADE EXECUTED*\n\n"
            f"• Signal: {signal_type}\n"
            f"• Action: {action.upper()} {shares} {etf}\n"
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

        await self.send_message(
            f"{emoji} *POSITION CLOSED*\n\n"
            f"• ETF: {etf}\n"
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
        await self.send_message(
            f"⚠️ *ERROR ALERT*\n\n"
            f"• Type: {error_type}\n"
            f"• Message: {message}\n\n"
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


# Synchronous wrapper for use in non-async code
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
        """Run an async coroutine synchronously."""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(coro)

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


# Quick test function
async def test_bot():
    """Test the Telegram bot."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token:
        print("❌ TELEGRAM_BOT_TOKEN not set")
        return

    if not chat_id:
        print("⚠️ TELEGRAM_CHAT_ID not set")
        print("Send /start to your bot to get your chat ID")

    bot = TelegramBot(token, chat_id)
    await bot.initialize()

    if chat_id:
        success = await bot.send_message(
            "🤖 *IBIT Trading Bot*\n\n"
            "✅ Connection test successful!\n"
            "Bot is ready to send trade notifications."
        )
        if success:
            print("✅ Test message sent successfully!")
        else:
            print("❌ Failed to send test message")
    else:
        print("ℹ️ Start your bot and send /start to get your chat ID")


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    asyncio.run(test_bot())
