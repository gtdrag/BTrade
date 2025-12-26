"""
Trailing Hedge Manager for IBIT Trading Bot.

Implements a conservative trailing hedge strategy that progressively
adds inverse positions as gains accumulate, locking in profits.

Hedge Tiers (Conservative):
- +2.5% gain: Add 15% inverse hedge
- +4.0% gain: Add 15% inverse hedge (total 30%)
- +5.5% gain: Add 10% inverse hedge (total 40% max)
"""

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

import structlog

from .utils import get_et_now

logger = structlog.get_logger(__name__)


class HedgeInstrument(Enum):
    """Inverse instruments for hedging."""

    SBIT = "SBIT"  # 2x inverse Bitcoin (hedge for BITU/IBIT long)
    BITU = "BITU"  # 2x long Bitcoin (hedge for SBIT short)


@dataclass
class HedgeTier:
    """A single hedge tier configuration."""

    gain_threshold_pct: float  # Trigger when position gains this %
    hedge_size_pct: float  # Add this % of original position as hedge
    triggered: bool = False  # Has this tier been triggered?
    triggered_at: Optional[str] = None  # When it triggered


@dataclass
class HedgeConfig:
    """Configuration for trailing hedge behavior."""

    enabled: bool = True

    # Hedge tiers (conservative defaults)
    tiers: List[HedgeTier] = field(
        default_factory=lambda: [
            HedgeTier(gain_threshold_pct=2.5, hedge_size_pct=15.0),
            HedgeTier(gain_threshold_pct=4.0, hedge_size_pct=15.0),
            HedgeTier(gain_threshold_pct=5.5, hedge_size_pct=10.0),
        ]
    )

    # Maximum total hedge as % of original position
    max_hedge_pct: float = 40.0

    # Minimum position gain in dollars before hedging
    min_gain_dollars: float = 20.0

    # Check interval in seconds (how often to check price)
    check_interval_seconds: int = 300  # 5 minutes

    def get_total_hedge_pct(self) -> float:
        """Get total hedge % from triggered tiers."""
        return sum(t.hedge_size_pct for t in self.tiers if t.triggered)

    def reset_tiers(self):
        """Reset all tiers to untriggered state."""
        for tier in self.tiers:
            tier.triggered = False
            tier.triggered_at = None


@dataclass
class ActivePosition:
    """Tracks an active position for hedge management."""

    instrument: str  # BITU or SBIT
    shares: int
    entry_price: float
    entry_time: str
    original_value: float  # shares * entry_price

    # Hedge tracking
    hedge_instrument: Optional[str] = None  # The inverse instrument
    hedge_shares: int = 0
    hedge_entries: List[Dict] = field(default_factory=list)  # History of hedge additions

    def get_hedge_instrument(self) -> str:
        """Get the appropriate inverse instrument for hedging."""
        if self.instrument in ("BITU", "IBIT"):
            return "SBIT"
        elif self.instrument == "SBIT":
            return "BITU"
        else:
            return "SBIT"  # Default to SBIT for unknown


class TrailingHedgeManager:
    """
    Manages trailing hedges for active positions.

    Usage:
        manager = TrailingHedgeManager()
        manager.register_position("BITU", 100, 25.50)

        # Periodically check and potentially add hedge
        hedge_order = manager.check_and_hedge(current_price=26.50)
        if hedge_order:
            execute_order(hedge_order)

        # At EOD, get close orders for both position and hedge
        close_orders = manager.get_close_orders()
    """

    def __init__(self, config: Optional[HedgeConfig] = None):
        """Initialize the hedge manager."""
        self.config = config or HedgeConfig()
        self.position: Optional[ActivePosition] = None
        self._notify_callback: Optional[Callable] = None

        # Load enabled state from environment
        env_enabled = os.environ.get("TRAILING_HEDGE_ENABLED", "true").lower()
        self.config.enabled = env_enabled in ("true", "1", "yes")

        logger.info(
            "TrailingHedgeManager initialized",
            enabled=self.config.enabled,
            tiers=len(self.config.tiers),
            max_hedge_pct=self.config.max_hedge_pct,
        )

    def set_notify_callback(self, callback: Callable):
        """Set callback for hedge notifications (e.g., Telegram)."""
        self._notify_callback = callback

    def register_position(
        self,
        instrument: str,
        shares: int,
        entry_price: float,
    ) -> None:
        """Register a new position for hedge tracking."""
        now = get_et_now()

        self.position = ActivePosition(
            instrument=instrument,
            shares=shares,
            entry_price=entry_price,
            entry_time=now.isoformat(),
            original_value=shares * entry_price,
            hedge_instrument=None,
            hedge_shares=0,
            hedge_entries=[],
        )

        # Reset tier triggers for new position
        self.config.reset_tiers()

        logger.info(
            "Position registered for hedge tracking",
            instrument=instrument,
            shares=shares,
            entry_price=entry_price,
            original_value=self.position.original_value,
        )

    def clear_position(self) -> None:
        """Clear the tracked position (after EOD close)."""
        self.position = None
        self.config.reset_tiers()
        logger.info("Position cleared from hedge tracking")

    def check_and_hedge(
        self,
        current_price: float,
    ) -> Optional[Dict]:
        """
        Check if hedge should be added based on current price.

        Args:
            current_price: Current price of the position instrument

        Returns:
            Dict with hedge order details if hedge should be added, None otherwise
            {
                "action": "BUY",
                "instrument": "SBIT",
                "shares": 15,
                "reason": "Trailing hedge tier 1 (+2.5%)",
            }
        """
        if not self.config.enabled:
            return None

        if not self.position:
            return None

        # Calculate current gain
        current_value = self.position.shares * current_price
        gain_dollars = current_value - self.position.original_value
        gain_pct = (gain_dollars / self.position.original_value) * 100

        # Check minimum gain threshold
        if gain_dollars < self.config.min_gain_dollars:
            return None

        # Check each tier
        current_total_hedge = self.config.get_total_hedge_pct()

        for i, tier in enumerate(self.config.tiers):
            if tier.triggered:
                continue

            if gain_pct >= tier.gain_threshold_pct:
                # Check if we'd exceed max hedge
                new_total = current_total_hedge + tier.hedge_size_pct
                if new_total > self.config.max_hedge_pct:
                    logger.info(
                        "Skipping hedge tier - would exceed max",
                        tier=i + 1,
                        current_hedge_pct=current_total_hedge,
                        max_hedge_pct=self.config.max_hedge_pct,
                    )
                    continue

                # Trigger this tier
                tier.triggered = True
                tier.triggered_at = get_et_now().isoformat()

                # Calculate hedge shares
                hedge_value = self.position.original_value * (tier.hedge_size_pct / 100)
                hedge_instrument = self.position.get_hedge_instrument()

                # We need current price of hedge instrument
                # For now, estimate shares (will be adjusted by actual execution)
                # This is approximate - actual order will use market price
                estimated_hedge_shares = max(1, int(hedge_value / current_price))

                # Record the hedge
                hedge_entry = {
                    "tier": i + 1,
                    "gain_pct": gain_pct,
                    "hedge_pct": tier.hedge_size_pct,
                    "hedge_value": hedge_value,
                    "timestamp": tier.triggered_at,
                }
                self.position.hedge_entries.append(hedge_entry)

                logger.info(
                    "Hedge tier triggered",
                    tier=i + 1,
                    gain_pct=f"{gain_pct:.2f}%",
                    threshold=f"{tier.gain_threshold_pct}%",
                    hedge_pct=f"{tier.hedge_size_pct}%",
                    hedge_value=f"${hedge_value:.2f}",
                    total_hedge_pct=f"{new_total}%",
                )

                # Notify via callback
                if self._notify_callback:
                    try:
                        self._notify_callback(
                            f"Trailing hedge tier {i + 1} triggered!\n"
                            f"Position gain: +{gain_pct:.2f}%\n"
                            f"Adding {tier.hedge_size_pct}% {hedge_instrument} hedge\n"
                            f"Total hedge: {new_total}%"
                        )
                    except Exception as e:
                        logger.warning(f"Hedge notification failed: {e}")

                return {
                    "action": "BUY",
                    "instrument": hedge_instrument,
                    "value": hedge_value,
                    "shares": estimated_hedge_shares,
                    "reason": f"Trailing hedge tier {i + 1} (+{tier.gain_threshold_pct}%)",
                    "position_gain_pct": gain_pct,
                    "total_hedge_pct": new_total,
                }

        return None

    def get_status(self) -> Dict:
        """Get current hedge status for display."""
        if not self.position:
            return {
                "active": False,
                "message": "No position being tracked",
            }

        triggered_tiers = [t for t in self.config.tiers if t.triggered]
        total_hedge_pct = self.config.get_total_hedge_pct()

        return {
            "active": True,
            "enabled": self.config.enabled,
            "position": {
                "instrument": self.position.instrument,
                "shares": self.position.shares,
                "entry_price": self.position.entry_price,
                "original_value": self.position.original_value,
            },
            "hedge": {
                "instrument": self.position.get_hedge_instrument(),
                "shares": self.position.hedge_shares,
                "total_pct": total_hedge_pct,
                "tiers_triggered": len(triggered_tiers),
                "tiers_total": len(self.config.tiers),
            },
            "entries": self.position.hedge_entries,
        }

    def get_close_orders(self, current_prices: Dict[str, float]) -> List[Dict]:
        """
        Get orders to close both position and any hedges at EOD.

        Args:
            current_prices: Dict of instrument -> current price

        Returns:
            List of close orders
        """
        orders = []

        if not self.position:
            return orders

        # Close main position
        orders.append(
            {
                "action": "SELL",
                "instrument": self.position.instrument,
                "shares": self.position.shares,
                "reason": "EOD close - main position",
            }
        )

        # Close hedge if any
        if self.position.hedge_shares > 0:
            orders.append(
                {
                    "action": "SELL",
                    "instrument": self.position.get_hedge_instrument(),
                    "shares": self.position.hedge_shares,
                    "reason": "EOD close - hedge position",
                }
            )

        return orders

    def update_hedge_shares(self, shares: int) -> None:
        """Update actual hedge shares after order execution."""
        if self.position:
            self.position.hedge_shares += shares
            if not self.position.hedge_instrument:
                self.position.hedge_instrument = self.position.get_hedge_instrument()

            logger.info(
                "Hedge shares updated",
                new_shares=shares,
                total_hedge_shares=self.position.hedge_shares,
            )


# Singleton instance
_hedge_manager: Optional[TrailingHedgeManager] = None


def get_hedge_manager(config: Optional[HedgeConfig] = None) -> TrailingHedgeManager:
    """Get or create the hedge manager singleton."""
    global _hedge_manager
    if _hedge_manager is None:
        _hedge_manager = TrailingHedgeManager(config)
    return _hedge_manager
