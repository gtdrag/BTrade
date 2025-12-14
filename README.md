# Bitcoin ETF Smart Trading Bot

Automated trading bot for Bitcoin ETFs using a **proven, backtested strategy** with +361.8% returns (vs +35.5% IBIT Buy & Hold).

## Strategy

The bot uses two high-probability signals with leveraged ETFs:

| Signal | ETF | Trigger | Win Rate | Purpose |
|--------|-----|---------|----------|---------|
| **Mean Reversion** | BITX (2x) | Previous day IBIT dropped -2%+ | 63% | Buy the dip with leverage |
| **Short Thursday** | SBIT (2x inverse) | It's Thursday | 55% | Thursday is Bitcoin's worst day |
| **No Signal** | Cash | All other days | - | Stay out when no edge |

**Key Insight:** Don't try to predict market direction. Use leverage ONLY on high-probability setups.

### Backtested Performance (Apr 2024 - Dec 2025)

| Metric | Value |
|--------|-------|
| Total Return | **+361.8%** |
| vs IBIT Buy & Hold | **+326.3%** |
| Sharpe Ratio | 3.18 |
| Max Drawdown | 12.4% |
| Win Rate | 58% |
| Active Days | 33% (cash the rest) |

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the dashboard
streamlit run app.py
```

Open http://localhost:8501 to view the dashboard.

## Features

- **Today's Signal**: See what trade to make today (if any)
- **Backtest**: Test the strategy with different parameters
- **Strategy Info**: Understand why this works

## ETF Universe

| ETF | Leverage | Description |
|-----|----------|-------------|
| IBIT | +1x | iShares Bitcoin Trust (reference) |
| BITX | +2x | 2x Long Bitcoin ETF |
| SBIT | -2x | 2x Short Bitcoin ETF |

## Configuration

Settings are available in the sidebar:

- **Mean Reversion**: Enable/disable, adjust threshold
- **Short Thursday**: Enable/disable
- **Dry Run Mode**: Paper trading (default: on)

## Files

```
app.py              # Main Streamlit dashboard
src/
  smart_strategy.py # Core strategy logic
  database.py       # Trade database
  utils.py          # Utilities
analysis/           # Research & analysis scripts
legacy/             # Old strategy implementations
```

## Disclaimer

This software is for educational purposes only. Past performance does not guarantee future results. Trade at your own risk. Always start with dry-run mode before live trading.
