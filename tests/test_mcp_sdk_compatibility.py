"""Exercise the MCP 1.x interfaces required by PAL over an actual SDK session."""

from datetime import timedelta

import pytest
from mcp import types
from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import AnyUrl


@pytest.mark.asyncio
async def test_resource_listing_and_read_round_trip():
    server = Server("resource-compatibility")
    uri = AnyUrl("pal://compatibility")

    @server.list_resources()
    async def list_resources():
        return [types.Resource(uri=uri, name="compatibility", mimeType="text/plain")]

    @server.read_resource()
    async def read_resource(requested_uri):
        assert requested_uri == uri
        return [ReadResourceContents(content="MCP 1.x resource", mime_type="text/plain")]

    assert server.create_initialization_options().capabilities.resources is not None
    async with create_connected_server_and_client_session(server, timedelta(seconds=5)) as client:
        listing = await client.list_resources()
        assert [resource.uri for resource in listing.resources] == [uri]
        result = await client.read_resource(uri)
        assert result.contents == [types.TextResourceContents(uri=uri, mimeType="text/plain", text="MCP 1.x resource")]


@pytest.mark.asyncio
@pytest.mark.parametrize("is_error", [False, True])
async def test_request_context_progress_and_explicit_tool_result_round_trip(is_error):
    server = Server("tool-compatibility")
    notifications = []

    @server.list_tools()
    async def list_tools():
        return [types.Tool(name="probe", inputSchema={"type": "object"})]

    @server.call_tool()
    async def call_tool(name, arguments):
        assert name == "probe"
        context = server.request_context
        assert context.meta.progressToken is not None
        await context.session.send_progress_notification(
            progress_token=context.meta.progressToken, progress=1, total=1, message="complete"
        )
        return types.CallToolResult(content=[types.TextContent(type="text", text="probe result")], isError=is_error)

    async def record_progress(progress, total, message):
        notifications.append((progress, total, message))

    async with create_connected_server_and_client_session(server, timedelta(seconds=5)) as client:
        result = await client.call_tool("probe", {}, progress_callback=record_progress)
        assert result.isError is is_error
        assert result.content == [types.TextContent(type="text", text="probe result")]
        assert notifications == [(1, 1, "complete")]
