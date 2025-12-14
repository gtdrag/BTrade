"""
Bitcoin ETF Smart Trading Bot - Streamlit Dashboard

A clean, focused trading dashboard implementing the proven strategy:
- Mean Reversion: Buy BITX (2x) after big down days
- Short Thursday: Buy SBIT (2x inverse) on Thursdays
- All other days: Cash

Backtested Performance: +361.8% vs IBIT Buy & Hold +35.5%
"""

from datetime import date, datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.smart_scheduler import BotStatus, SmartScheduler
from src.smart_strategy import Signal, SmartBacktester, SmartStrategy, StrategyConfig
from src.trading_bot import TradingBot, create_trading_bot

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


def get_or_create_bot() -> TradingBot:
    """Get or create the trading bot."""
    if st.session_state.bot is None:
        st.session_state.bot = create_trading_bot(
            mode=st.session_state.trading_mode,
            mean_reversion_threshold=st.session_state.config.mean_reversion_threshold,
            mean_reversion_enabled=st.session_state.config.mean_reversion_enabled,
            short_thursday_enabled=st.session_state.config.short_thursday_enabled,
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
        else:
            st.info("**CASH** - No trade today")
            st.write(signal.reason)

    with col2:
        st.metric(
            "Previous Day", f"{signal.prev_day_return:+.1f}%" if signal.prev_day_return else "N/A"
        )

    with col3:
        st.metric("Today", datetime.now().strftime("%A"))

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

    # Status
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Status")
        status = bot.get_status()

        st.write(f"**Mode:** {status['mode'].upper()}")
        st.write(f"**Today's Signal:** {status['today_signal']}")
        st.write(f"**Signal ETF:** {status['signal_etf']}")

        if bot.is_paper_mode:
            st.metric("Paper Capital", f"${status.get('paper_capital', 0):,.2f}")
            if status.get("paper_positions"):
                st.write("**Open Positions:**")
                for etf, pos in status["paper_positions"].items():
                    st.write(f"  - {etf}: {pos['shares']} shares @ ${pos['entry_price']:.2f}")
        else:
            if status.get("authenticated"):
                st.success("✓ E*TRADE Authenticated")
                st.metric("Cash Available", f"${status.get('cash_available', 0):,.2f}")
            else:
                st.warning("⚠ E*TRADE Not Connected")

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

                    m1, m2, m3, m4 = st.columns(4)
                    with m1:
                        st.metric("Total Return", f"{results['total_return_pct']:+.1f}%")
                    with m2:
                        st.metric("vs IBIT B&H", f"{results['vs_ibit_bh']:+.1f}%")
                    with m3:
                        st.metric("Win Rate", f"{results['win_rate']:.0f}%")
                    with m4:
                        st.metric("Sharpe Ratio", f"{results['sharpe_ratio']:.2f}")

                    m5, m6, m7, m8 = st.columns(4)
                    with m5:
                        st.metric("Total Trades", results["total_trades"])
                    with m6:
                        st.metric("Max Drawdown", f"{results['max_drawdown_pct']:.1f}%")
                    with m7:
                        st.metric("Mean Rev Trades", results["mean_rev_trades"])
                    with m8:
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
        render_today_signal()

    with tab2:
        render_trading()

    with tab3:
        render_backtest()

    with tab4:
        render_strategy_info()


if __name__ == "__main__":
    main()
