"""CLI diagnostics must remain errors through parsing, execution, and persistence."""

import asyncio
import errno
import json
import sys
from asyncio import create_subprocess_exec as native_spawn
from pathlib import Path
from unittest.mock import Mock

import pytest

from clink.agents import AgentOutput
from clink.parsers.base import ParsedCLIResponse, ParserError
from clink.parsers.claude import ClaudeJSONParser
from clink.parsers.codex import CodexJSONLParser
from clink.parsers.gemini import GeminiJSONParser
from tests.test_clink_claude_agent import DummyProcess
from tests.test_clink_environment import ControlledProcess
from tools.clink import CLinkTool
from tools.shared.exceptions import ToolExecutionError
from utils import conversation_coordination as coordination
from utils import conversation_memory as memory
from utils.storage_backend import InMemoryStorage

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
    monkeypatch.setattr(InMemoryStorage, "_cleanup_worker", lambda self: None)
    storage = InMemoryStorage()
    monkeypatch.setattr(memory, "get_storage", lambda: storage)
    resolve_provider = Mock(side_effect=AssertionError("clink must not resolve an API provider"))
    monkeypatch.setattr("providers.registry.ModelProviderRegistry.get_provider_for_model", resolve_provider)
    monkeypatch.setattr("clink.availability.shutil.which", lambda name, **kwargs: f"/usr/bin/{name}")

    launches = []

    def configure_process(stdout, stderr="", returncode=0):
        process = DummyProcess(stdout=stdout.encode(), stderr=stderr.encode(), returncode=returncode)

        async def launch(*args, **kwargs):
            launches.append(args)
            return process

        monkeypatch.setattr("clink.agents.base.asyncio.create_subprocess_exec", launch)

    yield storage, resolve_provider, launches, configure_process
    storage.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("cli_name,payload,stderr,diagnostic", FAILURES)
@pytest.mark.parametrize("continuation", [False, True], ids=["first-call", "follow-up"])
async def test_failed_cli_saves_no_assistant_turn(execution, cli_name, payload, stderr, diagnostic, continuation):
    storage, resolve_provider, launches, configure_process = execution
    configure_process(json.dumps(payload), stderr)
    arguments = {"cli_name": cli_name, "prompt": "Current input"}
    if continuation:
        thread_id = memory.create_thread("clink", {"cli_name": cli_name})
        memory.add_turn(thread_id, "user", "Previous input")
        memory.add_turn(thread_id, "assistant", "Previous answer")
        arguments["continuation_id"] = thread_id
    snapshot = storage._store.copy()

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
    assert storage._store == snapshot  # Includes exact serialized content and TTL.
    assert "continuation_id" not in metadata
    assert output["continuation_offer"] is None


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
    assert not storage._store
    assert "continuation_id" not in output["metadata"]
    assert output["continuation_offer"] is None
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
    assert "continuation_id" not in output["metadata"]
    assert output["continuation_offer"] is None
    assert not execution[0]._store


CODEX_ANSWER = {"type": "item.completed", "item": {"type": "agent_message", "text": "Codex answer"}}
CODEX_COMPLETION = {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}}
CODEX_FAILURE = {"type": "turn.failed", "error": {"message": "Quota exhausted"}}
CODEX_RESULTS = [
    pytest.param(
        [{"type": "error", "message": "Authentication failed"}], True, "Authentication failed", id="error-only"
    ),
    pytest.param([{"type": "error"}], True, "Codex CLI reported an error.", id="error-without-message"),
    pytest.param([CODEX_FAILURE], True, "Quota exhausted", id="failed-turn-only"),
    pytest.param([CODEX_ANSWER, CODEX_FAILURE], True, "Codex answer\n\nQuota exhausted", id="partial-failed-turn"),
    pytest.param(
        [CODEX_ANSWER, {"type": "turn.failed"}],
        True,
        "Codex answer\n\nCodex CLI reported a failed turn.",
        id="failed-turn-without-message",
    ),
    pytest.param(
        [CODEX_ANSWER, CODEX_FAILURE, CODEX_COMPLETION],
        True,
        "Codex answer\n\nQuota exhausted",
        id="completion-does-not-hide-failed-turn",
    ),
    pytest.param(
        [{"type": "error", "message": "Transient request failure"}, CODEX_ANSWER, CODEX_COMPLETION],
        False,
        "Codex answer",
        id="recovered-warning",
    ),
    pytest.param(
        [
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "exit_code": 1,
                    "aggregated_output": "Permission denied",
                    "status": "failed",
                },
            },
            CODEX_ANSWER,
            CODEX_COMPLETION,
        ],
        False,
        "Codex answer",
        id="permission-warning",
    ),
    pytest.param([CODEX_ANSWER, CODEX_COMPLETION], False, "Codex answer", id="successful-answer"),
]


@pytest.mark.parametrize("events,is_error,content", CODEX_RESULTS)
def test_codex_parser_distinguishes_terminal_failures_from_warnings(events, is_error, content):
    stdout = "\n".join(json.dumps(event) for event in events)
    parsed = CodexJSONLParser().parse(stdout, "CLI diagnostic")

    assert parsed.is_error is is_error
    assert parsed.content == content
    assert parsed.metadata["events"] == events
    assert parsed.metadata["stderr"] == "CLI diagnostic"
    if events[-1]["type"] == "turn.completed":
        assert parsed.metadata["usage"] == {"input_tokens": 10, "output_tokens": 5}
    if events[0]["type"] == "error" and not is_error:
        assert parsed.metadata["errors"] == ["Transient request failure"]


@pytest.mark.asyncio
@pytest.mark.parametrize("events,is_error,content", CODEX_RESULTS)
@pytest.mark.parametrize("returncode", [0, 124], ids=["zero-exit", "nonzero-recovered"])
@pytest.mark.parametrize("continuation", [False, True], ids=["first-call", "follow-up"])
async def test_codex_execution_classification_controls_assistant_persistence(
    execution, events, is_error, content, returncode, continuation
):
    storage, resolve_provider, launches, configure_process = execution
    stdout = "\n".join(json.dumps(event) for event in events)
    configure_process(stdout, "CLI diagnostic", returncode)
    arguments = {"cli_name": "codex", "prompt": "Current input"}
    expected_turns = []
    if continuation:
        thread_id = memory.create_thread("clink", {"cli_name": "codex"})
        memory.add_turn(thread_id, "user", "Previous input")
        memory.add_turn(thread_id, "assistant", "Previous answer")
        arguments["continuation_id"] = thread_id
        expected_turns = [("user", "Previous input"), ("assistant", "Previous answer")]
    snapshot = storage._store.copy()

    if is_error or returncode != 0:
        with pytest.raises(ToolExecutionError) as error:
            await CLinkTool().execute(arguments)
        output = json.loads(str(error.value))
        assert output["status"] == "error"
        assert content in output["content"]
        assert output["metadata"]["events"] == events
        assert output["metadata"]["stdout"] == stdout
        assert "continuation_id" not in output["metadata"]
        assert output["continuation_offer"] is None
        assert storage._store == snapshot
    else:
        output = json.loads((await CLinkTool().execute(arguments))[0].text)
        assert output["status"] == "continuation_available"
        assert output["content"] == content
        assert output["metadata"]["events_removed_for_normal"] is True
        if events[0]["type"] == "error":
            assert output["metadata"]["errors"] == ["Transient request failure"]
        thread_id = output["continuation_offer"]["continuation_id"]
        expected_turns.extend([("user", "Current input"), ("assistant", content)])
        assert [(turn.role, turn.content) for turn in memory.get_thread(thread_id).turns] == expected_turns
        assert len(storage._store) == 1

    assert output["metadata"]["return_code"] == returncode
    assert output["metadata"]["stderr"] == "CLI diagnostic"
    assert len(launches) == 1
    resolve_provider.assert_not_called()


@pytest.mark.parametrize("item", [None, "invalid", 42, True, [], ["invalid"]])
def test_codex_rejects_malformed_completed_item_even_with_an_answer(item):
    stdout = json.dumps(CODEX_ANSWER) + "\n" + json.dumps({"type": "item.completed", "item": item})
    with pytest.raises(ParserError, match="item.completed.*object"):
        CodexJSONLParser().parse(stdout, "Diagnostic")


@pytest.mark.parametrize("model_stats", [None, "invalid", 42, True, [], ["invalid"]])
@pytest.mark.parametrize("failed", [False, True])
def test_gemini_optional_stats_cannot_hide_answer_or_terminal_error(model_stats, failed):
    payload = {"response": "Answer", "stats": {"models": {"gemini": model_stats}}}
    if failed:
        payload["error"] = {"message": "Quota exhausted"}
    parsed = GeminiJSONParser().parse(json.dumps(payload), "Diagnostic")
    assert parsed.is_error is failed
    assert ("Quota exhausted" in parsed.content) if failed else parsed.content == "Answer"
    assert parsed.metadata["raw"] == payload
    assert parsed.metadata["stderr"] == "Diagnostic"


@pytest.mark.asyncio
@pytest.mark.parametrize("continuation", [False, True], ids=["first-call", "follow-up"])
@pytest.mark.parametrize("returncode", [0, 2])
@pytest.mark.parametrize(
    "cli_name,payload",
    [
        ("codex", {"type": "item.completed", "item": "invalid"}),
        ("gemini", {"error": {"message": "Quota exhausted"}, "stats": {"models": {"gemini": "invalid"}}}),
        ("gemini", {"stats": {"models": {"gemini": ["invalid"]}}}),
        ("gemini", {"error": {"message": {"detail": "Quota exhausted"}}}),
        ("gemini", {"error": {"message": 429}}),
    ],
)
async def test_malformed_nested_output_keeps_structured_diagnostics_and_history(
    execution, cli_name, payload, returncode, continuation
):
    storage, resolve_provider, launches, configure_process = execution
    stdout = json.dumps(payload)
    configure_process(stdout, "Original diagnostic", returncode)
    arguments = {"cli_name": cli_name, "prompt": "Current input"}
    if continuation:
        thread_id = memory.create_thread("clink", {"cli_name": cli_name})
        memory.add_turn(thread_id, "user", "Previous input")
        memory.add_turn(thread_id, "assistant", "Previous answer")
        arguments["continuation_id"] = thread_id
    snapshot = storage._store.copy()

    with pytest.raises(ToolExecutionError) as error:
        await CLinkTool().execute(arguments)

    output = json.loads(str(error.value))
    assert output["status"] == "error"
    assert output["metadata"]["stdout"] == stdout
    assert output["metadata"]["stderr"] == "Original diagnostic"
    assert output["metadata"]["return_code"] == returncode
    assert output["continuation_offer"] is None
    assert "continuation_id" not in output["metadata"]
    assert storage._store == snapshot
    assert len(launches) == 1
    resolve_provider.assert_not_called()


@pytest.mark.parametrize(
    "failure", ["removed-before-run", "file-before-run", "removed-at-spawn", "denied", "no-filename"]
)
async def test_directory_launch_failure_has_spawn_metadata_and_no_commit(execution, tmp_path, monkeypatch, failure):
    from clink.agents import create_agent

    storage, _, launches, configure_process = execution
    project = tmp_path / "project"
    project.mkdir()
    configure_process('{"response":"ok"}')
    thread_id = memory.create_thread("clink", {"clink_working_directory": {"mode": "scratch"}})
    memory.add_turn(thread_id, "user", "previous input")
    memory.add_turn(thread_id, "assistant", "previous answer")
    snapshot = storage._store.copy()
    spawn_attempts = []
    if failure in ("removed-before-run", "file-before-run"):

        def factory(client):
            project.rmdir()
            if failure == "file-before-run":
                project.write_text("now a file")
            return create_agent(client)

        monkeypatch.setattr("tools.clink.create_agent", factory)
    else:
        # Use the OS for the deletion race. No vendor executable is launched.
        monkeypatch.setattr("clink.agents.base.resolve_executable", lambda *args, **kwargs: sys.executable)

        async def spawn(*args, **kwargs):
            spawn_attempts.append(kwargs["cwd"])
            if failure == "denied":
                raise PermissionError(errno.EACCES, "synthetic access denied", kwargs["cwd"])
            project.rmdir()
            if failure == "no-filename":
                raise FileNotFoundError(errno.ENOENT, "synthetic unattributed failure")
            return await native_spawn(*args, **kwargs)

        monkeypatch.setattr("clink.agents.base.asyncio.create_subprocess_exec", spawn)

    with pytest.raises(ToolExecutionError) as error:
        await CLinkTool().execute(
            {
                "cli_name": "gemini",
                "prompt": "test",
                "continuation_id": thread_id,
                "working_directory_absolute_path": str(project),
            }
        )
    response = json.loads(error.value.payload)
    assert response["metadata"] == {
        "cli_name": "gemini",
        "return_code": None,
        "tool_name": "clink",
        "error_type": "WorkingDirectoryLaunchError",
        "stage": "spawn",
        "execution_started": False,
        "working_directory": {
            "mode": "directory",
            "launch_path": str(project),
            "canonical_path": str(project),
            "source": "request",
        },
    }
    assert "Selected working directory" in response["content"]
    assert "[Errno" in response["content"]
    assert "Executable not found" not in response["content"]
    assert response["continuation_offer"] is None
    assert storage._store == snapshot
    assert not coordination._entries
    assert not launches
    assert len(spawn_attempts) == (0 if failure in ("removed-before-run", "file-before-run") else 1)


@pytest.mark.parametrize("failure", ["missing-executable", "denied-executable", "unattributed", "other-os-error"])
async def test_executable_spawn_failures_are_not_directory_failures(execution, tmp_path, monkeypatch, failure):
    storage = execution[0]
    causes = {
        "missing-executable": FileNotFoundError(errno.ENOENT, "missing interpreter", "/synthetic/cli"),
        "denied-executable": PermissionError(errno.EACCES, "executable denied", "/synthetic/cli"),
        "unattributed": FileNotFoundError(errno.ENOENT, "unattributed failure"),
        "other-os-error": OSError(errno.EMFILE, "descriptor limit"),
    }

    async def spawn(*args, **kwargs):
        raise causes[failure]

    monkeypatch.setattr("clink.agents.base.asyncio.create_subprocess_exec", spawn)
    with pytest.raises(ToolExecutionError) as error:
        await CLinkTool().execute(
            {"cli_name": "gemini", "prompt": "test", "working_directory_absolute_path": str(tmp_path)}
        )
    response = json.loads(error.value.payload)
    assert str(causes[failure]) in response["content"]
    assert "error_type" not in response["metadata"]
    assert "stage" not in response["metadata"]
    assert response["metadata"]["working_directory"]["launch_path"] == str(tmp_path)
    assert not storage._store
    assert not coordination._entries


@pytest.mark.parametrize("selected", [False, True])
async def test_cancel_reaps_child_and_releases_bound_admission(execution, tmp_path, monkeypatch, selected):
    storage, _, _, configure_process = execution
    binding = (
        {"mode": "directory", "launch_path": str(tmp_path), "canonical_path": str(tmp_path)}
        if selected
        else {"mode": "scratch"}
    )
    thread_id = memory.create_thread("clink", {"clink_working_directory": binding})
    snapshot = storage._store.copy()
    spawned = asyncio.Event()
    processes = []

    async def spawn(*args, **kwargs):
        process = ControlledProcess(kwargs["cwd"], "cancel")
        processes.append(process)
        spawned.set()
        return process

    monkeypatch.setattr("clink.agents.base.asyncio.create_subprocess_exec", spawn)
    arguments = {"cli_name": "gemini", "prompt": "test", "continuation_id": thread_id}
    task = asyncio.create_task(CLinkTool().execute(arguments))
    try:
        await asyncio.wait_for(spawned.wait(), 2)
        process = processes[0]
        await asyncio.wait_for(process.started.wait(), 2)
        task.cancel()
        await asyncio.wait_for(process.reaping.wait(), 2)
        assert process.cwd.is_dir()
        assert storage._store == snapshot
        process.release_reap.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert process.killed and process.reaped
        assert process.cwd.exists() is selected
        assert not coordination._entries
        assert storage._store == snapshot
        configure_process('{"response":"retry succeeds"}')
        response = json.loads((await CLinkTool().execute(arguments))[0].text)
        assert response["metadata"]["working_directory"]["source"] == "conversation"
    finally:
        for process in processes:
            process.release_reap.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("cli_name", ["codex", "gemini"])
@pytest.mark.parametrize("native_rejection", [True, False])
@pytest.mark.parametrize("recovered", [True, False])
async def test_codex_repository_guidance_is_specific_and_never_retries(
    execution, tmp_path, cli_name, native_rejection, recovered
):
    storage, _, launches, configure_process = execution
    diagnostic = (Path(__file__).parent / "fixtures" / "codex_git_rejection.stderr").read_text().strip()
    stderr = diagnostic if native_rejection else "Authentication failed; check your credentials."
    stdout = json.dumps(CODEX_ANSWER) if recovered else ""
    configure_process(stdout, stderr, 1)
    with pytest.raises(ToolExecutionError) as error:
        await CLinkTool().execute(
            {"cli_name": cli_name, "prompt": "test", "working_directory_absolute_path": str(tmp_path)}
        )
    response = json.loads(error.value.payload)
    assert response["metadata"]["stderr"] == stderr
    if cli_name == "codex" and native_rejection:
        guidance = response["metadata"]["git_repository_guidance"]
        for phrase in ("existing Git repository", "--skip-git-repo-check", "additional_args", "restart PAL"):
            assert phrase in guidance
        assert diagnostic in response["content"]
        assert guidance in response["content"]
    else:
        assert "git_repository_guidance" not in response["metadata"]
        assert "restart PAL" not in response["content"]
    assert len(launches) == 1
    assert "--skip-git-repo-check" not in launches[0]
    assert not storage._store
    assert response["continuation_offer"] is None


async def test_codex_operator_skip_flag_survives_selected_directory(execution, tmp_path, monkeypatch):
    from clink.models import CLIClientConfig
    from clink.registry import ClinkRegistry

    registry = ClinkRegistry.__new__(ClinkRegistry)
    client = registry._resolve_config(
        CLIClientConfig(name="codex", command="codex", additional_args=["--skip-git-repo-check"]),
        source_path=tmp_path / "codex.json",
    )
    tool = CLinkTool()
    monkeypatch.setattr(tool._registry, "get_client", lambda _: client)
    execution[3](json.dumps(CODEX_ANSWER))
    result = json.loads(
        (await tool.execute({"cli_name": "codex", "prompt": "test", "working_directory_absolute_path": str(tmp_path)}))[
            0
        ].text
    )
    assert result["status"] == "continuation_available"
    assert execution[2][0].count("--skip-git-repo-check") == 1
    assert result["metadata"]["working_directory"]["source"] == "request"


async def test_scratch_allocation_failure_keeps_selection_without_exposing_temporary_path(execution, monkeypatch):
    cause = PermissionError(errno.EACCES, "access denied", "/synthetic/pal-clink-private")
    monkeypatch.setattr("clink.agents.base.tempfile.TemporaryDirectory", Mock(side_effect=cause))
    with pytest.raises(ToolExecutionError) as error:
        await CLinkTool().execute({"cli_name": "gemini", "prompt": "test"})
    response = json.loads(error.value.payload)
    assert response["metadata"] == {
        "cli_name": "gemini",
        "return_code": None,
        "tool_name": "clink",
        "error_type": "WorkingDirectoryLaunchError",
        "stage": "spawn",
        "execution_started": False,
        "working_directory": {"mode": "scratch", "launch_path": None, "canonical_path": None, "source": "scratch"},
    }
    assert "[Errno 13] access denied" in response["content"]
    assert cause.filename not in error.value.payload
    assert response["continuation_offer"] is None
    assert not execution[0]._store
    assert not execution[2]
