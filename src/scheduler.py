"""
Scheduler for IBIT Dip Bot.
Handles timing of market operations during trading hours.
"""

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Callable, Optional, Dict, Any
from enum import Enum

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

from .utils import get_et_now, get_market_times, is_trading_day, ET
from .strategy import IBITDipStrategy, TradeAction
from .database import get_database
from .notifications import NotificationManager


logger = logging.getLogger(__name__)


class BotStatus(Enum):
    """Bot status states."""
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


class TradingScheduler:
    """
    Manages scheduled execution of trading strategy.

    Key scheduled events:
    - 9:30 AM ET: Capture open price
    - 10:30 AM ET: Execute dip check and potential buy
    - 3:55 PM ET (Friday) / 3:58 PM ET (other days): Execute sell if position held
    """

    def __init__(
        self,
        strategy: IBITDipStrategy,
        notifications: Optional[NotificationManager] = None
    ):
        """
        Initialize scheduler.

        Args:
            strategy: Trading strategy instance
            notifications: Notification manager for alerts
        """
        self.strategy = strategy
        self.notifications = notifications
        self.db = get_database()

        self.scheduler = BackgroundScheduler(timezone=ET)
        self.status = BotStatus.STOPPED
        self._last_action: Optional[Dict[str, Any]] = None
        self._error_count = 0

        # Add error listener
        self.scheduler.add_listener(
            self._on_job_event,
            EVENT_JOB_ERROR | EVENT_JOB_EXECUTED
        )

    def _on_job_event(self, event):
        """Handle scheduler job events."""
        if event.exception:
            self._error_count += 1
            logger.error(f"Scheduler job failed: {event.exception}")
            self.db.log_event("ERROR", "Scheduler job failed", {
                "job_id": event.job_id,
                "error": str(event.exception)
            })
            if self.notifications:
                self.notifications.send_error(
                    f"Scheduler job {event.job_id} failed",
                    str(event.exception)
                )

    def setup_jobs(self):
        """Set up scheduled jobs for trading day."""
        # Clear existing jobs
        self.scheduler.remove_all_jobs()

        # Job 1: Capture open price at 9:30 AM ET
        self.scheduler.add_job(
            self._job_capture_open,
            CronTrigger(
                day_of_week='mon-fri',
                hour=9,
                minute=30,
                timezone=ET
            ),
            id='capture_open',
            name='Capture Open Price',
            replace_existing=True
        )

        # Job 2: Dip check at 10:30 AM ET (middle of dip window)
        self.scheduler.add_job(
            self._job_dip_check,
            CronTrigger(
                day_of_week='mon-fri',
                hour=10,
                minute=30,
                timezone=ET
            ),
            id='dip_check',
            name='Dip Check and Buy',
            replace_existing=True
        )

        # Job 3: Friday close at 3:55 PM ET
        self.scheduler.add_job(
            self._job_friday_close,
            CronTrigger(
                day_of_week='fri',
                hour=15,
                minute=55,
                timezone=ET
            ),
            id='friday_close',
            name='Friday Close',
            replace_existing=True
        )

        # Job 4: Regular close at 3:58 PM ET (Mon-Thu)
        self.scheduler.add_job(
            self._job_regular_close,
            CronTrigger(
                day_of_week='mon-thu',
                hour=15,
                minute=58,
                timezone=ET
            ),
            id='regular_close',
            name='Regular Close',
            replace_existing=True
        )

        # Job 5: Daily token renewal at 7:00 AM ET
        self.scheduler.add_job(
            self._job_renew_token,
            CronTrigger(
                day_of_week='mon-fri',
                hour=7,
                minute=0,
                timezone=ET
            ),
            id='renew_token',
            name='Renew OAuth Token',
            replace_existing=True
        )

        # Job 6: Status check every 5 minutes during market hours
        self.scheduler.add_job(
            self._job_status_check,
            CronTrigger(
                day_of_week='mon-fri',
                hour='9-16',
                minute='*/5',
                timezone=ET
            ),
            id='status_check',
            name='Status Check',
            replace_existing=True
        )

        logger.info("Scheduled jobs configured")

    def _job_capture_open(self):
        """Job: Capture market open price."""
        logger.info("Running job: capture_open")

        if not is_trading_day():
            logger.info("Not a trading day, skipping")
            return

        try:
            price = self.strategy.capture_open_price()
            if price:
                self._last_action = {
                    "job": "capture_open",
                    "time": get_et_now().isoformat(),
                    "price": price
                }
                self.db.log_event("INFO", "Open price captured", {"price": price})

                if self.notifications:
                    self.notifications.send_info(
                        "Market Open",
                        f"IBIT open price: ${price:.2f}"
                    )
        except Exception as e:
            logger.error(f"Failed to capture open price: {e}")
            raise

    def _job_dip_check(self):
        """Job: Check for dip and execute buy if conditions met."""
        logger.info("Running job: dip_check")

        if not is_trading_day():
            logger.info("Not a trading day, skipping")
            return

        try:
            signal = self.strategy.analyze()

            self._last_action = {
                "job": "dip_check",
                "time": get_et_now().isoformat(),
                "signal": signal.action.value,
                "dip_pct": signal.dip_percentage,
                "reason": signal.reason
            }

            if signal.action == TradeAction.BUY:
                result = self.strategy.execute(signal)

                if result.get("success"):
                    self.db.log_event("INFO", "Buy executed", result)
                    if self.notifications:
                        self.notifications.send_trade(
                            "BUY",
                            "IBIT",
                            result.get("shares", 0),
                            result.get("price", 0),
                            dip_pct=result.get("dip_percentage", 0)
                        )
                else:
                    logger.warning(f"Buy execution failed: {result.get('reason')}")
            else:
                logger.info(f"No buy signal: {signal.reason}")
                self.db.log_event("INFO", "Dip check - no buy", {
                    "dip_pct": signal.dip_percentage,
                    "threshold": signal.threshold_used,
                    "reason": signal.reason
                })

        except Exception as e:
            logger.error(f"Dip check failed: {e}")
            raise

    def _job_friday_close(self):
        """Job: Close position on Friday before weekend."""
        logger.info("Running job: friday_close")
        self._execute_close("Friday close")

    def _job_regular_close(self):
        """Job: Close position at end of regular trading day."""
        logger.info("Running job: regular_close")
        self._execute_close("Market close")

    def _execute_close(self, reason: str):
        """Execute position close if we have a position."""
        if not is_trading_day():
            logger.info("Not a trading day, skipping")
            return

        try:
            state = self.strategy.get_state()

            if not state.has_position:
                logger.info("No position to close")
                self._last_action = {
                    "job": "close",
                    "time": get_et_now().isoformat(),
                    "action": "none",
                    "reason": "No position"
                }
                return

            result = self.strategy.force_sell()

            self._last_action = {
                "job": "close",
                "time": get_et_now().isoformat(),
                "action": "sell" if result.get("success") else "failed",
                "result": result
            }

            if result.get("success"):
                self.db.log_event("INFO", f"Position closed ({reason})", result)
                if self.notifications:
                    self.notifications.send_trade(
                        "SELL",
                        "IBIT",
                        result.get("shares", 0),
                        result.get("exit_price", 0),
                        pnl=result.get("dollar_pnl", 0),
                        pnl_pct=result.get("percentage_pnl", 0)
                    )
            else:
                logger.error(f"Failed to close position: {result.get('reason')}")
                if self.notifications:
                    self.notifications.send_error(
                        "Failed to close position",
                        result.get("reason", "Unknown error")
                    )

        except Exception as e:
            logger.error(f"Close job failed: {e}")
            raise

    def _job_renew_token(self):
        """Job: Renew E*TRADE OAuth token."""
        logger.info("Running job: renew_token")

        try:
            success = self.strategy.client.renew_token()
            self._last_action = {
                "job": "renew_token",
                "time": get_et_now().isoformat(),
                "success": success
            }

            if success:
                self.db.log_event("INFO", "OAuth token renewed")
            else:
                logger.warning("Token renewal returned False")
                if self.notifications:
                    self.notifications.send_warning(
                        "Token Renewal Issue",
                        "OAuth token renewal may have failed. Check authentication."
                    )

        except Exception as e:
            logger.error(f"Token renewal failed: {e}")
            if self.notifications:
                self.notifications.send_error(
                    "Token Renewal Failed",
                    str(e)
                )
            raise

    def _job_status_check(self):
        """Job: Periodic status check and heartbeat."""
        try:
            state = self.strategy.get_state()

            # Log heartbeat
            self.db.log_event("DEBUG", "Status check", {
                "has_position": state.has_position,
                "dip_pct": state.dip_percentage,
                "current_price": state.current_price
            })

        except Exception as e:
            logger.warning(f"Status check error: {e}")

    def start(self):
        """Start the scheduler."""
        if self.status == BotStatus.RUNNING:
            logger.warning("Scheduler already running")
            return

        self.setup_jobs()
        self.scheduler.start()
        self.status = BotStatus.RUNNING
        self._error_count = 0

        self.db.log_event("INFO", "Scheduler started")
        logger.info("Trading scheduler started")

        if self.notifications:
            self.notifications.send_info("Bot Started", "IBIT Dip Bot is now running")

    def stop(self):
        """Stop the scheduler."""
        if self.status == BotStatus.STOPPED:
            return

        self.scheduler.shutdown(wait=False)
        self.status = BotStatus.STOPPED

        self.db.log_event("INFO", "Scheduler stopped")
        logger.info("Trading scheduler stopped")

        if self.notifications:
            self.notifications.send_info("Bot Stopped", "IBIT Dip Bot has been stopped")

    def pause(self):
        """Pause the scheduler (jobs remain but don't execute)."""
        self.scheduler.pause()
        self.status = BotStatus.PAUSED
        self.db.log_event("INFO", "Scheduler paused")
        logger.info("Trading scheduler paused")

    def resume(self):
        """Resume a paused scheduler."""
        self.scheduler.resume()
        self.status = BotStatus.RUNNING
        self.db.log_event("INFO", "Scheduler resumed")
        logger.info("Trading scheduler resumed")

    def get_status(self) -> Dict[str, Any]:
        """Get current scheduler status."""
        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None
            })

        return {
            "status": self.status.value,
            "jobs": jobs,
            "last_action": self._last_action,
            "error_count": self._error_count
        }

    def run_now(self, job_id: str) -> bool:
        """Manually trigger a job to run immediately."""
        job = self.scheduler.get_job(job_id)
        if job:
            job.modify(next_run_time=datetime.now(ET))
            logger.info(f"Triggered job: {job_id}")
            return True
        return False


class SimpleScheduler:
    """
    Simple scheduler using time.sleep() for environments without APScheduler.
    Runs in a loop checking for trigger times.
    """

    def __init__(
        self,
        strategy: IBITDipStrategy,
        notifications: Optional[NotificationManager] = None,
        check_interval: int = 30  # seconds
    ):
        self.strategy = strategy
        self.notifications = notifications
        self.check_interval = check_interval
        self.db = get_database()

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_triggers: Dict[str, str] = {}

    def _should_trigger(self, trigger_name: str, target_time: datetime) -> bool:
        """Check if we should trigger an action (haven't triggered today)."""
        now = get_et_now()
        today = now.date().isoformat()

        # Check if already triggered today
        last_trigger = self._last_triggers.get(trigger_name)
        if last_trigger == today:
            return False

        # Check if we're past the target time
        if now >= target_time:
            self._last_triggers[trigger_name] = today
            return True

        return False

    def _run_loop(self):
        """Main scheduling loop."""
        logger.info("Simple scheduler loop started")

        while self._running:
            try:
                now = get_et_now()
                times = get_market_times(now.date())

                if is_trading_day(now.date()):
                    # Check open price capture (9:30 AM)
                    if self._should_trigger("open", times["market_open"]):
                        logger.info("Triggering: capture open price")
                        self.strategy.capture_open_price()

                    # Check dip window (10:30 AM)
                    dip_check_time = times["dip_window_start"] + timedelta(minutes=30)
                    if self._should_trigger("dip_check", dip_check_time):
                        logger.info("Triggering: dip check")
                        signal = self.strategy.analyze()
                        if signal.action == TradeAction.BUY:
                            self.strategy.execute(signal)

                    # Check close (3:55 PM Friday, 3:58 PM other days)
                    is_friday = now.weekday() == 4
                    close_time = times["friday_close"] if is_friday else (
                        times["market_close"] - timedelta(minutes=2)
                    )

                    if self._should_trigger("close", close_time):
                        logger.info("Triggering: market close")
                        state = self.strategy.get_state()
                        if state.has_position:
                            self.strategy.force_sell()

                time.sleep(self.check_interval)

            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")
                time.sleep(60)  # Wait longer on error

        logger.info("Simple scheduler loop stopped")

    def start(self):
        """Start the scheduler in a background thread."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Simple scheduler started")

    def stop(self):
        """Stop the scheduler."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Simple scheduler stopped")
