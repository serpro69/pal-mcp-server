"""
Tests for Chat tool - validating SimpleTool architecture

This module contains unit tests to ensure that the Chat tool
(now using SimpleTool architecture) maintains proper functionality.
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tools.chat import ChatRequest, ChatTool
from tools.shared.exceptions import ToolExecutionError


@pytest.fixture(autouse=True)
def artifact_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(tmp_path))


class TestChatTool:
    """Test suite for ChatSimple tool"""

    def setup_method(self):
        """Set up test fixtures"""
        self.tool = ChatTool()

    def test_tool_metadata(self):
        """Test that tool metadata matches requirements"""
        assert self.tool.get_name() == "chat"
        assert "collaborative thinking" in self.tool.get_description()
        assert self.tool.get_system_prompt() is not None
        assert self.tool.get_default_temperature() > 0
        assert self.tool.get_model_category() is not None

    def test_schema_structure(self):
        """Test that schema has correct structure"""
        schema = self.tool.get_input_schema()

        # Basic schema structure
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "required" in schema

        # Required fields
        assert "prompt" in schema["required"]
        assert "working_directory_absolute_path" in schema["required"]

        # Properties
        properties = schema["properties"]
        assert "prompt" in properties
        assert "absolute_file_paths" in properties
        assert "images" in properties
        assert "working_directory_absolute_path" in properties

    def test_request_model_validation(self):
        """Test that the request model validates correctly"""
        # Test valid request
        request_data = {
            "prompt": "Test prompt",
            "absolute_file_paths": ["test.txt"],
            "images": ["test.png"],
            "model": "anthropic/claude-opus-4.1",
            "temperature": 0.7,
            "working_directory_absolute_path": "/tmp",  # Dummy absolute path
        }

        request = ChatRequest(**request_data)
        assert request.prompt == "Test prompt"
        assert request.absolute_file_paths == ["test.txt"]
        assert request.images == ["test.png"]
        assert request.model == "anthropic/claude-opus-4.1"
        assert request.temperature == 0.7
        assert request.working_directory_absolute_path == "/tmp"

    def test_required_fields(self):
        """Test that required fields are enforced"""
        # Missing prompt should raise validation error
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ChatRequest(model="anthropic/claude-opus-4.1", working_directory_absolute_path="/tmp")

    def test_model_availability(self):
        """Test that model availability works"""
        models = self.tool._get_available_models()
        assert len(models) > 0  # Should have some models
        assert isinstance(models, list)

    def test_model_field_schema(self):
        """Test that model field schema generation works correctly"""
        schema = self.tool.get_model_field_schema()

        assert schema["type"] == "string"
        assert "description" in schema

        # Description should route callers to listmodels, regardless of mode
        assert "listmodels" in schema["description"]
        if self.tool.is_effective_auto_mode():
            assert "auto mode" in schema["description"].lower()
        else:
            import config

            assert f"'{config.DEFAULT_MODEL}'" in schema["description"]

    @pytest.mark.asyncio
    async def test_prompt_preparation(self):
        """Test that prompt preparation works correctly"""
        request = ChatRequest(
            prompt="Test prompt",
            absolute_file_paths=[],
            working_directory_absolute_path="/tmp",
        )

        # Mock the system prompt and file handling
        with patch.object(self.tool, "get_system_prompt", return_value="System prompt"):
            with patch.object(self.tool, "handle_prompt_file_with_fallback", return_value="Test prompt"):
                with patch.object(self.tool, "_prepare_file_content_for_prompt", return_value=("", [])):
                    with patch.object(self.tool, "_validate_token_limit"):
                        with patch.object(self.tool, "get_websearch_instruction", return_value=""):
                            prompt = await self.tool.prepare_prompt(request)

                            assert "Test prompt" in prompt
                            assert prompt.startswith("=== USER REQUEST ===")
                            assert "System prompt" not in prompt

    def test_response_formatting(self):
        """Test that response formatting works correctly"""
        response = "Test response content"
        request = ChatRequest(prompt="Test", working_directory_absolute_path="/tmp")

        formatted = self.tool.format_response(response, request)

        assert "Test response content" in formatted
        assert "AGENT'S TURN:" in formatted
        assert "Evaluate this perspective" in formatted

    def test_format_response_multiple_generated_code_blocks(self, tmp_path):
        """The final generated-code block is saved to a unique artifact."""
        tool = ChatTool()
        tool._model_context = SimpleNamespace(capabilities=SimpleNamespace(allow_code_generation=True))

        response = (
            "Intro text\n"
            "<GENERATED-CODE>print('hello')</GENERATED-CODE>\n"
            "Other text\n"
            "<GENERATED-CODE>print('world')</GENERATED-CODE>"
        )

        request = ChatRequest(prompt="Test", working_directory_absolute_path=str(tmp_path))

        formatted = tool.format_response(response, request)

        (saved_path,) = (tmp_path / ".pal_artifacts").glob("*.code")
        saved_content = saved_path.read_text(encoding="utf-8")

        assert "print('world')" in saved_content
        assert "print('hello')" not in saved_content
        assert saved_content.count("<GENERATED-CODE>") == 1
        assert "<GENERATED-CODE>print('hello')" in formatted
        assert str(saved_path) in formatted

    def test_format_response_single_generated_code_block(self, tmp_path):
        """Single <GENERATED-CODE> block should be saved and removed from narrative."""
        tool = ChatTool()
        tool._model_context = SimpleNamespace(capabilities=SimpleNamespace(allow_code_generation=True))

        response = (
            "Intro text before code.\n"
            "<GENERATED-CODE>print('only-once')</GENERATED-CODE>\n"
            "Closing thoughts after code."
        )

        request = ChatRequest(prompt="Test", working_directory_absolute_path=str(tmp_path))

        formatted = tool.format_response(response, request)

        (saved_path,) = (tmp_path / ".pal_artifacts").glob("*.code")
        saved_content = saved_path.read_text(encoding="utf-8")

        assert "print('only-once')" in saved_content
        assert "<GENERATED-CODE>" in saved_content
        assert "print('only-once')" not in formatted
        assert "Closing thoughts after code." in formatted

    def test_format_response_ignores_unclosed_generated_code(self, tmp_path):
        """Unclosed generated-code tags should be ignored to avoid accidental clipping."""
        tool = ChatTool()
        tool._model_context = SimpleNamespace(capabilities=SimpleNamespace(allow_code_generation=True))

        response = "Intro text\n<GENERATED-CODE>print('oops')\nStill ongoing"

        request = ChatRequest(prompt="Test", working_directory_absolute_path=str(tmp_path))

        formatted = tool.format_response(response, request)

        saved_path = tmp_path / "pal_generated.code"
        assert not saved_path.exists()
        assert "print('oops')" in formatted

    def test_format_response_ignores_orphaned_closing_tag(self, tmp_path):
        """Stray closing tags should not trigger extraction."""
        tool = ChatTool()
        tool._model_context = SimpleNamespace(capabilities=SimpleNamespace(allow_code_generation=True))

        response = "Intro text\n</GENERATED-CODE> just text"

        request = ChatRequest(prompt="Test", working_directory_absolute_path=str(tmp_path))

        formatted = tool.format_response(response, request)

        saved_path = tmp_path / "pal_generated.code"
        assert not saved_path.exists()
        assert "</GENERATED-CODE> just text" in formatted

    def test_format_response_preserves_narrative_after_generated_code(self, tmp_path):
        """Narrative content after generated code must remain intact in the formatted output."""
        tool = ChatTool()
        tool._model_context = SimpleNamespace(capabilities=SimpleNamespace(allow_code_generation=True))

        response = (
            "Summary before code.\n"
            "<GENERATED-CODE>print('demo')</GENERATED-CODE>\n"
            "### Follow-up\n"
            "Further analysis and guidance after the generated snippet.\n"
        )

        request = ChatRequest(prompt="Test", working_directory_absolute_path=str(tmp_path))

        formatted = tool.format_response(response, request)

        assert "Summary before code." in formatted
        assert "### Follow-up" in formatted
        assert "Further analysis and guidance after the generated snippet." in formatted
        assert "print('demo')" not in formatted

    def test_tool_name(self):
        """Test tool name is correct"""
        assert self.tool.get_name() == "chat"

    def test_websearch_guidance(self):
        """Test web search guidance matches Chat tool style"""
        guidance = self.tool.get_websearch_guidance()
        chat_style_guidance = self.tool.get_chat_style_websearch_guidance()

        assert guidance == chat_style_guidance
        assert "Documentation for any technologies" in guidance
        assert "Current best practices" in guidance

    def test_convenience_methods(self):
        """Test SimpleTool convenience methods work correctly"""
        assert self.tool.supports_custom_request_model()

        # Test that the tool fields are defined correctly
        tool_fields = self.tool.get_tool_fields()
        assert "prompt" in tool_fields
        assert "absolute_file_paths" in tool_fields
        assert "images" in tool_fields

        required_fields = self.tool.get_required_fields()
        assert "prompt" in required_fields
        assert "working_directory_absolute_path" in required_fields


class TestChatRequestModel:
    """Test suite for ChatRequest model"""

    def test_field_descriptions(self):
        """Test that field descriptions are proper"""
        from tools.chat import CHAT_FIELD_DESCRIPTIONS

        # Field descriptions should exist and be descriptive
        assert len(CHAT_FIELD_DESCRIPTIONS["prompt"]) > 50
        assert "context" in CHAT_FIELD_DESCRIPTIONS["prompt"]
        files_desc = CHAT_FIELD_DESCRIPTIONS["absolute_file_paths"].lower()
        assert "absolute" in files_desc
        assert "visual context" in CHAT_FIELD_DESCRIPTIONS["images"]
        assert "directory" in CHAT_FIELD_DESCRIPTIONS["working_directory_absolute_path"].lower()

    def test_working_directory_absolute_path_description_matches_behavior(self):
        """Absolute working directory description should reflect existing-directory requirement."""
        from tools.chat import CHAT_FIELD_DESCRIPTIONS

        description = CHAT_FIELD_DESCRIPTIONS["working_directory_absolute_path"].lower()
        assert "existing directory" in description

    @pytest.mark.asyncio
    async def test_working_directory_absolute_path_must_exist(self, tmp_path):
        """Chat tool should reject non-existent working directories."""
        tool = ChatTool()
        missing_dir = tmp_path / "nonexistent_subdir"

        with pytest.raises(ToolExecutionError) as exc_info:
            await tool.execute(
                {
                    "prompt": "test",
                    "absolute_file_paths": [],
                    "images": [],
                    "working_directory_absolute_path": str(missing_dir),
                }
            )

        payload = json.loads(exc_info.value.payload)
        assert payload["status"] == "error"
        assert "existing directory" in payload["content"].lower()

    def test_default_values(self):
        """Test that default values work correctly"""
        request = ChatRequest(prompt="Test", working_directory_absolute_path="/tmp")

        assert request.prompt == "Test"
        assert request.absolute_file_paths == []  # Should default to empty list
        assert request.images == []  # Should default to empty list

    def test_inheritance(self):
        """Test that ChatRequest properly inherits from ToolRequest"""
        from tools.shared.base_models import ToolRequest

        request = ChatRequest(prompt="Test", working_directory_absolute_path="/tmp")
        assert isinstance(request, ToolRequest)

        # Should have inherited fields
        assert hasattr(request, "model")
        assert hasattr(request, "temperature")
        assert hasattr(request, "thinking_mode")
        assert hasattr(request, "continuation_id")
        assert hasattr(request, "images")  # From base model too


if __name__ == "__main__":
    pytest.main([__file__])


class TestChatArtifacts:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("continuation", [False, True])
    @pytest.mark.parametrize("target_kind", ["outside", "traversal", "sandbox_link", "root_link", "working_link"])
    async def test_policy_rejected_before_generation(self, tmp_path, monkeypatch, continuation, target_kind):
        root = tmp_path / "workspace"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(root))
        working = root
        if target_kind == "outside":
            working = outside
        elif target_kind == "traversal":
            working = root / ".." / "workspace"
        elif target_kind == "sandbox_link":
            (root / ".pal_artifacts").symlink_to(outside, target_is_directory=True)
        elif target_kind == "working_link":
            working = root / "linked"
            working.symlink_to(outside, target_is_directory=True)
        else:
            linked = tmp_path / "root-link"
            linked.symlink_to(root, target_is_directory=True)
            monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(linked))
        tool = ChatTool()
        context = SimpleNamespace(capabilities=SimpleNamespace(allow_code_generation=True))
        with patch.object(tool, "prepare_prompt") as prepare:
            with pytest.raises(ToolExecutionError) as exc:
                await tool.execute(
                    {
                        "prompt": "=== CONVERSATION HISTORY ===" if continuation else "generate",
                        "working_directory_absolute_path": str(working),
                        "continuation_id": "some-thread" if continuation else None,
                        "_model_context": context,
                    }
                )
        assert json.loads(exc.value.payload)["status"] == "error"
        prepare.assert_not_called()
        assert not list(outside.iterdir())

    @pytest.mark.parametrize("existing_kind", ["file", "symlink", "dangling_symlink"])
    def test_exclusive_destination_never_overwrites(self, tmp_path, monkeypatch, existing_kind):
        sandbox = tmp_path / ".pal_artifacts"
        sandbox.mkdir()
        destination = sandbox / "pal_generated_collision.code"
        original = tmp_path / "original"
        if existing_kind != "dangling_symlink":
            original.write_text("keep me")
        if existing_kind == "file":
            destination.write_text("keep me")
        else:
            destination.symlink_to(original)
        monkeypatch.setattr("tools.chat.uuid4", lambda: SimpleNamespace(hex="collision"))
        with pytest.raises(FileExistsError):
            ChatTool()._persist_generated_code_block("replacement", str(tmp_path))
        if existing_kind != "dangling_symlink":
            assert original.read_text() == "keep me"
            assert destination.read_text() == "keep me"
        else:
            assert destination.is_symlink()
            assert not original.exists()

    def test_concurrent_artifacts_in_nested_directory(self, tmp_path):
        nested = tmp_path / "nested directory"
        nested.mkdir()
        (nested / "pal_generated.code").write_text("old artifact")
        tool = ChatTool()
        with ThreadPoolExecutor(max_workers=2) as pool:
            paths = list(
                pool.map(lambda content: tool._persist_generated_code_block(content, str(nested)), ["one", "two"])
            )
        assert len(set(paths)) == 2
        assert [path.read_text() for path in paths] == ["one\n", "two\n"]
        assert all(path.parent == nested / ".pal_artifacts" for path in paths)
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in paths)
        assert (nested / "pal_generated.code").read_text() == "old artifact"

    def test_root_is_captured_at_construction(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PAL_WORKSPACE_ROOT")
        monkeypatch.chdir(tmp_path)
        tool = ChatTool()
        other = tmp_path / "other"
        other.mkdir()
        monkeypatch.chdir(other)
        monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(other))
        assert tool._persist_generated_code_block("code", str(tmp_path)).parent == tmp_path / ".pal_artifacts"

    def test_read_paths_independent_of_artifact_root(self, tmp_path):
        tool = ChatTool()
        request = ChatRequest(prompt="explain", working_directory_absolute_path="/tmp", absolute_file_paths=[__file__])
        assert tool._validate_file_paths(request) is None
        tool._model_context = SimpleNamespace(capabilities=SimpleNamespace(allow_code_generation=False))
        tool.validate_model_request(request)
        assert not (tmp_path / ".pal_artifacts").exists()

    def test_unsupported_platform_fails_explicitly(self, tmp_path, monkeypatch):
        monkeypatch.setattr(os, "supports_dir_fd", set())
        with pytest.raises(OSError, match="unsupported on this platform"):
            ChatTool()._persist_generated_code_block("code", str(tmp_path))

    @pytest.mark.asyncio
    async def test_write_failure_is_tool_error_without_inline_fallback(self, tmp_path):
        from providers.gemini import GeminiModelProvider
        from providers.shared import ModelResponse

        tool = ChatTool()
        provider = GeminiModelProvider(api_key="synthetic-test-key")
        with (
            patch("providers.registry.ModelProviderRegistry.get_provider_for_model", return_value=provider),
            patch.object(tool, "_model_supports_code_generation", return_value=True),
            patch.object(tool, "_persist_generated_code_block", side_effect=PermissionError("artifact write denied")),
            patch(
                "providers.gemini.GeminiModelProvider.generate_content",
                return_value=ModelResponse(
                    content="<GENERATED-CODE>proposal</GENERATED-CODE>",
                    model_name="gemini-2.5-flash",
                    provider="google",
                ),
            ),
        ):
            with pytest.raises(ToolExecutionError) as exc:
                await tool.execute(
                    {
                        "prompt": "generate",
                        "model": "gemini-2.5-flash",
                        "working_directory_absolute_path": str(tmp_path),
                    }
                )
        payload = json.loads(exc.value.payload)
        assert payload["status"] == "error"
        assert "artifact write denied" in payload["content"]
        assert "<GENERATED-CODE>" not in payload["content"]
        assert "AGENT'S TURN" not in payload["content"]

    @pytest.mark.parametrize("continued", [False, True])
    def test_history_keeps_raw_model_response(self, tmp_path, continued):
        from utils.conversation_memory import create_thread, get_thread

        tool = ChatTool()
        tool._model_context = SimpleNamespace(capabilities=SimpleNamespace(allow_code_generation=True))
        raw = "<GENERATED-CODE>raw model proposal</GENERATED-CODE>"
        request = ChatRequest(
            prompt="generate",
            working_directory_absolute_path=str(tmp_path),
            continuation_id=create_thread("chat", {}) if continued else None,
        )
        result = tool._parse_response(raw, request)
        assert ".pal_artifacts" in result.content
        thread = get_thread(result.continuation_offer.continuation_id)
        replies = [turn.content for turn in thread.turns if turn.role == "assistant"]
        assert replies == [raw]
