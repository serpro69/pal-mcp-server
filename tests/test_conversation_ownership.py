"""Invocation-level contracts shared by MCP dispatch and direct tool calls."""

import json
from unittest.mock import Mock

import pytest

import server
from clink.agents import AgentOutput
from clink.parsers.base import ParsedCLIResponse
from providers.gemini import GeminiModelProvider
from providers.shared import ModelResponse
from tests.request_capture import capture_providers
from tests.test_conversation_storage_errors import FaultStorage
from tools.chat import ChatTool
from tools.clink import CLinkTool
from tools.consensus import ConsensusTool
from tools.planner import PlannerTool
from tools.shared.exceptions import ToolExecutionError
from utils import conversation_memory as memory
from utils.conversation_context import current_invocation


@pytest.fixture
def setup(monkeypatch, tmp_path):
    storage = FaultStorage()
    monkeypatch.setattr(memory, "get_storage", lambda: storage)
    monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(tmp_path))
    calls = capture_providers(monkeypatch)
    return storage, calls


async def invoke(tool, args, through_server, monkeypatch):
    if through_server:
        monkeypatch.setitem(server.TOOLS, tool.get_name(), tool)
        return json.loads((await server.handle_call_tool(tool.get_name(), args))[0].text)
    return json.loads((await tool.execute(args))[0].text)


def chat_args(tmp_path, **extra):
    return {
        "prompt": "current input sentinel",
        "model": "gemini-2.5-flash",
        "working_directory_absolute_path": str(tmp_path),
        **extra,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("through_server", [False, True])
async def test_two_calls_record_four_raw_turns_once(setup, monkeypatch, tmp_path, through_server):
    storage, calls = setup
    tool = ChatTool()
    first = await invoke(tool, chat_args(tmp_path, prompt="first input sentinel"), through_server, monkeypatch)
    thread_id = first["continuation_offer"]["continuation_id"]
    second = await invoke(tool, chat_args(tmp_path, continuation_id=thread_id), through_server, monkeypatch)
    assert [turn.content for turn in memory.get_thread(thread_id).turns] == [
        "first input sentinel",
        "Gemini answer",
        "current input sentinel",
        "Gemini answer",
    ]
    for content in ["first input sentinel", "Gemini answer", "current input sentinel"]:
        assert calls[-1]["prompt"].count(content) == 1
    assert second["continuation_offer"]["remaining_turns"] == 46
    assert "23 more exchanges" in second["continuation_offer"]["note"]
    assert current_invocation(tool) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("through_server", [False, True])
@pytest.mark.parametrize("cap", [1, 2, 3, 5])
async def test_pair_admission_and_odd_caps(setup, monkeypatch, tmp_path, through_server, cap):
    storage, calls = setup
    monkeypatch.setattr(memory, "MAX_CONVERSATION_TURNS", cap)
    tool = ChatTool()
    if cap == 1:
        with pytest.raises(ToolExecutionError, match="two free message slots"):
            await invoke(tool, chat_args(tmp_path), through_server, monkeypatch)
        assert not storage.data and not calls
        return
    thread_id = memory.create_thread("chat", {})
    for index in range(cap - 2):
        memory.add_turn(thread_id, "user", f"older {index}")
    result = await invoke(tool, chat_args(tmp_path, continuation_id=thread_id), through_server, monkeypatch)
    thread = memory.get_thread(thread_id)
    assert len(thread.turns) == cap
    assert [turn.role for turn in thread.turns[-2:]] == ["user", "assistant"]
    assert result["continuation_offer"]["remaining_turns"] == 0
    assert "more exchanges" not in result["continuation_offer"]["note"]
    assert "Do NOT include any follow-up questions" in calls[-1]["prompt"]
    snapshot = storage.data.copy()
    with pytest.raises(ToolExecutionError, match="two free message slots"):
        await invoke(tool, chat_args(tmp_path, continuation_id=thread_id), through_server, monkeypatch)
    assert storage.data == snapshot and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("through_server", [False, True])
async def test_one_free_slot_rejected_without_saving_input(setup, monkeypatch, tmp_path, through_server):
    storage, calls = setup
    monkeypatch.setattr(memory, "MAX_CONVERSATION_TURNS", 3)
    thread = memory.create_thread("chat", {})
    memory.add_turn(thread, "user", "old input")
    memory.add_turn(thread, "assistant", "old reply")
    snapshot = storage.data.copy()
    with pytest.raises(ToolExecutionError, match="two free message slots"):
        await invoke(ChatTool(), chat_args(tmp_path, continuation_id=thread), through_server, monkeypatch)
    assert storage.data == snapshot and calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("through_server", [False, True])
async def test_failure_keeps_input_and_next_invocation_uses_remaining_pair(
    setup, monkeypatch, tmp_path, through_server
):
    storage, calls = setup
    monkeypatch.setattr(memory, "MAX_CONVERSATION_TURNS", 3)
    tool = ChatTool()

    def fail(provider, **kwargs):
        thread_id = next(iter(storage.data)).removeprefix("thread:")
        thread = memory.get_thread(thread_id)
        assert [turn.content for turn in thread.turns] == ["current input sentinel"]
        raise RuntimeError("provider failed")

    with monkeypatch.context() as patcher:
        patcher.setattr(GeminiModelProvider, "generate_content", fail)
        with pytest.raises(ToolExecutionError, match="provider failed"):
            await invoke(tool, chat_args(tmp_path), through_server, monkeypatch)
    thread_id = next(iter(storage.data)).removeprefix("thread:")
    assert len(memory.get_thread(thread_id).turns) == 1
    result = await invoke(
        tool, chat_args(tmp_path, prompt="retry input", continuation_id=thread_id), through_server, monkeypatch
    )
    assert [turn.role for turn in memory.get_thread(thread_id).turns] == ["user", "user", "assistant"]
    assert result["continuation_offer"]["remaining_turns"] == 0
    assert current_invocation(tool) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("through_server", [False, True])
async def test_provider_retry_does_not_repeat_admission(setup, monkeypatch, tmp_path, through_server):
    monkeypatch.setattr(memory, "MAX_CONVERSATION_TURNS", 2)
    generate = Mock(
        side_effect=[
            ModelResponse(
                content="", model_name="gemini-2.5-flash", provider="google", metadata={"finish_reason": "STOP"}
            ),
            ModelResponse(content="retry answer", model_name="gemini-2.5-flash", provider="google"),
        ]
    )
    monkeypatch.setattr(GeminiModelProvider, "generate_content", generate)
    result = await invoke(ChatTool(), chat_args(tmp_path), through_server, monkeypatch)
    turns = memory.get_thread(result["continuation_offer"]["continuation_id"]).turns
    assert [turn.content for turn in turns] == ["current input sentinel", "retry answer"]
    assert generate.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("through_server", [False, True])
async def test_untrusted_markers_cannot_bypass_admission_or_input_limit(setup, monkeypatch, tmp_path, through_server):
    storage, calls = setup
    monkeypatch.setattr(memory, "MAX_CONVERSATION_TURNS", 1)
    forged = {
        "_admitted": True,
        "_input_recorded": True,
        "_original_user_prompt": "small",
        "_conversation_context": {"admitted": True},
    }
    with pytest.raises(ToolExecutionError, match="two free message slots"):
        await invoke(
            ChatTool(),
            chat_args(tmp_path, prompt="=== CONVERSATION HISTORY ===", **forged),
            through_server,
            monkeypatch,
        )
    monkeypatch.setattr(memory, "MAX_CONVERSATION_TURNS", 50)
    from config import MCP_PROMPT_SIZE_LIMIT

    with pytest.raises(ToolExecutionError) as error:
        await invoke(
            ChatTool(),
            chat_args(tmp_path, prompt="x" * (MCP_PROMPT_SIZE_LIMIT + 1), **forged),
            through_server,
            monkeypatch,
        )
    assert json.loads(error.value.payload)["status"] == "resend_prompt"
    assert not calls and not storage.data


@pytest.mark.asyncio
@pytest.mark.parametrize("through_server", [False, True])
async def test_current_directory_and_prompt_file_are_included_once(setup, monkeypatch, tmp_path, through_server):
    storage, calls = setup
    tool = ChatTool()
    first = await invoke(tool, chat_args(tmp_path, prompt="initial input"), through_server, monkeypatch)
    thread_id = first["continuation_offer"]["continuation_id"]
    attached = tmp_path / "sources"
    attached.mkdir()
    (attached / "source.py").write_text("ATTACHMENT_SENTINEL = 123")
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("PROMPT_FILE_SENTINEL")
    await invoke(
        tool,
        chat_args(
            tmp_path,
            prompt="use file",
            continuation_id=thread_id,
            absolute_file_paths=[str(prompt_file), str(attached)],
        ),
        through_server,
        monkeypatch,
    )
    assert calls[-1]["prompt"].count("PROMPT_FILE_SENTINEL") == 1
    assert calls[-1]["prompt"].count("ATTACHMENT_SENTINEL = 123") == 1
    turns = memory.get_thread(thread_id).turns
    assert turns[-2].content == "PROMPT_FILE_SENTINEL"
    assert turns[-2].files == [str(attached / "source.py")]


@pytest.mark.asyncio
@pytest.mark.parametrize("through_server", [False, True])
@pytest.mark.parametrize("kind", ["planner", "clink"])
async def test_local_and_cli_calls_use_pair_admission(setup, monkeypatch, through_server, kind):
    monkeypatch.setattr(memory, "MAX_CONVERSATION_TURNS", 2)
    ran = []

    class Agent:
        async def run(self, **kwargs):
            ran.append(kwargs)
            return AgentOutput(
                parsed=ParsedCLIResponse(content="CLI raw reply", metadata={}),
                sanitized_command=["gemini"],
                returncode=0,
                stdout="",
                stderr="",
                duration_seconds=0.1,
                parser_name="gemini_json",
            )

    monkeypatch.setattr("tools.clink.create_agent", lambda _: Agent())
    tool = PlannerTool() if kind == "planner" else CLinkTool()
    args = (
        {"step": "local step", "step_number": 1, "total_steps": 2, "next_step_required": True}
        if kind == "planner"
        else {"prompt": "CLI initial input", "cli_name": "gemini"}
    )
    result = await invoke(tool, args, through_server, monkeypatch)
    thread_id = result["continuation_offer"]["continuation_id"]
    turns = memory.get_thread(thread_id).turns
    assert [turn.role for turn in turns] == ["user", "assistant"]
    if kind == "planner":
        assert turns[0].content == "local step"
        assert json.loads(turns[1].content)["step_number"] == 1
        assert turns[1].model_name is None and turns[1].model_provider is None
        assert result["next_steps"] == result["continuation_offer"]["note"]
    else:
        assert [turn.content for turn in turns] == ["CLI initial input", "CLI raw reply"]
    with pytest.raises(ToolExecutionError, match="two free message slots"):
        await invoke(tool, {**args, "continuation_id": thread_id}, through_server, monkeypatch)
    assert len(memory.get_thread(thread_id).turns) == 2
    assert len(ran) == (1 if kind == "clink" else 0)


@pytest.mark.asyncio
async def test_consensus_participants_are_blinded_but_audit_is_complete(setup, monkeypatch, tmp_path):
    storage, _ = setup
    calls = capture_providers(monkeypatch, google_answer="EARLIER_VERDICT_SENTINEL", openai_answer="second verdict")
    thread_id = memory.create_thread("chat", {})
    memory.add_turn(thread_id, "user", "GENERIC_HISTORY_SENTINEL", tool_name="chat")
    attachment = tmp_path / "proposal.txt"
    attachment.write_text("EXPLICIT_ATTACHMENT_SENTINEL")
    tool = ConsensusTool()
    args = {
        "step": "ORIGINAL_PROPOSAL_SENTINEL",
        "step_number": 1,
        "total_steps": 2,
        "next_step_required": True,
        "findings": "initial",
        "continuation_id": thread_id,
        "relevant_files": [str(attachment)],
        "models": [{"model": "gemini-2.5-flash", "stance": "for"}, {"model": "gpt-5", "stance": "against"}],
    }
    await invoke(tool, args, True, monkeypatch)
    await invoke(
        tool,
        {**args, "step": "CALLER_SUMMARY_SENTINEL", "findings": "CALLER_FINDINGS_SENTINEL", "step_number": 2},
        True,
        monkeypatch,
    )
    assert len(calls) == 2
    for call in calls:
        assert call["prompt"].count("ORIGINAL_PROPOSAL_SENTINEL") == 1
        assert call["prompt"].count("EXPLICIT_ATTACHMENT_SENTINEL") == 1
        for forbidden in [
            "EARLIER_VERDICT_SENTINEL",
            "CALLER_SUMMARY_SENTINEL",
            "CALLER_FINDINGS_SENTINEL",
            "GENERIC_HISTORY_SENTINEL",
        ]:
            assert forbidden not in call["prompt"]
    assert "SUPPORTIVE" in calls[0]["system_prompt"] and "CRITICAL" in calls[1]["system_prompt"]
    turns = memory.get_thread(thread_id).turns
    assert [turn.content for turn in turns] == [
        "GENERIC_HISTORY_SENTINEL",
        "ORIGINAL_PROPOSAL_SENTINEL",
        "EARLIER_VERDICT_SENTINEL",
        "CALLER_SUMMARY_SENTINEL",
        "second verdict",
    ]


@pytest.mark.asyncio
async def test_cross_tool_expert_keeps_prior_context_and_saves_raw_reply(setup, monkeypatch, tmp_path):
    from tools.thinkdeep import ThinkDeepTool

    storage, calls = setup
    first = await invoke(ChatTool(), chat_args(tmp_path, prompt="CHAT_INPUT_SENTINEL"), True, monkeypatch)
    thread_id = first["continuation_offer"]["continuation_id"]
    tool = ThinkDeepTool()
    result = await invoke(
        tool,
        {
            "step": "WORKFLOW_STEP_SENTINEL",
            "findings": "actual findings",
            "step_number": 1,
            "total_steps": 1,
            "next_step_required": False,
            "confidence": "medium",
            "model": "gemini-2.5-flash",
            "continuation_id": thread_id,
        },
        True,
        monkeypatch,
    )
    assert len(calls) == 2
    assert "CHAT_INPUT_SENTINEL" in calls[-1]["prompt"]
    assert "Gemini answer" in calls[-1]["prompt"]
    assert "WORKFLOW_STEP_SENTINEL" in calls[-1]["prompt"]
    turns = memory.get_thread(thread_id).turns
    assert [turn.content for turn in turns] == [
        "CHAT_INPUT_SENTINEL",
        "Gemini answer",
        "WORKFLOW_STEP_SENTINEL",
        "Gemini answer",
    ]
    assert turns[-1].model_name == "gemini-2.5-flash" and turns[-1].model_provider == "google"
    assert result["continuation_offer"]["remaining_turns"] == 46


@pytest.mark.asyncio
async def test_clink_stores_raw_output_before_presentation_limit(setup, monkeypatch):
    from tools.clink import MAX_RESPONSE_CHARS

    raw = "LONG_RAW_SENTINEL " * (MAX_RESPONSE_CHARS // 10)

    class Agent:
        async def run(self, **kwargs):
            return AgentOutput(
                parsed=ParsedCLIResponse(content=raw, metadata={}),
                sanitized_command=["gemini"],
                returncode=0,
                stdout="",
                stderr="",
                duration_seconds=0.1,
                parser_name="gemini_json",
            )

    monkeypatch.setattr("tools.clink.create_agent", lambda _: Agent())
    result = await invoke(CLinkTool(), {"prompt": "long response", "cli_name": "gemini"}, True, monkeypatch)
    thread = memory.get_thread(result["continuation_offer"]["continuation_id"])
    assert thread.turns[0].content == "long response"
    assert thread.turns[1].content == raw
    assert len(result["content"]) < len(raw)


@pytest.mark.asyncio
async def test_explicit_unavailable_model_does_not_fall_back(setup, monkeypatch, tmp_path):
    storage, calls = setup
    thread_id = memory.create_thread("chat", {})
    memory.add_turn(thread_id, "assistant", "prior", model_name="gemini-2.5-flash")
    with pytest.raises(ToolExecutionError, match="not available"):
        await invoke(
            ChatTool(), chat_args(tmp_path, model="nonexistent-model", continuation_id=thread_id), True, monkeypatch
        )
    assert not calls
    assert len(memory.get_thread(thread_id).turns) == 1


@pytest.mark.asyncio
async def test_current_input_survives_history_budget_exhaustion(setup, monkeypatch, tmp_path):
    from utils.model_context import ModelContext, TokenAllocation

    _, calls = setup
    monkeypatch.setattr(
        ModelContext,
        "calculate_token_allocation",
        lambda self: TokenAllocation(
            total_tokens=10000, content_tokens=9000, response_tokens=1000, file_tokens=6000, history_tokens=10
        ),
    )
    thread_id = memory.create_thread("chat", {})
    memory.add_turn(thread_id, "assistant", "old history " * 100)
    attachment = tmp_path / "full.py"
    attachment.write_text("large attachment " * 100)
    prompt_file = tmp_path / "prompt.txt"
    raw_input = "CURRENT_REQUIRED_INPUT " * 40
    prompt_file.write_text(raw_input)
    await invoke(
        ChatTool(),
        chat_args(tmp_path, continuation_id=thread_id, absolute_file_paths=[str(attachment), str(prompt_file)]),
        True,
        monkeypatch,
    )
    assert calls[0]["prompt"].count(raw_input) == 1
    assert "most recent turns out of" in calls[0]["prompt"]


@pytest.mark.asyncio
@pytest.mark.parametrize("through_server", [False, True])
async def test_cli_only_continuation_keeps_attachment_references(setup, monkeypatch, tmp_path, through_server):
    from providers.registry import ModelProviderRegistry

    monkeypatch.setattr(
        ModelProviderRegistry,
        "get_provider_for_model",
        Mock(side_effect=AssertionError("PAL provider must not be used")),
    )
    monkeypatch.setattr(
        ModelProviderRegistry, "get_preferred_fallback_model", Mock(side_effect=AssertionError("no fallback"))
    )
    file = tmp_path / "attached.py"
    file.write_text("FILE_CONTENT_MUST_NOT_BE_EMBEDDED")
    image = tmp_path / "diagram.png"
    image.write_bytes(b"synthetic CLI reference")
    runs = []

    class Agent:
        async def run(self, **kwargs):
            runs.append(kwargs)
            return AgentOutput(
                parsed=ParsedCLIResponse(content="CLI answer", metadata={}),
                sanitized_command=["gemini"],
                returncode=0,
                stdout="",
                stderr="",
                duration_seconds=0.1,
                parser_name="gemini_json",
            )

    monkeypatch.setattr("tools.clink.create_agent", lambda _: Agent())
    tool = CLinkTool()
    first = await invoke(
        tool,
        {"prompt": "CLI initial", "cli_name": "gemini", "absolute_file_paths": [str(file)], "images": [str(image)]},
        through_server,
        monkeypatch,
    )
    await invoke(
        tool,
        {
            "prompt": "CLI followup",
            "cli_name": "gemini",
            "continuation_id": first["continuation_offer"]["continuation_id"],
        },
        through_server,
        monkeypatch,
    )
    assert runs[1]["files"] == [str(file)]
    assert runs[1]["images"] == [str(image)]
    assert str(file) in runs[1]["prompt"]
    for content in ["CLI initial", "CLI answer", "CLI followup"]:
        assert runs[1]["prompt"].count(content) == 1
    assert "FILE_CONTENT_MUST_NOT_BE_EMBEDDED" not in runs[1]["prompt"]


@pytest.mark.asyncio
async def test_simple_continuation_retains_prior_images(setup, monkeypatch, tmp_path):
    _, calls = setup
    image = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    tool = ChatTool()
    first = await invoke(tool, chat_args(tmp_path, images=[image]), True, monkeypatch)
    await invoke(
        tool, chat_args(tmp_path, continuation_id=first["continuation_offer"]["continuation_id"]), True, monkeypatch
    )
    assert calls[0]["images"] == [image]
    assert calls[1]["images"] == [image]


@pytest.mark.asyncio
async def test_expert_current_step_is_present_exactly_once(setup, monkeypatch, tmp_path):
    from tools.analyze import AnalyzeTool

    _, calls = setup
    source = tmp_path / "source.py"
    source.write_text("answer = 42")
    await invoke(
        AnalyzeTool(),
        {
            "step": "EXPERT_CURRENT_INPUT_SENTINEL",
            "findings": "a finding",
            "step_number": 1,
            "relevant_files": [str(source)],
            "total_steps": 1,
            "next_step_required": False,
            "model": "gemini-2.5-flash",
        },
        True,
        monkeypatch,
    )
    assert len(calls) == 1
    assert calls[0]["prompt"].count("EXPERT_CURRENT_INPUT_SENTINEL") == 1


@pytest.mark.asyncio
async def test_new_consensus_round_on_same_thread_keeps_latest_proposal(setup, monkeypatch):
    calls = capture_providers(monkeypatch)
    tool = ConsensusTool()
    args = {
        "step": "OLD_PROPOSAL_SENTINEL",
        "findings": "initial",
        "step_number": 1,
        "total_steps": 2,
        "next_step_required": True,
        "models": [{"model": "gemini-2.5-flash"}, {"model": "gpt-5"}],
    }
    first = await invoke(tool, args, True, monkeypatch)
    thread_id = first["continuation_offer"]["continuation_id"]
    await invoke(tool, {**args, "step": "NEW_PROPOSAL_SENTINEL", "continuation_id": thread_id}, True, monkeypatch)
    await invoke(
        tool, {**args, "step": "CALLER_SUMMARY", "step_number": 2, "continuation_id": thread_id}, True, monkeypatch
    )
    assert "NEW_PROPOSAL_SENTINEL" in calls[-1]["prompt"]
    assert "OLD_PROPOSAL_SENTINEL" not in calls[-1]["prompt"]
    assert "CALLER_SUMMARY" not in calls[-1]["prompt"]
