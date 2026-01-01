"""
Railway Worker - Background process for 24/7 trading bot operation.

This module runs continuously on Railway, managing:
- APScheduler for scheduled trade execution
- Telegram bot for notifications and approvals
- E*TRADE token refresh

Usage:
    python -m src.worker
"""

import asyncio
import logging
import os
import signal
import sys

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from .database import get_database  # noqa: E402
from .etrade_client import create_etrade_client  # noqa: E402
from .smart_scheduler import SmartScheduler  # noqa: E402
from .telegram_bot import TelegramBot  # noqa: E402
from .trading_bot import create_trading_bot  # noqa: E402
from .utils import get_et_now  # noqa: E402

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


class TradingWorker:
    """
    Main worker process for Railway deployment.

    Manages the trading bot scheduler and Telegram bot polling.
    """

    def __init__(self):
        self.running = False
        self.scheduler = None
        self.telegram_bot = None
        self.trading_bot = None
        self.db = get_database()

        # Configuration from environment (with DB persistence override)
        env_mode = os.environ.get("TRADING_MODE", "paper")
        # Load persisted mode from database - this survives restarts
        # Persisted mode from /mode command takes precedence over env variable
        persisted_mode = self.db.get_trading_mode()
        # If user explicitly set mode via /mode command, use that
        # Persisted "live" always takes precedence (explicit user action)
        # Env var "live" takes precedence over default "paper"
        if persisted_mode == "live":
            self.trading_mode = "live"
        else:
            self.trading_mode = env_mode  # Use env var (defaults to paper)
        self.approval_mode = os.environ.get("APPROVAL_MODE", "required")
        self.approval_timeout = int(os.environ.get("APPROVAL_TIMEOUT_MINUTES", "10"))
        self.max_position_pct = float(os.environ.get("MAX_POSITION_PCT", "75"))
        self.account_id_key = os.environ.get("ETRADE_ACCOUNT_ID", "")

    def setup(self):
        """Initialize all components."""
        logger.info("=" * 60)
        logger.info("IBIT Trading Bot Worker Starting")
        logger.info("=" * 60)
        logger.info(f"Trading Mode: {self.trading_mode.upper()}")
        logger.info(f"Approval Mode: {self.approval_mode}")
        logger.info(f"Max Position: {self.max_position_pct}%")
        logger.info(f"Current Time (ET): {get_et_now()}")
        logger.info("=" * 60)

        # Create E*TRADE client for live mode
        etrade_client = None
        if self.trading_mode == "live":
            try:
                etrade_client = create_etrade_client()
                if etrade_client:
                    logger.info("E*TRADE client created successfully")
                    # Verify authentication actually works
                    if etrade_client.is_authenticated():
                        logger.info("E*TRADE AUTHENTICATION VERIFIED - tokens are valid")
                        # Test position query
                        try:
                            positions = etrade_client.get_account_positions(self.account_id_key)
                            logger.info(f"E*TRADE position check: {len(positions)} positions found")
                            for pos in positions:
                                symbol = pos.get("Product", {}).get("symbol", "?")
                                qty = pos.get("quantity", 0)
                                logger.info(f"  - {symbol}: {qty} shares")
                        except Exception as e:
                            logger.warning(f"Position check failed: {e}")
                    else:
                        logger.error("E*TRADE AUTHENTICATION FAILED - tokens may be expired!")
                        logger.error("The 3:55 PM close will NOT work without valid authentication")
                else:
                    logger.warning("E*TRADE client creation returned None - live trading disabled")
            except Exception as e:
                logger.error(f"Failed to create E*TRADE client: {e}")
                logger.warning("Continuing in paper mode due to E*TRADE client failure")

        # Load strategy parameters from database (persisted from /review recommendations)
        saved_params = self.db.get_all_strategy_params()
        logger.info(f"Loaded {len(saved_params)} strategy params from database")
        for param, value in saved_params.items():
            logger.info(f"  - {param}: {value}")

        # Create trading bot with DB-persisted strategy params
        self.trading_bot = create_trading_bot(
            mode=self.trading_mode,
            max_position_pct=self.max_position_pct,
            approval_mode=self.approval_mode,
            approval_timeout_minutes=self.approval_timeout,
            account_id_key=self.account_id_key,
            etrade_client=etrade_client,
            # Strategy params from database (override defaults)
            mean_reversion_enabled=saved_params.get("mean_reversion_enabled", True),
            mean_reversion_threshold=saved_params.get("mr_threshold", -2.0),
            crash_day_enabled=saved_params.get("crash_day_enabled", True),
            crash_day_threshold=saved_params.get("crash_threshold", -2.0),
            pump_day_enabled=saved_params.get("pump_day_enabled", True),
            pump_day_threshold=saved_params.get("pump_threshold", 2.0),
            ten_am_dump_enabled=saved_params.get("ten_am_dump_enabled", True),
        )

        # Create scheduler
        self.scheduler = SmartScheduler(self.trading_bot)

        # Create Telegram bot for polling (to receive approval responses)
        telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")

        if telegram_token and telegram_chat_id:
            self.telegram_bot = TelegramBot(
                token=telegram_token,
                chat_id=telegram_chat_id,
                approval_timeout_minutes=self.approval_timeout,
                scheduler=self.scheduler,
                trading_bot=self.trading_bot,
            )
            # Share the telegram bot with the scheduler for notifications
            self.scheduler.telegram_bot = self.telegram_bot
            logger.info("Telegram bot configured with interactive commands")
        else:
            logger.warning("Telegram not configured - notifications disabled")

    async def start_telegram_polling(self):
        """Start Telegram bot polling in background."""
        if self.telegram_bot:
            try:
                await self.telegram_bot.start_polling()
                logger.info("Telegram bot polling started")

                # Send startup notification with strategy params
                now = get_et_now()
                next_jobs = self._get_next_jobs_preview()
                strategy_status = self._get_strategy_status()
                await self.telegram_bot.send_message(
                    f"🤖 IBIT Trading Bot Online\n\n"
                    f"Mode: {self.trading_mode.upper()}\n"
                    f"Approval: {self.approval_mode}\n"
                    f"Time: {now.strftime('%I:%M %p ET')}\n"
                    f"Date: {now.strftime('%A, %b %d')}\n\n"
                    f"📊 Strategy Config:\n{strategy_status}\n\n"
                    f"📅 Upcoming:\n{next_jobs}\n\n"
                    f"Ready for trading signals!"
                )
            except Exception as e:
                logger.error(f"Failed to start Telegram polling: {e}")

    async def stop_telegram_polling(self):
        """Stop Telegram bot polling."""
        if self.telegram_bot:
            try:
                await self.telegram_bot.stop()
                logger.info("Telegram bot stopped")
            except Exception as e:
                logger.error(f"Error stopping Telegram bot: {e}")

    def start(self):
        """Start the worker."""
        self.running = True

        # Start scheduler
        self.scheduler.start()
        logger.info("Scheduler started")

        # Run async event loop
        asyncio.run(self._run_async())

    async def _run_async(self):
        """Main async loop that keeps Telegram polling running."""
        try:
            # Start Telegram polling
            await self.start_telegram_polling()

            # Keep running with async sleep (keeps event loop active for polling)
            logger.info("Worker running. Press Ctrl+C to stop.")
            while self.running:
                await asyncio.sleep(1)

        except KeyboardInterrupt:
            logger.info("Shutdown requested...")
        except asyncio.CancelledError:
            logger.info("Async task cancelled...")
        finally:
            # Cleanup
            await self.stop_telegram_polling()
            self.scheduler.stop()
            logger.info("Worker stopped")

    def stop(self):
        """Signal the worker to stop."""
        self.running = False

    def _get_next_jobs_preview(self) -> str:
        """Get a preview of the next scheduled jobs."""
        if not self.scheduler:
            return "No scheduler configured"

        try:
            jobs = self.scheduler.scheduler.get_jobs()
            if not jobs:
                return "No jobs scheduled"

            # Sort by next run time and get first 3
            sorted_jobs = sorted(
                [j for j in jobs if j.next_run_time],
                key=lambda x: x.next_run_time,
            )[:3]

            lines = []
            for job in sorted_jobs:
                time_str = job.next_run_time.strftime("%I:%M %p")
                lines.append(f"• {time_str}: {job.name}")

            return "\n".join(lines) if lines else "No upcoming jobs"
        except Exception:
            return "Unable to fetch schedule"

    def _get_strategy_status(self) -> str:
        """Get current strategy configuration status."""
        if not self.trading_bot:
            return "Bot not initialized"

        config = self.trading_bot.strategy.config
        lines = []

        # Show enabled/disabled strategies
        strategies = [
            ("10AM Dump", config.ten_am_dump_enabled),
            ("Mean Reversion", config.mean_reversion_enabled),
            ("Crash Day", config.crash_day_enabled),
            ("Pump Day", config.pump_day_enabled),
        ]

        for name, enabled in strategies:
            status = "✅" if enabled else "❌"
            lines.append(f"{status} {name}")

        return "\n".join(lines)


def main():
    """Entry point for the worker."""
    worker = TradingWorker()

    # Handle signals for graceful shutdown
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}")
        worker.stop()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Setup and run
    worker.setup()
    worker.start()


if __name__ == "__main__":
    main()
