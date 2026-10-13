"""Child environments and working directories use only synthetic credentials."""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from clink.agents.base import BaseCLIAgent, CLIAgentError
from clink.constants import CONFIG_DIR, INTERNAL_DEFAULTS, SHARED_ENV_PASSTHROUGH
from clink.models import CLIClientConfig, OutputCaptureConfig, ResolvedCLIClient, ResolvedCLIRole
from clink.registry import ClinkRegistry


def client(name="gemini", **changes):
    role = ResolvedCLIRole(name="default", prompt_path=Path("systemprompts/clink/default.txt").resolve())
    values = {
        "name": name,
        "executable": [sys.executable],
        "working_dir": None,
        "timeout_seconds": 30,
        "parser": "gemini_json",
        "roles": {"default": role},
        "env_passthrough": list(INTERNAL_DEFAULTS[name].env_passthrough),
    }
    values.update(changes)
    return ResolvedCLIClient(**values)


@pytest.mark.parametrize(
    "name,own", [("codex", "CODEX_API_KEY"), ("claude", "ANTHROPIC_API_KEY"), ("gemini", "GEMINI_API_KEY")]
)
def test_vendor_environment_and_shared_runtime(name, own):
    synthetic = dict.fromkeys(SHARED_ENV_PASSTHROUGH, "synthetic-runtime")
    vendor_keys = (
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "XAI_API_KEY",
        "DATABASE_PASSWORD",
    )
    synthetic.update(dict.fromkeys(vendor_keys, "synthetic-auth"))
    with patch.dict(os.environ, synthetic, clear=True):
        env = BaseCLIAgent(client(name))._build_environment()
    assert env[own] == "synthetic-auth"
    assert all(env[key.upper() if os.name == "nt" else key] == "synthetic-runtime" for key in SHARED_ENV_PASSTHROUGH)
    for key in vendor_keys:
        if key not in INTERNAL_DEFAULTS[name].env_passthrough:
            assert key not in env


def test_registry_passthrough_and_override_precedence():
    raw = CLIClientConfig(
        name="codex",
        command="codex",
        env_passthrough=["EXTRA_AUTH", "CODEX_API_KEY"],
        env={"EXTRA_AUTH": "configured", "EXPLICIT_ONLY": "operator"},
    )
    registry = ClinkRegistry.__new__(ClinkRegistry)
    resolved = registry._resolve_config(raw, source_path=CONFIG_DIR / "codex.json")
    with patch.dict(
        os.environ, {"EXTRA_AUTH": "inherited", "CODEX_API_KEY": "synthetic", "OPENAI_API_KEY": "openai"}, clear=True
    ):
        env = BaseCLIAgent(resolved)._build_environment()
    assert env == {
        "EXTRA_AUTH": "configured",
        "EXPLICIT_ONLY": "operator",
        "CODEX_API_KEY": "synthetic",
        "OPENAI_API_KEY": "openai",
    }
    assert resolved.env_passthrough.count("CODEX_API_KEY") == 1


def test_windows_case_insensitive_runtime_and_overrides(monkeypatch):
    configured = client(env={"path": "operator-path"})
    monkeypatch.setattr(
        "clink.availability.os",
        SimpleNamespace(
            name="nt",
            environ={
                "Path": "inherited",
                "SystemRoot": "windows",
                "GEMINI_API_KEY": "synthetic",
                "OPENAI_API_KEY": "unrelated",
            },
        ),
    )
    env = BaseCLIAgent(configured)._build_environment()
    assert env["PATH"] == "operator-path"
    assert env["SYSTEMROOT"] == "windows"
    assert "OPENAI_API_KEY" not in env
    assert sum(name.upper() == "PATH" for name in env) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("configured_cwd", [False, True])
async def test_real_child_observes_environment_and_cwd(tmp_path, monkeypatch, configured_cwd):
    script = 'import json,os; print(json.dumps({"response": json.dumps({"cwd": os.getcwd(), "own": os.getenv("GEMINI_API_KEY"), "other": os.getenv("OPENAI_API_KEY")})}))'
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-own")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-other")
    cfg = client(executable=[sys.executable, "-c", script], working_dir=tmp_path if configured_cwd else None)
    result = await BaseCLIAgent(cfg).run(role=cfg.get_role(None), prompt="test", files=[], images=[])
    observed = json.loads(result.parsed.content)
    assert observed["own"] == "synthetic-own"
    assert observed["other"] is None
    cwd = Path(observed["cwd"])
    if configured_cwd:
        assert cwd == tmp_path
        assert cwd.exists()
    else:
        assert cwd != Path.cwd()
        assert cwd.name.startswith("pal-clink-")
        assert not cwd.exists()


class ControlledProcess:
    def __init__(self, cwd, outcome="success"):
        self.cwd = Path(cwd)
        self.outcome = outcome
        self.returncode = None
        self.started = asyncio.Event()
        self.reaping = asyncio.Event()
        self.release_reap = asyncio.Event()
        self.killed = False
        self.reaped = False
        self._transport = SimpleNamespace(close=Mock())

    async def communicate(self, data=None):
        assert self.cwd.is_dir()
        if data is None:
            self.reaping.set()
            await self.release_reap.wait()
            assert self.cwd.is_dir()
            self.reaped = True
            return b"", b""
        self.started.set()
        if self.outcome == "cancel":
            await asyncio.Event().wait()
        if self.outcome == "timeout":
            raise asyncio.TimeoutError
        if self.outcome == "io_error":
            raise OSError("synthetic communication failure")
        self.returncode = 1 if self.outcome == "failure" else 0
        return b"invalid" if self.outcome in ("failure", "parse_error") else b'{"response":"ok"}', b""

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        assert self.killed
        self.reaped = True
        return self.returncode


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "failure", "parse_error", "timeout", "io_error", "cancel"])
async def test_scratch_cleanup_after_reaping_and_edit_forwarding(monkeypatch, outcome):
    created = []
    command = Mock(return_value=[sys.executable])
    spawned = asyncio.Event()

    async def spawn(*args, **kwargs):
        process = ControlledProcess(kwargs["cwd"], outcome)
        if outcome != "cancel":
            process.release_reap.set()
        created.append(process)
        spawned.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    cfg = client()
    agent = BaseCLIAgent(cfg)
    monkeypatch.setattr(agent, "_build_command", command)
    paths = ["/synthetic/editable"]
    task = asyncio.create_task(
        agent.run(
            role=cfg.get_role(None),
            prompt="test",
            files=[],
            images=[],
            system_prompt="system",
            allow_edits=True,
            editable_paths=paths,
        )
    )
    if outcome == "cancel":
        await asyncio.wait_for(spawned.wait(), 2)
        await asyncio.wait_for(created[0].started.wait(), 2)
        task.cancel()
        await asyncio.wait_for(created[0].reaping.wait(), 2)
        task.cancel()
        assert created[0].cwd.exists()
        created[0].release_reap.set()
    if outcome == "success":
        assert (await task).parsed.content == "ok"
    else:
        error = asyncio.CancelledError if outcome == "cancel" else OSError if outcome == "io_error" else CLIAgentError
        with pytest.raises(error):
            await task
    process = created[0]
    assert not process.cwd.exists()
    if outcome in ("timeout", "io_error", "cancel"):
        assert process.killed and process.reaped
    assert command.call_args.kwargs["editable_paths"] is paths
    assert command.call_args.kwargs["allow_edits"] is True
    assert command.call_args.kwargs["system_prompt"] == "system"


@pytest.mark.asyncio
async def test_missing_configured_directory_never_spawns(tmp_path, monkeypatch):
    spawn = Mock(side_effect=AssertionError("must not spawn"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    cfg = client(working_dir=tmp_path / "missing")
    with pytest.raises(CLIAgentError, match="existing directory"):
        await BaseCLIAgent(cfg).run(role=cfg.get_role(None), prompt="x", files=[], images=[])
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_executable_lookup_uses_configured_child_path(monkeypatch):
    cfg = client(env={"PATH": "/synthetic/operator/bin"})
    lookup = Mock(return_value=None)
    monkeypatch.setattr("clink.availability.shutil.which", lookup)
    with pytest.raises(CLIAgentError, match="not found in PATH"):
        await BaseCLIAgent(cfg).run(role=cfg.get_role(None), prompt="x", files=[], images=[])
    lookup.assert_called_once_with(sys.executable, path="/synthetic/operator/bin")


@pytest.mark.asyncio
async def test_concurrent_calls_have_distinct_private_scratch_directories(monkeypatch):
    directories = []
    both_started = asyncio.Event()

    async def spawn(*args, **kwargs):
        directory = Path(kwargs["cwd"])
        directories.append(directory)
        if os.name == "posix":
            assert directory.stat().st_mode & 0o777 == 0o700
        if len(directories) == 2:
            both_started.set()

        class Process:
            returncode = 0

            async def communicate(self, data):
                await both_started.wait()
                return b'{"response":"ok"}', b""

        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    cfg = client()
    agent = BaseCLIAgent(cfg)
    await asyncio.wait_for(
        asyncio.gather(*(agent.run(role=cfg.get_role(None), prompt="x", files=[], images=[]) for _ in range(2))), 2
    )
    assert len(set(directories)) == 2
    assert all(not directory.exists() for directory in directories)


@pytest.mark.asyncio
async def test_spawn_failure_cleans_scratch_and_output(tmp_path, monkeypatch):
    observed = {}

    async def spawn(*args, **kwargs):
        observed["cwd"] = Path(kwargs["cwd"])
        observed["output"] = Path(args[-1])
        assert observed["cwd"].is_dir() and observed["output"].is_file()
        raise FileNotFoundError("synthetic missing executable")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    cfg = client(output_to_file=OutputCaptureConfig(flag_template="--output {path}"))
    with pytest.raises(CLIAgentError, match="Executable not found"):
        await BaseCLIAgent(cfg).run(role=cfg.get_role(None), prompt="x", files=[], images=[])
    assert not observed["cwd"].exists()
    assert not observed["output"].exists()


@pytest.mark.asyncio
async def test_system_prompt_context_reset_on_error(monkeypatch):
    from tools.clink import CLinkRequest, CLinkTool, _active_system_prompt

    tool = CLinkTool()
    monkeypatch.setattr(tool, "handle_prompt_file_with_fallback", Mock(side_effect=RuntimeError("synthetic")))
    token = _active_system_prompt.set("outer")
    try:
        with pytest.raises(RuntimeError, match="synthetic"):
            await tool._prepare_prompt_for_role(
                CLinkRequest(prompt="x"),
                client().get_role(None),
                system_prompt="inner",
                include_system_prompt=True,
                cli_name="gemini",
            )
        assert _active_system_prompt.get() == "outer"
    finally:
        _active_system_prompt.reset(token)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX descendant/pipe regression")
async def test_cancel_finishes_when_descendant_holds_output_pipe(monkeypatch):
    original_spawn = asyncio.create_subprocess_exec
    started = asyncio.Event()
    observed = {}
    script = (
        "import subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "print(child.pid,flush=True); time.sleep(60)"
    )

    async def spawn(*args, **kwargs):
        process = await original_spawn(*args, **kwargs)
        observed["process"] = process
        observed["cwd"] = Path(kwargs["cwd"])
        observed["descendant"] = int(await process.stdout.readline())
        started.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr("clink.agents.base.DEFAULT_CLEANUP_TIMEOUT_SECONDS", 0.05)
    cfg = client(executable=[sys.executable, "-c", script])
    task = asyncio.create_task(BaseCLIAgent(cfg).run(role=cfg.get_role(None), prompt="x", files=[], images=[]))
    try:
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=2)
        assert task in done, "cancellation must finish while the descendant still holds the pipe"
        with pytest.raises(asyncio.CancelledError):
            await task
        assert observed["process"].returncode is not None
        assert not observed["cwd"].exists()
    finally:
        if "descendant" in observed:
            try:
                os.kill(observed["descendant"], signal.SIGKILL)
            except ProcessLookupError:
                pass
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
