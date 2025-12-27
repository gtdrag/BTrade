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
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import anthropic

from .database import get_database

logger = logging.getLogger(__name__)

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

## Recent Performance Data (Last 3 Months)

### Backtest Results - Current Parameters
{current_backtest}

### Parameter Sensitivity Analysis
{parameter_tests}

### Raw Market Data Summary
{market_summary}

## Your Task

Analyze this data and provide:

1. **Performance Assessment** (2-3 sentences)
   - Is the strategy working? What's the trend?

2. **Parameter Recommendations** (if any)
   - Should we adjust thresholds? Be specific with numbers.
   - Only recommend changes if data strongly supports it.

3. **Pattern Observations**
   - Any new patterns emerging in the data?
   - Day-of-week effects, time-of-day patterns, cross-market correlations?

4. **Risk Concerns**
   - Any warning signs? Increasing drawdowns? Deteriorating win rate?

5. **Action Items** (bullet list)
   - Specific, actionable recommendations
   - Include "NO CHANGES NEEDED" if current parameters are optimal

Format your response as a clear report suitable for a Telegram message (use markdown, keep it under 2000 characters).
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
class StrategyRecommendation:
    """Recommendation from Claude analysis."""

    summary: str  # Brief summary
    full_report: str  # Full markdown report
    has_recommendations: bool  # True if changes suggested
    recommended_params: Dict[str, float]  # Suggested parameter changes
    risk_level: str  # "low", "medium", "high"
    timestamp: str


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

        # Current strategy parameters
        self.current_params = {
            "mr_threshold": -2.0,
            "reversal_threshold": -2.0,
            "crash_threshold": -2.0,
            "pump_threshold": 2.0,
        }

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

        # 3. Test alternative parameters
        parameter_tests = []

        # Test different reversal thresholds
        for rev_thresh in [-1.5, -2.0, -2.5, -3.0]:
            result = self._run_backtest(
                ibit_data,
                mr_threshold=-2.0,
                reversal_threshold=rev_thresh,
                name=f"Reversal @ {rev_thresh}%",
            )
            parameter_tests.append(result)

        # Test different MR thresholds
        for mr_thresh in [-1.5, -2.0, -2.5, -3.0]:
            result = self._run_backtest(
                ibit_data,
                mr_threshold=mr_thresh,
                reversal_threshold=-2.0,
                name=f"MR Threshold @ {mr_thresh}%",
            )
            parameter_tests.append(result)

        # 4. Format data for Claude
        current_backtest = self._format_backtest_result(current_result)
        param_tests_str = "\n\n".join(self._format_backtest_result(r) for r in parameter_tests)
        market_summary = self._generate_market_summary(ibit_data)

        # 5. Build prompt
        prompt = STRATEGY_REVIEW_PROMPT.format(
            mr_threshold=self.current_params["mr_threshold"],
            reversal_threshold=self.current_params["reversal_threshold"],
            crash_threshold=self.current_params["crash_threshold"],
            pump_threshold=self.current_params["pump_threshold"],
            current_backtest=current_backtest,
            parameter_tests=param_tests_str,
            market_summary=market_summary,
        )

        # 6. Call Claude
        logger.info("Sending data to Claude for analysis...")

        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )

            full_report = response.content[0].text

            # Determine if there are recommendations
            has_recs = "NO CHANGES NEEDED" not in full_report.upper()

            # Log to database
            self.db.log_event(
                "STRATEGY_REVIEW",
                "Monthly strategy review completed",
                {
                    "model": "claude-sonnet-4-20250514",
                    "current_return": current_result.total_return_pct,
                    "has_recommendations": has_recs,
                    "response_length": len(full_report),
                    "timestamp": datetime.now().isoformat(),
                },
            )

            # Create summary (first paragraph)
            summary = full_report.split("\n\n")[0][:200] if full_report else "Review complete"

            return StrategyRecommendation(
                summary=summary,
                full_report=full_report,
                has_recommendations=has_recs,
                recommended_params={},  # Could parse from response
                risk_level="medium" if has_recs else "low",
                timestamp=datetime.now().isoformat(),
            )

        except Exception as e:
            logger.error(f"Claude API call failed: {e}")
            return StrategyRecommendation(
                summary=f"Review failed: {e}",
                full_report=f"Error calling Claude API: {e}",
                has_recommendations=False,
                recommended_params={},
                risk_level="unknown",
                timestamp=datetime.now().isoformat(),
            )


# Singleton instance
_reviewer: Optional[StrategyReviewer] = None


def get_strategy_reviewer() -> StrategyReviewer:
    """Get or create the strategy reviewer singleton."""
    global _reviewer
    if _reviewer is None:
        _reviewer = StrategyReviewer()
    return _reviewer
