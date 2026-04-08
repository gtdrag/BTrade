"""
WheelStrategy: cash-secured put entry signal generation and assignment detection.

Determines WHEN to sell puts (signal), WHICH puts to sell (strike selection),
WHETHER the account can afford it (cash validation), and WHAT happened at expiry
(assignment vs OTM expiry).

Threat mitigations:
  T-03-01: Log only summary data (pullback_pct, strike, delta) — never raw chain/balance.
  T-03-02: yfinance read-only; signal will fire/not-fire but no damage without approval.
  T-03-03: yfinance failure -> return None with warning log (no signal).
  T-03-04: State check is first operation — early return enforces state machine.
  T-03-11: Assignment requires BOTH: put gone from options AND IBIT shares in equity.
  T-03-12: Idempotency — skip detection if cycle is already HOLDING_SHARES or CASH.
  T-03-13: API failure in detection -> log and return None, do NOT mutate state.
  T-03-14: Every detection outcome logged via db.log_event() for audit trail.
"""

import logging
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional

import yfinance as yf

from src.database import Database
from src.utils import get_et_now
from src.wheel_state import WheelState

logger = logging.getLogger(__name__)


@dataclass
class PutSignal:
    """Signal to sell a cash-secured put on IBIT.

    All price/premium values are per-share (multiply by 100 for total contract value).
    max_risk = strike * 100 (maximum cash required to secure 1 contract).
    pullback_pct < 0 indicates IBIT is below its 5-day high (negative = drop).
    """

    strike: float
    expiry_date: str
    expiry_year: int
    expiry_month: int
    expiry_day: int
    delta: float
    premium: float   # bid price per share
    dte: int
    max_risk: float  # strike * 100
    symbol: str
    iv: float
    gamma: float
    theta: float
    vega: float
    pullback_pct: float  # e.g. -3.2 means IBIT is 3.2% below 5-day high


class WheelStrategy:
    """Decision engine for the wheel strategy's cash-secured put leg.

    Checks three gates in order:
    1. State gate: active cycle must be CASH or absent (no stacking).
    2. Signal gate: IBIT must be >= pullback_threshold below its 5-day high.
    3. Cash gate: available cash must cover strike * 100 (1 contract = 100 shares).

    If all three pass, returns a PutSignal for Telegram approval flow.
    """

    def __init__(
        self,
        client,
        db: Database,
        account_id_key: str = "default",
    ) -> None:
        self.client = client          # ETradeClient or MockETradeClient
        self.db = db
        self.account_id_key = account_id_key
        self.pullback_threshold = -2.0  # IBIT must drop >= 2% from 5-day high
        self.delta_min = 0.20
        self.delta_max = 0.30

    def get_put_signal(self) -> Optional[PutSignal]:
        """Check conditions and return a PutSignal if all gates pass.

        Returns:
            PutSignal if all conditions are met, None otherwise.

        Gate order (T-03-04: state check first):
        1. State gate — no signal if cycle is SHORT_PUT or HOLDING_SHARES.
        2. Price gate — IBIT must be >= 2% below 5-day high.
        3. Strike selection — must find a put in the 0.20-0.30 abs(delta) range.
        4. Cash gate — available cash must be >= selected strike * 100.
        """
        # --- Gate 1: State machine check (T-03-04) ---
        cycle = self.db.get_active_cycle()
        if cycle is not None:
            state = cycle["state"]
            if state in (WheelState.SHORT_PUT.value, WheelState.HOLDING_SHARES.value):
                logger.debug(
                    "WheelStrategy: no signal — active cycle is %s (blocking states)",
                    state,
                )
                return None

        # --- Gate 2: IBIT pullback signal (T-03-03: wrap in try/except) ---
        try:
            history = yf.Ticker("IBIT").history(period="5d")
        except Exception as exc:
            logger.warning("WheelStrategy: yfinance failure — %s; returning no signal", exc)
            return None

        if history is None or history.empty:
            logger.warning(
                "WheelStrategy: yfinance returned empty history for IBIT; returning no signal"
            )
            return None

        five_day_high = float(history["High"].max())
        current_price = float(history["Close"].iloc[-1])

        if five_day_high <= 0:
            logger.warning("WheelStrategy: five_day_high is zero or negative; returning no signal")
            return None

        pullback_pct = (current_price - five_day_high) / five_day_high * 100.0

        if pullback_pct > self.pullback_threshold:
            # Not enough of a pullback (threshold is -2.0; e.g., -1% does not qualify)
            logger.debug(
                "WheelStrategy: no signal — pullback_pct=%.2f%% (threshold=%.1f%%)",
                pullback_pct,
                self.pullback_threshold,
            )
            return None

        # --- Gate 3: Strike selection ---
        contract = self.select_put_strike()
        if contract is None:
            logger.info(
                "WheelStrategy: no signal — no put contract found in delta range %.2f-%.2f",
                self.delta_min,
                self.delta_max,
            )
            return None

        # --- Gate 4: Cash validation ---
        cash = self.client.get_cash_available(self.account_id_key)
        required = contract["strike"] * 100.0
        if cash < required:
            logger.info(
                "WheelStrategy: no signal — insufficient cash (%.0f < %.0f required for strike %.2f)",
                cash,
                required,
                contract["strike"],
            )
            return None

        # All gates passed — build and return the signal (T-03-01: log summary only)
        logger.info(
            "WheelStrategy: PutSignal generated — strike=%.2f delta=%.3f pullback=%.2f%% cash=%.0f",
            contract["strike"],
            contract["delta"],
            pullback_pct,
            cash,
        )

        expiry_date = contract.get("expiry_date")
        # expiry_date may be a date object from MockETradeClient
        if hasattr(expiry_date, "isoformat"):
            expiry_date_str = expiry_date.isoformat()
        else:
            expiry_date_str = str(expiry_date)

        return PutSignal(
            strike=float(contract["strike"]),
            expiry_date=expiry_date_str,
            expiry_year=int(contract["expiry_year"]),
            expiry_month=int(contract["expiry_month"]),
            expiry_day=int(contract["expiry_day"]),
            delta=float(contract["delta"]),
            premium=float(contract["bid"]),
            dte=int(contract["dte"]),
            max_risk=float(contract["strike"]) * 100.0,
            symbol=str(contract["symbol"]),
            iv=float(contract["iv"]),
            gamma=float(contract["gamma"]),
            theta=float(contract["theta"]),
            vega=float(contract["vega"]),
            pullback_pct=pullback_pct,
        )

    def select_put_strike(self) -> Optional[Dict]:
        """Fetch the options chain and select the optimal put strike.

        Selection criteria:
        - option_type == "PUT"
        - abs(delta) is in [delta_min, delta_max] (default 0.20-0.30)
        - highest bid among qualifying contracts (maximum premium income)

        Returns:
            The contract dict with the highest bid in the delta range, or None.
        """
        chain: List[Dict] = self.client.get_ibit_options_chain()

        puts = [c for c in chain if c["option_type"] == "PUT"]
        target_puts = [
            c for c in puts
            if self.delta_min <= abs(c["delta"]) <= self.delta_max
        ]

        if not target_puts:
            return None

        return max(target_puts, key=lambda c: c["bid"])

    def detect_and_process_expiry(self) -> Optional[str]:
        """Check if the active put has expired or been assigned.

        Reconciles the database record against live E*TRADE positions to determine
        whether the sold put was assigned (IBIT shares delivered) or expired
        worthless (OTM). Updates the state machine and logs every outcome.

        Threat mitigations applied:
          T-03-11: Assignment requires BOTH put gone AND IBIT shares present.
          T-03-12: Idempotency — returns None if cycle is already HOLDING_SHARES/CASH.
          T-03-13: API errors are caught; state is NOT mutated on failure.
          T-03-14: Every outcome logged via db.log_event() for audit trail.

        Returns:
            "assigned"     if put was assigned (IBIT shares now in account),
            "expired_otm"  if put expired worthless (no shares),
            None           if no action needed (no cycle, wrong state, not expired,
                           put still live, or API error).
        """
        # --- Step 1: Get active cycle ---
        cycle = self.db.get_active_cycle()
        if cycle is None:
            logger.debug("detect_and_process_expiry: no active cycle, skipping")
            return None

        # --- Step 2: State gate — only act on SHORT_PUT (T-03-12 idempotency) ---
        if cycle["state"] != WheelState.SHORT_PUT.value:
            logger.debug(
                "detect_and_process_expiry: cycle state is %s (not SHORT_PUT), skipping",
                cycle["state"],
            )
            return None

        # --- Step 3: Get open positions ---
        positions = self.db.get_cycle_positions(cycle["id"])
        open_positions = [p for p in positions if p["status"] == "OPEN"]
        if not open_positions:
            logger.warning(
                "detect_and_process_expiry: cycle %d is SHORT_PUT but has no open positions",
                cycle["id"],
            )
            return None

        put_pos = open_positions[0]  # one put per cycle in Phase 3

        # --- Step 4: Check if expiry date has passed ---
        expiry = date.fromisoformat(put_pos["expiry_date"])
        today = get_et_now().date()
        if today <= expiry:
            logger.debug(
                "detect_and_process_expiry: expiry %s not yet passed (today=%s), skipping",
                expiry,
                today,
            )
            return None

        # --- Step 5: Check if put is still live in E*TRADE (T-03-13: wrap API calls) ---
        try:
            live_options = self.client.get_options_positions(self.account_id_key)
        except Exception as exc:
            logger.error(
                "detect_and_process_expiry: get_options_positions failed — %s; "
                "skipping to retry next run (T-03-13)",
                exc,
            )
            return None

        live_symbols = {p["symbol"] for p in live_options}
        if put_pos["symbol"] in live_symbols:
            # Put is still open — settlement not complete yet
            logger.debug(
                "detect_and_process_expiry: put %s still in live positions, not settled yet",
                put_pos["symbol"],
            )
            return None

        # --- Step 6: Determine assignment vs OTM expiry (T-03-11: require BOTH conditions) ---
        try:
            equity_positions = self.client.get_account_positions(self.account_id_key)
        except Exception as exc:
            logger.error(
                "detect_and_process_expiry: get_account_positions failed — %s; "
                "skipping to retry next run (T-03-13)",
                exc,
            )
            return None

        ibit_shares = [
            p for p in equity_positions
            if p.get("Product", {}).get("symbol") == "IBIT"
            and p.get("Product", {}).get("securityType") == "EQ"
        ]

        if ibit_shares:
            # --- ASSIGNMENT PATH ---
            cost_basis = cycle["put_strike"] - cycle["put_premium_received"]
            logger.info(
                "detect_and_process_expiry: PUT ASSIGNED — strike=%.2f premium=%.2f "
                "cost_basis=%.2f",
                cycle["put_strike"],
                cycle["put_premium_received"],
                cost_basis,
            )

            self.db.transition_wheel_state(
                cycle["id"],
                WheelState.HOLDING_SHARES,
                "put_assigned",
                shares_held=100,
                cost_basis=cost_basis,
            )
            self.db.close_wheel_position(put_pos["id"], close_premium=0.0)
            self.db.log_event(
                "INFO",
                "put_assigned",
                {
                    "cycle_id": cycle["id"],
                    "strike": cycle["put_strike"],
                    "cost_basis": cost_basis,
                },
            )
            return "assigned"

        else:
            # --- OTM EXPIRY PATH ---
            realized_pnl = cycle["put_premium_received"] * 100  # 1 contract = 100 shares
            logger.info(
                "detect_and_process_expiry: PUT EXPIRED OTM — premium=%.2f realized_pnl=%.2f",
                cycle["put_premium_received"],
                realized_pnl,
            )

            self.db.transition_wheel_state(
                cycle["id"],
                WheelState.CASH,
                "put_expired_otm",
                realized_pnl=realized_pnl,
            )
            self.db.close_wheel_position(put_pos["id"], close_premium=0.0)
            self.db.log_event(
                "INFO",
                "put_expired_otm",
                {
                    "cycle_id": cycle["id"],
                    "realized_pnl": realized_pnl,
                },
            )
            return "expired_otm"
