#!/usr/bin/env python3
"""
Run comprehensive backtests on all IBIT strategies.
"""

import sys
from datetime import date
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.multi_strategy_backtester import run_comprehensive_backtest


def main():
    print("=" * 80)
    print("IBIT COMPREHENSIVE STRATEGY BACKTEST")
    print("=" * 80)

    # Run from IBIT launch to today
    start_date = date(2024, 1, 15)
    end_date = date.today()
    initial_capital = 10000.0

    print(f"\nPeriod: {start_date} to {end_date}")
    print(f"Initial Capital: ${initial_capital:,.2f}")
    print()

    # Run comprehensive backtest
    results, comparison = run_comprehensive_backtest(
        start_date=start_date, end_date=end_date, initial_capital=initial_capital
    )

    # Display comparison table
    print("\n" + "=" * 80)
    print("STRATEGY COMPARISON")
    print("=" * 80)
    print(comparison.to_string(index=False))

    # Find best strategy
    best_name = max(results.keys(), key=lambda k: results[k].total_return_pct)
    best_result = results[best_name]

    print("\n" + "=" * 80)
    print(f"BEST STRATEGY: {best_name}")
    print("=" * 80)
    print(best_result.summary())

    # Print all summaries
    print("\n" + "=" * 80)
    print("ALL STRATEGY SUMMARIES")
    print("=" * 80)

    for name, result in sorted(results.items(), key=lambda x: -x[1].total_return_pct):
        print(f"\n--- {name} ---")
        print(f"Trades: {result.total_trades}")
        print(f"Win Rate: {result.win_rate:.1f}%")
        print(f"Total Return: {result.total_return_pct:+.1f}%")
        print(f"vs Buy&Hold: {result.total_return_pct - result.buy_hold_return_pct:+.1f}%")

    print("\n" + "=" * 80)
    print("BACKTEST COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
