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
from .error_alerting import AlertSeverity, alert_anomaly, alert_error

logger = logging.getLogger(__name__)


# All configurable strategy parameters
STRATEGY_PARAMETERS = {
    # Threshold parameters (floats)
    "mr_threshold": {"type": "float", "default": -2.0, "display": "Mean Reversion Threshold"},
    "reversal_threshold": {
        "type": "float",
        "default": -2.0,
        "display": "Position Reversal Threshold",
    },
    "crash_threshold": {"type": "float", "default": -2.0, "display": "Crash Day Threshold"},
    "pump_threshold": {"type": "float", "default": 2.0, "display": "Pump Day Threshold"},
    # Enable/disable flags (booleans)
    "ten_am_dump_enabled": {"type": "bool", "default": True, "display": "10 AM Dump Strategy"},
    "mean_reversion_enabled": {
        "type": "bool",
        "default": True,
        "display": "Mean Reversion Strategy",
    },
    "crash_day_enabled": {"type": "bool", "default": True, "display": "Crash Day Strategy"},
    "pump_day_enabled": {"type": "bool", "default": True, "display": "Pump Day Strategy"},
    "btc_overnight_filter_enabled": {
        "type": "bool",
        "default": True,
        "display": "BTC Overnight Filter",
    },
    # Priority mode
    "signal_priority": {
        "type": "enum",
        "default": "ten_am_first",
        "options": ["ten_am_first", "mean_reversion_first"],
        "display": "Signal Priority Mode",
    },
}

# Tool definition for Claude to recommend parameter changes
PARAMETER_CHANGE_TOOL = {
    "name": "recommend_parameter_change",
    "description": (
        "Recommend a change to a strategy parameter based on backtest analysis. "
        "CRITICAL: You may ONLY recommend values that were actually tested in the "
        "parameter sensitivity analysis. Do NOT extrapolate or guess untested values. "
        "Only call this tool if backtest data strongly supports a specific tested value. "
        "Do not call if current parameters are optimal or if no tested value is clearly better. "
        "For boolean parameters, use true/false. For enum parameters, use exact option values."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "parameter": {
                "type": "string",
                "enum": list(STRATEGY_PARAMETERS.keys()),
                "description": "The parameter to change",
            },
            "current_value": {
                "oneOf": [{"type": "number"}, {"type": "boolean"}, {"type": "string"}],
                "description": "The current value of the parameter",
            },
            "recommended_value": {
                "oneOf": [{"type": "number"}, {"type": "boolean"}, {"type": "string"}],
                "description": (
                    "The recommended new value. MUST be one of the values from the "
                    "parameter sensitivity tests shown above - never extrapolate. "
                    "For boolean params: true/false. For enum params: exact option value."
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

## Current Strategy Configuration
The bot trades IBIT (Bitcoin ETF) using leveraged ETFs:
- BITU (2x long) for bullish signals
- SBIT (2x inverse) for bearish signals

**Active Strategies (and current status):**
1. **10 AM Dump** [{ten_am_dump_enabled}]: Buy SBIT at 9:35 AM, sell at 10:30 AM (daily)
2. **Mean Reversion** [{mean_reversion_enabled}]: Buy BITU after IBIT drops ≥{mr_threshold}% previous day
   - Filtered by BTC overnight movement (only trade if BTC up overnight)
3. **Position Reversal**: If BITU position drops ≥{reversal_threshold}% intraday, flip to SBIT
4. **Crash Day**: Buy SBIT if IBIT drops ≥{crash_threshold}% intraday
5. **Pump Day**: Buy BITU if IBIT rises ≥{pump_threshold}% intraday

**Signal Priority Mode:** {signal_priority}
- "ten_am_first": 10 AM Dump takes priority, blocks Mean Reversion on that day
- "mean_reversion_first": Mean Reversion takes priority, 10 AM Dump runs only on non-MR days

All positions close at 3:55 PM ET (never hold overnight).

{market_regime}

{previous_review_context}

## Recent Performance Data (Last 3 Months)

### Backtest Results - Current Parameters
{current_backtest}

### Threshold Parameter Sensitivity Analysis
{parameter_tests}

### Strategy Configuration Tests
{strategy_tests}

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

3. **Strategy Configuration Recommendations**
   - Should any strategies be ENABLED or DISABLED based on backtest results?
   - Should the signal priority mode change? (Compare 10 AM Priority vs MR Priority results)
   - Be specific: recommend based on the Strategy Configuration Tests above

4. **Threshold Recommendations** (if any)
   - Should we adjust thresholds? Be specific with numbers.
   - Only recommend changes if data strongly supports it.

5. **Pattern Observations**
   - Any new patterns emerging in the data?
   - Day-of-week effects, time-of-day patterns, cross-market correlations?

6. **Risk Concerns**
   - Any warning signs? Increasing drawdowns? Deteriorating win rate?

7. **Action Items** (bullet list)
   - Specific, actionable recommendations
   - Include "NO CHANGES NEEDED" if current parameters are optimal

Format your response as a clear report suitable for a Telegram message (use markdown, keep it under 2500 characters).

**CRITICAL RULES FOR RECOMMENDATIONS:**
1. You may ONLY recommend values that appear in the "Tested Values" list above
2. For boolean params (enable/disable): use true or false
3. For enum params (signal_priority): use exact option values ("ten_am_first" or "mean_reversion_first")
4. For float params: only use values from the tested list
5. When using the `recommend_parameter_change` tool, the `backtest_return` MUST match the exact return shown in the tests
6. If no tested value clearly outperforms the current setting, respond with "NO CHANGES NEEDED"

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
            alert_error(
                AlertSeverity.WARNING,
                f"Failed to fetch IBIT data for strategy review: {e}",
                {"days_requested": days},
                category="fetch_ibit_data",
            )
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

    def _detect_market_regime(self, data: List[Dict]) -> Dict[str, Any]:
        """
        Detect current market regime based on multiple indicators.

        Returns dict with:
        - regime: "strong_bull", "bull", "neutral", "bear", "strong_bear"
        - confidence: "low", "medium", "high"
        - indicators: detailed breakdown of each signal
        """
        if not data or len(data) < 20:
            return {
                "regime": "unknown",
                "confidence": "low",
                "indicators": {"error": "Insufficient data for regime detection"},
            }

        closes = [d["close"] for d in data]
        returns = []
        for i in range(1, len(closes)):
            ret = ((closes[i] - closes[i - 1]) / closes[i - 1]) * 100
            returns.append(ret)

        # 1. 20-day Moving Average Slope
        if len(closes) >= 20:
            ma_20 = statistics.mean(closes[-20:])
            ma_20_prev = statistics.mean(closes[-25:-5]) if len(closes) >= 25 else ma_20
            ma_slope = ((ma_20 - ma_20_prev) / ma_20_prev) * 100 if ma_20_prev else 0
        else:
            ma_slope = 0

        # 2. Consecutive Up/Down Days (last 10 days)
        recent_returns = returns[-10:] if len(returns) >= 10 else returns
        consecutive_up = 0
        consecutive_down = 0
        for r in reversed(recent_returns):
            if r > 0:
                if consecutive_down == 0:
                    consecutive_up += 1
                else:
                    break
            else:
                if consecutive_up == 0:
                    consecutive_down += 1
                else:
                    break

        up_days = sum(1 for r in recent_returns if r > 0)
        down_days = len(recent_returns) - up_days

        # 3. Volatility (20-day standard deviation)
        recent_vol = (
            statistics.stdev(returns[-20:]) if len(returns) >= 20 else statistics.stdev(returns)
        )

        # 4. Volatility Compression (compare recent vol to longer-term)
        if len(returns) >= 60:
            long_vol = statistics.stdev(returns[-60:])
            vol_ratio = recent_vol / long_vol if long_vol > 0 else 1.0
            compression = vol_ratio < 0.7  # Volatility compressed if recent < 70% of longer-term
        else:
            vol_ratio = 1.0
            compression = False

        # 5. Trend strength (price vs 20-day MA)
        current_price = closes[-1]
        ma_20_current = statistics.mean(closes[-20:]) if len(closes) >= 20 else current_price
        price_vs_ma = ((current_price - ma_20_current) / ma_20_current) * 100

        # Determine regime
        bull_signals = 0
        bear_signals = 0

        # MA slope signal
        if ma_slope > 2:
            bull_signals += 2
        elif ma_slope > 0.5:
            bull_signals += 1
        elif ma_slope < -2:
            bear_signals += 2
        elif ma_slope < -0.5:
            bear_signals += 1

        # Consecutive days signal
        if consecutive_up >= 4:
            bull_signals += 2
        elif consecutive_up >= 2:
            bull_signals += 1
        if consecutive_down >= 4:
            bear_signals += 2
        elif consecutive_down >= 2:
            bear_signals += 1

        # Up/down ratio signal
        if up_days >= 7:
            bull_signals += 1
        elif down_days >= 7:
            bear_signals += 1

        # Price vs MA signal
        if price_vs_ma > 5:
            bull_signals += 1
        elif price_vs_ma < -5:
            bear_signals += 1

        # Determine regime from signals
        net_signal = bull_signals - bear_signals
        if net_signal >= 4:
            regime = "strong_bull"
        elif net_signal >= 2:
            regime = "bull"
        elif net_signal <= -4:
            regime = "strong_bear"
        elif net_signal <= -2:
            regime = "bear"
        else:
            regime = "neutral"

        # Confidence based on signal strength
        signal_strength = abs(net_signal)
        if signal_strength >= 4:
            confidence = "high"
        elif signal_strength >= 2:
            confidence = "medium"
        else:
            confidence = "low"

        return {
            "regime": regime,
            "confidence": confidence,
            "indicators": {
                "ma_20_slope_pct": round(ma_slope, 2),
                "consecutive_up_days": consecutive_up,
                "consecutive_down_days": consecutive_down,
                "up_days_last_10": up_days,
                "down_days_last_10": down_days,
                "volatility_20d": round(recent_vol, 2),
                "volatility_ratio": round(vol_ratio, 2),
                "volatility_compressed": compression,
                "price_vs_ma20_pct": round(price_vs_ma, 2),
                "bull_signals": bull_signals,
                "bear_signals": bear_signals,
            },
        }

    def _format_regime_context(self, regime_data: Dict[str, Any]) -> str:
        """Format market regime data for inclusion in prompt."""
        regime = regime_data.get("regime", "unknown")
        confidence = regime_data.get("confidence", "low")
        indicators = regime_data.get("indicators", {})

        regime_emoji = {
            "strong_bull": "🚀",
            "bull": "📈",
            "neutral": "➡️",
            "bear": "📉",
            "strong_bear": "💥",
            "unknown": "❓",
        }

        lines = [
            "## Market Regime Analysis",
            f"**Current Regime**: {regime_emoji.get(regime, '')} {regime.upper()} (confidence: {confidence})",
            "",
            "**Indicators:**",
            f"- 20-day MA Slope: {indicators.get('ma_20_slope_pct', 0):+.2f}%",
            f"- Consecutive Up Days: {indicators.get('consecutive_up_days', 0)}",
            f"- Consecutive Down Days: {indicators.get('consecutive_down_days', 0)}",
            f"- Last 10 Days: {indicators.get('up_days_last_10', 0)} up / {indicators.get('down_days_last_10', 0)} down",
            f"- 20-day Volatility: {indicators.get('volatility_20d', 0):.2f}%",
            f"- Volatility Compressed: {'Yes ⚠️' if indicators.get('volatility_compressed') else 'No'}",
            f"- Price vs 20-day MA: {indicators.get('price_vs_ma20_pct', 0):+.2f}%",
            "",
            "**Regime Implications for Strategy:**",
        ]

        if regime in ["strong_bull", "bull"]:
            lines.extend(
                [
                    "- Mean Reversion triggers may be rare (fewer dips)",
                    "- Pump Day signals more likely",
                    "- Consider if thresholds should be lowered",
                    "- Watch for missed overnight gaps",
                ]
            )
        elif regime in ["strong_bear", "bear"]:
            lines.extend(
                [
                    "- Mean Reversion may have lower win rate (dead cat bounces)",
                    "- Position Reversal is critical for protection",
                    "- Crash Day signals more frequent",
                    "- Current defensive posture is appropriate",
                ]
            )
        else:
            lines.extend(
                [
                    "- Mixed signals - current balanced approach is appropriate",
                    "- Monitor for regime shift in either direction",
                    "- Volatility compression may precede a big move",
                ]
            )

        return "\n".join(lines)

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

            # Include market regime from that time
            regime = review.get("market_regime", {})
            if regime:
                regime_name = regime.get("regime", "unknown")
                regime_conf = regime.get("confidence", "unknown")
                sections.append(
                    f"**Market Regime at that time**: {regime_name} ({regime_conf} confidence)"
                )

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

    def _run_strategy_combination_tests(self, ibit_data: List[Dict]) -> Dict[str, Dict]:
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
        import os
        from datetime import timedelta

        from .data_providers import AlpacaProvider

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

    def _backtest_10am_dump(self, sbit_intraday: List[Dict]) -> Dict[str, Any]:
        """Backtest 10 AM dump strategy only."""
        import pandas as pd

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
        self, ibit_data: List[Dict], bitu_bars: List[Dict], btc_bars: List[Dict]
    ) -> Dict[str, Any]:
        """Backtest mean reversion strategy only."""

        import pandas as pd

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
        self,
        ibit_data: List[Dict],
        bitu_bars: List[Dict],
        sbit_bars: List[Dict],
        btc_bars: List[Dict],
        priority: str = "ten_am_first",
    ) -> Dict[str, Any]:
        """Backtest combined strategy with specified priority."""
        import pandas as pd

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

    def _find_nearest_bar(self, day_data, target_time: str, window_minutes: int = 5):
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

    def _format_strategy_tests(self, results: Dict[str, Dict]) -> str:
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
        import asyncio

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
            f"- mean_reversion_enabled: {tested_values.get('mean_reversion_enabled', [True, False])}\n\n"
            f"**Priority Mode (enum):**\n"
            f"- signal_priority: {tested_values.get('signal_priority', ['ten_am_first', 'mean_reversion_first'])}"
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
                f"Last 10d: {indicators.get('up_days_last_10', 0)}↑/{indicators.get('down_days_last_10', 0)}↓"
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
                        f"Invalid enum value for {param}: {new_value}. Must be one of {valid_options}"
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
