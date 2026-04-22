"""Unit tests for clink safe-by-default CLI execution (issue #417)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from clink.agents.base import BaseCLIAgent
from clink.agents.claude import ClaudeAgent
from clink.models import ResolvedCLIClient, ResolvedCLIRole
from tools.clink import CLinkRequest, CLinkTool
from tools.shared.exceptions import ToolExecutionError


def _fake_parser(monkeypatch):
    """Stub out parser resolution so agent construction doesn't require a real CLI parser."""

    class FakeParser:
        name = "fake"

    monkeypatch.setattr("clink.agents.base.get_parser", lambda name: FakeParser())


def _client(
    *,
    name: str = "demo",
    config_args: list[str] | None = None,
    safe_args: list[str] | None = None,
    edit_args: list[str] | None = None,
    runner: str | None = None,
) -> ResolvedCLIClient:
    return ResolvedCLIClient(
        name=name,
        executable=[name],
        working_dir=None,
        internal_args=[],
        config_args=list(config_args or []),
        safe_args=list(safe_args or []),
        edit_args=list(edit_args or []),
        env={},
        timeout_seconds=30,
        parser="fake",
        runner=runner,
        roles={"default": ResolvedCLIRole(name="default", prompt_path=Path("/tmp/p"), role_args=[])},
        output_to_file=None,
    )


def _role() -> ResolvedCLIRole:
    return ResolvedCLIRole(name="default", prompt_path=Path("/tmp/p"), role_args=[])


def test_base_agent_omits_edit_args_when_safe(monkeypatch):
    _fake_parser(monkeypatch)
    client = _client(config_args=["--cfg"], safe_args=["--safe"], edit_args=["--edit"])
    cmd = BaseCLIAgent(client)._build_command(role=_role(), system_prompt=None, allow_edits=False)
    assert cmd == ["demo", "--cfg", "--safe"]


def test_base_agent_applies_edit_args_when_allowed(monkeypatch):
    _fake_parser(monkeypatch)
    client = _client(config_args=["--cfg"], safe_args=["--safe"], edit_args=["--edit"])
    cmd = BaseCLIAgent(client)._build_command(role=_role(), system_prompt=None, allow_edits=True)
    assert cmd == ["demo", "--cfg", "--edit"]


def test_base_agent_has_no_path_restriction_support(monkeypatch):
    _fake_parser(monkeypatch)
    assert BaseCLIAgent.supports_path_restrictions is False


def test_claude_agent_safe_mode_strips_edit_args(monkeypatch):
    _fake_parser(monkeypatch)
    client = _client(
        name="claude",
        config_args=["--model", "sonnet"],
        safe_args=["--permission-mode", "default"],
        edit_args=["--permission-mode", "acceptEdits"],
        runner="claude",
    )
    cmd = ClaudeAgent(client)._build_command(
        role=_role(), system_prompt="SYS", allow_edits=False, editable_paths=["/tmp/ignored"]
    )
    # Safe mode uses default permission, never acceptEdits, and ignores editable_paths.
    assert "acceptEdits" not in cmd
    assert ["--permission-mode", "default"] == cmd[cmd.index("--permission-mode") : cmd.index("--permission-mode") + 2]
    assert "--allowedTools" not in cmd
    # System prompt still injected.
    assert "--append-system-prompt" in cmd and "SYS" in cmd


def test_claude_agent_edit_mode_adds_allow_listed_paths(monkeypatch):
    _fake_parser(monkeypatch)
    client = _client(
        name="claude",
        config_args=["--model", "sonnet"],
        safe_args=["--permission-mode", "default"],
        edit_args=["--permission-mode", "acceptEdits"],
        runner="claude",
    )
    cmd = ClaudeAgent(client)._build_command(
        role=_role(),
        system_prompt=None,
        allow_edits=True,
        editable_paths=["/work/a", "/work/b"],
    )
    assert "acceptEdits" in cmd
    assert cmd.count("--allowedTools") == 4
    assert "Edit(/work/a)" in cmd
    assert "Write(/work/a)" in cmd
    assert "Edit(/work/b)" in cmd
    assert "Write(/work/b)" in cmd


def test_claude_agent_supports_path_restrictions():
    assert ClaudeAgent.supports_path_restrictions is True


def test_request_validation_editable_paths_require_allow_edits():
    tool = CLinkTool()
    with pytest.raises(ToolExecutionError) as exc:
        asyncio.run(tool.execute({"prompt": "x", "editable_paths": ["/tmp/a"]}))
    assert "allow_edits=true" in str(exc.value)


def test_request_validation_rejects_relative_editable_paths():
    tool = CLinkTool()
    err = tool._validate_editable_paths(
        CLinkRequest(prompt="x", allow_edits=True, editable_paths=["relative/path"])
    )
    assert err and "must be absolute" in err


def test_request_validation_accepts_absolute_editable_paths():
    tool = CLinkTool()
    err = tool._validate_editable_paths(
        CLinkRequest(prompt="x", allow_edits=True, editable_paths=["/tmp/a", "/tmp/b"])
    )
    assert err is None


def test_non_claude_agent_rejects_editable_paths():
    tool = CLinkTool()
    with pytest.raises(ToolExecutionError) as exc:
        asyncio.run(
            tool.execute(
                {
                    "prompt": "x",
                    "cli_name": "gemini",
                    "allow_edits": True,
                    "editable_paths": ["/tmp/a"],
                }
            )
        )
    assert "does not support editable_paths" in str(exc.value)


def test_schema_exposes_new_fields():
    schema = CLinkTool().get_input_schema()
    assert schema["properties"]["allow_edits"]["type"] == "boolean"
    assert schema["properties"]["editable_paths"]["type"] == "array"


@pytest.mark.asyncio
async def test_prompt_relabels_as_untrusted_and_adds_policy_when_safe():
    tool = CLinkTool()
    from clink import get_registry

    role = get_registry().get_client("claude").get_role("default")

    safe_prompt = await tool._prepare_prompt_for_role(
        CLinkRequest(prompt="do things"),
        role,
        system_prompt="SYS",
        include_system_prompt=False,
    )
    assert "UNTRUSTED USER REQUEST" in safe_prompt
    assert "EXECUTION POLICY" in safe_prompt
    assert "do things" in safe_prompt

    edit_prompt = await tool._prepare_prompt_for_role(
        CLinkRequest(prompt="do things", allow_edits=True),
        role,
        system_prompt="SYS",
        include_system_prompt=False,
    )
    assert "UNTRUSTED USER REQUEST" in edit_prompt
    assert "EXECUTION POLICY" not in edit_prompt


@pytest.mark.asyncio
async def test_execute_forwards_allow_edits_to_agent(monkeypatch):
    """End-to-end: tool.execute propagates allow_edits and editable_paths into agent.run."""
    tool = CLinkTool()
    from clink.agents import AgentOutput
    from clink.parsers.base import ParsedCLIResponse

    captured: dict = {}

    class DummyAgent:
        supports_path_restrictions = True

        async def run(self, **kwargs):
            captured.update(kwargs)
            return AgentOutput(
                parsed=ParsedCLIResponse(content="ok", metadata={}),
                sanitized_command=["claude"],
                returncode=0,
                stdout="",
                stderr="",
                duration_seconds=0.01,
                parser_name="fake",
                output_file_content=None,
            )

    monkeypatch.setattr("tools.clink.create_agent", lambda client: DummyAgent())

    result = await tool.execute(
        {
            "prompt": "please",
            "cli_name": "claude",
            "allow_edits": True,
            "editable_paths": ["/tmp/a"],
        }
    )
    assert captured["allow_edits"] is True
    assert captured["editable_paths"] == ["/tmp/a"]
    payload = json.loads(result[0].text)
    assert payload["status"] in {"success", "continuation_available"}
