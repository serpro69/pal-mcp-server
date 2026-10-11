"""Invocation coordination for the single-event-loop MCP server.

The registry retains only active holders and waiters. Direct tool callers may use
the same coordinator explicitly, with a separate tool instance for each call.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class _Entry:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.users = 0


_entries: dict[str, _Entry] = {}


@asynccontextmanager
async def coordinate_conversation(thread_id: str | None) -> AsyncIterator[None]:
    """Hold one continuation's lock through admission, execution and persistence.

    New conversations have unpublished, unique IDs and no competing caller yet.
    Registration and ref-count cleanup do not await; cancellation while waiting
    or executing releases the reference, without cancelling other invocations.
    """
    if not thread_id:
        yield
        return

    entry = _entries.get(thread_id)
    if entry is None:
        entry = _entries[thread_id] = _Entry()
    entry.users += 1
    try:
        async with entry.lock:
            yield
    finally:
        entry.users -= 1
        if entry.users == 0:
            del _entries[thread_id]
