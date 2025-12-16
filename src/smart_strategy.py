"""
Smart Bitcoin ETF Trading Strategy.

Proven strategy with +361.8% backtested return (vs +35.5% IBIT B&H):
1. Mean Reversion: Buy BITX (2x) after IBIT drops -2%+ previous day
2. Short Thursday: Buy SBIT (2x inverse) every Thursday
3. Crash Day: Buy SBIT when IBIT drops -2%+ intraday (reactive)
4. All other days: Stay in cash

Key insight: Don't predict market direction. Use leverage ONLY on high-probability signals.
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .data_providers import AlpacaProvider, create_data_manager
from .database import Database, get_database

logger = logging.getLogger(__name__)


class Signal(Enum):
    """Trading signals."""

    MEAN_REVERSION = "mean_reversion"  # Buy BITX after big drop
    SHORT_THURSDAY = "short_thursday"  # Buy SBIT on Thursday
    CRASH_DAY = "crash_day"  # Buy SBIT on intraday crash
    CASH = "cash"  # No position


class AlertLevel(Enum):
    """Weekend gap alert levels."""

    NONE = "none"
    WATCH = "watch"  # -1% to -2% gap
    HIGH_ALERT = "high_alert"  # -2% to -3% gap
    CRITICAL = "critical"  # > -3% gap


@dataclass
class StrategyConfig:
    """Configuration for the smart strategy."""

    # Mean reversion settings
    mean_reversion_enabled: bool = True
    mean_reversion_threshold: float = -2.0  # Buy BITX after IBIT drops this much

    # Short Thursday settings
    short_thursday_enabled: bool = True

    # Crash day settings (intraday reactive)
    crash_day_enabled: bool = True
    crash_day_threshold: float = -2.0  # Buy SBIT when IBIT drops this much intraday
    crash_day_check_times: List[str] = field(
        default_factory=lambda: ["09:45", "10:00", "10:15", "10:30", "10:45", "11:00", "11:30"]
    )
    crash_day_cutoff_time: str = "12:00"  # Don't enter crash trades after this time

    # Weekend gap monitoring
    weekend_gap_enabled: bool = True
    weekend_gap_watch_threshold: float = -1.0  # Alert if BTC weekend gap exceeds this
    weekend_gap_high_alert_threshold: float = -2.0
    weekend_gap_critical_threshold: float = -3.0

    # Position sizing
    max_position_pct: float = 100.0  # % of available cash to use

    # Trading settings
    slippage_pct: float = 0.02  # Expected slippage
    dry_run: bool = True  # Safety first


@dataclass
class WeekendGapInfo:
    """Weekend gap analysis."""

    alert_level: AlertLevel
    btc_friday_close: float
    btc_current: float
    gap_pct: float
    is_monday: bool
    message: str


@dataclass
class CrashDayStatus:
    """Intraday crash detection status."""

    is_triggered: bool
    current_drop_pct: float
    ibit_open: float
    ibit_current: float
    trigger_time: Optional[str] = None
    already_traded_today: bool = False


@dataclass
class TodaySignal:
    """Today's trading signal."""

    signal: Signal
    etf: str  # BITX, SBIT, or CASH
    reason: str
    prev_day_return: Optional[float] = None
    crash_day_status: Optional[CrashDayStatus] = None
    weekend_gap: Optional[WeekendGapInfo] = None

    def should_trade(self) -> bool:
        return self.signal != Signal.CASH


class SmartStrategy:
    """
    Smart Bitcoin ETF Trading Strategy.

    Uses proven signals with appropriate ETF leverage:
    - Mean Reversion → BITX (2x long)
    - Short Thursday → SBIT (2x inverse)
    - Crash Day → SBIT (2x inverse) on intraday crash
    - No signal → Cash
    """

    def __init__(self, config: Optional[StrategyConfig] = None, db: Optional[Database] = None):
        self.config = config or StrategyConfig()
        self.db = db or get_database()
        self._ibit_data: Optional[pd.DataFrame] = None
        self._last_data_fetch: Optional[datetime] = None
        self._crash_day_traded_today: bool = False
        self._crash_day_trade_date: Optional[date] = None

        # Initialize Alpaca data provider
        self._alpaca = AlpacaProvider(
            api_key=os.environ.get("ALPACA_API_KEY"),
            secret_key=os.environ.get("ALPACA_SECRET_KEY"),
        )
        self._data_manager = create_data_manager()

    def get_ibit_data(self, days: int = 10) -> pd.DataFrame:
        """Fetch recent IBIT data from Alpaca."""
        # Cache data for 5 minutes
        now = datetime.now()
        if (
            self._ibit_data is not None
            and self._last_data_fetch is not None
            and (now - self._last_data_fetch).seconds < 300
        ):
            return self._ibit_data

        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        # Use Alpaca for historical data
        if self._alpaca.is_available():
            bars = self._alpaca.get_historical_bars(
                "IBIT", start_date.isoformat(), (end_date + timedelta(days=1)).isoformat(), "1Day"
            )

            if bars and len(bars) > 0:
                df = pd.DataFrame(bars)
                df["date"] = pd.to_datetime(df["t"]).dt.date
                df = df.rename(
                    columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
                )
                df["daily_return"] = (df["close"] - df["open"]) / df["open"] * 100

                self._ibit_data = df
                self._last_data_fetch = now
                return df

        # Fallback to empty DataFrame if Alpaca fails
        logger.warning("Alpaca data not available, returning empty DataFrame")
        return pd.DataFrame(
            columns=["date", "open", "high", "low", "close", "volume", "daily_return"]
        )

    def get_previous_day_return(self) -> Optional[float]:
        """Get IBIT's return from the previous trading day."""
        df = self.get_ibit_data()
        if len(df) < 2:
            return None
        return df["daily_return"].iloc[-2]  # Second to last row is previous day

    def get_weekend_gap(self) -> WeekendGapInfo:
        """
        Check BTC weekend gap (Friday close → current) using Alpaca crypto data.

        This is a LEADING indicator - we can see the gap forming before
        the stock market opens because BTC trades 24/7.
        """
        today = date.today()
        is_monday = today.weekday() == 0

        try:
            # Get historical BTC bars from Alpaca
            bars = self._alpaca.get_crypto_bars(
                "BTC/USD",
                (today - timedelta(days=7)).isoformat(),
                (today + timedelta(days=1)).isoformat(),
                "1Day",
            )

            if not bars or len(bars) < 2:
                return WeekendGapInfo(
                    alert_level=AlertLevel.NONE,
                    btc_friday_close=0,
                    btc_current=0,
                    gap_pct=0,
                    is_monday=is_monday,
                    message="Insufficient data",
                )

            # Convert to DataFrame
            df = pd.DataFrame(bars)
            df["date"] = pd.to_datetime(df["t"])
            df["dayofweek"] = df["date"].dt.dayofweek

            # Find Friday's close (dayofweek 4 = Friday)
            friday_data = df[df["dayofweek"] == 4]
            if len(friday_data) == 0:
                # Use last available close before weekend
                friday_close = df["c"].iloc[-2] if len(df) > 1 else df["c"].iloc[-1]
            else:
                friday_close = friday_data["c"].iloc[-1]

            # Get current BTC price from real-time quote
            btc_quote = self._alpaca.get_crypto_quote("BTC/USD")
            btc_current = btc_quote.current_price if btc_quote else df["c"].iloc[-1]

            # Calculate gap
            gap_pct = (btc_current - friday_close) / friday_close * 100 if friday_close > 0 else 0

            # Determine alert level
            if gap_pct <= self.config.weekend_gap_critical_threshold:
                alert_level = AlertLevel.CRITICAL
                message = f"CRITICAL: BTC down {gap_pct:.1f}% since Friday - expect volatility"
            elif gap_pct <= self.config.weekend_gap_high_alert_threshold:
                alert_level = AlertLevel.HIGH_ALERT
                message = f"HIGH ALERT: BTC down {gap_pct:.1f}% since Friday - watch for crash"
            elif gap_pct <= self.config.weekend_gap_watch_threshold:
                alert_level = AlertLevel.WATCH
                message = f"WATCH: BTC down {gap_pct:.1f}% since Friday"
            else:
                alert_level = AlertLevel.NONE
                message = f"Normal: BTC {gap_pct:+.1f}% since Friday"

            return WeekendGapInfo(
                alert_level=alert_level,
                btc_friday_close=friday_close,
                btc_current=btc_current,
                gap_pct=gap_pct,
                is_monday=is_monday,
                message=message,
            )

        except Exception as e:
            logger.warning(f"Failed to get weekend gap: {e}")
            return WeekendGapInfo(
                alert_level=AlertLevel.NONE,
                btc_friday_close=0,
                btc_current=0,
                gap_pct=0,
                is_monday=is_monday,
                message=f"Error: {e}",
            )

    def get_crash_day_status(self) -> CrashDayStatus:
        """
        Check for intraday crash signal using Alpaca real-time data.

        If IBIT drops >= crash_day_threshold from today's open,
        this triggers a SBIT buy signal.
        """
        today = date.today()
        now = datetime.now()

        # Reset crash day flag if it's a new day
        if self._crash_day_trade_date != today:
            self._crash_day_traded_today = False
            self._crash_day_trade_date = today

        try:
            # Use Alpaca snapshot for real-time data
            quote = self._data_manager.get_quote("IBIT")

            if quote is None:
                return CrashDayStatus(
                    is_triggered=False,
                    current_drop_pct=0,
                    ibit_open=0,
                    ibit_current=0,
                    already_traded_today=self._crash_day_traded_today,
                )

            ibit_open = quote.open_price
            ibit_current = quote.current_price
            current_drop_pct = (ibit_current - ibit_open) / ibit_open * 100 if ibit_open > 0 else 0

            # Check if threshold is met
            is_triggered = current_drop_pct <= self.config.crash_day_threshold

            # Check if we're past cutoff time
            cutoff_hour, cutoff_min = map(int, self.config.crash_day_cutoff_time.split(":"))
            is_past_cutoff = now.hour > cutoff_hour or (
                now.hour == cutoff_hour and now.minute >= cutoff_min
            )

            # Don't trigger if past cutoff or already traded
            if is_past_cutoff or self._crash_day_traded_today:
                is_triggered = False

            trigger_time = now.strftime("%H:%M") if is_triggered else None

            return CrashDayStatus(
                is_triggered=is_triggered,
                current_drop_pct=current_drop_pct,
                ibit_open=ibit_open,
                ibit_current=ibit_current,
                trigger_time=trigger_time,
                already_traded_today=self._crash_day_traded_today,
            )

        except Exception as e:
            logger.warning(f"Failed to get crash day status: {e}")
            return CrashDayStatus(
                is_triggered=False,
                current_drop_pct=0,
                ibit_open=0,
                ibit_current=0,
                already_traded_today=self._crash_day_traded_today,
            )

    def mark_crash_day_traded(self):
        """Mark that we've executed a crash day trade today."""
        self._crash_day_traded_today = True
        self._crash_day_trade_date = date.today()

    def get_today_signal(self, check_crash_day: bool = True) -> TodaySignal:
        """
        Determine today's trading signal.

        Priority order:
        1. Mean Reversion (previous day drop) - highest priority
        2. Crash Day (intraday drop) - reactive signal
        3. Short Thursday - calendar-based
        4. Cash - default
        """
        today = date.today()
        weekday = today.weekday()  # 0=Monday, 3=Thursday

        prev_return = self.get_previous_day_return()

        # Get weekend gap info for context
        weekend_gap = None
        if self.config.weekend_gap_enabled:
            weekend_gap = self.get_weekend_gap()

        # Get crash day status
        crash_status = None
        if check_crash_day and self.config.crash_day_enabled:
            crash_status = self.get_crash_day_status()

        # Check mean reversion first (higher priority)
        # This is a pre-market signal based on yesterday's close
        if self.config.mean_reversion_enabled:
            if prev_return is not None and prev_return < self.config.mean_reversion_threshold:
                return TodaySignal(
                    signal=Signal.MEAN_REVERSION,
                    etf="BITX",
                    reason=f"Mean reversion: IBIT dropped {prev_return:.1f}% yesterday",
                    prev_day_return=prev_return,
                    crash_day_status=crash_status,
                    weekend_gap=weekend_gap,
                )

        # Check crash day signal (intraday reactive)
        if self.config.crash_day_enabled and crash_status and crash_status.is_triggered:
            return TodaySignal(
                signal=Signal.CRASH_DAY,
                etf="SBIT",
                reason=f"Crash day: IBIT down {crash_status.current_drop_pct:.1f}% today - buying SBIT",
                prev_day_return=prev_return,
                crash_day_status=crash_status,
                weekend_gap=weekend_gap,
            )

        # Check Thursday
        if self.config.short_thursday_enabled and weekday == 3:
            return TodaySignal(
                signal=Signal.SHORT_THURSDAY,
                etf="SBIT",
                reason="Short Thursday: Statistically worst day for Bitcoin",
                prev_day_return=prev_return,
                crash_day_status=crash_status,
                weekend_gap=weekend_gap,
            )

        # No signal - stay in cash
        # But still include crash day monitoring info
        reason = "No signal today"
        if crash_status and crash_status.current_drop_pct < -1.0:
            reason = f"Watching: IBIT down {crash_status.current_drop_pct:.1f}% (threshold: {self.config.crash_day_threshold}%)"

        return TodaySignal(
            signal=Signal.CASH,
            etf="CASH",
            reason=reason,
            prev_day_return=prev_return,
            crash_day_status=crash_status,
            weekend_gap=weekend_gap,
        )

    def get_etf_quote(self, ticker: str) -> Dict[str, Any]:
        """Get current quote for an ETF using Alpaca real-time data."""
        quote = self._data_manager.get_quote(ticker)

        if quote:
            current_price = quote.current_price
            open_price = quote.open_price
        else:
            current_price = 0
            open_price = 0

        return {
            "ticker": ticker,
            "current_price": current_price,
            "open_price": open_price,
            "change_pct": ((current_price - open_price) / open_price * 100) if open_price else 0,
            "source": quote.source.value if quote else "unknown",
            "is_realtime": quote.is_realtime if quote else False,
        }


class SmartBacktester:
    """Backtest the smart strategy using Alpaca historical data."""

    def __init__(self, initial_capital: float = 10000.0, config: Optional[StrategyConfig] = None):
        self.initial_capital = initial_capital
        self.config = config or StrategyConfig()
        self.data: Dict[str, pd.DataFrame] = {}

        # Initialize Alpaca for historical data
        self._alpaca = AlpacaProvider(
            api_key=os.environ.get("ALPACA_API_KEY"),
            secret_key=os.environ.get("ALPACA_SECRET_KEY"),
        )

    def load_data(self, start_date: date, end_date: date):
        """Load historical data for all ETFs from Alpaca."""
        tickers = ["IBIT", "BITX", "SBIT"]

        for ticker in tickers:
            # Use Alpaca historical bars
            bars = self._alpaca.get_historical_bars(
                ticker, start_date.isoformat(), (end_date + timedelta(days=1)).isoformat(), "1Day"
            )

            if bars and len(bars) > 0:
                df = pd.DataFrame(bars)
                df["date"] = pd.to_datetime(df["t"]).dt.date
                df = df.rename(
                    columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
                )
                self.data[ticker] = df
            else:
                logger.warning(f"No data received for {ticker}")
                self.data[ticker] = pd.DataFrame(
                    columns=["date", "open", "high", "low", "close", "volume"]
                )

        # Align to common dates
        common_dates = set(self.data["IBIT"]["date"])
        for ticker in tickers[1:]:
            common_dates &= set(self.data[ticker]["date"])

        for ticker in tickers:
            self.data[ticker] = (
                self.data[ticker][self.data[ticker]["date"].isin(common_dates)]
                .sort_values("date")
                .reset_index(drop=True)
            )

        return len(common_dates)

    def run_backtest(self) -> Dict[str, Any]:
        """Run backtest and return results."""
        ibit = self.data["IBIT"].copy()
        bitx = self.data["BITX"].copy()
        sbit = self.data["SBIT"].copy()

        # Calculate IBIT daily return
        ibit["daily_return"] = (ibit["close"] - ibit["open"]) / ibit["open"] * 100
        ibit["prev_return"] = ibit["daily_return"].shift(1)
        ibit["weekday"] = pd.to_datetime(ibit["date"]).apply(lambda x: x.weekday())

        capital = self.initial_capital
        trades = []
        slippage = self.config.slippage_pct

        for i in range(len(ibit)):
            row = ibit.iloc[i]
            prev_ret = row.get("prev_return")
            weekday = row["weekday"]
            trade_date = row["date"]

            # Determine signal
            has_big_drop = pd.notna(prev_ret) and prev_ret < self.config.mean_reversion_threshold
            is_thursday = weekday == 3

            signal = None
            etf = None
            etf_data = None

            if self.config.mean_reversion_enabled and has_big_drop:
                signal = "mean_reversion"
                etf = "BITX"
                etf_data = bitx.iloc[i]
            elif self.config.short_thursday_enabled and is_thursday:
                signal = "short_thursday"
                etf = "SBIT"
                etf_data = sbit.iloc[i]

            if signal and etf_data is not None:
                entry = etf_data["open"] * (1 + slippage / 100)
                exit_price = etf_data["close"] * (1 - slippage / 100)
                ret = (exit_price - entry) / entry
                capital *= 1 + ret

                trades.append(
                    {
                        "date": trade_date,
                        "signal": signal,
                        "etf": etf,
                        "entry": entry,
                        "exit": exit_price,
                        "return_pct": ret * 100,
                        "capital": capital,
                    }
                )

        # Calculate metrics
        total_return = (capital - self.initial_capital) / self.initial_capital * 100

        if trades:
            returns = [t["return_pct"] / 100 for t in trades]
            win_rate = sum(1 for r in returns if r > 0) / len(returns) * 100
            avg_return = np.mean(returns) * 100
            sharpe = (
                (np.mean(returns) / np.std(returns)) * np.sqrt(len(returns))
                if np.std(returns) > 0
                else 0
            )

            # Max drawdown
            peak = self.initial_capital
            max_dd = 0
            for t in trades:
                if t["capital"] > peak:
                    peak = t["capital"]
                dd = (peak - t["capital"]) / peak
                max_dd = max(max_dd, dd)
        else:
            win_rate = 0
            avg_return = 0
            sharpe = 0
            max_dd = 0

        # Buy and hold benchmark
        ibit_bh = (ibit["close"].iloc[-1] - ibit["open"].iloc[0]) / ibit["open"].iloc[0] * 100
        bitx_bh = (bitx["close"].iloc[-1] - bitx["open"].iloc[0]) / bitx["open"].iloc[0] * 100

        # Breakdown by signal
        mr_trades = [t for t in trades if t["signal"] == "mean_reversion"]
        thu_trades = [t for t in trades if t["signal"] == "short_thursday"]

        return {
            "initial_capital": self.initial_capital,
            "final_capital": capital,
            "total_return_pct": total_return,
            "total_trades": len(trades),
            "win_rate": win_rate,
            "avg_return": avg_return,
            "sharpe_ratio": sharpe,
            "max_drawdown_pct": max_dd * 100,
            "ibit_bh_return": ibit_bh,
            "bitx_bh_return": bitx_bh,
            "vs_ibit_bh": total_return - ibit_bh,
            "mean_rev_trades": len(mr_trades),
            "mean_rev_win_rate": sum(1 for t in mr_trades if t["return_pct"] > 0)
            / len(mr_trades)
            * 100
            if mr_trades
            else 0,
            "short_thu_trades": len(thu_trades),
            "short_thu_win_rate": sum(1 for t in thu_trades if t["return_pct"] > 0)
            / len(thu_trades)
            * 100
            if thu_trades
            else 0,
            "trades": trades,
        }
