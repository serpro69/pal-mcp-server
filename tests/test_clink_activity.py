"""Observable CLI receipts survive presentation without retaining raw payloads."""

import json

import pytest

from clink.agents import AgentOutput
from clink.parsers.claude import ClaudeJSONParser
from clink.parsers.codex import (
    MAX_ACTIVITY_CHARS,
    MAX_ACTIVITY_COMMAND_CHARS,
    MAX_ACTIVITY_COMMANDS,
    CodexJSONLParser,
)
from clink.parsers.gemini import GeminiJSONParser
from tests.test_clink_execution_errors import execution as execution
from tools.clink import MAX_METADATA_CHARS, MAX_RESPONSE_CHARS, CLinkTool
from utils import conversation_memory as memory


def command_event(index, *, command=None, exit_code=0, status="completed", event_type="item.completed"):
    return {
        "type": event_type,
        "item": {
            "id": f"cmd-{index}",
            "type": "command_execution",
            "command": f"cat file-{index}.py" if command is None else command,
            "exit_code": exit_code,
            "status": status,
            "aggregated_output": "PRIVATE_OUTPUT_MARKER" * 1000,
        },
    }


def codex_stream(events, answer="Review complete"):
    return "\n".join(
        json.dumps(event)
        for event in [
            *events,
            {"type": "item.completed", "item": {"type": "agent_message", "text": answer}},
            {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}},
        ]
    )


def test_codex_activity_counts_completed_items_once_and_excludes_output():
    events = [
        command_event(0, event_type="item.started", status="in_progress", exit_code=None),
        command_event(0, event_type="item.updated", status="in_progress", exit_code=None),
        command_event(0),
        command_event(0),
        command_event(1, exit_code=1, status="failed"),
        command_event(2, exit_code=None, status="declined"),
        command_event(3, event_type="item.started", status="in_progress", exit_code=None),
    ]
    activity = CodexJSONLParser().parse(codex_stream(events), "").metadata["observed_activity"]
    assert activity == {
        "source": "codex_jsonl",
        "commands": [
            {"command": "cat file-0.py", "exit_code": 0, "status": "completed"},
            {"command": "cat file-1.py", "exit_code": 1, "status": "failed"},
            {"command": "cat file-2.py", "exit_code": None, "status": "declined"},
        ],
        "command_count": 3,
        "failed_count": 2,
        "denied_count": 1,
        "truncated": False,
    }
    assert "PRIVATE_OUTPUT_MARKER" not in json.dumps(activity)


def test_codex_activity_samples_first_and_last_commands_but_counts_all_failures():
    events = [command_event(i, exit_code=1 if i == 30 else 0) for i in range(50)]
    activity = CodexJSONLParser().parse(codex_stream(events), "").metadata["observed_activity"]
    assert [entry["command"] for entry in activity["commands"]] == [
        f"cat file-{i}.py" for i in [*range(10), *range(40, 50)]
    ]
    assert activity["command_count"] == 50
    assert activity["failed_count"] == 1
    assert activity["truncated"] is True


@pytest.mark.parametrize("text", ["x", "\x00", "\U0001f600"])
def test_codex_activity_bounds_entry_strings_and_serialized_size(text):
    events = [command_event(i, command=text * 1000) for i in range(30)]
    activity = CodexJSONLParser().parse(codex_stream(events), "").metadata["observed_activity"]
    assert 0 < len(activity["commands"]) <= MAX_ACTIVITY_COMMANDS
    assert all(len(entry["command"]) <= MAX_ACTIVITY_COMMAND_CHARS for entry in activity["commands"])
    assert all(entry["command_truncated"] for entry in activity["commands"])
    assert len(json.dumps(activity)) <= MAX_ACTIVITY_CHARS
    assert activity["command_count"] == 30
    assert activity["truncated"] is True


def test_codex_activity_does_not_invent_commands_from_answer_or_native_summary():
    events = [{"type": "info", "observed_activity": {"commands": ["cat invented.py"]}}]
    activity = CodexJSONLParser().parse(codex_stream(events, "I read invented.py"), "").metadata["observed_activity"]
    assert activity["commands"] == []
    assert activity["command_count"] == 0
    assert activity["truncated"] is False


def test_codex_activity_keeps_unknown_fields_unknown():
    event = command_event(0, exit_code=True, status={"untrusted": "value"})
    event["item"]["command"] = {"untrusted": "value"}
    activity = CodexJSONLParser().parse(codex_stream([event]), "").metadata["observed_activity"]
    assert activity["commands"] == [{"command": None, "exit_code": None, "status": "unknown"}]
    assert activity["failed_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("presentation", ["normal", "summary", "truncated"])
async def test_codex_success_returns_activity_through_all_presentations(execution, presentation):
    _, _, _, configure = execution
    answer = "Review complete"
    if presentation != "normal":
        answer = "x" * (MAX_RESPONSE_CHARS + 1)
    if presentation == "summary":
        answer += "<SUMMARY>Review complete</SUMMARY>"
    configure(codex_stream([command_event(0), command_event(1, exit_code=1, status="failed")], answer))
    response = (await CLinkTool().execute({"cli_name": "codex", "prompt": "Review files"}))[0].text
    payload = json.loads(response)
    metadata = payload["metadata"]
    assert metadata["observed_activity"]["command_count"] == 2
    assert metadata["observed_activity"]["failed_count"] == 1
    assert metadata["usage"] == {"input_tokens": 100, "output_tokens": 20}
    assert metadata["events_removed_for_normal"] is True
    assert metadata["history_saved"] is True
    assert not {"events", "raw_events", "raw", "raw_output_file"} & metadata.keys()
    assert "PRIVATE_OUTPUT_MARKER" not in response
    assert len(payload["content"]) <= MAX_RESPONSE_CHARS
    if presentation == "summary":
        assert metadata["output_summarized"] is True
    elif presentation == "truncated":
        assert metadata["output_truncated"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("cli_name", ["claude", "gemini"])
@pytest.mark.parametrize("presentation", ["normal", "summary", "truncated"])
async def test_success_removes_every_raw_copy_and_omits_unsupported_activity(monkeypatch, cli_name, presentation):
    answer = "OK"
    if presentation != "normal":
        answer = "x" * (MAX_RESPONSE_CHARS + 1)
    if presentation == "summary":
        answer += "<SUMMARY>OK</SUMMARY>"
    raw = (
        [{"type": "assistant", "message": "RAW_MARKER" * 10000}, {"type": "result", "result": answer}]
        if cli_name == "claude"
        else {"response": answer, "extra": "RAW_MARKER" * 10000}
    )
    parser = ClaudeJSONParser() if cli_name == "claude" else GeminiJSONParser()
    stdout = json.dumps(raw)

    class FakeAgent:
        async def run(self, **kwargs):
            return AgentOutput(
                parsed=parser.parse(stdout, ""),
                sanitized_command=[cli_name],
                returncode=0,
                stdout=stdout,
                stderr="",
                duration_seconds=0.1,
                parser_name=parser.name,
                output_file_content=stdout,
            )

    monkeypatch.setattr("tools.clink.create_agent", lambda client: FakeAgent())
    response = (await CLinkTool().execute({"cli_name": cli_name, "prompt": "Review"}))[0].text
    metadata = json.loads(response)["metadata"]
    assert not {"events", "raw_events", "raw", "raw_output_file", "observed_activity"} & metadata.keys()
    assert "RAW_MARKER" not in response
    assert len(json.dumps(metadata)) <= MAX_METADATA_CHARS


def test_success_metadata_budget_keeps_activity_and_reports_omitted_diagnostics():
    activity = CodexJSONLParser().parse(codex_stream([command_event(0)]), "").metadata["observed_activity"]
    metadata = {"observed_activity": activity, "stderr": "LARGE_DIAGNOSTIC" * 10000, "return_code": 0}
    bounded = CLinkTool._bound_success_metadata(metadata)
    assert len(json.dumps(bounded)) <= MAX_METADATA_CHARS
    assert bounded["observed_activity"] == activity
    assert bounded["return_code"] == 0
    assert bounded["metadata_truncated"] is True
    assert bounded["metadata_omitted_keys"] == ["stderr"]
    assert "stderr" in metadata  # Presentation does not mutate parser diagnostics.


def test_success_metadata_budget_counts_unicode_like_the_response_serializer():
    metadata = {"stderr": "Permission notice: " + "雪" * 2000, "return_code": 0}
    assert CLinkTool._bound_success_metadata(metadata) == metadata


def test_success_metadata_budget_preserves_exact_directory_selection():
    selection = {
        "mode": "directory",
        "source": "request",
        "launch_path": "/" + "long-project/" * 300,
        "canonical_path": "/" + "long-project/" * 300,
    }
    activity = (
        CodexJSONLParser()
        .parse(codex_stream([command_event(i, command="x" * 250) for i in range(20)]), "")
        .metadata["observed_activity"]
    )
    metadata = {"working_directory": selection, "observed_activity": activity, "stderr": "x" * 10000}
    bounded = CLinkTool._bound_success_metadata(metadata)
    assert bounded.pop("working_directory") == selection
    assert len(json.dumps(bounded)) <= MAX_METADATA_CHARS
    assert bounded["observed_activity"] == activity
    assert bounded["metadata_omitted_keys"] == ["stderr"]


@pytest.mark.asyncio
@pytest.mark.parametrize("save_fails", [False, True])
async def test_execute_bounds_diagnostics_without_losing_receipts_or_persistence_state(
    execution, monkeypatch, save_fails
):
    _, _, _, configure = execution
    configure(codex_stream([command_event(i, command="x" * 250) for i in range(20)]), "STDERR_MARKER" * 10000)
    if save_fails:

        def fail_commit(*args, **kwargs):
            raise memory.ConversationMemoryError("Synthetic persistence failure")

        monkeypatch.setattr("clink.conversation.CLIConversation.commit", fail_commit)
    response = json.loads((await CLinkTool().execute({"cli_name": "codex", "prompt": "Review"}))[0].text)
    metadata = response["metadata"]
    assert metadata["observed_activity"]["command_count"] == 20
    assert metadata["metadata_omitted_keys"] == ["stderr"]
    assert len(json.dumps(metadata)) <= MAX_METADATA_CHARS
    assert metadata["working_directory"]["mode"] == "scratch"
    assert metadata["tool_name"] == "clink" and metadata["provider_used"] == "codex"
    assert "history" in metadata
    assert metadata["history_saved"] is not save_fails
    assert metadata["conversation_ready"] is not save_fails
    if save_fails:
        assert "memory_persistence_warning" in metadata
        assert response["continuation_offer"] is None
    else:
        assert response["continuation_offer"]["continuation_id"]


def test_pruning_removes_output_file_copy():
    tool = CLinkTool()
    metadata = {"raw_output_file": "PRIVATE_OUTPUT_MARKER", "duration_seconds": 0.1}
    cleaned = tool._prune_metadata(metadata, tool._registry.get_client("codex"), reason="normal")
    assert cleaned == {"raw_output_file_removed_for_normal": True, "duration_seconds": 0.1}
