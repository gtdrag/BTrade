"""
Centralized Error Alerting System.

Provides automatic Telegram alerts for critical errors and anomalies.
Designed to prevent silent failures in the trading bot.

Usage:
    from .error_alerting import alert_error, AlertSeverity

    # Critical errors (always alert)
    alert_error(AlertSeverity.CRITICAL, "API connection failed", {"api": "Alpaca"})

    # Warnings (alert but don't spam)
    alert_error(AlertSeverity.WARNING, "Backtest returned 0 trades", {"strategy": "MR"})

    # Anomalies (data quality issues)
    alert_anomaly("expected_trades", 0, ">0", {"backtest": "mean_reversion"})
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class AlertSeverity(Enum):
    """Severity levels for alerts."""

    CRITICAL = "critical"  # Always send immediately
    WARNING = "warning"  # Rate-limited (max 1 per 5 min for same category)
    INFO = "info"  # Rate-limited (max 1 per 30 min for same category)
    ANOMALY = "anomaly"  # Data quality issues (rate-limited)


@dataclass
class AlertState:
    """Tracks alert state to prevent spam."""

    last_alert_time: Dict[str, datetime] = field(default_factory=dict)
    alert_counts: Dict[str, int] = field(default_factory=dict)

    # Rate limits by severity (in seconds)
    rate_limits = {
        AlertSeverity.CRITICAL: 0,  # No rate limit for critical
        AlertSeverity.WARNING: 300,  # 5 minutes
        AlertSeverity.INFO: 1800,  # 30 minutes
        AlertSeverity.ANOMALY: 600,  # 10 minutes
    }

    def should_alert(self, category: str, severity: AlertSeverity) -> bool:
        """Check if we should send this alert based on rate limiting."""
        if severity == AlertSeverity.CRITICAL:
            return True

        key = f"{severity.value}:{category}"
        last_time = self.last_alert_time.get(key)

        if last_time is None:
            return True

        elapsed = (datetime.now() - last_time).total_seconds()
        return elapsed >= self.rate_limits.get(severity, 300)

    def record_alert(self, category: str, severity: AlertSeverity):
        """Record that an alert was sent."""
        key = f"{severity.value}:{category}"
        self.last_alert_time[key] = datetime.now()
        self.alert_counts[key] = self.alert_counts.get(key, 0) + 1


# Global state (singleton)
_alert_state = AlertState()
_telegram_bot = None


def _get_telegram_bot():
    """Get or create Telegram bot instance for alerts."""
    global _telegram_bot

    if _telegram_bot is not None:
        return _telegram_bot

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        logger.debug("Telegram not configured - error alerts will only be logged")
        return None

    try:
        from .telegram_bot import TelegramBot, escape_markdown  # noqa: F401

        _telegram_bot = TelegramBot(token, chat_id)
        return _telegram_bot
    except Exception as e:
        logger.warning(f"Failed to create Telegram bot for alerts: {e}")
        return None


async def _send_telegram_alert(
    severity: AlertSeverity,
    category: str,
    message: str,
    context: Optional[Dict[str, Any]] = None,
) -> bool:
    """Send alert via Telegram (async)."""
    from .telegram_bot import escape_markdown

    bot = _get_telegram_bot()
    if bot is None:
        return False

    try:
        # Ensure bot is initialized
        if bot._app is None:
            await bot.initialize()

        # Format the alert - escape dynamic content to prevent Markdown parse errors
        emoji_map = {
            AlertSeverity.CRITICAL: "🚨",
            AlertSeverity.WARNING: "⚠️",
            AlertSeverity.INFO: "ℹ️",
            AlertSeverity.ANOMALY: "🔍",
        }
        emoji = emoji_map.get(severity, "⚠️")

        alert_text = (
            f"{emoji} *{severity.value.upper()} ALERT*\n\n"
            f"*Category:* `{escape_markdown(category)}`\n"
            f"*Message:* {escape_markdown(message)}"
        )

        if context:
            context_str = "\n".join(
                f"  • {escape_markdown(str(k))}: `{escape_markdown(str(v))}`"
                for k, v in context.items()
            )
            alert_text += f"\n\n*Context:*\n{context_str}"

        alert_text += f"\n\n_Time: {datetime.now().strftime('%H:%M:%S ET')}_"

        return await bot.send_message(alert_text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Failed to send Telegram alert: {e}")
        return False


def alert_error(
    severity: AlertSeverity,
    message: str,
    context: Optional[Dict[str, Any]] = None,
    category: Optional[str] = None,
):
    """
    Send an error alert to Telegram (non-blocking).

    Args:
        severity: Alert severity level
        message: Human-readable error message
        context: Optional dict with additional context
        category: Optional category for rate-limiting (defaults to caller function)

    Example:
        alert_error(AlertSeverity.WARNING, "Backtest returned 0 trades", {"strategy": "MR"})
    """
    import inspect

    # Auto-detect category from caller if not provided
    if category is None:
        frame = inspect.currentframe()
        if frame and frame.f_back:
            category = frame.f_back.f_code.co_name
        else:
            category = "unknown"

    # Always log regardless of rate limiting
    log_msg = f"[{severity.value.upper()}] {category}: {message}"
    if context:
        log_msg += f" | context={context}"

    if severity == AlertSeverity.CRITICAL:
        logger.error(log_msg)
    elif severity == AlertSeverity.WARNING:
        logger.warning(log_msg)
    else:
        logger.info(log_msg)

    # Check rate limiting
    if not _alert_state.should_alert(category, severity):
        logger.debug(f"Alert rate-limited: {category}")
        return

    # Record alert
    _alert_state.record_alert(category, severity)

    # Send synchronously using unified async utilities
    # This handles all event loop states correctly (running, closed, none)
    from .async_utils import run_async_from_sync

    try:
        run_async_from_sync(
            _send_telegram_alert(severity, category, message, context),
            timeout=10.0,
        )
    except Exception as send_err:
        logger.warning(f"Could not send alert: {send_err}")


def alert_critical(message: str, context: Optional[Dict[str, Any]] = None, category: str = None):
    """Convenience function for critical alerts (always sent immediately)."""
    alert_error(AlertSeverity.CRITICAL, message, context, category)


def alert_warning(message: str, context: Optional[Dict[str, Any]] = None, category: str = None):
    """Convenience function for warning alerts (rate-limited)."""
    alert_error(AlertSeverity.WARNING, message, context, category)


def alert_anomaly(
    metric_name: str,
    actual_value: Any,
    expected: str,
    context: Optional[Dict[str, Any]] = None,
):
    """
    Alert on data anomaly (unexpected values).

    Args:
        metric_name: Name of the metric that's wrong
        actual_value: The actual value observed
        expected: Description of what was expected (e.g., ">0", "between 1-10")
        context: Additional context

    Example:
        alert_anomaly("backtest_trades", 0, ">0", {"strategy": "mean_reversion"})
    """
    message = f"`{metric_name}` = {actual_value} (expected: {expected})"
    alert_error(AlertSeverity.ANOMALY, message, context, category=f"anomaly:{metric_name}")


# Sync wrappers for use in non-async code
def sync_alert_error(
    severity: AlertSeverity,
    message: str,
    context: Optional[Dict[str, Any]] = None,
    category: Optional[str] = None,
):
    """Synchronous version of alert_error (blocks until sent)."""
    import inspect

    if category is None:
        frame = inspect.currentframe()
        if frame and frame.f_back:
            category = frame.f_back.f_code.co_name
        else:
            category = "unknown"

    # Log
    log_msg = f"[{severity.value.upper()}] {category}: {message}"
    if context:
        log_msg += f" | context={context}"

    if severity == AlertSeverity.CRITICAL:
        logger.error(log_msg)
    elif severity == AlertSeverity.WARNING:
        logger.warning(log_msg)
    else:
        logger.info(log_msg)

    # Check rate limiting
    if not _alert_state.should_alert(category, severity):
        return

    _alert_state.record_alert(category, severity)

    # Send synchronously using unified async utilities
    # This handles all loop states correctly (running, closed, none)
    from .async_utils import run_async_from_sync

    try:
        run_async_from_sync(
            _send_telegram_alert(severity, category, message, context),
            timeout=10.0,
        )
    except Exception as send_err:
        logger.warning(f"Failed to send alert: {send_err}")


# Context manager for alerting on exceptions
class AlertOnException:
    """
    Context manager that sends alert if exception occurs.

    Usage:
        with AlertOnException("data_fetch", AlertSeverity.WARNING):
            data = fetch_market_data()
    """

    def __init__(
        self,
        category: str,
        severity: AlertSeverity = AlertSeverity.WARNING,
        context: Optional[Dict[str, Any]] = None,
        reraise: bool = True,
    ):
        self.category = category
        self.severity = severity
        self.context = context or {}
        self.reraise = reraise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.context["exception_type"] = exc_type.__name__
            alert_error(
                self.severity,
                str(exc_val),
                self.context,
                category=self.category,
            )
        return not self.reraise  # If reraise=False, suppress the exception
