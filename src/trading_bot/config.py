"""
Configuration types for the trading bot.

Contains enums and dataclasses used across the trading_bot package.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..notifications import NotificationConfig
from ..smart_strategy import Signal, StrategyConfig


class TradingMode(Enum):
    """Trading mode."""

    LIVE = "live"
    PAPER = "paper"


class ApprovalMode(Enum):
    """Trade approval mode for Telegram."""

    REQUIRED = "required"  # Must approve each trade via Telegram
    NOTIFY_ONLY = "notify_only"  # Send notification but auto-execute
    AUTO_EXECUTE = "auto_execute"  # No notification, just execute


@dataclass
class TradeResult:
    """Result of a trade execution."""

    success: bool
    signal: Signal
    etf: str
    action: str  # "BUY" or "SELL"
    shares: int = 0
    price: float = 0.0
    total_value: float = 0.0
    order_id: Optional[str] = None
    error: Optional[str] = None
    is_paper: bool = False


@dataclass
class BotConfig:
    """Configuration for the trading bot."""

    # Strategy settings
    strategy: StrategyConfig = field(default_factory=StrategyConfig)

    # Trading settings
    mode: TradingMode = TradingMode.PAPER
    max_position_pct: float = 100.0
    max_position_usd: Optional[float] = None

    # E*TRADE settings
    account_id_key: str = ""

    # Notifications
    notifications: NotificationConfig = field(default_factory=NotificationConfig)

    # Telegram approval settings
    approval_mode: ApprovalMode = ApprovalMode.REQUIRED
    approval_timeout_minutes: int = 10
