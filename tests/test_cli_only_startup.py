"""Startup and dispatch capabilities with isolated declarations and fake CLIs."""

import asyncio
import json
import os
import shlex
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

import config
import server
from clink.agents.base import BaseCLIAgent, CLIAgentError
from clink.availability import build_cli_environment, discover_cli_availability, resolve_executable
from clink.registry import get_registry
from providers.configuration import provider_classes
from providers.errors import RegistryConfigurationError
from providers.registry import ModelProviderRegistry
from providers.shared import ProviderType
from tests.test_clink_environment import client
from tools.shared.exceptions import ToolExecutionError
from utils import conversation_coordination as coordination
from utils import conversation_memory as memory
from utils import runtime_capabilities as capabilities
from utils.model_restrictions import get_restriction_service
from utils.storage_backend import InMemoryStorage

INDEPENDENT = {"clink", "version", "listmodels", "planner", "tracer", "docgen", "challenge", "apilookup", "jules"}


@pytest.fixture
def isolated_startup(monkeypatch, tmp_path, isolated_provider_environment):
    capabilities.reset_runtime_capabilities()
    for factory in provider_classes():
        monkeypatch.setattr(factory, "_registry", getattr(factory, "_registry", None), raising=False)
        monkeypatch.setattr(factory, "MODEL_CAPABILITIES", factory.MODEL_CAPABILITIES)
    monkeypatch.setattr(config, "IS_AUTO_MODE", True)
    monkeypatch.setattr(config, "DEFAULT_MODEL", "auto")
    monkeypatch.setattr(server, "DEFAULT_MODEL", "auto")
    monkeypatch.setattr(server, "TOOLS", dict(server.ALL_TOOLS))
    monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("PAL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PAL_TRANSCRIPTS", "false")
    monkeypatch.delenv("JULES_API_KEY", raising=False)
    manifests = tmp_path / "clients"
    manifests.mkdir()
    for name in ("gemini", "claude", "codex"):
        (manifests / f"{name}.json").write_text(
            json.dumps({"name": name, "command": str(tmp_path / f"missing-{name}")})
        )
    monkeypatch.setattr("clink.registry.CONFIG_DIR", manifests)
    monkeypatch.setattr("clink.registry.USER_CONFIG_DIR", tmp_path / "unused-user-config")
    monkeypatch.setattr("clink.registry.get_env", lambda name: None)
    monkeypatch.setattr("clink.registry._REGISTRY", None)
    monkeypatch.setattr(InMemoryStorage, "_cleanup_worker", lambda self: None)
    storage = InMemoryStorage()
    monkeypatch.setattr(memory, "get_storage", lambda: storage)
    yield manifests, storage
    storage.shutdown()
    capabilities.reset_runtime_capabilities()
    assert not coordination._entries


def install_fake_cli(manifests, tmp_path, name="gemini"):
    capture = tmp_path / "received.txt"
    script = tmp_path / "fake_cli.py"
    script.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "prompt=sys.stdin.read()\nPath(sys.argv[1]).write_text(prompt)\n"
        "print(json.dumps({'response':'FAKE_CLI_REPLY'}))\n"
    )
    (manifests / f"{name}.json").write_text(
        json.dumps(
            {
                "name": name,
                "command": shlex.join([sys.executable, str(script), str(capture)]),
            }
        )
    )
    return capture


def forbid_model_resolution(monkeypatch):
    blocked = Mock(side_effect=AssertionError("No PAL provider inference or model context is allowed"))
    monkeypatch.setattr(ModelProviderRegistry, "get_provider_for_model", blocked)
    monkeypatch.setattr(ModelProviderRegistry, "get_preferred_fallback_model", blocked)
    monkeypatch.setattr("utils.model_context.ModelContext", blocked)
    return blocked


@pytest.mark.parametrize("disabled", [set(), {"clink"}, {"clink", "planner", "version", "listmodels"}])
def test_provider_free_startup_preserves_enabled_independent_tools(isolated_startup, monkeypatch, disabled):
    monkeypatch.setattr(server, "TOOLS", server.apply_tool_filter(server.ALL_TOOLS, disabled))
    snapshot = server.initialize_runtime()
    assert snapshot.provider_free and not snapshot.configured_providers
    assert snapshot.enabled_tools == frozenset(server.TOOLS)
    assert snapshot.available_tools == INDEPENDENT - (disabled - server.ESSENTIAL_TOOLS)
    assert {status.name for status in snapshot.cli_statuses} == {"gemini", "claude", "codex"}
    assert not any(status.executable_found for status in snapshot.cli_statuses)
    assert all(status.status == "absent" for status in snapshot.provider_setup)
    assert capabilities.get_runtime_capabilities() is snapshot
    assert not ModelProviderRegistry()._providers


@pytest.mark.parametrize(
    "settings,expected",
    [
        ({}, {}),
        ({"CUSTOM_API_URL": "your_custom_api_url_here"}, {}),
        (
            {"CUSTOM_API_KEY": "private", "AZURE_OPENAI_ENDPOINT": "https://azure.invalid"},
            {ProviderType.CUSTOM: "incomplete", ProviderType.AZURE: "incomplete"},
        ),
        ({"AZURE_OPENAI_API_KEY": "private"}, {ProviderType.AZURE: "incomplete"}),
    ],
)
def test_startup_records_absence_and_incomplete_inputs_without_fallback_errors(
    isolated_startup, monkeypatch, settings, expected
):
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    snapshot = server.initialize_runtime()
    assert snapshot.provider_free
    assert {
        status.provider_type: status.status for status in snapshot.provider_setup if status.status != "absent"
    } == expected
    assert "private" not in repr(snapshot)


def test_api_mode_uses_registration_despite_empty_restricted_catalog(isolated_startup, monkeypatch, tmp_path, caplog):
    path = tmp_path / "custom.json"
    path.write_text(
        json.dumps({"models": [{"model_name": "local", "context_window": 10000, "max_output_tokens": 1000}]})
    )
    monkeypatch.setenv("CUSTOM_API_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("CUSTOM_MODELS_CONFIG_PATH", str(path))
    monkeypatch.setenv("CUSTOM_ALLOWED_MODELS", "unavailable-model")
    snapshot = server.initialize_runtime()
    assert snapshot.configured_providers == {ProviderType.CUSTOM}
    assert not snapshot.provider_free
    assert snapshot.available_tools == snapshot.enabled_tools
    assert ModelProviderRegistry.get_available_models(respect_restrictions=True) == {}
    assert "No models available for auto mode after restrictions" in caplog.text
    assert snapshot.unavailable_reason("chat") is None
    monkeypatch.delenv("CUSTOM_API_URL")
    assert capabilities.get_runtime_capabilities() is snapshot
    assert not snapshot.provider_free  # Environment edits do not change the startup mode.


def test_opencode_registration_with_other_partial_settings_remains_api_mode(isolated_startup, monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "synthetic")
    monkeypatch.setenv("CUSTOM_API_KEY", "private")
    snapshot = server.initialize_runtime()
    assert not snapshot.provider_free and snapshot.configured_providers == {ProviderType.OPENCODE_GO}
    assert (
        next(item for item in snapshot.provider_setup if item.provider_type == ProviderType.CUSTOM).status
        == "incomplete"
    )


def test_complete_azure_input_without_successful_registration_stays_provider_free(
    isolated_startup, monkeypatch, tmp_path, caplog
):
    path = tmp_path / "azure.json"
    path.write_text('{"models":[]}')
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "synthetic")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://azure.invalid")
    monkeypatch.setenv("AZURE_MODELS_CONFIG_PATH", str(path))
    snapshot = server.initialize_runtime()
    assert snapshot.provider_free
    assert (
        next(item for item in snapshot.provider_setup if item.provider_type == ProviderType.AZURE).status == "complete"
    )
    assert "Azure OpenAI models configuration is empty" in caplog.text


@pytest.mark.parametrize("with_credentials", [False, True])
def test_fatal_catalog_errors_do_not_publish_a_provider_free_fallback(
    isolated_startup, monkeypatch, tmp_path, with_credentials
):
    capabilities.initialize_runtime_capabilities(configured_providers=[ProviderType.GOOGLE], enabled_tools=server.TOOLS)
    path = tmp_path / "broken.json"
    path.write_text("{bad")
    monkeypatch.setenv("CUSTOM_MODELS_CONFIG_PATH", str(path))
    if with_credentials:
        monkeypatch.setenv("CUSTOM_API_URL", "http://localhost:11434/v1")
    with pytest.raises(RegistryConfigurationError, match="broken.json"):
        server.initialize_runtime()
    with pytest.raises(RuntimeError, match="not been initialized"):
        capabilities.get_runtime_capabilities()


def test_existing_restriction_validation_exception_stays_fatal(isolated_startup, monkeypatch):
    monkeypatch.setenv("CUSTOM_API_URL", "http://localhost:11434/v1")
    failure = ValueError("restriction validation failed")
    monkeypatch.setattr(get_restriction_service(), "validate_against_known_models", Mock(side_effect=failure))
    with pytest.raises(ValueError) as caught:
        server.initialize_runtime()
    assert caught.value is failure
    with pytest.raises(RuntimeError, match="not been initialized"):
        capabilities.get_runtime_capabilities()


def test_startup_discovery_does_not_execute_or_probe_login(isolated_startup, monkeypatch, tmp_path):
    manifests, _ = isolated_startup
    capture = install_fake_cli(manifests, tmp_path)
    blocked = Mock(side_effect=AssertionError("Discovery cannot start a process"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", blocked)
    monkeypatch.setattr(BaseCLIAgent, "run", blocked)
    snapshot = server.initialize_runtime()
    assert {item.name for item in snapshot.cli_statuses if item.executable_found} == {"gemini"}
    assert not capture.exists()
    blocked.assert_not_called()


@pytest.mark.parametrize("disabled_clink", [False, True])
@pytest.mark.asyncio
async def test_serve_initializes_capabilities_before_stdio_without_providers(
    isolated_startup, monkeypatch, disabled_clink
):
    if disabled_clink:
        monkeypatch.setattr(server, "TOOLS", server.apply_tool_filter(server.ALL_TOOLS, {"clink"}))
    observed = []

    @asynccontextmanager
    async def stdio():
        observed.append(capabilities.get_runtime_capabilities())
        yield object(), object()

    monkeypatch.setattr(server, "stdio_server", stdio)
    monkeypatch.setattr(server.server, "run", AsyncMock())
    await server._serve()
    assert len(observed) == 1 and observed[0].provider_free
    assert ("clink" in observed[0].available_tools) is (not disabled_clink)
    server.server.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_fake_cli_first_reply_needs_no_provider_or_model_context(isolated_startup, monkeypatch, tmp_path):
    manifests, storage = isolated_startup
    capture = install_fake_cli(manifests, tmp_path)
    blocked = forbid_model_resolution(monkeypatch)
    snapshot = server.initialize_runtime()
    result = await server.handle_call_tool("clink", {"cli_name": "gemini", "prompt": "FIRST_INPUT_MARKER"})
    payload = json.loads(result[0].text)
    assert payload["content"] == "FAKE_CLI_REPLY"
    assert payload["metadata"]["history_saved"] is True
    assert capture.read_text().count("FIRST_INPUT_MARKER") == 1
    assert snapshot.provider_free and not ModelProviderRegistry()._initialized_providers
    assert len(storage._store) == 1
    blocked.assert_not_called()


@pytest.mark.parametrize("name", sorted(set(server.ALL_TOOLS) - INDEPENDENT))
@pytest.mark.parametrize("disabled_clink", [False, True])
@pytest.mark.asyncio
async def test_unavailable_tool_rejected_before_coordination_history_or_dispatch(
    isolated_startup, monkeypatch, name, disabled_clink
):
    _, storage = isolated_startup
    if disabled_clink:
        monkeypatch.setattr(server, "TOOLS", server.apply_tool_filter(server.ALL_TOOLS, {"clink"}))
    snapshot = server.initialize_runtime()
    blocked = Mock(side_effect=AssertionError("Unavailable tools must not process conversations"))
    monkeypatch.setattr(coordination, "coordinate_conversation", blocked)
    monkeypatch.setattr(storage, "get", blocked)
    monkeypatch.setattr(storage, "setex", blocked)
    monkeypatch.setattr(storage, "compare_and_setex", blocked)
    monkeypatch.setattr(server, "_dispatch_tool", blocked)
    with pytest.raises(ToolExecutionError) as error:
        await server.handle_call_tool(name, {"continuation_id": str(uuid4())})
    payload = json.loads(error.value.payload)
    assert payload["status"] == "error" and payload["continuation_offer"] is None
    assert payload["metadata"]["error_type"] == "model_provider_required"
    assert payload["metadata"]["available_tools"] == sorted(snapshot.available_tools)
    assert "PAL model provider" in payload["content"]
    assert ("clink" in payload["content"]) is (not disabled_clink)
    assert ("install/configure" in payload["content"]) is (not disabled_clink)
    assert payload["metadata"]["execution"]["operations"] == []
    blocked.assert_not_called()
    assert storage._store == {}


@pytest.mark.asyncio
async def test_disabled_unknown_and_provider_restriction_remain_distinct(isolated_startup, monkeypatch):
    monkeypatch.setattr(server, "TOOLS", server.apply_tool_filter(server.ALL_TOOLS, {"clink", "chat"}))
    server.initialize_runtime()
    for name in ("clink", "chat"):
        with pytest.raises(ToolExecutionError) as error:
            await server.handle_call_tool(name, {})
        payload = json.loads(error.value.payload)
        assert payload["metadata"]["error_type"] == "tool_disabled"
        assert "DISABLED_TOOLS" in payload["content"]
    unknown = await server.handle_call_tool("unknown_tool", {})
    assert "Unknown tool: unknown_tool" in unknown[0].text
    with pytest.raises(ToolExecutionError, match="model_provider_required"):
        await server.handle_call_tool("consensus", {})


@pytest.mark.parametrize(
    "name,arguments",
    [
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
            {"step": "discover", "step_number": 1, "total_steps": 2, "next_step_required": True, "findings": "start"},
        ),
        ("challenge", {"prompt": "challenge this claim"}),
        ("apilookup", {"prompt": "look up API"}),
    ],
)
@pytest.mark.asyncio
async def test_local_tools_work_with_clink_disabled_and_no_executable(isolated_startup, monkeypatch, name, arguments):
    monkeypatch.setattr(server, "TOOLS", server.apply_tool_filter(server.ALL_TOOLS, {"clink"}))
    server.initialize_runtime()
    # Local workflows may check model availability and handle its absence; they
    # must never construct API context or perform provider inference.
    blocked = Mock(side_effect=AssertionError("Local tools must not invoke a PAL model"))
    for factory in provider_classes():
        monkeypatch.setattr(factory, "generate_content", blocked)
    monkeypatch.setattr("utils.model_context.ModelContext", blocked)
    result = await server.handle_call_tool(name, arguments)
    assert result and json.loads(result[0].text).get("status") != "error"
    blocked.assert_not_called()


@pytest.mark.asyncio
async def test_selected_cli_and_jules_prerequisites_fail_locally(isolated_startup, monkeypatch, tmp_path):
    manifests, _ = isolated_startup
    capture = install_fake_cli(manifests, tmp_path)
    snapshot = server.initialize_runtime()
    assert "jules" in snapshot.available_tools and "clink" in snapshot.available_tools
    with pytest.raises(ToolExecutionError, match="not found in PATH"):
        await server.handle_call_tool("clink", {"cli_name": "codex", "prompt": "do not substitute gemini"})
    assert not capture.exists()
    with pytest.raises(ToolExecutionError, match="JULES_API_KEY"):
        await server.handle_call_tool("jules", {"action": "list_sources"})
    result = await server.handle_call_tool("version", {})
    assert json.loads(result[0].text)["status"] == "success"
    assert capabilities.get_runtime_capabilities() is snapshot


@pytest.mark.asyncio
async def test_api_mode_still_dispatches_api_tools(isolated_startup, monkeypatch):
    monkeypatch.setenv("CUSTOM_API_URL", "http://localhost:11434/v1")
    server.initialize_runtime()
    dispatch = AsyncMock(return_value=[])
    monkeypatch.setattr(server, "_dispatch_tool", dispatch)
    await server.handle_call_tool("chat", {"prompt": "request"})
    dispatch.assert_awaited_once_with("chat", {"prompt": "request"})


@pytest.mark.parametrize("configured_path", [None, "", "/operator/bin"])
def test_shared_resolver_matches_runner_path_environment(monkeypatch, configured_path):
    monkeypatch.delenv("PATH", raising=False)
    cfg = client(env={} if configured_path is None else {"PATH": configured_path})
    lookup = Mock(return_value="/resolved/python")
    monkeypatch.setattr("clink.availability.shutil.which", lookup)
    assert resolve_executable(cfg) == "/resolved/python"
    lookup.assert_called_once_with(sys.executable, path=os.defpath if configured_path is None else configured_path)
    assert build_cli_environment(cfg) == BaseCLIAgent(cfg)._build_environment()


def test_resolver_uses_configured_first_token_without_running_remaining_arguments(monkeypatch):
    cfg = client(executable=[sys.executable, "/does/not/exist.py", "--login"], env={"SECRET": "private"})
    assert resolve_executable(cfg) is not None
    registry = SimpleNamespace(list_clients=lambda: [cfg.name], get_client=lambda name: cfg)
    statuses = discover_cli_availability(registry)
    assert statuses[0].executable_found and "private" not in repr(statuses)
    assert "/does/not/exist.py" not in repr(statuses)


@pytest.mark.parametrize("executable", [[], [""]])
@pytest.mark.asyncio
async def test_empty_executable_is_configuration_error_in_discovery_and_runner(executable):
    cfg = client(executable=executable)
    registry = SimpleNamespace(list_clients=lambda: [cfg.name], get_client=lambda name: cfg)
    with pytest.raises(ValueError, match="empty executable configuration"):
        discover_cli_availability(registry)
    with pytest.raises(CLIAgentError, match="empty executable configuration"):
        await BaseCLIAgent(cfg).run(role=cfg.get_role(None), prompt="request", files=[], images=[])


@pytest.mark.asyncio
async def test_invocation_rechecks_executable_removed_after_discovery(isolated_startup, monkeypatch):
    registry = get_registry()
    lookup = Mock(side_effect=lambda name, **kwargs: "/resolved/cli")
    monkeypatch.setattr("clink.availability.shutil.which", lookup)
    snapshot = server.initialize_runtime()
    assert all(status.executable_found for status in snapshot.cli_statuses)
    lookup.side_effect = None
    lookup.return_value = None
    spawn = AsyncMock(side_effect=AssertionError("Missing executable cannot be launched"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(ToolExecutionError, match="not found in PATH"):
        await server.handle_call_tool("clink", {"cli_name": registry.list_clients()[0], "prompt": "request"})
    spawn.assert_not_awaited()
    assert capabilities.get_runtime_capabilities() is snapshot
