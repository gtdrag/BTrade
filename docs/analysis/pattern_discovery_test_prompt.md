# Pattern Discovery Test Prompt

Copy and paste this entire prompt into ChatGPT, Claude, DeepSeek, Gemini, or any other AI to test pattern discovery capabilities.

---

## PROMPT START (Copy from here)

You are a quantitative analyst. I'm going to give you real market data for IBIT (Bitcoin ETF) and BTC (Bitcoin spot). Your task is to analyze this data and discover any statistically significant patterns I could exploit for trading.

### IBIT Hourly Performance Data (Last 90 Days)
Average return by hour of trading day:

| Hour (ET) | Avg Return % | Win Rate % | Sample Size |
|-----------|--------------|------------|-------------|
| 09:30     | +0.12%       | 54%        | 62          |
| 10:00     | -0.32%       | 42%        | 62          |
| 11:00     | -0.08%       | 48%        | 62          |
| 12:00     | +0.05%       | 51%        | 62          |
| 13:00     | +0.02%       | 50%        | 62          |
| 14:00     | -0.04%       | 49%        | 62          |
| 15:00     | +0.18%       | 56%        | 62          |
| 15:30     | +0.22%       | 58%        | 62          |

### IBIT Day-of-Week Performance (Last 90 Days)

| Day       | Avg Return % | Win Rate % | Sample Size |
|-----------|--------------|------------|-------------|
| Monday    | +0.35%       | 58%        | 13          |
| Tuesday   | +0.12%       | 54%        | 13          |
| Wednesday | +0.08%       | 52%        | 12          |
| Thursday  | -0.45%       | 38%        | 13          |
| Friday    | +0.15%       | 55%        | 12          |

### BTC Overnight Movement vs Next Day IBIT (Last 90 Days)
(BTC change from 4 PM ET to 9:30 AM ET next day)

| BTC Overnight | IBIT Next Day Avg | Win Rate | Samples |
|---------------|-------------------|----------|---------|
| Down > 2%     | +1.2%             | 71%      | 7       |
| Down 1-2%     | +0.4%             | 58%      | 12      |
| Down 0-1%     | +0.1%             | 52%      | 18      |
| Up 0-1%       | -0.1%             | 48%      | 22      |
| Up 1-2%       | -0.3%             | 44%      | 11      |
| Up > 2%       | -0.8%             | 35%      | 8       |

### BTC Weekend Gap vs Monday IBIT (Last 12 Weeks)

| BTC Weekend Gap | Monday IBIT Avg | Win Rate | Samples |
|-----------------|-----------------|----------|---------|
| Down > 3%       | +1.8%           | 75%      | 4       |
| Down 1-3%       | +0.6%           | 60%      | 5       |
| Flat (-1% to 1%)| +0.2%           | 55%      | 6       |
| Up 1-3%         | -0.4%           | 42%      | 4       |
| Up > 3%         | -1.1%           | 33%      | 3       |

### Day of Month Analysis (Last 6 Months)

| Period              | Avg Return % | Win Rate % | Notes                    |
|---------------------|--------------|------------|--------------------------|
| 1st of month        | +0.45%       | 62%        | Rebalancing flows?       |
| 2nd-7th             | +0.08%       | 51%        | Normal                   |
| 8th-14th            | -0.05%       | 49%        | Normal                   |
| 15th (mid-month)    | +0.22%       | 58%        | Payroll buying?          |
| 16th-21st           | +0.03%       | 50%        | Normal                   |
| Options Expiry Week | -0.18%       | 45%        | 3rd Friday week          |
| Last 3 days         | -0.25%       | 43%        | Window dressing selling? |

### What I Already Trade
1. **10 AM Dump**: Buy SBIT (2x inverse) at 9:35, sell at 10:30 (captures the 10 AM weakness)
2. **Short Thursday**: Buy SBIT on Thursday open, sell at close
3. **Mean Reversion**: After IBIT drops -2%+, buy BITU (2x long) next day

### Your Task
Analyze this data and tell me:

1. **New Patterns**: What other exploitable patterns do you see that I'm NOT already trading?

2. **Pattern Combinations**: Are there combinations that would improve win rate? (e.g., "Thursday + Options Expiry Week" or "Monday after big BTC weekend gap")

3. **Patterns to Avoid**: Any times/conditions where I should definitely NOT trade?

4. **Statistical Concerns**: Which patterns might be noise vs. signal given sample sizes?

5. **Suggested Trades**: For each new pattern, specify:
   - Entry time/condition
   - Exit time/condition
   - Direction (long BITU or short SBIT)
   - Expected edge

Please output your analysis in a structured format.

## PROMPT END

---

## How to Use This

1. **Copy** everything between "PROMPT START" and "PROMPT END"
2. **Paste** into:
   - [ChatGPT](https://chat.openai.com) (try GPT-4 or o1)
   - [Claude](https://claude.ai)
   - [DeepSeek](https://chat.deepseek.com)
   - [Gemini](https://gemini.google.com)
3. **Compare** the responses - which model gives you the most actionable insights?

## What to Look For

Good responses will:
- ✅ Identify the 3:30-4:00 PM strength (power hour)
- ✅ Notice the BTC overnight inverse correlation
- ✅ Suggest combining Thursday + options week for stronger signal
- ✅ Warn about small sample sizes (especially weekend gaps)
- ✅ Suggest avoiding month-end longs
- ✅ Provide specific entry/exit rules

Weak responses will:
- ❌ Just summarize the data without insights
- ❌ Suggest patterns with tiny sample sizes as high confidence
- ❌ Miss the statistical significance concerns
- ❌ Not provide actionable trading rules
