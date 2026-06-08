# Coding Conventions

**Analysis Date:** 2026-03-18

## Naming Patterns

**Files:**
- Snake case throughout: `smart_strategy.py`, `trading_bot.py`, `data_providers.py`
- Suffixes indicate purpose:
  - `_mixin.py`: Mixin classes that combine functionality (e.g., `execution_mixin.py`, `positions_mixin.py`)
  - No special prefixes
- Organized by functionality in subdirectories: `src/telegram/`, `src/trading_bot/`, `src/strategy_review/`

**Functions:**
- Snake case: `get_today_signal()`, `execute_signal()`, `record_trade_entry()`
- Prefixes indicate privacy/purpose:
  - `_private_method()`: Internal methods (leading underscore)
  - `get_*`: Accessor methods returning values
  - `mark_*`: State-modifying methods (`mark_crash_day_traded()`)
  - `check_*`: Boolean validation methods
- Async functions prefixed with `async def` keyword

**Variables:**
- Snake case: `available_cash`, `entry_price`, `trade_id`
- Protected attributes use single leading underscore: `self._crash_day_traded_today`, `self._ibit_data`
- Constants use UPPER_SNAKE_CASE: `ET` (timezone), `DEFAULT_DB_PATH`
- Loop variables: single letters (i, n, t) acceptable in comprehensions

**Types:**
- Dataclasses with `@dataclass` decorator for structured data:
  - `Signal` (Enum for trading signals)
  - `PositionAction` (Enum for position management)
  - `TodaySignal` (dataclass with signal details)
  - `CrashDayStatus`, `PumpDayStatus` (status dataclasses)
  - `TradeResult` (execution result)
  - `BotConfig`, `StrategyConfig` (configuration dataclasses)
- Enums inherit from `Enum` with `.value` properties for string representation

## Code Style

**Formatting:**
- Line length: 100 characters (configured in `pyproject.toml` via ruff)
- Quote style: Double quotes for all strings (ruff config: `quote-style = "double"`)
- Indentation: 4 spaces (no tabs)
- Trailing commas in multi-line structures enabled (`skip-magic-trailing-comma = false`)

**Linting:**
- Tool: ruff (Python formatter and linter)
- Config: `pyproject.toml` [tool.ruff] section
- Key rules enforced:
  - E: pycodestyle errors
  - F: Pyflakes (unused imports, undefined names)
  - I: isort (import ordering)
  - N: pep8-naming (naming conventions)
  - W: pycodestyle warnings
  - UP: pyupgrade (modern Python syntax)
- Line length violations (E501) disabled - formatter handles it
- Test files allow imports after sys.path manipulation (E402)

## Import Organization

**Order:**
1. Standard library imports (`datetime`, `logging`, `os`, `json`, etc.)
2. Third-party imports (`pytest`, `pandas`, `numpy`, `dataclasses`, etc.)
3. Local imports from `.` or `..` (relative imports)

**Example from `src/smart_strategy.py`:**
```python
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .data_providers import AlpacaProvider, MarketDataManager, create_data_manager
from .database import Database, get_database
from .error_alerting import AlertSeverity, alert_error
```

**Path Aliases:**
- No aliases currently used; prefer explicit relative imports
- All imports are absolute or relative (e.g., `from src.database import Database`)

## Error Handling

**Patterns:**
- Try-except blocks catch specific exceptions when possible
- Failed operations logged with `logger.warning()` (not error-level unless critical)
- Graceful degradation: return safe defaults on failure
- Example from `src/smart_strategy.py`:
  ```python
  try:
      quote = self._data_manager.get_quote("IBIT")
      # Process quote
  except Exception as e:
      logger.warning(f"Failed to get crash day status: {e}")
      alert_error(
          AlertSeverity.WARNING,
          f"Failed to get crash day status: {e}",
          category="crash_day_status",
      )
      return CrashDayStatus(
          is_triggered=False,
          current_drop_pct=0,
          ibit_open=0,
          ibit_current=0,
          already_traded_today=self._crash_day_traded_today,
      )
  ```

**Error Alerting:**
- Use `alert_error()` from `src/error_alerting.py` for production alerts
- Severity levels: `AlertSeverity.WARNING`, `AlertSeverity.CRITICAL`
- Include context dict with relevant data for debugging

## Logging

**Framework:** Python's `logging` module

**Patterns:**
- All modules get module-level logger: `logger = logging.getLogger(__name__)`
- Log levels:
  - `logger.warning()`: Expected issues (data unavailable, API errors)
  - `logger.error()`: Unexpected errors (should alert via Telegram)
  - `logger.info()`: Important state changes (trades executed)
- Include context in f-strings: `logger.warning(f"Failed to get data: {e}")`
- No logging in tests (pytest captures automatically)

**When to Log:**
- Entry/exit of critical functions (trading execution)
- Unexpected data states
- API failures (but don't log full error responses with PII)
- Performance milestones

**Never Log:**
- Full error responses (can contain secrets)
- Raw API responses with auth tokens
- Internal enum values (use `.value` for string representation)

## Comments

**When to Comment:**
- Explain WHY, not WHAT. The code shows what it does
- Magic numbers and thresholds:
  ```python
  crash_day_threshold: float = -1.5  # Buy SBIT when IBIT drops this much intraday
  ```
- Non-obvious algorithm choices:
  ```python
  # BTC overnight filter dramatically improves win rate: 84% vs 17%
  if self.config.btc_overnight_filter_enabled and btc_overnight:
  ```
- State machine transitions:
  ```python
  # Reset crash day flag if it's a new day
  if self._crash_day_trade_date != today:
  ```

**JSDoc/TSDoc:**
- Use triple-quoted docstrings for all modules, classes, and public methods
- Format: Google-style docstrings
- Example:
  ```python
  def get_today_signal(
      self,
      check_crash_day: bool = True,
      check_pump_day: bool = True,
      current_positions: Optional[Dict[str, Any]] = None,
  ) -> TodaySignal:
      """
      Determine today's trading signal.

      Args:
          check_crash_day: Whether to check for crash day signal
          check_pump_day: Whether to check for pump day signal
          current_positions: Dict of current positions {etf: {shares, entry_price}}

      Returns:
          TodaySignal with recommended action for today

      Priority order:
      1. Position management (if holding wrong-way position)
      2. 10 AM Dump (time-based)
      3. Mean Reversion (previous day drop + BTC filter)
      4. Crash Day (intraday drop)
      5. Pump Day (intraday pump)
      6. Cash (default)
      """
  ```

## Function Design

**Size:**
- Target: 30-50 lines per function (max ~100)
- Larger functions should be split by logical concern
- Mixins: methods 20-40 lines on average

**Parameters:**
- Type hints required: `def execute_signal(self, signal: TodaySignal, skip_approval: bool = False) -> TradeResult:`
- Default values for optional parameters
- Use dataclasses for complex parameter sets (e.g., `BotConfig`, `StrategyConfig`)
- Max 5 positional parameters; use config objects if more needed

**Return Values:**
- Type hints required
- Single return value or tuple of values
- Dataclasses for structured returns: `TradeResult`, `CrashDayStatus`
- Use `Optional[T]` for nullable values
- Example:
  ```python
  def get_open_price(self, date: date) -> Optional[float]:
      """Returns price or None if not found."""
  ```

## Module Design

**Exports:**
- Public API at module level - no leading underscores
- Private helpers prefixed with `_`
- No `__all__` lists currently used (explicit imports preferred)
- Example from `src/smart_strategy.py`:
  ```python
  # Public (exported)
  class Signal(Enum):
  class PositionAction(Enum):
  @dataclass class TodaySignal:
  class SmartStrategy:

  # Private (internal helpers)
  def _some_helper():  # Not at module level currently
  ```

**Barrel Files:**
- Used in `src/telegram/` and `src/trading_bot/` packages
- `__init__.py` imports key classes for package-level access
- Example from `src/trading_bot/__init__.py`:
  ```python
  from .config import ApprovalMode, BotConfig, TradingMode, TradeResult
  from .core import TradingBot
  ```
- Allows: `from src.trading_bot import TradingBot, BotConfig`

## Specific Patterns

**Dataclass Defaults:**
- Use `field(default_factory=...)` for mutable defaults:
  ```python
  crash_day_check_times: List[str] = field(
      default_factory=lambda: ["09:45", "10:00", "10:15", ...]
  )
  ```

**Nullable Fields:**
- Use `Optional[T]` with default `None`:
  ```python
  trigger_time: Optional[str] = None
  ```

**Enum String Values:**
- Always use `.value` for string representation in logs/JSON:
  ```python
  signal.signal.value  # Returns "cash", "crash_day", etc.
  ```

**JSON Serialization:**
- Custom `NumpyJSONEncoder` in `src/database.py` handles numpy types
- Use `safe_json_dumps()` for numpy-safe serialization

**Async/Sync Bridging:**
- Use `run_async_from_sync()` from `src.async_utils` in sync contexts
- Never use bare `asyncio.run()` - it can fail in complex threading scenarios
- Import and use consistently:
  ```python
  from src.async_utils import run_async_from_sync as run_async
  result = run_async(some_async_function())
  ```

**Database Singleton:**
- Always use `get_database()` factory, never instantiate:
  ```python
  from src.database import get_database
  db = get_database()  # Returns singleton
  ```

**Configuration:**
- Immutable dataclasses for all config: `BotConfig`, `StrategyConfig`, etc.
- Config objects passed as constructor parameters, not globals
- All defaults hardcoded in dataclass field defaults

**Testing Safety:**
- Tests that mock dependencies create fresh instances:
  ```python
  @pytest.fixture
  def mock_db(tmp_path):
      db_path = tmp_path / "test_trades.db"
      return Database(db_path)
  ```
- Mocks verify critical behaviors (duplicate prevention, approval handling)

---

*Convention analysis: 2026-03-18*
