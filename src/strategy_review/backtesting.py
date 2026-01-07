"""
Backtesting methods for strategy review.

Provides various backtest strategies including mean reversion, 10 AM dump,
and combined strategy tests.
"""

import logging
import os
import statistics
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import pandas as pd

from ..error_alerting import AlertSeverity, alert_anomaly, alert_error
from .models import BacktestResult

if TYPE_CHECKING:
    from .reviewer import StrategyReviewer

logger = logging.getLogger(__name__)


class BacktestMixin:
    """
    Mixin providing backtesting methods for StrategyReviewer.

    Requires from base class:
    - current_params: Dict[str, Any]
    """

    def _run_backtest(
        self: "StrategyReviewer",
        data: List[Dict],
        mr_threshold: float = -2.0,
        reversal_threshold: float = -2.0,
        name: str = "Test",
    ) -> BacktestResult:
        """Run a simple backtest with given parameters."""
        if not data or len(data) < 2:
            return BacktestResult(
                name=name,
                total_return_pct=0,
                total_trades=0,
                winning_trades=0,
                win_rate=0,
                avg_return_pct=0,
                max_drawdown_pct=0,
                sharpe_ratio=0,
            )

        trades = []
        capital = 10000
        peak = capital

        for i in range(1, len(data)):
            day = data[i]
            prev_day = data[i - 1]

            # Skip weekends
            if day["weekday"] >= 5:
                continue

            # Calculate previous day return
            prev_return = ((prev_day["close"] - prev_day["open"]) / prev_day["open"]) * 100

            # Check for mean reversion signal
            if prev_return <= mr_threshold:
                # Simulate BITU trade
                day_return = ((day["close"] - day["open"]) / day["open"]) * 100
                lev_return = day_return * 2  # 2x leverage

                # Check for reversal
                max_drawdown = ((day["low"] - day["open"]) / day["open"]) * 100 * 2
                if max_drawdown <= reversal_threshold:
                    # Reversal triggered - estimate partial recovery
                    first_leg = reversal_threshold
                    remaining = lev_return - reversal_threshold
                    trade_return = first_leg + (-remaining)  # Flip to inverse
                else:
                    trade_return = lev_return

                pnl = capital * (trade_return / 100)
                capital += pnl

                if capital > peak:
                    peak = capital

                trades.append(trade_return)

        if not trades:
            return BacktestResult(
                name=name,
                total_return_pct=0,
                total_trades=0,
                winning_trades=0,
                win_rate=0,
                avg_return_pct=0,
                max_drawdown_pct=0,
                sharpe_ratio=0,
            )

        total_return = ((capital - 10000) / 10000) * 100
        winning = [t for t in trades if t > 0]
        win_rate = (len(winning) / len(trades)) * 100 if trades else 0
        avg_return = statistics.mean(trades) if trades else 0
        std_return = statistics.stdev(trades) if len(trades) > 1 else 0
        sharpe = (avg_return / std_return * (252**0.5)) if std_return > 0 else 0
        max_dd = ((peak - capital) / peak) * 100 if peak > 0 else 0

        return BacktestResult(
            name=name,
            total_return_pct=total_return,
            total_trades=len(trades),
            winning_trades=len(winning),
            win_rate=win_rate,
            avg_return_pct=avg_return,
            max_drawdown_pct=max_dd,
            sharpe_ratio=sharpe,
        )

    def _format_backtest_result(self: "StrategyReviewer", result: BacktestResult) -> str:
        """Format backtest result for prompt."""
        return (
            f"**{result.name}**\n"
            f"  Return: {result.total_return_pct:+.2f}%\n"
            f"  Trades: {result.total_trades} ({result.winning_trades} wins)\n"
            f"  Win Rate: {result.win_rate:.1f}%\n"
            f"  Avg Return: {result.avg_return_pct:+.2f}%\n"
            f"  Max Drawdown: {result.max_drawdown_pct:.2f}%\n"
            f"  Sharpe: {result.sharpe_ratio:.2f}"
        )

    def _run_strategy_combination_tests(
        self: "StrategyReviewer", ibit_data: List[Dict]
    ) -> Dict[str, Dict]:
        """
        Run comprehensive backtests for strategy enable/disable combinations.

        Tests:
        1. 10 AM Dump Only
        2. Mean Reversion Only
        3. Both with 10 AM Dump Priority (current default)
        4. Both with Mean Reversion Priority
        5. Neither (baseline)

        Returns dict with results for each configuration.
        """
        from ..data_providers import AlpacaProvider

        # We need intraday data for 10 AM dump testing
        alpaca = AlpacaProvider(
            api_key=os.environ.get("ALPACA_API_KEY"),
            secret_key=os.environ.get("ALPACA_SECRET_KEY"),
        )

        if not alpaca.is_available():
            logger.warning("Alpaca not available for strategy combination tests")
            return {}

        # Determine date range from IBIT data
        if not ibit_data:
            return {}

        dates = [d["date"] for d in ibit_data if "date" in d]
        if not dates:
            return {}

        # Convert string dates to date objects if needed
        if isinstance(dates[0], str):
            from datetime import datetime

            dates = [datetime.strptime(d, "%Y-%m-%d").date() for d in dates]

        start_date = min(dates)
        end_date = max(dates)

        results = {}

        # Fetch additional data needed for comprehensive testing
        try:
            # Fetch SBIT intraday for 10 AM dump
            sbit_bars = alpaca.get_historical_bars(
                "SBIT",
                start_date.isoformat(),
                (end_date + timedelta(days=1)).isoformat(),
                "1Min",
            )

            # Fetch BITU daily for mean reversion
            bitu_bars = alpaca.get_historical_bars(
                "BITU",
                start_date.isoformat(),
                (end_date + timedelta(days=1)).isoformat(),
                "1Day",
            )

            # Fetch BTC for overnight filter
            btc_bars = alpaca.get_crypto_bars(
                "BTC/USD",
                start_date.isoformat(),
                (end_date + timedelta(days=1)).isoformat(),
                "1Day",
            )

            if not sbit_bars or not bitu_bars:
                logger.warning("Missing data for strategy tests")
                return {}

            # Run each test configuration
            results["10am_dump_only"] = self._backtest_10am_dump(sbit_bars)
            results["mean_reversion_only"] = self._backtest_mean_reversion(
                ibit_data, bitu_bars, btc_bars
            )
            results["combined_10am_priority"] = self._backtest_combined(
                ibit_data, bitu_bars, sbit_bars, btc_bars, priority="ten_am_first"
            )
            results["combined_mr_priority"] = self._backtest_combined(
                ibit_data, bitu_bars, sbit_bars, btc_bars, priority="mean_reversion_first"
            )

        except Exception as e:
            logger.error(f"Strategy combination tests failed: {e}")
            alert_error(
                AlertSeverity.WARNING,
                f"Strategy combination tests failed: {e}",
                {"start_date": str(start_date), "end_date": str(end_date)},
                category="strategy_tests",
            )
            return {}

        return results

    def _backtest_10am_dump(self: "StrategyReviewer", sbit_intraday: List[Dict]) -> Dict[str, Any]:
        """Backtest 10 AM dump strategy only."""
        if not sbit_intraday:
            return {"return": 0, "trades": 0, "win_rate": 0}

        df = pd.DataFrame(sbit_intraday)
        df["timestamp"] = pd.to_datetime(df["t"])
        df["timestamp_et"] = df["timestamp"].dt.tz_convert("America/New_York")
        df["date"] = df["timestamp_et"].dt.date
        df["time"] = df["timestamp_et"].dt.strftime("%H:%M")
        df = df.rename(columns={"o": "open", "c": "close"})

        capital = 10000.0
        trades = []
        slippage = 0.02

        for trade_date, day_data in df.groupby("date"):
            # Find bars near 9:35 and 10:30
            entry_bar = self._find_nearest_bar(day_data, "09:35", 5)
            exit_bar = self._find_nearest_bar(day_data, "10:30", 5)

            if entry_bar is None or exit_bar is None:
                continue

            entry_price = entry_bar["close"] * (1 + slippage / 100)
            exit_price = exit_bar["close"] * (1 - slippage / 100)

            ret = (exit_price - entry_price) / entry_price
            capital *= 1 + ret
            trades.append(ret * 100)

        total_return = (capital - 10000) / 10000 * 100
        win_rate = sum(1 for t in trades if t > 0) / len(trades) * 100 if trades else 0

        return {
            "return": total_return,
            "trades": len(trades),
            "win_rate": win_rate,
            "name": "10 AM Dump Only",
        }

    def _backtest_mean_reversion(
        self: "StrategyReviewer",
        ibit_data: List[Dict],
        bitu_bars: List[Dict],
        btc_bars: List[Dict],
    ) -> Dict[str, Any]:
        """Backtest mean reversion strategy only."""
        if not ibit_data or not bitu_bars:
            return {"return": 0, "trades": 0, "win_rate": 0, "name": "Mean Reversion Only"}

        # Build IBIT daily returns
        ibit_df = pd.DataFrame(ibit_data)
        ibit_df["daily_return"] = (ibit_df["close"] - ibit_df["open"]) / ibit_df["open"] * 100
        ibit_df["prev_return"] = ibit_df["daily_return"].shift(1)

        # Build BITU lookup - use STRING dates for consistent comparison
        bitu_df = pd.DataFrame(bitu_bars)
        bitu_df["date"] = pd.to_datetime(bitu_df["t"]).dt.strftime("%Y-%m-%d")
        bitu_df = bitu_df.rename(columns={"o": "open", "c": "close"})
        bitu_by_date = {row["date"]: row for _, row in bitu_df.iterrows()}

        # Build BTC overnight lookup - use STRING dates
        btc_overnight = {}
        if btc_bars:
            btc_df = pd.DataFrame(btc_bars)
            btc_df["date"] = pd.to_datetime(btc_df["t"]).dt.strftime("%Y-%m-%d")
            btc_df = btc_df.rename(columns={"o": "open", "c": "close"}).sort_values("date")
            for i in range(1, len(btc_df)):
                prev_close = btc_df.iloc[i - 1]["close"]
                today_open = btc_df.iloc[i]["open"]
                btc_overnight[btc_df.iloc[i]["date"]] = (
                    (today_open - prev_close) / prev_close * 100 if prev_close > 0 else 0
                )

        capital = 10000.0
        trades = []
        slippage = 0.02
        threshold = self.current_params["mr_threshold"]

        for i in range(1, len(ibit_df)):
            row = ibit_df.iloc[i]
            prev_ret = row["prev_return"]
            trade_date = row["date"]  # This is already a string from _fetch_market_data

            if pd.isna(prev_ret) or prev_ret >= threshold:
                continue

            # BTC overnight filter
            if trade_date in btc_overnight and btc_overnight[trade_date] <= 0:
                continue

            if trade_date not in bitu_by_date:
                continue

            bitu_row = bitu_by_date[trade_date]
            entry_price = bitu_row["open"] * (1 + slippage / 100)
            exit_price = bitu_row["close"] * (1 - slippage / 100)

            ret = (exit_price - entry_price) / entry_price
            capital *= 1 + ret
            trades.append(ret * 100)

        total_return = (capital - 10000) / 10000 * 100
        win_rate = sum(1 for t in trades if t > 0) / len(trades) * 100 if trades else 0

        # Alert if 0 trades when we had dip days (data quality check)
        if len(trades) == 0 and len(ibit_data) > 30:
            alert_anomaly(
                "mean_reversion_trades",
                0,
                ">0 if dip days exist",
                {"ibit_days": len(ibit_data), "bitu_days": len(bitu_bars)},
            )

        return {
            "return": total_return,
            "trades": len(trades),
            "win_rate": win_rate,
            "name": "Mean Reversion Only",
        }

    def _backtest_combined(
        self: "StrategyReviewer",
        ibit_data: List[Dict],
        bitu_bars: List[Dict],
        sbit_bars: List[Dict],
        btc_bars: List[Dict],
        priority: str = "ten_am_first",
    ) -> Dict[str, Any]:
        """Backtest combined strategy with specified priority."""
        if not ibit_data or not bitu_bars or not sbit_bars:
            return {"return": 0, "trades": 0, "win_rate": 0}

        # Prepare IBIT data
        ibit_df = pd.DataFrame(ibit_data)
        ibit_df["daily_return"] = (ibit_df["close"] - ibit_df["open"]) / ibit_df["open"] * 100
        ibit_df["prev_return"] = ibit_df["daily_return"].shift(1)

        # Prepare BITU lookup - use STRING dates for consistent comparison
        bitu_df = pd.DataFrame(bitu_bars)
        bitu_df["date"] = pd.to_datetime(bitu_df["t"]).dt.strftime("%Y-%m-%d")
        bitu_df = bitu_df.rename(columns={"o": "open", "c": "close"})
        bitu_by_date = {row["date"]: row for _, row in bitu_df.iterrows()}

        # Prepare SBIT intraday for 10 AM dump - use STRING dates
        sbit_df = pd.DataFrame(sbit_bars)
        sbit_df["timestamp"] = pd.to_datetime(sbit_df["t"])
        sbit_df["timestamp_et"] = sbit_df["timestamp"].dt.tz_convert("America/New_York")
        sbit_df["date"] = sbit_df["timestamp_et"].dt.strftime("%Y-%m-%d")
        sbit_df = sbit_df.rename(columns={"c": "close"})

        sbit_10am = {}
        for trade_date, day_data in sbit_df.groupby("date"):
            entry_bar = self._find_nearest_bar(day_data, "09:35", 5)
            exit_bar = self._find_nearest_bar(day_data, "10:30", 5)
            if entry_bar is not None and exit_bar is not None:
                sbit_10am[trade_date] = {"entry": entry_bar["close"], "exit": exit_bar["close"]}

        # BTC overnight filter - use STRING dates
        btc_overnight = {}
        if btc_bars:
            btc_df = pd.DataFrame(btc_bars)
            btc_df["date"] = pd.to_datetime(btc_df["t"]).dt.strftime("%Y-%m-%d")
            btc_df = btc_df.rename(columns={"o": "open", "c": "close"}).sort_values("date")
            for i in range(1, len(btc_df)):
                prev_close = btc_df.iloc[i - 1]["close"]
                today_open = btc_df.iloc[i]["open"]
                btc_overnight[btc_df.iloc[i]["date"]] = (
                    (today_open - prev_close) / prev_close * 100 if prev_close > 0 else 0
                )

        capital = 10000.0
        trades = []
        ten_am_trades = 0
        mr_trades = 0
        slippage = 0.02
        threshold = self.current_params["mr_threshold"]

        for i in range(1, len(ibit_df)):
            row = ibit_df.iloc[i]
            prev_ret = row["prev_return"]
            trade_date = row["date"]

            # Check if mean reversion conditions are met
            is_mr_day = (
                not pd.isna(prev_ret)
                and prev_ret < threshold
                and (trade_date not in btc_overnight or btc_overnight[trade_date] > 0)
            )

            # Execute based on priority
            if priority == "ten_am_first":
                # 10 AM dump takes priority - runs every day
                if trade_date in sbit_10am:
                    data = sbit_10am[trade_date]
                    entry_price = data["entry"] * (1 + slippage / 100)
                    exit_price = data["exit"] * (1 - slippage / 100)
                    ret = (exit_price - entry_price) / entry_price
                    capital *= 1 + ret
                    trades.append(ret * 100)
                    ten_am_trades += 1
                    continue  # Skip MR on this day

                # Mean reversion (only if 10 AM dump didn't fire)
                if is_mr_day and trade_date in bitu_by_date:
                    bitu_row = bitu_by_date[trade_date]
                    entry_price = bitu_row["open"] * (1 + slippage / 100)
                    exit_price = bitu_row["close"] * (1 - slippage / 100)
                    ret = (exit_price - entry_price) / entry_price
                    capital *= 1 + ret
                    trades.append(ret * 100)
                    mr_trades += 1

            else:  # mean_reversion_first
                # Mean reversion takes priority
                if is_mr_day and trade_date in bitu_by_date:
                    bitu_row = bitu_by_date[trade_date]
                    entry_price = bitu_row["open"] * (1 + slippage / 100)
                    exit_price = bitu_row["close"] * (1 - slippage / 100)
                    ret = (exit_price - entry_price) / entry_price
                    capital *= 1 + ret
                    trades.append(ret * 100)
                    mr_trades += 1
                    continue  # Skip 10 AM dump on MR days

                # 10 AM dump (only if MR didn't fire)
                if trade_date in sbit_10am:
                    data = sbit_10am[trade_date]
                    entry_price = data["entry"] * (1 + slippage / 100)
                    exit_price = data["exit"] * (1 - slippage / 100)
                    ret = (exit_price - entry_price) / entry_price
                    capital *= 1 + ret
                    trades.append(ret * 100)
                    ten_am_trades += 1

        total_return = (capital - 10000) / 10000 * 100
        win_rate = sum(1 for t in trades if t > 0) / len(trades) * 100 if trades else 0

        priority_name = "10AM Priority" if priority == "ten_am_first" else "MR Priority"
        return {
            "return": total_return,
            "trades": len(trades),
            "win_rate": win_rate,
            "ten_am_trades": ten_am_trades,
            "mr_trades": mr_trades,
            "name": f"Combined ({priority_name})",
        }

    def _find_nearest_bar(
        self: "StrategyReviewer", day_data, target_time: str, window_minutes: int = 5
    ) -> Optional[pd.Series]:
        """Find the bar nearest to target_time within a window."""
        target_hour, target_min = map(int, target_time.split(":"))
        target_minutes = target_hour * 60 + target_min

        day_data = day_data.copy()
        day_data["minutes"] = (
            day_data["timestamp_et"].dt.hour * 60 + day_data["timestamp_et"].dt.minute
        )
        day_data["diff"] = abs(day_data["minutes"] - target_minutes)

        nearby = day_data[day_data["diff"] <= window_minutes]
        if nearby.empty:
            return None

        return nearby.loc[nearby["diff"].idxmin()]

    def _format_strategy_tests(self: "StrategyReviewer", results: Dict[str, Dict]) -> str:
        """Format strategy combination test results for the prompt."""
        if not results:
            return "Strategy combination tests unavailable."

        lines = ["### Strategy Configuration Tests", ""]
        lines.append("| Configuration | Return | Trades | Win Rate | Details |")
        lines.append("|---------------|--------|--------|----------|---------|")

        for key, data in results.items():
            name = data.get("name", key)
            ret = data.get("return", 0)
            trades = data.get("trades", 0)
            win_rate = data.get("win_rate", 0)

            details = ""
            if "ten_am_trades" in data:
                details = f"{data['ten_am_trades']} 10AM / {data['mr_trades']} MR"

            lines.append(f"| {name} | {ret:+.2f}% | {trades} | {win_rate:.1f}% | {details} |")

        lines.append("")
        lines.append("**Interpretation:**")
        lines.append("- Compare returns to determine if strategies should be enabled/disabled")
        lines.append("- Compare priority modes to see which signal should take precedence")

        return "\n".join(lines)
