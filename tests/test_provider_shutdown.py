"""The server owns and joins provider cleanup before its loop can exit."""

import asyncio
import inspect
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import server
from providers import ModelProviderRegistry
from providers.azure_openai import AzureOpenAIProvider
from providers.base import ModelProvider
from providers.custom import CustomProvider
from providers.dial import DIALModelProvider
from providers.gemini import GeminiModelProvider
from providers.openai import OpenAIModelProvider
from providers.openrouter import OpenRouterProvider
from providers.xai import XAIModelProvider


def test_native_provider_contract():
    for provider in (
        ModelProvider,
        OpenAIModelProvider,
        AzureOpenAIProvider,
        CustomProvider,
        OpenRouterProvider,
        XAIModelProvider,
        GeminiModelProvider,
        DIALModelProvider,
    ):
        assert inspect.iscoroutinefunction(provider.generate_content)
        assert inspect.iscoroutinefunction(provider.aclose)


@pytest.mark.parametrize("outcome", ["disconnect", "error", "cancel", "startup_error"])
async def test_stdio_lifetime_awaits_allocated_clients(monkeypatch, outcome):
    entered = asyncio.Event()
    release = asyncio.Event()
    close_started = asyncio.Event()
    closed = []
    clients = [AsyncMock() for _ in range(2)]
    providers = [OpenAIModelProvider("synthetic"), OpenAIModelProvider("synthetic")]
    for index, (provider, client) in enumerate(zip(providers, clients)):

        async def close(index=index):
            close_started.set()
            await release.wait()
            closed.append(index)

        client.close.side_effect = close
        provider._client = client
    monkeypatch.setattr(
        ModelProviderRegistry(), "_initialized_providers", {0: providers[0], 1: providers[1], 2: providers[0]}
    )

    def configure():
        if outcome == "startup_error":
            raise ValueError("startup failed")

    monkeypatch.setattr(server, "configure_providers", configure)

    @asynccontextmanager
    async def stdio():
        yield object(), object()

    monkeypatch.setattr(server, "stdio_server", stdio)

    async def run(*args):
        entered.set()
        if outcome == "cancel":
            await asyncio.Event().wait()
        elif outcome == "error":
            raise ValueError("stdio failed")

    monkeypatch.setattr(server.server, "run", run)
    task = asyncio.create_task(server.main())
    try:
        if outcome != "startup_error":
            await asyncio.wait_for(entered.wait(), 2)
        if outcome == "cancel":
            task.cancel()
        # Cleanup must finish, even when cancellation arrives during close.
        await asyncio.wait_for(close_started.wait(), 2)
        assert not task.done()
        if outcome == "cancel":
            task.cancel()
        release.set()
        if outcome == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        elif outcome in {"error", "startup_error"}:
            with pytest.raises(ValueError, match="failed"):
                await asyncio.wait_for(task, 2)
        else:
            await asyncio.wait_for(task, 2)
        assert closed == [0, 1]
        for provider, client in zip(providers, clients):
            client.close.assert_awaited_once()
            await provider.aclose()
            client.close.assert_awaited_once()
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("primary_error", [False, True])
@pytest.mark.parametrize("cleanup_error", [RuntimeError, asyncio.CancelledError])
async def test_cleanup_failure_attempts_remaining_providers_and_preserves_error(
    monkeypatch, primary_error, cleanup_error
):
    first = SimpleNamespace(aclose=AsyncMock(side_effect=cleanup_error("close failed")))
    second = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(ModelProviderRegistry(), "_initialized_providers", {0: first, 1: second})

    async def serve():
        if primary_error:
            raise ValueError("serve failed")

    monkeypatch.setattr(server, "_serve", serve)
    with pytest.raises(
        ValueError if primary_error else cleanup_error,
        match="failed" if primary_error or cleanup_error is RuntimeError else None,
    ):
        await server.main()
    first.aclose.assert_awaited_once()
    second.aclose.assert_awaited_once()
