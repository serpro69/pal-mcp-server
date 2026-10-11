"""Concurrency contracts at the actual MCP dispatch boundary."""

import asyncio
import json

import pytest

import server
from tests.test_conversation_storage_errors import FaultStorage
from tools.planner import PlannerTool
from tools.shared.exceptions import ToolExecutionError
from utils import conversation_coordination as coordination
from utils import conversation_memory as memory


@pytest.fixture
def storage(monkeypatch):
    backend = FaultStorage()
    monkeypatch.setattr(memory, "get_storage", lambda: backend)
    assert not coordination._entries
    yield backend
    assert not coordination._entries


def args(step, thread_id=None, step_number=1, complete=True):
    return {
        "step": step,
        "step_number": step_number,
        "total_steps": 5,
        "next_step_required": not complete,
        "continuation_id": thread_id,
    }


async def call(**kwargs):
    result = await server.handle_call_tool("planner", args(**kwargs))
    return json.loads(result[0].text)


async def seed():
    return (await call(step="initial", complete=False))["continuation_offer"]["continuation_id"]


async def cleanup(tasks):
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("free_slots", [2, 4])
async def test_same_thread_admission_and_workflow_updates_are_serialized(storage, monkeypatch, free_slots):
    thread_id = await seed()
    monkeypatch.setattr(memory, "MAX_CONVERSATION_TURNS", 2 + free_slots)
    entered = asyncio.Event()
    finish = asyncio.Event()
    queued = asyncio.Event()
    executed = []
    original = PlannerTool.handle_work_completion

    async def block(self, response, request, arguments):
        executed.append(request.step)
        if request.step == "first":
            entered.set()
            await finish.wait()
        return await original(self, response, request, arguments)

    async def second_call():
        queued.set()
        return await call(step="second", thread_id=thread_id, step_number=3)

    monkeypatch.setattr(PlannerTool, "handle_work_completion", block)
    tasks = [asyncio.create_task(call(step="first", thread_id=thread_id, step_number=2))]
    try:
        await asyncio.wait_for(entered.wait(), 2)
        tasks.append(asyncio.create_task(second_call()))
        await asyncio.wait_for(queued.wait(), 2)
        assert coordination._entries[thread_id].users == 2
        assert executed == ["first"]
        assert len(memory.get_thread(thread_id).turns) == 3
        finish.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 2)
    finally:
        finish.set()
        await cleanup(tasks)
    thread = memory.get_thread(thread_id)
    if free_slots == 2:
        assert isinstance(results[1], ToolExecutionError)
        assert "two free message slots" in str(results[1])
        assert executed == ["first"]
        assert [turn.role for turn in thread.turns] == ["user", "assistant", "user", "assistant"]
    else:
        assert all(isinstance(result, dict) for result in results)
        assert [turn.content for turn in thread.turns if turn.role == "user"] == ["initial", "first", "second"]
        assert [turn.role for turn in thread.turns] == ["user", "assistant"] * 3
        assert len(thread.turns[-1].model_metadata["work_history"]) == 3
    assert not coordination._entries


async def test_different_existing_ids_overlap(storage, monkeypatch):
    thread_ids = [await seed(), await seed()]
    entered = []
    both_entered = asyncio.Event()
    finish = asyncio.Event()
    original = PlannerTool.handle_work_completion

    async def block(self, response, request, arguments):
        entered.append(request.continuation_id)
        if len(entered) == 2:
            both_entered.set()
        await finish.wait()
        return await original(self, response, request, arguments)

    monkeypatch.setattr(PlannerTool, "handle_work_completion", block)
    tasks = [asyncio.create_task(call(step="continue", thread_id=thread, step_number=2)) for thread in thread_ids]
    try:
        await asyncio.wait_for(both_entered.wait(), 2)
        assert set(entered) == set(thread_ids)
        finish.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 2)
    finally:
        finish.set()
        await cleanup(tasks)
    assert all(len(memory.get_thread(thread).turns) == 4 for thread in thread_ids)


@pytest.mark.parametrize("outcome", ["cancel", "failure"])
async def test_failed_holder_keeps_only_input_and_releases_unused_capacity(storage, monkeypatch, outcome):
    thread_id = await seed()
    monkeypatch.setattr(memory, "MAX_CONVERSATION_TURNS", 5)
    entered = asyncio.Event()
    finish = asyncio.Event()
    original = PlannerTool.handle_work_completion

    async def block(self, response, request, arguments):
        if request.step == "failed input":
            entered.set()
            await finish.wait()
            raise RuntimeError("synthetic failure")
        return await original(self, response, request, arguments)

    monkeypatch.setattr(PlannerTool, "handle_work_completion", block)
    task = asyncio.create_task(call(step="failed input", thread_id=thread_id, step_number=2))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        if outcome == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            finish.set()
            with pytest.raises(ToolExecutionError, match="synthetic failure"):
                await task
    finally:
        await cleanup([task])
    assert not coordination._entries
    assert [turn.role for turn in memory.get_thread(thread_id).turns] == ["user", "assistant", "user"]
    result = await asyncio.wait_for(call(step="recovery", thread_id=thread_id, step_number=2), 2)
    assert result["continuation_offer"]["remaining_turns"] == 0
    assert len(memory.get_thread(thread_id).turns) == 5


async def test_waiter_cancellation_does_not_evict_live_lock(storage, monkeypatch):
    thread_id = await seed()
    entered = asyncio.Event()
    finish = asyncio.Event()
    queued = asyncio.Event()
    original = PlannerTool.handle_work_completion

    async def block(self, response, request, arguments):
        entered.set()
        await finish.wait()
        return await original(self, response, request, arguments)

    async def waiting_call():
        queued.set()
        return await call(step="cancelled waiter", thread_id=thread_id, step_number=3)

    monkeypatch.setattr(PlannerTool, "handle_work_completion", block)
    tasks = [asyncio.create_task(call(step="holder", thread_id=thread_id, step_number=2))]
    try:
        await asyncio.wait_for(entered.wait(), 2)
        tasks.append(asyncio.create_task(waiting_call()))
        await asyncio.wait_for(queued.wait(), 2)
        tasks[1].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[1]
        assert coordination._entries[thread_id].users == 1
        assert len(memory.get_thread(thread_id).turns) == 3
        finish.set()
        await asyncio.wait_for(tasks[0], 2)
    finally:
        finish.set()
        await cleanup(tasks)


async def test_idle_entries_are_reclaimed_across_many_ids(storage):
    for index in range(100):
        async with coordination.coordinate_conversation(str(index)):
            assert len(coordination._entries) == 1
        assert not coordination._entries
