"""
Trading Bot Package

Modular trading bot for executing strategies with E*TRADE or paper trading.
Supports multiple data sources and Telegram notifications.

Main exports:
- TradingBot: Main trading bot class
- create_trading_bot: Factory function for creating configured bots
- TradingMode, ApprovalMode: Mode enums
- TradeResult, BotConfig: Result and configuration dataclasses
"""

from .config import ApprovalMode, BotConfig, TradeResult, TradingMode
from .core import TradingBot, create_trading_bot

__all__ = [
    # Main class and factory
    "TradingBot",
    "create_trading_bot",
    # Configuration types
    "TradingMode",
    "ApprovalMode",
    "TradeResult",
    "BotConfig",
]
