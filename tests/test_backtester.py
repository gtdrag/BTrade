"""
Unit tests for IBIT Dip Bot backtester module.
"""

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.backtester import (
    BacktestConfig,
    Backtester,
    BacktestResult,
    BacktestTrade,
    run_default_backtest,
)


class TestBacktestConfig:
    """Test BacktestConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = BacktestConfig()

        assert config.regular_threshold == 0.6
        assert config.monday_enabled is False
        assert config.initial_capital == 10000.0
        assert config.commission == 0.0

    def test_custom_config(self):
        """Test custom configuration."""
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 6, 30),
            regular_threshold=0.8,
            monday_enabled=True,
            initial_capital=50000,
        )

        assert config.start_date == date(2024, 1, 1)
        assert config.end_date == date(2024, 6, 30)
        assert config.regular_threshold == 0.8
        assert config.initial_capital == 50000


class TestBacktestTrade:
    """Test BacktestTrade dataclass."""

    def test_trade_creation(self):
        """Test creating a trade record."""
        trade = BacktestTrade(
            date=date(2024, 6, 15),
            day_of_week="Tuesday",
            open_price=50.0,
            entry_price=49.5,
            exit_price=50.5,
            dip_percentage=1.0,
            shares=100,
            dollar_pnl=100.0,
            percentage_pnl=2.02,
            cumulative_pnl=100.0,
        )

        assert trade.date == date(2024, 6, 15)
        assert trade.shares == 100
        assert trade.dollar_pnl == 100.0


class TestBacktestResult:
    """Test BacktestResult class."""

    @pytest.fixture
    def sample_trades(self):
        """Create sample trades for testing."""
        return [
            BacktestTrade(
                date=date(2024, 6, 15),
                day_of_week="Tuesday",
                open_price=50.0,
                entry_price=49.5,
                exit_price=50.5,
                dip_percentage=1.0,
                shares=100,
                dollar_pnl=100.0,
                percentage_pnl=2.02,
                cumulative_pnl=100.0,
            ),
            BacktestTrade(
                date=date(2024, 6, 16),
                day_of_week="Wednesday",
                open_price=51.0,
                entry_price=50.5,
                exit_price=50.0,
                dip_percentage=0.98,
                shares=100,
                dollar_pnl=-50.0,
                percentage_pnl=-0.99,
                cumulative_pnl=50.0,
            ),
            BacktestTrade(
                date=date(2024, 6, 17),
                day_of_week="Thursday",
                open_price=50.5,
                entry_price=50.0,
                exit_price=51.0,
                dip_percentage=0.99,
                shares=100,
                dollar_pnl=100.0,
                percentage_pnl=2.0,
                cumulative_pnl=150.0,
            ),
        ]

    def test_result_statistics(self, sample_trades):
        """Test result statistics calculation."""
        config = BacktestConfig()
        result = BacktestResult(config=config, trades=sample_trades)

        assert result.total_trades == 3
        assert result.winning_trades == 2
        assert result.losing_trades == 1
        assert result.win_rate == pytest.approx(66.67, rel=0.01)
        assert result.total_return == 150.0

    def test_result_to_dataframe(self, sample_trades):
        """Test converting result to DataFrame."""
        config = BacktestConfig()
        result = BacktestResult(config=config, trades=sample_trades)

        df = result.to_dataframe()

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 3
        assert "date" in df.columns
        assert "dollar_pnl" in df.columns

    def test_result_summary(self, sample_trades):
        """Test generating summary text."""
        config = BacktestConfig()
        result = BacktestResult(config=config, trades=sample_trades)

        summary = result.summary()

        assert "Backtest Results" in summary
        assert "Total Trades: 3" in summary
        assert "Win Rate:" in summary


class TestBacktester:
    """Test Backtester class."""

    @pytest.fixture
    def sample_data(self):
        """Create sample OHLCV data."""
        dates = pd.date_range(start="2024-06-01", end="2024-06-30", freq="B")

        data = []
        price = 50.0

        for d in dates:
            # Simulate price movement
            change = np.random.uniform(-0.02, 0.02)
            open_price = price
            low_price = price * (1 - abs(np.random.uniform(0, 0.015)))
            high_price = price * (1 + abs(np.random.uniform(0, 0.015)))
            close_price = price * (1 + change)

            data.append(
                {
                    "date": d.date(),
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                    "volume": 1000000,
                }
            )

            price = close_price

        return pd.DataFrame(data)

    def test_backtester_initialization(self):
        """Test backtester initialization."""
        config = BacktestConfig(start_date=date(2024, 6, 1), end_date=date(2024, 6, 30))
        backtester = Backtester(config)

        assert backtester.config.start_date == date(2024, 6, 1)
        assert backtester.config.end_date == date(2024, 6, 30)

    def test_run_backtest(self, sample_data):
        """Test running backtest with sample data."""
        config = BacktestConfig(
            start_date=date(2024, 6, 1),
            end_date=date(2024, 6, 30),
            regular_threshold=0.5,  # Lower threshold for more trades in test
        )
        backtester = Backtester(config)

        result = backtester.run(data=sample_data)

        assert isinstance(result, BacktestResult)
        assert result.config == config
        # May or may not have trades depending on random data
        assert result.total_trades >= 0

    def test_optimize_threshold(self, sample_data):
        """Test threshold optimization."""
        config = BacktestConfig(start_date=date(2024, 6, 1), end_date=date(2024, 6, 30))
        backtester = Backtester(config)
        backtester._data = sample_data

        optimal, results = backtester.optimize_threshold(
            thresholds=[0.3, 0.5, 0.7, 0.9], metric="return"
        )

        assert optimal in [0.3, 0.5, 0.7, 0.9]
        assert len(results) == 4
        assert all(isinstance(r, BacktestResult) for r in results.values())


class TestRunDefaultBacktest:
    """Test the convenience function."""

    def test_run_default_backtest_with_dates(self):
        """Test running default backtest with custom dates."""
        # Run backtest with yfinance data
        result = run_default_backtest(
            start_date=date(2024, 6, 1), end_date=date(2024, 6, 30), threshold=0.6
        )

        # Verify result structure
        assert isinstance(result, BacktestResult)
        assert result.config.start_date == date(2024, 6, 1)
        assert result.config.end_date == date(2024, 6, 30)
        assert result.config.regular_threshold == 0.6


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
