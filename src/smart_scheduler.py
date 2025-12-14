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
    - 9:35 AM ET: Execute morning signal (buy BITX or SBIT if signal exists)
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

        logger.info("Scheduler jobs configured")

    def _job_morning_signal(self):
        """Execute morning trading signal."""
        now = get_et_now()

        if not is_trading_day(now.date()):
            logger.info("Not a trading day, skipping")
            return

        logger.info("Executing morning signal check")

        try:
            result = self.bot.execute_signal()
            self._last_result = result

            if result.success:
                if result.signal != Signal.CASH:
                    logger.info(f"Trade executed: {result.action} {result.shares} {result.etf}")
                else:
                    logger.info("No trade signal today")
            else:
                logger.error(f"Trade failed: {result.error}")

        except Exception as e:
            logger.error(f"Morning signal job failed: {e}")
            self._error_count += 1

    def _job_close_positions(self):
        """Close any open positions before market close."""
        now = get_et_now()

        if not is_trading_day(now.date()):
            return

        logger.info("Closing positions before market close")

        try:
            # Close BITX position if exists
            if self.bot.is_paper_mode:
                for etf in list(self.bot._paper_positions.keys()):
                    result = self.bot.close_position(etf)
                    if result.success:
                        logger.info(f"Closed {etf} position")
                    else:
                        logger.error(f"Failed to close {etf}: {result.error}")
            else:
                # For live trading, close known ETF positions
                for etf in ["BITX", "SBIT"]:
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
