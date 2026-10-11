"""Offline code-generation contract; live recordings remain a separate release gate."""

import json

import pytest

from tests.request_capture import capture_providers
from tools.chat import ChatTool
from utils.conversation_memory import get_thread


@pytest.mark.asyncio
async def test_chat_codegen_saves_file(monkeypatch, tmp_path):
    raw = "<GENERATED-CODE><NEWFILE:calculator.py>\ndef add(a, b): return a + b\ndef multiply(a, b): return a * b\n</NEWFILE:calculator.py></GENERATED-CODE>"
    calls = capture_providers(monkeypatch, google_answer=raw)
    monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(tmp_path))
    preexisting = tmp_path / "pal_generated.code"
    preexisting.write_text("stale contents")
    prompt = "Generate calculator functions in the structured <GENERATED-CODE> format."
    output = json.loads(
        (
            await ChatTool().execute(
                {"prompt": prompt, "model": "gemini-2.5-pro", "working_directory_absolute_path": str(tmp_path)}
            )
        )[0].text
    )
    (artifact,) = (tmp_path / ".pal_artifacts").glob("*.code")
    assert "def add" in artifact.read_text() and "def multiply" in artifact.read_text()
    assert preexisting.read_text() == "stale contents"
    assert str(artifact) in output["content"]
    assert len(calls) == 1 and calls[0]["model_name"] == "gemini-2.5-pro"
    assert calls[0]["prompt"].count(prompt) == 1
    assert "Structured Code Generation" in calls[0]["system_prompt"]
    thread = get_thread(output["continuation_offer"]["continuation_id"])
    assert [turn.content for turn in thread.turns] == [prompt, raw]
