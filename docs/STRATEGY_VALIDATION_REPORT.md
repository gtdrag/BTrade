# IBIT Dip Strategy Validation Report

**Date:** December 13, 2025
**Status:** STRATEGY NOT VALIDATED - DO NOT USE FOR LIVE TRADING

---

## Executive Summary

The "10 AM ET Dip" trading strategy for IBIT, as described in the original PRD, has been thoroughly backtested using both daily OHLC data and 5-minute intraday data. **The strategy does not produce the returns claimed in the PRD and is not recommended for live trading.**

| Metric | PRD Claim | Daily Backtest | Intraday Backtest |
|--------|-----------|----------------|-------------------|
| Win Rate | 66.1% | 45.9% | **33.3%** |
| Total Return | +61.8% | -17.5% | **-22.7%** |
| Period | Jun-Dec 2025 | Jun-Dec 2025 | Sep-Dec 2025 |

---

## Methodology

### 1. Daily OHLC Backtesting

- **Data Source:** Yahoo Finance daily bars
- **Logic:** Check if daily LOW was >= threshold below OPEN, simulate entry at threshold level, exit at CLOSE
- **Periods Tested:** Multiple ranges from Jan 2024 to Dec 2025

### 2. Intraday Backtesting (More Accurate)

- **Data Source:** Yahoo Finance 5-minute bars (last 60 days available)
- **Logic:**
  1. Capture OPEN price at 9:30 AM
  2. Monitor 10:00-10:59 AM window for dip >= threshold
  3. If dip occurs, simulate entry at threshold level
  4. Exit at market close (last bar of day)
- **Period:** September 19, 2025 - December 12, 2025 (60 trading days)

---

## Findings

### Intraday Analysis (Most Reliable)

Using actual 5-minute data over 60 trading days:

```
Trades triggered (dip >= 0.6%): 30
Win rate: 33.3%
Average P&L: -0.76%
Total P&L: -22.70%
Best trade: +3.60%
Worst trade: -5.01%
```

### Threshold Analysis (Intraday Data)

| Threshold | Trades | Win Rate | Total P&L | Avg P&L |
|-----------|--------|----------|-----------|---------|
| 0.3% | 47 | 49% | -21.2% | -0.45% |
| 0.5% | 36 | 44% | -17.5% | -0.49% |
| 0.6% | 30 | 33% | -22.7% | -0.76% |
| 0.8% | 25 | 40% | -17.8% | -0.71% |
| 1.0% | 21 | 38% | -19.1% | -0.91% |
| 1.5% | 8 | 38% | -5.9% | -0.74% |

**Conclusion:** No threshold produces positive returns.

### Performance by Weekday

| Day | Avg P&L | Win Rate |
|-----|---------|----------|
| Monday | -0.02% | 25% |
| Tuesday | -0.60% | 33% |
| Wednesday | -0.27% | 40% |
| Thursday | -1.51% | 22% |
| Friday | -0.69% | 50% |

**Conclusion:** No day of week shows consistent profitability.

### Hourly Pattern Analysis

Analysis of hourly returns shows the 10:00 AM hour has the **worst** average return:

| Hour (ET) | Avg Return | Positive % |
|-----------|------------|------------|
| 9:00 | -0.09% | - |
| 10:00 | **-0.14%** | 43% |
| 11:00 | +0.06% | - |
| 12:00 | -0.07% | - |
| 13:00 | -0.03% | - |
| 14:00 | -0.04% | - |
| 15:00 | -0.03% | - |

**Conclusion:** The "10 AM recovery" pattern claimed in the PRD does not exist in the data.

---

## Daily OHLC Analysis

### Configurations That Beat Buy & Hold

Only 7 of 84 tested configurations beat buy & hold, and only in DOWN markets:

| Period | Threshold | Monday | Trades | Win Rate | Return | B&H | vs B&H |
|--------|-----------|--------|--------|----------|--------|-----|--------|
| Jun-Dec 2025 | 0.3% | Enabled | 117 | 54.7% | +6.9% | -13.3% | +20.2% |
| Jun-Dec 2025 | 0.3% | Disabled | 101 | 54.5% | -1.8% | -13.3% | +11.5% |

**Conclusion:** Strategy may have slight value in down markets (lose less than B&H) but does not generate the +60% returns claimed.

### Profitable Configurations

Only 9 of 84 configurations showed positive returns, all with modest gains:

| Period | Best Return | Best Win Rate |
|--------|-------------|---------------|
| Jan-Jun 2025 | +7.5% | 54.8% |
| Jun-Dec 2025 | +6.9% | 54.7% |

---

## Root Cause Analysis

### Why the PRD Claims Don't Match Reality

1. **PRD was dated December 12, 2025** - It claimed to have backtested data through "Dec 2025" which was impossible at the time of creation

2. **Hypothetical or simulated data** - The +61.8% return was likely generated from:
   - Theoretical calculations
   - Overfitted historical data
   - Incorrect methodology

3. **Daily OHLC limitations** - Daily bars cannot accurately capture:
   - Exact timing of the dip within 10:00-10:59 AM
   - Whether price actually recovered by close vs. continued down
   - The execution price we would actually get

4. **Pattern doesn't exist** - The fundamental premise that "IBIT dips at 10 AM and recovers by close" is not supported by actual intraday data

---

## Recommendations

### Immediate Actions

1. **DO NOT deploy this strategy with real money**
2. **DO NOT enable live trading mode**
3. Consider the bot code as educational/demonstration only

### If You Want to Proceed

1. **Collect your own intraday data** - Use E*TRADE API to collect 1-minute data going forward
2. **Paper trade for 3+ months** - Use dry-run mode to validate in real-time
3. **Require positive results** - Only consider live trading if paper trading shows:
   - Win rate > 55%
   - Positive total P&L
   - Outperformance vs buy-and-hold

### Alternative Approaches

If you're interested in IBIT trading, consider:

1. **Simple buy-and-hold** - Has historically outperformed this strategy
2. **Different timeframes** - Weekly or monthly rebalancing strategies
3. **Different assets** - The dip strategy might work for other securities
4. **Professional advice** - Consult a financial advisor before automated trading

---

## Technical Notes

### Data Sources Used

- Yahoo Finance (yfinance) - Daily and intraday data
- 5-minute bars: 60 days of history available
- 1-hour bars: Full IBIT history available

### Code Quality

The bot implementation is complete and functional:
- All 34 unit tests pass
- Dashboard works correctly
- Backtesting engine properly validated
- E*TRADE integration ready (not tested with live account)

The code itself is not the problem - the strategy is.

---

## Appendix: Sample Trade Results

### Intraday Backtest - All 30 Trades (0.6% threshold)

| Date | Weekday | Max Dip | Entry | Close | P&L |
|------|---------|---------|-------|-------|-----|
| 2025-09-25 | Thu | 0.65% | $63.00 | $62.09 | -1.44% |
| 2025-10-07 | Tue | 1.82% | $70.55 | $69.10 | -2.05% |
| 2025-10-09 | Thu | 2.20% | $69.71 | $68.74 | -1.40% |
| 2025-10-10 | Fri | 1.29% | $68.91 | $66.17 | -3.98% |
| 2025-10-17 | Fri | 0.98% | $59.54 | $60.47 | **+1.56%** |
| 2025-10-24 | Fri | 1.34% | $62.76 | $62.83 | **+0.10%** |
| 2025-11-26 | Wed | 0.76% | $49.25 | $51.02 | **+3.60%** |
| ... | ... | ... | ... | ... | ... |

(Full results: 10 wins, 20 losses)

---

**Report Generated:** December 13, 2025
**Validation Status:** FAILED
**Recommendation:** Do not use for live trading
