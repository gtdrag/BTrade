#!/usr/bin/env python3
"""
Correlation Analysis: What predicts mean reversion success?

Looking for market signals at/after close that correlate with
whether the next-day bounce succeeds or fails.
"""

from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf


def analyze_correlations():
    """Analyze what market signals correlate with bounce success."""
    print("=" * 70)
    print("CORRELATION ANALYSIS: What Predicts Mean Reversion Success?")
    print("=" * 70)

    # Get historical data
    end_date = datetime.now()
    start_date = datetime(2024, 1, 11)  # IBIT launch

    print(f"\nFetching data from {start_date.date()} to {end_date.date()}...")

    # Fetch IBIT daily data
    ibit = yf.download("IBIT", start=start_date, end=end_date, progress=False)
    ibit.columns = ibit.columns.droplevel(1) if isinstance(ibit.columns, pd.MultiIndex) else ibit.columns
    ibit["Return"] = ibit["Close"].pct_change() * 100
    ibit["PrevReturn"] = ibit["Return"].shift(1)

    # Fetch BTC for overnight correlation
    btc = yf.download("BTC-USD", start=start_date, end=end_date, progress=False)
    btc.columns = btc.columns.droplevel(1) if isinstance(btc.columns, pd.MultiIndex) else btc.columns
    btc["Return"] = btc["Close"].pct_change() * 100

    # Fetch VIX for volatility context
    vix = yf.download("^VIX", start=start_date, end=end_date, progress=False)
    vix.columns = vix.columns.droplevel(1) if isinstance(vix.columns, pd.MultiIndex) else vix.columns

    # Find mean reversion trigger days (IBIT down >= 2%)
    trigger_days = ibit[ibit["Return"] <= -2.0].copy()
    print(f"\nFound {len(trigger_days)} mean reversion trigger days")

    # Analyze each trigger
    results = []
    for date in trigger_days.index:
        try:
            # Get next trading day
            future_dates = ibit.index[ibit.index > date]
            if len(future_dates) == 0:
                continue
            next_day = future_dates[0]

            # IBIT data
            ibit_drop = ibit.loc[date, "Return"]
            ibit_bounce = ibit.loc[next_day, "Return"]
            ibit_volume = ibit.loc[date, "Volume"]
            avg_volume = ibit["Volume"].rolling(20).mean().loc[date]
            volume_ratio = ibit_volume / avg_volume if avg_volume > 0 else 1.0

            # BTC overnight change (approximation using daily close)
            # BTC trades 24/7, so its close is ~4pm vs IBIT's 4pm close
            # We want BTC movement AFTER IBIT closes
            btc_dates = btc.index[btc.index >= date]
            if len(btc_dates) >= 2:
                btc_close_trigger = btc.loc[btc_dates[0], "Close"]
                btc_close_next = btc.loc[btc_dates[1], "Close"]
                btc_overnight = ((btc_close_next - btc_close_trigger) / btc_close_trigger) * 100
            else:
                btc_overnight = 0

            # VIX level
            vix_dates = vix.index[vix.index <= date]
            vix_level = vix.loc[vix_dates[-1], "Close"] if len(vix_dates) > 0 else 20

            # End-of-day crash detection (was decline in last hour?)
            # We'll use intraday data if available, otherwise skip
            eod_crash = False  # Placeholder

            # Previous day trend (was it already dropping?)
            if date in ibit.index:
                idx = ibit.index.get_loc(date)
                if idx >= 1:
                    prev_return = ibit.iloc[idx - 1]["Return"]
                else:
                    prev_return = 0
            else:
                prev_return = 0

            # Success = next day positive
            success = ibit_bounce > 0

            # Calculate BITX return (2x leverage)
            bitx_return = ibit_bounce * 2

            results.append({
                "date": date,
                "ibit_drop": ibit_drop,
                "ibit_bounce": ibit_bounce,
                "bitx_return": bitx_return,
                "success": success,
                "volume_ratio": volume_ratio,
                "btc_overnight": btc_overnight,
                "vix_level": vix_level,
                "prev_day_return": prev_return,
                "consecutive_down": prev_return < 0,
            })

        except Exception as e:
            print(f"  Error processing {date}: {e}")
            continue

    df = pd.DataFrame(results)

    if len(df) == 0:
        print("No data to analyze!")
        return

    print(f"\nAnalyzed {len(df)} trigger events")

    # Overall stats
    print("\n" + "=" * 70)
    print("OVERALL PERFORMANCE")
    print("=" * 70)
    win_rate = df["success"].mean() * 100
    avg_bounce = df["ibit_bounce"].mean()
    avg_bitx = df["bitx_return"].mean()
    print(f"Win Rate: {win_rate:.1f}%")
    print(f"Average IBIT Bounce: {avg_bounce:+.2f}%")
    print(f"Average BITX Return: {avg_bitx:+.2f}%")

    # Correlation analysis
    print("\n" + "=" * 70)
    print("CORRELATION ANALYSIS")
    print("=" * 70)

    correlations = []

    # 1. Volume correlation
    high_vol = df[df["volume_ratio"] > 1.5]
    low_vol = df[df["volume_ratio"] <= 1.5]
    if len(high_vol) > 0 and len(low_vol) > 0:
        print(f"\n📊 VOLUME (vs 20-day avg):")
        print(f"   High Volume (>150%): {len(high_vol)} events, {high_vol['success'].mean()*100:.0f}% win rate, avg return: {high_vol['bitx_return'].mean():+.2f}%")
        print(f"   Normal Volume: {len(low_vol)} events, {low_vol['success'].mean()*100:.0f}% win rate, avg return: {low_vol['bitx_return'].mean():+.2f}%")
        vol_diff = high_vol['success'].mean() - low_vol['success'].mean()
        correlations.append(("Volume", vol_diff))

    # 2. BTC overnight correlation
    btc_up = df[df["btc_overnight"] > 0]
    btc_down = df[df["btc_overnight"] <= 0]
    if len(btc_up) > 0 and len(btc_down) > 0:
        print(f"\n🌙 BTC OVERNIGHT MOVE:")
        print(f"   BTC Up Overnight: {len(btc_up)} events, {btc_up['success'].mean()*100:.0f}% win rate, avg return: {btc_up['bitx_return'].mean():+.2f}%")
        print(f"   BTC Down Overnight: {len(btc_down)} events, {btc_down['success'].mean()*100:.0f}% win rate, avg return: {btc_down['bitx_return'].mean():+.2f}%")
        btc_diff = btc_up['success'].mean() - btc_down['success'].mean()
        correlations.append(("BTC Overnight", btc_diff))

    # 3. VIX level correlation
    vix_median = df["vix_level"].median()
    high_vix = df[df["vix_level"] > vix_median]
    low_vix = df[df["vix_level"] <= vix_median]
    if len(high_vix) > 0 and len(low_vix) > 0:
        print(f"\n📈 VIX LEVEL (median: {vix_median:.1f}):")
        print(f"   High VIX (>{vix_median:.0f}): {len(high_vix)} events, {high_vix['success'].mean()*100:.0f}% win rate, avg return: {high_vix['bitx_return'].mean():+.2f}%")
        print(f"   Low VIX: {len(low_vix)} events, {low_vix['success'].mean()*100:.0f}% win rate, avg return: {low_vix['bitx_return'].mean():+.2f}%")
        vix_diff = high_vix['success'].mean() - low_vix['success'].mean()
        correlations.append(("VIX Level", vix_diff))

    # 4. Consecutive down days
    consec = df[df["consecutive_down"]]
    not_consec = df[~df["consecutive_down"]]
    if len(consec) > 0 and len(not_consec) > 0:
        print(f"\n📉 CONSECUTIVE DOWN DAYS:")
        print(f"   After consecutive drop: {len(consec)} events, {consec['success'].mean()*100:.0f}% win rate, avg return: {consec['bitx_return'].mean():+.2f}%")
        print(f"   Isolated drop: {len(not_consec)} events, {not_consec['success'].mean()*100:.0f}% win rate, avg return: {not_consec['bitx_return'].mean():+.2f}%")
        consec_diff = not_consec['success'].mean() - consec['success'].mean()  # Positive if isolated is better
        correlations.append(("Isolated Drop", consec_diff))

    # 5. Severity of drop
    big_drop = df[df["ibit_drop"] <= -4]
    small_drop = df[df["ibit_drop"] > -4]
    if len(big_drop) > 0 and len(small_drop) > 0:
        print(f"\n💥 DROP SEVERITY:")
        print(f"   Big Drop (>= -4%): {len(big_drop)} events, {big_drop['success'].mean()*100:.0f}% win rate, avg return: {big_drop['bitx_return'].mean():+.2f}%")
        print(f"   Moderate Drop (-2% to -4%): {len(small_drop)} events, {small_drop['success'].mean()*100:.0f}% win rate, avg return: {small_drop['bitx_return'].mean():+.2f}%")
        sev_diff = big_drop['success'].mean() - small_drop['success'].mean()
        correlations.append(("Big Drop", sev_diff))

    # Rank correlations
    print("\n" + "=" * 70)
    print("SIGNAL STRENGTH RANKING")
    print("=" * 70)
    correlations.sort(key=lambda x: abs(x[1]), reverse=True)
    for name, diff in correlations:
        direction = "✅ BETTER" if diff > 0 else "❌ WORSE"
        print(f"  {name}: {diff*100:+.1f}pp {direction}")

    # Combined filter analysis
    print("\n" + "=" * 70)
    print("COMBINED FILTER ANALYSIS")
    print("=" * 70)

    # Best case: BTC up overnight + isolated drop
    best_filter = df[(df["btc_overnight"] > 0) & (~df["consecutive_down"])]
    if len(best_filter) > 0:
        print(f"\n🎯 BEST CASE (BTC up overnight + isolated drop):")
        print(f"   Events: {len(best_filter)}")
        print(f"   Win Rate: {best_filter['success'].mean()*100:.0f}%")
        print(f"   Avg BITX Return: {best_filter['bitx_return'].mean():+.2f}%")
        print(f"   Total Return: {best_filter['bitx_return'].sum():+.2f}%")

    # Worst case: BTC down overnight + consecutive drop
    worst_filter = df[(df["btc_overnight"] <= 0) & (df["consecutive_down"])]
    if len(worst_filter) > 0:
        print(f"\n⚠️ WORST CASE (BTC down overnight + consecutive drop):")
        print(f"   Events: {len(worst_filter)}")
        print(f"   Win Rate: {worst_filter['success'].mean()*100:.0f}%")
        print(f"   Avg BITX Return: {worst_filter['bitx_return'].mean():+.2f}%")
        print(f"   Total Return: {worst_filter['bitx_return'].sum():+.2f}%")

    # Recommendations
    print("\n" + "=" * 70)
    print("💡 RECOMMENDATIONS")
    print("=" * 70)

    if correlations:
        best_signal = correlations[0]
        print(f"\n1. STRONGEST SIGNAL: {best_signal[0]}")
        print(f"   Impact: {best_signal[1]*100:+.1f} percentage points on win rate")

    if len(best_filter) > 0 and best_filter['success'].mean() > df['success'].mean():
        improvement = (best_filter['success'].mean() - df['success'].mean()) * 100
        print(f"\n2. USE COMBINED FILTER for +{improvement:.0f}pp win rate improvement")
        print("   - Wait for BTC to be up overnight before trading")
        print("   - Skip if yesterday was also down")

    print("\n3. IMPLEMENTATION:")
    print("   - Check BTC price at 9:30 AM vs 4 PM previous day")
    print("   - Check if previous day was also a down day")
    print("   - Only trade when conditions are favorable")

    return df


if __name__ == "__main__":
    analyze_correlations()
