"""Invocation coordination for the single-event-loop MCP server.

The registry retains only active holders and waiters. Direct API/local tool
callers may coordinate explicitly; clink owns its non-waiting acquisition.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class _Entry:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.users = 0


_entries: dict[str, _Entry] = {}


class ConversationBusyError(Exception):
    """A non-waiting caller encountered an active holder or queued waiter."""


@asynccontextmanager
async def coordinate_conversation(thread_id: str | None, *, wait: bool = True) -> AsyncIterator[None]:
    """Hold one continuation's lock through admission, execution and persistence.

    New conversations have unpublished, unique IDs and no competing caller yet.
    Registration and ref-count cleanup do not await; cancellation while waiting
    or executing releases the reference, without cancelling other invocations.
    Non-waiting callers reject any existing user, including a waiter between
    lock release and resumption. An idle lock acquires without yielding.
    """
    if not thread_id:
        yield
        return

    entry = _entries.get(thread_id)
    if not wait and entry is not None and entry.users:
        raise ConversationBusyError("Conversation is busy. Retry this continuation after the active request completes.")
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
