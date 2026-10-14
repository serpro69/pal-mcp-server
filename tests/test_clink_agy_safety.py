"""Agy edit-mode requests inherit native policy and never enforce path scopes."""

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from clink.agents import create_agent
from clink.agents.base import CLIAgentError
from clink.availability import resolve_executable
from tests import test_clink_agy as agy_fixtures
from tools.clink import CLinkTool
from tools.shared.exceptions import ToolExecutionError

registry = agy_fixtures.registry
process = agy_fixtures.process


@pytest.mark.asyncio
@pytest.mark.parametrize("followup_fields", [{}, {"allow_edits": False}], ids=["omitted", "false"])
async def test_native_edit_mode_is_not_inherited_on_followup(registry, process, followup_fields):
    client = registry.get_client("agy")
    original_config = client.model_dump()
    tool = CLinkTool()
    first = json.loads((await tool.execute({"cli_name": "agy", "prompt": "FIRST", "allow_edits": True}))[0].text)
    second = json.loads(
        (
            await tool.execute(
                {
                    "prompt": "SECOND",
                    **followup_fields,
                    "continuation_id": first["continuation_offer"]["continuation_id"],
                }
            )
        )[0].text
    )
    assert len(process.calls) == 2
    assert process.calls[0][0][-2:] == ("--mode", "accept-edits")
    assert process.calls[0][0].count("--mode") == 1
    assert "--mode" not in process.calls[1][0]
    assert second["metadata"]["cli_name"] == "agy"
    assert "=== EXECUTION POLICY ===" in json.loads(process.inputs[1])["message"]["content"]
    assert client.model_dump() == original_config


@pytest.mark.asyncio
async def test_overlapping_calls_keep_mode_flags_request_local(registry, monkeypatch):
    client = registry.get_client("agy")
    original_config = client.model_dump()
    processes = []
    both_started = asyncio.Event()
    release = asyncio.Event()
    entered = []

    class OverlappingProcess(agy_fixtures.CapturedProcess):
        returncode = None

        async def communicate(self, data=None):
            if data is not None:
                entered.append(self)
                if len(entered) == 2:
                    both_started.set()
                await release.wait()
                self.returncode = 0
            return await super().communicate(data)

    async def spawn(*args, **kwargs):
        child = OverlappingProcess()
        processes.append((args, child))
        return child

    async def observe_overlap():
        try:
            await asyncio.wait_for(both_started.wait(), timeout=3)
            assert len(processes) == 2 and len(entered) == 2
        finally:
            release.set()

    monkeypatch.setattr("clink.agents.base.resolve_executable", lambda *args, **kwargs: sys.executable)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    tool = CLinkTool()
    results = await asyncio.wait_for(
        asyncio.gather(
            tool.execute({"cli_name": "agy", "prompt": "EDIT_REQUEST", "allow_edits": True}),
            tool.execute({"cli_name": "agy", "prompt": "INHERITED_REQUEST"}),
            observe_overlap(),
        ),
        timeout=5,
    )
    for result, edits in zip(results[:2], (True, False)):
        payload = json.loads(result[0].text)
        command = payload["metadata"]["command"]
        assert command.count("--mode") == int(edits)
        if edits:
            assert command[-2:] == ["--mode", "accept-edits"]
    assert client.model_dump() == original_config


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["executable", "config_args", "safe_args", "edit_args", "role_args"])
@pytest.mark.parametrize(
    "args",
    [
        ["--mode", "accept-edits"],
        ["--mode=auto"],
        ["-mode=plan"],
        ["--dangerously-skip-permissions"],
        ["--dangerously-skip-permissions=true"],
    ],
)
async def test_edit_opt_in_still_rejects_configured_modes_and_bypasses(registry, process, source, args):
    client = registry.get_client("agy")
    target = client.get_role(None) if source == "role_args" else client
    getattr(target, source).extend(args)
    with pytest.raises(ToolExecutionError, match="reserved"):
        await CLinkTool().execute({"cli_name": "agy", "prompt": "hello", "allow_edits": True})
    assert not process.calls


@pytest.mark.asyncio
async def test_false_request_may_report_a_write_allowed_by_saved_policy(registry, process):
    process.stdout = b'{"event":"result","result":{"status":"SUCCESS","response":"Saved policy allowed a write."}}\n'
    payload = json.loads((await CLinkTool().execute({"cli_name": "agy", "prompt": "Report file status"}))[0].text)
    assert payload["status"] in {"success", "continuation_available"}
    assert payload["content"] == "Saved policy allowed a write."
    assert "--mode" not in process.calls[0][0]


@pytest.mark.asyncio
async def test_agent_defensively_rejects_path_scope(registry, process, tmp_path):
    client = registry.get_client("agy")
    with pytest.raises(CLIAgentError, match="does not support editable_paths"):
        await create_agent(client).run(
            role=client.get_role(None),
            prompt="hello",
            files=[],
            images=[],
            allow_edits=True,
            editable_paths=[str(tmp_path / "sentinel.txt")],
        )
    assert not process.calls


def settings_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def is_observed_empty_result(exc):
    if exc.returncode != 0 or str(exc.__cause__) != "agy result response must be a nonempty string":
        return False
    # This parser failure follows validation of all envelopes, result cardinality,
    # and SUCCESS status. Distinguish the observed empty string from invalid types.
    events = [json.loads(line) for line in exc.stdout.split("\n") if line.strip()]
    result = next(event["result"] for event in events if event["event"] == "result")
    return result.get("response") == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fields,observed",
    [
        ({}, False),
        ({"response": None}, False),
        ({"response": 7}, False),
        ({"response": {}}, False),
        ({"response": []}, False),
        ({"response": " \n"}, False),
        ({"response": ""}, True),
        ({"response": "", "status": "ERROR"}, False),
    ],
)
async def test_live_observation_accepts_only_explicit_empty_success(registry, process, fields, observed):
    process.stdout = json.dumps({"event": "result", "result": {"status": "SUCCESS", **fields}}).encode()
    client = registry.get_client("agy")
    with pytest.raises(CLIAgentError) as caught:
        await create_agent(client).run(role=client.get_role(None), prompt="hello", files=[], images=[])
    assert is_observed_empty_result(caught.value) is observed


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("configured_cwd", [False, True], ids=["scratch", "project"])
async def test_live_agy_reads_and_native_edits_preserve_settings(registry, tmp_path, monkeypatch, configured_cwd):
    """Use PAL_RUN_AGY_INTEGRATION=1; failed required operations are never skipped.

    Tests the production agent/shared runner directly, without role instructions.
    Default-write effects are observations, not a universal denial requirement.
    Only documented user settings are hashed; authentication files are not read.
    """
    if os.environ.get("PAL_RUN_AGY_INTEGRATION") != "1":
        pytest.skip("Set PAL_RUN_AGY_INTEGRATION=1 with agy 1.3.5 already authenticated")
    project = tmp_path / "project"
    project.mkdir()
    marker = "PAL_AGY_READ_" + uuid4().hex
    marker_file = project / "marker.txt"
    marker_file.write_text(marker + "\n", encoding="utf-8")
    sentinel = project / "sentinel.txt"
    sentinel.write_text("ORIGINAL\n", encoding="utf-8")
    client = registry.get_client("agy")
    client.working_dir = project if configured_cwd else None
    client.timeout_seconds = 90
    agent = create_agent(client)
    child_env = agent._build_environment()
    executable = resolve_executable(client, environment=child_env)
    assert executable is not None, "Opted-in agy executable is missing"
    version_process = await asyncio.create_subprocess_exec(
        executable, "--version", env=child_env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        version_stdout, _ = await asyncio.wait_for(version_process.communicate(), timeout=10)
    except BaseException:
        await agent._kill_and_reap(version_process)
        raise
    assert version_process.returncode == 0 and version_stdout.decode().strip() == "1.3.5"
    settings = Path(child_env.get("HOME", str(Path.home()))) / ".gemini/antigravity-cli/settings.json"
    before = settings_hash(settings)
    invocations = []
    run_in_directory = agent._run_in_directory

    async def observe_directory(**kwargs):
        invocations.append({"cwd": kwargs["cwd"], "allow_edits": kwargs["allow_edits"]})
        return await run_in_directory(**kwargs)

    monkeypatch.setattr(agent, "_run_in_directory", observe_directory)
    evidence = {
        "version": "1.3.5",
        "cwd_mode": "project" if configured_cwd else "scratch",
        "project": str(project),
        "settings_file": str(settings),
        "settings_before": before,
        "environment_names": sorted(child_env),
        "invocations": invocations,
    }
    try:
        read = await agent.run(
            role=client.get_role(None),
            prompt=f"Use your native file-reading tool to read {marker_file}. Return its exact contents. Do not edit files or run commands.",
            files=[str(marker_file)],
            images=[],
        )
        assert marker in read.parsed.content, "Required file-only read marker was not returned"
        assert "--mode" not in read.sanitized_command
        assert settings_hash(settings) == before
        evidence["read_marker_returned"] = True

        # Under the recorded policy agy may soft-deny a write, exit zero, and emit
        # an empty SUCCESS response. Record that failure as an observation only;
        # auth/launch/timeout/protocol errors remain failures of the opted-in run.
        try:
            default = await agent.run(
                role=client.get_role(None),
                prompt=f"Use a native file-editing tool to replace only {sentinel} with DEFAULT_WRITE plus a newline. Do not use shell commands. Report the outcome, including any denied permission.",
                files=[str(sentinel)],
                images=[],
            )
            evidence["default_result"] = "usable_answer"
            assert "--mode" not in default.sanitized_command
        except CLIAgentError as exc:
            if not is_observed_empty_result(exc):
                raise
            evidence["default_result"] = "zero_exit_empty_response_error"
            evidence["default_stderr"] = exc.stderr[:2048]
        default_bytes = sentinel.read_text(encoding="utf-8")
        assert default_bytes in {"ORIGINAL\n", "DEFAULT_WRITE\n"}, "Unexpected default-write fixture contents"
        evidence["default_file_changed"] = default_bytes == "DEFAULT_WRITE\n"
        assert settings_hash(settings) == before

        edit = await agent.run(
            role=client.get_role(None),
            prompt=f"Use a native file-editing tool to replace only {sentinel} with NATIVE_EDIT plus a newline. Do not use shell commands. Report the change.",
            files=[str(sentinel)],
            images=[],
            allow_edits=True,
        )
        assert edit.sanitized_command.count("--mode") == 1
        assert edit.sanitized_command[-2:] == ["--mode", "accept-edits"]
        assert sentinel.read_text(encoding="utf-8") == "NATIVE_EDIT\n", "Required native edit did not occur"
        evidence["native_edit_verified"] = True
        assert len(invocations) == 3
        for invocation in invocations:
            cwd = Path(invocation["cwd"])
            assert (cwd == project) == configured_cwd
            if not configured_cwd:
                assert project != cwd and not project.is_relative_to(cwd)
    finally:
        evidence["settings_after"] = settings_hash(settings)
        evidence["settings_unchanged"] = evidence["settings_after"] == before
        print("AGY_LIVE_EVIDENCE " + json.dumps(evidence, sort_keys=True))
    assert evidence["settings_unchanged"], "Native invocation changed saved user settings"
