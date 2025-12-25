# Deployment Plan: Telegram Bot + Cloud Hosting

**Created:** December 25, 2025
**Status:** Approved, Ready for Implementation

---

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Cloud Hosting** | **Railway** | Supports 24/7 background workers, easy setup, ~$5/mo |
| **Mobile Notifications** | **Telegram Bot** | Free, cross-platform, interactive buttons, no app store needed |
| **Trade Approval** | **Human-in-the-loop** | User approves/rejects trades via Telegram before execution |
| **Web Dashboard** | **Optional** | Streamlit dashboard for backtesting, not required for daily operation |

### Why Railway (Not Vercel)
- Vercel is serverless (functions timeout, spin down when idle)
- Our bot needs to run **continuously** with APScheduler
- Railway supports long-running background workers
- Simple GitHub integration, auto-deploys on push

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  RAILWAY (Cloud - $5-7/month)                               │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  ibit-trading-bot (Python Worker)                      │ │
│  │  ├── APScheduler                                       │ │
│  │  │   ├── 9:35 AM  - Morning signal check               │ │
│  │  │   ├── 9:45-12  - Crash/Pump day monitoring (15min)  │ │
│  │  │   ├── 3:55 PM  - Close all positions                │ │
│  │  │   └── 8:00 AM  - E*TRADE token refresh              │ │
│  │  ├── Telegram Bot (python-telegram-bot)                │ │
│  │  │   ├── Sends trade approval requests                 │ │
│  │  │   ├── Receives approve/reject responses             │ │
│  │  │   └── Sends confirmations & daily summaries         │ │
│  │  └── E*TRADE Client                                    │ │
│  │      └── Executes trades after approval                │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  YOUR PHONE (Telegram App)                                  │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  Push Notification: "🚀 PUMP DAY SIGNAL"               │ │
│  │  ┌──────────────────────────────────────────────────┐  │ │
│  │  │  IBIT up +2.3% from open                         │  │ │
│  │  │  Recommendation: Buy BITU (2x long)              │  │ │
│  │  │  Position: $500 (75% of $667 cash)               │  │ │
│  │  │                                                  │  │ │
│  │  │  [ ✅ APPROVE ]    [ ❌ REJECT ]                 │  │ │
│  │  └──────────────────────────────────────────────────┘  │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

---

## Implementation Phases

### Phase 1: Telegram Bot Setup (30 min)
**Your involvement: ~10 minutes**

| Step | Owner | Task |
|------|-------|------|
| 1.1 | User | Create bot via @BotFather, get API token |
| 1.2 | User | Send message to bot to get your chat ID |
| 1.3 | Claude | Create `src/telegram_bot.py` |
| 1.4 | Claude | Add token to `.env` configuration |

### Phase 2: Approval Workflow (1.5 hrs)
**Your involvement: None**

| Step | Owner | Task |
|------|-------|------|
| 2.1 | Claude | Create approval message templates |
| 2.2 | Claude | Implement inline keyboard buttons |
| 2.3 | Claude | Add approval waiting logic with timeout |
| 2.4 | Claude | Integrate with trading bot execution |

### Phase 3: Notification System (1 hr)
**Your involvement: None**

| Notification Type | Trigger | Content |
|-------------------|---------|---------|
| 🚀 Trade Approval | Signal detected | Details + Approve/Reject buttons |
| ✅ Trade Executed | After approval | Entry price, shares, total cost |
| 📊 Position Closed | 3:55 PM | Exit price, P/L |
| ⚠️ Error Alert | Any failure | Error details |
| 📈 Daily Summary | 4:00 PM | Day's activity, total P/L |

### Phase 4: Railway Deployment (1-2 hrs)
**Your involvement: ~5 minutes**

| Step | Owner | Task |
|------|-------|------|
| 4.1 | Claude | Create `Dockerfile` |
| 4.2 | Claude | Create `railway.toml` config |
| 4.3 | Claude | Create `Procfile` for worker process |
| 4.4 | User | Create Railway account (free) |
| 4.5 | Claude | Deploy via GitHub integration |
| 4.6 | Claude | Configure environment variables |
| 4.7 | Claude | Set up E*TRADE token refresh |

### Phase 5: Testing (1 hr)
**Your involvement: ~5 minutes**

| Step | Owner | Task |
|------|-------|------|
| 5.1 | Claude | Run paper trading test |
| 5.2 | User | Approve/reject test trade from phone |
| 5.3 | Claude | Verify full workflow end-to-end |
| 5.4 | Claude | Adjust settings as needed |

---

## Files to Create/Modify

| File | Purpose | Status |
|------|---------|--------|
| `src/telegram_bot.py` | Telegram bot client & handlers | New |
| `src/notifications.py` | Message templates & formatting | New |
| `src/trading_bot.py` | Add approval hook before trades | Modify |
| `src/smart_scheduler.py` | Integrate notifications | Modify |
| `Dockerfile` | Container definition | New |
| `railway.toml` | Railway configuration | New |
| `Procfile` | Process definition | New |
| `.env.example` | Document all env vars | Modify |
| `requirements.txt` | Add python-telegram-bot | Modify |

---

## Environment Variables (Railway)

```bash
# E*TRADE (existing)
ETRADE_CONSUMER_KEY=xxx
ETRADE_CONSUMER_SECRET=xxx
ETRADE_ACCOUNT_ID=xxx

# Alpaca (existing)
ALPACA_API_KEY=xxx
ALPACA_SECRET_KEY=xxx

# Telegram (new)
TELEGRAM_BOT_TOKEN=xxx          # From @BotFather
TELEGRAM_CHAT_ID=xxx            # Your personal chat ID

# Bot Configuration (new)
APPROVAL_TIMEOUT_MINUTES=10     # Wait time before auto-skip
APPROVAL_MODE=required          # required | notify_only | auto_execute
TRADING_MODE=paper              # paper | live
```

---

## Configuration Options

| Setting | Options | Default | Description |
|---------|---------|---------|-------------|
| `APPROVAL_MODE` | `required`, `notify_only`, `auto_execute` | `required` | Whether trades need approval |
| `APPROVAL_TIMEOUT_MINUTES` | 1-60 | 10 | How long to wait for response |
| `SEND_DAILY_SUMMARY` | true/false | true | Send end-of-day report |
| `NOTIFY_ON_NO_SIGNAL` | true/false | false | Message when no trades today |

---

## Cost Summary

| Item | Cost |
|------|------|
| Railway hosting | ~$5-7/month |
| Telegram | Free |
| E*TRADE | Free (existing account) |
| Alpaca data | Free tier |
| **Total** | **~$5-7/month** |

---

## Timeline

| Phase | Duration | Cumulative |
|-------|----------|------------|
| Phase 1: Telegram Setup | 30 min | 30 min |
| Phase 2: Approval Workflow | 1.5 hrs | 2 hrs |
| Phase 3: Notifications | 1 hr | 3 hrs |
| Phase 4: Railway Deploy | 1-2 hrs | 4-5 hrs |
| Phase 5: Testing | 1 hr | 5-6 hrs |

**Your total time investment: ~15-20 minutes**

---

## Next Steps

1. [ ] User: Create Telegram bot via @BotFather
2. [ ] User: Provide bot token to Claude
3. [ ] Claude: Implement Phase 1-3
4. [ ] User: Create Railway account
5. [ ] Claude: Deploy to Railway (Phase 4)
6. [ ] Both: Test end-to-end (Phase 5)

---

## Rollback Plan

If anything goes wrong:
- Railway: One-click rollback to previous deployment
- Can always run locally as fallback
- Paper trading mode for safe testing
