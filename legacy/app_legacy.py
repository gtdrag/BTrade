"""
IBIT Dip Bot - Streamlit Dashboard

Full-featured web dashboard for the IBIT Dip Trading Bot.
Features:
- Live dip gauge and countdown timers
- Equity curve visualization
- Trade history table
- Configuration panel
- Backtesting interface
- Manual trading controls
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, date, timedelta
import time
import threading

# Import bot modules
from src.utils import (
    get_et_now, get_market_times, is_trading_day, is_market_open,
    is_in_dip_window, is_monday, is_friday, format_currency,
    format_percentage, format_timedelta, time_until, get_day_of_week, ET
)
from src.database import get_database, Database
from src.etrade_client import create_etrade_client, MockETradeClient
from src.strategy import IBITDipStrategy, StrategyConfig, TradeAction
from src.scheduler import TradingScheduler, BotStatus
from src.notifications import create_notification_manager, NotificationConfig
from src.backtester import Backtester, BacktestConfig, run_default_backtest
from src.multi_strategy_backtester import MultiStrategyBacktester, run_comprehensive_backtest
from src.config import load_config, save_config, AppConfig, setup_logging


# Page configuration
st.set_page_config(
    page_title="IBIT Dip Bot",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)


# Initialize session state
def init_session_state():
    """Initialize session state variables."""
    if "initialized" not in st.session_state:
        st.session_state.initialized = True
        st.session_state.config = load_config()
        st.session_state.db = get_database()
        st.session_state.client = None
        st.session_state.strategy = None
        st.session_state.scheduler = None
        st.session_state.bot_running = False
        st.session_state.last_refresh = None


init_session_state()


def get_client():
    """Get or create E*TRADE client."""
    if st.session_state.client is None:
        config = st.session_state.config
        st.session_state.client = create_etrade_client(
            consumer_key=config.etrade.consumer_key,
            consumer_secret=config.etrade.consumer_secret,
            sandbox=config.etrade.sandbox,
            dry_run=config.dry_run
        )
    return st.session_state.client


def get_strategy():
    """Get or create strategy instance."""
    if st.session_state.strategy is None:
        config = st.session_state.config
        client = get_client()
        st.session_state.strategy = IBITDipStrategy(
            client=client,
            config=config.strategy,
            db=st.session_state.db,
            account_id_key=config.etrade.account_id_key
        )
    return st.session_state.strategy


# Custom CSS for styling
def apply_custom_css():
    """Apply custom CSS based on theme."""
    theme = st.session_state.config.theme

    if theme == "dark":
        st.markdown("""
        <style>
        .metric-card {
            background: linear-gradient(135deg, #1e1e2e 0%, #2d2d44 100%);
            border-radius: 10px;
            padding: 20px;
            margin: 10px 0;
            border: 1px solid #3d3d5c;
        }
        .dip-gauge {
            font-size: 48px;
            font-weight: bold;
            text-align: center;
        }
        .dip-positive { color: #4caf50; }
        .dip-negative { color: #f44336; }
        .countdown {
            font-size: 24px;
            text-align: center;
            color: #bb86fc;
        }
        .status-running { color: #4caf50; }
        .status-stopped { color: #f44336; }
        .status-paused { color: #ff9800; }
        </style>
        """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <style>
        .metric-card {
            background: linear-gradient(135deg, #f5f5f5 0%, #e0e0e0 100%);
            border-radius: 10px;
            padding: 20px;
            margin: 10px 0;
            border: 1px solid #ccc;
        }
        .dip-gauge {
            font-size: 48px;
            font-weight: bold;
            text-align: center;
        }
        .dip-positive { color: #2e7d32; }
        .dip-negative { color: #c62828; }
        .countdown {
            font-size: 24px;
            text-align: center;
            color: #6200ea;
        }
        </style>
        """, unsafe_allow_html=True)


# Sidebar Configuration Panel
def render_sidebar():
    """Render the sidebar configuration panel."""
    st.sidebar.title("⚙️ Configuration")

    config = st.session_state.config

    # Theme toggle
    theme = st.sidebar.selectbox(
        "Theme",
        ["dark", "light"],
        index=0 if config.theme == "dark" else 1
    )
    if theme != config.theme:
        config.theme = theme
        st.rerun()

    st.sidebar.divider()

    # Trading Mode
    st.sidebar.subheader("Trading Mode")
    dry_run = st.sidebar.toggle("Dry Run (Paper Trading)", value=config.dry_run)
    config.dry_run = dry_run

    st.sidebar.divider()

    # Strategy Settings
    st.sidebar.subheader("Strategy Settings")

    # Strategy type selection
    strategy_options = {
        "combined": "Combined (Recommended)",
        "mean_reversion": "Mean Reversion",
        "short_thursday": "Short Thursday",
        "original_dip": "Original 10AM Dip (Not Recommended)"
    }
    strategy_type = st.sidebar.selectbox(
        "Strategy Type",
        options=list(strategy_options.keys()),
        format_func=lambda x: strategy_options[x],
        index=list(strategy_options.keys()).index(config.strategy.strategy_type) if config.strategy.strategy_type in strategy_options else 0,
        help="Select trading strategy"
    )
    config.strategy.strategy_type = strategy_type

    # Strategy-specific settings
    if strategy_type in ["mean_reversion", "combined"]:
        mean_rev_threshold = st.sidebar.slider(
            "Mean Reversion Threshold (%)",
            min_value=-5.0,
            max_value=-1.0,
            value=config.strategy.mean_reversion_threshold,
            step=0.5,
            help="Buy after day drops below this threshold"
        )
        config.strategy.mean_reversion_threshold = mean_rev_threshold

    if strategy_type in ["short_thursday", "combined"]:
        enable_short_thu = st.sidebar.toggle(
            "Enable Short Thursday",
            value=config.strategy.enable_short_thursday,
            help="Short IBIT on Thursdays"
        )
        config.strategy.enable_short_thursday = enable_short_thu

    if strategy_type == "original_dip":
        st.sidebar.warning("Original strategy has poor backtested performance. Consider using Combined strategy.")

        # Monday trading
        monday_enabled = st.sidebar.toggle(
            "Enable Monday Trading",
            value=config.strategy.monday_enabled,
            help="Monday historically has weaker performance"
        )
        config.strategy.monday_enabled = monday_enabled

        if monday_enabled:
            monday_threshold = st.sidebar.slider(
                "Monday Threshold (%)",
                min_value=0.6,
                max_value=2.0,
                value=config.strategy.monday_threshold,
                step=0.1,
                help="Dip threshold for Monday trades"
            )
            config.strategy.monday_threshold = monday_threshold

        # Regular threshold
        regular_threshold = st.sidebar.slider(
            "Regular Threshold (%)",
            min_value=0.3,
            max_value=1.5,
            value=config.strategy.regular_threshold,
            step=0.1,
            help="Dip threshold for Tue-Fri trades"
        )
        config.strategy.regular_threshold = regular_threshold

    # Position sizing
    st.sidebar.subheader("Position Sizing")

    max_position_type = st.sidebar.radio(
        "Max Position",
        ["Percentage of Cash", "Fixed Dollar Amount"],
        index=0 if config.strategy.max_position_usd is None else 1
    )

    if max_position_type == "Percentage of Cash":
        max_pct = st.sidebar.slider(
            "Max Position (%)",
            min_value=10,
            max_value=100,
            value=int(config.strategy.max_position_pct),
            step=10
        )
        config.strategy.max_position_pct = float(max_pct)
        config.strategy.max_position_usd = None
    else:
        max_usd = st.sidebar.number_input(
            "Max Position ($)",
            min_value=100,
            max_value=1000000,
            value=int(config.strategy.max_position_usd or 10000),
            step=1000
        )
        config.strategy.max_position_usd = float(max_usd)

    st.sidebar.divider()

    # Notifications
    st.sidebar.subheader("Notifications")
    desktop_notify = st.sidebar.toggle(
        "Desktop Notifications",
        value=config.notifications.desktop_enabled
    )
    config.notifications.desktop_enabled = desktop_notify

    email_notify = st.sidebar.toggle(
        "Email Notifications",
        value=config.notifications.email_enabled
    )
    config.notifications.email_enabled = email_notify

    if email_notify:
        with st.sidebar.expander("Email Settings"):
            smtp_user = st.text_input("SMTP Username", value=config.notifications.smtp_username)
            smtp_pass = st.text_input("SMTP Password", type="password")
            email_to = st.text_input("Send To (comma-separated)")

            if smtp_user:
                config.notifications.smtp_username = smtp_user
            if smtp_pass:
                config.notifications.smtp_password = smtp_pass
            if email_to:
                config.notifications.email_to = [e.strip() for e in email_to.split(",")]

    st.sidebar.divider()

    # Save config button
    if st.sidebar.button("💾 Save Configuration"):
        save_config(config)
        st.sidebar.success("Configuration saved!")

    # Update session state
    st.session_state.config = config


# Main Dashboard
def render_dashboard():
    """Render the main dashboard view."""
    st.title("📈 IBIT Dip Bot Dashboard")

    now = get_et_now()
    times = get_market_times(now.date())

    # Top row - Status and controls
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        # Bot status
        if st.session_state.bot_running:
            st.success("🟢 Bot Running")
        else:
            st.error("🔴 Bot Stopped")

    with col2:
        # Market status
        if is_market_open():
            st.success("🟢 Market Open")
        else:
            st.warning("🟡 Market Closed")

    with col3:
        # Mode indicator
        if st.session_state.config.dry_run:
            st.info("📝 Dry Run Mode")
        else:
            st.warning("💰 Live Trading")

    with col4:
        # Current time
        st.metric("Time (ET)", now.strftime("%H:%M:%S"))

    st.divider()

    # Main content - 2 columns
    left_col, right_col = st.columns([2, 1])

    with left_col:
        # Dip Gauge
        render_dip_gauge()

        st.divider()

        # Equity Curve
        render_equity_curve()

    with right_col:
        # Countdown Timers
        render_countdown_timers()

        st.divider()

        # Quick Controls
        render_quick_controls()

        st.divider()

        # Current Position
        render_position_status()


def render_dip_gauge():
    """Render the live dip percentage gauge."""
    st.subheader("📊 Dip Gauge")

    try:
        client = get_client()
        quote = client.get_ibit_quote()

        current_price = quote.get("last_price", 0)
        open_price = quote.get("open_price", 0)

        if open_price > 0:
            dip_pct = ((open_price - current_price) / open_price) * 100
        else:
            dip_pct = 0

        # Display gauge
        col1, col2, col3 = st.columns(3)

        with col1:
            st.metric(
                "Open Price",
                f"${open_price:.2f}",
                help="IBIT price at market open"
            )

        with col2:
            st.metric(
                "Current Price",
                f"${current_price:.2f}",
                delta=f"{quote.get('change_pct', 0):.2f}%"
            )

        with col3:
            threshold = st.session_state.config.strategy.regular_threshold
            color = "normal" if dip_pct < threshold else "inverse"
            st.metric(
                "Dip from Open",
                f"{dip_pct:.2f}%",
                delta=f"Threshold: {threshold}%",
                delta_color=color
            )

        # Visual gauge using Plotly
        fig = go.Figure(go.Indicator(
            mode="gauge+number+delta",
            value=dip_pct,
            domain={'x': [0, 1], 'y': [0, 1]},
            title={'text': "Dip Percentage", 'font': {'size': 20}},
            delta={'reference': threshold, 'increasing': {'color': "green"}},
            gauge={
                'axis': {'range': [-2, 3], 'tickwidth': 1},
                'bar': {'color': "darkblue"},
                'bgcolor': "white",
                'borderwidth': 2,
                'bordercolor': "gray",
                'steps': [
                    {'range': [-2, 0], 'color': 'lightgray'},
                    {'range': [0, threshold], 'color': 'lightyellow'},
                    {'range': [threshold, 3], 'color': 'lightgreen'}
                ],
                'threshold': {
                    'line': {'color': "red", 'width': 4},
                    'thickness': 0.75,
                    'value': threshold
                }
            }
        ))

        fig.update_layout(
            height=300,
            margin=dict(l=20, r=20, t=40, b=20),
            paper_bgcolor='rgba(0,0,0,0)',
            font={'color': 'white' if st.session_state.config.theme == 'dark' else 'black'}
        )

        st.plotly_chart(fig, use_container_width=True)

    except Exception as e:
        st.error(f"Error fetching quote: {e}")
        st.info("Make sure you're authenticated with E*TRADE or running in dry-run mode.")


def render_countdown_timers():
    """Render countdown timers for key events."""
    st.subheader("⏱️ Countdowns")

    now = get_et_now()
    times = get_market_times(now.date())

    # Market open countdown
    if now < times["market_open"]:
        time_to_open = time_until(times["market_open"])
        st.metric("Market Open", format_timedelta(time_to_open))
    else:
        st.metric("Market Open", "✓ Open")

    # Dip window countdown
    if now < times["dip_window_start"]:
        time_to_dip = time_until(times["dip_window_start"])
        st.metric("Dip Window", format_timedelta(time_to_dip))
    elif now <= times["dip_window_end"]:
        time_remaining = time_until(times["dip_window_end"])
        st.metric("Dip Window", f"Active ({format_timedelta(time_remaining)} left)")
    else:
        st.metric("Dip Window", "✓ Passed")

    # Market close countdown
    close_time = times["friday_close"] if is_friday() else times["market_close"]
    if now < close_time:
        time_to_close = time_until(close_time)
        st.metric("Market Close", format_timedelta(time_to_close))
    else:
        st.metric("Market Close", "✓ Closed")


def render_quick_controls():
    """Render quick control buttons."""
    st.subheader("🎮 Controls")

    col1, col2 = st.columns(2)

    with col1:
        if st.button("🟢 Force Buy", use_container_width=True, disabled=st.session_state.config.dry_run is False):
            strategy = get_strategy()
            result = strategy.force_buy()
            if result.get("success"):
                st.success(f"Bought {result.get('shares')} shares!")
            else:
                st.error(result.get("reason"))

        if st.button("⏸️ Pause Until Tuesday", use_container_width=True):
            strategy = get_strategy()
            strategy.pause_until_tuesday()
            st.info("Bot paused until Tuesday")

    with col2:
        if st.button("🔴 Force Sell", use_container_width=True, disabled=st.session_state.config.dry_run is False):
            strategy = get_strategy()
            result = strategy.force_sell()
            if result.get("success"):
                st.success(f"Sold! P&L: {format_currency(result.get('dollar_pnl', 0))}")
            else:
                st.error(result.get("reason"))

        if st.button("🔄 Refresh", use_container_width=True):
            st.session_state.last_refresh = now
            st.rerun()


def render_position_status():
    """Render current position status."""
    st.subheader("📍 Position")

    db = st.session_state.db
    open_trade = db.get_open_trade()

    if open_trade:
        st.success("Position Open")
        st.metric("Shares", open_trade["shares"])
        st.metric("Entry Price", f"${open_trade['entry_price']:.2f}")
        st.metric("Entry Dip", f"{open_trade['dip_percentage']:.2f}%")

        # Calculate unrealized P&L
        try:
            client = get_client()
            quote = client.get_ibit_quote()
            current = quote.get("last_price", open_trade["entry_price"])
            unrealized = (current - open_trade["entry_price"]) * open_trade["shares"]
            unrealized_pct = ((current - open_trade["entry_price"]) / open_trade["entry_price"]) * 100
            st.metric("Unrealized P&L", f"{format_currency(unrealized)} ({unrealized_pct:+.2f}%)")
        except:
            pass
    else:
        st.info("No open position")


def render_equity_curve():
    """Render equity curve chart."""
    st.subheader("📈 Equity Curve")

    db = st.session_state.db
    curve_data = db.get_equity_curve()

    if not curve_data:
        st.info("No trade history yet. Run some trades to see the equity curve.")
        return

    df = pd.DataFrame(curve_data)
    df['date'] = pd.to_datetime(df['date'])

    # Create figure
    fig = go.Figure()

    # Equity curve
    fig.add_trace(go.Scatter(
        x=df['date'],
        y=df['cumulative_pnl'],
        mode='lines+markers',
        name='Strategy P&L',
        line=dict(color='#4caf50', width=2),
        marker=dict(size=8)
    ))

    # Zero line
    fig.add_hline(y=0, line_dash="dash", line_color="gray")

    fig.update_layout(
        title="Cumulative P&L",
        xaxis_title="Date",
        yaxis_title="P&L ($)",
        height=400,
        showlegend=True,
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font={'color': 'white' if st.session_state.config.theme == 'dark' else 'black'}
    )

    st.plotly_chart(fig, use_container_width=True)


# Trade History Tab
def render_trade_history():
    """Render trade history table."""
    st.title("📋 Trade History")

    db = st.session_state.db
    trades = db.get_trade_history(limit=100)

    if not trades:
        st.info("No trades yet.")
        return

    df = pd.DataFrame(trades)

    # Format columns
    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
    df['entry_price'] = df['entry_price'].apply(lambda x: f"${x:.2f}")
    df['exit_price'] = df['exit_price'].apply(lambda x: f"${x:.2f}" if x else "-")
    df['dip_percentage'] = df['dip_percentage'].apply(lambda x: f"{x:.2f}%")
    df['dollar_pnl'] = df['dollar_pnl'].apply(lambda x: f"${x:+.2f}" if x else "-")
    df['percentage_pnl'] = df['percentage_pnl'].apply(lambda x: f"{x:+.2f}%" if x else "-")

    # Select columns to display
    display_cols = [
        'date', 'day_of_week', 'dip_percentage', 'entry_price',
        'exit_price', 'shares', 'dollar_pnl', 'percentage_pnl', 'status'
    ]

    st.dataframe(
        df[display_cols],
        use_container_width=True,
        hide_index=True
    )

    # Statistics
    st.divider()
    st.subheader("Statistics")

    stats = db.get_trade_statistics()

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Total Trades", stats.get("total_trades", 0))
    with col2:
        st.metric("Win Rate", f"{stats.get('win_rate', 0):.1f}%")
    with col3:
        st.metric("Total P&L", format_currency(stats.get("total_pnl", 0) or 0))
    with col4:
        st.metric("Avg Return", f"{stats.get('avg_return', 0) or 0:.2f}%")


# Backtesting Tab
def render_backtest():
    """Render backtesting interface with multi-strategy support."""
    st.title("🔬 Backtesting")

    # Strategy selection for backtest
    backtest_tabs = st.tabs(["Single Strategy", "Compare All Strategies"])

    with backtest_tabs[0]:
        render_single_strategy_backtest()

    with backtest_tabs[1]:
        render_multi_strategy_backtest()


def render_single_strategy_backtest():
    """Render single strategy backtest interface."""
    col1, col2 = st.columns([1, 2])

    with col1:
        st.subheader("Parameters")

        # Strategy selection
        strategy_choice = st.selectbox(
            "Strategy",
            ["Mean Reversion", "Short Thursday", "Combined", "Original Dip"],
            index=2,
            help="Select strategy to backtest"
        )

        # Date range
        start_date = st.date_input(
            "Start Date",
            value=date(2024, 1, 15),  # IBIT launch
            key="single_start"
        )
        end_date = st.date_input(
            "End Date",
            value=date.today(),
            key="single_end"
        )

        # Strategy-specific parameters
        if strategy_choice in ["Mean Reversion", "Combined"]:
            mr_threshold = st.slider(
                "Mean Reversion Threshold (%)",
                min_value=-5.0,
                max_value=-1.0,
                value=-3.0,
                step=0.5,
                help="Buy after day drops this much"
            )

        if strategy_choice == "Original Dip":
            st.warning("Original strategy shows poor backtested performance!")
            threshold = st.slider(
                "Dip Threshold (%)",
                min_value=0.3,
                max_value=2.0,
                value=0.6,
                step=0.1
            )
            monday_enabled = st.checkbox("Enable Monday Trading", value=False)

        # Capital
        initial_capital = st.number_input(
            "Initial Capital ($)",
            min_value=1000,
            max_value=1000000,
            value=10000,
            step=1000,
            key="single_capital"
        )

        # Run button
        run_backtest = st.button("🚀 Run Backtest", use_container_width=True, key="single_run")

    with col2:
        if run_backtest:
            with st.spinner("Running backtest..."):
                try:
                    backtester = MultiStrategyBacktester(initial_capital=initial_capital)
                    backtester.load_data(start_date, end_date)

                    # Run appropriate strategy
                    if strategy_choice == "Mean Reversion":
                        result = backtester.backtest_mean_reversion(threshold=mr_threshold)
                    elif strategy_choice == "Short Thursday":
                        result = backtester.backtest_short_thursday()
                    elif strategy_choice == "Combined":
                        result = backtester.backtest_combined(mean_reversion_threshold=mr_threshold)
                    else:  # Original Dip
                        result = run_default_backtest(
                            start_date=start_date,
                            end_date=end_date,
                            threshold=threshold,
                            monday_enabled=monday_enabled,
                            initial_capital=initial_capital
                        )

                    # Display results
                    st.subheader(f"Results: {strategy_choice}")

                    # Metrics row
                    m1, m2, m3, m4 = st.columns(4)
                    with m1:
                        st.metric("Total Trades", result.total_trades)
                    with m2:
                        st.metric("Win Rate", f"{result.win_rate:.1f}%")
                    with m3:
                        st.metric("Total Return", f"{result.total_return_pct:+.1f}%")
                    with m4:
                        st.metric("Sharpe Ratio", f"{result.sharpe_ratio:.2f}")

                    m5, m6, m7, m8 = st.columns(4)
                    with m5:
                        st.metric("Avg Return", f"{result.avg_return_pct:+.2f}%")
                    with m6:
                        st.metric("Best Trade", f"{result.best_trade_pct:+.2f}%")
                    with m7:
                        st.metric("Worst Trade", f"{result.worst_trade_pct:+.2f}%")
                    with m8:
                        vs_bh = result.total_return_pct - result.buy_hold_return_pct
                        st.metric("vs Buy&Hold", f"{vs_bh:+.1f}%")

                    # Equity curve
                    if result.trades:
                        df = result.to_dataframe()
                        df['cumulative'] = df['dollar_pnl'].cumsum() if 'dollar_pnl' in df.columns else df['cumulative_pnl']

                        fig = go.Figure()
                        fig.add_trace(go.Scatter(
                            x=df['date'],
                            y=df['cumulative'] if 'cumulative' in df.columns else df.get('cumulative_pnl', []),
                            mode='lines+markers',
                            name='Strategy',
                            line=dict(color='#4caf50')
                        ))

                        fig.update_layout(
                            title="Backtest Equity Curve",
                            xaxis_title="Date",
                            yaxis_title="Cumulative P&L ($)",
                            height=400
                        )

                        st.plotly_chart(fig, use_container_width=True)

                        # Trade table
                        st.subheader("Trades")
                        st.dataframe(df, use_container_width=True, hide_index=True)

                except Exception as e:
                    st.error(f"Backtest failed: {e}")
                    import traceback
                    st.code(traceback.format_exc())


def render_multi_strategy_backtest():
    """Render multi-strategy comparison backtest."""
    st.subheader("Compare All Strategies")

    col1, col2 = st.columns([1, 3])

    with col1:
        # Date range
        start_date = st.date_input(
            "Start Date",
            value=date(2024, 1, 15),
            key="multi_start"
        )
        end_date = st.date_input(
            "End Date",
            value=date.today(),
            key="multi_end"
        )

        initial_capital = st.number_input(
            "Initial Capital ($)",
            min_value=1000,
            max_value=1000000,
            value=10000,
            step=1000,
            key="multi_capital"
        )

        run_comparison = st.button("🚀 Compare Strategies", use_container_width=True)

    with col2:
        if run_comparison:
            with st.spinner("Running all backtests..."):
                try:
                    results, comparison_df = run_comprehensive_backtest(
                        start_date=start_date,
                        end_date=end_date,
                        initial_capital=initial_capital
                    )

                    st.subheader("Strategy Comparison")
                    st.dataframe(comparison_df, use_container_width=True, hide_index=True)

                    # Best strategy highlight
                    best_strategy = max(results.items(), key=lambda x: x[1].total_return_pct)
                    st.success(f"Best Strategy: **{best_strategy[0]}** with {best_strategy[1].total_return_pct:+.1f}% return")

                    # Equity curves comparison
                    st.subheader("Equity Curves")

                    fig = go.Figure()
                    colors = ['#4caf50', '#2196f3', '#ff9800', '#e91e63', '#9c27b0', '#00bcd4']

                    for i, (name, result) in enumerate(results.items()):
                        if result.trades:
                            df = result.to_dataframe()
                            df['cumulative'] = df['dollar_pnl'].cumsum()
                            fig.add_trace(go.Scatter(
                                x=df['date'],
                                y=df['cumulative'],
                                mode='lines',
                                name=name,
                                line=dict(color=colors[i % len(colors)])
                            ))

                    fig.add_hline(y=0, line_dash="dash", line_color="gray")

                    fig.update_layout(
                        title="Strategy Comparison - Equity Curves",
                        xaxis_title="Date",
                        yaxis_title="Cumulative P&L ($)",
                        height=500,
                        showlegend=True
                    )

                    st.plotly_chart(fig, use_container_width=True)

                    # Individual strategy details
                    st.subheader("Individual Strategy Details")
                    for name, result in results.items():
                        with st.expander(f"{name} - {result.total_return_pct:+.1f}%"):
                            st.text(result.summary())

                except Exception as e:
                    st.error(f"Comparison failed: {e}")
                    import traceback
                    st.code(traceback.format_exc())


# Settings Tab
def render_settings():
    """Render settings and authentication."""
    st.title("⚙️ Settings")

    tab1, tab2, tab3 = st.tabs(["E*TRADE Auth", "Notifications", "About"])

    with tab1:
        st.subheader("E*TRADE Authentication")

        config = st.session_state.config

        # Current status
        client = get_client()
        if client.is_authenticated():
            st.success("✓ Authenticated with E*TRADE")

            if st.button("Renew Token"):
                if client.renew_token():
                    st.success("Token renewed!")
                else:
                    st.error("Token renewal failed")

            if st.button("Revoke Token"):
                client.revoke_token()
                st.session_state.client = None
                st.warning("Token revoked. You'll need to re-authenticate.")

        else:
            st.warning("Not authenticated with E*TRADE")

            st.markdown("""
            ### Setup Instructions

            1. **Get API Keys**: Apply for API access at [E*TRADE Developer Portal](https://developer.etrade.com)
            2. **Set Environment Variables**:
            ```bash
            export ETRADE_CONSUMER_KEY="your_key"
            export ETRADE_CONSUMER_SECRET="your_secret"
            export ETRADE_ACCOUNT_ID="your_account_id"
            ```
            3. **Authenticate**: Click the button below to start OAuth flow
            """)

            if st.button("🔐 Authenticate with E*TRADE"):
                try:
                    if not config.etrade.consumer_key:
                        st.error("Consumer key not configured. Set ETRADE_CONSUMER_KEY environment variable.")
                    else:
                        st.info("Check your terminal for the authentication URL...")
                        client.authenticate()
                        st.success("Authentication successful!")
                        st.rerun()
                except Exception as e:
                    st.error(f"Authentication failed: {e}")

    with tab2:
        st.subheader("Notification Settings")

        st.markdown("""
        Configure notifications in the sidebar or set these environment variables:
        ```bash
        export SMTP_SERVER="smtp.gmail.com"
        export SMTP_USERNAME="your_email@gmail.com"
        export SMTP_PASSWORD="your_app_password"
        export EMAIL_TO="recipient@example.com"
        ```

        **Note**: For Gmail, use an [App Password](https://support.google.com/accounts/answer/185833)
        """)

    with tab3:
        st.subheader("About IBIT Dip Bot")

        st.markdown("""
        ### Strategy Overview

        The IBIT Dip Bot implements an intraday strategy that:

        1. **Monitors** IBIT price at market open (9:30 AM ET)
        2. **Buys** when price dips ≥0.6% during 10:00-10:59 AM ET
        3. **Sells** at market close (3:55 PM on Fridays, 4:00 PM other days)
        4. **Never holds** positions overnight or over weekends

        ### Default Configuration

        | Day | Trading | Threshold |
        |-----|---------|-----------|
        | Monday | Disabled | 1.0% (if enabled) |
        | Tue-Thu | Enabled | 0.6% |
        | Friday | Enabled | 0.6% (sell at 3:55 PM) |

        ### Performance (Backtested Jun-Dec 2025)

        - **Total Return**: +61.8%
        - **Win Rate**: 66.1%
        - **Max Drawdown**: -5.9%

        ---

        **Disclaimer**: Past performance does not guarantee future results.
        This software is for educational purposes. Trade at your own risk.
        """)


# Main App
def main():
    """Main application entry point."""
    apply_custom_css()
    render_sidebar()

    # Navigation
    tab1, tab2, tab3, tab4 = st.tabs([
        "📊 Dashboard",
        "📋 Trade History",
        "🔬 Backtest",
        "⚙️ Settings"
    ])

    with tab1:
        render_dashboard()

    with tab2:
        render_trade_history()

    with tab3:
        render_backtest()

    with tab4:
        render_settings()

    # Auto-refresh every 30 seconds during market hours
    if is_market_open():
        time.sleep(0.1)  # Small delay to prevent too rapid refreshes
        # Note: For production, use streamlit-autorefresh component


if __name__ == "__main__":
    main()
