"""
Historical Simulator - Sandboxed time-machine simulation of the recursive AI loop.

Simulates running the trading bot over a historical period, including:
- AI-driven strategy reviews at regular intervals
- Parameter evolution based on AI recommendations (auto-accepted)
- Comparison of static vs evolved parameters
- Complete isolation from production database

Usage:
    simulator = HistoricalSimulator()
    report = await simulator.run_simulation(
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 12, 31),
        review_interval_days=14,
    )
"""

import logging
import os
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import anthropic

logger = logging.getLogger(__name__)


# Simulation-specific Claude prompt (similar to strategy_review but clearer about simulation context)
SIMULATION_REVIEW_PROMPT = """You are a quantitative trading strategist reviewing the performance of a Bitcoin ETF trading strategy.

## SIMULATION CONTEXT
This is a HISTORICAL SIMULATION. You are analyzing data as if you were reviewing on {review_date}.
You can only see data up to this date - you have no knowledge of what happens after.

## Current Strategy Parameters
- Mean Reversion Threshold: {mr_threshold}%
- Position Reversal Threshold: {reversal_threshold}%
- Crash Day Threshold: {crash_threshold}%
- Pump Day Threshold: {pump_threshold}%

{market_regime}

{previous_review_context}

## Backtest Results - Current Parameters (last {lookback_days} days)
{current_backtest}

## Parameter Sensitivity Analysis
{parameter_tests}

**Tested Values (you may ONLY recommend from these):**
{tested_values}

## Market Data Summary
{market_summary}

## Your Task

Analyze this data and provide:

1. **Performance Assessment** (2-3 sentences)
2. **Parameter Recommendations** (if any) - only if data strongly supports
3. **Watch Items** - flag patterns to monitor

Keep response under 500 characters (this is a simulation, brevity is key).

**CRITICAL RULES:**
1. Only recommend values from the "Tested Values" list
2. If current parameters are optimal, say "NO CHANGES NEEDED"
"""


@dataclass
class SimulationReview:
    """A single review cycle in the simulation."""

    review_date: datetime
    review_number: int
    params_before: Dict[str, float]
    params_after: Dict[str, float]
    backtest_return: float
    recommendations: List[Dict[str, Any]]
    watch_items: List[Dict[str, Any]]
    summary: str
    market_regime: str
    regime_confidence: str


@dataclass
class SimulationResult:
    """Complete simulation results."""

    start_date: datetime
    end_date: datetime
    initial_params: Dict[str, float]
    final_params: Dict[str, float]
    reviews: List[SimulationReview]
    static_performance: float  # Backtest with initial params over full period
    evolved_performance: float  # Backtest with evolved params
    total_api_calls: int
    estimated_cost: float

    def param_changes_count(self) -> int:
        """Count total parameter changes made."""
        count = 0
        for review in self.reviews:
            count += len(review.recommendations)
        return count

    @staticmethod
    def _escape_markdown(text: str) -> str:
        """Escape special Telegram markdown characters."""
        # Characters that need escaping in Telegram Markdown
        special_chars = [
            "_",
            "*",
            "[",
            "]",
            "(",
            ")",
            "~",
            "`",
            ">",
            "#",
            "+",
            "-",
            "=",
            "|",
            "{",
            "}",
            ".",
            "!",
        ]
        for char in special_chars:
            text = text.replace(char, f"\\{char}")
        return text

    def format_report(self) -> str:
        """Generate formatted simulation report for Telegram."""
        lines = [
            "📊 *HISTORICAL SIMULATION REPORT*",
            f"Period: {self.start_date.strftime('%Y-%m-%d')} → {self.end_date.strftime('%Y-%m-%d')}",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "*PARAMETER EVOLUTION*",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]

        # Show initial vs final
        for param in self.initial_params:
            initial = self.initial_params[param]
            final = self.final_params[param]
            if initial != final:
                lines.append(f"• {param}: {initial} → {final}")
            else:
                lines.append(f"• {param}: {initial} (unchanged)")

        lines.extend(
            [
                "",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "*REVIEW LOG*",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            ]
        )

        # Handle case when no reviews ran
        if not self.reviews:
            lines.append("\n⚠️ *No reviews executed*")
            lines.append("Possible reasons:")
            lines.append("• No market data available for this period")
            lines.append("• IBIT ETF launched Jan 2024 - earlier dates won't work")
            lines.append("• Period too short (need 14+ days)")
            lines.extend(
                [
                    "",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    f"API Calls: {self.total_api_calls}",
                    f"Est. Cost: ${self.estimated_cost:.2f}",
                ]
            )
            return "\n".join(lines)

        # Show each review
        for review in self.reviews:
            date_str = review.review_date.strftime("%b %d")
            regime_emoji = {
                "strong_bull": "🚀",
                "bull": "📈",
                "neutral": "➡️",
                "bear": "📉",
                "strong_bear": "💥",
            }.get(review.market_regime, "❓")

            lines.append(f"\n*Review #{review.review_number}* ({date_str}) {regime_emoji}")
            lines.append(f"  Return: {review.backtest_return:+.1f}%")

            if review.recommendations:
                for rec in review.recommendations:
                    param = rec.get("parameter", "?")
                    old = rec.get("old_value", "?")
                    new = rec.get("new_value", "?")
                    conf = rec.get("confidence", "?")
                    lines.append(f"  → {param}: {old} → {new} \\[{conf}\\]")
                    reason = rec.get("reason", "")
                    if reason:
                        # Truncate long reasons and escape markdown
                        reason = reason[:60] + "..." if len(reason) > 60 else reason
                        reason = self._escape_markdown(reason)
                        lines.append(f"    {reason}")
            else:
                lines.append("  → No changes")

            if review.watch_items:
                for item in review.watch_items:
                    cat = item.get("category", "?")
                    desc = item.get("description", "?")[:40]
                    desc = self._escape_markdown(desc)
                    lines.append(f"  ⚠️ \\[{cat}\\] {desc}")

        lines.extend(
            [
                "",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "*PERFORMANCE COMPARISON*",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"Static params:  {self.static_performance:+.1f}%",
                f"Evolved params: {self.evolved_performance:+.1f}%",
            ]
        )

        diff = self.evolved_performance - self.static_performance
        if diff > 0:
            lines.append(f"*Improvement:   +{diff:.1f}%* ✅")
        elif diff < 0:
            lines.append(f"*Degradation:   {diff:.1f}%* ❌")
        else:
            lines.append("*No difference*")

        lines.extend(
            [
                "",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"API Calls: {self.total_api_calls}",
                f"Est. Cost: ${self.estimated_cost:.2f}",
                f"Changes Made: {self.param_changes_count()}",
            ]
        )

        return "\n".join(lines)


class HistoricalSimulator:
    """
    Sandboxed historical simulation of the recursive AI strategy optimization loop.

    This runs completely isolated from production:
    - No database writes
    - No parameter persistence
    - Pure simulation with read-only market data access
    """

    # Tool definitions (same as strategy_review.py but for simulation)
    PARAMETER_CHANGE_TOOL = {
        "name": "recommend_parameter_change",
        "description": (
            "Recommend a change to a strategy parameter based on backtest analysis. "
            "CRITICAL: Only recommend values that were actually tested."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "parameter": {
                    "type": "string",
                    "enum": [
                        "mr_threshold",
                        "reversal_threshold",
                        "crash_threshold",
                        "pump_threshold",
                    ],
                    "description": "The parameter to change",
                },
                "current_value": {
                    "type": "number",
                    "description": "The current value",
                },
                "recommended_value": {
                    "type": "number",
                    "description": "The recommended new value (must be from tested values)",
                },
                "backtest_return": {
                    "type": "number",
                    "description": "The return % from backtest for this value",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "Confidence level",
                },
                "reason": {
                    "type": "string",
                    "description": "Brief explanation",
                },
            },
            "required": ["parameter", "current_value", "recommended_value", "reason", "confidence"],
        },
    }

    WATCH_ITEM_TOOL = {
        "name": "flag_watch_item",
        "description": "Flag something to monitor in future reviews.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["pattern", "risk", "opportunity", "anomaly"],
                    "description": "Type of watch item",
                },
                "description": {
                    "type": "string",
                    "description": "What to watch for",
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "Priority level",
                },
            },
            "required": ["category", "description", "priority"],
        },
    }

    def __init__(self):
        self.api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        self.client = anthropic.Anthropic(api_key=self.api_key)

        # Sandboxed parameters - never touches production
        self.sim_params = {
            "mr_threshold": -2.0,
            "reversal_threshold": -2.0,
            "crash_threshold": -2.0,
            "pump_threshold": 2.0,
        }

        # Simulation state
        self.reviews: List[SimulationReview] = []
        self.api_calls = 0

    def _fetch_market_data(self, start_date: datetime, end_date: datetime) -> List[Dict[str, Any]]:
        """Fetch market data for a specific date range."""
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        alpaca_key = os.environ.get("ALPACA_API_KEY")
        alpaca_secret = os.environ.get("ALPACA_SECRET_KEY")

        if not alpaca_key or not alpaca_secret:
            logger.warning("Alpaca credentials not set")
            return []

        client = StockHistoricalDataClient(alpaca_key, alpaca_secret)

        try:
            request = StockBarsRequest(
                symbol_or_symbols=["IBIT"],
                timeframe=TimeFrame.Day,
                start=start_date,
                end=end_date,
            )
            bars = client.get_stock_bars(request)

            try:
                ibit_bars = bars["IBIT"]
            except KeyError:
                return []

            return [
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
            logger.error(f"Failed to fetch market data: {e}")
            return []

    def _run_backtest(
        self,
        data: List[Dict],
        mr_threshold: float = -2.0,
        reversal_threshold: float = -2.0,
        name: str = "Test",
    ) -> Dict[str, Any]:
        """Run a simple backtest with given parameters."""
        if not data or len(data) < 2:
            return {
                "name": name,
                "total_return_pct": 0,
                "total_trades": 0,
                "winning_trades": 0,
                "win_rate": 0,
                "avg_return_pct": 0,
                "max_drawdown_pct": 0,
                "sharpe_ratio": 0,
            }

        trades = []
        capital = 10000
        peak = capital

        for i in range(1, len(data)):
            day = data[i]
            prev_day = data[i - 1]

            if day["weekday"] >= 5:
                continue

            prev_return = ((prev_day["close"] - prev_day["open"]) / prev_day["open"]) * 100

            if prev_return <= mr_threshold:
                day_return = ((day["close"] - day["open"]) / day["open"]) * 100
                lev_return = day_return * 2

                max_drawdown = ((day["low"] - day["open"]) / day["open"]) * 100 * 2
                if max_drawdown <= reversal_threshold:
                    first_leg = reversal_threshold
                    remaining = lev_return - reversal_threshold
                    trade_return = first_leg + (-remaining)
                else:
                    trade_return = lev_return

                pnl = capital * (trade_return / 100)
                capital += pnl

                if capital > peak:
                    peak = capital

                trades.append(trade_return)

        if not trades:
            return {
                "name": name,
                "total_return_pct": 0,
                "total_trades": 0,
                "winning_trades": 0,
                "win_rate": 0,
                "avg_return_pct": 0,
                "max_drawdown_pct": 0,
                "sharpe_ratio": 0,
            }

        total_return = ((capital - 10000) / 10000) * 100
        winning = [t for t in trades if t > 0]
        win_rate = (len(winning) / len(trades)) * 100 if trades else 0
        avg_return = statistics.mean(trades) if trades else 0
        std_return = statistics.stdev(trades) if len(trades) > 1 else 0
        sharpe = (avg_return / std_return * (252**0.5)) if std_return > 0 else 0
        max_dd = ((peak - capital) / peak) * 100 if peak > 0 else 0

        return {
            "name": name,
            "total_return_pct": total_return,
            "total_trades": len(trades),
            "winning_trades": len(winning),
            "win_rate": win_rate,
            "avg_return_pct": avg_return,
            "max_drawdown_pct": max_dd,
            "sharpe_ratio": sharpe,
        }

    def _format_backtest_result(self, result: Dict[str, Any]) -> str:
        """Format backtest result for prompt."""
        return (
            f"**{result['name']}**\n"
            f"  Return: {result['total_return_pct']:+.2f}%\n"
            f"  Trades: {result['total_trades']} ({result['winning_trades']} wins)\n"
            f"  Win Rate: {result['win_rate']:.1f}%\n"
            f"  Sharpe: {result['sharpe_ratio']:.2f}"
        )

    def _detect_market_regime(self, data: List[Dict]) -> Dict[str, Any]:
        """Detect market regime from data."""
        if not data or len(data) < 20:
            return {"regime": "unknown", "confidence": "low", "indicators": {}}

        closes = [d["close"] for d in data]
        returns = []
        for i in range(1, len(closes)):
            ret = ((closes[i] - closes[i - 1]) / closes[i - 1]) * 100
            returns.append(ret)

        # 20-day MA slope
        if len(closes) >= 20:
            ma_20 = statistics.mean(closes[-20:])
            ma_20_prev = statistics.mean(closes[-25:-5]) if len(closes) >= 25 else ma_20
            ma_slope = ((ma_20 - ma_20_prev) / ma_20_prev) * 100 if ma_20_prev else 0
        else:
            ma_slope = 0

        # Recent trend
        recent_returns = returns[-10:] if len(returns) >= 10 else returns
        up_days = sum(1 for r in recent_returns if r > 0)

        # Determine regime
        bull_signals = 0
        bear_signals = 0

        if ma_slope > 2:
            bull_signals += 2
        elif ma_slope > 0.5:
            bull_signals += 1
        elif ma_slope < -2:
            bear_signals += 2
        elif ma_slope < -0.5:
            bear_signals += 1

        if up_days >= 7:
            bull_signals += 1
        elif up_days <= 3:
            bear_signals += 1

        net_signal = bull_signals - bear_signals
        if net_signal >= 3:
            regime = "strong_bull"
        elif net_signal >= 1:
            regime = "bull"
        elif net_signal <= -3:
            regime = "strong_bear"
        elif net_signal <= -1:
            regime = "bear"
        else:
            regime = "neutral"

        confidence = "high" if abs(net_signal) >= 3 else "medium" if abs(net_signal) >= 1 else "low"

        return {
            "regime": regime,
            "confidence": confidence,
            "indicators": {"ma_slope": ma_slope, "up_days": up_days},
        }

    def _generate_market_summary(self, data: List[Dict]) -> str:
        """Generate market summary."""
        if not data:
            return "No data available"

        returns = []
        for i in range(1, len(data)):
            ret = ((data[i]["close"] - data[i - 1]["close"]) / data[i - 1]["close"]) * 100
            returns.append(ret)

        if not returns:
            return "Insufficient data"

        big_drops = sum(1 for r in returns if r <= -2.0)
        big_pumps = sum(1 for r in returns if r >= 2.0)

        return (
            f"Period: {data[0]['date']} to {data[-1]['date']} ({len(data)} days)\n"
            f"Total Return: {sum(returns):+.2f}%\n"
            f"Big Drops (≥-2%): {big_drops} | Big Pumps (≥+2%): {big_pumps}"
        )

    async def _run_single_review(
        self,
        review_date: datetime,
        review_number: int,
        data: List[Dict],
        previous_reviews: List[SimulationReview],
        lookback_days: int = 90,
    ) -> SimulationReview:
        """Run a single simulated review cycle."""
        params_before = self.sim_params.copy()

        # Run backtest with current params
        current_result = self._run_backtest(
            data,
            mr_threshold=self.sim_params["mr_threshold"],
            reversal_threshold=self.sim_params["reversal_threshold"],
            name="Current",
        )

        # Test alternative parameters
        parameter_tests = []
        tested_values: Dict[str, List[float]] = {"mr_threshold": [], "reversal_threshold": []}

        # MR threshold variations
        current_mr = self.sim_params["mr_threshold"]
        mr_test_values = sorted(
            set(
                [current_mr - 1.0, current_mr - 0.5, current_mr, current_mr + 0.5, current_mr + 1.0]
            )
        )
        mr_test_values = [v for v in mr_test_values if -4.0 <= v <= -0.5]
        tested_values["mr_threshold"] = mr_test_values

        for mr_thresh in mr_test_values:
            result = self._run_backtest(
                data,
                mr_threshold=mr_thresh,
                reversal_threshold=self.sim_params["reversal_threshold"],
                name=f"MR @ {mr_thresh}%",
            )
            parameter_tests.append(result)

        # Reversal threshold variations
        current_rev = self.sim_params["reversal_threshold"]
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

        for rev_thresh in rev_test_values:
            result = self._run_backtest(
                data,
                mr_threshold=self.sim_params["mr_threshold"],
                reversal_threshold=rev_thresh,
                name=f"Reversal @ {rev_thresh}%",
            )
            parameter_tests.append(result)

        # Detect market regime
        regime_data = self._detect_market_regime(data)

        # Build previous review context
        prev_context = ""
        if previous_reviews:
            prev_lines = ["## Previous Reviews (Memory)"]
            for prev in previous_reviews[-2:]:  # Last 2 reviews
                prev_lines.append(f"\n### {prev.review_date.strftime('%Y-%m-%d')}")
                prev_lines.append(f"Regime: {prev.market_regime}")
                if prev.recommendations:
                    for rec in prev.recommendations:
                        prev_lines.append(
                            f"Changed: {rec['parameter']} {rec['old_value']} → {rec['new_value']}"
                        )
            prev_context = "\n".join(prev_lines)

        # Build prompt
        prompt = SIMULATION_REVIEW_PROMPT.format(
            review_date=review_date.strftime("%Y-%m-%d"),
            mr_threshold=self.sim_params["mr_threshold"],
            reversal_threshold=self.sim_params["reversal_threshold"],
            crash_threshold=self.sim_params["crash_threshold"],
            pump_threshold=self.sim_params["pump_threshold"],
            market_regime=f"Market Regime: {regime_data['regime'].upper()} ({regime_data['confidence']})",
            previous_review_context=prev_context,
            lookback_days=lookback_days,
            current_backtest=self._format_backtest_result(current_result),
            parameter_tests="\n\n".join(self._format_backtest_result(r) for r in parameter_tests),
            tested_values=f"mr_threshold: {tested_values['mr_threshold']}\nreversal_threshold: {tested_values['reversal_threshold']}",
            market_summary=self._generate_market_summary(data),
        )

        # Call Claude
        self.api_calls += 1
        recommendations = []
        watch_items = []
        summary = ""

        try:
            response = self.client.messages.create(
                model="claude-opus-4-5-20251101",
                max_tokens=800,
                messages=[{"role": "user", "content": prompt}],
                tools=[self.PARAMETER_CHANGE_TOOL, self.WATCH_ITEM_TOOL],
            )

            for block in response.content:
                if block.type == "text":
                    summary = block.text[:200]
                elif block.type == "tool_use" and block.name == "recommend_parameter_change":
                    rec_data = block.input
                    param = rec_data["parameter"]
                    old_value = self.sim_params.get(param, 0)
                    new_value = rec_data["recommended_value"]

                    # Validate against tested values
                    if param in tested_values and new_value in tested_values[param]:
                        # Auto-apply in simulation
                        self.sim_params[param] = new_value
                        recommendations.append(
                            {
                                "parameter": param,
                                "old_value": old_value,
                                "new_value": new_value,
                                "confidence": rec_data.get("confidence", "medium"),
                                "reason": rec_data.get("reason", ""),
                            }
                        )
                        logger.info(
                            f"[SIM] Review #{review_number}: {param} {old_value} → {new_value}"
                        )

                elif block.type == "tool_use" and block.name == "flag_watch_item":
                    item_data = block.input
                    watch_items.append(
                        {
                            "category": item_data.get("category"),
                            "description": item_data.get("description"),
                            "priority": item_data.get("priority", "medium"),
                        }
                    )

        except Exception as e:
            logger.error(f"[SIM] Review #{review_number} failed: {e}")
            summary = f"Review failed: {e}"

        return SimulationReview(
            review_date=review_date,
            review_number=review_number,
            params_before=params_before,
            params_after=self.sim_params.copy(),
            backtest_return=current_result["total_return_pct"],
            recommendations=recommendations,
            watch_items=watch_items,
            summary=summary,
            market_regime=regime_data["regime"],
            regime_confidence=regime_data["confidence"],
        )

    async def run_simulation(
        self,
        start_date: datetime,
        end_date: datetime,
        review_interval_days: int = 14,
        lookback_days: int = 60,
        initial_params: Optional[Dict[str, float]] = None,
    ) -> SimulationResult:
        """
        Run a complete historical simulation.

        Args:
            start_date: Simulation start date
            end_date: Simulation end date
            review_interval_days: Days between reviews (default 14 = bi-weekly)
            lookback_days: Days of data to analyze in each review
            initial_params: Starting parameters (uses defaults if not provided)

        Returns:
            SimulationResult with complete simulation data
        """
        # Cap end_date to today if it's in the future (can't simulate future data)
        today = datetime.now()
        if end_date > today:
            logger.info(f"Capping end_date from {end_date.strftime('%Y-%m-%d')} to today")
            end_date = today

        # Ensure we have at least 14 days to simulate
        if (end_date - start_date).days < 14:
            logger.error("Simulation period too short (need at least 14 days)")
            return SimulationResult(
                start_date=start_date,
                end_date=end_date,
                initial_params={
                    "mr_threshold": -2.0,
                    "reversal_threshold": -2.0,
                    "crash_threshold": -2.0,
                    "pump_threshold": 2.0,
                },
                final_params={
                    "mr_threshold": -2.0,
                    "reversal_threshold": -2.0,
                    "crash_threshold": -2.0,
                    "pump_threshold": 2.0,
                },
                reviews=[],
                static_performance=0,
                evolved_performance=0,
                total_api_calls=0,
                estimated_cost=0,
            )

        logger.info(
            f"Starting simulation: {start_date.strftime('%Y-%m-%d')} → "
            f"{end_date.strftime('%Y-%m-%d')}"
        )

        # Reset simulation state
        self.reviews = []
        self.api_calls = 0

        # Set initial parameters
        if initial_params:
            self.sim_params = initial_params.copy()
        else:
            self.sim_params = {
                "mr_threshold": -2.0,
                "reversal_threshold": -2.0,
                "crash_threshold": -2.0,
                "pump_threshold": 2.0,
            }

        initial_params_snapshot = self.sim_params.copy()

        # Fetch ALL market data for the period (plus lookback buffer)
        data_start = start_date - timedelta(days=lookback_days + 10)
        all_data = self._fetch_market_data(data_start, end_date)

        if not all_data:
            logger.error(
                f"No market data available for {start_date.strftime('%Y-%m-%d')} to "
                f"{end_date.strftime('%Y-%m-%d')}"
            )
            return SimulationResult(
                start_date=start_date,
                end_date=end_date,
                initial_params=initial_params_snapshot,
                final_params=self.sim_params,
                reviews=[],
                static_performance=0,
                evolved_performance=0,
                total_api_calls=0,
                estimated_cost=0,
            )

        logger.info(f"Fetched {len(all_data)} days of market data")

        # Generate review dates
        review_dates = []
        current_date = start_date + timedelta(days=review_interval_days)
        while current_date <= end_date:
            review_dates.append(current_date)
            current_date += timedelta(days=review_interval_days)

        logger.info(f"Simulation will run {len(review_dates)} reviews")

        # Run each review
        for i, review_date in enumerate(review_dates, 1):
            # Get data up to this review date only (time-windowed)
            review_date_str = review_date.strftime("%Y-%m-%d")
            windowed_data = [d for d in all_data if d["date"] <= review_date_str]

            # Only use last lookback_days
            if len(windowed_data) > lookback_days:
                windowed_data = windowed_data[-lookback_days:]

            if len(windowed_data) < 10:
                logger.warning(f"Skipping review {i}: insufficient data")
                continue

            review = await self._run_single_review(
                review_date=review_date,
                review_number=i,
                data=windowed_data,
                previous_reviews=self.reviews,
                lookback_days=lookback_days,
            )
            self.reviews.append(review)

            logger.info(
                f"[SIM] Review {i}/{len(review_dates)} complete. "
                f"Params: mr={self.sim_params['mr_threshold']}, "
                f"rev={self.sim_params['reversal_threshold']}"
            )

        # Calculate final performance comparison
        # Get data for full simulation period
        sim_period_data = [
            d
            for d in all_data
            if start_date.strftime("%Y-%m-%d") <= d["date"] <= end_date.strftime("%Y-%m-%d")
        ]

        # Static performance (initial params, no changes)
        static_result = self._run_backtest(
            sim_period_data,
            mr_threshold=initial_params_snapshot["mr_threshold"],
            reversal_threshold=initial_params_snapshot["reversal_threshold"],
            name="Static",
        )

        # Evolved performance (final params after all changes)
        evolved_result = self._run_backtest(
            sim_period_data,
            mr_threshold=self.sim_params["mr_threshold"],
            reversal_threshold=self.sim_params["reversal_threshold"],
            name="Evolved",
        )

        # Estimate cost (~$0.08 per API call for Claude Sonnet)
        estimated_cost = self.api_calls * 0.08

        return SimulationResult(
            start_date=start_date,
            end_date=end_date,
            initial_params=initial_params_snapshot,
            final_params=self.sim_params.copy(),
            reviews=self.reviews,
            static_performance=static_result["total_return_pct"],
            evolved_performance=evolved_result["total_return_pct"],
            total_api_calls=self.api_calls,
            estimated_cost=estimated_cost,
        )


async def run_year_simulation(year: int) -> SimulationResult:
    """Convenience function to simulate a full year."""
    simulator = HistoricalSimulator()
    return await simulator.run_simulation(
        start_date=datetime(year, 1, 1),
        end_date=datetime(year, 12, 31),
    )
