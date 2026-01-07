"""
Analysis command handlers for Telegram bot.

Commands in this module:
- Pattern discovery: /analyze, /patterns, /analyses, /promote, /retire
- Risk management: /hedge, /review
"""

import logging
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .utils import escape_markdown

if TYPE_CHECKING:
    from .bot import TelegramBot

logger = logging.getLogger(__name__)


class AnalysisCommandsMixin:
    """
    Mixin class providing analysis-related Telegram commands.

    This class requires the following attributes from the base class:
    - _is_authorized(update) -> bool
    - _send_unauthorized_response(update) -> None
    """

    # =========================================================================
    # Pattern Discovery Commands
    # =========================================================================

    async def _cmd_analyze(self: "TelegramBot", update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            from ..pattern_discovery import (
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
                    f"• *{escape_markdown(p.display_name)}*\n"
                    f"  {escape_markdown(p.signal.value.upper())} "
                    f"{escape_markdown(p.instrument)} @ "
                    f"{escape_markdown(p.entry_time)}-{escape_markdown(p.exit_time)}\n"
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
            await update.message.reply_text(f"❌ Analysis failed: {escape_markdown(str(e))}")

    async def _cmd_patterns(
        self: "TelegramBot", update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Handle /patterns command - view discovered patterns."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        try:
            from ..pattern_discovery import get_pattern_registry

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
                        f"  • {escape_markdown(p.display_name)}\n"
                        f"    {escape_markdown(p.signal.value.upper())} "
                        f"{escape_markdown(p.instrument)} @ {escape_markdown(p.entry_time)}"
                    )

            if paper:
                lines.append("\n🟡 *PAPER* (validation):")
                for p in paper:
                    lines.append(
                        f"  • {escape_markdown(p.display_name)}\n"
                        f"    {p.validation_trades} trades, ${p.validation_pnl:.2f} P&L"
                    )

            if candidates:
                lines.append("\n⚪ *CANDIDATE* (pending validation):")
                for p in candidates:
                    lines.append(
                        f"  • {escape_markdown(p.display_name)}\n"
                        f"    {p.confidence:.0%} conf, {p.expected_edge:.2f}% edge"
                    )

            lines.append(f"\nTotal: {len(registry.patterns)} pattern(s)")

            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

        except Exception as e:
            await update.message.reply_text(f"❌ Error: {escape_markdown(str(e))}")

    async def _cmd_analyses(
        self: "TelegramBot", update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Handle /analyses command - view past Claude pattern analyses."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        import json

        from ..database import get_database

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

                model = escape_markdown(details.get("model", "unknown"))
                lookback = details.get("lookback_days", "?")
                response = details.get("response", "")

                # Truncate response for display
                if len(response) > 500:
                    response = response[:500] + "..."
                # Escape the response but preserve code block formatting
                response_escaped = escape_markdown(response)

                lines.append(f"\n*{i}. {date_str}*")
                lines.append(f"Model: `{model}` | Lookback: {lookback} days")
                lines.append(f"```\n{response_escaped}\n```")

            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

        except Exception as e:
            await update.message.reply_text(f"❌ Error: {escape_markdown(str(e))}")

    async def _cmd_promote(self: "TelegramBot", update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /promote command - promote a pattern to paper or live status."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        from ..pattern_discovery import PatternStatus, get_pattern_registry

        args = context.args
        if not args or len(args) < 2:
            # Show usage and list available patterns
            registry = get_pattern_registry()
            candidates = registry.get_candidate_patterns()
            paper = registry.get_paper_patterns()

            pattern_list = ""
            if candidates:
                pattern_list += "\n*Candidates:*\n"
                pattern_list += "\n".join(f"  • `{escape_markdown(p.name)}`" for p in candidates)
            if paper:
                pattern_list += "\n*Paper:*\n"
                pattern_list += "\n".join(f"  • `{escape_markdown(p.name)}`" for p in paper)

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
        pattern_name_safe = escape_markdown(pattern_name)
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
                f"❌ Pattern `{pattern_name_safe}` not found.\n"
                "Use /patterns to see available patterns.",
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
                f"Pattern: `{pattern_name_safe}`\n"
                f"From: {old_status.value} → To: {new_status.value}\n\n"
                f"Proceeding with promotion...",
                parse_mode="Markdown",
            )

        success = registry.promote_pattern(pattern_name, new_status)

        if success:
            emoji = "🟡" if new_status == PatternStatus.PAPER else "🟢"
            await update.message.reply_text(
                f"{emoji} *Pattern Promoted*\n\n"
                f"Pattern: `{pattern_name_safe}`\n"
                f"Status: {old_status.value} → *{new_status.value}*\n\n"
                f"Use /patterns to view all patterns.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(f"❌ Failed to promote pattern `{pattern_name_safe}`.")

    async def _cmd_retire(self: "TelegramBot", update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /retire command - retire a pattern that's no longer working."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        from ..pattern_discovery import get_pattern_registry

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
                pattern_list += "\n".join(f"  • `{escape_markdown(p.name)}`" for p in live)
            if paper:
                pattern_list += "\n*Paper:*\n"
                pattern_list += "\n".join(f"  • `{escape_markdown(p.name)}`" for p in paper)
            if candidates:
                pattern_list += "\n*Candidates:*\n"
                pattern_list += "\n".join(f"  • `{escape_markdown(p.name)}`" for p in candidates)

            if not pattern_list:
                pattern_list = "\nNo patterns available to retire."

            await update.message.reply_text(
                f"🔴 *Retire Pattern*\n\n"
                f"Usage: `/retire <pattern_name> [reason]`\n{pattern_list}",
                parse_mode="Markdown",
            )
            return

        pattern_name = args[0]
        pattern_name_safe = escape_markdown(pattern_name)
        reason = " ".join(args[1:]) if len(args) > 1 else "Manually retired"
        reason_safe = escape_markdown(reason)

        registry = get_pattern_registry()
        pattern = registry.get_pattern(pattern_name)

        if not pattern:
            await update.message.reply_text(
                f"❌ Pattern `{pattern_name_safe}` not found.\n"
                "Use /patterns to see available patterns.",
                parse_mode="Markdown",
            )
            return

        old_status = pattern.status
        success = registry.retire_pattern(pattern_name, reason)

        if success:
            await update.message.reply_text(
                f"🔴 *Pattern Retired*\n\n"
                f"Pattern: `{pattern_name_safe}`\n"
                f"Previous status: {old_status.value}\n"
                f"Reason: {reason_safe}\n\n"
                f"The pattern will no longer trade.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(f"❌ Failed to retire pattern `{pattern_name_safe}`.")

    # =========================================================================
    # Risk Management Commands
    # =========================================================================

    async def _cmd_hedge(self: "TelegramBot", update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /hedge command - view and control trailing hedge settings."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        from ..trailing_hedge import get_hedge_manager

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
                        f"  {i}. +{tier.gain_threshold_pct}% gain → "
                        f"+{tier.hedge_size_pct}% hedge"
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
                    f"  • {escape_markdown(pos['instrument'])}: {pos['shares']} shares",
                    f"  • Entry: ${pos['entry_price']:.2f}",
                    f"  • Value: ${pos['original_value']:.2f}",
                    "",
                    "*Hedge Status:*",
                    f"  • Instrument: {escape_markdown(hedge['instrument'])}",
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
                "✅ *Trailing Hedge ENABLED*\n\n" "Hedges will be added as positions gain value.",
                parse_mode="Markdown",
            )
        elif subcommand == "off":
            manager.config.enabled = False
            await update.message.reply_text(
                "❌ *Trailing Hedge DISABLED*\n\n" "No automatic hedges will be placed.",
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

    async def _cmd_review(self: "TelegramBot", update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            from ..strategy_review import (
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

            # Escape AI-generated content to prevent Markdown parsing errors
            report_escaped = escape_markdown(result.full_report)
            message = header + report_escaped

            # Truncate if too long for Telegram (max 4096 chars)
            if len(message) > 4000:
                message = message[:3950] + "\n\n... (truncated)"

            await update.message.reply_text(message, parse_mode="Markdown")

            # If there are recommendations, show approval buttons
            if result.recommendations:
                set_pending_recommendations(result.recommendations)

                for i, rec in enumerate(result.recommendations):
                    # Build recommendation message with approval buttons
                    # Escape AI/dynamic content to prevent Markdown parsing errors
                    display_name = escape_markdown(rec.to_display_name())
                    current_val = escape_markdown(str(rec.current_value))
                    recommended_val = escape_markdown(str(rec.recommended_value))
                    reason = escape_markdown(rec.reason)

                    rec_msg = (
                        f"🔧 *Parameter Change Recommendation {i + 1}*\n\n"
                        f"*{display_name}*\n"
                        f"Current: `{current_val}`\n"
                        f"Recommended: `{recommended_val}`\n"
                        f"Confidence: {rec.confidence.upper()}\n\n"
                        f"_{reason}_"
                    )

                    # Show backtest return if available (proves it was actually tested)
                    if rec.backtest_return is not None:
                        rec_msg += f"\n\n📊 Backtest Return: `{rec.backtest_return:+.2f}%`"

                    if rec.expected_improvement:
                        rec_msg += f"\n📈 Expected: {escape_markdown(rec.expected_improvement)}"

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
