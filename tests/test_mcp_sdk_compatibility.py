"""Exercise PAL over stdio with both protocol generations supported by SDK 2.x."""

import json

import pytest
from mcp import Client, MCPError
from mcp.types import Implementation

from tests.cli_isolation import isolated_server_parameters


@pytest.mark.parametrize(("mode", "protocol"), [("legacy", "2025-11-25"), ("auto", "2026-07-28")])
async def test_pal_stdio_protocol_contract(tmp_path, mode, protocol):
    params = isolated_server_parameters(
        {
            "PAL_SKIP_DOTENV": "true",
            "PAL_STATE_DIR": str(tmp_path),
            "PAL_TRANSCRIPTS": "true",
        }
    )
    stages = []

    async def record_progress(progress, total, message):
        stages.append(json.loads(message)["stage"])

    async with Client(
        params, mode=mode, read_timeout_seconds=10, client_info=Implementation(name="cursor", version="1")
    ) as client:
        assert client.protocol_version == protocol
        assert client.server_info.name == "PAL"
        assert client.instructions
        assert "version" in {tool.name for tool in (await client.list_tools()).tools}
        result = await client.call_tool("version", {}, progress_callback=record_progress)
        assert not result.is_error
        payload = json.loads(result.content[0].text)
        assert payload["status"] == "success"
        assert "**Connected Client**: Cursor" in payload["content"]
        assert stages == ["started", "completed"]
        invalid = await client.call_tool("clink", {"prompt": 123})
        assert invalid.is_error and invalid.content[0].text.startswith("Input validation error:")
        assert (await client.list_prompts()).prompts
        prompt = await client.get_prompt("version")
        assert prompt.description and prompt.messages
        with pytest.raises(MCPError, match="Unknown prompt") as unknown_prompt:
            await client.get_prompt("unregistered-prompt")
        assert unknown_prompt.value.code == (0 if mode == "legacy" else -32602)
        if mode == "auto":
            with pytest.raises(MCPError, match="Unknown tool") as unknown_tool:
                await client.call_tool("unregistered-tool", {})
            assert unknown_tool.value.code == -32602
        assert [resource.uri for resource in (await client.list_resources()).resources] == ["pal://threads"]
        assert (await client.list_resource_templates()).resource_templates
        contents = (await client.read_resource("pal://threads")).contents
        assert contents[0].mime_type == "application/json"
        assert json.loads(contents[0].text)["threads"] == []
        with pytest.raises(MCPError) as missing:
            await client.read_resource("pal://unknown")
        assert missing.value.code == (-32002 if mode == "legacy" else -32602)
