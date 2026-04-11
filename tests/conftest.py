"""
Shared pytest configuration for the BTrade test suite.

Currently provides:
- Autouse cleanup fixture that resets the Database singleton and
  clears the DATABASE_PATH environment variable between tests. This
  addresses adversarial review TESTS.md [CRITICAL] CR-01 — the
  test_wheel_strategy.py `db` fixture used to set DATABASE_PATH but
  never reset it or the _db_instance singleton, so subsequent tests
  that called get_database() could reach into a stale tmp_path or
  observe the wrong instance under test ordering changes.
- `make_telegram_bot()` shared factory — addresses TESTS.md [HIGH]
  HI-01 and [LOW] LO-02 (four drift-prone copies of the same helper).
"""

import os
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _reset_database_singleton_and_env():
    """Ensure DATABASE_PATH env and _db_instance singleton are clean per test.

    This fixture runs automatically for every test. It snapshots the
    current DATABASE_PATH (if any), lets the test run, then restores
    the original value and resets the _db_instance singleton so the
    next test sees a fresh slate.
    """
    original_db_path = os.environ.get("DATABASE_PATH")
    try:
        yield
    finally:
        # Reset the singleton so the next test cannot observe a stale
        # Database instance from a prior tmp_path.
        import src.database

        src.database.reset_database()

        # Restore DATABASE_PATH to whatever it was before the test ran
        if original_db_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = original_db_path


def make_telegram_bot(token: str = "fake_token", chat_id: str = "12345"):
    """Build a real TelegramBot instance via its actual __init__ (HI-01/LO-02).

    Previously, each test file (test_put_approval, test_call_approval,
    test_profit_management) had its own copy of a `_make_telegram_bot()`
    helper that used `TelegramBot.__new__(TelegramBot)` to skip __init__
    and then manually poked ~20 instance attributes. Those copies had
    already drifted: test_profit_management set `_roll_approval` but
    test_put_approval / test_call_approval did not, and any new attribute
    added to `TelegramBot.__init__` had to be backported by hand to every
    copy — otherwise tests got false-negative AttributeErrors under rare
    code paths.

    Calling the real `__init__` with explicit token/chat_id avoids the
    "TELEGRAM_BOT_TOKEN not set" ValueError without needing to bypass the
    constructor, so every attribute set by `__init__` exists with the
    same type and default the production bot sees. `_app` is replaced
    with a MagicMock after construction because tests need to capture
    `bot._app.bot.send_message(...)` calls.
    """
    from src.telegram.bot import TelegramBot

    bot = TelegramBot(
        token=token,
        chat_id=chat_id,
        approval_timeout_minutes=10,
    )
    # The real __init__ leaves _app=None; tests need a mock so
    # `bot._app.bot.send_message(...)` can be asserted against.
    bot._app = MagicMock()
    bot._app.bot.send_message = AsyncMock()
    return bot
