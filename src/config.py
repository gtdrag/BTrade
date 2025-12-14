"""
Configuration management for IBIT Dip Bot.
Handles loading, saving, and validating configuration.
"""

import os
import json
import logging
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, List, Dict, Any

from .strategy import StrategyConfig
from .notifications import NotificationConfig


logger = logging.getLogger(__name__)


# Default config file path
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config.json"


@dataclass
class ETradeConfig:
    """E*TRADE API configuration."""
    consumer_key: str = ""
    consumer_secret: str = ""
    account_id_key: str = ""  # The specific account to trade in
    sandbox: bool = False     # Use sandbox environment


@dataclass
class AppConfig:
    """Main application configuration."""
    # E*TRADE settings
    etrade: ETradeConfig = field(default_factory=ETradeConfig)

    # Strategy settings
    strategy: StrategyConfig = field(default_factory=StrategyConfig)

    # Notification settings
    notifications: NotificationConfig = field(default_factory=NotificationConfig)

    # App settings
    dry_run: bool = True  # Default to dry run for safety
    log_level: str = "INFO"
    theme: str = "dark"  # "dark" or "light" for dashboard

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppConfig":
        """Create config from dictionary."""
        etrade_data = data.get("etrade", {})
        strategy_data = data.get("strategy", {})
        notifications_data = data.get("notifications", {})

        # Filter strategy_data to only include valid StrategyConfig fields
        valid_strategy_fields = {
            'strategy_type', 'regular_threshold', 'monday_threshold', 'monday_enabled',
            'mean_reversion_threshold', 'skip_thursday_for_mr', 'enable_short_thursday',
            'max_position_usd', 'max_position_pct', 'use_limit_orders', 'limit_offset_pct', 'dry_run'
        }
        filtered_strategy_data = {k: v for k, v in strategy_data.items() if k in valid_strategy_fields}

        # Filter notification_data to only include valid NotificationConfig fields
        valid_notification_fields = {
            'email_enabled', 'smtp_server', 'smtp_port', 'smtp_username', 'smtp_password',
            'email_from', 'email_to', 'desktop_enabled', 'sms_enabled', 'sms_to',
            'notify_on_trade', 'notify_on_error', 'notify_on_daily_summary'
        }
        filtered_notifications_data = {k: v for k, v in notifications_data.items() if k in valid_notification_fields}

        return cls(
            etrade=ETradeConfig(**etrade_data),
            strategy=StrategyConfig(**filtered_strategy_data),
            notifications=NotificationConfig(**filtered_notifications_data),
            dry_run=data.get("dry_run", True),
            log_level=data.get("log_level", "INFO"),
            theme=data.get("theme", "dark")
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary."""
        return {
            "etrade": asdict(self.etrade),
            "strategy": asdict(self.strategy),
            "notifications": asdict(self.notifications),
            "dry_run": self.dry_run,
            "log_level": self.log_level,
            "theme": self.theme
        }

    def validate(self) -> List[str]:
        """
        Validate configuration.

        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []

        # E*TRADE validation (only if not in dry run)
        if not self.dry_run:
            if not self.etrade.consumer_key:
                errors.append("E*TRADE consumer_key is required for live trading")
            if not self.etrade.consumer_secret:
                errors.append("E*TRADE consumer_secret is required for live trading")

        # Strategy validation
        if self.strategy.regular_threshold < 0.1 or self.strategy.regular_threshold > 5.0:
            errors.append("regular_threshold should be between 0.1% and 5.0%")

        if self.strategy.monday_enabled:
            if self.strategy.monday_threshold < 0.1 or self.strategy.monday_threshold > 5.0:
                errors.append("monday_threshold should be between 0.1% and 5.0%")

        if self.strategy.max_position_pct <= 0 or self.strategy.max_position_pct > 100:
            errors.append("max_position_pct should be between 0 and 100")

        # Notification validation
        if self.notifications.email_enabled:
            if not self.notifications.smtp_username:
                errors.append("SMTP username required when email is enabled")
            if not self.notifications.smtp_password:
                errors.append("SMTP password required when email is enabled")
            if not self.notifications.email_to:
                errors.append("Email recipients required when email is enabled")

        return errors


def load_config(path: Optional[Path] = None) -> AppConfig:
    """
    Load configuration from file.

    Also loads sensitive values from environment variables if not in config file.

    Args:
        path: Path to config file (default: config.json in project root)

    Returns:
        AppConfig instance
    """
    config_path = path or DEFAULT_CONFIG_PATH

    if config_path.exists():
        try:
            with open(config_path, 'r') as f:
                data = json.load(f)
            logger.info(f"Loaded config from {config_path}")
            config = AppConfig.from_dict(data)
        except Exception as e:
            logger.warning(f"Failed to load config: {e}. Using defaults.")
            config = AppConfig()
    else:
        logger.info("No config file found, using defaults")
        config = AppConfig()

    # Override with environment variables (more secure for credentials)
    _load_env_overrides(config)

    return config


def _load_env_overrides(config: AppConfig):
    """Load sensitive values from environment variables."""
    # E*TRADE credentials
    if os.environ.get("ETRADE_CONSUMER_KEY"):
        config.etrade.consumer_key = os.environ["ETRADE_CONSUMER_KEY"]
    if os.environ.get("ETRADE_CONSUMER_SECRET"):
        config.etrade.consumer_secret = os.environ["ETRADE_CONSUMER_SECRET"]
    if os.environ.get("ETRADE_ACCOUNT_ID"):
        config.etrade.account_id_key = os.environ["ETRADE_ACCOUNT_ID"]
    if os.environ.get("ETRADE_SANDBOX"):
        config.etrade.sandbox = os.environ["ETRADE_SANDBOX"].lower() in ("true", "1", "yes")

    # SMTP credentials
    if os.environ.get("SMTP_SERVER"):
        config.notifications.smtp_server = os.environ["SMTP_SERVER"]
    if os.environ.get("SMTP_USERNAME"):
        config.notifications.smtp_username = os.environ["SMTP_USERNAME"]
    if os.environ.get("SMTP_PASSWORD"):
        config.notifications.smtp_password = os.environ["SMTP_PASSWORD"]
    if os.environ.get("EMAIL_TO"):
        config.notifications.email_to = os.environ["EMAIL_TO"].split(",")

    # Dry run override
    if os.environ.get("DRY_RUN"):
        config.dry_run = os.environ["DRY_RUN"].lower() in ("true", "1", "yes")


def save_config(config: AppConfig, path: Optional[Path] = None):
    """
    Save configuration to file.

    Note: Sensitive credentials should be stored in environment variables,
    not in the config file.

    Args:
        config: AppConfig to save
        path: Path to save to (default: config.json in project root)
    """
    config_path = path or DEFAULT_CONFIG_PATH

    # Create a copy without sensitive data
    data = config.to_dict()

    # Remove sensitive fields
    data["etrade"]["consumer_key"] = ""
    data["etrade"]["consumer_secret"] = ""
    data["notifications"]["smtp_password"] = ""

    with open(config_path, 'w') as f:
        json.dump(data, f, indent=2, default=str)

    logger.info(f"Saved config to {config_path}")


def create_default_config(path: Optional[Path] = None):
    """Create a default config file if one doesn't exist."""
    config_path = path or DEFAULT_CONFIG_PATH

    if config_path.exists():
        logger.warning(f"Config file already exists at {config_path}")
        return

    config = AppConfig()
    save_config(config, config_path)
    logger.info(f"Created default config at {config_path}")


def setup_logging(level: str = "INFO"):
    """
    Configure logging for the application.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
    """
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format=log_format,
        datefmt=date_format,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                Path(__file__).parent.parent / "ibit_bot.log",
                mode='a'
            )
        ]
    )

    # Reduce noise from third-party libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests_oauthlib").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
