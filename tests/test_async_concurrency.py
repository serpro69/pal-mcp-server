"""Observable scheduling, context and cancellation at the temporary provider boundary."""

import asyncio
import json
import threading
from contextvars import ContextVar

import pytest

import server
from providers.gemini import GeminiModelProvider
from providers.openai import OpenAIModelProvider
from providers.shared import ModelResponse
from tests.request_capture import capture_providers
from tests.test_conversation_storage_errors import FaultStorage
from tools.shared.exceptions import ToolExecutionError
from utils import conversation_coordination as coordination
from utils import conversation_memory as memory


@pytest.fixture
def conversation_env(monkeypatch, tmp_path):
    storage = FaultStorage()
    monkeypatch.setattr(memory, "get_storage", lambda: storage)
    monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(tmp_path))
    capture_providers(monkeypatch)
    yield tmp_path
    assert not coordination._entries


async def chat(path, model="gemini-2.5-flash", **kwargs):
    result = await server.handle_call_tool(
        "chat", {"prompt": "question", "model": model, "working_directory_absolute_path": str(path), **kwargs}
    )
    return json.loads(result[0].text)


@pytest.mark.parametrize("native_async", [False, True])
async def test_chat_waits_overlap_and_keep_context_and_provenance(conversation_env, monkeypatch, native_async):
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    entered = asyncio.Queue()
    release = threading.Event()
    release_async = asyncio.Event()
    request_label = ContextVar("test_request_label")
    observed = []

    def response(self, kwargs):
        name = self.get_provider_type().value
        return ModelResponse(content=f"{name} answer", model_name=kwargs["model_name"], provider=name)

    def sync_generate(self, **kwargs):
        observed.append((request_label.get(), threading.get_ident()))
        loop.call_soon_threadsafe(entered.put_nowait, kwargs["model_name"])
        assert release.wait(5)
        return response(self, kwargs)

    async def async_generate(self, **kwargs):
        observed.append((request_label.get(), threading.get_ident()))
        entered.put_nowait(kwargs["model_name"])
        await release_async.wait()
        return response(self, kwargs)

    generate = async_generate if native_async else sync_generate
    monkeypatch.setattr(GeminiModelProvider, "generate_content", generate)
    monkeypatch.setattr(OpenAIModelProvider, "generate_content", generate)

    async def invoke(model):
        token = request_label.set(model)
        try:
            return await chat(conversation_env, model)
        finally:
            request_label.reset(token)

    tasks = [asyncio.create_task(invoke(model)) for model in ("gemini-2.5-flash", "gpt-5")]
    try:
        names = {await asyncio.wait_for(entered.get(), 2) for _ in range(2)}
        assert names == {"gemini-2.5-flash", "gpt-5"}
        assert all(not task.done() for task in tasks)
        release.set()
        release_async.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), 2)
    finally:
        release.set()
        release_async.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert {label for label, _ in observed} == names
    assert all((thread == loop_thread) == native_async for _, thread in observed)
    for result, provider, model in zip(results, ("google", "openai"), ("gemini-2.5-flash", "gpt-5")):
        assert result["metadata"]["model_used"] == model
        assert result["metadata"]["provider_used"] == provider
        thread = memory.get_thread(result["continuation_offer"]["continuation_id"])
        assert [turn.role for turn in thread.turns] == ["user", "assistant"]
        assert thread.turns[-1].content == f"{provider} answer"


@pytest.mark.parametrize("native_async", [False, True])
@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
async def test_chat_recovery_yields_and_preserves_failure_or_cancellation(
    conversation_env, monkeypatch, native_async, outcome
):
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    finished = threading.Event()
    release = threading.Event()
    release_async = asyncio.Event()
    calls = []
    thread_id = memory.create_thread("chat", {})

    def answer(kwargs):
        if len(calls) == 1:
            return ModelResponse(
                content="", model_name=kwargs["model_name"], provider="google", metadata={"finish_reason": "STOP"}
            )
        if outcome == "failure":
            raise RuntimeError("synthetic recovery failure")
        return ModelResponse(content="recovered answer", model_name=kwargs["model_name"], provider="google")

    def sync_generate(self, **kwargs):
        calls.append(kwargs)
        if len(calls) > 1:
            loop.call_soon_threadsafe(entered.set)
            try:
                assert release.wait(5)
                return answer(kwargs)
            finally:
                finished.set()
        return answer(kwargs)

    async def async_generate(self, **kwargs):
        calls.append(kwargs)
        if len(calls) > 1:
            entered.set()
            try:
                await release_async.wait()
                return answer(kwargs)
            finally:
                finished.set()
        return answer(kwargs)

    monkeypatch.setattr(GeminiModelProvider, "generate_content", async_generate if native_async else sync_generate)
    task = asyncio.create_task(chat(conversation_env, continuation_id=thread_id))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        # A provider-free request advances while recovery is still waiting.
        independent = await asyncio.wait_for(
            server.handle_call_tool(
                "planner", {"step": "plan", "step_number": 1, "total_steps": 2, "next_step_required": True}
            ),
            2,
        )
        assert independent
        assert not task.done()
        if outcome == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            release.set()
            release_async.set()
            if outcome == "failure":
                with pytest.raises(ToolExecutionError, match="synthetic recovery failure"):
                    await asyncio.wait_for(task, 2)
            else:
                result = await asyncio.wait_for(task, 2)
                assert result["status"] == "continuation_available"
                assert "recovered answer" in result["content"]
        assert len(calls) == 2
        assert not coordination._entries
    finally:
        release.set()
        release_async.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.to_thread(finished.wait, 5)
    thread = memory.get_thread(thread_id)
    assert [turn.role for turn in thread.turns] == (["user", "assistant"] if outcome == "success" else ["user"])
