"""
Utility functions for IBIT Dip Bot.
Handles timezone conversions, calculations, and common helpers.
"""

import datetime
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

# Eastern timezone for US markets
ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def get_et_now() -> datetime.datetime:
    """Get current time in Eastern timezone."""
    return datetime.datetime.now(ET)


def get_market_times(date: Optional[datetime.date] = None) -> dict:
    """
    Get key market times for a given date.

    Returns dict with:
        - market_open: 9:30 AM ET
        - dip_window_start: 10:00 AM ET
        - dip_window_end: 10:59 AM ET
        - friday_close: 3:55 PM ET (for Friday sells)
        - market_close: 4:00 PM ET
    """
    if date is None:
        date = get_et_now().date()

    return {
        "market_open": datetime.datetime(date.year, date.month, date.day, 9, 30, tzinfo=ET),
        "dip_window_start": datetime.datetime(date.year, date.month, date.day, 10, 0, tzinfo=ET),
        "dip_window_end": datetime.datetime(date.year, date.month, date.day, 10, 59, tzinfo=ET),
        "friday_close": datetime.datetime(date.year, date.month, date.day, 15, 55, tzinfo=ET),
        "market_close": datetime.datetime(date.year, date.month, date.day, 16, 0, tzinfo=ET),
    }


def is_market_day(date: Optional[datetime.date] = None) -> bool:
    """
    Check if date is a weekday (potential market day).
    Note: Does not check for market holidays.
    """
    if date is None:
        date = get_et_now().date()
    return date.weekday() < 5  # Mon=0, Fri=4


def is_market_open() -> bool:
    """Check if market is currently open (9:30 AM - 4:00 PM ET on weekdays)."""
    now = get_et_now()
    if not is_market_day(now.date()):
        return False

    times = get_market_times(now.date())
    return times["market_open"] <= now <= times["market_close"]


def is_in_dip_window() -> bool:
    """Check if current time is within the dip buying window (10:00-10:59 AM ET)."""
    now = get_et_now()
    if not is_market_day(now.date()):
        return False

    times = get_market_times(now.date())
    return times["dip_window_start"] <= now <= times["dip_window_end"]


def get_day_of_week(date: Optional[datetime.date] = None) -> str:
    """Get day of week name."""
    if date is None:
        date = get_et_now().date()
    return date.strftime("%A")


def is_monday(date: Optional[datetime.date] = None) -> bool:
    """Check if date is Monday."""
    if date is None:
        date = get_et_now().date()
    return date.weekday() == 0


def is_friday(date: Optional[datetime.date] = None) -> bool:
    """Check if date is Friday."""
    if date is None:
        date = get_et_now().date()
    return date.weekday() == 4


def calculate_dip_percentage(open_price: float, current_price: float) -> float:
    """
    Calculate dip percentage from open.
    Positive value means price is below open (a dip).

    Formula: (open_price - current_price) / open_price * 100
    """
    if open_price <= 0:
        return 0.0
    return ((open_price - current_price) / open_price) * 100


def calculate_shares(available_cash: float, price: float, max_position: Optional[float] = None) -> int:
    """
    Calculate number of whole shares to buy.

    Args:
        available_cash: Cash available for trading
        price: Current share price
        max_position: Maximum position size in dollars (optional)

    Returns:
        Number of whole shares (no fractional shares)
    """
    if price <= 0 or available_cash <= 0:
        return 0

    cash_to_use = available_cash
    if max_position is not None and max_position > 0:
        cash_to_use = min(available_cash, max_position)

    return int(cash_to_use // price)


def calculate_pnl(entry_price: float, exit_price: float, shares: int) -> Tuple[float, float]:
    """
    Calculate profit/loss for a trade.

    Returns:
        Tuple of (dollar_pnl, percentage_pnl)
    """
    if entry_price <= 0 or shares <= 0:
        return 0.0, 0.0

    dollar_pnl = (exit_price - entry_price) * shares
    percentage_pnl = ((exit_price - entry_price) / entry_price) * 100

    return dollar_pnl, percentage_pnl


def format_currency(amount: float) -> str:
    """Format amount as currency string."""
    return f"${amount:,.2f}"


def format_percentage(pct: float, decimals: int = 2) -> str:
    """Format percentage with sign."""
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.{decimals}f}%"


def time_until(target: datetime.datetime) -> datetime.timedelta:
    """Calculate time remaining until target datetime."""
    now = get_et_now()
    return target - now


def format_timedelta(td: datetime.timedelta) -> str:
    """Format timedelta as human-readable string."""
    total_seconds = int(td.total_seconds())

    if total_seconds < 0:
        return "Now"

    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours > 0:
        return f"{hours}h {minutes}m {seconds}s"
    elif minutes > 0:
        return f"{minutes}m {seconds}s"
    else:
        return f"{seconds}s"


# US Market Holidays (2024-2026) - Update as needed
MARKET_HOLIDAYS = {
    # 2024
    datetime.date(2024, 1, 1),   # New Year's Day
    datetime.date(2024, 1, 15),  # MLK Day
    datetime.date(2024, 2, 19),  # Presidents Day
    datetime.date(2024, 3, 29),  # Good Friday
    datetime.date(2024, 5, 27),  # Memorial Day
    datetime.date(2024, 6, 19),  # Juneteenth
    datetime.date(2024, 7, 4),   # Independence Day
    datetime.date(2024, 9, 2),   # Labor Day
    datetime.date(2024, 11, 28), # Thanksgiving
    datetime.date(2024, 12, 25), # Christmas
    # 2025
    datetime.date(2025, 1, 1),   # New Year's Day
    datetime.date(2025, 1, 20),  # MLK Day
    datetime.date(2025, 2, 17),  # Presidents Day
    datetime.date(2025, 4, 18),  # Good Friday
    datetime.date(2025, 5, 26),  # Memorial Day
    datetime.date(2025, 6, 19),  # Juneteenth
    datetime.date(2025, 7, 4),   # Independence Day
    datetime.date(2025, 9, 1),   # Labor Day
    datetime.date(2025, 11, 27), # Thanksgiving
    datetime.date(2025, 12, 25), # Christmas
    # 2026
    datetime.date(2026, 1, 1),   # New Year's Day
    datetime.date(2026, 1, 19),  # MLK Day
    datetime.date(2026, 2, 16),  # Presidents Day
    datetime.date(2026, 4, 3),   # Good Friday
    datetime.date(2026, 5, 25),  # Memorial Day
    datetime.date(2026, 6, 19),  # Juneteenth
    datetime.date(2026, 7, 3),   # Independence Day (observed)
    datetime.date(2026, 9, 7),   # Labor Day
    datetime.date(2026, 11, 26), # Thanksgiving
    datetime.date(2026, 12, 25), # Christmas
}


def is_market_holiday(date: Optional[datetime.date] = None) -> bool:
    """Check if date is a market holiday."""
    if date is None:
        date = get_et_now().date()
    return date in MARKET_HOLIDAYS


def is_trading_day(date: Optional[datetime.date] = None) -> bool:
    """Check if date is a valid trading day (weekday and not holiday)."""
    if date is None:
        date = get_et_now().date()
    return is_market_day(date) and not is_market_holiday(date)


def get_next_trading_day(from_date: Optional[datetime.date] = None) -> datetime.date:
    """Get the next trading day from a given date."""
    if from_date is None:
        from_date = get_et_now().date()

    next_day = from_date + datetime.timedelta(days=1)
    while not is_trading_day(next_day):
        next_day += datetime.timedelta(days=1)

    return next_day
