"""
WheelStrategy: cash-secured put entry signal, covered call signal generation, and expiry detection.

Determines WHEN to sell puts (signal), WHICH puts to sell (strike selection),
WHETHER the account can afford it (cash validation), WHAT happened at put expiry
(assignment vs OTM expiry), WHEN to sell covered calls (signal), WHICH call strikes
to sell (cost-basis-protected, delta-targeted), and WHAT happened at call expiry
(called away vs OTM expiry).

Also provides the 30-min monitoring layer (Plan 05-01):
  check_profit_target: True when ask <= 50% of premium_received (T-05-02)
  check_position_tested: True when IBIT within 2% of strike
  check_dte_warning: True when DTE <= 21 and dte_alert_sent == 0 (T-05-03)
  select_roll_strike: Picks further-OTM contract in 30-45 DTE range
  run_monitoring_checks: Orchestrates all checks; handles ETradeAPIError (T-05-05)

Threat mitigations:
  T-03-01: Log only summary data (pullback_pct, strike, delta) — never raw chain/balance.
  T-03-02: yfinance read-only; signal will fire/not-fire but no damage without approval.
  T-03-03: yfinance failure -> return None with warning log (no signal).
  T-03-04: State check is first operation — early return enforces state machine.
  T-03-11: Assignment requires BOTH: put gone from options AND IBIT shares in equity.
  T-03-12: Idempotency — skip detection if cycle is already HOLDING_SHARES or CASH.
  T-03-13: API failure in detection -> log and return None, do NOT mutate state.
  T-03-14: Every detection outcome logged via db.log_event() for audit trail.
  T-04-01: Hard filter — only suggest call strikes >= adjusted cost basis.
  T-04-02: Call-away requires BOTH: call gone from options AND IBIT shares gone from equity.
  T-04-03: State gate — only process COVERED_CALL state in call branch; idempotency maintained.
  T-04-04: API calls in call expiry detection wrapped in try/except; no state mutation on failure.
  T-04-05: Every call expiry outcome logged via db.log_event() for audit trail.
  T-04-06: Log only summary data (strike, delta, cost_basis) for call signal — never raw chain.
  T-05-02: Use ask price (conservative) for profit target check — prevents premature triggers.
  T-05-05: get_ibit_options_chain wrapped in try/except ETradeAPIError; return all-False on error.
"""

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional

import yfinance as yf

from .database import Database
from .etrade_client import ETradeAPIError
from .utils import get_et_now
from .wheel_state import WheelState

logger = logging.getLogger(__name__)


@dataclass
class CallSignal:
    """Signal to sell a covered call on IBIT shares already held.

    All price/premium values are per-share (multiply by 100 for total contract value).
    total_premium = premium * 100 (one contract = 100 shares).
    cost_basis is the adjusted cost basis per share at the time of signal generation --
    used for display and re-validation before execution.

    Threat mitigations:
      T-04-01: strike is always >= cost_basis (hard filter in select_call_strike).
      T-04-06: Only summary data (strike, delta, cost_basis) is logged -- never raw chain.
    """

    strike: float
    expiry_date: str
    expiry_year: int
    expiry_month: int
    expiry_day: int
    delta: float
    premium: float        # bid price per share
    dte: int
    total_premium: float  # premium * 100 (one contract)
    symbol: str
    iv: float
    gamma: float
    theta: float
    vega: float
    cost_basis: float     # adjusted cost basis -- for display and re-validation


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
        self.call_delta_min = 0.25
        self.call_delta_max = 0.35
        self.profit_target_pct = 0.50  # Close when 50% of premium captured

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

    def select_call_strike(self, cost_basis: float) -> Optional[Dict]:
        """Fetch the options chain and select the optimal covered call strike.

        Selection criteria:
        - option_type == "CALL"
        - self.call_delta_min <= abs(delta) <= self.call_delta_max (default 0.25-0.35)
        - HARD FILTER: strike >= cost_basis (T-04-01 cost basis protection)
        - highest bid among qualifying contracts (maximum premium income)

        Args:
            cost_basis: Adjusted cost basis per share. Only strikes at or above
                        this value are considered (hard filter, T-04-01).

        Returns:
            The contract dict with the highest bid that passes all filters, or None.
        """
        chain: List[Dict] = self.client.get_ibit_options_chain()

        calls = [c for c in chain if c["option_type"] == "CALL"]
        target_calls = [
            c for c in calls
            if self.call_delta_min <= abs(c["delta"]) <= self.call_delta_max
            and float(c["strike"]) >= cost_basis  # T-04-01: hard cost basis filter
        ]

        if not target_calls:
            return None

        return max(target_calls, key=lambda c: c["bid"])

    def get_call_signal(self) -> Optional[CallSignal]:
        """Check conditions and return a CallSignal if all gates pass.

        Returns:
            CallSignal if all conditions are met, None otherwise.

        Gate order:
        1. State gate — signal only when active cycle is HOLDING_SHARES.
        2. Strike gate — must find a call in the 0.25-0.35 abs(delta) range
                         AND at or above cost_basis (hard filter, T-04-01).
        3. Build signal — no cash gate needed (selling call on existing shares).
        """
        # --- Gate 1: State machine check ---
        cycle = self.db.get_active_cycle()
        if cycle is None:
            logger.debug("WheelStrategy.get_call_signal: no active cycle")
            return None

        state = cycle["state"]
        if state != WheelState.HOLDING_SHARES.value:
            logger.debug(
                "WheelStrategy.get_call_signal: cycle state is %s (must be HOLDING_SHARES)",
                state,
            )
            return None

        cost_basis = float(cycle["cost_basis"] or 0.0)

        # --- Gate 2: Strike selection (T-04-01: cost basis hard filter inside) ---
        contract = self.select_call_strike(cost_basis)
        if contract is None:
            logger.info(
                "WheelStrategy.get_call_signal: no call found in delta range %.2f-%.2f "
                "above cost_basis=%.2f",
                self.call_delta_min,
                self.call_delta_max,
                cost_basis,
            )
            return None

        # --- Gate 3: Build and return signal (T-04-06: log summary only) ---
        logger.info(
            "WheelStrategy.get_call_signal: CallSignal generated — strike=%.2f delta=%.3f "
            "cost_basis=%.2f",
            float(contract["strike"]),
            float(contract["delta"]),
            cost_basis,
        )

        expiry_date = contract.get("expiry_date")
        if hasattr(expiry_date, "isoformat"):
            expiry_date_str = expiry_date.isoformat()
        else:
            expiry_date_str = str(expiry_date)

        premium = float(contract["bid"])
        return CallSignal(
            strike=float(contract["strike"]),
            expiry_date=expiry_date_str,
            expiry_year=int(contract["expiry_year"]),
            expiry_month=int(contract["expiry_month"]),
            expiry_day=int(contract["expiry_day"]),
            delta=float(contract["delta"]),
            premium=premium,
            dte=int(contract["dte"]),
            total_premium=premium * 100.0,
            symbol=str(contract["symbol"]),
            iv=float(contract["iv"]),
            gamma=float(contract["gamma"]),
            theta=float(contract["theta"]),
            vega=float(contract["vega"]),
            cost_basis=cost_basis,
        )

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

        # --- Step 2: Branch on cycle state ---
        state = cycle["state"]

        if state == WheelState.SHORT_PUT.value:
            # ----------------------------------------------------------------
            # SHORT_PUT branch: detect put assignment or OTM expiry
            # ----------------------------------------------------------------

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
                # --- ASSIGNMENT PATH (atomic transition + position close) ---
                cost_basis = self.db.process_put_assignment(
                    cycle_id=cycle["id"],
                    position_id=put_pos["id"],
                    shares_held=100,
                )
                logger.info(
                    "detect_and_process_expiry: PUT ASSIGNED — strike=%.2f premium=%.2f "
                    "cost_basis=%.2f",
                    cycle["put_strike"],
                    cycle["put_premium_received"],
                    cost_basis,
                )
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
                # --- OTM EXPIRY PATH (atomic transition + position close) ---
                realized_pnl = self.db.process_put_otm_expiry(
                    cycle_id=cycle["id"],
                    position_id=put_pos["id"],
                )
                logger.info(
                    "detect_and_process_expiry: PUT EXPIRED OTM — premium=%.2f realized_pnl=%.2f",
                    cycle["put_premium_received"],
                    realized_pnl,
                )
                self.db.log_event(
                    "INFO",
                    "put_expired_otm",
                    {
                        "cycle_id": cycle["id"],
                        "realized_pnl": realized_pnl,
                    },
                )
                return "expired_otm"

        elif state == WheelState.COVERED_CALL.value:
            # ----------------------------------------------------------------
            # COVERED_CALL branch: detect call-away or OTM call expiry
            # (T-04-03: state gate, T-04-02: dual condition for call-away)
            # ----------------------------------------------------------------

            # --- Get open CALL position ---
            positions = self.db.get_cycle_positions(cycle["id"])
            open_positions = [p for p in positions if p["status"] == "OPEN"]
            if not open_positions:
                logger.warning(
                    "detect_and_process_expiry: COVERED_CALL cycle %d has no open positions",
                    cycle["id"],
                )
                return None

            call_pos = open_positions[-1]  # most recent if multiple (shouldn't happen)

            # --- Check if expiry date has passed ---
            expiry = date.fromisoformat(call_pos["expiry_date"])
            today = get_et_now().date()
            if today <= expiry:
                logger.debug(
                    "detect_and_process_expiry: call expiry %s not yet passed (today=%s), "
                    "skipping",
                    expiry,
                    today,
                )
                return None

            # --- Check if call still live in E*TRADE (T-04-04: wrap API) ---
            try:
                live_options = self.client.get_options_positions(self.account_id_key)
            except Exception as exc:
                logger.error(
                    "detect_and_process_expiry: get_options_positions failed — %s; "
                    "skipping to retry next run (T-04-04)",
                    exc,
                )
                return None

            live_symbols = {p["symbol"] for p in live_options}
            if call_pos["symbol"] in live_symbols:
                # Settlement not yet complete
                logger.debug(
                    "detect_and_process_expiry: call %s still in live positions, not settled yet",
                    call_pos["symbol"],
                )
                return None

            # --- Check if IBIT shares still present (T-04-02: dual condition) ---
            try:
                equity_positions = self.client.get_account_positions(self.account_id_key)
            except Exception as exc:
                logger.error(
                    "detect_and_process_expiry: get_account_positions failed — %s; "
                    "skipping to retry next run (T-04-04)",
                    exc,
                )
                return None

            ibit_shares = [
                p for p in equity_positions
                if p.get("Product", {}).get("symbol") == "IBIT"
                and p.get("Product", {}).get("securityType") == "EQ"
            ]

            if not ibit_shares:
                # --- CALLED AWAY: call gone AND shares gone ---
                # Full-cycle P&L = put premium + call premiums + share gain
                # Share gain is measured against the put strike (what we paid
                # on assignment), NOT against the adjusted cost basis — the
                # adjusted cost basis already incorporates the put premium, so
                # using it here would double-count the premium.
                put_premium = (cycle["put_premium_received"] or 0.0) * 100
                call_premiums = (cycle.get("covered_call_premiums_collected") or 0.0) * 100
                call_strike = float(call_pos["strike"])
                put_strike = float(cycle["put_strike"])
                shares_pnl = (call_strike - put_strike) * 100
                total_pnl = put_premium + call_premiums + shares_pnl

                logger.info(
                    "detect_and_process_expiry: CALLED AWAY — call_strike=%.2f "
                    "total_pnl=%.2f (put_prem=%.2f call_prems=%.2f shares_pnl=%.2f)",
                    call_strike,
                    total_pnl,
                    put_premium,
                    call_premiums,
                    shares_pnl,
                )

                self.db.transition_wheel_state(
                    cycle["id"],
                    WheelState.CASH,
                    "called_away",
                    realized_pnl=total_pnl,
                )
                self.db.close_wheel_position(call_pos["id"], close_premium=0.0)
                self.db.log_event(
                    "INFO",
                    "called_away",
                    {
                        "cycle_id": cycle["id"],
                        "call_strike": call_strike,
                        "total_pnl": total_pnl,
                    },
                )  # T-04-05: audit trail
                return "called_away"

            else:
                # --- OTM CALL EXPIRY: call gone, shares remain ---
                logger.info(
                    "detect_and_process_expiry: CALL EXPIRED OTM — cycle %d returns to "
                    "HOLDING_SHARES",
                    cycle["id"],
                )

                self.db.transition_wheel_state(
                    cycle["id"],
                    WheelState.HOLDING_SHARES,
                    "call_expired_otm",
                )
                self.db.close_wheel_position(call_pos["id"], close_premium=0.0)
                self.db.log_event(
                    "INFO",
                    "call_expired_otm",
                    {
                        "cycle_id": cycle["id"],
                    },
                )  # T-04-05: audit trail
                return "call_expired_otm"

        else:
            # Not a state we handle (e.g., CASH, HOLDING_SHARES without a call) -- skip
            logger.debug(
                "detect_and_process_expiry: cycle state is %s, skipping (no action for this state)",
                state,
            )
            return None

    # =========================================================================
    # Profit management monitoring (Phase 05 — Plan 05-01)
    # =========================================================================

    def check_profit_target(self, position: dict, chain: list) -> bool:
        """Return True when the position has reached 50% profit (ask <= 50% of premium_received).

        Uses ask price (T-05-02: conservative — prevents premature buy-to-close triggers
        caused by stale or wide bid/ask spreads).

        Args:
            position: Open options_positions row dict (must have 'symbol', 'premium_received').
            chain: List of contract dicts from get_ibit_options_chain().

        Returns:
            True if profit_pct >= profit_target_pct, False otherwise.
            Returns False if the option symbol is not found in the chain (e.g., outside DTE range).
        """
        symbol = position["symbol"]
        premium_received = float(position["premium_received"])

        # Guard against zero/negative premium — would divide by zero below.
        # A position with no premium received has no profit target to hit.
        if premium_received <= 0:
            logger.warning(
                "check_profit_target: symbol=%s has non-positive premium_received=%.2f, "
                "skipping profit target check",
                symbol, premium_received,
            )
            return False

        # Find our specific contract in the chain by OCC symbol
        contract = next((c for c in chain if c["symbol"] == symbol), None)
        if contract is None:
            logger.debug(
                "check_profit_target: symbol %s not found in chain (outside DTE range?)", symbol
            )
            return False

        current_ask = float(contract["ask"])
        profit_pct = (premium_received - current_ask) / premium_received
        result = profit_pct >= self.profit_target_pct
        logger.debug(
            "check_profit_target: symbol=%s premium=%.2f ask=%.2f profit_pct=%.2f%% target=%.0f%% hit=%s",
            symbol, premium_received, current_ask, profit_pct * 100, self.profit_target_pct * 100, result,
        )
        return result

    def check_position_tested(self, position: dict) -> bool:
        """Return True when IBIT price is within 2% of the option strike price.

        A "tested" position means the underlying is trading close to the strike,
        increasing assignment risk (for puts) or call-away risk (for calls).

        Args:
            position: Open options_positions row dict (must have 'strike').

        Returns:
            True if abs(ibit_price - strike) / strike <= 0.02, False otherwise.
            Returns False on API failure (logged as warning).
        """
        try:
            quote = self.client.get_ibit_quote()
            ibit_price = float(quote["last_price"])
            strike = float(position["strike"])
            distance_pct = abs(ibit_price - strike) / strike
            result = distance_pct <= 0.02
            logger.debug(
                "check_position_tested: ibit=%.2f strike=%.2f distance=%.2f%% tested=%s",
                ibit_price, strike, distance_pct * 100, result,
            )
            return result
        except Exception as exc:
            logger.warning("check_position_tested: failed to get IBIT quote — %s", exc)
            return False

    def check_dte_warning(self, position: dict) -> bool:
        """Return True when the position has 21 or fewer DTE and no alert has been sent.

        Respects the dte_alert_sent flag to prevent repeated alerts (T-05-03).

        Args:
            position: Open options_positions row dict (must have 'expiry_date', 'dte_alert_sent').

        Returns:
            True if DTE <= 21 and dte_alert_sent == 0, False otherwise.
        """
        if position.get("dte_alert_sent", 0) == 1:
            return False
        expiry = date.fromisoformat(position["expiry_date"])
        dte = (expiry - get_et_now().date()).days
        return dte <= 21

    def select_roll_strike(self, current_strike: float, option_type: str) -> Optional[Dict]:
        """Select an appropriate roll-to strike for an existing position.

        Fetches the live options chain and filters for contracts that are:
        - The same option_type as the current position
        - Further OTM than the current strike:
            PUT: strike < current_strike (lower strike = more OTM for puts)
            CALL: strike > current_strike (higher strike = more OTM for calls)
        - Within the 30-45 DTE range (standard wheel roll window)
        - Within the appropriate delta range:
            PUT: abs(delta) in [delta_min, delta_max] (0.20-0.30)
            CALL: abs(delta) in [call_delta_min, call_delta_max] (0.25-0.35)
        Returns the qualifying contract with the highest bid (maximum premium income).

        Args:
            current_strike: Strike price of the existing position.
            option_type: "PUT" or "CALL".

        Returns:
            Contract dict with the highest bid among qualifying contracts, or None.
        """
        chain: List[Dict] = self.client.get_ibit_options_chain()

        # Filter by option type and DTE range
        candidates = [
            c for c in chain
            if c["option_type"] == option_type
            and 30 <= int(c["dte"]) <= 45
        ]

        if option_type == "PUT":
            candidates = [
                c for c in candidates
                if float(c["strike"]) < current_strike
                and self.delta_min <= abs(float(c["delta"])) <= self.delta_max
            ]
        else:  # CALL
            candidates = [
                c for c in candidates
                if float(c["strike"]) > current_strike
                and self.call_delta_min <= abs(float(c["delta"])) <= self.call_delta_max
            ]

        if not candidates:
            logger.debug(
                "select_roll_strike: no qualifying %s contracts further OTM than %.2f in 30-45 DTE",
                option_type, current_strike,
            )
            return None

        return max(candidates, key=lambda c: float(c["bid"]))

    async def run_monitoring_checks(self, cycle: dict) -> Dict[str, Any]:
        """Orchestrate all profit management checks for the active options position.

        Runs in the following priority order (per RESEARCH.md Open Questions #3):
          1. DTE warning — independent of market conditions, always checked.
          2. Profit target — if hit, skip position-tested check (profit takes priority).
          3. Position tested — only checked when profit target is NOT hit.

        Returns a dict that Plan 02 will consume to trigger Telegram notification flows:
          - profit_target_hit: bool
          - position_tested: bool
          - dte_warning: bool
          - position: dict | None (the open position row, for Plan 02 use)
          - chain: list | None (the options chain, for Plan 02 use)

        Threat mitigations:
          T-05-05: get_ibit_options_chain wrapped in try/except ETradeAPIError;
                   returns all-False dict on stale quote error.

        Args:
            cycle: Active wheel cycle dict from get_active_cycle().

        Returns:
            Dict with five keys as described above.
        """
        _empty = {
            "profit_target_hit": False,
            "position_tested": False,
            "dte_warning": False,
            "position": None,
            "chain": None,
        }

        position = self.db.get_open_position_for_cycle(cycle["id"])
        if position is None:
            logger.debug("run_monitoring_checks: no open position for cycle %d, skipping", cycle["id"])
            return _empty

        # T-05-05: wrap chain fetch in try/except; stale quotes must not trigger false actions
        try:
            chain = self.client.get_ibit_options_chain()
        except ETradeAPIError as exc:
            logger.warning(
                "run_monitoring_checks: get_ibit_options_chain failed — %s; "
                "skipping this cycle (T-05-05)",
                exc,
            )
            return _empty

        # Check DTE warning (always evaluated — independent of price)
        dte_warning = self.check_dte_warning(position)

        # Check profit target using ask price (T-05-02: conservative)
        profit_target_hit = self.check_profit_target(position, chain)

        # Check position tested — skipped when profit target already hit (profit takes priority)
        if profit_target_hit:
            position_tested = False
        else:
            position_tested = self.check_position_tested(position)

        logger.debug(
            "run_monitoring_checks: cycle=%d profit_target=%s tested=%s dte_warning=%s",
            cycle["id"], profit_target_hit, position_tested, dte_warning,
        )

        return {
            "profit_target_hit": profit_target_hit,
            "position_tested": position_tested,
            "dte_warning": dte_warning,
            "position": position,
            "chain": chain,
        }
