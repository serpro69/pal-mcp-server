import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from mcp.types import CallToolRequest, CallToolRequestParams

from providers.gemini import GeminiModelProvider
from providers.registry import ModelProviderRegistry
from server import server as mcp_server
from tests.request_capture import capture_providers
from utils import conversation_memory as memory


def _install_dummy_provider(monkeypatch):
    """Ensure preflight model checks succeed without real provider configuration."""

    class DummyProvider:
        def get_provider_type(self):
            return SimpleNamespace(value="dummy")

        def get_capabilities(self, model_name):
            return SimpleNamespace(
                supports_extended_thinking=False,
                allow_code_generation=False,
                supports_images=False,
                context_window=1_000_000,
                max_image_size_mb=10,
            )

    monkeypatch.setattr(
        ModelProviderRegistry,
        "get_provider_for_model",
        classmethod(lambda cls, model_name: DummyProvider()),
    )
    monkeypatch.setattr(
        ModelProviderRegistry,
        "get_available_models",
        classmethod(lambda cls, respect_restrictions=False: {"gemini-2.5-flash": None}),
    )


@pytest.mark.asyncio
async def test_tool_execution_error_sets_is_error_flag_for_mcp_response(monkeypatch):
    """Ensure ToolExecutionError surfaces as CallToolResult with isError=True."""

    _install_dummy_provider(monkeypatch)

    handler = mcp_server.request_handlers[CallToolRequest]

    arguments = {
        "prompt": "Trigger working_directory_absolute_path validation failure",
        "working_directory_absolute_path": "relative/path",  # Not absolute -> ToolExecutionError from ChatTool
        "absolute_file_paths": [],
        "model": "gemini-2.5-flash",
    }

    request = CallToolRequest(params=CallToolRequestParams(name="chat", arguments=arguments))

    server_result = await handler(request)

    assert server_result.root.isError is True
    assert server_result.root.content, "Expected error response content"

    payload = server_result.root.content[0].text
    data = json.loads(payload)
    assert data["status"] == "error"
    assert "absolute" in data["content"].lower()


@pytest.mark.no_mock_provider
@pytest.mark.parametrize("answer", ["", "   \n\t", RuntimeError("expert unavailable")])
@pytest.mark.asyncio
async def test_expert_failure_sets_protocol_error_without_saving_reply(monkeypatch, answer):
    def respond(_kwargs):
        if isinstance(answer, Exception):
            raise answer
        return answer

    calls = capture_providers(monkeypatch, google_answer=respond)
    arguments = {
        "step": "Assess the proposed change",
        "findings": "Caller findings remain available",
        "step_number": 1,
        "total_steps": 1,
        "next_step_required": False,
        "model": "gemini-2.5-flash",
    }
    request = CallToolRequest(params=CallToolRequestParams(name="thinkdeep", arguments=arguments))
    result = (await mcp_server.request_handlers[CallToolRequest](request)).root

    assert result.isError is True
    payload = json.loads(result.content[0].text)
    assert payload["status"] == "error"
    assert payload["thinkdeep_complete"] is False
    assert payload["thinking_complete"] is False
    assert "expert_analysis" not in payload
    assert "important_considerations" not in payload
    assert payload["metadata"]["model_used"] == "gemini-2.5-flash"
    assert payload["metadata"]["provider_used"] == "google"
    assert len(calls) == 1
    thread_id = payload["continuation_offer"]["continuation_id"]
    thread = memory.get_thread(thread_id)
    assert [(turn.role, turn.content) for turn in thread.turns] == [("user", arguments["step"])]


@pytest.mark.no_mock_provider
@pytest.mark.asyncio
async def test_restored_expert_attachment_keeps_structured_budget_error(monkeypatch, tmp_path):
    calls = capture_providers(monkeypatch)
    original = GeminiModelProvider("synthetic").get_capabilities("gemini-2.5-flash")
    monkeypatch.setattr(
        GeminiModelProvider,
        "get_capabilities",
        lambda self, name: replace(original, context_window=20000, max_output_tokens=1000),
    )
    path = tmp_path / "large.py"
    path.write_text("oversized " * 5000)
    arguments = {
        "step": "Inspect the attachment",
        "findings": "Needs expert validation",
        "step_number": 1,
        "total_steps": 2,
        "next_step_required": True,
        "relevant_files": [str(path)],
        "model": "gemini-2.5-flash",
    }
    handler = mcp_server.request_handlers[CallToolRequest]
    first = (await handler(CallToolRequest(params=CallToolRequestParams(name="thinkdeep", arguments=arguments)))).root
    assert first.isError is False
    thread_id = json.loads(first.content[0].text)["continuation_offer"]["continuation_id"]
    arguments.update(continuation_id=thread_id, step_number=2, next_step_required=False, relevant_files=[])
    result = (await handler(CallToolRequest(params=CallToolRequestParams(name="thinkdeep", arguments=arguments)))).root

    assert result.isError is True
    payload = json.loads(result.content[0].text)
    assert payload["status"] == "code_too_large"
    assert payload["metadata"]["overage"] > 0
    assert payload["metadata"]["limit"] > 0
    assert payload["metadata"]["largest_files"][0]["path"] == str(path)
    assert not calls
    assert [turn.role for turn in memory.get_thread(thread_id).turns] == ["user", "assistant", "user"]
