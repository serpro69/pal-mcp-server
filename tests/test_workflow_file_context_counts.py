"""Coverage counts describe the actual request, including blinded participants."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

import server
from providers.gemini import GeminiModelProvider
from tests.mcp_client import call_tool as call_mcp_tool
from tests.request_capture import capture_providers
from tests.test_conversation_storage_errors import FaultStorage
from tests.test_file_size_gate import model_context
from tools.chat import ChatRequest, ChatTool
from tools.shared.exceptions import ToolExecutionError
from utils import conversation_memory as memory
from utils.file_utils import FileReadReport


@pytest.fixture
def captures(monkeypatch, tmp_path):
    storage = FaultStorage()
    monkeypatch.setattr(memory, "get_storage", lambda: storage)
    monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(tmp_path))
    return capture_providers(monkeypatch)


@pytest.mark.parametrize("tool_name", ["thinkdeep", "codereview", "debug"])
async def test_workflow_counts_only_successful_rendered_files(captures, tmp_path, tool_name):
    present = tmp_path / "present.py"
    present.write_text("CURRENT_FILE_SENTINEL")
    missing = tmp_path / "missing.py"
    result = await server.handle_call_tool(
        tool_name,
        {
            "model": "gemini-2.5-flash",
            "step": "inspect",
            "findings": "inspect files",
            "step_number": 1,
            "total_steps": 1,
            "next_step_required": False,
            "relevant_files": [str(present), str(present), str(missing)],
        },
    )
    data = json.loads(result[0].text)
    assert data["file_context"]["files_embedded"] == 1
    assert data["file_context"]["files_in_history"] == 0
    assert data["file_context"]["files_unavailable"] == 1
    assert captures[0]["prompt"].count("CURRENT_FILE_SENTINEL") == 1
    assert "FILE NOT FOUND" in captures[0]["prompt"]
    reply = memory.get_thread(data["continuation_id"]).turns[-1]
    assert reply.model_metadata["included_files"] == [str(present)]


async def test_local_completion_does_not_claim_expert_file_delivery(captures, tmp_path):
    path = tmp_path / "local.py"
    path.write_text("LOCAL_ONLY")
    result = await server.handle_call_tool(
        "codereview",
        {
            "model": "gemini-2.5-flash",
            "step": "done",
            "findings": "local findings",
            "step_number": 1,
            "total_steps": 1,
            "next_step_required": False,
            "relevant_files": [str(path)],
            "review_validation_type": "internal",
        },
    )
    data = json.loads(result[0].text)
    assert not captures
    reply = memory.get_thread(data["continuation_id"]).turns[-1]
    assert "included_files" not in reply.model_metadata
    assert data["file_context"]["files_embedded"] == 0
    assert data["file_context"]["files_in_history"] == 0


async def test_continuation_counts_exclude_deleted_historical_files(captures, tmp_path):
    deleted = tmp_path / "deleted.py"
    deleted.write_text("STALE_CONTENT")
    args = {"model": "gemini-2.5-flash", "prompt": "first", "working_directory_absolute_path": str(tmp_path)}
    first = json.loads((await server.handle_call_tool("chat", {**args, "absolute_file_paths": [str(deleted)]}))[0].text)
    deleted.unlink()
    present = tmp_path / "current.py"
    present.write_text("FRESH_CONTENT")
    second = json.loads(
        (
            await server.handle_call_tool(
                "chat",
                {
                    **args,
                    "prompt": "second",
                    "continuation_id": first["continuation_offer"]["continuation_id"],
                    "absolute_file_paths": [str(present)],
                },
            )
        )[0].text
    )
    coverage = second["metadata"]["file_context"]
    assert coverage["files_in_history"] == 1
    assert coverage["files_embedded"] == 0
    assert coverage["files_unavailable"] == 1
    assert "STALE_CONTENT" not in captures[1]["prompt"]
    assert captures[1]["prompt"].count("FRESH_CONTENT") == 1


async def test_continuation_file_gate_uses_remaining_input(captures, tmp_path, monkeypatch):
    original = GeminiModelProvider("synthetic").get_capabilities("gemini-2.5-flash")
    monkeypatch.setattr(
        GeminiModelProvider,
        "get_capabilities",
        lambda self, name: replace(
            original,
            context_window=20000,
            max_output_tokens=1000,
        ),
    )
    thread = memory.create_thread("chat", {})
    memory.add_turn(thread, "assistant", "previous result")
    path = tmp_path / "attachment.py"
    path.write_text("file context " * 300)
    with pytest.raises(ToolExecutionError) as caught:
        await server.handle_call_tool(
            "chat",
            {
                "model": "gemini-2.5-flash",
                "continuation_id": thread,
                "prompt": "x" * 45000,
                "absolute_file_paths": [str(path)],
                "working_directory_absolute_path": str(tmp_path),
            },
        )
    payload = json.loads(caught.value.payload)
    assert payload["status"] == "code_too_large"
    assert payload["metadata"]["binding_limit"] == "remaining_input"
    assert not captures


def test_history_omission_does_not_claim_embedded_content(captures, tmp_path):
    large = tmp_path / "large.py"
    large.write_text("unrendered " * 3000)
    small = tmp_path / "small.py"
    small.write_text("included")
    thread = memory.create_thread("chat", {})
    memory.add_turn(thread, "user", "review", files=[str(small), str(large), str(tmp_path / "missing.py")])
    report = FileReadReport()
    history, _ = memory.build_conversation_history(
        memory.get_thread(thread), model_context(10000, 1000), file_report=report
    )
    assert report.included == [str(small)]
    assert str(large) in report.omitted
    assert "unrendered" not in history
    assert "SKIPPED FILES" in history
    # A new rendering has no coverage merely because references exist in storage.
    assert ChatTool().get_conversation_embedded_files(thread) == []


async def test_consensus_never_charges_or_deduplicates_hidden_history(captures, tmp_path, monkeypatch):
    def forbidden(*args):
        raise AssertionError("Blinded participant must not inspect generic history budget")

    monkeypatch.setattr("utils.conversation_context.remaining_input_budget", forbidden)
    attachment = tmp_path / "participant.py"
    attachment.write_text("PARTICIPANT_FILE")
    first = json.loads(
        (
            await server.handle_call_tool(
                "consensus",
                {
                    "step": "ORIGINAL_PROPOSAL",
                    "findings": "proposal",
                    "step_number": 1,
                    "total_steps": 2,
                    "next_step_required": True,
                    "relevant_files": [str(attachment)],
                    "models": [{"model": "gemini-2.5-flash"}, {"model": "gpt-5"}],
                },
            )
        )[0].text
    )
    second_result = await server.handle_call_tool(
        "consensus",
        {
            "continuation_id": first["continuation_offer"]["continuation_id"],
            "step": "HIDDEN_SUMMARY " * 1000,
            "findings": "HIDDEN_VERDICT",
            "step_number": 2,
            "total_steps": 2,
            "next_step_required": False,
            "relevant_files": [str(attachment)],
        },
    )
    second = json.loads(second_result[0].text)
    for response in (first, second):
        coverage = response["model_response"]["metadata"]["file_context"]
        assert coverage["files_embedded"] == 1
        assert coverage["files_in_history"] == 0
    assert len(captures) == 2
    for call in captures:
        assert "ORIGINAL_PROPOSAL" in call["prompt"]
        assert call["prompt"].count("PARTICIPANT_FILE") == 1
        assert "HIDDEN_" not in call["prompt"]


@pytest.mark.parametrize("invalid", [None, [], "label", {"path": None}, {"path": 4}])
async def test_chat_rejects_malformed_file_descriptions(captures, tmp_path, invalid):
    args = {
        "prompt": "inspect",
        "model": "gemini-2.5-flash",
        "working_directory_absolute_path": str(tmp_path),
        "file_descriptions": invalid,
    }
    with pytest.raises(ValidationError):
        ChatRequest(**args)
    result = await call_mcp_tool("chat", args)
    assert result.is_error
    assert not captures


async def test_chat_describes_only_first_successful_embedding(captures, tmp_path):
    old = tmp_path / "old.py"
    fresh = tmp_path / "fresh.py"
    old.write_text("OLD_BODY")
    alias = tmp_path / "alias.py"
    alias.symlink_to(old)
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Use these files")
    args = {"prompt": "inspect", "model": "gemini-2.5-flash", "working_directory_absolute_path": str(tmp_path)}
    first = await call_mcp_tool(
        "chat",
        {
            **args,
            "absolute_file_paths": list(map(str, (prompt_file, old, fresh))),
            "file_descriptions": {
                str(old): "first-label",
                str(fresh): "unavailable-label",
                str(prompt_file): "prompt-label",
            },
        },
    )
    assert not first.is_error
    assert " [first-label] ---" in captures[0]["prompt"]
    assert "unavailable-label" not in captures[0]["prompt"] and "prompt-label" not in captures[0]["prompt"]
    thread = json.loads(first.content[0].text)["continuation_offer"]["continuation_id"]
    saved = memory.get_thread(thread)
    assert "file_descriptions" not in saved.initial_context
    assert saved.turns[-1].model_metadata["included_files"] == [str(old)]
    fresh.write_text("FRESH_BODY")
    result = await call_mcp_tool(
        "chat",
        {
            **args,
            "continuation_id": thread,
            "absolute_file_paths": [str(fresh), str(alias)],
            "file_descriptions": {str(tmp_path): "fresh-label", str(alias): "repeat-label"},
        },
    )
    assert not result.is_error
    coverage = json.loads(result.content[0].text)["metadata"]["file_context"]
    assert (coverage["files_in_history"], coverage["files_embedded"]) == (2, 0)
    prompt = captures[1]["prompt"]
    assert prompt.count("OLD_BODY") == prompt.count("FRESH_BODY") == prompt.count(" [fresh-label] ---") == 1
    assert prompt.index("FRESH_BODY") < prompt.index("OLD_BODY")
    assert "first-label" not in prompt and "repeat-label" not in prompt
    saved = memory.get_thread(thread)
    assert saved.turns[-1].model_metadata["included_files"] == [str(fresh), str(old)]
    assert all(
        label not in saved.model_dump_json() for label in ("first-label", "repeat-label", "fresh-label", "prompt-label")
    )


@pytest.mark.parametrize(
    ("prior_tool", "model", "metadata", "eligible"),
    [
        ("chat", "older-model", None, False),
        ("chat", "older-model", {"included_files": []}, True),
        ("codereview", None, {"work_history": []}, True),
        ("clink", "cli-model", {}, True),
        ("thinkdeep", "expert", {"included_files": ["CURRENT_FILE"]}, False),
    ],
)
async def test_chat_checks_committed_parent_evidence(captures, tmp_path, prior_tool, model, metadata, eligible):
    path = tmp_path / "return.py"
    path.write_text("RETURNING_BODY")
    parent = memory.create_thread(prior_tool, {})
    if metadata and metadata.get("included_files"):
        metadata = {"included_files": [str(path)]}
    memory.add_turn(
        parent,
        "assistant",
        "previous",
        tool_name=prior_tool,
        model_name=model,
        files=[str(path)],
        model_metadata=metadata,
    )
    child = memory.create_thread("chat", {}, parent_thread_id=parent)
    await server.handle_call_tool(
        "chat",
        {
            "model": "gemini-2.5-flash",
            "prompt": "inspect",
            "continuation_id": child,
            "working_directory_absolute_path": str(tmp_path),
            "absolute_file_paths": [str(path)],
            "file_descriptions": {str(path): "returning-label"},
        },
    )
    assert captures[0]["prompt"].count("RETURNING_BODY") == 1
    assert (" [returning-label] ---" in captures[0]["prompt"]) == eligible


async def test_continued_chat_annotation_can_exceed_file_allowance(captures, tmp_path, monkeypatch):
    from utils.file_utils import read_file_content
    from utils.model_context import ModelContext

    path = str(tmp_path / "file.py")
    Path(path).write_text("JUST_FITS")
    estimator = model_context()
    allowance = estimator.estimate_tokens(read_file_content(path)[0])
    budget = replace(estimator.file_budget(), limit=allowance)
    monkeypatch.setattr(ModelContext, "file_budget", lambda self, *args, **kwargs: budget)
    args = {
        "model": "gemini-2.5-flash",
        "prompt": "inspect",
        "working_directory_absolute_path": str(tmp_path),
        "absolute_file_paths": [path],
    }
    for annotated in (False, True):
        thread = memory.create_thread("chat", {})
        memory.add_turn(thread, "user", "prior reference", files=[path])
        if annotated:
            with pytest.raises(ToolExecutionError) as exc:
                await server.handle_call_tool(
                    "chat", {**args, "continuation_id": thread, "file_descriptions": {path: "over budget"}}
                )
            assert json.loads(exc.value.payload)["status"] == "code_too_large"
            assert memory.get_thread(thread).turns[-1].role == "user"
        else:
            await server.handle_call_tool("chat", {**args, "continuation_id": thread})
    assert len(captures) == 1


async def test_failed_expert_reply_leaves_file_eligible_for_chat(captures, tmp_path, monkeypatch):
    path = str(tmp_path / "retry.py")
    Path(path).write_text("RETRY_BODY")
    args = {
        "model": "gemini-2.5-flash",
        "step": "review",
        "findings": "initial",
        "step_number": 1,
        "total_steps": 1,
        "next_step_required": False,
        "relevant_files": [path],
    }
    with monkeypatch.context() as failed:
        calls = capture_providers(failed, google_answer='{"status":"analysis_error","error":"failed"}')
        with pytest.raises(ToolExecutionError) as exc:
            await server.handle_call_tool("thinkdeep", args)
        assert len(calls) == 1
    thread = json.loads(exc.value.payload)["continuation_id"]
    assert memory.get_thread(thread).turns[-1].model_metadata["included_files"] == []
    await server.handle_call_tool(
        "chat",
        {
            "model": args["model"],
            "prompt": "retry",
            "continuation_id": thread,
            "working_directory_absolute_path": str(tmp_path),
            "absolute_file_paths": [path],
            "file_descriptions": {path: "first committed"},
        },
    )
    assert " [first committed] ---" in captures[0]["prompt"]
