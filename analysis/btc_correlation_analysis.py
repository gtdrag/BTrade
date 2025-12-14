"""
Bitcoin Correlation & Volatility Analysis for IBIT
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

def get_data():
    """Fetch IBIT and BTC data."""
    # IBIT
    ibit = yf.Ticker("IBIT")
    ibit_daily = ibit.history(period="max", interval="1d").reset_index()
    ibit_daily.columns = [c.lower() for c in ibit_daily.columns]
    if 'date' in ibit_daily.columns:
        ibit_daily = ibit_daily.rename(columns={'date': 'datetime'})

    # Bitcoin
    btc = yf.Ticker("BTC-USD")
    btc_daily = btc.history(period="max", interval="1d").reset_index()
    btc_daily.columns = [c.lower() for c in btc_daily.columns]
    if 'date' in btc_daily.columns:
        btc_daily = btc_daily.rename(columns={'date': 'datetime'})

    # VIX for volatility regime
    vix = yf.Ticker("^VIX")
    vix_daily = vix.history(period="max", interval="1d").reset_index()
    vix_daily.columns = [c.lower() for c in vix_daily.columns]
    if 'date' in vix_daily.columns:
        vix_daily = vix_daily.rename(columns={'date': 'datetime'})

    return ibit_daily, btc_daily, vix_daily

def analyze_btc_overnight(ibit, btc):
    """Analyze BTC overnight moves as signals for IBIT."""
    print("\n" + "="*80)
    print("BITCOIN OVERNIGHT MOVE AS IBIT SIGNAL")
    print("="*80)

    # Merge on date
    ibit['date'] = pd.to_datetime(ibit['datetime']).dt.date
    btc['date'] = pd.to_datetime(btc['datetime']).dt.date

    ibit['ibit_return'] = (ibit['close'] - ibit['open']) / ibit['open'] * 100

    # BTC overnight return (from 4PM to 9:30AM next day approximated by close-to-open)
    btc['btc_overnight'] = (btc['open'] - btc['close'].shift(1)) / btc['close'].shift(1) * 100
    btc['btc_prev_day'] = (btc['close'] - btc['open']) / btc['open'] * 100

    merged = pd.merge(ibit[['date', 'open', 'close', 'ibit_return']],
                      btc[['date', 'btc_overnight', 'btc_prev_day']],
                      on='date', how='inner')

    print(f"\nData points: {len(merged)}")

    # Test: BTC overnight up -> IBIT long
    print("\n--- BTC Overnight as IBIT Signal ---")

    for threshold in [0.5, 1.0, 1.5, 2.0, 3.0]:
        btc_up = merged[merged['btc_overnight'] > threshold]
        if len(btc_up) > 5:
            ibit_return = btc_up['ibit_return']
            print(f"BTC overnight +{threshold}%: IBIT Avg: {ibit_return.mean():+.2f}% | Win: {(ibit_return > 0).mean()*100:.1f}% | n={len(btc_up)}")

        btc_down = merged[merged['btc_overnight'] < -threshold]
        if len(btc_down) > 5:
            ibit_return = btc_down['ibit_return']
            print(f"BTC overnight -{threshold}%: IBIT Avg: {ibit_return.mean():+.2f}% | Win: {(ibit_return > 0).mean()*100:.1f}% | n={len(btc_down)}")

    # Test: BTC previous day momentum
    print("\n--- BTC Previous Day as Signal ---")

    for threshold in [2.0, 3.0, 5.0]:
        btc_up = merged[merged['btc_prev_day'] > threshold]
        if len(btc_up) > 5:
            ibit_return = btc_up['ibit_return']
            print(f"BTC prev day +{threshold}%: IBIT Avg: {ibit_return.mean():+.2f}% | Win: {(ibit_return > 0).mean()*100:.1f}% | n={len(btc_up)}")

        btc_down = merged[merged['btc_prev_day'] < -threshold]
        if len(btc_down) > 5:
            ibit_return = btc_down['ibit_return']
            print(f"BTC prev day -{threshold}%: IBIT Avg: {ibit_return.mean():+.2f}% | Win: {(ibit_return > 0).mean()*100:.1f}% | n={len(btc_down)}")

    return merged

def analyze_vix_regime(ibit, vix):
    """Analyze VIX levels as trading filter."""
    print("\n" + "="*80)
    print("VIX REGIME ANALYSIS")
    print("="*80)

    ibit['date'] = pd.to_datetime(ibit['datetime']).dt.date
    vix['date'] = pd.to_datetime(vix['datetime']).dt.date

    ibit['ibit_return'] = (ibit['close'] - ibit['open']) / ibit['open'] * 100

    # Use previous day's VIX close as signal
    vix['vix_level'] = vix['close']
    vix['prev_vix'] = vix['vix_level'].shift(1)

    merged = pd.merge(ibit[['date', 'ibit_return']],
                      vix[['date', 'vix_level', 'prev_vix']],
                      on='date', how='inner')

    print(f"\nData points: {len(merged)}")

    # VIX level buckets
    print("\n--- IBIT Returns by VIX Level ---")
    vix_buckets = [(0, 15, 'Low (<15)'), (15, 20, 'Normal (15-20)'),
                   (20, 25, 'Elevated (20-25)'), (25, 100, 'High (>25)')]

    for low, high, label in vix_buckets:
        bucket = merged[(merged['prev_vix'] >= low) & (merged['prev_vix'] < high)]
        if len(bucket) > 10:
            ibit_return = bucket['ibit_return']
            print(f"VIX {label:15s}: IBIT Avg: {ibit_return.mean():+.2f}% | Win: {(ibit_return > 0).mean()*100:.1f}% | n={len(bucket)}")

    # VIX spike analysis
    print("\n--- VIX Spike as Signal ---")
    vix['vix_change'] = (vix['vix_level'] - vix['prev_vix']) / vix['prev_vix'] * 100

    merged2 = pd.merge(ibit[['date', 'ibit_return']],
                       vix[['date', 'vix_change']],
                       on='date', how='inner')

    for threshold in [10, 15, 20]:
        vix_spike = merged2[merged2['vix_change'] > threshold]
        if len(vix_spike) > 3:
            ibit_return = vix_spike['ibit_return']
            print(f"VIX spike +{threshold}%: IBIT Avg: {ibit_return.mean():+.2f}% | Win: {(ibit_return > 0).mean()*100:.1f}% | n={len(vix_spike)}")

    return merged

def analyze_volatility_clustering(ibit):
    """Analyze IBIT's own volatility for trading signals."""
    print("\n" + "="*80)
    print("IBIT VOLATILITY CLUSTERING")
    print("="*80)

    df = ibit.copy()
    df['return'] = (df['close'] - df['open']) / df['open'] * 100
    df['abs_return'] = df['return'].abs()
    df['range'] = (df['high'] - df['low']) / df['open'] * 100

    # Rolling volatility
    df['vol_5d'] = df['return'].rolling(5).std()
    df['vol_10d'] = df['return'].rolling(10).std()
    df['avg_range_5d'] = df['range'].rolling(5).mean()

    # Volatility expansion/contraction
    df['vol_expanding'] = df['vol_5d'] > df['vol_10d']

    print("\n--- Volatility Regime ---")

    # Low vol periods
    vol_25 = df['vol_5d'].quantile(0.25)
    vol_75 = df['vol_5d'].quantile(0.75)

    low_vol = df[df['vol_5d'] < vol_25]
    high_vol = df[df['vol_5d'] > vol_75]

    if len(low_vol) > 10:
        ret = low_vol['return']
        print(f"Low Vol (5d < {vol_25:.1f}%): Avg: {ret.mean():+.2f}% | Win: {(ret > 0).mean()*100:.1f}% | n={len(low_vol)}")

    if len(high_vol) > 10:
        ret = high_vol['return']
        print(f"High Vol (5d > {vol_75:.1f}%): Avg: {ret.mean():+.2f}% | Win: {(ret > 0).mean()*100:.1f}% | n={len(high_vol)}")

    # After big range days
    print("\n--- After Big Range Days ---")
    for pct in [3, 4, 5]:
        big_range = df[df['range'].shift(1) > pct]
        if len(big_range) > 5:
            ret = big_range['return']
            print(f"After {pct}%+ range day: Avg: {ret.mean():+.2f}% | Win: {(ret > 0).mean()*100:.1f}% | n={len(big_range)}")

    # Narrow range breakout
    print("\n--- Narrow Range Breakout (NR7) ---")
    df['range_7d_min'] = df['range'].rolling(7).min()
    df['is_nr7'] = df['range'] == df['range_7d_min']

    # Day after NR7
    df['was_nr7'] = df['is_nr7'].shift(1)
    nr7_follow = df[df['was_nr7'] == True]
    if len(nr7_follow) > 5:
        ret = nr7_follow['return']
        range_exp = nr7_follow['range'].mean()
        print(f"Day after NR7: Avg: {ret.mean():+.2f}% | Avg Range: {range_exp:.1f}% | n={len(nr7_follow)}")

    return df

def backtest_best_strategies(ibit):
    """Backtest the most promising combined strategies."""
    print("\n" + "="*80)
    print("BACKTEST: COMBINED OPTIMAL STRATEGY")
    print("="*80)

    df = ibit.copy()
    df['datetime'] = pd.to_datetime(df['datetime'])
    df['date'] = df['datetime'].dt.date
    df['weekday'] = df['datetime'].dt.dayofweek
    df['return'] = (df['close'] - df['open']) / df['open'] * 100
    df['prev_return'] = df['return'].shift(1)
    df['prev_2_return'] = df['return'].shift(1) + df['return'].shift(2)

    # Strategy: Buy after -3%+ down day, NOT on Thursday, hold 1 day
    print("\n--- Strategy: Mean Reversion + Day Filter ---")
    print("Rules: Buy after -3%+ day, exit next close, skip if next day is Thursday")

    signals = df[(df['prev_return'] < -3) & (df['weekday'] != 3)]
    if len(signals) > 5:
        returns = signals['return']
        total = returns.sum()
        wins = (returns > 0).sum()
        losses = (returns <= 0).sum()

        print(f"Trades: {len(returns)}")
        print(f"Wins: {wins} | Losses: {losses}")
        print(f"Win Rate: {(returns > 0).mean()*100:.1f}%")
        print(f"Avg Return: {returns.mean():+.2f}%")
        print(f"Total Return: {total:+.1f}%")
        print(f"Best: {returns.max():+.2f}% | Worst: {returns.min():+.2f}%")

        # Calculate Sharpe
        sharpe = returns.mean() / returns.std() * np.sqrt(52) if returns.std() > 0 else 0
        print(f"Sharpe Ratio: {sharpe:.2f}")

    # Strategy: Short Thursday only
    print("\n--- Strategy: Short Thursday ---")
    print("Rules: Short at open on Thursday, cover at close")

    thursdays = df[df['weekday'] == 3]
    short_returns = -thursdays['return']  # Short = inverse of long

    print(f"Trades: {len(short_returns)}")
    print(f"Win Rate: {(short_returns > 0).mean()*100:.1f}%")
    print(f"Avg Return: {short_returns.mean():+.2f}%")
    print(f"Total Return: {short_returns.sum():+.1f}%")

    # Strategy: After 2-day losing streak
    print("\n--- Strategy: After 2+ Down Days ---")
    print("Rules: Buy at open after 2+ consecutive down days")

    df['streak'] = 0
    streak = 0
    for i in range(1, len(df)):
        if df.iloc[i-1]['return'] < 0:
            streak = min(streak - 1, -1)
        else:
            streak = 0
        df.iloc[i, df.columns.get_loc('streak')] = streak

    streak_signals = df[df['streak'] <= -2]
    if len(streak_signals) > 5:
        returns = streak_signals['return']
        print(f"Trades: {len(returns)}")
        print(f"Win Rate: {(returns > 0).mean()*100:.1f}%")
        print(f"Avg Return: {returns.mean():+.2f}%")
        print(f"Total Return: {returns.sum():+.1f}%")

    # COMBINED: All signals
    print("\n" + "="*80)
    print("COMBINED EQUITY CURVE SIMULATION")
    print("="*80)

    df['signal'] = 0  # 0 = no trade, 1 = long, -1 = short

    # Long signals
    df.loc[(df['prev_return'] < -2), 'signal'] = 1  # Mean reversion

    # Short signals
    df.loc[df['weekday'] == 3, 'signal'] = -1  # Short Thursday

    # Conflict resolution: if both signals, take long (mean reversion)
    df.loc[(df['prev_return'] < -2) & (df['weekday'] == 3), 'signal'] = 1

    # Calculate strategy returns
    df['strategy_return'] = df['signal'] * df['return']

    # Only count days with signals
    active_days = df[df['signal'] != 0]

    print(f"Total trading days: {len(active_days)}")
    print(f"Long trades: {(active_days['signal'] == 1).sum()}")
    print(f"Short trades: {(active_days['signal'] == -1).sum()}")

    total_return = active_days['strategy_return'].sum()
    win_rate = (active_days['strategy_return'] > 0).mean() * 100
    avg_return = active_days['strategy_return'].mean()

    print(f"\nTotal Return: {total_return:+.1f}%")
    print(f"Win Rate: {win_rate:.1f}%")
    print(f"Avg Return per Trade: {avg_return:+.2f}%")

    # Compare to buy and hold
    bh_return = (df['close'].iloc[-1] - df['open'].iloc[0]) / df['open'].iloc[0] * 100
    print(f"\nBuy & Hold Return: {bh_return:+.1f}%")
    print(f"Strategy vs B&H: {total_return - bh_return:+.1f}%")

    return df

def main():
    print("="*80)
    print("IBIT ADVANCED CORRELATION & VOLATILITY ANALYSIS")
    print(f"Analysis Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("="*80)

    print("\nFetching data...")
    ibit, btc, vix = get_data()
    print(f"IBIT: {len(ibit)} days | BTC: {len(btc)} days | VIX: {len(vix)} days")

    # Run analyses
    analyze_btc_overnight(ibit.copy(), btc.copy())
    analyze_vix_regime(ibit.copy(), vix.copy())
    analyze_volatility_clustering(ibit.copy())
    backtest_best_strategies(ibit.copy())

    print("\n" + "="*80)
    print("ANALYSIS COMPLETE")
    print("="*80)

if __name__ == "__main__":
    main()
