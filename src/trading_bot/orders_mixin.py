"""
Order management mixin for TradingBot.

Provides methods for order tracking, fill polling, and duplicate prevention.
"""

import logging
import time
from typing import TYPE_CHECKING, Any, Dict, Optional

from ..utils import get_et_now

if TYPE_CHECKING:
    from .core import TradingBot

logger = logging.getLogger(__name__)


class OrdersMixin:
    """
    Mixin providing order management methods.

    Requires from base class:
    - client: Optional[ETradeClient]
    - config: BotConfig
    - _trades_today: Dict[str, str]
    """

    def _wait_for_order_fill(
        self: "TradingBot",
        order_id: str,
        timeout_seconds: int = 30,
        poll_interval: float = 0.5,
    ) -> Optional[Dict[str, Any]]:
        """
        Poll order status until filled or timeout.

        Args:
            order_id: The order ID to check
            timeout_seconds: Max time to wait for fill
            poll_interval: Seconds between status checks

        Returns:
            Dict with 'filled_qty' and 'avg_price' if filled, None if timeout/error
        """
        if not self.client:
            return None

        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            try:
                status = self.client.get_order_status(self.config.account_id_key, order_id)

                # Navigate E*TRADE response structure
                orders = status.get("Order", [])
                if not orders:
                    time.sleep(poll_interval)
                    continue

                order = orders[0]
                order_status = order.get("OrderDetail", [{}])[0].get("status", "")

                if order_status in ("EXECUTED", "FILLED"):
                    # Extract fill details
                    order_detail = order.get("OrderDetail", [{}])[0]
                    instrument = order_detail.get("Instrument", [{}])[0]

                    filled_qty = int(instrument.get("filledQuantity", 0))
                    avg_price = float(instrument.get("averageExecutionPrice", 0))

                    if filled_qty > 0 and avg_price > 0:
                        logger.info(
                            f"Order {order_id} filled: {filled_qty} shares @ ${avg_price:.2f}"
                        )
                        return {"filled_qty": filled_qty, "avg_price": avg_price}

                elif order_status in ("CANCELLED", "REJECTED", "EXPIRED"):
                    logger.warning(f"Order {order_id} not filled: {order_status}")
                    return None

                # Still pending, wait and retry
                time.sleep(poll_interval)

            except Exception as e:
                logger.warning(f"Error polling order status: {e}")
                time.sleep(poll_interval)

        logger.warning(f"Order {order_id} fill check timed out after {timeout_seconds}s")
        return None

    def _check_duplicate_trade(self: "TradingBot", signal_type: str) -> bool:
        """Check if we've already traded this signal type today."""
        today = get_et_now().strftime("%Y-%m-%d")

        # Clear stale entries from previous days
        stale_keys = [k for k, v in self._trades_today.items() if not v.startswith(today)]
        for k in stale_keys:
            del self._trades_today[k]

        return signal_type in self._trades_today

    def _record_trade(self: "TradingBot", signal_type: str):
        """Record that we've traded this signal type today."""
        self._trades_today[signal_type] = get_et_now().isoformat()
