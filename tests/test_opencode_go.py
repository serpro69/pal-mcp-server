"""Offline OpenCode Go SDK wire contracts and real PAL conversation ownership."""

import asyncio
import json
from datetime import date
from pathlib import Path
from uuid import UUID

import httpx
import pytest

import server
from config import __version__
from providers.configuration import provider_classes
from providers.errors import RegistryConfigurationError
from providers.opencode_go import OpenCodeGoProvider
from providers.registries.opencode_go import OpenCodeGoModelRegistry
from providers.registry import ModelProviderRegistry
from providers.shared import ProviderType
from scripts.generate_model_entries import generate_entries
from tests.test_conversation_storage_errors import FaultStorage
from tools.chat import ChatTool
from tools.shared.exceptions import ToolExecutionError
from utils import conversation_context, conversation_memory, model_restrictions

pytestmark = pytest.mark.no_mock_provider


def response_for(request):
    model = json.loads(request.content)["model"]
    if request.url.path.endswith("/responses"):
        return httpx.Response(
            200,
            json={
                "id": "r1",
                "object": "response",
                "model": model,
                "created_at": 0,
                "usage": {"input_tokens": 17, "output_tokens": 5, "total_tokens": 22},
                "output": [
                    {
                        "type": "message",
                        "id": "m1",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "Offline answer", "annotations": []}],
                    }
                ],
            },
        )
    return httpx.Response(
        200,
        json={
            "id": "c1",
            "object": "chat.completion",
            "created": 0,
            "model": model,
            "choices": [
                {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "Offline answer"}}
            ],
            "usage": {"prompt_tokens": 17, "completion_tokens": 5, "total_tokens": 22},
        },
    )


@pytest.fixture
async def configured(monkeypatch, tmp_path, isolated_provider_environment):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "synthetic-go-key")
    monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(OpenCodeGoProvider, "_registry", None)
    monkeypatch.setattr(OpenCodeGoProvider, "MODEL_CAPABILITIES", {})
    monkeypatch.setattr(model_restrictions, "_restriction_service", None)
    monkeypatch.setattr(conversation_memory, "get_storage", lambda: storage)
    storage = FaultStorage()
    ModelProviderRegistry.register_provider(ProviderType.OPENCODE_GO, OpenCodeGoProvider)
    provider = ModelProviderRegistry.get_provider(ProviderType.OPENCODE_GO)
    requests = []

    def capture(request):
        requests.append(request)
        thread = conversation_memory.get_thread(request.headers["x-opencode-session"])
        assert thread is not None and thread.turns[-1].role == "user"
        return response_for(request)

    provider._test_transport = httpx.MockTransport(capture)
    try:
        yield provider, requests, tmp_path
    finally:
        await provider.aclose()


async def chat(configured, model="glm-5.2", *, direct=False, **extra):
    _, _, path = configured
    args = {
        "prompt": "Explain a small Python function.",
        "model": model,
        "working_directory_absolute_path": str(path),
        **extra,
    }
    result = await ChatTool().execute(args) if direct else await server.handle_call_tool("chat", args)
    return json.loads(result[0].text)


@pytest.mark.parametrize(
    "model,path,field,ceiling",
    [
        ("glm-5.2", "/zen/go/v1/chat/completions", "max_tokens", 65536),
        ("gpt-6-luna", "/zen/go/v1/responses", "max_output_tokens", 128000),
    ],
)
@pytest.mark.parametrize("direct", [False, True])
async def test_first_and_continued_wire_identity(configured, model, path, field, ceiling, direct):
    provider, requests, _ = configured
    first = await chat(configured, model, direct=direct, temperature=0.25)
    thread = first["continuation_offer"]["continuation_id"]
    assert str(UUID(thread)) == thread
    second = await chat(configured, model, direct=direct, continuation_id=thread)
    assert second["continuation_offer"]["continuation_id"] == thread
    assert len(conversation_memory.get_thread(thread).turns) == 4
    assert len(requests) == 2
    for request in requests:
        payload = json.loads(request.content)
        assert str(request.url) == "https://opencode.ai" + path
        assert request.headers["authorization"] == "Bearer synthetic-go-key"
        assert request.headers["user-agent"] == f"pal-mcp-server/{__version__}"
        assert request.headers["x-opencode-session"] == thread
        assert payload["model"] == model and payload[field] == ceiling
        assert "extra_headers" not in payload and "store" not in payload
        if model == "glm-5.2":
            assert payload["temperature"] == 1.0 and payload["stream"] is False
        else:
            assert "temperature" not in payload and "messages" not in payload
            assert payload["reasoning"] == {"effort": "medium"}
    assert "x-opencode-session" not in provider._client.default_headers
    assert conversation_context.current_conversation_id() is None


async def test_concurrent_threads_keep_headers_and_reset(configured):
    provider, _, _ = configured
    both = asyncio.Event()
    requests = []

    async def capture(request):
        requests.append(request)
        if len(requests) == 2:
            both.set()
        await asyncio.wait_for(both.wait(), timeout=5)
        return response_for(request)

    provider._test_transport = httpx.MockTransport(capture)
    results = await asyncio.gather(chat(configured, prompt="alpha"), chat(configured, "gpt-6-luna", prompt="beta"))
    threads = {result["continuation_offer"]["continuation_id"] for result in results}
    assert len(threads) == 2
    assert {request.headers["x-opencode-session"] for request in requests} == threads
    for request in requests:
        thread = conversation_memory.get_thread(request.headers["x-opencode-session"])
        sentinel = thread.turns[0].content
        assert sentinel in request.content.decode()
        assert ("beta" if sentinel == "alpha" else "alpha") not in request.content.decode()
    assert conversation_context.current_conversation_id() is None


async def test_cancelled_call_resets_context(configured):
    provider, _, _ = configured
    started = asyncio.Event()

    async def hang(request):
        started.set()
        await asyncio.Future()

    provider._test_transport = httpx.MockTransport(hang)

    async def invoke():
        try:
            await chat(configured)
        finally:
            assert conversation_context.current_conversation_id() is None

    task = asyncio.create_task(invoke())
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "x-opencode-session" not in provider._client.default_headers


async def test_provider_failure_resets_context(configured):
    provider, _, _ = configured
    provider._test_transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"error": "unauthorized"}))
    with pytest.raises(ToolExecutionError):
        await chat(configured)
    assert conversation_context.current_conversation_id() is None


@pytest.mark.parametrize("tool", ["thinkdeep", "consensus"])
async def test_workflow_provider_requests_use_the_owned_thread(configured, tool):
    _, requests, _ = configured
    args = {
        "model": "glm-5.2",
        "step": "Evaluate a small Python change.",
        "findings": "Initial findings",
        "step_number": 1,
        "total_steps": 1,
        "next_step_required": False,
    }
    if tool == "consensus":
        args["models"] = [{"model": "glm-5.2"}, {"model": "gpt-6-luna"}]
    first = json.loads((await server.handle_call_tool(tool, args))[0].text)
    thread = first["continuation_offer"]["continuation_id"]
    assert len(requests) == 1 and requests[0].headers["x-opencode-session"] == thread
    if tool == "consensus":
        await server.handle_call_tool(
            tool,
            {
                "continuation_id": thread,
                "step": "Continue consultation.",
                "findings": "First response reviewed",
                "step_number": 2,
                "total_steps": 2,
                "next_step_required": False,
            },
        )
        assert len(requests) == 2 and requests[1].headers["x-opencode-session"] == thread
        assert requests[1].url.path.endswith("/responses")
    assert conversation_context.current_conversation_id() is None


@pytest.mark.parametrize(
    "identity", [None, "", "bad\r\nInjected: yes", "../thread", "00000000-0000-0000-0000-00000000000A"]
)
async def test_missing_or_unsafe_session_fails_before_client(configured, monkeypatch, identity):
    provider, requests, _ = configured
    monkeypatch.setattr(conversation_context, "current_conversation_id", lambda: identity)
    with pytest.raises(ValueError, match="conversation"):
        await provider.generate_content("hello", "glm-5.2", max_output_tokens=64)
    assert provider._client is None and not requests


async def test_lower_output_cap_and_usage(configured, monkeypatch):
    provider, _, _ = configured
    # Enter through the real tool to establish ownership; lower limit remains a provider API control.
    original = provider.generate_content
    responses = []

    async def bounded(**kwargs):
        kwargs["max_output_tokens"] = 64
        response = await original(**kwargs)
        responses.append(response)
        return response

    monkeypatch.setattr(provider, "generate_content", bounded)
    await chat(configured, "gpt-6-luna")
    assert responses[0].usage == {"input_tokens": 17, "output_tokens": 5, "total_tokens": 22}
    assert json.loads(configured[1][0].content)["max_output_tokens"] == 64


@pytest.mark.parametrize(
    "allowed,disabled,callable_model,visible",
    [
        ("", "", True, False),
        ("glm-5.2", "", True, True),
        ("gpt-6-luna", "", False, False),
        ("glm-5.2", "opencode-go:glm-5.2", False, False),
    ],
)
async def test_hidden_allowlist_deny_policy(configured, monkeypatch, allowed, disabled, callable_model, visible):
    provider, requests, _ = configured
    monkeypatch.setenv("OPENCODE_GO_ALLOWED_MODELS", allowed)
    monkeypatch.setenv("DISABLED_MODELS", disabled)
    monkeypatch.setattr(model_restrictions, "_restriction_service", None)
    provider.allowed_models = provider._parse_allowed_models()
    assert provider.validate_model_name("glm-5.2") is callable_model
    assert ("glm-5.2" in provider.list_models()) is visible
    if callable_model:
        await chat(configured)
        assert requests
    else:
        with pytest.raises(ToolExecutionError):
            await chat(configured)
        assert not requests


def test_unconfigured_provider_stays_inactive(isolated_provider_environment):
    assert OpenCodeGoProvider in provider_classes()
    server.configure_providers()
    assert ModelProviderRegistry.get_provider(ProviderType.OPENCODE_GO) is None
    assert "OPENCODE_GO_API_KEY" in OpenCodeGoProvider.CONFIGURATION.setup_hint()


def test_container_accepts_only_opencode_go_credentials(monkeypatch):
    from docker.scripts import healthcheck

    monkeypatch.setattr(
        healthcheck, "get_env", lambda name: "synthetic-go-test-key" if name == "OPENCODE_GO_API_KEY" else None
    )
    assert healthcheck.check_environment()


@pytest.mark.parametrize(
    "change",
    [
        {"api_protocol": "messages"},
        {"api_protocol": None},
        {"api_protocol": "responses", "use_openai_response_api": False},
        {"output_token_parameter": "max_completion_tokens"},
    ],
)
def test_unsupported_or_conflicting_protocol_is_configuration_error(tmp_path, change):
    entry = {"model_name": "test-model", "api_protocol": "chat_completions", **change}
    path = tmp_path / "go.json"
    path.write_text(json.dumps({"models": [entry]}))
    with pytest.raises(RegistryConfigurationError, match="go.json"):
        OpenCodeGoModelRegistry(str(path))


def test_catalog_candidates_remain_hidden_and_generator_requires_protocol():
    path = Path(__file__).resolve().parents[1] / "conf" / "opencode_go_models.json"
    catalog = json.loads(path.read_text())
    assert all(not entry["enabled_by_default"] for entry in catalog["models"])
    evidence = {
        "schema_version": 1,
        "provider": "opencode-go",
        "source_url": "https://opencode.ai/docs/go/",
        "observed_on": "2026-10-12",
        "complete": True,
        "models": [
            {
                "id": "candidate",
                "status": "active",
                "capabilities": {
                    "api_protocol": "chat_completions",
                    "context_window": 10000,
                    "max_output_tokens": 1000,
                },
            }
        ],
    }
    proposal = generate_entries(catalog, evidence, "opencode-go", date(2026, 10, 12))
    candidate = proposal["registry"]["models"][-1]
    assert candidate["api_protocol"] == "chat_completions" and candidate["enabled_by_default"] is False
