"""
Hedging and reversal mixin for TradingBot.

Provides methods for trailing hedges and loss reversal strategies.
"""

import logging
from typing import TYPE_CHECKING, Optional

from ..etrade_client import ETradeAPIError
from ..smart_strategy import Signal
from ..utils import get_et_now
from .config import TradeResult

if TYPE_CHECKING:
    from .core import TradingBot

logger = logging.getLogger(__name__)


class HedgeMixin:
    """
    Mixin providing hedging and reversal methods.

    Requires from base class:
    - client: Optional[ETradeClient]
    - config: BotConfig
    - db: Database
    - telegram: TelegramNotifier
    - hedge_manager: HedgeManager
    - strategy: SmartStrategy
    - is_paper_mode: bool (property)
    - _paper_positions: Dict[str, Dict]
    - _paper_capital: float
    - _position_lock: threading.RLock
    - _reversal_triggered_today: bool
    - _reversal_date: Optional[str]
    - get_quote(symbol): Dict[str, float]
    - get_open_positions(): Dict[str, Dict]
    - close_position(etf): TradeResult
    - _wait_for_order_fill(order_id): Optional[Dict]
    """

    def check_and_execute_hedge(self: "TradingBot") -> Optional[TradeResult]:
        """
        Check if a trailing hedge should be added and execute it.

        Called periodically during market hours to monitor position gains
        and add inverse positions to lock in profits.

        Thread-safe: Uses position lock to prevent concurrent modifications.

        Returns:
            TradeResult if hedge was executed, None otherwise
        """
        if not self.hedge_manager.position:
            return None

        # Get current price for the position
        position = self.hedge_manager.position
        quote = self.get_quote(position.instrument)
        if not quote or quote.get("current_price", 0) <= 0:
            logger.warning(f"Could not get quote for {position.instrument}")
            return None

        current_price = quote["current_price"]

        # Check if hedge should be triggered
        hedge_order = self.hedge_manager.check_and_hedge(current_price)
        if not hedge_order:
            return None

        # Execute the hedge
        hedge_instrument = hedge_order["instrument"]
        hedge_value = hedge_order["value"]

        # Get price for hedge instrument
        hedge_quote = self.get_quote(hedge_instrument)
        if not hedge_quote or hedge_quote.get("current_price", 0) <= 0:
            logger.warning(f"Could not get quote for hedge instrument {hedge_instrument}")
            return None

        hedge_price = hedge_quote["current_price"]
        hedge_shares = max(1, int(hedge_value / hedge_price))

        logger.info(
            f"Executing trailing hedge: {hedge_shares} {hedge_instrument} @ ${hedge_price:.2f}",
            reason=hedge_order["reason"],
            position_gain_pct=f"{hedge_order['position_gain_pct']:.2f}%",
        )

        # Acquire lock for position modification
        with self._position_lock:
            if self.is_paper_mode:
                return self._execute_paper_hedge(hedge_instrument, hedge_shares, hedge_price)
            else:
                return self._execute_live_hedge(hedge_instrument, hedge_shares, hedge_price)

    def _execute_paper_hedge(
        self: "TradingBot",
        hedge_instrument: str,
        hedge_shares: int,
        hedge_price: float,
    ) -> TradeResult:
        """Execute a paper hedge trade."""
        self._paper_capital -= hedge_shares * hedge_price
        if hedge_instrument not in self._paper_positions:
            self._paper_positions[hedge_instrument] = {
                "shares": hedge_shares,
                "entry_price": hedge_price,
                "entry_time": get_et_now(),
                "signal": "HEDGE",
            }
        else:
            # Add to existing hedge position
            self._paper_positions[hedge_instrument]["shares"] += hedge_shares

        # Update hedge manager
        self.hedge_manager.update_hedge_shares(hedge_shares)

        return TradeResult(
            success=True,
            signal=Signal.CASH,
            etf=hedge_instrument,
            action="BUY",
            shares=hedge_shares,
            price=hedge_price,
            total_value=hedge_shares * hedge_price,
            order_id=f"HEDGE-{get_et_now().strftime('%Y%m%d%H%M%S')}",
            is_paper=True,
        )

    def _execute_live_hedge(
        self: "TradingBot",
        hedge_instrument: str,
        hedge_shares: int,
        hedge_price: float,
    ) -> Optional[TradeResult]:
        """Execute a live hedge trade via E*TRADE."""
        if not self.client:
            logger.error("Cannot execute live hedge: E*TRADE client not configured")
            return None

        # Ensure token is fresh before starting preview+place sequence
        if not self.client.ensure_authenticated():
            logger.error("Cannot execute live hedge: E*TRADE not authenticated")
            return None

        try:
            # Preview order first (required for production)
            preview = self.client.preview_order(
                account_id_key=self.config.account_id_key,
                symbol=hedge_instrument,
                action="BUY",
                quantity=hedge_shares,
                order_type="MARKET",
            )

            order_response = self.client.place_order(
                account_id_key=self.config.account_id_key,
                symbol=hedge_instrument,
                action="BUY",
                quantity=hedge_shares,
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
                if filled_shares < hedge_shares:
                    logger.warning(
                        f"PARTIAL FILL: Hedge ordered {hedge_shares}, only {filled_shares} filled"
                    )
                    self.db.log_event(
                        "PARTIAL_FILL",
                        order_id=order_id,
                        symbol=hedge_instrument,
                        requested=hedge_shares,
                        filled=filled_shares,
                        shortfall=hedge_shares - filled_shares,
                        action="HEDGE",
                    )
                    self.telegram.notify_error(
                        "Partial Hedge Fill",
                        f"Hedge: {hedge_shares} requested, only {filled_shares} filled",
                    )
            else:
                # Fall back to requested if polling fails - ALERT USER
                logger.warning(f"Using requested shares for hedge order {order_id}")
                filled_shares = hedge_shares
                fill_price = hedge_price
                # Alert user that we're using estimated price
                self.telegram.notify_error(
                    "Hedge Fill Unconfirmed",
                    f"Order {order_id}: Using estimated price ${fill_price:.2f}. "
                    f"Check E*TRADE for actual fill.",
                )

            self.hedge_manager.update_hedge_shares(filled_shares)

            logger.info(
                f"[LIVE] Hedge executed: {filled_shares} {hedge_instrument} "
                f"@ ${fill_price:.2f} - Order ID: {order_id}"
            )

            return TradeResult(
                success=True,
                signal=Signal.CASH,
                etf=hedge_instrument,
                action="BUY",
                shares=filled_shares,
                price=fill_price,
                total_value=filled_shares * fill_price,
                order_id=order_id,
                is_paper=False,
            )

        except ETradeAPIError as e:
            logger.error(f"Failed to execute live hedge: {e}")
            return None

    def check_and_execute_reversal(self: "TradingBot") -> Optional[TradeResult]:
        """
        Check if position should be reversed (flip to inverse when losing).

        If a BITU position drops below the reversal threshold (-2%), we close it
        and open SBIT to profit from continued downward movement.

        Backtested result: +8.7% return with -2% reversal vs -8.1% without.

        Thread-safe: Uses position lock to prevent concurrent modifications.

        Returns:
            TradeResult if reversal was executed, None otherwise
        """
        # Check if reversal is enabled (no lock needed for config check)
        if not self.strategy.config.reversal_enabled:
            return None

        # Check if already reversed today
        today = get_et_now().strftime("%Y-%m-%d")
        if self._reversal_date != today:
            self._reversal_triggered_today = False
            self._reversal_date = today

        if self._reversal_triggered_today:
            return None

        # Acquire lock for position check and modification
        # This prevents race with other jobs (crash_day, pump_day, hedge)
        with self._position_lock:
            # Re-check flag under lock (another thread may have just completed reversal)
            if self._reversal_triggered_today:
                return None

            # Get current positions
            positions = self.get_open_positions()
            if not positions:
                return None

            # Only reverse BITU positions (long trades that are losing)
            if "BITU" not in positions:
                return None

            bitu_pos = positions["BITU"]
            shares = bitu_pos.get("shares", 0)
            entry_price = bitu_pos.get("entry_price", 0)

            if shares <= 0 or entry_price <= 0:
                return None

            # Get current BITU price
            quote = self.get_quote("BITU")
            if not quote or quote.get("current_price", 0) <= 0:
                logger.warning("Could not get BITU quote for reversal check")
                return None

            current_price = quote["current_price"]
            pnl_pct = ((current_price - entry_price) / entry_price) * 100

            # Check if reversal threshold is triggered
            threshold = self.strategy.config.reversal_threshold
            if pnl_pct > threshold:
                # Position not down enough to trigger reversal
                return None

            logger.info(
                f"Reversal triggered! BITU down {pnl_pct:.2f}% (threshold: {threshold}%)",
                shares=shares,
                entry_price=entry_price,
                current_price=current_price,
            )

            # Mark reversal as triggered
            self._reversal_triggered_today = True

            # Execute the reversal
            return self._execute_reversal(shares, entry_price, pnl_pct)

    def _execute_reversal(
        self: "TradingBot",
        shares: int,
        entry_price: float,
        pnl_pct: float,
    ) -> TradeResult:
        """Execute the BITU -> SBIT reversal."""
        # Step 1: Close BITU position
        close_result = self.close_position("BITU")
        if not close_result.success:
            logger.error(f"Failed to close BITU for reversal: {close_result.error}")
            self._reversal_triggered_today = False  # Allow retry
            return close_result

        # Step 2: Open SBIT position with the same capital
        # Get SBIT quote
        sbit_quote = self.get_quote("SBIT")
        if not sbit_quote or sbit_quote.get("current_price", 0) <= 0:
            logger.error("Could not get SBIT quote for reversal")
            # CRITICAL: BITU is closed but we can't open SBIT - alert user!
            self._alert_reversal_partial_failure("Could not get SBIT quote", shares, pnl_pct)
            return close_result  # At least we closed BITU

        sbit_price = sbit_quote["current_price"]

        # Use same number of shares for simplicity
        sbit_shares = shares

        if self.is_paper_mode:
            return self._execute_paper_reversal(sbit_shares, sbit_price, pnl_pct)
        else:
            return self._execute_live_reversal(
                sbit_shares, sbit_price, shares, pnl_pct, close_result
            )

    def _execute_paper_reversal(
        self: "TradingBot",
        sbit_shares: int,
        sbit_price: float,
        pnl_pct: float,
    ) -> TradeResult:
        """Execute paper reversal to SBIT."""
        self._paper_capital -= sbit_shares * sbit_price
        self._paper_positions["SBIT"] = {
            "shares": sbit_shares,
            "entry_price": sbit_price,
            "entry_time": get_et_now(),
            "signal": "REVERSAL",
        }

        # Register with hedge manager
        self.hedge_manager.register_position(
            instrument="SBIT",
            shares=sbit_shares,
            entry_price=sbit_price,
        )

        self.db.log_event(
            "REVERSAL_EXECUTED",
            f"[PAPER] Reversed BITU to SBIT at {pnl_pct:.2f}% loss",
            {
                "original_pnl_pct": pnl_pct,
                "sbit_shares": sbit_shares,
                "sbit_price": sbit_price,
                "timestamp": get_et_now().isoformat(),
            },
        )

        logger.info(f"[PAPER] Reversal executed: {sbit_shares} SBIT @ ${sbit_price:.2f}")

        return TradeResult(
            success=True,
            signal=Signal.CRASH_DAY,
            etf="SBIT",
            action="BUY",
            shares=sbit_shares,
            price=sbit_price,
            total_value=sbit_shares * sbit_price,
            order_id=f"REVERSAL-{get_et_now().strftime('%Y%m%d%H%M%S')}",
            is_paper=True,
        )

    def _execute_live_reversal(
        self: "TradingBot",
        sbit_shares: int,
        sbit_price: float,
        original_shares: int,
        pnl_pct: float,
        close_result: TradeResult,
    ) -> TradeResult:
        """Execute live reversal to SBIT via E*TRADE."""
        if not self.client:
            self._alert_reversal_partial_failure(
                "E*TRADE client not configured", original_shares, pnl_pct
            )
            return close_result

        if not self.client.ensure_authenticated():
            self._alert_reversal_partial_failure(
                "E*TRADE not authenticated", original_shares, pnl_pct
            )
            return close_result

        try:
            # Preview order
            preview = self.client.preview_order(
                account_id_key=self.config.account_id_key,
                symbol="SBIT",
                action="BUY",
                quantity=sbit_shares,
                order_type="MARKET",
            )

            order_response = self.client.place_order(
                account_id_key=self.config.account_id_key,
                symbol="SBIT",
                action="BUY",
                quantity=sbit_shares,
                order_type="MARKET",
                preview_ids=preview.get("PreviewIds", []),
            )

            order_id = str(order_response.get("OrderId", ""))

            # Poll for actual fill
            fill_info = self._wait_for_order_fill(order_id)
            if fill_info:
                filled_shares = fill_info["filled_qty"]
                fill_price = fill_info["avg_price"]

                if filled_shares < sbit_shares:
                    logger.warning(
                        f"PARTIAL FILL: Reversal ordered {sbit_shares} SBIT, "
                        f"only {filled_shares} filled"
                    )
                    self.db.log_event(
                        "PARTIAL_FILL",
                        order_id=order_id,
                        symbol="SBIT",
                        requested=sbit_shares,
                        filled=filled_shares,
                        shortfall=sbit_shares - filled_shares,
                        action="REVERSAL",
                    )
                    self.telegram.notify_error(
                        "Partial Reversal Fill",
                        f"Reversal: {sbit_shares} SBIT requested, only {filled_shares} filled",
                    )
            else:
                # Fall back to requested if polling fails - ALERT USER
                logger.warning(f"Using requested shares for reversal order {order_id}")
                filled_shares = sbit_shares
                fill_price = sbit_price
                # Alert user that we're using estimated price
                self.telegram.notify_error(
                    "Reversal Fill Unconfirmed",
                    f"Order {order_id}: Using estimated price ${fill_price:.2f}. "
                    f"Check E*TRADE for actual fill.",
                )

            # Register with hedge manager
            self.hedge_manager.register_position(
                instrument="SBIT",
                shares=filled_shares,
                entry_price=fill_price,
            )

            self.db.log_event(
                "REVERSAL_EXECUTED",
                f"[LIVE] Reversed BITU to SBIT at {pnl_pct:.2f}% loss",
                {
                    "original_pnl_pct": pnl_pct,
                    "bitu_shares": original_shares,
                    "sbit_shares": filled_shares,
                    "sbit_price": fill_price,
                    "order_id": order_id,
                    "timestamp": get_et_now().isoformat(),
                },
            )

            logger.info(
                f"[LIVE] Reversal executed: {filled_shares} SBIT @ ${fill_price:.2f} "
                f"- Order ID: {order_id}"
            )

            return TradeResult(
                success=True,
                signal=Signal.CRASH_DAY,
                etf="SBIT",
                action="BUY",
                shares=filled_shares,
                price=fill_price,
                total_value=filled_shares * fill_price,
                order_id=order_id,
                is_paper=False,
            )

        except ETradeAPIError as e:
            logger.error(f"Failed to execute live reversal: {e}")
            # CRITICAL: BITU is closed but SBIT order failed - alert user!
            self._alert_reversal_partial_failure(
                f"SBIT order failed: {e}", original_shares, pnl_pct
            )
            return close_result

    def _alert_reversal_partial_failure(
        self: "TradingBot",
        reason: str,
        original_shares: int,
        original_pnl_pct: float,
    ):
        """
        Alert user when reversal fails after closing BITU but before opening SBIT.

        This is a critical state - the user needs to manually check their account
        as they may be holding cash instead of their intended position.
        """
        self.db.log_event(
            "REVERSAL_PARTIAL_FAILURE",
            "CRITICAL: BITU closed but SBIT not opened",
            {
                "reason": reason,
                "original_shares": original_shares,
                "original_pnl_pct": original_pnl_pct,
                "timestamp": get_et_now().isoformat(),
            },
        )

        # Send urgent Telegram notification
        try:
            from ..telegram_bot import TelegramBot
            from ..utils import run_async

            async def _alert():
                bot = TelegramBot()
                await bot.initialize()
                mode = "[PAPER]" if self.is_paper_mode else "[LIVE]"
                await bot.send_message(
                    f"🚨🚨 REVERSAL INCOMPLETE 🚨🚨\n\n"
                    f"{mode} BITU was CLOSED but SBIT could NOT be opened!\n\n"
                    f"Reason: {reason}\n"
                    f"Original shares: {original_shares}\n"
                    f"Original P/L: {original_pnl_pct:.2f}%\n\n"
                    f"⚠️ YOU MAY BE HOLDING CASH INSTEAD OF SBIT\n"
                    f"CHECK YOUR ACCOUNT IMMEDIATELY!",
                    parse_mode=None,
                )

            run_async(_alert())
        except Exception as e:
            logger.error(f"Failed to send reversal failure alert: {e}")
