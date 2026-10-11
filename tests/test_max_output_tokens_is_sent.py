"""Caller propagation and pre-I/O rejection complement the actual SDK wire matrix."""

import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

import server
from providers.dial import DIALModelProvider
from providers.gemini import GeminiModelProvider
from providers.openai import OpenAIModelProvider
from providers.openrouter import OpenRouterProvider
from tests.request_capture import capture_providers
from tests.test_conversation_storage_errors import FaultStorage
from tools.shared.exceptions import ToolExecutionError
from utils import conversation_memory as memory


@pytest.mark.parametrize("path", ["primary", "recovery", "expert", "consensus"])
async def test_caller_sends_its_reservation(monkeypatch, tmp_path, path):
    storage = FaultStorage()
    monkeypatch.setattr(memory, "get_storage", lambda: storage)
    monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(tmp_path))
    calls = capture_providers(monkeypatch)
    if path == "recovery":

        async def generate(self, **kwargs):
            from providers.shared import ModelResponse

            calls.append(kwargs)
            return ModelResponse(
                content="" if len(calls) == 1 else "answer",
                model_name=kwargs["model_name"],
                provider="google",
                metadata={"finish_reason": "STOP"},
            )

        monkeypatch.setattr(GeminiModelProvider, "generate_content", generate)
    if path in {"primary", "recovery"}:
        result = await server.handle_call_tool(
            "chat",
            {
                "model": "gemini-2.5-flash",
                "prompt": "original proposal",
                "working_directory_absolute_path": str(tmp_path),
            },
        )
    else:
        result = await server.handle_call_tool(
            "consensus" if path == "consensus" else "thinkdeep",
            {
                "model": "gemini-2.5-flash",
                "step": "original proposal",
                "findings": "findings",
                "step_number": 1,
                "total_steps": 1,
                "next_step_required": False,
                **({"models": [{"model": "gemini-2.5-flash"}, {"model": "gpt-5"}]} if path == "consensus" else {}),
            },
        )
    assert calls[0]["max_output_tokens"] == 65536
    if path == "recovery":
        assert len(calls) == 2
        assert calls[1]["max_output_tokens"] == 65536
        assert len(calls[1]["prompt"]) > len(calls[0]["prompt"])
    if path == "consensus":
        payload = json.loads(result[0].text)
        thread_id = payload["continuation_offer"]["continuation_id"]
        await server.handle_call_tool(
            "consensus",
            {
                "continuation_id": thread_id,
                "step": "FORBIDDEN_SUMMARY" * 2000,
                "findings": "FORBIDDEN_FINDINGS",
                "step_number": 2,
                "total_steps": 2,
                "next_step_required": False,
            },
        )
        assert len(calls) == 2
        assert calls[1]["model_name"] == "gpt-5"
        assert calls[1]["max_output_tokens"] == 80000
        assert "original proposal" in calls[1]["prompt"]
        assert "FORBIDDEN" not in calls[1]["prompt"]


@pytest.mark.parametrize("kind", ["openai", "responses", "gemini", "dial", "azure", "openrouter", "custom", "xai"])
@pytest.mark.parametrize("invalid", ["capacity", "field", "input", "caller"])
async def test_invalid_requests_never_allocate_a_client(monkeypatch, kind, invalid):
    if kind in {"azure", "openrouter", "custom", "xai"}:
        from tests.test_provider_wire_async import make_provider

        provider, model, _, _ = make_provider(kind)
    elif kind == "gemini":
        provider, model = GeminiModelProvider("synthetic"), "gemini-2.5-flash"
    elif kind == "dial":
        provider, model = DIALModelProvider("synthetic", base_url="https://dial.invalid"), "o3"
    else:
        provider, model = OpenAIModelProvider("synthetic"), "o3-pro" if kind == "responses" else "gpt-5"
    original = provider.get_capabilities(model)
    cap = replace(original)
    if invalid == "capacity":
        cap.max_output_tokens = 0
    elif invalid == "field":
        cap.output_token_parameter = "unsupported_limit"
    monkeypatch.setattr(provider, "get_capabilities", lambda _: cap)
    with pytest.raises(ValueError):
        await provider.generate_content(
            "x" * (cap.context_window * 3) if invalid == "input" else "hello",
            model,
            max_output_tokens=0 if invalid == "caller" else None,
        )
    assert provider._client is None
    if kind == "dial":
        assert provider._http_client is None


async def test_unknown_openrouter_model_fails_before_io():
    provider = OpenRouterProvider("synthetic")
    with pytest.raises(ValueError, match="Unverified token capacities"):
        await provider.generate_content("hello", "unregistered/model")
    assert provider._client is None


async def test_recovery_rechecks_grown_payload_before_io(monkeypatch, tmp_path):
    storage = FaultStorage()
    monkeypatch.setattr(memory, "get_storage", lambda: storage)
    monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(tmp_path))
    capture_providers(monkeypatch)
    from providers.shared import ModelResponse
    from utils.model_context import ModelContext

    original = GeminiModelProvider("synthetic").get_capabilities("gemini-2.5-flash")
    monkeypatch.setattr(
        GeminiModelProvider,
        "get_capabilities",
        lambda self, name: replace(original, context_window=20000, max_output_tokens=8000),
    )
    generate = AsyncMock(
        return_value=ModelResponse(
            content="", model_name="gemini-2.5-flash", provider="google", metadata={"finish_reason": "STOP"}
        )
    )
    monkeypatch.setattr(GeminiModelProvider, "generate_content", generate)

    # Recompute from the retry payload, not its admitted primary prompt.
    def estimate(self, text):
        if "Please provide a substantive response." in text:
            return 20000
        return 100

    monkeypatch.setattr(ModelContext, "estimate_tokens", estimate)
    with pytest.raises(ToolExecutionError, match="input budget"):
        await server.handle_call_tool(
            "chat", {"model": "gemini-2.5-flash", "prompt": "hello", "working_directory_absolute_path": str(tmp_path)}
        )
    generate.assert_awaited_once()


async def test_retry_with_changed_model_recomputes_reservation(monkeypatch, tmp_path):
    storage = FaultStorage()
    monkeypatch.setattr(memory, "get_storage", lambda: storage)
    monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(tmp_path))

    def fail(kwargs):
        raise RuntimeError("synthetic failure")

    calls = capture_providers(monkeypatch, google_answer=fail)
    thread_id = memory.create_thread("chat", {})
    args = {"prompt": "retry me", "continuation_id": thread_id, "working_directory_absolute_path": str(tmp_path)}
    with pytest.raises(ToolExecutionError, match="synthetic failure"):
        await server.handle_call_tool("chat", {**args, "model": "gemini-2.5-flash"})
    await server.handle_call_tool("chat", {**args, "model": "gpt-5"})
    assert [call["max_output_tokens"] for call in calls] == [65536, 80000]
    assert [call["model_name"] for call in calls] == ["gemini-2.5-flash", "gpt-5"]


@pytest.mark.parametrize("tool", ["chat", "thinkdeep", "consensus"])
async def test_assembled_system_overflow_never_calls_provider(monkeypatch, tmp_path, tool):
    storage = FaultStorage()
    monkeypatch.setattr(memory, "get_storage", lambda: storage)
    monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(tmp_path))
    calls = capture_providers(monkeypatch)
    from tools.chat import ChatTool
    from tools.consensus import ConsensusTool
    from tools.thinkdeep import ThinkDeepTool

    cls = {"chat": ChatTool, "thinkdeep": ThinkDeepTool, "consensus": ConsensusTool}[tool]
    method = "_get_stance_enhanced_prompt" if tool == "consensus" else "get_system_prompt"
    monkeypatch.setattr(cls, method, lambda *args: "x" * 3_000_000)
    args = {"model": "gemini-2.5-flash"}
    if tool == "chat":
        args.update(prompt="hello", working_directory_absolute_path=str(tmp_path))
    else:
        args.update(step="hello", findings="findings", step_number=1, total_steps=1, next_step_required=False)
        if tool == "consensus":
            args["models"] = [{"model": "gemini-2.5-flash"}]
    if tool == "consensus":
        result = await server.handle_call_tool(tool, args)
        parsed = json.loads(result[0].text)
        assert parsed["complete_consensus"]["status"] == "failed"
        assert "input budget" in parsed["model_response"]["error"]
    elif tool == "thinkdeep":
        result = await server.handle_call_tool(tool, args)
        parsed = json.loads(result[0].text)
        assert parsed["status"] == "error"
        assert "input budget" in parsed["content"]
        assert parsed["metadata"]["model_used"] is None
    else:
        with pytest.raises(ToolExecutionError, match="input budget"):
            await server.handle_call_tool(tool, args)
    assert not calls
