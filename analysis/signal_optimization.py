#!/usr/bin/env python3
"""
Signal Optimization Analysis

Test whether additional market signals improve the strategy:
1. VIX (fear index)
2. Bitcoin volatility
3. RSI (momentum)
4. S&P 500 context
5. Volume anomalies

Goal: Ensure we're not leaving edge on the table.
"""

from datetime import date, timedelta
from typing import Dict, List, Tuple
import pandas as pd
import numpy as np
import yfinance as yf


def load_all_data(start_date: date, end_date: date) -> pd.DataFrame:
    """Load IBIT, BITX, SBIT, VIX, and SPY data."""
    tickers = {
        'IBIT': 'IBIT',
        'BITX': 'BITX',
        'SBIT': 'SBIT',
        'VIX': '^VIX',
        'SPY': 'SPY'
    }

    data = {}
    print("Loading data...")

    for name, ticker in tickers.items():
        t = yf.Ticker(ticker)
        df = t.history(start=start_date, end=end_date + timedelta(days=1), interval="1d")
        df = df.reset_index()
        df.columns = [c.lower() for c in df.columns]

        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date']).dt.date
        elif 'datetime' in df.columns:
            df['date'] = pd.to_datetime(df['datetime']).dt.date

        data[name] = df
        print(f"  {name}: {len(df)} days")

    # Align to common dates (use IBIT as base since BITX/SBIT launched later)
    common_dates = set(data['IBIT']['date'])
    for name in ['BITX', 'SBIT']:
        common_dates &= set(data[name]['date'])

    # Build merged dataframe
    merged = pd.DataFrame({'date': sorted(common_dates)})

    for name in ['IBIT', 'BITX', 'SBIT', 'VIX', 'SPY']:
        df = data[name]
        df = df[df['date'].isin(common_dates)].sort_values('date')

        for col in ['open', 'close', 'high', 'low', 'volume']:
            if col in df.columns:
                merged[f'{name.lower()}_{col}'] = df[col].values

    print(f"\nMerged data: {len(merged)} days")
    return merged


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate all technical indicators."""
    df = df.copy()

    # IBIT returns
    df['ibit_return'] = (df['ibit_close'] - df['ibit_open']) / df['ibit_open'] * 100
    df['ibit_prev_return'] = df['ibit_return'].shift(1)

    # Day of week
    df['weekday'] = pd.to_datetime(df['date']).apply(lambda x: x.weekday())

    # VIX levels
    df['vix_level'] = df['vix_close']
    df['vix_high'] = df['vix_close'] > 20  # High fear
    df['vix_low'] = df['vix_close'] < 15   # Low fear
    df['vix_extreme'] = df['vix_close'] > 25  # Extreme fear

    # VIX change
    df['vix_change'] = df['vix_close'].pct_change() * 100
    df['vix_spike'] = df['vix_change'] > 10  # VIX spiked 10%+

    # RSI (14-day)
    delta = df['ibit_close'].diff()
    gain = delta.where(delta > 0, 0).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    df['rsi_oversold'] = df['rsi'] < 30
    df['rsi_overbought'] = df['rsi'] > 70

    # Volatility (20-day realized)
    df['volatility'] = df['ibit_return'].rolling(20).std()
    df['high_vol'] = df['volatility'] > df['volatility'].median()

    # Volume anomaly
    df['volume_ma'] = df['ibit_volume'].rolling(20).mean()
    df['volume_ratio'] = df['ibit_volume'] / df['volume_ma']
    df['high_volume'] = df['volume_ratio'] > 1.5

    # SPY context
    df['spy_return'] = (df['spy_close'] - df['spy_open']) / df['spy_open'] * 100
    df['spy_prev_return'] = df['spy_return'].shift(1)
    df['spy_down'] = df['spy_prev_return'] < -1  # SPY dropped 1%+

    # Moving averages
    df['ibit_sma20'] = df['ibit_close'].rolling(20).mean()
    df['ibit_sma50'] = df['ibit_close'].rolling(50).mean()
    df['above_sma20'] = df['ibit_close'] > df['ibit_sma20']
    df['above_sma50'] = df['ibit_close'] > df['ibit_sma50']

    return df


def backtest_strategy(df: pd.DataFrame, mr_filter=None, thu_filter=None,
                      mr_threshold: float = -2.0) -> Dict:
    """
    Backtest strategy with optional filters.

    Args:
        df: Data with indicators
        mr_filter: Optional filter for mean reversion trades (column name or callable)
        thu_filter: Optional filter for Thursday trades
        mr_threshold: Mean reversion threshold
    """
    capital = 10000.0
    slippage = 0.02

    trades = []

    for i in range(len(df)):
        row = df.iloc[i]
        prev_ret = row.get('ibit_prev_return')
        weekday = row['weekday']

        # Check signals
        has_mr_signal = pd.notna(prev_ret) and prev_ret < mr_threshold
        is_thursday = weekday == 3

        # Apply filters
        if has_mr_signal and mr_filter is not None:
            if callable(mr_filter):
                has_mr_signal = mr_filter(row)
            elif isinstance(mr_filter, str):
                has_mr_signal = has_mr_signal and row.get(mr_filter, True)

        if is_thursday and thu_filter is not None:
            if callable(thu_filter):
                is_thursday = thu_filter(row)
            elif isinstance(thu_filter, str):
                is_thursday = is_thursday and row.get(thu_filter, True)

        # Execute trades
        signal = None
        etf = None

        if has_mr_signal:
            signal = 'mean_rev'
            etf = 'bitx'
        elif is_thursday:
            signal = 'short_thu'
            etf = 'sbit'

        if signal and etf:
            entry = row[f'{etf}_open'] * (1 + slippage / 100)
            exit_p = row[f'{etf}_close'] * (1 - slippage / 100)
            ret = (exit_p - entry) / entry
            capital *= (1 + ret)

            trades.append({
                'date': row['date'],
                'signal': signal,
                'return': ret
            })

    # Calculate metrics
    if not trades:
        return {
            'total_return': 0,
            'trades': 0,
            'win_rate': 0,
            'sharpe': 0
        }

    returns = [t['return'] for t in trades]
    total_return = (capital - 10000) / 10000 * 100
    win_rate = sum(1 for r in returns if r > 0) / len(returns) * 100
    sharpe = (np.mean(returns) / np.std(returns)) * np.sqrt(len(returns)) if np.std(returns) > 0 else 0

    mr_trades = [t for t in trades if t['signal'] == 'mean_rev']
    thu_trades = [t for t in trades if t['signal'] == 'short_thu']

    return {
        'total_return': total_return,
        'trades': len(trades),
        'win_rate': win_rate,
        'sharpe': sharpe,
        'mr_trades': len(mr_trades),
        'mr_win_rate': sum(1 for t in mr_trades if t['return'] > 0) / len(mr_trades) * 100 if mr_trades else 0,
        'thu_trades': len(thu_trades),
        'thu_win_rate': sum(1 for t in thu_trades if t['return'] > 0) / len(thu_trades) * 100 if thu_trades else 0
    }


def test_all_filters(df: pd.DataFrame):
    """Test all possible signal filters."""
    print("\n" + "="*80)
    print("SIGNAL OPTIMIZATION ANALYSIS")
    print("="*80)

    # Baseline (current strategy)
    baseline = backtest_strategy(df)
    print(f"\n{'BASELINE (Current Strategy)':-^80}")
    print(f"Return: {baseline['total_return']:+.1f}% | Trades: {baseline['trades']} | Win Rate: {baseline['win_rate']:.1f}% | Sharpe: {baseline['sharpe']:.2f}")

    # Test different filters
    filters_to_test = [
        # VIX filters
        ("VIX > 20 (High Fear)", "vix_high", None),
        ("VIX < 20 (Low Fear)", lambda r: not r.get('vix_high', False), None),
        ("VIX > 25 (Extreme Fear)", "vix_extreme", None),
        ("VIX Spike > 10%", "vix_spike", None),

        # RSI filters
        ("RSI < 30 (Oversold)", "rsi_oversold", None),
        ("RSI > 30 (Not Oversold)", lambda r: not r.get('rsi_oversold', False), None),
        ("RSI < 50", lambda r: r.get('rsi', 50) < 50, None),

        # Volatility filters
        ("High Volatility", "high_vol", None),
        ("Low Volatility", lambda r: not r.get('high_vol', False), None),

        # Volume filters
        ("High Volume (>1.5x avg)", "high_volume", None),
        ("Normal Volume", lambda r: not r.get('high_volume', False), None),

        # SPY context
        ("SPY also dropped", "spy_down", None),
        ("SPY didn't drop", lambda r: not r.get('spy_down', False), None),

        # Trend filters
        ("Above SMA20", "above_sma20", None),
        ("Below SMA20", lambda r: not r.get('above_sma20', False), None),
        ("Above SMA50", "above_sma50", None),
        ("Below SMA50", lambda r: not r.get('above_sma50', False), None),

        # Thursday-specific filters
        ("Thu: VIX > 20", None, "vix_high"),
        ("Thu: VIX < 20", None, lambda r: not r.get('vix_high', False)),
        ("Thu: High Vol", None, "high_vol"),
        ("Thu: Low Vol", None, lambda r: not r.get('high_vol', False)),

        # Combination filters
        ("MR + VIX>20, Thu + VIX<20", "vix_high", lambda r: not r.get('vix_high', False)),
        ("MR + Below SMA20", lambda r: not r.get('above_sma20', False), None),
        ("MR + RSI<50 + VIX>20", lambda r: r.get('rsi', 50) < 50 and r.get('vix_high', False), None),
    ]

    results = []

    print(f"\n{'FILTER TESTS':-^80}")
    print(f"{'Filter':<35} {'Return':>10} {'Trades':>8} {'Win%':>8} {'Sharpe':>8} {'vs Base':>10}")
    print("-"*80)

    for name, mr_filter, thu_filter in filters_to_test:
        result = backtest_strategy(df, mr_filter=mr_filter, thu_filter=thu_filter)
        vs_baseline = result['total_return'] - baseline['total_return']

        results.append((name, result, vs_baseline))

        print(f"{name:<35} {result['total_return']:>+9.1f}% {result['trades']:>8} {result['win_rate']:>7.1f}% {result['sharpe']:>8.2f} {vs_baseline:>+9.1f}%")

    # Find improvements
    print(f"\n{'IMPROVEMENTS OVER BASELINE':-^80}")
    improvements = [(n, r, v) for n, r, v in results if v > 0 and r['trades'] >= 20]
    improvements.sort(key=lambda x: x[2], reverse=True)

    if improvements:
        print(f"{'Filter':<35} {'Return':>10} {'Trades':>8} {'Win%':>8} {'Improvement':>12}")
        print("-"*80)
        for name, result, vs_baseline in improvements[:10]:
            print(f"{name:<35} {result['total_return']:>+9.1f}% {result['trades']:>8} {result['win_rate']:>7.1f}% {vs_baseline:>+11.1f}%")
    else:
        print("No filters improved over baseline with sufficient trades.")

    return results, baseline


def test_threshold_optimization(df: pd.DataFrame):
    """Test different mean reversion thresholds."""
    print(f"\n{'THRESHOLD OPTIMIZATION':-^80}")
    print(f"{'Threshold':>12} {'Return':>10} {'Trades':>8} {'Win%':>8} {'Sharpe':>8}")
    print("-"*50)

    best = None
    best_return = -999

    for threshold in [-1.0, -1.5, -2.0, -2.5, -3.0, -3.5, -4.0, -4.5, -5.0]:
        result = backtest_strategy(df, mr_threshold=threshold)

        if result['total_return'] > best_return and result['trades'] >= 20:
            best_return = result['total_return']
            best = (threshold, result)

        print(f"{threshold:>12.1f}% {result['total_return']:>+9.1f}% {result['trades']:>8} {result['win_rate']:>7.1f}% {result['sharpe']:>8.2f}")

    if best:
        print(f"\nOptimal threshold: {best[0]}% with {best[1]['total_return']:+.1f}% return")

    return best


def analyze_signal_correlations(df: pd.DataFrame):
    """Analyze which indicators correlate with successful trades."""
    print(f"\n{'SIGNAL CORRELATION ANALYSIS':-^80}")

    # Get trade outcomes
    df = df.copy()
    df['mr_signal'] = df['ibit_prev_return'] < -2.0
    df['thu_signal'] = df['weekday'] == 3

    # Calculate next-day BITX return for MR trades
    df['bitx_return'] = (df['bitx_close'] - df['bitx_open']) / df['bitx_open'] * 100
    df['sbit_return'] = (df['sbit_close'] - df['sbit_open']) / df['sbit_open'] * 100

    # Analyze MR trades
    mr_days = df[df['mr_signal'] == True].copy()
    if len(mr_days) > 0:
        print(f"\nMean Reversion Analysis ({len(mr_days)} trades):")

        indicators = ['vix_high', 'vix_extreme', 'rsi_oversold', 'high_vol', 'high_volume', 'spy_down', 'above_sma20']

        for ind in indicators:
            if ind in mr_days.columns:
                with_ind = mr_days[mr_days[ind] == True]['bitx_return']
                without_ind = mr_days[mr_days[ind] == False]['bitx_return']

                if len(with_ind) > 3 and len(without_ind) > 3:
                    print(f"  {ind}:")
                    print(f"    With:    {with_ind.mean():+.2f}% avg ({len(with_ind)} trades, {(with_ind > 0).mean()*100:.0f}% win)")
                    print(f"    Without: {without_ind.mean():+.2f}% avg ({len(without_ind)} trades, {(without_ind > 0).mean()*100:.0f}% win)")

    # Analyze Thursday trades
    thu_days = df[df['thu_signal'] == True].copy()
    if len(thu_days) > 0:
        print(f"\nShort Thursday Analysis ({len(thu_days)} trades):")

        for ind in indicators:
            if ind in thu_days.columns:
                with_ind = thu_days[thu_days[ind] == True]['sbit_return']
                without_ind = thu_days[thu_days[ind] == False]['sbit_return']

                if len(with_ind) > 3 and len(without_ind) > 3:
                    print(f"  {ind}:")
                    print(f"    With:    {with_ind.mean():+.2f}% avg ({len(with_ind)} trades, {(with_ind > 0).mean()*100:.0f}% win)")
                    print(f"    Without: {without_ind.mean():+.2f}% avg ({len(without_ind)} trades, {(without_ind > 0).mean()*100:.0f}% win)")


def main():
    print("="*80)
    print("BITCOIN ETF SIGNAL OPTIMIZATION")
    print("="*80)
    print("\nTesting if additional market signals can improve the strategy...")

    # Load data
    start_date = date(2024, 4, 15)  # BITX/SBIT launch
    end_date = date.today()

    df = load_all_data(start_date, end_date)
    df = calculate_indicators(df)

    # Run all tests
    results, baseline = test_all_filters(df)
    test_threshold_optimization(df)
    analyze_signal_correlations(df)

    # Summary
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)

    improvements = [(n, r, v) for n, r, v in results if v > 5 and r['trades'] >= 20]

    if improvements:
        print("\nFilters that improved returns by >5%:")
        for name, result, vs_baseline in sorted(improvements, key=lambda x: x[2], reverse=True):
            print(f"  {name}: +{vs_baseline:.1f}% improvement")
    else:
        print("\nNo filters significantly improved the baseline strategy.")
        print("The simple approach (MR + Short Thursday) appears optimal.")


if __name__ == "__main__":
    main()
