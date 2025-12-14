#!/usr/bin/env python3
"""
Dynamic Portfolio Optimizer for Bitcoin ETFs

Creates an optimal allocation strategy across:
- IBIT (+1x Bitcoin)
- BITX (+2x Bitcoin)
- BITI (-1x Bitcoin)
- SBIT (-2x Bitcoin)

Uses multiple signals to determine regime and optimal allocation.
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import date, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
from enum import Enum
import warnings
warnings.filterwarnings('ignore')


class MarketRegime(Enum):
    """Market regime classification."""
    STRONG_BULL = "strong_bull"      # Use BITX (+2x)
    MODERATE_BULL = "moderate_bull"  # Use IBIT (+1x)
    NEUTRAL = "neutral"              # Cash or minimal position
    MODERATE_BEAR = "moderate_bear"  # Use BITI (-1x)
    STRONG_BEAR = "strong_bear"      # Use SBIT (-2x)


@dataclass
class DailySignal:
    """Daily signal and allocation."""
    date: date
    regime: MarketRegime
    allocation: Dict[str, float]  # ticker -> weight
    signal_strength: float  # -1 to +1
    signals: Dict[str, float]  # individual signal components
    reasoning: str


@dataclass
class PortfolioState:
    """Current portfolio state."""
    date: date
    holdings: Dict[str, int]  # ticker -> shares
    cash: float
    total_value: float
    daily_return: float
    cumulative_return: float


@dataclass
class BacktestResult:
    """Results from portfolio backtest."""
    daily_states: List[PortfolioState]
    signals: List[DailySignal]

    total_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0

    # Comparison benchmarks
    ibit_buy_hold: float = 0.0
    bitx_buy_hold: float = 0.0


class DynamicPortfolioOptimizer:
    """
    Dynamic portfolio allocation system.

    Combines multiple signals to determine optimal allocation
    across Bitcoin ETFs with different leverage profiles.
    """

    def __init__(
        self,
        initial_capital: float = 10000.0,
        max_leverage: float = 2.0,
        rebalance_threshold: float = 0.1,  # Rebalance if allocation drifts >10%
        transaction_cost: float = 0.001,   # 0.1% per trade
    ):
        self.initial_capital = initial_capital
        self.max_leverage = max_leverage
        self.rebalance_threshold = rebalance_threshold
        self.transaction_cost = transaction_cost

        # ETF universe
        self.tickers = {
            'IBIT': 1.0,   # +1x
            'BITX': 2.0,   # +2x
            'BITI': -1.0,  # -1x
            'SBIT': -2.0,  # -2x
        }

        self._data: Dict[str, pd.DataFrame] = {}
        self._aligned_data: Optional[pd.DataFrame] = None

    def load_data(self, start_date: date, end_date: date) -> pd.DataFrame:
        """Load and align data for all tickers."""
        print("Loading data for all tickers...")

        for ticker in self.tickers.keys():
            try:
                t = yf.Ticker(ticker)
                df = t.history(start=start_date, end=end_date + timedelta(days=1), interval="1d")
                df = df.reset_index()
                df.columns = [c.lower() for c in df.columns]

                if 'date' in df.columns:
                    df['date'] = pd.to_datetime(df['date']).dt.date
                elif 'datetime' in df.columns:
                    df['date'] = pd.to_datetime(df['datetime']).dt.date

                self._data[ticker] = df
                print(f"  {ticker}: {len(df)} days")
            except Exception as e:
                print(f"  {ticker}: Failed to load - {e}")

        # Find common dates
        all_dates = None
        for ticker, df in self._data.items():
            dates = set(df['date'])
            if all_dates is None:
                all_dates = dates
            else:
                all_dates = all_dates & dates

        if not all_dates:
            raise ValueError("No overlapping dates found!")

        print(f"\nCommon date range: {min(all_dates)} to {max(all_dates)} ({len(all_dates)} days)")

        # Create aligned DataFrame
        aligned = pd.DataFrame({'date': sorted(all_dates)})

        for ticker, df in self._data.items():
            df_filtered = df[df['date'].isin(all_dates)].copy()
            df_filtered = df_filtered.sort_values('date').reset_index(drop=True)

            aligned[f'{ticker}_open'] = df_filtered['open'].values
            aligned[f'{ticker}_high'] = df_filtered['high'].values
            aligned[f'{ticker}_low'] = df_filtered['low'].values
            aligned[f'{ticker}_close'] = df_filtered['close'].values
            aligned[f'{ticker}_return'] = (df_filtered['close'] - df_filtered['open']) / df_filtered['open']

        self._aligned_data = aligned
        return aligned

    def calculate_signals(self, lookback: int = 20) -> pd.DataFrame:
        """Calculate all trading signals."""
        df = self._aligned_data.copy()

        # Use IBIT as primary signal source (less noise than leveraged products)
        close = df['IBIT_close']
        returns = df['IBIT_return']

        # 1. TREND SIGNALS
        df['sma_10'] = close.rolling(10).mean()
        df['sma_20'] = close.rolling(20).mean()
        df['sma_50'] = close.rolling(50).mean()

        # Trend score: -1 to +1
        df['trend_signal'] = 0.0
        df.loc[close > df['sma_20'], 'trend_signal'] += 0.25
        df.loc[close > df['sma_50'], 'trend_signal'] += 0.25
        df.loc[df['sma_20'] > df['sma_50'], 'trend_signal'] += 0.25
        df.loc[df['sma_10'] > df['sma_20'], 'trend_signal'] += 0.25
        df.loc[close < df['sma_20'], 'trend_signal'] -= 0.25
        df.loc[close < df['sma_50'], 'trend_signal'] -= 0.25
        df.loc[df['sma_20'] < df['sma_50'], 'trend_signal'] -= 0.25
        df.loc[df['sma_10'] < df['sma_20'], 'trend_signal'] -= 0.25

        # 2. MOMENTUM SIGNALS
        df['momentum_5d'] = close.pct_change(5)
        df['momentum_10d'] = close.pct_change(10)
        df['momentum_20d'] = close.pct_change(20)

        # Momentum score: -1 to +1
        df['momentum_signal'] = 0.0
        df.loc[df['momentum_5d'] > 0.02, 'momentum_signal'] += 0.33
        df.loc[df['momentum_10d'] > 0.05, 'momentum_signal'] += 0.33
        df.loc[df['momentum_20d'] > 0.10, 'momentum_signal'] += 0.34
        df.loc[df['momentum_5d'] < -0.02, 'momentum_signal'] -= 0.33
        df.loc[df['momentum_10d'] < -0.05, 'momentum_signal'] -= 0.33
        df.loc[df['momentum_20d'] < -0.10, 'momentum_signal'] -= 0.34

        # 3. MEAN REVERSION SIGNAL (short-term)
        df['prev_return'] = returns.shift(1)
        df['prev_2d_return'] = returns.shift(1) + returns.shift(2)

        # Mean reversion: buy after big drops, sell after big rallies
        df['mean_rev_signal'] = 0.0
        df.loc[df['prev_return'] < -0.03, 'mean_rev_signal'] = 0.5
        df.loc[df['prev_return'] < -0.05, 'mean_rev_signal'] = 1.0
        df.loc[df['prev_return'] > 0.03, 'mean_rev_signal'] = -0.3
        df.loc[df['prev_return'] > 0.05, 'mean_rev_signal'] = -0.5

        # 4. VOLATILITY SIGNAL
        df['volatility'] = returns.rolling(10).std()
        df['vol_percentile'] = df['volatility'].rolling(50).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1] if len(x) > 0 else 0.5
        )

        # High vol = reduce position size, low vol = increase
        df['vol_signal'] = 1.0 - (df['vol_percentile'] - 0.5)  # 0.5 to 1.5 multiplier

        # 5. DAY OF WEEK SIGNAL
        df['weekday'] = pd.to_datetime(df['date']).apply(lambda x: x.weekday())

        # Thursday bearish bias
        df['dow_signal'] = 0.0
        df.loc[df['weekday'] == 0, 'dow_signal'] = 0.1   # Monday slight bull
        df.loc[df['weekday'] == 3, 'dow_signal'] = -0.3  # Thursday bearish
        df.loc[df['weekday'] == 4, 'dow_signal'] = 0.1   # Friday slight bull

        # 6. COMPOSITE SIGNAL
        # Weighted combination
        weights = {
            'trend': 0.30,
            'momentum': 0.25,
            'mean_rev': 0.25,
            'dow': 0.10,
            'vol_adj': 0.10
        }

        df['composite_signal'] = (
            weights['trend'] * df['trend_signal'] +
            weights['momentum'] * df['momentum_signal'] +
            weights['mean_rev'] * df['mean_rev_signal'] +
            weights['dow'] * df['dow_signal']
        )

        # Apply volatility adjustment
        df['composite_signal'] = df['composite_signal'] * df['vol_signal']

        # Clip to -1, +1 range
        df['composite_signal'] = df['composite_signal'].clip(-1, 1)

        self._aligned_data = df
        return df

    def determine_allocation(self, signal: float) -> Tuple[MarketRegime, Dict[str, float]]:
        """
        Determine regime and allocation based on composite signal.

        Signal ranges:
        - Strong bull (>0.5): 100% BITX
        - Moderate bull (0.2 to 0.5): 100% IBIT
        - Neutral (-0.2 to 0.2): 50% cash, 50% IBIT
        - Moderate bear (-0.5 to -0.2): 100% BITI
        - Strong bear (<-0.5): 100% SBIT
        """
        if signal > 0.5:
            return MarketRegime.STRONG_BULL, {'BITX': 1.0}
        elif signal > 0.2:
            return MarketRegime.MODERATE_BULL, {'IBIT': 1.0}
        elif signal > -0.2:
            return MarketRegime.NEUTRAL, {'IBIT': 0.5}  # 50% cash implied
        elif signal > -0.5:
            return MarketRegime.MODERATE_BEAR, {'BITI': 1.0}
        else:
            return MarketRegime.STRONG_BEAR, {'SBIT': 1.0}

    def run_backtest(self) -> BacktestResult:
        """Run full backtest with dynamic allocation."""
        if self._aligned_data is None:
            raise ValueError("Must load data first!")

        # Calculate signals if not done
        if 'composite_signal' not in self._aligned_data.columns:
            self.calculate_signals()

        df = self._aligned_data

        # Initialize
        cash = self.initial_capital
        holdings: Dict[str, int] = {}
        states: List[PortfolioState] = []
        signals: List[DailySignal] = []
        cumulative_return = 0.0
        prev_value = self.initial_capital
        trades = 0
        wins = 0

        print("\nRunning backtest...")

        for i, row in df.iterrows():
            if i < 50:  # Need lookback for signals
                continue

            current_date = row['date']
            signal_value = row['composite_signal']

            if pd.isna(signal_value):
                continue

            # Determine target allocation
            regime, target_allocation = self.determine_allocation(signal_value)

            # Calculate current portfolio value
            portfolio_value = cash
            for ticker, shares in holdings.items():
                price = row[f'{ticker}_close']
                portfolio_value += shares * price

            # Rebalance to target allocation
            target_values = {t: portfolio_value * w for t, w in target_allocation.items()}

            # Execute rebalance
            for ticker in self.tickers.keys():
                current_shares = holdings.get(ticker, 0)
                current_value = current_shares * row[f'{ticker}_close'] if current_shares > 0 else 0
                target_value = target_values.get(ticker, 0)

                # Check if rebalance needed
                if abs(target_value - current_value) > self.rebalance_threshold * portfolio_value:
                    price = row[f'{ticker}_close']

                    # Sell current position
                    if current_shares > 0:
                        cash += current_shares * price * (1 - self.transaction_cost)
                        holdings[ticker] = 0
                        trades += 1

                    # Buy new position
                    if target_value > 0:
                        shares_to_buy = int(target_value / price)
                        if shares_to_buy > 0:
                            cost = shares_to_buy * price * (1 + self.transaction_cost)
                            if cost <= cash:
                                holdings[ticker] = shares_to_buy
                                cash -= cost
                                trades += 1

            # Calculate end of day value
            end_value = cash
            for ticker, shares in holdings.items():
                end_value += shares * row[f'{ticker}_close']

            # Track daily return
            daily_return = (end_value - prev_value) / prev_value if prev_value > 0 else 0
            cumulative_return = (end_value - self.initial_capital) / self.initial_capital

            if daily_return > 0:
                wins += 1

            # Store state
            states.append(PortfolioState(
                date=current_date,
                holdings=holdings.copy(),
                cash=cash,
                total_value=end_value,
                daily_return=daily_return,
                cumulative_return=cumulative_return
            ))

            # Store signal
            signals.append(DailySignal(
                date=current_date,
                regime=regime,
                allocation=target_allocation,
                signal_strength=signal_value,
                signals={
                    'trend': row['trend_signal'],
                    'momentum': row['momentum_signal'],
                    'mean_rev': row['mean_rev_signal'],
                    'dow': row['dow_signal'],
                    'vol_adj': row['vol_signal']
                },
                reasoning=f"Signal: {signal_value:.2f} -> {regime.value}"
            ))

            prev_value = end_value

        # Calculate final metrics
        result = BacktestResult(
            daily_states=states,
            signals=signals
        )

        if states:
            returns = [s.daily_return for s in states]
            result.total_return_pct = states[-1].cumulative_return * 100
            result.sharpe_ratio = (np.mean(returns) / np.std(returns) * np.sqrt(252)) if np.std(returns) > 0 else 0
            result.win_rate = (wins / len(states) * 100) if states else 0
            result.total_trades = trades

            # Calculate max drawdown
            peak = self.initial_capital
            max_dd = 0
            for state in states:
                if state.total_value > peak:
                    peak = state.total_value
                dd = (peak - state.total_value) / peak
                max_dd = max(max_dd, dd)
            result.max_drawdown_pct = max_dd * 100

            # Buy and hold comparisons
            first_idx = 50  # After lookback
            last_idx = len(df) - 1

            result.ibit_buy_hold = (df.iloc[last_idx]['IBIT_close'] - df.iloc[first_idx]['IBIT_close']) / df.iloc[first_idx]['IBIT_close'] * 100
            result.bitx_buy_hold = (df.iloc[last_idx]['BITX_close'] - df.iloc[first_idx]['BITX_close']) / df.iloc[first_idx]['BITX_close'] * 100

        return result

    def analyze_regime_distribution(self, signals: List[DailySignal]) -> Dict[str, int]:
        """Analyze distribution of regime calls."""
        distribution = {}
        for signal in signals:
            regime = signal.regime.value
            distribution[regime] = distribution.get(regime, 0) + 1
        return distribution

    def print_results(self, result: BacktestResult):
        """Print detailed backtest results."""
        print("\n" + "="*80)
        print("DYNAMIC PORTFOLIO BACKTEST RESULTS")
        print("="*80)

        print(f"\nPortfolio Performance:")
        print(f"  Total Return: {result.total_return_pct:+.1f}%")
        print(f"  Sharpe Ratio: {result.sharpe_ratio:.2f}")
        print(f"  Max Drawdown: {result.max_drawdown_pct:.1f}%")
        print(f"  Win Rate: {result.win_rate:.1f}%")
        print(f"  Total Trades: {result.total_trades}")

        print(f"\nBenchmark Comparison:")
        print(f"  vs IBIT Buy & Hold: {result.total_return_pct - result.ibit_buy_hold:+.1f}%")
        print(f"  vs BITX Buy & Hold: {result.total_return_pct - result.bitx_buy_hold:+.1f}%")
        print(f"  IBIT B&H Return: {result.ibit_buy_hold:+.1f}%")
        print(f"  BITX B&H Return: {result.bitx_buy_hold:+.1f}%")

        # Regime distribution
        regime_dist = self.analyze_regime_distribution(result.signals)
        print(f"\nRegime Distribution:")
        for regime, count in sorted(regime_dist.items()):
            pct = count / len(result.signals) * 100
            print(f"  {regime}: {count} days ({pct:.1f}%)")

        # Monthly breakdown
        print(f"\nMonthly Returns:")
        monthly = {}
        for state in result.daily_states:
            month_key = f"{state.date.year}-{state.date.month:02d}"
            if month_key not in monthly:
                monthly[month_key] = []
            monthly[month_key].append(state.daily_return)

        for month, returns in sorted(monthly.items())[-6:]:  # Last 6 months
            monthly_return = (np.prod([1 + r for r in returns]) - 1) * 100
            print(f"  {month}: {monthly_return:+.1f}%")


def run_optimization_variants():
    """Test multiple strategy variants."""
    print("="*80)
    print("TESTING OPTIMIZATION VARIANTS")
    print("="*80)

    # Test different signal weights
    variants = [
        {'trend': 0.4, 'momentum': 0.3, 'mean_rev': 0.2, 'dow': 0.1},
        {'trend': 0.2, 'momentum': 0.2, 'mean_rev': 0.4, 'dow': 0.2},
        {'trend': 0.5, 'momentum': 0.3, 'mean_rev': 0.1, 'dow': 0.1},
    ]

    results = []

    for i, weights in enumerate(variants):
        print(f"\n--- Variant {i+1}: {weights} ---")
        optimizer = DynamicPortfolioOptimizer()
        optimizer.load_data(date(2024, 4, 15), date.today())
        optimizer.calculate_signals()  # Must calculate signals first

        # Override weights
        df = optimizer._aligned_data
        df['composite_signal'] = (
            weights['trend'] * df['trend_signal'] +
            weights['momentum'] * df['momentum_signal'] +
            weights['mean_rev'] * df['mean_rev_signal'] +
            weights['dow'] * df['dow_signal']
        )
        df['composite_signal'] = df['composite_signal'].clip(-1, 1)

        result = optimizer.run_backtest()
        results.append((weights, result))
        print(f"Return: {result.total_return_pct:+.1f}%, Sharpe: {result.sharpe_ratio:.2f}")

    # Find best
    best = max(results, key=lambda x: x[1].total_return_pct)
    print(f"\nBest Variant: {best[0]} with {best[1].total_return_pct:+.1f}% return")

    return results


def main():
    """Main execution."""
    print("="*80)
    print("DYNAMIC BITCOIN ETF PORTFOLIO OPTIMIZER")
    print("="*80)
    print("\nETF Universe:")
    print("  IBIT: +1x Bitcoin")
    print("  BITX: +2x Bitcoin")
    print("  BITI: -1x Bitcoin (inverse)")
    print("  SBIT: -2x Bitcoin (inverse)")

    # Initialize optimizer
    optimizer = DynamicPortfolioOptimizer(
        initial_capital=10000.0,
        rebalance_threshold=0.1
    )

    # Load data (BITX/SBIT launched April 2024)
    start_date = date(2024, 4, 15)
    end_date = date.today()

    print(f"\nPeriod: {start_date} to {end_date}")

    optimizer.load_data(start_date, end_date)
    optimizer.calculate_signals()

    # Run backtest
    result = optimizer.run_backtest()
    optimizer.print_results(result)

    # Also test variants
    print("\n" + "="*80)
    run_optimization_variants()


if __name__ == "__main__":
    main()
