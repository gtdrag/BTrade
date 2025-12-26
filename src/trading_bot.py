"""
Trading Bot - Integration Layer

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
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from .data_providers import MarketDataManager, create_data_manager
from .database import Database, get_database
from .etrade_client import ETradeAPIError, ETradeAuthError, ETradeClient
from .notifications import NotificationConfig, NotificationManager, NotificationType
from .smart_strategy import Signal, SmartStrategy, StrategyConfig, TodaySignal
from .telegram_bot import ApprovalResult, TelegramNotifier
from .utils import get_et_now

logger = logging.getLogger(__name__)


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


class TradingBot:
    """
    Main trading bot integrating strategy, broker, and notifications.

    Workflow:
    1. Get today's signal from SmartStrategy
    2. If signal exists, execute trade via E*TRADE (or paper trade)
    3. Send notifications
    4. Log to database
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

    @property
    def is_paper_mode(self) -> bool:
        return self.config.mode == TradingMode.PAPER

    def _check_duplicate_trade(self, signal_type: str) -> bool:
        """Check if we've already traded this signal type today."""
        today = get_et_now().strftime("%Y-%m-%d")

        # Clear stale entries from previous days
        stale_keys = [k for k, v in self._trades_today.items() if not v.startswith(today)]
        for k in stale_keys:
            del self._trades_today[k]

        return signal_type in self._trades_today

    def _record_trade(self, signal_type: str):
        """Record that we've traded this signal type today."""
        self._trades_today[signal_type] = get_et_now().isoformat()

    def get_today_signal(self) -> TodaySignal:
        """Get today's trading signal."""
        return self.strategy.get_today_signal()

    def get_available_capital(self) -> float:
        """Get available capital for trading."""
        if self.is_paper_mode:
            return self._paper_capital

        if not self.client or not self.client.is_authenticated():
            raise ETradeAuthError("E*TRADE client not authenticated")

        return self.client.get_cash_available(self.config.account_id_key)

    def get_portfolio_value(self) -> Dict[str, Any]:
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

        # Live mode - use E*TRADE
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
            days_gain_pct = (
                (total_days_gain / (total_position_value - total_days_gain) * 100)
                if (total_position_value - total_days_gain) > 0
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

    def calculate_position_size(self, price: float) -> int:
        """Calculate number of shares to buy."""
        capital = self.get_available_capital()

        # Apply position limits
        max_capital = capital * (self.config.max_position_pct / 100)
        if self.config.max_position_usd:
            max_capital = min(max_capital, self.config.max_position_usd)

        shares = int(max_capital // price)
        return max(0, shares)

    def get_quote(self, symbol: str) -> Dict[str, float]:
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

    def execute_signal(self, signal: Optional[TodaySignal] = None) -> TradeResult:
        """
        Execute today's trading signal with optional Telegram approval.

        Args:
            signal: Optional pre-fetched signal. If None, fetches current signal.

        Returns:
            TradeResult with execution details
        """
        if signal is None:
            signal = self.get_today_signal()

        # No trade if CASH signal
        if signal.signal == Signal.CASH:
            logger.info(f"No trade today: {signal.reason}")
            return TradeResult(
                success=True,
                signal=signal.signal,
                etf="CASH",
                action="NONE",
                is_paper=self.is_paper_mode,
            )

        # Check for duplicate trade (already traded this signal type today)
        if self._check_duplicate_trade(signal.signal.value):
            logger.warning(f"Duplicate trade blocked: Already traded {signal.signal.value} today")
            self.db.log_event(
                "DUPLICATE_BLOCKED",
                f"Blocked duplicate {signal.signal.value} trade",
                {
                    "signal": signal.signal.value,
                    "etf": signal.etf,
                    "timestamp": get_et_now().isoformat(),
                    "previous_trade": self._trades_today.get(signal.signal.value),
                },
            )
            return TradeResult(
                success=False,
                signal=signal.signal,
                etf=signal.etf,
                action="BUY",
                error=f"Already traded {signal.signal.value} today - duplicate blocked",
                is_paper=self.is_paper_mode,
            )

        etf = signal.etf

        try:
            # Get quote
            quote = self.get_quote(etf)
            price = quote["current_price"]

            if price <= 0:
                raise ValueError(f"Invalid price for {etf}: {price}")

            # Calculate position size
            shares = self.calculate_position_size(price)
            position_value = shares * price

            if shares <= 0:
                return TradeResult(
                    success=False,
                    signal=signal.signal,
                    etf=etf,
                    action="BUY",
                    error="Insufficient capital for trade",
                )

            # Request Telegram approval if required
            if self.config.approval_mode == ApprovalMode.REQUIRED:
                logger.info(
                    f"Requesting Telegram approval for {signal.signal.value}: {shares} {etf}"
                )

                # Log approval request
                self.db.log_event(
                    "APPROVAL_REQUEST",
                    f"Requesting approval for {signal.signal.value}",
                    {
                        "signal": signal.signal.value,
                        "etf": etf,
                        "shares": shares,
                        "price": price,
                        "position_value": position_value,
                        "reason": signal.reason,
                        "timestamp": get_et_now().isoformat(),
                    },
                )

                approval = self.telegram.request_approval(
                    signal_type=signal.signal.value,
                    etf=etf,
                    reason=signal.reason,
                    shares=shares,
                    price=price,
                    position_value=position_value,
                )

                # Log approval response
                self.db.log_event(
                    "APPROVAL_RESPONSE",
                    f"User response: {approval.value}",
                    {
                        "signal": signal.signal.value,
                        "etf": etf,
                        "shares": shares,
                        "response": approval.value,
                        "timestamp": get_et_now().isoformat(),
                    },
                )

                if approval == ApprovalResult.REJECTED:
                    logger.info("Trade rejected by user")
                    return TradeResult(
                        success=False,
                        signal=signal.signal,
                        etf=etf,
                        action="BUY",
                        shares=shares,
                        price=price,
                        error="Trade rejected by user via Telegram",
                        is_paper=self.is_paper_mode,
                    )
                elif approval == ApprovalResult.TIMEOUT:
                    logger.info("Approval timed out - trade skipped")
                    return TradeResult(
                        success=False,
                        signal=signal.signal,
                        etf=etf,
                        action="BUY",
                        shares=shares,
                        price=price,
                        error="Approval timeout - no response received",
                        is_paper=self.is_paper_mode,
                    )
                elif approval == ApprovalResult.ERROR:
                    if self.is_paper_mode:
                        logger.warning("Telegram approval error - proceeding with paper trade")
                        # Fail-open for paper mode only (no real money at risk)
                    else:
                        # FAIL-SECURE: Never execute live trades without proper approval
                        logger.error("Telegram approval error - BLOCKING live trade for safety")
                        return TradeResult(
                            success=False,
                            signal=signal.signal,
                            etf=etf,
                            action="BUY",
                            shares=shares,
                            price=price,
                            error="Telegram error - live trade blocked for safety. Check Telegram connectivity.",
                            is_paper=False,
                        )

                logger.info("Trade approved via Telegram")

            elif self.config.approval_mode == ApprovalMode.NOTIFY_ONLY:
                # Send notification but don't wait for response
                self.telegram.send_message(
                    f"📊 *TRADE EXECUTING*\n\n"
                    f"Signal: {signal.signal.value}\n"
                    f"ETF: {etf}\n"
                    f"Shares: {shares}\n"
                    f"Price: ${price:.2f}\n"
                    f"Total: ${position_value:.2f}"
                )

            # Execute trade
            if self.is_paper_mode:
                result = self._execute_paper_trade(etf, shares, price, signal)
            else:
                result = self._execute_live_trade(etf, shares, signal)

            # Send notification (email/desktop)
            if result.success:
                # Record this trade to prevent duplicates
                self._record_trade(signal.signal.value)

                self._notify_trade(result, signal)
                # Also send Telegram confirmation
                self.telegram.notify_trade_executed(
                    signal_type=signal.signal.value,
                    etf=etf,
                    action="BUY",
                    shares=shares,
                    price=result.price,
                    total=result.total_value,
                )
            else:
                self._notify_error(result.error or "Trade failed")
                self.telegram.notify_error("Trade Execution", result.error or "Trade failed")

            # Log to database
            self._log_trade(result, signal)

            return result

        except Exception as e:
            logger.error(f"Trade execution failed: {e}")
            self._notify_error(str(e))
            self.telegram.notify_error("Trade Execution", str(e))
            return TradeResult(
                success=False,
                signal=signal.signal,
                etf=etf,
                action="BUY",
                error=str(e),
                is_paper=self.is_paper_mode,
            )

    def _execute_paper_trade(
        self, etf: str, shares: int, price: float, signal: TodaySignal
    ) -> TradeResult:
        """Execute a paper trade."""
        total_value = shares * price

        # Update paper capital
        self._paper_capital -= total_value

        # Track position
        self._paper_positions[etf] = {
            "shares": shares,
            "entry_price": price,
            "entry_time": get_et_now(),
            "signal": signal.signal.value,
        }

        logger.info(f"[PAPER] Bought {shares} {etf} @ ${price:.2f} = ${total_value:.2f}")

        return TradeResult(
            success=True,
            signal=signal.signal,
            etf=etf,
            action="BUY",
            shares=shares,
            price=price,
            total_value=total_value,
            order_id=f"PAPER-{get_et_now().strftime('%Y%m%d%H%M%S')}",
            is_paper=True,
        )

    def _execute_live_trade(self, etf: str, shares: int, signal: TodaySignal) -> TradeResult:
        """Execute a live trade via E*TRADE."""
        if not self.client or not self.client.is_authenticated():
            raise ETradeAuthError("E*TRADE client not authenticated")

        try:
            # Preview order first
            preview = self.client.preview_order(
                account_id_key=self.config.account_id_key,
                symbol=etf,
                action="BUY",
                quantity=shares,
                order_type="MARKET",
            )

            # Get estimated price from preview
            estimated_value = float(preview.get("Order", [{}])[0].get("estimatedTotalAmount", 0))

            # Place order
            order_response = self.client.place_order(
                account_id_key=self.config.account_id_key,
                symbol=etf,
                action="BUY",
                quantity=shares,
                order_type="MARKET",
                preview_ids=preview.get("PreviewIds", []),
            )

            order_id = str(order_response.get("OrderId", ""))

            # Get fill price (may need to poll for fill)
            fill_price = estimated_value / shares if shares > 0 else 0

            logger.info(f"[LIVE] Bought {shares} {etf} - Order ID: {order_id}")

            return TradeResult(
                success=True,
                signal=signal.signal,
                etf=etf,
                action="BUY",
                shares=shares,
                price=fill_price,
                total_value=estimated_value,
                order_id=order_id,
                is_paper=False,
            )

        except ETradeAPIError as e:
            logger.error(f"E*TRADE API error: {e}")
            return TradeResult(
                success=False,
                signal=signal.signal,
                etf=etf,
                action="BUY",
                error=str(e),
                is_paper=False,
            )

    def close_position(self, etf: str) -> TradeResult:
        """Close an open position (sell at market)."""
        if self.is_paper_mode:
            return self._close_paper_position(etf)
        else:
            return self._close_live_position(etf)

    def _close_paper_position(self, etf: str) -> TradeResult:
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

        logger.info(
            f"[PAPER] Sold {shares} {etf} @ ${exit_price:.2f} = ${total_value:.2f} (P&L: ${pnl:+.2f})"
        )

        # Send Telegram notification for position closed
        try:
            import asyncio

            from .telegram_bot import TelegramBot

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

            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            loop.run_until_complete(_notify())
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

    def _close_live_position(self, etf: str) -> TradeResult:
        """Close a live position via E*TRADE."""
        if not self.client:
            raise ETradeAuthError("E*TRADE client not configured")

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
            # Place sell order
            order_response = self.client.place_order(
                account_id_key=self.config.account_id_key,
                symbol=etf,
                action="SELL",
                quantity=shares,
                order_type="MARKET",
            )

            order_id = str(order_response.get("OrderId", ""))

            logger.info(f"[LIVE] Sold {shares} {etf} - Order ID: {order_id}")

            return TradeResult(
                success=True,
                signal=Signal.CASH,
                etf=etf,
                action="SELL",
                shares=shares,
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

    def _notify_trade(self, result: TradeResult, signal: TodaySignal):
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

    def _notify_error(self, error: str):
        """Send error notification."""
        if not self.notifications:
            return

        self.notifications.send("Trading Bot Error", error, NotificationType.ERROR)

    def _log_trade(self, result: TradeResult, signal: TodaySignal):
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
                "btc_overnight_pct": signal.btc_overnight.change_pct
                if signal.btc_overnight
                else None,
            },
        )

    def get_status(self) -> Dict[str, Any]:
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


def create_trading_bot(
    mode: str = "paper",
    etrade_client: Optional[ETradeClient] = None,
    account_id_key: str = "",
    mean_reversion_threshold: float = -2.0,
    mean_reversion_enabled: bool = True,
    short_thursday_enabled: bool = True,
    crash_day_enabled: bool = True,
    crash_day_threshold: float = -2.0,
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
