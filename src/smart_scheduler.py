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
from .telegram_bot import TelegramBot
from .trading_bot import TradeResult, TradingBot
from .utils import ET, get_et_now, is_trading_day

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

    def __init__(self, bot: TradingBot):
        self.bot = bot
        self.db = get_database()
        self.scheduler = BackgroundScheduler(timezone=ET)
        self.status = BotStatus.STOPPED
        self._last_result: Optional[TradeResult] = None
        self._error_count = 0

        # Add event listeners
        self.scheduler.add_listener(self._on_job_event, EVENT_JOB_ERROR | EVENT_JOB_EXECUTED)

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
                "change_pct": signal.btc_overnight.change_pct,
                "is_positive": signal.btc_overnight.is_positive,
                "message": signal.btc_overnight.message,
            }

        # Add crash day status if available
        if signal.crash_day_status:
            details["crash_day"] = {
                "current_drop_pct": signal.crash_day_status.current_drop_pct,
                "is_triggered": signal.crash_day_status.is_triggered,
                "threshold": signal.crash_day_status.threshold,
            }

        # Add pump day status if available
        if signal.pump_day_status:
            details["pump_day"] = {
                "current_gain_pct": signal.pump_day_status.current_gain_pct,
                "is_triggered": signal.pump_day_status.is_triggered,
                "threshold": signal.pump_day_status.threshold,
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

        # Crash day monitoring - check every 15 min from 9:45 AM to 12:00 PM ET
        if self.bot.config.strategy.crash_day_enabled:
            self.scheduler.add_job(
                self._job_crash_day_check,
                CronTrigger(
                    day_of_week="mon-fri",
                    hour="9-11",
                    minute="45,0,15,30,45",
                    timezone=ET,
                ),
                id="crash_day_check",
                name="Crash Day Monitor",
                misfire_grace_time=120,
            )

        # Pump day monitoring - check every 15 min from 9:45 AM to 12:00 PM ET
        if self.bot.config.strategy.pump_day_enabled:
            self.scheduler.add_job(
                self._job_pump_day_check,
                CronTrigger(
                    day_of_week="mon-fri",
                    hour="9-11",
                    minute="45,0,15,30,45",
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

        logger.info("Scheduler jobs configured")

    def _job_auth_reminder(self):
        """Send daily authentication reminder at 8:00 AM ET."""
        import asyncio

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
        async def _send_reminder():
            bot = TelegramBot()
            await bot.initialize()

            if is_authenticated:
                await bot.send_message(
                    f"☀️ *Good Morning!* Trading day started.\n\n"
                    f"🔐 E*TRADE: {auth_status}\n"
                    f"📅 {now.strftime('%A, %B %d')}\n\n"
                    f"Signal check at 9:35 AM ET",
                    parse_mode="Markdown",
                )
            else:
                await bot.send_message(
                    "🚨 *ACTION REQUIRED*\n\n"
                    "E*TRADE authentication expired!\n\n"
                    "➡️ Run /auth now to login\n"
                    "⏰ Must complete before 9:35 AM ET\n\n"
                    "Without auth, trades will be blocked.",
                    parse_mode="Markdown",
                )

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        loop.run_until_complete(_send_reminder())

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
        import asyncio

        day_name = now.strftime("%A")

        # Build reason for no signal
        reason_lines = []

        if day_name == "Thursday":
            reason_lines.append("• Thursday detected, but overnight filter blocked short")
        else:
            reason_lines.append("• No mean reversion trigger (IBIT didn't drop enough)")

        reason = "\n".join(reason_lines) if reason_lines else "• No qualifying conditions met"

        async def _send():
            bot = TelegramBot()
            await bot.initialize()
            await bot.send_message(
                f"📭 No Trade Signal Today\n\n"
                f"Time: {now.strftime('%I:%M %p ET')}\n"
                f"Day: {day_name}\n\n"
                f"Reason:\n{reason}\n\n"
                "Staying in cash. Monitoring for intraday opportunities..."
            )

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        loop.run_until_complete(_send())
        logger.info("No-signal notification sent")

    def _send_error_notification(self, error_msg: str):
        """Send error notification via Telegram."""
        import asyncio

        now = get_et_now()

        async def _send():
            bot = TelegramBot()
            await bot.initialize()
            # Don't use Markdown - error messages may contain special chars
            await bot.send_message(
                f"🚨 Bot Error Alert\n\n"
                f"Time: {now.strftime('%I:%M %p ET')}\n\n"
                f"Error: {error_msg}\n\n"
                "Please check logs for details.",
                parse_mode=None,
            )

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        loop.run_until_complete(_send())
        logger.info("Error notification sent")

    def _job_crash_day_check(self):
        """Check for intraday crash signal and execute if triggered."""
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

                # Check if we have an existing position that conflicts
                has_bitx = False
                has_sbit = False

                if self.bot.is_paper_mode:
                    has_bitx = "BITU" in self.bot._paper_positions
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
                                has_bitx = True
                            elif symbol == "SBIT" and qty > 0:
                                has_sbit = True
                    except Exception as e:
                        logger.warning(f"Could not check positions: {e}")

                # If holding BITU (long), we MUST close it - holding long during crash is disaster
                if has_bitx:
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

                # Now execute the crash day trade
                result = self.bot.execute_signal(signal)
                self._last_result = result

                if result.success:
                    # Mark that we've traded the crash day
                    self.bot.strategy.mark_crash_day_traded()
                    logger.info(
                        f"Crash day trade executed: {result.shares} SBIT @ ${result.price:.2f}"
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

    def _job_pump_day_check(self):
        """Check for intraday pump signal and execute if triggered."""
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

                # Now execute the pump day trade
                result = self.bot.execute_signal(signal)
                self._last_result = result

                if result.success:
                    # Mark that we've traded the pump day
                    self.bot.strategy.mark_pump_day_traded()
                    logger.info(
                        f"Pump day trade executed: {result.shares} BITU @ ${result.price:.2f}"
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

    def _job_ten_am_dump_exit(self):
        """Exit 10 AM dump position at 10:30 AM ET."""
        import asyncio

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
                async def _send_exit():
                    bot = TelegramBot()
                    await bot.initialize()
                    await bot.send_message(
                        f"🔔 *10 AM Dump Exit*\n\n"
                        f"Sold: {result.shares} SBIT @ ${result.price:.2f}\n"
                        f"P/L: {pnl_str}\n"
                        f"Time: {now.strftime('%I:%M %p ET')}\n\n"
                        "Strategy: Captured 10 AM weakness window",
                        parse_mode="Markdown",
                    )

                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                loop.run_until_complete(_send_exit())

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

    def _job_close_positions(self):
        """Close any open positions before market close."""
        now = get_et_now()

        if not is_trading_day(now.date()):
            return

        logger.info("Closing positions before market close")

        try:
            # Close BITU position if exists
            if self.bot.is_paper_mode:
                for etf in list(self.bot._paper_positions.keys()):
                    result = self.bot.close_position(etf)
                    if result.success:
                        logger.info(f"Closed {etf} position")
                    else:
                        logger.error(f"Failed to close {etf}: {result.error}")
            else:
                # For live trading, close known ETF positions
                for etf in ["BITU", "SBIT"]:
                    result = self.bot.close_position(etf)
                    if result.success and result.shares > 0:
                        logger.info(f"Closed {etf} position")

        except Exception as e:
            logger.error(f"Close positions job failed: {e}")
            self._error_count += 1

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
        import asyncio

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
            async def _send_summary():
                bot = TelegramBot()
                await bot.initialize()
                await bot.send_daily_summary(
                    trades_today=trades_today,
                    total_pnl=total_pnl,
                    win_rate=win_rate,
                    ending_cash=ending_cash,
                )

            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            loop.run_until_complete(_send_summary())

            logger.info(f"Daily summary sent: {trades_today} trades, P/L: ${total_pnl:.2f}")

        except Exception as e:
            logger.error(f"Daily summary failed: {e}")
            self._error_count += 1

    def _job_premarket_reminder(self):
        """Send pre-market reminder at 9:15 AM ET."""
        import asyncio

        now = get_et_now()

        if not is_trading_day(now.date()):
            # Send holiday/weekend notice instead
            day_name = now.strftime("%A")

            async def _send_closed():
                bot = TelegramBot()
                await bot.initialize()
                await bot.send_message(
                    f"📅 Market closed today ({day_name})\n\n"
                    "No trading activity scheduled. Enjoy your day off!"
                )

            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            loop.run_until_complete(_send_closed())
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

        async def _send_reminder():
            bot = TelegramBot()
            await bot.initialize()
            await bot.send_message(
                f"☀️ Market opens in 15 minutes!\n\n"
                f"Time: {now.strftime('%I:%M %p ET')}\n"
                f"Day: {day_name}"
                f"{strategy_hint}"
                f"{signal_preview}\n\n"
                "Signal check at 9:35 AM ET"
            )

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        loop.run_until_complete(_send_reminder())
        logger.info("Pre-market reminder sent")

    def _job_position_update(self):
        """Send hourly position update if holding a position."""
        import asyncio

        now = get_et_now()

        if not is_trading_day(now.date()):
            return

        # Check if we have any positions
        portfolio = self.bot.get_portfolio_value()
        positions = portfolio.get("positions", [])

        if not positions:
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

        async def _send_update():
            bot = TelegramBot()
            await bot.initialize()
            await bot.send_message(
                f"📊 Position Update ({now.strftime('%I:%M %p ET')})\n\n"
                + "\n\n".join(position_lines)
                + "\n\nPositions close at 3:55 PM ET"
            )

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        loop.run_until_complete(_send_update())
        logger.info("Position update sent")

    def _job_health_check(self):
        """Morning health check - verify account, data feeds, etc."""
        import asyncio

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

        async def _send_health():
            bot = TelegramBot()
            await bot.initialize()
            # Don't use Markdown - message may contain error strings
            await bot.send_message(message, parse_mode=None)

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        loop.run_until_complete(_send_health())
        logger.info(f"Health check sent: {len(issues)} issues found")

    def _job_pattern_analysis(self):
        """Monthly pattern analysis - runs LLM to discover new trading patterns."""
        import asyncio

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
                        f"• {p.display_name} ({p.instrument}, {p.confidence:.0%} conf)"
                        for p in new_patterns
                    )
                    message = (
                        f"📊 Monthly Pattern Analysis Complete\n\n"
                        f"Discovered {len(new_patterns)} new pattern(s):\n{pattern_list}\n\n"
                        f"Status: CANDIDATE (paper trade before promoting)"
                    )

                # Send Telegram notification
                bot = TelegramBot()
                await bot.initialize()
                await bot.send_message(message)
                logger.info(f"Pattern analysis complete: {len(new_patterns)} new patterns")

            except Exception as e:
                logger.error(f"Pattern analysis failed: {e}")
                # Send error notification
                try:
                    bot = TelegramBot()
                    await bot.initialize()
                    await bot.send_message(f"❌ Pattern analysis failed: {e}")
                except Exception:
                    pass

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        loop.run_until_complete(_run_analysis())

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
