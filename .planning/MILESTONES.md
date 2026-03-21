# Milestones

## v0 — Intraday Directional Trading (Pre-GSD)

**Status:** Validated (shipped, running in production)

**What shipped:**
- Intraday trading bot with 5 strategies (Mean Reversion, 10 AM Dump, Crash Day, Pump Day, Reversal)
- E*TRADE equity execution (paper + live modes)
- Telegram bot with approvals, notifications, commands
- Market data fallback chain (E*TRADE → Alpaca → Finnhub → Yahoo)
- SQLite persistence (trades, state, events, strategy params)
- APScheduler orchestration with market hours awareness
- Streamlit monitoring dashboard
- Railway deployment (Docker, worker process)

**Last phase:** N/A (pre-GSD)

---

## v1.0 — Wheel Strategy ◆ (Current)

**Status:** In progress

**Goal:** Replace intraday directional strategies with full wheel strategy on IBIT options

**Started:** 2026-03-18
