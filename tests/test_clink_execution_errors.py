"""CLI diagnostics must remain errors through parsing, execution, and persistence."""

import json
from unittest.mock import Mock

import pytest

from clink.agents import AgentOutput
from clink.parsers.base import ParsedCLIResponse
from clink.parsers.claude import ClaudeJSONParser
from clink.parsers.gemini import GeminiJSONParser
from tests.test_clink_claude_agent import DummyProcess
from tests.test_conversation_storage_errors import FaultStorage
from tools.clink import CLinkTool
from tools.shared.exceptions import ToolExecutionError
from utils import conversation_memory as memory

PARSERS = {"gemini": GeminiJSONParser, "claude": ClaudeJSONParser}

FAILURES = [
    pytest.param("gemini", {"error": {"message": "Quota exhausted"}}, "", "Quota exhausted", id="gemini-error"),
    pytest.param(
        "gemini",
        {"response": "Partial answer", "error": {"code": "AUTH_FAILED", "message": "Log in again"}},
        "",
        "Log in again",
        id="gemini-partial-with-error",
    ),
    pytest.param("gemini", {"error": "Authentication failed"}, "", "Authentication failed", id="gemini-string-error"),
    pytest.param(
        "gemini",
        {"error": {"code": "UNSUPPORTED_CLIENT"}, "response": "Partial answer"},
        "",
        "UNSUPPORTED_CLIENT",
        id="gemini-unsupported-client",
    ),
    pytest.param("gemini", {"response": ""}, "429 quota exhausted", "429", id="gemini-rate-limit"),
    pytest.param(
        "gemini",
        {"response": "", "stats": {"models": {"gemini": {"api": {"totalErrors": 2, "totalRequests": 2}}}}},
        "",
        "2 error(s)",
        id="gemini-api-errors",
    ),
    pytest.param("gemini", {"response": ""}, "Diagnostic only", "no textual output", id="gemini-stderr-only"),
    pytest.param(
        "claude",
        {"type": "result", "subtype": "success", "is_error": True, "result": "API Error"},
        "",
        "API Error",
        id="claude-is-error",
    ),
    pytest.param(
        "claude",
        {"type": "result", "subtype": "error_during_execution", "is_error": False, "result": "Partial answer"},
        "",
        "Partial answer",
        id="claude-failed-subtype",
    ),
    pytest.param(
        "claude",
        {"type": "result", "subtype": "error_max_turns", "errors": ["Reached turn limit"]},
        "",
        "Reached turn limit",
        id="claude-errors-without-answer",
    ),
    pytest.param(
        "claude", {"type": "result", "subtype": "error_max_budget_usd"}, "", "error_max_budget_usd", id="claude-budget"
    ),
    pytest.param(
        "claude",
        {"error": {"message": "Authentication failed"}},
        "",
        "Authentication failed",
        id="claude-error-envelope",
    ),
    pytest.param(
        "claude",
        [
            {"type": "assistant", "message": "Partial answer"},
            {"type": "result", "subtype": "error_during_execution", "is_error": True},
        ],
        "Terminal diagnostic",
        "Partial answer",
        id="claude-array-failed-result",
    ),
    pytest.param("claude", {}, "Diagnostic only", "no textual result", id="claude-stderr-only"),
]


def test_parsed_response_defaults_to_success_for_existing_callers():
    assert ParsedCLIResponse("Answer", {}).is_error is False


@pytest.mark.parametrize("cli_name,payload,stderr,diagnostic", FAILURES)
def test_parser_classifies_failure_and_retains_diagnostics(cli_name, payload, stderr, diagnostic):
    parsed = PARSERS[cli_name]().parse(json.dumps(payload), stderr)

    assert parsed.is_error is True
    assert diagnostic in parsed.content
    assert parsed.metadata["raw"] == payload
    if stderr:
        assert parsed.metadata["stderr"] == stderr


@pytest.fixture
def execution(monkeypatch):
    storage = FaultStorage()
    monkeypatch.setattr(memory, "get_storage", lambda: storage)
    resolve_provider = Mock(side_effect=AssertionError("clink must not resolve an API provider"))
    monkeypatch.setattr("providers.registry.ModelProviderRegistry.get_provider_for_model", resolve_provider)
    monkeypatch.setattr("clink.agents.base.shutil.which", lambda name, **kwargs: f"/usr/bin/{name}")

    launches = []

    def configure_process(stdout, stderr="", returncode=0):
        process = DummyProcess(stdout=stdout.encode(), stderr=stderr.encode(), returncode=returncode)

        async def launch(*args, **kwargs):
            launches.append(args)
            return process

        monkeypatch.setattr("clink.agents.base.asyncio.create_subprocess_exec", launch)

    return storage, resolve_provider, launches, configure_process


@pytest.mark.asyncio
@pytest.mark.parametrize("cli_name,payload,stderr,diagnostic", FAILURES)
@pytest.mark.parametrize("continuation", [False, True], ids=["first-call", "follow-up"])
async def test_failed_cli_saves_no_assistant_turn(execution, cli_name, payload, stderr, diagnostic, continuation):
    storage, resolve_provider, launches, configure_process = execution
    configure_process(json.dumps(payload), stderr)
    arguments = {"cli_name": cli_name, "prompt": "Current input"}
    expected_turns = []
    if continuation:
        thread_id = memory.create_thread("clink", {"cli_name": cli_name})
        memory.add_turn(thread_id, "user", "Previous input")
        memory.add_turn(thread_id, "assistant", "Previous answer")
        arguments["continuation_id"] = thread_id
        expected_turns = [("user", "Previous input"), ("assistant", "Previous answer")]

    with pytest.raises(ToolExecutionError) as error:
        await CLinkTool().execute(arguments)

    output = json.loads(str(error.value))
    assert output["status"] == "error"
    assert diagnostic in output["content"]
    metadata = output["metadata"]
    assert metadata["cli_name"] == cli_name
    assert metadata["return_code"] == 0
    assert metadata["raw"] == payload
    assert metadata["stdout"] == json.dumps(payload)
    if stderr:
        assert metadata["stderr"] == stderr
    assert len(launches) == 1
    resolve_provider.assert_not_called()
    assert len(storage.data) == 1
    # Task 3 replaces eager admission. For now only the current user is saved.
    thread = memory.get_thread(metadata["continuation_id"])
    assert [(turn.role, turn.content) for turn in thread.turns] == expected_turns + [("user", "Current input")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cli_name,stdout,stderr,returncode,diagnostic",
    [
        pytest.param(
            "gemini",
            "",
            'Tool failed\n{"error":{"message":"Edit denied","code":"denied"}}',
            54,
            "Edit denied",
            id="gemini-recovered-error",
        ),
        pytest.param("gemini", "", "UNSUPPORTED_CLIENT", 41, "UNSUPPORTED_CLIENT", id="gemini-recovered-unsupported"),
        pytest.param("gemini", "", "UNSUPPORTED_CLIENT", 0, "UNSUPPORTED_CLIENT", id="gemini-empty-unsupported"),
        pytest.param(
            "claude",
            '{"type":"result","subtype":"success","result":"Readable answer"}',
            "Fatal diagnostic",
            2,
            "Readable answer",
            id="claude-recovered-readable-answer",
        ),
        pytest.param("claude", "", "Authentication failed", 1, "exited with status 1", id="unrecovered-exit"),
    ],
)
async def test_runner_recovery_does_not_turn_failure_into_success(
    execution, cli_name, stdout, stderr, returncode, diagnostic
):
    storage, resolve_provider, launches, configure_process = execution
    configure_process(stdout, stderr, returncode)

    with pytest.raises(ToolExecutionError) as error:
        await CLinkTool().execute({"cli_name": cli_name, "prompt": "Current input"})

    output = json.loads(str(error.value))
    assert output["status"] == "error"
    assert diagnostic in output["content"]
    assert output["metadata"]["return_code"] == returncode
    assert output["metadata"]["stderr"] == stderr
    assert len(launches) == 1
    assert len(storage.data) == 1
    thread = memory.get_thread(output["metadata"]["continuation_id"])
    assert [turn.role for turn in thread.turns] == ["user"]
    resolve_provider.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cli_name,payload,stderr",
    [
        pytest.param(
            "gemini",
            {"response": "Answer", "stats": {"models": {"gemini": {"api": {"totalErrors": 1}}}}},
            "Permission denied for optional subtool; previous attempt: UNSUPPORTED_CLIENT",
            id="gemini-warning",
        ),
        pytest.param(
            "claude",
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Answer",
                "permission_denials": [{"tool_name": "Edit", "tool_input": {"file_path": "/example"}}],
            },
            "Permission denied for optional subtool",
            id="claude-warning",
        ),
    ],
)
async def test_successful_answers_with_warnings_remain_successful(execution, cli_name, payload, stderr):
    _, resolve_provider, launches, configure_process = execution
    stdout = json.dumps(payload)
    parsed = PARSERS[cli_name]().parse(stdout, stderr)
    assert parsed.is_error is False
    configure_process(stdout, stderr)

    output = json.loads((await CLinkTool().execute({"cli_name": cli_name, "prompt": "Current input"}))[0].text)

    assert output["status"] == "continuation_available"
    assert output["content"] == "Answer"
    assert output["metadata"]["stderr"] == stderr
    thread = memory.get_thread(output["continuation_offer"]["continuation_id"])
    assert [(turn.role, turn.content) for turn in thread.turns] == [("user", "Current input"), ("assistant", "Answer")]
    assert len(launches) == 1
    resolve_provider.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("returncode,is_error", [(0, True), (2, False)])
async def test_failure_rejected_before_pruning_or_presentation(execution, monkeypatch, returncode, is_error):
    diagnostic = "Diagnostic " * 2500 + "<SUMMARY>Must not replace diagnostics</SUMMARY>"

    class Agent:
        async def run(self, **kwargs):
            return AgentOutput(
                parsed=ParsedCLIResponse(diagnostic, {"events": ["failure detail"]}, is_error=is_error),
                sanitized_command=["claude"],
                returncode=returncode,
                stdout="raw stdout",
                stderr="raw stderr",
                duration_seconds=0.1,
                parser_name="claude_json",
            )

    monkeypatch.setattr("tools.clink.create_agent", lambda _: Agent())
    with pytest.raises(ToolExecutionError) as error:
        await CLinkTool().execute({"cli_name": "claude", "prompt": "Current input"})

    output = json.loads(str(error.value))
    assert diagnostic in output["content"]
    assert output["metadata"]["events"] == ["failure detail"]
    assert "output_summarized" not in output["metadata"]
    thread = memory.get_thread(output["metadata"]["continuation_id"])
    assert [turn.role for turn in thread.turns] == ["user"]
