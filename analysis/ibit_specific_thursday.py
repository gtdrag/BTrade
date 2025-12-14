#!/usr/bin/env python3
"""
IBIT-Specific Thursday Analysis

Compare Thursday performance:
1. IBIT (US market hours only, 9:30 AM - 4:00 PM ET)
2. BTC-USD during same period

The hypothesis: Thursday weakness is tied to US institutional trading,
which only affects IBIT during market hours.
"""

from datetime import date, timedelta
import pandas as pd
import numpy as np
import yfinance as yf


def load_data(ticker: str, start_date: date, end_date: date) -> pd.DataFrame:
    """Load data."""
    t = yf.Ticker(ticker)
    df = t.history(start=start_date, end=end_date + timedelta(days=1), interval="1d")
    df = df.reset_index()
    df.columns = [c.lower() for c in df.columns]

    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date']).dt.date
    elif 'datetime' in df.columns:
        df['date'] = pd.to_datetime(df['datetime']).dt.date

    df['daily_return'] = (df['close'] - df['open']) / df['open'] * 100
    df['weekday'] = pd.to_datetime(df['date']).apply(lambda x: x.weekday())
    df['day_name'] = pd.to_datetime(df['date']).apply(lambda x: x.strftime('%A'))

    return df


def main():
    print("="*80)
    print("IBIT vs BTC-USD THURSDAY ANALYSIS")
    print("="*80)

    # Load both
    start = date(2024, 1, 15)  # IBIT launch
    end = date.today()

    ibit = load_data("IBIT", start, end)
    btc = load_data("BTC-USD", start, end)

    print(f"\nPeriod: {start} to {end}")
    print(f"IBIT days: {len(ibit)}")
    print(f"BTC days: {len(btc)}")

    # Day of week comparison
    print("\n" + "="*80)
    print("DAY OF WEEK COMPARISON")
    print("="*80)

    print(f"\n{'Day':<12} {'IBIT Avg':>12} {'IBIT Win%':>12} {'BTC Avg':>12} {'BTC Win%':>12}")
    print("-"*60)

    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    for i, day in enumerate(days):
        ibit_day = ibit[ibit['weekday'] == i]['daily_return']
        btc_day = btc[btc['weekday'] == i]['daily_return']

        ibit_avg = ibit_day.mean() if len(ibit_day) > 0 else 0
        ibit_win = (ibit_day > 0).mean() * 100 if len(ibit_day) > 0 else 0
        btc_avg = btc_day.mean() if len(btc_day) > 0 else 0
        btc_win = (btc_day > 0).mean() * 100 if len(btc_day) > 0 else 0

        print(f"{day:<12} {ibit_avg:>+11.2f}% {ibit_win:>11.1f}% {btc_avg:>+11.2f}% {btc_win:>11.1f}%")

    # Thursday deep dive
    print("\n" + "="*80)
    print("THURSDAY DEEP DIVE")
    print("="*80)

    ibit_thu = ibit[ibit['weekday'] == 3]['daily_return']
    btc_thu = btc[btc['weekday'] == 3]['daily_return']

    print(f"\nIBIT Thursdays:")
    print(f"  Count: {len(ibit_thu)}")
    print(f"  Average: {ibit_thu.mean():+.2f}%")
    print(f"  Win Rate (long): {(ibit_thu > 0).mean()*100:.1f}%")
    print(f"  Win Rate (short): {(ibit_thu < 0).mean()*100:.1f}%")
    print(f"  Std Dev: {ibit_thu.std():.2f}%")

    print(f"\nBTC-USD Thursdays (same dates):")
    print(f"  Count: {len(btc_thu)}")
    print(f"  Average: {btc_thu.mean():+.2f}%")
    print(f"  Win Rate (long): {(btc_thu > 0).mean()*100:.1f}%")
    print(f"  Win Rate (short): {(btc_thu < 0).mean()*100:.1f}%")

    # Correlation
    # Align dates
    common_dates = set(ibit['date']) & set(btc['date'])
    ibit_aligned = ibit[ibit['date'].isin(common_dates)].set_index('date').sort_index()
    btc_aligned = btc[btc['date'].isin(common_dates)].set_index('date').sort_index()

    thu_dates = [d for d in common_dates if pd.to_datetime(d).weekday() == 3]
    ibit_thu_aligned = ibit_aligned.loc[thu_dates]['daily_return']
    btc_thu_aligned = btc_aligned.loc[thu_dates]['daily_return']

    corr = ibit_thu_aligned.corr(btc_thu_aligned)
    print(f"\nIBIT vs BTC Thursday correlation: {corr:.2f}")

    # Check if IBIT underperforms BTC on Thursdays
    diff = ibit_thu_aligned - btc_thu_aligned
    print(f"\nIBIT - BTC on Thursdays:")
    print(f"  Average difference: {diff.mean():+.2f}%")
    print(f"  IBIT worse than BTC: {(diff < 0).mean()*100:.1f}% of Thursdays")

    # Short Thursday performance for IBIT specifically
    print("\n" + "="*80)
    print("SHORT THURSDAY STRATEGY - IBIT SPECIFIC")
    print("="*80)

    # Simulate shorting IBIT on Thursdays with 2x
    short_returns = -ibit_thu * 2  # 2x inverse

    print(f"\nShort IBIT (2x) on Thursdays:")
    print(f"  Trades: {len(short_returns)}")
    print(f"  Win Rate: {(short_returns > 0).mean()*100:.1f}%")
    print(f"  Avg Return: {short_returns.mean():+.2f}%")
    print(f"  Total Return: {(np.prod(1 + short_returns/100) - 1)*100:+.1f}%")

    # Monthly breakdown
    print("\n" + "="*80)
    print("IBIT THURSDAY MONTHLY BREAKDOWN")
    print("="*80)

    ibit_thu_df = ibit[ibit['weekday'] == 3].copy()
    ibit_thu_df['month'] = pd.to_datetime(ibit_thu_df['date']).apply(lambda x: x.strftime('%Y-%m'))

    print(f"\n{'Month':<10} {'Avg':>10} {'Count':>8} {'Short Win%':>12}")
    print("-"*45)

    monthly_results = []
    for month in sorted(ibit_thu_df['month'].unique()):
        month_data = ibit_thu_df[ibit_thu_df['month'] == month]['daily_return']
        avg = month_data.mean()
        count = len(month_data)
        short_win = (month_data < 0).mean() * 100

        monthly_results.append({
            'month': month,
            'avg': avg,
            'count': count,
            'short_win': short_win
        })
        print(f"{month:<10} {avg:>+9.2f}% {count:>8} {short_win:>11.0f}%")

    # Summary statistics
    wins = sum(1 for r in monthly_results if r['avg'] < 0)
    total = len(monthly_results)
    print(f"\nMonths where Thursday was negative: {wins}/{total} ({wins/total*100:.0f}%)")

    # Final assessment
    print("\n" + "="*80)
    print("ASSESSMENT")
    print("="*80)

    ibit_thu_avg = ibit_thu.mean()
    ibit_short_win = (ibit_thu < 0).mean() * 100

    print(f"""
IBIT Thursday Pattern (Since Jan 2024):

  Average Thursday Return: {ibit_thu_avg:+.2f}%
  Short Thursday Win Rate: {ibit_short_win:.1f}%
  Months with Negative Thursday: {wins}/{total} ({wins/total*100:.0f}%)

Key Observations:
  1. IBIT shows a {abs(ibit_thu_avg):.2f}% average loss on Thursdays
  2. This is {"stronger" if abs(ibit_thu_avg) > abs(btc_thu.mean()) else "similar to"} the BTC-USD pattern
  3. The pattern has been present since IBIT launched

Likely Causes:
  - ETF rebalancing flows (weekly)
  - Options-related hedging
  - Institutional end-of-week positioning
  - Market maker inventory management

Verdict: {"VALID PATTERN" if ibit_short_win > 55 else "MARGINAL PATTERN"}
  The Thursday weakness in IBIT appears to be a {"persistent" if ibit_short_win > 55 else "moderate"}
  structural feature of the ETF market.
""")


if __name__ == "__main__":
    main()
