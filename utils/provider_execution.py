"""Temporary generation bridge, removed after native async migration (Task 19)."""

import asyncio
import inspect
from typing import Any

from providers.base import ModelProvider
from providers.shared import ModelResponse


async def invoke_provider(provider: ModelProvider, **kwargs: Any) -> ModelResponse:
    """Await async providers or offload legacy blocking I/O with request context.

    Cancelling this await cannot stop a synchronous worker's network request.
    Provider construction and client/cache safety must be established beforehand.
    """
    generate = provider.generate_content
    if inspect.iscoroutinefunction(generate):
        return await generate(**kwargs)
    return await asyncio.to_thread(generate, **kwargs)
