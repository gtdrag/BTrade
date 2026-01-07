"""
Trade execution mixin for TradingBot.

Provides methods for executing trades in paper and live modes.
"""

import logging
from typing import TYPE_CHECKING, Optional

from ..etrade_client import ETradeAPIError, ETradeAuthError
from ..smart_strategy import Signal, TodaySignal
from ..telegram_bot import ApprovalResult
from ..utils import get_et_now
from .config import ApprovalMode, TradeResult

if TYPE_CHECKING:
    from .core import TradingBot

logger = logging.getLogger(__name__)


class ExecutionMixin:
    """
    Mixin providing trade execution methods.

    Requires from base class:
    - client: Optional[ETradeClient]
    - config: BotConfig
    - db: Database
    - telegram: TelegramNotifier
    - notifications: NotificationManager
    - hedge_manager: HedgeManager
    - is_paper_mode: bool (property)
    - _paper_positions: Dict[str, Dict]
    - _paper_capital: float
    - _position_lock: threading.RLock
    - get_quote(symbol): Dict[str, float]
    - get_today_signal(): TodaySignal
    - get_open_positions(): Dict[str, Dict]
    - close_all_positions(reason): List[TradeResult]
    - calculate_position_size(price): int
    - _check_duplicate_trade(signal_type): bool
    - _record_trade(signal_type): None
    - _wait_for_order_fill(order_id): Optional[Dict]
    - _notify_trade(result, signal): None
    - _notify_error(error): None
    - _log_trade(result, signal): None
    """

    def execute_signal(
        self: "TradingBot",
        signal: Optional[TodaySignal] = None,
        skip_approval: bool = False,
    ) -> TradeResult:
        """
        Execute today's trading signal with optional Telegram approval.

        Args:
            signal: Optional pre-fetched signal. If None, fetches current signal.
            skip_approval: If True, bypass approval and auto-execute (for time-sensitive
                          signals like crash/pump days). Still sends notification.

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
            # Check if we have existing positions
            open_positions = self.get_open_positions()
            needs_reversal = False
            if open_positions:
                if etf not in open_positions:
                    # We need to close existing position(s) and enter new one
                    needs_reversal = True
                    logger.info(
                        f"New signal ({signal.signal.value}) will require closing "
                        f"existing position(s): {list(open_positions.keys())}"
                    )
                else:
                    # We already hold the ETF we want to buy - skip
                    logger.info(f"Already holding {etf} - no action needed")
                    return TradeResult(
                        success=True,
                        signal=signal.signal,
                        etf=etf,
                        action="HOLD",
                        error=f"Already holding {etf}",
                        is_paper=self.is_paper_mode,
                    )

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

            # Handle approval based on mode
            approval_result = self._handle_approval(
                signal,
                etf,
                shares,
                price,
                position_value,
                needs_reversal,
                open_positions,
                skip_approval,
            )

            if approval_result is not None:
                return approval_result

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

    def _handle_approval(
        self: "TradingBot",
        signal: TodaySignal,
        etf: str,
        shares: int,
        price: float,
        position_value: float,
        needs_reversal: bool,
        open_positions: dict,
        skip_approval: bool,
    ) -> Optional[TradeResult]:
        """
        Handle trade approval logic.

        Returns TradeResult if trade should be blocked, None if it should proceed.
        """
        # Request Telegram approval if required (unless skip_approval for emergency trades)
        if self.config.approval_mode == ApprovalMode.REQUIRED and not skip_approval:
            return self._request_telegram_approval(
                signal, etf, shares, price, position_value, needs_reversal, open_positions
            )

        elif skip_approval:
            # Emergency auto-execute (crash/pump day) - notify but don't wait
            reversal_msg = ""
            if needs_reversal:
                reversal_msg = f"\n⚠️ Closing existing {list(open_positions.keys())} first!"
            self.telegram.send_message(
                f"🚨 *AUTO-EXECUTING EMERGENCY TRADE*\n\n"
                f"Signal: {signal.signal.value}\n"
                f"ETF: {etf}\n"
                f"Shares: {shares}\n"
                f"Price: ${price:.2f}\n"
                f"Total: ${position_value:.2f}{reversal_msg}\n\n"
                f"⚡ Time-sensitive signal - executing immediately"
            )
            logger.info(f"Emergency auto-execute: {signal.signal.value} - {shares} {etf}")

            # Close existing positions if this is a reversal
            if needs_reversal:
                self.close_all_positions(reason=f"Emergency reversal for {signal.signal.value}")

        elif self.config.approval_mode == ApprovalMode.NOTIFY_ONLY:
            # Send notification but don't wait for response
            reversal_msg = ""
            if needs_reversal:
                reversal_msg = f"\n⚠️ Closing existing {list(open_positions.keys())} first!"
            self.telegram.send_message(
                f"📊 *TRADE EXECUTING*\n\n"
                f"Signal: {signal.signal.value}\n"
                f"ETF: {etf}\n"
                f"Shares: {shares}\n"
                f"Price: ${price:.2f}\n"
                f"Total: ${position_value:.2f}{reversal_msg}"
            )

            # Close existing positions if this is a reversal
            if needs_reversal:
                self.close_all_positions(reason=f"Reversal for {signal.signal.value} signal")

        else:
            # AUTO_EXECUTE mode - no approval or notification
            # Still need to handle reversals
            if needs_reversal:
                self.close_all_positions(reason=f"Auto reversal for {signal.signal.value} signal")

        return None  # Continue with trade execution

    def _request_telegram_approval(
        self: "TradingBot",
        signal: TodaySignal,
        etf: str,
        shares: int,
        price: float,
        position_value: float,
        needs_reversal: bool,
        open_positions: dict,
    ) -> Optional[TradeResult]:
        """Request Telegram approval and return result if trade should be blocked."""
        logger.info(f"Requesting Telegram approval for {signal.signal.value}: {shares} {etf}")

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

        # Include reversal info in approval request if applicable
        reversal_note = ""
        if needs_reversal:
            reversal_note = f"\n⚠️ Will CLOSE existing {list(open_positions.keys())} first!"

        approval = self.telegram.request_approval(
            signal_type=signal.signal.value,
            etf=etf,
            reason=signal.reason + reversal_note,
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
                    error="Telegram error - live trade blocked for safety. "
                    "Check Telegram connectivity.",
                    is_paper=False,
                )

        logger.info("Trade approved via Telegram")

        # Close existing positions if this is a reversal (AFTER approval)
        if needs_reversal:
            self.db.log_event(
                "SIGNAL_REVERSAL",
                f"Executing reversal: closing {list(open_positions.keys())} "
                f"for {signal.signal.value}",
                {
                    "new_signal": signal.signal.value,
                    "new_etf": etf,
                    "existing_positions": list(open_positions.keys()),
                    "timestamp": get_et_now().isoformat(),
                },
            )
            close_results = self.close_all_positions(
                reason=f"Approved reversal for {signal.signal.value} signal"
            )
            for close_result in close_results:
                if close_result.success:
                    self.telegram.send_message(
                        f"🔄 *Position Closed*\n\n"
                        f"Sold: {close_result.shares} {close_result.etf} "
                        f"@ ${close_result.price:.2f}\n"
                        f"Now entering: {etf}"
                    )

        return None  # Continue with trade execution

    def _execute_paper_trade(
        self: "TradingBot",
        etf: str,
        shares: int,
        price: float,
        signal: TodaySignal,
    ) -> TradeResult:
        """
        Execute a paper trade.

        Thread-safe: Uses position lock to prevent concurrent modifications.
        """
        total_value = shares * price

        with self._position_lock:
            # Update paper capital
            self._paper_capital -= total_value

            # Track position
            self._paper_positions[etf] = {
                "shares": shares,
                "entry_price": price,
                "entry_time": get_et_now(),
                "signal": signal.signal.value,
            }

        # Register with hedge manager for trailing hedge tracking (outside lock)
        self.hedge_manager.register_position(
            instrument=etf,
            shares=shares,
            entry_price=price,
        )

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

    def _execute_live_trade(
        self: "TradingBot", etf: str, shares: int, signal: TodaySignal
    ) -> TradeResult:
        """Execute a live trade via E*TRADE."""
        if not self.client:
            raise ETradeAuthError("E*TRADE client not configured")

        # Ensure token is fresh before starting preview+place sequence
        # This prevents token expiry between preview and place calls
        if not self.client.ensure_authenticated():
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

            # Poll for actual fill price (fall back to estimate if timeout)
            fill_info = self._wait_for_order_fill(order_id)
            if fill_info:
                fill_price = fill_info["avg_price"]
                filled_shares = fill_info["filled_qty"]
                total_value = fill_price * filled_shares

                # Check for partial fill
                if filled_shares < shares:
                    logger.warning(
                        f"PARTIAL FILL: Ordered {shares} {etf}, only {filled_shares} filled"
                    )
                    self.db.log_event(
                        "PARTIAL_FILL",
                        order_id=order_id,
                        symbol=etf,
                        requested=shares,
                        filled=filled_shares,
                        shortfall=shares - filled_shares,
                        action="BUY",
                    )
                    self.telegram.notify_error(
                        "Partial Fill Warning",
                        f"Ordered {shares} {etf}, only {filled_shares} filled @ ${fill_price:.2f}",
                    )
            else:
                # Fall back to estimate if polling fails - ALERT USER
                logger.warning(f"Using estimated fill price for order {order_id}")
                fill_price = estimated_value / shares if shares > 0 else 0
                filled_shares = shares
                total_value = estimated_value
                # Alert user that we're using estimated price (actual may differ)
                self.telegram.notify_error(
                    "Fill Price Unconfirmed",
                    f"Order {order_id}: Using estimated price ${fill_price:.2f}. "
                    f"Check E*TRADE for actual fill price.",
                )

            # Register with hedge manager for trailing hedge tracking
            self.hedge_manager.register_position(
                instrument=etf,
                shares=filled_shares,
                entry_price=fill_price,
            )

            logger.info(
                f"[LIVE] Bought {filled_shares} {etf} @ ${fill_price:.2f} - Order ID: {order_id}"
            )

            return TradeResult(
                success=True,
                signal=signal.signal,
                etf=etf,
                action="BUY",
                shares=filled_shares,
                price=fill_price,
                total_value=total_value,
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
