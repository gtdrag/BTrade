#!/usr/bin/env python3
"""
Final Portfolio Analysis - Detailed trade validation and visualization.

This validates the smart portfolio strategy that achieved +361.8% returns.
"""

from datetime import date, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from enum import Enum

import pandas as pd
import numpy as np
import yfinance as yf


@dataclass
class Trade:
    """Single trade record."""
    date: date
    signal: str
    etf: str
    entry: float
    exit: float
    return_pct: float
    cumulative_value: float


class FinalPortfolioAnalyzer:
    """Analyze the optimal portfolio strategy in detail."""

    def __init__(
        self,
        initial_capital: float = 10000.0,
        mean_rev_threshold: float = -2.0,
        slippage_pct: float = 0.02
    ):
        self.initial_capital = initial_capital
        self.mean_rev_threshold = mean_rev_threshold
        self.slippage_pct = slippage_pct
        self.data: Dict[str, pd.DataFrame] = {}
        self._aligned_data: Optional[pd.DataFrame] = None

    def load_data(self, start_date: date, end_date: date):
        """Load data for all ETFs."""
        tickers = ['IBIT', 'BITX', 'SBIT']

        print("Loading data...")
        for ticker in tickers:
            t = yf.Ticker(ticker)
            df = t.history(start=start_date, end=end_date + timedelta(days=1), interval="1d")
            df = df.reset_index()
            df.columns = [c.lower() for c in df.columns]
            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date']).dt.date
            elif 'datetime' in df.columns:
                df['date'] = pd.to_datetime(df['datetime']).dt.date
                df = df.drop(columns=['datetime'])
            self.data[ticker] = df
            print(f"  {ticker}: {len(df)} days")

        # Align dates
        common_dates = set(self.data['IBIT']['date'])
        for ticker in tickers[1:]:
            common_dates &= set(self.data[ticker]['date'])

        df = pd.DataFrame({'date': sorted(common_dates)})
        for ticker in tickers:
            ticker_df = self.data[ticker][self.data[ticker]['date'].isin(common_dates)]
            ticker_df = ticker_df.sort_values('date')
            df[f'{ticker.lower()}_open'] = ticker_df['open'].values
            df[f'{ticker.lower()}_close'] = ticker_df['close'].values

        df['weekday'] = pd.to_datetime(df['date']).apply(lambda x: x.weekday())
        df['ibit_daily_return'] = (df['ibit_close'] - df['ibit_open']) / df['ibit_open'] * 100
        df['ibit_prev_return'] = df['ibit_daily_return'].shift(1)

        self._aligned_data = df
        print(f"\nTotal trading days: {len(df)}")

    def run_detailed_backtest(self) -> Tuple[List[Trade], Dict]:
        """Run backtest with full trade details."""
        df = self._aligned_data.copy()

        capital = self.initial_capital
        trades: List[Trade] = []

        mr_trades = []
        thu_trades = []
        cash_days = 0

        for i, row in df.iterrows():
            is_thursday = row['weekday'] == 3
            prev_ret = row.get('ibit_prev_return', 0)
            had_big_drop = pd.notna(prev_ret) and prev_ret < self.mean_rev_threshold

            if had_big_drop:
                # Mean reversion - use BITX
                entry = row['bitx_open'] * (1 + self.slippage_pct / 100)
                exit_p = row['bitx_close'] * (1 - self.slippage_pct / 100)
                ret = (exit_p - entry) / entry
                capital *= (1 + ret)

                trade = Trade(
                    date=row['date'],
                    signal=f"Mean Rev (prev: {prev_ret:.1f}%)",
                    etf='BITX',
                    entry=entry,
                    exit=exit_p,
                    return_pct=ret * 100,
                    cumulative_value=capital
                )
                trades.append(trade)
                mr_trades.append(ret)

            elif is_thursday:
                # Short Thursday - use SBIT
                entry = row['sbit_open'] * (1 + self.slippage_pct / 100)
                exit_p = row['sbit_close'] * (1 - self.slippage_pct / 100)
                ret = (exit_p - entry) / entry
                capital *= (1 + ret)

                trade = Trade(
                    date=row['date'],
                    signal="Short Thursday",
                    etf='SBIT',
                    entry=entry,
                    exit=exit_p,
                    return_pct=ret * 100,
                    cumulative_value=capital
                )
                trades.append(trade)
                thu_trades.append(ret)

            else:
                # Cash day
                cash_days += 1

        # Calculate stats
        stats = {
            'initial_capital': self.initial_capital,
            'final_capital': capital,
            'total_return_pct': (capital - self.initial_capital) / self.initial_capital * 100,
            'total_trades': len(trades),
            'cash_days': cash_days,
            'mr_trades': len(mr_trades),
            'mr_win_rate': sum(1 for r in mr_trades if r > 0) / len(mr_trades) * 100 if mr_trades else 0,
            'mr_avg_return': np.mean(mr_trades) * 100 if mr_trades else 0,
            'mr_total_return': (np.prod([1 + r for r in mr_trades]) - 1) * 100 if mr_trades else 0,
            'thu_trades': len(thu_trades),
            'thu_win_rate': sum(1 for r in thu_trades if r > 0) / len(thu_trades) * 100 if thu_trades else 0,
            'thu_avg_return': np.mean(thu_trades) * 100 if thu_trades else 0,
            'thu_total_return': (np.prod([1 + r for r in thu_trades]) - 1) * 100 if thu_trades else 0,
            'ibit_bh': (df['ibit_close'].iloc[-1] - df['ibit_open'].iloc[0]) / df['ibit_open'].iloc[0] * 100,
            'bitx_bh': (df['bitx_close'].iloc[-1] - df['bitx_open'].iloc[0]) / df['bitx_open'].iloc[0] * 100,
        }

        # Calculate Sharpe
        all_returns = [t.return_pct / 100 for t in trades]
        if len(all_returns) > 1 and np.std(all_returns) > 0:
            stats['sharpe'] = (np.mean(all_returns) / np.std(all_returns)) * np.sqrt(len(all_returns))
        else:
            stats['sharpe'] = 0

        # Max drawdown
        peak = self.initial_capital
        max_dd = 0
        for t in trades:
            if t.cumulative_value > peak:
                peak = t.cumulative_value
            dd = (peak - t.cumulative_value) / peak
            max_dd = max(max_dd, dd)
        stats['max_drawdown'] = max_dd * 100

        return trades, stats

    def print_trade_log(self, trades: List[Trade], limit: int = 50):
        """Print detailed trade log."""
        print("\n" + "="*100)
        print("TRADE LOG (All Trades)")
        print("="*100)
        print(f"{'Date':<12} {'Signal':<25} {'ETF':<6} {'Entry':>10} {'Exit':>10} {'Return':>10} {'Cumulative':>12}")
        print("-"*100)

        for t in trades[:limit]:
            print(f"{str(t.date):<12} {t.signal:<25} {t.etf:<6} ${t.entry:>9.2f} ${t.exit:>9.2f} {t.return_pct:>+9.2f}% ${t.cumulative_value:>11,.2f}")

        if len(trades) > limit:
            print(f"... and {len(trades) - limit} more trades ...")

        # Print last 10 trades
        if len(trades) > limit:
            print("\nLast 10 trades:")
            print("-"*100)
            for t in trades[-10:]:
                print(f"{str(t.date):<12} {t.signal:<25} {t.etf:<6} ${t.entry:>9.2f} ${t.exit:>9.2f} {t.return_pct:>+9.2f}% ${t.cumulative_value:>11,.2f}")


def main():
    print("="*80)
    print("FINAL PORTFOLIO ANALYSIS - DETAILED VALIDATION")
    print("="*80)
    print("\nStrategy: Mean Reversion + Short Thursday (no baseline)")
    print("  - Buy BITX (2x) after IBIT drops -2% or more")
    print("  - Buy SBIT (2x inverse) every Thursday")
    print("  - Stay in CASH all other days")

    analyzer = FinalPortfolioAnalyzer(
        initial_capital=10000.0,
        mean_rev_threshold=-2.0
    )

    analyzer.load_data(date(2024, 4, 15), date.today())
    trades, stats = analyzer.run_detailed_backtest()

    print("\n" + "="*80)
    print("PERFORMANCE SUMMARY")
    print("="*80)

    print(f"\nInitial Capital: ${stats['initial_capital']:,.2f}")
    print(f"Final Capital:   ${stats['final_capital']:,.2f}")
    print(f"Total Return:    {stats['total_return_pct']:+.1f}%")
    print(f"Sharpe Ratio:    {stats['sharpe']:.2f}")
    print(f"Max Drawdown:    {stats['max_drawdown']:.1f}%")

    print(f"\n--- Trade Activity ---")
    print(f"Total Trades: {stats['total_trades']}")
    print(f"Cash Days:    {stats['cash_days']}")
    print(f"Active %:     {stats['total_trades'] / (stats['total_trades'] + stats['cash_days']) * 100:.1f}%")

    print(f"\n--- Mean Reversion (BITX 2x) ---")
    print(f"Trades:       {stats['mr_trades']}")
    print(f"Win Rate:     {stats['mr_win_rate']:.1f}%")
    print(f"Avg Return:   {stats['mr_avg_return']:+.2f}%")
    print(f"Total Return: {stats['mr_total_return']:+.1f}%")

    print(f"\n--- Short Thursday (SBIT 2x inverse) ---")
    print(f"Trades:       {stats['thu_trades']}")
    print(f"Win Rate:     {stats['thu_win_rate']:.1f}%")
    print(f"Avg Return:   {stats['thu_avg_return']:+.2f}%")
    print(f"Total Return: {stats['thu_total_return']:+.1f}%")

    print(f"\n--- Benchmark Comparison ---")
    print(f"Smart Portfolio: {stats['total_return_pct']:+.1f}%")
    print(f"IBIT Buy & Hold: {stats['ibit_bh']:+.1f}%")
    print(f"BITX Buy & Hold: {stats['bitx_bh']:+.1f}%")
    print(f"vs IBIT B&H:     {stats['total_return_pct'] - stats['ibit_bh']:+.1f}%")
    print(f"vs BITX B&H:     {stats['total_return_pct'] - stats['bitx_bh']:+.1f}%")

    # Print trade log
    analyzer.print_trade_log(trades, limit=30)

    # Analyze by month
    print("\n" + "="*80)
    print("MONTHLY PERFORMANCE")
    print("="*80)

    monthly = {}
    for t in trades:
        month_key = f"{t.date.year}-{t.date.month:02d}"
        if month_key not in monthly:
            monthly[month_key] = {'returns': [], 'trades': 0, 'mr': 0, 'thu': 0}
        monthly[month_key]['returns'].append(t.return_pct / 100)
        monthly[month_key]['trades'] += 1
        if 'Mean Rev' in t.signal:
            monthly[month_key]['mr'] += 1
        else:
            monthly[month_key]['thu'] += 1

    print(f"\n{'Month':<10} {'Return':>10} {'Trades':>8} {'MR':>6} {'Thu':>6}")
    print("-"*50)

    for month, data in sorted(monthly.items()):
        monthly_ret = (np.prod([1 + r for r in data['returns']]) - 1) * 100
        print(f"{month:<10} {monthly_ret:>+9.1f}% {data['trades']:>8} {data['mr']:>6} {data['thu']:>6}")

    # Win/Loss breakdown
    print("\n" + "="*80)
    print("WIN/LOSS BREAKDOWN")
    print("="*80)

    mr_wins = [t for t in trades if 'Mean Rev' in t.signal and t.return_pct > 0]
    mr_losses = [t for t in trades if 'Mean Rev' in t.signal and t.return_pct <= 0]
    thu_wins = [t for t in trades if 'Thursday' in t.signal and t.return_pct > 0]
    thu_losses = [t for t in trades if 'Thursday' in t.signal and t.return_pct <= 0]

    print(f"\nMean Reversion:")
    print(f"  Wins:   {len(mr_wins)} ({len(mr_wins)/(len(mr_wins)+len(mr_losses))*100:.1f}%)")
    print(f"  Losses: {len(mr_losses)} ({len(mr_losses)/(len(mr_wins)+len(mr_losses))*100:.1f}%)")
    if mr_wins:
        print(f"  Avg Win:  {np.mean([t.return_pct for t in mr_wins]):+.2f}%")
    if mr_losses:
        print(f"  Avg Loss: {np.mean([t.return_pct for t in mr_losses]):+.2f}%")

    print(f"\nShort Thursday:")
    print(f"  Wins:   {len(thu_wins)} ({len(thu_wins)/(len(thu_wins)+len(thu_losses))*100:.1f}%)")
    print(f"  Losses: {len(thu_losses)} ({len(thu_losses)/(len(thu_wins)+len(thu_losses))*100:.1f}%)")
    if thu_wins:
        print(f"  Avg Win:  {np.mean([t.return_pct for t in thu_wins]):+.2f}%")
    if thu_losses:
        print(f"  Avg Loss: {np.mean([t.return_pct for t in thu_losses]):+.2f}%")

    # Final summary
    print("\n" + "="*80)
    print("STRATEGY SUMMARY")
    print("="*80)
    print(f"""
The optimal strategy trades only when high-probability signals appear:

1. MEAN REVERSION (BITX 2x):
   - Trigger: Previous day IBIT dropped -2% or more
   - Action: Buy BITX at open, sell at close
   - Performance: {stats['mr_win_rate']:.0f}% win rate, {stats['mr_total_return']:+.1f}% total

2. SHORT THURSDAY (SBIT 2x inverse):
   - Trigger: It's Thursday
   - Action: Buy SBIT at open, sell at close
   - Performance: {stats['thu_win_rate']:.0f}% win rate, {stats['thu_total_return']:+.1f}% total

3. ALL OTHER DAYS: Stay in CASH

RESULTS:
   - Total Return: {stats['total_return_pct']:+.1f}% vs IBIT B&H {stats['ibit_bh']:+.1f}%
   - Outperformance: {stats['total_return_pct'] - stats['ibit_bh']:+.1f}%
   - Active only {stats['total_trades'] / (stats['total_trades'] + stats['cash_days']) * 100:.0f}% of trading days
   - Max Drawdown: {stats['max_drawdown']:.1f}%
""")


if __name__ == "__main__":
    main()
