"""
Bitcoin ETF Smart Trading Bot - Minimal Monitor Dashboard

A clean, single-screen trading monitor with pop-art styling.
Inspired by Hungarian animation aesthetics (Pannónia Film).

Features:
- Real-time position monitoring with P&L
- 45-second auto-refresh
- Slide-out settings panel
- Bold pop-art design
"""

import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables before local imports
load_dotenv(Path(__file__).parent / ".env")

import streamlit as st  # noqa: E402

from src.smart_scheduler import BotStatus, SmartScheduler  # noqa: E402
from src.smart_strategy import StrategyConfig  # noqa: E402
from src.trading_bot import create_trading_bot  # noqa: E402

# Page config - wide layout, custom title
st.set_page_config(
    page_title="BTRADE",
    page_icon="₿",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# Custom CSS for pop-art styling with Futura-like font
st.markdown(
    """
<style>
    /* Import Inter font (clean, Helvetica-like) */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

    /* Hide Streamlit branding */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}

    /* Apply Inter font globally */
    html, body, [class*="css"], .stMarkdown, .stButton > button, p, span, div {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Helvetica Neue', sans-serif !important;
    }

    /* Main background */
    .stApp {
        background-color: #1E3A8A;
    }

    /* Card styling */
    .card {
        background-color: #FFFFFF;
        border: 5px solid #000000;
        border-radius: 8px;
        padding: 20px;
        margin-bottom: 16px;
    }

    /* Position card - biggest */
    .position-card {
        background-color: #FFFFFF;
        border: 5px solid #000000;
        border-radius: 8px;
        padding: 24px;
        margin-bottom: 16px;
    }

    /* Status bar */
    .status-bar {
        background-color: #FFFFFF;
        border: 5px solid #000000;
        border-radius: 8px;
        padding: 16px;
        margin-bottom: 16px;
    }

    /* Green for gains */
    .gain {
        color: #16A34A;
        font-weight: bold;
    }

    /* Red for losses */
    .loss {
        color: #DC2626;
        font-weight: bold;
    }

    /* Orange accent */
    .accent {
        color: #FF6B35;
    }

    /* Blue labels */
    .label {
        color: #1E3A8A;
        font-weight: bold;
        text-transform: uppercase;
    }

    /* All caps text */
    .caps {
        text-transform: uppercase;
        letter-spacing: 1px;
    }

    /* Big numbers */
    .big-number {
        font-size: 3rem;
        font-weight: bold;
        line-height: 1;
    }

    .medium-number {
        font-size: 2rem;
        font-weight: bold;
    }

    .small-text {
        font-size: 0.875rem;
        opacity: 0.6;
    }

    /* Settings panel */
    .settings-panel {
        background-color: #FFFFFF;
        border-left: 5px solid #000000;
        padding: 20px;
        height: 100vh;
        position: fixed;
        right: 0;
        top: 0;
        width: 320px;
        z-index: 1000;
        overflow-y: auto;
    }

    /* Refresh indicator */
    .refresh-indicator {
        color: #FFFFFF;
        opacity: 0.6;
        font-size: 0.75rem;
        text-transform: uppercase;
    }

    /* Button styling */
    .stButton > button {
        background-color: #FF6B35;
        color: #FFFFFF;
        border: 4px solid #000000;
        border-radius: 8px;
        font-weight: bold;
        text-transform: uppercase;
        padding: 12px 24px;
    }

    .stButton > button:hover {
        background-color: #E55A2B;
        border-color: #000000;
    }

    /* Green button for running state */
    .stButton > button[kind="primary"] {
        background-color: #16A34A;
    }

    /* Sidebar styling - slides in from left */
    [data-testid="stSidebar"] {
        background-color: #FFFFFF;
        border-right: 5px solid #000000;
    }

    [data-testid="stSidebar"] .stMarkdown {
        color: #000000;
    }

    [data-testid="stSidebar"] h2 {
        color: #1E3A8A;
        border-bottom: 3px solid #000;
        padding-bottom: 10px;
    }

    /* Gear button styling */
    div[data-testid="column"]:last-child .stButton button {
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        font-size: 44px !important;
        color: #FF6B35 !important;
        text-shadow: -2px -2px 0 #000, 2px -2px 0 #000, -2px 2px 0 #000, 2px 2px 0 #000 !important;
        transition: transform 0.2s ease !important;
        padding: 0 !important;
        min-height: auto !important;
        line-height: 1 !important;
    }

    div[data-testid="column"]:last-child .stButton button:hover {
        background: transparent !important;
        transform: rotate(30deg) !important;
        color: #E55A2B !important;
        border: none !important;
    }

    div[data-testid="column"]:last-child .stButton button:active,
    div[data-testid="column"]:last-child .stButton button:focus {
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
    }

    /* Settings panel styling */
    .settings-panel-container {
        background-color: #FFFFFF;
        border: 5px solid #000000;
        border-radius: 8px;
        padding: 20px;
        margin-top: 16px;
    }

    /* Toggle styling */
    .stToggle label {
        font-weight: 600 !important;
        text-transform: uppercase !important;
    }
</style>
""",
    unsafe_allow_html=True,
)


# Session state initialization
def init_session_state():
    """Initialize session state variables."""
    if "config" not in st.session_state:
        st.session_state.config = StrategyConfig()
    if "trading_mode" not in st.session_state:
        st.session_state.trading_mode = os.environ.get("TRADING_MODE", "paper")
    if "bot" not in st.session_state:
        st.session_state.bot = None
    if "scheduler" not in st.session_state:
        st.session_state.scheduler = None
    if "last_refresh" not in st.session_state:
        st.session_state.last_refresh = datetime.now()
    if "show_settings" not in st.session_state:
        st.session_state.show_settings = False


def get_or_create_bot():
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


def format_currency(value: float) -> str:
    """Format a number as currency."""
    if value >= 0:
        return f"${value:,.0f}"
    else:
        return f"-${abs(value):,.0f}"


def format_pnl(value: float, include_sign: bool = True) -> str:
    """Format P&L with sign."""
    if include_sign:
        return f"+${value:,.0f}" if value >= 0 else f"-${abs(value):,.0f}"
    return f"${abs(value):,.0f}"


def format_percent(value: float) -> str:
    """Format percentage."""
    return f"+{value:.1f}%" if value >= 0 else f"{value:.1f}%"


def get_pnl_class(value: float) -> str:
    """Get CSS class for P&L coloring."""
    return "gain" if value >= 0 else "loss"


def render_position_card(portfolio: dict):
    """Render the main position card."""
    positions = portfolio.get("positions", [])

    if positions:
        pos = positions[0]  # Show first position
        symbol = pos["symbol"]
        shares = pos["shares"]
        entry_price = pos["entry_price"]
        current_price = pos["current_price"]
        pnl = pos["unrealized_pnl"]
        pnl_pct = pos["unrealized_pnl_pct"]
        pnl_class = get_pnl_class(pnl)

        st.markdown(
            f"""
        <div class="position-card">
            <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                <div>
                    <span style="font-size: 2.5rem; font-weight: bold; color: #000000;">{symbol}</span>
                    <span style="font-size: 1.25rem; color: #1E3A8A; margin-left: 8px;">2X LONG</span>
                </div>
                <div style="text-align: right;">
                    <div class="big-number {pnl_class}">{format_pnl(pnl)}</div>
                    <div class="{pnl_class}" style="font-size: 1.25rem;">{format_percent(pnl_pct)}</div>
                </div>
            </div>
            <div style="margin-top: 16px; opacity: 0.6;">
                <div style="font-size: 1rem;">{shares} SHARES @ ${entry_price:.2f}</div>
                <div style="font-size: 1rem;">NOW: ${current_price:.2f}</div>
            </div>
        </div>
        """,
            unsafe_allow_html=True,
        )
    else:
        # No position - show empty state
        st.markdown(
            """
        <div class="position-card">
            <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                <div>
                    <span style="font-size: 2.5rem; font-weight: bold; color: #000000;">NO POSITION</span>
                </div>
                <div style="text-align: right;">
                    <div class="big-number" style="color: #000000;">$0</div>
                </div>
            </div>
            <div style="margin-top: 16px; opacity: 0.6;">
                <div style="font-size: 1rem;">WAITING FOR SIGNAL</div>
            </div>
        </div>
        """,
            unsafe_allow_html=True,
        )


def render_cash_card(portfolio: dict):
    """Render the cash card."""
    cash = portfolio.get("cash", 0)

    st.markdown(
        f"""
    <div class="card">
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <span class="label" style="font-size: 1.5rem;">CASH</span>
            <span class="medium-number" style="color: #000000;">{format_currency(cash)}</span>
        </div>
    </div>
    """,
        unsafe_allow_html=True,
    )


def render_total_card(portfolio: dict):
    """Render the total portfolio card."""
    total_value = portfolio.get("total_value", 0)
    total_pnl = portfolio.get("total_pnl", 0)
    total_pnl_pct = portfolio.get("total_pnl_pct", 0)
    pnl_class = get_pnl_class(total_pnl)

    st.markdown(
        f"""
    <div class="card">
        <div style="display: flex; justify-content: space-between; align-items: flex-start;">
            <div>
                <div class="label" style="font-size: 1.5rem;">TOTAL</div>
                <div class="big-number" style="color: #000000; margin-top: 8px;">{format_currency(total_value)}</div>
            </div>
            <div style="text-align: right;">
                <div class="medium-number {pnl_class}">{format_pnl(total_pnl)}</div>
                <div class="{pnl_class}" style="font-size: 1.25rem;">{format_percent(total_pnl_pct)}</div>
            </div>
        </div>
    </div>
    """,
        unsafe_allow_html=True,
    )


def render_status_bar(bot, scheduler):
    """Render the status bar with gear button."""
    # Get bot status
    is_running = scheduler and scheduler.status == BotStatus.RUNNING
    status_text = "RUNNING" if is_running else "STOPPED"
    status_color = "#16A34A" if is_running else "#DC2626"

    # Get last action and next action
    status = bot.get_status()
    last_action = "NONE"
    next_action = "WAITING"

    # Check for today's signal
    signal_etf = status.get("signal_etf", "")
    if signal_etf:
        next_action = f"BUY {signal_etf}"

    # Get scheduled jobs if running
    if is_running and scheduler:
        sched_status = scheduler.get_status()
        jobs = sched_status.get("next_jobs", [])
        if jobs:
            next_job = jobs[0]
            next_run = next_job.get("next_run", "")[:16] if next_job.get("next_run") else ""
            if next_run:
                next_action = f"{next_job['name'].upper()} {next_run}"

    # Status bar - single HTML row for perfect alignment
    st.markdown(
        f"""
        <div style="display: flex; align-items: center; gap: 12px;">
            <div style="display: flex; align-items: center; gap: 10px; padding: 14px 18px; background: white; border: 5px solid black; border-radius: 8px;">
                <div style="width: 16px; height: 16px; border-radius: 50%; background-color: {status_color}; border: 3px solid #000; flex-shrink: 0;"></div>
                <span style="font-weight: 700; color: #000; font-size: 1rem; white-space: nowrap;">{status_text}</span>
            </div>
            <div style="display: flex; align-items: center; justify-content: center; gap: 24px; padding: 14px 18px; background: white; border: 5px solid black; border-radius: 8px; flex: 1;">
                <span style="color: #000; opacity: 0.6; font-size: 0.9rem;">LAST: {last_action}</span>
                <span style="color: #000; opacity: 0.6; font-size: 0.9rem;">NEXT: {next_action}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Gear button row
    _, gear_col = st.columns([9, 1])
    with gear_col:
        if st.button("⚙", key="gear_btn"):
            st.session_state.show_settings = not st.session_state.get("show_settings", False)
            st.rerun()

    # Settings panel when toggled
    if st.session_state.get("show_settings", False):
        with st.container():
            st.markdown(
                """<div style="background: white; border: 5px solid black; border-radius: 8px; padding: 20px; margin-top: -20px;">""",
                unsafe_allow_html=True,
            )
            render_settings_expander(bot, scheduler)
            st.markdown("</div>", unsafe_allow_html=True)


def render_refresh_indicator():
    """Render the refresh indicator."""
    elapsed = (datetime.now() - st.session_state.last_refresh).seconds
    st.markdown(
        f"""
    <div class="refresh-indicator">
        UPDATED {elapsed} SEC AGO
    </div>
    """,
        unsafe_allow_html=True,
    )


def render_settings_popover(bot, scheduler):
    """Render settings inside popover."""
    st.markdown("### ⚙️ SETTINGS")

    # Mode toggle
    st.markdown("**MODE**")
    mode = st.radio(
        "Trading Mode",
        ["PAPER", "LIVE"],
        index=0 if st.session_state.trading_mode == "paper" else 1,
        horizontal=True,
        label_visibility="collapsed",
        key="popover_mode",
    )
    new_mode = mode.lower()
    if new_mode != st.session_state.trading_mode:
        st.session_state.trading_mode = new_mode
        st.session_state.bot = None
        st.rerun()

    st.markdown("---")

    # Strategy toggles
    st.markdown("**STRATEGIES**")

    mr_enabled = st.toggle(
        "MEAN REVERSION",
        value=st.session_state.config.mean_reversion_enabled,
        key="popover_mr",
    )
    if mr_enabled != st.session_state.config.mean_reversion_enabled:
        st.session_state.config.mean_reversion_enabled = mr_enabled
        st.session_state.bot = None

    th_enabled = st.toggle(
        "SHORT THURSDAY",
        value=st.session_state.config.short_thursday_enabled,
        key="popover_th",
    )
    if th_enabled != st.session_state.config.short_thursday_enabled:
        st.session_state.config.short_thursday_enabled = th_enabled
        st.session_state.bot = None

    cd_enabled = st.toggle(
        "CRASH DAY",
        value=st.session_state.config.crash_day_enabled,
        key="popover_cd",
    )
    if cd_enabled != st.session_state.config.crash_day_enabled:
        st.session_state.config.crash_day_enabled = cd_enabled
        st.session_state.bot = None

    st.markdown("---")

    # Bot control
    is_running = scheduler and scheduler.status == BotStatus.RUNNING
    if is_running:
        if st.button("⏹️ STOP BOT", type="secondary", use_container_width=True, key="popover_stop"):
            scheduler.stop()
            st.rerun()
    else:
        if st.button("▶️ START BOT", type="primary", use_container_width=True, key="popover_start"):
            scheduler.start()
            st.rerun()


def render_settings_expander(bot, scheduler):
    """Render settings inside expander."""
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**MODE**")
        mode = st.radio(
            "Trading Mode",
            ["PAPER", "LIVE"],
            index=0 if st.session_state.trading_mode == "paper" else 1,
            horizontal=True,
            label_visibility="collapsed",
            key="expander_mode",
        )
        new_mode = mode.lower()
        if new_mode != st.session_state.trading_mode:
            st.session_state.trading_mode = new_mode
            st.session_state.bot = None

    with col2:
        is_running = scheduler and scheduler.status == BotStatus.RUNNING
        if is_running:
            if st.button("⏹️ STOP BOT", type="secondary", use_container_width=True, key="exp_stop"):
                scheduler.stop()
        else:
            if st.button("▶️ START BOT", type="primary", use_container_width=True, key="exp_start"):
                scheduler.start()

    st.markdown("---")
    st.markdown("**STRATEGIES**")

    col1, col2, col3 = st.columns(3)

    with col1:
        mr_enabled = st.toggle(
            "MEAN REVERSION",
            value=st.session_state.config.mean_reversion_enabled,
            key="exp_mr",
        )
        if mr_enabled != st.session_state.config.mean_reversion_enabled:
            st.session_state.config.mean_reversion_enabled = mr_enabled
            st.session_state.bot = None

    with col2:
        th_enabled = st.toggle(
            "SHORT THURSDAY",
            value=st.session_state.config.short_thursday_enabled,
            key="exp_th",
        )
        if th_enabled != st.session_state.config.short_thursday_enabled:
            st.session_state.config.short_thursday_enabled = th_enabled
            st.session_state.bot = None

    with col3:
        cd_enabled = st.toggle(
            "CRASH DAY",
            value=st.session_state.config.crash_day_enabled,
            key="exp_cd",
        )
        if cd_enabled != st.session_state.config.crash_day_enabled:
            st.session_state.config.crash_day_enabled = cd_enabled
            st.session_state.bot = None


def render_settings_panel(bot, scheduler):
    """Render settings panel below the status bar."""
    st.markdown(
        """
        <div class="settings-panel-container">
        """,
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**MODE**")
        mode = st.radio(
            "Trading Mode",
            ["PAPER", "LIVE"],
            index=0 if st.session_state.trading_mode == "paper" else 1,
            horizontal=True,
            label_visibility="collapsed",
            key="panel_mode",
        )
        new_mode = mode.lower()
        if new_mode != st.session_state.trading_mode:
            st.session_state.trading_mode = new_mode
            st.session_state.bot = None
            st.rerun()

    with col2:
        # Bot control
        is_running = scheduler and scheduler.status == BotStatus.RUNNING
        if is_running:
            if st.button(
                "⏹️ STOP BOT", type="secondary", use_container_width=True, key="panel_stop"
            ):
                scheduler.stop()
                st.rerun()
        else:
            if st.button(
                "▶️ START BOT", type="primary", use_container_width=True, key="panel_start"
            ):
                scheduler.start()
                st.rerun()

    st.markdown("---")
    st.markdown("**STRATEGIES**")

    col1, col2, col3 = st.columns(3)

    with col1:
        mr_enabled = st.toggle(
            "MEAN REVERSION",
            value=st.session_state.config.mean_reversion_enabled,
            key="panel_mr",
        )
        if mr_enabled != st.session_state.config.mean_reversion_enabled:
            st.session_state.config.mean_reversion_enabled = mr_enabled
            st.session_state.bot = None

    with col2:
        th_enabled = st.toggle(
            "SHORT THURSDAY",
            value=st.session_state.config.short_thursday_enabled,
            key="panel_th",
        )
        if th_enabled != st.session_state.config.short_thursday_enabled:
            st.session_state.config.short_thursday_enabled = th_enabled
            st.session_state.bot = None

    with col3:
        cd_enabled = st.toggle(
            "CRASH DAY",
            value=st.session_state.config.crash_day_enabled,
            key="panel_cd",
        )
        if cd_enabled != st.session_state.config.crash_day_enabled:
            st.session_state.config.crash_day_enabled = cd_enabled
            st.session_state.bot = None

    st.markdown("</div>", unsafe_allow_html=True)


def render_settings_sidebar():
    """Render the settings in sidebar (backup - mainly using popover now)."""
    with st.sidebar:
        st.markdown("## SETTINGS")
        st.markdown("---")

        # Mode toggle
        st.markdown("### MODE")
        mode = st.radio(
            "Trading Mode",
            ["PAPER", "LIVE"],
            index=0 if st.session_state.trading_mode == "paper" else 1,
            horizontal=True,
            label_visibility="collapsed",
        )
        new_mode = mode.lower()
        if new_mode != st.session_state.trading_mode:
            st.session_state.trading_mode = new_mode
            st.session_state.bot = None
            st.rerun()

        st.markdown("---")

        # Strategy toggles
        st.markdown("### STRATEGIES")

        mr_enabled = st.toggle(
            "MEAN REVERSION",
            value=st.session_state.config.mean_reversion_enabled,
        )
        if mr_enabled != st.session_state.config.mean_reversion_enabled:
            st.session_state.config.mean_reversion_enabled = mr_enabled
            st.session_state.bot = None

        th_enabled = st.toggle(
            "SHORT THURSDAY",
            value=st.session_state.config.short_thursday_enabled,
        )
        if th_enabled != st.session_state.config.short_thursday_enabled:
            st.session_state.config.short_thursday_enabled = th_enabled
            st.session_state.bot = None

        cd_enabled = st.toggle(
            "CRASH DAY",
            value=st.session_state.config.crash_day_enabled,
        )
        if cd_enabled != st.session_state.config.crash_day_enabled:
            st.session_state.config.crash_day_enabled = cd_enabled
            st.session_state.bot = None

        st.markdown("---")

        # Bot control
        bot = get_or_create_bot()

        if st.session_state.scheduler is None:
            st.session_state.scheduler = SmartScheduler(bot)

        scheduler = st.session_state.scheduler

        if scheduler.status == BotStatus.RUNNING:
            if st.button("STOP BOT", type="secondary", use_container_width=True):
                scheduler.stop()
                st.rerun()
        else:
            if st.button("START BOT", type="primary", use_container_width=True):
                scheduler.start()
                st.rerun()


def main():
    """Main app - minimal monitor dashboard."""
    init_session_state()

    # Get bot and data
    bot = get_or_create_bot()

    if st.session_state.scheduler is None:
        st.session_state.scheduler = SmartScheduler(bot)

    scheduler = st.session_state.scheduler

    # Get portfolio data
    portfolio = bot.get_portfolio_value()

    # Auto-refresh every 45 seconds
    st.session_state.last_refresh = datetime.now()

    # Settings are now in the gear button popover (in status bar)
    # Sidebar is kept as backup but not rendered by default

    # Main content area
    st.markdown(
        """
    <div style="max-width: 700px; margin: 0 auto; padding: 20px;">
    """,
        unsafe_allow_html=True,
    )

    # Position card (biggest)
    render_position_card(portfolio)

    # Cash card
    render_cash_card(portfolio)

    # Total card
    render_total_card(portfolio)

    # Status bar
    render_status_bar(bot, scheduler)

    # Refresh indicator
    render_refresh_indicator()

    st.markdown("</div>", unsafe_allow_html=True)

    # Auto-refresh using streamlit's native rerun with fragment
    # Note: Using st.empty() with time.sleep would block, so we use
    # streamlit's auto-rerun mechanism
    import time

    time.sleep(45)
    st.rerun()


if __name__ == "__main__":
    main()
