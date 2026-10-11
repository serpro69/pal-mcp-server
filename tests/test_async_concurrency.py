"""Observable scheduling, context and cancellation at the native async provider boundary."""

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


async def test_chat_waits_overlap_and_keep_context_and_provenance(conversation_env, monkeypatch):
    loop_thread = threading.get_ident()
    entered = asyncio.Queue()
    release_async = asyncio.Event()
    request_label = ContextVar("test_request_label")
    observed = []

    def response(self, kwargs):
        name = self.get_provider_type().value
        return ModelResponse(content=f"{name} answer", model_name=kwargs["model_name"], provider=name)

    async def async_generate(self, **kwargs):
        observed.append((request_label.get(), threading.get_ident()))
        entered.put_nowait(kwargs["model_name"])
        await release_async.wait()
        return response(self, kwargs)

    generate = async_generate
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
        release_async.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), 2)
    finally:
        release_async.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert {label for label, _ in observed} == names
    assert all(thread == loop_thread for _, thread in observed)
    for result, provider, model in zip(results, ("google", "openai"), ("gemini-2.5-flash", "gpt-5")):
        assert result["metadata"]["model_used"] == model
        assert result["metadata"]["provider_used"] == provider
        thread = memory.get_thread(result["continuation_offer"]["continuation_id"])
        assert [turn.role for turn in thread.turns] == ["user", "assistant"]
        assert thread.turns[-1].content == f"{provider} answer"


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
async def test_chat_recovery_yields_and_preserves_failure_or_cancellation(conversation_env, monkeypatch, outcome):
    entered = asyncio.Event()
    finished = threading.Event()
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

    monkeypatch.setattr(GeminiModelProvider, "generate_content", async_generate)
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
        release_async.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.to_thread(finished.wait, 5)
    thread = memory.get_thread(thread_id)
    assert [turn.role for turn in thread.turns] == (["user", "assistant"] if outcome == "success" else ["user"])


@pytest.mark.parametrize("tool_name", ["thinkdeep", "consensus"])
@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
async def test_workflow_provider_waits_yield_without_mixing_or_retrying_cancellation(
    conversation_env, monkeypatch, tool_name, outcome
):
    entered = asyncio.Queue()
    release_async = asyncio.Event()
    models = ["gemini-2.5-flash", "gpt-5"]
    finished = {model: threading.Event() for model in models}
    calls = []
    thread_ids = [memory.create_thread(tool_name, {}) for _ in models]

    def answer(self, kwargs):
        model = kwargs["model_name"]
        if outcome == "failure":
            raise RuntimeError(f"failed {model}")
        return ModelResponse(content=f"answer {model}", model_name=model, provider=self.get_provider_type())

    async def async_generate(self, **kwargs):
        model = kwargs["model_name"]
        calls.append(model)
        entered.put_nowait(model)
        try:
            await release_async.wait()
            return answer(self, kwargs)
        finally:
            finished[model].set()

    generate = async_generate
    monkeypatch.setattr(GeminiModelProvider, "generate_content", generate)
    monkeypatch.setattr(OpenAIModelProvider, "generate_content", generate)

    async def invoke(model, thread_id):
        result = await server.handle_call_tool(
            tool_name,
            {
                "step": f"proposal {model}",
                "findings": f"findings {model}",
                "step_number": 1,
                "total_steps": 1,
                "next_step_required": False,
                "model": model,
                "continuation_id": thread_id,
                **({"models": [{"model": model}]} if tool_name == "consensus" else {}),
            },
        )
        return json.loads(result[0].text)

    tasks = [asyncio.create_task(invoke(model, thread_id)) for model, thread_id in zip(models, thread_ids)]
    try:
        assert {await asyncio.wait_for(entered.get(), 2) for _ in models} == set(models)
        assert all(not task.done() for task in tasks)
        independent = await asyncio.wait_for(
            server.handle_call_tool(
                "planner",
                {
                    "step": "independent work",
                    "step_number": 1,
                    "total_steps": 2,
                    "next_step_required": True,
                },
            ),
            2,
        )
        assert independent
        if outcome == "cancel":
            for task in tasks:
                task.cancel()
            for task in tasks:
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert not coordination._entries
        else:
            release_async.set()
            results = await asyncio.wait_for(asyncio.gather(*tasks), 2)
            for result, model, provider in zip(results, models, ("google", "openai")):
                if tool_name == "consensus":
                    summary = result["complete_consensus"]
                    assert summary["status"] == ("complete" if outcome == "success" else "failed")
                    assert summary["successful_models"] == (1 if outcome == "success" else 0)
                    assert summary["failed_models"] == (0 if outcome == "success" else 1)
                    assert summary["initial_prompt"] == f"proposal {model}"
                    participant = result["model_response"]
                    assert participant["model"] == model
                    assert participant["verdict" if outcome == "success" else "error"] == (
                        f"answer {model}" if outcome == "success" else f"failed {model}"
                    )
                else:
                    assert result["metadata"]["model_used"] == model
                    assert result["metadata"]["provider_used"] == provider
                    if outcome == "success":
                        assert result["expert_analysis"]["raw_analysis"] == f"answer {model}"
                    else:
                        assert result["status"] == "error"
                        assert result["content"] == f"failed {model}"
        assert sorted(calls) == sorted(models)
    finally:
        release_async.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for event in finished.values():
            assert await asyncio.to_thread(event.wait, 5)
    for thread_id, model in zip(thread_ids, models):
        thread = memory.get_thread(thread_id)
        assert [turn.role for turn in thread.turns] == (["user", "assistant"] if outcome == "success" else ["user"])
        if outcome == "success":
            assert thread.turns[-1].content == f"answer {model}"
            assert thread.turns[-1].model_name == model
