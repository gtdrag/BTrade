"""
Paper-mode wheel smoke test — Stages 2, 3a, and 3c from the v1.0 test plan.

Runs the full wheel cycle end-to-end against a real `Database` +
`MockETradeClient`, bypassing Telegram and the scheduler by driving
`WheelExecutor` and `WheelStrategy` directly. Each stage creates its own
short-lived cycle, verifies DB state at every transition, and prints a
clear PASS/FAIL checkpoint.

What's covered:
  Stage 2  — Full cycle: CASH → SHORT_PUT → HOLDING_SHARES → COVERED_CALL → CASH
  Stage 3a — Profit take: BTC at 60% profit closes the SHORT_PUT cycle
  Stage 3c — DTE alert idempotency: check_dte_warning fires once, never twice

What's NOT covered (use the live Telegram flow for these):
  Stage 3b — Roll suggestion / guard messages (needs live chain mutations)
  Stage 4  — Streamlit dashboard visual rendering (manual)
  Stage 6  — Live E*TRADE dress rehearsal (small capital)

Usage:
    python -m tests.manual.paper_wheel_smoke

Exits 0 on full pass, 1 on any stage failure. Does not touch `trades.db`
or any existing state — every stage runs against a tmp SQLite file that
is deleted on exit.
"""

import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add project root to path so `src.*` imports work when run as a script.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.database import Database
from src.etrade_client import MockETradeClient
from src.utils import get_et_now
from src.wheel_executor import WheelExecutor
from src.wheel_state import WheelState
from src.wheel_strategy import CallSignal, PutSignal, WheelStrategy

# ---------------------------------------------------------------------------
# Terminal output helpers
# ---------------------------------------------------------------------------

GREEN = "\033[0;32m"
RED = "\033[0;31m"
YELLOW = "\033[1;33m"
BLUE = "\033[0;34m"
BOLD = "\033[1m"
RESET = "\033[0m"


class SmokeError(Exception):
    """Raised when a smoke-test assertion fails. Stage loop catches this."""


def ok(msg: str) -> None:
    print(f"  {GREEN}[PASS]{RESET} {msg}")


def fail(msg: str) -> None:
    """Mark the current stage failed and abort it."""
    print(f"  {RED}[FAIL]{RESET} {msg}")
    raise SmokeError(msg)


def section(title: str) -> None:
    print(f"\n{BOLD}{BLUE}── {title} ──{RESET}")


# ---------------------------------------------------------------------------
# Fixture helpers — build signals that match the real dataclasses
# ---------------------------------------------------------------------------


def _make_put_signal(strike: float, expiry: date, premium: float = 1.50) -> PutSignal:
    """Build a PutSignal whose DTE is computed from today → expiry."""
    dte = (expiry - get_et_now().date()).days
    return PutSignal(
        strike=strike,
        expiry_date=expiry.isoformat(),
        expiry_year=expiry.year,
        expiry_month=expiry.month,
        expiry_day=expiry.day,
        delta=-0.25,
        premium=premium,
        dte=dte,
        symbol=f"IBIT{expiry.strftime('%y%m%d')}P{int(strike * 1000):08d}",
        iv=0.35,
        gamma=0.05,
        theta=-0.04,
        vega=0.10,
        max_risk=strike * 100,
        pullback_pct=-2.5,
    )


def _make_call_signal(
    strike: float,
    expiry: date,
    cost_basis: float,
    premium: float = 1.80,
) -> CallSignal:
    """Build a CallSignal whose strike is above cost_basis (required by executor)."""
    dte = (expiry - get_et_now().date()).days
    return CallSignal(
        strike=strike,
        expiry_date=expiry.isoformat(),
        expiry_year=expiry.year,
        expiry_month=expiry.month,
        expiry_day=expiry.day,
        delta=0.28,
        premium=premium,
        dte=dte,
        symbol=f"IBIT{expiry.strftime('%y%m%d')}C{int(strike * 1000):08d}",
        iv=0.38,
        gamma=0.05,
        theta=-0.04,
        vega=0.12,
        total_premium=premium * 100,
        cost_basis=cost_basis,
    )


def _fixed_et_now(d: date) -> MagicMock:
    """Build a get_et_now replacement that reports the given date.

    `detect_and_process_expiry` only calls `.date()` on the result, so a
    MagicMock with a fixed date() return value is sufficient.
    """
    mock = MagicMock()
    mock.date.return_value = d
    return mock


# ---------------------------------------------------------------------------
# Stage 2 — Full wheel cycle
# ---------------------------------------------------------------------------


def stage_2_full_wheel_cycle(db: Database, client: MockETradeClient) -> None:
    section("Stage 2 — Full wheel cycle (CASH → SHORT_PUT → HOLDING_SHARES → COVERED_CALL → CASH)")

    executor = WheelExecutor(client, db, account_id_key="mock_key_001")
    strategy = WheelStrategy(client=client, db=db, account_id_key="mock_key_001")

    put_expiry = date(2026, 5, 15)

    # --- 2.1 Sell the put ---------------------------------------------------
    put_signal = _make_put_signal(strike=50.0, expiry=put_expiry)
    result = executor.execute_put_sell(put_signal)
    if not result.success:
        fail(f"execute_put_sell failed: {result.error}")
    ok("Put order placed (SELL_OPEN @ $1.50)")

    cycle = db.get_active_cycle()
    if cycle is None:
        fail("No active cycle after execute_put_sell")
    if cycle["state"] != WheelState.SHORT_PUT.value:
        fail(f"Expected SHORT_PUT, got {cycle['state']}")
    ok(f"Cycle #{cycle['id']} is in SHORT_PUT state")

    if cycle["put_premium_received"] != 1.50:
        fail(f"Expected put_premium_received=1.50, got {cycle['put_premium_received']}")
    if cycle["put_strike"] != 50.0:
        fail(f"Expected put_strike=50.0, got {cycle['put_strike']}")
    ok("Put strike and premium persisted to DB")

    if not client._options_positions:
        fail("MockETradeClient has no options positions after SELL_OPEN")
    ok("MockETradeClient tracked the short put")

    # --- 2.2 Simulate assignment (put gone + IBIT shares present) -----------
    # Clear the option from E*TRADE: the put has been exercised overnight.
    client._options_positions.clear()
    # Add IBIT shares: the put was exercised → 100 shares delivered at strike.
    client.positions["IBIT"] = {
        "quantity": 100,
        "cost_basis": 50.0,
    }

    # Patch get_et_now so today > expiry_date (detect_and_process_expiry gate).
    after_expiry = put_expiry + timedelta(days=2)
    with patch("src.wheel_strategy.get_et_now", return_value=_fixed_et_now(after_expiry)):
        outcome = strategy.detect_and_process_expiry()

    if outcome != "assigned":
        fail(f"Expected outcome='assigned', got {outcome!r}")
    ok("detect_and_process_expiry returned 'assigned'")

    cycle = db.get_active_cycle()
    if cycle["state"] != WheelState.HOLDING_SHARES.value:
        fail(f"Expected HOLDING_SHARES, got {cycle['state']}")
    ok("Cycle transitioned to HOLDING_SHARES")

    expected_cost_basis = 50.0 - 1.50  # strike − put premium
    if abs(cycle["cost_basis"] - expected_cost_basis) > 0.001:
        fail(f"Expected cost_basis={expected_cost_basis}, got {cycle['cost_basis']}")
    ok(f"Cost basis = ${cycle['cost_basis']:.2f} (strike − premium)")

    # --- 2.3 Sell the covered call (strike > cost basis) --------------------
    call_expiry = date(2026, 6, 19)
    call_signal = _make_call_signal(
        strike=52.0,
        expiry=call_expiry,
        cost_basis=cycle["cost_basis"],
    )
    result = executor.execute_call_sell(call_signal)
    if not result.success:
        fail(f"execute_call_sell failed: {result.error}")
    ok("Call order placed (SELL_OPEN @ $1.80, strike $52 > cost basis $48.50)")

    cycle = db.get_active_cycle()
    if cycle["state"] != WheelState.COVERED_CALL.value:
        fail(f"Expected COVERED_CALL, got {cycle['state']}")
    ok("Cycle transitioned to COVERED_CALL")

    # cost_basis should drop by the call premium
    expected_new_cb = 48.50 - 1.80
    if abs(cycle["cost_basis"] - expected_new_cb) > 0.001:
        fail(f"Expected cost_basis={expected_new_cb}, got {cycle['cost_basis']}")
    ok(f"Cost basis reduced to ${cycle['cost_basis']:.2f} (call premium applied)")

    # --- 2.4 Simulate called-away (call gone + shares gone) -----------------
    client._options_positions.clear()
    client.positions.clear()

    after_call_expiry = call_expiry + timedelta(days=2)
    with patch("src.wheel_strategy.get_et_now", return_value=_fixed_et_now(after_call_expiry)):
        outcome = strategy.detect_and_process_expiry()

    if outcome != "called_away":
        fail(f"Expected outcome='called_away', got {outcome!r}")
    ok("detect_and_process_expiry returned 'called_away'")

    if db.get_active_cycle() is not None:
        fail("Expected no active cycle after called_away")
    ok("No active cycle (cycle is closed)")

    # --- 2.5 Verify realized P&L matches hand math --------------------------
    history = db.get_cycle_history(limit=1)
    if not history:
        fail("No cycle history after called_away")
    last = history[0]
    if last["state"] != "CASH":
        fail(f"Expected closed cycle state=CASH, got {last['state']}")

    # P&L = put premium + call premium + share gain
    #     = $150          + $180         + ($52 − $50) × 100
    #     = $530
    expected_pnl = 150.0 + 180.0 + 200.0
    if abs(last["realized_pnl"] - expected_pnl) > 0.01:
        fail(f"Expected realized_pnl=${expected_pnl}, got ${last['realized_pnl']}")
    ok(f"Realized P&L = ${last['realized_pnl']:.2f} (put $150 + call $180 + shares $200)")


# ---------------------------------------------------------------------------
# Stage 3a — BTC profit take
# ---------------------------------------------------------------------------


def stage_3a_profit_take(db: Database, client: MockETradeClient) -> None:
    section("Stage 3a — Buy-to-close at 60% profit")

    executor = WheelExecutor(client, db, account_id_key="mock_key_001")

    # --- Setup: fresh SHORT_PUT cycle --------------------------------------
    put_expiry = date(2026, 7, 17)
    put_signal = _make_put_signal(strike=50.0, expiry=put_expiry, premium=1.50)
    result = executor.execute_put_sell(put_signal)
    if not result.success:
        fail(f"Setup: execute_put_sell failed: {result.error}")
    ok("Setup: fresh SHORT_PUT cycle created")

    cycle = db.get_active_cycle()
    position = db.get_open_position_for_cycle(cycle["id"])
    if position is None:
        fail("No open position for cycle")

    # --- BTC at 60% profit (ask=0.60 < 0.75 = 50% of premium) --------------
    result = executor.execute_btc(position, cycle, close_price=0.60)
    if not result.success:
        fail(f"execute_btc failed: {result.error}")
    ok("execute_btc succeeded")

    if db.get_active_cycle() is not None:
        fail(f"Expected no active cycle after BTC, got {db.get_active_cycle()['state']}")
    ok("Cycle closed, transitioned to CASH")

    # --- Verify the closed position captured the close price ---------------
    positions = db.get_cycle_positions(cycle["id"])
    closed = [p for p in positions if p["status"] == "CLOSED"]
    if not closed:
        fail("No CLOSED position after BTC")
    if abs(closed[0]["close_premium"] - 0.60) > 0.001:
        fail(f"Expected close_premium=0.60, got {closed[0]['close_premium']}")
    ok(f"Position closed with close_premium=${closed[0]['close_premium']:.2f}")

    # --- Sanity check the audit log entry ----------------------------------
    logs = db.get_logs(limit=20)
    btc_logs = [log for log in logs if log["event"] == "btc_order_placed"]
    if not btc_logs:
        fail("No btc_order_placed audit log entry")
    ok("Audit log recorded btc_order_placed event")


# ---------------------------------------------------------------------------
# Stage 3c — DTE alert idempotency
# ---------------------------------------------------------------------------


def stage_3c_dte_alert_idempotency(db: Database, client: MockETradeClient) -> None:
    section("Stage 3c — DTE alert idempotency (fires once, never twice)")

    executor = WheelExecutor(client, db, account_id_key="mock_key_001")
    strategy = WheelStrategy(client=client, db=db, account_id_key="mock_key_001")

    # --- Setup: cycle with an 18-DTE put (inside the 21-DTE alert window) --
    today = get_et_now().date()
    near_expiry = today + timedelta(days=18)
    put_signal = _make_put_signal(strike=48.0, expiry=near_expiry, premium=1.20)
    result = executor.execute_put_sell(put_signal)
    if not result.success:
        fail(f"Setup: execute_put_sell failed: {result.error}")
    ok("Setup: cycle with 18 DTE created")

    cycle = db.get_active_cycle()
    position = db.get_open_position_for_cycle(cycle["id"])

    # --- First call: alert should fire -------------------------------------
    fires_first = strategy.check_dte_warning(position)
    if not fires_first:
        fail("Expected check_dte_warning=True on first call (DTE=18, not yet sent)")
    ok("check_dte_warning → True on first call")

    # --- Simulate alert delivery -------------------------------------------
    db.mark_dte_alert_sent(position["id"])
    ok("mark_dte_alert_sent called (simulates send_dte_alert)")

    position_reloaded = db.get_open_position_for_cycle(cycle["id"])
    if position_reloaded["dte_alert_sent"] != 1:
        fail(f"Expected dte_alert_sent=1, got {position_reloaded['dte_alert_sent']}")
    ok("DB persisted dte_alert_sent=1")

    # --- Second call: MUST NOT fire ----------------------------------------
    fires_second = strategy.check_dte_warning(position_reloaded)
    if fires_second:
        fail("Expected check_dte_warning=False on second call (idempotency violated)")
    ok("check_dte_warning → False on second call (idempotent)")

    # --- Cleanup: close this cycle so later stages see CASH ---------------
    db.close_wheel_position(position["id"], 0.0)
    db.transition_wheel_state(
        cycle["id"],
        WheelState.CASH,
        "test_cleanup",
        realized_pnl=0.0,
    )
    ok("Cleanup: cycle closed")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print(f"{BOLD}BTrade v1.0 — paper-mode wheel smoke test{RESET}")
    print("DB: temp SQLite  •  Client: MockETradeClient  •  No Telegram, no scheduler")

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "smoke.db"
        db = Database(db_path=db_path)
        client = MockETradeClient()

        stages = [
            ("Stage 2 — Full wheel cycle", lambda: stage_2_full_wheel_cycle(db, client)),
            ("Stage 3a — Profit take (BTC)", lambda: stage_3a_profit_take(db, client)),
            (
                "Stage 3c — DTE alert idempotency",
                lambda: stage_3c_dte_alert_idempotency(db, client),
            ),
        ]

        passed: list[str] = []
        failed: list[tuple[str, str]] = []
        for name, stage_fn in stages:
            try:
                stage_fn()
                passed.append(name)
            except SmokeError as e:
                failed.append((name, str(e)))
            except Exception as e:  # noqa: BLE001 — surface unexpected errors cleanly
                failed.append((name, f"unexpected: {type(e).__name__}: {e}"))

    # --- Summary ------------------------------------------------------------
    print()
    total = len(stages)
    if failed:
        print(f"{BOLD}{RED}━━━ SMOKE TEST FAILED ({len(failed)}/{total}) ━━━{RESET}")
        for name, err in failed:
            print(f"  {RED}✗{RESET} {name}: {err}")
        if passed:
            print(f"  {GREEN}✓ Passed stages:{RESET} {', '.join(passed)}")
        return 1

    print(f"{BOLD}{GREEN}━━━ ALL STAGES PASSED ({total}/{total}) ━━━{RESET}")
    print()
    print(f"{YELLOW}Next steps (manual):{RESET}")
    print("  • Stage 4  — run `streamlit run app.py` and visually check the wheel section")
    print("  • Stage 3b — trigger a roll suggestion via live Telegram flow")
    print("  • Stage 6  — live E*TRADE dress rehearsal (small capital, one contract)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
