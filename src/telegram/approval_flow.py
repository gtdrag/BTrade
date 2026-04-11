"""
ApprovalFlow — per-flow state container for Telegram approval requests.

The TelegramBot manages several independent approval flows (put sell, call
sell, buy-to-close, defensive roll). Each flow needs its own event/result/
callback_id so they cannot collide when multiple are pending. Before this
abstraction, each flow kept 5 loose instance attributes on the bot (16
total across 4 flows) — this is the "approval state explosion" finding
from the adversarial architecture review.

Each ApprovalFlow groups the state for a single flow into one object.
Flow-specific context (signal, chain, position, new_contract) is stored
as optional attributes so callers can use attribute syntax naturally.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class ApprovalFlow:
    """Per-flow state for a Telegram approval request.

    Attributes:
        event: asyncio.Event fired when a decision arrives. None when no
            approval is pending.
        result: "approved" / "rejected" / strike string, or None if
            pending / timed-out.
        callback_id: Identifier embedded in button callback_data. Stale
            button taps (from prior, timed-out, or completed approvals)
            are rejected because their callback_id will not match.
        signal: Flow-specific — the PutSignal or CallSignal being
            approved for sell-to-open flows.
        chain: Flow-specific — the full options chain snapshot used to
            render alternative strikes in the adjust flow.
        position: Flow-specific — the position dict being closed
            (buy-to-close) or rolled (defensive roll).
        new_contract: Flow-specific — the proposed new contract for a
            defensive roll.
    """

    event: Optional[asyncio.Event] = None
    result: Optional[str] = None
    callback_id: Optional[str] = None
    signal: Optional[Any] = None
    chain: Optional[List[Dict[str, Any]]] = None
    position: Optional[Dict[str, Any]] = None
    new_contract: Optional[Dict[str, Any]] = None

    def begin(self, callback_id: str) -> None:
        """Start a new approval wait with a fresh event and callback_id.

        The caller is responsible for populating flow-specific context
        (signal/chain/position/new_contract) before calling this.
        """
        self.event = asyncio.Event()
        self.result = None
        self.callback_id = callback_id

    def set_decision(self, callback_id: str, result: str) -> bool:
        """Record a decision if the callback_id matches the pending request.

        Returns True if the decision was accepted (callback_id matches the
        currently pending approval). Returns False for stale callbacks —
        button taps from prior or already-completed approvals. This is the
        HI-01 ghost-approval guard.
        """
        if self.callback_id is None or callback_id != self.callback_id:
            return False
        self.result = result
        if self.event is not None:
            self.event.set()
        return True

    def clear(self) -> None:
        """Reset all flow state after a decision is consumed or timed out."""
        self.event = None
        self.result = None
        self.callback_id = None
        self.signal = None
        self.chain = None
        self.position = None
        self.new_contract = None
