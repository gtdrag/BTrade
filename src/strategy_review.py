"""
Monthly Strategy Review - Automated strategy optimization via Claude.

Runs monthly to:
1. Collect 3 months of market data (IBIT, BTC/USD, BITO)
2. Backtest current strategy parameters
3. Test alternative parameter combinations
4. Send results to Claude for analysis
5. Generate recommendations report
"""

import logging
import os
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import anthropic

from .database import get_database

logger = logging.getLogger(__name__)


# Tool definition for Claude to recommend parameter changes
PARAMETER_CHANGE_TOOL = {
    "name": "recommend_parameter_change",
    "description": (
        "Recommend a change to a strategy parameter based on backtest analysis. "
        "CRITICAL: You may ONLY recommend values that were actually tested in the "
        "parameter sensitivity analysis. Do NOT extrapolate or guess untested values. "
        "Only call this tool if backtest data strongly supports a specific tested value. "
        "Do not call if current parameters are optimal or if no tested value is clearly better."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "parameter": {
                "type": "string",
                "enum": ["mr_threshold", "reversal_threshold", "crash_threshold", "pump_threshold"],
                "description": "The parameter to change",
            },
            "current_value": {
                "type": "number",
                "description": "The current value of the parameter",
            },
            "recommended_value": {
                "type": "number",
                "description": (
                    "The recommended new value. MUST be one of the values from the "
                    "parameter sensitivity tests shown above - never extrapolate."
                ),
            },
            "backtest_return": {
                "type": "number",
                "description": "The exact return percentage from the backtest for this value",
            },
            "expected_improvement": {
                "type": "string",
                "description": "Expected improvement based on backtest (e.g., '+5% return vs current')",
            },
            "confidence": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Confidence level - high only if backtest clearly shows improvement",
            },
            "reason": {
                "type": "string",
                "description": "Brief explanation referencing the specific backtest results",
            },
        },
        "required": [
            "parameter",
            "current_value",
            "recommended_value",
            "backtest_return",
            "reason",
            "confidence",
        ],
    },
}

# Tool for Claude to flag things to watch/monitor for next review
WATCH_ITEM_TOOL = {
    "name": "flag_watch_item",
    "description": (
        "Flag something important to monitor in future reviews. Use this to create "
        "a record of patterns, concerns, or observations that should be tracked over time. "
        "These items will be shown in the next review so you can follow up on them."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": ["pattern", "risk", "opportunity", "anomaly", "prediction"],
                "description": "Type of watch item",
            },
            "description": {
                "type": "string",
                "description": "What to watch for (be specific and measurable)",
            },
            "metric": {
                "type": "string",
                "description": "The specific metric or indicator to track (e.g., 'Thursday win rate', 'drawdown frequency')",
            },
            "current_value": {
                "type": "string",
                "description": "Current state of this metric (e.g., '45% win rate', '3 drawdowns > 5%')",
            },
            "threshold": {
                "type": "string",
                "description": "When should this trigger concern? (e.g., 'if drops below 40%', 'if exceeds 5 occurrences')",
            },
            "priority": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "How important is this to monitor",
            },
        },
        "required": ["category", "description", "metric", "priority"],
    },
}

# Claude prompt for strategy review
STRATEGY_REVIEW_PROMPT = """You are a quantitative trading strategist reviewing the performance of a Bitcoin ETF trading strategy.

## Current Strategy
The bot trades IBIT (Bitcoin ETF) using leveraged ETFs:
- BITU (2x long) for bullish signals
- SBIT (2x inverse) for bearish signals

**Active Strategies:**
1. **Mean Reversion**: Buy BITU after IBIT drops ≥{mr_threshold}% previous day
   - Filtered by BTC overnight movement (only trade if BTC up overnight)
2. **Position Reversal**: If BITU position drops ≥{reversal_threshold}% intraday, flip to SBIT
3. **Crash Day**: Buy SBIT if IBIT drops ≥{crash_threshold}% intraday
4. **Pump Day**: Buy BITU if IBIT rises ≥{pump_threshold}% intraday

All positions close at 3:55 PM ET (never hold overnight).

{previous_review_context}

## Recent Performance Data (Last 3 Months)

### Backtest Results - Current Parameters
{current_backtest}

### Parameter Sensitivity Analysis
{parameter_tests}

**Tested Values (you may ONLY recommend from these):**
{tested_values}

### Raw Market Data Summary
{market_summary}

## Your Task

Analyze this data and provide:

1. **Performance Assessment** (2-3 sentences)
   - Is the strategy working? What's the trend?

2. **Follow-up on Previous Watch Items** (if any exist above)
   - Check the status of each flagged item
   - Has the concern materialized or resolved?

3. **Parameter Recommendations** (if any)
   - Should we adjust thresholds? Be specific with numbers.
   - Only recommend changes if data strongly supports it.

4. **Pattern Observations**
   - Any new patterns emerging in the data?
   - Day-of-week effects, time-of-day patterns, cross-market correlations?

5. **Risk Concerns**
   - Any warning signs? Increasing drawdowns? Deteriorating win rate?

6. **Action Items** (bullet list)
   - Specific, actionable recommendations
   - Include "NO CHANGES NEEDED" if current parameters are optimal

Format your response as a clear report suitable for a Telegram message (use markdown, keep it under 2000 characters).

**CRITICAL RULES FOR RECOMMENDATIONS:**
1. You may ONLY recommend values that appear in the "Tested Values" list above
2. Do NOT extrapolate or guess values that weren't tested (e.g., if -1.5 and -2.0 were tested, do NOT recommend -1.0)
3. When using the `recommend_parameter_change` tool, the `backtest_return` MUST match the exact return shown in the sensitivity analysis
4. If no tested value clearly outperforms the current setting, respond with "NO CHANGES NEEDED"

**WATCH ITEMS:**
Use the `flag_watch_item` tool to flag anything important to monitor in future reviews:
- Emerging patterns that need more data to confirm
- Metrics that are approaching concerning thresholds
- Anomalies worth tracking over time
- Predictions you want to verify next month
These items will be shown to you in the next review so you can follow up on them.
"""


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


class StrategyReviewer:
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

        # Default strategy parameters
        self.current_params = {
            "mr_threshold": -2.0,
            "reversal_threshold": -2.0,
            "crash_threshold": -2.0,
            "pump_threshold": 2.0,
        }

        # Load any persisted parameters from database (override defaults)
        saved_params = self.db.get_all_strategy_params()
        for param, value in saved_params.items():
            if param in self.current_params:
                self.current_params[param] = value
                logger.info(f"Loaded saved parameter: {param} = {value}")

    def _fetch_market_data(self, days: int = 90) -> Dict[str, List[Dict]]:
        """Fetch market data from Alpaca."""
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        alpaca_key = os.environ.get("ALPACA_API_KEY")
        alpaca_secret = os.environ.get("ALPACA_SECRET_KEY")

        if not alpaca_key or not alpaca_secret:
            logger.warning("Alpaca credentials not set")
            return {}

        client = StockHistoricalDataClient(alpaca_key, alpaca_secret)

        end_date = datetime.now()
        start_date = end_date - timedelta(days=days + 10)

        data = {}

        # Fetch IBIT
        try:
            request = StockBarsRequest(
                symbol_or_symbols=["IBIT"],
                timeframe=TimeFrame.Day,
                start=start_date,
                end=end_date,
            )
            bars = client.get_stock_bars(request)

            # BarSet doesn't support 'in' check properly - use try/except
            try:
                ibit_bars = bars["IBIT"]
            except KeyError:
                ibit_bars = []

            data["ibit"] = [
                {
                    "date": bar.timestamp.strftime("%Y-%m-%d"),
                    "weekday": bar.timestamp.weekday(),
                    "open": float(bar.open),
                    "high": float(bar.high),
                    "low": float(bar.low),
                    "close": float(bar.close),
                }
                for bar in ibit_bars
            ]
        except Exception as e:
            logger.error(f"Failed to fetch IBIT data: {e}")
            data["ibit"] = []

        return data

    def _run_backtest(
        self,
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

    def _format_backtest_result(self, result: BacktestResult) -> str:
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

    def _generate_market_summary(self, data: List[Dict]) -> str:
        """Generate market summary statistics."""
        if not data:
            return "No data available"

        returns = []
        for i in range(1, len(data)):
            ret = ((data[i]["close"] - data[i - 1]["close"]) / data[i - 1]["close"]) * 100
            returns.append(ret)

        if not returns:
            return "Insufficient data"

        # Day of week analysis
        dow_returns = {i: [] for i in range(5)}
        for i, day in enumerate(data[1:], 1):
            if day["weekday"] < 5:
                ret = ((day["close"] - day["open"]) / day["open"]) * 100
                dow_returns[day["weekday"]].append(ret)

        dow_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        dow_summary = []
        for i, name in enumerate(dow_names):
            if dow_returns[i]:
                avg = statistics.mean(dow_returns[i])
                dow_summary.append(f"  {name}: {avg:+.2f}% avg")

        # Big move days
        big_drops = sum(1 for r in returns if r <= -2.0)
        big_pumps = sum(1 for r in returns if r >= 2.0)

        return (
            f"**Period**: {data[0]['date']} to {data[-1]['date']} ({len(data)} days)\n"
            f"**Total Return**: {sum(returns):+.2f}%\n"
            f"**Volatility**: {statistics.stdev(returns):.2f}% daily\n"
            f"**Big Drops (≥-2%)**: {big_drops} days\n"
            f"**Big Pumps (≥+2%)**: {big_pumps} days\n\n"
            f"**Day-of-Week Performance**:\n" + "\n".join(dow_summary)
        )

    def _build_previous_review_context(self, previous_reviews: List[Dict]) -> str:
        """Build context section from previous reviews for recursive learning."""
        if not previous_reviews:
            return ""

        sections = ["## Previous Review Context (Memory)"]

        for i, review in enumerate(previous_reviews):
            review_date = review.get("review_date", "Unknown")
            backtest_return = review.get("backtest_return", 0)
            summary = review.get("summary", "No summary available")

            sections.append(f"\n### Review from {review_date}")
            sections.append(f"**Performance at that time**: {backtest_return:+.2f}% return")
            sections.append(f"**Summary**: {summary[:300]}")

            # Include recommendations that were made
            recs = review.get("recommendations", [])
            if recs:
                sections.append("\n**Recommendations made:**")
                for rec in recs:
                    sections.append(f"- {rec.get('param')}: {rec.get('from')} → {rec.get('to')}")

            # Include watch items that were flagged
            watch_items = review.get("watch_items", [])
            if watch_items:
                sections.append("\n**Watch items flagged (FOLLOW UP ON THESE):**")
                for item in watch_items:
                    status = "✓ Resolved" if item.get("resolved") else "⚠️ Active"
                    sections.append(
                        f"- [{item.get('category', 'unknown').upper()}] {item.get('description', 'No description')}\n"
                        f"  Metric: {item.get('metric', 'N/A')} | "
                        f"Current: {item.get('current_value', 'N/A')} | "
                        f"Threshold: {item.get('threshold', 'N/A')} | "
                        f"Status: {status}"
                    )

        return "\n".join(sections)

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
                recommended_params={},
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

        # 3. Test alternative parameters with DYNAMIC ranges around current values
        parameter_tests = []
        tested_values: Dict[str, List[float]] = {
            "mr_threshold": [],
            "reversal_threshold": [],
        }

        # Generate test values around current MR threshold (±0.5, ±1.0)
        current_mr = self.current_params["mr_threshold"]
        mr_test_values = sorted(
            set(
                [current_mr - 1.0, current_mr - 0.5, current_mr, current_mr + 0.5, current_mr + 1.0]
            )
        )
        # Keep values in reasonable range (not too close to 0, not too extreme)
        mr_test_values = [v for v in mr_test_values if -4.0 <= v <= -0.5]
        tested_values["mr_threshold"] = mr_test_values

        # Generate test values around current reversal threshold
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

        # Test MR thresholds (using CURRENT reversal as baseline)
        for mr_thresh in mr_test_values:
            result = self._run_backtest(
                ibit_data,
                mr_threshold=mr_thresh,
                reversal_threshold=self.current_params["reversal_threshold"],  # Use current!
                name=f"MR @ {mr_thresh}%",
            )
            parameter_tests.append(result)

        # Test reversal thresholds (using CURRENT MR as baseline)
        for rev_thresh in rev_test_values:
            result = self._run_backtest(
                ibit_data,
                mr_threshold=self.current_params["mr_threshold"],  # Use current!
                reversal_threshold=rev_thresh,
                name=f"Reversal @ {rev_thresh}%",
            )
            parameter_tests.append(result)

        # 4. Format data for Claude
        current_backtest = self._format_backtest_result(current_result)
        param_tests_str = "\n\n".join(self._format_backtest_result(r) for r in parameter_tests)
        market_summary = self._generate_market_summary(ibit_data)

        # Build explicit tested values list for Claude
        tested_values_str = (
            f"- mr_threshold: {tested_values['mr_threshold']}\n"
            f"- reversal_threshold: {tested_values['reversal_threshold']}"
        )

        # 4.5 Load previous reviews for context (recursive memory)
        previous_reviews = self.db.get_previous_reviews(limit=2)
        previous_review_context = self._build_previous_review_context(previous_reviews)

        # 5. Build prompt
        prompt = STRATEGY_REVIEW_PROMPT.format(
            mr_threshold=self.current_params["mr_threshold"],
            reversal_threshold=self.current_params["reversal_threshold"],
            crash_threshold=self.current_params["crash_threshold"],
            pump_threshold=self.current_params["pump_threshold"],
            previous_review_context=previous_review_context,
            current_backtest=current_backtest,
            parameter_tests=param_tests_str,
            tested_values=tested_values_str,
            market_summary=market_summary,
        )

        # 6. Call Claude with tool use
        logger.info("Sending data to Claude for analysis...")

        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
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
                    "model": "claude-sonnet-4-20250514",
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
                market_conditions=market_summary,
            )
            logger.info(f"Saved strategy review #{review_id} with {len(watch_items)} watch items")

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

        # Validate the parameter exists
        valid_params = ["mr_threshold", "reversal_threshold", "crash_threshold", "pump_threshold"]
        if param not in valid_params:
            logger.error(f"Invalid parameter: {param}")
            return False

        try:
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
            return False


# Singleton instance
_reviewer: Optional[StrategyReviewer] = None

# Store pending recommendations for Telegram approval flow
_pending_recommendations: List[ParameterRecommendation] = []


def get_strategy_reviewer() -> StrategyReviewer:
    """Get or create the strategy reviewer singleton."""
    global _reviewer
    if _reviewer is None:
        _reviewer = StrategyReviewer()
    return _reviewer


def get_pending_recommendations() -> List[ParameterRecommendation]:
    """Get recommendations pending user approval."""
    return _pending_recommendations


def set_pending_recommendations(recommendations: List[ParameterRecommendation]):
    """Store recommendations for user approval via Telegram."""
    global _pending_recommendations
    _pending_recommendations = recommendations


def clear_pending_recommendations():
    """Clear pending recommendations."""
    global _pending_recommendations
    _pending_recommendations = []
