"""
Strategy Review Package.

Monthly strategy review using Claude for analysis.
Combines market data, backtesting, and AI-powered recommendations.
"""

from .config import (
    PARAMETER_CHANGE_TOOL,
    STRATEGY_PARAMETERS,
    STRATEGY_REVIEW_PROMPT,
    WATCH_ITEM_TOOL,
)
from .models import (
    BacktestResult,
    ParameterRecommendation,
    StrategyRecommendation,
)
from .reviewer import StrategyReviewer

__all__ = [
    # Main class
    "StrategyReviewer",
    # Models
    "BacktestResult",
    "ParameterRecommendation",
    "StrategyRecommendation",
    # Config
    "STRATEGY_PARAMETERS",
    "PARAMETER_CHANGE_TOOL",
    "WATCH_ITEM_TOOL",
    "STRATEGY_REVIEW_PROMPT",
]
