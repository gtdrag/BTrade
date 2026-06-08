"""
Wheel strategy state machine, cycle and position dataclasses.

Provides WheelState enum, transition validation, and typed dataclasses
for wheel cycles and options positions.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class WheelState(Enum):
    """States of the wheel strategy cycle.

    State flow:
        CASH -> SHORT_PUT -> HOLDING_SHARES -> COVERED_CALL -> CASH
                          -> CASH (put expires worthless)
                                              -> HOLDING_SHARES (call expires)
    """

    CASH = "CASH"
    SHORT_PUT = "SHORT_PUT"
    HOLDING_SHARES = "HOLDING_SHARES"
    COVERED_CALL = "COVERED_CALL"


# Valid state transitions for the wheel strategy.
# Each key maps to the set of states it can legally transition into.
VALID_TRANSITIONS: dict = {
    WheelState.CASH: {WheelState.SHORT_PUT},
    WheelState.SHORT_PUT: {WheelState.CASH, WheelState.HOLDING_SHARES},
    WheelState.HOLDING_SHARES: {WheelState.COVERED_CALL},
    WheelState.COVERED_CALL: {WheelState.HOLDING_SHARES, WheelState.CASH},
}


def transition(current: WheelState, next_state: WheelState) -> WheelState:
    """Validate and return the next wheel state.

    All state changes must go through this function. It enforces the
    legal transition graph defined in VALID_TRANSITIONS, preventing
    invalid state jumps (T-02-01 threat mitigation).

    Args:
        current: The current WheelState.
        next_state: The proposed next WheelState.

    Returns:
        next_state if the transition is valid.

    Raises:
        ValueError: If next_state is not a valid transition from current.
    """
    valid_next = VALID_TRANSITIONS[current]
    if next_state not in valid_next:
        valid_names = [s.value for s in valid_next]
        raise ValueError(
            f"Invalid wheel transition: {current.value} -> {next_state.value}. "
            f"Valid transitions from {current.value}: {valid_names}"
        )
    logger.debug("Wheel state transition: %s -> %s", current.value, next_state.value)
    return next_state


@dataclass
class WheelCycle:
    """Represents a single wheel strategy cycle from start to completion.

    A cycle begins when a cash-secured put is sold (CASH -> SHORT_PUT)
    and ends when shares are called away (COVERED_CALL -> CASH) or
    a put expires worthless (SHORT_PUT -> CASH).

    Cost basis tracking:
        initial (on assignment): cost_basis = put_strike - put_premium_received
        ongoing: cost_basis -= each covered call premium collected
    """

    id: int
    state: WheelState
    underlying: str
    put_strike: Optional[float]
    put_premium_received: float
    put_expiry_date: Optional[str]
    shares_held: int
    cost_basis: float
    covered_call_premiums_collected: float
    realized_pnl: Optional[float]
    opened_at: str
    closed_at: Optional[str]
    created_at: str
    updated_at: str


@dataclass
class OptionsPosition:
    """Represents a single options contract position within a wheel cycle.

    One cycle may have multiple positions over its lifetime:
    - The initial put (SHORT_PUT state)
    - One or more covered calls (COVERED_CALL state)

    Positions are never deleted — mark status='CLOSED' for audit trail.
    Greek values are stored at entry time for analysis; they are not
    updated after entry (use current market data for live Greeks).
    """

    id: int
    cycle_id: int
    symbol: str
    option_type: str  # "PUT" or "CALL"
    strike: float
    expiry_date: str
    dte_at_entry: int
    quantity: int
    premium_received: float
    delta: Optional[float]
    gamma: Optional[float]
    theta: Optional[float]
    vega: Optional[float]
    iv: Optional[float]
    status: str  # "OPEN" or "CLOSED"
    close_premium: Optional[float]
    opened_at: str
    closed_at: Optional[str]
    created_at: str
    updated_at: str
