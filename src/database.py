"""
Database module for IBIT Dip Bot.
Handles SQLite operations for trades, settings, and bot state.

Database path can be configured via DATABASE_PATH environment variable.
This is useful for Railway deployments with persistent volumes:
  - Set DATABASE_PATH=/data/trades.db
  - Mount a volume at /data
"""

import datetime
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from .utils import get_et_now


def get_default_db_path() -> Path:
    """Get database path from environment or use default.

    Environment variable: DATABASE_PATH
    Default: ./trades.db (relative to project root)

    For Railway with persistent volume:
        DATABASE_PATH=/data/trades.db
    """
    env_path = os.environ.get("DATABASE_PATH")
    if env_path:
        path = Path(env_path)
        # Ensure parent directory exists
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    return Path(__file__).parent.parent / "trades.db"


# Default database path (can be overridden by DATABASE_PATH env var)
DEFAULT_DB_PATH = get_default_db_path()


class Database:
    """SQLite database handler for IBIT Dip Bot."""

    def __init__(self, db_path: Optional[Path] = None):
        """Initialize database connection."""
        self.db_path = db_path or DEFAULT_DB_PATH
        self._init_db()

    @contextmanager
    def _get_connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        """Initialize database tables."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Trades table - stores all executed trades
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    day_of_week TEXT NOT NULL,
                    open_price REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    dip_percentage REAL NOT NULL,
                    shares INTEGER NOT NULL,
                    entry_time TEXT NOT NULL,
                    exit_time TEXT,
                    dollar_pnl REAL,
                    percentage_pnl REAL,
                    status TEXT NOT NULL DEFAULT 'open',
                    is_dry_run INTEGER NOT NULL DEFAULT 0,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """
            )

            # Bot state table - tracks current position and status
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    is_paused INTEGER NOT NULL DEFAULT 0,
                    pause_until TEXT,
                    current_position_shares INTEGER DEFAULT 0,
                    current_position_entry_price REAL,
                    current_position_date TEXT,
                    last_open_price REAL,
                    last_open_price_date TEXT,
                    total_trades INTEGER DEFAULT 0,
                    winning_trades INTEGER DEFAULT 0,
                    total_pnl REAL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
            """
            )

            # Daily prices table - stores daily open prices
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_prices (
                    date TEXT PRIMARY KEY,
                    open_price REAL NOT NULL,
                    captured_at TEXT NOT NULL
                )
            """
            )

            # Logs table - for audit trail
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    level TEXT NOT NULL,
                    event TEXT NOT NULL,
                    details TEXT,
                    created_at TEXT NOT NULL
                )
            """
            )

            # Strategy parameters table - stores approved Claude recommendations
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_params (
                    param_name TEXT PRIMARY KEY,
                    param_value REAL NOT NULL,
                    previous_value REAL,
                    reason TEXT,
                    confidence TEXT,
                    applied_at TEXT NOT NULL,
                    applied_by TEXT DEFAULT 'claude_recommendation'
                )
            """
            )

            # Strategy reviews table - stores full review history for recursive learning
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    review_date TEXT NOT NULL,
                    full_report TEXT NOT NULL,
                    summary TEXT,
                    current_params TEXT,
                    backtest_return REAL,
                    recommendations TEXT,
                    watch_items TEXT,
                    market_regime TEXT,
                    market_conditions TEXT,
                    created_at TEXT NOT NULL
                )
            """
            )

            # Migration: Add market_regime column if it doesn't exist (for existing databases)
            cursor.execute("PRAGMA table_info(strategy_reviews)")
            columns = [row[1] for row in cursor.fetchall()]
            if "market_regime" not in columns:
                cursor.execute("ALTER TABLE strategy_reviews ADD COLUMN market_regime TEXT")

            # Initialize bot state if not exists
            cursor.execute(
                """
                INSERT OR IGNORE INTO bot_state (id, updated_at) VALUES (1, ?)
            """,
                (get_et_now().isoformat(),),
            )

    # ==================== Trade Operations ====================

    def record_trade_entry(
        self,
        date: datetime.date,
        day_of_week: str,
        open_price: float,
        entry_price: float,
        dip_percentage: float,
        shares: int,
        is_dry_run: bool = False,
        notes: Optional[str] = None,
    ) -> int:
        """Record a new trade entry. Returns trade ID."""
        now = get_et_now()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO trades (
                    date, day_of_week, open_price, entry_price, dip_percentage,
                    shares, entry_time, status, is_dry_run, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)
            """,
                (
                    date.isoformat(),
                    day_of_week,
                    open_price,
                    entry_price,
                    dip_percentage,
                    shares,
                    now.isoformat(),
                    1 if is_dry_run else 0,
                    notes,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            return cursor.lastrowid

    def record_trade_exit(
        self, trade_id: int, exit_price: float, dollar_pnl: float, percentage_pnl: float
    ):
        """Record trade exit (sell)."""
        now = get_et_now()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE trades SET
                    exit_price = ?,
                    exit_time = ?,
                    dollar_pnl = ?,
                    percentage_pnl = ?,
                    status = 'closed',
                    updated_at = ?
                WHERE id = ?
            """,
                (
                    exit_price,
                    now.isoformat(),
                    dollar_pnl,
                    percentage_pnl,
                    now.isoformat(),
                    trade_id,
                ),
            )

    def get_open_trade(self) -> Optional[Dict[str, Any]]:
        """Get current open trade if exists."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM trades WHERE status = 'open' ORDER BY id DESC LIMIT 1
            """
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_trade_history(
        self, limit: int = 100, include_dry_runs: bool = True
    ) -> List[Dict[str, Any]]:
        """Get trade history."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if include_dry_runs:
                cursor.execute(
                    """
                    SELECT * FROM trades ORDER BY date DESC, id DESC LIMIT ?
                """,
                    (limit,),
                )
            else:
                cursor.execute(
                    """
                    SELECT * FROM trades WHERE is_dry_run = 0
                    ORDER BY date DESC, id DESC LIMIT ?
                """,
                    (limit,),
                )
            return [dict(row) for row in cursor.fetchall()]

    def get_trade_statistics(self, include_dry_runs: bool = False) -> Dict[str, Any]:
        """Calculate trade statistics."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            dry_run_filter = "" if include_dry_runs else "AND is_dry_run = 0"

            cursor.execute(
                f"""
                SELECT
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN percentage_pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
                    SUM(CASE WHEN percentage_pnl <= 0 THEN 1 ELSE 0 END) as losing_trades,
                    SUM(dollar_pnl) as total_pnl,
                    AVG(percentage_pnl) as avg_return,
                    MAX(percentage_pnl) as best_trade,
                    MIN(percentage_pnl) as worst_trade,
                    AVG(dip_percentage) as avg_dip
                FROM trades
                WHERE status = 'closed' {dry_run_filter}
            """
            )

            row = cursor.fetchone()
            stats = dict(row)

            # Calculate win rate
            total = stats["total_trades"] or 0
            winning = stats["winning_trades"] or 0
            stats["win_rate"] = (winning / total * 100) if total > 0 else 0

            return stats

    # ==================== Bot State Operations ====================

    def get_bot_state(self) -> Dict[str, Any]:
        """Get current bot state."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM bot_state WHERE id = 1")
            row = cursor.fetchone()
            return dict(row) if row else {}

    def update_bot_state(self, **kwargs):
        """Update bot state fields."""
        if not kwargs:
            return

        now = get_et_now()
        kwargs["updated_at"] = now.isoformat()

        fields = ", ".join(f"{k} = ?" for k in kwargs.keys())
        values = list(kwargs.values())

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE bot_state SET {fields} WHERE id = 1", values)

    def set_paused(self, paused: bool, until: Optional[datetime.datetime] = None):
        """Set bot paused state."""
        self.update_bot_state(
            is_paused=1 if paused else 0, pause_until=until.isoformat() if until else None
        )

    def set_position(
        self, shares: int, entry_price: Optional[float] = None, date: Optional[datetime.date] = None
    ):
        """Update current position in bot state."""
        self.update_bot_state(
            current_position_shares=shares,
            current_position_entry_price=entry_price,
            current_position_date=date.isoformat() if date else None,
        )

    def clear_position(self):
        """Clear current position."""
        self.set_position(0, None, None)

    # ==================== Daily Price Operations ====================

    def store_open_price(self, date: datetime.date, price: float):
        """Store daily open price."""
        now = get_et_now()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO daily_prices (date, open_price, captured_at)
                VALUES (?, ?, ?)
            """,
                (date.isoformat(), price, now.isoformat()),
            )

        # Also update bot state
        self.update_bot_state(last_open_price=price, last_open_price_date=date.isoformat())

    def get_open_price(self, date: datetime.date) -> Optional[float]:
        """Get stored open price for a date."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT open_price FROM daily_prices WHERE date = ?
            """,
                (date.isoformat(),),
            )
            row = cursor.fetchone()
            return row["open_price"] if row else None

    # ==================== Logging Operations ====================

    def log_event(self, level: str, event: str, details: Optional[Dict] = None):
        """Log an event to the database."""
        now = get_et_now()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO logs (timestamp, level, event, details, created_at)
                VALUES (?, ?, ?, ?, ?)
            """,
                (
                    now.isoformat(),
                    level,
                    event,
                    json.dumps(details) if details else None,
                    now.isoformat(),
                ),
            )

    def get_logs(self, limit: int = 100, level: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get recent logs."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if level:
                cursor.execute(
                    """
                    SELECT * FROM logs WHERE level = ? ORDER BY id DESC LIMIT ?
                """,
                    (level, limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT * FROM logs ORDER BY id DESC LIMIT ?
                """,
                    (limit,),
                )
            return [dict(row) for row in cursor.fetchall()]

    def get_events(
        self, since: Optional[str] = None, level: Optional[str] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get events, optionally filtered by date and level.

        Args:
            since: ISO date string (YYYY-MM-DD) to filter events from
            level: Filter by event level/type
            limit: Max number of events to return
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            query = "SELECT * FROM logs WHERE 1=1"
            params = []

            if since:
                query += " AND timestamp >= ?"
                params.append(since)

            if level:
                query += " AND level = ?"
                params.append(level)

            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    # ==================== Equity Curve ====================

    def get_equity_curve(self, include_dry_runs: bool = False) -> List[Dict[str, Any]]:
        """Get cumulative equity curve data."""
        trades = self.get_trade_history(limit=1000, include_dry_runs=include_dry_runs)

        if not trades:
            return []

        # Sort by date ascending
        trades = sorted(trades, key=lambda x: (x["date"], x["id"]))

        # Calculate cumulative returns
        cumulative_pnl = 0
        curve = []

        for trade in trades:
            if trade["status"] == "closed" and trade["dollar_pnl"] is not None:
                cumulative_pnl += trade["dollar_pnl"]
                curve.append(
                    {
                        "date": trade["date"],
                        "trade_id": trade["id"],
                        "trade_pnl": trade["dollar_pnl"],
                        "trade_pct": trade["percentage_pnl"],
                        "cumulative_pnl": cumulative_pnl,
                    }
                )

        return curve

    # ==================== Strategy Parameters ====================

    def save_strategy_param(
        self,
        param_name: str,
        param_value: float,
        previous_value: Optional[float] = None,
        reason: Optional[str] = None,
        confidence: Optional[str] = None,
    ) -> bool:
        """Save or update a strategy parameter.

        Args:
            param_name: Name of the parameter (e.g., 'mr_threshold')
            param_value: The new value
            previous_value: The value before this change
            reason: Why this change was made
            confidence: Confidence level ('low', 'medium', 'high')

        Returns:
            True if saved successfully
        """
        now = get_et_now()
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO strategy_params
                    (param_name, param_value, previous_value, reason, confidence, applied_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (
                        param_name,
                        param_value,
                        previous_value,
                        reason,
                        confidence,
                        now.isoformat(),
                    ),
                )
            return True
        except Exception:
            return False

    def get_strategy_param(self, param_name: str) -> Optional[float]:
        """Get a single strategy parameter value.

        Args:
            param_name: Name of the parameter

        Returns:
            The parameter value, or None if not found
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT param_value FROM strategy_params WHERE param_name = ?",
                (param_name,),
            )
            row = cursor.fetchone()
            return row["param_value"] if row else None

    def get_all_strategy_params(self) -> Dict[str, float]:
        """Get all saved strategy parameters.

        Returns:
            Dict mapping param_name to param_value
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT param_name, param_value FROM strategy_params")
            return {row["param_name"]: row["param_value"] for row in cursor.fetchall()}

    def get_strategy_param_history(self, param_name: str) -> Optional[Dict[str, Any]]:
        """Get full details of a strategy parameter including history.

        Args:
            param_name: Name of the parameter

        Returns:
            Dict with param details, or None if not found
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM strategy_params WHERE param_name = ?",
                (param_name,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    # ==================== Strategy Review Operations ====================

    def save_strategy_review(
        self,
        full_report: str,
        summary: str,
        current_params: Dict[str, float],
        backtest_return: float,
        recommendations: List[Dict[str, Any]],
        watch_items: List[Dict[str, Any]],
        market_regime: Optional[Dict[str, Any]] = None,
        market_conditions: Optional[str] = None,
    ) -> int:
        """Save a complete strategy review for future reference.

        Args:
            full_report: Claude's complete analysis text
            summary: Brief summary of the review
            current_params: Parameters at time of review
            backtest_return: Current strategy return from backtest
            recommendations: List of parameter recommendations made
            watch_items: List of things Claude flagged to monitor
            market_regime: Detected market regime data
            market_conditions: Optional market context summary

        Returns:
            The ID of the saved review
        """
        now = get_et_now().isoformat()
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO strategy_reviews
                    (review_date, full_report, summary, current_params, backtest_return,
                     recommendations, watch_items, market_regime, market_conditions, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        now[:10],  # Just the date part
                        full_report,
                        summary,
                        json.dumps(current_params),
                        backtest_return,
                        json.dumps(recommendations),
                        json.dumps(watch_items),
                        json.dumps(market_regime) if market_regime else None,
                        market_conditions,
                        now,
                    ),
                )
                review_id = cursor.lastrowid
                conn.commit()
                return review_id
        except Exception as e:
            print(f"Error saving strategy review: {e}")
            return -1

    def get_previous_reviews(self, limit: int = 2) -> List[Dict[str, Any]]:
        """Get the most recent strategy reviews for context.

        Args:
            limit: Maximum number of reviews to return (default 2)

        Returns:
            List of review dicts, most recent first
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, review_date, full_report, summary, current_params,
                       backtest_return, recommendations, watch_items, market_regime,
                       market_conditions
                FROM strategy_reviews
                ORDER BY created_at DESC
                LIMIT ?
            """,
                (limit,),
            )
            reviews = []
            for row in cursor.fetchall():
                review = dict(row)
                # Parse JSON fields
                if review.get("current_params"):
                    review["current_params"] = json.loads(review["current_params"])
                if review.get("recommendations"):
                    review["recommendations"] = json.loads(review["recommendations"])
                if review.get("watch_items"):
                    review["watch_items"] = json.loads(review["watch_items"])
                if review.get("market_regime"):
                    review["market_regime"] = json.loads(review["market_regime"])
                reviews.append(review)
            return reviews

    def get_all_watch_items(self, resolved: bool = False) -> List[Dict[str, Any]]:
        """Get all active watch items from recent reviews.

        Args:
            resolved: If True, include resolved items; if False, only active

        Returns:
            List of watch items with their source review dates
        """
        reviews = self.get_previous_reviews(limit=5)
        watch_items = []
        for review in reviews:
            items = review.get("watch_items", [])
            for item in items:
                item["from_review_date"] = review["review_date"]
                if not resolved and item.get("resolved"):
                    continue
                watch_items.append(item)
        return watch_items


# Singleton instance
_db_instance: Optional[Database] = None


def get_database(db_path: Optional[Path] = None) -> Database:
    """Get or create database singleton."""
    global _db_instance
    if _db_instance is None:
        _db_instance = Database(db_path)
    return _db_instance
