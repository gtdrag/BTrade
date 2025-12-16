#!/usr/bin/env python3
"""
Bitcoin ETF Trading Bot - Standalone Runner

Run this script to start the automated trading bot.
It will execute trades based on the smart strategy schedule:
- 9:35 AM ET: Check for Mean Reversion or Short Thursday signal
- 9:45 AM - 12:00 PM ET: Monitor for Crash Day signal (every 15 min)
- 3:55 PM ET: Close all positions

Usage:
    python run_bot.py              # Paper trading (default)
    python run_bot.py --live       # Live trading (requires E*TRADE setup)
    python run_bot.py --once       # Run signal check once and exit

Environment variables for live trading:
    ETRADE_CONSUMER_KEY     - Your E*TRADE API key
    ETRADE_CONSUMER_SECRET  - Your E*TRADE API secret
    ETRADE_ACCOUNT_ID       - Your E*TRADE account ID
"""

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime

from src.config import setup_logging
from src.etrade_client import ETradeClient
from src.smart_scheduler import SmartScheduler
from src.smart_strategy import StrategyConfig
from src.trading_bot import BotConfig, TradingBot, TradingMode
from src.utils import get_et_now

# Setup logging
setup_logging("INFO")
logger = logging.getLogger(__name__)


def create_bot(live_mode: bool = False) -> TradingBot:
    """Create and configure the trading bot."""
    # Strategy config
    strategy_config = StrategyConfig(
        mean_reversion_enabled=True,
        mean_reversion_threshold=-2.0,
        short_thursday_enabled=True,
        crash_day_enabled=True,
        crash_day_threshold=-2.0,
    )

    # Bot config
    mode = TradingMode.LIVE if live_mode else TradingMode.PAPER

    if live_mode:
        # Check for E*TRADE credentials
        consumer_key = os.environ.get("ETRADE_CONSUMER_KEY")
        consumer_secret = os.environ.get("ETRADE_CONSUMER_SECRET")
        account_id = os.environ.get("ETRADE_ACCOUNT_ID")

        if not all([consumer_key, consumer_secret, account_id]):
            logger.error("Missing E*TRADE credentials!")
            logger.error("Set ETRADE_CONSUMER_KEY, ETRADE_CONSUMER_SECRET, and ETRADE_ACCOUNT_ID")
            sys.exit(1)

        # Create E*TRADE client
        client = ETradeClient(consumer_key, consumer_secret)

        if not client.is_authenticated():
            logger.info("E*TRADE authentication required...")
            client.authenticate()

        bot_config = BotConfig(
            strategy=strategy_config,
            mode=mode,
            account_id_key=account_id,
        )

        return TradingBot(config=bot_config, client=client)
    else:
        bot_config = BotConfig(
            strategy=strategy_config,
            mode=mode,
        )
        return TradingBot(config=bot_config)


def run_once(bot: TradingBot):
    """Run the signal check once and exit."""
    logger.info("Running one-time signal check...")

    signal_result = bot.get_today_signal()
    now = get_et_now()

    print("\n" + "=" * 60)
    print(f"Bitcoin ETF Trading Bot - {now.strftime('%Y-%m-%d %H:%M:%S')} ET")
    print("=" * 60)
    print(f"\nMode: {'PAPER' if bot.is_paper_mode else 'LIVE'}")
    print(f"Signal: {signal_result.signal.value.upper()}")
    print(f"ETF: {signal_result.etf}")
    print(f"Reason: {signal_result.reason}")

    if signal_result.prev_day_return:
        print(f"Previous Day Return: {signal_result.prev_day_return:+.2f}%")

    if signal_result.crash_day_status:
        crash = signal_result.crash_day_status
        print("\nIntraday Status:")
        print(f"  IBIT Open: ${crash.ibit_open:.2f}")
        print(f"  IBIT Current: ${crash.ibit_current:.2f}")
        print(f"  Drop: {crash.current_drop_pct:+.2f}%")

    if signal_result.weekend_gap:
        gap = signal_result.weekend_gap
        print(f"\nWeekend Gap: {gap.gap_pct:+.2f}% ({gap.alert_level.value})")

    print("\n" + "=" * 60)

    if signal_result.should_trade():
        confirm = input(f"\nExecute trade ({signal_result.etf})? [y/N]: ").strip().lower()
        if confirm == "y":
            result = bot.execute_signal(signal_result)
            if result.success:
                print(
                    f"\n✓ Trade executed: {result.action} {result.shares} {result.etf} @ ${result.price:.2f}"
                )
            else:
                print(f"\n✗ Trade failed: {result.error}")


def run_scheduled(bot: TradingBot):
    """Run the bot with scheduled jobs."""
    scheduler = SmartScheduler(bot)

    # Handle shutdown gracefully
    def shutdown_handler(signum, frame):
        logger.info("Shutdown signal received...")
        scheduler.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    # Start scheduler
    scheduler.start()

    mode = "PAPER" if bot.is_paper_mode else "LIVE"
    print("\n" + "=" * 60)
    print(f"Bitcoin ETF Trading Bot - {mode} MODE")
    print("=" * 60)
    print("\nScheduled jobs:")
    for job in scheduler.scheduler.get_jobs():
        next_run = (
            job.next_run_time.strftime("%Y-%m-%d %H:%M:%S %Z") if job.next_run_time else "N/A"
        )
        print(f"  • {job.name}: {next_run}")

    print("\nBot is running. Press Ctrl+C to stop.")
    print("=" * 60 + "\n")

    # Keep running
    try:
        while True:
            time.sleep(60)
            # Log heartbeat every hour
            if datetime.now().minute == 0:
                status = scheduler.get_status()
                logger.info(
                    f"Bot heartbeat - Status: {status['status']}, Errors: {status['error_count']}"
                )
    except KeyboardInterrupt:
        pass
    finally:
        scheduler.stop()


def main():
    parser = argparse.ArgumentParser(
        description="Bitcoin ETF Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run in live trading mode (requires E*TRADE credentials)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run signal check once and exit (don't start scheduler)",
    )

    args = parser.parse_args()

    # Create bot
    bot = create_bot(live_mode=args.live)

    if args.once:
        run_once(bot)
    else:
        run_scheduled(bot)


if __name__ == "__main__":
    main()
