"""Discovery, prompts, and independent calls agree on the startup capability snapshot."""

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

import config
import server
from providers.configuration import provider_classes
from providers.registry import ModelProviderRegistry
from tests.test_cli_only_startup import install_fake_cli
from tests.test_cli_only_startup import isolated_startup as isolated_startup
from tools.listmodels import ListModelsTool
from tools.shared.exceptions import ToolExecutionError
from utils import conversation_memory as memory
from utils.runtime_capabilities import get_runtime_capabilities

INDEPENDENT = {"clink", "version", "listmodels", "planner", "tracer", "docgen", "challenge", "apilookup", "jules"}
CONVERSATIONS = {"clink", "planner", "tracer", "docgen"}


@pytest.fixture
def provider_free(isolated_startup, monkeypatch, tmp_path):
    manifests, storage = isolated_startup
    capture = install_fake_cli(manifests, tmp_path)
    monkeypatch.setenv("JULES_API_KEY", "synthetic-jules-only")
    snapshot = server.initialize_runtime()
    blocked = Mock(side_effect=AssertionError("Independent tools must not perform provider inference"))
    for factory in provider_classes():
        monkeypatch.setattr(factory, "generate_content", blocked)
    monkeypatch.setattr("utils.model_context.ModelContext", blocked)
    real_client = httpx.AsyncClient
    requests, clients = [], []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"sources": [{"name": "sources/github/test/repo"}]})

    def client_factory(**kwargs):
        client = real_client(transport=httpx.MockTransport(respond), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr("tools.jules.httpx.AsyncClient", client_factory)
    yield snapshot, storage, capture, requests
    blocked.assert_not_called()
    assert all(client.is_closed for client in clients)


def prompt_name(name):
    return server.PROMPT_TEMPLATES.get(name, {}).get("name", name)


def prompt_text(result):
    return result.messages[0].content.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,arguments",
    [
        ("clink", {"cli_name": "gemini", "prompt": "CLI_INPUT"}),
        ("version", {}),
        ("listmodels", {}),
        ("planner", {"step": "plan", "step_number": 1, "total_steps": 2, "next_step_required": True}),
        (
            "tracer",
            {
                "step": "trace",
                "step_number": 1,
                "total_steps": 2,
                "next_step_required": True,
                "findings": "start",
                "target_description": "function",
                "trace_mode": "precision",
            },
        ),
        (
            "docgen",
            {
                "step": "discover",
                "step_number": 1,
                "total_steps": 2,
                "next_step_required": True,
                "findings": "start",
                "total_files_to_document": 1,
                "num_files_documented": 0,
            },
        ),
        ("challenge", {"prompt": "CRITICAL_CLAIM"}),
        ("apilookup", {"prompt": "LOOKUP_REQUEST"}),
        ("jules", {"action": "list_sources"}),
    ],
)
async def test_all_nine_tools_are_listed_prompted_and_callable_without_providers(provider_free, name, arguments):
    snapshot, storage, capture, requests = provider_free
    assert snapshot.provider_free and snapshot.available_tools == INDEPENDENT
    assert {tool.name for tool in await server.handle_list_tools()} == INDEPENDENT
    names = {prompt.name for prompt in await server.handle_list_prompts()}
    assert names == {prompt_name(tool) for tool in INDEPENDENT} | {"continue"}
    instruction = prompt_text(await server.handle_get_prompt(prompt_name(name), {"model": "UNAVAILABLE_MODEL"}))
    assert name in instruction.lower()
    assert "UNAVAILABLE_MODEL" not in instruction
    result = json.loads((await server.handle_call_tool(name, arguments))[0].text)
    assert result.get("status") != "error"
    if name in {"challenge", "apilookup", "jules"}:
        assert result.get("continuation_offer") is None
        assert not storage._store
    if name == "challenge":
        assert result["status"] == "challenge_accepted"
        assert "CRITICAL_CLAIM" in result["challenge_prompt"]
    elif name == "apilookup":
        assert result["status"] == "web_lookup_needed" and result["user_prompt"] == "LOOKUP_REQUEST"
    elif name == "jules":
        assert result["sources"] == [{"name": "sources/github/test/repo"}]
        assert requests[0].headers["x-goog-api-key"] == "synthetic-jules-only"
        assert requests[0].method == "GET"
    elif name == "clink":
        assert result["content"] == "FAKE_CLI_REPLY"
        assert "CLI_INPUT" in capture.read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    sorted(
        {
            spelling
            for tool, template in server.PROMPT_TEMPLATES.items()
            if tool not in INDEPENDENT
            for spelling in (tool, template["name"])
        }
    ),
)
async def test_unavailable_canonical_names_and_aliases_cannot_bypass_discovery(provider_free, name):
    assert name not in {prompt.name for prompt in await server.handle_list_prompts()}
    with pytest.raises(ToolExecutionError, match="model_provider_required"):
        await server.handle_get_prompt(name, {"model": "gpt-5"})
    if name in server.ALL_TOOLS:
        with pytest.raises(ToolExecutionError, match="model_provider_required"):
            await server.handle_call_tool(name, {})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "disabled",
    [
        {"clink"},
        {"planner", "docgen", "challenge", "jules"},
        {"version", "listmodels"},
        CONVERSATIONS,
        INDEPENDENT,
    ],
)
async def test_disabled_and_essential_tool_rules_apply_to_all_surfaces(provider_free, monkeypatch, disabled):
    monkeypatch.setattr(server, "TOOLS", server.apply_tool_filter(server.ALL_TOOLS, disabled))
    snapshot = server.initialize_runtime()
    expected = INDEPENDENT - (disabled - server.ESSENTIAL_TOOLS)
    assert snapshot.available_tools == expected
    assert {tool.name for tool in await server.handle_list_tools()} == expected
    names = {prompt.name for prompt in await server.handle_list_prompts()}
    assert names == {prompt_name(tool) for tool in expected} | ({"continue"} if expected & CONVERSATIONS else set())
    for name in disabled - server.ESSENTIAL_TOOLS:
        with pytest.raises(ToolExecutionError, match="tool_disabled"):
            await server.handle_get_prompt(prompt_name(name))
        with pytest.raises(ToolExecutionError, match="tool_disabled"):
            await server.handle_call_tool(name, {})


@pytest.mark.asyncio
@pytest.mark.parametrize("disabled", [set(), {"clink"}, {"planner", "tracer", "docgen"}, CONVERSATIONS])
async def test_continue_uses_only_available_pal_conversation_tools(provider_free, monkeypatch, disabled):
    monkeypatch.setattr(server, "TOOLS", server.apply_tool_filter(server.ALL_TOOLS, disabled))
    server.initialize_runtime()
    targets = CONVERSATIONS - disabled
    if not targets:
        with pytest.raises(ValueError, match="No available tools support PAL conversation continuation"):
            await server.handle_get_prompt("continue")
        return
    text = prompt_text(await server.handle_get_prompt("continue"))
    assert "same available conversation tool" in text
    assert "latest continuation_id" in text
    for name in targets:
        assert name in text
    for name in {"chat", "challenge", "apilookup", "jules"} | disabled:
        assert name not in text
    if "clink" in targets:
        assert "cli_name and role may be omitted" in text and "explicit overrides" in text


@pytest.mark.asyncio
async def test_docgen_first_followup_and_completion_retain_local_history(provider_free):
    _, storage, _, _ = provider_free
    base = {"total_steps": 2, "total_files_to_document": 1, "num_files_documented": 0}
    first = json.loads(
        (
            await server.handle_call_tool(
                "docgen",
                {
                    **base,
                    "step": "DISCOVER_INPUT",
                    "findings": "DISCOVER_FINDINGS",
                    "step_number": 1,
                    "next_step_required": True,
                },
            )
        )[0].text
    )
    thread_id = first["continuation_offer"]["continuation_id"]
    second = json.loads(
        (
            await server.handle_call_tool(
                "docgen",
                {
                    **base,
                    "continuation_id": thread_id,
                    "step": "DOCUMENT_INPUT",
                    "findings": "DOCUMENT_FINDINGS",
                    "step_number": 2,
                    "next_step_required": False,
                },
            )
        )[0].text
    )
    assert second["status"] == "documentation_analysis_required"
    assert second["continuation_offer"]["continuation_id"] == thread_id
    third = json.loads(
        (
            await server.handle_call_tool(
                "docgen",
                {
                    **base,
                    "continuation_id": thread_id,
                    "step": "COMPLETE_INPUT",
                    "findings": "COMPLETE_FINDINGS",
                    "step_number": 3,
                    "next_step_required": False,
                    "num_files_documented": 1,
                },
            )
        )[0].text
    )
    assert third["status"] == "documentation_analysis_complete"
    assert third["documentation_analysis_complete"] is True
    assert "no external analysis was performed" in third["next_steps"]
    assert "expert_analysis" not in third
    turns = memory.get_thread(thread_id).turns
    assert len(storage._store) == 1 and len(turns) == 6
    assert [turn.content for turn in turns if turn.role == "user"] == [
        "DISCOVER_INPUT",
        "DOCUMENT_INPUT",
        "COMPLETE_INPUT",
    ]
    assert all(turn.model_provider is None and turn.model_name is None for turn in turns)


@pytest.mark.asyncio
async def test_provider_free_listmodels_uses_only_snapshot_and_distinguishes_setup(provider_free, monkeypatch):
    monkeypatch.setenv("CUSTOM_API_KEY", "PRIVATE_CUSTOM_KEY")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://private-endpoint.invalid")
    snapshot = server.initialize_runtime()
    blocked = Mock(side_effect=AssertionError("Provider-free listing must use only the startup snapshot"))
    monkeypatch.setattr(ModelProviderRegistry, "get_provider", blocked)
    monkeypatch.setattr(ModelProviderRegistry, "get_available_models", blocked)
    monkeypatch.setattr("clink.availability.resolve_executable", blocked)
    first = json.loads((await ListModelsTool().execute({}))[0].text)
    content, metadata = first["content"], first["metadata"]
    assert "No PAL model providers are configured" in content
    assert "**Custom/Local API**: Incomplete settings; missing CUSTOM_API_URL" in content
    assert "**Azure OpenAI**: Incomplete settings; missing AZURE_OPENAI_API_KEY" in content
    assert "**OpenCode Go**: Absent settings; set OPENCODE_GO_API_KEY" in content
    assert "authentication unverified" in content and "startup" in content
    assert "not PAL API model identifiers" in content
    assert "PRIVATE_CUSTOM_KEY" not in str(first) and "private-endpoint.invalid" not in str(first)
    assert metadata["configured_providers"] == 0 and metadata["mode"] == "provider-free"
    assert metadata["available_tools"] == sorted(INDEPENDENT)
    assert {item["name"]: item["executable_found"] for item in metadata["cli_clients"]} == {
        "gemini": True,
        "claude": False,
        "codex": False,
    }
    assert all(item["authentication"] == "unverified" for item in metadata["cli_clients"])
    monkeypatch.setenv("GEMINI_API_KEY", "new-key-needs-restart")
    assert json.loads((await ListModelsTool().execute({}))[0].text) == first
    assert get_runtime_capabilities() is snapshot
    blocked.assert_not_called()


async def handshake(monkeypatch):
    @asynccontextmanager
    async def stdio():
        yield object(), object()

    run = AsyncMock()
    monkeypatch.setattr(server, "stdio_server", stdio)
    monkeypatch.setattr(server.server, "run", run)
    await server._serve()
    return run.call_args.args[2].instructions


@pytest.mark.asyncio
@pytest.mark.parametrize("clink_enabled", [False, True])
async def test_handshake_has_provider_free_guidance_without_unavailable_model(
    provider_free, monkeypatch, clink_enabled
):
    if not clink_enabled:
        monkeypatch.setattr(server, "TOOLS", server.apply_tool_filter(server.ALL_TOOLS, {"clink"}))
    monkeypatch.setattr(config, "IS_AUTO_MODE", False)
    monkeypatch.setattr(config, "DEFAULT_MODEL", "UNAVAILABLE_DEFAULT")
    monkeypatch.setattr(server, "DEFAULT_MODEL", "UNAVAILABLE_DEFAULT")
    text = await handshake(monkeypatch)
    assert "No PAL model providers" in text and "UNAVAILABLE_DEFAULT" not in text
    assert ("clink" in text) is clink_enabled
    for name in INDEPENDENT - {"clink"}:
        assert name in text
    if clink_enabled:
        assert "requires cli_name when multiple CLI clients" in text
        assert "follow-up may omit cli_name and role" in text
        assert "authentication is unverified" in text


@pytest.mark.asyncio
async def test_missing_cli_guidance_and_disabled_clink_are_truthful(isolated_startup, monkeypatch):
    text = await handshake(monkeypatch)
    assert "No configured CLI executable was found at startup" in text
    listing = json.loads((await ListModelsTool().execute({}))[0].text)
    assert "Install or configure your selected CLI" in listing["content"]
    monkeypatch.setattr(server, "TOOLS", server.apply_tool_filter(server.ALL_TOOLS, {"clink"}))
    text = await handshake(monkeypatch)
    assert "clink" not in text and "install and authenticate" not in text
    listing = json.loads((await ListModelsTool().execute({}))[0].text)
    assert "Use clink" not in listing["content"]
    assert "clink" not in listing["metadata"]["available_tools"]


@pytest.mark.asyncio
@pytest.mark.parametrize("auto_mode", [False, True])
async def test_api_mode_listing_aliases_continue_and_handshake_remain_unchanged(
    isolated_startup, monkeypatch, tmp_path, auto_mode
):
    path = tmp_path / "custom.json"
    path.write_text('{"models":[]}')
    monkeypatch.setenv("CUSTOM_API_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("CUSTOM_MODELS_CONFIG_PATH", str(path))
    monkeypatch.setattr(config, "IS_AUTO_MODE", auto_mode)
    monkeypatch.setattr(server, "DEFAULT_MODEL", "fixed-model")
    text = await handshake(monkeypatch)
    expected = "When the user names a specific model (e.g. 'use chat with gpt5'), send that exact model in the tool call. " + (
        "When no model is mentioned, first use the `listmodels` tool from PAL to obtain available models to choose the best one from."
        if auto_mode
        else "When no model is mentioned, default to 'fixed-model'."
    )
    assert text == expected
    assert {tool.name for tool in await server.handle_list_tools()} == set(server.TOOLS)
    assert (
        prompt_text(await server.handle_get_prompt("review", {"model": "chosen"}))
        == "Perform a comprehensive code review with chosen"
    )
    assert prompt_text(await server.handle_get_prompt("continue")) == (
        "Continue the previous conversation using the chat tool. "
        "CRITICAL: You MUST provide the continuation_id from the previous response to maintain conversation context. "
        "Additionally, you should reuse the same model that was used in the previous exchange for consistency, unless "
        "the user specifically asks for a different model name to be used."
    )
    listing = json.loads((await ListModelsTool().execute({}))[0].text)
    assert listing["content"].startswith("# Available AI Models")
    assert "**Configured Providers**: 1" in listing["content"]
    assert "**Total Available Models**: 0" in listing["content"]
    assert "mode" not in listing["metadata"]


@pytest.mark.asyncio
async def test_disabled_api_prompt_alias_and_continue_do_not_offer_disabled_tools(isolated_startup, monkeypatch):
    monkeypatch.setenv("CUSTOM_API_URL", "http://localhost:11434/v1")
    monkeypatch.setattr(server, "TOOLS", server.apply_tool_filter(server.ALL_TOOLS, {"chat", "codereview"}))
    server.initialize_runtime()
    names = {prompt.name for prompt in await server.handle_list_prompts()}
    assert "review" not in names and "continue" not in names
    for name in ("review", "codereview", "continue"):
        with pytest.raises(ToolExecutionError, match="tool_disabled"):
            await server.handle_get_prompt(name)


@pytest.mark.asyncio
async def test_unregistered_prompt_stays_unknown(provider_free):
    with pytest.raises(ValueError, match="Unknown prompt"):
        await server.handle_get_prompt("unknown-prompt")
