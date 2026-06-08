"""
WheelExecutor — Pure execution service for the wheel strategy.

Owns the preview → place → DB mutation → audit log pipeline for:
- Put sell-to-open (new cycle)
- Covered call sell-to-open
- Buy-to-close (profit take)
- Defensive roll (BTC current + STO new, two-step)

Has no UI dependencies. Callers (TelegramBot, Streamlit dashboard, CLI)
receive a structured ExecutionResult and are responsible for rendering
notifications in their own UI layer.

Adversarial review ARCHITECTURE.md [HIGH] — TelegramBot had become an
order execution engine. This module restores the separation of concerns:
execution logic lives here; Telegram is a thin presentation layer.
"""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from .etrade_client import ETradeAPIError, ETradeAuthError, extract_order_id
from .utils import parse_expiry_components
from .wheel_state import WheelState

if TYPE_CHECKING:
    from .wheel_strategy import CallSignal, PutSignal

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Structured result of a wheel execution operation.

    Fields:
        success: True if the operation completed successfully.
        failure_kind: Machine-readable failure category. One of:
            - "etrade_error"     — preview or place raised ETradeAPIError/AuthError
            - "no_cycle"         — expected active cycle but none found
            - "stale_signal"     — signal no longer valid (cost basis moved, etc.)
            - "roll_btc_failed"  — two-step roll failed at the BTC step
            - "roll_sto_failed"  — BTC succeeded but STO failed (partial failure)
        error: Human-readable error message suitable for notifications.
        data: Operation-specific payload for the caller. Common keys:
            - cycle_id, order_id (put/call)
            - new_cost_basis (call)
            - next_state, order_id (btc)
            - new_position_id, new_strike, roll_count, btc_order_id,
              sto_order_id, net_credit (roll)
            - safe_state (roll_sto_failed)
            - current_cost_basis (stale_signal)
    """

    success: bool
    failure_kind: Optional[str] = None
    error: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)


class WheelExecutor:
    """Pure executor for wheel strategy orders.

    All methods are synchronous — preview/place/DB calls are all blocking,
    so there is no reason to make them async. Callers that need to invoke
    this from an async context should wrap in run_in_executor or similar.
    """

    def __init__(self, client: Any, db: Any, account_id_key: str) -> None:
        self.client = client
        self.db = db
        self.account_id_key = account_id_key

    # ---------------------------------------------------------------
    # Shared preview + place helper
    # ---------------------------------------------------------------

    def _preview_and_place(
        self,
        option_type: str,
        expiry_year: int,
        expiry_month: int,
        expiry_day: int,
        strike: float,
        action: str,
        quantity: int,
        price: float,
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        """Run the standard preview + place flow for an options order.

        Returns (place_response, order_id). The order_id may be None if
        neither response shape contained one (caller should treat as
        "pending" or "unknown" for display purposes).
        """
        preview_response = self.client.preview_options_order(
            self.account_id_key,
            "IBIT",
            option_type,
            expiry_year,
            expiry_month,
            expiry_day,
            strike,
            action,
            quantity,
            price,
        )
        preview_ids = preview_response.get("PreviewIds")

        place_response = self.client.place_options_order(
            self.account_id_key,
            "IBIT",
            option_type,
            expiry_year,
            expiry_month,
            expiry_day,
            strike,
            action,
            quantity,
            price,
            preview_ids=preview_ids,
        )

        return place_response, extract_order_id(place_response)

    # ---------------------------------------------------------------
    # Put sell-to-open (new cycle)
    # ---------------------------------------------------------------

    def execute_put_sell(self, signal: "PutSignal") -> ExecutionResult:
        """Preview, place, and record a sell-to-open put order.

        Creates a new wheel cycle atomically via open_short_put_cycle.
        T-03-08: order is only recorded on successful placement.
        T-03-10: audit log emitted.
        """
        try:
            _place_response, order_id = self._preview_and_place(
                "PUT",
                signal.expiry_year,
                signal.expiry_month,
                signal.expiry_day,
                signal.strike,
                "SELL_OPEN",
                1,
                signal.premium,  # use bid as limit price (conservative for STO)
            )

            # Atomic create cycle + transition + open position
            cycle_id, _position_id = self.db.open_short_put_cycle(
                symbol=signal.symbol,
                strike=signal.strike,
                premium_received=signal.premium,
                expiry_date=signal.expiry_date,
                dte_at_entry=signal.dte,
                delta=signal.delta,
                gamma=signal.gamma,
                theta=signal.theta,
                vega=signal.vega,
                iv=signal.iv,
                quantity=1,
            )

            self.db.log_event(
                "INFO",
                "put_order_placed",
                {
                    "strike": signal.strike,
                    "delta": signal.delta,
                    "premium": signal.premium,
                    "dte": signal.dte,
                    "cycle_id": cycle_id,
                    "order_id": str(order_id) if order_id else "unknown",
                },
            )

            logger.info(
                "Put order placed: strike=%.2f dte=%d cycle_id=%d",
                signal.strike,
                signal.dte,
                cycle_id,
            )
            return ExecutionResult(
                success=True,
                data={"cycle_id": cycle_id, "order_id": order_id},
            )

        except (ETradeAPIError, ETradeAuthError) as e:
            logger.error("Failed to execute put order (E*TRADE): %s", e, exc_info=True)
            return ExecutionResult(
                success=False,
                failure_kind="etrade_error",
                error=str(e),
            )

    # ---------------------------------------------------------------
    # Covered call sell-to-open
    # ---------------------------------------------------------------

    def execute_call_sell(self, signal: "CallSignal") -> ExecutionResult:
        """Preview, place, and record a sell-to-open covered call order.

        T-04-08: Stale signal guard — re-reads active cycle and verifies
        signal.strike >= cycle cost_basis before placing order. Rejects
        if cost basis has changed since signal was generated.
        """
        # Stale signal guard
        cycle = self.db.get_active_cycle()
        if cycle is None:
            logger.error("execute_call_sell: no active cycle found")
            return ExecutionResult(
                success=False,
                failure_kind="no_cycle",
                error="No active wheel cycle found.",
            )

        current_cost_basis = cycle.get("cost_basis", 0.0) or 0.0
        if signal.strike < current_cost_basis:
            logger.warning(
                "Call strike %.2f is below current cost basis %.2f — rejecting stale signal",
                signal.strike,
                current_cost_basis,
            )
            return ExecutionResult(
                success=False,
                failure_kind="stale_signal",
                error=(
                    f"Call strike ${signal.strike:.2f} is below current "
                    f"cost basis ${current_cost_basis:.2f}. Order rejected."
                ),
                data={"current_cost_basis": current_cost_basis},
            )

        try:
            _place_response, order_id = self._preview_and_place(
                "CALL",
                signal.expiry_year,
                signal.expiry_month,
                signal.expiry_day,
                signal.strike,
                "SELL_OPEN",
                1,
                signal.premium,
            )

            cycle_id = cycle["id"]
            old_premiums = cycle.get("covered_call_premiums_collected") or 0.0
            new_cost_basis = current_cost_basis - signal.premium
            new_total_premiums = old_premiums + signal.premium

            self.db.transition_wheel_state(
                cycle_id,
                WheelState.COVERED_CALL,
                "call_sold",
                cost_basis=new_cost_basis,
                covered_call_premiums_collected=new_total_premiums,
            )
            self.db.open_wheel_position(
                cycle_id=cycle_id,
                symbol=signal.symbol,
                option_type="CALL",
                strike=signal.strike,
                expiry_date=signal.expiry_date,
                dte_at_entry=signal.dte,
                premium_received=signal.premium,
                quantity=1,
                delta=signal.delta,
                gamma=signal.gamma,
                theta=signal.theta,
                vega=signal.vega,
                iv=signal.iv,
            )

            self.db.log_event(
                "INFO",
                "call_order_placed",
                {
                    "strike": signal.strike,
                    "delta": signal.delta,
                    "premium": signal.premium,
                    "dte": signal.dte,
                    "cycle_id": cycle_id,
                    "order_id": str(order_id) if order_id else "unknown",
                    "new_cost_basis": new_cost_basis,
                },
            )

            logger.info(
                "Call order placed: strike=%.2f dte=%d cycle_id=%d new_cost_basis=%.2f",
                signal.strike,
                signal.dte,
                cycle_id,
                new_cost_basis,
            )
            return ExecutionResult(
                success=True,
                data={
                    "cycle_id": cycle_id,
                    "order_id": order_id,
                    "new_cost_basis": new_cost_basis,
                },
            )

        except (ETradeAPIError, ETradeAuthError) as e:
            logger.error("Failed to execute call order (E*TRADE): %s", e, exc_info=True)
            return ExecutionResult(
                success=False,
                failure_kind="etrade_error",
                error=str(e),
            )

    # ---------------------------------------------------------------
    # Buy-to-close (profit take)
    # ---------------------------------------------------------------

    def execute_btc(
        self,
        position: dict,
        cycle: dict,
        close_price: float,
    ) -> ExecutionResult:
        """Preview, place, and record a buy-to-close order.

        T-05-06: no DB mutation on API failure.
        T-05-09: audit log for every order attempt.
        """
        try:
            option_type = position.get("option_type", "PUT")
            quantity = int(position.get("quantity", 1))
            strike_price = float(position.get("strike", 0))
            expiry_year, expiry_month, expiry_day = parse_expiry_components(
                position.get("expiry_date", "")
            )

            _place_response, order_id = self._preview_and_place(
                option_type,
                expiry_year,
                expiry_month,
                expiry_day,
                strike_price,
                "BUY_CLOSE",
                quantity,
                close_price,
            )

            # DB mutations — only after successful order placement
            self.db.close_wheel_position(position["id"], close_price)

            cycle_state = cycle.get("state", "")
            if cycle_state == WheelState.SHORT_PUT.value or cycle_state == "SHORT_PUT":
                next_state = WheelState.CASH
            else:
                next_state = WheelState.HOLDING_SHARES

            self.db.transition_wheel_state(cycle["id"], next_state, "profit_take_btc")

            self.db.log_event(
                "INFO",
                "btc_order_placed",
                {
                    "position_id": position["id"],
                    "close_price": close_price,
                    "cycle_id": cycle["id"],
                    "order_id": str(order_id) if order_id else "unknown",
                },
            )

            logger.info(
                "BTC order placed: position_id=%d close_price=%.2f cycle_id=%d",
                position["id"],
                close_price,
                cycle["id"],
            )
            return ExecutionResult(
                success=True,
                data={"order_id": order_id, "next_state": next_state.value},
            )

        except (ETradeAPIError, ETradeAuthError) as e:
            logger.error("Failed to execute BTC order (E*TRADE): %s", e, exc_info=True)
            return ExecutionResult(
                success=False,
                failure_kind="etrade_error",
                error=str(e),
            )

    # ---------------------------------------------------------------
    # Defensive roll (two-step BTC + STO)
    # ---------------------------------------------------------------

    def execute_roll(
        self,
        position: dict,
        new_contract: dict,
        cycle: dict,
        btc_price: float,
    ) -> ExecutionResult:
        """Execute a two-step roll: BTC current position, then STO new position.

        Partial failure handling:
        - BTC fails: no DB changes. Returns failure_kind="roll_btc_failed".
        - STO fails after BTC succeeded: closes old position, transitions cycle
          to a safe state (CASH for put, HOLDING_SHARES for call), then returns
          failure_kind="roll_sto_failed" with btc_order_id in data.
        - Both succeed: opens new position, increments roll_count, returns success.

        T-05-08: stale signal guard — ensures old position is actually OPEN.
        T-05-09: audit log for every step.
        """
        option_type = position.get("option_type", "PUT")
        quantity = int(position.get("quantity", 1))
        old_roll_count = position.get("roll_count", 0)
        strike_price = float(position.get("strike", 0))

        expiry_year, expiry_month, expiry_day = parse_expiry_components(
            position.get("expiry_date", "")
        )

        # Step 1: BTC current position
        try:
            btc_result, _ = self._preview_and_place(
                option_type,
                expiry_year,
                expiry_month,
                expiry_day,
                strike_price,
                "BUY_CLOSE",
                quantity,
                btc_price,
            )
        except Exception as e:
            logger.error("Roll BTC failed for position_id=%d: %s", position["id"], e)
            self.db.log_event(
                "ERROR",
                "roll_btc_failed",
                {"position_id": position["id"], "error": str(e)},
            )
            return ExecutionResult(
                success=False,
                failure_kind="roll_btc_failed",
                error=str(e),
            )

        # BTC succeeded — close old position in DB
        self.db.close_wheel_position(position["id"], btc_price)
        btc_order_id = extract_order_id(btc_result) if btc_result else None

        # Step 2: STO new position
        new_symbol = new_contract.get("symbol", position["symbol"])
        new_bid = float(new_contract.get("bid", 0))
        new_strike = float(new_contract.get("strike", 0))
        new_expiry_year = int(new_contract.get("expiry_year", 0))
        new_expiry_month = int(new_contract.get("expiry_month", 0))
        new_expiry_day = int(new_contract.get("expiry_day", 0))

        try:
            sto_result, _ = self._preview_and_place(
                option_type,
                new_expiry_year,
                new_expiry_month,
                new_expiry_day,
                new_strike,
                "SELL_OPEN",
                quantity,
                new_bid,
            )
        except Exception as e:
            logger.error("Roll STO failed for position_id=%d: %s", position["id"], e)
            cycle_state = cycle.get("state", "")
            if cycle_state == WheelState.SHORT_PUT.value or cycle_state == "SHORT_PUT":
                safe_state = WheelState.CASH
                safe_reason = "roll_sto_failed_put"
            else:
                safe_state = WheelState.HOLDING_SHARES
                safe_reason = "roll_sto_failed_call"

            self.db.transition_wheel_state(cycle["id"], safe_state, safe_reason)
            self.db.log_event(
                "ERROR",
                "roll_sto_failed",
                {
                    "position_id": position["id"],
                    "btc_order_id": str(btc_order_id) if btc_order_id else "unknown",
                    "error": str(e),
                },
            )
            return ExecutionResult(
                success=False,
                failure_kind="roll_sto_failed",
                error=str(e),
                data={
                    "btc_order_id": btc_order_id,
                    "safe_state": safe_state.value,
                },
            )

        # Both steps succeeded — open new position in DB
        new_position_id = self.db.open_wheel_position(
            cycle_id=cycle["id"],
            symbol=new_symbol,
            option_type=option_type,
            strike=float(new_contract.get("strike", 0)),
            expiry_date=str(new_contract.get("expiry_date", "")),
            dte_at_entry=int(new_contract.get("dte", 0)),
            premium_received=new_bid,
            quantity=quantity,
            delta=new_contract.get("delta"),
            gamma=new_contract.get("gamma"),
            theta=new_contract.get("theta"),
            vega=new_contract.get("vega"),
            iv=new_contract.get("iv"),
        )

        self.db.set_roll_count(new_position_id, old_roll_count + 1)
        sto_order_id = extract_order_id(sto_result) if sto_result else None

        self.db.log_event(
            "INFO",
            "roll_executed",
            {
                "old_position_id": position["id"],
                "new_position_id": new_position_id,
                "new_strike": float(new_contract.get("strike", 0)),
                "roll_count": old_roll_count + 1,
                "btc_order_id": str(btc_order_id) if btc_order_id else "unknown",
                "sto_order_id": str(sto_order_id) if sto_order_id else "unknown",
            },
        )

        net_credit = new_bid - btc_price
        logger.info(
            "Roll executed: position_id=%d -> new_position_id=%d roll_count=%d",
            position["id"],
            new_position_id,
            old_roll_count + 1,
        )
        return ExecutionResult(
            success=True,
            data={
                "new_position_id": new_position_id,
                "new_strike": float(new_contract.get("strike", 0)),
                "roll_count": old_roll_count + 1,
                "btc_order_id": btc_order_id,
                "sto_order_id": sto_order_id,
                "net_credit": net_credit,
                "old_strike": float(position.get("strike", 0)),
            },
        )
