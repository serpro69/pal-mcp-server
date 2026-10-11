"""Gemini native generation, wire compatibility, and client ownership."""

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from google import genai
from google.genai import types

from providers.gemini import GeminiModelProvider


def reply(text="réponse ✅", **kwargs):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(parts=[types.Part(text=text)]), finish_reason="STOP")],
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=7, candidates_token_count=3),
        **kwargs,
    )


def fake_client(generate):
    return SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate), aclose=AsyncMock()), close=Mock()
    )


async def test_native_calls_overlap_and_share_one_client(monkeypatch):
    entered = asyncio.Queue()
    release = asyncio.Event()

    async def generate(**kwargs):
        prompt = kwargs["contents"][0]["parts"][0]["text"]
        entered.put_nowait(prompt)
        await release.wait()
        return reply(prompt)

    client = fake_client(generate)
    construct = Mock(return_value=client)
    monkeypatch.setattr("providers.gemini.genai.Client", construct)
    provider = GeminiModelProvider("synthetic")
    tasks = [
        asyncio.create_task(provider.generate_content(prompt, "gemini-2.5-flash")) for prompt in ("first", "second")
    ]
    try:
        assert {await asyncio.wait_for(entered.get(), 2) for _ in tasks} == {"first", "second"}
        assert all(not task.done() for task in tasks)
        construct.assert_called_once()
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), 2)
        assert [result.content for result in results] == ["first", "second"]
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(provider.aclose(), provider.aclose())
    client.aio.aclose.assert_awaited_once()
    client.close.assert_called_once()


async def test_unused_cleanup_never_constructs_client(monkeypatch):
    constructor = Mock(side_effect=AssertionError("unexpected allocation"))
    monkeypatch.setattr("providers.gemini.genai.Client", constructor)
    provider = GeminiModelProvider("synthetic")
    await provider.aclose()
    await provider.aclose()
    constructor.assert_not_called()


@pytest.mark.parametrize("phase", ["request", "backoff"])
async def test_cancellation_propagates_without_retry(monkeypatch, phase):
    entered = asyncio.Event()

    async def generate(**kwargs):
        if phase == "backoff":
            raise RuntimeError("503 temporarily unavailable")
        entered.set()
        await asyncio.Event().wait()

    async def sleep(delay):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("providers.base.asyncio.sleep", sleep)
    generate = AsyncMock(side_effect=generate)
    client = fake_client(generate)
    provider = GeminiModelProvider("synthetic")
    provider._client = client
    task = asyncio.create_task(provider.generate_content("hello", "gemini-2.5-flash"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        generate.assert_awaited_once()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await provider.aclose()
    client.aio.aclose.assert_awaited_once()


@pytest.mark.parametrize("error,attempts", [(RuntimeError("503 unavailable"), 2), (ValueError("invalid request"), 1)])
async def test_retry_classification(monkeypatch, error, attempts):
    generate = AsyncMock(side_effect=[error, reply()])
    provider = GeminiModelProvider("synthetic")
    provider._client = fake_client(generate)
    delay = AsyncMock()
    monkeypatch.setattr("providers.base.asyncio.sleep", delay)
    try:
        if attempts == 2:
            assert (await provider.generate_content("hello", "gemini-2.5-flash")).content == "réponse ✅"
            delay.assert_awaited_once_with(1)
        else:
            with pytest.raises(RuntimeError, match="after 1 attempt") as raised:
                await provider.generate_content("hello", "gemini-2.5-flash")
            assert raised.value.__cause__ is error
            delay.assert_not_awaited()
        assert generate.await_count == attempts
    finally:
        await provider.aclose()


async def test_actual_sdk_wire_images_thinking_timeout_and_cleanup(monkeypatch):
    original_client = genai.Client
    requests = []
    created = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": "réponse ✅"}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3, "totalTokenCount": 10},
            },
        )

    def constructor(**kwargs):
        options = kwargs["http_options"]
        options.client_args = {"transport": httpx.MockTransport(handle)}
        options.async_client_args = {"transport": httpx.MockTransport(handle)}
        sdk = original_client(**kwargs)
        created.append(sdk)
        return sdk

    monkeypatch.setenv("CUSTOM_READ_TIMEOUT", "1234")
    monkeypatch.setattr("providers.gemini.genai.Client", constructor)
    provider = GeminiModelProvider("synthetic", base_url="https://gemini.example.invalid")
    try:
        result = await provider.generate_content(
            "héllo",
            "gemini-2.5-flash",
            system_prompt="system",
            temperature=0.4,
            max_output_tokens=32,
            thinking_mode="low",
            images=["data:image/png;base64,aGVsbG8="],
        )
        (request,) = requests
        assert request.url.host == "gemini.example.invalid"
        assert request.url.path == "/v1beta/models/gemini-2.5-flash:generateContent"
        assert request.headers["x-goog-api-key"] == "synthetic"
        payload = json.loads(request.content)
        assert payload["contents"] == [
            {"parts": [{"text": "system\n\nhéllo"}, {"inlineData": {"mimeType": "image/png", "data": "aGVsbG8="}}]}
        ]
        assert payload["generationConfig"] == {
            "temperature": 0.4,
            "candidateCount": 1,
            "maxOutputTokens": 32,
            "thinkingConfig": {"thinking_budget": 1966},
        }
        assert request.extensions["timeout"]["read"] == 1.234  # Retain existing SDK timeout conversion.
        assert result.content == "réponse ✅"
        assert result.usage == {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}
        assert result.metadata == {
            "thinking_mode": "low",
            "finish_reason": "STOP",
            "is_blocked_by_safety": False,
            "safety_feedback": None,
        }
    finally:
        await provider.aclose()
        await provider.aclose()
    assert created[0]._api_client._httpx_client.is_closed
    assert created[0]._api_client._async_httpx_client.is_closed


async def test_safety_and_absent_usage_preserved():
    blocked = types.GenerateContentResponse(
        candidates=[], prompt_feedback=types.GenerateContentResponsePromptFeedback(block_reason="SAFETY")
    )
    provider = GeminiModelProvider("synthetic")
    provider._client = fake_client(AsyncMock(return_value=blocked))
    try:
        result = await provider.generate_content("hello", "gemini-2.5-flash")
        assert result.content is None
        assert result.usage == {}
        assert result.metadata["is_blocked_by_safety"] is True
        assert result.metadata["finish_reason"] == "SAFETY"
        assert "SAFETY" in result.metadata["safety_feedback"]
    finally:
        await provider.aclose()


async def test_image_preparation_does_not_block_other_requests(monkeypatch):
    provider = GeminiModelProvider("synthetic")
    provider._client = fake_client(AsyncMock(return_value=reply()))
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def process(path):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return {"inline_data": {"mime_type": "image/png", "data": "aGVsbG8="}}

    monkeypatch.setattr(provider, "_process_image", process)
    task = asyncio.create_task(provider.generate_content("hello", "gemini-2.5-flash", images=["synthetic.png"]))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert (
            await asyncio.wait_for(provider.generate_content("other", "gemini-2.5-flash"), 2)
        ).content == "réponse ✅"
        release.set()
        await asyncio.wait_for(task, 2)
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await provider.aclose()


@pytest.mark.parametrize("cancelled", [False, True])
async def test_async_cleanup_failure_still_closes_sync_and_can_retry(cancelled):
    entered = asyncio.Event()

    async def close_async():
        entered.set()
        if cancelled:
            await asyncio.Event().wait()
        raise RuntimeError("async cleanup failed")

    client = fake_client(AsyncMock())
    client.aio.aclose.side_effect = close_async
    provider = GeminiModelProvider("synthetic")
    provider._client = client
    task = asyncio.create_task(provider.aclose())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        if cancelled:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancelled else RuntimeError):
            await task
        client.close.assert_called_once()
        assert provider._client is client
        client.aio.aclose.side_effect = None
        await provider.aclose()
        await provider.aclose()
        assert client.aio.aclose.await_count == 2
        client.close.assert_called_once()
        assert provider._client is None
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_sync_cleanup_failure_does_not_repeat_completed_async_cleanup():
    client = fake_client(AsyncMock())
    client.close.side_effect = [RuntimeError("sync cleanup failed"), None]
    provider = GeminiModelProvider("synthetic")
    provider._client = client
    with pytest.raises(RuntimeError, match="sync cleanup failed"):
        await provider.aclose()
    await provider.aclose()
    client.aio.aclose.assert_awaited_once()
    assert client.close.call_count == 2
    assert provider._client is None


async def test_both_cleanup_failures_preserve_original_async_error():
    client = fake_client(AsyncMock())
    original = RuntimeError("async cleanup failed")
    client.aio.aclose.side_effect = original
    client.close.side_effect = ValueError("sync cleanup failed")
    provider = GeminiModelProvider("synthetic")
    provider._client = client
    with pytest.raises(RuntimeError) as error:
        await provider.aclose()
    assert error.value is original
    client.close.assert_called_once()
    client.aio.aclose.side_effect = None
    client.close.side_effect = None
    await provider.aclose()
    assert provider._client is None
