"""CLI-native model selection stays request-local and commits only on success."""

import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

import server
from clink.agents import AgentOutput, CLIAgentError, create_agent
from clink.agents.base import BaseCLIAgent
from clink.constants import CONFIG_DIR
from clink.models import MAX_CLI_MODEL_CHARS, CLIClientConfig
from clink.parsers.base import ParsedCLIResponse
from clink.registry import ClinkRegistry
from tools.clink import CLinkRequest, CLinkTool
from tools.shared.exceptions import ToolExecutionError
from utils import conversation_memory as memory
from utils.storage_backend import InMemoryStorage


@pytest.fixture
def registry(monkeypatch, tmp_path):
    monkeypatch.setattr("clink.registry.USER_CONFIG_DIR", tmp_path / "no-user-clients")
    monkeypatch.delenv("CLI_CLIENTS_CONFIG_PATH", raising=False)
    registry = ClinkRegistry()
    monkeypatch.setattr("tools.clink.get_registry", lambda: registry)
    return registry


@pytest.fixture
def runtime(registry, monkeypatch):
    monkeypatch.setattr(InMemoryStorage, "_cleanup_worker", lambda self: None)
    storage = InMemoryStorage()
    monkeypatch.setattr(memory, "get_storage", lambda: storage)
    monkeypatch.setattr(storage, "compare_and_setex", Mock(wraps=storage.compare_and_setex))
    monkeypatch.setenv("PAL_TRANSCRIPTS", "false")
    output = AgentOutput(
        parsed=ParsedCLIResponse("answer", {"model_used": "observed-native-model", "model_requested": "spoof"}),
        sanitized_command=["fake-cli"],
        returncode=0,
        stdout="answer",
        stderr="",
        duration_seconds=0.1,
        parser_name="fake",
    )
    agent = Mock(run=AsyncMock(return_value=output))
    clients = []

    def create(client):
        clients.append(client)
        return agent

    monkeypatch.setattr("tools.clink.create_agent", create)
    forbidden = Mock(side_effect=AssertionError("CLI model must not resolve a PAL provider"))
    monkeypatch.setattr("providers.registry.ModelProviderRegistry.get_provider_for_model", forbidden)
    monkeypatch.setattr("utils.model_context.ModelContext", forbidden)
    try:
        yield SimpleNamespace(storage=storage, agent=agent, clients=clients, registry=registry, tool=CLinkTool())
    finally:
        storage.shutdown()
    forbidden.assert_not_called()


@pytest.fixture(params=[False, True], ids=["direct", "mcp"])
def invoke(request, runtime):
    async def call(**arguments):
        output = (
            await server.handle_call_tool("clink", arguments)
            if request.param
            else await runtime.tool.execute(arguments)
        )
        return json.loads(output[0].text)

    return call


@pytest.mark.parametrize("model", ["", " ", " opus", "opus ", "-m", "--permission-mode", "a\x00b", "a\nb", 1, True])
def test_invalid_model_is_rejected(model):
    with pytest.raises(ValidationError):
        CLinkRequest(prompt="test", cli_name="claude", model=model)


def test_model_length_bounds_protected_selection_metadata():
    assert CLinkRequest(prompt="test", model="m" * MAX_CLI_MODEL_CHARS).model == "m" * MAX_CLI_MODEL_CHARS
    with pytest.raises(ValidationError):
        CLinkRequest(prompt="test", model="m" * (MAX_CLI_MODEL_CHARS + 1))


def test_requested_model_survives_diagnostic_pruning(monkeypatch):
    monkeypatch.setattr("tools.clink.MAX_METADATA_CHARS", 64)
    selected = "m" * MAX_CLI_MODEL_CHARS
    result = CLinkTool._bound_success_metadata({"model_requested": selected, "extra": "x" * 100})
    assert result["model_requested"] == selected
    assert result["metadata_truncated"] is True


@pytest.mark.parametrize(
    "template",
    [
        ["--model"],
        ["--model", "prefix-{model}"],
        ["--model", "{model}", "{model}"],
        ["--permission-mode", "{model}"],
        ["--model", "{other}"],
        "--model {model}",
        None,
    ],
)
def test_template_rejects_non_model_flags_and_split_values(template):
    with pytest.raises(ValidationError):
        CLIClientConfig(name="claude", command="claude", model_args=template)


def test_default_model_requires_declared_template():
    with pytest.raises(ValidationError, match="default_model requires"):
        CLIClientConfig(name="claude", command="claude", default_model="sonnet")


@pytest.mark.parametrize("vendor", ["claude", "codex"])
def test_default_and_explicit_model_argv_preserve_other_arguments(registry, vendor):
    config = registry.get_client(vendor)
    role = config.get_role("default")
    before = create_agent(config)._build_command(role=role, system_prompt="--model", allow_edits=False)
    assert before.count("--model") == (2 if vendor == "claude" else 0)
    if vendor == "claude":
        assert before[before.index("--model") + 1] == "sonnet"
    requested = config.model_copy(deep=True, update={"requested_model": "model with spaces; $(ignored)"})
    after = create_agent(requested)._build_command(role=role, system_prompt="--model", allow_edits=False)
    position = after.index("--model")
    assert after[position + 1] == "model with spaces; $(ignored)"
    assert after.count("--model") == (2 if vendor == "claude" else 1)
    if vendor == "claude":
        assert after[after.index("--append-system-prompt") + 1] == "--model"
        assert after[after.index("--permission-mode") + 1] == "default"
    assert config.requested_model is None


@pytest.mark.parametrize("bucket", ["config_args", "safe_args", "edit_args", "role_args"])
@pytest.mark.parametrize("flag", ["--model", "-m", "--model=legacy", "-mlegacy"])
def test_explicit_model_rejects_legacy_argument_pins(registry, bucket, flag):
    config = registry.get_client("claude").model_copy(deep=True, update={"requested_model": "opus"})
    role = config.get_role("default")
    arguments = [flag, "legacy"] if flag in {"--model", "-m"} else [flag]
    if bucket == "role_args":
        role.role_args = arguments
    else:
        setattr(config, bucket, arguments)
    with pytest.raises(CLIAgentError, match="default_model"):
        create_agent(config)._build_command(role=role, system_prompt=None, allow_edits=bucket == "edit_args")


@pytest.mark.parametrize("allow_edits", [False, True])
def test_inactive_permission_model_flags_do_not_conflict(registry, allow_edits):
    config = registry.get_client("claude").model_copy(deep=True, update={"requested_model": "opus"})
    setattr(config, "safe_args" if allow_edits else "edit_args", ["--model", "inactive-model"])
    command = create_agent(config)._build_command(
        role=config.get_role(None), system_prompt=None, allow_edits=allow_edits
    )
    assert command.count("--model") == 1
    assert command[command.index("--model") + 1] == "opus"
    assert "inactive-model" not in command


def test_no_override_preserves_legacy_role_model_argv(registry):
    config = registry.get_client("claude").model_copy(deep=True)
    config.get_role(None).role_args = ["--model", "role-model"]
    command = create_agent(config)._build_command(role=config.get_role(None), system_prompt=None)
    assert command[command.index("--model") + 1] == "sonnet"
    assert command[-2:] == ["--model", "role-model"]


async def test_model_value_reaches_a_real_child_as_one_argument(tmp_path, registry):
    script = tmp_path / "echo-arguments.py"
    script.write_text("import json, sys\nprint(json.dumps({'response': json.dumps(sys.argv[1:])}))\n", encoding="utf-8")
    model = "name with spaces; $(touch SHOULD_NOT_EXIST)"
    config = registry.get_client("gemini").model_copy(
        deep=True,
        update={
            "executable": [sys.executable, str(script)],
            "model_args": ["--model", "{model}"],
            "requested_model": model,
            "working_dir": tmp_path,
        },
    )
    result = await BaseCLIAgent(config).run(role=config.get_role(None), prompt="test", files=[], images=[])
    assert json.loads(result.parsed.content) == ["-o", "json", "--model", model]
    assert not (tmp_path / "SHOULD_NOT_EXIST").exists()


@pytest.mark.parametrize(
    "platform,executable,model,rejected",
    [
        ("nt", "C:/tools/cli.cmd", "model & argument", True),
        ("nt", "C:/tools/cli.CMD", "model", True),
        ("nt", "C:/tools/cli.bat", "model", True),
        ("nt", "C:/tools/cli.cmd. ", "model", True),
        ("nt", "C:/tools/cli.exe", "model & argument", False),
        ("nt", "C:/tools/cli.cmd", None, False),
        ("posix", "/tools/cli.cmd", "model", False),
    ],
)
async def test_windows_batch_launchers_reject_caller_models_before_spawn(
    monkeypatch, registry, platform, executable, model, rejected
):
    config = registry.get_client("gemini").model_copy(
        deep=True, update={"model_args": ["--model", "{model}"], "requested_model": model}
    )
    spawn = AsyncMock(
        return_value=SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b'{"response":"ok"}', b"")))
    )
    monkeypatch.setattr("clink.agents.base.os", SimpleNamespace(name=platform))
    monkeypatch.setattr("clink.agents.base.resolve_executable", lambda *args, **kwargs: executable)
    monkeypatch.setattr("clink.agents.base.asyncio.create_subprocess_exec", spawn)
    if rejected:
        with pytest.raises(CLIAgentError, match="Windows batch"):
            await BaseCLIAgent(config).run(role=config.get_role(None), prompt="test", files=[], images=[])
        spawn.assert_not_awaited()
    else:
        assert (
            await BaseCLIAgent(config).run(role=config.get_role(None), prompt="test", files=[], images=[])
        ).parsed.content == "ok"
        spawn.assert_awaited_once()
        if model is not None:
            assert spawn.call_args.args[-1] == model


async def test_model_inherits_only_for_same_cli_and_explicit_switch_wins(runtime, invoke):
    first = await invoke(cli_name="claude", model="opus", prompt="first")
    thread_id = first["continuation_offer"]["continuation_id"]
    assert first["metadata"]["model_requested"] == "opus"
    assert first["metadata"]["model_used"] == "observed-native-model"
    for overrides, expected in [
        ({}, "opus"),
        ({"model": None}, "opus"),
        ({"role": "planner"}, "opus"),
        ({"cli_name": "claude", "model": "sonnet"}, "sonnet"),
        ({"cli_name": "codex"}, None),
        ({"cli_name": "claude"}, None),
        ({"cli_name": "codex", "model": "codex-native"}, "codex-native"),
        ({}, "codex-native"),
    ]:
        result = await invoke(prompt="next", continuation_id=thread_id, **overrides)
        assert result["metadata"].get("model_requested") == expected
        assert runtime.clients[-1].requested_model == expected
        assert memory.get_thread(thread_id).initial_context["clink_defaults"].get("model") == expected
    assert all(runtime.registry.get_client(name).requested_model is None for name in runtime.registry.list_clients())


async def test_omission_keeps_defaults_and_does_not_inherit_reported_model(runtime, invoke):
    first = await invoke(cli_name="claude", prompt="first")
    thread_id = first["continuation_offer"]["continuation_id"]
    second = await invoke(prompt="next", continuation_id=thread_id)
    assert "model_requested" not in first["metadata"] and "model_requested" not in second["metadata"]
    assert runtime.clients[-1].requested_model is None
    assert runtime.clients[-1].default_model == "sonnet"
    assert memory.get_thread(thread_id).initial_context["clink_defaults"] == {"cli_name": "claude", "role": "default"}


async def test_unsupported_manifest_rejects_model_without_launch_or_history(runtime, invoke):
    with pytest.raises(ToolExecutionError, match="no model_args template"):
        await invoke(cli_name="gemini", model="native", prompt="test")
    runtime.agent.run.assert_not_awaited()
    assert not runtime.storage._store


async def test_inherited_model_requires_current_manifest_support(runtime, invoke):
    first = await invoke(cli_name="claude", model="opus", prompt="first")
    snapshot = runtime.storage._store.copy()
    runtime.registry.get_client("claude").model_args = []
    with pytest.raises(ToolExecutionError, match="no model_args template"):
        await invoke(prompt="next", continuation_id=first["continuation_offer"]["continuation_id"])
    assert runtime.agent.run.await_count == 1
    assert runtime.storage._store == snapshot


async def test_invalid_saved_model_fails_before_launch(runtime, invoke):
    first = await invoke(cli_name="claude", model="opus", prompt="first")
    thread_id = first["continuation_offer"]["continuation_id"]
    raw, deadline = runtime.storage._store[f"thread:{thread_id}"]
    stored = json.loads(raw)
    stored["initial_context"]["clink_defaults"]["model"] = "--permission-mode"
    runtime.storage._store[f"thread:{thread_id}"] = (json.dumps(stored), deadline)
    snapshot = runtime.storage._store.copy()
    with pytest.raises(ToolExecutionError, match="model must be"):
        await invoke(prompt="next", continuation_id=thread_id)
    assert runtime.agent.run.await_count == 1
    assert runtime.storage._store == snapshot


@pytest.mark.parametrize("failure", ["cli", "commit"])
async def test_failed_model_change_does_not_replace_successful_selection(runtime, invoke, failure):
    first = await invoke(cli_name="claude", model="opus", prompt="first")
    thread_id = first["continuation_offer"]["continuation_id"]
    snapshot = runtime.storage._store.copy()
    if failure == "cli":
        runtime.agent.run.side_effect = CLIAgentError("failed CLI")
        with pytest.raises(ToolExecutionError):
            await invoke(cli_name="codex", model="new-model", prompt="next", continuation_id=thread_id)
    else:
        runtime.storage.compare_and_setex.return_value = False
        result = await invoke(cli_name="codex", model="new-model", prompt="next", continuation_id=thread_id)
        assert result["metadata"]["history_saved"] is False
    assert runtime.storage._store == snapshot
    assert memory.get_thread(thread_id).initial_context["clink_defaults"] == {
        "cli_name": "claude",
        "role": "default",
        "model": "opus",
    }


async def test_concurrent_models_do_not_mutate_shared_manifest(runtime):
    outputs = await asyncio.gather(
        *(
            runtime.tool.execute({"cli_name": "claude", "model": model, "prompt": "test"})
            for model in ("opus", "sonnet")
        )
    )
    assert [json.loads(output[0].text)["metadata"]["model_requested"] for output in outputs] == ["opus", "sonnet"]
    assert sorted(client.requested_model for client in runtime.clients) == ["opus", "sonnet"]
    assert runtime.registry.get_client("claude").requested_model is None


def test_complete_legacy_manifest_retains_no_override_behavior(monkeypatch, tmp_path):
    legacy = json.loads((CONFIG_DIR / "claude.json").read_text())
    legacy.pop("model_args")
    legacy.pop("default_model")
    legacy["additional_args"] = ["--model", "legacy-default"]
    path = tmp_path / "claude.json"
    path.write_text(json.dumps(legacy))
    monkeypatch.setenv("CLI_CLIENTS_CONFIG_PATH", str(path))
    monkeypatch.setattr("clink.registry.USER_CONFIG_DIR", tmp_path / "no-user-clients")
    config = ClinkRegistry().get_client("claude")
    command = create_agent(config)._build_command(role=config.get_role(None), system_prompt=None)
    assert command[command.index("--model") + 1] == "legacy-default"
    assert config.model_args == []
