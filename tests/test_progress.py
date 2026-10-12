"""MCP progress is isolated, bounded, optional, and never owns the answer."""

import asyncio
import json
from datetime import timedelta

import anyio
import pytest
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.context import RequestContext
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import RequestParams, TextContent

import server
from providers.gemini import GeminiModelProvider
from providers.shared import ModelResponse
from tests.request_capture import capture_providers
from tests.test_conversation_storage_errors import FaultStorage
from tools.shared.exceptions import ToolExecutionError
from utils import conversation_memory as memory
from utils import progress


class Session:
    def __init__(self):
        self.notifications = []
        self.updated = asyncio.Queue()

    async def send_progress_notification(self, **kwargs):
        self.notifications.append(kwargs)
        self.updated.put_nowait(kwargs)


def context(session, token):
    return RequestContext(
        request_id=1, meta=RequestParams.Meta(progressToken=token), session=session, lifespan_context={}
    )


def assert_no_heartbeat():
    assert not [task for task in asyncio.all_tasks() if task.get_name() == "pal-progress-heartbeat"]


@pytest.mark.parametrize("token", [None, 0, "a-token"])
async def test_optional_progress_and_safe_usage(token):
    session = Session()
    async with progress.request_progress(context(session, token)) as reporter:
        async with progress.progress_operation("provider") as operation:
            operation.usage = {"input_tokens": 0, "output_tokens": 4, "total_tokens": "bad", "prompt": "SECRET"}
    assert progress._current.get() is None
    assert_no_heartbeat()
    assert reporter.metadata()["operations"][0]["usage"] == {"input_tokens": 0, "output_tokens": 4}
    assert reporter.metadata()["operations"][0]["status"] == "completed"
    if token is None:
        assert not session.notifications
    else:
        assert [item["progress"] for item in session.notifications] == [1, 2, 3, 4]
        assert {item["progress_token"] for item in session.notifications} == {token}
        assert "SECRET" not in json.dumps(session.notifications)
        assert json.loads(session.notifications[-2]["message"])["usage"]["input_tokens"] == 0


@pytest.mark.parametrize("failure", ["error", "timeout", "final"])
async def test_notification_failures_preserve_answer_and_warn(monkeypatch, failure):
    session = Session()
    original = session.send_progress_notification

    async def send(**kwargs):
        if failure == "timeout":
            await asyncio.Event().wait()
        if failure == "error" or (failure == "final" and json.loads(kwargs["message"])["stage"] == "completed"):
            raise RuntimeError("SECRET credentials must not enter diagnostics")
        await original(**kwargs)

    session.send_progress_notification = send
    monkeypatch.setattr(progress, "NOTIFICATION_TIMEOUT_SECONDS", 0.01)
    async with progress.request_progress(context(session, 0)) as reporter:
        async with progress.progress_operation("provider"):
            answer = [TextContent(type="text", text=json.dumps({"content": "answer", "metadata": {"keep": 1}}))]
    result = json.loads(reporter.annotate(answer)[0].text)
    assert result["content"] == "answer"
    assert result["metadata"]["keep"] == 1
    assert len(result["metadata"]["execution"]["progress_warnings"]) == 1
    assert "SECRET" not in json.dumps(result)
    assert_no_heartbeat()


async def test_concurrent_requests_keep_tokens_counters_and_usage_separate(monkeypatch):
    monkeypatch.setattr(progress, "HEARTBEAT_SECONDS", 0.001)
    session = Session()
    entered = asyncio.Queue()
    release = asyncio.Event()

    async def invoke(token):
        async with progress.request_progress(context(session, token)) as reporter:
            async with progress.progress_operation("provider") as operation:
                entered.put_nowait(token)
                await release.wait()
                operation.usage = {"input_tokens": token}
        assert progress._current.get() is None
        return reporter.metadata()

    tasks = [asyncio.create_task(invoke(token)) for token in (0, 7)]
    try:
        assert {await asyncio.wait_for(entered.get(), 2) for _ in tasks} == {0, 7}
        heard = set()
        while len(heard) < 2:
            item = await asyncio.wait_for(session.updated.get(), 2)
            if item["progress"] >= 3:
                heard.add(item["progress_token"])
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), 2)
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    for token, result in zip((0, 7), results):
        assert result["operations"][0]["usage"] == {"input_tokens": token}
        notices = [item for item in session.notifications if item["progress_token"] == token]
        assert [item["progress"] for item in notices] == list(range(1, len(notices) + 1))
    assert_no_heartbeat()


@pytest.mark.parametrize("outcome", ["failure", "cancel"])
async def test_operation_exit_joins_heartbeat_and_resets_context(monkeypatch, outcome):
    monkeypatch.setattr(progress, "HEARTBEAT_SECONDS", 0.001)
    session = Session()
    entered = asyncio.Event()
    release = asyncio.Event()
    reset = []

    async def run():
        try:
            async with progress.request_progress(context(session, "cancel")):
                async with progress.progress_operation("expert"):
                    entered.set()
                    await release.wait()
                    raise ValueError("provider failure")
        finally:
            reset.append(progress._current.get())

    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        if outcome == "cancel":
            task.cancel()
        else:
            release.set()
        with pytest.raises(asyncio.CancelledError if outcome == "cancel" else ValueError):
            await asyncio.wait_for(task, 2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert reset == [None]
    assert_no_heartbeat()


@pytest.mark.parametrize("kind", ["primary", "recovery", "expert", "consensus"])
async def test_dispatch_records_only_current_provider_operations(monkeypatch, tmp_path, kind):
    monkeypatch.setattr(memory, "get_storage", lambda: storage)
    storage = FaultStorage()
    monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(tmp_path))
    capture_providers(monkeypatch)
    calls = []

    async def generate(self, **kwargs):
        calls.append(kwargs)
        return ModelResponse(
            content="" if kind == "recovery" and len(calls) == 1 else "answer",
            model_name=kwargs["model_name"],
            provider="google",
            usage={"input_tokens": len(calls)},
            metadata={"finish_reason": "STOP"},
        )

    monkeypatch.setattr(GeminiModelProvider, "generate_content", generate)
    session = Session()
    token = request_ctx.set(context(session, 0))
    try:
        if kind in {"primary", "recovery"}:
            result = await server.handle_call_tool(
                "chat",
                {"prompt": "SECRET", "model": "gemini-2.5-flash", "working_directory_absolute_path": str(tmp_path)},
            )
        else:
            args = {
                "step": "SECRET",
                "findings": "SECRET",
                "step_number": 1,
                "total_steps": 1,
                "next_step_required": False,
                "model": "gemini-2.5-flash",
            }
            if kind == "consensus":
                args["models"] = [{"model": "gemini-2.5-flash"}, {"model": "gpt-5"}]
            result = await server.handle_call_tool("thinkdeep" if kind == "expert" else "consensus", args)
    finally:
        request_ctx.reset(token)
    metadata = json.loads(result[0].text)["metadata"]["execution"]
    assert [op["stage"] for op in metadata["operations"]] == {
        "primary": ["provider"],
        "recovery": ["provider", "provider_retry"],
        "expert": ["expert"],
        "consensus": ["consensus"],
    }[kind]
    assert [op["usage"] for op in metadata["operations"]] == [{"input_tokens": n} for n in range(1, len(calls) + 1)]
    assert "SECRET" not in json.dumps(session.notifications)
    assert progress._current.get() is None


async def test_actual_mcp_v1_session_progress_round_trip(monkeypatch):
    async def dispatch(name, arguments):
        async with progress.progress_operation("provider"):
            return [TextContent(type="text", text="plain answer")]

    monkeypatch.setattr(server, "_dispatch_tool", dispatch)
    received = []

    async def callback(value, total, message):
        received.append((value, total, json.loads(message)))

    async with create_connected_server_and_client_session(server.server, timedelta(seconds=5)) as client:
        result = await client.call_tool("version", {}, progress_callback=callback)
    assert not result.isError
    assert result.content[0].text == "plain answer"
    assert result.content[0].meta["pal_execution"]["operations"][0]["stage"] == "provider"
    assert [value for value, _, _ in received] == [1, 2, 3, 4]
    assert all(total is None for _, total, _ in received)
    assert_no_heartbeat()


async def test_repeated_cancellation_joins_inflight_notification(monkeypatch):
    monkeypatch.setattr(progress, "HEARTBEAT_SECONDS", 0.001)
    in_notification = asyncio.Event()
    cleaning = asyncio.Event()
    release = asyncio.Event()
    cleaned = asyncio.Event()
    session = Session()
    original = session.send_progress_notification

    async def send(**kwargs):
        if kwargs["progress"] == 3:
            in_notification.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
                cleaned.set()
        await original(**kwargs)

    session.send_progress_notification = send

    async def run():
        async with progress.request_progress(context(session, 0)):
            async with progress.progress_operation("provider"):
                await asyncio.Event().wait()

    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(in_notification.wait(), 2)
        task.cancel()
        await asyncio.wait_for(cleaning.wait(), 2)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert cleaned.is_set()
    assert_no_heartbeat()


async def test_sdk_anyio_cancel_scope_still_joins_heartbeat():
    session = Session()
    with anyio.CancelScope() as scope:
        async with progress.request_progress(context(session, 0)):
            async with progress.progress_operation("provider"):
                scope.cancel()
                await anyio.lowlevel.checkpoint()
    assert progress._current.get() is None
    assert_no_heartbeat()


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("notification_failure", [False, True])
async def test_mcp_error_keeps_execution_metadata_and_terminal_progress(monkeypatch, structured, notification_failure):
    if notification_failure:

        async def fail_notification(*args, **kwargs):
            raise OSError("SECRET transport details")

        monkeypatch.setattr("mcp.server.session.ServerSession.send_progress_notification", fail_notification)

    async def dispatch(name, arguments):
        async with progress.progress_operation("provider"):
            raise ToolExecutionError(
                json.dumps({"status": "error", "content": "provider failed"}) if structured else "provider failed"
            )

    monkeypatch.setattr(server, "_dispatch_tool", dispatch)
    received = []

    async def callback(value, total, message):
        received.append(json.loads(message)["stage"])

    async with create_connected_server_and_client_session(server.server, timedelta(seconds=5)) as client:
        result = await client.call_tool("version", {}, progress_callback=callback)
    assert result.isError
    payload = json.loads(result.content[0].text)
    assert payload["content"] == "provider failed"
    execution = payload["metadata"]["execution"]
    assert execution["duration_seconds"] >= 0
    assert execution["operations"][0]["status"] == "failed"
    if notification_failure:
        assert received == []
        assert len(execution["progress_warnings"]) == 1
        assert "SECRET" not in json.dumps(execution)
    else:
        assert received == ["started", "provider", "failed"]
    assert_no_heartbeat()
