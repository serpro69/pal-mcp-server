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

PROMPT = 'Quotes: "hello"; braces: {"x": 1}\n雪 café 🐍\nC:\\tmp\\path\n$(echo literal)\u2028next\u0085last\u2029end'


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

    async def communicate(self, data):
        self.inputs.append(data)
        return self.stdout, b""


@pytest.fixture
def process(monkeypatch):
    process = CapturedProcess()

    async def spawn(*args, **kwargs):
        process.calls.append((args, kwargs))
        assert Path(kwargs["cwd"]).is_dir()
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
    assert registry.list_roles("agy") == ["default"]
    assert client.get_role(None).prompt_path == Path("systemprompts/clink/default.txt").resolve()
    tool = CLinkTool()
    assert tool._default_cli_name == "gemini"
    schema = tool.get_input_schema()
    assert set(schema["properties"]["cli_name"]["enum"]) == {"agy", "claude", "codex", "gemini"}
    assert schema["anyOf"] == [{"required": ["cli_name"]}, {"required": ["continuation_id"]}]
    assert tool.get_annotations()["readOnlyHint"] is False
    for description in (
        CLinkRequest.model_fields["allow_edits"].description,
        schema["properties"]["allow_edits"]["description"],
    ):
        assert "agy" in description
        assert "false inherits saved permissions" in description
        assert "does not guarantee read-only" in description
        assert "true is not yet supported" in description


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
async def test_edit_request_is_rejected_before_spawn(registry, process):
    with pytest.raises(ToolExecutionError, match="does not yet support allow_edits=true"):
        await CLinkTool().execute({"cli_name": "agy", "prompt": "hello", "allow_edits": True})
    assert not process.calls


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
    assert parsed.metadata == {}
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


def test_retained_live_fixture_parses():
    fixture = (
        Path(__file__).resolve().parents[1]
        / "docs/feat/wip/antigravity-clink/fixtures/enterprise-1.3.5-smoke-scratch.jsonl"
    )
    assert AgyJSONLParser().parse(fixture.read_text(), "").content == "PAL_AGY_TRANSPORT_OK\n"


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
