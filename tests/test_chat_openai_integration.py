"""Offline OpenAI routing and continuation request contracts.

Existing live cassettes are retained unchanged; refreshing them is a release check.
"""

import json

import pytest

import server
from providers.registry import ModelProviderRegistry
from tests.request_capture import capture_providers
from tools.chat import ChatTool
from utils.conversation_memory import get_thread


@pytest.mark.asyncio
async def test_chat_auto_mode_with_openai(monkeypatch, tmp_path):
    calls = capture_providers(monkeypatch, openai_answer="About 384,400 km.")
    monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(ModelProviderRegistry, "get_preferred_fallback_model", lambda *args: "gpt-5")
    result = json.loads(
        (
            await server.handle_call_tool(
                "chat",
                {"prompt": "How far is the Moon?", "model": "auto", "working_directory_absolute_path": str(tmp_path)},
            )
        )[0].text
    )
    assert calls[0]["model_name"] == "gpt-5"
    assert calls[0]["prompt"].count("How far is the Moon?") == 1
    assert calls[0]["system_prompt"]
    assert result["metadata"]["provider_used"] == "openai"
    assert "384,400" in result["content"]


@pytest.mark.asyncio
async def test_chat_openai_continuation(monkeypatch, tmp_path):
    calls = capture_providers(monkeypatch, openai_answer="OpenAI raw reply")
    monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(tmp_path))
    tool = ChatTool()
    first = json.loads(
        (
            await tool.execute(
                {
                    "prompt": "Explain lunar distance.",
                    "model": "gpt-5",
                    "working_directory_absolute_path": str(tmp_path),
                }
            )
        )[0].text
    )
    thread_id = first["continuation_offer"]["continuation_id"]
    second = json.loads(
        (
            await tool.execute(
                {
                    "prompt": "Explain the variation.",
                    "continuation_id": thread_id,
                    "working_directory_absolute_path": str(tmp_path),
                }
            )
        )[0].text
    )
    assert [call["model_name"] for call in calls] == ["gpt-5", "gpt-5"]
    for content in ["Explain lunar distance.", "OpenAI raw reply", "Explain the variation."]:
        assert calls[1]["prompt"].count(content) == 1
    thread = get_thread(thread_id)
    assert [turn.role for turn in thread.turns] == ["user", "assistant", "user", "assistant"]
    assert second["continuation_offer"]["remaining_turns"] == 46
    assert "23 more exchanges" in second["continuation_offer"]["note"]
