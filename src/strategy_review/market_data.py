"""
Market data fetching and analysis for strategy review.

Provides methods for fetching market data, generating summaries,
and detecting market regimes.
"""

import logging
import os
import statistics
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict, List

from ..error_alerting import AlertSeverity, alert_error

if TYPE_CHECKING:
    from .reviewer import StrategyReviewer

logger = logging.getLogger(__name__)


class MarketDataMixin:
    """
    Mixin providing market data methods for StrategyReviewer.

    Requires from base class:
    - No specific requirements (uses external APIs)
    """

    def _fetch_market_data(self: "StrategyReviewer", days: int = 90) -> Dict[str, List[Dict]]:
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

    def _generate_market_summary(self: "StrategyReviewer", data: List[Dict]) -> str:
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

    def _detect_market_regime(self: "StrategyReviewer", data: List[Dict]) -> Dict[str, Any]:
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

    def _format_regime_context(self: "StrategyReviewer", regime_data: Dict[str, Any]) -> str:
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
            f"**Current Regime**: {regime_emoji.get(regime, '')} {regime.upper()} "
            f"(confidence: {confidence})",
            "",
            "**Indicators:**",
            f"- 20-day MA Slope: {indicators.get('ma_20_slope_pct', 0):+.2f}%",
            f"- Consecutive Up Days: {indicators.get('consecutive_up_days', 0)}",
            f"- Consecutive Down Days: {indicators.get('consecutive_down_days', 0)}",
            f"- Last 10 Days: {indicators.get('up_days_last_10', 0)} up / "
            f"{indicators.get('down_days_last_10', 0)} down",
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

    def _build_previous_review_context(
        self: "StrategyReviewer", previous_reviews: List[Dict]
    ) -> str:
        """Build context section from previous reviews for recursive learning."""
        if not previous_reviews:
            return ""

        sections = ["## Previous Review Context (Memory)"]

        for review in previous_reviews:
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
                        f"- [{item.get('category', 'unknown').upper()}] "
                        f"{item.get('description', 'No description')}\n"
                        f"  Metric: {item.get('metric', 'N/A')} | "
                        f"Current: {item.get('current_value', 'N/A')} | "
                        f"Threshold: {item.get('threshold', 'N/A')} | "
                        f"Status: {status}"
                    )

        return "\n".join(sections)
