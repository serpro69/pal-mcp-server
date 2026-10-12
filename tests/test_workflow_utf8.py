"""
Unit tests to validate UTF-8 encoding in workflow tools
and the generation of properly encoded JSON responses.
"""

import json
import os
import unittest
from unittest.mock import AsyncMock

import pytest

from providers.gemini import GeminiModelProvider
from providers.shared import ModelResponse
from tests.request_capture import capture_providers
from tests.test_conversation_storage_errors import FaultStorage
from tools.analyze import AnalyzeTool
from tools.codereview import CodeReviewTool
from tools.debug import DebugIssueTool
from tools.shared.exceptions import ToolExecutionError
from utils import conversation_memory as memory


class TestWorkflowToolsUTF8(unittest.IsolatedAsyncioTestCase):
    """Tests for UTF-8 encoding in workflow tools."""

    def setUp(self):
        """Test setup."""
        self.original_locale = os.getenv("LOCALE")
        # Default to French for tests
        os.environ["LOCALE"] = "fr-FR"

    def tearDown(self):
        """Cleanup after tests."""
        if self.original_locale is not None:
            os.environ["LOCALE"] = self.original_locale
        else:
            os.environ.pop("LOCALE", None)

    def test_workflow_json_response_structure(self):
        """Test the structure of JSON responses from workflow tools."""
        # Mock response with UTF-8 characters
        test_response = {
            "status": "pause_for_analysis",
            "step_number": 1,
            "total_steps": 3,
            "next_step_required": True,
            "findings": "Code analysis reveals performance issues 🔍",
            "files_checked": ["/src/main.py"],
            "relevant_files": ["/src/main.py"],
            "issues_found": [{"severity": "high", "description": "Function too complex - refactoring needed"}],
            "investigation_required": True,
            "required_actions": ["Review code dependencies", "Analyze architectural patterns"],
        }

        # Test JSON serialization with ensure_ascii=False
        json_str = json.dumps(test_response, indent=2, ensure_ascii=False)

        # Check UTF-8 characters are preserved
        self.assertIn("🔍", json_str)
        # No escaped characters
        self.assertNotIn("\\u", json_str)

        # Test parsing
        parsed = json.loads(json_str)
        self.assertEqual(parsed["findings"], test_response["findings"])
        self.assertEqual(len(parsed["issues_found"]), 1)

    def test_utf8_emoji_preservation_in_workflow_responses(self):
        """Test that emojis are preserved in workflow tool responses."""
        # Mock workflow response with various emojis
        test_data = {
            "status": "analysis_complete",
            "severity_indicators": {
                "critical": "🔴",
                "high": "🟠",
                "medium": "🟡",
                "low": "🟢",
                "success": "✅",
                "error": "❌",
                "warning": "⚠️",
            },
            "progress": "Analysis completed 🎉",
            "recommendations": [
                "Optimize performance 🚀",
                "Improve documentation 📚",
                "Add unit tests 🧪",
            ],
        }

        # Test JSON encoding with ensure_ascii=False
        json_str = json.dumps(test_data, ensure_ascii=False, indent=2)

        # Check emojis are preserved
        self.assertIn("🔴", json_str)
        self.assertIn("🟠", json_str)
        self.assertIn("🟡", json_str)
        self.assertIn("🟢", json_str)
        self.assertIn("✅", json_str)
        self.assertIn("❌", json_str)
        self.assertIn("⚠️", json_str)
        self.assertIn("🎉", json_str)
        self.assertIn("🚀", json_str)
        self.assertIn("📚", json_str)
        self.assertIn("🧪", json_str)

        # No escaped Unicode
        self.assertNotIn("\\u", json_str)

        # Test parsing preserves emojis
        parsed = json.loads(json_str)
        self.assertEqual(parsed["severity_indicators"]["critical"], "🔴")
        self.assertEqual(parsed["progress"], "Analysis completed 🎉")


@pytest.mark.parametrize("tool_class", [AnalyzeTool, CodeReviewTool, DebugIssueTool])
@pytest.mark.parametrize("fails", [False, True])
async def test_workflow_expert_unicode_result_and_error(monkeypatch, tmp_path, tool_class, fails):
    storage = FaultStorage()
    monkeypatch.setattr(memory, "get_storage", lambda: storage)
    monkeypatch.setenv("LOCALE", "fr-FR")
    capture_providers(monkeypatch)
    model = "gemini-2.5-flash"
    answer = (
        "ÉLEVÉ: problème de données; fonctions spécialisées, validation appropriée, paramètres présents 🔴 🟠 🟡 ✅"
    )
    error = "Échec de l’analyse: données indisponibles ❌"
    generate = AsyncMock(
        return_value=ModelResponse(
            content=json.dumps({"status": "analysis_complete", "raw_analysis": answer}, ensure_ascii=False),
            model_name=model,
            provider="google",
        ),
        side_effect=RuntimeError(error) if fails else None,
    )
    monkeypatch.setattr(GeminiModelProvider, "generate_content", generate)
    source = tmp_path / "example.py"
    source.write_text("données = 1\n", encoding="utf-8")
    arguments = {
        "step": "Analyser les données",
        "findings": "Une validation est nécessaire",
        "hypothesis": "Validation des paramètres insuffisante",
        "confidence": "medium",
        "step_number": 1,
        "total_steps": 1,
        "next_step_required": False,
        "files_checked": [str(source)],
        "relevant_files": [str(source)],
        "model": model,
    }
    if fails:
        with pytest.raises(ToolExecutionError) as failure:
            await tool_class().execute(arguments)
        result = json.loads(failure.value.payload)
    else:
        output = await tool_class().execute(arguments)
        result = json.loads(output[0].text)
    generate.assert_awaited_once()
    sent = generate.call_args.kwargs
    assert "fr-FR" in sent["system_prompt"] + sent["prompt"]
    assert result["metadata"]["model_used"] == model
    assert result["metadata"]["provider_used"] == "google"
    thread = memory.get_thread(result["continuation_offer"]["continuation_id"])
    if fails:
        assert result["status"] == "error"
        assert result["content"] == error
        assert "expert_analysis" not in result
        assert [turn.role for turn in thread.turns] == ["user"]
    else:
        assert result["expert_analysis"]["raw_analysis"] == answer
        assert answer in output[0].text
        assert [turn.role for turn in thread.turns] == ["user", "assistant"]
        assert json.loads(thread.turns[-1].content)["raw_analysis"] == answer


if __name__ == "__main__":
    unittest.main(verbosity=2)
