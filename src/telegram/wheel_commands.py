"""
Wheel strategy command handlers for Telegram bot.

Commands in this module:
- /wheel  - Show current wheel cycle status, positions, Greeks, DTE, P&L (TR-02)
- /wheelmode - Toggle wheel mode on/off (TR-03)
"""

import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from ..database import get_database
from ..utils import get_et_now

if TYPE_CHECKING:
    from .bot import TelegramBot

logger = logging.getLogger(__name__)


class WheelCommandsMixin:
    """Mixin for /wheel and /wheelmode Telegram commands (TR-02, TR-03)."""

    async def _cmd_wheel(self: "TelegramBot", update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show current wheel cycle status, positions, and P&L (TR-02)."""
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        db = get_database()
        cycle = db.get_active_cycle()

        if cycle is None:
            await update.message.reply_text("No active wheel cycle.")
            return

        positions = db.get_cycle_positions(cycle["id"])
        from ..wheel_notifications import compute_wheel_cycle_status

        status = compute_wheel_cycle_status(cycle, positions, get_et_now().date())

        lines = [f"*WHEEL CYCLE -- {status['state']}*\n"]
        lines.append(f"Cost Basis: ${status['cost_basis']:.2f}/share")
        lines.append(f"Total Premium: ${status['total_premium']:.2f}")

        if status["open_positions"]:
            lines.append("\n*Open Positions:*")
            for p in status["open_positions"]:
                pnl_str = ""
                premium_received = p.get("premium_received") or 0.0
                current_value = p.get("current_value")
                if premium_received and current_value is not None:
                    pnl = (premium_received - current_value) * 100
                    pnl_str = f" | P&L: ${pnl:.2f}"

                delta = p.get("delta", "N/A")
                lines.append(
                    f"  {p['option_type']} ${p['strike']:.0f} exp {p['expiry_date']} "
                    f"({p['dte']} DTE) | delta={delta}{pnl_str} | "
                    f"premium=${premium_received:.2f}"
                )
            lines.append("\n_Greeks shown are entry values._")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _cmd_wheelmode(
        self: "TelegramBot", update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Toggle wheel mode on/off (TR-03).

        Usage:
          /wheelmode on   - Enable wheel mode (disables intraday strategies)
          /wheelmode off  - Disable wheel mode (re-enables intraday strategies)
          /wheelmode      - Show current wheel mode status

        Rate limited to one mode-change per 5 seconds to prevent rapid
        on/off spam writing to the DB (LO-04).
        """
        if not self._is_authorized(update):
            await self._send_unauthorized_response(update)
            return

        db = get_database()
        args = context.args

        # Rate limit (LO-04): reject mode changes within 5s of the previous
        # toggle. Status queries (no args) bypass the rate limit.
        if args:
            last_change = getattr(self, "_wheelmode_last_change_at", None)
            now_ts = get_et_now().timestamp()
            if last_change is not None and (now_ts - last_change) < 5.0:
                await update.message.reply_text(
                    "⏳ Wheel mode was just toggled. Please wait a few seconds.",
                )
                return

        # Guard: no args — show current status (T-06-05: no IndexError on empty args)
        if not args:
            state = db.get_bot_state()
            enabled = state.get("wheel_mode_enabled", 1)
            status_str = "ON" if enabled else "OFF"
            await update.message.reply_text(
                f"Wheel Mode is currently *{status_str}*.\n\n"
                "Use `/wheelmode on` or `/wheelmode off` to change.",
                parse_mode="Markdown",
            )
            return

        # Validate arg (T-06-04: only accept "on" or "off")
        arg = args[0].lower()
        if arg not in ("on", "off"):
            await update.message.reply_text(
                "Usage: /wheelmode on|off\n\n"
                "  on  - Enable wheel mode (intraday jobs gated)\n"
                "  off - Disable wheel mode (intraday jobs run normally)"
            )
            return

        if arg == "on":
            db.update_bot_state(wheel_mode_enabled=1)
            await update.message.reply_text(
                "Wheel Mode *ENABLED*.\n\nIntraday strategies are now gated.",
                parse_mode="Markdown",
            )
            logger.info("Wheel mode ENABLED via /wheelmode Telegram command")
        else:
            db.update_bot_state(wheel_mode_enabled=0)
            await update.message.reply_text(
                "Wheel Mode *DISABLED*.\n\nIntraday strategies will run normally.",
                parse_mode="Markdown",
            )
            logger.info("Wheel mode DISABLED via /wheelmode Telegram command")

        # Update the rate-limit timestamp on successful toggle
        self._wheelmode_last_change_at = get_et_now().timestamp()
