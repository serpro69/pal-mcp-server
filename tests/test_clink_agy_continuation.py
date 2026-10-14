"""Real agy preparation/history through direct and MCP calls; fake only the CLI."""

import asyncio
import json
import logging
import os
import sys

import pytest

import server
from simulator_tests.test_clink_agy_validation import agy_server_environment, run_agy_validation
from tests import test_clink_agy as agy_fixtures
from tests import test_clink_continuation as continuation_fixtures
from tools.clink import CLinkTool
from tools.shared.exceptions import ToolExecutionError
from utils import conversation_memory as memory

registry = agy_fixtures.registry
storage = continuation_fixtures.storage


def test_live_environment_preserves_auth_routing_and_excludes_unrelated_secrets(monkeypatch, tmp_path):
    routing = {
        "AGY_ADC_AUTH": "true",
        "GOOGLE_APPLICATION_CREDENTIALS": "/synthetic/adc.json",
        "GOOGLE_CLOUD_QUOTA_PROJECT": "synthetic-project",
        "GOOGLE_CLOUD_LOCATION": "synthetic-location",
    }
    for name, value in {**routing, "GEMINI_API_KEY": "excluded", "UNRELATED_SECRET": "excluded"}.items():
        monkeypatch.setenv(name, value)
    env = agy_server_environment(tmp_path)
    assert all(env[name] == value for name, value in routing.items())
    assert "GEMINI_API_KEY" not in env and "UNRELATED_SECRET" not in env
    assert env["PAL_SKIP_DOTENV"] == "true"
    assert env["CLI_CLIENTS_CONFIG_PATH"] == str(tmp_path / "clients")


def reply(content):
    return agy_fixtures.stream(agy_fixtures.result_event(content)).encode()


@pytest.fixture
def cli_processes(monkeypatch):
    state = {"processes": [], "outputs": []}

    async def spawn(*args, **kwargs):
        child = agy_fixtures.CapturedProcess()
        if state["outputs"]:
            child.stdout, child.returncode = state["outputs"].pop(0)
        state["processes"].append((args, child))
        return child

    monkeypatch.setattr("clink.agents.base.resolve_executable", lambda *args, **kwargs: sys.executable)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return state


@pytest.fixture(params=["direct", "mcp"])
def invoke(registry, storage, request):
    async def call(arguments):
        result = (
            await server.handle_call_tool("clink", arguments)
            if request.param == "mcp"
            else await CLinkTool().execute(arguments)
        )
        return json.loads(result[0].text)

    return call


def input_text(captured):
    command, process = captured
    assert len(process.inputs) == 1
    assert not {"--continue", "-c", "--conversation"}.intersection(command)
    return json.loads(process.inputs[0])["message"]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["default", "planner", "codereviewer"])
async def test_roles_and_continuation_record_each_raw_exchange_once(registry, storage, cli_processes, invoke, role):
    cli_processes["outputs"] = [(reply("FIRST_ANSWER"), 0), (reply("SECOND_ANSWER"), 0)]
    first = await invoke({"cli_name": "agy", "role": role, "prompt": "FIRST_INPUT"})
    thread_id = first["continuation_offer"]["continuation_id"]
    assert storage.compare_and_setex.call_count == 1
    second = await invoke({"continuation_id": thread_id, "prompt": "SECOND_INPUT"})
    assert storage.compare_and_setex.call_count == 2
    assert len(cli_processes["processes"]) == 2
    assert cli_processes["processes"][0][1] is not cli_processes["processes"][1][1]
    role_prompt = registry.get_client("agy").get_role(role).prompt_path.read_text().strip()
    first_input, second_input = map(input_text, cli_processes["processes"])
    assert first_input.count(role_prompt) == second_input.count(role_prompt) == 1
    assert first_input.count("FIRST_INPUT") == 1
    for marker in ("FIRST_INPUT", "FIRST_ANSWER", "SECOND_INPUT"):
        assert second_input.count(marker) == 1
    assert second["metadata"]["cli_name"] == "agy" and second["metadata"]["role"] == role
    assert second["metadata"]["history"]["retained_messages"] == 2
    assert second["continuation_offer"]["continuation_id"] == thread_id
    assert [turn.content for turn in memory.get_thread(thread_id).turns] == [
        "FIRST_INPUT",
        "FIRST_ANSWER",
        "SECOND_INPUT",
        "SECOND_ANSWER",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("continued", [False, True])
@pytest.mark.parametrize(
    "output,code",
    [
        (reply("successful-looking output"), 2),
        (agy_fixtures.stream(agy_fixtures.result_event(status="ERROR", error="Sign in with agy.")).encode(), 0),
        (b'{"event":"init"}\n', 0),
        (b"malformed", 0),
    ],
)
async def test_failed_exchange_never_changes_history(storage, cli_processes, invoke, continued, output, code):
    arguments = {"cli_name": "agy", "role": "codereviewer", "prompt": "FAILED_CURRENT_INPUT"}
    if continued:
        first = await invoke({"cli_name": "agy", "role": "planner", "prompt": "PREVIOUS_INPUT"})
        arguments["continuation_id"] = first["continuation_offer"]["continuation_id"]
    snapshot = storage._store.copy()
    writes = storage.compare_and_setex.call_count
    cli_processes["outputs"] = [(output, code)]
    with pytest.raises(ToolExecutionError):
        await invoke(arguments)
    assert storage._store == snapshot
    assert storage.compare_and_setex.call_count == writes
    assert len(cli_processes["processes"]) == 1 + int(continued)


@pytest.mark.asyncio
async def test_new_multi_client_call_requires_selection_and_sole_client_is_implicit(registry, cli_processes, invoke):
    with pytest.raises(ToolExecutionError, match="cli_name is required"):
        await invoke({"prompt": "hello"})
    assert not cli_processes["processes"]
    registry._clients = {"agy": registry.get_client("agy")}
    result = await invoke({"prompt": "hello", "role": "planner"})
    assert result["metadata"]["cli_name"] == "agy" and result["metadata"]["role"] == "planner"


@pytest.mark.asyncio
async def test_explicit_role_override_replaces_successful_saved_role(cli_processes, invoke):
    first = await invoke({"cli_name": "agy", "role": "planner", "prompt": "FIRST_INPUT"})
    second = await invoke(
        {
            "continuation_id": first["continuation_offer"]["continuation_id"],
            "cli_name": "agy",
            "role": "codereviewer",
            "prompt": "SECOND_INPUT",
        }
    )
    assert second["metadata"]["role"] == "codereviewer"
    assert "You are an external CLI code reviewer" in input_text(cli_processes["processes"][1])
    assert "You are the planning agent" not in input_text(cli_processes["processes"][1])


@pytest.mark.asyncio
async def test_history_budget_keeps_newest_whole_exchange(registry, storage, cli_processes, invoke):
    old = "OLD_INPUT_" + "x" * 5000
    first = await invoke({"cli_name": "agy", "prompt": old})
    thread_id = first["continuation_offer"]["continuation_id"]
    await invoke({"continuation_id": thread_id, "prompt": "RETAINED_INPUT"})
    registry.get_client("agy").history_token_budget = 300
    third = await invoke({"continuation_id": thread_id, "prompt": "CURRENT_INPUT"})
    prepared = input_text(cli_processes["processes"][2])
    assert "OLD_INPUT_" not in prepared
    assert prepared.count("RETAINED_INPUT") == prepared.count("CURRENT_INPUT") == 1
    assert third["metadata"]["history"]["retained_messages"] == 2
    assert third["metadata"]["history"]["omitted_messages"] == 2
    assert third["metadata"]["history"]["estimated_tokens"] <= 300
    assert len(memory.get_thread(thread_id).turns) == 6


@pytest.mark.asyncio
async def test_display_limit_preserves_raw_storage_but_forwards_visible_reply(storage, cli_processes, invoke):
    raw = "HIDDEN_PREFIX" + "x" * 21000 + "<SUMMARY>VISIBLE_SUMMARY</SUMMARY>"
    cli_processes["outputs"] = [(reply(raw), 0)]
    first = await invoke({"cli_name": "agy", "prompt": "FIRST_INPUT"})
    assert first["content"] == "VISIBLE_SUMMARY" and first["metadata"]["output_summarized"] is True
    thread_id = first["continuation_offer"]["continuation_id"]
    assert memory.get_thread(thread_id).turns[1].content == raw
    await invoke({"continuation_id": thread_id, "prompt": "SECOND_INPUT"})
    prepared = input_text(cli_processes["processes"][1])
    assert prepared.count("VISIBLE_SUMMARY") == 1
    assert "HIDDEN_PREFIX" not in prepared


@pytest.mark.integration
@pytest.mark.parametrize("role", ["default", "planner", "codereviewer"])
@pytest.mark.parametrize("configured_cwd", [False, True], ids=["scratch", "project"])
def test_live_agy_role_read_and_history(role, configured_cwd):
    """Run in each prepared personal/enterprise environment; never inspect account tokens."""
    if os.environ.get("PAL_RUN_AGY_INTEGRATION") != "1":
        pytest.skip("Set PAL_RUN_AGY_INTEGRATION=1 with agy 1.3.5 already authenticated")
    evidence = run_agy_validation(logging.getLogger(__name__), role=role, configured_cwd=configured_cwd)
    print("AGY_ROLE_EVIDENCE " + json.dumps(evidence, sort_keys=True))
