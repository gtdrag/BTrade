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

import json
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Settings file for persistence across refreshes
SETTINGS_FILE = Path(__file__).parent / ".user_settings.json"


def load_user_settings() -> dict:
    """Load user settings from file."""
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_user_settings(settings: dict):
    """Save user settings to file."""
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f)
    except Exception:
        pass


def persist_current_settings():
    """Save current session state settings to file."""
    settings = {
        "trading_mode": st.session_state.trading_mode,
        "mean_reversion_enabled": st.session_state.config.mean_reversion_enabled,
        "short_thursday_enabled": st.session_state.config.short_thursday_enabled,
        "crash_day_enabled": st.session_state.config.crash_day_enabled,
        "btc_overnight_filter_enabled": st.session_state.config.btc_overnight_filter_enabled,
        "position_pct": st.session_state.get("position_pct", 75),
    }
    save_user_settings(settings)


# Load environment variables before local imports
load_dotenv(Path(__file__).parent / ".env")

import streamlit as st  # noqa: E402

from src.etrade_client import ETradeClient  # noqa: E402
from src.notifications import NotificationConfig  # noqa: E402
from src.smart_scheduler import BotStatus, SmartScheduler  # noqa: E402
from src.smart_strategy import StrategyConfig  # noqa: E402
from src.trading_bot import create_trading_bot  # noqa: E402

# Page config - must be first Streamlit command
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

    /* Hide sidebar completely */
    [data-testid="stSidebar"] {
        display: none !important;
    }

    /* Dialog styling - pop-art theme */
    [data-testid="stModal"] > div {
        background: #FFFFFF !important;
        border: 5px solid #000000 !important;
        border-radius: 12px !important;
    }

    /* Dialog backdrop - blur effect */
    [data-testid="stModal"]::before {
        backdrop-filter: blur(4px) !important;
        -webkit-backdrop-filter: blur(4px) !important;
    }

    /* Dialog header styling */
    [data-testid="stModal"] h3 {
        color: #000 !important;
        font-size: 1rem !important;
        text-transform: uppercase !important;
        letter-spacing: 1px !important;
        margin-top: 16px !important;
    }

    /* Toggle styling in dialog */
    [data-testid="stModal"] .stToggle label {
        font-weight: 600 !important;
    }

    /* Radio styling in dialog */
    [data-testid="stModal"] .stRadio > div {
        background: #F5F5F5;
        border: 3px solid #000;
        border-radius: 8px;
        padding: 8px;
    }

    /* Divider in dialog */
    [data-testid="stModal"] hr {
        border-color: #000 !important;
        border-width: 2px !important;
    }
</style>
""",
    unsafe_allow_html=True,
)


# Session state initialization
def init_session_state():
    """Initialize session state variables."""
    # Load persisted settings
    saved = load_user_settings()

    if "config" not in st.session_state:
        st.session_state.config = StrategyConfig()
        # Apply saved strategy settings
        if "mean_reversion_enabled" in saved:
            st.session_state.config.mean_reversion_enabled = saved["mean_reversion_enabled"]
        if "short_thursday_enabled" in saved:
            st.session_state.config.short_thursday_enabled = saved["short_thursday_enabled"]
        if "crash_day_enabled" in saved:
            st.session_state.config.crash_day_enabled = saved["crash_day_enabled"]
        if "btc_overnight_filter_enabled" in saved:
            st.session_state.config.btc_overnight_filter_enabled = saved[
                "btc_overnight_filter_enabled"
            ]

    if "trading_mode" not in st.session_state:
        # Use saved mode, or fall back to env var, or default to paper
        st.session_state.trading_mode = saved.get(
            "trading_mode", os.environ.get("TRADING_MODE", "paper")
        )

    if "position_pct" not in st.session_state:
        # Use saved value, or fall back to env var, or default to 75%
        st.session_state.position_pct = saved.get(
            "position_pct", int(os.environ.get("MAX_POSITION_PCT", "75"))
        )

    if "bot" not in st.session_state:
        st.session_state.bot = None
    if "scheduler" not in st.session_state:
        st.session_state.scheduler = None
    if "last_refresh" not in st.session_state:
        st.session_state.last_refresh = datetime.now()
    if "dialog_open" not in st.session_state:
        st.session_state.dialog_open = False


def get_or_create_bot():
    """Get or create the trading bot."""
    if st.session_state.bot is None:
        # Create E*TRADE client for live mode
        etrade_client = None
        account_id = ""

        if st.session_state.trading_mode == "live":
            consumer_key = os.environ.get("ETRADE_CONSUMER_KEY")
            consumer_secret = os.environ.get("ETRADE_CONSUMER_SECRET")
            account_id = os.environ.get("ETRADE_ACCOUNT_ID", "")

            if consumer_key and consumer_secret:
                etrade_client = ETradeClient(consumer_key, consumer_secret)
                # Try to authenticate (will use saved tokens if available)
                if not etrade_client.is_authenticated():
                    # Store client for re-auth flow
                    st.session_state.etrade_client = etrade_client
                    st.session_state.needs_reauth = True
                    etrade_client = None
                else:
                    st.session_state.needs_reauth = False

        # Read position limits - use session state (UI control) for percentage
        max_position_pct = float(st.session_state.position_pct)
        max_position_usd_str = os.environ.get("MAX_POSITION_USD", "")
        max_position_usd = float(max_position_usd_str) if max_position_usd_str else None

        # Read email notification settings from environment
        email_to_str = os.environ.get("EMAIL_TO", "")
        notification_config = NotificationConfig(
            email_enabled=os.environ.get("EMAIL_ENABLED", "").lower() == "true",
            smtp_server=os.environ.get("SMTP_SERVER", "smtp.gmail.com"),
            smtp_port=int(os.environ.get("SMTP_PORT", "587")),
            smtp_username=os.environ.get("SMTP_USERNAME", ""),
            smtp_password=os.environ.get("SMTP_PASSWORD", ""),
            email_to=[e.strip() for e in email_to_str.split(",") if e.strip()],
            desktop_enabled=os.environ.get("DESKTOP_ENABLED", "").lower() == "true",
        )

        st.session_state.bot = create_trading_bot(
            mode=st.session_state.trading_mode,
            etrade_client=etrade_client,
            account_id_key=account_id,
            mean_reversion_threshold=st.session_state.config.mean_reversion_threshold,
            mean_reversion_enabled=st.session_state.config.mean_reversion_enabled,
            short_thursday_enabled=st.session_state.config.short_thursday_enabled,
            crash_day_enabled=st.session_state.config.crash_day_enabled,
            crash_day_threshold=st.session_state.config.crash_day_threshold,
            max_position_pct=max_position_pct,
            max_position_usd=max_position_usd,
            notification_config=notification_config,
        )
    return st.session_state.bot


def get_cached_status(bot):
    """Get bot status from session cache or fetch if stale (>30s)."""
    now = datetime.now()
    cache_key = "cached_status"
    cache_time_key = "cached_status_time"

    # Check if we have a recent cache
    if cache_key in st.session_state and cache_time_key in st.session_state:
        age = (now - st.session_state[cache_time_key]).total_seconds()
        if age < 30:
            return st.session_state[cache_key]

    # Fetch fresh data
    status = bot.get_status()
    st.session_state[cache_key] = status
    st.session_state[cache_time_key] = now
    return status


def get_cached_portfolio(bot):
    """Get portfolio from session cache or fetch if stale (>30s)."""
    now = datetime.now()
    cache_key = "cached_portfolio"
    cache_time_key = "cached_portfolio_time"

    # Check if we have a recent cache
    if cache_key in st.session_state and cache_time_key in st.session_state:
        age = (now - st.session_state[cache_time_key]).total_seconds()
        if age < 30:
            return st.session_state[cache_key]

    # Fetch fresh data
    portfolio = bot.get_portfolio_value()
    st.session_state[cache_key] = portfolio
    st.session_state[cache_time_key] = now
    return portfolio


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


def is_market_open() -> bool:
    """Check if US stock market is currently open."""
    from src.utils import get_et_now

    now = get_et_now()
    # Market is closed on weekends
    if now.weekday() >= 5:
        return False
    # Market hours: 9:30 AM - 4:00 PM ET
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close


def render_header_bar(bot, is_live: bool, cached_status: dict):
    """Render compact header bar with mode, connection, market status, and signal."""
    # Check connection status
    is_connected = False
    if is_live and bot.client:
        is_connected = bot.client.is_authenticated()

    # Check market status
    market_open = is_market_open()

    # Mode indicator
    mode_color = "#DC2626" if is_live else "#1E3A8A"
    mode_text = "LIVE" if is_live else "PAPER"

    # Connection indicator
    conn_color = "#16A34A" if is_connected else "#9CA3AF"
    conn_icon = "✓" if is_connected else "✗"

    # Market indicator
    market_color = "#16A34A" if market_open else "#9CA3AF"
    market_icon = "●" if market_open else "○"
    market_text = "OPEN" if market_open else "CLOSED"

    # Signal
    signal = cached_status.get("today_signal", "CASH").upper()
    etf = cached_status.get("signal_etf", "")
    if signal == "LONG":
        signal_color = "#16A34A"
        signal_text = f"BUY {etf}" if etf else "LONG"
    elif signal == "SHORT":
        signal_color = "#DC2626"
        signal_text = f"BUY {etf}" if etf else "SHORT"
    else:
        signal_color = "#6B7280"
        signal_text = "CASH"

    st.markdown(
        f"""
        <div style="display: flex; align-items: center; justify-content: space-between; background: white; border: 4px solid #000; border-radius: 8px; padding: 10px 16px; margin-bottom: 12px;">
            <div style="display: flex; align-items: center; gap: 16px;">
                <span style="background: {mode_color}; color: white; padding: 4px 10px; border-radius: 4px; font-weight: 700; font-size: 0.75rem;">● {mode_text}</span>
                <span style="color: {conn_color}; font-weight: 600; font-size: 0.75rem;">{conn_icon} CONN</span>
                <span style="color: {market_color}; font-weight: 600; font-size: 0.75rem;">{market_icon} {market_text}</span>
            </div>
            <div style="background: {signal_color}; color: white; padding: 6px 14px; border-radius: 4px; font-weight: 800; font-size: 0.875rem;">
                {signal_text}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_position_card(portfolio: dict):
    """Render position grid showing all ETFs + cash."""
    positions = portfolio.get("positions", [])
    cash = portfolio.get("cash", 0)

    # Build lookup dict for positions
    pos_lookup = {p["symbol"]: p for p in positions}

    # ETF definitions: (symbol, label, type_color)
    etfs = [
        ("BITU", "2X LONG", "#1E3A8A"),
        ("SBIT", "2X INV", "#DC2626"),
        ("IBIT", "1X", "#6B7280"),
    ]

    # Build HTML for each ETF card
    etf_cards = []
    for symbol, label, color in etfs:
        pos = pos_lookup.get(symbol)
        if pos and pos.get("shares", 0) > 0:
            value = pos["current_value"]
            pnl = pos["unrealized_pnl"]
            pnl_pct = pos["unrealized_pnl_pct"]
            shares = pos["shares"]
            pnl_color = "#16A34A" if pnl >= 0 else "#DC2626"
            pnl_sign = "+" if pnl >= 0 else ""
            card = f'<div style="flex:1;background:white;border:4px solid #000;border-radius:8px;padding:12px;text-align:center;"><div style="font-size:1.25rem;font-weight:800;color:#000;">{symbol}</div><div style="font-size:0.65rem;color:{color};font-weight:600;margin-bottom:6px;">{label}</div><div style="font-size:1.1rem;font-weight:700;color:#000;">${value:,.0f}</div><div style="font-size:0.75rem;color:{pnl_color};font-weight:600;">{pnl_sign}{pnl_pct:.1f}%</div><div style="font-size:0.65rem;opacity:0.5;margin-top:2px;">{shares} shares</div></div>'
        else:
            card = f'<div style="flex:1;background:white;border:4px solid #000;border-radius:8px;padding:12px;text-align:center;opacity:0.5;"><div style="font-size:1.25rem;font-weight:800;color:#000;">{symbol}</div><div style="font-size:0.65rem;color:{color};font-weight:600;margin-bottom:6px;">{label}</div><div style="font-size:1.1rem;font-weight:700;color:#9CA3AF;">$0</div><div style="font-size:0.75rem;color:#9CA3AF;">—</div><div style="font-size:0.65rem;opacity:0.5;margin-top:2px;">0 shares</div></div>'
        etf_cards.append(card)

    # Cash card
    cash_card = f'<div style="flex:1;background:white;border:4px solid #000;border-radius:8px;padding:12px;text-align:center;"><div style="font-size:1.25rem;font-weight:800;color:#000;">CASH</div><div style="font-size:0.65rem;color:#16A34A;font-weight:600;margin-bottom:6px;">AVAILABLE</div><div style="font-size:1.1rem;font-weight:700;color:#000;">${cash:,.0f}</div><div style="font-size:0.75rem;color:#16A34A;">●</div><div style="font-size:0.65rem;opacity:0.5;margin-top:2px;">ready</div></div>'

    html = f'<div style="display:flex;gap:10px;margin-bottom:12px;">{etf_cards[0]}{etf_cards[1]}{etf_cards[2]}{cash_card}</div>'
    st.markdown(html, unsafe_allow_html=True)


def render_metrics_row(portfolio: dict):
    """Render today's P&L and total portfolio value in a 2-column row."""
    days_gain = portfolio.get("days_gain", 0)
    days_gain_pct = portfolio.get("days_gain_pct", 0)
    total_value = portfolio.get("total_value", 0)
    total_pnl = portfolio.get("total_pnl", 0)
    total_pnl_pct = portfolio.get("total_pnl_pct", 0)

    days_class = get_pnl_class(days_gain)
    total_class = get_pnl_class(total_pnl)

    st.markdown(
        f"""
        <div style="display: flex; gap: 12px; margin-bottom: 12px;">
            <div class="card" style="flex: 1; padding: 12px 16px;">
                <div class="label" style="font-size: 0.75rem; opacity: 0.6;">TODAY'S P&L</div>
                <div class="{days_class}" style="font-size: 1.5rem; font-weight: 700; margin-top: 2px;">{format_pnl(days_gain)}</div>
                <div class="{days_class}" style="font-size: 0.875rem;">{format_percent(days_gain_pct)}</div>
            </div>
            <div class="card" style="flex: 1; padding: 12px 16px;">
                <div class="label" style="font-size: 0.75rem; opacity: 0.6;">TOTAL PORTFOLIO</div>
                <div style="font-size: 1.5rem; font-weight: 700; color: #000; margin-top: 2px;">{format_currency(total_value)}</div>
                <div class="{total_class}" style="font-size: 0.875rem;">{format_pnl(total_pnl)} ({format_percent(total_pnl_pct)})</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_trade_log(db):
    """Render compact trade log as a single line."""
    from src.database import get_database

    if db is None:
        db = get_database()

    # Get recent trade logs
    logs = db.get_logs(limit=5, level="TRADE")

    if not logs:
        trades_html = '<span style="opacity: 0.5;">No trades yet</span>'
    else:
        # Build compact trade items
        trade_items = []
        for log in logs[:5]:
            details = log.get("details", {})
            if isinstance(details, str):
                import json

                try:
                    details = json.loads(details)
                except Exception:
                    details = {}

            timestamp = log.get("timestamp", "")[5:10]  # Just MM-DD
            etf = details.get("etf", "?")

            # Determine color based on ETF
            if etf == "BITX":
                color = "#16A34A"
            elif etf == "SBIT":
                color = "#DC2626"
            else:
                color = "#6B7280"

            trade_items.append(
                f'<span style="color: {color}; font-weight: 600;">{timestamp} {etf}</span>'
            )

        trades_html = " · ".join(trade_items)

    st.markdown(
        f"""
        <div style="background: white; border: 3px solid #000; border-radius: 6px; padding: 8px 14px; display: flex; align-items: center; gap: 12px;">
            <span style="font-size: 0.7rem; font-weight: 700; color: #1E3A8A; text-transform: uppercase;">Trades</span>
            <span style="font-size: 0.75rem; overflow-x: auto; white-space: nowrap;">{trades_html}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_status_bar(bot, scheduler, cached_status):
    """Render the status bar with inline gear button."""
    # Get bot status
    is_running = scheduler and scheduler.status == BotStatus.RUNNING
    status_text = "RUNNING" if is_running else "STOPPED"
    status_color = "#16A34A" if is_running else "#DC2626"

    # Use cached status to avoid slow yfinance calls
    status = cached_status
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

    # Status bar with gear button - using columns for alignment
    status_col, info_col, gear_col = st.columns([2, 5, 1])

    with status_col:
        st.markdown(
            f"""
            <div style="display: flex; align-items: center; gap: 10px; padding: 14px 18px; background: white; border: 5px solid black; border-radius: 8px; height: 56px;">
                <div style="width: 16px; height: 16px; border-radius: 50%; background-color: {status_color}; border: 3px solid #000; flex-shrink: 0;"></div>
                <span style="font-weight: 700; color: #000; font-size: 1rem; white-space: nowrap;">{status_text}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with info_col:
        st.markdown(
            f"""
            <div style="display: flex; align-items: center; justify-content: center; gap: 24px; padding: 14px 18px; background: white; border: 5px solid black; border-radius: 8px; height: 56px;">
                <span style="color: #000; opacity: 0.6; font-size: 0.9rem;">LAST: {last_action}</span>
                <span style="color: #000; opacity: 0.6; font-size: 0.9rem;">NEXT: {next_action}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with gear_col:
        # Gear button sets flag to open settings dialog
        if st.button("⚙", key="gear_btn", help="Open Settings"):
            st.session_state.dialog_open = True


@st.fragment(run_every=45)
def render_refresh_indicator():
    """Render the refresh indicator with auto-refresh every 45 seconds."""
    st.session_state.last_refresh = datetime.now()
    st.markdown(
        """
    <div class="refresh-indicator">
        AUTO-REFRESH: 45s
    </div>
    """,
        unsafe_allow_html=True,
    )


@st.dialog("🔑 E*TRADE Re-Authentication Required", width="small")
def show_reauth_dialog():
    """Show re-authentication dialog when tokens expire."""
    st.markdown(
        "Your E*TRADE session has expired. Please re-authenticate to continue with LIVE trading."
    )

    # Get or create request token
    if "oauth_request_token" not in st.session_state:
        client = st.session_state.get("etrade_client")
        if client:
            try:
                auth_url, request_token = client.get_authorization_url()
                st.session_state.oauth_request_token = request_token
                st.session_state.oauth_auth_url = auth_url
            except Exception as e:
                st.error(f"Failed to start OAuth: {e}")
                if st.button("Cancel", use_container_width=True):
                    st.session_state.needs_reauth = False
                    st.session_state.trading_mode = "paper"
                    st.session_state.bot = None
                    st.rerun()
                return

    auth_url = st.session_state.get("oauth_auth_url", "")

    st.markdown("**Step 1:** Click the link below to authorize:")
    st.markdown(f"[Open E*TRADE Authorization]({auth_url})")

    st.markdown("**Step 2:** Enter the 5-character code:")
    verifier = st.text_input("Verifier Code", max_chars=5, placeholder="e.g., AB1CD")

    col1, col2 = st.columns(2)
    with col1:
        if st.button(
            "✓ SUBMIT", type="primary", use_container_width=True, disabled=len(verifier) != 5
        ):
            client = st.session_state.get("etrade_client")
            request_token = st.session_state.get("oauth_request_token")
            if client and request_token:
                try:
                    client.complete_authorization(verifier.strip().upper(), request_token)
                    st.session_state.needs_reauth = False
                    st.session_state.bot = None  # Force bot recreation with new auth
                    # Clean up OAuth state
                    if "oauth_request_token" in st.session_state:
                        del st.session_state["oauth_request_token"]
                    if "oauth_auth_url" in st.session_state:
                        del st.session_state["oauth_auth_url"]
                    st.success("Authentication successful!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Authentication failed: {e}")

    with col2:
        if st.button("Switch to PAPER", use_container_width=True):
            st.session_state.needs_reauth = False
            st.session_state.trading_mode = "paper"
            st.session_state.bot = None
            # Clean up OAuth state
            if "oauth_request_token" in st.session_state:
                del st.session_state["oauth_request_token"]
            if "oauth_auth_url" in st.session_state:
                del st.session_state["oauth_auth_url"]
            st.rerun()


@st.dialog("⚙️ Settings", width="small")
def show_settings_dialog(scheduler):
    """Settings dialog with strategy toggles and bot control."""

    # Show auth flow if needed (inside the dialog)
    if st.session_state.get("show_auth_in_settings"):
        st.markdown("### 🔑 E*TRADE Authentication Required")
        st.markdown("You need to authenticate with E*TRADE to use LIVE mode.")

        client = st.session_state.get("etrade_client")

        # Get auth URL if we don't have one
        if "settings_auth_url" not in st.session_state and client:
            try:
                auth_url, request_token = client.get_authorization_url()
                st.session_state.settings_auth_url = auth_url
                st.session_state.settings_auth_token = request_token
            except Exception as e:
                st.error(f"Failed to get auth URL: {e}")

        if st.session_state.get("settings_auth_url"):
            st.markdown(
                f"**Step 1:** [Click here to authorize with E*TRADE]({st.session_state.settings_auth_url})"
            )
            st.markdown("**Step 2:** Enter the code below:")
            verifier = st.text_input("Verification code", max_chars=5, placeholder="e.g., AB1CD")

            col1, col2 = st.columns(2)
            with col1:
                if st.button(
                    "✓ CONNECT",
                    type="primary",
                    use_container_width=True,
                    disabled=len(verifier) != 5,
                ):
                    try:
                        client.complete_authorization(
                            verifier.strip().upper(), st.session_state.settings_auth_token
                        )
                        # Success - apply the pending mode and start
                        st.session_state.trading_mode = st.session_state.get("pending_mode", "live")
                        st.session_state.bot = None
                        st.session_state.scheduler = None
                        st.session_state.start_scheduler = True
                        # Clean up
                        for key in [
                            "show_auth_in_settings",
                            "settings_auth_url",
                            "settings_auth_token",
                            "pending_mode",
                        ]:
                            if key in st.session_state:
                                del st.session_state[key]
                        st.session_state.dialog_open = False
                        persist_current_settings()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Authentication failed: {e}")

            with col2:
                if st.button("Cancel", use_container_width=True):
                    # Cancel - clean up and stay in settings
                    for key in [
                        "show_auth_in_settings",
                        "settings_auth_url",
                        "settings_auth_token",
                        "pending_mode",
                    ]:
                        if key in st.session_state:
                            del st.session_state[key]
                    st.rerun()

        return  # Don't show rest of settings while in auth flow

    # Mode toggle
    st.markdown("### MODE")
    mode = st.radio(
        "Trading Mode",
        ["PAPER", "LIVE"],
        index=0 if st.session_state.trading_mode == "paper" else 1,
        horizontal=True,
        label_visibility="collapsed",
        key="settings_mode",
    )

    # Show E*TRADE connection status when in LIVE mode
    if mode == "LIVE":
        consumer_key = os.environ.get("ETRADE_CONSUMER_KEY")
        consumer_secret = os.environ.get("ETRADE_CONSUMER_SECRET")
        if consumer_key and consumer_secret:
            check_client = ETradeClient(consumer_key, consumer_secret)
            is_connected = check_client.is_authenticated()

            if is_connected:
                st.success("✓ E*TRADE Connected")
            else:
                st.error("⚠️ E*TRADE Disconnected")
                if st.button(
                    "🔑 RECONNECT E*TRADE",
                    type="primary",
                    use_container_width=True,
                    key="settings_reconnect",
                ):
                    st.session_state.etrade_client = check_client
                    st.session_state.show_auth_in_settings = True
                    st.rerun()
        else:
            st.warning("E*TRADE credentials not configured in .env")

    st.divider()

    # Strategy toggles - use keys to persist state across reruns
    st.markdown("### STRATEGIES")

    mr_enabled = st.toggle(
        "MEAN REVERSION",
        value=st.session_state.config.mean_reversion_enabled,
        help="Buy BITX after IBIT drops 2%+",
        key="settings_mr",
    )

    btc_filter = st.toggle(
        "↳ BTC OVERNIGHT FILTER",
        value=st.session_state.config.btc_overnight_filter_enabled,
        help="Only trade when BTC is up overnight (84% vs 17% win rate)",
        disabled=not mr_enabled,
        key="settings_btc",
    )

    th_enabled = st.toggle(
        "SHORT THURSDAY",
        value=st.session_state.config.short_thursday_enabled,
        help="Buy SBIT every Thursday",
        key="settings_th",
    )

    cd_enabled = st.toggle(
        "CRASH DAY",
        value=st.session_state.config.crash_day_enabled,
        help="Buy SBIT on 2%+ intraday drops",
        key="settings_cd",
    )

    st.divider()

    # Position sizing
    st.markdown("### POSITION SIZE")
    position_pct = st.slider(
        "% of available cash per trade",
        min_value=10,
        max_value=100,
        value=st.session_state.position_pct,
        step=5,
        help="How much of your available cash to use for each trade",
        key="settings_position_pct",
    )
    st.caption(f"Currently: **{position_pct}%** of available cash")

    st.divider()

    # Bot control
    st.markdown("### BOT CONTROL")
    col1, col2 = st.columns(2)
    with col1:
        if scheduler.status == BotStatus.RUNNING:
            if st.button("⏹️ STOP", type="secondary", use_container_width=True, key="settings_stop"):
                scheduler.stop()
                st.session_state.dialog_open = False
                st.rerun()
        else:
            if st.button("▶️ START", type="primary", use_container_width=True, key="settings_start"):
                # Check if switching to LIVE mode needs auth first
                new_mode = mode.lower()
                needs_auth = False

                if new_mode == "live":
                    consumer_key = os.environ.get("ETRADE_CONSUMER_KEY")
                    consumer_secret = os.environ.get("ETRADE_CONSUMER_SECRET")
                    if consumer_key and consumer_secret:
                        test_client = ETradeClient(consumer_key, consumer_secret)
                        if not test_client.is_authenticated():
                            needs_auth = True
                            st.session_state.pending_mode = new_mode
                            st.session_state.etrade_client = test_client
                            st.session_state.show_auth_in_settings = True
                            st.rerun()

                if not needs_auth:
                    # Apply settings and start
                    if new_mode != st.session_state.trading_mode:
                        st.session_state.trading_mode = new_mode
                        st.session_state.bot = None
                        st.session_state.scheduler = None
                        for key in [
                            "cached_portfolio",
                            "cached_portfolio_time",
                            "cached_status",
                            "cached_status_time",
                        ]:
                            if key in st.session_state:
                                del st.session_state[key]
                    if mr_enabled != st.session_state.config.mean_reversion_enabled:
                        st.session_state.config.mean_reversion_enabled = mr_enabled
                        st.session_state.bot = None
                    if btc_filter != st.session_state.config.btc_overnight_filter_enabled:
                        st.session_state.config.btc_overnight_filter_enabled = btc_filter
                        st.session_state.bot = None
                    if th_enabled != st.session_state.config.short_thursday_enabled:
                        st.session_state.config.short_thursday_enabled = th_enabled
                        st.session_state.bot = None
                    if cd_enabled != st.session_state.config.crash_day_enabled:
                        st.session_state.config.crash_day_enabled = cd_enabled
                        st.session_state.bot = None
                    if position_pct != st.session_state.position_pct:
                        st.session_state.position_pct = position_pct
                        st.session_state.bot = None
                    st.session_state.start_scheduler = True
                    st.session_state.dialog_open = False
                    persist_current_settings()
                    st.rerun()

    with col2:
        if st.button("CLOSE", use_container_width=True, key="settings_close"):
            # Close dialog and apply settings
            st.session_state.dialog_open = False
            # Apply all settings on close
            new_mode = mode.lower()
            if new_mode != st.session_state.trading_mode:
                st.session_state.trading_mode = new_mode
                st.session_state.bot = None
                st.session_state.scheduler = None
                # Clear cached data so live data is fetched fresh
                if "cached_portfolio" in st.session_state:
                    del st.session_state["cached_portfolio"]
                if "cached_portfolio_time" in st.session_state:
                    del st.session_state["cached_portfolio_time"]
                if "cached_status" in st.session_state:
                    del st.session_state["cached_status"]
                if "cached_status_time" in st.session_state:
                    del st.session_state["cached_status_time"]
            if mr_enabled != st.session_state.config.mean_reversion_enabled:
                st.session_state.config.mean_reversion_enabled = mr_enabled
                st.session_state.bot = None
            if btc_filter != st.session_state.config.btc_overnight_filter_enabled:
                st.session_state.config.btc_overnight_filter_enabled = btc_filter
                st.session_state.bot = None
            if th_enabled != st.session_state.config.short_thursday_enabled:
                st.session_state.config.short_thursday_enabled = th_enabled
                st.session_state.bot = None
            if cd_enabled != st.session_state.config.crash_day_enabled:
                st.session_state.config.crash_day_enabled = cd_enabled
                st.session_state.bot = None
            if position_pct != st.session_state.position_pct:
                st.session_state.position_pct = position_pct
                st.session_state.bot = None
            # Persist settings to survive refresh
            persist_current_settings()
            st.rerun()


def check_system_health() -> list:
    """Check for system issues and return list of warnings/errors."""
    issues = []

    # Check Alpaca credentials
    alpaca_key = os.environ.get("ALPACA_API_KEY", "")
    alpaca_secret = os.environ.get("ALPACA_SECRET_KEY", "")
    if not alpaca_key or not alpaca_secret:
        issues.append(("warning", "Alpaca API not configured - BTC overnight filter disabled"))
    else:
        # Test Alpaca connection
        try:
            from alpaca.data.historical import CryptoHistoricalDataClient

            CryptoHistoricalDataClient(alpaca_key, alpaca_secret)
        except Exception as e:
            issues.append(("error", f"Alpaca connection failed: {e}"))

    # Check email configuration
    email_enabled = os.environ.get("EMAIL_ENABLED", "").lower() == "true"
    if email_enabled:
        smtp_user = os.environ.get("SMTP_USERNAME", "")
        smtp_pass = os.environ.get("SMTP_PASSWORD", "")
        if not smtp_user or not smtp_pass or "your" in smtp_user.lower():
            issues.append(("warning", "Email notifications enabled but not configured"))

    return issues


def main():
    """Main app - minimal monitor dashboard."""
    init_session_state()

    # Get bot and data
    bot = get_or_create_bot()

    # If LIVE mode and not authenticated, show reconnect button
    if st.session_state.trading_mode == "live":
        consumer_key = os.environ.get("ETRADE_CONSUMER_KEY")
        consumer_secret = os.environ.get("ETRADE_CONSUMER_SECRET")
        if consumer_key and consumer_secret:
            check_client = ETradeClient(consumer_key, consumer_secret)
            if not check_client.is_authenticated():
                st.error("⚠️ E*TRADE session expired")
                if st.button("🔑 RECONNECT E*TRADE", type="primary", use_container_width=True):
                    try:
                        auth_url, request_token = check_client.get_authorization_url()
                        st.session_state.oauth_url = auth_url
                        st.session_state.oauth_token = request_token
                        st.session_state.etrade_client = check_client
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed: {e}")

                # Show auth flow if we have a URL
                if st.session_state.get("oauth_url"):
                    st.markdown(f"**1.** [Click here to authorize]({st.session_state.oauth_url})")
                    verifier = st.text_input("**2.** Enter the 5-character code:", max_chars=5)
                    if len(verifier) == 5:
                        if st.button("✓ SUBMIT CODE", type="primary"):
                            try:
                                client = st.session_state.get("etrade_client")
                                token = st.session_state.get("oauth_token")
                                client.complete_authorization(verifier.upper(), token)
                                st.session_state.bot = None
                                del st.session_state["oauth_url"]
                                del st.session_state["oauth_token"]
                                st.success("Connected!")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Auth failed: {e}")
                st.stop()  # Don't render rest of page until authenticated

    if st.session_state.scheduler is None:
        st.session_state.scheduler = SmartScheduler(bot)

    scheduler = st.session_state.scheduler

    # Check if we need to start the scheduler (from dialog button)
    if st.session_state.get("start_scheduler"):
        scheduler.start()
        del st.session_state["start_scheduler"]

    # Check system health and display any issues
    if "health_checked" not in st.session_state:
        st.session_state.health_issues = check_system_health()
        st.session_state.health_checked = True

    # Display any system issues at the top
    for issue_type, message in st.session_state.get("health_issues", []):
        if issue_type == "error":
            st.error(f"🚨 {message}")
        else:
            st.warning(f"⚠️ {message}")

    # Get cached data (avoids slow yfinance calls on every click)
    portfolio = get_cached_portfolio(bot)
    cached_status = get_cached_status(bot)

    # Check for errors in portfolio data
    if portfolio.get("error"):
        st.error(f"🚨 Portfolio Error: {portfolio['error']}")

    # Auto-refresh every 45 seconds
    st.session_state.last_refresh = datetime.now()

    # Main content area - compact padding for single screen
    st.markdown(
        """
    <div style="max-width: 700px; margin: 0 auto; padding: 12px 20px;">
    """,
        unsafe_allow_html=True,
    )

    # Header bar with mode, connection, market status, AND signal (compact)
    is_live = st.session_state.trading_mode == "live"
    render_header_bar(bot, is_live, cached_status)

    # Position card (hero element)
    render_position_card(portfolio)

    # Cash + Today + Total in one row (3-column)
    render_metrics_row(portfolio)

    # Status bar with gear button
    render_status_bar(bot, scheduler, cached_status)

    # Compact trade log
    render_trade_log(bot.db)

    # Refresh indicator
    render_refresh_indicator()

    st.markdown("</div>", unsafe_allow_html=True)

    # Show settings dialog if open
    if st.session_state.dialog_open:
        show_settings_dialog(scheduler)


if __name__ == "__main__":
    main()
