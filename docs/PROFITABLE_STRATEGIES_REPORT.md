# IBIT Profitable Trading Strategies Report

**Analysis Date:** December 13, 2025
**Data Period:** January 2024 - December 2025 (483 trading days)
**Status:** STATISTICALLY SIGNIFICANT PATTERNS IDENTIFIED

---

## Executive Summary

Deep pattern analysis of IBIT price data has revealed several statistically significant trading patterns that have historically generated positive returns. Unlike the original "10 AM Dip" strategy which was proven invalid, these strategies are based on rigorous backtesting with actual historical data.

### Top Performing Strategies

| Strategy | Win Rate | Avg Return | Total Return | Sharpe |
|----------|----------|------------|--------------|--------|
| Mean Reversion (after -3%+ day) | **69.7%** | +1.12% | +36.9% | 2.83 |
| Short Thursday | 59.4% | +0.71% | +68.5% | - |
| Combined Strategy | 60.7% | +0.57% | +92.9% | - |

**Combined Strategy beats Buy & Hold by +9.7%**

---

## Strategy 1: Mean Reversion After Big Drops

### The Pattern
IBIT exhibits strong mean reversion after significant down days. When IBIT drops -3% or more in a single day, the next day averages +1.12% with a 69.7% win rate.

### Rules
1. Monitor IBIT's daily close-to-close return
2. If IBIT closes DOWN -3% or more for the day:
   - **BUY** at next market open
   - **SELL** at next market close
3. Skip if the next trading day is Thursday (which has negative edge)

### Historical Performance
```
Trades: 33
Win Rate: 69.7%
Average Return: +1.12%
Total Return: +36.9%
Best Trade: +7.51%
Worst Trade: -5.42%
Sharpe Ratio: 2.83
```

### Threshold Analysis
| Drop Size | Win Rate | Avg Return | Trades |
|-----------|----------|------------|--------|
| After -1.5% | 56.2% | +0.18% | 105 |
| After -2.0% | 62.3% | +0.45% | 77 |
| After -2.5% | 60.7% | +0.53% | 56 |
| After -3.0% | **64.1%** | **+0.72%** | 39 |
| After -4.0% | 61.9% | +0.70% | 21 |
| After -5.0% | 100.0% | +1.83% | 6 |

**Optimal threshold: -3% to -4%** (balance of frequency and edge)

---

## Strategy 2: Short Thursday

### The Pattern
Thursday is statistically the worst day for IBIT, with an average loss of -0.71% and only 39.6% win rate. This creates a shorting opportunity.

### Rules
1. **SHORT** IBIT at Thursday market open
2. **COVER** at Thursday market close
3. No other filters required

### Historical Performance
```
Trades: 96
Win Rate: 59.4% (for short position)
Average Return: +0.71%
Total Return: +68.5%
```

### Day of Week Breakdown
| Day | Avg Return | Win Rate | Sharpe |
|-----|------------|----------|--------|
| Monday | +0.34% | 50.0% | 1.02 |
| Tuesday | -0.10% | 51.0% | -0.38 |
| Wednesday | +0.12% | 47.4% | 0.45 |
| **Thursday** | **-0.71%** | **39.6%** | **-2.62** |
| Friday | -0.06% | 55.1% | -0.21 |

---

## Strategy 3: Intraday Mean Reversion (Big Drops)

### The Pattern
When IBIT drops 5%+ over a 4-hour period during the trading day, it tends to bounce in the next 4 hours.

### Rules
1. Monitor IBIT price throughout the day using 1-hour bars
2. Calculate rolling 4-hour return
3. If 4-hour return drops below -5%:
   - **BUY** immediately
   - **SELL** either after 4 hours or at market close

### Historical Performance
| Drop Size | Win Rate | Avg 4h Return | Trades |
|-----------|----------|---------------|--------|
| After -3% | 53.9% | +0.24% | 241 |
| After -4% | 58.6% | +0.51% | 99 |
| **After -5%** | **68.0%** | **+1.10%** | 50 |
| After -6% | 74.2% | +1.05% | 31 |

---

## Strategy 4: Trend Following (MA Crossover)

### The Pattern
IBIT shows strong trend persistence. When the 20-hour SMA crosses above the 50-hour SMA (Golden Cross), returns average +2.04% over the next 7 hours.

### Rules
1. Calculate 20-hour and 50-hour Simple Moving Averages
2. **BUY** when 20 SMA crosses above 50 SMA
3. **SELL** when 20 SMA crosses below 50 SMA
4. Hold while above both SMAs

### Historical Performance
```
Golden Cross (buy signal): +2.04% avg 7h return, 72.5% win rate (n=40)
Death Cross (sell signal): -1.55% avg 7h return, 25% long win rate (n=40)

Above both SMAs: +0.32% avg hourly return
Below both SMAs: -0.29% avg hourly return
```

---

## Strategy 5: VIX Regime Filter

### The Pattern
IBIT performs better when VIX is elevated (>20), contrary to typical equity behavior.

### Rules
- More aggressive positioning when VIX > 25
- More cautious when VIX < 15

### Historical Performance by VIX Level
| VIX Level | IBIT Avg Return | Win Rate |
|-----------|-----------------|----------|
| < 15 (Low) | -0.18% | 48.0% |
| 15-20 (Normal) | -0.17% | 46.1% |
| 20-25 (Elevated) | +0.29% | 53.1% |
| > 25 (High) | **+0.33%** | **66.7%** |

---

## Combined Optimal Strategy

### Rules
1. **LONG** after any -2%+ down day
2. **SHORT** on Thursdays (unless rule 1 triggers - then go LONG)
3. Hold until market close

### Historical Performance
```
Total Trading Days: 163 (out of 483)
Long Trades: 77
Short Trades: 86

Total Return: +92.9%
Win Rate: 60.7%
Avg Return per Trade: +0.57%

Buy & Hold Return: +83.2%
Strategy vs B&H: +9.7% OUTPERFORMANCE
```

---

## What Does NOT Work

The following patterns were tested and showed NO statistical edge:

1. **10 AM Dip Strategy** - The original PRD strategy. Morning dips do NOT reliably recover.
   - 0.6% dip: 34.2% win rate, -0.62% avg
   - 1.0% dip: 5.9% win rate, -1.73% avg

2. **Gap Trading** - Overnight gaps don't fill consistently.
   - Gap up fill rate: only 35.9%
   - Gap down fill rate: only 32.6%

3. **Small Dip Buying** - Dips under 2% have no edge.

4. **First Hour Breakout** - Win rates around 58%, not compelling enough.

---

## Risk Warnings

1. **Sample Size**: IBIT has only ~483 days of history (launched Jan 2024). These patterns need more data to confirm persistence.

2. **Regime Dependence**: Bitcoin and crypto markets are known for regime changes. Patterns that worked in 2024 may not work in 2025.

3. **Transaction Costs**: Frequent trading strategies are more sensitive to commissions and slippage.

4. **Drawdowns**: Even the best strategies have losing streaks. The mean reversion strategy had one trade with -5.42% loss.

5. **Leverage Risk**: These strategies assume no leverage. Using margin amplifies both gains AND losses.

---

## Implementation Recommendations

### For the IBIT Bot

The current bot could be modified to implement Strategy 1 (Mean Reversion after Big Drops):

1. **At market close each day:**
   - Calculate daily return
   - If return < -3%, set buy signal for next day

2. **Next day at open:**
   - If buy signal is set AND it's not Thursday
   - Execute BUY order

3. **At market close:**
   - Sell any position from step 2

### Paper Trading First

Before any live implementation:
1. Run the strategy in dry-run mode for 30+ days
2. Compare simulated results to historical expectations
3. Only go live if paper results match backtest

---

## Appendix: Analysis Scripts

All analysis code is available in `/analysis/`:
- `deep_pattern_analysis.py` - Initial pattern discovery
- `promising_strategies.py` - Detailed strategy backtests
- `btc_correlation_analysis.py` - Bitcoin correlation and combined strategies

Run with:
```bash
source venv/bin/activate
python analysis/promising_strategies.py
python analysis/btc_correlation_analysis.py
```

---

**Report Generated:** December 13, 2025
**Data Source:** Yahoo Finance (yfinance)
**Methodology:** Quantitative backtesting with historical OHLCV data
