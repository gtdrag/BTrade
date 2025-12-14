#!/usr/bin/env python3
"""
Run comprehensive backtests on BITX (2x leveraged Bitcoin ETF).
Compare to IBIT results.
"""

import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent))


@dataclass
class BacktestTrade:
    """Record of a single backtest trade."""

    date: date
    direction: str
    strategy: str
    entry_price: float
    exit_price: float
    shares: int
    dollar_pnl: float
    percentage_pnl: float
    reason: str


@dataclass
class BacktestResults:
    """Results from backtesting a strategy."""

    ticker: str
    strategy_name: str
    start_date: date
    end_date: date
    initial_capital: float
    trades: List[BacktestTrade] = field(default_factory=list)

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

        self.total_return = sum(t.dollar_pnl for t in self.trades)
        self.total_return_pct = (
            (self.total_return / self.initial_capital * 100) if self.initial_capital > 0 else 0
        )

        returns = [t.percentage_pnl for t in self.trades]
        self.avg_return_pct = np.mean(returns) if returns else 0
        self.best_trade_pct = max(returns) if returns else 0
        self.worst_trade_pct = min(returns) if returns else 0

        if len(returns) > 1 and np.std(returns) > 0:
            self.sharpe_ratio = (np.mean(returns) / np.std(returns)) * np.sqrt(252)


def load_data(ticker: str, start_date: date, end_date: date) -> pd.DataFrame:
    """Load data for a ticker."""
    t = yf.Ticker(ticker)
    df = t.history(start=start_date, end=end_date + timedelta(days=1), interval="1d")
    df = df.reset_index()
    df.columns = [c.lower() for c in df.columns]
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"]).dt.date
    elif "datetime" in df.columns:
        df["date"] = pd.to_datetime(df["datetime"]).dt.date
    return df


def backtest_mean_reversion(
    df: pd.DataFrame,
    ticker: str,
    threshold: float,
    initial_capital: float,
    skip_thursday: bool = True,
) -> BacktestResults:
    """Backtest mean reversion strategy."""
    df = df.copy()
    df["daily_return"] = (df["close"] - df["open"]) / df["open"] * 100
    df["prev_return"] = df["daily_return"].shift(1)
    df["weekday"] = pd.to_datetime(df["date"]).apply(lambda x: x.weekday())

    results = BacktestResults(
        ticker=ticker,
        strategy_name=f"Mean Reversion ({threshold}%)",
        start_date=df["date"].iloc[0],
        end_date=df["date"].iloc[-1],
        initial_capital=initial_capital,
    )

    capital = initial_capital
    slippage_pct = 0.01

    for i, row in df.iterrows():
        if pd.isna(row["prev_return"]):
            continue
        if row["prev_return"] >= threshold:
            continue
        if skip_thursday and row["weekday"] == 3:
            continue

        entry_price = row["open"] * (1 + slippage_pct / 100)
        exit_price = row["close"] * (1 - slippage_pct / 100)
        shares = int(capital // entry_price)

        if shares <= 0:
            continue

        dollar_pnl = (exit_price - entry_price) * shares
        pct_pnl = (exit_price - entry_price) / entry_price * 100

        results.trades.append(
            BacktestTrade(
                date=row["date"],
                direction="long",
                strategy="mean_reversion",
                entry_price=entry_price,
                exit_price=exit_price,
                shares=shares,
                dollar_pnl=dollar_pnl,
                percentage_pnl=pct_pnl,
                reason=f"Prev day: {row['prev_return']:.2f}%",
            )
        )

    first_price = df["open"].iloc[0]
    last_price = df["close"].iloc[-1]
    results.buy_hold_return_pct = (last_price - first_price) / first_price * 100
    results.calculate_metrics()
    return results


def backtest_short_thursday(
    df: pd.DataFrame, ticker: str, initial_capital: float
) -> BacktestResults:
    """Backtest short Thursday strategy."""
    df = df.copy()
    df["weekday"] = pd.to_datetime(df["date"]).apply(lambda x: x.weekday())

    results = BacktestResults(
        ticker=ticker,
        strategy_name="Short Thursday",
        start_date=df["date"].iloc[0],
        end_date=df["date"].iloc[-1],
        initial_capital=initial_capital,
    )

    capital = initial_capital
    slippage_pct = 0.01

    for i, row in df.iterrows():
        if row["weekday"] != 3:
            continue

        entry_price = row["open"] * (1 - slippage_pct / 100)
        exit_price = row["close"] * (1 + slippage_pct / 100)
        shares = int(capital // entry_price)

        if shares <= 0:
            continue

        dollar_pnl = (entry_price - exit_price) * shares
        pct_pnl = (entry_price - exit_price) / entry_price * 100

        results.trades.append(
            BacktestTrade(
                date=row["date"],
                direction="short",
                strategy="short_thursday",
                entry_price=entry_price,
                exit_price=exit_price,
                shares=shares,
                dollar_pnl=dollar_pnl,
                percentage_pnl=pct_pnl,
                reason="Thursday short",
            )
        )

    first_price = df["open"].iloc[0]
    last_price = df["close"].iloc[-1]
    results.buy_hold_return_pct = (last_price - first_price) / first_price * 100
    results.calculate_metrics()
    return results


def backtest_combined(
    df: pd.DataFrame, ticker: str, mr_threshold: float, initial_capital: float
) -> BacktestResults:
    """Backtest combined strategy."""
    df = df.copy()
    df["daily_return"] = (df["close"] - df["open"]) / df["open"] * 100
    df["prev_return"] = df["daily_return"].shift(1)
    df["weekday"] = pd.to_datetime(df["date"]).apply(lambda x: x.weekday())

    results = BacktestResults(
        ticker=ticker,
        strategy_name=f"Combined (MR: {mr_threshold}%)",
        start_date=df["date"].iloc[0],
        end_date=df["date"].iloc[-1],
        initial_capital=initial_capital,
    )

    capital = initial_capital
    slippage_pct = 0.01

    for i, row in df.iterrows():
        trade = None

        # Mean reversion takes priority
        if not pd.isna(row["prev_return"]) and row["prev_return"] < mr_threshold:
            entry_price = row["open"] * (1 + slippage_pct / 100)
            exit_price = row["close"] * (1 - slippage_pct / 100)
            shares = int(capital // entry_price)

            if shares > 0:
                dollar_pnl = (exit_price - entry_price) * shares
                pct_pnl = (exit_price - entry_price) / entry_price * 100
                trade = BacktestTrade(
                    date=row["date"],
                    direction="long",
                    strategy="combined_mr",
                    entry_price=entry_price,
                    exit_price=exit_price,
                    shares=shares,
                    dollar_pnl=dollar_pnl,
                    percentage_pnl=pct_pnl,
                    reason=f"MR: prev {row['prev_return']:.2f}%",
                )

        # Short Thursday if no MR signal
        elif row["weekday"] == 3:
            entry_price = row["open"] * (1 - slippage_pct / 100)
            exit_price = row["close"] * (1 + slippage_pct / 100)
            shares = int(capital // entry_price)

            if shares > 0:
                dollar_pnl = (entry_price - exit_price) * shares
                pct_pnl = (entry_price - exit_price) / entry_price * 100
                trade = BacktestTrade(
                    date=row["date"],
                    direction="short",
                    strategy="combined_thu",
                    entry_price=entry_price,
                    exit_price=exit_price,
                    shares=shares,
                    dollar_pnl=dollar_pnl,
                    percentage_pnl=pct_pnl,
                    reason="Short Thursday",
                )

        if trade:
            results.trades.append(trade)

    first_price = df["open"].iloc[0]
    last_price = df["close"].iloc[-1]
    results.buy_hold_return_pct = (last_price - first_price) / first_price * 100
    results.calculate_metrics()
    return results


def run_comparison(
    ticker1: str, ticker2: str, start_date: date, end_date: date, initial_capital: float = 10000.0
):
    """Run comparison between two tickers."""
    print(f"\nLoading {ticker1} data...")
    df1 = load_data(ticker1, start_date, end_date)
    print(f"Loaded {len(df1)} days for {ticker1}")

    print(f"\nLoading {ticker2} data...")
    df2 = load_data(ticker2, start_date, end_date)
    print(f"Loaded {len(df2)} days for {ticker2}")

    # Find common date range
    dates1 = set(df1["date"])
    dates2 = set(df2["date"])
    common_dates = dates1 & dates2

    if not common_dates:
        print("No overlapping dates found!")
        return

    min_date = min(common_dates)
    max_date = max(common_dates)

    print(f"\nCommon date range: {min_date} to {max_date} ({len(common_dates)} days)")

    # Filter to common dates
    df1 = df1[df1["date"].isin(common_dates)].sort_values("date").reset_index(drop=True)
    df2 = df2[df2["date"].isin(common_dates)].sort_values("date").reset_index(drop=True)

    results = {}

    # Run all strategies on both tickers
    for ticker, df in [(ticker1, df1), (ticker2, df2)]:
        print(f"\nRunning backtests on {ticker}...")

        results[f"{ticker}_mr_2"] = backtest_mean_reversion(df, ticker, -2.0, initial_capital)
        results[f"{ticker}_mr_3"] = backtest_mean_reversion(df, ticker, -3.0, initial_capital)
        results[f"{ticker}_short_thu"] = backtest_short_thursday(df, ticker, initial_capital)
        results[f"{ticker}_combined_2"] = backtest_combined(df, ticker, -2.0, initial_capital)
        results[f"{ticker}_combined_3"] = backtest_combined(df, ticker, -3.0, initial_capital)

    return results, df1, df2


def print_comparison_table(results: Dict[str, BacktestResults], ticker1: str, ticker2: str):
    """Print side-by-side comparison."""
    strategies = ["mr_2", "mr_3", "short_thu", "combined_2", "combined_3"]
    strategy_names = {
        "mr_2": "Mean Reversion (-2%)",
        "mr_3": "Mean Reversion (-3%)",
        "short_thu": "Short Thursday",
        "combined_2": "Combined (-2%)",
        "combined_3": "Combined (-3%)",
    }

    print("\n" + "=" * 100)
    print(f"{'STRATEGY COMPARISON':^100}")
    print("=" * 100)

    header = f"{'Strategy':<25} | {'':^35} | {'':^35}"
    print(header)
    print(f"{'':<25} | {ticker1:^35} | {ticker2:^35}")
    print("-" * 100)

    for strat in strategies:
        r1 = results.get(f"{ticker1}_{strat}")
        r2 = results.get(f"{ticker2}_{strat}")

        if r1 and r2:
            name = strategy_names[strat]
            col1 = f"{r1.total_return_pct:+.1f}% ({r1.win_rate:.0f}% win, {r1.total_trades} trades)"
            col2 = f"{r2.total_return_pct:+.1f}% ({r2.win_rate:.0f}% win, {r2.total_trades} trades)"
            print(f"{name:<25} | {col1:^35} | {col2:^35}")

    print("-" * 100)

    # Buy and hold comparison
    r1 = results.get(f"{ticker1}_combined_2")
    r2 = results.get(f"{ticker2}_combined_2")
    if r1 and r2:
        print(
            f"{'Buy & Hold':<25} | {r1.buy_hold_return_pct:+.1f}%{' ':^28} | {r2.buy_hold_return_pct:+.1f}%{' ':^28}"
        )


def main():
    print("=" * 80)
    print("IBIT vs BITX STRATEGY COMPARISON")
    print("(IBIT = 1x Bitcoin ETF, BITX = 2x Leveraged Bitcoin ETF)")
    print("=" * 80)

    # BITX launched April 2024
    start_date = date(2024, 4, 15)
    end_date = date.today()
    initial_capital = 10000.0

    print(f"\nPeriod: {start_date} to {end_date}")
    print(f"Initial Capital: ${initial_capital:,.2f}")

    results, df_ibit, df_bitx = run_comparison(
        "IBIT", "BITX", start_date, end_date, initial_capital
    )

    print_comparison_table(results, "IBIT", "BITX")

    # Detailed results
    print("\n" + "=" * 80)
    print("DETAILED RESULTS")
    print("=" * 80)

    for key in sorted(results.keys()):
        r = results[key]
        print(f"\n--- {key} ---")
        print(f"Trades: {r.total_trades} (Long: {r.long_trades}, Short: {r.short_trades})")
        print(f"Win Rate: {r.win_rate:.1f}%")
        print(f"Total Return: {r.total_return_pct:+.1f}%")
        print(f"Avg Return/Trade: {r.avg_return_pct:+.2f}%")
        print(f"Best Trade: {r.best_trade_pct:+.2f}%")
        print(f"Worst Trade: {r.worst_trade_pct:+.2f}%")
        print(f"Sharpe Ratio: {r.sharpe_ratio:.2f}")
        print(f"Buy & Hold: {r.buy_hold_return_pct:+.1f}%")
        print(f"vs B&H: {r.total_return_pct - r.buy_hold_return_pct:+.1f}%")

    # Risk comparison
    print("\n" + "=" * 80)
    print("RISK COMPARISON (Best Trade vs Worst Trade)")
    print("=" * 80)

    for strat in ["combined_2", "combined_3"]:
        r1 = results.get(f"IBIT_{strat}")
        r2 = results.get(f"BITX_{strat}")
        if r1 and r2:
            print(f"\n{strat}:")
            print(f"  IBIT: Best {r1.best_trade_pct:+.2f}% / Worst {r1.worst_trade_pct:+.2f}%")
            print(f"  BITX: Best {r2.best_trade_pct:+.2f}% / Worst {r2.worst_trade_pct:+.2f}%")
            print(
                f"  Leverage effect: ~{abs(r2.worst_trade_pct / r1.worst_trade_pct):.1f}x on worst trade"
            )


if __name__ == "__main__":
    main()
