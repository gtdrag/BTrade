"""
Telegram bot package.

This package provides a modular Telegram bot for trade notifications and approval.

Main components:
- TelegramBot: Main bot class with all command handlers
- TelegramNotifier: Synchronous wrapper for non-async code
- ApprovalResult: Enum for trade approval outcomes
- escape_markdown: Utility to escape Telegram markdown characters

Command modules:
- trading_commands: /mode, /pause, /resume, /balance, /positions, /signal, /jobs, /logs
- analysis_commands: /analyze, /patterns, /analyses, /promote, /retire, /hedge, /review
- auth_commands: /auth, /verify
- backtest_commands: /backtest, /simulate
"""

from .bot import TelegramBot
from .notifier import TelegramNotifier
from .utils import ApprovalResult, TradeApprovalRequest, escape_markdown

__all__ = [
    "TelegramBot",
    "TelegramNotifier",
    "ApprovalResult",
    "TradeApprovalRequest",
    "escape_markdown",
]
