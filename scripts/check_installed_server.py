"""Exercise the installed PAL entry point with a fixed MCP 1.x client."""

import asyncio
import json
import shlex
import sys
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def check_installed_server(executable: Path) -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        echo = root / "echo_prompt.py"
        echo.write_text("import sys\nsys.stdout.buffer.write(sys.stdin.buffer.read())\n", encoding="utf-8")
        # A later personal manifest can replace a fixed client name.
        name = f"installed-echo-{uuid4().hex}"
        manifest = root / "installed-echo.json"
        manifest.write_text(
            json.dumps(
                {
                    "name": name,
                    "command": shlex.join([sys.executable, str(echo)]),
                    "safe_args": [],
                    "edit_args": [],
                    "env_passthrough": [],
                    "custom": {
                        "prompt": {"mode": "stdin"},
                        "output": {"format": "text"},
                        "workspace": "any",
                        "failure_hint": "Check the local echo fixture.",
                    },
                }
            ),
            encoding="utf-8",
        )
        parameters = StdioServerParameters(
            command=str(executable.resolve(strict=True)),
            cwd=directory,
            env={
                "CLI_CLIENTS_CONFIG_PATH": str(manifest),
                "PAL_SKIP_DOTENV": "true",
                "PAL_STATE_DIR": directory,
                "LOG_LEVEL": "WARNING",
                "XDG_CONFIG_HOME": directory,
            },
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as client:
                info = await client.initialize()
                assert info.serverInfo.name == "PAL", info.serverInfo
                listing = await client.list_tools()
                names = [tool.name for tool in listing.tools]
                assert "version" in names, names
                result = await client.call_tool("clink", {"cli_name": name, "prompt": "Echo this request."})
                assert not result.isError, result.content
                reply = json.loads(result.content[0].text)["content"]
                assert "Answer the user's request using the information supplied in the conversation." in reply, reply
                assert "Echo this request." in reply, reply
                print(f"Connected to {info.serverInfo.name} {info.serverInfo.version}, listed {len(names)} tools")


if __name__ == "__main__":
    asyncio.run(check_installed_server(Path(sys.argv[1])))
