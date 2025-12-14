"""
Unit tests for IBIT Dip Bot strategy logic.
"""

import pytest
from datetime import datetime, date, timedelta
from unittest.mock import Mock, patch, MagicMock
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.strategy import (
    IBITDipStrategy, StrategyConfig, StrategyState, TradeAction, TradeSignal
)
from src.etrade_client import MockETradeClient
from src.database import Database
from src.utils import ET


class TestStrategyConfig:
    """Test StrategyConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = StrategyConfig()

        assert config.regular_threshold == 0.6
        assert config.monday_threshold == 1.0
        assert config.monday_enabled is False
        assert config.max_position_pct == 100.0
        assert config.dry_run is False

    def test_custom_config(self):
        """Test custom configuration values."""
        config = StrategyConfig(
            regular_threshold=0.8,
            monday_enabled=True,
            monday_threshold=1.5,
            max_position_usd=5000
        )

        assert config.regular_threshold == 0.8
        assert config.monday_enabled is True
        assert config.monday_threshold == 1.5
        assert config.max_position_usd == 5000


class TestIBITDipStrategy:
    """Test IBITDipStrategy class."""

    @pytest.fixture
    def mock_client(self):
        """Create mock E*TRADE client."""
        client = MockETradeClient(initial_cash=10000)
        client.set_mock_price("IBIT", 50.0)
        return client

    @pytest.fixture
    def mock_db(self, tmp_path):
        """Create test database."""
        db_path = tmp_path / "test_trades.db"
        return Database(db_path)

    @pytest.fixture
    def strategy(self, mock_client, mock_db):
        """Create strategy instance for testing."""
        config = StrategyConfig(dry_run=True)
        return IBITDipStrategy(
            client=mock_client,
            config=config,
            db=mock_db,
            account_id_key="mock_key_001"
        )

    def test_strategy_initialization(self, strategy):
        """Test strategy initializes correctly."""
        assert strategy.client is not None
        assert strategy.config is not None
        assert strategy.db is not None

    def test_get_state(self, strategy):
        """Test getting strategy state."""
        state = strategy.get_state()

        assert isinstance(state, StrategyState)
        assert state.has_position is False
        assert state.is_paused is False

    @patch('src.strategy.get_et_now')
    @patch('src.strategy.is_trading_day')
    def test_analyze_no_trading_day(self, mock_trading_day, mock_now, strategy):
        """Test analysis on non-trading day."""
        mock_trading_day.return_value = False
        mock_now.return_value = datetime(2025, 12, 14, 10, 30, tzinfo=ET)  # Saturday

        signal = strategy.analyze()

        assert signal.action == TradeAction.HOLD
        assert "Not a trading day" in signal.reason

    @patch('src.strategy.get_et_now')
    @patch('src.strategy.is_trading_day')
    @patch('src.strategy.is_monday')
    def test_analyze_monday_disabled(self, mock_monday, mock_trading_day, mock_now, strategy):
        """Test that Monday trading is disabled by default."""
        mock_trading_day.return_value = True
        mock_monday.return_value = True
        mock_now.return_value = datetime(2025, 12, 15, 10, 30, tzinfo=ET)

        # Store an open price
        strategy.db.store_open_price(date(2025, 12, 15), 50.0)

        signal = strategy.analyze()

        assert signal.action == TradeAction.HOLD
        assert "Monday trading disabled" in signal.reason

    @patch('src.strategy.get_et_now')
    @patch('src.strategy.is_trading_day')
    @patch('src.strategy.is_monday')
    @patch('src.strategy.is_in_dip_window')
    def test_analyze_dip_below_threshold(self, mock_dip_window, mock_monday, mock_trading_day, mock_now, strategy):
        """Test analysis when dip is below threshold."""
        mock_trading_day.return_value = True
        mock_monday.return_value = False
        mock_dip_window.return_value = True
        mock_now.return_value = datetime(2025, 12, 16, 10, 30, tzinfo=ET)

        # Set prices: 0.3% dip (below 0.6% threshold)
        strategy.client.set_mock_price("IBIT", 49.85)  # 0.3% below 50
        strategy.db.store_open_price(date(2025, 12, 16), 50.0)

        signal = strategy.analyze()

        assert signal.action == TradeAction.HOLD
        assert "< threshold" in signal.reason

    def test_capture_open_price(self, strategy):
        """Test capturing open price."""
        with patch('src.strategy.is_trading_day', return_value=True):
            with patch('src.strategy.get_et_now') as mock_now:
                mock_now.return_value = datetime(2025, 12, 16, 9, 30, tzinfo=ET)

                price = strategy.capture_open_price()

                assert price is not None
                assert price > 0

    def test_force_buy(self, strategy):
        """Test force buy functionality."""
        result = strategy.force_buy()

        assert result.get("success") is True
        assert result.get("action") == "BUY"
        assert result.get("shares") > 0

    def test_force_sell_no_position(self, strategy):
        """Test force sell with no position."""
        result = strategy.force_sell()

        assert result.get("success") is False
        assert "No position" in result.get("reason", "")

    def test_force_sell_with_position(self, strategy):
        """Test force sell with open position."""
        # First buy
        buy_result = strategy.force_buy()
        assert buy_result.get("success") is True

        # Then sell
        sell_result = strategy.force_sell()
        assert sell_result.get("success") is True
        assert sell_result.get("action") == "SELL"


class TestTradeSignal:
    """Test TradeSignal dataclass."""

    def test_hold_signal(self):
        """Test creating a hold signal."""
        signal = TradeSignal(
            action=TradeAction.HOLD,
            reason="No dip detected"
        )

        assert signal.action == TradeAction.HOLD
        assert signal.shares == 0

    def test_buy_signal(self):
        """Test creating a buy signal."""
        signal = TradeSignal(
            action=TradeAction.BUY,
            reason="Dip threshold met",
            dip_percentage=0.8,
            shares=100,
            price=49.5,
            threshold_used=0.6
        )

        assert signal.action == TradeAction.BUY
        assert signal.shares == 100
        assert signal.dip_percentage == 0.8

    def test_sell_signal(self):
        """Test creating a sell signal."""
        signal = TradeSignal(
            action=TradeAction.SELL,
            reason="Market close",
            shares=100,
            price=50.5
        )

        assert signal.action == TradeAction.SELL
        assert signal.shares == 100


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
