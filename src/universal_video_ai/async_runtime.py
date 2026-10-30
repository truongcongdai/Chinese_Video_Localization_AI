"""Run blocking work without releasing capacity before its thread exits."""
from __future__ import annotations

import asyncio
from contextvars import copy_context
from functools import partial
from typing import Callable, TypeVar

__all__ = ["run_blocking_bounded"]

T = TypeVar("T")


async def run_blocking_bounded(
    slots: asyncio.Semaphore, function: Callable[..., T], *args,
    on_complete: Callable[[], None] | None = None, **kwargs
) -> T:
    """Keep a slot until actual completion, even when the caller is cancelled.

    Cancelling a coroutine cannot stop its running thread. Shield the executor
    future and release capacity from that future's completion callback instead
    of the cancelled caller's finally block. Context variables propagate as in
    asyncio.to_thread.
    """
    acquired = False
    try:
        await slots.acquire()
        acquired = True
        work = partial(function, *args, **kwargs)
        future = asyncio.get_running_loop().run_in_executor(None, copy_context().run, work)
    except BaseException:
        if acquired:
            slots.release()
        if on_complete is not None:
            on_complete()
        raise

    def finished(result: asyncio.Future) -> None:
        slots.release()
        if on_complete is not None:
            on_complete()
        if not result.cancelled():
            # Observe failures even if the requesting coroutine has timed out.
            result.exception()

    future.add_done_callback(finished)
    return await asyncio.shield(future)
