"""
Trading command handlers for Telegram bot.

Commands in this module:
- Bot control: /mode, /pause, /resume
- Information: /balance, /positions, /signal, /jobs, /logs
"""

import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from ..async_utils import run_sync_in_executor
from .utils import escape_markdown

if TYPE_CHECKING:
    from .bot import TelegramBot

logger = logging.getLogger(__name__)


class TradingCommandsMixin:
    """
    Mixin class providing trading-related Telegram commands.

    This class requires the following attributes from the base class:
    - _is_authorized(update) -> bool
    - _send_unauthorized_response(update) -> None
    - trading_bot: Optional[TradingBot]
    - scheduler: Optional[SmartScheduler]
    - _is_paused: bool
    """

    # =========================================================================
    # Bot Control Commands
    # =========================================================================

    async def _cmd_mode(self: "TelegramBot", update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /mode command - switch between paper and live mode."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        # Deferred import to avoid circular dependency
        from ..trading_bot import TradingMode

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
            # Persist mode change so it survives restarts
            self.trading_bot.db.set_trading_mode("live")
            await update.message.reply_text(
                "💰 *Switched to LIVE MODE*\n\n"
                "⚠️ Real money trades will be executed!\n"
                "All trades require your approval.\n\n"
                "Mode persisted - will survive restarts.",
                parse_mode="Markdown",
            )
        else:
            self.trading_bot.config.mode = TradingMode.PAPER
            # Persist mode change so it survives restarts
            self.trading_bot.db.set_trading_mode("paper")
            await update.message.reply_text(
                "📝 *Switched to PAPER MODE*\n\n"
                "Simulated trades only. No real money at risk.\n\n"
                "Mode persisted - will survive restarts.",
                parse_mode="Markdown",
            )

        logger.info(f"Mode switched to {new_mode.upper()} (persisted to database)")

    async def _cmd_pause(self: "TelegramBot", update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    async def _cmd_resume(self: "TelegramBot", update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    # =========================================================================
    # Information Commands
    # =========================================================================

    async def _cmd_balance(self: "TelegramBot", update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /balance command - show account balance."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        if not self.trading_bot:
            await update.message.reply_text("❌ Trading bot not available.")
            return

        try:
            # Run in thread to avoid event loop conflicts with HTTP libraries
            portfolio = await run_sync_in_executor(self.trading_bot.get_portfolio_value)
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
            await update.message.reply_text(f"❌ Error fetching balance: {escape_markdown(str(e))}")

    async def _cmd_positions(
        self: "TelegramBot", update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Handle /positions command - show current positions."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        if not self.trading_bot:
            await update.message.reply_text("❌ Trading bot not available.")
            return

        try:
            # Run in thread to avoid event loop conflicts with HTTP libraries
            portfolio = await run_sync_in_executor(self.trading_bot.get_portfolio_value)
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
                symbol = escape_markdown(pos.get("symbol", "?"))
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
            await update.message.reply_text(
                f"❌ Error fetching positions: {escape_markdown(str(e))}"
            )

    async def _cmd_signal(self: "TelegramBot", update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /signal command - check today's signal."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        if not self.trading_bot:
            await update.message.reply_text("❌ Trading bot not available.")
            return

        try:
            from ..utils import get_et_now, is_trading_day

            now = get_et_now()
            day_name = now.strftime("%A")

            if not is_trading_day(now.date()):
                await update.message.reply_text(
                    f"📅 *Market Closed*\n\nToday is {day_name}. No trading.",
                    parse_mode="Markdown",
                )
                return

            # Run in thread to avoid event loop conflicts with HTTP libraries
            signal = await run_sync_in_executor(self.trading_bot.strategy.get_today_signal)
            signal_name = signal.signal.value.upper().replace("_", " ")

            if signal.signal.value == "cash":
                reason = escape_markdown(signal.reason or "No qualifying conditions")
                await update.message.reply_text(
                    f"📭 *No Signal Today*\n\n"
                    f"Day: {day_name}\n"
                    f"Reason: {reason}\n\n"
                    "Staying in cash.",
                    parse_mode="Markdown",
                )
            else:
                emoji = self._get_signal_emoji(signal.signal.value)
                etf = escape_markdown(signal.etf if hasattr(signal, "etf") else "TBD")
                reason = escape_markdown(signal.reason or "Conditions met")

                await update.message.reply_text(
                    f"{emoji} *Signal: {signal_name}*\n\n"
                    f"Day: {day_name}\n"
                    f"ETF: {etf}\n"
                    f"Reason: {reason}",
                    parse_mode="Markdown",
                )
        except Exception as e:
            await update.message.reply_text(f"❌ Error checking signal: {escape_markdown(str(e))}")

    async def _cmd_jobs(self: "TelegramBot", update: Update, context: ContextTypes.DEFAULT_TYPE):
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
                lines.append(f"• {time_str} ({date_str}): {escape_markdown(job.name)}")

            if self._is_paused:
                lines.append("\n⏸ _Scheduler is PAUSED_")

            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ Error fetching jobs: {escape_markdown(str(e))}")

    async def _cmd_logs(self: "TelegramBot", update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /logs command - view recent activity logs."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        from ..database import get_database
        from ..utils import get_et_now

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

                # Truncate event if too long and escape markdown characters
                evt_short = evt[:40] + "..." if len(evt) > 40 else evt
                lines.append(f"{emoji} {time_str}: {escape_markdown(evt_short)}")

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
            await update.message.reply_text(f"❌ Error fetching logs: {escape_markdown(str(e))}")
