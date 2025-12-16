"""
Intraday Crash Signal Analysis.

Question: If IBIT drops 2%+ from open during the day, should we:
  A) Buy SBIT immediately (bet on continued decline)
  B) Wait and buy BITX tomorrow (bet on bounce)
  C) Do nothing

This analysis examines historical intraday data to find the optimal approach.
"""

import sys
from datetime import date, timedelta, datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

# Thresholds to test
DROP_THRESHOLDS = [-1.5, -2.0, -2.5, -3.0]
CHECK_TIMES = ['10:00', '10:30', '11:00', '11:30', '12:00', '14:00']


def get_intraday_data(ticker: str, start_date: date, end_date: date) -> pd.DataFrame:
    """Fetch intraday (15-min) data for analysis."""
    t = yf.Ticker(ticker)

    # yfinance limits intraday data, so we fetch in chunks
    all_data = []
    current = start_date

    while current < end_date:
        chunk_end = min(current + timedelta(days=59), end_date)  # ~60 day chunks
        try:
            df = t.history(start=current, end=chunk_end + timedelta(days=1), interval="15m")
            if len(df) > 0:
                all_data.append(df)
        except Exception as e:
            print(f"  Warning: Could not fetch {ticker} data for {current} to {chunk_end}: {e}")
        current = chunk_end + timedelta(days=1)

    if not all_data:
        return pd.DataFrame()

    combined = pd.concat(all_data)
    combined = combined[~combined.index.duplicated(keep='first')]
    return combined.sort_index()


def analyze_crash_signals(ibit_data: pd.DataFrame, sbit_data: pd.DataFrame,
                          threshold: float, check_time: str) -> dict:
    """
    Analyze what happens when we detect a crash at a specific time.

    Strategy: If IBIT is down >= threshold% from open at check_time,
    buy SBIT and hold until close.
    """
    # Group by date
    ibit_data['date'] = ibit_data.index.date
    ibit_data['time'] = ibit_data.index.time
    sbit_data['date'] = sbit_data.index.date
    sbit_data['time'] = sbit_data.index.time

    trades = []

    for trade_date in ibit_data['date'].unique():
        day_ibit = ibit_data[ibit_data['date'] == trade_date].copy()
        day_sbit = sbit_data[sbit_data['date'] == trade_date].copy()

        if len(day_ibit) < 10 or len(day_sbit) < 10:  # Need enough data points
            continue

        # Get open price (first bar of the day)
        ibit_open = day_ibit['Open'].iloc[0]

        # Find the check time bar
        check_hour, check_min = map(int, check_time.split(':'))
        check_bars = day_ibit[
            (day_ibit.index.hour == check_hour) &
            (day_ibit.index.minute >= check_min) &
            (day_ibit.index.minute < check_min + 15)
        ]

        if len(check_bars) == 0:
            continue

        check_bar = check_bars.iloc[0]
        ibit_price_at_check = check_bar['Close']
        drop_from_open = (ibit_price_at_check - ibit_open) / ibit_open * 100

        # Check if drop threshold is met
        if drop_from_open > threshold:  # Not enough drop
            continue

        # Signal triggered! Find SBIT entry and exit
        sbit_check_bars = day_sbit[
            (day_sbit.index.hour == check_hour) &
            (day_sbit.index.minute >= check_min) &
            (day_sbit.index.minute < check_min + 15)
        ]

        if len(sbit_check_bars) == 0:
            continue

        sbit_entry = sbit_check_bars.iloc[0]['Close']  # Buy at check time
        sbit_close = day_sbit['Close'].iloc[-1]  # Sell at close

        # Also track what IBIT did rest of day
        ibit_close = day_ibit['Close'].iloc[-1]
        ibit_rest_of_day = (ibit_close - ibit_price_at_check) / ibit_price_at_check * 100

        # Calculate SBIT return
        sbit_return = (sbit_close - sbit_entry) / sbit_entry * 100

        trades.append({
            'date': trade_date,
            'ibit_open': ibit_open,
            'ibit_at_check': ibit_price_at_check,
            'ibit_close': ibit_close,
            'drop_at_signal': drop_from_open,
            'ibit_rest_of_day': ibit_rest_of_day,
            'sbit_entry': sbit_entry,
            'sbit_close': sbit_close,
            'sbit_return': sbit_return,
            'check_time': check_time,
            'threshold': threshold,
        })

    if not trades:
        return None

    df = pd.DataFrame(trades)

    # Calculate statistics
    wins = df[df['sbit_return'] > 0]
    losses = df[df['sbit_return'] <= 0]

    # Did IBIT continue falling or bounce?
    continued_falling = df[df['ibit_rest_of_day'] < 0]
    bounced = df[df['ibit_rest_of_day'] >= 0]

    return {
        'threshold': threshold,
        'check_time': check_time,
        'total_signals': len(df),
        'win_rate': len(wins) / len(df) * 100 if len(df) > 0 else 0,
        'avg_return': df['sbit_return'].mean(),
        'total_return': df['sbit_return'].sum(),
        'best_trade': df['sbit_return'].max(),
        'worst_trade': df['sbit_return'].min(),
        'avg_drop_at_signal': df['drop_at_signal'].mean(),
        'continued_falling_pct': len(continued_falling) / len(df) * 100,
        'avg_continued_move': df['ibit_rest_of_day'].mean(),
        'trades': df,
    }


def analyze_next_day_bitx(ibit_data: pd.DataFrame, bitx_data: pd.DataFrame,
                           threshold: float) -> dict:
    """
    Analyze what happens if we wait and buy BITX the next day instead.

    Current strategy: After IBIT drops >= threshold%, buy BITX next day open-to-close.
    """
    ibit_daily = ibit_data.groupby(ibit_data.index.date).agg({
        'Open': 'first',
        'Close': 'last'
    })
    ibit_daily['daily_return'] = (ibit_daily['Close'] - ibit_daily['Open']) / ibit_daily['Open'] * 100

    bitx_daily = bitx_data.groupby(bitx_data.index.date).agg({
        'Open': 'first',
        'Close': 'last'
    })
    bitx_daily['daily_return'] = (bitx_daily['Close'] - bitx_daily['Open']) / bitx_daily['Open'] * 100

    trades = []
    dates = list(ibit_daily.index)

    for i, trade_date in enumerate(dates[:-1]):
        if ibit_daily.loc[trade_date, 'daily_return'] > threshold:
            continue  # Not a drop day

        next_date = dates[i + 1]
        if next_date not in bitx_daily.index:
            continue

        bitx_return = bitx_daily.loc[next_date, 'daily_return']

        trades.append({
            'trigger_date': trade_date,
            'trade_date': next_date,
            'ibit_drop': ibit_daily.loc[trade_date, 'daily_return'],
            'bitx_return': bitx_return,
        })

    if not trades:
        return None

    df = pd.DataFrame(trades)
    wins = df[df['bitx_return'] > 0]

    return {
        'threshold': threshold,
        'strategy': 'next_day_bitx',
        'total_signals': len(df),
        'win_rate': len(wins) / len(df) * 100,
        'avg_return': df['bitx_return'].mean(),
        'total_return': df['bitx_return'].sum(),
        'trades': df,
    }


def main():
    print("=" * 80)
    print("INTRADAY CRASH SIGNAL ANALYSIS")
    print("=" * 80)
    print("\nQuestion: When IBIT drops 2%+ from open, should we buy SBIT same-day")
    print("or wait and buy BITX the next day?\n")

    # Date range - go back as far as intraday data allows (~60 days typically)
    end_date = date.today()
    start_date = end_date - timedelta(days=180)  # Try to get 6 months

    print(f"Fetching intraday data from {start_date} to {end_date}...")
    print("(Note: yfinance limits intraday data to ~60 days, so we may have less)\n")

    # Fetch data
    print("Fetching IBIT 15-min data...")
    ibit_data = get_intraday_data("IBIT", start_date, end_date)
    print(f"  Got {len(ibit_data)} bars")

    print("Fetching SBIT 15-min data...")
    sbit_data = get_intraday_data("SBIT", start_date, end_date)
    print(f"  Got {len(sbit_data)} bars")

    print("Fetching BITX 15-min data...")
    bitx_data = get_intraday_data("BITX", start_date, end_date)
    print(f"  Got {len(bitx_data)} bars")

    if len(ibit_data) == 0:
        print("\nERROR: Could not fetch intraday data. Try again later.")
        return

    # Analyze different thresholds and check times
    print("\n" + "=" * 80)
    print("STRATEGY A: Buy SBIT same-day when drop detected")
    print("=" * 80)

    results = []

    for threshold in DROP_THRESHOLDS:
        for check_time in CHECK_TIMES:
            result = analyze_crash_signals(ibit_data.copy(), sbit_data.copy(),
                                          threshold, check_time)
            if result:
                results.append(result)

    if results:
        print(f"\n{'Threshold':<12} {'Check Time':<12} {'Signals':<10} {'Win Rate':<12} {'Avg Return':<12} {'Total Return':<14} {'IBIT Continued':<15}")
        print("-" * 95)

        for r in sorted(results, key=lambda x: x['total_return'], reverse=True):
            print(f"{r['threshold']:>+.1f}%{'':<7} {r['check_time']:<12} {r['total_signals']:<10} "
                  f"{r['win_rate']:>6.1f}%{'':<5} {r['avg_return']:>+6.2f}%{'':<5} "
                  f"{r['total_return']:>+7.1f}%{'':<6} {r['continued_falling_pct']:>5.1f}%")

        # Best configuration
        best = max(results, key=lambda x: x['total_return'])
        print(f"\n*** BEST CONFIG: {best['threshold']}% threshold at {best['check_time']} ***")
        print(f"    Signals: {best['total_signals']}, Win Rate: {best['win_rate']:.1f}%, "
              f"Avg Return: {best['avg_return']:+.2f}%, Total: {best['total_return']:+.1f}%")

        # Show the trades for best config
        if best['total_signals'] > 0:
            print(f"\n    Recent trades with this config:")
            for _, trade in best['trades'].tail(10).iterrows():
                outcome = "✓" if trade['sbit_return'] > 0 else "✗"
                print(f"      {trade['date']}: IBIT dropped {trade['drop_at_signal']:+.1f}% at {trade['check_time']}, "
                      f"SBIT return: {trade['sbit_return']:+.2f}% {outcome}")

    # Compare to next-day BITX strategy
    print("\n" + "=" * 80)
    print("STRATEGY B: Wait and buy BITX next day (current strategy)")
    print("=" * 80)

    for threshold in DROP_THRESHOLDS:
        result = analyze_next_day_bitx(ibit_data.copy(), bitx_data.copy(), threshold)
        if result:
            print(f"\n{threshold:+.1f}% threshold:")
            print(f"  Signals: {result['total_signals']}, Win Rate: {result['win_rate']:.1f}%, "
                  f"Avg Return: {result['avg_return']:+.2f}%, Total: {result['total_return']:+.1f}%")

    # Key insight
    print("\n" + "=" * 80)
    print("KEY INSIGHTS")
    print("=" * 80)

    if results:
        best_sbit = max(results, key=lambda x: x['total_return'])

        # Check if IBIT tends to continue falling or bounce
        print(f"""
1. AFTER A {best_sbit['threshold']}% DROP BY {best_sbit['check_time']}:
   - IBIT continued falling: {best_sbit['continued_falling_pct']:.0f}% of the time
   - IBIT bounced back: {100 - best_sbit['continued_falling_pct']:.0f}% of the time
   - Average rest-of-day move: {best_sbit['avg_continued_move']:+.2f}%

2. SAME-DAY SBIT STRATEGY:
   - Win rate: {best_sbit['win_rate']:.0f}%
   - Avg return per trade: {best_sbit['avg_return']:+.2f}%
   - Total return: {best_sbit['total_return']:+.1f}%

3. RECOMMENDATION:
""")

        if best_sbit['win_rate'] >= 55 and best_sbit['avg_return'] > 0:
            print(f"   ✓ VIABLE: Same-day SBIT strategy shows promise")
            print(f"   Consider adding: Check at {best_sbit['check_time']}, trigger at {best_sbit['threshold']}% drop")
            print(f"   This would COMPLEMENT the existing next-day BITX strategy")
        elif best_sbit['continued_falling_pct'] < 50:
            print(f"   ✗ NOT RECOMMENDED: IBIT tends to bounce after the drop")
            print(f"   The current next-day BITX strategy is likely better")
        else:
            print(f"   ⚠ MIXED RESULTS: More data needed for confidence")
            print(f"   Consider paper trading this for a few weeks")


if __name__ == "__main__":
    main()
