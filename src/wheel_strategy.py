"""
WheelStrategy: cash-secured put entry signal generation.

Determines WHEN to sell puts (signal), WHICH puts to sell (strike selection),
and WHETHER the account can afford it (cash validation).

Threat mitigations:
  T-03-01: Log only summary data (pullback_pct, strike, delta) — never raw chain/balance.
  T-03-02: yfinance read-only; signal will fire/not-fire but no damage without approval.
  T-03-03: yfinance failure -> return None with warning log (no signal).
  T-03-04: State check is first operation — early return enforces state machine.
"""

import logging
from dataclasses import dataclass
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
