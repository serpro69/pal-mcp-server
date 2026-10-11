"""Helper functions for HTTP transport injection in tests."""

import asyncio

import httpx

from tests.http_transport_recorder import TransportFactory


def inject_transport(monkeypatch, cassette_path: str):
    """Inject HTTP transport into OpenAICompatibleProvider for testing.

    This helper simplifies the monkey patching pattern used across tests
    to inject custom HTTP transports for recording/replaying API calls.

    Also ensures OpenAI provider is properly registered for tests that need it.

    Args:
        monkeypatch: pytest monkeypatch fixture
        cassette_path: Path to cassette file for recording/replay

    Returns:
        The created transport instance

    Example:
        transport = inject_transport(monkeypatch, "path/to/cassette.json")
    """
    # Ensure OpenAI provider is registered - always needed for transport injection
    from providers.openai import OpenAIModelProvider
    from providers.registry import ModelProviderRegistry
    from providers.shared import ProviderType

    # Always register OpenAI provider for transport tests (API key might be dummy)
    ModelProviderRegistry.register_provider(ProviderType.OPENAI, OpenAIModelProvider)

    # Create transport
    transport = TransportFactory.create_transport(str(cassette_path))

    # Inject transport using the established pattern
    from providers.openai_compatible import OpenAICompatibleProvider

    class AsyncRecorderTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            # Existing cassette recording is synchronous and fully buffers replies.
            return await asyncio.to_thread(transport.handle_request, request)

        async def aclose(self):
            await asyncio.to_thread(transport.close)

    monkeypatch.setattr(OpenAICompatibleProvider, "_test_transport", AsyncRecorderTransport(), raising=False)
    return transport
