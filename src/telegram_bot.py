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

        # Add command handlers - bot control
        self._app.add_handler(CommandHandler("mode", self._cmd_mode))
        self._app.add_handler(CommandHandler("pause", self._cmd_pause))
        self._app.add_handler(CommandHandler("resume", self._cmd_resume))

        # Add command handlers - information
        self._app.add_handler(CommandHandler("balance", self._cmd_balance))
        self._app.add_handler(CommandHandler("positions", self._cmd_positions))
        self._app.add_handler(CommandHandler("signal", self._cmd_signal))
        self._app.add_handler(CommandHandler("jobs", self._cmd_jobs))
        self._app.add_handler(CommandHandler("logs", self._cmd_logs))
        self._app.add_handler(CommandHandler("analyze", self._cmd_analyze))
        self._app.add_handler(CommandHandler("patterns", self._cmd_patterns))
        self._app.add_handler(CommandHandler("analyses", self._cmd_analyses))
        self._app.add_handler(CommandHandler("promote", self._cmd_promote))
        self._app.add_handler(CommandHandler("retire", self._cmd_retire))
        self._app.add_handler(CommandHandler("hedge", self._cmd_hedge))
        self._app.add_handler(CommandHandler("review", self._cmd_review))

        # Add command handlers - E*TRADE authentication
        self._app.add_handler(CommandHandler("auth", self._cmd_auth))
        self._app.add_handler(CommandHandler("verify", self._cmd_verify))

        # Add command handlers - backtesting
        self._app.add_handler(CommandHandler("backtest", self._cmd_backtest))

        # Add command handlers - simulation
        self._app.add_handler(CommandHandler("simulate", self._cmd_simulate))

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
            "  `/simulate Jan 2024 to Jun 2024` - Month range\n"
            "  `/simulate 2024-01-01 to 2024-06-30` - Date range\n\n"
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

    # ========== Bot Control Commands ==========

    async def _cmd_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /mode command - switch between paper and live mode."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        # Deferred import to avoid circular dependency
        from .trading_bot import TradingMode

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

            self.trading_bot.config.mode = TradingMode.LIVE
            await update.message.reply_text(
                "💰 *Switched to LIVE MODE*\n\n"
                "⚠️ Real money trades will be executed!\n"
                "All trades require your approval.",
                parse_mode="Markdown",
            )
        else:
            self.trading_bot.config.mode = TradingMode.PAPER
            await update.message.reply_text(
                "📝 *Switched to PAPER MODE*\n\nSimulated trades only. No real money at risk.",
                parse_mode="Markdown",
            )

        logger.info(f"Mode switched to {new_mode.upper()}")

    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /pause command - pause the scheduler."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

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
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        if not self._is_paused:
            await update.message.reply_text("▶️ Already running.")
            return

        if self.scheduler:
            self.scheduler.scheduler.resume()
            self._is_paused = False
            await update.message.reply_text(
                "▶️ *Scheduler RESUMED*\n\nTrading operations are active again.",
                parse_mode="Markdown",
            )
            logger.info("Scheduler resumed via Telegram")
        else:
            await update.message.reply_text("❌ Scheduler not available.")

    # ========== Information Commands ==========

    async def _cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /balance command - show account balance."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

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
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        if not self.trading_bot:
            await update.message.reply_text("❌ Trading bot not available.")
            return

        try:
            portfolio = self.trading_bot.get_portfolio_value()
            positions = portfolio.get("positions", [])

            if not positions:
                await update.message.reply_text(
                    "📭 *No Open Positions*\n\nCurrently 100% in cash.",
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
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        if not self.trading_bot:
            await update.message.reply_text("❌ Trading bot not available.")
            return

        try:
            from .utils import get_et_now, is_trading_day

            now = get_et_now()
            day_name = now.strftime("%A")

            if not is_trading_day(now.date()):
                await update.message.reply_text(
                    f"📅 *Market Closed*\n\nToday is {day_name}. No trading.",
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
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

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

    async def _cmd_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /logs command - view recent activity logs."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        from .database import get_database
        from .utils import get_et_now

        try:
            db = get_database()
            now = get_et_now()
            today_str = now.strftime("%Y-%m-%d")

            # Get today's logs, most recent first
            events = db.get_events(since=today_str, limit=20)

            if not events:
                await update.message.reply_text(
                    "📋 *Activity Logs*\n\nNo activity logged today.",
                    parse_mode="Markdown",
                )
                return

            lines = [f"📋 *Activity Logs* ({now.strftime('%b %d')})\n"]

            for event in reversed(events):  # Show oldest first
                timestamp = event.get("timestamp", "")
                level = event.get("level", "")
                evt = event.get("event", "")

                # Parse timestamp to show just time
                try:
                    from datetime import datetime

                    ts = datetime.fromisoformat(timestamp)
                    time_str = ts.strftime("%I:%M %p")
                except Exception:
                    time_str = timestamp[:8] if timestamp else "?"

                # Emoji based on event type
                emoji = "📊"
                if "SIGNAL" in level:
                    emoji = "🔍"
                elif "APPROVAL" in level:
                    emoji = "✋"
                elif "TRADE" in level:
                    emoji = "💰"
                elif "ERROR" in level:
                    emoji = "❌"
                elif "DUPLICATE" in level:
                    emoji = "🚫"
                elif "SCHEDULER" in level:
                    emoji = "⏰"

                # Truncate event if too long
                evt_short = evt[:40] + "..." if len(evt) > 40 else evt
                lines.append(f"{emoji} {time_str}: {evt_short}")

            # Add summary of event types
            signal_checks = sum(1 for e in events if "SIGNAL" in e.get("level", ""))
            trades = sum(1 for e in events if "TRADE" in e.get("level", ""))
            approvals = sum(1 for e in events if "APPROVAL" in e.get("level", ""))

            if signal_checks or trades or approvals:
                lines.append(
                    f"\n📈 Summary: {signal_checks} signals, {approvals} approvals, {trades} trades"
                )

            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

        except Exception as e:
            await update.message.reply_text(f"❌ Error fetching logs: {e}")

    async def _cmd_analyze(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /analyze command - run pattern discovery analysis with raw data."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        await update.message.reply_text(
            "🔍 *Pattern Analysis Starting*\n\n"
            "Collecting 90 days of raw market data:\n"
            "• IBIT (Bitcoin spot ETF)\n"
            "• BTC/USD (spot crypto)\n"
            "• BITO (Bitcoin futures ETF)\n\n"
            "This may take 30-60 seconds.",
            parse_mode="Markdown",
        )

        try:
            from .pattern_discovery import (
                PatternStatus,
                get_data_collector,
                get_pattern_analyzer,
                get_pattern_registry,
            )

            # Collect raw OHLCV data for multiple instruments
            collector = get_data_collector(lookback_days=90)
            raw_data = collector.collect_raw_bars()

            if not raw_data or not raw_data.get("ibit_bars"):
                await update.message.reply_text("❌ Failed to collect market data from Alpaca")
                return

            # Show data summary
            ibit_count = len(raw_data.get("ibit_bars", []))
            btc_count = len(raw_data.get("btc_bars", []))
            bito_count = len(raw_data.get("bito_bars", []))

            await update.message.reply_text(
                f"📊 *Raw Data Collected*\n\n"
                f"• IBIT: {ibit_count} daily bars\n"
                f"• BTC/USD: {btc_count} daily bars\n"
                f"• BITO: {bito_count} daily bars\n\n"
                f"Sending raw OHLCV data to Claude for pattern analysis...\n"
                f"Claude will analyze cross-market correlations.",
                parse_mode="Markdown",
            )

            # Run analysis with raw data
            registry = get_pattern_registry()
            active_patterns = registry.get_live_patterns()

            analyzer = get_pattern_analyzer()
            new_patterns = await analyzer.analyze_raw(
                ibit_bars=raw_data.get("ibit_bars", []),
                btc_bars=raw_data.get("btc_bars", []),
                bito_bars=raw_data.get("bito_bars", []),
                active_patterns=active_patterns,
            )

            if not new_patterns:
                await update.message.reply_text(
                    "📊 *Analysis Complete*\n\n"
                    "No new patterns discovered that meet quality thresholds:\n"
                    "• Sample size ≥ 15\n"
                    "• Win rate ≥ 52%\n"
                    "• Expected edge ≥ 0.15%\n\n"
                    "This is normal - the system is being conservative.\n"
                    "Use /analyses to view Claude's full response.",
                    parse_mode="Markdown",
                )
            else:
                # Add patterns as candidates
                for pattern in new_patterns:
                    pattern.status = PatternStatus.CANDIDATE
                    registry.add_pattern(pattern)

                pattern_list = "\n".join(
                    f"• *{p.display_name}*\n"
                    f"  {p.signal.value.upper()} {p.instrument} @ {p.entry_time}-{p.exit_time}\n"
                    f"  {p.confidence:.0%} win rate, {p.expected_edge:.2f}% edge"
                    for p in new_patterns
                )

                await update.message.reply_text(
                    f"🎯 *Analysis Complete*\n\n"
                    f"Discovered {len(new_patterns)} new pattern(s):\n\n"
                    f"{pattern_list}\n\n"
                    f"Status: CANDIDATE\n"
                    f"Use /patterns to view all patterns.\n"
                    f"Use /analyses to view Claude's full response.",
                    parse_mode="Markdown",
                )

        except Exception as e:
            await update.message.reply_text(f"❌ Analysis failed: {e}")

    async def _cmd_patterns(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /patterns command - view discovered patterns."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        try:
            from .pattern_discovery import get_pattern_registry

            registry = get_pattern_registry()

            if not registry.patterns:
                await update.message.reply_text(
                    "📋 *Pattern Registry*\n\n"
                    "No patterns discovered yet.\n"
                    "Use /analyze to run pattern discovery.",
                    parse_mode="Markdown",
                )
                return

            # Group by status
            live = registry.get_live_patterns()
            paper = registry.get_paper_patterns()
            candidates = registry.get_candidate_patterns()

            lines = ["📋 *Pattern Registry*\n"]

            if live:
                lines.append("\n🟢 *LIVE* (actively trading):")
                for p in live:
                    lines.append(
                        f"  • {p.display_name}\n"
                        f"    {p.signal.value.upper()} {p.instrument} @ {p.entry_time}"
                    )

            if paper:
                lines.append("\n🟡 *PAPER* (validation):")
                for p in paper:
                    lines.append(
                        f"  • {p.display_name}\n"
                        f"    {p.validation_trades} trades, ${p.validation_pnl:.2f} P&L"
                    )

            if candidates:
                lines.append("\n⚪ *CANDIDATE* (pending validation):")
                for p in candidates:
                    lines.append(
                        f"  • {p.display_name}\n"
                        f"    {p.confidence:.0%} conf, {p.expected_edge:.2f}% edge"
                    )

            lines.append(f"\nTotal: {len(registry.patterns)} pattern(s)")

            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")

    async def _cmd_analyses(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /analyses command - view past Claude pattern analyses."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        import json

        from .database import get_database

        try:
            db = get_database()

            # Get PATTERN_ANALYSIS events
            events = db.get_events(level="PATTERN_ANALYSIS", limit=5)

            if not events:
                await update.message.reply_text(
                    "🔬 *Claude Analyses*\n\n"
                    "No pattern analyses found.\n"
                    "Use /analyze to run pattern discovery.",
                    parse_mode="Markdown",
                )
                return

            lines = ["🔬 *Claude Analyses*\n"]

            for i, event in enumerate(events, 1):
                timestamp = event.get("timestamp", "")
                details_str = event.get("details", "{}")

                try:
                    details = json.loads(details_str) if details_str else {}
                except Exception:
                    details = {}

                # Parse timestamp
                try:
                    from datetime import datetime

                    ts = datetime.fromisoformat(timestamp)
                    date_str = ts.strftime("%b %d, %I:%M %p")
                except Exception:
                    date_str = timestamp[:16] if timestamp else "?"

                model = details.get("model", "unknown")
                lookback = details.get("lookback_days", "?")
                response = details.get("response", "")

                # Truncate response for display
                if len(response) > 500:
                    response = response[:500] + "..."

                lines.append(f"\n*{i}. {date_str}*")
                lines.append(f"Model: `{model}` | Lookback: {lookback} days")
                lines.append(f"```\n{response}\n```")

            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")

    async def _cmd_promote(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /promote command - promote a pattern to paper or live status."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        from .pattern_discovery import PatternStatus, get_pattern_registry

        args = context.args
        if not args or len(args) < 2:
            # Show usage and list available patterns
            registry = get_pattern_registry()
            candidates = registry.get_candidate_patterns()
            paper = registry.get_paper_patterns()

            pattern_list = ""
            if candidates:
                pattern_list += "\n*Candidates:*\n"
                pattern_list += "\n".join(f"  • `{p.name}`" for p in candidates)
            if paper:
                pattern_list += "\n*Paper:*\n"
                pattern_list += "\n".join(f"  • `{p.name}`" for p in paper)

            if not pattern_list:
                pattern_list = "\nNo patterns available to promote."

            await update.message.reply_text(
                "📈 *Promote Pattern*\n\n"
                "Usage: `/promote <pattern_name> <paper|live>`\n"
                f"{pattern_list}",
                parse_mode="Markdown",
            )
            return

        pattern_name = args[0]
        target_status = args[1].lower()

        if target_status not in ("paper", "live"):
            await update.message.reply_text(
                "❌ Invalid status. Use `paper` or `live`.",
                parse_mode="Markdown",
            )
            return

        registry = get_pattern_registry()
        pattern = registry.get_pattern(pattern_name)

        if not pattern:
            await update.message.reply_text(
                f"❌ Pattern `{pattern_name}` not found.\nUse /patterns to see available patterns.",
                parse_mode="Markdown",
            )
            return

        # Validate promotion path
        new_status = PatternStatus.PAPER if target_status == "paper" else PatternStatus.LIVE
        old_status = pattern.status

        if new_status == PatternStatus.LIVE and old_status == PatternStatus.CANDIDATE:
            # Warn about skipping paper validation
            await update.message.reply_text(
                f"⚠️ *Warning*: Promoting directly to LIVE skips paper validation.\n\n"
                f"Pattern: `{pattern_name}`\n"
                f"From: {old_status.value} → To: {new_status.value}\n\n"
                f"Proceeding with promotion...",
                parse_mode="Markdown",
            )

        success = registry.promote_pattern(pattern_name, new_status)

        if success:
            emoji = "🟡" if new_status == PatternStatus.PAPER else "🟢"
            await update.message.reply_text(
                f"{emoji} *Pattern Promoted*\n\n"
                f"Pattern: `{pattern_name}`\n"
                f"Status: {old_status.value} → *{new_status.value}*\n\n"
                f"Use /patterns to view all patterns.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(f"❌ Failed to promote pattern `{pattern_name}`.")

    async def _cmd_hedge(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /hedge command - view and control trailing hedge settings."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        from .trailing_hedge import get_hedge_manager

        args = context.args
        manager = get_hedge_manager()

        if not args:
            # Show current hedge status
            status = manager.get_status()

            if not status.get("active"):
                lines = [
                    "🛡️ *Trailing Hedge Status*\n",
                    f"Enabled: {'✅' if manager.config.enabled else '❌'}",
                    "",
                    "*No active position being tracked*",
                    "",
                    "Hedges will activate after opening a position.",
                    "",
                    "*Tier Configuration:*",
                ]
                for i, tier in enumerate(manager.config.tiers, 1):
                    lines.append(
                        f"  {i}. +{tier.gain_threshold_pct}% gain → +{tier.hedge_size_pct}% hedge"
                    )
                lines.append(f"\nMax hedge: {manager.config.max_hedge_pct}%")
                lines.append("\n*Commands:*")
                lines.append("`/hedge on` - Enable hedging")
                lines.append("`/hedge off` - Disable hedging")
            else:
                pos = status["position"]
                hedge = status["hedge"]

                lines = [
                    "🛡️ *Trailing Hedge Status*\n",
                    f"Enabled: {'✅' if status['enabled'] else '❌'}",
                    "",
                    "*Active Position:*",
                    f"  • {pos['instrument']}: {pos['shares']} shares",
                    f"  • Entry: ${pos['entry_price']:.2f}",
                    f"  • Value: ${pos['original_value']:.2f}",
                    "",
                    "*Hedge Status:*",
                    f"  • Instrument: {hedge['instrument']}",
                    f"  • Shares: {hedge['shares']}",
                    f"  • Coverage: {hedge['total_pct']:.1f}%",
                    f"  • Tiers triggered: {hedge['tiers_triggered']}/{hedge['tiers_total']}",
                ]

                # Show tier details
                lines.append("\n*Tier Configuration:*")
                for i, tier in enumerate(manager.config.tiers, 1):
                    status_emoji = "✅" if tier.triggered else "⏳"
                    lines.append(
                        f"  {status_emoji} +{tier.gain_threshold_pct}% → "
                        f"+{tier.hedge_size_pct}% hedge"
                    )

            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
            return

        # Handle subcommands
        subcommand = args[0].lower()

        if subcommand == "on":
            manager.config.enabled = True
            await update.message.reply_text(
                "✅ *Trailing Hedge ENABLED*\n\nHedges will be added as positions gain value.",
                parse_mode="Markdown",
            )
        elif subcommand == "off":
            manager.config.enabled = False
            await update.message.reply_text(
                "❌ *Trailing Hedge DISABLED*\n\nNo automatic hedges will be placed.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                "❌ Unknown subcommand.\n\n"
                "Usage:\n"
                "`/hedge` - View status\n"
                "`/hedge on` - Enable hedging\n"
                "`/hedge off` - Disable hedging",
                parse_mode="Markdown",
            )

    async def _cmd_review(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /review command - run strategy review with Claude analysis."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        await update.message.reply_text(
            "📊 *Strategy Review Starting*\n\n"
            "Collecting 90 days of IBIT market data...\n"
            "Running backtests with different parameters...\n"
            "Sending to Claude for analysis...\n\n"
            "This may take 30-60 seconds.",
            parse_mode="Markdown",
        )

        try:
            from .strategy_review import (
                get_strategy_reviewer,
                set_pending_recommendations,
            )

            reviewer = get_strategy_reviewer()
            result = await reviewer.run_monthly_review()

            # Build response message
            if result.has_recommendations:
                header = "📊 *Strategy Review Complete*\n⚠️ Recommendations Detected!\n\n"
            else:
                header = "📊 *Strategy Review Complete*\n✅ No changes needed\n\n"

            message = header + result.full_report

            # Truncate if too long for Telegram (max 4096 chars)
            if len(message) > 4000:
                message = message[:3950] + "\n\n... (truncated)"

            await update.message.reply_text(message, parse_mode="Markdown")

            # If there are recommendations, show approval buttons
            if result.recommendations:
                set_pending_recommendations(result.recommendations)

                for i, rec in enumerate(result.recommendations):
                    # Build recommendation message with approval buttons
                    rec_msg = (
                        f"🔧 *Parameter Change Recommendation {i + 1}*\n\n"
                        f"*{rec.to_display_name()}*\n"
                        f"Current: `{rec.current_value}`\n"
                        f"Recommended: `{rec.recommended_value}`\n"
                        f"Confidence: {rec.confidence.upper()}\n\n"
                        f"_{rec.reason}_"
                    )

                    # Show backtest return if available (proves it was actually tested)
                    if rec.backtest_return is not None:
                        rec_msg += f"\n\n📊 Backtest Return: `{rec.backtest_return:+.2f}%`"

                    if rec.expected_improvement:
                        rec_msg += f"\n📈 Expected: {rec.expected_improvement}"

                    keyboard = InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "✅ Apply Change", callback_data=f"apply_param_{i}"
                                ),
                                InlineKeyboardButton(
                                    "❌ Reject", callback_data=f"reject_param_{i}"
                                ),
                            ]
                        ]
                    )

                    await update.message.reply_text(
                        rec_msg,
                        parse_mode="Markdown",
                        reply_markup=keyboard,
                    )

        except Exception as e:
            logger.error(f"Strategy review failed: {e}")
            await update.message.reply_text(
                f"❌ Strategy review failed: {e}",
                parse_mode=None,  # Avoid markdown parsing issues with error messages
            )

    async def _cmd_retire(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /retire command - retire a pattern that's no longer working."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        from .pattern_discovery import get_pattern_registry

        args = context.args
        if not args:
            # Show usage and list active patterns
            registry = get_pattern_registry()
            live = registry.get_live_patterns()
            paper = registry.get_paper_patterns()
            candidates = registry.get_candidate_patterns()

            pattern_list = ""
            if live:
                pattern_list += "\n*Live:*\n"
                pattern_list += "\n".join(f"  • `{p.name}`" for p in live)
            if paper:
                pattern_list += "\n*Paper:*\n"
                pattern_list += "\n".join(f"  • `{p.name}`" for p in paper)
            if candidates:
                pattern_list += "\n*Candidates:*\n"
                pattern_list += "\n".join(f"  • `{p.name}`" for p in candidates)

            if not pattern_list:
                pattern_list = "\nNo patterns available to retire."

            await update.message.reply_text(
                f"🔴 *Retire Pattern*\n\nUsage: `/retire <pattern_name> [reason]`\n{pattern_list}",
                parse_mode="Markdown",
            )
            return

        pattern_name = args[0]
        reason = " ".join(args[1:]) if len(args) > 1 else "Manually retired"

        registry = get_pattern_registry()
        pattern = registry.get_pattern(pattern_name)

        if not pattern:
            await update.message.reply_text(
                f"❌ Pattern `{pattern_name}` not found.\nUse /patterns to see available patterns.",
                parse_mode="Markdown",
            )
            return

        old_status = pattern.status
        success = registry.retire_pattern(pattern_name, reason)

        if success:
            await update.message.reply_text(
                f"🔴 *Pattern Retired*\n\n"
                f"Pattern: `{pattern_name}`\n"
                f"Previous status: {old_status.value}\n"
                f"Reason: {reason}\n\n"
                f"The pattern will no longer trade.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(f"❌ Failed to retire pattern `{pattern_name}`.")

    # ========== Backtesting Commands ==========

    def _parse_time_range(self, args: list) -> tuple:
        """
        Parse natural language time range into days.

        Examples:
            "3 months" -> 90 days
            "1 week" -> 7 days
            "30 days" -> 30 days
            "6m" -> 180 days
            "2w" -> 14 days

        Returns:
            Tuple of (days, description) or (None, error_message)
        """
        if not args:
            return 90, "3 months"  # Default

        # Join args and normalize
        text = " ".join(args).lower().strip()

        # Handle shorthand like "3m", "2w", "30d"
        import re

        shorthand = re.match(r"^(\d+)\s*([mwd])$", text)
        if shorthand:
            num = int(shorthand.group(1))
            unit = shorthand.group(2)
            if unit == "m":
                return num * 30, f"{num} month{'s' if num > 1 else ''}"
            elif unit == "w":
                return num * 7, f"{num} week{'s' if num > 1 else ''}"
            elif unit == "d":
                return num, f"{num} day{'s' if num > 1 else ''}"

        # Handle full words
        match = re.match(r"^(\d+)\s*(month|months|week|weeks|day|days)$", text)
        if match:
            num = int(match.group(1))
            unit = match.group(2)
            if "month" in unit:
                return num * 30, f"{num} month{'s' if num > 1 else ''}"
            elif "week" in unit:
                return num * 7, f"{num} week{'s' if num > 1 else ''}"
            elif "day" in unit:
                return num, f"{num} day{'s' if num > 1 else ''}"

        # Handle just a number (assume days)
        if text.isdigit():
            num = int(text)
            return num, f"{num} days"

        return None, f"Could not parse '{text}'. Try: `3 months`, `2 weeks`, `30 days`"

    async def _cmd_backtest(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /backtest command - run strategy backtests."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        # Parse time range
        days, description = self._parse_time_range(context.args)

        if days is None:
            await update.message.reply_text(
                f"❌ {description}\n\n"
                "*Usage:*\n"
                "`/backtest 3 months`\n"
                "`/backtest 2 weeks`\n"
                "`/backtest 30 days`\n"
                "`/backtest 6m`\n"
                "`/backtest` (defaults to 3 months)",
                parse_mode="Markdown",
            )
            return

        await update.message.reply_text(
            f"📊 *Running Backtest*\n\n"
            f"Period: {description} ({days} days)\n"
            f"Loading IBIT data from Yahoo Finance...\n\n"
            f"This may take 10-30 seconds.",
            parse_mode="Markdown",
        )

        try:
            from datetime import date, timedelta

            from .multi_strategy_backtester import MultiStrategyBacktester

            # Calculate date range
            end_date = date.today()
            start_date = end_date - timedelta(days=days)

            # Run backtests
            backtester = MultiStrategyBacktester(initial_capital=10000.0)
            backtester.load_data(start_date, end_date)

            # Run each strategy
            mean_rev = backtester.backtest_mean_reversion(threshold=-2.0, skip_thursday=True)
            short_thu = backtester.backtest_short_thursday()
            combined = backtester.backtest_combined(
                mean_reversion_threshold=-2.0, enable_short_thursday=True
            )

            # Format results
            lines = [
                "📈 *Backtest Results*",
                f"_{start_date} to {end_date}_\n",
            ]

            # Mean Reversion results
            lines.append("*1️⃣ Mean Reversion* (buy after -2% days)")
            lines.append(f"   Trades: {mean_rev.total_trades}")
            lines.append(f"   Win Rate: {mean_rev.win_rate:.1f}%")
            lines.append(f"   Return: {mean_rev.total_return_pct:+.1f}%")
            lines.append(f"   Avg Trade: {mean_rev.avg_return_pct:+.2f}%")
            lines.append("")

            # Short Thursday results
            lines.append("*2️⃣ Short Thursday* (short every Thursday)")
            lines.append(f"   Trades: {short_thu.total_trades}")
            lines.append(f"   Win Rate: {short_thu.win_rate:.1f}%")
            lines.append(f"   Return: {short_thu.total_return_pct:+.1f}%")
            lines.append(f"   Avg Trade: {short_thu.avg_return_pct:+.2f}%")
            lines.append("")

            # Combined results
            lines.append("*3️⃣ Combined Strategy*")
            lines.append(f"   Trades: {combined.total_trades}")
            lines.append(f"   Win Rate: {combined.win_rate:.1f}%")
            lines.append(f"   Return: {combined.total_return_pct:+.1f}%")
            lines.append(f"   Sharpe: {combined.sharpe_ratio:.2f}")
            lines.append(f"   Max DD: {combined.max_drawdown_pct:.1f}%")
            lines.append("")

            # Buy & Hold comparison
            lines.append("*📊 Comparison*")
            lines.append(f"   Buy & Hold: {combined.buy_hold_return_pct:+.1f}%")
            lines.append(
                f"   Strategy vs B&H: {combined.total_return_pct - combined.buy_hold_return_pct:+.1f}%"
            )

            # Best strategy
            best = max(
                [
                    ("Mean Reversion", mean_rev),
                    ("Short Thursday", short_thu),
                    ("Combined", combined),
                ],
                key=lambda x: x[1].total_return_pct,
            )
            lines.append("")
            lines.append(f"🏆 *Best:* {best[0]} ({best[1].total_return_pct:+.1f}%)")

            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

        except ImportError as e:
            await update.message.reply_text(
                f"❌ Missing dependency: {e}\n\n" "Install with: `pip install yfinance`",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Backtest failed: {e}")
            await update.message.reply_text(f"❌ Backtest failed: {e}")

    # ========== Historical Simulation Commands ==========

    def _parse_simulation_period(self, args: list) -> tuple:
        """
        Parse simulation period from command arguments.

        Examples:
            "2024" -> (Jan 1 2024, Dec 31 2024)
            "6 months" -> (6 months ago, today)
            "1 year" -> (1 year ago, today)
            "2024-01-01 to 2024-06-30" -> explicit date range
            "Jan 2024 to Jun 2024" -> month-year range

        Returns:
            Tuple of (start_date, end_date, description) or (None, None, error_message)
        """
        import calendar
        import re
        from datetime import datetime, timedelta

        # Month name mapping
        month_map = {
            "jan": 1,
            "january": 1,
            "feb": 2,
            "february": 2,
            "mar": 3,
            "march": 3,
            "apr": 4,
            "april": 4,
            "may": 5,
            "jun": 6,
            "june": 6,
            "jul": 7,
            "july": 7,
            "aug": 8,
            "august": 8,
            "sep": 9,
            "sept": 9,
            "september": 9,
            "oct": 10,
            "october": 10,
            "nov": 11,
            "november": 11,
            "dec": 12,
            "december": 12,
        }

        def parse_month_year(text: str) -> tuple:
            """Parse 'Jan 2024' or 'January 2024' into (year, month)."""
            text = text.strip().lower()
            for month_name, month_num in month_map.items():
                if text.startswith(month_name):
                    # Extract year
                    year_match = re.search(r"(\d{4})", text)
                    if year_match:
                        return int(year_match.group(1)), month_num
            return None, None

        def last_day_of_month(year: int, month: int) -> int:
            """Get the last day of a month."""
            return calendar.monthrange(year, month)[1]

        if not args:
            # Default to current year
            now = datetime.now()
            return (
                datetime(now.year, 1, 1),
                datetime(now.year, 12, 31),
                f"{now.year}",
            )

        text = " ".join(args).strip()
        text_lower = text.lower()

        # Handle explicit date range with "to" or "-"
        # Pattern: "2024-01-01 to 2024-06-30" or "2024-01-01 - 2024-06-30"
        iso_range = re.match(
            r"^(\d{4}-\d{2}-\d{2})\s*(?:to|-)\s*(\d{4}-\d{2}-\d{2})$",
            text_lower,
        )
        if iso_range:
            try:
                start_date = datetime.strptime(iso_range.group(1), "%Y-%m-%d")
                end_date = datetime.strptime(iso_range.group(2), "%Y-%m-%d")
                desc = f"{start_date.strftime('%b %d, %Y')} to {end_date.strftime('%b %d, %Y')}"
                return (start_date, end_date, desc)
            except ValueError:
                pass

        # Handle month-year range: "Jan 2024 to Jun 2024"
        if " to " in text_lower:
            parts = text_lower.split(" to ")
            if len(parts) == 2:
                start_year, start_month = parse_month_year(parts[0])
                end_year, end_month = parse_month_year(parts[1])

                if start_year and start_month and end_year and end_month:
                    start_date = datetime(start_year, start_month, 1)
                    end_date = datetime(end_year, end_month, last_day_of_month(end_year, end_month))
                    # Get month names for description
                    start_name = calendar.month_abbr[start_month]
                    end_name = calendar.month_abbr[end_month]
                    desc = f"{start_name} {start_year} to {end_name} {end_year}"
                    return (start_date, end_date, desc)

        # Handle single month-year: "Jan 2024" -> full month
        single_year, single_month = parse_month_year(text_lower)
        if single_year and single_month:
            start_date = datetime(single_year, single_month, 1)
            end_date = datetime(
                single_year, single_month, last_day_of_month(single_year, single_month)
            )
            month_name = calendar.month_name[single_month]
            desc = f"{month_name} {single_year}"
            return (start_date, end_date, desc)

        # Handle year like "2024"
        if text_lower.isdigit() and len(text_lower) == 4:
            year = int(text_lower)
            return (
                datetime(year, 1, 1),
                datetime(year, 12, 31),
                f"{year}",
            )

        # Handle relative periods like "6 months", "1 year"
        match = re.match(r"^(\d+)\s*(month|months|year|years)$", text_lower)
        if match:
            num = int(match.group(1))
            unit = match.group(2)
            end_date = datetime.now()

            if "year" in unit:
                start_date = datetime(end_date.year - num, end_date.month, end_date.day)
                desc = f"{num} year{'s' if num > 1 else ''}"
            else:
                start_date = end_date - timedelta(days=num * 30)
                desc = f"{num} month{'s' if num > 1 else ''}"

            return (start_date, end_date, desc)

        # Handle shorthand like "6m", "1y"
        shorthand = re.match(r"^(\d+)\s*([my])$", text_lower)
        if shorthand:
            num = int(shorthand.group(1))
            unit = shorthand.group(2)
            end_date = datetime.now()

            if unit == "y":
                start_date = datetime(end_date.year - num, end_date.month, end_date.day)
                desc = f"{num} year{'s' if num > 1 else ''}"
            else:
                start_date = end_date - timedelta(days=num * 30)
                desc = f"{num} month{'s' if num > 1 else ''}"

            return (start_date, end_date, desc)

        return (
            None,
            None,
            f"Could not parse '{text}'.\n"
            "Try: `2024`, `6 months`, `Jan 2024 to Jun 2024`, `2024-01-01 to 2024-06-30`",
        )

    async def _cmd_simulate(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /simulate command - run historical AI evolution simulation."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        # Parse simulation period
        start_date, end_date, description = self._parse_simulation_period(context.args)

        if start_date is None:
            await update.message.reply_text(
                f"❌ {description}\n\n"
                "*Usage:*\n"
                "`/simulate 2024` - Full year\n"
                "`/simulate 6 months` - Last 6 months\n"
                "`/simulate Jan 2024 to Jun 2024` - Month range\n"
                "`/simulate 2024-01-01 to 2024-06-30` - Date range\n"
                "`/simulate Mar 2024` - Single month\n",
                parse_mode="Markdown",
            )
            return

        # Check if period is reasonable
        days = (end_date - start_date).days
        estimated_reviews = days // 14
        estimated_cost = estimated_reviews * 0.08

        # Warn about long simulations
        if estimated_reviews > 30:
            await update.message.reply_text(
                f"⚠️ *Long Simulation Warning*\n\n"
                f"Period: {description}\n"
                f"Reviews: ~{estimated_reviews}\n"
                f"Est. Cost: ~${estimated_cost:.2f}\n"
                f"Est. Time: ~{estimated_reviews * 3} seconds\n\n"
                f"Proceeding anyway...",
                parse_mode="Markdown",
            )

        # Send initial status
        status_msg = await update.message.reply_text(
            f"🔬 *Starting Historical Simulation*\n\n"
            f"Period: {description}\n"
            f"({start_date.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')})\n\n"
            f"Reviews: ~{estimated_reviews}\n"
            f"Est. Cost: ~${estimated_cost:.2f}\n\n"
            f"⏳ Running simulation...\n"
            f"This may take a few minutes.",
            parse_mode="Markdown",
        )

        try:
            from .historical_simulator import HistoricalSimulator

            simulator = HistoricalSimulator()
            result = await simulator.run_simulation(
                start_date=start_date,
                end_date=end_date,
                review_interval_days=14,
                lookback_days=60,
            )

            # Generate and send report
            report = result.format_report()

            # Split if too long for Telegram
            if len(report) > 4000:
                # Send in parts
                parts = [report[i : i + 4000] for i in range(0, len(report), 4000)]
                for i, part in enumerate(parts):
                    if i == 0:
                        await status_msg.edit_text(part, parse_mode="Markdown")
                    else:
                        await update.message.reply_text(part, parse_mode="Markdown")
            else:
                await status_msg.edit_text(report, parse_mode="Markdown")

        except Exception as e:
            logger.error(f"Simulation failed: {e}")
            await status_msg.edit_text(
                f"❌ *Simulation Failed*\n\n" f"Error: {e}\n\n" f"Check logs for details.",
                parse_mode="Markdown",
            )

    # ========== E*TRADE Authentication Commands ==========

    async def _cmd_auth(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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

            # Create a temporary client for auth
            from .etrade_client import ETradeClient

            try:
                temp_client = ETradeClient(consumer_key, consumer_secret)
            except Exception as e:
                await update.message.reply_text(f"❌ Failed to create client: {e}")
                return
        else:
            temp_client = self.trading_bot.client

        if not temp_client:
            await update.message.reply_text("❌ E*TRADE client not available.")
            return

        try:
            # Get authorization URL
            auth_url, request_token = temp_client.get_authorization_url()

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

        except Exception as e:
            logger.error(f"Failed to get auth URL: {e}")
            # Don't use Markdown - error messages may contain special chars
            await update.message.reply_text(
                f"❌ Authorization Failed\n\nCould not connect to E*TRADE:\n{e}"
            )

    async def _cmd_verify(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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

            # Complete authorization
            success = client.complete_authorization(verifier, request_token)

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
        from .strategy_review import (
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

            if success:
                await query.edit_message_text(
                    text=(
                        f"✅ *Parameter Updated!*\n\n"
                        f"*{rec.to_display_name()}*\n"
                        f"`{rec.current_value}` → `{rec.recommended_value}`\n\n"
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
            await query.edit_message_text(
                text=(
                    f"❌ *Recommendation Rejected*\n\n"
                    f"*{rec.to_display_name()}*\n"
                    f"Keeping current value: `{rec.current_value}`"
                ),
                parse_mode="Markdown",
            )

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
