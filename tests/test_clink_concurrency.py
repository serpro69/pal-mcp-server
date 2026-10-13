"""Non-waiting CLI admission shares ordering and cleanup with queued API/local calls."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

import server
from clink.agents import AgentOutput, CLIAgentError
from clink.agents.base import BaseCLIAgent
from clink.parsers.base import ParsedCLIResponse
from tests.request_capture import capture_providers
from tests.test_clink_environment import ControlledProcess, client
from tools.chat import ChatTool
from tools.clink import CLinkTool
from tools.planner import PlannerTool
from tools.shared.exceptions import ToolExecutionError
from utils import conversation_coordination as coordination
from utils import conversation_memory as memory
from utils.storage_backend import InMemoryStorage


@pytest.fixture
def storage(monkeypatch, tmp_path):
    assert not coordination._entries
    monkeypatch.setattr(InMemoryStorage, "_cleanup_worker", lambda self: None)
    backend = InMemoryStorage()
    monkeypatch.setattr(memory, "get_storage", lambda: backend)
    monkeypatch.setenv("PAL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("PAL_TRANSCRIPTS", "false")
    yield backend
    backend.shutdown()
    assert not coordination._entries


def output(content="CLI_REPLY"):
    return AgentOutput(
        parsed=ParsedCLIResponse(content, {}),
        sanitized_command=["fake-cli"],
        returncode=0,
        stdout="",
        stderr="",
        duration_seconds=0.1,
        parser_name="fake",
    )


@pytest.fixture
def agent(monkeypatch):
    agent = Mock()
    agent.run = AsyncMock(return_value=output())
    monkeypatch.setattr("tools.clink.create_agent", lambda _: agent)
    return agent


@pytest.fixture(params=["direct", "mcp"])
def invoke(request):
    tool = CLinkTool()

    async def call(arguments):
        result = (
            await server.handle_call_tool("clink", arguments)
            if request.param == "mcp"
            else await tool.execute(arguments)
        )
        return json.loads(result[0].text)

    return call


def seed():
    thread_id = memory.create_thread("clink", {"clink_defaults": {"cli_name": "gemini", "role": "default"}})
    memory.add_turn(thread_id, "user", "SEED_INPUT")
    memory.add_turn(thread_id, "assistant", "SEED_REPLY")
    return thread_id


def args(thread_id, prompt="FOLLOWUP", **changes):
    return {"continuation_id": thread_id, "prompt": prompt, **changes}


def assert_busy(error):
    payload = json.loads(error.value.payload)
    assert payload["status"] == "error"
    assert "busy" in payload["content"] and "Retry" in payload["content"]
    assert payload["metadata"]["retryable"] is True
    assert payload["metadata"]["error_type"] == "ConversationBusyError"
    assert payload["continuation_offer"] is None
    assert "continuation_id" not in payload["metadata"]
    return payload


async def cleanup(tasks):
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def observe_waiter(monkeypatch, thread_id):
    """Signal an actual queued acquire, with the real lock still doing its work."""
    queued = asyncio.Event()
    lock = coordination._entries[thread_id].lock
    acquire = lock.acquire

    async def observed_acquire():
        queued.set()
        return await acquire()

    monkeypatch.setattr(lock, "acquire", observed_acquire)
    return queued


@pytest.mark.asyncio
@pytest.mark.parametrize("spelling", ["canonical", "uppercase", "hex"])
async def test_clink_rejects_overlap_before_preparation_reads_or_writes(storage, agent, invoke, monkeypatch, spelling):
    thread_id = seed()
    entered, release = asyncio.Event(), asyncio.Event()

    async def generate(**kwargs):
        entered.set()
        await release.wait()
        return output()

    agent.run.side_effect = generate
    task = asyncio.create_task(invoke(args(thread_id, "FIRST_FOLLOWUP")))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        snapshot = storage._store.copy()
        supplied_id = (
            thread_id.upper()
            if spelling == "uppercase"
            else thread_id.replace("-", "") if spelling == "hex" else thread_id
        )
        with monkeypatch.context() as patcher:
            prepare = Mock(side_effect=AssertionError("Busy calls must not prepare or read history"))
            patcher.setattr("tools.clink.prepare_request", prepare)
            patcher.setattr(storage, "get", prepare)
            with pytest.raises(ToolExecutionError) as error:
                await asyncio.wait_for(invoke(args(supplied_id, "REJECTED_INPUT")), 2)
            assert_busy(error)
            prepare.assert_not_called()
        assert storage._store == snapshot
        assert not task.done() and not release.is_set()
        assert agent.run.await_count == 1
        assert coordination._entries[thread_id].users == 1
        release.set()
        first = await asyncio.wait_for(task, 2)
        assert first["metadata"]["history_saved"] is True
        retry = await invoke(args(thread_id, "RETRY_INPUT"))
        assert retry["metadata"]["history_saved"] is True
    finally:
        release.set()
        await cleanup([task])
    assert [turn.content for turn in memory.get_thread(thread_id).turns] == [
        "SEED_INPUT",
        "SEED_REPLY",
        "FIRST_FOLLOWUP",
        "CLI_REPLY",
        "RETRY_INPUT",
        "CLI_REPLY",
    ]


def other_args(name, thread_id, tmp_path):
    if name == "planner":
        return {
            "continuation_id": thread_id,
            "step": "LOCAL_FOLLOWUP",
            "step_number": 1,
            "total_steps": 1,
            "next_step_required": False,
        }
    return {
        "continuation_id": thread_id,
        "prompt": "API_FOLLOWUP",
        "model": "gemini-2.5-flash",
        "working_directory_absolute_path": str(tmp_path),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["planner", "chat"])
async def test_api_and_local_calls_queue_behind_clink_and_see_its_committed_reply(
    storage, agent, invoke, monkeypatch, tmp_path, name
):
    provider_calls = capture_providers(monkeypatch)
    thread_id = seed()
    entered, release = asyncio.Event(), asyncio.Event()
    observed = []

    async def generate(**kwargs):
        entered.set()
        await release.wait()
        return output("COMMITTED_CLI_REPLY")

    agent.run.side_effect = generate
    tool_class = PlannerTool if name == "planner" else ChatTool
    execute = tool_class.execute

    async def after_queue(self, arguments):
        observed.append([turn.content for turn in memory.get_thread(thread_id).turns])
        return await execute(self, arguments)

    monkeypatch.setattr(tool_class, "execute", after_queue)
    tasks = [asyncio.create_task(invoke(args(thread_id, "CLI_FOLLOWUP")))]
    try:
        await asyncio.wait_for(entered.wait(), 2)
        queued = observe_waiter(monkeypatch, thread_id)
        tasks.append(asyncio.create_task(server.handle_call_tool(name, other_args(name, thread_id, tmp_path))))
        await asyncio.wait_for(queued.wait(), 2)
        assert coordination._entries[thread_id].users == 2
        assert observed == []
        assert len(memory.get_thread(thread_id).turns) == 2
        with pytest.raises(ToolExecutionError) as error:
            await asyncio.wait_for(invoke(args(thread_id, "REJECTED_INPUT")), 2)
        assert_busy(error)
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 2)
    finally:
        release.set()
        await cleanup(tasks)
    assert observed == [["SEED_INPUT", "SEED_REPLY", "CLI_FOLLOWUP", "COMMITTED_CLI_REPLY"]]
    turns = memory.get_thread(thread_id).turns
    assert [turn.role for turn in turns] == ["user", "assistant"] * 3
    assert turns[-2].tool_name == turns[-1].tool_name == name
    if name == "chat":
        assert len(provider_calls) == 1
        assert "COMMITTED_CLI_REPLY" in provider_calls[0]["prompt"]
    else:
        assert provider_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["planner", "chat"])
async def test_clink_rejects_a_local_or_api_holder(storage, agent, invoke, monkeypatch, tmp_path, name):
    capture_providers(monkeypatch)
    thread_id = seed()
    entered, release = asyncio.Event(), asyncio.Event()
    tool_class = PlannerTool if name == "planner" else ChatTool
    execute = tool_class.execute

    async def blocked(self, arguments):
        entered.set()
        await release.wait()
        return await execute(self, arguments)

    monkeypatch.setattr(tool_class, "execute", blocked)
    task = asyncio.create_task(server.handle_call_tool(name, other_args(name, thread_id, tmp_path)))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        snapshot = storage._store.copy()
        with pytest.raises(ToolExecutionError) as error:
            await asyncio.wait_for(invoke(args(thread_id)), 2)
        assert_busy(error)
        assert storage._store == snapshot
        agent.run.assert_not_awaited()
        release.set()
        await asyncio.wait_for(task, 2)
        result = await invoke(args(thread_id))
        assert result["metadata"]["history_saved"] is True
    finally:
        release.set()
        await cleanup([task])


@pytest.mark.asyncio
async def test_clink_rejects_queued_waiter_during_unlocked_handoff(storage, agent, invoke, monkeypatch):
    thread_id = seed()
    release = asyncio.Event()
    holder = coordination.coordinate_conversation(thread_id)
    await holder.__aenter__()
    queued = observe_waiter(monkeypatch, thread_id)

    async def waiting():
        async with coordination.coordinate_conversation(thread_id):
            await release.wait()

    task = asyncio.create_task(waiting())
    holder_released = False
    try:
        await asyncio.wait_for(queued.wait(), 2)
        await holder.__aexit__(None, None, None)
        holder_released = True
        assert not coordination._entries[thread_id].lock.locked()
        assert coordination._entries[thread_id].users == 1
        # Advance inline: scheduling a separate task would let the waiter resume
        # first and would only test the easier active-holder case.
        attempt = invoke(args(thread_id))
        try:
            with pytest.raises(ToolExecutionError) as error:
                attempt.send(None)
            assert_busy(error)
        finally:
            attempt.close()
        agent.run.assert_not_awaited()
        assert len(memory.get_thread(thread_id).turns) == 2
    finally:
        if not holder_released:
            await holder.__aexit__(None, None, None)
        release.set()
        await cleanup([task])


@pytest.mark.asyncio
async def test_cancelled_api_waiter_does_not_remove_clink_holder(storage, agent, invoke, monkeypatch, tmp_path):
    thread_id = seed()
    entered, release = asyncio.Event(), asyncio.Event()

    async def generate(**kwargs):
        entered.set()
        await release.wait()
        return output()

    agent.run.side_effect = generate
    tasks = [asyncio.create_task(invoke(args(thread_id)))]
    try:
        await asyncio.wait_for(entered.wait(), 2)
        queued = observe_waiter(monkeypatch, thread_id)
        tasks.append(asyncio.create_task(server.handle_call_tool("chat", other_args("chat", thread_id, tmp_path))))
        await asyncio.wait_for(queued.wait(), 2)
        tasks[1].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[1]
        assert coordination._entries[thread_id].users == 1
        with pytest.raises(ToolExecutionError) as error:
            await asyncio.wait_for(invoke(args(thread_id)), 2)
        assert_busy(error)
        release.set()
        await asyncio.wait_for(tasks[0], 2)
    finally:
        release.set()
        await cleanup(tasks)
    assert len(memory.get_thread(thread_id).turns) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["read", "validation", "runner", "parsed", "commit", "unexpected"])
async def test_every_error_exit_releases_coordination_for_retry(storage, agent, invoke, monkeypatch, fault):
    thread_id = seed()
    snapshot = storage._store.copy()
    with monkeypatch.context() as patcher:
        if fault == "read":
            patcher.setattr(storage, "get", Mock(side_effect=OSError("storage fault")))
        elif fault == "runner":
            agent.run.side_effect = CLIAgentError("timeout")
        elif fault == "parsed":
            agent.run.return_value.parsed.is_error = True
        elif fault == "commit":
            patcher.setattr(storage, "compare_and_setex", lambda *args: False)
        elif fault == "unexpected":
            agent.run.side_effect = RuntimeError("unexpected failure")
        arguments = args(thread_id, role="missing") if fault == "validation" else args(thread_id)
        if fault == "commit":
            result = await invoke(arguments)
            assert result["metadata"]["history_saved"] is False
            assert result["content"] == "CLI_REPLY"
        else:
            with pytest.raises(RuntimeError if fault == "unexpected" else ToolExecutionError):
                await invoke(arguments)
        assert not coordination._entries
        assert storage._store == snapshot
    agent.run.side_effect = None
    agent.run.return_value = output()
    assert (await invoke(args(thread_id)))["metadata"]["history_saved"] is True


@pytest.mark.asyncio
async def test_holder_cancellation_preserves_memory_and_releases_lock(storage, agent, invoke):
    thread_id = seed()
    snapshot = storage._store.copy()
    entered = asyncio.Event()

    async def cancelled(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    agent.run.side_effect = cancelled
    task = asyncio.create_task(invoke(args(thread_id)))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await cleanup([task])
    assert not coordination._entries
    assert storage._store == snapshot
    agent.run.side_effect = None
    assert (await invoke(args(thread_id)))["metadata"]["history_saved"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_id", ["", "invalid", 4, [], {}])
async def test_invalid_ids_never_enter_coordination(storage, agent, invoke, monkeypatch, bad_id):
    coordinate = Mock(side_effect=AssertionError("Invalid IDs must not become lock keys"))
    monkeypatch.setattr("tools.clink.coordinate_conversation", coordinate)
    with pytest.raises(ToolExecutionError, match="valid, nonempty UUID"):
        await invoke(args(bad_id))
    coordinate.assert_not_called()
    agent.run.assert_not_awaited()
    assert storage._store == {}


@pytest.mark.asyncio
async def test_missing_ids_release_entries(storage, agent, invoke):
    for _ in range(30):
        with pytest.raises(ToolExecutionError, match="not found or has expired"):
            await invoke(args(str(uuid4())))
        assert not coordination._entries
    agent.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_memory_commit_and_audit_hold_the_same_lock(storage, agent, invoke, monkeypatch):
    thread_id = seed()
    events = []
    commit = storage.compare_and_setex

    def checked_commit(*args):
        entry = coordination._entries[thread_id]
        assert entry.lock.locked() and entry.users == 1
        events.append("commit")
        return commit(*args)

    def checked_audit(current_id, event):
        entry = coordination._entries[thread_id]
        assert entry.lock.locked() and entry.users == 1
        assert current_id == thread_id
        assert len(memory.get_thread(thread_id).turns) == 4
        events.append(event["role"])

    monkeypatch.setattr(storage, "compare_and_setex", checked_commit)
    monkeypatch.setattr("clink.conversation.record_event", checked_audit)
    await invoke(args(thread_id))
    assert events == ["commit", "user", "assistant"]
    assert not coordination._entries


@pytest.mark.asyncio
@pytest.mark.parametrize("continued", [False, True])
async def test_distinct_and_new_threads_still_overlap(storage, agent, invoke, continued):
    ids = [seed(), seed()] if continued else [None, None]
    both, release = asyncio.Event(), asyncio.Event()
    entered = []

    async def generate(**kwargs):
        marker = "REQUEST_A" if "REQUEST_A" in kwargs["prompt"] else "REQUEST_B"
        entered.append(marker)
        if len(entered) == 2:
            both.set()
        await release.wait()
        return output(f"reply to {marker}")

    agent.run.side_effect = generate
    tasks = [
        asyncio.create_task(invoke(args(thread_id, marker, cli_name=cli)))
        for thread_id, marker, cli in zip(ids, ["REQUEST_A", "REQUEST_B"], ["gemini", "claude"])
    ]
    try:
        await asyncio.wait_for(both.wait(), 2)
        assert set(entered) == {"REQUEST_A", "REQUEST_B"}
        assert len(coordination._entries) == (2 if continued else 0)
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), 2)
    finally:
        release.set()
        await cleanup(tasks)
    for result, marker, cli in zip(results, ["REQUEST_A", "REQUEST_B"], ["gemini", "claude"]):
        thread = memory.get_thread(result["continuation_offer"]["continuation_id"])
        assert [turn.content for turn in thread.turns[-2:]] == [marker, f"reply to {marker}"]
        assert thread.initial_context["clink_defaults"]["cli_name"] == cli


@pytest.mark.asyncio
async def test_busy_mcp_error_keeps_progress_annotation(storage, agent):
    thread_id = seed()
    async with coordination.coordinate_conversation(thread_id):
        with pytest.raises(ToolExecutionError) as error:
            await server.handle_call_tool("clink", args(thread_id))
        payload = assert_busy(error)
        assert payload["metadata"]["execution"]["duration_seconds"] >= 0
        assert payload["metadata"]["execution"]["operations"] == []
        agent.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_holds_admission_until_cli_child_is_reaped(storage, invoke, monkeypatch):
    thread_id = seed()
    snapshot = storage._store.copy()
    spawned = asyncio.Event()
    processes = []
    cfg = client()

    async def spawn(*args, **kwargs):
        process = ControlledProcess(kwargs["cwd"], "cancel")
        processes.append(process)
        spawned.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr("tools.clink.create_agent", lambda _: BaseCLIAgent(cfg))
    task = asyncio.create_task(invoke(args(thread_id)))
    try:
        await asyncio.wait_for(spawned.wait(), 2)
        process = processes[0]
        await asyncio.wait_for(process.started.wait(), 2)
        task.cancel()
        await asyncio.wait_for(process.reaping.wait(), 2)
        task.cancel()  # Repeated cancellation must not release admission early.
        with pytest.raises(ToolExecutionError) as error:
            await asyncio.wait_for(invoke(args(thread_id)), 2)
        assert_busy(error)
        assert not process.reaped and process.cwd.exists()
        assert coordination._entries[thread_id].users == 1
        process.release_reap.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
    finally:
        for process in processes:
            process.release_reap.set()
        await cleanup([task])
    assert process.killed and process.reaped and not process.cwd.exists()
    assert not coordination._entries
    assert storage._store == snapshot
