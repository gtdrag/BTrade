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
"""

import os

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
