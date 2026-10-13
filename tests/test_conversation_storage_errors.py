"""Storage failures must not look like missing context or lose a generated answer."""

import json
import uuid
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from providers.gemini import GeminiModelProvider
from providers.shared import ModelResponse
from tools.chat import ChatRequest, ChatTool
from tools.planner import PlannerTool
from tools.shared.exceptions import ToolExecutionError
from utils import conversation_memory as memory


class FaultStorage:
    def __init__(self):
        self.data = {}
        self.read_error = None
        self.write_error = None
        self.reject_write = False

    def get(self, key):
        if self.read_error:
            raise self.read_error
        return self.data.get(key)

    def setex(self, key, ttl, value):
        if self.write_error:
            raise self.write_error
        if self.reject_write:
            return False
        self.data[key] = value

    def compare_and_setex(self, key, ttl, expected, value):
        if self.data.get(key) != expected:
            return False
        return self.setex(key, ttl, value) is not False


@pytest.fixture
def storage(monkeypatch):
    backend = FaultStorage()
    monkeypatch.setattr(memory, "get_storage", lambda: backend)
    return backend


@pytest.fixture
def chat_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(tmp_path))
    provider = GeminiModelProvider(api_key="synthetic")
    generate = AsyncMock(
        return_value=ModelResponse(content="generated answer", model_name="gemini-2.5-flash", provider="google")
    )
    monkeypatch.setattr(provider, "generate_content", generate)
    monkeypatch.setattr("providers.registry.ModelProviderRegistry.get_provider_for_model", lambda _: provider)
    return generate


def test_absent_and_expired_storage_is_not_an_outage(storage):
    assert memory.get_thread(str(uuid.uuid4())) is None
    assert memory.get_thread("invalid") is None
    thread = memory.create_thread("chat", {})
    storage.data.clear()  # The backend removes expired entries and returns None.
    assert memory.get_thread(thread) is None
    assert not memory.add_turn(thread, "user", "x")
    with pytest.raises(memory.ConversationNotFoundError):
        memory.add_turn(thread, "user", "x", required=True)


@pytest.mark.parametrize("stored", ["", "not JSON", "{}", "[]", '{"turns":"invalid"}'])
def test_corrupt_storage_has_typed_cause(storage, stored):
    thread = str(uuid.uuid4())
    storage.data[f"thread:{thread}"] = stored
    with pytest.raises(memory.ConversationDecodeError) as error:
        memory.get_thread(thread)
    assert isinstance(error.value.__cause__, ValidationError)


@pytest.mark.parametrize("operation", ["create", "get", "add", "chain"])
def test_backend_faults_have_typed_original_cause(storage, operation):
    thread = memory.create_thread("chat", {})
    fault = OSError("synthetic storage fault")
    if operation in ("create", "add"):
        storage.write_error = fault
    else:
        storage.read_error = fault
    with pytest.raises(memory.ConversationStorageError) as error:
        if operation == "create":
            memory.create_thread("chat", {})
        elif operation == "get":
            memory.get_thread(thread)
        elif operation == "add":
            memory.add_turn(thread, "user", "x")
        else:
            memory.get_thread_chain(thread)
    assert error.value.__cause__ is fault
    assert "synthetic storage fault" not in str(error.value)


def test_corrupt_parent_is_not_silently_omitted(storage):
    parent = memory.create_thread("chat", {})
    child = memory.create_thread("chat", {}, parent_thread_id=parent)
    storage.data[f"thread:{parent}"] = "corrupt"
    with pytest.raises(memory.ConversationDecodeError):
        memory.get_thread_chain(child)


def test_limit_and_write_rejection_are_distinct(storage, monkeypatch):
    monkeypatch.setattr(memory, "MAX_CONVERSATION_TURNS", 1)
    thread = memory.create_thread("chat", {})
    assert memory.add_turn(thread, "user", "one")
    assert not memory.add_turn(thread, "assistant", "two")
    with pytest.raises(memory.ConversationLimitError):
        memory.add_turn(thread, "assistant", "two", required=True)
    storage.reject_write = True
    with pytest.raises(memory.ConversationStorageError, match="rejected"):
        memory.create_thread("chat", {})


@pytest.mark.parametrize("operation", ["get", "create", "add"])
def test_programming_faults_are_not_storage_outages(storage, operation):
    thread = memory.create_thread("chat", {})
    bug = TypeError("programming error")
    if operation == "get":
        storage.read_error = bug
    else:
        storage.write_error = bug
    with pytest.raises(TypeError) as error:
        if operation == "get":
            memory.get_thread(thread)
        elif operation == "create":
            memory.create_thread("chat", {})
        else:
            memory.add_turn(thread, "user", "x")
    assert error.value is bug


@pytest.mark.asyncio
@pytest.mark.parametrize("through_server", [False, True])
@pytest.mark.parametrize("fault", ["read", "write", "corrupt", "absent", "limit"])
async def test_required_continuation_failure_stops_generation(
    storage, chat_provider, tmp_path, monkeypatch, through_server, fault
):
    import server

    thread = memory.create_thread("chat", {})
    memory.add_turn(thread, "user", "original")
    if fault == "read":
        storage.read_error = OSError("read fault")
    elif fault == "write":
        storage.write_error = OSError("write fault")
    elif fault == "corrupt":
        storage.data[f"thread:{thread}"] = "corrupt"
    elif fault == "absent":
        storage.data.clear()
    else:
        monkeypatch.setattr(memory, "MAX_CONVERSATION_TURNS", 1)
    args = {
        "prompt": "new input",
        "model": "gemini-2.5-flash",
        "continuation_id": thread,
        "working_directory_absolute_path": str(tmp_path),
    }
    with pytest.raises((ToolExecutionError, memory.ConversationMemoryError, ValueError)):
        if through_server:
            await server.handle_call_tool("chat", args)
        else:
            await ChatTool().execute(args)
    chat_provider.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("continued", [False, True])
async def test_generated_answer_survives_final_write_failure(storage, chat_provider, tmp_path, continued):
    args = {"prompt": "input", "model": "gemini-2.5-flash", "working_directory_absolute_path": str(tmp_path)}
    if continued:
        args["continuation_id"] = memory.create_thread("chat", {})

    def answer(**kwargs):
        storage.write_error = OSError("final write fault")
        return ModelResponse(content="generated answer", model_name="gemini-2.5-flash", provider="google")

    chat_provider.side_effect = answer
    result = json.loads((await ChatTool().execute(args))[0].text)
    assert "generated answer" in result["content"]
    assert result["metadata"]["memory_persistence_warning"]["code"] == "ConversationStorageError"
    assert result["metadata"]["conversation_ready"] is False
    assert result["continuation_offer"] is None
    chat_provider.assert_called_once()


def test_response_path_does_not_hide_programming_errors(storage, tmp_path):
    thread_id = memory.create_thread("chat", {})
    storage.write_error = TypeError("serialization bug")
    request = ChatRequest(prompt="input", working_directory_absolute_path=str(tmp_path), continuation_id=thread_id)
    with pytest.raises(TypeError, match="serialization bug"):
        ChatTool()._parse_response("answer", request)


@pytest.mark.asyncio
async def test_workflow_result_survives_save_failure(storage, monkeypatch):
    tool = PlannerTool()
    original = tool.customize_workflow_response

    def fail_after_work(response, request):
        result = original(response, request)
        storage.write_error = OSError("final write fault")
        return result

    monkeypatch.setattr(tool, "customize_workflow_response", fail_after_work)
    result = json.loads(
        (
            await tool.execute(
                {"step": "plan", "step_number": 1, "total_steps": 2, "next_step_required": True, "findings": "found"}
            )
        )[0].text
    )
    assert result["step_number"] == 1
    assert result["metadata"]["memory_persistence_warning"]["code"] == "ConversationStorageError"


@pytest.mark.asyncio
async def test_clink_answer_survives_memory_failure(storage, monkeypatch):
    from clink.agents import AgentOutput
    from clink.parsers.base import ParsedCLIResponse
    from tools.clink import CLinkTool

    class Agent:
        async def run(self, **kwargs):
            storage.write_error = OSError("save failed")
            return AgentOutput(
                parsed=ParsedCLIResponse(content="CLI answer", metadata={}),
                sanitized_command=["gemini"],
                returncode=0,
                stdout="answer",
                stderr="",
                duration_seconds=0.1,
                parser_name="gemini_json",
            )

    monkeypatch.setattr("tools.clink.create_agent", lambda _: Agent())
    result = json.loads((await CLinkTool().execute({"prompt": "input", "cli_name": "gemini"}))[0].text)
    assert result["content"] == "CLI answer"
    assert result["metadata"]["memory_persistence_warning"]["code"] == "ConversationStorageError"
    assert result["continuation_offer"] is None


@pytest.mark.asyncio
async def test_consensus_answer_survives_memory_failure(storage, monkeypatch):
    from tools.consensus import ConsensusTool

    tool = ConsensusTool()

    async def consult(config, request):
        storage.write_error = OSError("save failed")
        return {"model": config["model"], "stance": "neutral", "status": "success", "verdict": "participant answer"}

    monkeypatch.setattr(tool, "_consult_model", consult)
    result = json.loads(
        (
            await tool.execute(
                {
                    "step": "proposal",
                    "step_number": 1,
                    "total_steps": 2,
                    "next_step_required": True,
                    "findings": "initial",
                    "models": [{"model": "flash"}, {"model": "pro"}],
                }
            )
        )[0].text
    )
    assert result["model_response"]["verdict"] == "participant answer"
    assert result["metadata"]["memory_persistence_warning"]["code"] == "ConversationStorageError"
    assert "continuation_offer" not in result


@pytest.mark.asyncio
async def test_workflow_history_fault_stops_work(storage, monkeypatch):
    thread = memory.create_thread("planner", {})
    storage.read_error = OSError("read failed")
    tool = PlannerTool()
    work = Mock(side_effect=AssertionError("must not work"))
    monkeypatch.setattr(tool, "prepare_step_data", work)
    with pytest.raises(ToolExecutionError) as error:
        await tool.execute(
            {"step": "next", "step_number": 2, "total_steps": 3, "next_step_required": True, "continuation_id": thread}
        )
    assert isinstance(error.value.__cause__, memory.ConversationStorageError)
    work.assert_not_called()
