"""
Backward-compatibility shim that imports from the modular trading_bot package.

All imports from `src.trading_bot` continue to work as before.
New code should import directly from `src.trading_bot` package.
"""

from .trading_bot import (
    ApprovalMode,
    BotConfig,
    TradeResult,
    TradingBot,
    TradingMode,
    create_trading_bot,
)

__all__ = [
    "TradingBot",
    "create_trading_bot",
    "TradingMode",
    "ApprovalMode",
    "TradeResult",
    "BotConfig",
]
