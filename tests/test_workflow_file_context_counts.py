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
from utils.file_utils import FileReadReport, read_file_content


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


@pytest.mark.parametrize(
    ("second_label", "expected_status"),
    [(None, "success"), ("second perspective", "success"), ("over budget " * 15, "error")],
    ids=["plain-second", "different-labels", "second-over-budget"],
)
async def test_consensus_never_charges_or_deduplicates_hidden_history(
    captures, tmp_path, monkeypatch, second_label, expected_status
):
    from utils.model_context import ModelContext

    def forbidden(*args):
        raise AssertionError("Blinded participant must not inspect generic history budget")

    monkeypatch.setattr("utils.conversation_context.remaining_input_budget", forbidden)
    attachment = tmp_path / "participant.py"
    attachment.write_text("PARTICIPANT_FILE")
    plain, _ = read_file_content(str(attachment), include_line_numbers=True)
    # Give the second participant room for the plain block and a short label.
    second_limit = model_context().estimate_tokens(plain) + 8
    file_budget = ModelContext.file_budget

    def participant_budget(context, remaining_tokens=None, reserve_tokens=1000):
        budget = file_budget(context, remaining_tokens, reserve_tokens)
        if context.model_name == "gpt-5":
            return replace(budget, limit=min(budget.limit, second_limit))
        return budget

    monkeypatch.setattr(ModelContext, "file_budget", participant_budget)
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
                    "file_descriptions": {str(tmp_path): "first perspective"},
                    "models": [{"model": "gemini-2.5-flash"}, {"model": "gpt-5"}],
                },
            )
        )[0].text
    )
    thread = first["continuation_offer"]["continuation_id"]
    second_labels = {"file_descriptions": {str(attachment): second_label}} if second_label is not None else {}
    second_result = await server.handle_call_tool(
        "consensus",
        {
            "continuation_id": thread,
            "step": "HIDDEN_SUMMARY " * 1000,
            "findings": "HIDDEN_VERDICT",
            "step_number": 2,
            "total_steps": 2,
            "next_step_required": False,
            **second_labels,
            "relevant_files": [str(attachment)],
        },
    )
    second = json.loads(second_result[0].text)
    first_model = first["model_response"]
    second_model = second["model_response"]
    assert first_model["status"] == "success"
    first_coverage = first_model["metadata"]["file_context"]
    assert first_coverage["budget"] > second_limit
    assert (first_coverage["files_embedded"], first_coverage["files_in_history"]) == (1, 0)
    assert captures[0]["prompt"].count(" [first perspective] ---") == 1
    if second_label:
        assert second_label not in captures[0]["prompt"]

    assert second_model["status"] == expected_status
    saved = memory.get_thread(thread)
    assert saved.turns[1].model_metadata["included_files"] == [str(attachment)]
    stored = saved.model_dump_json()
    assert "file_descriptions" not in stored and "first perspective" not in stored
    if second_label:
        assert second_label.strip() not in stored
    if expected_status == "error":
        assert len(captures) == 1
        assert second["complete_consensus"]["status"] == "partial"
        error = json.loads(second_model["error"])
        assert error["status"] == "code_too_large"
        assert error["metadata"]["limit"] == second_limit
        assert error["metadata"]["overage"] > 0
        return

    assert len(captures) == 2
    coverage = second_model["metadata"]["file_context"]
    assert (coverage["files_embedded"], coverage["files_in_history"], coverage["budget"]) == (1, 0, second_limit)
    for call in captures:
        assert "ORIGINAL_PROPOSAL" in call["prompt"]
        assert call["prompt"].count("PARTICIPANT_FILE") == 1
        assert "HIDDEN_" not in call["prompt"]
    expected = plain.replace(") ---\n", f") [{second_label}] ---\n", 1) if second_label else plain
    assert expected in captures[1]["prompt"]
    assert "first perspective" not in captures[1]["prompt"]

    # Synthesis uses the shared workflow executor and records no participant labels.
    await server.handle_call_tool(
        "consensus",
        {
            "continuation_id": thread,
            "step": "synthesize",
            "findings": "compare verdicts",
            "step_number": 3,
            "total_steps": 3,
            "next_step_required": False,
            "relevant_files": [str(attachment)],
            "file_descriptions": {str(attachment): "synthesis-only label"},
        },
    )
    assert len(captures) == 2
    saved = memory.get_thread(thread)
    assert saved.turns[-1].model_metadata["work_history"][-1]["step_number"] == 3
    assert "file_descriptions" not in saved.model_dump_json()


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


async def workflow_step(name, number, *, more=False, **kwargs):
    args = {
        "model": "gemini-2.5-flash",
        "step": f"Investigate step {number}",
        "findings": f"Evidence for step {number}",
        "step_number": number,
        "total_steps": number + int(more),
        "next_step_required": more,
        **kwargs,
    }
    return json.loads((await server.handle_call_tool(name, args))[0].text)


@pytest.mark.parametrize("annotated", [False, True], ids=["plain", "annotated"])
async def test_continued_debug_renders_one_essential_files_block(captures, tmp_path, annotated):
    source = tmp_path / "a.py"
    reference = tmp_path / "b.py"
    source.write_text("start()\nstop()")
    reference.write_text("helper()")
    labels = {"file_descriptions": {str(source): "suspected cause"}} if annotated else {}
    first = await workflow_step("debug", 1, more=True, relevant_files=[str(reference), str(source)], **labels)
    assert not captures
    thread = first["continuation_id"]
    second = await workflow_step("debug", 2, continuation_id=thread)

    assert len(captures) == 1
    prompt = captures[0]["prompt"]
    source_block, _ = read_file_content(str(source), include_line_numbers=True)
    if annotated:
        source_block = source_block.replace(") ---\n", ") [suspected cause] ---\n", 1)
    reference_block, _ = read_file_content(str(reference), include_line_numbers=True)
    assert (
        f"=== ESSENTIAL FILES FOR DEBUGGING ===\n{source_block}\n\n{reference_block}\n=== END ESSENTIAL FILES ==="
        in prompt
    )
    assert prompt.count("=== ESSENTIAL FILES FOR DEBUGGING ===") == 1
    assert prompt.count("--- BEGIN FILE:") == 2
    assert second["file_context"]["files_embedded"] == 2
    assert second["file_context"]["files_in_history"] == 0
    assert memory.get_thread(thread).turns[-1].model_metadata["included_files"] == [str(source), str(reference)]


async def test_workflow_labels_follow_their_step_and_freeze_on_expert_delivery(captures, tmp_path, monkeypatch):
    monkeypatch.setenv("PAL_TRANSCRIPTS", "true")
    monkeypatch.setenv("PAL_STATE_DIR", str(tmp_path / "state"))
    inherited, direct, historical = (tmp_path / name for name in ("a.py", "b.py", "c.py"))
    alias = tmp_path / "alias.py"
    for path in (inherited, direct, historical):
        path.write_text(f"body of {path.name}")
    alias.symlink_to(direct)
    long_label = "a" * 201 + "TRUNCATED_SUFFIX"
    effective = "a" * 199 + "…"
    first = await workflow_step(
        "thinkdeep",
        1,
        more=True,
        relevant_files=[str(inherited), str(direct)],
        file_descriptions={str(tmp_path): long_label, str(direct): "first", str(historical): "UNMATCHED"},
    )
    thread = first["continuation_id"]
    first_state = memory.get_thread(thread)
    assert "file_descriptions" not in first_state.initial_context
    assert first_state.turns[-1].model_metadata["work_history"][0]["file_descriptions"] == {
        str(inherited): effective,
        str(direct): "first",
    }
    # Another tool supplies a sibling by reference between the workflow's own steps.
    await workflow_step("codereview", 1, more=True, continuation_id=thread, relevant_files=[str(historical)])
    await workflow_step(
        "thinkdeep",
        2,
        more=True,
        continuation_id=thread,
        relevant_files=[str(alias)],
        file_descriptions={str(alias): "second"},
    )
    assert not captures
    third = await workflow_step(
        "thinkdeep", 3, continuation_id=thread, relevant_files=[str(direct)], file_descriptions={str(direct): "third"}
    )
    assert third["file_context"]["files_embedded"] == 3
    await workflow_step(
        "thinkdeep",
        4,
        continuation_id=thread,
        relevant_files=[str(alias)],
        file_descriptions={str(alias): "AFTER_DELIVERY"},
    )
    for call in captures:
        prompt = call["prompt"]
        headers = [line for line in prompt.splitlines() if line.startswith("--- BEGIN FILE:")]
        assert len(headers) == 3
        by_file = {
            path: next(line for line in headers if str(path) in line) for path in (inherited, direct, historical)
        }
        assert by_file[inherited].endswith(f"[{effective}] ---")
        assert by_file[direct].endswith("[third] ---") and by_file[historical].endswith(") ---")
        assert all(prompt.count(f"body of {path.name}") == 1 for path in by_file)
        assert " [first] ---" not in prompt and " [second] ---" not in prompt
    assert len(captures) == 2
    saved = memory.get_thread(thread)
    state = saved.turns[-1].model_metadata
    assert set(state["included_files"]) == set(map(str, (inherited, direct, historical)))
    assert [step.get("file_descriptions", {}).get(str(direct)) for step in state["work_history"]] == [
        "first",
        "second",
        "third",
        None,
    ]
    transcript = (tmp_path / "state" / "transcripts" / f"{thread}.jsonl").read_text()
    records = [json.loads(line) for line in transcript.splitlines()]
    assert records[-1]["model_metadata"]["work_history"] == state["work_history"]
    for text in (transcript, saved.model_dump_json(), *(call["prompt"] for call in captures)):
        assert all(secret not in text for secret in ("TRUNCATED_SUFFIX", "UNMATCHED", "AFTER_DELIVERY"))
    # Neither a different expert nor a restarted workflow reuses the earlier labels.
    await workflow_step("codereview", 2, continuation_id=thread, relevant_files=[str(direct)])
    await workflow_step("thinkdeep", 1, continuation_id=thread, relevant_files=[str(direct)])
    assert len(captures) == 4
    for call in captures[-2:]:
        assert "body of b.py" in call["prompt"]
        assert "[third]" not in call["prompt"] and effective not in call["prompt"]


async def test_workflow_replays_old_step_and_preserves_labels_after_provider_failure(captures, tmp_path, monkeypatch):
    path = str(tmp_path / "source.py")
    Path(path).write_text("RETRY_FILE")
    first = await workflow_step("thinkdeep", 1, more=True, relevant_files=[path])
    thread = first["continuation_id"]
    assert "file_descriptions" not in memory.get_thread(thread).turns[-1].model_metadata["work_history"][0]

    async def failed_provider(*args, **kwargs):
        raise RuntimeError("synthetic provider failure")

    with monkeypatch.context() as patch:
        patch.setattr(GeminiModelProvider, "generate_content", failed_provider)
        with pytest.raises(ToolExecutionError):
            await workflow_step(
                "thinkdeep",
                2,
                continuation_id=thread,
                relevant_files=[path],
                file_descriptions={path: " retry\n] --- ", "relative": "DROP_ME"},
            )
    failed = memory.get_thread(thread).turns[-1]
    assert failed.role == "user"
    assert "included_files" not in failed.model_metadata
    assert failed.model_metadata["work_history"][-1]["file_descriptions"] == {path: "retry ) —"}
    assert "DROP_ME" not in failed.model_dump_json()
    await workflow_step("thinkdeep", 3, continuation_id=thread, relevant_files=[path], file_descriptions={path: "  "})
    assert len(captures) == 1 and " [retry ) —] ---" in captures[0]["prompt"]


@pytest.mark.parametrize("tool", ["analyze", "codereview", "precommit", "refactor", "secaudit", "testgen", "thinkdeep"])
async def test_generic_expert_receives_annotated_attachment(captures, tmp_path, tool):
    path = str(tmp_path / "focus.py")
    Path(path).write_text("FOCUS_BODY")
    options = {"path": str(tmp_path)} if tool == "precommit" else {}
    first = await workflow_step(tool, 1, more=True, relevant_files=[path], file_descriptions={path: "focus"}, **options)
    assert not captures
    await workflow_step(tool, 2, continuation_id=first["continuation_id"])
    assert len(captures) == 1
    assert captures[0]["prompt"].count("FOCUS_BODY") == captures[0]["prompt"].count(" [focus] ---") == 1


async def test_workflow_drops_pending_label_if_chat_delivered_first(captures, tmp_path):
    first_path, second_path = (str(tmp_path / name) for name in ("first.py", "second.py"))
    for path in (first_path, second_path):
        Path(path).write_text("body")
    first = await workflow_step(
        "thinkdeep",
        1,
        more=True,
        relevant_files=[first_path, second_path],
        file_descriptions={first_path: "pending", second_path: "settled"},
    )
    thread = first["continuation_id"]
    # Chat renders the whole available history. The missing sibling can first reach the expert later.
    Path(second_path).unlink()
    chat = {
        "prompt": "inspect",
        "model": "gemini-2.5-flash",
        "working_directory_absolute_path": str(tmp_path),
        "continuation_id": thread,
        "absolute_file_paths": [first_path],
    }
    await server.handle_call_tool("chat", chat)
    assert memory.get_thread(thread).turns[-1].model_metadata["included_files"] == [first_path]
    Path(second_path).write_text("restored")
    await workflow_step("thinkdeep", 2, continuation_id=thread)
    await server.handle_call_tool("chat", chat)
    await workflow_step(
        "thinkdeep",
        3,
        continuation_id=thread,
        relevant_files=[second_path],
        file_descriptions={second_path: "too late"},
    )
    assert len(captures) == 4
    for call in (captures[1], captures[3]):
        header = next(line for line in call["prompt"].splitlines() if line.startswith(f"--- BEGIN FILE: {first_path} "))
        assert header.endswith(") ---")
        assert " [pending]" not in call["prompt"] and " [too late]" not in call["prompt"]
        assert call["prompt"].count(" [settled] ---") == 1


@pytest.mark.parametrize("directory_reply", [True, False])
async def test_legacy_reply_only_covers_its_recorded_files(captures, tmp_path, directory_reply):
    old, new = (str(tmp_path / name) for name in ("old.py", "new.py"))
    Path(old).touch()
    thread = memory.create_thread("thinkdeep", {})
    memory.add_turn(
        thread, "user", "inspect directory", files=[old] if directory_reply else [old, new], tool_name="thinkdeep"
    )
    memory.add_turn(
        thread,
        "assistant",
        "done",
        model_name="older-model",
        files=[str(tmp_path) if directory_reply else old],
        tool_name="thinkdeep",
    )
    Path(new).touch()
    await server.handle_call_tool(
        "chat",
        {
            "model": "gemini-2.5-flash",
            "prompt": "continue",
            "continuation_id": thread,
            "working_directory_absolute_path": str(tmp_path),
            "absolute_file_paths": [new, old],
            "file_descriptions": {new: "new child", old: "already present"},
        },
    )
    assert " [new child] ---" in captures[0]["prompt"] and "already present" not in captures[0]["prompt"]


async def test_consumed_prompt_file_alone_preserves_plain_attachment(captures, tmp_path):
    path = str(tmp_path / "prompt.txt")
    Path(path).write_text("PROMPT_BODY")
    await server.handle_call_tool(
        "chat",
        {
            "model": "gemini-2.5-flash",
            "prompt": "inspect",
            "working_directory_absolute_path": str(tmp_path),
            "absolute_file_paths": [path],
            "file_descriptions": {path: "consumed"},
        },
    )
    assert " [consumed] ---" not in captures[0]["prompt"]
    assert f"--- BEGIN FILE: {path} " in captures[0]["prompt"]


@pytest.mark.parametrize("continued", [False, True], ids=["initial", "continued"])
async def test_explicit_prompt_reintroduced_by_directory_stays_plain(captures, tmp_path, continued):
    folder = tmp_path / "attachments"
    folder.mkdir()
    prompt_file = folder / "prompt.txt"
    sibling = folder / "reference.py"
    prompt_file.write_text("Review this reference")
    sibling.write_text("reference_body")
    args = {"prompt": "inspect", "model": "gemini-2.5-flash", "working_directory_absolute_path": str(tmp_path)}
    if continued:
        initial = await server.handle_call_tool("chat", args)
        args["continuation_id"] = json.loads(initial[0].text)["continuation_offer"]["continuation_id"]
    await server.handle_call_tool(
        "chat",
        {
            **args,
            "absolute_file_paths": [str(prompt_file), str(folder)],
            "file_descriptions": {str(folder): "reference", str(prompt_file): "consumed"},
        },
    )
    headers = [line for line in captures[-1]["prompt"].splitlines() if line.startswith("--- BEGIN FILE:")]
    assert len(headers) == 2
    assert any(str(prompt_file) in line and line.endswith(") ---") for line in headers)
    assert any(str(sibling) in line and line.endswith(" [reference] ---") for line in headers)


async def test_legacy_file_alias_keeps_original_delivery_after_retarget(captures, tmp_path):
    old = tmp_path / "old.py"
    new = tmp_path / "new.py"
    alias = tmp_path / "alias.py"
    old.touch()
    new.touch()
    alias.symlink_to(old)
    thread = memory.create_thread("thinkdeep", {})
    memory.add_turn(thread, "user", "inspect file", files=[str(alias.resolve())], tool_name="thinkdeep")
    memory.add_turn(thread, "assistant", "done", model_name="older-model", files=[str(alias)], tool_name="thinkdeep")
    alias.unlink()
    alias.symlink_to(new)
    await server.handle_call_tool(
        "chat",
        {
            "model": "gemini-2.5-flash",
            "prompt": "continue",
            "continuation_id": thread,
            "working_directory_absolute_path": str(tmp_path),
            "absolute_file_paths": [str(old), str(alias)],
            "file_descriptions": {str(old): "already delivered", str(alias): "first delivery"},
        },
    )
    headers = [line for line in captures[0]["prompt"].splitlines() if line.startswith("--- BEGIN FILE:")]
    assert any(str(new) in line and line.endswith(" [first delivery] ---") for line in headers)
    assert any(str(old) in line and line.endswith(") ---") for line in headers)
