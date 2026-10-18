"""Real child processes observe per-request cwd without sharing registry state."""

import asyncio
import json
import os
import shlex
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from clink.agents.base import BaseCLIAgent
from clink.constants import CONFIG_DIR, INTERNAL_DEFAULTS
from clink.parsers import get_parser
from clink.registry import ClinkRegistry
from tools.clink import CLinkTool
from tools.shared.exceptions import ToolExecutionError
from utils import conversation_coordination as coordination
from utils import conversation_memory as memory
from utils.storage_backend import InMemoryStorage

FAKE = Path(__file__).parent / "fixtures" / "cli_only_fake.py"
VENDORS = ("agy", "claude", "codex", "gemini")
EDIT_FLAGS = {
    "agy": "accept-edits",
    "claude": "acceptEdits",
    "codex": "--dangerously-bypass-approvals-and-sandbox",
    "gemini": "--yolo",
}


@pytest.fixture
async def runtime(tmp_path, monkeypatch):
    manifests = tmp_path / "clients"
    manifests.mkdir()
    captures = tmp_path / "captures"
    captures.mkdir()
    for vendor in VENDORS:
        (captures / vendor).mkdir()
        config = json.loads((CONFIG_DIR / f"{vendor}.json").read_text(encoding="utf-8"))
        config.update(
            command=shlex.join([sys.executable, str(FAKE.resolve()), vendor, str(captures / vendor), "0"]),
            timeout_seconds=10,
            env={"CLINK_TEST_SCENARIO": "directory", "CLINK_TEST_MARKER": "project.marker"},
        )
        for role, definition in config["roles"].items():
            definition["role_args"] = ["--fixture-role", role]
        (manifests / f"{vendor}.json").write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr("clink.registry.USER_CONFIG_DIR", tmp_path / "absent-user-config")
    monkeypatch.setattr("clink.registry.get_env", lambda name: str(manifests))
    registry = ClinkRegistry()
    monkeypatch.setattr("tools.clink.get_registry", lambda: registry)
    monkeypatch.setattr(InMemoryStorage, "_cleanup_worker", lambda self: None)
    storage = InMemoryStorage()
    monkeypatch.setattr(memory, "get_storage", lambda: storage)
    monkeypatch.setenv("PAL_TRANSCRIPTS", "false")
    monkeypatch.setenv("PAL_STATE_DIR", str(tmp_path / "state"))
    forbidden_provider = Mock(side_effect=AssertionError("Native fixture calls must not resolve an API provider"))
    monkeypatch.setattr("providers.registry.ModelProviderRegistry.get_provider_for_model", forbidden_provider)

    spawns = {}
    cleaned = []
    native_spawn = asyncio.create_subprocess_exec

    async def observe_spawn(*args, **kwargs):
        process = await native_spawn(*args, **kwargs)
        spawns[process.pid] = SimpleNamespace(process=process, cwd=kwargs["cwd"], argv=list(args))
        return process

    class ObservedWorkspace(tempfile.TemporaryDirectory):
        def cleanup(self):
            matching = [call for call in spawns.values() if call.cwd == self.name]
            assert all(call.process.returncode is not None for call in matching), "Scratch removed before child reaping"
            cleaned.append(self.name)
            super().cleanup()

    monkeypatch.setattr("clink.agents.base.asyncio.create_subprocess_exec", observe_spawn)
    monkeypatch.setattr("clink.agents.base.tempfile.TemporaryDirectory", ObservedWorkspace)

    def configure(vendor, **changes):
        path = manifests / f"{vendor}.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config.update(changes)
        path.write_text(json.dumps(config), encoding="utf-8")
        registry.reload()
        return registry.get_client(vendor)

    def capture(vendor, stage):
        return json.loads((captures / vendor / f"{stage}.json").read_text(encoding="utf-8"))

    try:
        yield SimpleNamespace(
            registry=registry,
            storage=storage,
            spawns=spawns,
            cleaned=cleaned,
            configure=configure,
            capture=capture,
            tool=CLinkTool(),
        )
    finally:
        live = [call.process for call in spawns.values() if call.process.returncode is None]
        try:
            for process in live:
                await BaseCLIAgent._kill_and_reap(process)
        finally:
            storage.shutdown()
        assert not live, "Test leaked a native fixture process"
        assert not coordination._entries
        forbidden_provider.assert_not_called()


@pytest.fixture
def projects(tmp_path):
    paths = []
    for name in ("project A", "project B", "configured"):
        path = tmp_path / name
        path.mkdir()
        (path / "project.marker").write_text(name, encoding="utf-8")
        paths.append(path)
    alias = tmp_path / "project alias"
    alias.symlink_to(paths[0], target_is_directory=True)
    return *paths, alias


async def invoke(runtime, vendor, stage, **fields):
    arguments = {"cli_name": vendor, "prompt": f"CLINK_TEST_STAGE={stage} CURRENT_{stage}", **fields}
    return json.loads((await runtime.tool.execute(arguments))[0].text)


def assert_observation(runtime, vendor, stage, result, path, source):
    captured = runtime.capture(vendor, stage)
    spawned = runtime.spawns[captured["pid"]]
    assert spawned.process.returncode == 0
    assert spawned.argv[5:] == captured["argv"]  # Python, script, vendor, captures, gate precede native args.
    observed = json.loads(result["content"])
    assert observed == {"cwd": captured["cwd"], "marker": captured["marker"]}
    assert result["metadata"]["working_directory"] == {
        "mode": "scratch" if path is None else "directory",
        "launch_path": None if path is None else str(path),
        "canonical_path": None if path is None else str(path.resolve()),
        "source": source,
    }
    thread = memory.get_thread(result["continuation_offer"]["continuation_id"])
    expected_binding = (
        {"mode": "scratch"}
        if path is None
        else {
            "mode": "directory",
            "launch_path": str(path),
            "canonical_path": str(path.resolve()),
        }
    )
    assert thread.initial_context["clink_working_directory"] == expected_binding
    if path is None:
        assert captured["marker"] is None
        assert spawned.cwd in runtime.cleaned and not Path(spawned.cwd).exists()
        assert Path(captured["cwd"]).name.startswith("pal-clink-")
    else:
        assert spawned.cwd == str(path)
        assert os.path.samefile(captured["cwd"], path)
        assert captured["marker"] == (path / "project.marker").read_text(encoding="utf-8")
        assert path.is_dir() and spawned.cwd not in runtime.cleaned
    return captured


@pytest.mark.parametrize("vendor", VENDORS)
@pytest.mark.parametrize("source", ["request", "configuration", "scratch"])
async def test_real_adapters_keep_launch_spelling_history_and_current_edit_controls(runtime, projects, vendor, source):
    a, b, configured, alias = projects
    chosen = None if source == "scratch" else alias
    client = runtime.configure(
        vendor, working_dir=str(alias if source == "configuration" else configured) if chosen else None
    )
    snapshot = client.model_dump()
    parent_cwd = Path.cwd()
    fields = {"role": "planner", "allow_edits": True}
    if source == "request":
        fields["working_directory_absolute_path"] = f"{alias}/./"
    if vendor == "claude":
        fields["editable_paths"] = [str(alias / "project.marker")]
    first = await invoke(runtime, vendor, "first", **fields)
    first_capture = assert_observation(runtime, vendor, "first", first, chosen, source)
    thread_id = first["continuation_offer"]["continuation_id"]
    second = await invoke(runtime, vendor, "followup", continuation_id=thread_id)
    second_capture = assert_observation(runtime, vendor, "followup", second, chosen, "conversation")
    repeat = {"working_directory_absolute_path": str(a)} if chosen else {}
    third = await invoke(runtime, vendor, "repeat", continuation_id=thread_id, allow_edits=True, **repeat)
    third_capture = assert_observation(runtime, vendor, "repeat", third, chosen, "conversation")

    assert second_capture["prompt"].count("CURRENT_first") == 1
    assert first["content"] in second_capture["prompt"]
    assert (
        second_capture["prompt"].index("CURRENT_first")
        < second_capture["prompt"].index(first["content"])
        < second_capture["prompt"].index("CURRENT_followup")
    )
    assert "CURRENT_followup" in third_capture["prompt"]
    assert second["content"] in third_capture["prompt"]
    assert second["metadata"]["history"]["retained_messages"] == 2
    assert third["metadata"]["history"]["retained_messages"] == 4
    for captured, edits in ((first_capture, True), (second_capture, False), (third_capture, True)):
        args = captured["argv"]
        assert args.count(EDIT_FLAGS[vendor]) == int(edits)
        assert ("=== EXECUTION POLICY ===" in captured["prompt"]) is (not edits)
        assert args[args.index("--fixture-role") + 1] == "planner"
        for argument in client.internal_args + client.config_args + (client.edit_args if edits else client.safe_args):
            assert argument in args
        if vendor == "codex":
            assert ("--skip-git-repo-check" in args) is (chosen is None)
        elif vendor == "agy":
            assert args[:4] == ["--input-format", "stream-json", "--output-format", "stream-json"]
            assert args.count("--mode") == int(edits)
            if edits:
                assert args[-2:] == ["--mode", "accept-edits"]
            assert json.loads(captured["stdin"]) == {"event": "user", "message": {"content": captured["prompt"]}}
        elif vendor == "claude":
            assert args[args.index("--permission-mode") + 1] == ("acceptEdits" if edits else "default")
            prompt = client.get_role("planner").prompt_path.read_text(encoding="utf-8")
            assert args[args.index("--append-system-prompt") + 1] == prompt
    if vendor == "claude":
        assert f"Edit({alias / 'project.marker'})" in first_capture["argv"]
        assert f"Write({alias / 'project.marker'})" in first_capture["argv"]
        assert f"Edit({a / 'project.marker'})" not in first_capture["argv"]
        assert "--allowedTools" not in second_capture["argv"] + third_capture["argv"]
    if chosen is None:
        assert len({first_capture["cwd"], second_capture["cwd"], third_capture["cwd"]}) == 3
    assert client.model_dump() == snapshot
    assert Path.cwd() == parent_cwd
    assert all(path.is_dir() for path in (a, b, configured, alias))


@asynccontextmanager
async def child_gate():
    connections = asyncio.Queue()
    peers = []

    def accept(reader, writer):
        peers.append(writer)
        connections.put_nowait((reader, writer))

    listener = await asyncio.start_server(accept, "127.0.0.1", 0)
    try:
        yield SimpleNamespace(port=listener.sockets[0].getsockname()[1], connections=connections)
    finally:
        listener.close()
        for peer in peers:
            peer.close()
        for peer in peers:
            await asyncio.wait_for(peer.wait_closed(), 5)
        await asyncio.wait_for(listener.wait_closed(), 5)


async def receive_child(gate):
    reader, writer = await asyncio.wait_for(gate.connections.get(), 5)
    assert await asyncio.wait_for(reader.readexactly(1), 5) == b"R"
    return writer


@pytest.mark.parametrize("vendor", VENDORS)
@pytest.mark.parametrize("configured_fallback", [False, True])
@pytest.mark.parametrize("bound", [False, True], ids=["new-requests", "bound-threads"])
async def test_same_client_overlaps_projects_and_fallback_without_mutation(
    runtime, projects, vendor, configured_fallback, bound
):
    _, b, configured, alias = projects
    fallback = configured if configured_fallback else None
    client = runtime.configure(vendor, working_dir=str(fallback) if fallback else None)
    choices = (alias, b, fallback)
    parent_cwd = Path.cwd()
    async with child_gate() as gate:
        client.executable[-1] = str(gate.port)
        client.env["CLINK_TEST_SCENARIO"] = "held-directory"
        snapshot = client.model_dump()
        arguments = []
        for path in choices:
            if bound:
                binding = (
                    {"mode": "scratch"}
                    if path is None
                    else {
                        "mode": "directory",
                        "launch_path": str(path),
                        "canonical_path": str(path.resolve()),
                    }
                )
                thread_id = memory.create_thread("clink", {"clink_working_directory": binding})
                arguments.append({"continuation_id": thread_id})
            else:
                arguments.append({"working_directory_absolute_path": str(path)} if path in (alias, b) else {})
        tasks = [
            asyncio.create_task(invoke(runtime, vendor, f"parallel_{i}", **fields))
            for i, fields in enumerate(arguments)
        ]
        try:
            writers = [await receive_child(gate) for _ in tasks]
            assert all(not task.done() for task in tasks)
            assert len(runtime.spawns) == 3
            for i, path in enumerate(choices):
                captured = runtime.capture(vendor, f"parallel_{i}")
                spawned = runtime.spawns[captured["pid"]]
                assert spawned.process.returncode is None
                assert os.path.samefile(captured["cwd"], spawned.cwd)
                if path is not None:
                    assert spawned.cwd == str(path) and os.path.samefile(captured["cwd"], path)
                else:
                    assert Path(spawned.cwd).is_dir()
            assert not runtime.cleaned
            assert client.model_dump() == snapshot and Path.cwd() == parent_cwd
            for writer in writers:
                writer.write(b"G")
                await writer.drain()
            results = await asyncio.wait_for(asyncio.gather(*tasks), 10)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        for i, (result, path) in enumerate(zip(results, choices)):
            source = "conversation" if bound else "request" if i < 2 else "configuration" if path else "scratch"
            assert_observation(runtime, vendor, f"parallel_{i}", result, path, source)
        assert client.model_dump() == snapshot and Path.cwd() == parent_cwd
        assert len({result["continuation_offer"]["continuation_id"] for result in results}) == 3


@pytest.mark.parametrize("vendor", VENDORS)
@pytest.mark.parametrize("selected", [False, True], ids=["scratch", "project"])
@pytest.mark.parametrize("outcome", ["failure", "timeout", "cancel"])
async def test_real_child_failure_timeout_and_cancel_preserve_state_and_cleanup(
    runtime, projects, vendor, selected, outcome
):
    project = projects[0] if selected else None
    client = runtime.registry.get_client(vendor)
    fields = {"working_directory_absolute_path": str(project)} if project else {}
    thread_id = memory.create_thread("clink", {"clink_working_directory": {"mode": "scratch"}})
    memory.add_turn(thread_id, "user", "Earlier input")
    memory.add_turn(thread_id, "assistant", "Earlier reply")
    snapshot = runtime.storage._store.copy()
    async with child_gate() as gate:
        client.executable[-1] = str(gate.port)
        client.env["CLINK_TEST_SCENARIO"] = "failure" if outcome == "failure" else "held-directory"
        client.timeout_seconds = 2 if outcome == "timeout" else 10
        task = asyncio.create_task(invoke(runtime, vendor, "failed", continuation_id=thread_id, **fields))
        try:
            if outcome != "failure":
                await receive_child(gate)
                captured = runtime.capture(vendor, "failed")
                assert Path(runtime.spawns[captured["pid"]].cwd).is_dir()
                if outcome == "cancel":
                    task.cancel()
            expected_error = asyncio.CancelledError if outcome == "cancel" else ToolExecutionError
            with pytest.raises(expected_error) as error:
                await asyncio.wait_for(task, 5)
            if outcome != "cancel":
                response = json.loads(error.value.payload)
                assert response["continuation_offer"] is None
                assert response["metadata"]["return_code"] == (7 if outcome == "failure" else None)
                assert response["metadata"]["working_directory"]["launch_path"] == (str(project) if project else None)
                assert ("timed out" in response["content"]) is (outcome == "timeout")
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    captured = runtime.capture(vendor, "failed")
    spawned = runtime.spawns[captured["pid"]]
    assert spawned.process.returncode is not None
    assert Path(spawned.cwd).exists() is selected
    assert (spawned.cwd in runtime.cleaned) is (not selected)
    assert runtime.storage._store == snapshot
    assert not coordination._entries
    assert len(runtime.spawns) == 1


async def test_codex_operator_skip_flag_reaches_selected_real_child(runtime, projects):
    alias = projects[-1]
    configured_args = runtime.registry.get_client("codex").config_args + ["--skip-git-repo-check"]
    client = runtime.configure("codex", additional_args=configured_args)
    snapshot = client.model_dump()
    result = await invoke(runtime, "codex", "operator_skip", working_directory_absolute_path=str(alias))
    captured = assert_observation(runtime, "codex", "operator_skip", result, alias, "request")
    assert captured["argv"].count("--skip-git-repo-check") == 1
    assert client.model_dump() == snapshot


@pytest.mark.parametrize("vendor", VENDORS)
async def test_shared_fixture_wire_formats_preserve_default_history_and_duplicate_checks(tmp_path, vendor):
    async def run(prompt):
        wire = json.dumps({"event": "user", "message": {"content": prompt}}) + "\n" if vendor == "agy" else prompt
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(FAKE.resolve()),
            vendor,
            str(tmp_path),
            "0",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(tmp_path),
            env={},
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(wire.encode()), 5)
        except BaseException:
            await BaseCLIAgent._kill_and_reap(process)
            raise
        return process.returncode, stdout.decode(), stderr.decode()

    parser = get_parser(INTERNAL_DEFAULTS[vendor].parser)
    first = f"CLINK_TEST_STAGE=first FIRST_USER_MARKER_{vendor}"
    code, stdout, stderr = await run(first)
    assert code == 0, stderr
    captured = json.loads((tmp_path / "first.json").read_text(encoding="utf-8"))
    assert "marker" not in captured
    assert os.path.samefile(captured["cwd"], tmp_path)
    reply = parser.parse(stdout, stderr).content
    assert reply == f"FIRST_ASSISTANT_MARKER_{vendor}"
    code, stdout, stderr = await run(f"{first}\n{reply}\nCLINK_TEST_STAGE=followup")
    assert code == 0, stderr
    assert parser.parse(stdout, stderr).content == f"CONTEXT_OK_{vendor}_followup"
    code, _, stderr = await run(first)
    assert code != 0 and "FileExistsError" in stderr
    code, _, stderr = await run("CLINK_TEST_STAGE=missing_history")
    assert code != 0 and "AssertionError" in stderr
