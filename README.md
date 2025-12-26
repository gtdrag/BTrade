# Bitcoin ETF Smart Trading Bot

An automated trading bot for Bitcoin ETFs that uses **high-probability signals** with leveraged ETFs. Runs 24/7 on Railway with Telegram-based trade approvals.

## Strategy Overview

The bot trades only when specific conditions are met that historically favor profitable trades. Instead of predicting market direction, it uses leverage on well-defined setups.

**Core Philosophy:** Stay in cash most days. Only trade when the odds are heavily in your favor.

### The Five Trading Signals

| Signal | Trigger | ETF | Description |
|--------|---------|-----|-------------|
| **Mean Reversion** | IBIT down 2%+ yesterday + BTC up overnight | BITX (2x) | Buy the bounce after a dip |
| **Short Thursday** | Every Thursday | SBIT (2x inverse) | Thursday is historically Bitcoin's weakest day |
| **Crash Day** | IBIT down 2%+ intraday (before noon) | SBIT (2x inverse) | Ride the momentum down |
| **Pump Day** | IBIT up 2%+ intraday (before noon) | BITX (2x) | Ride the momentum up |
| **No Signal** | None of the above | Cash | Stay out when no edge |

### The BTC Overnight Filter

A key innovation that dramatically improves Mean Reversion win rates:

| BTC Overnight Move | Win Rate | Avg Return |
|--------------------|----------|------------|
| BTC UP overnight | **84%** | +5.5% |
| BTC DOWN overnight | 17% | -3.7% |

**How it works:** After IBIT closes at 4 PM, Bitcoin keeps trading 24/7. If BTC recovers overnight, the bounce is already happening - we trade. If BTC keeps falling, we skip - avoiding catching a falling knife.

### ETF Universe

| ETF | Leverage | Description |
|-----|----------|-------------|
| IBIT | 1x | iShares Bitcoin Trust (reference/benchmark) |
| BITX | 2x Long | 2x daily return of Bitcoin |
| SBIT | 2x Inverse | 2x inverse daily return of Bitcoin |

---

## Architecture

```
Railway (Cloud Worker - 24/7)
├── APScheduler
│   ├── 8:30 AM  - Health check & token refresh
│   ├── 9:15 AM  - Pre-market reminder
│   ├── 9:35 AM  - Morning signal check
│   ├── 9:45-12  - Crash/Pump day monitoring (15-min intervals)
│   ├── 10-3 PM  - Hourly position updates
│   └── 3:55 PM  - Close all positions
├── Telegram Bot
│   ├── Trade approval requests (Approve/Reject buttons)
│   ├── Interactive commands (/status, /mode, /balance, etc.)
│   └── Notifications & daily summaries
└── E*TRADE Client
    └── Executes trades after user approval
```

### Telegram Commands

| Command | Description |
|---------|-------------|
| `/status` | Comprehensive bot status |
| `/mode [paper\|live]` | Switch trading modes |
| `/pause` / `/resume` | Control the scheduler |
| `/balance` | Show account balance |
| `/positions` | Show current positions |
| `/signal` | Check today's trading signal |
| `/jobs` | View scheduled jobs |
| `/auth` | Start E*TRADE OAuth flow |
| `/verify CODE` | Complete E*TRADE login |

---

## Quick Start

### Prerequisites

- Python 3.10+
- E*TRADE brokerage account with API access
- Telegram account

### Local Development

```bash
# Clone the repository
git clone https://github.com/gtdrag/BTrade.git
cd BTrade

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env
# Edit .env with your credentials

# Run the Streamlit dashboard (optional, for backtesting)
streamlit run app.py
```

### Environment Variables

```bash
# E*TRADE API
ETRADE_CONSUMER_KEY=your_consumer_key
ETRADE_CONSUMER_SECRET=your_consumer_secret
ETRADE_ACCOUNT_ID=your_account_id

# Telegram Bot
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Trading Settings
TRADING_MODE=paper          # paper or live
APPROVAL_MODE=required      # required, notify_only, or auto_execute
MAX_POSITION_PCT=75         # Max % of cash per trade

# Data Providers (optional, for real-time quotes)
ALPACA_API_KEY=your_alpaca_key
ALPACA_SECRET_KEY=your_alpaca_secret
```

### Railway Deployment

1. Fork this repository
2. Create a new Railway project
3. Connect your GitHub repo
4. Add environment variables in Railway dashboard
5. Deploy - the worker starts automatically

---

## Project Structure

```
├── app.py                    # Streamlit dashboard (backtesting, manual control)
├── src/
│   ├── smart_strategy.py     # Core strategy logic (5 signals)
│   ├── smart_scheduler.py    # APScheduler jobs for automated trading
│   ├── trading_bot.py        # Integration layer (strategy + broker + notifications)
│   ├── telegram_bot.py       # Telegram bot for approvals & commands
│   ├── etrade_client.py      # E*TRADE API client (OAuth, orders, quotes)
│   ├── data_providers.py     # Market data (Alpaca, E*TRADE, Yahoo)
│   ├── worker.py             # Railway worker entry point
│   ├── database.py           # SQLite trade logging
│   ├── notifications.py      # Email, SMS, desktop notifications
│   └── utils.py              # Timezone utilities
├── docs/
│   ├── STRATEGY_GUIDE.md     # Detailed strategy explanation
│   ├── SECURITY_REVIEW.md    # Security audit documentation
│   ├── architecture.md       # Technical architecture
│   ├── deployment-plan.md    # Deployment guide
│   └── prd.md                # Product requirements
├── analysis/                 # Research & backtesting scripts
└── tests/                    # Test suite
```

---

## Security

This bot handles real money and has been security reviewed. Key protections:

- **Telegram Authorization**: Only your configured chat ID can send commands
- **OAuth Token Security**: Tokens stored with restricted file permissions
- **Human-in-the-Loop**: All trades require explicit approval (in `required` mode)
- **Paper Mode Default**: Starts in paper trading mode for safety

See [docs/SECURITY_REVIEW.md](docs/SECURITY_REVIEW.md) for the full security audit.

---

## Trading Modes

| Mode | Description |
|------|-------------|
| **Paper** | Simulated trades, no real money at risk |
| **Live** | Real trades via E*TRADE (requires OAuth) |

### Approval Modes

| Mode | Behavior |
|------|----------|
| **required** | User must tap Approve/Reject for each trade |
| **notify_only** | Sends notification, auto-executes |
| **auto_execute** | Silent execution (not recommended) |

---

## Daily Workflow

```
8:30 AM   - Bot sends health check, refreshes E*TRADE token
9:15 AM   - Pre-market reminder with today's signal preview
9:35 AM   - Morning signal check
            → If Mean Reversion or Short Thursday triggers:
              Sends approval request → User approves → Trade executes
9:45-12   - Monitors for Crash Day / Pump Day signals
            → If triggered: Sends approval request
3:55 PM   - Closes all open positions
4:00 PM   - Sends daily summary
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [Strategy Guide](docs/STRATEGY_GUIDE.md) | Detailed explanation of all trading signals |
| [Architecture](docs/architecture.md) | Technical system design |
| [Deployment Plan](docs/deployment-plan.md) | Railway + Telegram setup |
| [Security Review](docs/SECURITY_REVIEW.md) | Security audit and fixes |
| [PRD](docs/prd.md) | Product requirements document |

---

## Development

### Running Tests

```bash
pytest tests/ -v
```

### Code Quality

```bash
# Linting
ruff check .

# Formatting
ruff format .
```

Pre-commit hooks are configured for automatic linting on commit.

---

## Disclaimer

**This software is for educational purposes only.**

- Past performance does not guarantee future results
- Leveraged ETFs carry significant risk and are not suitable for all investors
- Always start with paper trading mode before using real money
- The authors are not responsible for any financial losses

Trade at your own risk.

---

## License

MIT License - see [LICENSE](LICENSE) for details.

---

## Version

**v0.1.0** - Production-Ready Prototype (December 2025)

See [CHANGELOG](CHANGELOG.md) for version history.
