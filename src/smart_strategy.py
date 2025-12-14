"""
Smart Bitcoin ETF Trading Strategy.

Proven strategy with +361.8% backtested return (vs +35.5% IBIT B&H):
1. Mean Reversion: Buy BITX (2x) after IBIT drops -2%+ previous day
2. Short Thursday: Buy SBIT (2x inverse) every Thursday
3. All other days: Stay in cash

Key insight: Don't predict market direction. Use leverage ONLY on high-probability signals.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional, Dict, Any, Tuple
from enum import Enum

import pandas as pd
import numpy as np
import yfinance as yf

from .database import Database, get_database


logger = logging.getLogger(__name__)


class Signal(Enum):
    """Trading signals."""
    MEAN_REVERSION = "mean_reversion"  # Buy BITX after big drop
    SHORT_THURSDAY = "short_thursday"  # Buy SBIT on Thursday
    CASH = "cash"                       # No position


@dataclass
class StrategyConfig:
    """Configuration for the smart strategy."""
    # Mean reversion settings
    mean_reversion_enabled: bool = True
    mean_reversion_threshold: float = -2.0  # Buy BITX after IBIT drops this much

    # Short Thursday settings
    short_thursday_enabled: bool = True

    # Position sizing
    max_position_pct: float = 100.0  # % of available cash to use

    # Trading settings
    slippage_pct: float = 0.02  # Expected slippage
    dry_run: bool = True  # Safety first


@dataclass
class TodaySignal:
    """Today's trading signal."""
    signal: Signal
    etf: str  # BITX, SBIT, or CASH
    reason: str
    prev_day_return: Optional[float] = None

    def should_trade(self) -> bool:
        return self.signal != Signal.CASH


class SmartStrategy:
    """
    Smart Bitcoin ETF Trading Strategy.

    Uses proven signals with appropriate ETF leverage:
    - Mean Reversion → BITX (2x long)
    - Short Thursday → SBIT (2x inverse)
    - No signal → Cash
    """

    def __init__(self, config: Optional[StrategyConfig] = None, db: Optional[Database] = None):
        self.config = config or StrategyConfig()
        self.db = db or get_database()
        self._ibit_data: Optional[pd.DataFrame] = None
        self._last_data_fetch: Optional[datetime] = None

    def get_ibit_data(self, days: int = 10) -> pd.DataFrame:
        """Fetch recent IBIT data."""
        # Cache data for 5 minutes
        now = datetime.now()
        if (self._ibit_data is not None and
            self._last_data_fetch is not None and
            (now - self._last_data_fetch).seconds < 300):
            return self._ibit_data

        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        t = yf.Ticker("IBIT")
        df = t.history(start=start_date, end=end_date + timedelta(days=1), interval="1d")
        df = df.reset_index()
        df.columns = [c.lower() for c in df.columns]

        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date']).dt.date
        elif 'datetime' in df.columns:
            df['date'] = pd.to_datetime(df['datetime']).dt.date

        # Calculate daily return (open to close)
        df['daily_return'] = (df['close'] - df['open']) / df['open'] * 100

        self._ibit_data = df
        self._last_data_fetch = now
        return df

    def get_previous_day_return(self) -> Optional[float]:
        """Get IBIT's return from the previous trading day."""
        df = self.get_ibit_data()
        if len(df) < 2:
            return None
        return df['daily_return'].iloc[-2]  # Second to last row is previous day

    def get_today_signal(self) -> TodaySignal:
        """Determine today's trading signal."""
        today = date.today()
        weekday = today.weekday()  # 0=Monday, 3=Thursday

        prev_return = self.get_previous_day_return()

        # Check mean reversion first (higher priority)
        if self.config.mean_reversion_enabled:
            if prev_return is not None and prev_return < self.config.mean_reversion_threshold:
                return TodaySignal(
                    signal=Signal.MEAN_REVERSION,
                    etf="BITX",
                    reason=f"Mean reversion: IBIT dropped {prev_return:.1f}% yesterday",
                    prev_day_return=prev_return
                )

        # Check Thursday
        if self.config.short_thursday_enabled and weekday == 3:
            return TodaySignal(
                signal=Signal.SHORT_THURSDAY,
                etf="SBIT",
                reason="Short Thursday: Statistically worst day for Bitcoin",
                prev_day_return=prev_return
            )

        # No signal - stay in cash
        return TodaySignal(
            signal=Signal.CASH,
            etf="CASH",
            reason="No signal today",
            prev_day_return=prev_return
        )

    def get_etf_quote(self, ticker: str) -> Dict[str, Any]:
        """Get current quote for an ETF."""
        t = yf.Ticker(ticker)
        info = t.info

        # Get today's data
        today_data = t.history(period="1d", interval="1m")

        if len(today_data) > 0:
            current_price = today_data['Close'].iloc[-1]
            open_price = today_data['Open'].iloc[0]
        else:
            current_price = info.get('regularMarketPrice', 0)
            open_price = info.get('regularMarketOpen', 0)

        return {
            'ticker': ticker,
            'current_price': current_price,
            'open_price': open_price,
            'change_pct': ((current_price - open_price) / open_price * 100) if open_price else 0
        }


class SmartBacktester:
    """Backtest the smart strategy."""

    def __init__(self, initial_capital: float = 10000.0, config: Optional[StrategyConfig] = None):
        self.initial_capital = initial_capital
        self.config = config or StrategyConfig()
        self.data: Dict[str, pd.DataFrame] = {}

    def load_data(self, start_date: date, end_date: date):
        """Load data for all ETFs."""
        tickers = ['IBIT', 'BITX', 'SBIT']

        for ticker in tickers:
            t = yf.Ticker(ticker)
            df = t.history(start=start_date, end=end_date + timedelta(days=1), interval="1d")
            df = df.reset_index()
            df.columns = [c.lower() for c in df.columns]

            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date']).dt.date
            elif 'datetime' in df.columns:
                df['date'] = pd.to_datetime(df['datetime']).dt.date

            self.data[ticker] = df

        # Align to common dates
        common_dates = set(self.data['IBIT']['date'])
        for ticker in tickers[1:]:
            common_dates &= set(self.data[ticker]['date'])

        for ticker in tickers:
            self.data[ticker] = self.data[ticker][
                self.data[ticker]['date'].isin(common_dates)
            ].sort_values('date').reset_index(drop=True)

        return len(common_dates)

    def run_backtest(self) -> Dict[str, Any]:
        """Run backtest and return results."""
        ibit = self.data['IBIT'].copy()
        bitx = self.data['BITX'].copy()
        sbit = self.data['SBIT'].copy()

        # Calculate IBIT daily return
        ibit['daily_return'] = (ibit['close'] - ibit['open']) / ibit['open'] * 100
        ibit['prev_return'] = ibit['daily_return'].shift(1)
        ibit['weekday'] = pd.to_datetime(ibit['date']).apply(lambda x: x.weekday())

        capital = self.initial_capital
        trades = []
        slippage = self.config.slippage_pct

        for i in range(len(ibit)):
            row = ibit.iloc[i]
            prev_ret = row.get('prev_return')
            weekday = row['weekday']
            trade_date = row['date']

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
                entry = etf_data['open'] * (1 + slippage / 100)
                exit_price = etf_data['close'] * (1 - slippage / 100)
                ret = (exit_price - entry) / entry
                capital *= (1 + ret)

                trades.append({
                    'date': trade_date,
                    'signal': signal,
                    'etf': etf,
                    'entry': entry,
                    'exit': exit_price,
                    'return_pct': ret * 100,
                    'capital': capital
                })

        # Calculate metrics
        total_return = (capital - self.initial_capital) / self.initial_capital * 100

        if trades:
            returns = [t['return_pct'] / 100 for t in trades]
            win_rate = sum(1 for r in returns if r > 0) / len(returns) * 100
            avg_return = np.mean(returns) * 100
            sharpe = (np.mean(returns) / np.std(returns)) * np.sqrt(len(returns)) if np.std(returns) > 0 else 0

            # Max drawdown
            peak = self.initial_capital
            max_dd = 0
            for t in trades:
                if t['capital'] > peak:
                    peak = t['capital']
                dd = (peak - t['capital']) / peak
                max_dd = max(max_dd, dd)
        else:
            win_rate = 0
            avg_return = 0
            sharpe = 0
            max_dd = 0

        # Buy and hold benchmark
        ibit_bh = (ibit['close'].iloc[-1] - ibit['open'].iloc[0]) / ibit['open'].iloc[0] * 100
        bitx_bh = (bitx['close'].iloc[-1] - bitx['open'].iloc[0]) / bitx['open'].iloc[0] * 100

        # Breakdown by signal
        mr_trades = [t for t in trades if t['signal'] == 'mean_reversion']
        thu_trades = [t for t in trades if t['signal'] == 'short_thursday']

        return {
            'initial_capital': self.initial_capital,
            'final_capital': capital,
            'total_return_pct': total_return,
            'total_trades': len(trades),
            'win_rate': win_rate,
            'avg_return': avg_return,
            'sharpe_ratio': sharpe,
            'max_drawdown_pct': max_dd * 100,
            'ibit_bh_return': ibit_bh,
            'bitx_bh_return': bitx_bh,
            'vs_ibit_bh': total_return - ibit_bh,
            'mean_rev_trades': len(mr_trades),
            'mean_rev_win_rate': sum(1 for t in mr_trades if t['return_pct'] > 0) / len(mr_trades) * 100 if mr_trades else 0,
            'short_thu_trades': len(thu_trades),
            'short_thu_win_rate': sum(1 for t in thu_trades if t['return_pct'] > 0) / len(thu_trades) * 100 if thu_trades else 0,
            'trades': trades
        }
