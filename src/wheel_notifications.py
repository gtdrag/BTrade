"""
Wheel notification formatters — pure functions that build message strings.

The scheduler's `_job_assignment_detection` method was previously a ~180-line
god-method that detected expiry results, computed annualized returns, and
built four different Telegram messages inline. The adversarial architecture
review flagged this as business logic that belongs in a service layer, not
the scheduler.

This module extracts the per-outcome message formatting into pure functions
that can be tested in isolation without a scheduler, Telegram bot, database,
or E*TRADE client. The scheduler now just picks the right formatter based on
the expiry result and sends the returned message.

Each formatter returns a fully-formatted Telegram message string (ready to
pass to `send_message`). None of them have side effects.
"""

from datetime import date as _date
from typing import Any, Dict, List, Optional


def compute_wheel_cycle_status(
    cycle: Dict[str, Any],
    positions: List[Dict[str, Any]],
    today: _date,
) -> Dict[str, Any]:
    """Compute common wheel cycle status fields used by /wheel and daily summary.

    Both `_cmd_wheel` (Telegram command) and `_job_wheel_daily_summary`
    (scheduled notification) previously computed the same fields inline
    (state, cost_basis, total_premium, open positions with DTE) and only
    differed in how they formatted the per-position lines. This helper
    returns the structured data so each consumer can format it in its
    own style without re-duplicating the gather logic.

    Returns a dict with keys:
        state: cycle state string
        cost_basis: cost_basis per share (float)
        total_premium: total premium collected in dollars (float, × 100)
        open_positions: list of position dicts, each annotated with
            `dte` (int if parseable, else "?")
    """
    state = cycle["state"]
    cost_basis = cycle.get("cost_basis") or 0.0
    total_premium = (
        (cycle.get("put_premium_received") or 0.0)
        + (cycle.get("covered_call_premiums_collected") or 0.0)
    ) * 100  # per-share × 100 shares = contract total

    open_positions: List[Dict[str, Any]] = []
    for p in positions:
        if p.get("status") != "OPEN":
            continue
        try:
            expiry = _date.fromisoformat(p["expiry_date"])
            dte: Any = max(0, (expiry - today).days)
        except (ValueError, TypeError):
            dte = "?"
        p_with_dte = dict(p)
        p_with_dte["dte"] = dte
        open_positions.append(p_with_dte)

    return {
        "state": state,
        "cost_basis": cost_basis,
        "total_premium": total_premium,
        "open_positions": open_positions,
    }


def format_assignment_message(
    cost_basis: float,
    strike: float,
    call_signal: Optional[Any],
) -> str:
    """Format the notification sent when a sold put was assigned.

    Args:
        cost_basis: per-share cost basis after deducting put premium.
        strike: the put strike that was assigned.
        call_signal: CallSignal if a profitable covered call can be sold,
            or None if no strike above cost basis is profitable.
    """
    if call_signal is None:
        return (
            "*PUT ASSIGNMENT DETECTED*\n\n"
            f"Shares acquired: 100 IBIT\n"
            f"Strike: ${strike:.2f}\n"
            f"Cost basis: ${cost_basis:.2f}/share\n\n"
            "No profitable call strikes above cost basis. "
            "Will re-check when conditions improve."
        )
    return (
        "*PUT ASSIGNMENT DETECTED*\n\n"
        f"Shares acquired: 100 IBIT\n"
        f"Strike: ${strike:.2f}\n"
        f"Cost basis: ${cost_basis:.2f}/share\n\n"
        f"Suggesting covered call: ${call_signal.strike:.2f} strike, "
        f"{call_signal.dte} DTE, ${call_signal.premium:.2f} premium..."
    )


def format_called_away_message(last_cycle: Dict[str, Any]) -> str:
    """Format the full-cycle summary when shares were called away.

    Computes annualized return from the cycle's realized P&L, capital at
    risk (strike × 100), and days held.

    Args:
        last_cycle: The just-closed cycle dict from get_cycle_history(limit=1).
    """
    pnl = last_cycle.get("realized_pnl", 0.0) or 0.0
    opened_at = last_cycle.get("opened_at", "")
    closed_at = last_cycle.get("closed_at", "")
    put_premium = (last_cycle.get("put_premium_received") or 0.0) * 100
    call_premiums = (last_cycle.get("covered_call_premiums_collected") or 0.0) * 100

    # Annualized return calculation
    days_in_cycle = 1  # minimum to avoid division by zero
    if opened_at and closed_at:
        try:
            from datetime import datetime as _dt

            d_open = _dt.fromisoformat(opened_at)
            d_close = _dt.fromisoformat(closed_at)
            days_in_cycle = max((d_close - d_open).days, 1)
        except (ValueError, TypeError):
            pass

    capital_at_risk = (last_cycle.get("put_strike") or 50.0) * 100
    annualized_return = (
        (pnl / capital_at_risk) * (365 / days_in_cycle) * 100 if capital_at_risk > 0 else 0.0
    )

    return (
        "*SHARES CALLED AWAY -- CYCLE COMPLETE*\n\n"
        f"Put premium: ${put_premium:.2f}\n"
        f"Call premiums: ${call_premiums:.2f}\n"
        f"Total P&L: ${pnl:.2f}\n"
        f"Days in cycle: {days_in_cycle}\n"
        f"Annualized return: {annualized_return:.1f}%\n\n"
        "Wheel cycle complete. Ready for next put signal."
    )


def compute_called_away_metrics(last_cycle: Dict[str, Any]) -> Dict[str, float]:
    """Return structured metrics for the called-away cycle.

    Used for audit logging alongside format_called_away_message.
    """
    pnl = last_cycle.get("realized_pnl", 0.0) or 0.0
    opened_at = last_cycle.get("opened_at", "")
    closed_at = last_cycle.get("closed_at", "")

    days_in_cycle = 1
    if opened_at and closed_at:
        try:
            from datetime import datetime as _dt

            d_open = _dt.fromisoformat(opened_at)
            d_close = _dt.fromisoformat(closed_at)
            days_in_cycle = max((d_close - d_open).days, 1)
        except (ValueError, TypeError):
            pass

    capital_at_risk = (last_cycle.get("put_strike") or 50.0) * 100
    annualized_return = (
        (pnl / capital_at_risk) * (365 / days_in_cycle) * 100 if capital_at_risk > 0 else 0.0
    )

    return {
        "realized_pnl": pnl,
        "days_in_cycle": float(days_in_cycle),
        "annualized_return": annualized_return,
    }


def format_call_expired_otm_message(
    cost_basis: float,
    call_signal: Optional[Any],
) -> str:
    """Format the notification when a covered call expired out-of-the-money.

    Args:
        cost_basis: per-share cost basis of the held shares.
        call_signal: CallSignal for the next suggested covered call, or
            None if no profitable strike exists above cost basis.
    """
    if call_signal is None:
        return (
            "*COVERED CALL EXPIRED (OTM)*\n\n"
            f"Shares kept. Premium already collected.\n"
            f"Cost basis: ${cost_basis:.2f}/share\n\n"
            "No profitable call strikes above cost basis. "
            "Will re-check when conditions improve."
        )
    return (
        "*COVERED CALL EXPIRED (OTM)*\n\n"
        f"Shares kept. Premium already collected.\n"
        f"Cost basis: ${cost_basis:.2f}/share\n\n"
        f"Suggesting new covered call: ${call_signal.strike:.2f} strike, "
        f"{call_signal.dte} DTE, ${call_signal.premium:.2f} premium..."
    )


def format_put_expired_otm_message(realized_pnl: float) -> str:
    """Format the notification when a sold put expired worthless."""
    return (
        "*PUT EXPIRED WORTHLESS (OTM)*\n\n"
        f"Full premium kept\n"
        f"Realized P&L: ${realized_pnl:.2f}\n\n"
        "Cycle complete. Ready for next put signal."
    )
