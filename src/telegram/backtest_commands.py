"""
Backtesting command handlers for Telegram bot.

Commands in this module:
- /backtest - Run strategy backtests
- /simulate - Run historical AI evolution simulation
"""

import calendar
import logging
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional, Tuple

from telegram import Update
from telegram.ext import ContextTypes

from .utils import escape_markdown

if TYPE_CHECKING:
    from .bot import TelegramBot

logger = logging.getLogger(__name__)


class BacktestCommandsMixin:
    """
    Mixin class providing backtesting-related Telegram commands.

    This class requires the following attributes from the base class:
    - _is_authorized(update) -> bool
    - _send_unauthorized_response(update) -> None
    """

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _parse_time_range(self: "TelegramBot", args: list) -> Tuple[Optional[int], str]:
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

    def _parse_simulation_period(
        self: "TelegramBot", args: list
    ) -> Tuple[Optional[datetime], Optional[datetime], str]:
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

        def parse_month_year(text: str) -> Tuple[Optional[int], Optional[int]]:
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
                desc = f"{start_date.strftime('%b %d, %Y')} to " f"{end_date.strftime('%b %d, %Y')}"
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

    # =========================================================================
    # Backtest Commands
    # =========================================================================

    async def _cmd_backtest(
        self: "TelegramBot", update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
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
            from datetime import date

            from ..multi_strategy_backtester import MultiStrategyBacktester

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
                f"   Strategy vs B&H: "
                f"{combined.total_return_pct - combined.buy_hold_return_pct:+.1f}%"
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
                f"❌ Missing dependency: {escape_markdown(str(e))}\n\n"
                "Install with: `pip install yfinance`",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Backtest failed: {e}")
            await update.message.reply_text(f"❌ Backtest failed: {escape_markdown(str(e))}")

    # =========================================================================
    # Historical Simulation Commands
    # =========================================================================

    async def _cmd_simulate(
        self: "TelegramBot", update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Handle /simulate command - run historical AI evolution simulation."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        # Check for email flag
        args = list(context.args) if context.args else []
        send_email = False
        if "email" in [a.lower() for a in args]:
            send_email = True
            args = [a for a in args if a.lower() != "email"]

        # Parse simulation period
        start_date, end_date, description = self._parse_simulation_period(args)

        if start_date is None:
            await update.message.reply_text(
                f"❌ {description}\n\n"
                "*Usage:*\n"
                "`/simulate 2024` - Full year\n"
                "`/simulate 6 months` - Last 6 months\n"
                "`/simulate 2024 email` - Send report via email\n"
                "`/simulate Jan 2024 to Jun 2024` - Month range\n"
                "`/simulate 2024-01-01 to 2024-06-30` - Date range\n",
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

        # Check email configuration if requested
        if send_email:
            from ..email_reports import is_email_configured

            if not is_email_configured():
                await update.message.reply_text(
                    "❌ *Email not configured*\n\n"
                    "Set these environment variables:\n"
                    "• `RESEND_API_KEY` - API key from resend.com\n"
                    "• `REPORT_EMAIL` - Recipient email",
                    parse_mode="Markdown",
                )
                return

        # Send initial status
        email_note = " (email report)" if send_email else ""
        status_msg = await update.message.reply_text(
            f"🔬 *Starting Historical Simulation*{email_note}\n\n"
            f"Period: {description}\n"
            f"Reviews: ~{estimated_reviews}\n"
            f"Est. Cost: ~${estimated_cost:.2f}\n\n"
            f"⏳ Running simulation...\n"
            f"This may take a few minutes.",
            parse_mode="Markdown",
        )

        try:
            from ..historical_simulator import HistoricalSimulator

            simulator = HistoricalSimulator()
            result = await simulator.run_simulation(
                start_date=start_date,
                end_date=end_date,
                review_interval_days=14,
                lookback_days=60,
            )

            if send_email:
                # Send via email
                from ..email_reports import send_simulation_report

                success = send_simulation_report(result)
                if success:
                    diff = result.evolved_performance - result.static_performance
                    diff_str = f"+{diff:.1f}%" if diff > 0 else f"{diff:.1f}%"
                    await status_msg.edit_text(
                        f"✅ *Simulation Complete*\n\n"
                        f"📧 Report sent to your email!\n\n"
                        f"*Quick Summary:*\n"
                        f"• Reviews: {len(result.reviews)}\n"
                        f"• Static: {result.static_performance:+.1f}%\n"
                        f"• Evolved: {result.evolved_performance:+.1f}%\n"
                        f"• Difference: {diff_str}\n"
                        f"• Changes: {result.param_changes_count()}",
                        parse_mode="Markdown",
                    )
                else:
                    await status_msg.edit_text(
                        "❌ *Simulation completed but email failed*\n\n"
                        "Check SMTP configuration and logs.",
                        parse_mode="Markdown",
                    )
            else:
                # Send via Telegram - escape AI-generated content
                report = escape_markdown(result.format_report())

                # Split if too long for Telegram
                if len(report) > 4000:
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
                f"❌ *Simulation Failed*\n\n"
                f"Error: {escape_markdown(str(e))}\n\nCheck logs for details.",
                parse_mode="Markdown",
            )
