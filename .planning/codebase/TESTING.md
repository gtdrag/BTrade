# Testing Patterns

**Analysis Date:** 2026-03-18

## Test Framework

**Runner:**
- pytest [latest]
- Config: `pyproject.toml` [tool.pytest.ini_options]
- Async support: pytest-asyncio with `asyncio_mode = "auto"`

**Assertion Library:**
- pytest's built-in assertions (no special library needed)
- Simple `assert value == expected` pattern

**Run Commands:**
```bash
python -m pytest tests/ -v              # Run all tests
python -m pytest tests/ -k "crash"      # Run tests matching pattern
python -m pytest tests/ -v --tb=short   # Verbose with short tracebacks
```

**Configuration Details:**
- Test paths: `tests/`
- Test file pattern: `test_*.py`
- Test function pattern: `test_*`
- Deprecation warnings ignored
- Short traceback format (configured in pyproject.toml)

## Test File Organization

**Location:**
- Separate directory: `tests/` at project root
- Mirror source structure loosely (not strictly 1:1)
- Current test files:
  - `tests/test_smart_strategy.py` - Strategy signal generation
  - `tests/test_trading_bot.py` - Trade execution and position management
  - `tests/test_database.py` - Database persistence
  - `tests/test_integration_async.py` - Async/sync bridging integration
  - `tests/test_async_utils.py` - Async utility functions
  - `tests/test_backtester.py` - Historical backtesting
  - `tests/test_strategy.py` - Legacy strategy tests

**Naming:**
- Test files: `test_*.py` prefix
- Test classes: `Test*` prefix (e.g., `TestCriticalConfigDefaults`)
- Test methods: `test_*` prefix
- Descriptive names explaining what is tested

**Structure:**
```
tests/
├── __init__.py
├── test_smart_strategy.py      # 320 lines - strategy signal tests
├── test_trading_bot.py         # 356 lines - execution tests
├── test_database.py            # 236 lines - persistence tests
├── test_integration_async.py   # 398 lines - async/sync bridging
├── test_async_utils.py         # Utility tests
├── test_backtester.py          # Backtest tests
└── test_strategy.py            # Legacy tests
```

## Test Structure

**Suite Organization:**
```python
class TestCriticalConfigDefaults:
    """Test that critical config values are set correctly."""

    def test_crash_threshold_is_negative_1_5_percent(self):
        """CRITICAL: Crash threshold must be -1.5%."""
        config = StrategyConfig()
        assert config.crash_day_threshold == -1.5, "Crash threshold should be -1.5%"
```

**Patterns Observed:**

1. **Grouping by concern**: Tests grouped in classes by topic
   - `TestCriticalConfigDefaults` - Configuration validation
   - `TestSignalTypes` - Signal enum completeness
   - `TestExecuteSignalCashSignal` - Behavior per signal type
   - `TestDuplicatePrevention` - Critical safety checks

2. **Descriptive docstrings**: Every test has a docstring explaining its purpose
   ```python
   def test_blocks_duplicate_crash_day_trade(self, trading_bot):
       """Should block duplicate CRASH_DAY trades."""
   ```

3. **Fixtures for setup**: Pytest fixtures provide test dependencies
   ```python
   @pytest.fixture
   def mock_db(tmp_path):
       """Create test database."""
       db_path = tmp_path / "test_trades.db"
       return Database(db_path)
   ```

4. **Clear assertions**: Direct assertions with failure messages
   ```python
   assert result.success is True
   assert result.action == "NONE"
   assert result.etf == "CASH"
   ```

## Test Structure Examples

### Fixture Pattern (from test_trading_bot.py)
```python
@pytest.fixture
def mock_db(tmp_path):
    """Create test database."""
    db_path = tmp_path / "test_trades.db"
    return Database(db_path)


@pytest.fixture
def mock_client():
    """Create mock E*TRADE client."""
    client = MockETradeClient(initial_cash=10000)
    client.set_mock_price("SBIT", 50.0)
    client.set_mock_price("BITU", 50.0)
    client.set_mock_price("IBIT", 50.0)
    return client


@pytest.fixture
def trading_bot(mock_client, mock_db):
    """Create trading bot for testing."""
    config = BotConfig(
        mode=TradingMode.PAPER,
        approval_mode=ApprovalMode.AUTO_EXECUTE,
        account_id_key="test_account",
    )
    bot = TradingBot(
        config=config,
        client=mock_client,
        db=mock_db,
    )
    return bot
```

### Class-Based Tests (from test_trading_bot.py)
```python
class TestExecuteSignalCashSignal:
    """Test CASH signal handling."""

    def test_cash_signal_returns_no_trade(self, trading_bot):
        """CASH signal should return success with no trade."""
        signal = TodaySignal(
            signal=Signal.CASH,
            etf="CASH",
            reason="No trading conditions met",
        )

        result = trading_bot.execute_signal(signal)

        assert result.success is True
        assert result.action == "NONE"
        assert result.etf == "CASH"

    def test_cash_signal_does_not_place_order(self, trading_bot, mock_client):
        """CASH signal should not attempt to place any orders."""
        signal = TodaySignal(
            signal=Signal.CASH,
            etf="CASH",
            reason="No trading conditions met",
        )

        # Track if place_order was called
        mock_client.place_order = MagicMock()

        trading_bot.execute_signal(signal)

        mock_client.place_order.assert_not_called()
```

## Mocking

**Framework:** unittest.mock (MagicMock)

**Patterns:**
```python
# Create mock
mock_client.place_order = MagicMock()

# Verify not called
mock_client.place_order.assert_not_called()

# Verify called once with args
trading_bot.telegram.request_approval.assert_called_once()

# Mock with return value
trading_bot.telegram.request_approval = MagicMock(
    return_value=ApprovalResult.APPROVED
)
```

**What to Mock:**
- External APIs (E*TRADE client via `MockETradeClient`)
- Telegram bot (for approval testing)
- Database (use temp files in fixtures, not mocks)
- Time-dependent logic (use configuration overrides instead)

**What NOT to Mock:**
- Database operations (use real SQLite in temp file)
- Strategy signal generation (test with real data)
- Configuration objects (use real config dataclasses)
- Core business logic (test actual execution)

**Integration Test Approach (from test_integration_async.py):**
```python
def test_notifier_run_async_from_thread(self):
    """TelegramNotifier._run_async should work from a separate thread."""
    from src.telegram.notifier import TelegramNotifier

    notifier = TelegramNotifier(token=None, chat_id=None)
    results = []
    errors = []

    async def test_coro():
        await asyncio.sleep(0.01)
        return "from_thread"

    def thread_target():
        try:
            result = notifier._run_async(test_coro())
            results.append(result)
        except Exception as e:
            errors.append(e)

    thread = threading.Thread(target=thread_target)
    thread.start()
    thread.join(timeout=5)

    assert len(errors) == 0, f"Errors: {errors}"
    assert results == ["from_thread"]
```

## Fixtures and Factories

**Test Data:**
- No separate fixture files; fixtures live in test classes
- Database fixtures use `tmp_path` (pytest's temp directory)
- Mock clients inherit from real clients (e.g., `MockETradeClient`)

**Location:**
- All fixtures in `tests/test_*.py` files using `@pytest.fixture`
- Fixtures passed as parameters to test methods
- Scoped to individual test functions (function scope by default)

**Pattern:**
```python
@pytest.fixture
def db(self, tmp_path):
    """Create test database."""
    db_path = tmp_path / "test_trades.db"
    return Database(db_path)
```

## Coverage

**Requirements:** Not explicitly enforced

**View Coverage:**
```bash
python -m pytest tests/ --cov=src --cov-report=html
```

**Target Areas (observed in codebase):**
- Critical functions: 100% (signal generation, execution)
- Configuration validation: 100% (config defaults)
- Error paths: 80%+ (graceful degradation)
- Async utilities: 95%+ (integration bugs high priority)

## Test Types

**Unit Tests:**
- Scope: Individual methods and functions
- Approach: Isolate units with fixtures and mocks
- Example: `test_crash_threshold_is_negative_1_5_percent` - tests config default
- Strategy tests: `test_smart_strategy.py` validates signal generation logic
- Database tests: `test_database.py` validates CRUD operations with real SQLite

**Integration Tests:**
- Scope: Multiple components working together
- File: `tests/test_integration_async.py` (398 lines)
- Approach: Test actual runtime paths without mocking infrastructure
- Examples:
  - Async notifications from sync scheduler context
  - Multiple concurrent scheduler jobs
  - Error recovery and event loop cleanup
  - Real threading scenarios simulating APScheduler

**E2E Tests:**
- Framework: Not formally implemented
- Backtester serves as quasi-E2E: tests strategy across historical data
- File: `tests/test_backtester.py` - validates full strategy over time period

## Common Patterns

**Async Testing (from test_integration_async.py):**
```python
def test_notifier_run_async_from_sync_context(self):
    """TelegramNotifier._run_async should work from pure sync context."""
    from src.telegram.notifier import TelegramNotifier

    notifier = TelegramNotifier(token=None, chat_id=None)

    async def test_coro():
        await asyncio.sleep(0.01)
        return "success"

    result = notifier._run_async(test_coro())
    assert result == "success"
```

**Thread Safety Testing (from test_integration_async.py):**
```python
def test_notifier_multiple_concurrent_calls(self):
    """Multiple threads calling notifier simultaneously should work."""
    from src.telegram.notifier import TelegramNotifier

    notifier = TelegramNotifier(token=None, chat_id=None)
    results = []
    errors = []
    lock = threading.Lock()

    async def test_coro(n):
        await asyncio.sleep(0.01)
        return n * 2

    def thread_target(n):
        try:
            result = notifier._run_async(test_coro(n))
            with lock:
                results.append((n, result))
        except Exception as e:
            with lock:
                errors.append((n, str(e)))

    threads = [threading.Thread(target=thread_target, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert len(errors) == 0, f"Errors: {errors}"
    assert len(results) == 5
```

**Error Testing (from test_smart_strategy.py):**
```python
def test_allows_different_signal_types_if_no_position_conflict(self, trading_bot):
    """Different signal types should not be blocked by duplicate prevention."""
    crash_signal = TodaySignal(
        signal=Signal.CRASH_DAY,
        etf="SBIT",
        reason="IBIT down 2%",
    )

    result1 = trading_bot.execute_signal(crash_signal)
    assert result1.success is True

    # Reset position but keep duplicate tracking
    trading_bot._paper_positions.clear()
    trading_bot._paper_capital = 10000.0

    pump_signal = TodaySignal(
        signal=Signal.PUMP_DAY,
        etf="BITU",
        reason="IBIT up 2%",
    )

    result2 = trading_bot.execute_signal(pump_signal)
    assert result2.success is True
```

## Critical Test Focus Areas

**Safety-Critical (100% coverage required):**
- Duplicate trade prevention
- Configuration thresholds (crash day -1.5%, pump day +1.5%)
- Cutoff times (3:30 PM for crash/pump)
- skip_approval parameter handling
- Position reversal logic

**Regression Prevention (integration tests):**
- Async/sync bridging in scheduler -> Telegram flow
- Event loop handling under concurrent load
- Thread safety of database operations
- Error notification sending from sync context

**Test Organization Philosophy:**
- Tests document expected behavior (read as specification)
- Docstrings explain the WHY
- Fixtures provide clear setup/teardown
- Integration tests catch bugs that unit tests miss

---

*Testing analysis: 2026-03-18*
