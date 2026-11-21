"""Exercise the installed PAL entry point with a fixed MCP 1.x client."""

import asyncio
import sys
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def check_installed_server(executable: Path) -> None:
    with TemporaryDirectory() as directory:
        parameters = StdioServerParameters(
            command=str(executable.resolve(strict=True)),
            cwd=directory,
            env={"PAL_STATE_DIR": directory, "XDG_CONFIG_HOME": directory},
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as client:
                info = await client.initialize()
                assert info.serverInfo.name == "PAL", info.serverInfo
                listing = await client.list_tools()
                names = [tool.name for tool in listing.tools]
                assert "version" in names, names
                print(f"Connected to {info.serverInfo.name} {info.serverInfo.version}, listed {len(names)} tools")


if __name__ == "__main__":
    asyncio.run(check_installed_server(Path(sys.argv[1])))
