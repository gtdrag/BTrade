"""
Position management mixin for TradingBot.

Provides methods for checking, getting, and closing positions.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List

from ..etrade_client import ETradeAPIError, ETradeAuthError
from ..smart_strategy import Signal
from ..utils import get_et_now
from .config import TradeResult

if TYPE_CHECKING:
    from .core import TradingBot

logger = logging.getLogger(__name__)


class PositionsMixin:
    """
    Mixin providing position management methods.

    Requires from base class:
    - client: Optional[ETradeClient]
    - config: BotConfig
    - db: Database
    - data_manager: MarketDataManager
    - telegram: TelegramNotifier
    - hedge_manager: HedgeManager
    - is_paper_mode: bool (property)
    - _paper_positions: Dict[str, Dict]
    - _paper_capital: float
    - _position_lock: threading.RLock
    - get_quote(symbol): Dict[str, float]
    """

    def has_open_position(self: "TradingBot") -> bool:
        """Check if we have any open positions."""
        if self.is_paper_mode:
            return len(self._paper_positions) > 0
        else:
            if not self.client or not self.client.is_authenticated():
                return False
            positions = self.client.get_account_positions(self.config.account_id_key)
            # Check for positions in our tradeable ETFs
            for pos in positions:
                symbol = pos.get("Product", {}).get("symbol", "")
                if symbol in ["BITU", "SBIT", "BITX"]:
                    return True
            return False

    def get_open_positions(self: "TradingBot") -> Dict[str, Dict]:
        """Get all open positions in tradeable ETFs."""
        if self.is_paper_mode:
            return self._paper_positions.copy()
        else:
            if not self.client or not self.client.is_authenticated():
                return {}
            positions = self.client.get_account_positions(self.config.account_id_key)
            result = {}
            for pos in positions:
                symbol = pos.get("Product", {}).get("symbol", "")
                if symbol in ["BITU", "SBIT", "BITX"]:
                    result[symbol] = {
                        "shares": int(pos.get("quantity", 0)),
                        "entry_price": float(pos.get("costBasis", 0)) / int(pos.get("quantity", 1)),
                    }
            return result

    def close_all_positions(self: "TradingBot", reason: str = "New signal") -> List[TradeResult]:
        """
        Close all open positions before entering a new trade.

        Thread-safe: Uses position lock to prevent concurrent modifications.
        """
        results = []

        with self._position_lock:
            # Get positions under lock to ensure consistent view
            positions = self.get_open_positions()

            for etf in list(positions.keys()):
                logger.info(f"Closing existing {etf} position before new trade: {reason}")
                self.db.log_event(
                    "POSITION_CLOSE",
                    f"Closing {etf} for new signal",
                    {"etf": etf, "reason": reason, "timestamp": get_et_now().isoformat()},
                )
                # Note: close_position also acquires _position_lock, but RLock allows re-entry
                result = self.close_position(etf)
                results.append(result)

        return results

    def close_position(self: "TradingBot", etf: str) -> TradeResult:
        """Close an open position (sell at market).

        Thread-safe: Uses position lock to prevent concurrent modifications.
        """
        with self._position_lock:
            if self.is_paper_mode:
                return self._close_paper_position(etf)
            else:
                return self._close_live_position(etf)

    def _close_paper_position(self: "TradingBot", etf: str) -> TradeResult:
        """Close a paper position."""
        if etf not in self._paper_positions:
            return TradeResult(
                success=False,
                signal=Signal.CASH,
                etf=etf,
                action="SELL",
                error=f"No open position in {etf}",
                is_paper=True,
            )

        position = self._paper_positions[etf]
        shares = position["shares"]
        entry_price = position["entry_price"]

        # Get current price
        quote = self.get_quote(etf)
        exit_price = quote["current_price"]
        total_value = shares * exit_price

        # Calculate P&L
        pnl = (exit_price - entry_price) * shares
        pnl_pct = ((exit_price - entry_price) / entry_price) * 100

        # Update capital
        self._paper_capital += total_value

        # Remove position
        del self._paper_positions[etf]

        # Clear hedge manager tracking
        self.hedge_manager.clear_position()

        logger.info(
            f"[PAPER] Sold {shares} {etf} @ ${exit_price:.2f} = ${total_value:.2f} "
            f"(P&L: ${pnl:+.2f})"
        )

        # Send Telegram notification for position closed
        try:
            from ..telegram_bot import TelegramBot
            from ..utils import run_async

            async def _notify():
                bot = TelegramBot()
                await bot.initialize()
                await bot.send_position_closed(
                    etf=etf,
                    shares=shares,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                )

            run_async(_notify())
        except Exception as e:
            logger.warning(f"Failed to send Telegram position closed notification: {e}")

        return TradeResult(
            success=True,
            signal=Signal.CASH,
            etf=etf,
            action="SELL",
            shares=shares,
            price=exit_price,
            total_value=total_value,
            order_id=f"PAPER-SELL-{get_et_now().strftime('%Y%m%d%H%M%S')}",
            is_paper=True,
        )

    def _close_live_position(self: "TradingBot", etf: str) -> TradeResult:
        """Close a live position via E*TRADE."""
        if not self.client:
            raise ETradeAuthError("E*TRADE client not configured")

        # Ensure token is fresh before starting preview+place sequence
        if not self.client.ensure_authenticated():
            raise ETradeAuthError("E*TRADE client not authenticated")

        # Get current positions
        positions = self.client.get_account_positions(self.config.account_id_key)

        # Find position in ETF
        etf_position = None
        for pos in positions:
            if pos.get("Product", {}).get("symbol") == etf:
                etf_position = pos
                break

        if not etf_position:
            return TradeResult(
                success=False,
                signal=Signal.CASH,
                etf=etf,
                action="SELL",
                error=f"No position found in {etf}",
                is_paper=False,
            )

        shares = int(etf_position.get("quantity", 0))

        try:
            # Preview order first (required for production)
            preview = self.client.preview_order(
                account_id_key=self.config.account_id_key,
                symbol=etf,
                action="SELL",
                quantity=shares,
                order_type="MARKET",
            )

            # Place sell order with preview IDs
            order_response = self.client.place_order(
                account_id_key=self.config.account_id_key,
                symbol=etf,
                action="SELL",
                quantity=shares,
                order_type="MARKET",
                preview_ids=preview.get("PreviewIds", []),
            )

            order_id = str(order_response.get("OrderId", ""))

            # Poll for actual fill confirmation
            fill_info = self._wait_for_order_fill(order_id)
            if fill_info:
                filled_shares = fill_info["filled_qty"]
                fill_price = fill_info["avg_price"]

                # Check for partial fill
                if filled_shares < shares:
                    logger.warning(
                        f"PARTIAL FILL: Ordered SELL {shares} {etf}, only {filled_shares} filled"
                    )
                    self.db.log_event(
                        "PARTIAL_FILL",
                        order_id=order_id,
                        symbol=etf,
                        requested=shares,
                        filled=filled_shares,
                        shortfall=shares - filled_shares,
                        action="SELL",
                    )
                    self.telegram.notify_error(
                        "Partial Fill Warning",
                        f"Sell order: {shares} {etf} requested, only {filled_shares} filled",
                    )
            else:
                # Fall back to requested shares if polling fails - ALERT USER
                logger.warning(f"Using requested shares for sell order {order_id}")
                filled_shares = shares
                fill_price = 0.0  # Unknown - user should check E*TRADE
                # Alert user that we couldn't confirm the fill
                self.telegram.notify_error(
                    "Sell Fill Unconfirmed",
                    f"Order {order_id}: Could not confirm fill for {shares} {etf}. "
                    f"Check E*TRADE for actual execution.",
                )

            # Clear hedge manager tracking
            self.hedge_manager.clear_position()

            logger.info(
                f"[LIVE] Sold {filled_shares} {etf} @ ${fill_price:.2f} - Order ID: {order_id}"
            )

            return TradeResult(
                success=True,
                signal=Signal.CASH,
                etf=etf,
                action="SELL",
                shares=filled_shares,
                price=fill_price,
                total_value=filled_shares * fill_price,
                order_id=order_id,
                is_paper=False,
            )

        except ETradeAPIError as e:
            return TradeResult(
                success=False,
                signal=Signal.CASH,
                etf=etf,
                action="SELL",
                error=str(e),
                is_paper=False,
            )

    def get_portfolio_value(self: "TradingBot") -> Dict[str, Any]:
        """
        Get real-time portfolio value with unrealized P&L.

        Returns dict with:
            cash: Available cash
            positions: List of positions with current value
            total_value: Cash + position values
            unrealized_pnl: Total unrealized profit/loss
            unrealized_pnl_pct: Unrealized P&L as percentage
        """
        if self.is_paper_mode:
            return self._get_paper_portfolio_value()
        else:
            return self._get_live_portfolio_value()

    def _get_paper_portfolio_value(self: "TradingBot") -> Dict[str, Any]:
        """Get paper trading portfolio value."""
        cash = self._paper_capital
        positions = []
        total_position_value = 0.0
        total_cost_basis = 0.0

        for symbol, pos in self._paper_positions.items():
            shares = pos.get("shares", 0)
            entry_price = pos.get("entry_price", 0)
            cost_basis = shares * entry_price

            # Get current price
            quote = self.data_manager.get_quote(symbol)
            current_price = quote.current_price if quote else entry_price
            current_value = shares * current_price
            unrealized_pnl = current_value - cost_basis
            unrealized_pnl_pct = (unrealized_pnl / cost_basis * 100) if cost_basis > 0 else 0

            positions.append(
                {
                    "symbol": symbol,
                    "shares": shares,
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "cost_basis": cost_basis,
                    "current_value": current_value,
                    "unrealized_pnl": unrealized_pnl,
                    "unrealized_pnl_pct": unrealized_pnl_pct,
                    "source": quote.source.value if quote else "unknown",
                }
            )

            total_position_value += current_value
            total_cost_basis += cost_basis

        total_value = cash + total_position_value
        total_unrealized_pnl = total_position_value - total_cost_basis
        starting_capital = 10000.0  # Initial paper capital
        total_pnl = total_value - starting_capital
        total_pnl_pct = total_pnl / starting_capital * 100

        return {
            "cash": cash,
            "positions": positions,
            "total_position_value": total_position_value,
            "total_value": total_value,
            "unrealized_pnl": total_unrealized_pnl,
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
            "starting_capital": starting_capital,
        }

    def _get_live_portfolio_value(self: "TradingBot") -> Dict[str, Any]:
        """Get live E*TRADE portfolio value."""
        if not self.client or not self.client.is_authenticated():
            return {"error": "E*TRADE client not authenticated"}

        try:
            # Get cash available
            cash = self.client.get_cash_available(self.config.account_id_key)

            # Get positions
            raw_positions = self.client.get_account_positions(self.config.account_id_key)
            positions = []
            total_position_value = 0.0
            total_cost_basis = 0.0
            total_days_gain = 0.0

            for pos in raw_positions:
                symbol = pos.get("Product", {}).get("symbol", pos.get("symbolDescription", "?"))
                shares = int(pos.get("quantity", 0))
                cost_basis = float(pos.get("costBasis", 0) or pos.get("totalCost", 0) or 0)
                entry_price = cost_basis / shares if shares > 0 else 0
                current_value = float(pos.get("marketValue", 0) or 0)
                current_price = current_value / shares if shares > 0 else 0
                unrealized_pnl = float(pos.get("totalGain", 0) or 0)
                unrealized_pnl_pct = float(pos.get("totalGainPct", 0) or 0)
                # Day's gain (today's change)
                days_gain = float(pos.get("daysGain", 0) or 0)
                days_gain_pct = float(pos.get("daysGainPct", 0) or 0)

                positions.append(
                    {
                        "symbol": symbol,
                        "shares": shares,
                        "entry_price": entry_price,
                        "current_price": current_price,
                        "cost_basis": cost_basis,
                        "current_value": current_value,
                        "unrealized_pnl": unrealized_pnl,
                        "unrealized_pnl_pct": unrealized_pnl_pct,
                        "days_gain": days_gain,
                        "days_gain_pct": days_gain_pct,
                        "source": "etrade",
                    }
                )

                total_position_value += current_value
                total_cost_basis += cost_basis
                total_days_gain += days_gain

            total_value = cash + total_position_value
            total_unrealized_pnl = total_position_value - total_cost_basis

            # For live mode, we don't track starting capital the same way
            # Just show unrealized P&L from positions
            # Note: denominator is yesterday's value (current - today's gain)
            yesterdays_value = total_position_value - total_days_gain
            days_gain_pct = (
                (total_days_gain / yesterdays_value * 100)
                if abs(yesterdays_value) > 0.01  # Avoid division by zero/near-zero
                else 0
            )

            return {
                "cash": cash,
                "positions": positions,
                "total_position_value": total_position_value,
                "total_value": total_value,
                "unrealized_pnl": total_unrealized_pnl,
                "total_pnl": total_unrealized_pnl,
                "total_pnl_pct": (total_unrealized_pnl / total_cost_basis * 100)
                if total_cost_basis > 0
                else 0,
                "starting_capital": total_value,  # No fixed starting capital in live mode
                "days_gain": total_days_gain,
                "days_gain_pct": days_gain_pct,
            }

        except Exception as e:
            logger.error(f"Error fetching E*TRADE portfolio: {e}")
            return {"error": f"Failed to fetch portfolio: {e}"}
