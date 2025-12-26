"""
Pattern Discovery Engine for IBIT Trading Bot.

Uses LLM analysis to discover trading patterns from historical data,
stores patterns in a registry, and integrates with the trading strategy.
"""

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog

from .utils import get_et_now

logger = structlog.get_logger(__name__)

# Default paths
PATTERNS_DIR = Path(__file__).parent.parent / "patterns"
PATTERNS_FILE = PATTERNS_DIR / "active_patterns.json"


class PatternStatus(Enum):
    """Status of a discovered pattern."""

    CANDIDATE = "candidate"  # Just discovered, needs validation
    PAPER = "paper"  # Being paper traded for validation
    LIVE = "live"  # Validated and actively trading
    RETIRED = "retired"  # Stopped working, no longer used


class PatternSignal(Enum):
    """Signal direction for a pattern."""

    LONG = "long"  # Buy BITU (2x leveraged long)
    SHORT = "short"  # Buy SBIT (2x leveraged short)


@dataclass
class TradingPattern:
    """A discovered trading pattern."""

    name: str  # Unique identifier, e.g., "late_afternoon_strength"
    display_name: str  # Human readable, e.g., "Late Afternoon Strength"
    signal: PatternSignal  # LONG or SHORT
    instrument: str  # "BITU" or "SBIT"

    # Timing
    entry_time: str  # "HH:MM" format, e.g., "15:30"
    exit_time: str  # "HH:MM" format, e.g., "15:55"

    # Conditions (optional filters)
    conditions: List[str] = field(default_factory=list)
    # e.g., ["day_of_week == Monday", "btc_overnight_change < -0.02"]

    # Statistics from discovery
    confidence: float = 0.0  # Win rate, 0.0-1.0
    sample_size: int = 0  # Number of historical occurrences
    expected_edge: float = 0.0  # Expected return percentage

    # Lifecycle tracking
    status: PatternStatus = PatternStatus.CANDIDATE
    discovered_at: str = ""  # ISO datetime
    last_validated: str = ""  # ISO datetime
    validation_trades: int = 0  # Number of paper trades completed
    validation_pnl: float = 0.0  # Cumulative paper trade P&L

    # Priority (lower = higher priority, like our existing signals)
    priority: int = 100

    # Source tracking
    source: str = "llm_discovery"  # "manual", "llm_discovery", "backtest"
    notes: str = ""

    def __post_init__(self):
        if not self.discovered_at:
            self.discovered_at = get_et_now().isoformat()
        if isinstance(self.status, str):
            self.status = PatternStatus(self.status)
        if isinstance(self.signal, str):
            self.signal = PatternSignal(self.signal)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        d = asdict(self)
        d["status"] = self.status.value
        d["signal"] = self.signal.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TradingPattern":
        """Create from dictionary."""
        return cls(**data)

    def should_trade_now(self, current_time: datetime) -> bool:
        """Check if this pattern should trigger a trade right now."""
        if self.status != PatternStatus.LIVE:
            return False

        entry_hour, entry_min = map(int, self.entry_time.split(":"))

        # Check if we're within 2 minutes of entry time
        if current_time.hour == entry_hour:
            if entry_min <= current_time.minute < entry_min + 2:
                # Check conditions
                return self._check_conditions(current_time)

        return False

    def should_exit_now(self, current_time: datetime) -> bool:
        """Check if this pattern should exit right now."""
        exit_hour, exit_min = map(int, self.exit_time.split(":"))

        # Check if we're within 2 minutes of exit time
        if current_time.hour == exit_hour:
            if exit_min <= current_time.minute < exit_min + 2:
                return True

        return False

    def _check_conditions(self, current_time: datetime) -> bool:
        """Evaluate pattern conditions."""
        if not self.conditions:
            return True

        # Simple condition evaluation
        for condition in self.conditions:
            if "day_of_week" in condition:
                day_name = current_time.strftime("%A")
                if "Monday" in condition and day_name != "Monday":
                    return False
                if "Tuesday" in condition and day_name != "Tuesday":
                    return False
                if "Wednesday" in condition and day_name != "Wednesday":
                    return False
                if "Thursday" in condition and day_name != "Thursday":
                    return False
                if "Friday" in condition and day_name != "Friday":
                    return False

            # More conditions can be added here
            # e.g., btc_overnight_change, options_week, end_of_month

        return True


@dataclass
class PatternRegistry:
    """Registry for storing and managing discovered patterns."""

    patterns: Dict[str, TradingPattern] = field(default_factory=dict)
    patterns_file: Path = PATTERNS_FILE
    last_loaded: Optional[str] = None

    def __post_init__(self):
        # Ensure patterns directory exists
        self.patterns_file.parent.mkdir(parents=True, exist_ok=True)
        self.load()

    def load(self) -> None:
        """Load patterns from JSON file."""
        if self.patterns_file.exists():
            try:
                with open(self.patterns_file) as f:
                    data = json.load(f)

                self.patterns = {}
                for name, pattern_data in data.get("patterns", {}).items():
                    self.patterns[name] = TradingPattern.from_dict(pattern_data)

                self.last_loaded = get_et_now().isoformat()
                logger.info(
                    "Loaded patterns from registry",
                    count=len(self.patterns),
                    file=str(self.patterns_file),
                )
            except Exception as e:
                logger.error("Failed to load patterns", error=str(e))
                self.patterns = {}
        else:
            logger.info("No patterns file found, starting fresh")
            self.patterns = {}

    def save(self) -> None:
        """Save patterns to JSON file."""
        try:
            data = {
                "version": "1.0",
                "updated_at": get_et_now().isoformat(),
                "patterns": {name: p.to_dict() for name, p in self.patterns.items()},
            }

            with open(self.patterns_file, "w") as f:
                json.dump(data, f, indent=2)

            logger.info(
                "Saved patterns to registry",
                count=len(self.patterns),
                file=str(self.patterns_file),
            )
        except Exception as e:
            logger.error("Failed to save patterns", error=str(e))

    def add_pattern(self, pattern: TradingPattern) -> None:
        """Add or update a pattern."""
        self.patterns[pattern.name] = pattern
        self.save()
        logger.info(
            "Added pattern to registry",
            name=pattern.name,
            status=pattern.status.value,
        )

    def remove_pattern(self, name: str) -> bool:
        """Remove a pattern by name."""
        if name in self.patterns:
            del self.patterns[name]
            self.save()
            logger.info("Removed pattern from registry", name=name)
            return True
        return False

    def get_pattern(self, name: str) -> Optional[TradingPattern]:
        """Get a pattern by name."""
        return self.patterns.get(name)

    def get_live_patterns(self) -> List[TradingPattern]:
        """Get all patterns with LIVE status."""
        return [p for p in self.patterns.values() if p.status == PatternStatus.LIVE]

    def get_paper_patterns(self) -> List[TradingPattern]:
        """Get all patterns being paper traded."""
        return [p for p in self.patterns.values() if p.status == PatternStatus.PAPER]

    def get_candidate_patterns(self) -> List[TradingPattern]:
        """Get all candidate patterns."""
        return [p for p in self.patterns.values() if p.status == PatternStatus.CANDIDATE]

    def promote_pattern(self, name: str, to_status: PatternStatus) -> bool:
        """Promote a pattern to a new status."""
        pattern = self.patterns.get(name)
        if pattern:
            old_status = pattern.status
            pattern.status = to_status
            pattern.last_validated = get_et_now().isoformat()
            self.save()
            logger.info(
                "Promoted pattern",
                name=name,
                from_status=old_status.value,
                to_status=to_status.value,
            )
            return True
        return False

    def retire_pattern(self, name: str, reason: str = "") -> bool:
        """Retire a pattern that's no longer working."""
        pattern = self.patterns.get(name)
        if pattern:
            pattern.status = PatternStatus.RETIRED
            pattern.notes = f"Retired: {reason}" if reason else "Retired"
            pattern.last_validated = get_et_now().isoformat()
            self.save()
            logger.info("Retired pattern", name=name, reason=reason)
            return True
        return False

    def get_active_signal(self, current_time: datetime) -> Optional[TradingPattern]:
        """Get the highest priority pattern that should trade now."""
        live_patterns = self.get_live_patterns()

        # Sort by priority (lower = higher priority)
        live_patterns.sort(key=lambda p: p.priority)

        for pattern in live_patterns:
            if pattern.should_trade_now(current_time):
                return pattern

        return None


class PatternAnalyzer:
    """Analyzes historical data using LLM to discover patterns."""

    # Default prompt template for pattern discovery
    DISCOVERY_PROMPT_TEMPLATE = """You are a quantitative analyst. Analyze this market data and discover exploitable trading patterns.

### IBIT Day-of-Week Performance (Last {lookback_days} Days)

{day_of_week_data}

### IBIT Hourly Performance

{hourly_data}

### BTC Overnight Movement vs Next Day IBIT

{overnight_data}

### Currently Active Patterns
{active_patterns}

### Your Task
Analyze this data and output ONLY a JSON object with discovered patterns. Each pattern must have:
- name: lowercase_with_underscores
- display_name: Human Readable Name
- signal: "long" or "short"
- instrument: "BITU" (for long) or "SBIT" (for short)
- entry_time: "HH:MM" format
- exit_time: "HH:MM" format
- conditions: list of condition strings (can be empty)
- confidence: win rate as decimal (0.0-1.0)
- sample_size: number of historical occurrences
- expected_edge: expected return percentage
- priority: integer (lower = higher priority, suggest 50-150)
- notes: brief explanation

Only include patterns with:
- Sample size >= 20
- Confidence >= 0.52 (52% win rate)
- Expected edge >= 0.15% per trade

Output format:
```json
{{
  "patterns": [
    {{
      "name": "example_pattern",
      "display_name": "Example Pattern",
      "signal": "long",
      "instrument": "BITU",
      "entry_time": "09:35",
      "exit_time": "15:55",
      "conditions": [],
      "confidence": 0.58,
      "sample_size": 62,
      "expected_edge": 0.35,
      "priority": 75,
      "notes": "Explanation of the pattern"
    }}
  ],
  "analysis_notes": "Brief summary of findings"
}}
```"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-sonnet-4-20250514",
        lookback_days: int = 90,
    ):
        """Initialize the pattern analyzer.

        Args:
            api_key: Anthropic API key (or set ANTHROPIC_API_KEY env var)
            model: Model to use for analysis
            lookback_days: Days of historical data to analyze
        """
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.model = model
        self.lookback_days = lookback_days

        if not self.api_key:
            logger.warning("No Anthropic API key configured for pattern analysis")

    def format_market_data(
        self,
        day_of_week_stats: Dict[str, Dict],
        hourly_stats: Dict[str, Dict],
        overnight_stats: Dict[str, Dict],
    ) -> Dict[str, str]:
        """Format market data for the LLM prompt.

        Args:
            day_of_week_stats: {day: {avg_return, win_rate, samples}}
            hourly_stats: {hour: {avg_return, win_rate, samples}}
            overnight_stats: {bucket: {avg_return, win_rate, samples}}

        Returns:
            Dict with formatted strings for each data section
        """
        # Format day of week data
        dow_lines = ["| Day       | Avg Return % | Win Rate % | Sample Size |"]
        dow_lines.append("|-----------|--------------|------------|-------------|")
        for day, stats in day_of_week_stats.items():
            dow_lines.append(
                f"| {day:<9} | {stats['avg_return']:+.2f}%       | {stats['win_rate']:.0f}%        | {stats['samples']:<11} |"
            )
        day_of_week_data = "\n".join(dow_lines)

        # Format hourly data
        hourly_lines = ["| Hour (ET) | Avg Return % | Win Rate % | Sample Size |"]
        hourly_lines.append("|-----------|--------------|------------|-------------|")
        for hour, stats in hourly_stats.items():
            hourly_lines.append(
                f"| {hour:<9} | {stats['avg_return']:+.2f}%       | {stats['win_rate']:.0f}%        | {stats['samples']:<11} |"
            )
        hourly_data = "\n".join(hourly_lines)

        # Format overnight data
        overnight_lines = ["| BTC Overnight | IBIT Next Day Avg | Win Rate | Samples |"]
        overnight_lines.append("|---------------|-------------------|----------|---------|")
        for bucket, stats in overnight_stats.items():
            overnight_lines.append(
                f"| {bucket:<13} | {stats['avg_return']:+.1f}%             | {stats['win_rate']:.0f}%      | {stats['samples']:<7} |"
            )
        overnight_data = "\n".join(overnight_lines)

        return {
            "day_of_week_data": day_of_week_data,
            "hourly_data": hourly_data,
            "overnight_data": overnight_data,
        }

    def build_prompt(
        self,
        day_of_week_stats: Dict[str, Dict],
        hourly_stats: Dict[str, Dict],
        overnight_stats: Dict[str, Dict],
        active_patterns: List[TradingPattern],
    ) -> str:
        """Build the full discovery prompt."""
        formatted = self.format_market_data(day_of_week_stats, hourly_stats, overnight_stats)

        # Format active patterns
        if active_patterns:
            pattern_lines = []
            for p in active_patterns:
                pattern_lines.append(
                    f"- {p.display_name}: {p.signal.value} {p.instrument} "
                    f"at {p.entry_time}-{p.exit_time} "
                    f"(edge: {p.expected_edge:.2f}%)"
                )
            active_patterns_str = "\n".join(pattern_lines)
        else:
            active_patterns_str = "None currently active"

        return self.DISCOVERY_PROMPT_TEMPLATE.format(
            lookback_days=self.lookback_days,
            day_of_week_data=formatted["day_of_week_data"],
            hourly_data=formatted["hourly_data"],
            overnight_data=formatted["overnight_data"],
            active_patterns=active_patterns_str,
        )

    async def analyze(
        self,
        day_of_week_stats: Dict[str, Dict],
        hourly_stats: Dict[str, Dict],
        overnight_stats: Dict[str, Dict],
        active_patterns: Optional[List[TradingPattern]] = None,
    ) -> List[TradingPattern]:
        """Run LLM analysis and return discovered patterns.

        Args:
            day_of_week_stats: Day of week performance data
            hourly_stats: Hourly performance data
            overnight_stats: BTC overnight vs IBIT next day data
            active_patterns: Currently active patterns to avoid duplicates

        Returns:
            List of newly discovered TradingPattern objects
        """
        if not self.api_key:
            logger.error("Cannot run analysis: no API key configured")
            return []

        try:
            import anthropic

            client = anthropic.Anthropic(api_key=self.api_key)

            prompt = self.build_prompt(
                day_of_week_stats,
                hourly_stats,
                overnight_stats,
                active_patterns or [],
            )

            logger.info("Running LLM pattern analysis", model=self.model)

            message = client.messages.create(
                model=self.model,
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )

            response_text = message.content[0].text

            # Extract JSON from response
            patterns = self._parse_response(response_text)

            logger.info("Pattern analysis complete", patterns_found=len(patterns))
            return patterns

        except ImportError:
            logger.error("anthropic package not installed. Run: pip install anthropic")
            return []
        except Exception as e:
            logger.error("Pattern analysis failed", error=str(e))
            return []

    def _parse_response(self, response_text: str) -> List[TradingPattern]:
        """Parse LLM response into TradingPattern objects."""
        patterns = []

        try:
            # Find JSON in response
            import re

            json_match = re.search(r"```json\s*(.*?)\s*```", response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # Try to find raw JSON
                json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
                if json_match:
                    json_str = json_match.group(0)
                else:
                    logger.warning("No JSON found in LLM response")
                    return []

            data = json.loads(json_str)

            for p_data in data.get("patterns", []):
                try:
                    pattern = TradingPattern(
                        name=p_data["name"],
                        display_name=p_data["display_name"],
                        signal=PatternSignal(p_data["signal"]),
                        instrument=p_data["instrument"],
                        entry_time=p_data["entry_time"],
                        exit_time=p_data["exit_time"],
                        conditions=p_data.get("conditions", []),
                        confidence=p_data.get("confidence", 0.0),
                        sample_size=p_data.get("sample_size", 0),
                        expected_edge=p_data.get("expected_edge", 0.0),
                        priority=p_data.get("priority", 100),
                        status=PatternStatus.CANDIDATE,
                        source="llm_discovery",
                        notes=p_data.get("notes", ""),
                    )
                    patterns.append(pattern)
                except Exception as e:
                    logger.warning("Failed to parse pattern", error=str(e), data=p_data)

            if "analysis_notes" in data:
                logger.info("LLM analysis notes", notes=data["analysis_notes"])

        except json.JSONDecodeError as e:
            logger.error("Failed to parse JSON from LLM response", error=str(e))
        except Exception as e:
            logger.error("Failed to parse LLM response", error=str(e))

        return patterns


class MarketDataCollector:
    """Collects and aggregates historical market data for pattern analysis."""

    def __init__(self, lookback_days: int = 90):
        """Initialize the data collector.

        Args:
            lookback_days: Number of days of historical data to collect
        """
        self.lookback_days = lookback_days

    def collect_from_alpaca(
        self,
        alpaca_key: Optional[str] = None,
        alpaca_secret: Optional[str] = None,
    ) -> Dict[str, Dict]:
        """Collect historical data from Alpaca API.

        Returns:
            Dict with day_of_week_stats, hourly_stats, overnight_stats
        """
        from .data_providers import AlpacaProvider

        provider = AlpacaProvider(alpaca_key, alpaca_secret)
        if not provider.is_available():
            logger.error("Alpaca API not configured")
            return {}

        # Calculate date range
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=self.lookback_days)).strftime("%Y-%m-%d")

        # Fetch IBIT daily bars
        ibit_bars = provider.get_historical_bars("IBIT", start_date, end_date, "1Day")
        if not ibit_bars:
            logger.error("Failed to fetch IBIT historical data")
            return {}

        # Fetch BTC hourly bars for overnight analysis
        btc_bars = provider.get_crypto_bars("BTC/USD", start_date, end_date, "1Hour")

        # Process data
        day_of_week_stats = self._calc_day_of_week_stats(ibit_bars)
        overnight_stats = self._calc_overnight_stats(ibit_bars, btc_bars or [])

        # For hourly stats, we need intraday data
        hourly_bars = provider.get_historical_bars("IBIT", start_date, end_date, "1Hour")
        hourly_stats = self._calc_hourly_stats(hourly_bars or [])

        return {
            "day_of_week_stats": day_of_week_stats,
            "hourly_stats": hourly_stats,
            "overnight_stats": overnight_stats,
        }

    def _calc_day_of_week_stats(self, bars: List[Dict]) -> Dict[str, Dict]:
        """Calculate day-of-week performance statistics."""
        from collections import defaultdict

        day_returns = defaultdict(list)

        for bar in bars:
            # Parse timestamp
            timestamp = bar.get("t", "")
            if isinstance(timestamp, str):
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            else:
                dt = timestamp

            day_name = dt.strftime("%A")

            # Calculate daily return
            open_price = bar.get("o", 0)
            close_price = bar.get("c", 0)
            if open_price > 0:
                daily_return = ((close_price - open_price) / open_price) * 100
                day_returns[day_name].append(daily_return)

        # Calculate stats for each day
        stats = {}
        for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
            returns = day_returns.get(day, [])
            if returns:
                avg_return = sum(returns) / len(returns)
                win_rate = (sum(1 for r in returns if r > 0) / len(returns)) * 100
                stats[day] = {
                    "avg_return": avg_return,
                    "win_rate": win_rate,
                    "samples": len(returns),
                }
            else:
                stats[day] = {"avg_return": 0, "win_rate": 50, "samples": 0}

        return stats

    def _calc_hourly_stats(self, bars: List[Dict]) -> Dict[str, Dict]:
        """Calculate hourly performance statistics."""
        from collections import defaultdict

        hour_returns = defaultdict(list)

        for bar in bars:
            timestamp = bar.get("t", "")
            if isinstance(timestamp, str):
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            else:
                dt = timestamp

            # Only include market hours (9:30 AM - 4:00 PM ET)
            hour = dt.hour
            if 9 <= hour <= 15:
                hour_str = f"{hour:02d}:00" if hour != 9 else "09:30"

                open_price = bar.get("o", 0)
                close_price = bar.get("c", 0)
                if open_price > 0:
                    hourly_return = ((close_price - open_price) / open_price) * 100
                    hour_returns[hour_str].append(hourly_return)

        # Calculate stats
        stats = {}
        for hour_str in ["09:30", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00"]:
            returns = hour_returns.get(hour_str, [])
            if returns:
                avg_return = sum(returns) / len(returns)
                win_rate = (sum(1 for r in returns if r > 0) / len(returns)) * 100
                stats[hour_str] = {
                    "avg_return": avg_return,
                    "win_rate": win_rate,
                    "samples": len(returns),
                }
            else:
                stats[hour_str] = {"avg_return": 0, "win_rate": 50, "samples": 0}

        return stats

    def _calc_overnight_stats(self, ibit_bars: List[Dict], btc_bars: List[Dict]) -> Dict[str, Dict]:
        """Calculate BTC overnight move vs IBIT next day performance."""
        from collections import defaultdict

        # Build BTC price index by date
        btc_by_date = {}
        for bar in btc_bars:
            timestamp = bar.get("t", "")
            if isinstance(timestamp, str):
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            else:
                dt = timestamp

            date_str = dt.strftime("%Y-%m-%d")
            hour = dt.hour

            if date_str not in btc_by_date:
                btc_by_date[date_str] = {"close_16": None, "open_930": None}

            # 4 PM close and 9:30 AM open (approximate with hourly bars)
            if hour == 16:
                btc_by_date[date_str]["close_16"] = bar.get("c", 0)
            elif hour == 9:
                btc_by_date[date_str]["open_930"] = bar.get("o", 0)

        # Build IBIT returns by date
        ibit_returns = {}
        for bar in ibit_bars:
            timestamp = bar.get("t", "")
            if isinstance(timestamp, str):
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            else:
                dt = timestamp

            date_str = dt.strftime("%Y-%m-%d")
            open_price = bar.get("o", 0)
            close_price = bar.get("c", 0)
            if open_price > 0:
                ibit_returns[date_str] = ((close_price - open_price) / open_price) * 100

        # Calculate overnight BTC moves and correlate with next day IBIT
        bucket_returns = defaultdict(list)

        sorted_dates = sorted(ibit_returns.keys())
        for i, date_str in enumerate(sorted_dates[1:], 1):
            prev_date = sorted_dates[i - 1]

            # Get BTC overnight change (4 PM prev day to 9:30 AM current day)
            prev_btc = btc_by_date.get(prev_date, {})
            curr_btc = btc_by_date.get(date_str, {})

            prev_close = prev_btc.get("close_16")
            curr_open = curr_btc.get("open_930")

            if prev_close and curr_open and prev_close > 0:
                btc_overnight = ((curr_open - prev_close) / prev_close) * 100

                # Categorize into buckets
                if btc_overnight <= -2:
                    bucket = "Down > 2%"
                elif btc_overnight <= -1:
                    bucket = "Down 1-2%"
                elif btc_overnight <= 0:
                    bucket = "Down 0-1%"
                elif btc_overnight <= 1:
                    bucket = "Up 0-1%"
                elif btc_overnight <= 2:
                    bucket = "Up 1-2%"
                else:
                    bucket = "Up > 2%"

                ibit_return = ibit_returns.get(date_str)
                if ibit_return is not None:
                    bucket_returns[bucket].append(ibit_return)

        # Calculate stats for each bucket
        stats = {}
        bucket_order = ["Down > 2%", "Down 1-2%", "Down 0-1%", "Up 0-1%", "Up 1-2%", "Up > 2%"]
        for bucket in bucket_order:
            returns = bucket_returns.get(bucket, [])
            if returns:
                avg_return = sum(returns) / len(returns)
                win_rate = (sum(1 for r in returns if r > 0) / len(returns)) * 100
                stats[bucket] = {
                    "avg_return": avg_return,
                    "win_rate": win_rate,
                    "samples": len(returns),
                }
            else:
                stats[bucket] = {"avg_return": 0, "win_rate": 50, "samples": 0}

        return stats


def get_data_collector(lookback_days: int = 90) -> MarketDataCollector:
    """Get a data collector instance."""
    return MarketDataCollector(lookback_days=lookback_days)


# Singleton instances
_registry_instance: Optional[PatternRegistry] = None
_analyzer_instance: Optional[PatternAnalyzer] = None


def get_pattern_registry(patterns_file: Optional[Path] = None) -> PatternRegistry:
    """Get or create pattern registry singleton."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = PatternRegistry(patterns_file=patterns_file or PATTERNS_FILE)
    return _registry_instance


def get_pattern_analyzer(
    api_key: Optional[str] = None,
    model: str = "claude-sonnet-4-20250514",
) -> PatternAnalyzer:
    """Get or create pattern analyzer singleton."""
    global _analyzer_instance
    if _analyzer_instance is None:
        _analyzer_instance = PatternAnalyzer(api_key=api_key, model=model)
    return _analyzer_instance
