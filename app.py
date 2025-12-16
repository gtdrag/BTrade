"""
Bitcoin ETF Smart Trading Bot - Streamlit Dashboard

A clean, focused trading dashboard implementing the proven strategy:
- Mean Reversion: Buy BITX (2x) after big down days
- Short Thursday: Buy SBIT (2x inverse) on Thursdays
- All other days: Cash

Backtested Performance: +361.8% vs IBIT Buy & Hold +35.5%
"""

import os
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables before local imports (they need env vars)
load_dotenv(Path(__file__).parent / ".env")

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from src.smart_scheduler import BotStatus, SmartScheduler  # noqa: E402
from src.smart_strategy import (  # noqa: E402
    AlertLevel,
    Signal,
    SmartBacktester,
    SmartStrategy,
    StrategyConfig,
)
from src.trading_bot import TradingBot, create_trading_bot  # noqa: E402

# Page config
st.set_page_config(page_title="Bitcoin ETF Bot", page_icon="₿", layout="wide")

# Session state initialization
if "config" not in st.session_state:
    st.session_state.config = StrategyConfig()
if "trading_mode" not in st.session_state:
    st.session_state.trading_mode = "paper"
if "bot" not in st.session_state:
    st.session_state.bot = None
if "scheduler" not in st.session_state:
    st.session_state.scheduler = None


@st.cache_data(ttl=60)  # Cache for 1 minute
def get_market_regime_data():
    """
    Calculate market regime indicators using Alpaca real-time data.

    Based on analysis findings:
    - Strategy outperforms when: high volatility, downtrend, more big down days
    - B&H outperforms when: low volatility, strong uptrend
    """
    import pandas as pd

    from src.data_providers import AlpacaProvider

    end_date = date.today()
    start_date = end_date - timedelta(days=21)  # ~3 weeks of data

    try:
        # Use Alpaca for historical data
        alpaca = AlpacaProvider(
            api_key=os.environ.get("ALPACA_API_KEY"),
            secret_key=os.environ.get("ALPACA_SECRET_KEY"),
        )

        if not alpaca.is_available():
            return {"error": "Alpaca API keys not configured"}

        bars = alpaca.get_historical_bars(
            "IBIT", start_date.isoformat(), end_date.isoformat(), "1Day"
        )

        if not bars or len(bars) < 5:
            return {"error": f"Insufficient data: got {len(bars) if bars else 0} rows"}

        # Convert to DataFrame
        df = pd.DataFrame(bars)
        df["Date"] = pd.to_datetime(df["t"])
        df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})

        # Calculate metrics
        df["daily_return"] = (df["Close"] - df["Open"]) / df["Open"] * 100

        # 14-day metrics
        recent = df.tail(14)

        # Trend: total return over period
        trend_14d = (
            (recent["Close"].iloc[-1] - recent["Close"].iloc[0]) / recent["Close"].iloc[0] * 100
        )

        # Volatility: std of daily returns
        volatility = recent["daily_return"].std()

        # Big down days (< -2%)
        big_down_days = sum(recent["daily_return"] < -2.0)

        # Average daily return
        avg_daily = recent["daily_return"].mean()

        # Determine regime
        # Strategy favored: downtrend OR high volatility OR has big down days
        # B&H favored: uptrend AND low volatility AND no big down days

        strategy_score = 0
        reasons = []

        # Trend factor (strongest predictor: -0.69 correlation)
        if trend_14d < -5:
            strategy_score += 3
            reasons.append(f"Strong downtrend ({trend_14d:+.1f}%)")
        elif trend_14d < 0:
            strategy_score += 2
            reasons.append(f"Downtrend ({trend_14d:+.1f}%)")
        elif trend_14d > 10:
            strategy_score -= 2
            reasons.append(f"Strong uptrend ({trend_14d:+.1f}%)")
        elif trend_14d > 5:
            strategy_score -= 1
            reasons.append(f"Uptrend ({trend_14d:+.1f}%)")

        # Big down days factor (+0.53 correlation)
        if big_down_days >= 2:
            strategy_score += 2
            reasons.append(f"{big_down_days} big down days (triggers)")
        elif big_down_days == 1:
            strategy_score += 1
            reasons.append("1 big down day")
        else:
            reasons.append("No big down days recently")

        # Volatility factor (+0.42 correlation)
        if volatility > 2.5:
            strategy_score += 1
            reasons.append(f"High volatility ({volatility:.1f}%)")
        elif volatility < 1.5:
            strategy_score -= 1
            reasons.append(f"Low volatility ({volatility:.1f}%)")

        # Determine regime
        if strategy_score >= 2:
            regime = "STRATEGY_FAVORED"
            regime_label = "Strategy Favored"
            regime_color = "green"
            regime_icon = "🎯"
        elif strategy_score <= -1:
            regime = "BH_FAVORED"
            regime_label = "Buy & Hold Favored"
            regime_color = "orange"
            regime_icon = "📈"
        else:
            regime = "NEUTRAL"
            regime_label = "Neutral"
            regime_color = "gray"
            regime_icon = "⚖️"

        return {
            "regime": regime,
            "regime_label": regime_label,
            "regime_color": regime_color,
            "regime_icon": regime_icon,
            "score": strategy_score,
            "trend_14d": trend_14d,
            "volatility": volatility,
            "big_down_days": big_down_days,
            "avg_daily": avg_daily,
            "reasons": reasons,
            "last_updated": datetime.now().strftime("%H:%M"),
        }
    except Exception as e:
        return {"error": str(e)}


def render_market_regime():
    """Render the market regime indicator."""
    st.subheader("Market Regime")

    data = get_market_regime_data()

    if data is None:
        st.warning("Could not load market data")
        return

    if "error" in data:
        st.warning(f"Error loading regime data: {data['error']}")
        return

    # Main regime indicator
    col1, col2, col3, col4 = st.columns([2, 1, 1, 1])

    with col1:
        # Regime badge
        if data["regime"] == "STRATEGY_FAVORED":
            st.success(f"{data['regime_icon']} **{data['regime_label']}**")
            st.caption("Conditions favor our strategy over buy-and-hold")
        elif data["regime"] == "BH_FAVORED":
            st.warning(f"{data['regime_icon']} **{data['regime_label']}**")
            st.caption("Strong uptrend - buy-and-hold may outperform")
        else:
            st.info(f"{data['regime_icon']} **{data['regime_label']}**")
            st.caption("Mixed signals - strategy and B&H roughly equal")

    with col2:
        st.metric(
            "14-Day Trend",
            f"{data['trend_14d']:+.1f}%",
            help="IBIT price change over last 14 days. Negative = strategy favored.",
        )

    with col3:
        st.metric(
            "Volatility",
            f"{data['volatility']:.1f}%",
            help="Daily return standard deviation. Higher = strategy favored.",
        )

    with col4:
        st.metric(
            "Big Down Days",
            f"{data['big_down_days']}",
            help="Days with -2%+ drops (last 14 days). More = more mean reversion opportunities.",
        )

    # Expandable details
    with st.expander("📊 Regime Analysis Details"):
        st.write("**Current Conditions:**")
        for reason in data["reasons"]:
            st.write(f"  • {reason}")

        st.divider()

        st.write("**How This Works:**")
        st.markdown(
            """
        Based on analysis of 124 rolling periods, the strategy outperforms when:
        - **IBIT is trending DOWN** (correlation: -0.69)
        - **More big down days** occur (correlation: +0.53)
        - **Higher volatility** in the market (correlation: +0.42)

        In strong uptrends with low volatility, buy-and-hold typically wins
        because the strategy is in cash most days and misses the rally.
        """
        )

        st.caption(f"Last updated: {data['last_updated']}")


def get_or_create_bot() -> TradingBot:
    """Get or create the trading bot."""
    if st.session_state.bot is None:
        st.session_state.bot = create_trading_bot(
            mode=st.session_state.trading_mode,
            mean_reversion_threshold=st.session_state.config.mean_reversion_threshold,
            mean_reversion_enabled=st.session_state.config.mean_reversion_enabled,
            short_thursday_enabled=st.session_state.config.short_thursday_enabled,
            crash_day_enabled=st.session_state.config.crash_day_enabled,
            crash_day_threshold=st.session_state.config.crash_day_threshold,
        )
    return st.session_state.bot


def render_header():
    """Render the header with strategy summary."""
    st.title("₿ Bitcoin ETF Smart Trading Bot")

    # Status indicators
    col1, col2, col3 = st.columns([2, 1, 1])

    with col1:
        st.caption("Proven strategy: +361.8% return (vs +35.5% IBIT B&H)")

    with col2:
        mode = st.session_state.trading_mode.upper()
        if mode == "PAPER":
            st.success(f"📝 {mode} MODE")
        else:
            st.warning(f"💰 {mode} MODE")

    with col3:
        if st.session_state.scheduler and st.session_state.scheduler.status == BotStatus.RUNNING:
            st.success("🤖 Bot Running")
        else:
            st.info("🤖 Bot Stopped")


def render_today_signal():
    """Render today's trading signal."""
    st.header("Today's Signal")

    strategy = SmartStrategy(config=st.session_state.config)
    signal = strategy.get_today_signal()

    # Signal card
    col1, col2, col3 = st.columns([2, 1, 1])

    with col1:
        if signal.signal == Signal.MEAN_REVERSION:
            st.success(f"**BUY {signal.etf}** (2x Long)")
            st.write(signal.reason)
        elif signal.signal == Signal.SHORT_THURSDAY:
            st.error(f"**BUY {signal.etf}** (2x Inverse)")
            st.write(signal.reason)
        elif signal.signal == Signal.CRASH_DAY:
            st.error(f"🚨 **CRASH DAY: BUY {signal.etf}** (2x Inverse)")
            st.write(signal.reason)
        else:
            st.info("**CASH** - No trade today")
            st.write(signal.reason)

    with col2:
        st.metric(
            "Previous Day", f"{signal.prev_day_return:+.1f}%" if signal.prev_day_return else "N/A"
        )

    with col3:
        st.metric("Today", datetime.now().strftime("%A"))

    # Crash Day Monitoring Section
    if signal.crash_day_status:
        st.subheader("Intraday Crash Monitor")
        crash = signal.crash_day_status

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("IBIT Open", f"${crash.ibit_open:.2f}" if crash.ibit_open else "N/A")
        with c2:
            st.metric("IBIT Current", f"${crash.ibit_current:.2f}" if crash.ibit_current else "N/A")
        with c3:
            # Color based on drop severity
            drop_pct = crash.current_drop_pct
            if drop_pct <= -2:
                st.metric(
                    "Drop from Open", f"{drop_pct:+.2f}%", delta="TRIGGERED", delta_color="inverse"
                )
            elif drop_pct <= -1:
                st.metric(
                    "Drop from Open", f"{drop_pct:+.2f}%", delta="Watching", delta_color="off"
                )
            else:
                st.metric("Drop from Open", f"{drop_pct:+.2f}%")
        with c4:
            threshold = st.session_state.config.crash_day_threshold
            st.metric("Trigger At", f"{threshold}%")

        if crash.already_traded_today:
            st.info("✓ Already executed crash day trade today")

    # Weekend Gap Alert (show on Mondays or when significant)
    if signal.weekend_gap and signal.weekend_gap.alert_level != AlertLevel.NONE:
        gap = signal.weekend_gap
        if gap.alert_level == AlertLevel.CRITICAL:
            st.error(f"🔴 {gap.message}")
        elif gap.alert_level == AlertLevel.HIGH_ALERT:
            st.warning(f"🟠 {gap.message}")
        elif gap.alert_level == AlertLevel.WATCH:
            st.info(f"🟡 {gap.message}")

    # Show relevant ETF quote if trading today
    if signal.should_trade():
        st.subheader(f"{signal.etf} Quote")
        try:
            quote = strategy.get_etf_quote(signal.etf)
            c1, c2, c3 = st.columns(3)
            with c1:
                st.metric("Open", f"${quote['open_price']:.2f}")
            with c2:
                st.metric("Current", f"${quote['current_price']:.2f}")
            with c3:
                st.metric("Change", f"{quote['change_pct']:+.2f}%")
        except Exception as e:
            st.warning(f"Could not fetch quote: {e}")


def render_trading():
    """Render trading controls."""
    st.header("Trading")

    bot = get_or_create_bot()

    # Portfolio Value Section (Real-time P&L)
    st.subheader("📊 Portfolio (Real-Time)")

    portfolio = bot.get_portfolio_value()

    # Main metrics row
    col_p1, col_p2, col_p3, col_p4 = st.columns(4)

    with col_p1:
        st.metric(
            "Total Value",
            f"${portfolio.get('total_value', 0):,.2f}",
            delta=f"{portfolio.get('total_pnl_pct', 0):+.1f}%",
            delta_color="normal" if portfolio.get("total_pnl", 0) >= 0 else "inverse",
        )

    with col_p2:
        st.metric("Cash", f"${portfolio.get('cash', 0):,.2f}")

    with col_p3:
        st.metric("Positions Value", f"${portfolio.get('total_position_value', 0):,.2f}")

    with col_p4:
        pnl = portfolio.get("total_pnl", 0)
        st.metric(
            "Total P&L",
            f"${pnl:+,.2f}",
            delta=f"from ${portfolio.get('starting_capital', 10000):,.0f}",
            delta_color="off",
        )

    # Show positions with real-time prices
    positions = portfolio.get("positions", [])
    if positions:
        st.write("**Open Positions:**")
        for pos in positions:
            pnl_color = "green" if pos["unrealized_pnl"] >= 0 else "red"
            source_badge = f"[{pos['source'].upper()}]"
            st.markdown(
                f"- **{pos['symbol']}**: {pos['shares']} shares @ ${pos['entry_price']:.2f} → "
                f"${pos['current_price']:.2f} {source_badge} | "
                f"P&L: **:{pnl_color}[${pos['unrealized_pnl']:+,.2f} ({pos['unrealized_pnl_pct']:+.1f}%)]**"
            )
    else:
        st.info("No open positions - 100% cash")

    st.divider()

    # Status and Actions
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Today's Signal")
        status = bot.get_status()

        st.write(f"**Mode:** {status['mode'].upper()}")
        st.write(f"**Signal:** {status['today_signal'].upper()}")
        st.write(f"**ETF:** {status['signal_etf']}")
        st.write(f"**Reason:** {status['signal_reason']}")

        # Data source indicator
        dm_status = bot.data_manager.get_status()
        active_source = dm_status.get("active") or "checking"
        if dm_status.get("is_realtime"):
            st.success(f"📡 Data: {active_source.upper()} (Real-Time)")
        else:
            st.warning(f"📡 Data: {active_source.upper()} (15-min delay)")

    with col2:
        st.subheader("Actions")

        # Execute signal button
        if st.button("Execute Today's Signal", type="primary", use_container_width=True):
            with st.spinner("Executing..."):
                result = bot.execute_signal()
                if result.success:
                    if result.signal == Signal.CASH:
                        st.info("No trade signal today")
                    else:
                        st.success(
                            f"✓ {result.action} {result.shares} {result.etf} @ ${result.price:.2f}"
                        )
                else:
                    st.error(f"✗ Trade failed: {result.error}")

        # Close positions button
        if st.button("Close All Positions", use_container_width=True):
            with st.spinner("Closing positions..."):
                if bot.is_paper_mode:
                    for etf in list(bot._paper_positions.keys()):
                        result = bot.close_position(etf)
                        if result.success:
                            st.success(f"✓ Closed {etf}")
                        else:
                            st.error(f"✗ Failed to close {etf}: {result.error}")
                else:
                    for etf in ["BITX", "SBIT"]:
                        result = bot.close_position(etf)
                        if result.success and result.shares > 0:
                            st.success(f"✓ Closed {etf}")

        st.divider()

        # Scheduler controls
        st.subheader("Automation")

        if st.session_state.scheduler is None:
            st.session_state.scheduler = SmartScheduler(bot)

        scheduler = st.session_state.scheduler

        if scheduler.status == BotStatus.RUNNING:
            if st.button("Stop Bot", type="secondary", use_container_width=True):
                scheduler.stop()
                st.info("Bot stopped")
                st.rerun()
        else:
            if st.button("Start Bot", type="primary", use_container_width=True):
                scheduler.start()
                st.success("Bot started - will execute signals automatically")
                st.rerun()

        # Show scheduled jobs
        if scheduler.status == BotStatus.RUNNING:
            sched_status = scheduler.get_status()
            if sched_status.get("next_jobs"):
                st.write("**Scheduled Jobs:**")
                for job in sched_status["next_jobs"]:
                    next_run = job["next_run"][:16] if job["next_run"] else "N/A"
                    st.caption(f"  • {job['name']}: {next_run}")


def render_backtest():
    """Render backtest section."""
    st.header("Backtest")

    col1, col2 = st.columns([1, 3])

    with col1:
        st.subheader("Settings")

        start_date = st.date_input(
            "Start Date", value=date(2024, 4, 15), help="BITX and SBIT launched April 2024"
        )

        end_date = st.date_input("End Date", value=date.today())

        initial_capital = st.number_input(
            "Initial Capital ($)", min_value=1000, max_value=1000000, value=10000, step=1000
        )

        mr_threshold = st.slider(
            "Mean Reversion Threshold",
            min_value=-5.0,
            max_value=-1.0,
            value=-2.0,
            step=0.5,
            help="Buy BITX after IBIT drops this much",
        )

        run_btn = st.button("Run Backtest", type="primary", use_container_width=True)

    with col2:
        if run_btn:
            with st.spinner("Running backtest..."):
                config = StrategyConfig(mean_reversion_threshold=mr_threshold)
                backtester = SmartBacktester(initial_capital=initial_capital, config=config)

                try:
                    backtester.load_data(start_date, end_date)
                    results = backtester.run_backtest()

                    # Summary metrics
                    st.subheader("Results")

                    # Performance comparison box
                    ibit_bh = results["ibit_bh_return"]
                    strategy_return = results["total_return_pct"]
                    outperformance = results["vs_ibit_bh"]

                    col_a, col_b = st.columns(2)
                    with col_a:
                        st.metric(
                            "Strategy Return",
                            f"{strategy_return:+.1f}%",
                            help="Your return using this strategy",
                        )
                    with col_b:
                        st.metric(
                            "IBIT Buy & Hold",
                            f"{ibit_bh:+.1f}%",
                            delta=f"Strategy beat B&H by {outperformance:+.1f}%"
                            if outperformance > 0
                            else f"B&H beat strategy by {-outperformance:+.1f}%",
                            delta_color="normal" if outperformance > 0 else "inverse",
                            help="What you'd have made just holding IBIT",
                        )

                    m1, m2, m3, m4 = st.columns(4)
                    with m1:
                        st.metric("Win Rate", f"{results['win_rate']:.0f}%")
                    with m2:
                        st.metric("Sharpe Ratio", f"{results['sharpe_ratio']:.2f}")
                    with m3:
                        st.metric("Total Trades", results["total_trades"])
                    with m4:
                        st.metric("Max Drawdown", f"{results['max_drawdown_pct']:.1f}%")

                    m5, m6 = st.columns(2)
                    with m5:
                        st.metric("Mean Rev Trades", results["mean_rev_trades"])
                    with m6:
                        st.metric("Thu Short Trades", results["short_thu_trades"])

                    # Capital growth
                    st.metric(
                        "Final Capital",
                        f"${results['final_capital']:,.2f}",
                        delta=f"${results['final_capital'] - initial_capital:,.2f}",
                    )

                    # Equity curve
                    if results["trades"]:
                        st.subheader("Equity Curve")

                        df = pd.DataFrame(results["trades"])
                        df["date"] = pd.to_datetime(df["date"])

                        fig = go.Figure()

                        fig.add_trace(
                            go.Scatter(
                                x=df["date"],
                                y=df["capital"],
                                mode="lines+markers",
                                name="Portfolio Value",
                                line=dict(color="#00C853", width=2),
                                marker=dict(
                                    size=8,
                                    color=df["return_pct"].apply(
                                        lambda x: "#00C853" if x > 0 else "#FF5252"
                                    ),
                                ),
                            )
                        )

                        # Add baseline
                        fig.add_hline(
                            y=initial_capital,
                            line_dash="dash",
                            line_color="gray",
                            annotation_text="Initial Capital",
                        )

                        fig.update_layout(
                            xaxis_title="Date",
                            yaxis_title="Portfolio Value ($)",
                            height=400,
                            showlegend=False,
                        )

                        st.plotly_chart(fig, use_container_width=True)

                        # Trade table
                        st.subheader("Trade History")

                        display_df = df[
                            ["date", "signal", "etf", "entry", "exit", "return_pct", "capital"]
                        ].copy()
                        display_df["date"] = display_df["date"].dt.strftime("%Y-%m-%d")
                        display_df["entry"] = display_df["entry"].apply(lambda x: f"${x:.2f}")
                        display_df["exit"] = display_df["exit"].apply(lambda x: f"${x:.2f}")
                        display_df["return_pct"] = display_df["return_pct"].apply(
                            lambda x: f"{x:+.2f}%"
                        )
                        display_df["capital"] = display_df["capital"].apply(lambda x: f"${x:,.2f}")

                        display_df.columns = [
                            "Date",
                            "Signal",
                            "ETF",
                            "Entry",
                            "Exit",
                            "Return",
                            "Capital",
                        ]

                        st.dataframe(display_df, use_container_width=True, hide_index=True)

                except Exception as e:
                    st.error(f"Backtest failed: {e}")
                    import traceback

                    st.code(traceback.format_exc())


def render_strategy_info():
    """Render strategy explanation."""
    st.header("Strategy")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Mean Reversion (BITX)")
        st.markdown(
            """
        **Trigger:** Previous day IBIT dropped -2% or more

        **Action:** Buy BITX (2x leveraged) at market open, sell at close

        **Why it works:** After significant drops, Bitcoin tends to bounce back.
        Using 2x leverage on high-probability setups amplifies returns.

        **Win Rate:** ~63% | **Avg Return:** +1.3%
        """
        )

    with col2:
        st.subheader("Short Thursday (SBIT)")
        st.markdown(
            """
        **Trigger:** It's Thursday

        **Action:** Buy SBIT (2x inverse) at market open, sell at close

        **Why it works:** Thursday is statistically the worst day for Bitcoin.
        SBIT profits when Bitcoin goes down.

        **Win Rate:** ~55% | **Avg Return:** +1.1%
        """
        )

    st.divider()

    st.subheader("ETF Universe")
    etf_data = {
        "ETF": ["IBIT", "BITX", "SBIT"],
        "Leverage": ["+1x", "+2x", "-2x"],
        "Description": [
            "iShares Bitcoin Trust (baseline)",
            "2x Long Bitcoin ETF (for mean reversion)",
            "2x Short Bitcoin ETF (for Thursday shorts)",
        ],
        "Used When": ["Not used in strategy", "After big down days", "Every Thursday"],
    }
    st.dataframe(pd.DataFrame(etf_data), use_container_width=True, hide_index=True)


def render_settings():
    """Render settings sidebar."""
    with st.sidebar:
        st.header("Settings")

        # Trading mode
        mode = st.radio(
            "Trading Mode",
            ["Paper", "Live"],
            index=0 if st.session_state.trading_mode == "paper" else 1,
            help="Paper mode simulates trades without real money",
        )
        new_mode = mode.lower()
        if new_mode != st.session_state.trading_mode:
            st.session_state.trading_mode = new_mode
            st.session_state.bot = None  # Reset bot on mode change

        # E*TRADE settings for live mode
        if st.session_state.trading_mode == "live":
            st.subheader("E*TRADE Connection")

            import os

            has_key = bool(os.environ.get("ETRADE_CONSUMER_KEY"))
            has_secret = bool(os.environ.get("ETRADE_CONSUMER_SECRET"))
            has_account = bool(os.environ.get("ETRADE_ACCOUNT_ID"))

            if has_key and has_secret:
                st.success("API Keys: Configured")
            else:
                st.error("API Keys: Not Set")
                st.caption("Set ETRADE_CONSUMER_KEY and")
                st.caption("ETRADE_CONSUMER_SECRET env vars")

            if has_account:
                st.success("Account: Configured")
            else:
                st.warning("Account: Not Set")
                st.caption("Set ETRADE_ACCOUNT_ID env var")

            st.caption("Run `python scripts/etrade_setup.py`")
            st.caption("for guided setup")

        st.divider()

        # Strategy settings
        st.subheader("Strategy")

        st.session_state.config.mean_reversion_enabled = st.toggle(
            "Mean Reversion",
            value=st.session_state.config.mean_reversion_enabled,
            help="Buy BITX after big down days",
        )

        if st.session_state.config.mean_reversion_enabled:
            st.session_state.config.mean_reversion_threshold = st.slider(
                "MR Threshold",
                min_value=-5.0,
                max_value=-1.0,
                value=st.session_state.config.mean_reversion_threshold,
                step=0.5,
                help="Buy after IBIT drops this much",
            )

        st.session_state.config.short_thursday_enabled = st.toggle(
            "Short Thursday",
            value=st.session_state.config.short_thursday_enabled,
            help="Buy SBIT every Thursday",
        )

        st.session_state.config.crash_day_enabled = st.toggle(
            "Crash Day Signal",
            value=st.session_state.config.crash_day_enabled,
            help="Buy SBIT when IBIT drops 2%+ intraday",
        )

        if st.session_state.config.crash_day_enabled:
            st.session_state.config.crash_day_threshold = st.slider(
                "Crash Threshold",
                min_value=-5.0,
                max_value=-1.0,
                value=st.session_state.config.crash_day_threshold,
                step=0.5,
                help="Buy SBIT when IBIT drops this much intraday",
            )

        st.divider()

        # Performance summary
        st.caption("**Backtested Performance**")
        st.caption("Return: +361.8%")
        st.caption("vs IBIT B&H: +326.3%")
        st.caption("Active: 33% of days")


def main():
    """Main app."""
    render_settings()
    render_header()

    tab1, tab2, tab3, tab4 = st.tabs(["Today", "Trading", "Backtest", "Strategy"])

    with tab1:
        render_market_regime()
        st.divider()
        render_today_signal()

    with tab2:
        render_trading()

    with tab3:
        render_backtest()

    with tab4:
        render_strategy_info()


if __name__ == "__main__":
    main()
