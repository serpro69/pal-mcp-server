"""Exercise the complete first-call persistence path with a captured provider."""

import json

import pytest

from tests.request_capture import capture_providers
from tools.chat import ChatTool
from utils.conversation_memory import get_thread


@pytest.mark.asyncio
async def test_first_response_persisted_in_conversation_history(tmp_path, monkeypatch):
    capture_providers(monkeypatch, google_answer="Here is the initial answer.")
    monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(tmp_path))
    result = json.loads(
        (
            await ChatTool().execute(
                {
                    "prompt": "First question?",
                    "model": "gemini-2.5-flash",
                    "working_directory_absolute_path": str(tmp_path),
                }
            )
        )[0].text
    )
    thread = get_thread(result["continuation_offer"]["continuation_id"])
    assert [turn.role for turn in thread.turns] == ["user", "assistant"]
    assert [turn.content for turn in thread.turns] == ["First question?", "Here is the initial answer."]
