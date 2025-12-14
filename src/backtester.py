"""
Backtesting module for IBIT Dip Bot.
Validates strategy performance using historical IBIT data.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path

import pandas as pd
import numpy as np

from .utils import is_market_holiday, ET


logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    """Configuration for backtesting."""
    # Date range
    start_date: date = None
    end_date: date = None

    # Strategy parameters
    regular_threshold: float = 0.6  # Default dip threshold
    monday_threshold: float = 1.0   # Monday threshold (if enabled)
    monday_enabled: bool = False    # Trade on Mondays

    # Capital
    initial_capital: float = 10000.0
    max_position_pct: float = 100.0  # % of capital per trade

    # Costs
    commission: float = 0.0  # Per trade commission
    slippage_pct: float = 0.01  # Estimated slippage %

    def __post_init__(self):
        if self.start_date is None:
            self.start_date = date(2024, 6, 1)
        if self.end_date is None:
            self.end_date = date.today()


@dataclass
class BacktestTrade:
    """Record of a single backtest trade."""
    date: date
    day_of_week: str
    open_price: float
    entry_price: float  # Price at ~10:30 AM
    exit_price: float   # Price at close
    dip_percentage: float
    shares: int
    dollar_pnl: float
    percentage_pnl: float
    cumulative_pnl: float


@dataclass
class BacktestResult:
    """Results from a backtest run."""
    config: BacktestConfig
    trades: List[BacktestTrade]

    # Summary statistics
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0

    total_return: float = 0.0
    total_return_pct: float = 0.0
    avg_return_pct: float = 0.0

    best_trade_pct: float = 0.0
    worst_trade_pct: float = 0.0

    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0

    # Buy and hold comparison
    buy_hold_return_pct: float = 0.0

    # Daily returns for analysis
    daily_returns: List[float] = field(default_factory=list)

    def __post_init__(self):
        if self.trades:
            self._calculate_statistics()

    def _calculate_statistics(self):
        """Calculate summary statistics from trades."""
        if not self.trades:
            return

        self.total_trades = len(self.trades)
        self.winning_trades = sum(1 for t in self.trades if t.percentage_pnl > 0)
        self.losing_trades = self.total_trades - self.winning_trades
        self.win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0

        # Returns
        self.total_return = sum(t.dollar_pnl for t in self.trades)
        self.total_return_pct = (self.total_return / self.config.initial_capital * 100) if self.config.initial_capital > 0 else 0
        self.avg_return_pct = np.mean([t.percentage_pnl for t in self.trades]) if self.trades else 0

        # Best/worst
        returns = [t.percentage_pnl for t in self.trades]
        self.best_trade_pct = max(returns) if returns else 0
        self.worst_trade_pct = min(returns) if returns else 0

        # Max drawdown
        self.max_drawdown_pct = self._calculate_max_drawdown()

        # Sharpe ratio (annualized)
        self.daily_returns = returns
        if len(returns) > 1:
            returns_array = np.array(returns)
            if returns_array.std() > 0:
                # Assume ~250 trading days per year
                self.sharpe_ratio = (returns_array.mean() / returns_array.std()) * np.sqrt(250)

    def _calculate_max_drawdown(self) -> float:
        """Calculate maximum drawdown from peak."""
        if not self.trades:
            return 0.0

        cumulative = [t.cumulative_pnl for t in self.trades]
        peak = cumulative[0]
        max_dd = 0.0

        for value in cumulative:
            if value > peak:
                peak = value
            drawdown = (peak - value) / (self.config.initial_capital + peak) * 100 if peak > 0 else 0
            max_dd = max(max_dd, drawdown)

        return max_dd

    def to_dataframe(self) -> pd.DataFrame:
        """Convert trades to DataFrame."""
        if not self.trades:
            return pd.DataFrame()

        return pd.DataFrame([{
            'date': t.date,
            'day_of_week': t.day_of_week,
            'open_price': t.open_price,
            'entry_price': t.entry_price,
            'exit_price': t.exit_price,
            'dip_pct': t.dip_percentage,
            'shares': t.shares,
            'dollar_pnl': t.dollar_pnl,
            'pct_pnl': t.percentage_pnl,
            'cumulative_pnl': t.cumulative_pnl
        } for t in self.trades])

    def summary(self) -> str:
        """Generate text summary of results."""
        return f"""
Backtest Results
================
Period: {self.config.start_date} to {self.config.end_date}
Threshold: {self.config.regular_threshold}% (Monday: {'Enabled @ ' + str(self.config.monday_threshold) + '%' if self.config.monday_enabled else 'Disabled'})
Initial Capital: ${self.config.initial_capital:,.2f}

Performance
-----------
Total Trades: {self.total_trades}
Win Rate: {self.win_rate:.1f}% ({self.winning_trades}W / {self.losing_trades}L)
Total Return: ${self.total_return:,.2f} ({self.total_return_pct:+.1f}%)
Average Return: {self.avg_return_pct:+.2f}% per trade
Best Trade: {self.best_trade_pct:+.2f}%
Worst Trade: {self.worst_trade_pct:+.2f}%
Max Drawdown: {self.max_drawdown_pct:.1f}%
Sharpe Ratio: {self.sharpe_ratio:.2f}

Buy & Hold Return: {self.buy_hold_return_pct:+.1f}%
Strategy vs B&H: {self.total_return_pct - self.buy_hold_return_pct:+.1f}%
"""


class Backtester:
    """
    Backtesting engine for IBIT dip strategy.

    Uses historical intraday data to simulate trades.
    """

    def __init__(self, config: Optional[BacktestConfig] = None):
        """Initialize backtester."""
        self.config = config or BacktestConfig()
        self._data: Optional[pd.DataFrame] = None

    def load_data(self, source: str = "yahoo") -> pd.DataFrame:
        """
        Load historical IBIT data.

        Args:
            source: Data source - "yahoo", "csv", or path to CSV file

        Returns:
            DataFrame with OHLCV data
        """
        if source == "yahoo":
            self._data = self._load_yahoo_data()
        elif source == "csv" or Path(source).exists():
            self._data = self._load_csv_data(source)
        else:
            raise ValueError(f"Unknown data source: {source}")

        return self._data

    def _load_yahoo_data(self) -> pd.DataFrame:
        """Load data from Yahoo Finance."""
        try:
            import yfinance as yf

            ticker = yf.Ticker("IBIT")
            df = ticker.history(
                start=self.config.start_date,
                end=self.config.end_date + timedelta(days=1),
                interval="1d"
            )

            if df.empty:
                raise ValueError("No data returned from Yahoo Finance")

            df = df.reset_index()
            df.columns = [c.lower() for c in df.columns]
            df['date'] = pd.to_datetime(df['date']).dt.date

            logger.info(f"Loaded {len(df)} days of data from Yahoo Finance")
            return df

        except ImportError:
            raise ImportError("yfinance required for Yahoo data. Install with: pip install yfinance")

    def _load_csv_data(self, path: str) -> pd.DataFrame:
        """Load data from CSV file."""
        df = pd.read_csv(path)
        df.columns = [c.lower() for c in df.columns]

        # Parse date column
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date']).dt.date
        elif 'datetime' in df.columns:
            df['date'] = pd.to_datetime(df['datetime']).dt.date

        # Filter to date range
        df = df[(df['date'] >= self.config.start_date) & (df['date'] <= self.config.end_date)]

        logger.info(f"Loaded {len(df)} days of data from {path}")
        return df

    def run(self, data: Optional[pd.DataFrame] = None) -> BacktestResult:
        """
        Run backtest simulation.

        Args:
            data: Optional DataFrame with OHLCV data. If not provided, loads from default source.

        Returns:
            BacktestResult with trades and statistics
        """
        if data is not None:
            self._data = data
        elif self._data is None:
            self.load_data()

        trades = []
        capital = self.config.initial_capital
        cumulative_pnl = 0.0

        # Get first and last prices for buy & hold comparison
        first_price = None
        last_price = None

        for _, row in self._data.iterrows():
            trade_date = row['date']

            # Skip weekends and holidays
            if trade_date.weekday() >= 5 or is_market_holiday(trade_date):
                continue

            open_price = row['open']
            high_price = row['high']
            low_price = row['low']
            close_price = row['close']

            # Track first/last for buy & hold
            if first_price is None:
                first_price = open_price
            last_price = close_price

            # Determine day of week
            day_of_week = trade_date.strftime('%A')
            is_monday = trade_date.weekday() == 0

            # Skip Monday if disabled
            if is_monday and not self.config.monday_enabled:
                continue

            # Get appropriate threshold
            threshold = self.config.monday_threshold if is_monday else self.config.regular_threshold

            # Calculate the MAXIMUM dip that occurred during the day (open to low)
            # This tells us if a dip opportunity existed
            max_dip_pct = ((open_price - low_price) / open_price) * 100 if open_price > 0 else 0

            # Check if the max dip meets threshold (did the opportunity exist?)
            if max_dip_pct < threshold:
                continue  # No dip opportunity that day

            # A dip occurred! Estimate our entry price.
            # Strategy: We're watching at 10:00-10:59 AM, buy when we see >= threshold dip
            # Realistically, we'd enter near the threshold level, not at the absolute low
            # Entry estimate: Open price minus the threshold amount (we bought when threshold was hit)
            entry_price = open_price * (1 - threshold / 100)

            # The dip we captured is approximately the threshold (what triggered our buy)
            dip_pct = threshold

            # Apply slippage
            entry_price_with_slippage = entry_price * (1 + self.config.slippage_pct / 100)

            # Calculate position size
            position_value = capital * (self.config.max_position_pct / 100)
            shares = int(position_value // entry_price_with_slippage)

            if shares <= 0:
                continue  # Can't afford any shares

            # Exit at close
            exit_price = close_price * (1 - self.config.slippage_pct / 100)

            # Calculate P&L
            dollar_pnl = (exit_price - entry_price_with_slippage) * shares
            dollar_pnl -= self.config.commission * 2  # Entry + exit commission

            pct_pnl = ((exit_price - entry_price_with_slippage) / entry_price_with_slippage) * 100

            cumulative_pnl += dollar_pnl

            trade = BacktestTrade(
                date=trade_date,
                day_of_week=day_of_week,
                open_price=open_price,
                entry_price=entry_price_with_slippage,
                exit_price=exit_price,
                dip_percentage=dip_pct,
                shares=shares,
                dollar_pnl=dollar_pnl,
                percentage_pnl=pct_pnl,
                cumulative_pnl=cumulative_pnl
            )
            trades.append(trade)

        # Calculate buy & hold return
        buy_hold_return_pct = 0.0
        if first_price and last_price and first_price > 0:
            buy_hold_return_pct = ((last_price - first_price) / first_price) * 100

        result = BacktestResult(
            config=self.config,
            trades=trades
        )
        result.buy_hold_return_pct = buy_hold_return_pct

        logger.info(f"Backtest complete: {len(trades)} trades, {result.total_return_pct:+.1f}% return")

        return result

    def optimize_threshold(
        self,
        thresholds: List[float] = None,
        metric: str = "return"
    ) -> Tuple[float, Dict[float, BacktestResult]]:
        """
        Find optimal threshold by testing multiple values.

        Args:
            thresholds: List of thresholds to test (default: 0.3 to 1.5 in 0.1 increments)
            metric: Optimization metric - "return", "sharpe", "win_rate"

        Returns:
            Tuple of (optimal_threshold, dict of threshold -> BacktestResult)
        """
        if thresholds is None:
            thresholds = [round(x * 0.1, 1) for x in range(3, 16)]  # 0.3 to 1.5

        results = {}
        original_threshold = self.config.regular_threshold

        for threshold in thresholds:
            self.config.regular_threshold = threshold
            result = self.run()
            results[threshold] = result
            logger.info(f"Threshold {threshold}%: {result.total_trades} trades, "
                       f"{result.win_rate:.1f}% win rate, {result.total_return_pct:+.1f}% return")

        # Restore original
        self.config.regular_threshold = original_threshold

        # Find optimal
        if metric == "return":
            optimal = max(results.keys(), key=lambda t: results[t].total_return_pct)
        elif metric == "sharpe":
            optimal = max(results.keys(), key=lambda t: results[t].sharpe_ratio)
        elif metric == "win_rate":
            optimal = max(results.keys(), key=lambda t: results[t].win_rate)
        else:
            raise ValueError(f"Unknown metric: {metric}")

        logger.info(f"Optimal threshold ({metric}): {optimal}%")
        return optimal, results

    def compare_configurations(
        self,
        configs: List[BacktestConfig]
    ) -> List[BacktestResult]:
        """
        Compare multiple strategy configurations.

        Args:
            configs: List of BacktestConfig to test

        Returns:
            List of BacktestResult for each config
        """
        results = []
        for config in configs:
            self.config = config
            result = self.run()
            results.append(result)

        return results


def run_default_backtest(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    threshold: float = 0.6,
    monday_enabled: bool = False,
    initial_capital: float = 10000.0
) -> BacktestResult:
    """
    Convenience function to run a backtest with default settings.

    Args:
        start_date: Start date (default: 6 months ago)
        end_date: End date (default: today)
        threshold: Dip threshold (default: 0.6%)
        monday_enabled: Trade on Mondays
        initial_capital: Starting capital

    Returns:
        BacktestResult
    """
    if start_date is None:
        start_date = date.today() - timedelta(days=180)
    if end_date is None:
        end_date = date.today()

    config = BacktestConfig(
        start_date=start_date,
        end_date=end_date,
        regular_threshold=threshold,
        monday_enabled=monday_enabled,
        initial_capital=initial_capital
    )

    backtester = Backtester(config)
    return backtester.run()
