"""Consensus coverage reflects successful participants across fresh invocations."""

import json

import pytest

from providers.openai import OpenAIModelProvider
from providers.registry import ModelProviderRegistry
from providers.shared import ProviderType
from tests.request_capture import capture_providers
from tests.test_conversation_storage_errors import FaultStorage
from tools.consensus import ConsensusTool
from utils import conversation_memory as memory


def arguments(**overrides):
    return {
        "step": "ORIGINAL_PROPOSAL",
        "findings": "caller analysis",
        "step_number": 1,
        "total_steps": 2,
        "next_step_required": True,
        **overrides,
    }


async def run_step(**kwargs):
    return json.loads((await ConsensusTool().execute(arguments(**kwargs)))[0].text)


@pytest.mark.parametrize("outcome", ["success", "one_failure", "lost_provider", "zero_success", "empty"])
async def test_consensus_outcomes_survive_continuation_and_both_completions(monkeypatch, outcome):
    monkeypatch.setattr(memory, "get_storage", lambda: storage)
    storage = FaultStorage()

    def answer(kwargs):
        if outcome == "zero_success":
            raise RuntimeError("synthetic consultation failure")
        return "SUCCESSFUL_VERDICT"

    calls = capture_providers(monkeypatch, google_answer=answer, openai_answer=answer)
    first = await run_step(models=[{"model": "gemini-2.5-flash", "stance": "for"}, {"model": "gpt-5"}])
    thread_id = first["continuation_offer"]["continuation_id"]
    if outcome == "one_failure":

        def fail(self, **kwargs):
            raise RuntimeError("second participant failed")

        monkeypatch.setattr(OpenAIModelProvider, "generate_content", fail)
    elif outcome == "lost_provider":
        monkeypatch.setattr(ModelProviderRegistry(), "_initialized_providers", {})
        monkeypatch.setitem(ModelProviderRegistry()._providers, ProviderType.OPENAI, lambda api_key: None)
    elif outcome == "empty":
        capture_providers(monkeypatch, openai_answer="   ")

    final = await run_step(step="CALLER_SUMMARY", step_number=2, continuation_id=thread_id)
    expected_status = "complete" if outcome == "success" else "failed" if outcome == "zero_success" else "partial"
    expected_success = 2 if outcome == "success" else 0 if outcome == "zero_success" else 1
    summary = final["complete_consensus"]
    assert summary["status"] == expected_status
    assert final["status"] == f"consensus_workflow_{expected_status}"
    assert final["consensus_workflow_status"] == expected_status
    assert final["consensus_complete"] is (outcome == "success")
    assert final["next_step_required"] is False
    assert summary["initial_prompt"] == "ORIGINAL_PROPOSAL"
    assert summary["requested_models"] == 2
    assert summary["successful_models"] == summary["total_responses"] == expected_success
    assert summary["failed_models"] == 2 - expected_success
    assert summary["pending_models"] == 0
    assert len(summary["participant_errors"]) == 2 - expected_success
    assert summary["successful_providers"] == (
        ["google", "openai"] if outcome == "success" else ["google"] if expected_success else []
    )
    assert summary["successful_provider_count"] == expected_success
    assert summary["consensus_confidence"] is None
    assert final["metadata"]["models_consulted"] == summary["models_consulted"]
    assert final["metadata"]["consensus_complete"] == final["consensus_complete"]
    if expected_success:
        assert final["accumulated_responses"][0]["verdict"] == "SUCCESSFUL_VERDICT"
    if outcome != "success":
        assert expected_status.upper() in final["next_steps"]
    assert all(call["prompt"] == "ORIGINAL_PROPOSAL" for call in calls)

    # The extra synthesis step takes the base workflow completion path on a fresh tool.
    synthesis = await run_step(step="synthesize", step_number=3, next_step_required=False, continuation_id=thread_id)
    for key in ("complete_consensus", "status", "consensus_complete", "next_steps", "consensus_workflow_status"):
        assert synthesis[key] == final[key]
    assert synthesis["metadata"]["models_consulted"] == summary["models_consulted"]


async def test_skipped_and_repeated_participants_do_not_inflate_coverage(monkeypatch):
    storage = FaultStorage()
    monkeypatch.setattr(memory, "get_storage", lambda: storage)
    capture_providers(monkeypatch)
    first = await run_step(models=[{"model": "gpt-5", "stance": stance} for stance in ("for", "against", "neutral")])
    thread_id = first["continuation_offer"]["continuation_id"]
    for _ in range(2):
        final = await run_step(step_number=3, continuation_id=thread_id)
        summary = final["complete_consensus"]
        assert summary["status"] == "partial"
        assert summary["requested_models"] == 3
        assert summary["successful_models"] == 2
        assert summary["failed_models"] == 0
        assert summary["pending_models"] == 1
        assert summary["successful_providers"] == ["openai"]
        assert summary["successful_provider_count"] == 1


async def test_unknown_provider_coverage_is_not_inferred():
    tool = ConsensusTool()
    tool.models_to_consult = [{"model": "legacy"}]
    tool.accumulated_responses = [{"model": "legacy", "status": "success", "verdict": "legacy answer"}]
    result = await tool.handle_work_completion({}, None, {})
    assert result["complete_consensus"]["successful_models"] == 1
    assert result["complete_consensus"]["successful_providers"] == []
