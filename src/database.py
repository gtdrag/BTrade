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
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .utils import get_et_now
from .wheel_state import WheelState, transition as validate_transition


class NumpyJSONEncoder(json.JSONEncoder):
    """JSON encoder that handles numpy types (which are not serializable by default)."""

    def default(self, obj):
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def safe_json_dumps(obj: Any, **kwargs) -> str:
    """Serialize object to JSON, handling numpy types.

    Accepts all json.dumps kwargs (indent, sort_keys, etc).
    """
    return json.dumps(obj, cls=NumpyJSONEncoder, **kwargs)


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
        """Context manager for database connections.

        Thread-safe: Uses WAL mode for concurrent access from APScheduler jobs.
        """
        conn = sqlite3.connect(self.db_path, timeout=30.0)
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
        """Initialize database tables and enable WAL mode for thread safety."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Enable WAL mode for better concurrent access
            # This allows multiple readers and one writer simultaneously
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")  # 30 second timeout

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
                    trading_mode TEXT DEFAULT 'paper',
                    wheel_mode_enabled INTEGER DEFAULT 1,
                    updated_at TEXT NOT NULL
                )
            """
            )

            # Migration: Add trading_mode column if it doesn't exist (for existing DBs)
            cursor.execute("PRAGMA table_info(bot_state)")
            columns = [col[1] for col in cursor.fetchall()]
            if "trading_mode" not in columns:
                cursor.execute("ALTER TABLE bot_state ADD COLUMN trading_mode TEXT DEFAULT 'paper'")

            # Migration: Add wheel_mode_enabled column if it doesn't exist (for existing DBs)
            # TR-01: Default 1 (enabled) so existing DBs safely disable intraday on upgrade
            if "wheel_mode_enabled" not in columns:
                cursor.execute("ALTER TABLE bot_state ADD COLUMN wheel_mode_enabled INTEGER DEFAULT 1")

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
            # Indexes for log queries — without these, get_events() scans the full
            # table, which degrades over time as monitoring jobs accumulate rows.
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level)"
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

            # Wheel strategy tables (Phase 2)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS wheel_cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    state TEXT NOT NULL DEFAULT 'CASH',
                    underlying TEXT NOT NULL DEFAULT 'IBIT',
                    put_strike REAL,
                    put_premium_received REAL DEFAULT 0.0,
                    put_expiry_date TEXT,
                    shares_held INTEGER DEFAULT 0,
                    cost_basis REAL DEFAULT 0.0,
                    covered_call_premiums_collected REAL DEFAULT 0.0,
                    realized_pnl REAL,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """
            )
            # Index for get_active_cycle() which filters on closed_at IS NULL
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_wheel_cycles_closed_at "
                "ON wheel_cycles(closed_at)"
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS options_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cycle_id INTEGER NOT NULL REFERENCES wheel_cycles(id),
                    symbol TEXT NOT NULL,
                    option_type TEXT NOT NULL,
                    strike REAL NOT NULL,
                    expiry_date TEXT NOT NULL,
                    dte_at_entry INTEGER NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 1,
                    premium_received REAL NOT NULL,
                    delta REAL,
                    gamma REAL,
                    theta REAL,
                    vega REAL,
                    iv REAL,
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    close_premium REAL,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """
            )
            # Index for get_cycle_positions() and get_open_position_for_cycle()
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_options_positions_cycle_id "
                "ON options_positions(cycle_id)"
            )
            # Index for queries filtering by status (OPEN vs CLOSED)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_options_positions_status "
                "ON options_positions(status)"
            )

            # Migration: Add roll_count and dte_alert_sent columns for profit management (Phase 5)
            cursor.execute("PRAGMA table_info(options_positions)")
            options_columns = [row[1] for row in cursor.fetchall()]
            if "roll_count" not in options_columns:
                cursor.execute(
                    "ALTER TABLE options_positions ADD COLUMN roll_count INTEGER DEFAULT 0"
                )
            if "dte_alert_sent" not in options_columns:
                cursor.execute(
                    "ALTER TABLE options_positions ADD COLUMN dte_alert_sent INTEGER DEFAULT 0"
                )

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

    # Allowed column names for update_bot_state — whitelist prevents SQL injection
    # via untrusted kwargs keys. Must match the bot_state schema in _init_db().
    _BOT_STATE_COLUMNS = frozenset({
        "is_paused",
        "pause_until",
        "current_position_shares",
        "current_position_entry_price",
        "current_position_date",
        "last_open_price",
        "last_open_price_date",
        "total_trades",
        "winning_trades",
        "total_pnl",
        "trading_mode",
        "wheel_mode_enabled",
        "updated_at",
    })

    def update_bot_state(self, **kwargs):
        """Update bot state fields.

        Raises:
            ValueError: If any kwarg key is not in _BOT_STATE_COLUMNS.
                Prevents SQL injection via untrusted column names.
        """
        if not kwargs:
            return

        # Whitelist check — reject any unknown column names BEFORE they reach SQL
        unknown = set(kwargs.keys()) - self._BOT_STATE_COLUMNS
        if unknown:
            raise ValueError(
                f"update_bot_state received unknown columns: {sorted(unknown)}. "
                f"Allowed: {sorted(self._BOT_STATE_COLUMNS)}"
            )

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

    def get_trading_mode(self) -> str:
        """Get persisted trading mode.

        Returns:
            'paper' or 'live' - defaults to 'paper' if not set
        """
        state = self.get_bot_state()
        return state.get("trading_mode", "paper") or "paper"

    def set_trading_mode(self, mode: str):
        """Persist trading mode change.

        Args:
            mode: 'paper' or 'live'
        """
        if mode not in ("paper", "live"):
            raise ValueError(f"Invalid mode: {mode}. Must be 'paper' or 'live'")
        self.update_bot_state(trading_mode=mode)
        self.log_event("MODE_CHANGE", new_mode=mode)

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
                    safe_json_dumps(details) if details else None,
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

    def get_all_strategy_params(self) -> Dict[str, Any]:
        """Get all saved strategy parameters.

        Returns:
            Dict mapping param_name to param_value (float, bool, or str)
        """
        # Map of params that should be booleans (SQLite stores as 0/1)
        bool_params = {
            "mean_reversion_enabled",
            "ten_am_dump_enabled",
            "crash_day_enabled",
            "pump_day_enabled",
            "btc_overnight_filter_enabled",
        }

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT param_name, param_value FROM strategy_params")
            result = {}
            for row in cursor.fetchall():
                name = row["param_name"]
                value = row["param_value"]
                # Convert 0/1 back to bool for boolean params
                if name in bool_params and isinstance(value, (int, float)):
                    value = bool(value)
                result[name] = value
            return result

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
                        safe_json_dumps(current_params),
                        backtest_return,
                        safe_json_dumps(recommendations),
                        safe_json_dumps(watch_items),
                        safe_json_dumps(market_regime) if market_regime else None,
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

    # ==================== Wheel Strategy Operations ====================

    def create_wheel_cycle(self) -> int:
        """Create a new wheel cycle in CASH state.

        Enforces single-active-cycle invariant: raises ValueError if an unclosed
        cycle already exists (T-02-08 threat mitigation).

        Returns:
            Integer cycle_id of the newly created cycle.

        Raises:
            ValueError: If an active (unclosed) cycle already exists.
        """
        now = get_et_now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM wheel_cycles WHERE closed_at IS NULL LIMIT 1"
            )
            existing = cursor.fetchone()
            if existing:
                raise ValueError(
                    f"Active cycle already exists (id={existing['id']}). "
                    "Close it before creating a new cycle."
                )
            cursor.execute(
                """
                INSERT INTO wheel_cycles
                    (state, underlying, opened_at, created_at, updated_at)
                VALUES ('CASH', 'IBIT', ?, ?, ?)
                """,
                (now, now, now),
            )
            return cursor.lastrowid

    def get_active_cycle(self) -> Optional[Dict[str, Any]]:
        """Get the current active (unclosed) wheel cycle.

        Returns:
            Dict with all wheel_cycles columns, or None if no active cycle.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM wheel_cycles WHERE closed_at IS NULL ORDER BY id DESC LIMIT 1"
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_cycle_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get wheel cycle history, most recent first.

        Args:
            limit: Maximum number of cycles to return.

        Returns:
            List of dicts for all cycles (including closed), ordered by id DESC.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM wheel_cycles ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def transition_wheel_state(
        self,
        cycle_id: int,
        next_state: WheelState,
        reason: str,
        **updates,
    ) -> None:
        """Transition the active wheel cycle to a new state.

        Validates the transition via the state machine, updates the database
        row, and logs the event. Optionally accepts extra keyword arguments
        to update additional cycle fields (e.g., put_strike, cost_basis).

        All state changes must go through this method — never direct SQL UPDATE
        of the state column (T-02-05 threat mitigation).

        Args:
            cycle_id: ID of the cycle to transition.
            next_state: The target WheelState.
            reason: Human-readable reason for the transition (logged).
            **updates: Additional wheel_cycles columns to set (e.g.,
                       put_strike=48.0, put_premium_received=2.50,
                       shares_held=100, cost_basis=47.50).

        Raises:
            ValueError: If cycle_id doesn't match the active cycle, or if the
                        transition is invalid per the state machine.
        """
        cycle = self.get_active_cycle()
        if cycle is None or cycle["id"] != cycle_id:
            # Check history in case it's already closed
            raise ValueError(
                f"No active cycle with id={cycle_id}. "
                "Only the active (unclosed) cycle can be transitioned."
            )

        current_state = WheelState(cycle["state"])
        # Raises ValueError on invalid transition (T-02-05)
        validate_transition(current_state, next_state)

        now = get_et_now().isoformat()

        # Build SET clause from fixed fields + caller-supplied updates
        set_fields: Dict[str, Any] = {
            "state": next_state.value,
            "updated_at": now,
        }
        # Auto-set closed_at when transitioning to CASH (unless caller overrides)
        if next_state == WheelState.CASH and "closed_at" not in updates:
            set_fields["closed_at"] = now

        set_fields.update(updates)

        # Keys come from controlled caller code, not user input (T-02-06)
        assignments = ", ".join(f"{k} = ?" for k in set_fields.keys())
        values = list(set_fields.values()) + [cycle_id]

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE wheel_cycles SET {assignments} WHERE id = ?",
                values,
            )

        self.log_event(
            "INFO",
            "wheel_transition",
            details={
                "cycle_id": cycle_id,
                "from_state": current_state.value,
                "to_state": next_state.value,
                "reason": reason,
            },
        )

    def open_wheel_position(
        self,
        cycle_id: int,
        symbol: str,
        option_type: str,
        strike: float,
        expiry_date: str,
        dte_at_entry: int,
        premium_received: float,
        quantity: int = 1,
        delta: Optional[float] = None,
        gamma: Optional[float] = None,
        theta: Optional[float] = None,
        vega: Optional[float] = None,
        iv: Optional[float] = None,
    ) -> int:
        """Record a new options position within a wheel cycle.

        Greeks are stored as individual columns (not JSON) to allow future
        SQL-based analysis (DB-01 requirement).

        Args:
            cycle_id: The wheel cycle this position belongs to.
            symbol: OCC option symbol (e.g., "IBIT260515P00048000").
            option_type: "PUT" or "CALL" — validated before INSERT (T-02-07).
            strike: Strike price of the option.
            expiry_date: Expiry date string (YYYY-MM-DD).
            dte_at_entry: Days to expiration at the time of entry.
            premium_received: Premium collected per share (multiply by 100 for total).
            quantity: Number of contracts (default 1).
            delta: Delta greek at entry (optional).
            gamma: Gamma greek at entry (optional).
            theta: Theta greek at entry (optional).
            vega: Vega greek at entry (optional).
            iv: Implied volatility at entry (optional).

        Returns:
            Integer position_id of the newly created position.

        Raises:
            ValueError: If option_type is not "PUT" or "CALL".
            ValueError: If cycle_id does not exist in wheel_cycles.
        """
        if option_type not in ("PUT", "CALL"):
            raise ValueError(
                f"Invalid option_type '{option_type}'. Must be 'PUT' or 'CALL'."
            )

        now = get_et_now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Validate cycle_id exists (T-02-07)
            cursor.execute(
                "SELECT id FROM wheel_cycles WHERE id = ?",
                (cycle_id,),
            )
            if cursor.fetchone() is None:
                raise ValueError(
                    f"cycle_id={cycle_id} does not exist in wheel_cycles."
                )

            cursor.execute(
                """
                INSERT INTO options_positions (
                    cycle_id, symbol, option_type, strike, expiry_date,
                    dte_at_entry, quantity, premium_received,
                    delta, gamma, theta, vega, iv,
                    status, opened_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)
                """,
                (
                    cycle_id, symbol, option_type, strike, expiry_date,
                    dte_at_entry, quantity, premium_received,
                    delta, gamma, theta, vega, iv,
                    now, now, now,
                ),
            )
            return cursor.lastrowid

    def close_wheel_position(self, position_id: int, close_premium: float) -> None:
        """Mark an options position as CLOSED with a closing premium.

        Positions are NEVER deleted — they are marked CLOSED for audit trail.
        (Locked decision per CONTEXT.md.)

        Args:
            position_id: ID of the options_positions row to close.
            close_premium: The premium paid to close (buy back) the position.
        """
        now = get_et_now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE options_positions
                SET status = 'CLOSED',
                    close_premium = ?,
                    closed_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (close_premium, now, now, position_id),
            )

    # ==================== Atomic Wheel Operations ====================
    # These methods group multiple wheel-related writes into a single
    # transaction so that a crash mid-flow cannot leave an orphaned cycle,
    # a position without a cycle, or a cycle in an inconsistent state.
    # Each method opens ONE connection and performs all writes inside it.

    def open_short_put_cycle(
        self,
        symbol: str,
        strike: float,
        premium_received: float,
        expiry_date: str,
        dte_at_entry: int,
        delta: Optional[float] = None,
        gamma: Optional[float] = None,
        theta: Optional[float] = None,
        vega: Optional[float] = None,
        iv: Optional[float] = None,
        quantity: int = 1,
    ) -> Tuple[int, int]:
        """Atomically create a cycle, transition to SHORT_PUT, and open the put position.

        Groups create_wheel_cycle + transition_wheel_state + open_wheel_position
        into a single transaction so any failure rolls everything back. Prevents
        orphaned cycles that would block future operations.

        Returns:
            Tuple of (cycle_id, position_id).

        Raises:
            ValueError: If an active cycle already exists.
        """
        now = get_et_now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Ensure no active cycle exists (T-02-08)
            cursor.execute(
                "SELECT id FROM wheel_cycles WHERE closed_at IS NULL LIMIT 1"
            )
            if cursor.fetchone():
                raise ValueError(
                    "Active cycle already exists. Close it before opening a new one."
                )

            # 2. Create cycle in CASH then transition to SHORT_PUT
            cursor.execute(
                """
                INSERT INTO wheel_cycles
                    (state, underlying, opened_at, created_at, updated_at)
                VALUES ('CASH', 'IBIT', ?, ?, ?)
                """,
                (now, now, now),
            )
            cycle_id = cursor.lastrowid

            # Validate the transition via the state machine
            validate_transition(WheelState.CASH, WheelState.SHORT_PUT)

            cursor.execute(
                """
                UPDATE wheel_cycles
                SET state = ?, put_strike = ?, put_premium_received = ?,
                    put_expiry_date = ?, updated_at = ?
                WHERE id = ?
                """,
                (WheelState.SHORT_PUT.value, strike, premium_received,
                 expiry_date, now, cycle_id),
            )

            # 3. Open the PUT position row
            cursor.execute(
                """
                INSERT INTO options_positions (
                    cycle_id, symbol, option_type, strike, expiry_date,
                    dte_at_entry, quantity, premium_received,
                    delta, gamma, theta, vega, iv,
                    status, opened_at, created_at, updated_at
                ) VALUES (?, ?, 'PUT', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)
                """,
                (cycle_id, symbol, strike, expiry_date, dte_at_entry,
                 quantity, premium_received, delta, gamma, theta, vega, iv,
                 now, now, now),
            )
            position_id = cursor.lastrowid

        # 4. Log event AFTER commit — non-critical, can be a separate transaction
        self.log_event(
            "INFO",
            "wheel_transition",
            details={
                "cycle_id": cycle_id,
                "from_state": "CASH",
                "to_state": "SHORT_PUT",
                "reason": "put_sold",
            },
        )

        return cycle_id, position_id

    def process_put_assignment(
        self, cycle_id: int, position_id: int, shares_held: int = 100
    ) -> float:
        """Atomically transition cycle SHORT_PUT -> HOLDING_SHARES and close the put.

        Groups transition_wheel_state + close_wheel_position into one transaction.
        Cost basis is computed from the existing cycle's put_strike and
        put_premium_received.

        Returns:
            The computed cost basis per share (for caller's notification use).

        Raises:
            ValueError: If the cycle is not in SHORT_PUT state or doesn't exist.
        """
        now = get_et_now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Read the cycle and validate state
            cursor.execute(
                "SELECT * FROM wheel_cycles WHERE id = ?", (cycle_id,)
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError(f"Cycle {cycle_id} does not exist")
            cycle = dict(row)
            if cycle["state"] != WheelState.SHORT_PUT.value:
                raise ValueError(
                    f"Cycle {cycle_id} is in state {cycle['state']}, "
                    f"expected SHORT_PUT"
                )

            validate_transition(WheelState.SHORT_PUT, WheelState.HOLDING_SHARES)

            cost_basis = cycle["put_strike"] - cycle["put_premium_received"]

            # Transition cycle
            cursor.execute(
                """
                UPDATE wheel_cycles
                SET state = ?, shares_held = ?, cost_basis = ?, updated_at = ?
                WHERE id = ?
                """,
                (WheelState.HOLDING_SHARES.value, shares_held, cost_basis, now, cycle_id),
            )

            # Close the put position
            cursor.execute(
                """
                UPDATE options_positions
                SET status = 'CLOSED', close_premium = 0.0, closed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, position_id),
            )

        self.log_event(
            "INFO",
            "wheel_transition",
            details={
                "cycle_id": cycle_id,
                "from_state": "SHORT_PUT",
                "to_state": "HOLDING_SHARES",
                "reason": "put_assigned",
                "cost_basis": cost_basis,
            },
        )

        return cost_basis

    def process_put_otm_expiry(
        self, cycle_id: int, position_id: int
    ) -> float:
        """Atomically transition cycle SHORT_PUT -> CASH and close the expired put.

        Groups transition_wheel_state + close_wheel_position into one transaction.
        Realized P&L equals the full put premium (100% of premium kept).

        Returns:
            The realized P&L (put_premium_received * 100).

        Raises:
            ValueError: If the cycle is not in SHORT_PUT state or doesn't exist.
        """
        now = get_et_now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                "SELECT * FROM wheel_cycles WHERE id = ?", (cycle_id,)
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError(f"Cycle {cycle_id} does not exist")
            cycle = dict(row)
            if cycle["state"] != WheelState.SHORT_PUT.value:
                raise ValueError(
                    f"Cycle {cycle_id} is in state {cycle['state']}, "
                    f"expected SHORT_PUT"
                )

            validate_transition(WheelState.SHORT_PUT, WheelState.CASH)

            realized_pnl = (cycle["put_premium_received"] or 0.0) * 100

            # Transition cycle to CASH (cycle closes)
            cursor.execute(
                """
                UPDATE wheel_cycles
                SET state = ?, realized_pnl = ?, closed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (WheelState.CASH.value, realized_pnl, now, now, cycle_id),
            )

            # Close the put position
            cursor.execute(
                """
                UPDATE options_positions
                SET status = 'CLOSED', close_premium = 0.0, closed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, position_id),
            )

        self.log_event(
            "INFO",
            "wheel_transition",
            details={
                "cycle_id": cycle_id,
                "from_state": "SHORT_PUT",
                "to_state": "CASH",
                "reason": "put_expired_otm",
                "realized_pnl": realized_pnl,
            },
        )

        return realized_pnl

    def get_cycle_positions(self, cycle_id: int) -> List[Dict[str, Any]]:
        """Get all options positions for a given wheel cycle.

        Args:
            cycle_id: The wheel cycle ID to query.

        Returns:
            List of dicts for all positions in the cycle, ordered by id ASC.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM options_positions WHERE cycle_id = ? ORDER BY id ASC",
                (cycle_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def increment_roll_count(self, position_id: int) -> None:
        """Increment the roll_count for an options position by 1.

        Called each time a position is rolled to a later expiry.

        Args:
            position_id: ID of the options_positions row to update.
        """
        now = get_et_now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE options_positions SET roll_count = roll_count + 1, updated_at = ? WHERE id = ?",
                (now, position_id),
            )

    def mark_dte_alert_sent(self, position_id: int) -> None:
        """Mark that the 21-DTE alert has been sent for this position.

        Idempotent — safe to call multiple times; dte_alert_sent stays at 1.

        Args:
            position_id: ID of the options_positions row to update.
        """
        now = get_et_now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE options_positions SET dte_alert_sent = 1, updated_at = ? WHERE id = ?",
                (now, position_id),
            )

    def set_roll_count(self, position_id: int, count: int) -> None:
        """Set the roll_count on a position to a specific value.

        Used after opening a rolled position to carry forward the roll history
        (new_position.roll_count = old_position.roll_count + 1).

        Args:
            position_id: ID of the options_positions row to update.
            count: The exact roll_count value to set.
        """
        now = get_et_now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE options_positions SET roll_count = ?, updated_at = ? WHERE id = ?",
                (count, now, position_id),
            )

    def get_open_position_for_cycle(self, cycle_id: int) -> Optional[Dict[str, Any]]:
        """Get the most recent OPEN options position for a wheel cycle.

        Returns the latest open position (by id DESC) or None if no open
        position exists. Used by monitoring checks to find the active contract.

        Args:
            cycle_id: The wheel cycle ID to query.

        Returns:
            Dict of the open options_positions row, or None if no OPEN row.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM options_positions WHERE cycle_id = ? AND status = 'OPEN' ORDER BY id DESC LIMIT 1",
                (cycle_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def compute_cycle_pnl(
        self, cycle: Dict[str, Any], current_price: float = 0.0
    ) -> Dict[str, float]:
        """Compute P&L for a wheel cycle from cycle data.

        P&L is computed on READ, never stored — avoids stale data and keeps
        the database as a source of truth for raw values only.

        For HOLDING_SHARES or COVERED_CALL states, unrealized P&L is
        (current_price - cost_basis) * shares_held.

        Args:
            cycle: A cycle dict as returned by get_active_cycle() or
                   get_cycle_history().
            current_price: Current market price per share (used for unrealized).

        Returns:
            Dict with keys:
                - "unrealized_pnl": Float, 0.0 if not holding shares.
                - "realized_pnl": Float, from cycle["realized_pnl"] or 0.0.
        """
        state = cycle.get("state", "")
        if state in ("HOLDING_SHARES", "COVERED_CALL"):
            shares = cycle.get("shares_held") or 100
            cost_basis = cycle.get("cost_basis") or 0.0
            unrealized = (current_price - cost_basis) * shares
        else:
            unrealized = 0.0

        realized = cycle.get("realized_pnl") or 0.0
        return {"unrealized_pnl": unrealized, "realized_pnl": realized}


# Singleton instance
_db_instance: Optional[Database] = None


def get_database(db_path: Optional[Path] = None) -> Database:
    """Get or create the database singleton.

    Args:
        db_path: Path to the database file. Honored only on the FIRST call.
                 Subsequent calls ignore this argument and return the existing
                 instance. If you need a different database (e.g. for tests),
                 call reset_database() first.

    Raises:
        RuntimeError: If db_path is provided but differs from the path used
                      to construct the existing singleton. This catches silent
                      bugs where a caller expects a different database.
    """
    global _db_instance
    if _db_instance is None:
        _db_instance = Database(db_path)
        return _db_instance

    # Singleton already exists — warn if caller passed a different path
    if db_path is not None and Path(db_path) != Path(_db_instance.db_path):
        raise RuntimeError(
            f"get_database() called with db_path={db_path!r}, but the "
            f"singleton was already initialized with db_path={_db_instance.db_path!r}. "
            "Call reset_database() first to override."
        )
    return _db_instance


def reset_database() -> None:
    """Reset the database singleton. Intended for test isolation only.

    After calling this, the next get_database() call will construct a fresh
    instance — including a new SQLite connection pool. Production code should
    never call this.
    """
    global _db_instance
    _db_instance = None
