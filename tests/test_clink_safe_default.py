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


def test_non_claude_agent_rejects_editable_paths_without_invoking_agent(monkeypatch):
    """Rejection must happen during pre-await validation, NOT after agent.run is reached.

    Regression guard: if the supports_path_restrictions check drifts back below
    _prepare_prompt_for_role / create_agent, this test catches it — because
    create_agent is monkeypatched to a factory that tracks calls.
    """
    tool = CLinkTool()
    agent_factory_calls: list = []

    class _ShouldNotRun:
        supports_path_restrictions = False

        async def run(self, **kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("agent.run must not be invoked on a rejected request")

    def tracking_factory(client):
        agent_factory_calls.append(client.name)
        return _ShouldNotRun()

    monkeypatch.setattr("tools.clink.create_agent", tracking_factory)

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
    # The agent factory itself must not have been called either — the guard
    # fires before create_agent in the new placement.
    assert agent_factory_calls == []


def test_schema_exposes_new_fields():
    schema = CLinkTool().get_input_schema()
    assert schema["properties"]["allow_edits"]["type"] == "boolean"
    assert schema["properties"]["editable_paths"]["type"] == "array"


@pytest.mark.asyncio
async def test_prompt_relabels_as_untrusted_and_adds_policy_when_safe(tmp_path):
    tool = CLinkTool()
    # Use a stub role backed by a temp prompt file — avoid depending on
    # the real registry / conf/cli_clients/*.json being present.
    prompt_file = tmp_path / "role.txt"
    prompt_file.write_text("role-system-prompt")
    role = ResolvedCLIRole(name="default", prompt_path=prompt_file, role_args=[])

    safe_prompt = await tool._prepare_prompt_for_role(
        CLinkRequest(prompt="do things"),
        role,
        system_prompt="SYS",
        include_system_prompt=False,
        cli_name="claude",
    )
    assert "UNTRUSTED USER REQUEST" in safe_prompt
    assert "EXECUTION POLICY" in safe_prompt
    assert "do things" in safe_prompt
    assert "claude CLI" in safe_prompt  # _agent_capabilities_guidance is parameterized

    edit_prompt = await tool._prepare_prompt_for_role(
        CLinkRequest(prompt="do things", allow_edits=True),
        role,
        system_prompt="SYS",
        include_system_prompt=False,
        cli_name="claude",
    )
    assert "UNTRUSTED USER REQUEST" in edit_prompt
    assert "EXECUTION POLICY" not in edit_prompt


@pytest.mark.asyncio
async def test_agent_capabilities_guidance_uses_actual_cli_name(tmp_path):
    """Guidance string must name the CLI in use, not hardcode 'Gemini'."""
    tool = CLinkTool()
    prompt_file = tmp_path / "role.txt"
    prompt_file.write_text("role")
    role = ResolvedCLIRole(name="default", prompt_path=prompt_file, role_args=[])

    for cli_name in ("claude", "codex", "gemini"):
        prompt = await tool._prepare_prompt_for_role(
            CLinkRequest(prompt="hi"),
            role,
            system_prompt="SYS",
            include_system_prompt=False,
            cli_name=cli_name,
        )
        assert f"{cli_name} CLI" in prompt, f"guidance must name {cli_name}"


@pytest.mark.parametrize(
    ("cli_name", "dangerous_flag"),
    [
        ("claude", "acceptEdits"),
        ("gemini", "--yolo"),
        ("codex", "--dangerously-bypass-approvals-and-sandbox"),
    ],
)
def test_real_config_gates_dangerous_flag_on_allow_edits(cli_name, dangerous_flag):
    """End-to-end over the real shipped configs: dangerous flag is absent in safe mode,
    present in edit mode, for every CLI we ship."""
    from clink import get_registry
    from clink.agents import create_agent

    client = get_registry().get_client(cli_name)
    agent = create_agent(client)
    role = client.get_role("default")

    safe_cmd = agent._build_command(role=role, system_prompt=None, allow_edits=False)
    edit_cmd = agent._build_command(role=role, system_prompt=None, allow_edits=True)

    assert dangerous_flag not in safe_cmd, f"{cli_name}: safe mode must omit {dangerous_flag}"
    assert dangerous_flag in edit_cmd, f"{cli_name}: edit mode must include {dangerous_flag}"


def test_empty_string_editable_path_rejected():
    tool = CLinkTool()
    err = tool._validate_editable_paths(
        CLinkRequest(prompt="x", allow_edits=True, editable_paths=[""])
    )
    assert err is not None and "must not be empty" in err


@pytest.mark.parametrize("bad_char", ["(", ")", "$", ";", "|", "&", "`", "*", "?"])
def test_editable_path_with_shell_metacharacter_rejected(bad_char):
    tool = CLinkTool()
    err = tool._validate_editable_paths(
        CLinkRequest(prompt="x", allow_edits=True, editable_paths=[f"/tmp/foo{bad_char}bar"])
    )
    assert err is not None and "shell-metacharacters" in err


def test_editable_paths_canonicalized_in_place():
    """`/safe/../etc` traversal is normalized so the downstream allow-list matches what was validated."""
    tool = CLinkTool()
    req = CLinkRequest(prompt="x", allow_edits=True, editable_paths=["/tmp/safe/../etc"])
    err = tool._validate_editable_paths(req)
    assert err is None
    # After validation, the path is the canonical form (no '..' component).
    assert req.editable_paths == ["/tmp/etc"]


def test_config_rejects_dangerous_flag_in_additional_args():
    """model_validator: --yolo in additional_args is a config-level error."""
    from clink.models import CLIClientConfig

    with pytest.raises(ValueError, match="write-enabling"):
        CLIClientConfig(name="x", additional_args=["--yolo"])


def test_config_rejects_claude_accept_edits_in_additional_args():
    """model_validator: '--permission-mode acceptEdits' sequence in additional_args rejected."""
    from clink.models import CLIClientConfig

    with pytest.raises(ValueError, match="acceptEdits"):
        CLIClientConfig(name="x", additional_args=["--permission-mode", "acceptEdits"])


def test_config_rejects_identical_safe_and_edit_args():
    """model_validator: identical safe_args and edit_args make allow_edits a no-op."""
    from clink.models import CLIClientConfig

    with pytest.raises(ValueError, match="must differ"):
        CLIClientConfig(
            name="x",
            safe_args=["--shared", "value"],
            edit_args=["--shared", "value"],
        )


def test_config_allows_same_flag_with_different_values_in_each_bucket():
    """Flag name can appear in both buckets — only the values differ. This is the
    legitimate Claude pattern (--permission-mode default vs acceptEdits)."""
    from clink.models import CLIClientConfig

    c = CLIClientConfig(
        name="x",
        safe_args=["--permission-mode", "default"],
        edit_args=["--permission-mode", "acceptEdits"],
    )
    assert c.safe_args[1] == "default"
    assert c.edit_args[1] == "acceptEdits"


def test_config_accepts_valid_three_bucket_layout():
    """Sanity: the shipped config shape loads without tripping the validator."""
    from clink.models import CLIClientConfig

    c = CLIClientConfig(
        name="claude",
        additional_args=["--model", "sonnet"],
        safe_args=["--permission-mode", "default"],
        edit_args=["--permission-mode", "acceptEdits"],
    )
    assert c.safe_args == ["--permission-mode", "default"]


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
