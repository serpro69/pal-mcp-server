"""Scenario session ownership and selection behavior without paid providers."""

import asyncio
import json
import logging
import os
import re
import select
import signal
import subprocess
import sys
from contextlib import asynccontextmanager

import pytest
from mcp.types import CallToolResult, TextContent

import communication_simulator_test as runner
from providers.configuration import provider_configurations
from simulator_tests import server_session
from simulator_tests import test_clink_cli_only as live_clink
from simulator_tests.base_test import BaseSimulatorTest
from simulator_tests.conversation_base_test import ConversationBaseTest
from simulator_tests.test_codereview_validation import CodeReviewValidationTest
from simulator_tests.test_debug_certain_confidence import DebugCertainConfidenceTest


@pytest.fixture
def fake_transport(monkeypatch):
    events = []

    @asynccontextmanager
    async def transport(parameters):
        events.append(("open", parameters, asyncio.current_task()))
        try:
            yield object(), object()
        finally:
            events.append(("close", asyncio.current_task()))

    class Client:
        def __init__(self, *args, **kwargs):
            self.count = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            events.append(("session_close",))

        async def initialize(self):
            events.append(("initialize",))

        async def call_tool(self, name, arguments):
            self.count += 1
            return CallToolResult(content=[TextContent(type="text", text=json.dumps({"count": self.count}))])

    monkeypatch.setattr(server_session, "stdio_client", transport)
    monkeypatch.setattr(server_session, "ClientSession", Client)
    return events, Client


@pytest.mark.parametrize(
    "base", [BaseSimulatorTest, ConversationBaseTest, CodeReviewValidationTest, DebugCertainConfidenceTest]
)
@pytest.mark.parametrize("fail", [False, True])
def test_scenario_owns_one_session_and_cleans_failure(fake_transport, base, fail):
    events, _ = fake_transport

    class Scenario(base):
        def run_test(self):
            call = self.call_mcp_tool
            assert json.loads(call("chat", {})[0])["count"] == 1
            self.cleanup_test_files()  # Some legacy scenarios clean files mid-run.
            assert json.loads(call("chat", {})[0])["count"] == 2
            if fail:
                raise RuntimeError("scenario failed")
            return True

    test = Scenario()
    if fail:
        with pytest.raises(RuntimeError, match="scenario failed"):
            test.run()
    else:
        assert test.run()
    assert [e[0] for e in events] == ["open", "initialize", "session_close", "close"]
    assert events[0][1].command == sys.executable
    assert events[0][2] is events[-1][1]  # AnyIO scopes must exit in their owning task.
    assert test._session is None


def test_failed_initialization_closes_transport(fake_transport, monkeypatch):
    events, client = fake_transport

    async def fail(self):
        raise RuntimeError("handshake failed")

    monkeypatch.setattr(client, "initialize", fail)
    with pytest.raises(RuntimeError, match="handshake failed"):
        with server_session.SimulatorSession():
            pass
    assert events[-1][0] == "close"


def test_tool_error_is_preserved(fake_transport, monkeypatch):
    _, client = fake_transport

    async def failed(self, name, args):
        return CallToolResult(isError=True, content=[TextContent(type="text", text='{"status":"error"}')])

    monkeypatch.setattr(client, "call_tool", failed)
    test = BaseSimulatorTest()
    try:
        assert test.call_mcp_tool("chat", {}) == ('{"status":"error"}', None)
        assert test.last_result.isError
    finally:
        test.close_session()


@pytest.mark.parametrize(
    "args",
    [["--quick", "--ci"], ["--individual", "x", "--tests", "y"], ["--list-tests", "--quick"], ["--ci", "--tests", "x"]],
)
def test_selection_modes_exclusive(args):
    with pytest.raises(SystemExit) as caught:
        runner.parse_arguments(args)
    assert caught.value.code == 2


def test_selected_failure_still_runs_rest_and_prints_summary(monkeypatch, caplog):
    sim = runner.CommunicationSimulator(selected_tests=["session_errors", "workflow_session"])
    called = []
    monkeypatch.setattr(sim, "setup_test_environment", lambda: True)
    sim.available_tests = {
        "session_errors": lambda: False,
        "workflow_session": lambda: called.append("second") or True,
    }
    with caplog.at_level(logging.INFO):
        assert runner.run_test_suite(sim) == 1
    assert called == ["second"]
    assert sim.test_results == {"session_errors": False, "workflow_session": True}
    assert "1/2 tests passed" in caplog.text


def test_ci_selection_is_explicit_and_model_configurable(monkeypatch):
    monkeypatch.setenv("SIMULATOR_MODEL", "local-llama")
    sim = runner.CommunicationSimulator(ci_mode=True)
    assert set(sim.test_results) == {"session_continuation", "workflow_session", "session_errors"}
    assert all(sim.test_registry[name].provider_agnostic for name in sim.test_results)
    assert BaseSimulatorTest().model == "local-llama"


def test_individual_failure_summary_and_cleanup_even_with_keep_logs(monkeypatch, caplog):
    sim = runner.CommunicationSimulator(keep_logs=True)
    closed = []
    monkeypatch.setattr(sim, "cleanup", lambda: closed.append(True))
    monkeypatch.setattr(sim, "setup_test_environment", lambda: True)

    def fail():
        raise RuntimeError("scenario failure")

    sim.available_tests["session_errors"] = fail
    with caplog.at_level(logging.INFO):
        assert runner.run_individual_test(sim, "session_errors") == 1
    assert sim.test_results == {"session_errors": False}
    assert "0/1 tests passed" in caplog.text and closed == [True]


def test_real_stdio_keeps_planner_state(monkeypatch, tmp_path):
    for declaration in provider_configurations().values():
        for name in declaration.environment_names():
            monkeypatch.setenv(name, "")
    monkeypatch.setenv("PAL_MCP_FORCE_ENV_OVERRIDE", "false")
    monkeypatch.setenv("PAL_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PAL_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("PAL_TRANSCRIPTS", "false")
    monkeypatch.setenv("DISABLED_TOOLS", "")
    with server_session.SimulatorSession(timeout=15) as session:
        first = session.call_tool(
            "planner",
            {
                "step": "Plan test",
                "step_number": 1,
                "total_steps": 2,
                "next_step_required": True,
            },
        )
        assert not first.isError
        thread = BaseSimulatorTest()._extract_continuation_id(first.content[0].text)
        assert thread
        second = session.call_tool(
            "planner",
            {
                "step": "Finish plan",
                "step_number": 2,
                "total_steps": 2,
                "next_step_required": False,
                "continuation_id": thread,
            },
        )
        assert not second.isError
        assert BaseSimulatorTest()._extract_continuation_id(second.content[0].text) == thread


@pytest.mark.skipif(os.name == "nt", reason="SIGINT child probe is POSIX-specific")
@pytest.mark.parametrize("phase", ["initialize", "call"])
def test_interrupt_closes_pending_portal_and_transport(phase):
    script = """
import asyncio
from contextlib import asynccontextmanager
from simulator_tests import server_session as module
from simulator_tests.base_test import BaseSimulatorTest

@asynccontextmanager
async def transport(*args):
    try:
        yield None, None
    finally:
        print("CLOSED", flush=True)

class Client:
    def __init__(self, *args, **kwargs): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def initialize(self):
        if PHASE == "initialize":
            print("READY", flush=True)
            await asyncio.Future()
    async def call_tool(self, *args):
        print("READY", flush=True)
        await asyncio.Future()

module.stdio_client = transport
module.ClientSession = Client
class Scenario(BaseSimulatorTest):
    def run_test(self): self.call_mcp_tool("chat", {})
try:
    Scenario().run()
except KeyboardInterrupt:
    print("INTERRUPTED", flush=True)
""".replace("PHASE", repr(phase))
    process = subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        assert select.select([process.stdout], [], [], 10)[0], "child did not reach pending operation"
        assert process.stdout.readline().strip() == "READY"
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
        assert "CLOSED" in stdout and "INTERRUPTED" in stdout
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)


@pytest.fixture
def live_scenarios(monkeypatch):
    """Selection is tested without constructing a live scenario or opening a CLI."""
    import simulator_tests

    events = []

    class Live:
        provider_agnostic = True  # A live selector must still keep this out of --ci.
        live_selector_env = "PAL_CLINK_LIVE_TEST"
        live_selector_values = ("codex", "claude", "gemini")
        test_description = "Synthetic live scenario"

        def __init__(self, **kwargs):
            events.append("construct")

        def run(self):
            events.append("run")
            return True

    class Offline:
        provider_agnostic = True
        test_description = "Synthetic offline scenario"

        def __init__(self, **kwargs):
            pass

        def run(self):
            events.append("offline")
            return True

    monkeypatch.setattr(simulator_tests, "TEST_REGISTRY", {"live": Live, "offline": Offline})
    monkeypatch.delenv("PAL_CLINK_LIVE_TEST", raising=False)
    return events, Live


@pytest.mark.parametrize("individual", [False, True])
@pytest.mark.parametrize("selector", [None, "", "invalid", " claude", "CLAUDE", "codex", "claude", "gemini"])
def test_live_selection_outcomes_before_execution(live_scenarios, monkeypatch, caplog, individual, selector):
    events, _ = live_scenarios
    if selector is not None:
        monkeypatch.setenv("PAL_CLINK_LIVE_TEST", selector)
    sim = runner.CommunicationSimulator(selected_tests=["live"])
    monkeypatch.setattr(sim, "setup_test_environment", lambda: events.append("setup") or True)
    monkeypatch.setattr(sim, "cleanup", lambda: events.append("cleanup"))
    with caplog.at_level(logging.INFO):
        code = runner.run_individual_test(sim, "live") if individual else runner.run_test_suite(sim)
    if not selector:
        assert code == 0
        assert sim.test_results == {}
        assert "PAL_CLINK_LIVE_TEST" in sim.skipped_tests["live"]
        assert events == ["cleanup"]
        assert "SKIPPED" in caplog.text and "No tests executed" in caplog.text
        assert "PASSED" not in caplog.text and "SUCCESS" not in caplog.text
        assert "tests passed" not in caplog.text
        assert "Passed: 0; failed: 0; skipped: 1" in caplog.text
    elif selector in ("codex", "claude", "gemini"):
        assert code == 0 and sim.test_results == {"live": True} and not sim.skipped_tests
        assert events == ["setup", "construct", "run", "cleanup"]
        assert "Passed: 1; failed: 0; skipped: 0" in caplog.text
    else:
        assert code == 1 and sim.test_results == {"live": False} and not sim.skipped_tests
        assert events == ["cleanup"]
        assert "invalid PAL_CLINK_LIVE_TEST" in caplog.text
        assert "Passed: 0; failed: 1; skipped: 0" in caplog.text


@pytest.mark.parametrize("individual", [False, True])
@pytest.mark.parametrize("outcome", ["prerequisite", "request", "false", "interrupt"])
def test_selected_live_failures_remain_failures(live_scenarios, monkeypatch, caplog, individual, outcome):
    events, live = live_scenarios
    monkeypatch.setenv("PAL_CLINK_LIVE_TEST", "claude")

    def run(self):
        events.append("run")
        if outcome == "interrupt":
            raise KeyboardInterrupt
        if outcome == "false":
            return False
        raise RuntimeError(f"{outcome} failed")

    monkeypatch.setattr(live, "run", run)
    sim = runner.CommunicationSimulator(selected_tests=["live"])
    monkeypatch.setattr(sim, "setup_test_environment", lambda: True)
    monkeypatch.setattr(sim, "cleanup", lambda: events.append("cleanup"))
    with caplog.at_level(logging.INFO):
        code = runner.run_individual_test(sim, "live") if individual else runner.run_test_suite(sim)
    assert code == (130 if outcome == "interrupt" else 1)
    assert sim.test_results == {"live": None if outcome == "interrupt" else False}
    assert not sim.skipped_tests
    assert "run" in events and events[-1] == "cleanup"
    assert "PASSED" not in caplog.text and "SKIPPED" not in caplog.text


@pytest.mark.parametrize("offline_passes", [False, True])
def test_mixed_live_skip_does_not_change_executed_result(live_scenarios, monkeypatch, caplog, offline_passes):
    events, _ = live_scenarios
    sim = runner.CommunicationSimulator()
    monkeypatch.setattr(sim, "setup_test_environment", lambda: True)
    sim.available_tests["offline"] = lambda: offline_passes
    with caplog.at_level(logging.INFO):
        assert runner.run_test_suite(sim) == (0 if offline_passes else 1)
    assert sim.test_results == {"offline": offline_passes}
    assert set(sim.skipped_tests) == {"live"}
    assert events == []
    assert f"{int(offline_passes)}/1 tests passed" in caplog.text
    assert "skipped: 1" in caplog.text


@pytest.mark.parametrize("ci_mode", [False, True])
def test_non_candidate_live_selectors_are_ignored(live_scenarios, monkeypatch, ci_mode):
    events, _ = live_scenarios
    monkeypatch.setenv("PAL_CLINK_LIVE_TEST", "invalid")
    sim = runner.CommunicationSimulator(ci_mode=ci_mode, selected_tests=None if ci_mode else ["offline"])
    monkeypatch.setattr(sim, "setup_test_environment", lambda: True)
    assert runner.run_test_suite(sim) == 0
    assert sim.test_results == {"offline": True} and sim.skipped_tests == {}
    assert events == ["offline"]


def test_simulator_reuse_resets_skips(live_scenarios, monkeypatch):
    sim = runner.CommunicationSimulator(selected_tests=["live"])
    monkeypatch.setattr(sim, "setup_test_environment", lambda: True)
    assert runner.run_test_suite(sim) == 0
    monkeypatch.setenv("PAL_CLINK_LIVE_TEST", "codex")
    assert runner.run_individual_test(sim, "live") == 0
    assert sim.test_results == {"live": True} and sim.skipped_tests == {}


@pytest.mark.parametrize("selector", [None, "invalid", "codex", "claude", "gemini"])
def test_pytest_live_gate_never_skips_opted_in_failure(monkeypatch, selector):
    from tests import test_clink_integration as integration

    monkeypatch.delenv(live_clink.LIVE_SELECTOR_ENV, raising=False)
    if selector is not None:
        monkeypatch.setenv(live_clink.LIVE_SELECTOR_ENV, selector)
    calls = []

    def fail(vendor, logger):
        calls.append(vendor)
        raise RuntimeError("selected request failed")

    monkeypatch.setattr(integration, "run_live_clink", fail)
    if selector is None:
        with pytest.raises(pytest.skip.Exception, match="PAL_CLINK_LIVE_TEST"):
            integration.test_clink_provider_free_first_request_and_followup()
        assert not calls
    elif selector == "invalid":
        with pytest.raises(ValueError, match="Invalid PAL_CLINK_LIVE_TEST"):
            integration.test_clink_provider_free_first_request_and_followup()
        assert not calls
    else:
        with pytest.raises(RuntimeError, match="selected request failed"):
            integration.test_clink_provider_free_first_request_and_followup()
        assert calls == [selector]


def test_explicit_session_parameters_preserve_owned_transport(fake_transport, tmp_path):
    events, _ = fake_transport
    parameters = server_session.StdioServerParameters(command="isolated-python", args=["launcher"], cwd=str(tmp_path))
    with server_session.SimulatorSession(parameters=parameters) as session:
        assert not session.call_tool("clink", {}).isError
    assert events[0][1] is parameters
    assert events[0][2] is events[-1][1]


@pytest.mark.parametrize("vendor", live_clink.LIVE_CLI_NAMES)
def test_live_environment_excludes_provider_settings_preserves_cli_auth(monkeypatch, tmp_path, vendor):
    for declaration in provider_configurations().values():
        for name in declaration.environment_names():
            monkeypatch.setenv(name, "PROVIDER_MUST_NOT_LEAK")
    monkeypatch.setenv("CLI_CLIENTS_CONFIG_PATH", "PARENT_MANIFEST_MUST_NOT_LEAK")
    monkeypatch.setenv("PAL_MCP_FORCE_ENV_OVERRIDE", "true")
    monkeypatch.setenv("CODEX_HOME", "saved-codex-location")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "saved-claude-location")
    monkeypatch.setenv("GOOGLE_API_KEY", "selected-gemini-auth")
    monkeypatch.setenv("UNRELATED_SECRET", "MUST_NOT_LEAK")
    env = live_clink.live_server_environment(tmp_path, vendor)
    assert not any("MUST_NOT_LEAK" in value for value in env.values())
    assert env["PAL_SKIP_DOTENV"] == "true" and "PAL_MCP_FORCE_ENV_OVERRIDE" not in env
    assert env["CLI_CLIENTS_CONFIG_PATH"] == str(tmp_path / "clients")
    assert env.get("HOME") == os.environ.get("HOME")
    assert ("CODEX_HOME" in env) is (vendor == "codex")
    assert ("CLAUDE_CONFIG_DIR" in env) is (vendor == "claude")
    assert ("GOOGLE_API_KEY" in env) is (vendor == "gemini")


class FakeLiveSession:
    """Return realistic envelopes while checking the dependent request's inputs."""

    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "listmodels":
            value = {
                "status": "success",
                "metadata": {
                    "mode": "provider-free",
                    "configured_providers": 0,
                    "provider_setup": [{"status": "absent"}],
                    "cli_clients": [{"name": "claude", "executable_found": True}],
                },
            }
        elif self.failure in {"mcp", "payload"}:
            value = {"status": "error", "content": "authentication unavailable"}
        else:
            if len(self.calls) == 2:
                label = re.search(r"Remember the label ([a-f0-9]{32})", arguments["prompt"])[1]
                self.answer = f"<CLINK_ANSWER>{label}|abcdefghijkl|4</CLINK_ANSWER>"
                assert arguments["allow_edits"] is False
            else:
                assert set(arguments) == {"prompt", "continuation_id"}
                assert "abcdefghijkl" not in arguments["prompt"]
                if self.failure == "history":
                    self.answer = "I don't have the prior answer"
            value = {
                "status": "continuation_available",
                "content": self.answer + "\n<SUMMARY>Required role summary</SUMMARY>",
                "metadata": {"history_saved": True, "history": {"retained_messages": 2}},
                "continuation_offer": {"continuation_id": "same-thread"},
            }
        return CallToolResult(
            isError=name == "clink" and self.failure == "mcp",
            content=[TextContent(type="text", text=json.dumps(value))],
        )


@pytest.mark.parametrize("failure", [None, "mcp", "payload", "history"])
def test_live_conversation_requires_success_and_both_sides_of_history(failure):
    session = FakeLiveSession(failure)
    if failure:
        with pytest.raises(AssertionError, match="request failed|lost prior input/reply"):
            live_clink.check_live_conversation(session, "claude")
    else:
        live_clink.check_live_conversation(session, "claude")
        assert len(session.calls) == 3


@pytest.mark.parametrize("failure", [None, "request", "interrupt", "version", "missing"])
def test_live_workspace_session_and_prerequisite_ownership(monkeypatch, failure):
    from pathlib import Path

    events = []
    roots = []
    monkeypatch.setattr(live_clink.shutil, "which", lambda *a, **k: None if failure == "missing" else sys.executable)

    def version(command, *, cwd, env, **kwargs):
        roots.append(cwd)
        assert command == [sys.executable, "--version"] and env["PAL_SKIP_DOTENV"] == "true"
        if failure == "version":
            raise subprocess.TimeoutExpired(command, 15)
        return subprocess.CompletedProcess(command, 0, stdout="test-cli 1.0")

    monkeypatch.setattr(live_clink.subprocess, "run", version)

    class Session:
        def __init__(self, *, timeout, parameters):
            self.root = Path(parameters.cwd)
            manifest = json.loads((self.root / "clients" / "claude.json").read_text())
            assert manifest["safe_args"] == ["--permission-mode", "default"]
            assert "working_dir" not in manifest  # Retain runner-owned scratch cwd.
            assert parameters.env["DEFAULT_MODEL"] == "auto"
            assert parameters.args[-1] == str(self.root)

        def __enter__(self):
            events.append("open")
            return self

        def __exit__(self, *exc):
            (self.root / "server-stopped").touch()
            events.append("close")

    def conversation(session, vendor):
        assert vendor == "claude"
        if failure == "interrupt":
            raise KeyboardInterrupt
        if failure == "request":
            raise RuntimeError("request failed")

    monkeypatch.setattr(live_clink, "SimulatorSession", Session)
    monkeypatch.setattr(live_clink, "check_live_conversation", conversation)
    error = {
        "request": RuntimeError,
        "interrupt": KeyboardInterrupt,
        "version": subprocess.TimeoutExpired,
        "missing": RuntimeError,
    }
    if failure:
        with pytest.raises(error[failure]):
            live_clink.run_live_clink("claude", logging.getLogger(__name__))
    else:
        live_clink.run_live_clink("claude", logging.getLogger(__name__))
    assert all(not root.exists() for root in roots)
    assert events == ([] if failure in {"version", "missing"} else ["open", "close"])
