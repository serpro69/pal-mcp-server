"""Workflow provenance describes attempted calls, not configured model defaults."""

import asyncio
import json

import pytest

import server
from providers.gemini import GeminiModelProvider
from tests.request_capture import capture_providers
from tests.test_conversation_storage_errors import FaultStorage
from tools.analyze import AnalyzeTool
from tools.codereview import CodeReviewTool
from tools.debug import DebugIssueTool
from tools.planner import PlannerTool
from tools.precommit import PrecommitTool
from tools.shared.exceptions import ToolExecutionError
from tools.thinkdeep import ThinkDeepTool
from tools.tracer import TracerTool
from tools.workflow.base import WorkflowTool
from utils import conversation_memory as memory

pytestmark = pytest.mark.no_mock_provider


@pytest.fixture
def captures(monkeypatch):
    storage = FaultStorage()
    monkeypatch.setattr(memory, "get_storage", lambda: storage)
    return capture_providers(monkeypatch)


def arguments(**overrides):
    return {
        "step": "investigate the issue",
        "findings": "CALLER_FINDINGS_SENTINEL",
        "step_number": 1,
        "total_steps": 1,
        "next_step_required": False,
        "model": "gemini-2.5-flash",
        **overrides,
    }


async def invoke(tool, args, through_server=False):
    if through_server:
        output = await server.handle_call_tool(tool.get_name(), args)
    else:
        output = await tool.execute(args)
    return json.loads(output[0].text)


def last_turn(result):
    return memory.get_thread(result["continuation_offer"]["continuation_id"]).turns[-1]


def assert_provenance(result, model, provider):
    assert result["metadata"]["model_used"] == model
    assert result["metadata"]["provider_used"] == provider


@pytest.mark.parametrize(
    "tool_class,options",
    [
        (ThinkDeepTool, {"next_step_required": True}),
        (PlannerTool, {}),
        (TracerTool, {"trace_mode": "precision"}),
        (CodeReviewTool, {"review_validation_type": "internal"}),
        (ThinkDeepTool, {"confidence": "certain"}),
        (CodeReviewTool, {"use_assistant_model": False}),
    ],
)
async def test_local_paths_report_null_provenance(captures, tmp_path, tool_class, options):
    file = tmp_path / "sample.py"
    file.write_text("value = 1\n")
    result = await invoke(tool_class(), arguments(relevant_files=[str(file)], **options), through_server=True)
    assert not captures
    assert_provenance(result, None, None)
    turn = last_turn(result)
    assert turn.role == "assistant"
    assert turn.model_name is None and turn.model_provider is None
    assert turn.model_metadata["provider_attempt"] is None
    assert result["metadata"]["tool_name"] == tool_class().get_name()
    assert result["step_number"] == 1


@pytest.mark.parametrize("through_server", [False, True])
@pytest.mark.parametrize(
    "model,provider,answer", [("gemini-2.5-flash", "google", "Gemini answer"), ("gpt-5", "openai", "OpenAI answer")]
)
async def test_successful_expert_result_and_memory_match_actual_attempt(
    captures, through_server, model, provider, answer
):
    result = await invoke(ThinkDeepTool(), arguments(model=model), through_server)
    assert len(captures) == 1
    assert captures[0]["model_name"] == model and captures[0]["provider"] == provider
    assert_provenance(result, model, provider)
    assert result["expert_analysis"]["raw_analysis"] == answer
    assert result["thinking_status"]["thinking_confidence"] is None
    assert result["complete_thinking"]["final_confidence"] is None
    turn = last_turn(result)
    assert turn.content == answer
    assert turn.model_name == model and turn.model_provider == provider
    assert turn.model_metadata["provider_attempt"] == {"model": model, "provider": provider}


async def test_context_failure_before_provider_has_null_provenance(captures, monkeypatch):
    def fail(self, findings):
        raise ValueError("preparation failed")

    monkeypatch.setattr(ThinkDeepTool, "prepare_expert_analysis_context", fail)
    with pytest.raises(ToolExecutionError) as failure:
        await invoke(ThinkDeepTool(), arguments())
    result = json.loads(failure.value.payload)
    assert not captures
    assert result["status"] == "error"
    assert "preparation failed" in result["content"]
    assert_provenance(result, None, None)
    assert last_turn(result).model_metadata["provider_attempt"] is None


async def test_attempted_provider_error_is_recorded_without_synthetic_reply(captures, monkeypatch):
    attempted = []

    def fail(self, **kwargs):
        attempted.append(kwargs["model_name"])
        raise RuntimeError("provider failed")

    monkeypatch.setattr(GeminiModelProvider, "generate_content", fail)
    with pytest.raises(ToolExecutionError) as failure:
        await invoke(ThinkDeepTool(), arguments(), through_server=True)
    result = json.loads(failure.value.payload)
    assert attempted == ["gemini-2.5-flash"]
    assert result["status"] == "error"
    assert_provenance(result, "gemini-2.5-flash", "google")
    thread_id = result["continuation_offer"]["continuation_id"]
    thread = memory.get_thread(thread_id)
    assert len(thread.turns) == 1 and thread.turns[0].role == "user"
    assert thread.turns[0].model_name is None  # A user input has no model provenance.
    assert thread.turns[0].model_metadata["provider_attempt"] == {"model": "gemini-2.5-flash", "provider": "google"}
    recovered = await invoke(
        ThinkDeepTool(), arguments(continuation_id=thread_id, step_number=2, confidence="certain"), True
    )
    assert_provenance(recovered, None, None)
    assert last_turn(recovered).model_name is None
    assert attempted == ["gemini-2.5-flash"]


@pytest.mark.parametrize("direct_workflow", [False, True])
async def test_invalid_reused_object_does_not_echo_previous_model(captures, direct_workflow):
    tool = ThinkDeepTool()
    await invoke(tool, arguments())
    execute = tool.execute_workflow if direct_workflow else tool.execute
    with pytest.raises(ToolExecutionError) as failure:
        await execute(arguments(step_number="invalid"))
    result = json.loads(failure.value.payload)
    assert_provenance(result, None, None)
    assert len(captures) == 1


async def test_internal_review_alias_labels_caller_and_does_not_invent_confidence(captures, tmp_path):
    file = tmp_path / "sample.py"
    file.write_text("pass\n")
    result = await invoke(CodeReviewTool(), arguments(relevant_files=[str(file)], review_validation_type="internal"))
    completion = result["complete_code_review"]
    assert completion["caller_findings"] == "CALLER_FINDINGS_SENTINEL"
    assert completion["final_analysis"] == completion["caller_findings"]
    assert completion["final_analysis_source"] == "caller"
    assert completion["confidence_level"] is None
    assert result["code_review_status"]["current_confidence"] is None
    assert "no external model analysis was performed" in result["next_steps"]
    assert result["expert_analysis"]["status"] == "skipped_due_to_internal_analysis_type"
    assert not captures


async def test_explicit_legacy_review_confidence_is_preserved(captures, tmp_path):
    file = tmp_path / "sample.py"
    file.write_text("pass\n")
    result = await invoke(
        CodeReviewTool(),
        arguments(relevant_files=[str(file)], review_validation_type="internal", confidence="high"),
    )
    assert result["complete_code_review"]["confidence_level"] == "high"
    assert result["code_review_status"]["current_confidence"] == "high"
    assert not captures


@pytest.mark.parametrize(
    "tool_class,key",
    [
        (CodeReviewTool, "complete_code_review"),
        (AnalyzeTool, "complete_analysis"),
        (PrecommitTool, "complete_validation"),
    ],
)
async def test_summaries_do_not_report_internal_default_confidence(captures, tmp_path, tool_class, key):
    file = tmp_path / "sample.py"
    file.write_text("pass\n")
    result = await invoke(tool_class(), arguments(relevant_files=[str(file)], path=str(tmp_path)))
    assert len(captures) == 1
    summary = result[key]["work_summary"]
    assert "CALLER_FINDINGS_SENTINEL" in summary
    assert "confidence)" not in summary


async def test_summary_retains_only_confidence_explicitly_supplied_on_that_step(captures, tmp_path):
    file = tmp_path / "sample.py"
    file.write_text("pass\n")
    first = await invoke(
        CodeReviewTool(),
        arguments(
            relevant_files=[str(file)],
            confidence="high",
            next_step_required=True,
        ),
    )
    thread_id = first["continuation_offer"]["continuation_id"]
    result = await invoke(CodeReviewTool(), arguments(continuation_id=thread_id, step_number=2))
    summary = result["complete_code_review"]["work_summary"]
    assert summary.count("(high confidence)") == 1
    assert "Step 1 (high confidence)" in summary
    assert "Step 2: CALLER_FINDINGS_SENTINEL" in summary


async def test_legacy_summary_without_explicit_confidence_marker_remains_unknown(captures, tmp_path):
    file = tmp_path / "sample.py"
    file.write_text("pass\n")
    first = await invoke(CodeReviewTool(), arguments(relevant_files=[str(file)], next_step_required=True))
    thread_id = first["continuation_offer"]["continuation_id"]
    thread = memory.get_thread(thread_id)
    for step in thread.turns[-1].model_metadata["work_history"]:
        step.pop("caller_confidence")  # Stored before explicit confidence tracking.
    memory._save_thread(thread)
    result = await invoke(CodeReviewTool(), arguments(continuation_id=thread_id, step_number=2))
    assert "confidence)" not in result["complete_code_review"]["work_summary"]


@pytest.mark.parametrize("through_server", [False, True])
@pytest.mark.parametrize("failure", ["path", "prompt_size"])
async def test_early_validation_errors_have_null_provenance(captures, through_server, failure):
    from config import MCP_PROMPT_SIZE_LIMIT

    overrides = (
        {"relevant_files": ["relative-invalid.py"]}
        if failure == "path"
        else {"step": "x" * (MCP_PROMPT_SIZE_LIMIT + 1)}
    )
    with pytest.raises(ToolExecutionError) as error:
        await invoke(ThinkDeepTool(), arguments(**overrides), through_server)
    assert_provenance(json.loads(error.value.payload), None, None)
    assert not captures


@pytest.mark.parametrize("failure", ["model", "file_size"])
async def test_mcp_policy_errors_have_null_provenance(captures, monkeypatch, tmp_path, failure):
    args = arguments(model="unavailable-model")
    if failure == "file_size":
        monkeypatch.setattr(
            "utils.file_utils.check_total_file_size",
            lambda *args, **kwargs: {"status": "error", "content": "files exceed budget", "metadata": {"limit": 1}},
        )
        args = arguments(relevant_files=[str(tmp_path / "oversized.py")])
    with pytest.raises(ToolExecutionError) as error:
        await invoke(ThinkDeepTool(), args, through_server=True)
    result = json.loads(error.value.payload)
    assert_provenance(result, None, None)
    if failure == "file_size":
        assert result["metadata"]["limit"] == 1
    assert not captures


@pytest.mark.parametrize(
    "tool_class,key,value",
    [
        (DebugIssueTool, "complete_investigation", "caller hypothesis"),
        (ThinkDeepTool, "complete_thinking", "CALLER_FINDINGS_SENTINEL"),
    ],
)
async def test_skipped_analysis_retains_explicit_confidence_and_compatibility_alias(captures, tool_class, key, value):
    result = await invoke(tool_class(), arguments(confidence="certain", hypothesis="caller hypothesis"))
    completion = result[key]
    assert completion["caller_findings"] == value
    assert completion["final_analysis"] == value
    assert completion["final_analysis_source"] == "caller"
    assert completion["confidence_level"] == "certain"
    assert not captures


async def test_one_completion_handler_preserves_optional_description(captures):
    tool = ThinkDeepTool()
    request = tool.get_workflow_request_model()(**arguments(confidence="certain"))
    result = tool.handle_completion_without_expert_analysis(request, tool.consolidated_findings, "original description")
    assert result["complete_thinking"]["initial_request"] == "original description"
    assert "handle_completion_without_expert_analysis" not in WorkflowTool.__dict__


async def test_concurrent_provider_results_and_persistence_keep_their_own_provenance(captures, monkeypatch):
    both_called = asyncio.Event()
    finish = asyncio.Event()
    completed = []
    original = ThinkDeepTool._call_expert_analysis

    async def interleave(self, args, request):
        result = await original(self, args, request)
        completed.append(request.model)
        if len(completed) == 2:
            both_called.set()
        await finish.wait()
        return result

    monkeypatch.setattr(ThinkDeepTool, "_call_expert_analysis", interleave)
    models = [("gemini-2.5-flash", "google"), ("gpt-5", "openai")]
    tasks = [asyncio.create_task(invoke(ThinkDeepTool(), arguments(model=model), True)) for model, _ in models]
    try:
        await asyncio.wait_for(both_called.wait(), 2)
        finish.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), 2)
    finally:
        finish.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    for result, (model, provider) in zip(results, models):
        assert_provenance(result, model, provider)
        turn = last_turn(result)
        assert turn.model_name == model and turn.model_provider == provider
        assert turn.model_metadata["provider_attempt"] == {"model": model, "provider": provider}
    assert len(captures) == 2
