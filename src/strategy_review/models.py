"""
Data models for the strategy review module.

Contains dataclasses for backtest results, recommendations, and review reports.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class BacktestResult:
    """Result from a strategy backtest."""

    name: str
    total_return_pct: float
    total_trades: int
    winning_trades: int
    win_rate: float
    avg_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float


@dataclass
class ParameterRecommendation:
    """A single parameter change recommendation from Claude."""

    parameter: str  # e.g., "mr_threshold"
    current_value: float
    recommended_value: float
    reason: str
    confidence: str  # "low", "medium", "high"
    backtest_return: Optional[float] = None  # Return % from actual backtest
    expected_improvement: Optional[str] = None
    applied: bool = False  # True if user approved and applied

    def to_display_name(self) -> str:
        """Get human-readable parameter name."""
        names = {
            "mr_threshold": "Mean Reversion Threshold",
            "reversal_threshold": "Position Reversal Threshold",
            "crash_threshold": "Crash Day Threshold",
            "pump_threshold": "Pump Day Threshold",
        }
        return names.get(self.parameter, self.parameter)


@dataclass
class StrategyRecommendation:
    """Recommendation from Claude analysis."""

    summary: str  # Brief summary
    full_report: str  # Full markdown report
    has_recommendations: bool  # True if changes suggested
    recommendations: List[ParameterRecommendation] = field(default_factory=list)
    risk_level: str = "low"  # "low", "medium", "high"
    timestamp: str = ""

    # Legacy field for backwards compatibility
    @property
    def recommended_params(self) -> Dict[str, float]:
        """Get recommended params as dict (legacy compatibility)."""
        return {r.parameter: r.recommended_value for r in self.recommendations}
