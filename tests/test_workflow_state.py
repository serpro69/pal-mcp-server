"""Workflow state belongs to its tool and conversation, never a schema prototype."""

import asyncio
import json

import pytest

import server
from tests.request_capture import capture_providers
from tests.test_conversation_storage_errors import FaultStorage
from tools.analyze import AnalyzeTool
from tools.codereview import CodeReviewTool
from tools.consensus import ConsensusTool
from tools.planner import PlannerTool
from tools.precommit import PrecommitTool
from tools.refactor import RefactorTool
from tools.secaudit import SecauditTool
from tools.thinkdeep import ThinkDeepTool
from tools.tracer import TracerTool
from utils import conversation_memory as memory


@pytest.fixture
def state_env(monkeypatch):
    storage = FaultStorage()
    monkeypatch.setattr(memory, "get_storage", lambda: storage)
    return capture_providers(monkeypatch)


def step_args(**overrides):
    return {
        "step": "original task",
        "findings": "first findings",
        "step_number": 1,
        "total_steps": 4,
        "next_step_required": True,
        "model": "gemini-2.5-flash",
        **overrides,
    }


async def execute(tool, args):
    return json.loads((await tool.execute(args))[0].text)


@pytest.mark.parametrize("name", list(server.TOOLS))
def test_registered_tools_support_fresh_construction(name):
    prototype = server.TOOLS[name]
    fresh = type(prototype)()
    assert fresh is not prototype
    assert fresh.get_input_schema() == prototype.get_input_schema()
    assert fresh.get_name() == name


@pytest.mark.parametrize(
    "tool_class,field,options,expected",
    [
        (CodeReviewTool, "review_config", {"focus_on": "REVIEW_SENTINEL"}, ("focus_on", "REVIEW_SENTINEL")),
        (AnalyzeTool, "analysis_config", {"analysis_type": "performance"}, ("analysis_type", "performance")),
        (PrecommitTool, "git_config", {"compare_to": "BASE_SENTINEL"}, ("compare_to", "BASE_SENTINEL")),
        (RefactorTool, "refactor_config", {"refactor_type": "modernize"}, ("refactor_type", "modernize")),
        (TracerTool, "trace_config", {"trace_mode": "dependencies"}, ("trace_mode", "dependencies")),
        (ThinkDeepTool, "stored_request_params", {"temperature": 0.25}, ("temperature", 0.25)),
        (SecauditTool, "security_config", {"security_scope": "SCOPE_SENTINEL"}, ("security_scope", "SCOPE_SENTINEL")),
    ],
)
async def test_configuration_survives_fresh_objects_and_intermediate_steps(
    state_env, tmp_path, tool_class, field, options, expected
):
    file = tmp_path / "sample.py"
    file.write_text("value = 1\n")
    first = await execute(tool_class(), step_args(relevant_files=[str(file)], path=str(tmp_path), **options))
    thread_id = first["continuation_offer"]["continuation_id"]
    for step in (2, 3):
        tool = tool_class()
        await execute(tool, step_args(step_number=step, step=f"step {step}", continuation_id=thread_id))
        assert getattr(tool, field)[expected[0]] == expected[1]
        thread = memory.get_thread(thread_id)
        assert thread.turns[-1].model_metadata["tool_state"][field][expected[0]] == expected[1]
        assert len(tool.work_history) == step
        assert tool.initial_request == "original task"


async def test_planner_branches_restore_and_reused_object_resets(state_env):
    tool = PlannerTool()
    first = await execute(tool, step_args(is_branch_point=True, branch_id="branch-a", branch_from_step=1))
    thread_a = first["continuation_offer"]["continuation_id"]
    other = await execute(
        tool, step_args(step="other task", is_branch_point=True, branch_id="branch-b", branch_from_step=1)
    )
    thread_b = other["continuation_offer"]["continuation_id"]
    assert other["metadata"]["branches"] == ["branch-b"]
    assert len(tool.work_history) == 1
    fresh = PlannerTool()
    resumed = await execute(fresh, step_args(step_number=2, continuation_id=thread_a))
    assert resumed["metadata"]["branches"] == ["branch-a"]
    assert fresh.get_initial_request("fallback") == "original task"
    assert len(memory.get_thread(thread_a).turns) == 4
    assert len(memory.get_thread(thread_b).turns) == 2


async def test_legacy_and_cross_tool_metadata_cannot_borrow_configuration(state_env):
    tool = PlannerTool()
    await execute(tool, step_args(is_branch_point=True, branch_id="must-not-leak", branch_from_step=1))
    thread_id = memory.create_thread("planner", {})
    memory.add_turn(
        thread_id,
        "assistant",
        "legacy result",
        tool_name="planner",
        model_metadata={"work_history": [], "initial_request": "legacy task"},
    )
    memory.add_turn(
        thread_id,
        "assistant",
        "different tool",
        tool_name="thinkdeep",
        model_metadata={"work_history": [], "tool_state": {"branches": {"foreign": []}}},
    )
    result = await execute(tool, step_args(step_number=2, continuation_id=thread_id))
    assert result["metadata"]["branches"] == []
    assert tool.get_initial_request("fallback") == "legacy task"
    assert len(tool.work_history) == 1


async def test_dispatch_interleaves_independent_instances_then_restores(state_env, monkeypatch):
    entered = []
    both_entered = asyncio.Event()
    finish = asyncio.Event()
    original = PlannerTool.handle_work_completion

    async def barrier(self, response, request, arguments):
        entered.append(self)
        if len(entered) == 2:
            both_entered.set()
        await finish.wait()
        return await original(self, response, request, arguments)

    monkeypatch.setattr(PlannerTool, "handle_work_completion", barrier)
    tasks = [
        asyncio.create_task(server.handle_call_tool("planner", step_args(step=name, next_step_required=False)))
        for name in ("alpha", "beta")
    ]
    try:
        await asyncio.wait_for(both_entered.wait(), 2)
        assert entered[0] is not entered[1]
        assert all(tool is not server.TOOLS["planner"] for tool in entered)
        finish.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), 2)
    finally:
        finish.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    for name, result in zip(("alpha", "beta"), results):
        thread_id = json.loads(result[0].text)["continuation_offer"]["continuation_id"]
        await server.handle_call_tool("planner", step_args(step_number=2, continuation_id=thread_id))
        state = memory.get_thread(thread_id).turns[-1].model_metadata
        assert state["initial_request"] == name
        assert len(state["work_history"]) == 2


async def test_thinkdeep_uses_initial_parameters_after_two_restorations(state_env):
    first = await execute(ThinkDeepTool(), step_args(temperature=0.25, thinking_mode="high"))
    thread_id = first["continuation_offer"]["continuation_id"]
    await execute(ThinkDeepTool(), step_args(step_number=2, continuation_id=thread_id))
    await execute(ThinkDeepTool(), step_args(step_number=3, next_step_required=False, continuation_id=thread_id))
    assert state_env[-1]["temperature"] == 0.25
    assert state_env[-1]["thinking_mode"] == "high"


async def test_failed_consensus_participant_preserves_roster_without_fake_reply(state_env, monkeypatch):
    from providers.gemini import GeminiModelProvider

    def fail(self, **kwargs):
        raise RuntimeError("participant failed")

    monkeypatch.setattr(GeminiModelProvider, "generate_content", fail)
    first = await execute(
        ConsensusTool(),
        step_args(models=[{"model": "gemini-2.5-flash"}, {"model": "gpt-5"}]),
    )
    thread_id = first["continuation_offer"]["continuation_id"]
    assert [turn.role for turn in memory.get_thread(thread_id).turns] == ["user"]
    second = await execute(ConsensusTool(), step_args(step="caller summary", step_number=2, continuation_id=thread_id))
    assert second["model_consulted"] == "gpt-5"
    assert second["accumulated_responses"][0]["status"] == "error"
    assert state_env[-1]["prompt"] == "original task"
    assert [turn.role for turn in memory.get_thread(thread_id).turns] == ["user", "user", "assistant"]
