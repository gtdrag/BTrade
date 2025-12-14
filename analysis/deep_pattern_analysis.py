"""
Deep Pattern Analysis for IBIT
Comprehensive search for profitable trading patterns
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from scipy import stats
import warnings
warnings.filterwarnings('ignore')

def get_ibit_data():
    """Fetch all available IBIT data."""
    ticker = yf.Ticker("IBIT")

    # Daily data - full history
    daily = ticker.history(period="max", interval="1d")
    daily = daily.reset_index()
    daily.columns = [c.lower() for c in daily.columns]
    # Normalize column name
    if 'date' in daily.columns:
        daily = daily.rename(columns={'date': 'datetime'})

    # Intraday data - 5 min bars (60 days max)
    intraday = ticker.history(period="60d", interval="5m")
    intraday = intraday.reset_index()
    intraday.columns = [c.lower() for c in intraday.columns]
    if 'date' in intraday.columns:
        intraday = intraday.rename(columns={'date': 'datetime'})

    # Hourly data - 730 days max
    hourly = ticker.history(period="730d", interval="1h")
    hourly = hourly.reset_index()
    hourly.columns = [c.lower() for c in hourly.columns]
    if 'date' in hourly.columns:
        hourly = hourly.rename(columns={'date': 'datetime'})

    return daily, intraday, hourly

def analyze_daily_patterns(df):
    """Analyze daily price patterns."""
    print("\n" + "="*80)
    print("DAILY PATTERN ANALYSIS")
    print("="*80)

    df = df.copy()
    df['date'] = pd.to_datetime(df['datetime']).dt.date
    df['weekday'] = pd.to_datetime(df['datetime']).dt.dayofweek
    df['weekday_name'] = pd.to_datetime(df['datetime']).dt.day_name()

    # Daily returns
    df['daily_return'] = (df['close'] - df['open']) / df['open'] * 100
    df['overnight_return'] = (df['open'] - df['close'].shift(1)) / df['close'].shift(1) * 100
    df['intraday_range'] = (df['high'] - df['low']) / df['open'] * 100

    # Gap analysis
    df['gap'] = df['overnight_return']
    df['gap_filled'] = ((df['gap'] > 0) & (df['low'] <= df['close'].shift(1))) | \
                       ((df['gap'] < 0) & (df['high'] >= df['close'].shift(1)))

    print("\n1. DAY OF WEEK ANALYSIS")
    print("-" * 40)
    for day in range(5):
        day_data = df[df['weekday'] == day]
        if len(day_data) > 0:
            day_name = day_data['weekday_name'].iloc[0]
            avg_return = day_data['daily_return'].mean()
            win_rate = (day_data['daily_return'] > 0).mean() * 100
            avg_range = day_data['intraday_range'].mean()
            print(f"{day_name:10s}: Avg Return: {avg_return:+6.2f}% | Win Rate: {win_rate:5.1f}% | Avg Range: {avg_range:5.2f}% | n={len(day_data)}")

    print("\n2. GAP ANALYSIS (Overnight)")
    print("-" * 40)
    gap_up = df[df['gap'] > 0.5]
    gap_down = df[df['gap'] < -0.5]

    if len(gap_up) > 0:
        gap_up_fade = gap_up['daily_return'].mean()
        gap_up_fill = gap_up['gap_filled'].mean() * 100
        print(f"Gap Up (>0.5%):  Avg Intraday: {gap_up_fade:+5.2f}% | Fill Rate: {gap_up_fill:5.1f}% | n={len(gap_up)}")

    if len(gap_down) > 0:
        gap_down_fade = gap_down['daily_return'].mean()
        gap_down_fill = gap_down['gap_filled'].mean() * 100
        print(f"Gap Down (<-0.5%): Avg Intraday: {gap_down_fade:+5.2f}% | Fill Rate: {gap_down_fill:5.1f}% | n={len(gap_down)}")

    print("\n3. MOMENTUM ANALYSIS (Previous Day Effect)")
    print("-" * 40)
    df['prev_return'] = df['daily_return'].shift(1)

    # After up day
    after_up = df[df['prev_return'] > 0]
    if len(after_up) > 0:
        print(f"After UP day:   Avg: {after_up['daily_return'].mean():+5.2f}% | Win: {(after_up['daily_return'] > 0).mean()*100:5.1f}% | n={len(after_up)}")

    after_down = df[df['prev_return'] < 0]
    if len(after_down) > 0:
        print(f"After DOWN day: Avg: {after_down['daily_return'].mean():+5.2f}% | Win: {(after_down['daily_return'] > 0).mean()*100:5.1f}% | n={len(after_down)}")

    # After big moves
    after_big_up = df[df['prev_return'] > 2]
    after_big_down = df[df['prev_return'] < -2]

    if len(after_big_up) > 3:
        print(f"After BIG UP (>2%):   Avg: {after_big_up['daily_return'].mean():+5.2f}% | Win: {(after_big_up['daily_return'] > 0).mean()*100:5.1f}% | n={len(after_big_up)}")
    if len(after_big_down) > 3:
        print(f"After BIG DOWN (<-2%): Avg: {after_big_down['daily_return'].mean():+5.2f}% | Win: {(after_big_down['daily_return'] > 0).mean()*100:5.1f}% | n={len(after_big_down)}")

    print("\n4. STREAK ANALYSIS")
    print("-" * 40)
    df['streak'] = 0
    streak = 0
    for i in range(1, len(df)):
        if df.iloc[i-1]['daily_return'] > 0:
            streak = streak + 1 if streak > 0 else 1
        elif df.iloc[i-1]['daily_return'] < 0:
            streak = streak - 1 if streak < 0 else -1
        else:
            streak = 0
        df.iloc[i, df.columns.get_loc('streak')] = streak

    for streak_len in [2, 3]:
        after_up_streak = df[df['streak'] >= streak_len]
        after_down_streak = df[df['streak'] <= -streak_len]

        if len(after_up_streak) > 3:
            print(f"After {streak_len}+ UP days:   Avg: {after_up_streak['daily_return'].mean():+5.2f}% | Win: {(after_up_streak['daily_return'] > 0).mean()*100:5.1f}% | n={len(after_up_streak)}")
        if len(after_down_streak) > 3:
            print(f"After {streak_len}+ DOWN days: Avg: {after_down_streak['daily_return'].mean():+5.2f}% | Win: {(after_down_streak['daily_return'] > 0).mean()*100:5.1f}% | n={len(after_down_streak)}")

    print("\n5. VOLATILITY REGIME ANALYSIS")
    print("-" * 40)
    df['volatility'] = df['daily_return'].rolling(5).std()
    df['vol_regime'] = pd.qcut(df['volatility'].dropna(), q=3, labels=['Low', 'Med', 'High'])

    for regime in ['Low', 'Med', 'High']:
        regime_data = df[df['vol_regime'] == regime]
        if len(regime_data) > 0:
            print(f"{regime} Vol: Avg Return: {regime_data['daily_return'].mean():+5.2f}% | Win: {(regime_data['daily_return'] > 0).mean()*100:5.1f}% | n={len(regime_data)}")

    return df

def analyze_intraday_patterns(df):
    """Analyze intraday patterns with 5-min data."""
    print("\n" + "="*80)
    print("INTRADAY PATTERN ANALYSIS (5-min data)")
    print("="*80)

    df = df.copy()
    df['datetime'] = pd.to_datetime(df['datetime'])
    df['date'] = df['datetime'].dt.date
    df['time'] = df['datetime'].dt.time
    df['hour'] = df['datetime'].dt.hour
    df['minute'] = df['datetime'].dt.minute

    # Group by date to get daily metrics
    daily_groups = df.groupby('date')

    results = []
    for date, day_data in daily_groups:
        if len(day_data) < 10:
            continue

        day_data = day_data.sort_values('datetime')

        # Get key prices
        open_price = day_data.iloc[0]['open']
        close_price = day_data.iloc[-1]['close']

        # Morning session (9:30-10:30)
        morning = day_data[(day_data['hour'] == 9) | ((day_data['hour'] == 10) & (day_data['minute'] <= 30))]

        # 10 AM hour
        hour_10 = day_data[day_data['hour'] == 10]

        # 11 AM hour
        hour_11 = day_data[day_data['hour'] == 11]

        # Afternoon (2-4 PM)
        afternoon = day_data[day_data['hour'] >= 14]

        if len(morning) > 0 and len(afternoon) > 0:
            morning_low = morning['low'].min()
            morning_high = morning['high'].max()
            afternoon_close = close_price

            # Morning dip then recovery
            morning_dip = (open_price - morning_low) / open_price * 100
            morning_recovery = (afternoon_close - morning_low) / morning_low * 100 if morning_dip > 0.3 else 0

            # First hour range
            first_hour = day_data[day_data['hour'] <= 10]
            if len(first_hour) > 0:
                first_hour_high = first_hour['high'].max()
                first_hour_low = first_hour['low'].min()

                # Breakout analysis
                rest_of_day = day_data[day_data['hour'] > 10]
                if len(rest_of_day) > 0:
                    broke_high = rest_of_day['high'].max() > first_hour_high
                    broke_low = rest_of_day['low'].min() < first_hour_low
            else:
                broke_high = False
                broke_low = False

            results.append({
                'date': date,
                'open': open_price,
                'close': close_price,
                'daily_return': (close_price - open_price) / open_price * 100,
                'morning_dip': morning_dip,
                'morning_recovery': morning_recovery,
                'broke_first_hour_high': broke_high,
                'broke_first_hour_low': broke_low,
                'weekday': pd.Timestamp(date).dayofweek
            })

    results_df = pd.DataFrame(results)

    print("\n1. MORNING DIP RECOVERY ANALYSIS")
    print("-" * 40)
    for threshold in [0.3, 0.5, 0.8, 1.0, 1.5, 2.0]:
        dip_days = results_df[results_df['morning_dip'] >= threshold]
        if len(dip_days) >= 3:
            avg_recovery = dip_days['morning_recovery'].mean()
            avg_daily = dip_days['daily_return'].mean()
            win_rate = (dip_days['daily_return'] > 0).mean() * 100
            print(f"Dip >= {threshold}%: Recovery: {avg_recovery:+5.2f}% | Daily: {avg_daily:+5.2f}% | Win: {win_rate:5.1f}% | n={len(dip_days)}")

    print("\n2. FIRST HOUR BREAKOUT ANALYSIS")
    print("-" * 40)

    high_break = results_df[results_df['broke_first_hour_high'] == True]
    low_break = results_df[results_df['broke_first_hour_low'] == True]

    if len(high_break) > 0:
        print(f"Broke 1st Hour HIGH: Avg Daily: {high_break['daily_return'].mean():+5.2f}% | Win: {(high_break['daily_return'] > 0).mean()*100:5.1f}% | n={len(high_break)}")
    if len(low_break) > 0:
        print(f"Broke 1st Hour LOW:  Avg Daily: {low_break['daily_return'].mean():+5.2f}% | Win: {(low_break['daily_return'] > 0).mean()*100:5.1f}% | n={len(low_break)}")

    both_break = results_df[(results_df['broke_first_hour_high'] == True) & (results_df['broke_first_hour_low'] == True)]
    if len(both_break) > 0:
        print(f"Broke BOTH:          Avg Daily: {both_break['daily_return'].mean():+5.2f}% | n={len(both_break)}")

    print("\n3. HOURLY RETURN PATTERNS")
    print("-" * 40)

    # Calculate hourly returns
    hourly_returns = []
    for date, day_data in daily_groups:
        day_data = day_data.sort_values('datetime')
        for hour in range(9, 16):
            hour_data = day_data[day_data['hour'] == hour]
            if len(hour_data) >= 2:
                hour_open = hour_data.iloc[0]['open']
                hour_close = hour_data.iloc[-1]['close']
                hour_return = (hour_close - hour_open) / hour_open * 100
                hourly_returns.append({
                    'date': date,
                    'hour': hour,
                    'return': hour_return
                })

    hourly_df = pd.DataFrame(hourly_returns)
    for hour in range(9, 16):
        hour_data = hourly_df[hourly_df['hour'] == hour]
        if len(hour_data) > 0:
            print(f"{hour}:00 ET: Avg: {hour_data['return'].mean():+5.3f}% | Win: {(hour_data['return'] > 0).mean()*100:5.1f}% | n={len(hour_data)}")

    return results_df

def analyze_hourly_patterns(df):
    """Analyze hourly data for longer-term patterns."""
    print("\n" + "="*80)
    print("HOURLY/SWING PATTERN ANALYSIS")
    print("="*80)

    df = df.copy()
    df['datetime'] = pd.to_datetime(df['datetime'])
    df['date'] = df['datetime'].dt.date
    df['hour'] = df['datetime'].dt.hour

    # Calculate returns
    df['return_1h'] = df['close'].pct_change() * 100
    df['return_4h'] = df['close'].pct_change(4) * 100
    df['return_1d'] = df['close'].pct_change(7) * 100  # ~7 trading hours

    print("\n1. MEAN REVERSION ANALYSIS")
    print("-" * 40)

    # After large moves
    for threshold in [2, 3, 5]:
        big_down = df[df['return_4h'] < -threshold]
        if len(big_down) > 5:
            # Look at next 4 hours
            next_returns = []
            for idx in big_down.index:
                if idx + 4 < len(df):
                    next_ret = (df.loc[idx + 4, 'close'] - df.loc[idx, 'close']) / df.loc[idx, 'close'] * 100
                    next_returns.append(next_ret)
            if next_returns:
                avg_bounce = np.mean(next_returns)
                win_rate = sum(1 for r in next_returns if r > 0) / len(next_returns) * 100
                print(f"After {threshold}%+ DROP (4h): Next 4h Avg: {avg_bounce:+5.2f}% | Win: {win_rate:5.1f}% | n={len(next_returns)}")

        big_up = df[df['return_4h'] > threshold]
        if len(big_up) > 5:
            next_returns = []
            for idx in big_up.index:
                if idx + 4 < len(df):
                    next_ret = (df.loc[idx + 4, 'close'] - df.loc[idx, 'close']) / df.loc[idx, 'close'] * 100
                    next_returns.append(next_ret)
            if next_returns:
                avg_fade = np.mean(next_returns)
                win_rate = sum(1 for r in next_returns if r < 0) / len(next_returns) * 100
                print(f"After {threshold}%+ RALLY (4h): Next 4h Avg: {avg_fade:+5.2f}% | Fade Win: {win_rate:5.1f}% | n={len(next_returns)}")

    print("\n2. TREND FOLLOWING ANALYSIS")
    print("-" * 40)

    # SMA crossover signals
    df['sma_20'] = df['close'].rolling(20).mean()
    df['sma_50'] = df['close'].rolling(50).mean()
    df['above_sma20'] = df['close'] > df['sma_20']
    df['above_sma50'] = df['close'] > df['sma_50']

    above_both = df[(df['above_sma20'] == True) & (df['above_sma50'] == True)]
    below_both = df[(df['above_sma20'] == False) & (df['above_sma50'] == False)]

    if len(above_both) > 10:
        print(f"Above SMA20 & SMA50: Avg 1h Return: {above_both['return_1h'].mean():+5.3f}% | n={len(above_both)}")
    if len(below_both) > 10:
        print(f"Below SMA20 & SMA50: Avg 1h Return: {below_both['return_1h'].mean():+5.3f}% | n={len(below_both)}")

    print("\n3. WEEKLY SEASONALITY")
    print("-" * 40)

    # Aggregate to daily
    daily = df.groupby('date').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last'
    }).reset_index()

    daily['weekday'] = pd.to_datetime(daily['date']).dt.dayofweek
    daily['daily_return'] = (daily['close'] - daily['open']) / daily['open'] * 100

    # Week of month effect
    daily['week_of_month'] = (pd.to_datetime(daily['date']).dt.day - 1) // 7 + 1

    for week in range(1, 5):
        week_data = daily[daily['week_of_month'] == week]
        if len(week_data) > 5:
            print(f"Week {week} of Month: Avg: {week_data['daily_return'].mean():+5.2f}% | Win: {(week_data['daily_return'] > 0).mean()*100:5.1f}% | n={len(week_data)}")

    return df

def find_best_strategies(daily_df, intraday_results):
    """Synthesize findings into actionable strategies."""
    print("\n" + "="*80)
    print("STRATEGY SYNTHESIS & RECOMMENDATIONS")
    print("="*80)

    strategies = []

    # Strategy 1: Gap Fade
    print("\n--- STRATEGY 1: GAP FADE ---")
    print("Concept: Fade overnight gaps that are likely to fill")

    # Strategy 2: Mean Reversion after Big Moves
    print("\n--- STRATEGY 2: MEAN REVERSION ---")
    print("Concept: Buy after large intraday drops, expecting bounce")

    # Strategy 3: Momentum/Trend Following
    print("\n--- STRATEGY 3: TREND FOLLOWING ---")
    print("Concept: Go with the trend on breakout days")

    # Strategy 4: Day of Week
    print("\n--- STRATEGY 4: DAY OF WEEK ---")
    print("Concept: Trade only on statistically favorable days")

    # Strategy 5: Volatility Regime
    print("\n--- STRATEGY 5: VOLATILITY FILTER ---")
    print("Concept: Only trade in favorable volatility conditions")

def run_full_analysis():
    """Run complete analysis."""
    print("="*80)
    print("IBIT DEEP PATTERN ANALYSIS")
    print(f"Analysis Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("="*80)

    print("\nFetching data...")
    daily, intraday, hourly = get_ibit_data()

    print(f"Daily data: {len(daily)} bars from {daily['datetime'].min()} to {daily['datetime'].max()}")
    print(f"Intraday data: {len(intraday)} bars (5-min)")
    print(f"Hourly data: {len(hourly)} bars")

    # Run analyses
    daily_results = analyze_daily_patterns(daily)
    intraday_results = analyze_intraday_patterns(intraday)
    hourly_results = analyze_hourly_patterns(hourly)

    # Synthesize
    find_best_strategies(daily_results, intraday_results)

    return daily, intraday, hourly

if __name__ == "__main__":
    run_full_analysis()
