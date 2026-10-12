"""Scenario session ownership and selection behavior without paid providers."""

import asyncio
import json
import logging
import os
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
