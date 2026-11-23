"""Generic clink clients through the real runner, pipes and conversation boundary."""

import asyncio
import errno
import json
import logging
import os
import shlex
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from clink import availability, diagnostics
from clink.agents import create_agent
from clink.agents.base import CLIAgentError
from clink.constants import DEFAULT_STREAM_LIMIT
from clink.registry import ClinkRegistry, RegistryLoadError
from tests.test_clink_working_directory_subprocess import child_gate
from tools.clink import CLinkRequest, CLinkTool
from tools.shared.exceptions import ToolExecutionError
from utils import conversation_memory as memory
from utils.storage_backend import InMemoryStorage

FIXTURES = Path(__file__).parent / "fixtures"
SCRIPT = (FIXTURES / "generic_cli.py").resolve()


@pytest.fixture
def cli(tmp_path, monkeypatch):
    capture = tmp_path / "capture.json"
    manifest = json.loads((FIXTURES / "clink_generic/team-ask.json").read_text(encoding="utf-8"))
    manifest.update(
        command=shlex.join([sys.executable, str(SCRIPT)]),
        env={"FIXTURE_CAPTURE": str(capture), "PYTHONIOENCODING": "utf-8"},
    )
    path = tmp_path / "client.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr("clink.registry.get_env", lambda name: str(path))
    registry = ClinkRegistry()
    monkeypatch.setattr("tools.clink.get_registry", lambda: registry)
    monkeypatch.setattr(InMemoryStorage, "_cleanup_worker", lambda self: None)
    storage = InMemoryStorage()
    monkeypatch.setattr(memory, "get_storage", lambda: storage)
    monkeypatch.setenv("PAL_TRANSCRIPTS", "false")

    def configure(**changes):
        manifest.update(changes)
        path.write_text(json.dumps(manifest), encoding="utf-8")
        registry.reload()
        return registry.get_client("team-ask")

    launches = []
    spawn = asyncio.create_subprocess_exec

    async def observe(*argv, **kwargs):
        proc = await spawn(*argv, **kwargs)
        launches.append((argv, Path(kwargs["cwd"]), proc))
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", observe)
    try:
        yield SimpleNamespace(
            registry=registry,
            configure=configure,
            manifest=manifest,
            launches=launches,
            storage=storage,
            tool=CLinkTool(),
            capture=lambda: json.loads(capture.read_text(encoding="utf-8")),
        )
    finally:
        storage.shutdown()
        assert all(process.returncode is not None for _, _, process in launches)


async def invoke(cli, **arguments):
    result = await cli.tool.execute({"cli_name": "team-ask", "prompt": "Question ☀", **arguments})
    return json.loads(result[0].text)


async def failure(cli, **arguments):
    with pytest.raises(ToolExecutionError) as caught:
        await invoke(cli, **arguments)
    payload = json.loads(caught.value.payload)
    metadata = payload["metadata"]
    assert metadata["cli_name"] == "team-ask" and metadata["reason"] in payload["content"]
    assert metadata["failure_hint"] == cli.manifest["custom"]["failure_hint"]
    assert metadata["failure_hint"] in payload["content"]
    assert not {"stdout", "stderr", "raw"}.intersection(metadata)
    return metadata


async def test_role_stdin_permissions_environment_and_continuation(cli, tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("TEST_CLI_TOKEN", "SYNTHETIC_CREDENTIAL")
    monkeypatch.setenv("GEMINI_API_KEY", "SYNTHETIC_VENDOR_KEY")
    role = tmp_path / "operator.txt"
    role.write_text("Operator instructions.", encoding="utf-8")
    cli.configure(
        additional_args=["STATIC_PRIVATE"],
        model_args=["-m", "{model}"],
        default_model="chosen",
        safe_args=["--read-only"],
        edit_args=["--apply"],
        env_passthrough=["TEST_CLI_TOKEN"],
        roles={"operator": {"prompt_path": str(role), "role_args": ["--operator"]}},
    )
    prompt = 'Question ☀\nSecond line with a NUL \0 and a quotation mark "'
    first = await invoke(cli, prompt=prompt)
    got = cli.capture()
    expected = await cli.tool.prepare_prompt(CLinkRequest(cli_name="team-ask", prompt=prompt))
    assert got["prompt"] == expected and expected.count(prompt) == 1
    neutral = cli.registry.get_client("team-ask").get_role(None).prompt_path.read_text().strip()
    assert neutral in expected and "EXECUTION POLICY" in expected
    assert got["argv"] == ["STATIC_PRIVATE", "-m", "chosen", "--read-only"]
    assert got["configured_token"] == "SYNTHETIC_CREDENTIAL" and not got["vendor_key_present"]
    assert first["content"] == "  Fixture answer ✓  \n"
    assert first["metadata"]["command"] == [Path(sys.executable).name]
    assert "STDERR_PRIVATE" not in json.dumps(first)
    assert "  Fixture answer" not in caplog.text and "STATIC_PRIVATE" not in caplog.text
    assert "STDERR_PRIVATE" not in caplog.text
    thread = first["continuation_offer"]["continuation_id"]
    second = await invoke(cli, role="operator", prompt="Follow-up", allow_edits=True, continuation_id=thread)
    got = cli.capture()
    assert second["metadata"]["history"]["retained_messages"] == 2
    assert got["argv"] == ["STATIC_PRIVATE", "-m", "chosen", "--apply", "--operator"]
    assert "Operator instructions." in got["prompt"] and neutral not in got["prompt"]
    assert got["prompt"].count("Follow-up") == got["prompt"].count(first["content"].strip()) == 1
    for prepared in (expected, got["prompt"]):
        assert "web searches" not in prepared and "using your own CLI tools" not in prepared
    assert "EXECUTION POLICY" not in got["prompt"]


@pytest.mark.parametrize(
    "mode,stage,returncode", [("failure", "exit", 7), ("bad-utf8", "parse", 0), ("blank", "parse", 0)]
)
async def test_failed_followup_is_private_and_preserves_history(cli, mode, stage, returncode, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("TEST_CLI_TOKEN", "PRIVATE_TEST_TOKEN")
    first = await invoke(cli)
    snapshot = cli.storage._store.copy()
    cli.configure(env={**cli.manifest["env"], "FIXTURE_MODE": mode}, env_passthrough=["TEST_CLI_TOKEN"])
    meta = await failure(cli, prompt="PRIVATE_REQUEST", continuation_id=first["continuation_offer"]["continuation_id"])
    assert meta["stage"] == stage and meta["return_code"] == returncode and meta["execution_started"]
    assert cli.storage._store == snapshot
    for marker in ("PRIVATE_REQUEST", "PLAUSIBLE_PRIVATE_ANSWER", "PRIVATE_TEST_TOKEN"):
        assert marker not in caplog.text and marker not in json.dumps(meta)


async def test_empty_edit_bucket_scope_missing_role_and_executable(cli, tmp_path):
    assert (await failure(cli, allow_edits=True))["stage"] == "validation"
    assert "editable_paths" in (await failure(cli, allow_edits=True, editable_paths=[str(tmp_path)]))["reason"]
    assert "role" in (await failure(cli, role="missing"))["reason"]
    path = tmp_path / "role.txt"
    path.write_text("role")
    cli.configure(roles={"special": {"prompt_path": str(path)}})
    path.unlink()
    assert "prompt file" in (await failure(cli, role="special"))["reason"]
    cli.configure(command=shlex.quote(str(tmp_path / "absent")), roles={})
    assert (await failure(cli))["stage"] == "lookup"
    details = await failure(cli, working_directory_absolute_path=str(tmp_path / "missing-dir"))
    assert details["error_type"] == "InvalidWorkingDirectoryError" and not details["execution_started"]
    assert not cli.launches


def test_stderr_redacts_overlapping_literals_and_escapes_controls():
    text = "start ABCDE ababa\n\r\t\x00\x1b\x7f\x85\u2028 end"
    excerpt = diagnostics._stderr_excerpt(text.encode(), "ABC", {"TOKEN": "CDE", "PROXY": "aba", "EMPTY": ""})
    assert excerpt == r"start ***** *****\n\r\t\u0000\u001b\u007f\u0085\u2028 end"


def test_stderr_masks_the_whole_prompt_across_the_tail_boundary():
    prompt = "long prompt " * 400 + "PRIVATE_SUFFIX"
    excerpt = diagnostics._stderr_excerpt(("head " + prompt + " Remaining detail").encode(), prompt, {})
    assert excerpt.startswith("[truncated] ") and excerpt.endswith(" Remaining detail")
    assert set(excerpt.removeprefix("[truncated] ").removesuffix(" Remaining detail")) == {"*"}
    assert len(excerpt) == 2048

    expanded = diagnostics._stderr_excerpt(b"\x00" * 1024 + b" END", "", {})
    assert expanded == "[truncated] " + r"\u0000" * 338 + " END"
    assert len(expanded) <= 2048


@pytest.mark.skipif(os.name != "posix", reason="POSIX uses native bytes for argv and the environment")
def test_private_stderr_masks_native_bytes_before_decoding(monkeypatch):
    prompt = "café question"
    native = prompt.encode("latin-1")
    original = os.fsencode
    monkeypatch.setattr(os, "fsencode", lambda value: native if value == prompt else original(value))
    # The inherited value begins inside what the complete stderr decodes as ©.
    env = {"TOKEN": os.fsdecode(b"\xa9secret")}
    stderr = b"DETAIL " + native + b" " + prompt.encode() + b" \xc2\xa9secret"
    excerpt = diagnostics._stderr_excerpt(stderr, prompt, env, flag_prompt=True)
    assert excerpt.startswith("DETAIL ") and set(excerpt[7:]) == {"*", " "}


@pytest.fixture
def main_log(tmp_path, monkeypatch, capsys):
    handler = diagnostics.MainLogHandler(tmp_path / "main.log", maxBytes=4096, backupCount=1, encoding="utf-8")
    monkeypatch.setattr(logging.getLogger(), "handlers", [handler, logging.StreamHandler()])
    try:
        yield handler
    finally:
        handler.close()


@pytest.mark.parametrize("mode,detail", [("bad-utf8", "NATIVE_ENCODING_DETAIL"), ("blank", "NATIVE_PARSE_DETAIL")])
async def test_complete_stderr_available_on_parse_failure(cli, main_log, capsys, mode, detail):
    cli.configure(
        custom={**cli.manifest["custom"], "log_stderr_on_failure": True},
        env={**cli.manifest["env"], "FIXTURE_MODE": mode},
    )
    meta = await failure(cli)
    assert meta["stage"] == "parse" and meta["return_code"] == 0
    assert detail in Path(main_log.baseFilename).read_text()
    assert detail not in json.dumps(meta) + capsys.readouterr().err


@pytest.mark.parametrize("fault", ["write", "rollover", "filter"])
async def test_private_diagnostic_failure_preserves_cli_error(cli, main_log, monkeypatch, capsys, fault):
    cli.configure(
        custom={**cli.manifest["custom"], "log_stderr_on_failure": True},
        env={**cli.manifest["env"], "FIXTURE_MODE": "failure"},
    )
    error = OSError("injected sink failure")
    if fault == "write":
        monkeypatch.setattr(main_log.stream, "write", Mock(side_effect=error))
    elif fault == "rollover":
        main_log.maxBytes = 1
        monkeypatch.setattr(main_log, "doRollover", Mock(side_effect=error))
    else:
        monkeypatch.setattr(diagnostics, "_stderr_excerpt", Mock(side_effect=error))
    monkeypatch.setattr(logging, "raiseExceptions", True)
    meta = await failure(cli)
    assert meta["stage"] == "exit" and meta["return_code"] == 7
    host = capsys.readouterr().err
    assert "NATIVE_FAILURE_DETAIL" not in host + json.dumps(meta) + Path(main_log.baseFilename).read_text()
    if fault != "filter":
        # The ordinary CLI failure log still uses logging's standard error path.
        assert "--- Logging error ---" in host
        assert "failure stderr:" not in host


async def test_project_requires_directory_on_new_call(cli, tmp_path):
    cfg = cli.configure(custom={**cli.manifest["custom"], "workspace": "project"})
    meta = await failure(cli)
    assert meta["stage"] == "validation" and not meta["execution_started"]
    assert meta["working_directory"]["source"] == "scratch"
    assert "working_directory_absolute_path" in meta["reason"]
    assert "working_dir in the CLI manifest" in meta["reason"] and "restart PAL" in meta["reason"]
    with pytest.raises(CLIAgentError, match="project directory"):
        await create_agent(cfg).run(role=cfg.get_role(None), prompt="direct", files=[], images=[])
    assert not cli.launches and not cli.storage._store

    cli.configure(working_dir=str(tmp_path))
    result = await invoke(cli)
    assert result["metadata"]["working_directory"]["source"] == "configuration"
    assert Path(cli.capture()["cwd"]).samefile(tmp_path)


async def test_project_promotes_saved_scratch_only_after_success(cli, tmp_path):
    thread = memory.create_thread("clink", {"clink_working_directory": {"mode": "scratch"}})
    memory.add_turn(thread, "user", "Original scratch question")
    memory.add_turn(thread, "assistant", "Original scratch answer")
    snapshot = cli.storage._store.copy()
    cli.configure(custom={**cli.manifest["custom"], "workspace": "project"}, working_dir=str(tmp_path))
    meta = await failure(cli, continuation_id=thread)
    assert meta["stage"] == "validation" and not meta["execution_started"]
    assert meta["working_directory"]["source"] == "conversation"
    assert "working_directory_absolute_path" in meta["reason"] and "same continuation ID" in meta["reason"]
    assert "restart" not in meta["reason"]
    assert not cli.launches and cli.storage._store == snapshot

    project = tmp_path / "ordinary directory"
    project.mkdir()
    args = {"continuation_id": thread, "working_directory_absolute_path": str(project)}
    cli.configure(env={**cli.manifest["env"], "FIXTURE_MODE": "failure"})
    failed = await failure(cli, prompt="Failed promotion", **args)
    assert failed["stage"] == "exit" and failed["return_code"] == 7
    assert Path(cli.capture()["cwd"]).samefile(project) and cli.storage._store == snapshot

    cli.configure(env={**cli.manifest["env"], "FIXTURE_MODE": "reply"})
    promoted = await invoke(cli, prompt="Successful promotion", **args)
    assert promoted["continuation_offer"]["continuation_id"] == thread
    assert promoted["metadata"]["working_directory"]["source"] == "request"
    assert memory.get_thread(thread).initial_context["clink_working_directory"] == {
        "mode": "directory",
        "launch_path": str(project),
        "canonical_path": str(project.resolve()),
    }
    prompt = cli.capture()["prompt"]
    assert "Original scratch answer" in prompt and "Failed promotion" not in prompt


async def test_project_native_exit_preserves_bound_directory_for_retry(cli, tmp_path):
    mode = tmp_path / "fixture-mode"
    mode.write_text("reply")
    cli.configure(
        custom={**cli.manifest["custom"], "workspace": "project", "failure_hint": "Check project access."},
        env={**cli.manifest["env"], "FIXTURE_MODE_FILE": str(mode)},
    )
    first = await invoke(cli, working_directory_absolute_path=str(tmp_path))
    thread = first["continuation_offer"]["continuation_id"]
    snapshot = cli.storage._store.copy()
    mode.write_text("failure")
    meta = await failure(cli, continuation_id=thread, prompt="Denied follow-up")
    assert meta["stage"] == "exit" and meta["return_code"] == 7 and meta["execution_started"]
    assert meta["working_directory"]["source"] == "conversation" and Path(cli.capture()["cwd"]).samefile(tmp_path)
    assert cli.storage._store == snapshot

    other = tmp_path / "other project"
    other.mkdir()
    conflict = await failure(cli, continuation_id=thread, working_directory_absolute_path=str(other))
    assert conflict["new_conversation_required"] and not conflict["execution_started"]
    assert len(cli.launches) == 2 and cli.storage._store == snapshot

    mode.write_text("reply")
    retry = await invoke(cli, continuation_id=thread)
    assert retry["continuation_offer"]["continuation_id"] == thread
    assert retry["metadata"]["history"]["retained_messages"] == 2
    assert Path(cli.capture()["cwd"]).samefile(tmp_path)
    assert "Denied follow-up" not in cli.capture()["prompt"]


async def test_runtime_rechecks_assembled_safe_arguments(cli):
    cli.registry.get_client("team-ask").get_role(None).role_args = ["--yolo"]
    assert "write-enabling" in (await failure(cli))["reason"]
    assert not cli.launches


def configure_protocol(cli, *, flag=None, path=None, **changes):
    protocol = dict(cli.manifest["custom"])
    env = dict(cli.manifest["env"])
    if flag is not None:
        protocol["prompt"] = {"mode": "flag", "flag": flag}
        env["FIXTURE_PROMPT_FLAG"] = flag
    if path is not None:
        protocol["output"] = {"format": "json", "path": path}
        env["FIXTURE_MODE"] = "json"
    return cli.configure(custom=protocol, env=env, **changes)


@pytest.mark.parametrize("flag", ["-q", "-question", "--question"])
async def test_flag_is_last_option_with_one_literal_prompt_and_eof(cli, flag, caplog):
    caplog.set_level(logging.DEBUG)
    cfg = configure_protocol(
        cli,
        flag=flag,
        additional_args=["-quiet"],
        model_args=["-m", "{model}"],
        default_model="default-model",
        safe_args=["--read-only"],
        roles={"default": {"role_args": ["--role"]}},
    )
    prompt = '-Private prompt ☀ with spaces\nand "quotes", a backslash \\ and a trailing newline\n'
    result = await create_agent(cfg).run(role=cfg.get_role(None), prompt=prompt, files=[], images=[])
    capture = cli.capture()
    assert capture["argv"] == ["-quiet", "-m", "default-model", "--read-only", "--role", flag, prompt]
    assert capture["prompt"] == prompt and capture["stdin"] == ""
    assert result.parsed.content == "  Fixture answer ✓  \n" and result.parsed.metadata == {}
    assert result.sanitized_command == [Path(sys.executable).name]
    assert "Private prompt" not in caplog.text


@pytest.mark.parametrize("flag", [None, "--question"], ids=["stdin-json", "flag-json"])
async def test_json_reply_follows_literal_keys_and_preserves_string(cli, flag):
    configure_protocol(cli, flag=flag, path=["reply.text", "0"])
    answer = " \nExact leaf ☀\r\n "
    document = {"reply": {"text": "wrong"}, "reply.text": {"0": answer}, "unrelated": "PRIVATE_NATIVE_METADATA"}
    cli.configure(env={**cli.manifest["env"], "FIXTURE_JSON": json.dumps(document)})
    result = await invoke(cli)
    capture = cli.capture()
    prepared = await cli.tool.prepare_prompt(CLinkRequest(cli_name="team-ask", prompt="Question ☀"))
    assert capture["prompt"] == prepared
    assert capture["stdin"] == ("" if flag else prepared)
    assert capture["argv"] == ([flag, prepared] if flag else [])
    assert result["content"] == answer and result["metadata"]["parser"] == "json"
    assert "PRIVATE_NATIVE_METADATA" not in json.dumps(result)


@pytest.mark.parametrize(
    "document,reason",
    [
        ('{"reply":{}}', "missing key"),
        ('{"reply":["PRIVATE_NATIVE_VALUE"]}', "expected an object"),
        ('{"reply":{"text":42}}', "nonblank string"),
        ('{"reply":{"text":"  "}}', "nonblank string"),
        (r'{"reply":{"text":"\ud800"}}', "UTF-8"),
        ('{"reply":{"text":"PRIVATE_NATIVE_VALUE"}} {}', "single valid JSON document"),
        ('{"reply":{"text":"PRIVATE_NATIVE_VALUE"},"invalid":NaN}', "single valid JSON document"),
        ("PRIVATE_NATIVE_VALUE", "single valid JSON document"),
    ],
)
async def test_json_parse_failure_names_configured_path_and_preserves_history(cli, caplog, document, reason):
    configure_protocol(cli, flag="-q", path=["reply", "text"])
    first = await invoke(cli)
    thread = first["continuation_offer"]["continuation_id"]
    snapshot = cli.storage._store.copy()
    cli.configure(env={**cli.manifest["env"], "FIXTURE_JSON": document})
    meta = await failure(cli, continuation_id=thread)
    assert meta["stage"] == "parse" and meta["return_code"] == 0
    assert '["reply", "text"]' in meta["reason"] and reason in meta["reason"]
    assert "PRIVATE_NATIVE_VALUE" not in json.dumps(meta) + caplog.text
    assert cli.storage._store == snapshot


async def test_flag_rejects_nul_from_saved_history_before_spawn(cli):
    configure_protocol(cli, flag="-q", path=["reply", "text"])
    cli.configure(env={**cli.manifest["env"], "FIXTURE_JSON": json.dumps({"reply": {"text": "saved\0answer"}})})
    first = await invoke(cli)
    assert first["content"] == "saved\0answer"
    snapshot = cli.storage._store.copy()
    meta = await failure(cli, continuation_id=first["continuation_offer"]["continuation_id"])
    assert meta["stage"] == "validation" and not meta["execution_started"] and "U+0000" in meta["reason"]
    assert "saved history" in meta["reason"] and "new conversation" in meta["reason"]
    assert cli.storage._store == snapshot and len(cli.launches) == 1


@pytest.mark.parametrize("flag", [None, "-q"])
async def test_unencodable_prepared_prompt_never_spawns(cli, flag):
    cfg = configure_protocol(cli, flag=flag)
    with pytest.raises(CLIAgentError, match="UTF-8"):
        await create_agent(cfg).run(role=cfg.get_role(None), prompt="bad\ud800", files=[], images=[])
    assert not cli.launches


def test_prompt_flag_cannot_enable_edits(cli):
    with pytest.raises(RegistryLoadError, match=r"custom\.prompt.*flag.*write-enabling"):
        configure_protocol(cli, flag="--yolo")


async def test_write_check_uses_the_flag_value_without_parsing_it_as_an_option(cli):
    cfg = configure_protocol(cli, flag="--permission-mode")
    agent = create_agent(cfg)
    with pytest.raises(CLIAgentError, match="write-enabling"):
        await agent.run(role=cfg.get_role(None), prompt="acceptEdits", files=[], images=[])
    assert not cli.launches
    cfg = configure_protocol(cli, flag="-q")
    await create_agent(cfg).run(role=cfg.get_role(None), prompt="--yolo", files=[], images=[])
    assert cli.capture()["argv"] == ["-q", "--yolo"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX uses the filesystem encoding for argv")
async def test_flag_with_unrepresentable_native_argv_has_safe_validation_error(cli, monkeypatch):
    configure_protocol(cli, flag="-q")
    original = os.fsencode

    def legacy_encode(value):
        if isinstance(value, str) and "PRIVATE_UNICODE_☀" in value:
            return value.encode("ascii")
        return original(value)

    monkeypatch.setattr(os, "fsencode", legacy_encode)
    meta = await failure(cli, prompt="PRIVATE_UNICODE_☀")
    assert meta["stage"] == "validation" and not meta["execution_started"]
    assert "host filesystem encoding" in meta["reason"] and "PRIVATE_UNICODE" not in json.dumps(meta)
    assert not cli.launches


@pytest.mark.parametrize("executable", [r"C:\tools\cli.CMD", r"C:\tools\cli.BaT. "])
async def test_windows_batch_rejected_for_flag_delivery(cli, monkeypatch, executable):
    configure_protocol(cli, flag="-q")
    monkeypatch.setattr("clink.agents.base.os", SimpleNamespace(name="nt"))
    monkeypatch.setattr("clink.agents.base.resolve_executable", lambda *args, **kwargs: executable)
    meta = await failure(cli)
    assert meta["stage"] == "validation" and not meta["execution_started"] and "batch" in meta["reason"]
    assert not cli.launches


@pytest.mark.parametrize("windows", [False, True], ids=["posix-e2big", "windows-206"])
async def test_os_rejects_long_flag_followup_without_replay_or_history_change(cli, monkeypatch, caplog, windows):
    caplog.set_level(logging.DEBUG)
    configure_protocol(cli, flag="--question", path=["reply", "text"])
    first = await invoke(cli, prompt="First exchange")
    thread = first["continuation_offer"]["continuation_id"]
    snapshot = cli.storage._store.copy()
    native_spawn = asyncio.create_subprocess_exec
    attempts = []
    # Exercise the complete prompt, including the saved exchange, on any host.
    budget = len(cli.capture()["prompt"].encode("utf-8")) + 2048
    oversized = "PRIVATE_OVERSIZED_REQUEST" + "x" * budget

    async def limited_spawn(*args, **kwargs):
        attempts.append(args[-1])
        if len(args[-1].encode("utf-8")) > budget:
            exc = OSError(errno.ENOENT if windows else errno.E2BIG, args[-1])
            if windows:
                exc.winerror = 206
            raise exc
        return await native_spawn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", limited_spawn)
    meta = await failure(cli, continuation_id=thread, prompt=oversized)
    assert len(attempts) == 1 and "First exchange" in attempts[0]
    assert cli.storage._store == snapshot and not meta["execution_started"] and meta["stage"] == "launch"
    for phrase in ("Shorten", "new conversation", "summary", "history_token_budget", "restart"):
        assert phrase in meta["reason"]
    assert "PRIVATE_OVERSIZED_REQUEST" not in json.dumps(meta) + caplog.text
    retry = await invoke(cli, continuation_id=thread, prompt="Retry")
    assert len(attempts) == 2 and "PRIVATE_OVERSIZED_REQUEST" not in attempts[-1]
    assert retry["metadata"]["history"]["retained_messages"] == 2


async def test_prompt_preparation_reports_error_type_without_payload(cli, monkeypatch, caplog):
    async def bad_prompt(*args, **kwargs):
        raise ValueError("PRIVATE_PROMPT_DATA")

    monkeypatch.setattr(cli.tool, "_prepare_prompt_for_role", bad_prompt)
    details = await failure(cli)
    assert details["error_type"] == "ValueError" and "ValueError" in caplog.text
    assert "PRIVATE_PROMPT_DATA" not in caplog.text and "PRIVATE_PROMPT_DATA" not in json.dumps(details)
    assert not cli.launches


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable format error")
async def test_generic_launch_failure(cli, tmp_path):
    executable = tmp_path / "invalid-executable"
    executable.write_text("Invalid format")
    executable.chmod(0o700)
    cli.configure(command=shlex.quote(str(executable)))
    meta = await failure(cli)
    assert meta["stage"] == "launch" and not meta["execution_started"]
    assert not cli.launches and not cli.storage._store


@pytest.mark.parametrize(
    "section,values",
    [
        ("safe_args", ["--yolo"]),
        ("command", "example --dangerously-bypass-approvals-and-sandbox"),
        ("roles", {"extra": {"role_args": ["--permission-mode=acceptEdits"]}}),
        ("roles", {"extra": {"role_args": ["acceptEdits"]}}),
    ],
)
def test_default_argv_is_validated_across_fields_and_roles(cli, section, values):
    args = {"safe_args": ["--permission-mode"], section: values}
    with pytest.raises(RegistryLoadError, match="write-enabling"):
        cli.configure(**args)


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable symlink fixture")
@pytest.mark.parametrize("command,path", [("./bin/tool", ""), ("tool", "bin"), ("tool", "missing:")])
async def test_discovery_and_launch_use_startup_executable(cli, tmp_path, monkeypatch, command, path):
    startup, project = tmp_path / "startup", tmp_path / "project"
    startup.mkdir()
    project.mkdir()
    exe = startup / ("bin/tool" if "bin" in (command + path) else "tool")
    exe.parent.mkdir(exist_ok=True)
    exe.symlink_to(sys.executable)
    monkeypatch.setattr(availability, "STARTUP_DIRECTORY", str(startup))
    cfg = cli.configure(command=shlex.join([command, str(SCRIPT)]), env={**cli.manifest["env"], "PATH": path})
    assert next(
        item for item in availability.discover_cli_availability(cli.registry) if item.name == cfg.name
    ).executable_found
    await invoke(cli, working_directory_absolute_path=str(project))
    argv, cwd, _ = cli.launches[0]
    assert Path(argv[0]).is_absolute() and Path(argv[0]) == exe and cwd == project
    assert Path(cli.capture()["cwd"]).samefile(project)
    exe.unlink()
    assert (await failure(cli, working_directory_absolute_path=str(project)))["stage"] == "lookup"
    assert len(cli.launches) == 1


def test_empty_path_disables_bare_executable_lookup(cli, tmp_path, monkeypatch):
    program = tmp_path / "tool"
    program.write_text("fixture")
    program.chmod(0o700)
    monkeypatch.setattr(availability, "STARTUP_DIRECTORY", str(tmp_path))
    cfg = cli.configure(command="tool", env={"PATH": ""})
    assert availability.resolve_executable(cfg) is None


def test_windows_lookup_uses_effective_search_order_and_pathext(monkeypatch):
    startup = r"C:\server"
    monkeypatch.setattr(availability, "STARTUP_DIRECTORY", startup)
    found = {r"C:\server\bin\tool.CMD", r"C:\server\bin\tool.EXE"}
    monkeypatch.setattr(os.path, "isfile", found.__contains__)
    monkeypatch.setattr(os, "access", lambda name, mode: name in found)
    assert (
        availability._windows_executable("tool", {"Path": "missing;bin", "PathExt": ".CMD;.EXE"})
        == r"C:\server\bin\tool.CMD"
    )
    assert (
        availability._windows_executable(r"bin\tool", {"PATH": "ignored", "PATHEXT": ".EXE"})
        == r"C:\server\bin\tool.EXE"
    )
    assert availability._windows_executable("tool.CMD", {"PATH": "bin", "PATHEXT": ".EXE"}) is None
    found.update({r"C:\server\tool.EXE", r"C:\server\bin\no-suffix"})
    env = {"PATH": "bin", "PATHEXT": ".EXE"}
    assert availability._windows_executable("tool", env) == r"C:\server\tool.EXE"
    assert (
        availability._windows_executable("tool", {**env, "NoDefaultCurrentDirectoryInExePath": ""})
        == r"C:\server\bin\tool.EXE"
    )
    assert availability._windows_executable(r"bin\no-suffix", env) == r"C:\server\bin\no-suffix"
    found.add(r"C:\server\bin\tool.EXE.CMD")
    assert availability._windows_executable(r"bin\tool.EXE", {"PATHEXT": ".CMD"}) == r"C:\server\bin\tool.EXE"
    found.remove(r"C:\server\bin\tool.EXE")
    assert availability._windows_executable(r"bin\tool.EXE", {"PATHEXT": ".CMD"}) is None


@pytest.mark.skipif(os.name != "nt", reason="native Windows process launch")
@pytest.mark.parametrize("explicit", [False, True], ids=["relative-path-entry", "relative-command"])
async def test_windows_discovery_and_launch_from_another_workspace(cli, tmp_path, monkeypatch, explicit):
    python = Path(sys.executable)
    startup = python.parent.parent
    monkeypatch.setattr(availability, "STARTUP_DIRECTORY", str(startup))
    command = str(python.relative_to(startup)) if explicit else python.stem
    cli.configure(
        command=shlex.join([command, str(SCRIPT)]),
        env={
            **cli.manifest["env"],
            "Path": python.parent.name,
            "PathExt": ".EXE",
            "NoDefaultCurrentDirectoryInExePath": "1",
        },
    )
    match = availability.resolve_executable(cli.registry.get_client("team-ask"))
    assert match is not None and Path(match).samefile(python)
    await invoke(cli, working_directory_absolute_path=str(tmp_path))
    argv, cwd, _ = cli.launches[0]
    assert argv[0] == match and cwd == tmp_path


@pytest.mark.parametrize("mode", ["limits", "stdout", "stderr"])
async def test_real_stream_limits_and_reaping(cli, mode):
    cfg = cli.configure(
        env={**cli.manifest["env"], "FIXTURE_MODE": mode, "FIXTURE_LIMIT": str(DEFAULT_STREAM_LIMIT)},
        timeout_seconds=10,
    )
    snapshot = cli.storage._store.copy()
    if mode == "limits":
        result = await create_agent(cfg).run(role=cfg.get_role(None), prompt="p" * 200_000, files=[], images=[])
        assert result.parsed.content == "x" * DEFAULT_STREAM_LIMIT and result.parsed.metadata == {}
    else:
        meta = await failure(cli)
        assert meta["stage"] == "output_limit" and mode in meta["reason"] and cli.storage._store == snapshot
    _, cwd, process = cli.launches[0]
    assert process.returncode is not None and not cwd.exists()


@pytest.mark.parametrize("cancel", [True, False], ids=["cancel", "timeout"])
async def test_interrupted_generic_child_is_reaped_before_scratch_cleanup(cli, cancel, monkeypatch):
    async with child_gate() as gate:
        cli.configure(
            custom={**cli.manifest["custom"], "log_stderr_on_failure": True},
            env={**cli.manifest["env"], "FIXTURE_MODE": "hold", "FIXTURE_PORT": str(gate.port)},
            timeout_seconds=10 if cancel else 1,
        )
        write_stderr = Mock()
        monkeypatch.setattr(diagnostics, "_write_stderr", write_stderr)
        cleanup = tempfile.TemporaryDirectory.cleanup

        def check_cleanup(directory):
            if cli.launches and str(cli.launches[-1][1]) == directory.name:
                assert cli.launches[-1][2].returncode is not None
            cleanup(directory)

        monkeypatch.setattr("tempfile.TemporaryDirectory.cleanup", check_cleanup)
        task = asyncio.create_task(invoke(cli) if cancel else failure(cli))
        reader, _ = await asyncio.wait_for(gate.connections.get(), 5)
        assert await reader.readexactly(1) == b"R"
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert (await task)["stage"] == "timeout"
        assert await asyncio.wait_for(reader.read(), 5) == b""
        write_stderr.assert_not_called()
        assert not cli.launches[0][1].exists() and not cli.storage._store


async def test_persistence_failure_after_success_keeps_reply(cli, monkeypatch):
    commit = Mock(side_effect=memory.ConversationStorageError("fixture write failed"))
    monkeypatch.setattr("clink.conversation.CLIConversation.commit", commit)
    result = await invoke(cli)
    assert result["content"] == "  Fixture answer ✓  \n"
    assert result["metadata"]["history_saved"] is False and "memory_persistence_warning" in result["metadata"]
    assert len(cli.launches) == 1
