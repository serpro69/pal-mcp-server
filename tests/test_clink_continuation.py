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
    assert updated.initial_context == original_context
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
@pytest.mark.parametrize("same_thread", [False, True], ids=["different-threads", "uncoordinated-same-thread"])
async def test_shared_tool_instance_owns_request_local_commits(storage, agent, monkeypatch, same_thread):
    tool = CLinkTool()
    first = json.loads((await tool.execute({"cli_name": "gemini", "prompt": "seed"}))[0].text)
    thread_id = first["continuation_offer"]["continuation_id"]
    second_id = thread_id if same_thread else memory.create_thread("clink", {})
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
    assert sum(saved) == (1 if same_thread else 2)
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
