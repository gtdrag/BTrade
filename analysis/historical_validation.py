#!/usr/bin/env python3
"""
Historical Validation of Strategy Signals

Test the Mean Reversion and Short Thursday signals on:
1. IBIT (Jan 2024 - present)
2. Bitcoin BTC-USD (2019 - present, 6 years)
3. Bitcoin BTC-USD (2015 - present, 10 years)

This validates that the patterns are real and not just recent noise.
"""

from datetime import date, timedelta
from typing import Dict, List, Tuple
import pandas as pd
import numpy as np
import yfinance as yf


def load_data(ticker: str, start_date: date, end_date: date) -> pd.DataFrame:
    """Load historical data for a ticker."""
    t = yf.Ticker(ticker)
    df = t.history(start=start_date, end=end_date + timedelta(days=1), interval="1d")
    df = df.reset_index()
    df.columns = [c.lower() for c in df.columns]

    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date']).dt.date
    elif 'datetime' in df.columns:
        df['date'] = pd.to_datetime(df['datetime']).dt.date

    return df


def calculate_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate strategy signals."""
    df = df.copy()

    # Daily return (open to close)
    df['daily_return'] = (df['close'] - df['open']) / df['open'] * 100
    df['prev_return'] = df['daily_return'].shift(1)

    # Day of week
    df['weekday'] = pd.to_datetime(df['date']).apply(lambda x: x.weekday())
    df['day_name'] = pd.to_datetime(df['date']).apply(lambda x: x.strftime('%A'))

    return df


def analyze_mean_reversion(df: pd.DataFrame, threshold: float = -2.0) -> Dict:
    """Analyze mean reversion signal performance."""
    df = df.copy()

    # Find days after big drops
    df['mr_signal'] = df['prev_return'] < threshold
    mr_days = df[df['mr_signal'] == True]

    if len(mr_days) == 0:
        return {'trades': 0}

    returns = mr_days['daily_return'].values

    # Simulate with 2x leverage (like BITX)
    leveraged_returns = returns * 2

    return {
        'trades': len(mr_days),
        'win_rate': (returns > 0).mean() * 100,
        'avg_return': returns.mean(),
        'avg_return_2x': leveraged_returns.mean(),
        'total_return_1x': (np.prod(1 + returns/100) - 1) * 100,
        'total_return_2x': (np.prod(1 + leveraged_returns/100) - 1) * 100,
        'best': returns.max(),
        'worst': returns.min(),
        'std': returns.std()
    }


def analyze_day_of_week(df: pd.DataFrame) -> pd.DataFrame:
    """Analyze returns by day of week."""
    df = df.copy()

    day_stats = []
    for day in range(5):  # Mon-Fri
        day_data = df[df['weekday'] == day]['daily_return']
        day_name = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'][day]

        if len(day_data) > 0:
            day_stats.append({
                'day': day_name,
                'trades': len(day_data),
                'avg_return': day_data.mean(),
                'win_rate': (day_data > 0).mean() * 100,
                'std': day_data.std(),
                'total': (np.prod(1 + day_data/100) - 1) * 100
            })

    return pd.DataFrame(day_stats)


def analyze_short_thursday(df: pd.DataFrame) -> Dict:
    """Analyze shorting on Thursday (inverse returns)."""
    df = df.copy()

    thursdays = df[df['weekday'] == 3]

    if len(thursdays) == 0:
        return {'trades': 0}

    # Inverse returns (what you'd get shorting)
    returns = -thursdays['daily_return'].values

    # Simulate with 2x leverage (like SBIT)
    leveraged_returns = returns * 2

    return {
        'trades': len(thursdays),
        'win_rate': (returns > 0).mean() * 100,
        'avg_return': returns.mean(),
        'avg_return_2x': leveraged_returns.mean(),
        'total_return_1x': (np.prod(1 + returns/100) - 1) * 100,
        'total_return_2x': (np.prod(1 + leveraged_returns/100) - 1) * 100,
        'best': returns.max(),
        'worst': returns.min()
    }


def simulate_combined_strategy(df: pd.DataFrame, mr_threshold: float = -2.0) -> Dict:
    """Simulate the combined strategy on historical data."""
    df = df.copy()
    df = calculate_signals(df)

    capital = 10000.0
    trades = []

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev_ret = df.iloc[i-1]['daily_return']
        weekday = row['weekday']
        daily_ret = row['daily_return']

        signal = None

        # Mean reversion (2x leverage)
        if prev_ret < mr_threshold:
            signal = 'mean_rev'
            trade_return = daily_ret * 2  # 2x leverage
        # Short Thursday (2x inverse)
        elif weekday == 3:
            signal = 'short_thu'
            trade_return = -daily_ret * 2  # 2x inverse

        if signal:
            # Apply slippage
            trade_return = trade_return - 0.04  # 0.02% each way
            capital *= (1 + trade_return/100)

            trades.append({
                'date': row['date'],
                'signal': signal,
                'return': trade_return
            })

    if not trades:
        return {'total_return': 0, 'trades': 0}

    returns = [t['return'] for t in trades]

    return {
        'initial': 10000,
        'final': capital,
        'total_return': (capital - 10000) / 10000 * 100,
        'trades': len(trades),
        'win_rate': sum(1 for r in returns if r > 0) / len(returns) * 100,
        'avg_return': np.mean(returns),
        'sharpe': (np.mean(returns) / np.std(returns)) * np.sqrt(len(returns)) if np.std(returns) > 0 else 0,
        'mr_trades': sum(1 for t in trades if t['signal'] == 'mean_rev'),
        'thu_trades': sum(1 for t in trades if t['signal'] == 'short_thu')
    }


def analyze_by_year(df: pd.DataFrame, mr_threshold: float = -2.0) -> pd.DataFrame:
    """Analyze strategy performance by year."""
    df = df.copy()
    df['year'] = pd.to_datetime(df['date']).apply(lambda x: x.year)

    years = sorted(df['year'].unique())
    results = []

    for year in years:
        year_df = df[df['year'] == year].reset_index(drop=True)
        if len(year_df) < 50:  # Skip partial years
            continue

        result = simulate_combined_strategy(year_df, mr_threshold)
        result['year'] = year
        results.append(result)

    return pd.DataFrame(results)


def main():
    print("="*80)
    print("HISTORICAL VALIDATION OF STRATEGY SIGNALS")
    print("="*80)

    # Test periods
    test_periods = [
        ("IBIT", "IBIT", date(2024, 1, 15), date.today(), "ETF Launch to Present"),
        ("BTC 2 Years", "BTC-USD", date(2023, 1, 1), date.today(), "Recent History"),
        ("BTC 5 Years", "BTC-USD", date(2020, 1, 1), date.today(), "5 Year History"),
        ("BTC 10 Years", "BTC-USD", date(2015, 1, 1), date.today(), "10 Year History"),
    ]

    for name, ticker, start, end, desc in test_periods:
        print(f"\n{'='*80}")
        print(f"{name}: {desc}")
        print(f"Period: {start} to {end}")
        print("="*80)

        df = load_data(ticker, start, end)
        df = calculate_signals(df)
        print(f"Loaded {len(df)} trading days")

        # Day of week analysis
        print(f"\n--- Day of Week Analysis ---")
        dow_stats = analyze_day_of_week(df)
        print(dow_stats.to_string(index=False))

        worst_day = dow_stats.loc[dow_stats['avg_return'].idxmin(), 'day']
        print(f"\nWorst day: {worst_day}")

        # Mean reversion analysis
        print(f"\n--- Mean Reversion Analysis (after -2% days) ---")
        mr_stats = analyze_mean_reversion(df, threshold=-2.0)
        if mr_stats['trades'] > 0:
            print(f"Trades: {mr_stats['trades']}")
            print(f"Win Rate: {mr_stats['win_rate']:.1f}%")
            print(f"Avg Return (1x): {mr_stats['avg_return']:+.2f}%")
            print(f"Avg Return (2x): {mr_stats['avg_return_2x']:+.2f}%")
            print(f"Total Return (1x): {mr_stats['total_return_1x']:+.1f}%")
            print(f"Total Return (2x): {mr_stats['total_return_2x']:+.1f}%")
            print(f"Best/Worst: {mr_stats['best']:+.2f}% / {mr_stats['worst']:+.2f}%")

        # Short Thursday analysis
        print(f"\n--- Short Thursday Analysis ---")
        thu_stats = analyze_short_thursday(df)
        if thu_stats['trades'] > 0:
            print(f"Trades: {thu_stats['trades']}")
            print(f"Win Rate: {thu_stats['win_rate']:.1f}%")
            print(f"Avg Return (1x): {thu_stats['avg_return']:+.2f}%")
            print(f"Avg Return (2x): {thu_stats['avg_return_2x']:+.2f}%")
            print(f"Total Return (1x): {thu_stats['total_return_1x']:+.1f}%")
            print(f"Total Return (2x): {thu_stats['total_return_2x']:+.1f}%")

        # Combined strategy simulation
        print(f"\n--- Combined Strategy Simulation ---")
        combined = simulate_combined_strategy(df)
        print(f"Total Return: {combined['total_return']:+.1f}%")
        print(f"Trades: {combined['trades']} (MR: {combined['mr_trades']}, Thu: {combined['thu_trades']})")
        print(f"Win Rate: {combined['win_rate']:.1f}%")
        print(f"Sharpe Ratio: {combined['sharpe']:.2f}")
        print(f"${combined['initial']:,.0f} → ${combined['final']:,.0f}")

        # Year by year breakdown for longer periods
        if (end - start).days > 365 * 2:
            print(f"\n--- Year by Year Breakdown ---")
            yearly = analyze_by_year(df)
            if len(yearly) > 0:
                print(f"{'Year':<6} {'Return':>10} {'Trades':>8} {'Win%':>8} {'Sharpe':>8}")
                print("-"*45)
                for _, row in yearly.iterrows():
                    print(f"{int(row['year']):<6} {row['total_return']:>+9.1f}% {int(row['trades']):>8} {row['win_rate']:>7.1f}% {row['sharpe']:>8.2f}")

                # Consistency check
                positive_years = (yearly['total_return'] > 0).sum()
                total_years = len(yearly)
                print(f"\nPositive years: {positive_years}/{total_years} ({positive_years/total_years*100:.0f}%)")

    # Final summary
    print("\n" + "="*80)
    print("VALIDATION SUMMARY")
    print("="*80)

    # Load 10-year BTC data for final stats
    btc_df = load_data("BTC-USD", date(2015, 1, 1), date.today())
    btc_df = calculate_signals(btc_df)

    print(f"""
Testing Period: 10 years of Bitcoin data (2015-2025)

MEAN REVERSION (Buy after -2% drop):
""")
    mr = analyze_mean_reversion(btc_df, -2.0)
    print(f"  - {mr['trades']} opportunities over 10 years")
    print(f"  - {mr['win_rate']:.1f}% win rate")
    print(f"  - {mr['avg_return']:+.2f}% average next-day return")
    print(f"  - Pattern is {'CONSISTENT' if mr['win_rate'] > 50 else 'NOT consistent'}")

    print(f"""
SHORT THURSDAY:
""")
    thu = analyze_short_thursday(btc_df)
    print(f"  - {thu['trades']} Thursdays over 10 years")
    print(f"  - {thu['win_rate']:.1f}% win rate when shorting")
    print(f"  - {thu['avg_return']:+.2f}% average return (shorting)")
    print(f"  - Pattern is {'CONSISTENT' if thu['win_rate'] > 50 else 'NOT consistent'}")

    combined = simulate_combined_strategy(btc_df)
    print(f"""
COMBINED STRATEGY (10 Years):
  - Total Return: {combined['total_return']:+.1f}%
  - {combined['trades']} total trades
  - {combined['win_rate']:.1f}% win rate
  - Sharpe Ratio: {combined['sharpe']:.2f}
""")


if __name__ == "__main__":
    main()
