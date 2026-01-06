"""
Smart Scheduler - Automated execution of trading strategy.

Runs the smart strategy at market open and close.
"""

import logging
from enum import Enum
from typing import Optional

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .database import get_database
from .smart_strategy import Signal
from .telegram_bot import TelegramBot, escape_markdown
from .trading_bot import TradeResult, TradingBot
from .utils import ET, get_et_now, is_trading_day, run_async

logger = logging.getLogger(__name__)


class BotStatus(Enum):
    """Bot status."""

    STOPPED = "stopped"
    RUNNING = "running"
    ERROR = "error"


class SmartScheduler:
    """
    Scheduler for automated strategy execution.

    Schedule:
    - 9:35 AM ET: Execute morning signal (buy BITU or SBIT if signal exists)
    - 3:55 PM ET: Close any open positions

    The strategy is intraday - never hold overnight.
    """

    def __init__(self, bot: TradingBot, telegram_bot: Optional[TelegramBot] = None):
        self.bot = bot
        self.telegram_bot = telegram_bot  # Shared instance, set after creation if not passed
        self.db = get_database()
        self.scheduler = BackgroundScheduler(timezone=ET)
        self.status = BotStatus.STOPPED
        self._last_result: Optional[TradeResult] = None
        self._error_count = 0

        # Add event listeners
        self.scheduler.add_listener(self._on_job_event, EVENT_JOB_ERROR | EVENT_JOB_EXECUTED)

    def _send_notification(self, message: str, parse_mode: str = "Markdown"):
        """Send a notification via the shared Telegram bot instance."""
        if not self.telegram_bot:
            logger.warning("Telegram bot not configured, skipping notification")
            return

        async def _send():
            try:
                await self.telegram_bot.send_message(message, parse_mode=parse_mode)
            except Exception as e:
                logger.error(f"Failed to send Telegram notification: {e}")

        run_async(_send())

    def _on_job_event(self, event):
        """Handle job events."""
        if event.exception:
            self._error_count += 1
            logger.error(f"Job failed: {event.exception}")
            self.db.log_event("SCHEDULER_ERROR", str(event.exception))

    def _log_signal_check(self, job_type: str, signal, now):
        """Log comprehensive signal check data for analytics."""
        details = {
            "job_type": job_type,
            "timestamp": now.isoformat(),
            "day_of_week": now.strftime("%A"),
            "signal": signal.signal.value,
            "etf": signal.etf,
            "reason": signal.reason,
            "prev_day_return": signal.prev_day_return,
        }

        # Add BTC overnight data if available
        if signal.btc_overnight:
            details["btc_overnight"] = {
                "change_pct": signal.btc_overnight.overnight_change_pct,
                "is_positive": signal.btc_overnight.is_up,
                "message": signal.btc_overnight.message,
            }

        # Add crash day status if available
        if signal.crash_day_status:
            details["crash_day"] = {
                "current_drop_pct": signal.crash_day_status.current_drop_pct,
                "is_triggered": signal.crash_day_status.is_triggered,
                "threshold": self.bot.config.strategy.crash_day_threshold,
            }

        # Add pump day status if available
        if signal.pump_day_status:
            details["pump_day"] = {
                "current_gain_pct": signal.pump_day_status.current_gain_pct,
                "is_triggered": signal.pump_day_status.is_triggered,
                "threshold": self.bot.config.strategy.pump_day_threshold,
            }

        # Add weekend gap if available
        if signal.weekend_gap:
            details["weekend_gap"] = {
                "gap_pct": signal.weekend_gap.gap_pct,
                "alert_level": signal.weekend_gap.alert_level.value
                if signal.weekend_gap.alert_level
                else None,
            }

        self.db.log_event("SIGNAL_CHECK", f"{job_type}: {signal.signal.value}", details)
        logger.info(f"Signal check logged: {job_type} -> {signal.signal.value}")

    def setup_jobs(self):
        """Set up scheduled jobs."""
        self.scheduler.remove_all_jobs()

        # Morning execution - 9:35 AM ET (5 min after open to let prices settle)
        self.scheduler.add_job(
            self._job_morning_signal,
            CronTrigger(day_of_week="mon-fri", hour=9, minute=35, timezone=ET),
            id="morning_signal",
            name="Execute Morning Signal",
            misfire_grace_time=300,
        )

        # Crash day monitoring - check every 15 min from 9:45 AM to 3:30 PM ET
        if self.bot.config.strategy.crash_day_enabled:
            self.scheduler.add_job(
                self._job_crash_day_check,
                CronTrigger(
                    day_of_week="mon-fri",
                    hour="9-15",
                    minute="0,15,30,45",
                    timezone=ET,
                ),
                id="crash_day_check",
                name="Crash Day Monitor",
                misfire_grace_time=120,
            )

        # Pump day monitoring - check every 15 min from 9:45 AM to 3:30 PM ET
        if self.bot.config.strategy.pump_day_enabled:
            self.scheduler.add_job(
                self._job_pump_day_check,
                CronTrigger(
                    day_of_week="mon-fri",
                    hour="9-15",
                    minute="0,15,30,45",
                    timezone=ET,
                ),
                id="pump_day_check",
                name="Pump Day Monitor",
                misfire_grace_time=120,
            )

        # 10 AM Dump exit - 10:30 AM ET (close SBIT from morning 10 AM dump trade)
        if self.bot.config.strategy.ten_am_dump_enabled:
            self.scheduler.add_job(
                self._job_ten_am_dump_exit,
                CronTrigger(day_of_week="mon-fri", hour=10, minute=30, timezone=ET),
                id="ten_am_dump_exit",
                name="10 AM Dump Exit",
                misfire_grace_time=120,
            )

        # Close positions - 3:55 PM ET (before market close)
        self.scheduler.add_job(
            self._job_close_positions,
            CronTrigger(day_of_week="mon-fri", hour=15, minute=55, timezone=ET),
            id="close_positions",
            name="Close Positions",
            misfire_grace_time=300,
        )

        # Trailing hedge check - every 5 minutes from 9:40 AM to 3:50 PM ET
        self.scheduler.add_job(
            self._job_hedge_check,
            CronTrigger(
                day_of_week="mon-fri",
                hour="9-15",
                minute="*/5",
                timezone=ET,
            ),
            id="hedge_check",
            name="Trailing Hedge Check",
            misfire_grace_time=120,
        )

        # Reversal check - every 5 minutes during market hours
        # Flips BITU to SBIT if position drops -2% (backed by backtesting)
        self.scheduler.add_job(
            self._job_reversal_check,
            CronTrigger(
                day_of_week="mon-fri",
                hour="9-15",
                minute="*/5",
                timezone=ET,
            ),
            id="reversal_check",
            name="Position Reversal Check",
            misfire_grace_time=120,
        )

        # Token renewal for E*TRADE (if live mode) - 8:00 AM ET
        if not self.bot.is_paper_mode and self.bot.client:
            self.scheduler.add_job(
                self._job_renew_token,
                CronTrigger(day_of_week="mon-fri", hour=8, minute=0, timezone=ET),
                id="renew_token",
                name="Renew E*TRADE Token",
                misfire_grace_time=3600,
            )

        # Daily summary - 4:00 PM ET (after positions closed)
        self.scheduler.add_job(
            self._job_daily_summary,
            CronTrigger(day_of_week="mon-fri", hour=16, minute=0, timezone=ET),
            id="daily_summary",
            name="Daily Summary",
            misfire_grace_time=600,
        )

        # Pre-market reminder - 9:15 AM ET
        self.scheduler.add_job(
            self._job_premarket_reminder,
            CronTrigger(day_of_week="mon-fri", hour=9, minute=15, timezone=ET),
            id="premarket_reminder",
            name="Pre-Market Reminder",
            misfire_grace_time=300,
        )

        # Hourly position updates - every hour during market hours (10 AM - 3 PM)
        self.scheduler.add_job(
            self._job_position_update,
            CronTrigger(day_of_week="mon-fri", hour="10-15", minute=0, timezone=ET),
            id="position_update",
            name="Hourly Position Update",
            misfire_grace_time=300,
        )

        # Health check - 8:30 AM ET (check cash, data feeds, etc.)
        self.scheduler.add_job(
            self._job_health_check,
            CronTrigger(day_of_week="mon-fri", hour=8, minute=30, timezone=ET),
            id="health_check",
            name="Morning Health Check",
            misfire_grace_time=600,
        )

        # Daily auth reminder - 8:00 AM ET (prompt to authenticate before trading)
        self.scheduler.add_job(
            self._job_auth_reminder,
            CronTrigger(day_of_week="mon-fri", hour=8, minute=0, timezone=ET),
            id="auth_reminder",
            name="Daily Auth Reminder",
            misfire_grace_time=600,
        )

        # Monthly pattern analysis - 1st of each month at 6:00 AM ET
        # Runs LLM analysis to discover new trading patterns
        self.scheduler.add_job(
            self._job_pattern_analysis,
            CronTrigger(day=1, hour=6, minute=0, timezone=ET),
            id="pattern_analysis",
            name="Monthly Pattern Analysis",
            misfire_grace_time=3600,  # 1 hour grace period
        )

        # Bi-weekly strategy review - 1st and 15th of each month at 7:00 AM ET
        # Runs every 2 weeks to catch market regime shifts faster
        # Backtests current strategy, detects regime, sends Claude for analysis
        self.scheduler.add_job(
            self._job_strategy_review,
            CronTrigger(day="1,15", hour=7, minute=0, timezone=ET),
            id="strategy_review",
            name="Bi-weekly Strategy Review",
            misfire_grace_time=3600,
        )

        logger.info("Scheduler jobs configured")

    def _job_auth_reminder(self):
        """Send daily authentication reminder at 8:00 AM ET."""
        now = get_et_now()

        if not is_trading_day(now.date()):
            return

        # Check if E*TRADE is authenticated
        is_authenticated = False
        auth_status = "Unknown"

        if self.bot.is_paper_mode:
            auth_status = "Paper Mode (no auth needed)"
            is_authenticated = True
        elif self.bot.client:
            try:
                is_authenticated = self.bot.client.is_authenticated()
                auth_status = "Connected" if is_authenticated else "NOT CONNECTED"
            except Exception as e:
                auth_status = f"Error: {e}"
                is_authenticated = False
        else:
            auth_status = "No E*TRADE client configured"

        # Always send reminder on trading days
        if is_authenticated:
            self._send_notification(
                f"☀️ *Good Morning!* Trading day started.\n\n"
                f"🔐 E*TRADE: {auth_status}\n"
                f"📅 {now.strftime('%A, %B %d')}\n\n"
                f"Signal check at 9:35 AM ET"
            )
        else:
            self._send_notification(
                "🚨 *ACTION REQUIRED*\n\n"
                "E*TRADE authentication expired!\n\n"
                "➡️ Run /auth now to login\n"
                "⏰ Must complete before 9:35 AM ET\n\n"
                "Without auth, trades will be blocked."
            )

        self.db.log_event(
            "AUTH_REMINDER",
            f"Daily auth check: {auth_status}",
            {"is_authenticated": is_authenticated, "mode": self.bot.config.mode.value},
        )
        logger.info(f"Auth reminder sent: {auth_status}")

    def _job_morning_signal(self):
        """Execute morning trading signal."""

        now = get_et_now()

        if not is_trading_day(now.date()):
            logger.info("Not a trading day, skipping")
            return

        logger.info("Executing morning signal check")

        try:
            # Get and log the signal with full context BEFORE execution
            signal = self.bot.get_today_signal()
            self._log_signal_check("MORNING_SIGNAL", signal, now)

            # Don't check crash day in morning - that's handled separately
            result = self.bot.execute_signal(signal)
            self._last_result = result

            if result.success:
                if result.signal != Signal.CASH:
                    logger.info(f"Trade executed: {result.action} {result.shares} {result.etf}")
                    # Trade notification is handled by execute_signal via approval flow

                    # Mark 10 AM dump position if that's what we entered
                    if result.signal == Signal.TEN_AM_DUMP:
                        self.bot.strategy.mark_ten_am_dump_entered()
                        logger.info("Marked 10 AM dump position as open (will exit at 10:30)")
                else:
                    # No signal today - send notification
                    logger.info("No trade signal today")
                    self._send_no_signal_notification(now)
            else:
                logger.error(f"Trade failed: {result.error}")
                self._send_error_notification(f"Trade failed: {result.error}")

        except Exception as e:
            logger.error(f"Morning signal job failed: {e}")
            self._error_count += 1
            self._send_error_notification(f"Morning signal check failed: {e}")

    def _send_no_signal_notification(self, now):
        """Send notification when there's no trade signal."""
        day_name = now.strftime("%A")

        # Build reason for no signal
        reason_lines = []

        if day_name == "Thursday":
            reason_lines.append("• Thursday detected, but overnight filter blocked short")
        else:
            reason_lines.append("• No mean reversion trigger (IBIT didn't drop enough)")

        reason = "\n".join(reason_lines) if reason_lines else "• No qualifying conditions met"

        self._send_notification(
            f"📭 No Trade Signal Today\n\n"
            f"Time: {now.strftime('%I:%M %p ET')}\n"
            f"Day: {day_name}\n\n"
            f"Reason:\n{reason}\n\n"
            "Staying in cash. Monitoring for intraday opportunities...",
            parse_mode=None,
        )
        logger.info("No-signal notification sent")

    def _send_error_notification(self, error_msg: str):
        """Send error notification via Telegram."""
        now = get_et_now()

        # Don't use Markdown - error messages may contain special chars
        self._send_notification(
            f"🚨 Bot Error Alert\n\n"
            f"Time: {now.strftime('%I:%M %p ET')}\n\n"
            f"Error: {error_msg}\n\n"
            "Please check logs for details.",
            parse_mode=None,
        )
        logger.info("Error notification sent")

    def _job_crash_day_check(self):
        """Check for intraday crash signal and execute if triggered.

        Thread-safe: Uses position lock to prevent TOCTOU race conditions.
        """
        now = get_et_now()

        if not is_trading_day(now.date()):
            return

        try:
            # Get fresh signal with crash day check
            signal = self.bot.strategy.get_today_signal(check_crash_day=True)

            # Log every crash day check for analytics
            self._log_signal_check("CRASH_DAY_CHECK", signal, now)

            if signal.signal == Signal.CRASH_DAY:
                logger.info(
                    f"CRASH DAY TRIGGERED: IBIT down {signal.crash_day_status.current_drop_pct:.1f}%"
                )

                # Acquire lock for atomic position check + modification
                # Prevents race with other jobs (reversal, hedge, pump_day)
                with self.bot._position_lock:
                    # Check if we have an existing position that conflicts
                    has_bitu = False
                    has_sbit = False

                    if self.bot.is_paper_mode:
                        has_bitu = "BITU" in self.bot._paper_positions
                        has_sbit = "SBIT" in self.bot._paper_positions
                    elif self.bot.client:
                        # For live trading, check actual positions
                        try:
                            positions = self.bot.client.get_account_positions(
                                self.bot.config.account_id_key
                            )
                            for pos in positions:
                                symbol = pos.get("Product", {}).get("symbol", "")
                                qty = pos.get("quantity", 0)
                                if symbol == "BITU" and qty > 0:
                                    has_bitu = True
                                elif symbol == "SBIT" and qty > 0:
                                    has_sbit = True
                        except Exception as e:
                            logger.warning(f"Could not check positions: {e}")

                    # If holding BITU (long), we MUST close it - holding long during crash is disaster
                    if has_bitu:
                        logger.warning("CRASH during BITU position! Closing BITU first...")
                        close_result = self.bot.close_position("BITU")
                        if close_result.success:
                            logger.info(f"Emergency close: Sold BITU @ ${close_result.price:.2f}")
                        else:
                            logger.error(f"Failed to close BITU: {close_result.error}")
                            return  # Don't proceed if we can't close

                    # If already holding SBIT, we're already positioned correctly
                    elif has_sbit:
                        logger.info("Already holding SBIT - correctly positioned for crash")
                        return

                    # Now execute the crash day trade (still under lock)
                    # skip_approval=True for time-sensitive emergency trades
                    result = self.bot.execute_signal(signal, skip_approval=True)
                    self._last_result = result

                    if result.success:
                        # Mark that we've traded the crash day
                        self.bot.strategy.mark_crash_day_traded()
                        logger.info(
                            f"Crash day trade AUTO-EXECUTED: {result.shares} SBIT @ ${result.price:.2f}"
                        )
                    else:
                        logger.error(f"Crash day trade failed: {result.error}")
            else:
                if signal.crash_day_status:
                    drop = signal.crash_day_status.current_drop_pct
                    threshold = self.bot.config.strategy.crash_day_threshold
                    logger.debug(f"Crash day check: IBIT {drop:+.1f}% (threshold: {threshold}%)")

        except Exception as e:
            logger.error(f"Crash day check failed: {e}")
            self._error_count += 1
            self._send_error_notification(f"Crash day check failed: {e}")

    def _job_pump_day_check(self):
        """Check for intraday pump signal and execute if triggered.

        Thread-safe: Uses position lock to prevent TOCTOU race conditions.
        """
        now = get_et_now()

        if not is_trading_day(now.date()):
            return

        try:
            # Get fresh signal with pump day check
            signal = self.bot.strategy.get_today_signal(check_crash_day=False, check_pump_day=True)

            # Log every pump day check for analytics
            self._log_signal_check("PUMP_DAY_CHECK", signal, now)

            if signal.signal == Signal.PUMP_DAY:
                logger.info(
                    f"PUMP DAY TRIGGERED: IBIT up {signal.pump_day_status.current_gain_pct:.1f}%"
                )

                # Acquire lock for atomic position check + modification
                # Prevents race with other jobs (reversal, hedge, crash_day)
                with self.bot._position_lock:
                    # Check if we have an existing position that conflicts
                    has_bitu = False
                    has_sbit = False

                    if self.bot.is_paper_mode:
                        has_bitu = "BITU" in self.bot._paper_positions
                        has_sbit = "SBIT" in self.bot._paper_positions
                    elif self.bot.client:
                        # For live trading, check actual positions
                        try:
                            positions = self.bot.client.get_account_positions(
                                self.bot.config.account_id_key
                            )
                            for pos in positions:
                                symbol = pos.get("Product", {}).get("symbol", "")
                                qty = pos.get("quantity", 0)
                                if symbol == "BITU" and qty > 0:
                                    has_bitu = True
                                elif symbol == "SBIT" and qty > 0:
                                    has_sbit = True
                        except Exception as e:
                            logger.warning(f"Could not check positions: {e}")

                    # If holding SBIT (inverse), we MUST close it - holding inverse during pump is disaster
                    if has_sbit:
                        logger.warning("PUMP during SBIT position! Closing SBIT first...")
                        close_result = self.bot.close_position("SBIT")
                        if close_result.success:
                            logger.info(f"Emergency close: Sold SBIT @ ${close_result.price:.2f}")
                        else:
                            logger.error(f"Failed to close SBIT: {close_result.error}")
                            return  # Don't proceed if we can't close

                    # If already holding BITU, we're already positioned correctly
                    elif has_bitu:
                        logger.info("Already holding BITU - correctly positioned for pump")
                        return

                    # Now execute the pump day trade (still under lock)
                    # skip_approval=True for time-sensitive emergency trades
                    result = self.bot.execute_signal(signal, skip_approval=True)
                    self._last_result = result

                    if result.success:
                        # Mark that we've traded the pump day
                        self.bot.strategy.mark_pump_day_traded()
                        logger.info(
                            f"Pump day trade AUTO-EXECUTED: {result.shares} BITU @ ${result.price:.2f}"
                        )
                    else:
                        logger.error(f"Pump day trade failed: {result.error}")
            else:
                if signal.pump_day_status:
                    gain = signal.pump_day_status.current_gain_pct
                    threshold = self.bot.config.strategy.pump_day_threshold
                    logger.debug(f"Pump day check: IBIT {gain:+.1f}% (threshold: +{threshold}%)")

        except Exception as e:
            logger.error(f"Pump day check failed: {e}")
            self._error_count += 1
            self._send_error_notification(f"Pump day check failed: {e}")

    def _job_ten_am_dump_exit(self):
        """Exit 10 AM dump position at 10:30 AM ET."""
        now = get_et_now()

        if not is_trading_day(now.date()):
            return

        # Check if we have a 10 AM dump position open
        if not self.bot.strategy._ten_am_dump_position_open:
            logger.debug("No 10 AM dump position to close")
            return

        logger.info("Executing 10 AM dump exit at 10:30 AM ET")

        try:
            # Close the SBIT position
            result = self.bot.close_position("SBIT")

            if result.success and result.shares > 0:
                # Mark the position as closed
                self.bot.strategy.mark_ten_am_dump_exited()

                # Calculate P/L
                pnl = result.pnl if hasattr(result, "pnl") else 0
                pnl_str = f"${pnl:+.2f}" if pnl else "TBD"

                # Send notification
                self._send_notification(
                    f"🔔 *10 AM Dump Exit*\n\n"
                    f"Sold: {result.shares} SBIT @ ${result.price:.2f}\n"
                    f"P/L: {pnl_str}\n"
                    f"Time: {now.strftime('%I:%M %p ET')}\n\n"
                    "Strategy: Captured 10 AM weakness window"
                )

                logger.info(f"10 AM dump exit: Sold {result.shares} SBIT @ ${result.price:.2f}")

                self.db.log_event(
                    "TEN_AM_DUMP_EXIT",
                    "Exited 10 AM dump position",
                    {
                        "shares": result.shares,
                        "price": result.price,
                        "pnl": pnl,
                        "timestamp": now.isoformat(),
                    },
                )
            elif result.shares == 0:
                # No position to close
                logger.info("No SBIT position to close for 10 AM dump")
                self.bot.strategy.mark_ten_am_dump_exited()
            else:
                logger.error(f"Failed to close 10 AM dump position: {result.error}")

        except Exception as e:
            logger.error(f"10 AM dump exit failed: {e}")
            self._error_count += 1
            self._send_error_notification(f"10 AM dump exit failed: {e}")

    def _close_position_with_retry(self, etf: str, max_retries: int = 3) -> tuple:
        """
        Close a position with retry logic and exponential backoff.

        Args:
            etf: The ETF symbol to close
            max_retries: Maximum number of retry attempts

        Returns:
            Tuple of (success: bool, result_or_error: TradeResult|str)
        """
        import time

        for attempt in range(max_retries):
            try:
                result = self.bot.close_position(etf)

                if result.success:
                    if result.shares > 0:
                        logger.info(
                            f"Closed {etf} position: {result.shares} shares (attempt {attempt + 1})"
                        )
                        return (True, result)
                    else:
                        # shares=0 means no position existed
                        return (True, None)

                elif "No position found" in (result.error or ""):
                    # No position to close - not a failure, don't retry
                    logger.info(f"No {etf} position to close")
                    return (True, None)

                else:
                    # Real failure - log and maybe retry
                    logger.warning(
                        f"Close {etf} attempt {attempt + 1}/{max_retries} failed: {result.error}"
                    )
                    if attempt < max_retries - 1:
                        wait_time = 2**attempt  # 1s, 2s, 4s
                        logger.info(f"Retrying in {wait_time}s...")
                        time.sleep(wait_time)
                    else:
                        return (False, result.error)

            except Exception as e:
                logger.error(f"EXCEPTION closing {etf} (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    wait_time = 2**attempt
                    logger.info(f"Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    return (False, f"Exception after {max_retries} attempts: {e}")

        return (False, f"Failed after {max_retries} attempts")

    def _job_close_positions(self):
        """Close any open positions before market close with retry logic."""
        now = get_et_now()

        if not is_trading_day(now.date()):
            return

        logger.info("Closing positions before market close")

        # First, log what positions E*TRADE sees
        portfolio = self.bot.get_portfolio_value()
        logger.info(f"EOD close - portfolio check: {portfolio}")
        if "error" in portfolio:
            logger.error(f"EOD close - E*TRADE error: {portfolio.get('error')}")

        close_failures = []
        close_successes = []

        # Determine which ETFs to try closing
        if self.bot.is_paper_mode:
            etfs_to_close = list(self.bot._paper_positions.keys())
        else:
            etfs_to_close = ["BITU", "SBIT"]

        # Close each position with retry logic
        for etf in etfs_to_close:
            success, result_or_error = self._close_position_with_retry(etf, max_retries=3)

            if success:
                if result_or_error is not None:  # Actually closed a position
                    close_successes.append((etf, result_or_error))
                # else: no position existed, which is fine
            else:
                close_failures.append((etf, result_or_error))
                self._error_count += 1

        # Send Telegram notification for results
        self._send_close_positions_notification(close_successes, close_failures)

    def _send_close_positions_notification(self, successes: list, failures: list):
        """Send Telegram notification for EOD close results."""
        try:
            from .utils import get_et_now

            mode = "[PAPER]" if self.bot.is_paper_mode else "[LIVE]"
            now = get_et_now()
            time_str = now.strftime("%I:%M %p ET")

            if failures:
                # CRITICAL ALERT for failures
                message = f"🚨 *EOD CLOSE FAILED* 🚨\n\n{mode} Failed to close positions:\n"
                for etf, error in failures:
                    message += f"• {etf}: {error}\n"
                message += (
                    "\n⚠️ *POSITIONS MAY STILL BE OPEN*\n"
                    "Check your brokerage account immediately!"
                )
            elif successes:
                # Success notification
                message = f"✅ *EOD Positions Closed*\n\n{mode}\n"
                for etf, result in successes:
                    message += f"• Sold {result.shares} {etf}"
                    if hasattr(result, "price") and result.price:
                        message += f" @ ${result.price:.2f}"
                    message += "\n"
            else:
                # No positions to close - still send confirmation
                message = (
                    f"✅ *EOD Check Complete*\n\n{mode} at {time_str}\nNo open positions to close."
                )

            self._send_notification(message)

        except Exception as e:
            logger.error(f"Failed to send close notification: {e}")

    def _job_hedge_check(self):
        """Check and execute trailing hedges if position has gained enough."""
        now = get_et_now()

        if not is_trading_day(now.date()):
            return

        # Only run during market hours (9:40 AM - 3:50 PM ET)
        if now.hour < 9 or (now.hour == 9 and now.minute < 40):
            return
        if now.hour > 15 or (now.hour == 15 and now.minute > 50):
            return

        # Check if we have a position to hedge
        if not self.bot.hedge_manager.position:
            return

        try:
            result = self.bot.check_and_execute_hedge()

            if result and result.success:
                logger.info(
                    f"Trailing hedge executed: {result.shares} {result.etf} @ ${result.price:.2f}",
                )

                # Send Telegram notification
                self._send_hedge_notification(result)

                self.db.log_event(
                    "HEDGE_EXECUTED",
                    f"Trailing hedge: {result.shares} {result.etf}",
                    {
                        "etf": result.etf,
                        "shares": result.shares,
                        "price": result.price,
                        "value": result.total_value,
                        "is_paper": result.is_paper,
                    },
                )

        except Exception as e:
            logger.error(f"Hedge check job failed: {e}")
            self._error_count += 1
            self._send_error_notification(f"Hedge check failed: {e}")

    def _send_hedge_notification(self, result):
        """Send Telegram notification for hedge execution."""
        try:
            mode = "[PAPER]" if result.is_paper else "[LIVE]"

            # Get hedge status
            status = self.bot.hedge_manager.get_status()
            total_hedge_pct = status.get("hedge", {}).get("total_pct", 0)

            message = (
                f"🛡️ *Trailing Hedge Executed*\n\n"
                f"{mode} Bought {result.shares} {result.etf}\n"
                f"Price: ${result.price:.2f}\n"
                f"Value: ${result.total_value:.2f}\n\n"
                f"Total hedge: {total_hedge_pct:.0f}% of position"
            )

            self._send_notification(message)

        except Exception as e:
            logger.warning(f"Failed to send hedge notification: {e}")

    def _job_reversal_check(self):
        """Check and execute position reversal if BITU is down enough."""
        now = get_et_now()

        if not is_trading_day(now.date()):
            return

        # Only run during market hours (9:40 AM - 3:50 PM ET)
        if now.hour < 9 or (now.hour == 9 and now.minute < 40):
            return
        if now.hour > 15 or (now.hour == 15 and now.minute > 50):
            return

        # Check if we have a BITU position that might need reversing
        positions = self.bot.get_open_positions()
        if "BITU" not in positions:
            return

        try:
            result = self.bot.check_and_execute_reversal()

            if result and result.success:
                logger.info(
                    f"Position reversal executed: {result.shares} {result.etf} @ ${result.price:.2f}",
                )

                # Send Telegram notification
                self._send_reversal_notification(result)

                self.db.log_event(
                    "REVERSAL_NOTIFICATION",
                    f"Position reversed to {result.etf}",
                    {
                        "etf": result.etf,
                        "shares": result.shares,
                        "price": result.price,
                        "value": result.total_value,
                        "is_paper": result.is_paper,
                    },
                )

        except Exception as e:
            logger.error(f"Reversal check job failed: {e}")
            self._error_count += 1
            self._send_error_notification(f"Reversal check failed: {e}")

    def _send_reversal_notification(self, result):
        """Send Telegram notification for position reversal."""
        try:
            mode = "[PAPER]" if result.is_paper else "[LIVE]"

            message = (
                f"🔄 *Position Reversed!*\n\n"
                f"{mode} BITU was down -2% → flipped to SBIT\n\n"
                f"New position:\n"
                f"• {result.shares} {result.etf}\n"
                f"• Price: ${result.price:.2f}\n"
                f"• Value: ${result.total_value:.2f}\n\n"
                f"_Riding the trend down to EOD close_"
            )

            self._send_notification(message)

        except Exception as e:
            logger.warning(f"Failed to send reversal notification: {e}")

    def _job_renew_token(self):
        """Renew E*TRADE token."""
        if self.bot.client and not self.bot.is_paper_mode:
            try:
                self.bot.client.renew_token()
                logger.info("E*TRADE token renewed")
            except Exception as e:
                logger.error(f"Token renewal failed: {e}")

    def _job_daily_summary(self):
        """Send daily summary via Telegram at 4:00 PM ET."""
        now = get_et_now()

        if not is_trading_day(now.date()):
            return

        logger.info("Sending daily summary")

        try:
            # Get today's trades from database
            today_str = now.strftime("%Y-%m-%d")
            events = self.db.get_events(since=today_str, level="TRADE")

            trades_today = len(events)
            total_pnl = 0.0
            wins = 0

            for event in events:
                details = event.get("details", {})
                if isinstance(details, dict):
                    pnl = details.get("pnl", 0)
                    if pnl:
                        total_pnl += pnl
                        if pnl > 0:
                            wins += 1

            win_rate = (wins / trades_today * 100) if trades_today > 0 else 0

            # Get ending cash
            portfolio = self.bot.get_portfolio_value()
            ending_cash = portfolio.get("cash", 0)

            # Send Telegram summary
            if self.telegram_bot:

                async def _send_summary():
                    try:
                        await self.telegram_bot.send_daily_summary(
                            trades_today=trades_today,
                            total_pnl=total_pnl,
                            win_rate=win_rate,
                            ending_cash=ending_cash,
                        )
                    except Exception as e:
                        logger.error(f"Failed to send daily summary: {e}")

                run_async(_send_summary())

            logger.info(f"Daily summary sent: {trades_today} trades, P/L: ${total_pnl:.2f}")

        except Exception as e:
            logger.error(f"Daily summary failed: {e}")
            self._error_count += 1

    def _job_premarket_reminder(self):
        """Send pre-market reminder at 9:15 AM ET."""
        now = get_et_now()

        if not is_trading_day(now.date()):
            # Send holiday/weekend notice instead
            day_name = now.strftime("%A")
            self._send_notification(
                f"📅 Market closed today ({day_name})\n\n"
                "No trading activity scheduled. Enjoy your day off!",
                parse_mode=None,
            )
            return

        # Get today's expected signal
        signal = self.bot.strategy.get_today_signal()
        signal_preview = ""
        if signal.signal.value != "cash":
            signal_preview = f"\n📊 Potential signal: {signal.signal.value.upper()}"

        # Check what day it is for strategy hints
        day_name = now.strftime("%A")
        strategy_hint = ""
        if day_name == "Thursday":
            strategy_hint = "\n📅 Short Thursday strategy active"

        self._send_notification(
            f"☀️ Market opens in 15 minutes!\n\n"
            f"Time: {now.strftime('%I:%M %p ET')}\n"
            f"Day: {day_name}"
            f"{strategy_hint}"
            f"{signal_preview}\n\n"
            "Signal check at 9:35 AM ET",
            parse_mode=None,
        )
        logger.info("Pre-market reminder sent")

    def _job_position_update(self):
        """Send hourly position update if holding a position."""
        now = get_et_now()

        if not is_trading_day(now.date()):
            return

        # Check if we have any positions
        portfolio = self.bot.get_portfolio_value()
        logger.info(f"Position update - portfolio result: {portfolio}")

        # Check for error response
        if "error" in portfolio:
            logger.error(f"Position update failed - E*TRADE error: {portfolio.get('error')}")
            self._send_notification(
                f"⚠️ Position Update Failed\n\nE*TRADE error: {portfolio.get('error')}",
                parse_mode=None,
            )
            return

        positions = portfolio.get("positions", [])

        if not positions:
            logger.info("Position update - no positions found, skipping")
            return  # No positions, skip update

        # Build position update message
        position_lines = []
        for pos in positions:
            symbol = pos.get("symbol", "?")
            shares = pos.get("shares", 0)
            entry = pos.get("entry_price", 0)
            current = pos.get("current_price", 0)
            pnl = pos.get("unrealized_pnl", 0)
            pnl_pct = pos.get("unrealized_pnl_pct", 0)

            emoji = "📈" if pnl >= 0 else "📉"
            sign = "+" if pnl >= 0 else ""
            position_lines.append(
                f"{emoji} {symbol}: {shares} shares\n"
                f"   Entry: {entry:.2f} → Now: {current:.2f}\n"
                f"   P/L: {sign}{pnl:.2f} ({sign}{pnl_pct:.1f}%)"
            )

        self._send_notification(
            f"📊 Position Update ({now.strftime('%I:%M %p ET')})\n\n"
            + "\n\n".join(position_lines)
            + "\n\nPositions close at 3:55 PM ET",
            parse_mode=None,
        )
        logger.info("Position update sent")

    def _job_health_check(self):
        """Morning health check - verify account, data feeds, etc."""
        now = get_et_now()

        if not is_trading_day(now.date()):
            return

        issues = []
        status_lines = []

        # Check cash balance
        try:
            portfolio = self.bot.get_portfolio_value()
            cash = portfolio.get("cash", 0)
            if cash < 100:
                issues.append(f"💰 Low cash balance: ${cash:.2f}")
            else:
                status_lines.append(f"💰 Cash: ${cash:.2f}")
        except Exception as e:
            issues.append(f"💰 Could not check cash: {e}")

        # Check data feed
        try:
            quote = self.bot.data_manager.get_quote("IBIT")
            if quote:
                source = quote.source.value
                if quote.is_realtime:
                    status_lines.append(f"📡 Data: {source} (real-time)")
                else:
                    issues.append(f"📡 Data: {source} (DELAYED - may affect signals)")
            else:
                issues.append("📡 Data feed unavailable")
        except Exception as e:
            issues.append(f"📡 Data feed error: {e}")

        # Check E*TRADE auth (if live mode)
        if not self.bot.is_paper_mode:
            if self.bot.client and self.bot.client.is_authenticated():
                status_lines.append("🔐 E*TRADE: Connected")
            else:
                issues.append(
                    "🔐 E*TRADE: NOT AUTHENTICATED!\n   ➡️ Run /auth to login before 9:35 AM"
                )

        # Build message
        if issues:
            message = "⚠️ Morning Health Check - Issues Found\n\n" + "\n".join(issues)
            if status_lines:
                message += "\n\n✅ OK:\n" + "\n".join(status_lines)
        else:
            message = (
                "✅ Morning Health Check - All Good!\n\n"
                + "\n".join(status_lines)
                + f"\n\nMode: {self.bot.config.mode.value.upper()}"
            )

        # Don't use Markdown - message may contain error strings
        self._send_notification(message, parse_mode=None)
        logger.info(f"Health check sent: {len(issues)} issues found")

    def _job_pattern_analysis(self):
        """Monthly pattern analysis - runs LLM to discover new trading patterns."""
        from .pattern_discovery import (
            PatternStatus,
            get_data_collector,
            get_pattern_analyzer,
            get_pattern_registry,
        )

        logger.info("Starting monthly pattern analysis")

        async def _run_analysis():
            try:
                # Collect historical data
                collector = get_data_collector(lookback_days=90)
                data = collector.collect_from_alpaca()

                if not data:
                    logger.error("Failed to collect market data for pattern analysis")
                    return

                # Get current patterns to avoid duplicates
                registry = get_pattern_registry()
                active_patterns = registry.get_live_patterns()

                # Run LLM analysis
                analyzer = get_pattern_analyzer()
                new_patterns = await analyzer.analyze(
                    day_of_week_stats=data.get("day_of_week_stats", {}),
                    hourly_stats=data.get("hourly_stats", {}),
                    overnight_stats=data.get("overnight_stats", {}),
                    active_patterns=active_patterns,
                )

                if not new_patterns:
                    logger.info("No new patterns discovered")
                    message = "📊 Monthly Pattern Analysis Complete\n\nNo new patterns discovered."
                else:
                    # Add new patterns as candidates
                    for pattern in new_patterns:
                        pattern.status = PatternStatus.CANDIDATE
                        registry.add_pattern(pattern)

                    pattern_list = "\n".join(
                        f"• {escape_markdown(p.display_name)} ({escape_markdown(p.instrument)}, {p.confidence:.0%} conf)"
                        for p in new_patterns
                    )
                    message = (
                        f"📊 Monthly Pattern Analysis Complete\n\n"
                        f"Discovered {len(new_patterns)} new pattern(s):\n{pattern_list}\n\n"
                        f"Status: CANDIDATE (paper trade before promoting)"
                    )

                # Send Telegram notification
                if self.telegram_bot:
                    try:
                        await self.telegram_bot.send_message(message)
                    except Exception as notify_err:
                        logger.error(f"Failed to send pattern analysis notification: {notify_err}")
                logger.info(f"Pattern analysis complete: {len(new_patterns)} new patterns")

            except Exception as e:
                logger.error(f"Pattern analysis failed: {e}")
                # Send error notification
                if self.telegram_bot:
                    try:
                        await self.telegram_bot.send_message(
                            f"❌ Pattern analysis failed: {escape_markdown(str(e))}"
                        )
                    except Exception:
                        pass

        run_async(_run_analysis())

    def _job_strategy_review(self):
        """Bi-weekly strategy review - backtests parameters and sends Claude analysis."""
        from .strategy_review import get_strategy_reviewer

        logger.info("Starting bi-weekly strategy review")

        async def _run_review():
            try:
                reviewer = get_strategy_reviewer()
                recommendation = await reviewer.run_monthly_review()

                # Build Telegram message
                if recommendation.has_recommendations:
                    header = "📊 *Strategy Review*\n⚠️ Recommendations Detected!\n\n"
                else:
                    header = "📊 *Strategy Review*\n✅ No changes needed\n\n"

                # The full report is from Claude - escape special chars
                message = header + escape_markdown(recommendation.full_report)

                # Truncate if too long for Telegram (max 4096 chars)
                if len(message) > 4000:
                    message = message[:3950] + "\n\n... (truncated)"

                # Send via Telegram
                if self.telegram_bot:
                    try:
                        await self.telegram_bot.send_message(message, parse_mode="Markdown")
                    except Exception as notify_err:
                        logger.error(f"Failed to send strategy review notification: {notify_err}")

                logger.info(
                    f"Strategy review complete: recommendations={recommendation.has_recommendations}"
                )

            except Exception as e:
                logger.error(f"Strategy review failed: {e}")
                # Send error notification
                if self.telegram_bot:
                    try:
                        await self.telegram_bot.send_message(
                            f"❌ Strategy review failed: {escape_markdown(str(e))}"
                        )
                    except Exception:
                        pass

        run_async(_run_review())

    def start(self):
        """Start the scheduler."""
        if self.status == BotStatus.RUNNING:
            logger.warning("Scheduler already running")
            return

        self.setup_jobs()
        self.scheduler.start()
        self.status = BotStatus.RUNNING
        logger.info("Scheduler started")

        self.db.log_event("SCHEDULER_START", "Bot scheduler started")

    def stop(self):
        """Stop the scheduler."""
        if self.status != BotStatus.RUNNING:
            return

        self.scheduler.shutdown(wait=False)
        self.status = BotStatus.STOPPED
        logger.info("Scheduler stopped")

        self.db.log_event("SCHEDULER_STOP", "Bot scheduler stopped")

    def run_now(self) -> TradeResult:
        """Manually trigger the morning signal (for testing)."""
        logger.info("Manual signal execution triggered")
        return self.bot.execute_signal()

    def get_status(self) -> dict:
        """Get scheduler status."""
        return {
            "status": self.status.value,
            "last_result": {
                "success": self._last_result.success if self._last_result else None,
                "signal": self._last_result.signal.value if self._last_result else None,
                "etf": self._last_result.etf if self._last_result else None,
            }
            if self._last_result
            else None,
            "error_count": self._error_count,
            "next_jobs": [
                {
                    "id": job.id,
                    "name": job.name,
                    "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                }
                for job in self.scheduler.get_jobs()
            ]
            if self.status == BotStatus.RUNNING
            else [],
        }
