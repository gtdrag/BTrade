"""
Notifications mixin for TradingBot.

Provides methods for sending notifications and logging trades.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict

from ..notifications import NotificationType
from ..smart_strategy import TodaySignal
from ..utils import get_et_now
from .config import TradeResult

if TYPE_CHECKING:
    from .core import TradingBot

logger = logging.getLogger(__name__)


class NotificationsMixin:
    """
    Mixin providing notification and logging methods.

    Requires from base class:
    - notifications: NotificationManager
    - db: Database
    - is_paper_mode: bool (property)
    - config: BotConfig
    - _paper_capital: float
    - _paper_positions: Dict[str, Dict]
    - client: Optional[ETradeClient]
    - get_today_signal(): TodaySignal
    - get_available_capital(): float
    """

    def _notify_trade(self: "TradingBot", result: TradeResult, signal: TodaySignal):
        """Send trade notification."""
        if not self.notifications:
            return

        mode = "[PAPER]" if result.is_paper else "[LIVE]"
        title = f"{mode} Trade Executed: {result.etf}"
        message = (
            f"Signal: {signal.signal.value}\n"
            f"Action: {result.action} {result.shares} shares\n"
            f"Price: ${result.price:.2f}\n"
            f"Total: ${result.total_value:.2f}\n"
            f"Reason: {signal.reason}"
        )

        self.notifications.send(title, message, NotificationType.TRADE)

    def _notify_error(self: "TradingBot", error: str):
        """Send error notification."""
        if not self.notifications:
            return

        self.notifications.send("Trading Bot Error", error, NotificationType.ERROR)

    def _log_trade(self: "TradingBot", result: TradeResult, signal: TodaySignal):
        """Log trade to database with comprehensive execution details."""
        now = get_et_now()
        self.db.log_event(
            level="TRADE_EXECUTION" if result.success else "TRADE_ERROR",
            event=f"{result.action} {result.shares} {result.etf}",
            details={
                "timestamp": now.isoformat(),
                "day_of_week": now.strftime("%A"),
                "signal": signal.signal.value,
                "etf": result.etf,
                "action": result.action,
                "shares": result.shares,
                "price": result.price,
                "total_value": result.total_value,
                "order_id": result.order_id,
                "is_paper": result.is_paper,
                "success": result.success,
                "error": result.error,
                "reason": signal.reason,
                "prev_day_return": signal.prev_day_return,
                "btc_overnight_pct": signal.btc_overnight.overnight_change_pct
                if signal.btc_overnight
                else None,
            },
        )

    def get_status(self: "TradingBot") -> Dict[str, Any]:
        """Get current bot status."""
        signal = self.get_today_signal()

        status = {
            "mode": self.config.mode.value,
            "today_signal": signal.signal.value,
            "signal_etf": signal.etf,
            "signal_reason": signal.reason,
            "timestamp": get_et_now().isoformat(),
        }

        if self.is_paper_mode:
            status["paper_capital"] = self._paper_capital
            status["paper_positions"] = self._paper_positions
        else:
            if self.client and self.client.is_authenticated():
                try:
                    status["cash_available"] = self.get_available_capital()
                    status["authenticated"] = True
                except Exception as e:
                    status["authenticated"] = False
                    status["auth_error"] = str(e)
            else:
                status["authenticated"] = False

        return status
