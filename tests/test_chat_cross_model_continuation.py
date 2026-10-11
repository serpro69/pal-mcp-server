"""Capture both provider requests in a cross-model continuation without live APIs."""

import json

import pytest

import server
from tests.request_capture import capture_providers
from utils.conversation_memory import get_thread


@pytest.mark.asyncio
async def test_chat_cross_model_continuation(monkeypatch, tmp_path):
    calls = capture_providers(monkeypatch, google_answer="7", openai_answer="The number was 7.")
    monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(tmp_path))
    first_prompt = "Pick a number between 1 and 10 and respond with JUST that number."
    first = json.loads(
        (
            await server.handle_call_tool(
                "chat",
                {"prompt": first_prompt, "model": "gemini-2.5-flash", "working_directory_absolute_path": str(tmp_path)},
            )
        )[0].text
    )
    thread_id = first["continuation_offer"]["continuation_id"]
    second_prompt = "What was the number?"
    second = json.loads(
        (
            await server.handle_call_tool(
                "chat",
                {
                    "prompt": second_prompt,
                    "model": "gpt-5",
                    "continuation_id": thread_id,
                    "working_directory_absolute_path": str(tmp_path),
                },
            )
        )[0].text
    )
    assert [call["provider"] for call in calls] == ["google", "openai"]
    assert calls[1]["model_name"] == "gpt-5"
    assert calls[1]["prompt"].count(first_prompt) == 1
    assert calls[1]["prompt"].count(second_prompt) == 1
    assert "\n7\n" in calls[1]["prompt"]
    assert second["metadata"]["provider_used"] == "openai"
    assert [turn.content for turn in get_thread(thread_id).turns] == [
        first_prompt,
        "7",
        second_prompt,
        "The number was 7.",
    ]
