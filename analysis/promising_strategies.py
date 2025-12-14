"""
Deep dive into the most promising trading strategies for IBIT
Based on initial pattern analysis findings
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

def get_data():
    """Fetch IBIT data."""
    ticker = yf.Ticker("IBIT")

    # Daily data
    daily = ticker.history(period="max", interval="1d").reset_index()
    daily.columns = [c.lower() for c in daily.columns]
    if 'date' in daily.columns:
        daily = daily.rename(columns={'date': 'datetime'})

    # 5-min data
    intraday = ticker.history(period="60d", interval="5m").reset_index()
    intraday.columns = [c.lower() for c in intraday.columns]
    if 'date' in intraday.columns:
        intraday = intraday.rename(columns={'date': 'datetime'})

    # Hourly data
    hourly = ticker.history(period="730d", interval="1h").reset_index()
    hourly.columns = [c.lower() for c in hourly.columns]
    if 'date' in hourly.columns:
        hourly = hourly.rename(columns={'date': 'datetime'})

    return daily, intraday, hourly

def strategy_1_mean_reversion_big_drops(daily):
    """
    STRATEGY 1: BUY AFTER BIG DROPS

    Finding: After -2%+ days, next day averages +0.45% with 62.3% win rate
    """
    print("\n" + "="*80)
    print("STRATEGY 1: MEAN REVERSION AFTER BIG DROPS")
    print("="*80)

    df = daily.copy()
    df['daily_return'] = (df['close'] - df['open']) / df['open'] * 100
    df['prev_return'] = df['daily_return'].shift(1)
    df['next_return'] = df['daily_return'].shift(-1)

    # Test different thresholds
    print("\nThreshold Analysis - Buy at CLOSE after X% DROP:")
    print("-" * 70)

    best_threshold = None
    best_sharpe = -999

    for threshold in [-1.5, -2.0, -2.5, -3.0, -3.5, -4.0, -5.0]:
        signals = df[df['daily_return'] < threshold]
        if len(signals) < 5:
            continue

        returns = signals['next_return'].dropna()
        if len(returns) == 0:
            continue

        avg_return = returns.mean()
        win_rate = (returns > 0).mean() * 100
        total_return = returns.sum()
        sharpe = returns.mean() / returns.std() * np.sqrt(252) if returns.std() > 0 else 0

        print(f"After {threshold:+5.1f}% drop: Next Day Avg: {avg_return:+5.2f}% | Win: {win_rate:5.1f}% | Total: {total_return:+6.1f}% | Sharpe: {sharpe:5.2f} | n={len(returns)}")

        if sharpe > best_sharpe and len(returns) >= 10:
            best_sharpe = sharpe
            best_threshold = threshold

    print(f"\nBest Threshold: {best_threshold}% (Sharpe: {best_sharpe:.2f})")

    # Detailed analysis of best threshold
    if best_threshold:
        print(f"\n--- Detailed Analysis for {best_threshold}% threshold ---")
        signals = df[df['daily_return'] < best_threshold].copy()

        # Day of week breakdown
        signals['weekday'] = pd.to_datetime(signals['datetime']).dt.day_name()
        print("\nBy Day of Week (when signal triggers):")
        for day in ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']:
            day_signals = signals[signals['weekday'] == day]
            if len(day_signals) > 2:
                day_returns = day_signals['next_return'].dropna()
                print(f"  {day}: Avg: {day_returns.mean():+5.2f}% | Win: {(day_returns > 0).mean()*100:5.1f}% | n={len(day_returns)}")

    return best_threshold

def strategy_2_first_hour_breakout(intraday):
    """
    STRATEGY 2: FIRST HOUR BREAKOUT

    Finding: Breaking first hour high -> +1.15% avg, 80.6% win rate
             Breaking first hour low -> -1.48% avg (short wins 81.8%)
    """
    print("\n" + "="*80)
    print("STRATEGY 2: FIRST HOUR BREAKOUT")
    print("="*80)

    df = intraday.copy()
    df['datetime'] = pd.to_datetime(df['datetime'])
    df['date'] = df['datetime'].dt.date
    df['hour'] = df['datetime'].dt.hour
    df['minute'] = df['datetime'].dt.minute

    results = []

    for date, day_data in df.groupby('date'):
        day_data = day_data.sort_values('datetime')
        if len(day_data) < 20:
            continue

        # First hour: 9:30-10:30
        first_hour = day_data[(day_data['hour'] == 9) | ((day_data['hour'] == 10) & (day_data['minute'] <= 30))]
        if len(first_hour) < 5:
            continue

        first_hour_high = first_hour['high'].max()
        first_hour_low = first_hour['low'].min()
        first_hour_close = first_hour.iloc[-1]['close']

        # Rest of day
        rest = day_data[(day_data['hour'] > 10) | ((day_data['hour'] == 10) & (day_data['minute'] > 30))]
        if len(rest) < 5:
            continue

        day_close = day_data.iloc[-1]['close']

        # Check for breakouts
        broke_high_time = None
        broke_low_time = None

        for _, bar in rest.iterrows():
            if broke_high_time is None and bar['high'] > first_hour_high:
                broke_high_time = bar['datetime']
            if broke_low_time is None and bar['low'] < first_hour_low:
                broke_low_time = bar['datetime']

        results.append({
            'date': date,
            'first_hour_high': first_hour_high,
            'first_hour_low': first_hour_low,
            'first_hour_range': first_hour_high - first_hour_low,
            'first_hour_close': first_hour_close,
            'day_close': day_close,
            'broke_high': broke_high_time is not None,
            'broke_low': broke_low_time is not None,
            'broke_high_time': broke_high_time,
            'broke_low_time': broke_low_time,
            'long_return': (day_close - first_hour_high) / first_hour_high * 100 if broke_high_time else None,
            'short_return': (first_hour_low - day_close) / first_hour_low * 100 if broke_low_time else None
        })

    results_df = pd.DataFrame(results)

    # Long breakout analysis
    print("\n--- LONG BREAKOUT (Buy break of first hour high) ---")
    long_trades = results_df[results_df['broke_high'] == True]
    if len(long_trades) > 0:
        long_returns = long_trades['long_return'].dropna()
        print(f"Trades: {len(long_returns)}")
        print(f"Win Rate: {(long_returns > 0).mean()*100:.1f}%")
        print(f"Avg Return: {long_returns.mean():+.2f}%")
        print(f"Total Return: {long_returns.sum():+.1f}%")
        print(f"Best Trade: {long_returns.max():+.2f}%")
        print(f"Worst Trade: {long_returns.min():+.2f}%")

    # Short breakout analysis
    print("\n--- SHORT BREAKOUT (Short break of first hour low) ---")
    short_trades = results_df[results_df['broke_low'] == True]
    if len(short_trades) > 0:
        short_returns = short_trades['short_return'].dropna()
        print(f"Trades: {len(short_returns)}")
        print(f"Win Rate: {(short_returns > 0).mean()*100:.1f}%")
        print(f"Avg Return: {short_returns.mean():+.2f}%")
        print(f"Total Return: {short_returns.sum():+.1f}%")
        print(f"Best Trade: {short_returns.max():+.2f}%")
        print(f"Worst Trade: {short_returns.min():+.2f}%")

    # Combined directional strategy
    print("\n--- DIRECTIONAL STRATEGY (Trade in direction of first breakout) ---")
    # Only trade FIRST breakout direction
    directional_returns = []
    for _, row in results_df.iterrows():
        if row['broke_high_time'] and row['broke_low_time']:
            # Both broke - trade whichever broke first
            if row['broke_high_time'] < row['broke_low_time']:
                directional_returns.append(row['long_return'])
            else:
                directional_returns.append(row['short_return'])
        elif row['broke_high']:
            directional_returns.append(row['long_return'])
        elif row['broke_low']:
            directional_returns.append(row['short_return'])

    if directional_returns:
        directional_returns = [r for r in directional_returns if r is not None]
        returns_arr = np.array(directional_returns)
        print(f"Trades: {len(returns_arr)}")
        print(f"Win Rate: {(returns_arr > 0).mean()*100:.1f}%")
        print(f"Avg Return: {returns_arr.mean():+.2f}%")
        print(f"Total Return: {returns_arr.sum():+.1f}%")

    return results_df

def strategy_3_big_intraday_bounce(hourly):
    """
    STRATEGY 3: BUY AFTER BIG INTRADAY DROPS

    Finding: After 5%+ 4-hour drop, next 4 hours average +1.10% with 68% win rate
    """
    print("\n" + "="*80)
    print("STRATEGY 3: INTRADAY MEAN REVERSION (BIG DROPS)")
    print("="*80)

    df = hourly.copy()
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.reset_index(drop=True)

    # Calculate rolling 4-hour returns
    df['return_4h'] = df['close'].pct_change(4) * 100

    # Calculate next 4-hour return
    df['next_4h'] = df['close'].shift(-4) / df['close'] * 100 - 100

    print("\nThreshold Analysis - Buy after X% 4-hour DROP:")
    print("-" * 70)

    for threshold in [3, 4, 5, 6, 7]:
        signals = df[df['return_4h'] < -threshold]
        if len(signals) < 5:
            continue

        returns = signals['next_4h'].dropna()
        if len(returns) == 0:
            continue

        avg_return = returns.mean()
        win_rate = (returns > 0).mean() * 100
        total_return = returns.sum()
        sharpe = returns.mean() / returns.std() * np.sqrt(252 * 2) if returns.std() > 0 else 0  # ~2 trades per day possible

        print(f"After -{threshold}% (4h): Next 4h Avg: {avg_return:+5.2f}% | Win: {win_rate:5.1f}% | Total: {total_return:+6.1f}% | n={len(returns)}")

    # Also test for holding until end of day
    print("\n--- Hold until EOD after 5% 4-hour drop ---")
    df['date'] = df['datetime'].dt.date
    df['hour'] = df['datetime'].dt.hour

    eod_returns = []
    signals = df[df['return_4h'] < -5]

    for idx, signal in signals.iterrows():
        signal_date = signal['date']
        signal_hour = signal['hour']

        # Get rest of day
        rest_of_day = df[(df['date'] == signal_date) & (df.index > idx)]
        if len(rest_of_day) > 0:
            eod_price = rest_of_day.iloc[-1]['close']
            signal_price = signal['close']
            eod_return = (eod_price - signal_price) / signal_price * 100
            eod_returns.append(eod_return)

    if eod_returns:
        eod_arr = np.array(eod_returns)
        print(f"Trades: {len(eod_arr)}")
        print(f"Win Rate: {(eod_arr > 0).mean()*100:.1f}%")
        print(f"Avg Return: {eod_arr.mean():+.2f}%")
        print(f"Total Return: {eod_arr.sum():+.1f}%")

def strategy_4_trend_following(hourly):
    """
    STRATEGY 4: TREND FOLLOWING WITH MOVING AVERAGES

    Finding: Above SMA20 & SMA50 -> +0.32% avg per hour
             Below SMA20 & SMA50 -> -0.29% avg per hour
    """
    print("\n" + "="*80)
    print("STRATEGY 4: TREND FOLLOWING (SMA CROSSOVER)")
    print("="*80)

    df = hourly.copy()
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.reset_index(drop=True)

    # Calculate SMAs
    df['sma_10'] = df['close'].rolling(10).mean()
    df['sma_20'] = df['close'].rolling(20).mean()
    df['sma_50'] = df['close'].rolling(50).mean()

    df['return_1h'] = df['close'].pct_change() * 100
    df['return_7h'] = df['close'].pct_change(7) * 100  # ~1 trading day

    # Test different SMA combinations
    print("\n--- SMA Regime Analysis (1-hour forward returns) ---")

    # Price above both
    above_both = df[(df['close'] > df['sma_20']) & (df['close'] > df['sma_50'])]
    below_both = df[(df['close'] < df['sma_20']) & (df['close'] < df['sma_50'])]

    if len(above_both) > 100:
        returns = above_both['return_1h'].dropna()
        print(f"Above SMA20 & SMA50: Avg 1h: {returns.mean():+.3f}% | Win: {(returns > 0).mean()*100:.1f}% | n={len(returns)}")

    if len(below_both) > 100:
        returns = below_both['return_1h'].dropna()
        print(f"Below SMA20 & SMA50: Avg 1h: {returns.mean():+.3f}% | Win: {(returns > 0).mean()*100:.1f}% | n={len(returns)}")

    # Golden cross / Death cross signals
    print("\n--- MA Crossover Signals (7-hour forward returns) ---")

    df['sma20_above_50'] = df['sma_20'] > df['sma_50']
    df['cross_up'] = (df['sma20_above_50'] == True) & (df['sma20_above_50'].shift(1) == False)
    df['cross_down'] = (df['sma20_above_50'] == False) & (df['sma20_above_50'].shift(1) == True)

    golden_cross = df[df['cross_up'] == True]
    death_cross = df[df['cross_down'] == True]

    if len(golden_cross) > 3:
        returns = golden_cross['return_7h'].dropna()
        print(f"Golden Cross (20 crosses above 50): Avg 7h: {returns.mean():+.2f}% | Win: {(returns > 0).mean()*100:.1f}% | n={len(returns)}")

    if len(death_cross) > 3:
        returns = death_cross['return_7h'].dropna()
        print(f"Death Cross (20 crosses below 50): Avg 7h: {returns.mean():+.2f}% | Win: {(returns > 0).mean()*100:.1f}% | n={len(returns)}")

def strategy_5_day_of_week(daily):
    """
    STRATEGY 5: DAY OF WEEK FILTER

    Finding: Monday +0.34% avg, 50% win
             Thursday -0.71% avg, 39.6% win (SHORT opportunity?)
    """
    print("\n" + "="*80)
    print("STRATEGY 5: DAY OF WEEK FILTER")
    print("="*80)

    df = daily.copy()
    df['datetime'] = pd.to_datetime(df['datetime'])
    df['weekday'] = df['datetime'].dt.dayofweek
    df['weekday_name'] = df['datetime'].dt.day_name()
    df['daily_return'] = (df['close'] - df['open']) / df['open'] * 100

    print("\n--- Full Day of Week Analysis ---")
    for day in range(5):
        day_data = df[df['weekday'] == day]
        if len(day_data) > 10:
            returns = day_data['daily_return']
            day_name = day_data['weekday_name'].iloc[0]
            sharpe = returns.mean() / returns.std() * np.sqrt(52) if returns.std() > 0 else 0

            print(f"{day_name:10s}: Avg: {returns.mean():+5.2f}% | Win: {(returns > 0).mean()*100:5.1f}% | "
                  f"Sharpe: {sharpe:5.2f} | Best: {returns.max():+5.1f}% | Worst: {returns.min():+5.1f}% | n={len(returns)}")

    # Test: Long Mon-Wed, Avoid Thu
    print("\n--- Strategy: Long Mon-Wed, Skip Thu-Fri ---")
    strategy_days = df[df['weekday'].isin([0, 1, 2])]  # Mon, Tue, Wed
    if len(strategy_days) > 20:
        returns = strategy_days['daily_return']
        print(f"Mon-Wed Only: Avg: {returns.mean():+.2f}% | Win: {(returns > 0).mean()*100:.1f}% | Total: {returns.sum():+.1f}% | n={len(returns)}")

    # Test: Short Thursday
    print("\n--- Strategy: Short Thursday Only ---")
    thu_data = df[df['weekday'] == 3]
    if len(thu_data) > 10:
        # Short return = negative of long return
        short_returns = -thu_data['daily_return']
        print(f"Short Thu: Avg: {short_returns.mean():+.2f}% | Win: {(short_returns > 0).mean()*100:.1f}% | Total: {short_returns.sum():+.1f}% | n={len(short_returns)}")

def strategy_6_combined_filters(daily, intraday):
    """
    STRATEGY 6: COMBINED FILTERS

    Combine multiple edge conditions
    """
    print("\n" + "="*80)
    print("STRATEGY 6: COMBINED MULTI-FACTOR")
    print("="*80)

    df = daily.copy()
    df['datetime'] = pd.to_datetime(df['datetime'])
    df['weekday'] = df['datetime'].dt.dayofweek
    df['daily_return'] = (df['close'] - df['open']) / df['open'] * 100
    df['prev_return'] = df['daily_return'].shift(1)
    df['next_return'] = df['daily_return'].shift(-1)

    # Combination: Previous day down + NOT Thursday
    print("\n--- Filter: Previous day DOWN + NOT Thursday ---")
    filtered = df[(df['prev_return'] < 0) & (df['weekday'] != 3)]
    if len(filtered) > 20:
        returns = filtered['daily_return']
        print(f"Trades: {len(returns)} | Avg: {returns.mean():+.2f}% | Win: {(returns > 0).mean()*100:.1f}% | Total: {returns.sum():+.1f}%")

    # Combination: Previous day BIG DOWN (-2%+) + Monday
    print("\n--- Filter: Prev day DOWN >2% + Monday ---")
    filtered = df[(df['prev_return'] < -2) & (df['weekday'] == 0)]
    if len(filtered) > 5:
        returns = filtered['daily_return']
        print(f"Trades: {len(returns)} | Avg: {returns.mean():+.2f}% | Win: {(returns > 0).mean()*100:.1f}% | Total: {returns.sum():+.1f}%")

    # Combination: After 2+ down streak + NOT Thursday
    df['streak'] = 0
    streak = 0
    for i in range(1, len(df)):
        if df.iloc[i-1]['daily_return'] < 0:
            streak = streak - 1 if streak < 0 else -1
        else:
            streak = 0
        df.iloc[i, df.columns.get_loc('streak')] = streak

    print("\n--- Filter: After 2+ DOWN streak + NOT Thursday ---")
    filtered = df[(df['streak'] <= -2) & (df['weekday'] != 3)]
    if len(filtered) > 10:
        returns = filtered['daily_return']
        print(f"Trades: {len(returns)} | Avg: {returns.mean():+.2f}% | Win: {(returns > 0).mean()*100:.1f}% | Total: {returns.sum():+.1f}%")

def generate_recommendations():
    """Generate final trading recommendations."""
    print("\n" + "="*80)
    print("FINAL RECOMMENDATIONS")
    print("="*80)

    print("""
Based on deep pattern analysis of IBIT, here are the most promising strategies:

╔═══════════════════════════════════════════════════════════════════════════════╗
║  STRATEGY A: FIRST HOUR BREAKOUT (HIGHEST CONVICTION)                        ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║  • Wait for first trading hour (9:30-10:30 AM ET) to establish range         ║
║  • If price breaks ABOVE first hour high after 10:30 AM → GO LONG            ║
║  • If price breaks BELOW first hour low after 10:30 AM → GO SHORT            ║
║  • Exit at market close                                                       ║
║                                                                               ║
║  Historical Performance:                                                      ║
║    Long Breakout:  80.6% win rate, +1.15% avg return                         ║
║    Short Breakout: 81.8% win rate, +1.48% avg return                         ║
╚═══════════════════════════════════════════════════════════════════════════════╝

╔═══════════════════════════════════════════════════════════════════════════════╗
║  STRATEGY B: MEAN REVERSION AFTER BIG DROPS                                  ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║  • Monitor for days with -2% or worse close-to-close decline                 ║
║  • Buy at close on down day, sell next day at close                          ║
║  • Better if drop occurs on days OTHER than Wednesday                        ║
║                                                                               ║
║  Historical Performance:                                                      ║
║    After -2%+ day: 62.3% win rate, +0.45% avg next-day return                ║
║    After -5%+ intraday drop: 68% win rate, +1.10% avg 4-hour return          ║
╚═══════════════════════════════════════════════════════════════════════════════╝

╔═══════════════════════════════════════════════════════════════════════════════╗
║  STRATEGY C: AVOID THURSDAYS                                                 ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║  • Thursday is statistically the worst day for IBIT                          ║
║  • Either skip trading entirely or consider SHORT bias                       ║
║                                                                               ║
║  Historical Performance:                                                      ║
║    Thursday avg: -0.71%, only 39.6% win rate                                 ║
║    Short Thursday: 60.4% win rate (inverse)                                  ║
╚═══════════════════════════════════════════════════════════════════════════════╝

╔═══════════════════════════════════════════════════════════════════════════════╗
║  STRATEGY D: TREND FOLLOWING                                                 ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║  • Only take long positions when price is above both SMA20 and SMA50         ║
║  • Avoid or go short when below both                                         ║
║                                                                               ║
║  Historical Performance:                                                      ║
║    Above both SMAs: +0.32% avg hourly return                                 ║
║    Below both SMAs: -0.29% avg hourly return                                 ║
╚═══════════════════════════════════════════════════════════════════════════════╝

═══════════════════════════════════════════════════════════════════════════════
WHAT DOES NOT WORK:
═══════════════════════════════════════════════════════════════════════════════
• The original "10 AM dip" strategy - morning dips do NOT reliably recover
• Gap trading - gaps don't fill consistently enough for an edge
• Buying any dip without size filter - small dips have no edge

═══════════════════════════════════════════════════════════════════════════════
IMPORTANT CAVEATS:
═══════════════════════════════════════════════════════════════════════════════
1. Past performance does not guarantee future results
2. Sample sizes are relatively small (IBIT launched Jan 2024)
3. Bitcoin/crypto markets are highly volatile and unpredictable
4. These patterns may be regime-dependent
5. Transaction costs and slippage will reduce returns
6. Paper trade extensively before any live trading
""")

def main():
    """Run all strategy analyses."""
    print("Fetching IBIT data...")
    daily, intraday, hourly = get_data()

    print(f"Daily: {len(daily)} bars | Intraday: {len(intraday)} bars | Hourly: {len(hourly)} bars")

    # Run all strategy analyses
    strategy_1_mean_reversion_big_drops(daily)
    strategy_2_first_hour_breakout(intraday)
    strategy_3_big_intraday_bounce(hourly)
    strategy_4_trend_following(hourly)
    strategy_5_day_of_week(daily)
    strategy_6_combined_filters(daily, intraday)

    # Generate recommendations
    generate_recommendations()

if __name__ == "__main__":
    main()
