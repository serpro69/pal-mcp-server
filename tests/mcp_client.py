"""Call PAL through the SDK's in-memory client and protocol dispatcher."""

from mcp import Client
from mcp.types import CallToolResult

import server


async def call_tool(name: str, arguments: dict) -> CallToolResult:
    async with Client(server.server, read_timeout_seconds=5) as client:
        return await client.call_tool(name, arguments)
