"""
Configuration constants for the strategy review module.

Contains strategy parameters, tool definitions, and the review prompt template.
"""

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
                "description": (
                    "The specific metric or indicator to track "
                    "(e.g., 'Thursday win rate', 'drawdown frequency')"
                ),
            },
            "current_value": {
                "type": "string",
                "description": (
                    "Current state of this metric " "(e.g., '45% win rate', '3 drawdowns > 5%')"
                ),
            },
            "threshold": {
                "type": "string",
                "description": (
                    "When should this trigger concern? "
                    "(e.g., 'if drops below 40%', 'if exceeds 5 occurrences')"
                ),
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
