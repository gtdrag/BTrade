"""
Critical tests for TradingBot.

These tests verify core trading logic that handles real money:
- Signal execution flow
- Duplicate trade prevention
- Position management
- Approval handling
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database import Database
from src.etrade_client import MockETradeClient
from src.smart_strategy import Signal, TodaySignal
from src.trading_bot import ApprovalMode, BotConfig, TradeResult, TradingBot, TradingMode


@pytest.fixture
def mock_db(tmp_path):
    """Create test database."""
    db_path = tmp_path / "test_trades.db"
    return Database(db_path)


@pytest.fixture
def mock_client():
    """Create mock E*TRADE client."""
    client = MockETradeClient(initial_cash=10000)
    client.set_mock_price("SBIT", 50.0)
    client.set_mock_price("BITU", 50.0)
    client.set_mock_price("IBIT", 50.0)
    return client


@pytest.fixture
def trading_bot(mock_client, mock_db):
    """Create trading bot for testing."""
    config = BotConfig(
        mode=TradingMode.PAPER,
        approval_mode=ApprovalMode.AUTO_EXECUTE,  # Skip approval for most tests
        account_id_key="test_account",
    )
    bot = TradingBot(
        config=config,
        client=mock_client,
        db=mock_db,
    )
    return bot


class TestExecuteSignalCashSignal:
    """Test CASH signal handling."""

    def test_cash_signal_returns_no_trade(self, trading_bot):
        """CASH signal should return success with no trade."""
        signal = TodaySignal(
            signal=Signal.CASH,
            etf="CASH",
            reason="No trading conditions met",
        )

        result = trading_bot.execute_signal(signal)

        assert result.success is True
        assert result.action == "NONE"
        assert result.etf == "CASH"

    def test_cash_signal_does_not_place_order(self, trading_bot, mock_client):
        """CASH signal should not attempt to place any orders."""
        signal = TodaySignal(
            signal=Signal.CASH,
            etf="CASH",
            reason="No trading conditions met",
        )

        # Track if place_order was called
        mock_client.place_order = MagicMock()

        trading_bot.execute_signal(signal)

        mock_client.place_order.assert_not_called()


class TestExecuteSignalDuplicatePrevention:
    """Test duplicate trade blocking."""

    def test_blocks_duplicate_crash_day_trade(self, trading_bot):
        """Should block duplicate CRASH_DAY trades."""
        signal = TodaySignal(
            signal=Signal.CRASH_DAY,
            etf="SBIT",
            reason="IBIT down 2%",
        )

        # First trade should succeed
        result1 = trading_bot.execute_signal(signal)
        assert result1.success is True

        # Second trade should be blocked
        result2 = trading_bot.execute_signal(signal)
        assert result2.success is False
        assert "duplicate" in result2.error.lower() or "already traded" in result2.error.lower()

    def test_blocks_duplicate_pump_day_trade(self, trading_bot):
        """Should block duplicate PUMP_DAY trades."""
        signal = TodaySignal(
            signal=Signal.PUMP_DAY,
            etf="BITU",
            reason="IBIT up 2%",
        )

        # First trade should succeed
        result1 = trading_bot.execute_signal(signal)
        assert result1.success is True

        # Second trade should be blocked
        result2 = trading_bot.execute_signal(signal)
        assert result2.success is False
        assert "duplicate" in result2.error.lower() or "already traded" in result2.error.lower()

    def test_allows_different_signal_types_if_no_position_conflict(self, trading_bot):
        """Different signal types should not be blocked by duplicate prevention."""
        # This test verifies that duplicate prevention is per-signal-type,
        # not a global "one trade per day" rule.
        # Note: A second trade may still fail due to capital/position constraints,
        # but it should NOT fail due to "duplicate" blocking.

        crash_signal = TodaySignal(
            signal=Signal.CRASH_DAY,
            etf="SBIT",
            reason="IBIT down 2%",
        )

        # First trade succeeds
        result1 = trading_bot.execute_signal(crash_signal)
        assert result1.success is True

        # Reset position but keep duplicate tracking
        # This simulates: we closed the SBIT position, now want to enter BITU
        trading_bot._paper_positions.clear()
        trading_bot._paper_capital = 10000.0  # Reset capital

        pump_signal = TodaySignal(
            signal=Signal.PUMP_DAY,
            etf="BITU",
            reason="IBIT up 2%",
        )

        # Pump trade should succeed (different signal type, capital restored)
        result2 = trading_bot.execute_signal(pump_signal)
        assert result2.success is True


class TestExecuteSignalExistingPosition:
    """Test behavior when already holding positions."""

    def test_holds_when_already_in_desired_position(self, trading_bot):
        """Should return HOLD when already in the desired ETF."""
        # First, enter SBIT position
        signal1 = TodaySignal(
            signal=Signal.CRASH_DAY,
            etf="SBIT",
            reason="First trade",
        )
        result1 = trading_bot.execute_signal(signal1)
        assert result1.success is True

        # Reset duplicate tracking but keep position
        trading_bot._trades_today.clear()

        # Try to enter SBIT again with different signal
        signal2 = TodaySignal(
            signal=Signal.MEAN_REVERSION,
            etf="SBIT",
            reason="Second signal same ETF",
        )
        result2 = trading_bot.execute_signal(signal2)

        assert result2.success is True
        assert result2.action == "HOLD"
        assert "Already holding" in result2.error


class TestExecuteSignalSkipApproval:
    """Test skip_approval parameter for auto-execution."""

    def test_skip_approval_true_bypasses_telegram(self, trading_bot):
        """skip_approval=True should not request Telegram approval."""
        # Create bot with approval required
        trading_bot.config.approval_mode = ApprovalMode.REQUIRED

        # Mock telegram to track if called
        trading_bot.telegram = MagicMock()
        trading_bot.telegram.request_approval = MagicMock()

        signal = TodaySignal(
            signal=Signal.CRASH_DAY,
            etf="SBIT",
            reason="Crash day - auto execute",
        )

        result = trading_bot.execute_signal(signal, skip_approval=True)

        # Should not have requested approval
        trading_bot.telegram.request_approval.assert_not_called()
        assert result.success is True

    def test_skip_approval_false_requests_telegram(self, trading_bot):
        """skip_approval=False (default) should request Telegram approval."""
        # Create bot with approval required
        trading_bot.config.approval_mode = ApprovalMode.REQUIRED

        # Mock telegram
        from src.telegram_bot import ApprovalResult

        trading_bot.telegram = MagicMock()
        trading_bot.telegram.request_approval = MagicMock(return_value=ApprovalResult.APPROVED)

        signal = TodaySignal(
            signal=Signal.CRASH_DAY,
            etf="SBIT",
            reason="Crash day - needs approval",
        )

        trading_bot.execute_signal(signal, skip_approval=False)

        # Should have requested approval
        trading_bot.telegram.request_approval.assert_called_once()


class TestExecuteSignalPaperMode:
    """Test paper trading mode."""

    def test_paper_mode_tracks_positions(self, trading_bot):
        """Paper mode should track positions correctly."""
        assert trading_bot.is_paper_mode is True

        signal = TodaySignal(
            signal=Signal.CRASH_DAY,
            etf="SBIT",
            reason="Paper trade test",
        )

        result = trading_bot.execute_signal(signal)

        assert result.success is True
        assert result.is_paper is True

        # Should have position tracked
        positions = trading_bot.get_open_positions()
        assert "SBIT" in positions

    def test_paper_mode_returns_paper_flag(self, trading_bot):
        """TradeResult should indicate paper mode."""
        signal = TodaySignal(
            signal=Signal.CRASH_DAY,
            etf="SBIT",
            reason="Paper trade test",
        )

        result = trading_bot.execute_signal(signal)

        assert result.is_paper is True


class TestGetOpenPositions:
    """Test position tracking."""

    def test_no_positions_initially(self, trading_bot):
        """Should have no positions at start."""
        positions = trading_bot.get_open_positions()
        assert positions == {}

    def test_tracks_position_after_buy(self, trading_bot):
        """Should track position after buying."""
        signal = TodaySignal(
            signal=Signal.CRASH_DAY,
            etf="SBIT",
            reason="Test",
        )

        trading_bot.execute_signal(signal)

        positions = trading_bot.get_open_positions()
        assert "SBIT" in positions
        assert positions["SBIT"]["shares"] > 0


class TestCalculatePositionSize:
    """Test position sizing logic."""

    def test_respects_max_position_pct(self, trading_bot):
        """Should not exceed max position percentage."""
        trading_bot.config.max_position_pct = 50.0  # 50% max
        trading_bot._paper_capital = 10000.0

        # At $50/share, 50% of $10000 = $5000 = 100 shares max
        shares = trading_bot.calculate_position_size(50.0)

        assert shares <= 100

    def test_returns_whole_shares(self, trading_bot):
        """Should return whole number of shares."""
        shares = trading_bot.calculate_position_size(47.33)

        assert isinstance(shares, int)
        assert shares == int(shares)


class TestTradeResultDataclass:
    """Test TradeResult dataclass."""

    def test_trade_result_has_required_fields(self):
        """TradeResult should have all required fields."""
        result = TradeResult(
            success=True,
            signal=Signal.CRASH_DAY,
            etf="SBIT",
            action="BUY",
            shares=100,
            price=50.0,
            is_paper=True,
        )

        assert result.success is True
        assert result.signal == Signal.CRASH_DAY
        assert result.etf == "SBIT"
        assert result.action == "BUY"
        assert result.shares == 100
        assert result.price == 50.0
        assert result.is_paper is True

    def test_trade_result_error_field(self):
        """TradeResult should support error field."""
        result = TradeResult(
            success=False,
            signal=Signal.CRASH_DAY,
            etf="SBIT",
            action="BUY",
            error="Test error",
        )

        assert result.success is False
        assert result.error == "Test error"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
