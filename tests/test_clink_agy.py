"""Agy transport and protocol contracts, without executing the real CLI."""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from clink.agents import create_agent
from clink.agents.agy import AgyAgent
from clink.agents.base import CLIAgentError
from clink.models import OutputCaptureConfig
from clink.parsers import get_parser
from clink.parsers.agy import AgyJSONLParser
from clink.parsers.base import ParserError
from clink.registry import ClinkRegistry
from tools.clink import CLinkRequest, CLinkTool
from tools.shared.exceptions import ToolExecutionError

PROMPT_MARKER = "PAL_AGY_PRIVATE_PROMPT_MARKER"
PROMPT = (
    PROMPT_MARKER
    + '\nQuotes: "hello"; braces: {"x": 1}\n雪 café 🐍\nC:\\tmp\\path\n$(echo literal)\u2028next\u0085last\u2029end'
)


def result_event(response="answer", **changes):
    result = {"status": "SUCCESS", "response": response}
    result.update(changes)
    return {"event": "result", "result": result}


def stream(*events):
    return "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n"


@pytest.fixture
def registry(monkeypatch, tmp_path):
    monkeypatch.setattr("clink.registry.USER_CONFIG_DIR", tmp_path / "no-user-config")
    monkeypatch.setattr("clink.registry.get_env", lambda name: None)
    registry = ClinkRegistry()
    monkeypatch.setattr("tools.clink.get_registry", lambda: registry)
    monkeypatch.setenv("PAL_TRANSCRIPTS", "false")
    return registry


class CapturedProcess:
    returncode = 0

    def __init__(self):
        self.inputs = []
        self.calls = []
        self.stdout = stream(result_event("  final answer\n")).encode("utf-8")
        self.stderr = b""
        self.timeout = False
        self.spawn_error = None
        self.kills = 0
        self.drains = 0
        self.waits = 0
        self._transport = Mock()

    async def communicate(self, data=None):
        if data is not None:
            self.inputs.append(data)
            if self.timeout:
                raise asyncio.TimeoutError
        else:
            self.drains += 1
        return self.stdout, self.stderr

    def kill(self):
        self.kills += 1
        self.returncode = -9

    async def wait(self):
        self.waits += 1
        return self.returncode


@pytest.fixture
def process(monkeypatch):
    process = CapturedProcess()

    async def spawn(*args, **kwargs):
        process.calls.append((args, kwargs))
        assert Path(kwargs["cwd"]).is_dir()
        if process.spawn_error is not None:
            raise process.spawn_error
        return process

    monkeypatch.setattr("clink.agents.base.resolve_executable", lambda *args, **kwargs: sys.executable)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return process


def test_discovery_and_schema_preserve_selection_contract(registry):
    client = registry.get_client("agy")
    assert isinstance(create_agent(client), AgyAgent)
    assert isinstance(get_parser(client.parser), AgyJSONLParser)
    assert client.executable == ["agy"]
    assert client.internal_args == ["--input-format", "stream-json", "--output-format", "stream-json"]
    assert client.config_args == client.safe_args == client.edit_args == []
    assert client.env == {}
    assert client.working_dir is None
    assert registry.list_roles("agy") == ["codereviewer", "default", "planner"]
    assert client.get_role(None).prompt_path == Path("systemprompts/clink/default.txt").resolve()
    tool = CLinkTool()
    assert tool._default_cli_name == "gemini"
    schema = tool.get_input_schema()
    assert set(schema["properties"]["cli_name"]["enum"]) == {"agy", "claude", "codex", "gemini"}
    assert "anyOf" not in schema  # Anthropic API rejects top-level anyOf; rule is enforced at runtime
    assert tool.get_annotations()["readOnlyHint"] is False
    for description in (
        CLinkRequest.model_fields["allow_edits"].description,
        schema["properties"]["allow_edits"]["description"],
    ):
        assert "agy" in description
        assert "false inherits saved permissions" in description
        assert "does not guarantee read-only" in description
        assert "true requests --mode accept-edits" in description
        assert "does not support editable_paths" in description


@pytest.mark.asyncio
async def test_default_request_reaches_process_with_one_prepared_event(registry, process):
    tool = CLinkTool()
    response = json.loads((await tool.execute({"cli_name": "agy", "prompt": PROMPT}))[0].text)
    assert response["status"] in {"success", "continuation_available"}
    assert response["content"] == "  final answer\n"
    assert len(process.calls) == len(process.inputs) == 1
    data = process.inputs[0]
    assert data.endswith(b"\n") and data.count(b"\n") == 1
    event = json.loads(data)
    assert set(event) == {"event", "message"}
    assert event["event"] == "user"
    assert set(event["message"]) == {"content"}
    text = event["message"]["content"]
    assert text.count(PROMPT) == 1
    assert registry.get_client("agy").get_role(None).prompt_path.read_text().strip() in text
    assert "=== EXECUTION POLICY ===" in text
    command, _ = process.calls[0]
    assert command == (sys.executable, "--input-format", "stream-json", "--output-format", "stream-json")
    assert response["metadata"]["command"] == list(command)
    assert PROMPT not in " ".join(command)


@pytest.mark.asyncio
async def test_new_multi_client_request_still_requires_selection(registry, process):
    with pytest.raises(ToolExecutionError, match="cli_name is required"):
        await CLinkTool().execute({"prompt": "hello"})
    assert not process.calls


@pytest.mark.asyncio
async def test_sole_agy_client_can_be_selected_automatically(registry, process):
    registry._clients = {"agy": registry.get_client("agy")}
    tool = CLinkTool()
    assert "anyOf" not in tool.get_input_schema()
    payload = json.loads((await tool.execute({"prompt": "hello"}))[0].text)
    assert payload["metadata"]["cli_name"] == "agy"
    assert len(process.calls) == 1


@pytest.mark.asyncio
async def test_edit_request_adds_native_mode_once(registry, process):
    payload = json.loads(
        (await CLinkTool().execute({"cli_name": "agy", "prompt": "hello", "allow_edits": True}))[0].text
    )
    assert payload["status"] in {"success", "continuation_available"}
    assert len(process.calls) == 1
    command = process.calls[0][0]
    assert command.count("--mode") == 1
    assert command[-2:] == ("--mode", "accept-edits")
    assert "=== EXECUTION POLICY ===" not in json.loads(process.inputs[0])["message"]["content"]


RESERVED = [
    "--input-format",
    "--output-format",
    "--print",
    "--prompt",
    "-p",
    "--prompt-interactive",
    "-i",
    "--continue",
    "-c",
    "--conversation",
    "--mode",
    "--dangerously-skip-permissions",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["executable", "config_args", "safe_args", "edit_args", "role_args"])
@pytest.mark.parametrize("equals", [False, True], ids=["split", "equals"])
@pytest.mark.parametrize("option", RESERVED)
async def test_reserved_options_fail_before_spawn(registry, process, source, equals, option):
    client = registry.get_client("agy")
    role = client.get_role(None)
    args = [option + "=PRIVATE_VALUE"] if equals else [option, "PRIVATE_VALUE"]
    target = role if source == "role_args" else client
    getattr(target, source).extend(args)
    with pytest.raises(CLIAgentError, match="reserved") as caught:
        await create_agent(client).run(role=role, prompt=PROMPT, files=[], images=[])
    assert "PRIVATE_VALUE" not in str(caught.value)
    assert not process.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("option", ["--", "-input-format=stream-json", "-mode=auto", "-dangerously-skip-permissions"])
@pytest.mark.parametrize("source", ["executable", "config_args", "safe_args", "edit_args", "role_args"])
async def test_terminator_and_single_dash_conflicts(registry, process, option, source):
    client = registry.get_client("agy")
    role = client.get_role(None)
    getattr(role if source == "role_args" else client, source).append(option)
    with pytest.raises(CLIAgentError, match="reserved"):
        await create_agent(client).run(role=role, prompt=PROMPT, files=[], images=[])
    assert not process.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "internal_args",
    [
        [],
        ["--input-format", "text"],
        ["--input-format", "stream-json", "--output-format", "stream-json", "--input-format", "stream-json"],
    ],
)
async def test_invalid_internal_protocol_fails_before_spawn(registry, process, internal_args):
    client = registry.get_client("agy")
    client.internal_args = internal_args
    with pytest.raises(CLIAgentError, match="built-in stream-json"):
        await create_agent(client).run(role=client.get_role(None), prompt=PROMPT, files=[], images=[])
    assert not process.calls


@pytest.mark.asyncio
async def test_output_file_capture_fails_before_spawn(registry, process):
    client = registry.get_client("agy")
    client.output_to_file = OutputCaptureConfig(flag_template="--output {path}")
    with pytest.raises(CLIAgentError, match="output_to_file"):
        await create_agent(client).run(role=client.get_role(None), prompt=PROMPT, files=[], images=[])
    assert not process.calls


@pytest.mark.asyncio
async def test_model_and_timeout_arguments_remain_configurable(registry, process):
    client = registry.get_client("agy")
    client.executable.extend(["--model", "operator-model"])
    client.config_args = ["--print-timeout=40s"]
    client.get_role(None).role_args = ["--effort", "high"]
    await create_agent(client).run(role=client.get_role(None), prompt=PROMPT, files=[], images=[])
    command = process.calls[0][0]
    assert command.count("--input-format") == command.count("--output-format") == 1
    assert command[1:3] == ("--model", "operator-model")
    assert "--print-timeout=40s" in command
    assert command[-2:] == ("--effort", "high")


@pytest.mark.parametrize("content", [" answer\n", PROMPT])
def test_parser_preserves_final_text_and_ignores_informational_events(content):
    output = "\n" + stream(
        {"event": "future_event", "data": {"anything": [1, True]}}, result_event(content), {"event": "info"}
    )
    parsed = AgyJSONLParser().parse(output, "permission notice")
    assert parsed.content == content
    assert parsed.metadata == {"agy_status": "SUCCESS"}
    assert parsed.is_error is False


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        " \n\n",
        "not json",
        "[]",
        "null",
        "42",
        '"text"',
        "{}",
        '{"event": 42}',
        '{"event": null}',
        stream({"event": "init"}),
        stream({"event": "result"}),
        stream({"event": "result", "result": []}),
        stream({"event": "result", "result": None}),
        stream(result_event(), result_event()),
        stream({"event": "result", "result": {"response": "answer"}}),
        stream(result_event(status="ERROR")),
        stream(result_event(status="CANCELLED")),
        stream(result_event(status="success")),
        stream(result_event(status=0)),
        stream(result_event(response=None)),
        stream(result_event(response=1)),
        stream(result_event(response={})),
        stream(result_event(response="")),
        stream(result_event(response=" \n\t")),
        stream(result_event()) + "malformed\n",
        '{"event":"info","data":NaN}\n' + stream(result_event()),
        '{"event":"info","data":Infinity}\n' + stream(result_event()),
    ],
)
def test_parser_rejects_invalid_or_incomplete_results(stdout):
    with pytest.raises(ParserError):
        AgyJSONLParser().parse(stdout, "")


DENIED_COMMAND = {"action": "command", "display_name": "RunCommand"}


def test_parser_names_denied_actions_when_headless_denial_leaves_response_empty():
    output = stream(result_event(response="", denied_actions=[DENIED_COMMAND, {"action": "read_url"}]))
    with pytest.raises(ParserError) as caught:
        AgyJSONLParser().parse(output, "")
    message = str(caught.value)
    assert "auto-denied: command (RunCommand), read_url" in message
    assert "permissions.allow" in message and "settings.json" in message
    assert "nonempty string" not in message


def test_parser_attaches_denied_actions_to_nonempty_response():
    output = stream(result_event(response="partial answer", denied_actions=[DENIED_COMMAND]))
    parsed = AgyJSONLParser().parse(output, "")
    assert parsed.content == "partial answer"
    assert parsed.metadata == {"agy_status": "SUCCESS", "agy_denied_actions": ["command (RunCommand)"]}


@pytest.mark.parametrize(
    "denied_actions",
    [None, "command", {}, [], [1], ["command"], [{}], [{"action": 1}], [{"action": " "}], [{"display_name": "X"}]],
)
def test_parser_ignores_malformed_denied_actions(denied_actions):
    assert "agy_denied_actions" not in (
        AgyJSONLParser().parse(stream(result_event(denied_actions=denied_actions)), "").metadata
    )
    with pytest.raises(ParserError, match="nonempty string"):
        AgyJSONLParser().parse(stream(result_event(response="", denied_actions=denied_actions)), "")


def test_parser_bounds_denied_action_labels():
    denied = [{"action": f"action-{index}", "display_name": "x" * 500} for index in range(25)]
    parsed = AgyJSONLParser().parse(stream(result_event(denied_actions=denied)), "")
    labels = parsed.metadata["agy_denied_actions"]
    assert len(labels) == 10
    assert all(len(label) == 80 and label.startswith("action-") for label in labels)


def test_retained_live_fixture_parses():
    fixture = (
        Path(__file__).resolve().parents[1]
        / "docs/feat/wip/antigravity-clink/fixtures/enterprise-1.3.5-smoke-scratch.jsonl"
    )
    assert AgyJSONLParser().parse(fixture.read_text(), "").content == "PAL_AGY_TRANSPORT_OK\n"


@pytest.mark.asyncio
async def test_selected_metadata_cannot_overwrite_pal_execution_or_continuation(registry, process):
    process.stdout = stream(
        {"event": "init", "init": {"model": "reported-model", "cwd": "private", "tools": ["private"]}},
        {"event": "step_update", "step_update": {"text_delta": PROMPT, "usage": {"input_tokens": 999}}},
        result_event(
            conversation_id="native-session-id",
            duration_seconds=9876.5,
            num_turns=1,
            usage={
                "input_tokens": 10,
                "output_tokens": 2,
                "thinking_tokens": 0,
                "cache_read_tokens": 3,
                "total_tokens": 12,
                "private": PROMPT,
            },
            command=[PROMPT],
            cli_name="spoofed",
            role="spoofed",
            return_code=99,
            continuation_id="spoofed",
            model_used="spoofed",
            events=[PROMPT],
            raw=PROMPT,
            stderr=PROMPT,
        ),
    ).encode()
    payload = json.loads((await CLinkTool().execute({"cli_name": "agy", "prompt": PROMPT}))[0].text)
    metadata = payload["metadata"]
    assert metadata["agy_status"] == "SUCCESS"
    assert metadata["agy_conversation_id"] == "native-session-id"
    assert metadata["agy_duration_seconds"] == 9876.5
    assert metadata["duration_seconds"] != 9876.5
    assert metadata["num_turns"] == 1
    assert metadata["usage"] == {
        "input_tokens": 10,
        "output_tokens": 2,
        "thinking_tokens": 0,
        "cache_read_tokens": 3,
        "total_tokens": 12,
    }
    assert metadata["model_used"] == "reported-model"
    assert metadata["cli_name"] == "agy" and metadata["role"] == "default"
    assert metadata["return_code"] == 0 and metadata["command"] == list(process.calls[0][0])
    assert "continuation_id" not in metadata
    assert payload["continuation_offer"]["continuation_id"] not in {"spoofed", "native-session-id"}
    assert PROMPT_MARKER not in json.dumps(metadata)
    assert not {"events", "raw", "stdout", "stderr", "result", "init", "step_update"} & metadata.keys()


@pytest.mark.parametrize("value", [None, True, False, -1, 1.5, "7", {}, [], "", "  "])
def test_invalid_optional_metadata_is_omitted(value):
    event = result_event(conversation_id=value, duration_seconds=value, num_turns=value, usage={"input_tokens": value})
    parsed = AgyJSONLParser().parse(stream({"event": "init", "init": {"model": value}}, event), "")
    expected = {"agy_status": "SUCCESS"}
    if isinstance(value, str) and value.strip():
        expected.update(agy_conversation_id=value, model_used=value)
    if value == 1.5:
        expected["agy_duration_seconds"] = 1.5
    assert parsed.metadata == expected


def test_zero_counts_are_preserved_and_unknown_usage_keys_are_omitted():
    parsed = AgyJSONLParser().parse(
        stream(
            result_event(
                duration_seconds=0,
                num_turns=0,
                usage={"input_tokens": 0, "output_tokens": True, "arbitrary_tokens": 12},
            )
        ),
        "",
    )
    assert parsed.metadata == {
        "agy_status": "SUCCESS",
        "agy_duration_seconds": 0,
        "num_turns": 0,
        "usage": {"input_tokens": 0},
    }


@pytest.mark.parametrize("usage", [None, [], "tokens", 7, {}])
def test_invalid_or_empty_usage_is_omitted(usage):
    assert AgyJSONLParser().parse(stream(result_event(usage=usage)), "").metadata == {"agy_status": "SUCCESS"}


def test_overflowed_optional_numbers_are_omitted():
    output = (
        '{"event":"result","result":{"status":"SUCCESS","response":"ok",'
        '"duration_seconds":1e999,"num_turns":1e999,"usage":{"input_tokens":1e999}}}'
    )
    assert AgyJSONLParser().parse(output, "").metadata == {"agy_status": "SUCCESS"}


@pytest.mark.asyncio
@pytest.mark.parametrize("length", [0, 2047, 2048, 2049, 8000])
@pytest.mark.parametrize("failure", [False, True], ids=["success", "nonzero-exit"])
async def test_stderr_diagnostics_are_bounded_on_both_paths(registry, process, length, failure):
    diagnostic = ("Permission notice: blocked an operation. " + "雪" * length)[:length]
    process.stderr = diagnostic.encode()
    if failure:
        process.returncode = 2
        with pytest.raises(ToolExecutionError) as caught:
            await CLinkTool().execute({"cli_name": "agy", "prompt": PROMPT})
        payload = json.loads(str(caught.value))
        assert payload["status"] == "error"
        assert "exited with status 2" in payload["content"]
        assert len(payload["content"]) <= 2048
    else:
        payload = json.loads((await CLinkTool().execute({"cli_name": "agy", "prompt": PROMPT}))[0].text)
        assert payload["content"] == "  final answer\n"
        assert payload["status"] in {"success", "continuation_available"}
    metadata = payload["metadata"]
    assert "stdout" not in metadata
    if length:
        assert len(metadata["stderr"]) <= 2048
        assert metadata["stderr"].startswith("Permission notice")
        if length > 2048:
            assert metadata["stderr"].endswith("[truncated]")
            assert metadata["stderr_truncated"] is True
        else:
            assert metadata["stderr"] == diagnostic
            assert "stderr_truncated" not in metadata
    else:
        assert "stderr" not in metadata


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stdout,returncode,expected",
    [
        (stream(result_event()), 7, "exited with status 7"),
        (stream(result_event(status="ERROR", error="Sign in through agy.")), 2, "Sign in through agy."),
        (stream(result_event(status="ERROR", error="Sign in through agy.")), 0, "ERROR"),
        (stream(result_event(status="CANCELLED")), 0, "CANCELLED"),
        (stream(result_event(status="FUTURE_STATUS")), 0, "FUTURE_STATUS"),
        (stream(result_event(status=None)), 0, "missing or invalid"),
        ("malformed RAW_STREAM_MARKER", 0, "Invalid agy JSON event"),
        (stream({"event": "init"}), 0, "Missing agy result event"),
        (stream(result_event()) + '{"event":', 0, "Invalid agy JSON event"),
        (stream(result_event(), result_event()), 0, "duplicate results"),
        (stream(result_event(response="  ")), 0, "nonempty string"),
        (stream(result_event(response="", denied_actions=[DENIED_COMMAND])), 0, "auto-denied: command (RunCommand)"),
        ("{}", 0, "Invalid agy event envelope"),
    ],
)
async def test_failures_are_tool_errors_without_raw_output_or_replay(
    registry, process, caplog, stdout, returncode, expected
):
    caplog.set_level("DEBUG", logger="clink.runner.agy")
    process.stdout = (stream({"event": "user", "message": {"content": PROMPT}}) + stdout).encode()
    process.returncode = returncode
    process.stderr = b"Native diagnostic notice"
    with pytest.raises(ToolExecutionError) as caught:
        await CLinkTool().execute({"cli_name": "agy", "prompt": PROMPT})
    payload = json.loads(str(caught.value))
    assert payload["status"] == "error" and expected in payload["content"]
    assert payload["metadata"]["return_code"] == returncode
    assert payload["metadata"]["stderr"] == "Native diagnostic notice"
    assert not {"stdout", "raw", "events", "result", "command"} & payload["metadata"].keys()
    assert len(process.calls) == len(process.inputs) == 1
    assert PROMPT in json.loads(process.inputs[0])["message"]["content"]
    assert PROMPT_MARKER not in " ".join(process.calls[0][0])
    assert PROMPT_MARKER not in json.dumps(payload)
    assert "RAW_STREAM_MARKER" not in json.dumps(payload)
    assert "Executing CLI command:" in caplog.text and PROMPT_MARKER not in caplog.text


@pytest.mark.asyncio
async def test_denied_actions_surface_in_successful_payload_metadata(registry, process):
    process.stdout = stream(result_event(response="partial answer", denied_actions=[DENIED_COMMAND])).encode()
    payload = json.loads((await CLinkTool().execute({"cli_name": "agy", "prompt": PROMPT}))[0].text)
    assert payload["content"] == "partial answer"
    assert payload["metadata"]["agy_denied_actions"] == ["command (RunCommand)"]


@pytest.mark.asyncio
async def test_result_error_description_is_selected_and_bounded(registry, process):
    process.stdout = stream(
        result_event(
            status="ERROR",
            error="Authentication required. Sign in with agy. " + "x" * 5000,
            response="RAW_RESPONSE_MARKER",
        )
    ).encode()
    with pytest.raises(ToolExecutionError) as caught:
        await CLinkTool().execute({"cli_name": "agy", "prompt": PROMPT})
    payload = json.loads(str(caught.value))
    assert "ERROR" in payload["content"] and "Sign in with agy" in payload["content"]
    assert len(payload["content"]) == 2048 and payload["content"].endswith("[truncated]")
    assert payload["metadata"]["error_truncated"] is True
    assert "RAW_RESPONSE_MARKER" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_timeout_reaps_one_process_without_replay_or_command_prompt(registry, process, caplog):
    caplog.set_level("DEBUG", logger="clink.runner.agy")
    process.returncode = None
    process.timeout = True
    with pytest.raises(ToolExecutionError) as caught:
        await CLinkTool().execute({"cli_name": "agy", "prompt": PROMPT})
    payload = json.loads(str(caught.value))
    assert payload["status"] == "error" and "timed out" in payload["content"]
    assert payload["metadata"]["return_code"] is None
    assert len(process.calls) == len(process.inputs) == process.kills == process.drains == process.waits == 1
    process._transport.close.assert_called_once()
    assert not Path(process.calls[0][1]["cwd"]).exists()
    assert PROMPT_MARKER not in " ".join(process.calls[0][0])
    assert "Executing CLI command:" in caplog.text and PROMPT_MARKER not in caplog.text
    assert "stdout" not in payload["metadata"] and PROMPT_MARKER not in json.dumps(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("at_spawn", [False, True])
async def test_missing_executable_is_a_tool_error(registry, process, monkeypatch, at_spawn):
    if at_spawn:
        process.spawn_error = FileNotFoundError("executable disappeared")
    else:
        monkeypatch.setattr("clink.agents.base.resolve_executable", lambda *args, **kwargs: None)
    with pytest.raises(ToolExecutionError) as caught:
        await CLinkTool().execute({"cli_name": "agy", "prompt": PROMPT})
    payload = json.loads(str(caught.value))
    assert payload["status"] == "error" and "Executable" in payload["content"]
    assert "not found" in payload["content"]
    assert len(process.calls) == int(at_spawn) and not process.inputs


@pytest.mark.asyncio
async def test_success_logs_only_command_and_keeps_prompt_on_stdin(registry, process, caplog):
    caplog.set_level("DEBUG", logger="clink.runner.agy")
    payload = json.loads((await CLinkTool().execute({"cli_name": "agy", "prompt": PROMPT}))[0].text)
    assert PROMPT in json.loads(process.inputs[0])["message"]["content"]
    assert PROMPT_MARKER not in " ".join(process.calls[0][0])
    assert PROMPT_MARKER not in " ".join(payload["metadata"]["command"])
    assert "Executing CLI command:" in caplog.text and PROMPT_MARKER not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,stdout",
    [
        ("gemini", '{"response":"ok"}'),
        ("claude", '{"type":"result","subtype":"success","result":"ok"}'),
        ("codex", '{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}'),
    ],
)
@pytest.mark.parametrize("failure", [False, True])
async def test_existing_clients_keep_unbounded_diagnostics_and_error_stdout(registry, process, name, stdout, failure):
    process.stdout = b"legacy raw error output" if failure else stdout.encode()
    process.stderr = b"legacy diagnostic " + b"x" * 4096
    if failure:
        process.returncode = 9
        with pytest.raises(ToolExecutionError) as caught:
            await CLinkTool().execute({"cli_name": name, "prompt": "hello"})
        payload = json.loads(str(caught.value))
        assert payload["metadata"]["stdout"] == "legacy raw error output"
    else:
        payload = json.loads((await CLinkTool().execute({"cli_name": name, "prompt": "hello"}))[0].text)
        assert payload["content"] == "ok"
    assert payload["metadata"]["stderr"] == process.stderr.decode()
    assert "stderr_truncated" not in payload["metadata"]


@pytest.mark.asyncio
@pytest.mark.parametrize("agy_runner", [False, True])
async def test_diagnostic_policy_follows_resolved_runner(registry, process, agy_runner):
    client = registry.get_client("agy")
    if agy_runner:
        client.name = "renamed-client"
        client.runner = "AGY"
        registry._clients[client.name] = registry._clients.pop("agy")
    else:
        client.runner = "gemini"
        client.parser = "gemini_json"
        process.stdout = b'{"response":"ok"}'
    process.stderr = b"x" * 4096
    payload = json.loads((await CLinkTool().execute({"cli_name": client.name, "prompt": "hello"}))[0].text)
    assert len(payload["metadata"]["stderr"]) == (2048 if agy_runner else 4096)
    assert payload["metadata"].get("stderr_truncated", False) == agy_runner


@pytest.mark.asyncio
async def test_nonzero_exit_cannot_be_rescued_by_success_result(registry, process):
    process.returncode = 1
    client = registry.get_client("agy")
    with pytest.raises(CLIAgentError, match="exited with status 1"):
        await create_agent(client).run(role=client.get_role(None), prompt=PROMPT, files=[], images=[])
    assert len(process.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,stdout",
    [
        ("gemini", '{"response":"ok"}'),
        ("claude", '{"type":"result","subtype":"success","result":"ok"}'),
        ("codex", '{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}'),
    ],
)
async def test_existing_clients_keep_exact_utf8_stdin(registry, process, name, stdout):
    client = registry.get_client(name)
    process.stdout = stdout.encode()
    output = await create_agent(client).run(role=client.get_role(None), prompt=PROMPT, files=[], images=[])
    assert process.inputs == [PROMPT.encode("utf-8")]
    assert output.parsed.content == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("configured_cwd", [False, True])
async def test_real_fixture_waits_for_eof_and_receives_one_event(registry, tmp_path, configured_cwd):
    fixture = tmp_path / "eof_fixture.py"
    fixture.write_text(
        "import json, os, sys\n"
        "data = sys.stdin.buffer.read()\n"
        "assert data.endswith(b'\\n') and data.count(b'\\n') == 1\n"
        "event = json.loads(data)\n"
        "assert event['event'] == 'user'\n"
        "reply = json.dumps({'content': event['message']['content'], 'cwd': os.getcwd()})\n"
        "print(json.dumps({'event': 'result', 'result': {'status': 'SUCCESS', 'response': reply}}))\n"
    )
    client = registry.get_client("agy")
    client.executable = [sys.executable, str(fixture)]
    client.timeout_seconds = 5
    client.working_dir = tmp_path if configured_cwd else None
    result = await create_agent(client).run(role=client.get_role(None), prompt=PROMPT, files=[], images=[])
    observed = json.loads(result.parsed.content)
    assert observed["content"] == PROMPT
    cwd = Path(observed["cwd"])
    if configured_cwd:
        assert cwd == tmp_path.resolve() and cwd.is_dir()
    else:
        assert cwd != tmp_path.resolve()
        assert cwd.name.startswith("pal-clink-") and not cwd.exists()


@pytest.mark.asyncio
async def test_encoder_failure_reaps_spawned_process_and_removes_scratch(registry, monkeypatch):
    client = registry.get_client("agy")
    agent = create_agent(client)
    spawned = []

    class Process:
        returncode = None
        _transport = Mock()

        def kill(self):
            self.returncode = -9

        async def communicate(self):
            assert self.returncode == -9
            return b"", b""

        async def wait(self):
            assert self.returncode == -9

    async def spawn(*args, **kwargs):
        spawned.append(Path(kwargs["cwd"]))
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr("clink.agents.base.resolve_executable", lambda *args, **kwargs: sys.executable)
    monkeypatch.setattr(agent, "_encode_stdin", Mock(side_effect=ValueError("encoding failed")))
    with pytest.raises(ValueError, match="encoding failed"):
        await agent.run(role=client.get_role(None), prompt=PROMPT, files=[], images=[])
    assert len(spawned) == 1 and not spawned[0].exists()
    Process._transport.close.assert_called_once()
