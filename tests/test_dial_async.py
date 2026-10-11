"""DIAL wire compatibility and shared async transport lifecycle."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from providers.dial import DIALModelProvider
from tests.test_provider_wire_async import chat_response


class CountingTransport(httpx.MockTransport):
    def __init__(self, handler):
        super().__init__(handler)
        self.closes = 0

    async def aclose(self):
        self.closes += 1
        await super().aclose()


def inject_pool(monkeypatch, transport):
    clients = []
    original = httpx.AsyncClient

    class Client(original):
        def __init__(self, **kwargs):
            super().__init__(transport=transport, **kwargs)
            clients.append(self)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    return clients


@pytest.mark.parametrize(
    "model,canonical,sampling",
    [
        ("o3", "o3-2025-04-16", False),
        ("o4-mini", "o4-mini-2025-04-16", False),
        ("sonnet-4.1", "anthropic.claude-sonnet-4.1-20250805-v1:0", True),
    ],
)
async def test_wire_migration_baseline(monkeypatch, model, canonical, sampling):
    requests = []

    def handle(request):
        requests.append(request)
        return chat_response()

    transport = CountingTransport(handle)
    clients = inject_pool(monkeypatch, transport)
    monkeypatch.setenv("DIAL_API_VERSION", "2024-12-01-preview")
    provider = DIALModelProvider("synthetic", base_url="https://dial.example.invalid")
    assert not clients
    try:
        result = await provider.generate_content(
            "héllo",
            model,
            system_prompt="system",
            temperature=0.4,
            max_output_tokens=32,
            images=["data:image/png;base64,aGVsbG8="],
        )
        (request,) = requests
        assert request.url.path == f"/openai/deployments/{canonical}/chat/completions"
        assert dict(request.url.params) == {"api-version": "2024-12-01-preview"}
        assert request.headers["api-key"] == "synthetic"
        assert "authorization" not in request.headers
        expected = {
            "model": canonical,
            "stream": False,
            "messages": [
                {"role": "system", "content": "system"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "héllo"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}},
                    ],
                },
            ],
        }
        if sampling:
            expected.update(temperature=0.4, max_tokens=32)
        # Baseline defect: Task 20 MUST update this for no-temperature deployments.
        assert json.loads(request.content) == expected
        assert request.extensions["timeout"] == provider.timeout_config.as_dict()
        assert result.content == "réponse ✅"
        assert result.model_name == model
        assert result.usage == {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}
        assert len(clients) == 1
        assert provider._client is None
    finally:
        await provider.aclose()
        await provider.aclose()
    assert transport.closes == 1
    assert clients[0].is_closed
    assert provider._close_task.done()


async def test_cold_concurrent_deployments_share_one_pool(monkeypatch):
    entered = asyncio.Queue()
    release = asyncio.Event()

    async def handle(request):
        entered.put_nowait(json.loads(request.content)["model"])
        await release.wait()
        return chat_response()

    transport = CountingTransport(handle)
    clients = inject_pool(monkeypatch, transport)
    provider = DIALModelProvider("synthetic")
    tasks = [asyncio.create_task(provider.generate_content("hello", model)) for model in ("o3", "o3", "o4-mini")]
    try:
        names = [await asyncio.wait_for(entered.get(), 2) for _ in tasks]
        assert names.count("o3-2025-04-16") == 2
        assert names.count("o4-mini-2025-04-16") == 1
        assert len(clients) == 1
        assert len(provider._deployment_clients) == 2
        assert all(wrapper._client is clients[0] for wrapper in provider._deployment_clients.values())
        assert all(not task.done() for task in tasks)
        release.set()
        assert all(result.content == "réponse ✅" for result in await asyncio.wait_for(asyncio.gather(*tasks), 2))
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(provider.aclose(), provider.aclose())
    assert transport.closes == 1
    assert not provider._deployment_clients


async def test_failed_wrapper_construction_retains_owned_pool_for_retry(monkeypatch):
    transport = CountingTransport(lambda request: chat_response())
    clients = inject_pool(monkeypatch, transport)
    provider = DIALModelProvider("synthetic")
    constructor = Mock(side_effect=ValueError("constructor failed"))
    monkeypatch.setattr("openai.AsyncOpenAI", constructor)
    try:
        with pytest.raises(ValueError, match="constructor failed"):
            provider._get_deployment_client("o3")
        assert not provider._deployment_clients
        assert not clients[0].is_closed
        assert transport.closes == 0
        constructor.side_effect = None
        sdk = provider._get_deployment_client("o3")
        assert provider._get_deployment_client("o3") is sdk
        assert len(clients) == 1
    finally:
        await provider.aclose()
    assert transport.closes == 1


async def test_failed_pool_construction_can_retry(monkeypatch):
    provider = DIALModelProvider("synthetic")
    create = Mock(side_effect=ValueError("pool construction failed"))
    monkeypatch.setattr(provider, "_create_dial_http_client", create)
    with pytest.raises(ValueError, match="pool construction failed"):
        provider._get_deployment_client("o3")
    assert provider._http_client is None
    assert not provider._deployment_clients
    create.side_effect = None
    create.return_value = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr("openai.AsyncOpenAI", Mock())
    provider._get_deployment_client("o3")
    await provider.aclose()
    create.return_value.aclose.assert_awaited_once()


async def test_unused_cleanup_allocates_nothing(monkeypatch):
    create = Mock(side_effect=AssertionError("unexpected pool"))
    monkeypatch.setattr(DIALModelProvider, "_create_dial_http_client", create)
    provider = DIALModelProvider("synthetic")
    await provider.aclose()
    await provider.aclose()
    create.assert_not_called()
    assert provider._close_task is None
    with pytest.raises(RuntimeError, match="closed"):
        provider._get_deployment_client("o3")


async def test_request_cancellation_reaches_transport_without_closing_pool(monkeypatch):
    entered = asyncio.Event()
    exited = asyncio.Event()
    calls = []

    async def handle(request):
        calls.append(request)
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            exited.set()

    transport = CountingTransport(handle)
    inject_pool(monkeypatch, transport)
    provider = DIALModelProvider("synthetic")
    task = asyncio.create_task(provider.generate_content("hello", "o3"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert exited.is_set()
        assert len(calls) == 1
        assert transport.closes == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await provider.aclose()
    assert transport.closes == 1


async def test_repeated_cancellation_and_concurrent_close_join_one_pool_cleanup(monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    class Transport(CountingTransport):
        async def aclose(self):
            self.closes += 1
            entered.set()
            await release.wait()
            finished.set()

    transport = Transport(lambda request: chat_response())
    inject_pool(monkeypatch, transport)
    provider = DIALModelProvider("synthetic")
    await provider.generate_content("hello", "o3")
    tasks = [asyncio.create_task(provider.aclose()) for _ in range(2)]
    try:
        await asyncio.wait_for(entered.wait(), 2)
        tasks[0].cancel()
        await asyncio.sleep(0)
        tasks[0].cancel()
        await asyncio.sleep(0)
        assert not tasks[0].done()
        assert not finished.is_set()
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 2)
        assert isinstance(results[0], asyncio.CancelledError)
        assert results[1] is None
        assert finished.is_set()
        assert provider._close_task.done()
        assert transport.closes == 1
        await provider.aclose()
        assert transport.closes == 1
        assert provider._http_client is None
        assert not provider._deployment_clients
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_close_failure_stays_visible_without_restarting_httpx_cleanup():
    error = RuntimeError("transport close failed")
    pool = SimpleNamespace(aclose=AsyncMock(side_effect=error))
    provider = DIALModelProvider("synthetic")
    provider._http_client = pool
    for _ in range(2):
        with pytest.raises(RuntimeError) as raised:
            await provider.aclose()
        assert raised.value is error
    pool.aclose.assert_awaited_once()
    assert provider._close_task.done()
    with pytest.raises(RuntimeError, match="closed"):
        provider._get_deployment_client("o3")


@pytest.mark.parametrize("retryable", [False, True])
async def test_native_retry_preserves_dial_error_contract(monkeypatch, retryable):
    error = RuntimeError("503 unavailable" if retryable else "invalid request")
    create = AsyncMock(side_effect=error)
    provider = DIALModelProvider("synthetic")
    monkeypatch.setattr(
        provider,
        "_get_deployment_client",
        lambda name: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
    )
    sleep = AsyncMock()
    monkeypatch.setattr("providers.base.asyncio.sleep", sleep)
    with pytest.raises(ValueError, match="after 4 attempts" if retryable else "DIAL API error") as raised:
        await provider.generate_content("hello", "o3")
    assert raised.value.__cause__ is error
    assert create.await_count == (4 if retryable else 1)
    assert [call.args[0] for call in sleep.await_args_list] == ([1, 3, 5] if retryable else [])
    await provider.aclose()
