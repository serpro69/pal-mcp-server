"""A synchronous scenario owns one MCP client, event loop and server process."""

import os
import sys
from contextlib import ExitStack, asynccontextmanager
from datetime import timedelta
from pathlib import Path

from anyio.from_thread import start_blocking_portal
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class SimulatorSession:
    """Bridge existing synchronous scenarios to the supported MCP async client.

    The portal wrapper enters and exits the SDK's task groups in the same task.
    SDK transport teardown closes stdin, waits, and terminates/reaps the child.
    """

    def __init__(self, timeout: float = 3600):
        self.timeout = timeout
        self._resources = ExitStack()

    @asynccontextmanager
    async def _connect(self):
        repo = Path(__file__).resolve().parents[1]
        parameters = StdioServerParameters(
            command=sys.executable, args=[str(repo / "server.py")], cwd=str(repo), env=dict(os.environ)
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=self.timeout)) as session:
                await session.initialize()
                yield session

    def __enter__(self):
        try:
            self._portal = self._resources.enter_context(start_blocking_portal())
            self._session = self._resources.enter_context(self._portal.wrap_async_context_manager(self._connect()))
        except BaseException:
            self._resources.__exit__(*sys.exc_info())
            raise
        return self

    def call_tool(self, name: str, arguments: dict):
        return self._portal.call(self._session.call_tool, name, arguments)

    def __exit__(self, *exc):
        return self._resources.__exit__(*exc)
