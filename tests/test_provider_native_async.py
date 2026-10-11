"""Native provider scheduling, retry and ownership contracts."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from tests.test_provider_initialization import make_provider, patch_sdk
from tests.test_provider_retry_logic import _mock_chat_response


@pytest.mark.parametrize("kind", ["openai", "azure"])
async def test_native_cold_start_and_network_waits_overlap(monkeypatch, kind):
    provider, model = make_provider(kind)
    entered = asyncio.Queue()
    release = asyncio.Event()
    clients = []

    async def generate(**kwargs):
        entered.put_nowait(kwargs["messages"][-1]["content"])
        await release.wait()
        return _mock_chat_response(kwargs["messages"][-1]["content"])

    def constructor(**kwargs):
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=generate)),
            close=AsyncMock(side_effect=kwargs["http_client"].aclose),
        )
        clients.append(client)
        return client

    patch_sdk(monkeypatch, kind, constructor)
    tasks = [asyncio.create_task(provider.generate_content(prompt, model)) for prompt in ("first", "second")]
    try:
        assert {await asyncio.wait_for(entered.get(), 2) for _ in tasks} == {"first", "second"}
        assert len(clients) == 1
        assert all(not task.done() for task in tasks)
        release.set()
        responses = await asyncio.wait_for(asyncio.gather(*tasks), 2)
        assert [response.content for response in responses] == ["first", "second"]
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await provider.aclose()
        await provider.aclose()
    clients[0].close.assert_awaited_once()


@pytest.mark.parametrize("kind", ["openai", "azure"])
async def test_native_failed_constructor_closes_transport_and_remains_retryable(monkeypatch, kind):
    provider, _ = make_provider(kind)
    transports = []

    def constructor(**kwargs):
        transports.append(kwargs["http_client"])
        raise ValueError("synthetic constructor failure")

    patch_sdk(monkeypatch, kind, constructor)
    with pytest.raises(ValueError, match="constructor failure"):
        await provider._get_client()
    assert provider._client is None
    assert all(client.is_closed for client in transports)
    sdk = SimpleNamespace(close=AsyncMock())
    patch_sdk(monkeypatch, kind, lambda **kwargs: sdk)
    # A zero-network client is sufficient to prove failed publication released its lock.
    monkeypatch.setattr(provider, "_create_http_client", AsyncMock(return_value=None))
    assert await provider._get_client() is sdk
    await provider.aclose()
    await provider.aclose()
    sdk.close.assert_awaited_once()


@pytest.mark.parametrize("kind", ["openai", "azure"])
async def test_unused_close_allocates_nothing(monkeypatch, kind):
    provider, _ = make_provider(kind)
    create = AsyncMock(side_effect=AssertionError("cleanup allocated a client"))
    monkeypatch.setattr(provider, "_create_client", create)
    await provider.aclose()
    await provider.aclose()
    create.assert_not_called()


@pytest.mark.parametrize("phase", ["request", "backoff"])
async def test_cancellation_is_not_retried_or_translated(monkeypatch, phase):
    provider, model = make_provider("openai")
    entered = asyncio.Event()
    forever = asyncio.Event()

    async def create(**kwargs):
        if phase == "backoff":
            raise RuntimeError("temporary network interruption")
        entered.set()
        await forever.wait()

    async def delay(seconds):
        entered.set()
        await forever.wait()

    generation = AsyncMock(side_effect=create)
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=generation)), close=AsyncMock()
    )
    monkeypatch.setattr("providers.base.asyncio.sleep", delay)
    task = asyncio.create_task(provider.generate_content("hello", model))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        generation.assert_awaited_once()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await provider.aclose()


async def test_actual_async_transport_cancellation_releases_request():
    provider, model = make_provider("openai")
    entered = asyncio.Event()
    finished = asyncio.Event()

    async def handle(request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    provider._test_transport = httpx.MockTransport(handle)
    task = asyncio.create_task(provider.generate_content("hello", model))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await provider.aclose()


async def test_image_preparation_yields_and_preserves_wire_payload(monkeypatch):
    import json
    import threading

    provider, model = make_provider("openai")
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    requests = []
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}}

    def process_image(path):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return image

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}]})

    monkeypatch.setattr(provider, "_process_image", process_image)
    provider._test_transport = httpx.MockTransport(handle)
    task = asyncio.create_task(provider.generate_content("hello", model, images=["synthetic.png"]))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        # An independent request finishes while local image preparation waits.
        assert (await asyncio.wait_for(provider.generate_content("other", model), 2)).content == "answer"
        release.set()
        assert (await asyncio.wait_for(task, 2)).content == "answer"
        assert requests[-1]["messages"][-1]["content"] == [{"type": "text", "text": "hello"}, image]
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await provider.aclose()
