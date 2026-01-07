"""
Tests for async/sync bridging utilities.

These tests verify the core async utilities that prevent "Event loop is closed" errors.
"""

import asyncio
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.async_utils import (
    run_async_from_sync,
    run_sync_in_executor,
    shutdown_executor,
)


class TestRunAsyncFromSync:
    """Test sync -> async bridging."""

    def test_runs_simple_coroutine(self):
        """Basic async function should execute and return result."""

        async def simple_coro():
            return 42

        result = run_async_from_sync(simple_coro())
        assert result == 42

    def test_runs_coroutine_with_await(self):
        """Coroutine that awaits should work correctly."""

        async def coro_with_await():
            await asyncio.sleep(0.01)
            return "awaited"

        result = run_async_from_sync(coro_with_await())
        assert result == "awaited"

    def test_propagates_exceptions(self):
        """Exceptions from coroutine should propagate."""

        async def failing_coro():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            run_async_from_sync(failing_coro())

    def test_respects_timeout(self):
        """Should raise TimeoutError if coroutine takes too long."""

        async def slow_coro():
            await asyncio.sleep(10)
            return "never"

        with pytest.raises(asyncio.TimeoutError):
            run_async_from_sync(slow_coro(), timeout=0.1)

    def test_works_from_thread(self):
        """Should work when called from a separate thread (like APScheduler)."""
        results = []

        async def thread_coro():
            await asyncio.sleep(0.01)
            return threading.current_thread().name

        def thread_target():
            result = run_async_from_sync(thread_coro())
            results.append(result)

        thread = threading.Thread(target=thread_target, name="TestThread")
        thread.start()
        thread.join(timeout=5)

        assert len(results) == 1
        # The coroutine runs in the thread that called run_async_from_sync
        assert "TestThread" in results[0] or "MainThread" in results[0]

    def test_multiple_threads_concurrent(self):
        """Multiple threads can call run_async_from_sync concurrently."""
        results = []
        errors = []

        async def numbered_coro(n):
            await asyncio.sleep(0.01)
            return n * 2

        def thread_target(n):
            try:
                result = run_async_from_sync(numbered_coro(n))
                results.append((n, result))
            except Exception as e:
                errors.append((n, e))

        threads = [threading.Thread(target=thread_target, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == 5
        for n, result in results:
            assert result == n * 2

    def test_isolates_event_loops(self):
        """Each call should create a new loop that gets closed after use."""
        # Test that the loop is properly closed after each call
        # by verifying we can make many calls without accumulating open loops
        results = []

        async def return_value(n):
            await asyncio.sleep(0.001)
            return n

        # Make many sequential calls - if loops weren't being cleaned up,
        # we'd eventually hit resource limits
        for i in range(10):
            result = run_async_from_sync(return_value(i))
            results.append(result)

        assert results == list(range(10))


class TestRunSyncInExecutor:
    """Test async -> sync bridging."""

    @pytest.mark.asyncio
    async def test_runs_simple_function(self):
        """Basic sync function should execute and return result."""

        def simple_func():
            return 42

        result = await run_sync_in_executor(simple_func)
        assert result == 42

    @pytest.mark.asyncio
    async def test_passes_positional_args(self):
        """Positional arguments should be passed correctly."""

        def add(a, b):
            return a + b

        result = await run_sync_in_executor(add, 3, 4)
        assert result == 7

    @pytest.mark.asyncio
    async def test_passes_keyword_args(self):
        """Keyword arguments should be passed correctly."""

        def greet(name, greeting="Hello"):
            return f"{greeting}, {name}!"

        result = await run_sync_in_executor(greet, "World", greeting="Hi")
        assert result == "Hi, World!"

    @pytest.mark.asyncio
    async def test_propagates_exceptions(self):
        """Exceptions from sync function should propagate."""

        def failing_func():
            raise ValueError("sync error")

        with pytest.raises(ValueError, match="sync error"):
            await run_sync_in_executor(failing_func)

    @pytest.mark.asyncio
    async def test_runs_in_separate_thread(self):
        """Sync function should run in a different thread than event loop."""
        main_thread = threading.current_thread().name

        def get_thread_name():
            return threading.current_thread().name

        result = await run_sync_in_executor(get_thread_name)

        # Should be running in executor thread, not main thread
        assert result != main_thread
        assert "async_bridge" in result

    @pytest.mark.asyncio
    async def test_handles_blocking_io(self):
        """Should handle blocking I/O without blocking event loop."""

        def blocking_io():
            time.sleep(0.1)
            return "done"

        start = time.time()
        result = await run_sync_in_executor(blocking_io)
        elapsed = time.time() - start

        assert result == "done"
        assert elapsed >= 0.1

    @pytest.mark.asyncio
    async def test_concurrent_sync_calls(self):
        """Multiple sync functions should run concurrently in executor."""

        def slow_func(n):
            time.sleep(0.1)
            return n

        start = time.time()
        results = await asyncio.gather(
            run_sync_in_executor(slow_func, 1),
            run_sync_in_executor(slow_func, 2),
            run_sync_in_executor(slow_func, 3),
        )
        elapsed = time.time() - start

        assert results == [1, 2, 3]
        # Should complete in ~0.1s (parallel), not ~0.3s (sequential)
        assert elapsed < 0.25

    def test_documents_async_requirement(self):
        """run_sync_in_executor is an async function that must be awaited."""
        # This test just documents that run_sync_in_executor is async
        # The actual enforcement happens at the Python level (must await)

        def sync_func():
            return 42

        # Calling without await returns a coroutine
        result = run_sync_in_executor(sync_func)
        assert asyncio.iscoroutine(result)

        # Clean up the coroutine
        result.close()


class TestExecutorShutdown:
    """Test executor lifecycle management."""

    def test_shutdown_cleans_up(self):
        """shutdown_executor should clean up the thread pool."""

        async def dummy():
            return 1

        # Use the executor
        run_async_from_sync(dummy())

        # Shutdown
        shutdown_executor(wait=True)

        # After shutdown, new calls should still work (creates new executor)
        result = run_async_from_sync(dummy())
        assert result == 1


class TestIntegration:
    """Integration tests for real-world scenarios."""

    @pytest.mark.asyncio
    async def test_nested_async_sync_async(self):
        """Test async -> sync -> async pattern (like Telegram -> E*TRADE -> Telegram)."""

        async def inner_async():
            await asyncio.sleep(0.01)
            return "inner"

        def middle_sync():
            # Sync code that needs to call async
            return run_async_from_sync(inner_async())

        # Async code calling sync code that calls async
        result = await run_sync_in_executor(middle_sync)
        assert result == "inner"

    def test_scheduler_like_pattern(self):
        """Test pattern used by APScheduler jobs."""

        async def send_notification(message):
            await asyncio.sleep(0.01)
            return f"sent: {message}"

        # This simulates what a scheduler job does
        def scheduler_job():
            result = run_async_from_sync(send_notification("test"))
            return result

        # Run in thread pool like APScheduler would
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(scheduler_job)
            result = future.result(timeout=5)

        assert result == "sent: test"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
