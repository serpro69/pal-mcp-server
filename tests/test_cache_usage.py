"""Endpoint cache fields and noncumulative invocation/transcript accounting."""

import asyncio
import json
import sys
from types import SimpleNamespace

import httpx
import pytest

import server
from providers.dial import DIALModelProvider
from providers.gemini import GeminiModelProvider
from providers.openai import OpenAIModelProvider
from providers.openrouter import OpenRouterProvider
from providers.shared import ModelResponse
from scripts.cache_report import read_events, usage_report
from tests.request_capture import capture_providers
from tests.test_conversation_storage_errors import FaultStorage
from tests.test_dial_async import inject_pool
from tests.test_provider_wire_async import make_provider
from utils import conversation_memory as memory

pytestmark = pytest.mark.no_mock_provider


@pytest.mark.parametrize("responses", [False, True])
@pytest.mark.parametrize("value", [None, 0, 17, -1, True, "17", 1.5])
def test_cache_usage_keeps_unknown_distinct_from_zero(responses, value):
    provider = OpenAIModelProvider("synthetic")
    details = SimpleNamespace(cached_tokens=value, cache_write_tokens=value)
    metadata = SimpleNamespace(**{"input_tokens_details" if responses else "prompt_tokens_details": details})
    usage = provider._extract_usage(SimpleNamespace(usage=metadata), responses_api=responses)
    assert usage == (
        {"cached_input_tokens": value, "cache_write_tokens": value} if type(value) is int and value >= 0 else {}
    )
    assert provider._extract_usage(SimpleNamespace(usage=None), responses_api=responses) == {}
    assert provider._extract_usage(SimpleNamespace(usage=SimpleNamespace()), responses_api=responses) == {}


@pytest.mark.parametrize("value", [None, 0, 17, -1, True, "17", 1.5])
def test_gemini_cache_read_has_no_invented_write_count(value):
    provider = GeminiModelProvider("synthetic")
    usage = provider._extract_usage(SimpleNamespace(usage_metadata=SimpleNamespace(cached_content_token_count=value)))
    assert usage == ({"cached_input_tokens": value} if type(value) is int and value >= 0 else {})


@pytest.mark.parametrize("kind", ["openai", "openrouter", "azure", "custom", "xai", "dial"])
async def test_chat_cache_fields_survive_actual_sdk_deserialization(kind, monkeypatch):
    body = {
        "id": "cache-fixture",
        "model": "fixture",
        "created": 0,
        "choices": [{"message": {"role": "assistant", "content": "answer"}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 5,
            "total_tokens": 105,
            "prompt_tokens_details": {"cached_tokens": 70, "cache_write_tokens": 0},
        },
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    if kind == "dial":
        inject_pool(monkeypatch, transport)
        provider, model = DIALModelProvider("synthetic", base_url="https://dial.invalid"), "o3"
    else:
        provider, model, _, _ = make_provider(kind)
        provider._test_transport = transport
    try:
        response = await provider.generate_content("fixture", model)
        assert response.usage == {
            "input_tokens": 100,
            "output_tokens": 5,
            "total_tokens": 105,
            "cached_input_tokens": 70,
            "cache_write_tokens": 0,
        }
    finally:
        await provider.aclose()


@pytest.mark.parametrize("router", [False, True])
@pytest.mark.parametrize("details", [{}, {"cached_tokens": 0}, {"cached_tokens": 70, "cache_write_tokens": 12}])
async def test_responses_cache_fields_include_newer_sdk_extras(router, details):
    provider = OpenRouterProvider("synthetic") if router else OpenAIModelProvider("synthetic")
    model = "openai/gpt-5.2-pro" if router else "o3-pro"
    body = {
        "id": "r1",
        "model": model,
        "object": "response",
        "created_at": 0,
        "usage": {"input_tokens": 100, "output_tokens": 5, "total_tokens": 105, "input_tokens_details": details},
        "output": [
            {
                "type": "message",
                "id": "m1",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "answer", "annotations": []}],
            }
        ],
    }
    provider._test_transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    try:
        response = await provider.generate_content("fixture", model)
        expected = {"input_tokens": 100, "output_tokens": 5, "total_tokens": 105}
        if "cached_tokens" in details:
            expected["cached_input_tokens"] = details["cached_tokens"]
        if "cache_write_tokens" in details:
            expected["cache_write_tokens"] = details["cache_write_tokens"]
        assert response.usage == expected
    finally:
        await provider.aclose()


@pytest.fixture
def audit(monkeypatch, tmp_path):
    if sys.platform == "win32":
        pytest.skip("Private transcript acceptance requires POSIX; Windows recording intentionally fails closed")
    monkeypatch.setattr(memory, "get_storage", lambda: storage)
    storage = FaultStorage()
    monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("PAL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PAL_TRANSCRIPTS", "true")
    capture_providers(monkeypatch)
    return tmp_path / "state" / "transcripts"


async def test_empty_reply_recovery_records_each_returned_call_once(monkeypatch, tmp_path, audit):
    calls = 0

    async def generate(self, **kwargs):
        nonlocal calls
        calls += 1
        return ModelResponse(
            content="" if calls == 1 else "answer",
            model_name=kwargs["model_name"],
            provider="google",
            usage={"input_tokens": 10 * calls, "cached_input_tokens": 0},
            metadata={"finish_reason": "STOP"},
        )

    monkeypatch.setattr(GeminiModelProvider, "generate_content", generate)
    result = json.loads(
        (
            await server.handle_call_tool(
                "chat",
                {"prompt": "retry", "model": "gemini-2.5-flash", "working_directory_absolute_path": str(tmp_path)},
            )
        )[0].text
    )
    turns = memory.get_thread(result["continuation_offer"]["continuation_id"]).turns
    assert len(turns) == 2
    assert [call["usage"]["input_tokens"] for call in turns[-1].model_metadata["provider_calls"]] == [10, 20]
    report = usage_report(read_events(audit.glob("*.jsonl")))
    assert report["groups"][0]["calls"] == 2
    assert report["groups"][0]["fields"]["input_tokens"]["sum"] == 30
    assert report["groups"][0]["fields"]["cache_write_tokens"]["sum"] is None


async def test_concurrent_invocations_keep_usage_separate(monkeypatch, tmp_path, audit):
    entered = asyncio.Queue()
    release = asyncio.Event()

    async def generate(self, **kwargs):
        prompt = kwargs["prompt"]
        entered.put_nowait(True)
        await release.wait()
        return ModelResponse(
            content="answer",
            model_name=kwargs["model_name"],
            provider="google",
            usage={"input_tokens": 11 if "FIRST_TOKEN" in prompt else 22},
        )

    monkeypatch.setattr(GeminiModelProvider, "generate_content", generate)
    tasks = [
        asyncio.create_task(
            server.handle_call_tool(
                "chat", {"prompt": text, "model": "gemini-2.5-flash", "working_directory_absolute_path": str(tmp_path)}
            )
        )
        for text in ("FIRST_TOKEN", "SECOND_TOKEN")
    ]
    try:
        for _ in tasks:
            await asyncio.wait_for(entered.get(), 2)
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), 2)
        for result, expected in zip(results, [11, 22]):
            thread_id = json.loads(result[0].text)["continuation_offer"]["continuation_id"]
            calls = memory.get_thread(thread_id).turns[-1].model_metadata["provider_calls"]
            assert len(calls) == 1 and calls[0]["usage"] == {"input_tokens": expected}
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
