"""
Telegram Bot for Trade Notifications and Approval.

This module is a backward-compatibility shim that imports from the modular
telegram package. The actual implementation is now in src/telegram/.

For new code, import directly from src.telegram:
    from .telegram import TelegramBot, TelegramNotifier, ApprovalResult

Structure:
- src/telegram/bot.py: Main TelegramBot class
- src/telegram/notifier.py: Synchronous TelegramNotifier wrapper
- src/telegram/utils.py: Shared utilities (escape_markdown, ApprovalResult)
- src/telegram/trading_commands.py: /mode, /pause, /resume, /balance, etc.
- src/telegram/analysis_commands.py: /analyze, /patterns, /promote, etc.
- src/telegram/auth_commands.py: /auth, /verify
- src/telegram/backtest_commands.py: /backtest, /simulate
"""

# Re-export everything from the modular telegram package for backward compatibility
from .telegram import (
    ApprovalResult,
    TelegramBot,
    TelegramNotifier,
    TradeApprovalRequest,
    escape_markdown,
)

# For direct module imports (e.g., from .telegram_bot import ...)
__all__ = [
    "TelegramBot",
    "TelegramNotifier",
    "ApprovalResult",
    "TradeApprovalRequest",
    "escape_markdown",
]


# Unified async utilities - re-export for backward compatibility
from .async_utils import run_sync_in_executor as run_sync_in_thread  # noqa: F401, E402


# Quick test function
async def test_bot():
    """Test the Telegram bot."""
    import os

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token:
        print("❌ TELEGRAM_BOT_TOKEN not set")
        return

    if not chat_id:
        print("⚠️ TELEGRAM_CHAT_ID not set")
        print("Send /start to your bot to get your chat ID")

    bot = TelegramBot(token, chat_id)
    await bot.initialize()

    if chat_id:
        success = await bot.send_message(
            "🤖 *IBIT Trading Bot*\n\n"
            "✅ Connection test successful!\n"
            "Bot is ready to send trade notifications."
        )
        if success:
            print("✅ Test message sent successfully!")
        else:
            print("❌ Failed to send test message")
    else:
        print("ℹ️ Start your bot and send /start to get your chat ID")


if __name__ == "__main__":
    import asyncio

    from dotenv import load_dotenv

    load_dotenv()
    asyncio.run(test_bot())
