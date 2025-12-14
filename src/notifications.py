"""
Notification system for IBIT Dip Bot.
Handles email, SMS, and desktop notifications.
"""

import logging
import os
import smtplib
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from enum import Enum
from typing import List, Optional

from .utils import format_currency, format_percentage, get_et_now

logger = logging.getLogger(__name__)


class NotificationType(Enum):
    """Types of notifications."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    TRADE = "trade"


@dataclass
class NotificationConfig:
    """Configuration for notifications."""

    # Email settings
    email_enabled: bool = False
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""  # App password for Gmail
    email_from: str = ""
    email_to: List[str] = None

    # Desktop notifications
    desktop_enabled: bool = True

    # SMS via email gateway (e.g., number@vtext.com for Verizon)
    sms_enabled: bool = False
    sms_to: List[str] = None

    # Notification preferences
    notify_on_trade: bool = True
    notify_on_error: bool = True
    notify_on_daily_summary: bool = True

    def __post_init__(self):
        if self.email_to is None:
            self.email_to = []
        if self.sms_to is None:
            self.sms_to = []


class NotificationManager:
    """Manages sending notifications through various channels."""

    def __init__(self, config: Optional[NotificationConfig] = None):
        """Initialize notification manager."""
        self.config = config or NotificationConfig()
        self._desktop_available = self._check_desktop_available()

    def _check_desktop_available(self) -> bool:
        """Check if desktop notifications are available."""
        try:
            import importlib.util

            return importlib.util.find_spec("plyer") is not None
        except ImportError:
            logger.warning("plyer not installed - desktop notifications disabled")
            return False

    def send(
        self, title: str, message: str, notification_type: NotificationType = NotificationType.INFO
    ):
        """
        Send notification through all enabled channels.

        Args:
            title: Notification title
            message: Notification body
            notification_type: Type of notification
        """
        logger.info(f"Notification [{notification_type.value}]: {title} - {message}")

        # Send desktop notification
        if self.config.desktop_enabled and self._desktop_available:
            self._send_desktop(title, message)

        # Send email notification
        if self.config.email_enabled and self.config.email_to:
            self._send_email(title, message, notification_type)

        # Send SMS notification (for errors and trades only)
        if self.config.sms_enabled and self.config.sms_to:
            if notification_type in (NotificationType.ERROR, NotificationType.TRADE):
                self._send_sms(title, message)

    def _send_desktop(self, title: str, message: str):
        """Send desktop notification."""
        try:
            from plyer import notification

            notification.notify(
                title=f"IBIT Bot: {title}",
                message=message[:256],  # Truncate long messages
                app_name="IBIT Dip Bot",
                timeout=10,
            )
        except Exception as e:
            logger.warning(f"Desktop notification failed: {e}")

    def _send_email(self, title: str, message: str, notification_type: NotificationType):
        """Send email notification."""
        if not all(
            [
                self.config.smtp_server,
                self.config.smtp_username,
                self.config.smtp_password,
                self.config.email_to,
            ]
        ):
            logger.warning("Email not configured properly")
            return

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[IBIT Bot] {title}"
            msg["From"] = self.config.email_from or self.config.smtp_username
            msg["To"] = ", ".join(self.config.email_to)

            # Plain text version
            text_body = f"""
IBIT Dip Bot Notification
=========================

{title}

{message}

---
Sent at: {get_et_now().strftime('%Y-%m-%d %H:%M:%S ET')}
Type: {notification_type.value}
"""

            # HTML version
            html_body = f"""
<html>
<body style="font-family: Arial, sans-serif; padding: 20px;">
    <h2 style="color: #333;">IBIT Dip Bot</h2>
    <h3 style="color: {'#d32f2f' if notification_type == NotificationType.ERROR else '#1976d2'};">
        {title}
    </h3>
    <p style="font-size: 14px; color: #555;">{message.replace(chr(10), '<br>')}</p>
    <hr style="border: 1px solid #eee;">
    <p style="font-size: 12px; color: #999;">
        Sent at: {get_et_now().strftime('%Y-%m-%d %H:%M:%S ET')}
    </p>
</body>
</html>
"""

            msg.attach(MIMEText(text_body, "plain"))
            msg.attach(MIMEText(html_body, "html"))

            with smtplib.SMTP(self.config.smtp_server, self.config.smtp_port) as server:
                server.starttls()
                server.login(self.config.smtp_username, self.config.smtp_password)
                server.sendmail(
                    self.config.email_from or self.config.smtp_username,
                    self.config.email_to,
                    msg.as_string(),
                )

            logger.info(f"Email sent to {self.config.email_to}")

        except Exception as e:
            logger.error(f"Failed to send email: {e}")

    def _send_sms(self, title: str, message: str):
        """Send SMS via email-to-SMS gateway."""
        if not self.config.sms_to:
            return

        # SMS messages should be short
        short_message = f"{title}: {message[:100]}"

        try:
            msg = MIMEText(short_message)
            msg["Subject"] = "IBIT Bot"
            msg["From"] = self.config.email_from or self.config.smtp_username
            msg["To"] = ", ".join(self.config.sms_to)

            with smtplib.SMTP(self.config.smtp_server, self.config.smtp_port) as server:
                server.starttls()
                server.login(self.config.smtp_username, self.config.smtp_password)
                server.sendmail(
                    self.config.email_from or self.config.smtp_username,
                    self.config.sms_to,
                    msg.as_string(),
                )

            logger.info(f"SMS sent to {self.config.sms_to}")

        except Exception as e:
            logger.error(f"Failed to send SMS: {e}")

    # Convenience methods

    def send_info(self, title: str, message: str):
        """Send info notification."""
        self.send(title, message, NotificationType.INFO)

    def send_warning(self, title: str, message: str):
        """Send warning notification."""
        self.send(title, message, NotificationType.WARNING)

    def send_error(self, title: str, message: str):
        """Send error notification."""
        if self.config.notify_on_error:
            self.send(title, message, NotificationType.ERROR)

    def send_trade(
        self,
        action: str,
        symbol: str,
        shares: int,
        price: float,
        dip_pct: Optional[float] = None,
        pnl: Optional[float] = None,
        pnl_pct: Optional[float] = None,
    ):
        """Send trade notification."""
        if not self.config.notify_on_trade:
            return

        title = f"{action} {shares} {symbol}"

        if action.upper() == "BUY":
            message = f"Bought {shares} shares at {format_currency(price)}"
            if dip_pct is not None:
                message += f"\nDip: {format_percentage(-dip_pct)}"
        else:
            message = f"Sold {shares} shares at {format_currency(price)}"
            if pnl is not None:
                message += f"\nP&L: {format_currency(pnl)} ({format_percentage(pnl_pct or 0)})"

        self.send(title, message, NotificationType.TRADE)

    def send_daily_summary(
        self,
        date: str,
        traded: bool,
        pnl: Optional[float] = None,
        pnl_pct: Optional[float] = None,
        total_pnl: Optional[float] = None,
        win_rate: Optional[float] = None,
    ):
        """Send daily summary notification."""
        if not self.config.notify_on_daily_summary:
            return

        title = f"Daily Summary - {date}"

        if traded:
            message = (
                f"Today's trade: {format_currency(pnl or 0)} ({format_percentage(pnl_pct or 0)})"
            )
        else:
            message = "No trades today"

        if total_pnl is not None:
            message += f"\n\nTotal P&L: {format_currency(total_pnl)}"
        if win_rate is not None:
            message += f"\nWin Rate: {win_rate:.1f}%"

        self.send(title, message, NotificationType.INFO)


def create_notification_manager(
    email_enabled: bool = False,
    smtp_server: str = None,
    smtp_username: str = None,
    smtp_password: str = None,
    email_to: List[str] = None,
    desktop_enabled: bool = True,
) -> NotificationManager:
    """
    Factory function to create NotificationManager.

    Credentials can be passed directly or via environment variables:
    - SMTP_SERVER
    - SMTP_USERNAME
    - SMTP_PASSWORD
    - EMAIL_TO (comma-separated)
    """
    config = NotificationConfig(
        email_enabled=email_enabled,
        smtp_server=smtp_server or os.environ.get("SMTP_SERVER", "smtp.gmail.com"),
        smtp_port=int(os.environ.get("SMTP_PORT", "587")),
        smtp_username=smtp_username or os.environ.get("SMTP_USERNAME", ""),
        smtp_password=smtp_password or os.environ.get("SMTP_PASSWORD", ""),
        email_to=email_to or os.environ.get("EMAIL_TO", "").split(",")
        if os.environ.get("EMAIL_TO")
        else [],
        desktop_enabled=desktop_enabled,
    )

    return NotificationManager(config)
