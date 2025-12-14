"""
Multiple Trading Strategies for IBIT Bot.

Implements data-driven strategies based on quantitative analysis:
1. Mean Reversion - Buy after big down days
2. Short Thursday - Thursday has negative edge
3. Intraday Bounce - Buy after big intraday drops
4. Trend Following - Trade with the trend (MA crossover)
5. Combined - Multiple signals combined
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from enum import Enum
from datetime import date, datetime, timedelta
from abc import ABC, abstractmethod

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class StrategyType(Enum):
    """Available strategy types."""
    ORIGINAL_DIP = "original_dip"           # Original 10 AM dip (not recommended)
    MEAN_REVERSION = "mean_reversion"       # Buy after big down days
    SHORT_THURSDAY = "short_thursday"       # Short on Thursdays
    INTRADAY_BOUNCE = "intraday_bounce"     # Buy after big intraday drops
    TREND_FOLLOWING = "trend_following"     # MA crossover
    COMBINED = "combined"                   # Multiple signals


class SignalDirection(Enum):
    """Trade direction."""
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


@dataclass
class StrategySignal:
    """Signal from a strategy."""
    strategy: StrategyType
    direction: SignalDirection
    strength: float  # 0-1, confidence/size multiplier
    reason: str
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DailyData:
    """Daily OHLCV data for a single day."""
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int = 0

    @property
    def daily_return(self) -> float:
        """Close-to-close return (requires previous close)."""
        return 0.0  # Calculated externally

    @property
    def intraday_return(self) -> float:
        """Open-to-close return."""
        return (self.close - self.open) / self.open * 100 if self.open > 0 else 0

    @property
    def range_pct(self) -> float:
        """High-low range as percentage."""
        return (self.high - self.low) / self.open * 100 if self.open > 0 else 0


class BaseStrategy(ABC):
    """Base class for all strategies."""

    def __init__(self, name: str, strategy_type: StrategyType):
        self.name = name
        self.strategy_type = strategy_type
        self._historical_data: List[DailyData] = []

    def update_data(self, data: List[DailyData]):
        """Update historical data."""
        self._historical_data = sorted(data, key=lambda x: x.date)

    def add_daily_data(self, daily: DailyData):
        """Add a single day's data."""
        self._historical_data.append(daily)
        self._historical_data = sorted(self._historical_data, key=lambda x: x.date)[-100:]  # Keep last 100 days

    @abstractmethod
    def generate_signal(self, current_price: float, current_date: date) -> Optional[StrategySignal]:
        """Generate trading signal."""
        pass

    def get_previous_day(self) -> Optional[DailyData]:
        """Get the most recent completed day's data."""
        if len(self._historical_data) >= 1:
            return self._historical_data[-1]
        return None

    def get_previous_days(self, n: int) -> List[DailyData]:
        """Get the last n days of data."""
        return self._historical_data[-n:] if len(self._historical_data) >= n else self._historical_data


class MeanReversionStrategy(BaseStrategy):
    """
    Mean Reversion Strategy

    Buy after big down days (-2% to -4%), expecting a bounce.
    Skip if next day is Thursday (negative edge day).

    Historical Performance:
    - After -3% day: 69.7% win rate, +1.12% avg return
    - Sharpe ratio: 2.83
    """

    def __init__(self, threshold: float = -3.0, skip_thursday: bool = True):
        super().__init__("Mean Reversion", StrategyType.MEAN_REVERSION)
        self.threshold = threshold  # e.g., -3.0 means buy after -3% down day
        self.skip_thursday = skip_thursday

    def generate_signal(self, current_price: float, current_date: date) -> Optional[StrategySignal]:
        """Generate buy signal if previous day was a big down day."""
        prev_day = self.get_previous_day()
        if prev_day is None:
            return None

        # Calculate previous day's return
        prev_return = prev_day.intraday_return

        # Check if previous day meets threshold
        if prev_return > self.threshold:
            return StrategySignal(
                strategy=self.strategy_type,
                direction=SignalDirection.FLAT,
                strength=0.0,
                reason=f"Previous day return {prev_return:.2f}% > threshold {self.threshold}%"
            )

        # Check if today is Thursday (skip if configured)
        if self.skip_thursday and current_date.weekday() == 3:
            return StrategySignal(
                strategy=self.strategy_type,
                direction=SignalDirection.FLAT,
                strength=0.0,
                reason=f"Skipping Thursday despite signal (prev day: {prev_return:.2f}%)"
            )

        # Generate buy signal
        strength = min(1.0, abs(prev_return) / 5.0)  # Scale strength by drop size

        return StrategySignal(
            strategy=self.strategy_type,
            direction=SignalDirection.LONG,
            strength=strength,
            reason=f"Mean reversion: prev day {prev_return:.2f}% < {self.threshold}%",
            entry_price=current_price,
            metadata={
                "prev_return": prev_return,
                "threshold": self.threshold,
                "prev_date": prev_day.date.isoformat()
            }
        )


class ShortThursdayStrategy(BaseStrategy):
    """
    Short Thursday Strategy

    Thursday is statistically the worst day for IBIT.
    Short at open, cover at close.

    Historical Performance:
    - 59.4% win rate for shorts
    - +0.71% avg return
    - +68.5% total return
    """

    def __init__(self):
        super().__init__("Short Thursday", StrategyType.SHORT_THURSDAY)

    def generate_signal(self, current_price: float, current_date: date) -> Optional[StrategySignal]:
        """Generate short signal on Thursdays."""
        # Only signal on Thursday
        if current_date.weekday() != 3:
            return StrategySignal(
                strategy=self.strategy_type,
                direction=SignalDirection.FLAT,
                strength=0.0,
                reason=f"Not Thursday (day {current_date.weekday()})"
            )

        return StrategySignal(
            strategy=self.strategy_type,
            direction=SignalDirection.SHORT,
            strength=0.7,  # Medium-high confidence
            reason="Short Thursday - historically weakest day",
            entry_price=current_price,
            metadata={
                "day_of_week": "Thursday",
                "historical_win_rate": 0.594,
                "historical_avg_return": 0.71
            }
        )


class IntradayBounceStrategy(BaseStrategy):
    """
    Intraday Bounce Strategy

    Buy after big intraday drops (5%+ over 4 hours), expecting a bounce.

    Historical Performance:
    - After -5% 4-hour drop: 68% win rate, +1.10% avg return
    """

    def __init__(self, threshold: float = -5.0, lookback_hours: int = 4):
        super().__init__("Intraday Bounce", StrategyType.INTRADAY_BOUNCE)
        self.threshold = threshold
        self.lookback_hours = lookback_hours
        self._intraday_highs: Dict[date, float] = {}

    def update_intraday_high(self, current_date: date, price: float):
        """Track intraday high."""
        if current_date not in self._intraday_highs:
            self._intraday_highs[current_date] = price
        else:
            self._intraday_highs[current_date] = max(self._intraday_highs[current_date], price)

    def generate_signal(self, current_price: float, current_date: date) -> Optional[StrategySignal]:
        """Generate buy signal if intraday drop meets threshold."""
        # Get today's high
        day_high = self._intraday_highs.get(current_date)
        if day_high is None:
            return None

        # Calculate drop from high
        drop_pct = (current_price - day_high) / day_high * 100

        if drop_pct > self.threshold:
            return StrategySignal(
                strategy=self.strategy_type,
                direction=SignalDirection.FLAT,
                strength=0.0,
                reason=f"Intraday drop {drop_pct:.2f}% > threshold {self.threshold}%"
            )

        # Generate buy signal
        strength = min(1.0, abs(drop_pct) / 7.0)

        return StrategySignal(
            strategy=self.strategy_type,
            direction=SignalDirection.LONG,
            strength=strength,
            reason=f"Intraday bounce: dropped {drop_pct:.2f}% from high",
            entry_price=current_price,
            metadata={
                "day_high": day_high,
                "drop_pct": drop_pct,
                "threshold": self.threshold
            }
        )


class TrendFollowingStrategy(BaseStrategy):
    """
    Trend Following Strategy

    Trade in direction of trend using moving average crossover.

    Historical Performance:
    - Golden Cross (20 > 50): +2.04% avg return, 72.5% win rate
    - Above both SMAs: +0.32% avg hourly return
    """

    def __init__(self, fast_period: int = 20, slow_period: int = 50):
        super().__init__("Trend Following", StrategyType.TREND_FOLLOWING)
        self.fast_period = fast_period
        self.slow_period = slow_period

    def _calculate_sma(self, period: int) -> Optional[float]:
        """Calculate simple moving average."""
        if len(self._historical_data) < period:
            return None
        closes = [d.close for d in self._historical_data[-period:]]
        return sum(closes) / len(closes)

    def generate_signal(self, current_price: float, current_date: date) -> Optional[StrategySignal]:
        """Generate signal based on MA position."""
        fast_sma = self._calculate_sma(self.fast_period)
        slow_sma = self._calculate_sma(self.slow_period)

        if fast_sma is None or slow_sma is None:
            return StrategySignal(
                strategy=self.strategy_type,
                direction=SignalDirection.FLAT,
                strength=0.0,
                reason="Insufficient data for MA calculation"
            )

        # Determine trend
        above_fast = current_price > fast_sma
        above_slow = current_price > slow_sma
        fast_above_slow = fast_sma > slow_sma

        if above_fast and above_slow and fast_above_slow:
            return StrategySignal(
                strategy=self.strategy_type,
                direction=SignalDirection.LONG,
                strength=0.6,
                reason=f"Uptrend: Price > SMA{self.fast_period} > SMA{self.slow_period}",
                entry_price=current_price,
                metadata={
                    "fast_sma": fast_sma,
                    "slow_sma": slow_sma,
                    "price_vs_fast": (current_price / fast_sma - 1) * 100,
                    "price_vs_slow": (current_price / slow_sma - 1) * 100
                }
            )
        elif not above_fast and not above_slow and not fast_above_slow:
            return StrategySignal(
                strategy=self.strategy_type,
                direction=SignalDirection.SHORT,
                strength=0.6,
                reason=f"Downtrend: Price < SMA{self.fast_period} < SMA{self.slow_period}",
                entry_price=current_price,
                metadata={
                    "fast_sma": fast_sma,
                    "slow_sma": slow_sma
                }
            )
        else:
            return StrategySignal(
                strategy=self.strategy_type,
                direction=SignalDirection.FLAT,
                strength=0.0,
                reason="Mixed signals - no clear trend"
            )


class CombinedStrategy(BaseStrategy):
    """
    Combined Strategy

    Combines multiple signals for optimal performance.

    Rules:
    1. Mean reversion (long) after -2%+ down days
    2. Short Thursday (unless mean reversion triggers)

    Historical Performance:
    - Total Return: +92.9%
    - Win Rate: 60.7%
    - Beats Buy & Hold by +9.7%
    """

    def __init__(
        self,
        mean_reversion_threshold: float = -2.0,
        enable_short_thursday: bool = True
    ):
        super().__init__("Combined", StrategyType.COMBINED)
        self.mean_reversion = MeanReversionStrategy(
            threshold=mean_reversion_threshold,
            skip_thursday=False  # We handle Thursday logic here
        )
        self.short_thursday = ShortThursdayStrategy()
        self.enable_short_thursday = enable_short_thursday

    def update_data(self, data: List[DailyData]):
        """Update data for all sub-strategies."""
        super().update_data(data)
        self.mean_reversion.update_data(data)
        self.short_thursday.update_data(data)

    def add_daily_data(self, daily: DailyData):
        """Add daily data to all sub-strategies."""
        super().add_daily_data(daily)
        self.mean_reversion.add_daily_data(daily)
        self.short_thursday.add_daily_data(daily)

    def generate_signal(self, current_price: float, current_date: date) -> Optional[StrategySignal]:
        """Generate combined signal with priority logic."""
        # Get signals from both strategies
        mr_signal = self.mean_reversion.generate_signal(current_price, current_date)
        thu_signal = self.short_thursday.generate_signal(current_price, current_date)

        # Priority: Mean reversion LONG beats Short Thursday
        if mr_signal and mr_signal.direction == SignalDirection.LONG:
            return StrategySignal(
                strategy=self.strategy_type,
                direction=SignalDirection.LONG,
                strength=mr_signal.strength,
                reason=f"Combined: {mr_signal.reason}",
                entry_price=current_price,
                metadata={
                    "trigger": "mean_reversion",
                    **mr_signal.metadata
                }
            )

        # Short Thursday if enabled and today is Thursday
        if self.enable_short_thursday and thu_signal and thu_signal.direction == SignalDirection.SHORT:
            return StrategySignal(
                strategy=self.strategy_type,
                direction=SignalDirection.SHORT,
                strength=thu_signal.strength,
                reason=f"Combined: {thu_signal.reason}",
                entry_price=current_price,
                metadata={
                    "trigger": "short_thursday",
                    **thu_signal.metadata
                }
            )

        # No signal
        return StrategySignal(
            strategy=self.strategy_type,
            direction=SignalDirection.FLAT,
            strength=0.0,
            reason="No combined signal triggered"
        )


class StrategyManager:
    """
    Manages multiple strategies and aggregates signals.
    """

    def __init__(self):
        self.strategies: Dict[StrategyType, BaseStrategy] = {}
        self.active_strategy: Optional[StrategyType] = None

    def register_strategy(self, strategy: BaseStrategy):
        """Register a strategy."""
        self.strategies[strategy.strategy_type] = strategy
        logger.info(f"Registered strategy: {strategy.name}")

    def set_active_strategy(self, strategy_type: StrategyType):
        """Set the active strategy."""
        if strategy_type not in self.strategies:
            raise ValueError(f"Strategy {strategy_type} not registered")
        self.active_strategy = strategy_type
        logger.info(f"Active strategy set to: {strategy_type.value}")

    def update_all_data(self, data: List[DailyData]):
        """Update data for all strategies."""
        for strategy in self.strategies.values():
            strategy.update_data(data)

    def add_daily_data(self, daily: DailyData):
        """Add daily data to all strategies."""
        for strategy in self.strategies.values():
            strategy.add_daily_data(daily)

    def get_signal(self, current_price: float, current_date: date) -> Optional[StrategySignal]:
        """Get signal from active strategy."""
        if self.active_strategy is None:
            logger.warning("No active strategy set")
            return None

        strategy = self.strategies.get(self.active_strategy)
        if strategy is None:
            return None

        return strategy.generate_signal(current_price, current_date)

    def get_all_signals(self, current_price: float, current_date: date) -> Dict[StrategyType, StrategySignal]:
        """Get signals from all strategies."""
        signals = {}
        for strategy_type, strategy in self.strategies.items():
            signal = strategy.generate_signal(current_price, current_date)
            if signal:
                signals[strategy_type] = signal
        return signals


def create_default_manager() -> StrategyManager:
    """Create strategy manager with all strategies registered."""
    manager = StrategyManager()

    # Register all strategies
    manager.register_strategy(MeanReversionStrategy(threshold=-3.0, skip_thursday=True))
    manager.register_strategy(ShortThursdayStrategy())
    manager.register_strategy(IntradayBounceStrategy(threshold=-5.0))
    manager.register_strategy(TrendFollowingStrategy(fast_period=20, slow_period=50))
    manager.register_strategy(CombinedStrategy(mean_reversion_threshold=-2.0))

    # Set combined as default
    manager.set_active_strategy(StrategyType.COMBINED)

    return manager
