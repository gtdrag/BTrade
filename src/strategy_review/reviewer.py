"""
Main Strategy Reviewer class.

Monthly strategy review using Claude for analysis.
Combines market data, backtesting, and AI-powered recommendations.
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Dict, List

import anthropic

from ..database import get_database
from ..error_alerting import AlertSeverity, alert_error
from .backtesting import BacktestMixin
from .config import (
    PARAMETER_CHANGE_TOOL,
    STRATEGY_PARAMETERS,
    STRATEGY_REVIEW_PROMPT,
    WATCH_ITEM_TOOL,
)
from .market_data import MarketDataMixin
from .models import ParameterRecommendation, StrategyRecommendation

logger = logging.getLogger(__name__)


class StrategyReviewer(MarketDataMixin, BacktestMixin):
    """
    Monthly strategy reviewer using Claude for analysis.

    Usage:
        reviewer = StrategyReviewer()
        report = await reviewer.run_monthly_review()
        # Send report via Telegram
    """

    def __init__(self):
        self.api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.db = get_database()

        # Default strategy parameters (all configurable params)
        self.current_params = {
            # Threshold parameters
            "mr_threshold": -2.0,
            "reversal_threshold": -2.0,
            "crash_threshold": -2.0,
            "pump_threshold": 2.0,
            # Enable/disable flags
            "ten_am_dump_enabled": True,
            "mean_reversion_enabled": True,
            "crash_day_enabled": True,
            "pump_day_enabled": True,
            "btc_overnight_filter_enabled": True,
            # Priority mode
            "signal_priority": "ten_am_first",  # or "mean_reversion_first"
        }

        # Load any persisted parameters from database (override defaults)
        saved_params = self.db.get_all_strategy_params()
        for param, value in saved_params.items():
            if param in self.current_params:
                self.current_params[param] = value
                logger.info(f"Loaded saved parameter: {param} = {value}")

    async def run_monthly_review(self) -> StrategyRecommendation:
        """
        Run the monthly strategy review.

        Returns:
            StrategyRecommendation with Claude's analysis
        """
        logger.info("Starting monthly strategy review...")

        # 1. Fetch market data
        market_data = self._fetch_market_data(days=90)
        ibit_data = market_data.get("ibit", [])

        if not ibit_data:
            return StrategyRecommendation(
                summary="Review failed - no market data available",
                full_report="Unable to fetch market data from Alpaca.",
                has_recommendations=False,
                recommendations=[],
                risk_level="unknown",
                timestamp=datetime.now().isoformat(),
            )

        # 2. Run current strategy backtest
        current_result = self._run_backtest(
            ibit_data,
            mr_threshold=self.current_params["mr_threshold"],
            reversal_threshold=self.current_params["reversal_threshold"],
            name="Current Strategy",
        )

        # 3. Comprehensive parameter testing
        parameter_tests = []
        tested_values: Dict[str, Any] = {}

        # 3a. Test threshold parameters
        current_mr = self.current_params["mr_threshold"]
        mr_test_values = sorted(
            set(
                [current_mr - 1.0, current_mr - 0.5, current_mr, current_mr + 0.5, current_mr + 1.0]
            )
        )
        mr_test_values = [v for v in mr_test_values if -4.0 <= v <= -0.5]
        tested_values["mr_threshold"] = mr_test_values

        current_rev = self.current_params["reversal_threshold"]
        rev_test_values = sorted(
            set(
                [
                    current_rev - 1.0,
                    current_rev - 0.5,
                    current_rev,
                    current_rev + 0.5,
                    current_rev + 1.0,
                ]
            )
        )
        rev_test_values = [v for v in rev_test_values if -4.0 <= v <= -0.5]
        tested_values["reversal_threshold"] = rev_test_values

        for mr_thresh in mr_test_values:
            result = self._run_backtest(
                ibit_data,
                mr_threshold=mr_thresh,
                reversal_threshold=self.current_params["reversal_threshold"],
                name=f"MR @ {mr_thresh}%",
            )
            parameter_tests.append(result)

        for rev_thresh in rev_test_values:
            result = self._run_backtest(
                ibit_data,
                mr_threshold=self.current_params["mr_threshold"],
                reversal_threshold=rev_thresh,
                name=f"Reversal @ {rev_thresh}%",
            )
            parameter_tests.append(result)

        # 3b. Test strategy enable/disable combinations
        # Run in thread pool to avoid blocking async event loop
        try:
            strategy_tests = await asyncio.to_thread(
                self._run_strategy_combination_tests, ibit_data
            )
        except Exception as e:
            logger.warning(f"Strategy combination tests failed: {e}")
            alert_error(
                AlertSeverity.WARNING,
                f"Async strategy combination tests failed: {e}",
                category="async_strategy_tests",
            )
            strategy_tests = {}
        tested_values["ten_am_dump_enabled"] = [True, False]
        tested_values["mean_reversion_enabled"] = [True, False]
        tested_values["signal_priority"] = ["ten_am_first", "mean_reversion_first"]

        # 3c. Format strategy test results
        strategy_tests_str = self._format_strategy_tests(strategy_tests)

        # 4. Format data for Claude
        current_backtest = self._format_backtest_result(current_result)
        param_tests_str = "\n\n".join(self._format_backtest_result(r) for r in parameter_tests)
        market_summary = self._generate_market_summary(ibit_data)

        # Build explicit tested values list for Claude
        tested_values_str = (
            f"**Threshold Parameters (floats):**\n"
            f"- mr_threshold: {tested_values['mr_threshold']}\n"
            f"- reversal_threshold: {tested_values['reversal_threshold']}\n\n"
            f"**Strategy Enable/Disable (booleans):**\n"
            f"- ten_am_dump_enabled: {tested_values.get('ten_am_dump_enabled', [True, False])}\n"
            f"- mean_reversion_enabled: "
            f"{tested_values.get('mean_reversion_enabled', [True, False])}\n\n"
            f"**Priority Mode (enum):**\n"
            f"- signal_priority: "
            f"{tested_values.get('signal_priority', ['ten_am_first', 'mean_reversion_first'])}"
        )

        # 4.5 Detect market regime
        regime_data = self._detect_market_regime(ibit_data)
        market_regime_str = self._format_regime_context(regime_data)
        logger.info(
            f"Market regime detected: {regime_data['regime']} "
            f"(confidence: {regime_data['confidence']})"
        )

        # 4.6 Load previous reviews for context (recursive memory)
        previous_reviews = self.db.get_previous_reviews(limit=2)
        previous_review_context = self._build_previous_review_context(previous_reviews)

        # 5. Build prompt
        prompt = STRATEGY_REVIEW_PROMPT.format(
            mr_threshold=self.current_params["mr_threshold"],
            reversal_threshold=self.current_params["reversal_threshold"],
            crash_threshold=self.current_params["crash_threshold"],
            pump_threshold=self.current_params["pump_threshold"],
            ten_am_dump_enabled=self.current_params["ten_am_dump_enabled"],
            mean_reversion_enabled=self.current_params["mean_reversion_enabled"],
            signal_priority=self.current_params["signal_priority"],
            market_regime=market_regime_str,
            previous_review_context=previous_review_context,
            current_backtest=current_backtest,
            parameter_tests=param_tests_str,
            strategy_tests=strategy_tests_str,
            tested_values=tested_values_str,
            market_summary=market_summary,
        )

        # 6. Call Claude with tool use
        logger.info("Sending data to Claude for analysis...")

        try:
            response = self.client.messages.create(
                model="claude-opus-4-5-20251101",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
                tools=[PARAMETER_CHANGE_TOOL, WATCH_ITEM_TOOL],
            )

            # Parse response - extract text, recommendations, and watch items
            full_report = ""
            recommendations: List[ParameterRecommendation] = []
            watch_items: List[Dict[str, Any]] = []

            for block in response.content:
                if block.type == "text":
                    full_report += block.text
                elif block.type == "tool_use" and block.name == "flag_watch_item":
                    # Parse watch item
                    item_data = block.input
                    watch_items.append(
                        {
                            "category": item_data.get("category"),
                            "description": item_data.get("description"),
                            "metric": item_data.get("metric"),
                            "current_value": item_data.get("current_value"),
                            "threshold": item_data.get("threshold"),
                            "priority": item_data.get("priority", "medium"),
                            "resolved": False,
                        }
                    )
                    logger.info(
                        f"Claude flagged watch item: [{item_data.get('category')}] "
                        f"{item_data.get('description')}"
                    )
                elif block.type == "tool_use" and block.name == "recommend_parameter_change":
                    # Parse the structured recommendation
                    rec_data = block.input
                    rec = ParameterRecommendation(
                        parameter=rec_data["parameter"],
                        current_value=rec_data["current_value"],
                        recommended_value=rec_data["recommended_value"],
                        reason=rec_data["reason"],
                        confidence=rec_data.get("confidence", "medium"),
                        backtest_return=rec_data.get("backtest_return"),
                        expected_improvement=rec_data.get("expected_improvement"),
                    )

                    # Validate: recommended value should be in tested values
                    param = rec.parameter
                    if param in tested_values:
                        if rec.recommended_value not in tested_values[param]:
                            logger.warning(
                                f"Claude recommended untested value {rec.recommended_value} "
                                f"for {param}. Tested values: {tested_values[param]}"
                            )
                            # Still include it but log the warning
                            rec.confidence = "low"  # Downgrade confidence

                    recommendations.append(rec)
                    logger.info(
                        f"Claude recommends: {rec.parameter} "
                        f"{rec.current_value} → {rec.recommended_value} "
                        f"(backtest: {rec.backtest_return}%, confidence: {rec.confidence})"
                    )

            has_recs = len(recommendations) > 0

            # Log to database
            self.db.log_event(
                "STRATEGY_REVIEW",
                "Monthly strategy review completed",
                {
                    "model": "claude-opus-4-5-20251101",
                    "current_return": current_result.total_return_pct,
                    "has_recommendations": has_recs,
                    "num_recommendations": len(recommendations),
                    "recommendations": [
                        {"param": r.parameter, "from": r.current_value, "to": r.recommended_value}
                        for r in recommendations
                    ],
                    "num_watch_items": len(watch_items),
                    "response_length": len(full_report),
                    "timestamp": datetime.now().isoformat(),
                },
            )

            # Create summary (first paragraph)
            summary = full_report.split("\n\n")[0][:200] if full_report else "Review complete"

            # Build regime header to prepend to report (so user always sees detected regime)
            regime_emoji = {
                "strong_bull": "🚀",
                "bull": "📈",
                "neutral": "➡️",
                "bear": "📉",
                "strong_bear": "💥",
                "unknown": "❓",
            }
            regime_name = regime_data.get("regime", "unknown")
            regime_conf = regime_data.get("confidence", "low")
            indicators = regime_data.get("indicators", {})

            regime_header = (
                f"**Market Regime**: {regime_emoji.get(regime_name, '')} {regime_name.upper()} "
                f"({regime_conf} confidence)\n"
                f"📊 MA Slope: {indicators.get('ma_20_slope_pct', 0):+.1f}% | "
                f"Vol: {indicators.get('volatility_20d', 0):.1f}% | "
                f"Last 10d: {indicators.get('up_days_last_10', 0)}↑/"
                f"{indicators.get('down_days_last_10', 0)}↓"
            )
            if indicators.get("volatility_compressed"):
                regime_header += " | ⚠️ Vol Compressed"
            regime_header += "\n\n---\n\n"

            # Prepend regime header to full report
            full_report = regime_header + full_report

            # Save full review for recursive memory
            review_id = self.db.save_strategy_review(
                full_report=full_report,
                summary=summary,
                current_params=self.current_params.copy(),
                backtest_return=current_result.total_return_pct,
                recommendations=[
                    {"param": r.parameter, "from": r.current_value, "to": r.recommended_value}
                    for r in recommendations
                ],
                watch_items=watch_items,
                market_regime=regime_data,
                market_conditions=market_summary,
            )
            logger.info(
                f"Saved strategy review #{review_id} with {len(watch_items)} watch items, "
                f"regime: {regime_data['regime']}"
            )

            return StrategyRecommendation(
                summary=summary,
                full_report=full_report,
                has_recommendations=has_recs,
                recommendations=recommendations,
                risk_level="high"
                if any(r.confidence == "high" for r in recommendations)
                else "medium"
                if has_recs
                else "low",
                timestamp=datetime.now().isoformat(),
            )

        except Exception as e:
            logger.error(f"Claude API call failed: {e}")
            alert_error(
                AlertSeverity.CRITICAL,
                f"Strategy review Claude API call failed: {e}",
                {"model": "claude-sonnet-4-20250514"},
                category="claude_api_review",
            )
            return StrategyRecommendation(
                summary=f"Review failed: {e}",
                full_report=f"Error calling Claude API: {e}",
                has_recommendations=False,
                recommendations=[],
                risk_level="unknown",
                timestamp=datetime.now().isoformat(),
            )

    def apply_recommendation(self, recommendation: ParameterRecommendation) -> bool:
        """
        Apply a parameter recommendation to the strategy config.

        This updates the live strategy configuration file.

        Args:
            recommendation: The recommendation to apply

        Returns:
            True if applied successfully
        """
        param = recommendation.parameter
        new_value = recommendation.recommended_value

        # Validate the parameter exists (use STRATEGY_PARAMETERS as source of truth)
        if param not in STRATEGY_PARAMETERS:
            logger.error(f"Invalid parameter: {param}")
            return False

        # Type coercion based on parameter definition
        param_def = STRATEGY_PARAMETERS[param]
        param_type = param_def["type"]

        try:
            if param_type == "float":
                new_value = float(new_value)
            elif param_type == "bool":
                # Handle string "true"/"false" from JSON
                if isinstance(new_value, str):
                    new_value = new_value.lower() == "true"
                else:
                    new_value = bool(new_value)
            elif param_type == "enum":
                # Validate enum value
                valid_options = param_def.get("options", [])
                if new_value not in valid_options:
                    logger.error(
                        f"Invalid enum value for {param}: {new_value}. "
                        f"Must be one of {valid_options}"
                    )
                    return False
            # string type doesn't need conversion

            # Update our internal tracking
            old_value = self.current_params[param]
            self.current_params[param] = new_value
            recommendation.applied = True

            # Persist to database for survival across restarts
            self.db.save_strategy_param(
                param_name=param,
                param_value=new_value,
                previous_value=old_value,
                reason=recommendation.reason,
                confidence=recommendation.confidence,
            )

            # Log the change
            self.db.log_event(
                "PARAMETER_CHANGE",
                f"Applied recommendation: {param} → {new_value}",
                {
                    "parameter": param,
                    "old_value": old_value,
                    "new_value": new_value,
                    "param_type": param_type,
                    "reason": recommendation.reason,
                    "confidence": recommendation.confidence,
                    "timestamp": datetime.now().isoformat(),
                },
            )

            logger.info(
                f"Applied parameter change: {param} = {new_value} "
                f"(was {old_value}) - persisted to database"
            )

            return True

        except Exception as e:
            logger.error(f"Failed to apply recommendation: {e}")
            alert_error(
                AlertSeverity.WARNING,
                f"Failed to apply recommendation: {e}",
                {"parameter": param, "new_value": str(new_value)},
                category="apply_recommendation",
            )
            return False
