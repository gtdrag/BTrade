"""
Multi-Strategy Backtester for IBIT Bot.

Backtests all identified profitable strategies:
1. Mean Reversion - Buy after big down days
2. Short Thursday - Short on Thursdays
3. Intraday Bounce - Buy after big intraday drops
4. Trend Following - MA crossover
5. Combined - Multiple signals
"""

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .strategies import (
    DailyData,
    StrategyType,
)

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    """Record of a single backtest trade."""

    date: date
    direction: str  # "long" or "short"
    strategy: str
    entry_price: float
    exit_price: float
    shares: int
    dollar_pnl: float
    percentage_pnl: float
    reason: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BacktestResults:
    """Results from backtesting a strategy."""

    strategy_name: str
    strategy_type: StrategyType
    start_date: date
    end_date: date
    initial_capital: float

    trades: List[BacktestTrade] = field(default_factory=list)

    # Computed metrics
    total_trades: int = 0
    long_trades: int = 0
    short_trades: int = 0
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

    buy_hold_return_pct: float = 0.0

    def calculate_metrics(self):
        """Calculate all metrics from trades."""
        if not self.trades:
            return

        self.total_trades = len(self.trades)
        self.long_trades = sum(1 for t in self.trades if t.direction == "long")
        self.short_trades = sum(1 for t in self.trades if t.direction == "short")
        self.winning_trades = sum(1 for t in self.trades if t.percentage_pnl > 0)
        self.losing_trades = self.total_trades - self.winning_trades

        self.win_rate = (
            (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0
        )

        # Returns
        self.total_return = sum(t.dollar_pnl for t in self.trades)
        self.total_return_pct = (
            (self.total_return / self.initial_capital * 100) if self.initial_capital > 0 else 0
        )

        returns = [t.percentage_pnl for t in self.trades]
        self.avg_return_pct = np.mean(returns) if returns else 0
        self.best_trade_pct = max(returns) if returns else 0
        self.worst_trade_pct = min(returns) if returns else 0

        # Max drawdown
        self.max_drawdown_pct = self._calculate_max_drawdown()

        # Sharpe ratio
        if len(returns) > 1 and np.std(returns) > 0:
            self.sharpe_ratio = (np.mean(returns) / np.std(returns)) * np.sqrt(252)

    def _calculate_max_drawdown(self) -> float:
        """Calculate maximum drawdown."""
        if not self.trades:
            return 0.0

        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0

        for trade in self.trades:
            cumulative += trade.dollar_pnl
            if cumulative > peak:
                peak = cumulative
            drawdown = (peak - cumulative) / (self.initial_capital + peak) * 100 if peak > 0 else 0
            max_dd = max(max_dd, drawdown)

        return max_dd

    def to_dataframe(self) -> pd.DataFrame:
        """Convert trades to DataFrame."""
        if not self.trades:
            return pd.DataFrame()

        return pd.DataFrame(
            [
                {
                    "date": t.date,
                    "direction": t.direction,
                    "strategy": t.strategy,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "shares": t.shares,
                    "dollar_pnl": t.dollar_pnl,
                    "pct_pnl": t.percentage_pnl,
                    "reason": t.reason,
                }
                for t in self.trades
            ]
        )

    def summary(self) -> str:
        """Generate text summary."""
        return f"""
Strategy: {self.strategy_name}
Period: {self.start_date} to {self.end_date}
Initial Capital: ${self.initial_capital:,.2f}

Performance
-----------
Total Trades: {self.total_trades} (Long: {self.long_trades}, Short: {self.short_trades})
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


class MultiStrategyBacktester:
    """
    Backtester supporting multiple strategy types.
    """

    def __init__(
        self, initial_capital: float = 10000.0, commission: float = 0.0, slippage_pct: float = 0.01
    ):
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage_pct = slippage_pct
        self._data: Optional[pd.DataFrame] = None

    def load_data(self, start_date: date, end_date: date) -> pd.DataFrame:
        """Load IBIT data from Yahoo Finance."""
        try:
            import yfinance as yf

            ticker = yf.Ticker("IBIT")
            df = ticker.history(start=start_date, end=end_date + timedelta(days=1), interval="1d")

            if df.empty:
                raise ValueError("No data returned")

            df = df.reset_index()
            df.columns = [c.lower() for c in df.columns]

            # Normalize date column
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"]).dt.date
            elif "datetime" in df.columns:
                df["date"] = pd.to_datetime(df["datetime"]).dt.date

            self._data = df
            logger.info(f"Loaded {len(df)} days of data")
            return df

        except ImportError:
            raise ImportError("yfinance required. Install with: pip install yfinance")

    def _prepare_daily_data(self) -> List[DailyData]:
        """Convert DataFrame to list of DailyData."""
        if self._data is None:
            return []

        daily_data = []
        for _, row in self._data.iterrows():
            daily_data.append(
                DailyData(
                    date=row["date"],
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=row["close"],
                    volume=int(row.get("volume", 0)),
                )
            )

        return daily_data

    def backtest_mean_reversion(
        self,
        threshold: float = -3.0,
        skip_thursday: bool = True,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> BacktestResults:
        """
        Backtest Mean Reversion strategy.

        Buy at open after a day with return < threshold.
        Sell at close same day.
        """
        if self._data is None:
            if start_date and end_date:
                self.load_data(start_date, end_date)
            else:
                raise ValueError("No data loaded")

        df = self._data.copy()
        df["daily_return"] = (df["close"] - df["open"]) / df["open"] * 100
        df["prev_return"] = df["daily_return"].shift(1)
        df["weekday"] = pd.to_datetime(df["date"]).apply(lambda x: x.weekday())

        results = BacktestResults(
            strategy_name=f"Mean Reversion ({threshold}%)",
            strategy_type=StrategyType.MEAN_REVERSION,
            start_date=df["date"].iloc[0],
            end_date=df["date"].iloc[-1],
            initial_capital=self.initial_capital,
        )

        capital = self.initial_capital

        for i, row in df.iterrows():
            if pd.isna(row["prev_return"]):
                continue

            # Check signal conditions
            if row["prev_return"] >= threshold:
                continue  # Previous day not down enough

            if skip_thursday and row["weekday"] == 3:
                continue  # Skip Thursday

            # Execute trade
            entry_price = row["open"] * (1 + self.slippage_pct / 100)
            exit_price = row["close"] * (1 - self.slippage_pct / 100)

            shares = int(capital // entry_price)
            if shares <= 0:
                continue

            dollar_pnl = (exit_price - entry_price) * shares - self.commission * 2
            pct_pnl = (exit_price - entry_price) / entry_price * 100

            trade = BacktestTrade(
                date=row["date"],
                direction="long",
                strategy="mean_reversion",
                entry_price=entry_price,
                exit_price=exit_price,
                shares=shares,
                dollar_pnl=dollar_pnl,
                percentage_pnl=pct_pnl,
                reason=f"Prev day: {row['prev_return']:.2f}%",
                metadata={"threshold": threshold, "prev_return": row["prev_return"]},
            )
            results.trades.append(trade)

        # Calculate buy & hold
        first_price = df["open"].iloc[0]
        last_price = df["close"].iloc[-1]
        results.buy_hold_return_pct = (last_price - first_price) / first_price * 100

        results.calculate_metrics()
        return results

    def backtest_short_thursday(
        self, start_date: Optional[date] = None, end_date: Optional[date] = None
    ) -> BacktestResults:
        """
        Backtest Short Thursday strategy.

        Short at open on Thursday, cover at close.
        """
        if self._data is None:
            if start_date and end_date:
                self.load_data(start_date, end_date)
            else:
                raise ValueError("No data loaded")

        df = self._data.copy()
        df["weekday"] = pd.to_datetime(df["date"]).apply(lambda x: x.weekday())

        results = BacktestResults(
            strategy_name="Short Thursday",
            strategy_type=StrategyType.SHORT_THURSDAY,
            start_date=df["date"].iloc[0],
            end_date=df["date"].iloc[-1],
            initial_capital=self.initial_capital,
        )

        capital = self.initial_capital

        for i, row in df.iterrows():
            # Only trade Thursdays
            if row["weekday"] != 3:
                continue

            # Execute short trade
            entry_price = row["open"] * (1 - self.slippage_pct / 100)  # Short entry
            exit_price = row["close"] * (1 + self.slippage_pct / 100)  # Cover

            shares = int(capital // entry_price)
            if shares <= 0:
                continue

            # Short P&L: profit when price goes down
            dollar_pnl = (entry_price - exit_price) * shares - self.commission * 2
            pct_pnl = (entry_price - exit_price) / entry_price * 100

            trade = BacktestTrade(
                date=row["date"],
                direction="short",
                strategy="short_thursday",
                entry_price=entry_price,
                exit_price=exit_price,
                shares=shares,
                dollar_pnl=dollar_pnl,
                percentage_pnl=pct_pnl,
                reason="Thursday short",
                metadata={"day": "Thursday"},
            )
            results.trades.append(trade)

        # Calculate buy & hold
        first_price = df["open"].iloc[0]
        last_price = df["close"].iloc[-1]
        results.buy_hold_return_pct = (last_price - first_price) / first_price * 100

        results.calculate_metrics()
        return results

    def backtest_combined(
        self,
        mean_reversion_threshold: float = -2.0,
        enable_short_thursday: bool = True,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> BacktestResults:
        """
        Backtest Combined strategy.

        Priority:
        1. Long after big down days (mean reversion)
        2. Short Thursday (if no mean reversion signal)
        """
        if self._data is None:
            if start_date and end_date:
                self.load_data(start_date, end_date)
            else:
                raise ValueError("No data loaded")

        df = self._data.copy()
        df["daily_return"] = (df["close"] - df["open"]) / df["open"] * 100
        df["prev_return"] = df["daily_return"].shift(1)
        df["weekday"] = pd.to_datetime(df["date"]).apply(lambda x: x.weekday())

        results = BacktestResults(
            strategy_name=f"Combined (MR: {mean_reversion_threshold}%, Short Thu: {enable_short_thursday})",
            strategy_type=StrategyType.COMBINED,
            start_date=df["date"].iloc[0],
            end_date=df["date"].iloc[-1],
            initial_capital=self.initial_capital,
        )

        capital = self.initial_capital

        for i, row in df.iterrows():
            trade = None

            # Check mean reversion signal first (takes priority)
            if not pd.isna(row["prev_return"]) and row["prev_return"] < mean_reversion_threshold:
                # Long signal
                entry_price = row["open"] * (1 + self.slippage_pct / 100)
                exit_price = row["close"] * (1 - self.slippage_pct / 100)

                shares = int(capital // entry_price)
                if shares > 0:
                    dollar_pnl = (exit_price - entry_price) * shares - self.commission * 2
                    pct_pnl = (exit_price - entry_price) / entry_price * 100

                    trade = BacktestTrade(
                        date=row["date"],
                        direction="long",
                        strategy="combined_mean_reversion",
                        entry_price=entry_price,
                        exit_price=exit_price,
                        shares=shares,
                        dollar_pnl=dollar_pnl,
                        percentage_pnl=pct_pnl,
                        reason=f"Mean reversion: prev {row['prev_return']:.2f}%",
                        metadata={"trigger": "mean_reversion", "prev_return": row["prev_return"]},
                    )

            # If no mean reversion, check short Thursday
            elif enable_short_thursday and row["weekday"] == 3:
                entry_price = row["open"] * (1 - self.slippage_pct / 100)
                exit_price = row["close"] * (1 + self.slippage_pct / 100)

                shares = int(capital // entry_price)
                if shares > 0:
                    dollar_pnl = (entry_price - exit_price) * shares - self.commission * 2
                    pct_pnl = (entry_price - exit_price) / entry_price * 100

                    trade = BacktestTrade(
                        date=row["date"],
                        direction="short",
                        strategy="combined_short_thursday",
                        entry_price=entry_price,
                        exit_price=exit_price,
                        shares=shares,
                        dollar_pnl=dollar_pnl,
                        percentage_pnl=pct_pnl,
                        reason="Short Thursday",
                        metadata={"trigger": "short_thursday"},
                    )

            if trade:
                results.trades.append(trade)

        # Calculate buy & hold
        first_price = df["open"].iloc[0]
        last_price = df["close"].iloc[-1]
        results.buy_hold_return_pct = (last_price - first_price) / first_price * 100

        results.calculate_metrics()
        return results

    def backtest_all_strategies(
        self, start_date: Optional[date] = None, end_date: Optional[date] = None
    ) -> Dict[str, BacktestResults]:
        """Run backtests on all strategies."""
        if self._data is None:
            if start_date and end_date:
                self.load_data(start_date, end_date)
            else:
                raise ValueError("No data loaded")

        results = {}

        # Mean Reversion variants
        for threshold in [-2.0, -3.0, -4.0]:
            key = f"mean_reversion_{abs(threshold)}"
            results[key] = self.backtest_mean_reversion(threshold=threshold)

        # Short Thursday
        results["short_thursday"] = self.backtest_short_thursday()

        # Combined variants
        for threshold in [-2.0, -3.0]:
            key = f"combined_{abs(threshold)}"
            results[key] = self.backtest_combined(mean_reversion_threshold=threshold)

        return results

    def compare_strategies(self, results: Dict[str, BacktestResults]) -> pd.DataFrame:
        """Create comparison table of strategy results."""
        comparison = []

        for name, result in results.items():
            comparison.append(
                {
                    "Strategy": name,
                    "Trades": result.total_trades,
                    "Win Rate": f"{result.win_rate:.1f}%",
                    "Total Return": f"{result.total_return_pct:+.1f}%",
                    "Avg Return": f"{result.avg_return_pct:+.2f}%",
                    "Best Trade": f"{result.best_trade_pct:+.2f}%",
                    "Worst Trade": f"{result.worst_trade_pct:+.2f}%",
                    "Sharpe": f"{result.sharpe_ratio:.2f}",
                    "vs B&H": f"{result.total_return_pct - result.buy_hold_return_pct:+.1f}%",
                }
            )

        return pd.DataFrame(comparison)


def run_comprehensive_backtest(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    initial_capital: float = 10000.0,
) -> Tuple[Dict[str, BacktestResults], pd.DataFrame]:
    """
    Run comprehensive backtest of all strategies.

    Returns tuple of (results dict, comparison DataFrame)
    """
    if start_date is None:
        start_date = date(2024, 1, 15)  # IBIT launch was Jan 11, 2024
    if end_date is None:
        end_date = date.today()

    backtester = MultiStrategyBacktester(initial_capital=initial_capital)
    backtester.load_data(start_date, end_date)

    results = backtester.backtest_all_strategies()
    comparison = backtester.compare_strategies(results)

    return results, comparison
