"""
Critical tests for SmartStrategy trading logic.

These tests verify the core trading flows that cost real money if broken:
- Config defaults (thresholds, cutoff times)
- Signal types and position actions exist
- TodaySignal dataclass methods
- skip_approval parameter for auto-execution
"""

import sys
from pathlib import Path

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.smart_strategy import (
    CrashDayStatus,
    PositionAction,
    PumpDayStatus,
    Signal,
    SmartStrategy,
    StrategyConfig,
    TodaySignal,
)


class TestCriticalConfigDefaults:
    """Test that critical config values are set correctly."""

    def test_crash_threshold_is_negative_1_5_percent(self):
        """CRITICAL: Crash threshold must be -1.5%."""
        config = StrategyConfig()
        assert config.crash_day_threshold == -1.5, "Crash threshold should be -1.5%"

    def test_pump_threshold_is_positive_1_5_percent(self):
        """CRITICAL: Pump threshold must be +1.5%."""
        config = StrategyConfig()
        assert config.pump_day_threshold == 1.5, "Pump threshold should be +1.5%"

    def test_crash_cutoff_is_3_30_pm(self):
        """CRITICAL: Crash cutoff must be 3:30 PM (15:30), NOT 12:00 PM."""
        config = StrategyConfig()
        assert config.crash_day_cutoff_time == "15:30", "Crash cutoff should be 15:30"

    def test_pump_cutoff_is_3_30_pm(self):
        """CRITICAL: Pump cutoff must be 3:30 PM (15:30), NOT 12:00 PM."""
        config = StrategyConfig()
        assert config.pump_day_cutoff_time == "15:30", "Pump cutoff should be 15:30"

    def test_crash_day_enabled_by_default(self):
        """Crash day should be enabled by default."""
        config = StrategyConfig()
        assert config.crash_day_enabled is True

    def test_pump_day_enabled_by_default(self):
        """Pump day should be enabled by default."""
        config = StrategyConfig()
        assert config.pump_day_enabled is True


class TestSignalTypes:
    """Test all signal types exist and are correctly defined."""

    def test_cash_signal_exists(self):
        """CASH signal type exists."""
        assert hasattr(Signal, "CASH")
        assert Signal.CASH.value == "cash"

    def test_crash_day_signal_exists(self):
        """CRASH_DAY signal type exists."""
        assert hasattr(Signal, "CRASH_DAY")
        assert Signal.CRASH_DAY.value == "crash_day"

    def test_pump_day_signal_exists(self):
        """PUMP_DAY signal type exists."""
        assert hasattr(Signal, "PUMP_DAY")
        assert Signal.PUMP_DAY.value == "pump_day"

    def test_close_long_signal_exists(self):
        """CLOSE_LONG signal type exists (for position awareness)."""
        assert hasattr(Signal, "CLOSE_LONG")
        assert Signal.CLOSE_LONG.value == "close_long"

    def test_close_short_signal_exists(self):
        """CLOSE_SHORT signal type exists (for position awareness)."""
        assert hasattr(Signal, "CLOSE_SHORT")
        assert Signal.CLOSE_SHORT.value == "close_short"

    def test_hold_signal_exists(self):
        """HOLD signal type exists (for position awareness)."""
        assert hasattr(Signal, "HOLD")
        assert Signal.HOLD.value == "hold"

    def test_mean_reversion_signal_exists(self):
        """MEAN_REVERSION signal type exists."""
        assert hasattr(Signal, "MEAN_REVERSION")

    def test_ten_am_dump_signal_exists(self):
        """TEN_AM_DUMP signal type exists."""
        assert hasattr(Signal, "TEN_AM_DUMP")


class TestPositionActionTypes:
    """Test position action types for position awareness."""

    def test_none_action_exists(self):
        """NONE action exists."""
        assert hasattr(PositionAction, "NONE")
        assert PositionAction.NONE.value == "none"

    def test_hold_action_exists(self):
        """HOLD action exists."""
        assert hasattr(PositionAction, "HOLD")
        assert PositionAction.HOLD.value == "hold"

    def test_close_action_exists(self):
        """CLOSE action exists."""
        assert hasattr(PositionAction, "CLOSE")
        assert PositionAction.CLOSE.value == "close"

    def test_switch_action_exists(self):
        """SWITCH action exists."""
        assert hasattr(PositionAction, "SWITCH")
        assert PositionAction.SWITCH.value == "switch"


class TestTodaySignalMethods:
    """Test TodaySignal dataclass methods."""

    def test_should_trade_returns_true_for_crash_day(self):
        """CRASH_DAY signal should trade."""
        signal = TodaySignal(signal=Signal.CRASH_DAY, etf="SBIT", reason="test")
        assert signal.should_trade() is True

    def test_should_trade_returns_true_for_pump_day(self):
        """PUMP_DAY signal should trade."""
        signal = TodaySignal(signal=Signal.PUMP_DAY, etf="BITU", reason="test")
        assert signal.should_trade() is True

    def test_should_trade_returns_false_for_cash(self):
        """CASH signal should NOT trade."""
        signal = TodaySignal(signal=Signal.CASH, etf="CASH", reason="test")
        assert signal.should_trade() is False

    def test_should_trade_returns_false_for_hold(self):
        """HOLD signal should NOT trade."""
        signal = TodaySignal(signal=Signal.HOLD, etf="SBIT", reason="test")
        assert signal.should_trade() is False

    def test_requires_position_change_true_for_switch(self):
        """SWITCH action requires position change."""
        signal = TodaySignal(
            signal=Signal.CLOSE_LONG,
            etf="SBIT",
            reason="test",
            position_action=PositionAction.SWITCH,
        )
        assert signal.requires_position_change() is True

    def test_requires_position_change_true_for_close(self):
        """CLOSE action requires position change."""
        signal = TodaySignal(
            signal=Signal.CLOSE_LONG,
            etf="SBIT",
            reason="test",
            position_action=PositionAction.CLOSE,
        )
        assert signal.requires_position_change() is True

    def test_requires_position_change_false_for_hold(self):
        """HOLD action does NOT require position change."""
        signal = TodaySignal(
            signal=Signal.HOLD, etf="SBIT", reason="test", position_action=PositionAction.HOLD
        )
        assert signal.requires_position_change() is False

    def test_requires_position_change_false_for_none(self):
        """NONE action does NOT require position change."""
        signal = TodaySignal(
            signal=Signal.CRASH_DAY, etf="SBIT", reason="test", position_action=PositionAction.NONE
        )
        assert signal.requires_position_change() is False


class TestStrategyStateFlags:
    """Test strategy state tracking flags."""

    def test_crash_day_not_traded_initially(self):
        """Crash day traded flag should be False initially."""
        strategy = SmartStrategy(config=StrategyConfig())
        assert strategy._crash_day_traded_today is False

    def test_pump_day_not_traded_initially(self):
        """Pump day traded flag should be False initially."""
        strategy = SmartStrategy(config=StrategyConfig())
        assert strategy._pump_day_traded_today is False

    def test_mark_crash_day_traded_sets_flag(self):
        """mark_crash_day_traded() should set the flag to True."""
        strategy = SmartStrategy(config=StrategyConfig())
        strategy.mark_crash_day_traded()
        assert strategy._crash_day_traded_today is True

    def test_mark_pump_day_traded_sets_flag(self):
        """mark_pump_day_traded() should set the flag to True."""
        strategy = SmartStrategy(config=StrategyConfig())
        strategy.mark_pump_day_traded()
        assert strategy._pump_day_traded_today is True

    def test_crash_and_pump_flags_independent(self):
        """Crash and pump flags should be independent (for whipsaw scenarios)."""
        strategy = SmartStrategy(config=StrategyConfig())

        # Trade pump
        strategy.mark_pump_day_traded()
        assert strategy._pump_day_traded_today is True
        assert strategy._crash_day_traded_today is False  # Still False!

        # Trade crash
        strategy.mark_crash_day_traded()
        assert strategy._pump_day_traded_today is True
        assert strategy._crash_day_traded_today is True


class TestExecuteSignalSkipApproval:
    """Test the skip_approval parameter for auto-execution."""

    def test_skip_approval_parameter_exists(self):
        """CRITICAL: execute_signal must have skip_approval parameter."""
        import inspect

        from src.trading_bot import TradingBot

        sig = inspect.signature(TradingBot.execute_signal)
        params = list(sig.parameters.keys())

        assert "skip_approval" in params, "skip_approval parameter missing!"

    def test_skip_approval_defaults_to_false(self):
        """CRITICAL: skip_approval must default to False (require approval)."""
        import inspect

        from src.trading_bot import TradingBot

        sig = inspect.signature(TradingBot.execute_signal)
        default = sig.parameters["skip_approval"].default

        assert default is False, "skip_approval must default to False for safety"


class TestSchedulerAutoExecute:
    """Test that scheduler uses skip_approval=True for crash/pump."""

    def test_crash_day_check_uses_skip_approval(self):
        """CRITICAL: Crash day check must pass skip_approval=True."""

        with open("src/smart_scheduler.py") as f:
            content = f.read()

        # Check that crash day execute_signal call includes skip_approval=True
        assert (
            "execute_signal(signal, skip_approval=True)" in content
            or "execute_signal(signal,skip_approval=True)" in content.replace(" ", "")
        ), "Crash day check must use skip_approval=True"

    def test_pump_day_check_uses_skip_approval(self):
        """CRITICAL: Pump day check must pass skip_approval=True."""
        # Count occurrences of skip_approval=True in scheduler
        with open("src/smart_scheduler.py") as f:
            content = f.read()

        count = content.count("skip_approval=True")
        assert count >= 2, f"Expected at least 2 skip_approval=True (crash+pump), found {count}"


class TestCrashDayStatusDataclass:
    """Test CrashDayStatus dataclass."""

    def test_crash_day_status_has_required_fields(self):
        """CrashDayStatus must have all required fields."""
        status = CrashDayStatus(
            is_triggered=True,
            current_drop_pct=-2.5,
            ibit_open=100.0,
            ibit_current=97.5,
            trigger_time="11:30",
            already_traded_today=False,
        )

        assert status.is_triggered is True
        assert status.current_drop_pct == -2.5
        assert status.ibit_open == 100.0
        assert status.ibit_current == 97.5
        assert status.already_traded_today is False


class TestPumpDayStatusDataclass:
    """Test PumpDayStatus dataclass."""

    def test_pump_day_status_has_required_fields(self):
        """PumpDayStatus must have all required fields."""
        status = PumpDayStatus(
            is_triggered=True,
            current_gain_pct=2.5,
            ibit_open=100.0,
            ibit_current=102.5,
            trigger_time="11:30",
            already_traded_today=False,
        )

        assert status.is_triggered is True
        assert status.current_gain_pct == 2.5
        assert status.ibit_open == 100.0
        assert status.ibit_current == 102.5
        assert status.already_traded_today is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
