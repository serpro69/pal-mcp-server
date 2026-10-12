"""MCP v1 resource contracts, bounded reads and a real stdio round trip."""

import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCNotification, JSONRPCRequest

import server
from providers.configuration import provider_configurations
from utils import conversation_memory as memory
from utils import transcript_reader as reader


@pytest.fixture
def audit(tmp_path, monkeypatch):
    monkeypatch.setenv("PAL_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PAL_TRANSCRIPTS", "true")
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    return tmp_path / "transcripts"


def server_parameters(tmp_path, enabled):
    repo = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    for declaration in provider_configurations().values():
        for name in declaration.environment_names():
            env[name] = ""
    env.update(
        PAL_STATE_DIR=str(tmp_path),
        PAL_LOG_DIR=str(tmp_path / "child-logs"),
        PAL_TRANSCRIPTS=str(enabled).lower(),
        PAL_MCP_FORCE_ENV_OVERRIDE="false",
        DISABLED_MODELS="",
        PREFERRED_MODELS="",
        DEFAULT_MODEL="auto",
    )
    return StdioServerParameters(command=sys.executable, args=[str(repo / "server.py")], env=env, cwd=str(repo))


def test_disabled_hides_retained_reads(audit, monkeypatch):
    audit.mkdir()
    thread = str(uuid4())
    (audit / f"{thread}.jsonl").write_text("private retained contents")
    monkeypatch.setenv("PAL_TRANSCRIPTS", "false")
    for uri in ("pal://threads", f"pal://threads/{thread}"):
        with pytest.raises(reader.ResourceNotFoundError):
            reader.read_resource(uri)


@pytest.mark.asyncio
async def test_disabled_discovery_and_resource_error(monkeypatch):
    monkeypatch.setenv("PAL_TRANSCRIPTS", "false")
    assert await server.handle_list_resources() == []
    assert await server.handle_list_resource_templates() == []
    with pytest.raises(McpError) as caught:
        await server.handle_read_resource("pal://threads")
    assert caught.value.error.code == -32002


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,message",
    [(PermissionError("private path"), "permission denied"), (OSError("private path"), "storage unavailable")],
)
async def test_io_errors_distinct_from_missing_without_leaking_paths(monkeypatch, error, message):
    def fail(uri):
        raise error

    monkeypatch.setattr(server, "read_resource", fail)
    with pytest.raises(McpError) as caught:
        await server.handle_read_resource("pal://threads")
    assert caught.value.error.code == -32603
    assert message in caught.value.error.message
    assert "private path" not in caught.value.error.message


@pytest.mark.skipif(sys.platform == "win32", reason="Private transcripts fail closed pending Windows ACL validation")
class TestPosixResources:
    @pytest.mark.asyncio
    async def test_invalid_json_numbers_and_surrogates_are_skipped(self, audit):
        thread = memory.create_thread("chat", {})
        path = audit / f"{thread}.jsonl"
        record = json.loads(path.read_text())
        with path.open("ab") as stream:
            stream.write(json.dumps({**record, "metadata": {"bad": 1234}}).replace("1234}", "1e999}").encode() + b"\n")
            stream.write(json.dumps({**record, "tool_name": "\ud800"}).encode() + b"\n")
            nested = 0
            for _ in range(70):
                nested = [nested]
            stream.write(json.dumps({**record, "metadata": nested}).encode() + b"\n")
        result = json.loads((await server.handle_read_resource(f"pal://threads/{thread}"))[0].content)
        assert len(result["records"]) == 1
        assert result["diagnostics"]["skipped_malformed_records"] == 3

    @pytest.mark.asyncio
    async def test_changed_state_configuration_has_safe_error(self, audit, tmp_path, monkeypatch):
        state_file = tmp_path / "private-state-path"
        state_file.write_text("not a directory")
        monkeypatch.setenv("PAL_STATE_DIR", str(state_file))
        with pytest.raises(McpError) as caught:
            await server.handle_read_resource("pal://threads")
        assert caught.value.error.code == -32603
        assert caught.value.error.message == "Transcript storage unavailable"
        assert str(state_file) not in caught.value.error.message

    @pytest.mark.asyncio
    async def test_raw_stdio_rejects_dot_segments_before_sdk_normalization(self, audit, tmp_path):
        thread = memory.create_thread("chat", {})

        async def roundtrip():
            with (tmp_path / "raw-stderr").open("w") as errors:
                async with stdio_client(server_parameters(tmp_path, True), errlog=errors) as (read, write):

                    async def request(request_id, method, params):
                        await write.send(
                            SessionMessage(
                                JSONRPCMessage(
                                    JSONRPCRequest(jsonrpc="2.0", id=request_id, method=method, params=params)
                                )
                            )
                        )
                        response = await read.receive()
                        assert not isinstance(response, Exception)
                        assert response.message.root.id == request_id
                        return response.message.root

                    await request(
                        1,
                        "initialize",
                        {
                            "protocolVersion": "2025-11-25",
                            "capabilities": {},
                            "clientInfo": {"name": "raw-test", "version": "1"},
                        },
                    )
                    await write.send(
                        SessionMessage(
                            JSONRPCMessage(JSONRPCNotification(jsonrpc="2.0", method="notifications/initialized"))
                        )
                    )
                    for number, uri in enumerate(
                        (f"pal://threads/x/../{thread}", f"pal://threads/%2e/{thread}", f"pal://THREADS/{thread}"), 2
                    ):
                        response = await request(number, "resources/read", {"uri": uri})
                        assert response.error.code == -32002
                    valid = await request(5, "resources/read", {"uri": f"pal://threads/{thread}"})
                    assert json.loads(valid.result["contents"][0]["text"])["thread_id"] == thread

        await asyncio.wait_for(roundtrip(), timeout=20)

    def test_empty_enabled_index(self, audit):
        assert reader.list_threads()["threads"] == []
        assert not audit.exists()

    def test_deterministic_bounded_listing_and_skipped_files(self, audit, monkeypatch):
        ids = [memory.create_thread("chat", {}) for _ in range(4)]
        (audit / "not-a-transcript").write_text("ignored")
        (audit / f"{uuid4()}.jsonl").symlink_to(audit / f"{ids[0]}.jsonl")
        monkeypatch.setattr(reader, "MAX_LIST_ITEMS", 2)
        result = reader.list_threads()
        assert [r["thread_id"] for r in result["threads"]] == sorted(ids)[:2]
        assert result["truncated"] and result["total_threads"] == 4
        assert result["skipped_files"] == 2
        monkeypatch.setattr(reader, "MAX_DIRECTORY_ENTRIES", 2)
        with pytest.raises(OSError, match="scan limit"):
            reader.list_threads()

    @pytest.mark.parametrize(
        "uri",
        [
            "file:///etc/passwd",
            "pal://threads/../outside",
            "pal://threads/",
            "pal://threads?all=1",
            "pal://threads/%2e%2e",
            "pal://threads/" + "0" * 32,
            "pal://threads/ABCDEFAB-1234-1234-1234-ABCDEFABCDEF",
        ],
    )
    def test_malformed_uris_do_not_select_files(self, audit, uri):
        with pytest.raises(reader.ResourceNotFoundError):
            reader.read_resource(uri)
        assert not audit.exists()

    def test_valid_read_reports_corrupt_and_interrupted_records(self, audit):
        thread = memory.create_thread("chat", {})
        memory.add_turn(thread, "user", "hello π\nsecond line", tool_name="chat")
        path = audit / f"{thread}.jsonl"
        with path.open("ab") as stream:
            stream.write(b'not json\n{"schema_version":99}\n\xff\n[]\n{"bad":NaN}\n{"interrupted":')
        result = reader.read_resource(f"pal://threads/{thread}")
        assert result["records"][1]["content"] == "hello π\nsecond line"
        assert len(result["records"]) == 2
        assert result["diagnostics"]["skipped_malformed_records"] == 5
        assert result["diagnostics"]["interrupted_final_lines"] == 1
        assert not result["truncated"]

    def test_huge_transcript_and_record_limits(self, audit, monkeypatch):
        thread = memory.create_thread("chat", {})
        memory.add_turn(thread, "assistant", "x" * (reader.MAX_READ_BYTES + 200))
        result = reader.read_thread(thread)
        assert result["truncated"]
        assert len(result["records"]) == 1
        assert result["diagnostics"]["interrupted_final_lines"] == 0
        monkeypatch.setattr(reader, "MAX_READ_RECORDS", 1)
        small = memory.create_thread("chat", {})
        memory.add_turn(small, "user", "small")
        assert reader.read_thread(small)["truncated"]
        assert len(reader.read_thread(small)["records"]) == 1

    def test_deep_json_and_wrong_thread_are_skipped(self, audit):
        thread = memory.create_thread("chat", {})
        path = audit / f"{thread}.jsonl"
        other = path.read_bytes().replace(thread.encode(), str(uuid4()).encode())
        with path.open("ab") as stream:
            stream.write(b"[" * 2000 + b"0" + b"]" * 2000 + b"\n" + other)
        result = reader.read_thread(thread)
        assert len(result["records"]) == 1
        assert result["diagnostics"]["skipped_malformed_records"] == 2

    @pytest.mark.asyncio
    async def test_reader_permission_and_scan_limit_map_through_mcp(self, audit, monkeypatch):
        thread = memory.create_thread("chat", {})
        with monkeypatch.context() as patcher:
            patcher.setattr(reader, "MAX_DIRECTORY_ENTRIES", 0)
            with pytest.raises(McpError) as caught:
                await server.handle_read_resource("pal://threads")
            assert caught.value.error.code == -32603
            assert "scan limit" in caught.value.error.message

        def deny(*args):
            raise PermissionError("permission denied by filesystem")

        monkeypatch.setattr(os, "fchmod", deny)
        with pytest.raises(McpError) as caught:
            await server.handle_read_resource(f"pal://threads/{thread}")
        assert caught.value.error.code == -32603
        assert "permission denied" in caught.value.error.message

    @pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory", "fifo", "missing"])
    def test_unsafe_or_missing_files_are_not_found(self, audit, tmp_path, kind):
        audit.mkdir()
        thread = str(uuid4())
        path = audit / f"{thread}.jsonl"
        target = tmp_path / "target"
        target.write_text("private target")
        if kind == "symlink":
            path.symlink_to(target)
        elif kind == "hardlink":
            os.link(target, path)
        elif kind == "directory":
            path.mkdir()
        elif kind == "fifo":
            os.mkfifo(path)
        with pytest.raises(reader.ResourceNotFoundError):
            reader.read_thread(thread)
        assert target.read_text() == "private target"

    @pytest.mark.asyncio
    async def test_mcp_decorated_handler_returns_json_contents(self, audit):
        from mcp.types import ReadResourceRequest, ReadResourceRequestParams
        from pydantic import AnyUrl

        thread = memory.create_thread("chat", {})
        handler = server.server.request_handlers[ReadResourceRequest]
        response = await handler(
            ReadResourceRequest(params=ReadResourceRequestParams(uri=AnyUrl(f"pal://threads/{thread}")))
        )
        assert response.root.contents[0].mimeType == "application/json"
        assert json.loads(response.root.contents[0].text)["thread_id"] == thread

    @pytest.mark.asyncio
    @pytest.mark.parametrize("enabled", [True, False])
    async def test_stdio_handshake_list_read_and_protocol_errors(self, audit, tmp_path, enabled):
        thread = memory.create_thread("chat", {})
        memory.add_turn(thread, "assistant", "stdio audit answer")
        repo = Path(__file__).resolve().parents[1]
        # Empty provider settings isolate this local protocol test from services.
        env = dict(os.environ)
        for declaration in provider_configurations().values():
            for name in declaration.environment_names():
                env[name] = ""
        env.update(
            PAL_STATE_DIR=str(tmp_path),
            PAL_LOG_DIR=str(tmp_path / "child-logs"),
            PAL_TRANSCRIPTS=str(enabled).lower(),
            PAL_MCP_FORCE_ENV_OVERRIDE="false",
            DISABLED_MODELS="",
            PREFERRED_MODELS="",
            DEFAULT_MODEL="auto",
        )
        params = StdioServerParameters(command=sys.executable, args=[str(repo / "server.py")], env=env, cwd=str(repo))

        async def roundtrip():
            with (tmp_path / "child-stderr").open("w") as errors:
                async with stdio_client(params, errlog=errors) as (read, write):
                    async with ClientSession(read, write) as session:
                        initialized = await session.initialize()
                        assert initialized.capabilities.resources is not None
                        listed = await session.list_resources()
                        templates = await session.list_resource_templates()
                        assert bool(listed.resources) is enabled
                        assert bool(templates.resourceTemplates) is enabled
                        if enabled:
                            index = json.loads((await session.read_resource("pal://threads")).contents[0].text)
                            assert index["threads"][0]["thread_id"] == thread
                            data = json.loads((await session.read_resource(f"pal://threads/{thread}")).contents[0].text)
                            assert data["records"][1]["content"] == "stdio audit answer"
                        for uri in ("pal://threads/invalid", f"pal://threads/{uuid4()}", "file:///etc/passwd"):
                            with pytest.raises(McpError) as caught:
                                await session.read_resource(uri)
                            assert caught.value.error.code == -32002
                        if not enabled:
                            with pytest.raises(McpError) as caught:
                                await session.read_resource(f"pal://threads/{thread}")
                            assert caught.value.error.code == -32002

        await asyncio.wait_for(roundtrip(), timeout=20)
