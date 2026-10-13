"""Complete-exchange CLI history, independent of provider context or tokenizers."""

import json
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

import server
from clink.agents import AgentOutput
from clink.constants import DEFAULT_HISTORY_TOKEN_BUDGET
from clink.conversation import render_history
from clink.models import CLIClientConfig, ResolvedCLIClient
from clink.parsers.base import ParsedCLIResponse
from clink.registry import ClinkRegistry
from tools.clink import CLinkTool
from tools.shared.exceptions import ToolExecutionError
from utils import conversation_coordination as coordination
from utils import conversation_memory as memory
from utils.storage_backend import InMemoryStorage
from utils.token_utils import estimate_tokens


def turn(role, content, **kwargs):
    return memory.ConversationTurn(role=role, content=content, timestamp="2026-10-13T00:00:00Z", **kwargs)


def thread(turns):
    return memory.ThreadContext(
        thread_id=str(uuid4()),
        created_at="2026-10-13T00:00:00Z",
        last_updated_at="2026-10-13T00:00:00Z",
        tool_name="chat",
        turns=turns,
        initial_context={},
    )


def test_default_budget_and_empty_history():
    result = render_history(thread([]))
    assert result.text == "" and result.turns == []
    assert result.metadata == {
        "estimated_tokens": 0,
        "token_budget": 16_384,
        "retained_messages": 0,
        "omitted_messages": 0,
        "incomplete_messages": 0,
        "incomplete_legacy_omitted": False,
    }
    assert DEFAULT_HISTORY_TOKEN_BUDGET == 16_384


def test_newest_suffix_keeps_multi_assistant_exchanges_and_order():
    turns = [
        turn("user", "OLD_QUESTION" + "x" * 2000),
        turn("assistant", "OLD_ANSWER"),
        turn("user", "MIDDLE_QUESTION"),
        turn("assistant", "MIDDLE_ANSWER"),
        turn("assistant", "MIDDLE_SECOND_ANSWER"),
        turn("user", "LATEST_QUESTION"),
        turn("assistant", "LATEST_ANSWER"),
    ]
    context = thread(turns)
    before = context.model_dump_json()
    result = render_history(context, token_budget=300)
    assert result.turns == turns[2:]
    assert "OLD_QUESTION" not in result.text and "OLD_ANSWER" not in result.text
    positions = [result.text.index(item.content) for item in turns[2:]]
    assert positions == sorted(positions)
    assert "Omitted 2 messages" in result.text
    assert "Turn 3 (Agent)" in result.text
    assert result.metadata["retained_messages"] == 5
    assert result.metadata["omitted_messages"] == 2
    assert result.metadata["estimated_tokens"] == estimate_tokens(result.text) <= 300
    assert context.model_dump_json() == before


def test_oversized_middle_exchange_does_not_allow_skipping_back_to_an_older_one():
    turns = [
        turn("user", "OLDEST"),
        turn("assistant", "old answer"),
        turn("user", "MIDDLE"),
        turn("assistant", "x" * 3000),
        turn("user", "NEWEST"),
        turn("assistant", "new answer"),
    ]
    result = render_history(thread(turns), token_budget=300)
    assert result.turns == turns[-2:]
    assert "OLDEST" not in result.text and "MIDDLE" not in result.text
    assert result.metadata["omitted_messages"] == 4


def test_exact_final_budget_includes_wrappers_and_guidance():
    context = thread([turn("user", "question"), turn("assistant", "answer")])
    complete = render_history(context)
    exact_budget = estimate_tokens(complete.text)
    assert exact_budget > estimate_tokens("questionanswer")
    assert render_history(context, token_budget=exact_budget).text == complete.text
    with pytest.raises(ValueError, match="newest complete exchange.*history-only budget"):
        render_history(context, token_budget=exact_budget - 1)


def test_omission_notice_is_included_in_final_budget():
    latest = [turn("user", "latest question"), turn("assistant", "latest answer")]
    context = thread(latest)
    budget = estimate_tokens(render_history(context).text)
    context.turns = [turn("user", "x" * 5000), turn("assistant", "old answer")] + latest
    with pytest.raises(ValueError, match="newest complete exchange"):
        render_history(context, token_budget=budget)


@pytest.mark.parametrize("field", ["files", "images"])
def test_path_references_are_rendered_and_charged_to_budget(field):
    reference = "/unread-historical-" + "x" * 4000
    context = thread([turn("user", "question", **{field: [reference]}), turn("assistant", "answer")])
    result = render_history(context)
    assert f"{field.title()}: {reference}" in result.text
    assert result.metadata["estimated_tokens"] == estimate_tokens(result.text)
    with pytest.raises(ValueError, match="including wrappers and references"):
        render_history(context, token_budget=300)


@pytest.mark.parametrize(
    "legacy",
    [
        [("user", "UNANSWERED")],
        [("assistant", "ORPHAN")],
        [("system", "UNSUPPORTED")],
        [("user", "UNANSWERED"), ("system", "UNSUPPORTED"), ("assistant", "ORPHAN")],
    ],
)
def test_nonempty_history_without_a_complete_exchange_is_an_error(legacy):
    with pytest.raises(ValueError, match="no complete exchange"):
        render_history(thread([turn(role, content) for role, content in legacy]))


def test_incomplete_legacy_groups_are_counted_and_never_repaired():
    context = thread(
        [
            turn("assistant", "ORPHAN"),
            turn("user", "UNANSWERED"),
            turn("user", "COMPLETE_QUESTION"),
            turn("assistant", "COMPLETE_ANSWER"),
            turn("assistant", "SECOND_ANSWER"),
            turn("user", "TRAILING_USER"),
        ]
    )
    before = context.model_dump_json()
    result = render_history(context)
    assert result.turns == context.turns[2:5]
    assert result.metadata["incomplete_messages"] == 3
    assert result.metadata["omitted_messages"] == 3
    assert result.metadata["incomplete_legacy_omitted"] is True
    assert "3 from incomplete legacy groups" in result.text
    for marker in ("ORPHAN", "UNANSWERED", "TRAILING_USER"):
        assert marker not in result.text
    assert context.model_dump_json() == before


@pytest.mark.parametrize("visible", ["DISPLAYED_REPLY", ""])
def test_assistant_display_projection_preserves_raw_record(visible):
    context = thread(
        [
            turn("user", "ORIGINAL_INPUT", model_metadata={"clink": {"visible_reply": "WRONG_USER_TEXT"}}),
            turn(
                "assistant", "RAW_REPLY", model_metadata={"clink": {"visible_reply": visible}, "usage": {"tokens": 10}}
            ),
        ]
    )
    result = render_history(context)
    assert "ORIGINAL_INPUT" in result.text and "WRONG_USER_TEXT" not in result.text
    assert "RAW_REPLY" not in result.text
    if visible:
        assert visible in result.text
    assert context.turns[1].content == "RAW_REPLY"
    assert context.turns[1].model_metadata["usage"] == {"tokens": 10}


@pytest.mark.parametrize("metadata", [None, {}, {"clink": None}, {"clink": {"visible_reply": None}}])
def test_legacy_reply_without_display_projection_uses_raw_content(metadata):
    result = render_history(thread([turn("user", "question"), turn("assistant", "RAW_REPLY", model_metadata=metadata)]))
    assert "RAW_REPLY" in result.text


def config_data(model):
    if model is CLIClientConfig:
        return {"name": "gemini", "command": "gemini"}
    return {
        "name": "gemini",
        "executable": ["gemini"],
        "working_dir": None,
        "timeout_seconds": 30,
        "parser": "gemini_json",
        "roles": {},
    }


@pytest.mark.parametrize("model", [CLIClientConfig, ResolvedCLIClient])
@pytest.mark.parametrize("value", [0, -1, 1.5, 8192.0, True, False, "8192", "invalid", None, [], {}])
@pytest.mark.parametrize("json_input", [False, True], ids=["python", "json"])
def test_history_budget_rejects_invalid_types_and_nonpositive_values(model, value, json_input):
    data = {**config_data(model), "history_token_budget": value}
    with pytest.raises(ValidationError, match="history_token_budget"):
        model.model_validate_json(json.dumps(data)) if json_input else model.model_validate(data)


@pytest.mark.parametrize("model", [CLIClientConfig, ResolvedCLIClient])
@pytest.mark.parametrize("value", [1, 8192, 16_384, 32_768])
def test_history_budget_accepts_positive_integers(model, value):
    result = model.model_validate_json(json.dumps({**config_data(model), "history_token_budget": value}))
    assert result.history_token_budget == value


@pytest.mark.parametrize("model", [CLIClientConfig, ResolvedCLIClient])
def test_history_budget_defaults_when_omitted(model):
    assert model.model_validate(config_data(model)).history_token_budget == 16_384


@pytest.fixture
def manifest_dir(monkeypatch, tmp_path):
    builtins = tmp_path / "clients"
    builtins.mkdir()
    monkeypatch.setattr("clink.registry.CONFIG_DIR", builtins)
    monkeypatch.setattr("clink.registry.USER_CONFIG_DIR", tmp_path / "unused-user-clients")
    monkeypatch.setattr("clink.registry.get_env", lambda name: None)
    return builtins


def write_manifest(directory, name="gemini", **values):
    path = directory / f"{name}.json"
    path.write_text(json.dumps({"name": name, "command": name, **values}))
    return path


@pytest.mark.parametrize("value", [0, -1, 1.5, 8192.0, True, False, "8192", "invalid", None])
def test_invalid_manifest_budget_fails_registry_load(manifest_dir, value):
    write_manifest(manifest_dir, history_token_budget=value)
    with pytest.raises(ValidationError, match="history_token_budget"):
        ClinkRegistry()


def test_registry_preserves_each_clients_explicit_or_default_budget(manifest_dir):
    write_manifest(manifest_dir, "gemini", history_token_budget=8192)
    write_manifest(manifest_dir, "claude", history_token_budget=32_768)
    write_manifest(manifest_dir, "codex")
    registry = ClinkRegistry()
    assert registry.get_client("gemini").history_token_budget == 8192
    assert registry.get_client("claude").history_token_budget == 32_768
    assert registry.get_client("codex").history_token_budget == 16_384


def test_budget_manifest_precedence_and_existing_registry_snapshot(manifest_dir, tmp_path, monkeypatch):
    configured, user = tmp_path / "configured", tmp_path / "user"
    configured.mkdir()
    user.mkdir()
    write_manifest(manifest_dir, history_token_budget=8192)
    write_manifest(configured, history_token_budget=24_000)
    write_manifest(user, history_token_budget=32_768)
    monkeypatch.setattr("clink.registry.get_env", lambda name: str(configured))
    assert ClinkRegistry().get_client("gemini").history_token_budget == 24_000
    monkeypatch.setattr("clink.registry.USER_CONFIG_DIR", user)
    existing = ClinkRegistry()
    assert existing.get_client("gemini").history_token_budget == 32_768
    write_manifest(user, history_token_budget=48_000)
    assert existing.get_client("gemini").history_token_budget == 32_768
    assert ClinkRegistry().get_client("gemini").history_token_budget == 48_000


@pytest.fixture
def runtime(manifest_dir, monkeypatch, tmp_path):
    write_manifest(manifest_dir, "gemini", history_token_budget=300)
    write_manifest(manifest_dir, "claude", history_token_budget=32_768)
    write_manifest(manifest_dir, "codex")
    registry = ClinkRegistry()
    monkeypatch.setattr("tools.clink.get_registry", lambda: registry)
    monkeypatch.setattr(InMemoryStorage, "_cleanup_worker", lambda self: None)
    storage = InMemoryStorage()
    monkeypatch.setattr(memory, "get_storage", lambda: storage)
    monkeypatch.setenv("PAL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PAL_TRANSCRIPTS", "false")
    agent = Mock()
    agent.run = AsyncMock(
        return_value=AgentOutput(
            parsed=ParsedCLIResponse("CLI_REPLY", {}),
            sanitized_command=["fake"],
            returncode=0,
            stdout="",
            stderr="",
            duration_seconds=0.1,
            parser_name="fake",
        )
    )
    monkeypatch.setattr("tools.clink.create_agent", lambda _: agent)
    blocked = Mock(side_effect=AssertionError("History budgets must not resolve PAL providers or model context"))
    monkeypatch.setattr("providers.registry.ModelProviderRegistry.get_provider_for_model", blocked)
    monkeypatch.setattr("providers.registry.ModelProviderRegistry.get_preferred_fallback_model", blocked)
    monkeypatch.setattr("utils.model_context.ModelContext", blocked)
    yield registry, storage, agent
    storage.shutdown()
    blocked.assert_not_called()
    assert not coordination._entries


@pytest.fixture(params=["direct", "mcp"])
def invoke(runtime, request):
    tool = CLinkTool()

    async def call(arguments):
        result = (
            await server.handle_call_tool("clink", arguments)
            if request.param == "mcp"
            else await tool.execute(arguments)
        )
        return json.loads(result[0].text)

    return call


def seed_history(*, saved_cli="claude", large_latest=False):
    thread_id = memory.create_thread("chat", {"clink_defaults": {"cli_name": saved_cli, "role": "default"}})
    if not large_latest:
        memory.add_turn(thread_id, "user", "OLD_INPUT" + "x" * 70_000, files=["/old-file.py"])
        memory.add_turn(thread_id, "assistant", "OLD_REPLY")
    memory.add_turn(thread_id, "user", "LATEST_INPUT", files=["/latest-file.py"], images=["/latest-image.png"])
    memory.add_turn(thread_id, "assistant", "LATEST_REPLY" + ("x" * 70_000 if large_latest else ""))
    return thread_id


@pytest.mark.asyncio
@pytest.mark.parametrize("cli_name,budget,retained", [("gemini", 300, 2), ("codex", 16_384, 2), ("claude", 32_768, 4)])
async def test_selected_client_controls_complete_exchanges_and_exact_history_budget(
    runtime, invoke, cli_name, budget, retained
):
    _, _, agent = runtime
    thread_id = seed_history()
    current = "CURRENT_INPUT" + "y" * 2000
    result = await invoke({"cli_name": cli_name, "prompt": current, "continuation_id": thread_id})
    info = result["metadata"]["history"]
    assert info["token_budget"] == budget
    assert info["retained_messages"] == retained and info["omitted_messages"] == 4 - retained
    prompt = agent.run.call_args.kwargs["prompt"]
    assert prompt.count(current) == 1  # The current request can exceed the history budget.
    start = prompt.index("=== CONVERSATION HISTORY")
    end = prompt.index("\n\n=== NEW USER INPUT", start)
    assert info["estimated_tokens"] == estimate_tokens(prompt[start:end]) <= budget
    assert "LATEST_INPUT" in prompt and "LATEST_REPLY" in prompt
    assert ("OLD_INPUT" in prompt) is (retained == 4)
    assert ("OLD_REPLY" in prompt) is (retained == 4)
    assert ("/old-file.py" in prompt) is (retained == 4)
    assert prompt.count("/latest-file.py") == prompt.count("/latest-image.png") == 1
    assert memory.get_thread(thread_id).turns[-2].content == current


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", ["saved", "explicit"])
async def test_larger_selected_budget_applies_before_default_could_reject_history(runtime, invoke, selection):
    _, _, agent = runtime
    thread_id = seed_history(saved_cli="claude" if selection == "saved" else "gemini", large_latest=True)
    arguments = {"prompt": "current", "continuation_id": thread_id}
    if selection == "explicit":
        arguments["cli_name"] = "claude"
    result = await invoke(arguments)
    assert result["metadata"]["cli_name"] == "claude"
    assert result["metadata"]["history"]["token_budget"] == 32_768
    assert result["metadata"]["history"]["estimated_tokens"] > 16_384
    assert "LATEST_REPLY" + "x" * 70_000 in agent.run.call_args.kwargs["prompt"]


@pytest.mark.asyncio
@pytest.mark.parametrize("cli_name,budget", [("gemini", 300), ("codex", 16_384)])
async def test_small_budget_rejects_newest_exchange_without_launch_or_memory_change(runtime, invoke, cli_name, budget):
    _, storage, agent = runtime
    thread_id = seed_history(large_latest=True)
    snapshot = storage._store.copy()
    with pytest.raises(ToolExecutionError, match=f"history-only budget of {budget}"):
        await invoke({"cli_name": cli_name, "prompt": "current", "continuation_id": thread_id})
    agent.run.assert_not_awaited()
    assert storage._store == snapshot


@pytest.mark.asyncio
async def test_budget_is_configuration_only_and_current_request_is_outside_it(runtime, invoke):
    result = await invoke({"cli_name": "gemini", "prompt": "x" * 2000, "history_token_budget": 1})
    assert result["metadata"]["history"]["token_budget"] == 300
    assert result["metadata"]["history"]["estimated_tokens"] == 0
    assert "history_token_budget" not in CLinkTool().get_input_schema()["properties"]
    followup = await invoke(
        {"continuation_id": result["continuation_offer"]["continuation_id"], "prompt": "next", "cli_name": "claude"}
    )
    assert followup["metadata"]["history"]["token_budget"] == 32_768


@pytest.mark.asyncio
async def test_client_budget_accounts_for_omission_notice_at_exact_boundary(runtime, invoke, monkeypatch):
    registry, storage, agent = runtime
    thread_id = seed_history()
    snapshot = storage._store.copy()
    context = memory.get_thread(thread_id)
    # This budget fits the final complete exchange alone, but not the additional
    # omission notice required by the older exchange in the actual snapshot.
    newest = context.model_copy(update={"turns": context.turns[-2:]})
    budget = estimate_tokens(render_history(newest).text)
    client = registry.get_client("gemini")
    monkeypatch.setattr(client, "history_token_budget", budget)
    with pytest.raises(ToolExecutionError, match="newest complete exchange"):
        await invoke({"cli_name": "gemini", "continuation_id": thread_id, "prompt": "next"})
    assert storage._store == snapshot
    agent.run.assert_not_awaited()
