"""
Unified async/sync bridging utilities.

This module provides safe, consistent utilities for crossing async/sync boundaries.
All async/sync bridges in the codebase should use these functions.

There are exactly TWO patterns needed:

1. SYNC -> ASYNC: `run_async_from_sync(coro)`
   - Use when: APScheduler jobs need to call async Telegram methods
   - Creates an isolated event loop that doesn't affect other threads

2. ASYNC -> SYNC: `await run_sync_in_executor(func, *args)`
   - Use when: Telegram async handlers need to call sync E*TRADE methods
   - Runs sync code in a thread pool to avoid blocking the event loop

IMPORTANT: Never mix these patterns. Choose based on your CURRENT context:
- In an async function? Use run_sync_in_executor()
- In a sync function? Use run_async_from_sync()
"""

import asyncio
import concurrent.futures
import functools
import logging
from typing import Any, Callable, Coroutine, Optional, TypeVar

logger = logging.getLogger(__name__)

# Type vars for better type hints
T = TypeVar("T")

# Shared thread pool for sync operations
# Using module-level to ensure single instance across imports
_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None


def _get_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Get or create the shared thread pool executor."""
    global _executor
    if _executor is None or _executor._shutdown:
        _executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="async_bridge",
        )
    return _executor


# =============================================================================
# PATTERN 1: SYNC -> ASYNC
# Use when you're in sync code (like APScheduler jobs) and need to call async
# =============================================================================


def run_async_from_sync(coro: Coroutine[Any, Any, T], timeout: float = 60.0) -> T:
    """
    Run an async coroutine from synchronous code.

    This is the ONLY correct way to call async code from sync contexts like:
    - APScheduler job functions
    - Database callbacks
    - Signal handlers

    Creates an isolated event loop that doesn't affect the global event loop
    policy, preventing "Event loop is closed" errors in other threads.

    Args:
        coro: The coroutine to run
        timeout: Maximum seconds to wait (default 60)

    Returns:
        The result of the coroutine

    Raises:
        asyncio.TimeoutError: If the coroutine takes longer than timeout
        Any exception raised by the coroutine

    Example:
        def scheduler_job():
            # This is sync code called by APScheduler
            result = run_async_from_sync(telegram_bot.send_message("Hello"))
            return result
    """
    # Check if we're accidentally being called from an async context
    try:
        loop = asyncio.get_running_loop()
        if loop and not loop.is_closed():
            # We're in an async context - this is a programming error
            # But handle gracefully by using run_coroutine_threadsafe
            logger.warning(
                "run_async_from_sync called from async context - "
                "consider using 'await' directly instead"
            )
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            return future.result(timeout=timeout)
    except RuntimeError:
        # No running loop - this is the expected case
        pass

    # Create isolated event loop
    loop = asyncio.new_event_loop()
    try:
        # Wrap with timeout
        async def with_timeout():
            return await asyncio.wait_for(coro, timeout=timeout)

        return loop.run_until_complete(with_timeout())
    finally:
        # Clean shutdown
        try:
            # Cancel any pending tasks
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass  # Best effort cleanup
        finally:
            loop.close()


# =============================================================================
# PATTERN 2: ASYNC -> SYNC
# Use when you're in async code (like Telegram handlers) and need to call sync
# =============================================================================


async def run_sync_in_executor(
    func: Callable[..., T],
    *args: Any,
    **kwargs: Any,
) -> T:
    """
    Run a synchronous function in a thread executor.

    This is the ONLY correct way to call sync code from async contexts like:
    - Telegram bot command handlers
    - Async HTTP handlers
    - Any 'async def' function

    Uses a persistent thread pool to avoid executor creation/shutdown issues.
    The sync function runs completely isolated from the event loop.

    Args:
        func: The synchronous function to run
        *args: Positional arguments to pass to the function
        **kwargs: Keyword arguments to pass to the function

    Returns:
        The result of the function

    Raises:
        Any exception raised by the function

    Example:
        async def telegram_handler(update):
            # This is async code in Telegram bot
            positions = await run_sync_in_executor(
                etrade_client.get_positions,
                account_id
            )
            return positions
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError as e:
        # No running loop - this shouldn't happen in async context
        logger.error(f"run_sync_in_executor called without running loop: {e}")
        raise RuntimeError(
            "run_sync_in_executor must be called from an async context. "
            "If you're in sync code, call the function directly."
        ) from e

    executor = _get_executor()

    # Create partial if we have args/kwargs
    if args or kwargs:
        func_to_run = functools.partial(func, *args, **kwargs)
    else:
        func_to_run = func

    return await loop.run_in_executor(executor, func_to_run)


# =============================================================================
# CLEANUP
# =============================================================================


def shutdown_executor(wait: bool = True) -> None:
    """
    Shutdown the shared thread pool executor.

    Call this during application shutdown for clean exit.

    Args:
        wait: If True, wait for pending tasks to complete
    """
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=wait)
        _executor = None
        logger.info("Async bridge executor shut down")


# =============================================================================
# DEPRECATED - Keep for backward compatibility but log warnings
# =============================================================================


def run_async(coro: Coroutine) -> Any:
    """
    DEPRECATED: Use run_async_from_sync() instead.

    This function is kept for backward compatibility but will be removed.
    """
    logger.warning(
        "run_async() is deprecated, use run_async_from_sync() instead",
        stacklevel=2,
    )
    return run_async_from_sync(coro)
