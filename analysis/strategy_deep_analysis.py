"""
Deep Analysis of Smart Strategy Performance.

Analyzes strategy performance across multiple time periods and correlates
with market conditions (VIX, volatility, trend).
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.smart_strategy import SmartBacktester, StrategyConfig


def run_period_backtest(start_date: date, end_date: date, config: StrategyConfig = None) -> dict:
    """Run backtest for a specific period."""
    config = config or StrategyConfig()
    backtester = SmartBacktester(initial_capital=10000, config=config)

    try:
        days = backtester.load_data(start_date, end_date)
        if days < 5:  # Not enough data
            return None
        results = backtester.run_backtest()
        results['start_date'] = start_date
        results['end_date'] = end_date
        results['days'] = days
        return results
    except Exception as e:
        print(f"  Error for {start_date} to {end_date}: {e}")
        return None


def get_vix_data(start_date: date, end_date: date) -> pd.DataFrame:
    """Fetch VIX data for the period."""
    vix = yf.Ticker("^VIX")
    df = vix.history(start=start_date, end=end_date + timedelta(days=1))
    df = df.reset_index()
    df.columns = [c.lower() for c in df.columns]
    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date']).dt.date
    return df


def get_market_data(start_date: date, end_date: date) -> dict:
    """Get market context data (VIX, IBIT trend, volatility)."""
    data = {}

    # VIX
    try:
        vix_df = get_vix_data(start_date, end_date)
        if len(vix_df) > 0:
            data['vix_avg'] = vix_df['close'].mean()
            data['vix_start'] = vix_df['close'].iloc[0]
            data['vix_end'] = vix_df['close'].iloc[-1]
            data['vix_max'] = vix_df['close'].max()
            data['vix_min'] = vix_df['close'].min()
    except Exception as e:
        print(f"  VIX data error: {e}")

    # IBIT data for volatility and trend
    try:
        ibit = yf.Ticker("IBIT")
        ibit_df = ibit.history(start=start_date, end=end_date + timedelta(days=1))
        ibit_df = ibit_df.reset_index()

        if len(ibit_df) > 0:
            # Daily returns
            returns = (ibit_df['Close'] - ibit_df['Open']) / ibit_df['Open'] * 100
            data['ibit_volatility'] = returns.std()
            data['ibit_avg_daily_return'] = returns.mean()
            data['ibit_trend'] = (ibit_df['Close'].iloc[-1] - ibit_df['Close'].iloc[0]) / ibit_df['Close'].iloc[0] * 100

            # Count of big down days (triggers)
            data['big_down_days'] = sum(returns < -2.0)
            data['thursdays'] = len([d for d in pd.to_datetime(ibit_df['Date']) if d.weekday() == 3])
    except Exception as e:
        print(f"  IBIT data error: {e}")

    return data


def generate_rolling_periods(total_days: int = 365) -> list:
    """Generate a list of periods to analyze."""
    periods = []
    today = date.today()

    # Fixed periods from today
    fixed_periods = [
        ("Last 2 weeks", 14),
        ("Last 1 month", 30),
        ("Last 2 months", 60),
        ("Last 3 months", 90),
        ("Last 6 months", 180),
        ("Since ETF launch (Apr 2024)", (today - date(2024, 4, 15)).days),
    ]

    for name, days in fixed_periods:
        start = today - timedelta(days=days)
        # BITX/SBIT launched April 2024
        if start >= date(2024, 4, 15):
            periods.append({
                'name': name,
                'start': start,
                'end': today,
                'days': days
            })

    # Rolling 2-week windows going back
    print("\nGenerating rolling 2-week periods...")
    window_size = 14
    step_size = 7  # Move by 1 week each time
    earliest_date = date(2024, 4, 15)  # ETF launch

    current_end = today
    while current_end - timedelta(days=window_size) >= earliest_date:
        current_start = current_end - timedelta(days=window_size)
        periods.append({
            'name': f"2wk: {current_start.strftime('%m/%d')} - {current_end.strftime('%m/%d/%y')}",
            'start': current_start,
            'end': current_end,
            'days': window_size,
            'is_rolling': True
        })
        current_end -= timedelta(days=step_size)

    # Rolling 1-month windows
    print("Generating rolling 1-month periods...")
    window_size = 30
    step_size = 15

    current_end = today
    while current_end - timedelta(days=window_size) >= earliest_date:
        current_start = current_end - timedelta(days=window_size)
        periods.append({
            'name': f"1mo: {current_start.strftime('%m/%d')} - {current_end.strftime('%m/%d/%y')}",
            'start': current_start,
            'end': current_end,
            'days': window_size,
            'is_rolling': True
        })
        current_end -= timedelta(days=step_size)

    return periods


def analyze_all_periods():
    """Run comprehensive analysis across all periods."""
    print("=" * 80)
    print("DEEP STRATEGY ANALYSIS")
    print("=" * 80)

    periods = generate_rolling_periods()
    print(f"\nAnalyzing {len(periods)} periods...\n")

    results = []

    for i, period in enumerate(periods):
        is_rolling = period.get('is_rolling', False)
        if not is_rolling:
            print(f"\n[{i+1}/{len(periods)}] {period['name']}")

        # Run backtest
        backtest = run_period_backtest(period['start'], period['end'])
        if backtest is None:
            continue

        # Get market context
        market = get_market_data(period['start'], period['end'])

        result = {
            'period_name': period['name'],
            'start_date': period['start'],
            'end_date': period['end'],
            'period_days': period['days'],
            'is_rolling': is_rolling,

            # Strategy performance
            'strategy_return': backtest['total_return_pct'],
            'ibit_bh_return': backtest['ibit_bh_return'],
            'vs_bh': backtest['vs_ibit_bh'],
            'total_trades': backtest['total_trades'],
            'win_rate': backtest['win_rate'],
            'sharpe': backtest['sharpe_ratio'],
            'max_dd': backtest['max_drawdown_pct'],

            # Signal breakdown
            'mr_trades': backtest['mean_rev_trades'],
            'mr_win_rate': backtest['mean_rev_win_rate'],
            'thu_trades': backtest['short_thu_trades'],
            'thu_win_rate': backtest['short_thu_win_rate'],

            # Market context
            'vix_avg': market.get('vix_avg', 0),
            'vix_max': market.get('vix_max', 0),
            'ibit_volatility': market.get('ibit_volatility', 0),
            'ibit_trend': market.get('ibit_trend', 0),
            'big_down_days': market.get('big_down_days', 0),
        }
        results.append(result)

        if not is_rolling:
            print(f"  Strategy: {backtest['total_return_pct']:+.1f}% | IBIT B&H: {backtest['ibit_bh_return']:+.1f}% | vs B&H: {backtest['vs_ibit_bh']:+.1f}%")
            print(f"  Trades: {backtest['total_trades']} | Win Rate: {backtest['win_rate']:.0f}% | VIX avg: {market.get('vix_avg', 0):.1f}")

    return pd.DataFrame(results)


def print_summary_report(df: pd.DataFrame):
    """Print a comprehensive summary report."""
    print("\n" + "=" * 80)
    print("SUMMARY REPORT")
    print("=" * 80)

    # Filter to fixed periods only for main summary
    fixed = df[~df['is_rolling']].copy()

    print("\n### FIXED PERIOD PERFORMANCE ###")
    print("-" * 80)
    print(f"{'Period':<35} {'Strategy':>10} {'IBIT B&H':>10} {'vs B&H':>10} {'Trades':>8} {'Win%':>8}")
    print("-" * 80)

    for _, row in fixed.iterrows():
        print(f"{row['period_name']:<35} {row['strategy_return']:>+9.1f}% {row['ibit_bh_return']:>+9.1f}% {row['vs_bh']:>+9.1f}% {row['total_trades']:>8} {row['win_rate']:>7.0f}%")

    # Rolling period analysis
    rolling = df[df['is_rolling']].copy()

    if len(rolling) > 0:
        print("\n\n### ROLLING PERIOD ANALYSIS ###")
        print("-" * 80)

        # Strategy vs B&H comparison
        beating_bh = rolling[rolling['vs_bh'] > 0]
        losing_to_bh = rolling[rolling['vs_bh'] <= 0]

        print(f"\nPeriods where STRATEGY BEATS Buy & Hold: {len(beating_bh)} / {len(rolling)} ({len(beating_bh)/len(rolling)*100:.0f}%)")
        print(f"Periods where Buy & Hold wins:           {len(losing_to_bh)} / {len(rolling)} ({len(losing_to_bh)/len(rolling)*100:.0f}%)")

        print(f"\n{'Metric':<30} {'Strategy Wins':>20} {'B&H Wins':>20}")
        print("-" * 70)

        if len(beating_bh) > 0 and len(losing_to_bh) > 0:
            print(f"{'Avg VIX':<30} {beating_bh['vix_avg'].mean():>19.1f} {losing_to_bh['vix_avg'].mean():>19.1f}")
            print(f"{'Avg IBIT Volatility':<30} {beating_bh['ibit_volatility'].mean():>19.2f}% {losing_to_bh['ibit_volatility'].mean():>19.2f}%")
            print(f"{'Avg IBIT Trend':<30} {beating_bh['ibit_trend'].mean():>19.1f}% {losing_to_bh['ibit_trend'].mean():>19.1f}%")
            print(f"{'Avg Big Down Days':<30} {beating_bh['big_down_days'].mean():>19.1f} {losing_to_bh['big_down_days'].mean():>19.1f}")
            print(f"{'Avg Mean Rev Trades':<30} {beating_bh['mr_trades'].mean():>19.1f} {losing_to_bh['mr_trades'].mean():>19.1f}")
            print(f"{'Avg Mean Rev Win Rate':<30} {beating_bh['mr_win_rate'].mean():>18.0f}% {losing_to_bh['mr_win_rate'].mean():>18.0f}%")
            print(f"{'Avg Thursday Win Rate':<30} {beating_bh['thu_win_rate'].mean():>18.0f}% {losing_to_bh['thu_win_rate'].mean():>18.0f}%")

        # Best and worst periods
        print("\n\n### BEST PERIODS (Strategy vs B&H) ###")
        best = rolling.nlargest(5, 'vs_bh')
        for _, row in best.iterrows():
            print(f"  {row['period_name']}: Strategy {row['strategy_return']:+.1f}% vs B&H {row['ibit_bh_return']:+.1f}% (Δ {row['vs_bh']:+.1f}%) | VIX: {row['vix_avg']:.1f}")

        print("\n### WORST PERIODS (Strategy vs B&H) ###")
        worst = rolling.nsmallest(5, 'vs_bh')
        for _, row in worst.iterrows():
            print(f"  {row['period_name']}: Strategy {row['strategy_return']:+.1f}% vs B&H {row['ibit_bh_return']:+.1f}% (Δ {row['vs_bh']:+.1f}%) | VIX: {row['vix_avg']:.1f}")

        # Correlation analysis
        print("\n\n### CORRELATION ANALYSIS ###")
        print("-" * 80)
        print("Correlation between strategy outperformance (vs B&H) and market conditions:")

        correlations = {}
        for col in ['vix_avg', 'vix_max', 'ibit_volatility', 'ibit_trend', 'big_down_days', 'mr_trades']:
            if rolling[col].std() > 0 and rolling['vs_bh'].std() > 0:
                corr = rolling['vs_bh'].corr(rolling[col])
                correlations[col] = corr
                direction = "↑" if corr > 0 else "↓"
                strength = "strong" if abs(corr) > 0.5 else "moderate" if abs(corr) > 0.3 else "weak"
                print(f"  {col:<25}: {corr:+.3f} ({strength} {direction})")

        # Recent trend
        print("\n\n### RECENT TREND (Last 10 Rolling Periods) ###")
        print("-" * 80)
        recent = rolling.head(10)
        print(f"{'Period':<40} {'Strategy':>10} {'IBIT B&H':>10} {'vs B&H':>10}")
        print("-" * 70)
        for _, row in recent.iterrows():
            winner = "✓ Strategy" if row['vs_bh'] > 0 else "✗ B&H wins"
            print(f"{row['period_name']:<40} {row['strategy_return']:>+9.1f}% {row['ibit_bh_return']:>+9.1f}% {row['vs_bh']:>+9.1f}% {winner}")

        recent_wins = len(recent[recent['vs_bh'] > 0])
        print(f"\nRecent 10 periods: Strategy won {recent_wins}/10 ({recent_wins*10}%)")


def main():
    """Main entry point."""
    print("Starting deep analysis...")
    print("This will take a few minutes to fetch data and run backtests.\n")

    df = analyze_all_periods()

    # Save raw data
    output_path = Path(__file__).parent / "strategy_analysis_results.csv"
    df.to_csv(output_path, index=False)
    print(f"\nRaw data saved to: {output_path}")

    # Print summary
    print_summary_report(df)

    # Key insights
    print("\n" + "=" * 80)
    print("KEY INSIGHTS")
    print("=" * 80)

    rolling = df[df['is_rolling']].copy()
    if len(rolling) > 0:
        winning_pct = len(rolling[rolling['vs_bh'] > 0]) / len(rolling) * 100

        # VIX relationship
        high_vix = rolling[rolling['vix_avg'] > rolling['vix_avg'].median()]
        low_vix = rolling[rolling['vix_avg'] <= rolling['vix_avg'].median()]

        high_vix_win = len(high_vix[high_vix['vs_bh'] > 0]) / len(high_vix) * 100 if len(high_vix) > 0 else 0
        low_vix_win = len(low_vix[low_vix['vs_bh'] > 0]) / len(low_vix) * 100 if len(low_vix) > 0 else 0

        print(f"""
1. OVERALL: Strategy beats B&H in {winning_pct:.0f}% of rolling periods.

2. VIX RELATIONSHIP:
   - High VIX periods (above median): Strategy wins {high_vix_win:.0f}% of time
   - Low VIX periods (below median): Strategy wins {low_vix_win:.0f}% of time
   - Conclusion: {"Strategy works BETTER in high volatility" if high_vix_win > low_vix_win else "Strategy works BETTER in low volatility" if low_vix_win > high_vix_win else "No clear VIX relationship"}

3. MARKET TREND:
   - In UP trends: Check if strategy still adds value or if B&H is enough
   - In DOWN trends: Strategy should shine (leverage on bounces)

4. SIGNAL QUALITY:
   - Mean Reversion avg win rate: {rolling['mr_win_rate'].mean():.0f}%
   - Short Thursday avg win rate: {rolling['thu_win_rate'].mean():.0f}%
""")

        # Check recent underperformance
        recent = rolling.head(5)
        recent_losses = len(recent[recent['vs_bh'] <= 0])
        if recent_losses >= 3:
            print(f"""
⚠️  WARNING: Strategy has underperformed B&H in {recent_losses}/5 most recent periods!
   This could indicate:
   - Market regime has changed
   - Strong uptrend making B&H optimal
   - Signals losing their edge
""")


if __name__ == "__main__":
    main()
