"""Provider-free conversations through the real server and MCP stdio transport."""

import asyncio
import json
import os
import shlex
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from providers.configuration import provider_configurations

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
VENDORS = ("gemini", "claude", "codex")
INDEPENDENT = {"clink", "version", "listmodels", "planner", "tracer", "docgen", "challenge", "apilookup", "jules"}
EDIT_FLAGS = {"gemini": "--yolo", "claude": "acceptEdits", "codex": "--dangerously-bypass-approvals-and-sandbox"}


def payload(result, *, error=False):
    assert result.isError is error, result.content
    assert len(result.content) == 1 and result.content[0].type == "text"
    value = json.loads(result.content[0].text)
    assert (value["status"] == "error") is error, value
    return value


@dataclass
class CLIOnlyServer:
    root: Path
    vendor: str
    provider_settings: set[str]
    vendors: tuple[str, ...] = VENDORS
    client_overrides: dict[str, dict] = field(default_factory=dict)
    observe_directories: bool = False

    def capture(self, stage):
        return json.loads((self.root / "captures" / self.vendor / f"{stage}.json").read_text(encoding="utf-8"))

    def stages(self):
        return {path.stem for path in (self.root / "captures" / self.vendor).glob("*.json")}

    @asynccontextmanager
    async def connect(self, *, gate_port=0):
        manifests = self.root / "clients"
        manifests.mkdir()
        for vendor in self.vendors:
            captures = self.root / "captures" / vendor
            captures.mkdir(parents=True)
            config = json.loads((REPO / "conf" / "cli_clients" / f"{vendor}.json").read_text(encoding="utf-8"))
            config.update(self.client_overrides.get(vendor, {}))
            config["command"] = shlex.join(
                [sys.executable, str(FIXTURES / "cli_only_fake.py"), vendor, str(captures), str(gate_port)]
            )
            config["timeout_seconds"] = 20
            (manifests / f"{vendor}.json").write_text(json.dumps(config), encoding="utf-8")

        # Discovery includes the new built-in client even when it is not installed.
        # Pin this observation so the test never depends on a host agy executable.
        if "agy" not in self.vendors:
            agy = json.loads((REPO / "conf" / "cli_clients" / "agy.json").read_text(encoding="utf-8"))
            agy["command"] = shlex.quote(str(self.root / "missing-agy"))
            (manifests / "agy.json").write_text(json.dumps(agy), encoding="utf-8")

        # Do not copy os.environ: pytest and the developer's shell contain keys,
        # model overrides and paths that can silently turn this into an API test.
        # The SDK adds only its own documented platform-runtime allowlist.
        env = {
            name: os.environ[name]
            for name in ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "LANG")
            if name in os.environ
        }
        env.update(
            PAL_SKIP_DOTENV="true",
            PAL_STATE_DIR=str(self.root / "state"),
            PAL_LOG_DIR=str(self.root / "logs"),
            PAL_WORKSPACE_ROOT=str(self.root),
            PAL_TRANSCRIPTS="false",
            CLI_CLIENTS_CONFIG_PATH=str(manifests),
            DEFAULT_MODEL="auto",
            DISABLED_TOOLS="",
            LOG_LEVEL="WARNING",
        )
        if self.observe_directories:
            (self.root / "home").mkdir(exist_ok=True)
            env.update(CLINK_TEST_OBSERVE="true", HOME=str(self.root / "home"))
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(FIXTURES / "cli_only_server.py"), str(self.root)],
            cwd=str(REPO),
            env=env,
        )
        with (self.root / "stderr.log").open("w", encoding="utf-8") as errors:
            async with stdio_client(parameters, errlog=errors) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=10)) as session:
                    initialized = await session.initialize()
                    yield session, initialized
        assert (self.root / "server-stopped").exists(), "SDK teardown did not shut down the actual server"
        assert not (self.root / "forbidden-calls.txt").exists()
        isolation = json.loads((self.root / "isolation.json").read_text(encoding="utf-8"))
        assert isolation == {
            "provider_settings_present": [],
            "user_cli_config": str(self.root / "user-cli-config"),
            "pytest_loaded": False,
        }


@pytest.fixture
def cli_server(tmp_path, monkeypatch, vendor):
    # Deliberately poison the parent beyond conftest's dummy keys. A broad env
    # copy would either activate a provider or load an invalid catalog in child.
    settings = {name for declaration in provider_configurations().values() for name in declaration.environment_names()}
    for name in settings:
        monkeypatch.setenv(name, "PARENT_CONFIGURATION_MUST_NOT_LEAK")
    monkeypatch.setenv("CLI_CLIENTS_CONFIG_PATH", str(tmp_path / "wrong-clients"))
    monkeypatch.setenv("PAL_MCP_FORCE_ENV_OVERRIDE", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "PARENT_CHILD_KEY_MUST_NOT_LEAK")
    monkeypatch.setenv("GOOGLE_API_KEY", "PARENT_CHILD_KEY_MUST_NOT_LEAK")
    return CLIOnlyServer(tmp_path, vendor, settings | {"ANTHROPIC_API_KEY", "GOOGLE_API_KEY"})


@pytest.mark.parametrize("vendor", VENDORS)
async def test_stdio_cli_conversation_discovery_history_and_permissions(cli_server, vendor):
    async def scenario():
        async with cli_server.connect() as (session, initialized):
            assert "No PAL model providers are configured" in initialized.instructions
            assert "clink" in initialized.instructions and "continuation_id" in initialized.instructions
            assert {tool.name for tool in (await session.list_tools()).tools} == INDEPENDENT
            assert {prompt.name for prompt in (await session.list_prompts()).prompts} == INDEPENDENT | {"continue"}
            prompt = await session.get_prompt("clink")
            assert "clink" in prompt.messages[0].content.text.lower()
            continuation = await session.get_prompt("continue")
            assert "previous" in continuation.messages[0].content.text.lower()
            assert "continuation_id" in continuation.messages[0].content.text

            diagnostics = payload(await session.call_tool("listmodels", {}))["metadata"]
            assert diagnostics["mode"] == "provider-free" and diagnostics["configured_providers"] == 0
            assert all(provider["status"] == "absent" for provider in diagnostics["provider_setup"])
            availability = {client["name"]: client["executable_found"] for client in diagnostics["cli_clients"]}
            assert availability == {**dict.fromkeys(VENDORS, True), "agy": False}
            assert not cli_server.stages(), "Discovery invoked a CLI"
            for unavailable in ("chat", "consensus"):
                denied = payload(await session.call_tool(unavailable, {"continuation_id": str(uuid4())}), error=True)
                assert denied["metadata"]["error_type"] == "model_provider_required"
                assert denied["continuation_offer"] is None

            first_arguments = {
                "cli_name": vendor,
                "role": "planner",
                "prompt": f"CLINK_TEST_STAGE=first FIRST_USER_MARKER_{vendor}",
                "allow_edits": True,
            }
            if vendor == "claude":
                first_arguments["editable_paths"] = [str(cli_server.root)]
            first = payload(await session.call_tool("clink", first_arguments))
            assert first["content"] == f"FIRST_ASSISTANT_MARKER_{vendor}"
            thread_id = first["continuation_offer"]["continuation_id"]
            assert str(UUID(thread_id)) == thread_id
            assert first["metadata"]["history_saved"] is True
            assert first["continuation_offer"]["remaining_turns"] == 48

            followup = payload(
                await session.call_tool(
                    "clink", {"continuation_id": thread_id, "prompt": "CLINK_TEST_STAGE=followup SECOND_USER_MARKER"}
                )
            )
            assert followup["content"] == f"CONTEXT_OK_{vendor}_followup"
            assert followup["metadata"]["cli_name"] == vendor and followup["metadata"]["role"] == "planner"
            assert followup["metadata"]["history"]["retained_messages"] == 2
            assert followup["continuation_offer"]["continuation_id"] == thread_id
            assert followup["continuation_offer"]["remaining_turns"] == 46
            first_capture, second_capture = cli_server.capture("first"), cli_server.capture("followup")
            history = second_capture["prompt"]
            markers = [f"FIRST_USER_MARKER_{vendor}", first["content"], "SECOND_USER_MARKER"]
            assert all(history.count(marker) == 1 for marker in markers)
            assert [history.index(marker) for marker in markers] == sorted(history.index(marker) for marker in markers)
            assert EDIT_FLAGS[vendor] in first_capture["argv"]
            assert EDIT_FLAGS[vendor] not in second_capture["argv"]
            assert "--allowedTools" not in second_capture["argv"]
            if vendor == "claude":
                assert f"Edit({cli_server.root})" in first_capture["argv"]
                assert f"Write({cli_server.root})" in first_capture["argv"]

            # Another explicit opt-in must not inherit the first request's scope.
            third = payload(
                await session.call_tool(
                    "clink",
                    {
                        "continuation_id": thread_id,
                        "prompt": "CLINK_TEST_STAGE=edits THIRD_USER_MARKER",
                        "allow_edits": True,
                    },
                )
            )
            assert third["content"] == f"CONTEXT_OK_{vendor}_edits"
            assert third["metadata"]["history"]["retained_messages"] == 4
            assert third["continuation_offer"]["remaining_turns"] == 44
            third_capture = cli_server.capture("edits")
            assert EDIT_FLAGS[vendor] in third_capture["argv"]
            assert "--allowedTools" not in third_capture["argv"]
            assert cli_server.stages() == {"first", "followup", "edits"}
            for capture in (first_capture, second_capture, third_capture):
                assert not cli_server.provider_settings.intersection(capture["environment_names"])

    await asyncio.wait_for(scenario(), timeout=30)


@pytest.mark.parametrize("vendor", VENDORS)
async def test_stdio_overlapping_cli_continuation_is_busy_before_first_finishes(cli_server, vendor):
    async def scenario():
        connected = asyncio.get_running_loop().create_future()
        peers = []

        def accept(reader, writer):
            # Own sockets immediately, even if cancellation wins before the
            # test resumes from the connection future.
            peers.append(writer)
            if not connected.done():
                connected.set_result((reader, writer))
            else:
                writer.close()

        listener = await asyncio.start_server(accept, "127.0.0.1", 0)
        try:
            port = listener.sockets[0].getsockname()[1]
            async with cli_server.connect(gate_port=port) as (session, _):
                first = payload(
                    await session.call_tool(
                        "clink", {"cli_name": vendor, "prompt": f"CLINK_TEST_STAGE=first FIRST_USER_MARKER_{vendor}"}
                    )
                )
                thread_id = first["continuation_offer"]["continuation_id"]
                held = asyncio.create_task(
                    session.call_tool(
                        "clink", {"continuation_id": thread_id, "prompt": "CLINK_TEST_STAGE=held HELD_MARKER"}
                    )
                )
                try:
                    reader, writer = await asyncio.wait_for(connected, timeout=5)
                    assert await asyncio.wait_for(reader.readexactly(1), timeout=5) == b"R"
                    overlap = {"continuation_id": thread_id, "prompt": "CLINK_TEST_STAGE=overlap OVERLAP_MARKER"}
                    busy = payload(await asyncio.wait_for(session.call_tool("clink", overlap), timeout=3), error=True)
                    assert busy["metadata"]["error_type"] == "ConversationBusyError"
                    assert busy["metadata"]["retryable"] is True and busy["continuation_offer"] is None
                    assert not held.done(), "Busy response arrived only after the held request finished"
                    assert cli_server.stages() == {"first", "held"}

                    # Release only after observing busy through the actual wire.
                    writer.write(b"G")
                    await writer.drain()
                    completed = payload(await asyncio.wait_for(held, timeout=5))
                    assert completed["content"] == f"CONTEXT_OK_{vendor}_held"
                    assert completed["continuation_offer"]["remaining_turns"] == 46
                    retried = payload(await session.call_tool("clink", overlap))
                    assert retried["content"] == f"CONTEXT_OK_{vendor}_overlap"
                    assert retried["continuation_offer"]["remaining_turns"] == 44
                    assert retried["metadata"]["history"]["retained_messages"] == 4
                    assert cli_server.stages() == {"first", "held", "overlap"}
                    history = cli_server.capture("overlap")["prompt"]
                    assert history.count("HELD_MARKER") == 1 and history.count("OVERLAP_MARKER") == 1
                finally:
                    if not held.done():
                        held.cancel()
                    await asyncio.gather(held, return_exceptions=True)
        finally:
            listener.close()
            for peer in peers:
                peer.close()
            for peer in peers:
                await asyncio.wait_for(peer.wait_closed(), timeout=5)
            # Python 3.12+ also waits for accepted transports to close.
            await asyncio.wait_for(listener.wait_closed(), timeout=5)

    await asyncio.wait_for(scenario(), timeout=30)
