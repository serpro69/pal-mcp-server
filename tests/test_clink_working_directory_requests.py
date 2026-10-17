"""Directory selection at the request, factory, and atomic storage boundaries."""

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.types import CallToolRequest, CallToolRequestParams, Tool
from pydantic import ValidationError

import server
from clink.agents import AgentOutput, BaseCLIAgent, CLIAgentError, create_agent, get_agent_class
from clink.parsers.base import ParsedCLIResponse
from clink.registry import ClinkRegistry
from clink.working_directory import resolve_working_directory
from tests.request_capture import capture_providers
from tools.chat import ChatTool
from tools.clink import CLinkRequest, CLinkTool
from tools.shared.exceptions import ToolExecutionError
from utils import conversation_coordination as coordination
from utils import conversation_memory as memory
from utils.storage_backend import InMemoryStorage


def output():
    return AgentOutput(
        parsed=ParsedCLIResponse("CLI_REPLY", {}),
        sanitized_command=["fake-cli"],
        returncode=0,
        stdout="",
        stderr="",
        duration_seconds=0.1,
        parser_name="fake",
    )


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    assert not coordination._entries
    monkeypatch.setattr("clink.registry.USER_CONFIG_DIR", tmp_path / "no-user-config")
    monkeypatch.setattr("clink.registry.get_env", lambda name: None)
    registry = ClinkRegistry()
    monkeypatch.setattr("tools.clink.get_registry", lambda: registry)
    monkeypatch.setattr(InMemoryStorage, "_cleanup_worker", lambda self: None)
    storage = InMemoryStorage()
    monkeypatch.setattr(memory, "get_storage", lambda: storage)
    monkeypatch.setattr(storage, "compare_and_setex", Mock(wraps=storage.compare_and_setex))
    monkeypatch.setenv("PAL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("PAL_TRANSCRIPTS", "false")
    run = AsyncMock(return_value=output())

    async def fake_run(agent, **kwargs):
        return await run(agent, **kwargs)

    monkeypatch.setattr(BaseCLIAgent, "run", fake_run)
    factory = Mock(wraps=create_agent)
    monkeypatch.setattr("tools.clink.create_agent", factory)
    yield SimpleNamespace(registry=registry, storage=storage, run=run, factory=factory)
    storage.shutdown()
    assert not coordination._entries


@pytest.fixture
def projects(tmp_path):
    a, b, configured = [tmp_path / name for name in ("project A", "project B", "configured")]
    for directory in (a, b, configured):
        directory.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(a, target_is_directory=True)
    return a, b, configured, alias


@pytest.fixture(params=["direct", "dispatch"])
def invoke(runtime, request):
    tool = CLinkTool()

    async def call(arguments):
        response = (
            await tool.execute(arguments)
            if request.param == "direct"
            else await server.handle_call_tool("clink", arguments)
        )
        return json.loads(response[0].text)

    return call


def record(path):
    if path is None:
        return {"mode": "scratch"}
    return {"mode": "directory", "launch_path": str(path), "canonical_path": str(path.resolve())}


def saved(result):
    return memory.get_thread(result["continuation_offer"]["continuation_id"])


def seed(context, *, tool="chat", parent=None):
    thread_id = memory.create_thread(tool, context, parent)
    memory.add_turn(thread_id, "user", "OLD_INPUT")
    memory.add_turn(thread_id, "assistant", "OLD_REPLY")
    return thread_id


def test_schema_adds_only_optional_directory_and_preserves_other_requirements(runtime):
    schema = CLinkTool().get_input_schema()
    assert schema["required"] == ["prompt"]
    assert schema["anyOf"] == [{"required": ["cli_name"]}, {"required": ["continuation_id"]}]
    field = schema["properties"]["working_directory_absolute_path"]
    assert field["type"] == ["string", "null"]
    assert field["default"] is None
    for phrase in ("PAL server", "'~'", "manifest working_dir", "scratch", "inherit", "new conversation"):
        assert phrase in field["description"]
    assert "working_dir" not in schema["properties"]
    assert "working_dir" not in CLinkRequest.model_fields
    assert "working_directory_absolute_path" in ChatTool().get_input_schema()["required"]
    assert CLinkRequest(prompt="test").working_directory_absolute_path is None


@pytest.mark.parametrize("value", [None, "", "relative", "~/project", "/project"])
def test_model_accepts_strings_or_null_before_path_validation(value):
    assert CLinkRequest(prompt="test", working_directory_absolute_path=value).working_directory_absolute_path == value


@pytest.mark.parametrize("value", [42, 1.5, False, [], {}, b"/tmp", Path("/tmp")])
async def test_wrong_types_fail_model_before_loading_conversation(runtime, monkeypatch, value):
    load = Mock(side_effect=AssertionError("Wrong types must not load a conversation"))
    monkeypatch.setattr("tools.clink.CLIConversation.load", load)
    with pytest.raises(ValidationError):
        CLinkRequest(prompt="test", working_directory_absolute_path=value)
    with pytest.raises(ToolExecutionError) as error:
        await CLinkTool().execute({"cli_name": "gemini", "prompt": "test", "working_directory_absolute_path": value})
    assert json.loads(error.value.payload)["metadata"] == {"tool_name": "clink", "error_type": "ValidationError"}
    load.assert_not_called()
    runtime.factory.assert_not_called()
    assert runtime.storage._store == {}


@pytest.mark.parametrize("value", [42, 1.5, False, [], {}])
async def test_sdk_rejects_wrong_json_types_before_pal_handler(runtime, monkeypatch, value):
    tool = CLinkTool()
    monkeypatch.setattr(
        server.server,
        "_get_cached_tool_definition",
        AsyncMock(return_value=Tool(name="clink", inputSchema=tool.get_input_schema())),
    )
    load = Mock(side_effect=AssertionError("SDK rejection must not load a conversation"))
    monkeypatch.setattr("tools.clink.CLIConversation.load", load)
    response = await server.server.request_handlers[CallToolRequest](
        CallToolRequest(
            params=CallToolRequestParams(
                name="clink",
                arguments={"cli_name": "gemini", "prompt": "test", "working_directory_absolute_path": value},
            )
        )
    )
    assert response.root.isError is True
    assert response.root.structuredContent is None
    assert response.root.content[0].text.startswith("Input validation error:")
    load.assert_not_called()
    runtime.factory.assert_not_called()
    assert runtime.storage._store == {}


@pytest.mark.parametrize("value_kind", ["null", "directory", "relative"])
async def test_sdk_accepted_types_reach_directory_resolution(runtime, monkeypatch, projects, value_kind):
    a, _, _, _ = projects
    value = {"null": None, "directory": str(a), "relative": "relative"}[value_kind]
    tool = CLinkTool()
    monkeypatch.setattr(
        server.server,
        "_get_cached_tool_definition",
        AsyncMock(return_value=Tool(name="clink", inputSchema=tool.get_input_schema())),
    )
    resolve = Mock(wraps=resolve_working_directory)
    monkeypatch.setattr("tools.clink.resolve_working_directory", resolve)
    response = await server.server.request_handlers[CallToolRequest](
        CallToolRequest(
            params=CallToolRequestParams(
                name="clink",
                arguments={"cli_name": "gemini", "prompt": "test", "working_directory_absolute_path": value},
            )
        )
    )
    resolve.assert_called_once()
    assert resolve.call_args.kwargs["requested"] == value
    result = json.loads(response.root.content[0].text)
    if value_kind == "relative":
        assert response.root.isError is True
        assert result["metadata"]["error_type"] == "InvalidWorkingDirectoryError"
        runtime.factory.assert_not_called()
        assert runtime.storage._store == {}
    else:
        assert response.root.isError is False
        assert saved(result).initial_context["clink_working_directory"] == record(
            a if value_kind == "directory" else None
        )


@pytest.mark.parametrize("cli", ["agy", "claude", "codex", "gemini"])
@pytest.mark.parametrize("source", ["request", "configuration", "scratch"])
async def test_selected_directory_reaches_each_real_adapter_and_one_atomic_write(
    runtime, invoke, projects, cli, source
):
    a, b, _, alias = projects
    client = runtime.registry.get_client(cli)
    client.working_dir = b if source == "request" else alias if source == "configuration" else None
    before = client.model_dump()
    parent_cwd = Path.cwd()
    arguments = {"cli_name": cli, "prompt": "NEW_INPUT"}
    if source == "request":
        arguments["working_directory_absolute_path"] = f"{alias}/./"
    expected = alias if source != "scratch" else None

    async def inspect(agent, **kwargs):
        assert isinstance(agent, get_agent_class(client))
        assert agent.client is not client
        assert agent.client.working_dir == expected
        assert kwargs["role"] is agent.client.roles["default"]
        assert agent.client.roles["default"] is not client.roles["default"]
        assert agent.client.model_dump(exclude={"working_dir"}) == client.model_dump(exclude={"working_dir"})
        assert runtime.storage._store == {}
        # Nested mutations prove the registry is isolated, not merely the cwd field.
        agent.client.env["LOCAL_ONLY"] = "value"
        agent.client.safe_args.append("local-only")
        agent.client.roles["default"].role_args.append("local-role")
        return output()

    runtime.run.side_effect = inspect
    result = await invoke(arguments)
    assert result["metadata"]["history_saved"] is True
    assert runtime.storage.compare_and_setex.call_count == 1
    assert saved(result).initial_context["clink_working_directory"] == record(expected)
    assert [turn.content for turn in saved(result).turns] == ["NEW_INPUT", "CLI_REPLY"]
    assert client.model_dump() == before
    assert Path.cwd() == parent_cwd


async def test_request_overrides_missing_manifest_and_expands_home(runtime, invoke, monkeypatch, projects):
    a, b, _, _ = projects
    runtime.registry.get_client("gemini").working_dir = b / "missing"
    monkeypatch.setattr("os.path.expanduser", Mock(return_value=str(a)))
    result = await invoke({"cli_name": "gemini", "prompt": "test", "working_directory_absolute_path": "~/project"})
    assert runtime.run.call_args.args[0].client.working_dir == a
    assert saved(result).initial_context["working_directory_absolute_path"] == "~/project"
    assert saved(result).initial_context["clink_working_directory"] == record(a)


@pytest.mark.parametrize("repeat", ["omitted", "null", "canonical", "alias"])
@pytest.mark.parametrize("cli", ["agy", "claude", "codex", "gemini"])
async def test_project_survives_omission_aliases_cli_and_role_changes(runtime, invoke, projects, cli, repeat):
    a, b, _, alias = projects
    first = await invoke(
        {"cli_name": "gemini", "prompt": "FIRST", "working_directory_absolute_path": str(alias), "allow_edits": True}
    )
    original = saved(first)
    runtime.registry.get_client(cli).working_dir = b
    arguments = {"continuation_id": original.thread_id, "cli_name": cli, "role": "planner", "prompt": "NEXT"}
    if repeat != "omitted":
        arguments["working_directory_absolute_path"] = {"null": None, "canonical": str(a), "alias": str(alias)}[repeat]
    second = await invoke(arguments)
    context = saved(second).initial_context
    assert context == {**original.initial_context, "clink_defaults": {"cli_name": cli, "role": "planner"}}
    assert context["clink_working_directory"] == record(alias)
    assert runtime.run.call_args.args[0].client.working_dir == alias
    assert runtime.run.call_args.kwargs["allow_edits"] is False
    assert runtime.run.call_args.kwargs["editable_paths"] == []
    assert [turn.content for turn in saved(second).turns] == ["FIRST", "CLI_REPLY", "NEXT", "CLI_REPLY"]
    assert "FIRST" in runtime.run.call_args.kwargs["prompt"]


async def test_scratch_ignores_later_manifest_then_promotes_with_history(runtime, invoke, projects):
    a, b, _, alias = projects
    first = await invoke({"cli_name": "gemini", "prompt": "SCRATCH_FIRST"})
    thread_id = saved(first).thread_id
    runtime.registry.get_client("claude").working_dir = b
    second = await invoke({"cli_name": "claude", "continuation_id": thread_id, "prompt": "SCRATCH_SECOND"})
    assert runtime.run.call_args.args[0].client.working_dir is None
    assert saved(second).initial_context["clink_working_directory"] == {"mode": "scratch"}
    before = runtime.storage._store.copy()

    async def before_commit(agent, **kwargs):
        assert runtime.storage._store == before
        assert agent.client.working_dir == alias
        assert "SCRATCH_FIRST" in kwargs["prompt"] and "SCRATCH_SECOND" in kwargs["prompt"]
        return output()

    runtime.run.side_effect = before_commit
    third = await invoke(
        {"continuation_id": thread_id, "prompt": "PROJECT_FIRST", "working_directory_absolute_path": str(alias)}
    )
    assert saved(third).thread_id == thread_id
    assert saved(third).initial_context["clink_working_directory"] == record(alias)
    assert [turn.content for turn in saved(third).turns] == [
        "SCRATCH_FIRST",
        "CLI_REPLY",
        "SCRATCH_SECOND",
        "CLI_REPLY",
        "PROJECT_FIRST",
        "CLI_REPLY",
    ]
    assert runtime.storage.compare_and_setex.call_count == 3


@pytest.mark.parametrize("failure", ["runner", "parsed", "exit", "cancel", "cas", "write"])
@pytest.mark.parametrize("state", ["new", "legacy", "scratch", "directory"])
async def test_failed_execution_or_commit_preserves_binding_history_and_ttl(
    runtime, invoke, monkeypatch, projects, failure, state
):
    a, _, _, _ = projects
    arguments = {"cli_name": "gemini", "prompt": "FAILED_INPUT", "working_directory_absolute_path": str(a)}
    if state != "new":
        context = {"provenance": "original"}
        if state != "legacy":
            context["clink_working_directory"] = record(a if state == "directory" else None)
        arguments["continuation_id"] = seed(context)
    before = runtime.storage._store.copy()
    audit = Mock()
    monkeypatch.setattr("clink.conversation.record_event", audit)
    if failure in {"runner", "cancel"}:
        runtime.run.side_effect = CLIAgentError("failed") if failure == "runner" else asyncio.CancelledError()
    elif failure == "parsed":
        runtime.run.return_value.parsed.is_error = True
    elif failure == "exit":
        runtime.run.return_value.returncode = 1
    elif failure == "cas":
        runtime.storage.compare_and_setex.side_effect = lambda *args: False
    else:
        runtime.storage.compare_and_setex.side_effect = OSError("failed write")

    if failure in {"cas", "write"}:
        result = await invoke(arguments)
        assert result["content"] == "CLI_REPLY"
        assert result["metadata"]["history_saved"] is False
        assert result["metadata"]["memory_persistence_warning"]
        assert result["continuation_offer"] is None
        assert runtime.storage.compare_and_setex.call_count == 1
    else:
        with pytest.raises(asyncio.CancelledError if failure == "cancel" else ToolExecutionError):
            await invoke(arguments)
        runtime.storage.compare_and_setex.assert_not_called()
    assert runtime.storage._store == before
    audit.assert_not_called()


@pytest.mark.parametrize("change", ["expiry", "other-writer"])
async def test_stale_commit_cannot_promote_scratch_or_resurrect_history(runtime, invoke, monkeypatch, projects, change):
    a, _, _, _ = projects
    clock = {"now": 1000.0}
    monkeypatch.setattr("utils.storage_backend.time.time", lambda: clock["now"])
    thread_id = seed({"clink_working_directory": {"mode": "scratch"}})
    expected = {}
    audit = Mock()
    monkeypatch.setattr("clink.conversation.record_event", audit)

    async def change_snapshot(agent, **kwargs):
        assert agent.client.working_dir == a
        if change == "expiry":
            clock["now"] += memory.CONVERSATION_TIMEOUT_SECONDS
        else:
            memory.add_turn(thread_id, "user", "OTHER_WRITER", tool_name="chat")
            expected.update(runtime.storage._store)
        return output()

    runtime.run.side_effect = change_snapshot
    result = await invoke(
        {
            "cli_name": "gemini",
            "prompt": "PROJECT_ATTEMPT",
            "continuation_id": thread_id,
            "working_directory_absolute_path": str(a),
        }
    )
    assert result["content"] == "CLI_REPLY"
    assert result["metadata"]["history_saved"] is False
    assert result["metadata"]["memory_persistence_warning"]["code"] == "ConversationConflictError"
    assert result["continuation_offer"] is None
    assert runtime.storage._store == expected
    audit.assert_not_called()
    if change == "other-writer":
        assert memory.get_thread(thread_id).initial_context["clink_working_directory"] == {"mode": "scratch"}


@pytest.mark.parametrize("state", ["new", "scratch", "directory"])
@pytest.mark.parametrize("value", ["", "relative", "/missing-clink-directory\x00"])
async def test_invalid_directory_precedes_history_and_agent_without_state_changes(
    runtime, invoke, monkeypatch, projects, state, value
):
    a, _, _, _ = projects
    arguments = {"cli_name": "gemini", "prompt": "test", "working_directory_absolute_path": value}
    if state != "new":
        arguments["continuation_id"] = seed({"clink_working_directory": record(a if state == "directory" else None)})
    before = runtime.storage._store.copy()
    prepare = Mock(side_effect=AssertionError("Invalid directories must not prepare history"))
    monkeypatch.setattr("tools.clink.CLIConversation.prepare_history", prepare)
    with pytest.raises(ToolExecutionError) as error:
        await invoke(arguments)
    assert json.loads(error.value.payload)["metadata"]["error_type"] == "InvalidWorkingDirectoryError"
    prepare.assert_not_called()
    runtime.factory.assert_not_called()
    runtime.storage.compare_and_setex.assert_not_called()
    assert runtime.storage._store == before


@pytest.mark.parametrize("fault", ["conflict", "missing", "retargeted", "null-binding", "bad-binding"])
async def test_bound_rejections_never_spawn_write_or_refresh(runtime, invoke, monkeypatch, projects, fault):
    a, b, _, alias = projects
    context = {"clink_working_directory": record(alias)}
    requested = None
    if fault == "conflict":
        requested = str(b)
    elif fault == "missing":
        alias.unlink()
    elif fault == "retargeted":
        alias.unlink()
        alias.symlink_to(b, target_is_directory=True)
    elif fault == "null-binding":
        context["clink_working_directory"] = None
    else:
        context["clink_working_directory"] = {"mode": "directory", "launch_path": str(a)}
    thread_id = seed(context)
    before = runtime.storage._store.copy()
    history = Mock(side_effect=AssertionError("Rejected directories must not prepare history"))
    monkeypatch.setattr("tools.clink.CLIConversation.prepare_history", history)
    with pytest.raises(ToolExecutionError) as error:
        await invoke(
            {
                "cli_name": "gemini",
                "prompt": "test",
                "continuation_id": thread_id,
                "working_directory_absolute_path": requested,
            }
        )
    payload = json.loads(error.value.payload)
    assert payload["continuation_offer"] is None
    assert payload["metadata"]["error_type"] == (
        "WorkingDirectoryConflictError"
        if fault == "conflict"
        else (
            "BoundWorkingDirectoryUnavailableError"
            if fault in {"missing", "retargeted"}
            else "InvalidWorkingDirectoryBindingError"
        )
    )
    if fault == "conflict":
        assert "start a new conversation" in payload["content"]
    history.assert_not_called()
    runtime.factory.assert_not_called()
    runtime.storage.compare_and_setex.assert_not_called()
    assert runtime.storage._store == before


@pytest.mark.parametrize("origin", ["chat", "clink"])
async def test_missing_binding_migrates_without_inheriting_raw_fields_or_parent(runtime, invoke, projects, origin):
    a, b, configured, _ = projects
    parent = seed({"clink_working_directory": record(a)}, tool="clink")
    original = {"working_directory_absolute_path": str(b), "working_dir": str(a), "provenance": "keep"}
    thread_id = seed(original, tool=origin, parent=parent)
    parent_before = runtime.storage._store[f"thread:{parent}"]
    runtime.registry.get_client("gemini").working_dir = configured
    result = await invoke({"cli_name": "gemini", "prompt": "CURRENT", "continuation_id": thread_id})
    thread = saved(result)
    assert thread.initial_context == {
        **original,
        "clink_defaults": {"cli_name": "gemini", "role": "default"},
        "clink_working_directory": record(configured),
    }
    assert thread.parent_thread_id == parent and thread.tool_name == origin
    assert runtime.run.call_args.args[0].client.working_dir == configured
    assert runtime.run.call_args.kwargs["prompt"].count("OLD_INPUT") == 2
    assert runtime.storage._store[f"thread:{parent}"] == parent_before


async def test_api_and_local_interleaving_preserve_the_clink_project(runtime, invoke, monkeypatch, projects):
    a, b, _, alias = projects
    provider_calls = capture_providers(monkeypatch)
    first = await invoke({"cli_name": "gemini", "prompt": "FIRST", "working_directory_absolute_path": str(alias)})
    thread_id = saved(first).thread_id
    original_context = saved(first).initial_context
    await server.handle_call_tool(
        "chat",
        {
            "prompt": "API_INPUT",
            "model": "gemini-2.5-flash",
            "continuation_id": thread_id,
            "working_directory_absolute_path": str(b),
        },
    )
    await server.handle_call_tool(
        "planner",
        {
            "step": "LOCAL_INPUT",
            "step_number": 1,
            "total_steps": 1,
            "next_step_required": False,
            "continuation_id": thread_id,
        },
    )
    assert memory.get_thread(thread_id).initial_context == original_context
    runtime.registry.get_client("claude").working_dir = b
    last = await invoke({"prompt": "CLI_AGAIN", "cli_name": "claude", "continuation_id": thread_id})
    assert saved(last).initial_context["clink_working_directory"] == record(alias)
    assert runtime.run.call_args.args[0].client.working_dir == alias
    assert "API_INPUT" in runtime.run.call_args.kwargs["prompt"]
    assert "LOCAL_INPUT" in runtime.run.call_args.kwargs["prompt"]
    assert len(provider_calls) == 1


@pytest.mark.parametrize("cli", ["agy", "claude", "codex", "gemini"])
@pytest.mark.parametrize("continued", [False, True])
async def test_overlapping_requests_keep_independent_client_copies_and_bindings(
    runtime, invoke, projects, cli, continued
):
    a, b, configured, _ = projects
    client = runtime.registry.get_client(cli)
    client.working_dir = configured
    before = client.model_dump()
    cwd = Path.cwd()
    choices = [a, b, None]
    ids = [seed({"clink_working_directory": record(path)}) for path in choices] if continued else [None] * 3
    ready, release = asyncio.Event(), asyncio.Event()
    observed = []

    async def blocked(agent, **kwargs):
        observed.append(agent.client)
        if len(observed) == 3:
            ready.set()
        await release.wait()
        return output()

    runtime.run.side_effect = blocked
    tasks = [
        asyncio.create_task(
            invoke(
                {
                    "cli_name": cli,
                    "prompt": f"INPUT_{i}",
                    "continuation_id": thread_id,
                    "working_directory_absolute_path": str(path) if path else None,
                }
            )
        )
        for i, (thread_id, path) in enumerate(zip(ids, choices))
    ]
    try:
        await asyncio.wait_for(ready.wait(), 3)
        assert len({id(local) for local in observed}) == 3
        assert all(local is not client for local in observed)
        observed[0].env["REQUEST_LOCAL"] = "one"
        assert all("REQUEST_LOCAL" not in local.env for local in observed[1:])
        assert client.model_dump() == before and Path.cwd() == cwd
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), 3)
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    for result, path in zip(results, [a, b, None if continued else configured]):
        assert saved(result).initial_context["clink_working_directory"] == record(path)
    assert client.model_dump() == before and Path.cwd() == cwd


async def test_directory_resolution_yields_and_cancellation_cannot_spawn_or_commit(
    runtime, invoke, monkeypatch, projects
):
    a, b, _, _ = projects
    thread_id = seed({"clink_working_directory": {"mode": "scratch"}})
    before = runtime.storage._store[f"thread:{thread_id}"]
    entered = asyncio.Event()
    release, finished = threading.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()

    def blocked(**kwargs):
        assert threading.get_ident() != loop_thread
        if kwargs["requested"] == str(a):
            loop.call_soon_threadsafe(entered.set)
            try:
                if not release.wait(5):
                    raise TimeoutError("test resolution gate timed out")
                return resolve_working_directory(**kwargs)
            finally:
                finished.set()
        return resolve_working_directory(**kwargs)

    monkeypatch.setattr("tools.clink.resolve_working_directory", blocked)
    arguments = {
        "cli_name": "gemini",
        "prompt": "WAITING",
        "continuation_id": thread_id,
        "working_directory_absolute_path": str(a),
    }
    task = asyncio.create_task(invoke(arguments))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert coordination._entries[thread_id].lock.locked()
        with pytest.raises(ToolExecutionError, match="busy"):
            await invoke(arguments)
        independent = await asyncio.wait_for(
            invoke({"cli_name": "gemini", "prompt": "OTHER", "working_directory_absolute_path": str(b)}), 2
        )
        assert saved(independent).initial_context["clink_working_directory"] == record(b)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.to_thread(finished.wait, 2)
    assert runtime.factory.call_count == 1  # Only the independent request launched.
    assert runtime.storage._store[f"thread:{thread_id}"] == before
