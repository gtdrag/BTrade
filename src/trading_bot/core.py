"""
Trading Bot - Core Module

Main TradingBot class combining all mixins, plus factory function.
Connects SmartStrategy with E*TRADE execution, notifications, and scheduling.
Supports both live trading and paper trading modes.

Data Sources (in priority order):
1. E*TRADE Production (real-time, requires approved API keys)
2. Alpaca (real-time, free API keys)
3. Finnhub (real-time with slight delay, free tier)
4. Yahoo Finance (15-min delay, no auth - fallback only)
"""

import logging
import os
import threading
from typing import Any, Dict, Optional

from ..data_providers import MarketDataManager, create_data_manager
from ..database import Database, get_database
from ..etrade_client import ETradeAuthError, ETradeClient
from ..notifications import NotificationConfig, NotificationManager
from ..smart_strategy import SmartStrategy, StrategyConfig, TodaySignal
from ..telegram_bot import TelegramNotifier
from ..trailing_hedge import get_hedge_manager
from .config import ApprovalMode, BotConfig, TradingMode
from .execution_mixin import ExecutionMixin
from .hedge_mixin import HedgeMixin
from .notifications_mixin import NotificationsMixin
from .orders_mixin import OrdersMixin
from .positions_mixin import PositionsMixin

logger = logging.getLogger(__name__)


class TradingBot(
    OrdersMixin,
    PositionsMixin,
    ExecutionMixin,
    HedgeMixin,
    NotificationsMixin,
):
    """
    Main trading bot integrating strategy, broker, and notifications.

    Workflow:
    1. Get today's signal from SmartStrategy
    2. If signal exists, execute trade via E*TRADE (or paper trade)
    3. Send notifications
    4. Log to database

    This class combines functionality from multiple mixins:
    - OrdersMixin: Order tracking, fill polling, duplicate prevention
    - PositionsMixin: Position queries, closing, portfolio value
    - ExecutionMixin: Trade execution (paper and live)
    - HedgeMixin: Trailing hedges and loss reversal
    - NotificationsMixin: Notifications and logging
    """

    def __init__(
        self,
        config: BotConfig,
        client: Optional[ETradeClient] = None,
        notifications: Optional[NotificationManager] = None,
        db: Optional[Database] = None,
        data_manager: Optional[MarketDataManager] = None,
        telegram: Optional[TelegramNotifier] = None,
    ):
        self.config = config
        self.client = client
        self.notifications = notifications or NotificationManager(config.notifications)
        self.db = db or get_database()
        self.strategy = SmartStrategy(config=config.strategy)

        # Telegram notifier for trade approvals
        self.telegram = telegram or TelegramNotifier(
            token=os.environ.get("TELEGRAM_BOT_TOKEN"),
            chat_id=os.environ.get("TELEGRAM_CHAT_ID"),
            approval_timeout_minutes=config.approval_timeout_minutes,
        )

        # Data manager for market quotes (uses best available source)
        self.data_manager = data_manager or create_data_manager(
            etrade_client=client if client and not getattr(client, "sandbox", True) else None
        )

        # Paper trading state
        self._paper_capital = 10000.0
        self._paper_positions: Dict[str, Dict] = {}

        # Daily trade tracking to prevent duplicates
        self._trades_today: Dict[str, str] = {}  # signal_type -> timestamp

        # Trailing hedge manager
        self.hedge_manager = get_hedge_manager()

        # Reversal tracking (flip to inverse when losing)
        self._reversal_triggered_today: bool = False
        self._reversal_date: Optional[str] = None
        self._original_signal = None  # Track what signal we're potentially reversing

        # Thread safety: Reentrant lock for position modification operations
        # Prevents concurrent jobs from racing on the same position
        # RLock allows same thread to reacquire (e.g., reversal -> close_position)
        self._position_lock = threading.RLock()

    @property
    def is_paper_mode(self) -> bool:
        return self.config.mode == TradingMode.PAPER

    def get_today_signal(self, include_position_context: bool = True) -> TodaySignal:
        """
        Get today's trading signal.

        Args:
            include_position_context: If True, passes current positions to strategy
                                     for position-aware signal generation.
        """
        current_positions = None
        if include_position_context:
            try:
                current_positions = self.get_open_positions()
            except Exception as e:
                logger.warning(f"Could not get positions for signal context: {e}")

        return self.strategy.get_today_signal(current_positions=current_positions)

    def get_available_capital(self) -> float:
        """Get available capital for trading."""
        if self.is_paper_mode:
            return self._paper_capital

        if not self.client or not self.client.is_authenticated():
            raise ETradeAuthError("E*TRADE client not authenticated")

        return self.client.get_cash_available(self.config.account_id_key)

    def calculate_position_size(self, price: float) -> int:
        """Calculate number of shares to buy."""
        capital = self.get_available_capital()

        # Apply position limits
        max_capital = capital * (self.config.max_position_pct / 100)
        if self.config.max_position_usd:
            max_capital = min(max_capital, self.config.max_position_usd)

        shares = int(max_capital // price)
        return max(0, shares)

    def get_quote(self, symbol: str) -> Dict[str, Any]:
        """Get current quote for a symbol using best available data source."""
        # Use data manager for quotes (automatically uses best available source)
        quote = self.data_manager.get_quote(symbol)

        if quote:
            return {
                "current_price": quote.current_price,
                "open_price": quote.open_price,
                "bid": quote.bid,
                "ask": quote.ask,
                "source": quote.source.value,
                "is_realtime": quote.is_realtime,
            }

        # Fallback to strategy's yfinance method if data manager fails
        logger.warning(f"Data manager failed for {symbol}, using strategy fallback")
        return self.strategy.get_etf_quote(symbol)


def create_trading_bot(
    mode: str = "paper",
    etrade_client: Optional[ETradeClient] = None,
    account_id_key: str = "",
    mean_reversion_threshold: float = -2.0,
    mean_reversion_enabled: bool = True,
    short_thursday_enabled: bool = True,
    crash_day_enabled: bool = True,
    crash_day_threshold: float = -1.5,
    pump_day_enabled: bool = True,
    pump_day_threshold: float = 1.5,
    ten_am_dump_enabled: bool = True,
    max_position_pct: float = 100.0,
    max_position_usd: Optional[float] = None,
    notification_config: Optional[NotificationConfig] = None,
    approval_mode: str = "required",
    approval_timeout_minutes: int = 10,
) -> TradingBot:
    """
    Factory function to create a configured TradingBot.

    Args:
        mode: "paper" or "live"
        etrade_client: Optional E*TRADE client for live trading
        account_id_key: E*TRADE account ID for live trading
        mean_reversion_threshold: Threshold for mean reversion signal
        mean_reversion_enabled: Enable mean reversion strategy
        short_thursday_enabled: Enable short Thursday strategy
        crash_day_enabled: Enable intraday crash detection
        crash_day_threshold: Threshold for intraday crash signal
        pump_day_enabled: Enable intraday pump detection
        pump_day_threshold: Threshold for intraday pump signal
        ten_am_dump_enabled: Enable 10 AM dump strategy (daily 9:35-10:30)
        max_position_pct: Max percentage of cash per trade (1-100)
        max_position_usd: Max dollar amount per trade (optional)
        notification_config: Optional notification configuration
        approval_mode: "required", "notify_only", or "auto_execute"
        approval_timeout_minutes: Minutes to wait for Telegram approval

    Returns:
        Configured TradingBot instance
    """
    strategy_config = StrategyConfig(
        mean_reversion_enabled=mean_reversion_enabled,
        mean_reversion_threshold=mean_reversion_threshold,
        short_thursday_enabled=short_thursday_enabled,
        crash_day_enabled=crash_day_enabled,
        crash_day_threshold=crash_day_threshold,
        pump_day_enabled=pump_day_enabled,
        pump_day_threshold=pump_day_threshold,
        ten_am_dump_enabled=ten_am_dump_enabled,
    )

    # Parse approval mode
    approval_mode_enum = ApprovalMode.REQUIRED
    if approval_mode == "notify_only":
        approval_mode_enum = ApprovalMode.NOTIFY_ONLY
    elif approval_mode == "auto_execute":
        approval_mode_enum = ApprovalMode.AUTO_EXECUTE

    bot_config = BotConfig(
        strategy=strategy_config,
        mode=TradingMode.LIVE if mode == "live" else TradingMode.PAPER,
        max_position_pct=max_position_pct,
        max_position_usd=max_position_usd,
        account_id_key=account_id_key,
        notifications=notification_config or NotificationConfig(),
        approval_mode=approval_mode_enum,
        approval_timeout_minutes=approval_timeout_minutes,
    )

    # Create notification manager from config
    notifications = NotificationManager(bot_config.notifications)

    return TradingBot(config=bot_config, client=etrade_client, notifications=notifications)
