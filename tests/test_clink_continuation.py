"""Atomic CLI exchange recording through both direct and MCP dispatch paths."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

import server
from clink.agents import AgentOutput, CLIAgentError
from clink.parsers.base import ParsedCLIResponse
from tools.clink import MAX_RESPONSE_CHARS, CLinkTool
from tools.shared.exceptions import ToolExecutionError
from utils import conversation_memory as memory
from utils import transcripts
from utils.storage_backend import InMemoryStorage


@pytest.fixture
def storage(monkeypatch, tmp_path):
    monkeypatch.setattr(InMemoryStorage, "_cleanup_worker", lambda self: None)
    backend = InMemoryStorage()
    monkeypatch.setattr(memory, "get_storage", lambda: backend)
    monkeypatch.setattr(backend, "compare_and_setex", Mock(wraps=backend.compare_and_setex))
    monkeypatch.setenv("PAL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("PAL_TRANSCRIPTS", "false")
    yield backend
    backend.shutdown()


@pytest.fixture
def agent(monkeypatch):
    output = AgentOutput(
        parsed=ParsedCLIResponse("RAW_REPLY", {"model_used": "CLI_MODEL", "usage": {"input_tokens": 12}}),
        sanitized_command=["fake-cli"],
        returncode=0,
        stdout="raw",
        stderr="",
        duration_seconds=0.1,
        parser_name="fake",
    )
    agent = Mock()
    agent.run = AsyncMock(return_value=output)
    monkeypatch.setattr("tools.clink.create_agent", lambda _: agent)
    return agent


@pytest.fixture(params=[False, True], ids=["direct", "mcp"])
def invoke(request):
    tool = CLinkTool()

    async def call(arguments):
        result = await server.handle_call_tool("clink", arguments) if request.param else await tool.execute(arguments)
        return json.loads(result[0].text)

    return call


def assert_no_offer(output):
    assert output["continuation_offer"] is None
    # ToolOutput permits null metadata for ordinary request-validation errors.
    assert "continuation_id" not in (output["metadata"] or {})


@pytest.mark.asyncio
@pytest.mark.parametrize("cli_name", ["gemini", "claude", "codex"])
async def test_sequential_exchange_is_one_write_and_uses_raw_history(storage, agent, invoke, monkeypatch, cli_name):
    blocked = Mock(side_effect=AssertionError("No PAL provider or model context is allowed"))
    monkeypatch.setattr("providers.registry.ModelProviderRegistry.get_provider_for_model", blocked)
    monkeypatch.setattr("providers.registry.ModelProviderRegistry.get_preferred_fallback_model", blocked)
    monkeypatch.setattr("utils.model_context.ModelContext", blocked)
    clock = {"now": 1000.0}
    monkeypatch.setattr("utils.storage_backend.time.time", lambda: clock["now"])

    async def reply(**kwargs):
        # No current input is visible while the external CLI is running.
        assert len(storage._store) == (agent.run.await_count - 1)
        for raw, _ in storage._store.values():
            assert [turn["content"] for turn in json.loads(raw)["turns"]] == ["FIRST_INPUT", "RAW_REPLY"]
        return agent.run.return_value

    agent.run.side_effect = reply
    first = await invoke({"cli_name": cli_name, "prompt": "FIRST_INPUT"})
    thread_id = first["continuation_offer"]["continuation_id"]
    assert storage.compare_and_setex.call_count == 1
    old_deadline = storage._store[f"thread:{thread_id}"][1]
    clock["now"] += 10
    second = await invoke({"cli_name": cli_name, "prompt": "SECOND_INPUT", "continuation_id": thread_id})
    assert storage.compare_and_setex.call_count == 2
    assert storage._store[f"thread:{thread_id}"][1] == old_deadline + 10
    assert second["metadata"]["history_saved"] is True
    assert second["continuation_offer"]["remaining_turns"] == 46
    assert "23 more exchanges" in second["continuation_offer"]["note"]
    prompt = agent.run.call_args.kwargs["prompt"]
    for marker in ("FIRST_INPUT", "RAW_REPLY", "SECOND_INPUT"):
        assert prompt.count(marker) == 1
    thread = memory.get_thread(thread_id)
    assert [turn.content for turn in thread.turns] == ["FIRST_INPUT", "RAW_REPLY", "SECOND_INPUT", "RAW_REPLY"]
    assert all(turn.tool_name == "clink" for turn in thread.turns)
    assert thread.turns[-1].model_provider == cli_name
    assert thread.turns[-1].model_name == "CLI_MODEL"
    assert thread.turns[-1].model_metadata["usage"] == {"input_tokens": 12}
    blocked.assert_not_called()


@pytest.mark.asyncio
async def test_prompt_file_is_expanded_once_and_internal_fields_are_ignored(
    storage, agent, invoke, monkeypatch, tmp_path
):
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("PROMPT_FILE_INPUT")
    from tools.shared import base_tool

    reader = Mock(wraps=base_tool.read_file_content)
    monkeypatch.setattr(base_tool, "read_file_content", reader)
    output = await invoke(
        {
            "cli_name": "gemini",
            "prompt": "placeholder",
            "absolute_file_paths": [str(prompt_file)],
            "_model_context": object(),
            "_remaining_tokens": 0,
            "_resolved_model_name": "untrusted",
        }
    )
    assert reader.call_count == 1
    assert agent.run.call_args.kwargs["prompt"].count("PROMPT_FILE_INPUT") == 1
    assert agent.run.call_args.kwargs["files"] == []
    thread = memory.get_thread(output["continuation_offer"]["continuation_id"])
    assert "PROMPT_FILE_INPUT" in thread.turns[0].content
    assert thread.turns[0].files == []
    assert not any(key.startswith("_") for key in thread.initial_context)


@pytest.mark.asyncio
async def test_parent_history_and_attachments_do_not_restore_permissions(storage, agent, invoke, monkeypatch, tmp_path):
    historical_file = tmp_path / "old.py"
    historical_file.write_text("HISTORICAL_CONTENT_MUST_NOT_BE_READ")
    parent = memory.create_thread("chat", {"provenance": "origin"})
    memory.add_turn(parent, "user", "PARENT_INPUT", files=[str(historical_file)], images=["/old.png"], tool_name="chat")
    memory.add_turn(parent, "assistant", "PARENT_REPLY", tool_name="chat")
    child = memory.create_thread("chat", {"allow_edits": True, "editable_paths": [str(tmp_path)]}, parent)
    memory.add_turn(child, "user", "CHILD_INPUT")
    memory.add_turn(child, "assistant", "CHILD_REPLY")
    original_context = memory.get_thread(child).initial_context
    current_file = tmp_path / "new.py"
    current_file.write_text("CURRENT_ATTACHMENT")
    reader = Mock(side_effect=AssertionError("Historical files must not be read"))
    monkeypatch.setattr("utils.file_utils.read_files", reader)
    monkeypatch.setattr("tools.shared.base_tool.read_file_content", reader)

    result = await invoke(
        {
            "cli_name": "claude",
            "prompt": "CURRENT_INPUT",
            "continuation_id": child.upper(),
            "absolute_file_paths": [str(current_file), str(current_file)],
            "images": ["/new.png"],
        }
    )
    assert result["continuation_offer"]["continuation_id"] == child
    call = agent.run.call_args.kwargs
    for marker in ("PARENT_INPUT", "PARENT_REPLY", "CHILD_INPUT", "CHILD_REPLY", "CURRENT_INPUT"):
        assert call["prompt"].count(marker) == 1
    assert "HISTORICAL_CONTENT_MUST_NOT_BE_READ" not in call["prompt"]
    assert call["files"] == [str(current_file), str(historical_file)]
    assert call["images"] == ["/new.png", "/old.png"]
    assert call["allow_edits"] is False and call["editable_paths"] == []
    updated = memory.get_thread(child)
    assert updated.initial_context == {**original_context, "clink_defaults": {"cli_name": "claude", "role": "default"}}
    assert updated.parent_thread_id == parent and updated.tool_name == "chat"
    assert len(updated.turns) == 4
    assert updated.turns[-2].files == [str(current_file)]
    assert updated.turns[-2].images == ["/new.png"]
    assert updated.turns[-1].files == call["files"]
    reader.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {"continuation_id": ""},
        {"continuation_id": "not-uuid"},
        {"continuation_id": str(uuid4())},
        {"cli_name": "missing"},
        {"role": "missing"},
        {"absolute_file_paths": ["relative.py"]},
        {"editable_paths": ["/tmp/edit"]},
        {"allow_edits": True, "editable_paths": ["relative"]},
        {"prompt": "x" * 100_001},
        {"images": "invalid"},
    ],
)
async def test_invalid_requests_do_not_launch_or_write(storage, agent, invoke, extra):
    with pytest.raises(ToolExecutionError) as error:
        await invoke({"cli_name": "gemini", "prompt": "request", **extra})
    assert_no_offer(json.loads(error.value.payload))
    agent.run.assert_not_awaited()
    assert not storage._store
    storage.compare_and_setex.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_data", ["not JSON", "{}", "[]"])
async def test_corrupt_snapshot_fails_before_execution(storage, agent, invoke, bad_data):
    thread_id = str(uuid4())
    storage.setex(f"thread:{thread_id}", 100, bad_data)
    snapshot = storage._store.copy()
    with pytest.raises(ToolExecutionError, match="could not be decoded"):
        await invoke({"cli_name": "gemini", "prompt": "request", "continuation_id": thread_id})
    assert storage._store == snapshot
    agent.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_failure_is_distinct_from_missing_history(storage, agent, invoke, monkeypatch):
    thread_id = memory.create_thread("clink", {})
    snapshot = storage._store.copy()
    monkeypatch.setattr(storage, "get", Mock(side_effect=OSError("private read failure")))
    with pytest.raises(ToolExecutionError, match="Conversation storage read failed") as error:
        await invoke({"cli_name": "gemini", "prompt": "request", "continuation_id": thread_id})
    assert "private read failure" not in error.value.payload
    assert_no_offer(json.loads(error.value.payload))
    assert storage._store == snapshot
    agent.run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("cap", [1, 2, 3, 4, 5])
async def test_two_slot_capacity_and_continuation_offers(storage, agent, invoke, monkeypatch, cap):
    monkeypatch.setattr(memory, "MAX_CONVERSATION_TURNS", cap)
    args = {"cli_name": "gemini", "prompt": "request"}
    if cap == 1:
        with pytest.raises(ToolExecutionError, match="two free message slots"):
            await invoke(args)
        assert not storage._store
        agent.run.assert_not_awaited()
        return
    first = await invoke(args)
    assert first["metadata"]["history_saved"] is True
    thread_id = next(iter(storage._store)).removeprefix("thread:")
    if cap < 4:
        assert_no_offer(first)
    else:
        assert first["continuation_offer"]["remaining_turns"] == cap - 2
        assert "1 more exchanges" in first["continuation_offer"]["note"]
        second = await invoke({**args, "continuation_id": thread_id})
        assert_no_offer(second)
    snapshot = storage._store.copy()
    calls = agent.run.await_count
    with pytest.raises(ToolExecutionError, match="two free message slots"):
        await invoke({**args, "continuation_id": thread_id})
    assert storage._store == snapshot and agent.run.await_count == calls


@pytest.mark.asyncio
@pytest.mark.parametrize("continuation", [False, True], ids=["new", "existing"])
@pytest.mark.parametrize("fault", ["reject", "write-error", "capacity-change"])
async def test_reply_survives_failed_commit_without_offer_or_audit(
    storage, agent, invoke, monkeypatch, continuation, fault
):
    args = {"cli_name": "gemini", "prompt": "request"}
    if continuation:
        first = await invoke(args)
        args["continuation_id"] = first["continuation_offer"]["continuation_id"]
    snapshot = storage._store.copy()
    audit = Mock()
    monkeypatch.setattr("clink.conversation.record_event", audit)

    async def generate(**kwargs):
        if fault == "reject":
            storage.compare_and_setex.return_value = False
            storage.compare_and_setex.side_effect = lambda *args: False
        elif fault == "write-error":
            storage.compare_and_setex.side_effect = OSError("private storage failure")
        else:
            monkeypatch.setattr(memory, "MAX_CONVERSATION_TURNS", 1)
        return agent.run.return_value

    agent.run.side_effect = generate
    result = await invoke(args)
    assert result["content"] == "RAW_REPLY"
    assert result["status"] == "success"
    assert result["metadata"]["history_saved"] is False
    assert result["metadata"]["conversation_ready"] is False
    assert result["metadata"]["memory_persistence_warning"]
    assert_no_offer(result)
    assert storage._store == snapshot
    audit.assert_not_called()
    assert agent.run.await_count == (2 if continuation else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["expiry", "other-writer"])
async def test_snapshot_is_never_resurrected_or_overwritten(storage, agent, invoke, monkeypatch, change):
    clock = {"now": 1000.0}
    monkeypatch.setattr("utils.storage_backend.time.time", lambda: clock["now"])
    first = await invoke({"cli_name": "gemini", "prompt": "first"})
    thread_id = first["continuation_offer"]["continuation_id"]
    audit = Mock()
    monkeypatch.setattr("clink.conversation.record_event", audit)
    expected = {}

    async def generate(**kwargs):
        if change == "expiry":
            clock["now"] += memory.CONVERSATION_TIMEOUT_SECONDS
        else:
            memory.add_turn(thread_id, "user", "OTHER_WRITER", tool_name="chat")
            expected.update(storage._store)
        return agent.run.return_value

    agent.run.side_effect = generate
    result = await invoke({"cli_name": "gemini", "prompt": "second", "continuation_id": thread_id})
    assert result["content"] == "RAW_REPLY"
    assert result["metadata"]["history_saved"] is False
    assert result["metadata"]["memory_persistence_warning"]["code"] == "ConversationConflictError"
    assert_no_offer(result)
    assert storage._store == expected
    audit.assert_not_called()
    assert agent.run.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [CLIAgentError("timeout"), asyncio.CancelledError()])
async def test_exception_and_cancellation_preserve_snapshot(storage, agent, invoke, failure):
    first = await invoke({"cli_name": "gemini", "prompt": "first"})
    thread_id = first["continuation_offer"]["continuation_id"]
    snapshot = storage._store.copy()
    agent.run.side_effect = failure
    expected_exception = asyncio.CancelledError if isinstance(failure, asyncio.CancelledError) else ToolExecutionError
    with pytest.raises(expected_exception):
        await invoke({"cli_name": "gemini", "prompt": "second", "continuation_id": thread_id})
    assert storage._store == snapshot
    assert storage.compare_and_setex.call_count == 1


@pytest.mark.asyncio
async def test_transcripts_emit_raw_pairs_only_after_commit(storage, agent, invoke, monkeypatch, tmp_path):
    monkeypatch.setenv("PAL_TRANSCRIPTS", "true")
    raw_reply = "RAW_LONG_REPLY " * (MAX_RESPONSE_CHARS // 10)
    agent.run.return_value.parsed.content = raw_reply
    original_append = transcripts.append_event
    observed = []

    def append(thread_id, event):
        thread = memory.get_thread(thread_id)
        assert len(thread.turns) in (2, 4)
        observed.append((len(thread.turns), event["event"]))
        original_append(thread_id, event)

    monkeypatch.setattr(transcripts, "append_event", append)
    first = await invoke({"cli_name": "gemini", "prompt": "héllo"})
    thread_id = first["continuation_offer"]["continuation_id"]
    assert len(first["content"]) < len(raw_reply)
    await invoke({"cli_name": "gemini", "prompt": "follow-up", "continuation_id": thread_id})
    assert observed == [(2, "thread"), (2, "turn"), (2, "turn"), (4, "turn"), (4, "turn")]
    records = [
        json.loads(line) for line in (tmp_path / "state/transcripts" / f"{thread_id}.jsonl").read_text().splitlines()
    ]
    assert [record["turn"] for record in records[1:]] == [1, 2, 3, 4]
    assert [record["content"] for record in records[1:]] == ["héllo", raw_reply, "follow-up", raw_reply]
    for record, turn in zip(records[1:], memory.get_thread(thread_id).turns):
        assert {key: record[key] for key in turn.model_dump()} == turn.model_dump(mode="json")


@pytest.mark.asyncio
@pytest.mark.parametrize("audit_failure", [False, True], ids=["disabled", "failed"])
async def test_audit_cannot_change_memory_or_repeat_execution(
    storage, agent, invoke, monkeypatch, tmp_path, caplog, audit_failure
):
    if audit_failure:
        monkeypatch.setenv("PAL_TRANSCRIPTS", "true")
        monkeypatch.setattr(transcripts, "append_event", Mock(side_effect=OSError("private audit failure")))
    result = await invoke({"cli_name": "gemini", "prompt": "first"})
    assert result["metadata"]["history_saved"] is True
    assert len(memory.get_thread(result["continuation_offer"]["continuation_id"]).turns) == 2
    assert agent.run.await_count == 1
    assert not (tmp_path / "state").exists()
    if audit_failure:
        assert "Transcript append failed" in caplog.text


@pytest.mark.asyncio
async def test_shared_tool_instance_owns_request_local_commits_for_distinct_threads(storage, agent, monkeypatch):
    tool = CLinkTool()
    first = json.loads((await tool.execute({"cli_name": "gemini", "prompt": "seed"}))[0].text)
    thread_id = first["continuation_offer"]["continuation_id"]
    second_id = memory.create_thread("clink", {})
    ready = asyncio.Event()
    count = 0

    async def generate(**kwargs):
        nonlocal count
        count += 1
        if count == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), 2)
        marker = "INPUT_A" if "INPUT_A" in kwargs["prompt"] else "INPUT_B"
        result = agent.run.return_value
        return AgentOutput(
            parsed=ParsedCLIResponse(f"reply to {marker}", {}),
            sanitized_command=result.sanitized_command,
            returncode=0,
            stdout="",
            stderr="",
            duration_seconds=0.1,
            parser_name="fake",
        )

    agent.run.side_effect = generate
    outputs = await asyncio.gather(
        tool.execute({"cli_name": "gemini", "prompt": "INPUT_A", "continuation_id": thread_id}),
        tool.execute({"cli_name": "claude", "prompt": "INPUT_B", "continuation_id": second_id}),
    )
    saved = [json.loads(output[0].text)["metadata"]["history_saved"] for output in outputs]
    assert saved == [True, True]
    for current_id in {thread_id, second_id}:
        turns = memory.get_thread(current_id).turns
        assert turns[-1].content == f"reply to {turns[-2].content}"
        assert turns[-1].model_provider == ("gemini" if turns[-2].content == "INPUT_A" else "claude")


def test_compare_and_setex_checks_expiry_conflicts_and_refreshes_only_on_success(storage, monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr("utils.storage_backend.time.time", lambda: clock["now"])
    assert storage.compare_and_setex("key", 10, None, "old") is True
    snapshot = storage._store.copy()
    assert storage.compare_and_setex("key", 20, None, "collision") is False
    assert storage.compare_and_setex("key", 20, "different", "bad") is False
    assert storage._store == snapshot
    clock["now"] += 2
    assert storage.compare_and_setex("key", 20, "old", "new") is True
    assert storage._store["key"] == ("new", 1022.0)
    clock["now"] = 1022.0
    assert storage.compare_and_setex("key", 20, "new", "resurrected") is False
    assert storage.get("key") is None
    assert storage.compare_and_setex("key", 20, None, "fresh") is True


def test_compare_and_setex_has_one_winner_across_threads(storage):
    storage.setex("key", 100, "snapshot")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda index: storage.compare_and_setex("key", 100, "snapshot", str(index)), range(8)))
    assert results.count(True) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cli_name", ["gemini", "claude", "codex"])
async def test_saved_selection_continues_without_cli_or_role_and_never_inherits_edits(
    storage, agent, invoke, tmp_path, cli_name
):
    first_args = {"cli_name": cli_name, "role": "planner", "prompt": "first", "allow_edits": True}
    if cli_name == "claude":
        first_args["editable_paths"] = [str(tmp_path)]
    first = await invoke(first_args)
    thread_id = first["continuation_offer"]["continuation_id"]
    second = await invoke({"prompt": "follow-up", "continuation_id": thread_id})
    assert second["metadata"]["cli_name"] == cli_name
    assert second["metadata"]["role"] == "planner"
    assert agent.run.call_args.kwargs["allow_edits"] is False
    assert agent.run.call_args.kwargs["editable_paths"] == []
    assert "must NOT perform any filesystem modifications" in agent.run.call_args.kwargs["prompt"]
    thread = memory.get_thread(thread_id)
    assert thread.initial_context["cli_name"] == cli_name
    assert thread.initial_context["allow_edits"] is True  # Original provenance is preserved.
    assert thread.initial_context["clink_defaults"] == {"cli_name": cli_name, "role": "planner"}


@pytest.mark.asyncio
async def test_explicit_overrides_become_the_next_successful_defaults(storage, agent, invoke):
    first = await invoke({"cli_name": "gemini", "role": "planner", "prompt": "first"})
    thread_id = first["continuation_offer"]["continuation_id"]
    for overrides, expected in [
        ({"cli_name": "claude"}, {"cli_name": "claude", "role": "planner"}),
        ({"role": "codereviewer"}, {"cli_name": "claude", "role": "codereviewer"}),
        ({"cli_name": "codex", "role": "default"}, {"cli_name": "codex", "role": "default"}),
        ({}, {"cli_name": "codex", "role": "default"}),
    ]:
        result = await invoke({"prompt": "next", "continuation_id": thread_id, **overrides})
        assert {key: result["metadata"][key] for key in expected} == expected
        assert memory.get_thread(thread_id).initial_context["clink_defaults"] == expected
    assert memory.get_thread(thread_id).initial_context["cli_name"] == "gemini"
    assert memory.get_thread(thread_id).initial_context["role"] == "planner"


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [{"cli_name": ""}, {"cli_name": "missing"}, {"role": ""}, {"role": "missing"}])
async def test_invalid_explicit_selection_does_not_fall_back_to_saved_defaults(storage, agent, invoke, override):
    first = await invoke({"cli_name": "gemini", "prompt": "first"})
    snapshot = storage._store.copy()
    with pytest.raises(ToolExecutionError):
        await invoke({"prompt": "next", "continuation_id": first["continuation_offer"]["continuation_id"], **override})
    assert agent.run.await_count == 1
    assert storage._store == snapshot


@pytest.mark.asyncio
async def test_saved_role_missing_on_explicit_other_cli_requires_valid_override(storage, agent, invoke, monkeypatch):
    first = await invoke({"cli_name": "gemini", "role": "planner", "prompt": "first"})
    thread_id = first["continuation_offer"]["continuation_id"]
    snapshot = storage._store.copy()
    registry = CLinkTool()._registry
    get_client = registry.get_client

    def without_planner(name):
        client = get_client(name)
        if name == "claude":
            return client.model_copy(
                update={"roles": {key: value for key, value in client.roles.items() if key != "planner"}}
            )
        return client

    monkeypatch.setattr(registry, "get_client", without_planner)
    with pytest.raises(ToolExecutionError, match="Specify a valid role"):
        await invoke({"cli_name": "claude", "prompt": "next", "continuation_id": thread_id})
    assert storage._store == snapshot and agent.run.await_count == 1
    result = await invoke({"cli_name": "claude", "role": "default", "prompt": "next", "continuation_id": thread_id})
    assert result["metadata"]["role"] == "default"


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["new", "api", "legacy-cli"])
async def test_ambiguous_selection_requires_explicit_cli(storage, agent, invoke, origin):
    arguments = {"prompt": "next"}
    if origin != "new":
        # Neither an API model nor legacy initial cli_name/role is a saved successful selection.
        thread_id = memory.create_thread(
            "chat" if origin == "api" else "clink",
            {"model": "gemini-2.5-pro", "cli_name": "codex", "role": "planner", "allow_edits": True},
        )
        memory.add_turn(thread_id, "user", "first")
        memory.add_turn(thread_id, "assistant", "answer")
        arguments["continuation_id"] = thread_id
    snapshot = storage._store.copy()
    with pytest.raises(ToolExecutionError, match="cli_name is required"):
        await invoke(arguments)
    agent.run.assert_not_awaited()
    assert storage._store == snapshot
    result = await invoke({**arguments, "cli_name": "claude"})
    assert result["metadata"]["cli_name"] == "claude"
    assert result["metadata"]["role"] == "default"
    assert agent.run.call_args.kwargs["allow_edits"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["cli", "commit"])
async def test_selection_changes_only_after_successful_commit(storage, agent, invoke, failure):
    first = await invoke({"cli_name": "gemini", "role": "planner", "prompt": "first"})
    thread_id = first["continuation_offer"]["continuation_id"]
    snapshot = storage._store.copy()
    arguments = {"cli_name": "claude", "role": "default", "prompt": "next", "continuation_id": thread_id}
    if failure == "cli":
        agent.run.return_value.parsed.is_error = True
        with pytest.raises(ToolExecutionError):
            await invoke(arguments)
    else:
        storage.compare_and_setex.side_effect = lambda *args: False
        result = await invoke(arguments)
        assert result["metadata"]["history_saved"] is False
        assert_no_offer(result)
    assert storage._store == snapshot
    assert memory.get_thread(thread_id).initial_context["clink_defaults"] == {"cli_name": "gemini", "role": "planner"}


@pytest.mark.asyncio
@pytest.mark.parametrize("summary", [False, True])
async def test_reduced_reply_is_next_cli_history_but_raw_storage_and_usage_survive(storage, agent, invoke, summary):
    raw = "RAW_LARGE_BODY " * MAX_RESPONSE_CHARS
    if summary:
        raw += "<SUMMARY>VISIBLE_SUMMARY</SUMMARY>"
    agent.run.return_value.parsed.content = raw
    first = await invoke({"cli_name": "gemini", "prompt": "FIRST_INPUT"})
    thread_id = first["continuation_offer"]["continuation_id"]
    thread = memory.get_thread(thread_id)
    assert thread.turns[1].content == raw
    assert thread.turns[1].model_metadata["clink"]["visible_reply"] == first["content"]
    assert thread.turns[1].model_metadata["usage"] == {"input_tokens": 12}
    assert thread.turns[1].model_metadata["provider_calls"] == []
    second = await invoke({"prompt": "SECOND_INPUT", "continuation_id": thread_id})
    prompt = agent.run.call_args.kwargs["prompt"]
    assert first["content"] in prompt and raw not in prompt
    assert prompt.count("FIRST_INPUT") == prompt.count("SECOND_INPUT") == 1
    assert second["metadata"]["history"]["retained_messages"] == 2
    assert second["metadata"]["history"]["estimated_tokens"] <= 16_384
    assert memory.get_thread(thread_id).turns[1].content == raw


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True], ids=["oversized-newest", "incomplete-only"])
async def test_unusable_history_fails_before_launch_without_storage_or_ttl_changes(storage, agent, invoke, legacy):
    thread_id = memory.create_thread("chat", {})
    memory.add_turn(thread_id, "user", "OLD_INPUT")
    if not legacy:
        memory.add_turn(thread_id, "assistant", "x" * 70_000)
    snapshot = storage._store.copy()
    with pytest.raises(ToolExecutionError, match="no complete exchange" if legacy else "newest complete exchange"):
        await invoke({"cli_name": "gemini", "prompt": "next", "continuation_id": thread_id})
    agent.run.assert_not_awaited()
    assert storage._store == snapshot
    storage.compare_and_setex.assert_not_called()


@pytest.mark.asyncio
async def test_omitted_history_references_do_not_escape_the_budget(storage, agent, invoke):
    thread_id = memory.create_thread("chat", {})
    memory.add_turn(thread_id, "user", "OLD_INPUT" + "x" * 70_000, files=["/old-file.py"], images=["/old-image.png"])
    memory.add_turn(thread_id, "assistant", "OLD_REPLY")
    memory.add_turn(thread_id, "user", "LATEST_INPUT", files=["/kept-file.py"], images=["/kept-image.png"])
    memory.add_turn(thread_id, "assistant", "LATEST_REPLY")
    result = await invoke({"cli_name": "gemini", "prompt": "CURRENT_INPUT", "continuation_id": thread_id})
    call = agent.run.call_args.kwargs
    assert "OLD_INPUT" not in call["prompt"] and "/old-" not in call["prompt"]
    assert call["files"] == ["/kept-file.py"] and call["images"] == ["/kept-image.png"]
    assert call["prompt"].count("/kept-file.py") == call["prompt"].count("/kept-image.png") == 1
    history = call["prompt"].split("=== CONVERSATION HISTORY", 1)[1].split("\n\n=== NEW USER INPUT", 1)[0]
    from utils.token_utils import estimate_tokens

    assert result["metadata"]["history"]["estimated_tokens"] == estimate_tokens("=== CONVERSATION HISTORY" + history)
    assert result["metadata"]["history"]["omitted_messages"] == 2


def test_parent_history_depth_and_cycles_remain_bounded(storage):
    from clink.conversation import CLIConversation
    from tools.clink import CLinkRequest

    parent = None
    ids = []
    for index in range(22):
        parent = memory.create_thread("chat", {}, parent)
        memory.add_turn(parent, "user", f"INPUT_{index:02}")
        memory.add_turn(parent, "assistant", f"REPLY_{index:02}")
        ids.append(parent)
    conversation = CLIConversation.prepare(CLinkRequest(prompt="current", continuation_id=parent))
    assert conversation.history_metadata["retained_messages"] == 40
    assert "INPUT_00" not in conversation.user_content and "INPUT_01" not in conversation.user_content
    for index in range(2, 22):
        assert conversation.user_content.count(f"INPUT_{index:02}") == 1
    oldest = memory.get_thread(ids[0])
    oldest.parent_thread_id = ids[1]
    storage.setex(f"thread:{ids[0]}", 100, oldest.model_dump_json())
    cycle = CLIConversation.prepare(CLinkRequest(prompt="current", continuation_id=ids[1]))
    assert cycle.history_metadata["retained_messages"] == 4
    assert cycle.user_content.count("INPUT_00") == cycle.user_content.count("INPUT_01") == 1


def test_multi_cli_schema_allows_continuation_instead_of_cli_selection():
    schema = CLinkTool().get_input_schema()
    assert schema["required"] == ["prompt"]
    assert schema["anyOf"] == [{"required": ["cli_name"]}, {"required": ["continuation_id"]}]
    assert "last successful" in schema["properties"]["cli_name"]["description"]
