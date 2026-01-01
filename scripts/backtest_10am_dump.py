#!/usr/bin/env python3
"""
Backtest comparing strategy WITH and WITHOUT 10 AM Dump.

10 AM Dump Strategy:
- Entry: 9:35 AM (buy SBIT)
- Exit: 10:30 AM (sell SBIT)
- Runs every trading day

This script uses intraday data to properly simulate the 10 AM dump trades.
"""

import os
import sys
from datetime import date, timedelta
from typing import Any, Dict, List

import numpy as np
import pandas as pd

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from src.data_providers import AlpacaProvider  # noqa: E402


def get_intraday_data(
    alpaca: AlpacaProvider, symbol: str, start_date: date, end_date: date
) -> pd.DataFrame:
    """Fetch intraday (minute) bars for a symbol."""
    bars = alpaca.get_historical_bars(
        symbol, start_date.isoformat(), (end_date + timedelta(days=1)).isoformat(), "1Min"
    )

    if not bars:
        return pd.DataFrame()

    df = pd.DataFrame(bars)
    df["timestamp"] = pd.to_datetime(df["t"])
    # Convert UTC to ET (Eastern Time)
    df["timestamp_et"] = df["timestamp"].dt.tz_convert("America/New_York")
    df["date"] = df["timestamp_et"].dt.date
    df["time"] = df["timestamp_et"].dt.strftime("%H:%M")
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    return df


def get_daily_data(
    alpaca: AlpacaProvider, symbol: str, start_date: date, end_date: date
) -> pd.DataFrame:
    """Fetch daily bars for a symbol."""
    bars = alpaca.get_historical_bars(
        symbol, start_date.isoformat(), (end_date + timedelta(days=1)).isoformat(), "1Day"
    )

    if not bars:
        return pd.DataFrame()

    df = pd.DataFrame(bars)
    df["date"] = pd.to_datetime(df["t"]).dt.date
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    return df


def get_btc_overnight(
    alpaca: AlpacaProvider, start_date: date, end_date: date
) -> Dict[date, float]:
    """Get BTC overnight changes (yesterday close → today open)."""
    bars = alpaca.get_crypto_bars(
        "BTC/USD", start_date.isoformat(), (end_date + timedelta(days=1)).isoformat(), "1Day"
    )

    if not bars:
        return {}

    df = pd.DataFrame(bars)
    df["date"] = pd.to_datetime(df["t"]).dt.date
    df = df.rename(columns={"o": "open", "c": "close"})
    df = df.sort_values("date").reset_index(drop=True)

    overnight = {}
    for i in range(1, len(df)):
        prev_close = df.iloc[i - 1]["close"]
        today_open = df.iloc[i]["open"]
        pct = (today_open - prev_close) / prev_close * 100 if prev_close > 0 else 0
        overnight[df.iloc[i]["date"]] = pct

    return overnight


def find_nearest_bar(
    day_data: pd.DataFrame, target_time: str, window_minutes: int = 5
) -> pd.Series:
    """Find the bar nearest to target_time within a window."""
    target_hour, target_min = map(int, target_time.split(":"))
    target_minutes = target_hour * 60 + target_min

    day_data = day_data.copy()
    day_data["minutes"] = day_data["timestamp_et"].dt.hour * 60 + day_data["timestamp_et"].dt.minute
    day_data["diff"] = abs(day_data["minutes"] - target_minutes)

    # Filter to within window
    nearby = day_data[day_data["diff"] <= window_minutes]
    if nearby.empty:
        return pd.Series()

    return nearby.loc[nearby["diff"].idxmin()]


def backtest_10am_dump_only(
    sbit_intraday: pd.DataFrame, initial_capital: float = 10000.0
) -> Dict[str, Any]:
    """Backtest ONLY the 10 AM dump strategy (SBIT 9:35 → 10:30)."""
    capital = initial_capital
    trades = []
    slippage = 0.02  # 0.02%

    # Group by date
    for trade_date, day_data in sbit_intraday.groupby("date"):
        # Find nearest bars to 9:35 entry and 10:30 exit (within 5 min window)
        entry_bar = find_nearest_bar(day_data, "09:35", window_minutes=5)
        exit_bar = find_nearest_bar(day_data, "10:30", window_minutes=5)

        if entry_bar.empty or exit_bar.empty:
            continue

        entry_price = entry_bar["close"] * (1 + slippage / 100)
        exit_price = exit_bar["close"] * (1 - slippage / 100)

        ret = (exit_price - entry_price) / entry_price
        capital *= 1 + ret

        trades.append(
            {
                "date": trade_date,
                "signal": "10am_dump",
                "etf": "SBIT",
                "entry": entry_price,
                "exit": exit_price,
                "return_pct": ret * 100,
                "capital": capital,
            }
        )

    return calculate_metrics(trades, initial_capital, capital)


def backtest_mean_reversion(
    ibit_daily: pd.DataFrame,
    bitu_daily: pd.DataFrame,
    btc_overnight: Dict[date, float],
    threshold: float = -2.0,
    initial_capital: float = 10000.0,
    use_btc_filter: bool = True,
) -> Dict[str, Any]:
    """Backtest mean reversion strategy (BITU after IBIT drops 2%+)."""
    capital = initial_capital
    trades = []
    skipped = []
    slippage = 0.02

    # Calculate previous day returns
    ibit_daily = ibit_daily.sort_values("date").reset_index(drop=True)
    ibit_daily["daily_return"] = (
        (ibit_daily["close"] - ibit_daily["open"]) / ibit_daily["open"] * 100
    )
    ibit_daily["prev_return"] = ibit_daily["daily_return"].shift(1)

    # Create BITU lookup by date
    bitu_by_date = {row["date"]: row for _, row in bitu_daily.iterrows()}

    for i in range(1, len(ibit_daily)):
        row = ibit_daily.iloc[i]
        prev_ret = row["prev_return"]
        trade_date = row["date"]

        if pd.isna(prev_ret) or prev_ret >= threshold:
            continue

        # Check BTC overnight filter
        if use_btc_filter and trade_date in btc_overnight:
            btc_change = btc_overnight[trade_date]
            if btc_change <= 0:
                skipped.append(
                    {
                        "date": trade_date,
                        "reason": f"BTC down {btc_change:.2f}%",
                        "prev_return": prev_ret,
                    }
                )
                continue

        # Execute trade
        if trade_date not in bitu_by_date:
            continue

        bitu_row = bitu_by_date[trade_date]
        entry_price = bitu_row["open"] * (1 + slippage / 100)
        exit_price = bitu_row["close"] * (1 - slippage / 100)

        ret = (exit_price - entry_price) / entry_price
        capital *= 1 + ret

        trades.append(
            {
                "date": trade_date,
                "signal": "mean_reversion",
                "etf": "BITU",
                "entry": entry_price,
                "exit": exit_price,
                "return_pct": ret * 100,
                "capital": capital,
                "prev_ibit_return": prev_ret,
            }
        )

    metrics = calculate_metrics(trades, initial_capital, capital)
    metrics["skipped_by_btc_filter"] = len(skipped)
    metrics["skipped_details"] = skipped
    return metrics


def backtest_combined(
    ibit_daily: pd.DataFrame,
    bitu_daily: pd.DataFrame,
    sbit_intraday: pd.DataFrame,
    btc_overnight: Dict[date, float],
    include_10am_dump: bool = True,
    initial_capital: float = 10000.0,
) -> Dict[str, Any]:
    """
    Backtest combined strategy.

    Priority (matching actual code):
    1. 10 AM Dump (if enabled) - takes priority at 9:35 AM
    2. Mean Reversion
    """
    capital = initial_capital
    trades = []
    skipped = []
    slippage = 0.02
    threshold = -2.0

    # Prepare data
    ibit_daily = ibit_daily.sort_values("date").reset_index(drop=True)
    ibit_daily["daily_return"] = (
        (ibit_daily["close"] - ibit_daily["open"]) / ibit_daily["open"] * 100
    )
    ibit_daily["prev_return"] = ibit_daily["daily_return"].shift(1)

    bitu_by_date = {row["date"]: row for _, row in bitu_daily.iterrows()}

    # Get 10 AM dump data by date
    sbit_10am = {}
    for trade_date, day_data in sbit_intraday.groupby("date"):
        entry_bar = find_nearest_bar(day_data, "09:35", window_minutes=5)
        exit_bar = find_nearest_bar(day_data, "10:30", window_minutes=5)
        if not entry_bar.empty and not exit_bar.empty:
            sbit_10am[trade_date] = {
                "entry": entry_bar["close"],
                "exit": exit_bar["close"],
            }

    # Track which dates had 10 AM dump trades
    ten_am_dump_dates = set()

    for i in range(1, len(ibit_daily)):
        row = ibit_daily.iloc[i]
        prev_ret = row["prev_return"]
        trade_date = row["date"]

        # Check if 10 AM dump should run (it runs EVERY day if enabled)
        if include_10am_dump and trade_date in sbit_10am:
            # 10 AM dump takes priority
            data = sbit_10am[trade_date]
            entry_price = data["entry"] * (1 + slippage / 100)
            exit_price = data["exit"] * (1 - slippage / 100)

            ret = (exit_price - entry_price) / entry_price
            capital *= 1 + ret

            trades.append(
                {
                    "date": trade_date,
                    "signal": "10am_dump",
                    "etf": "SBIT",
                    "entry": entry_price,
                    "exit": exit_price,
                    "return_pct": ret * 100,
                    "capital": capital,
                }
            )
            ten_am_dump_dates.add(trade_date)

            # Skip mean reversion check for this day since 10 AM dump took priority
            # (This matches actual code behavior at 9:35 AM)
            continue

        # Mean reversion check (only if 10 AM dump didn't fire)
        if pd.isna(prev_ret) or prev_ret >= threshold:
            continue

        # BTC overnight filter
        if trade_date in btc_overnight:
            btc_change = btc_overnight[trade_date]
            if btc_change <= 0:
                skipped.append(
                    {
                        "date": trade_date,
                        "reason": f"BTC down {btc_change:.2f}%",
                        "prev_return": prev_ret,
                    }
                )
                continue

        # Execute mean reversion
        if trade_date not in bitu_by_date:
            continue

        bitu_row = bitu_by_date[trade_date]
        entry_price = bitu_row["open"] * (1 + slippage / 100)
        exit_price = bitu_row["close"] * (1 - slippage / 100)

        ret = (exit_price - entry_price) / entry_price
        capital *= 1 + ret

        trades.append(
            {
                "date": trade_date,
                "signal": "mean_reversion",
                "etf": "BITU",
                "entry": entry_price,
                "exit": exit_price,
                "return_pct": ret * 100,
                "capital": capital,
            }
        )

    metrics = calculate_metrics(trades, initial_capital, capital)
    metrics["ten_am_dump_trades"] = len([t for t in trades if t["signal"] == "10am_dump"])
    metrics["mean_reversion_trades"] = len([t for t in trades if t["signal"] == "mean_reversion"])
    metrics["skipped_by_btc_filter"] = len(skipped)
    return metrics


def backtest_combined_mr_priority(
    ibit_daily: pd.DataFrame,
    bitu_daily: pd.DataFrame,
    sbit_intraday: pd.DataFrame,
    btc_overnight: Dict[date, float],
    initial_capital: float = 10000.0,
) -> Dict[str, Any]:
    """
    Backtest combined strategy with MEAN REVERSION taking priority.

    On mean reversion days: do mean reversion (BITU), skip 10 AM dump
    On non-mean-reversion days: do 10 AM dump (SBIT)
    """
    capital = initial_capital
    trades = []
    skipped = []
    slippage = 0.02
    threshold = -2.0

    # Prepare data
    ibit_daily = ibit_daily.sort_values("date").reset_index(drop=True)
    ibit_daily["daily_return"] = (
        (ibit_daily["close"] - ibit_daily["open"]) / ibit_daily["open"] * 100
    )
    ibit_daily["prev_return"] = ibit_daily["daily_return"].shift(1)

    bitu_by_date = {row["date"]: row for _, row in bitu_daily.iterrows()}

    # Get 10 AM dump data by date
    sbit_10am = {}
    for trade_date, day_data in sbit_intraday.groupby("date"):
        entry_bar = find_nearest_bar(day_data, "09:35", window_minutes=5)
        exit_bar = find_nearest_bar(day_data, "10:30", window_minutes=5)
        if not entry_bar.empty and not exit_bar.empty:
            sbit_10am[trade_date] = {
                "entry": entry_bar["close"],
                "exit": exit_bar["close"],
            }

    # Build set of mean reversion days (for reference)
    mean_rev_days = set()

    for i in range(1, len(ibit_daily)):
        row = ibit_daily.iloc[i]
        prev_ret = row["prev_return"]
        trade_date = row["date"]

        # Check if this is a mean reversion day
        is_mean_rev_day = False
        if not pd.isna(prev_ret) and prev_ret < threshold:
            # Check BTC overnight filter
            if trade_date in btc_overnight:
                btc_change = btc_overnight[trade_date]
                if btc_change > 0:
                    is_mean_rev_day = True
                else:
                    skipped.append(
                        {
                            "date": trade_date,
                            "reason": f"BTC down {btc_change:.2f}%",
                            "prev_return": prev_ret,
                        }
                    )

        if is_mean_rev_day:
            # MEAN REVERSION takes priority
            mean_rev_days.add(trade_date)

            if trade_date not in bitu_by_date:
                continue

            bitu_row = bitu_by_date[trade_date]
            entry_price = bitu_row["open"] * (1 + slippage / 100)
            exit_price = bitu_row["close"] * (1 - slippage / 100)

            ret = (exit_price - entry_price) / entry_price
            capital *= 1 + ret

            trades.append(
                {
                    "date": trade_date,
                    "signal": "mean_reversion",
                    "etf": "BITU",
                    "entry": entry_price,
                    "exit": exit_price,
                    "return_pct": ret * 100,
                    "capital": capital,
                }
            )
        else:
            # Not a mean reversion day - do 10 AM dump if available
            if trade_date in sbit_10am:
                data = sbit_10am[trade_date]
                entry_price = data["entry"] * (1 + slippage / 100)
                exit_price = data["exit"] * (1 - slippage / 100)

                ret = (exit_price - entry_price) / entry_price
                capital *= 1 + ret

                trades.append(
                    {
                        "date": trade_date,
                        "signal": "10am_dump",
                        "etf": "SBIT",
                        "entry": entry_price,
                        "exit": exit_price,
                        "return_pct": ret * 100,
                        "capital": capital,
                    }
                )

    metrics = calculate_metrics(trades, initial_capital, capital)
    metrics["ten_am_dump_trades"] = len([t for t in trades if t["signal"] == "10am_dump"])
    metrics["mean_reversion_trades"] = len([t for t in trades if t["signal"] == "mean_reversion"])
    metrics["skipped_by_btc_filter"] = len(skipped)
    return metrics


def calculate_metrics(
    trades: List[Dict], initial_capital: float, final_capital: float
) -> Dict[str, Any]:
    """Calculate backtest metrics."""
    total_return = (final_capital - initial_capital) / initial_capital * 100

    if not trades:
        return {
            "initial_capital": initial_capital,
            "final_capital": final_capital,
            "total_return_pct": total_return,
            "total_trades": 0,
            "win_rate": 0,
            "avg_return": 0,
            "sharpe_ratio": 0,
            "max_drawdown_pct": 0,
            "trades": [],
        }

    returns = [t["return_pct"] / 100 for t in trades]
    win_rate = sum(1 for r in returns if r > 0) / len(returns) * 100
    avg_return = np.mean(returns) * 100
    sharpe = (
        (np.mean(returns) / np.std(returns)) * np.sqrt(len(returns)) if np.std(returns) > 0 else 0
    )

    # Max drawdown
    peak = initial_capital
    max_dd = 0
    for t in trades:
        if t["capital"] > peak:
            peak = t["capital"]
        dd = (peak - t["capital"]) / peak
        max_dd = max(max_dd, dd)

    return {
        "initial_capital": initial_capital,
        "final_capital": final_capital,
        "total_return_pct": total_return,
        "total_trades": len(trades),
        "win_rate": win_rate,
        "avg_return": avg_return,
        "sharpe_ratio": sharpe,
        "max_drawdown_pct": max_dd * 100,
        "trades": trades,
    }


def main():
    print("=" * 70)
    print("BACKTEST: 10 AM Dump Strategy Comparison")
    print("=" * 70)

    # Initialize Alpaca
    alpaca = AlpacaProvider(
        api_key=os.environ.get("ALPACA_API_KEY"),
        secret_key=os.environ.get("ALPACA_SECRET_KEY"),
    )

    if not alpaca.is_available():
        print("ERROR: Alpaca API not available")
        return

    # Date range: last 3 months
    end_date = date.today()
    start_date = end_date - timedelta(days=90)

    print(f"\nDate Range: {start_date} to {end_date}")
    print("\nFetching data...")

    # Fetch data
    print("  - IBIT daily bars...")
    ibit_daily = get_daily_data(alpaca, "IBIT", start_date, end_date)
    print(f"    Got {len(ibit_daily)} days")

    print("  - BITU daily bars...")
    bitu_daily = get_daily_data(alpaca, "BITU", start_date, end_date)
    print(f"    Got {len(bitu_daily)} days")

    print("  - SBIT intraday bars (this may take a moment)...")
    sbit_intraday = get_intraday_data(alpaca, "SBIT", start_date, end_date)
    print(f"    Got {len(sbit_intraday)} bars")

    print("  - BTC overnight data...")
    btc_overnight = get_btc_overnight(alpaca, start_date, end_date)
    print(f"    Got {len(btc_overnight)} days")

    if ibit_daily.empty or bitu_daily.empty or sbit_intraday.empty:
        print("\nERROR: Failed to fetch required data")
        return

    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)

    # Test 1: 10 AM Dump ONLY
    print("\n--- 10 AM Dump Strategy ONLY ---")
    dump_only = backtest_10am_dump_only(sbit_intraday)
    print(f"Total Return:    {dump_only['total_return_pct']:+.2f}%")
    print(f"Total Trades:    {dump_only['total_trades']}")
    print(f"Win Rate:        {dump_only['win_rate']:.1f}%")
    print(f"Avg Trade:       {dump_only['avg_return']:+.2f}%")
    print(f"Sharpe Ratio:    {dump_only['sharpe_ratio']:.2f}")
    print(f"Max Drawdown:    {dump_only['max_drawdown_pct']:.2f}%")

    # Test 2: Mean Reversion ONLY (with BTC filter)
    print("\n--- Mean Reversion Strategy ONLY (with BTC filter) ---")
    mr_only = backtest_mean_reversion(ibit_daily, bitu_daily, btc_overnight)
    print(f"Total Return:    {mr_only['total_return_pct']:+.2f}%")
    print(f"Total Trades:    {mr_only['total_trades']}")
    print(f"Win Rate:        {mr_only['win_rate']:.1f}%")
    print(f"Avg Trade:       {mr_only['avg_return']:+.2f}%")
    print(f"Sharpe Ratio:    {mr_only['sharpe_ratio']:.2f}")
    print(f"Max Drawdown:    {mr_only['max_drawdown_pct']:.2f}%")
    print(f"Skipped (BTC):   {mr_only['skipped_by_btc_filter']}")

    # Test 3: Combined WITH 10 AM Dump
    print("\n--- Combined Strategy WITH 10 AM Dump ---")
    with_dump = backtest_combined(
        ibit_daily, bitu_daily, sbit_intraday, btc_overnight, include_10am_dump=True
    )
    print(f"Total Return:    {with_dump['total_return_pct']:+.2f}%")
    print(f"Total Trades:    {with_dump['total_trades']}")
    print(f"  - 10 AM Dump:  {with_dump['ten_am_dump_trades']}")
    print(f"  - Mean Rev:    {with_dump['mean_reversion_trades']}")
    print(f"Win Rate:        {with_dump['win_rate']:.1f}%")
    print(f"Avg Trade:       {with_dump['avg_return']:+.2f}%")
    print(f"Sharpe Ratio:    {with_dump['sharpe_ratio']:.2f}")
    print(f"Max Drawdown:    {with_dump['max_drawdown_pct']:.2f}%")

    # Test 4: Combined WITHOUT 10 AM Dump
    print("\n--- Combined Strategy WITHOUT 10 AM Dump ---")
    without_dump = backtest_combined(
        ibit_daily, bitu_daily, sbit_intraday, btc_overnight, include_10am_dump=False
    )
    print(f"Total Return:    {without_dump['total_return_pct']:+.2f}%")
    print(f"Total Trades:    {without_dump['total_trades']}")
    print(f"  - Mean Rev:    {without_dump['mean_reversion_trades']}")
    print(f"Win Rate:        {without_dump['win_rate']:.1f}%")
    print(f"Avg Trade:       {without_dump['avg_return']:+.2f}%")
    print(f"Sharpe Ratio:    {without_dump['sharpe_ratio']:.2f}")
    print(f"Max Drawdown:    {without_dump['max_drawdown_pct']:.2f}%")

    # Test 5: Combined with MEAN REVERSION Priority
    print("\n--- Combined Strategy with MR PRIORITY (opposite) ---")
    mr_priority = backtest_combined_mr_priority(
        ibit_daily, bitu_daily, sbit_intraday, btc_overnight
    )
    print(f"Total Return:    {mr_priority['total_return_pct']:+.2f}%")
    print(f"Total Trades:    {mr_priority['total_trades']}")
    print(f"  - Mean Rev:    {mr_priority['mean_reversion_trades']}")
    print(f"  - 10 AM Dump:  {mr_priority['ten_am_dump_trades']}")
    print(f"Win Rate:        {mr_priority['win_rate']:.1f}%")
    print(f"Avg Trade:       {mr_priority['avg_return']:+.2f}%")
    print(f"Sharpe Ratio:    {mr_priority['sharpe_ratio']:.2f}")
    print(f"Max Drawdown:    {mr_priority['max_drawdown_pct']:.2f}%")

    # Summary comparison
    print("\n" + "=" * 70)
    print("SUMMARY COMPARISON")
    print("=" * 70)
    print(f"\n{'Strategy':<35} {'Return':>10} {'Trades':>8} {'Win Rate':>10} {'Sharpe':>8}")
    print("-" * 75)
    print(
        f"{'10 AM Dump Only':<35} {dump_only['total_return_pct']:>+9.2f}% {dump_only['total_trades']:>8} {dump_only['win_rate']:>9.1f}% {dump_only['sharpe_ratio']:>8.2f}"
    )
    print(
        f"{'Mean Reversion Only':<35} {mr_only['total_return_pct']:>+9.2f}% {mr_only['total_trades']:>8} {mr_only['win_rate']:>9.1f}% {mr_only['sharpe_ratio']:>8.2f}"
    )
    print("-" * 75)
    print(
        f"{'Combined: 10AM Dump Priority':<35} {with_dump['total_return_pct']:>+9.2f}% {with_dump['total_trades']:>8} {with_dump['win_rate']:>9.1f}% {with_dump['sharpe_ratio']:>8.2f}"
    )
    print(
        f"{'Combined: MR Priority':<35} {mr_priority['total_return_pct']:>+9.2f}% {mr_priority['total_trades']:>8} {mr_priority['win_rate']:>9.1f}% {mr_priority['sharpe_ratio']:>8.2f}"
    )
    print(
        f"{'Combined: No 10AM Dump':<35} {without_dump['total_return_pct']:>+9.2f}% {without_dump['total_trades']:>8} {without_dump['win_rate']:>9.1f}% {without_dump['sharpe_ratio']:>8.2f}"
    )

    print("\n" + "=" * 75)
    print("KEY COMPARISON:")
    print("-" * 75)

    # Compare the two priority modes
    dump_priority_ret = with_dump["total_return_pct"]
    mr_priority_ret = mr_priority["total_return_pct"]

    print(
        f"  10 AM Dump Priority: {dump_priority_ret:+.2f}% ({with_dump['ten_am_dump_trades']} dump / {with_dump['mean_reversion_trades']} MR)"
    )
    print(
        f"  Mean Reversion Priority: {mr_priority_ret:+.2f}% ({mr_priority['ten_am_dump_trades']} dump / {mr_priority['mean_reversion_trades']} MR)"
    )

    diff = dump_priority_ret - mr_priority_ret
    print()
    if diff > 0:
        print(f"VERDICT: 10 AM Dump Priority is BETTER by {diff:+.2f}%")
    elif diff < 0:
        print(f"VERDICT: Mean Reversion Priority is BETTER by {abs(diff):+.2f}%")
    else:
        print("VERDICT: Both strategies perform equally")
    print("=" * 75)


if __name__ == "__main__":
    main()
