"""Keep advertised capability/safety contracts while reducing repeated prose."""

import copy
import json
from pathlib import Path

import pytest
from mcp.types import CallToolRequest, CallToolRequestParams

import config
import server
import tools
from scripts.tool_surface import capture_surface
from tools.planner import PlannerTool
from tools.shared.base_tool import BaseTool
from tools.tracer import TracerTool
from utils.runtime_capabilities import initialize_runtime_capabilities

BASELINE = Path(__file__).parent / "fixtures/tool_surface_baseline.json"
pytestmark = pytest.mark.no_mock_provider


@pytest.mark.asyncio
async def test_provider_free_surface_retains_all_enabled_independent_tools():
    runtime = initialize_runtime_capabilities(configured_providers=(), enabled_tools=server.TOOLS)
    expected = {
        "clink",
        "version",
        "listmodels",
        "planner",
        "tracer",
        "docgen",
        "challenge",
        "apilookup",
        "jules",
    } & server.TOOLS.keys()
    assert runtime.available_tools == expected
    assert {tool.name for tool in await server.handle_list_tools()} == expected


@pytest.fixture
def surface(monkeypatch, isolated_provider_environment):
    monkeypatch.setattr(config, "DEFAULT_MODEL", "auto")
    monkeypatch.setattr(BaseTool, "_get_ranked_model_summaries", lambda self, limit=5: ([], 0, False))
    monkeypatch.setattr(BaseTool, "_get_restriction_note", lambda self: None)
    return capture_surface()


def test_surface_preserves_names_parameters_types_defaults_and_safety(surface):
    baseline = json.loads(BASELINE.read_text())
    assert surface["tools"].keys() == baseline["tools"].keys() | {"jules"}
    for name, old in baseline["tools"].items():
        expected = copy.deepcopy(old["schema"])
        if name in ("planner", "tracer"):
            expected["required"].remove("model")
            expected["properties"]["model"]["deprecated"] = True
            expected["properties"]["use_assistant_model"].update(default=False, deprecated=True)
        if name == "clink":
            # CLI-only Task 4 permits a follow-up to reuse committed selection. The rule is
            # not encoded as a top-level anyOf: the Anthropic API rejects it and Claude Code
            # drops the tool. It is described on cli_name and enforced at runtime instead.
            expected["required"].remove("cli_name")
            # Antigravity adds one client with the existing shared role names.
            expected["properties"]["cli_name"]["enum"].insert(0, "agy")
            # Project-directory Task 2 adds an optional string/null selection.
            expected["properties"]["working_directory_absolute_path"] = {
                "type": ["string", "null"],
                "default": None,
            }
        assert surface["tools"][name]["schema"] == expected, name
        assert surface["tools"][name]["annotations"] == old["annotations"], name
    for name, field in baseline["tools"]["clink"]["safety_schema"]["properties"].items():
        field = copy.deepcopy(field)
        if name == "cli_name":
            field["enum"].insert(0, "agy")
            field["description"] = field["description"].replace("Available: ", "Available: agy, ")
            field["description"] += (
                ". Required for a new conversation; may be omitted with continuation_id to reuse "
                "the last successful CLI selection."
            )
        elif name == "role":
            field["description"] = field["description"].replace(
                "Optional role preset defined for the selected CLI (defaults to 'default').",
                "Role preset for the selected CLI. Reuses the last successful role on continuation, "
                "otherwise defaults to 'default'.",
            )
            field["description"] = field["description"].replace(
                "Roles per CLI: ", "Roles per CLI: agy: codereviewer, default, planner; "
            )
        elif name == "allow_edits":
            field["description"] = (
                "Opt in to native filesystem edits where supported. Defaults to false. "
                "For agy, false inherits saved permissions and does not guarantee read-only execution; "
                "true requests --mode accept-edits. Agy does not support editable_paths."
            )
        if name != "continuation_id":  # Only shared continuation prose is shortened.
            assert surface["tools"]["clink"]["safety_schema"]["properties"][name] == field
    assert surface["tools"]["clink"]["description"] == baseline["tools"]["clink"]["description"]
    for metric in ("description_bytes", "serialized_bytes"):
        assert sum(surface["tools"][name][metric] for name in baseline["tools"]) < baseline["totals"][metric]


def test_all_systemprompt_modules_and_cli_prompts_are_guarded(surface):
    baseline = json.loads(BASELINE.read_text())
    prompt_dir = Path(__file__).resolve().parents[1] / "systemprompts"
    actual_paths = {str(p.relative_to(prompt_dir)) for p in prompt_dir.rglob("*") if p.suffix in (".py", ".txt")}
    assert set(surface["systemprompts_sha256"]) == actual_paths
    assert set(surface["systemprompts_sha256"]) == set(baseline["systemprompts_sha256"]) | {"jules_prompt.py"}
    # Task 40 deliberately moved the unchanged stance block after shared instructions.
    baseline["systemprompts_sha256"][
        "consensus_prompt.py"
    ] = "2b2dbbe7f3197fa0e8859e13bae950355aad4a3272b89b90e2b308eba8093903"
    for name, digest in baseline["systemprompts_sha256"].items():
        assert surface["systemprompts_sha256"][name] == digest


def test_exported_tools_cover_server_capabilities(monkeypatch):
    exported = {getattr(tools, name)().get_name() for name in tools.__all__}
    # Every registered capability has one implementation/export, not synonyms.
    assert set(server.TOOLS).issubset(exported)
    assert len(exported) == len(tools.__all__) == 19


@pytest.mark.parametrize("tool_class", [PlannerTool, TracerTool])
@pytest.mark.parametrize("default_model", ["auto", "unavailable-default"])
def test_provider_free_schema_never_resolves_models(monkeypatch, tool_class, default_model):
    monkeypatch.setattr(config, "DEFAULT_MODEL", default_model)

    def fail(*args, **kwargs):
        raise AssertionError("Provider-free schema must not resolve a model")

    monkeypatch.setattr(BaseTool, "get_model_field_schema", fail)
    monkeypatch.setattr(BaseTool, "is_effective_auto_mode", fail)
    schema = tool_class().get_input_schema()
    assert "model" not in schema["required"]
    assert schema["properties"]["model"]["deprecated"]
    assert schema["properties"]["use_assistant_model"]["default"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["planner", "tracer"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_provider_free_and_legacy_calls_pass_mcp_schema_validation(
    monkeypatch, isolated_provider_environment, name, legacy
):
    monkeypatch.setattr(config, "DEFAULT_MODEL", "auto")
    monkeypatch.setattr(server.server, "_tool_cache", {})
    args = {"step": "Inspect the next action", "step_number": 1, "total_steps": 1, "next_step_required": False}
    if name == "tracer":
        args.update(findings="A local trace", trace_mode="precision", target_description="trace target")
    if legacy:
        args.update(model="unavailable-legacy-model", use_assistant_model=True)
    result = await server.server.request_handlers[CallToolRequest](
        CallToolRequest(params=CallToolRequestParams(name=name, arguments=args))
    )
    assert result.root.isError is False
    response = json.loads(result.root.content[0].text)
    assert response["metadata"]["model_used"] is None
    assert response["metadata"]["provider_used"] is None


@pytest.mark.asyncio
async def test_legacy_field_transition_keeps_mcp_type_validation(monkeypatch, isolated_provider_environment):
    monkeypatch.setattr(config, "DEFAULT_MODEL", "auto")
    monkeypatch.setattr(server.server, "_tool_cache", {})
    args = {
        "step": "Plan",
        "step_number": 1,
        "total_steps": 1,
        "next_step_required": False,
        "use_assistant_model": "not-a-boolean",
    }
    result = await server.server.request_handlers[CallToolRequest](
        CallToolRequest(params=CallToolRequestParams(name="planner", arguments=args))
    )
    assert result.root.isError is True


def test_short_descriptions_retain_continuation_and_validation_rules(surface):
    from tools.shared.base_models import COMMON_FIELD_DESCRIPTIONS, WORKFLOW_FIELD_DESCRIPTIONS

    continuation = COMMON_FIELD_DESCRIPTIONS["continuation_id"]
    assert "latest continuation_id" in continuation and "across tools" in continuation
    assert "skips external validation" in WORKFLOW_FIELD_DESCRIPTIONS["confidence"]
    assert "False uses only your investigation" in WORKFLOW_FIELD_DESCRIPTIONS["use_assistant_model"]


def test_duplicate_capability_names_are_rejected():
    with pytest.raises(ValueError, match="Duplicate capability"):
        capture_surface([PlannerTool(), PlannerTool()])
