"""
Telegram utilities and shared types.

Common utilities used across all Telegram command modules.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


def escape_markdown(text: str) -> str:
    """
    Escape special characters for Telegram Markdown parsing.

    Telegram's Markdown parser fails if these characters appear unmatched.
    This function escapes them to prevent parse errors.
    """
    if not text:
        return ""
    # Escape in specific order to avoid double-escaping
    return (
        str(text)
        .replace("\\", "\\\\")  # Escape backslashes first
        .replace("_", "\\_")
        .replace("*", "\\*")
        .replace("`", "\\`")
        .replace("[", "\\[")
    )


class ApprovalResult(Enum):
    """Result of trade approval request."""

    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass
class TradeApprovalRequest:
    """Pending trade approval request."""

    signal_type: str
    etf: str
    reason: str
    shares: int
    price: float
    position_value: float
    timestamp: datetime
