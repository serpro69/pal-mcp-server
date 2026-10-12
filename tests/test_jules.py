"""Jules contracts use a fake HTTP transport; never start a remote coding task."""

import asyncio
import json

import httpx
import pytest
from mcp.types import CallToolRequest, CallToolRequestParams

import server
from tools.jules import JulesTool
from tools.shared.exceptions import ToolExecutionError

pytestmark = [pytest.mark.asyncio, pytest.mark.no_mock_provider]
CREATE = {"action": "create", "prompt": "Add tests Ω", "source": "sources/github/team/repo", "starting_branch": "main"}
SESSION = {"name": "sessions/opaque-123", "id": "opaque-123", "state": "QUEUED"}


@pytest.fixture
def transport(monkeypatch, isolated_provider_environment):
    monkeypatch.setenv("JULES_API_KEY", "synthetic-jules-key")
    real_client = httpx.AsyncClient
    clients, requests = [], []

    def install(handler):
        async def record(request):
            requests.append(request)
            result = handler(request)
            return await result if asyncio.iscoroutine(result) else result

        def factory(**kwargs):
            client = real_client(transport=httpx.MockTransport(record), **kwargs)
            clients.append(client)
            return client

        monkeypatch.setattr("tools.jules.httpx.AsyncClient", factory)
        return requests, clients

    return install


async def call(arguments):
    result = await JulesTool().execute(arguments)
    return json.loads(result[0].text)


async def test_sources_pagination_auth_and_provider_free_dispatch(transport):
    requests, clients = transport(
        lambda request: httpx.Response(200, json={"sources": [{"name": CREATE["source"]}], "nextPageToken": "next/+?="})
    )
    result = await server.handle_call_tool("jules", {"action": "list_sources", "page_size": 2, "page_token": "old/+?="})
    data = json.loads(result[0].text)
    assert data["sources"] == [{"name": CREATE["source"]}]
    assert data["next_page_token"] == "next/+?="
    request = requests[0]
    assert request.method == "GET" and str(request.url).startswith("https://jules.googleapis.com/v1alpha/sources?")
    assert dict(request.url.params) == {"pageSize": "2", "pageToken": "old/+?="}
    assert request.headers["x-goog-api-key"] == "synthetic-jules-key"
    assert request.headers["user-agent"].startswith("pal-mcp-server/")
    assert request.extensions["timeout"]["read"] == 30.0
    assert not request.content and all(client.is_closed for client in clients)


@pytest.mark.parametrize(
    "choices", [{}, {"require_plan_approval": False, "auto_create_pr": True, "title": "Test title"}]
)
async def test_create_payload_requires_explicit_pr_opt_in(transport, choices):
    requests, clients = transport(lambda request: httpx.Response(200, json=SESSION))
    result = await call({**CREATE, **choices})
    assert result["session"] == SESSION
    assert len(requests) == 1 and requests[0].method == "POST"
    assert requests[0].url.path == "/v1alpha/sessions"
    payload = json.loads(requests[0].content)
    assert payload["prompt"] == CREATE["prompt"]
    assert payload["sourceContext"] == {"source": CREATE["source"], "githubRepoContext": {"startingBranch": "main"}}
    assert payload["requirePlanApproval"] is choices.get("require_plan_approval", True)
    assert payload["automationMode"] == ("AUTO_CREATE_PR" if choices else "AUTOMATION_MODE_UNSPECIFIED")
    assert ("title" in payload) == bool(choices)
    assert all(client.is_closed for client in clients)


async def test_status_preserves_unknown_state_outputs_and_activity_cursor(transport):
    session = {**SESSION, "state": "FUTURE_STATE", "outputs": [{"pullRequest": {"url": "https://example.org/pr/1"}}]}
    requests, _ = transport(
        lambda request: httpx.Response(
            200,
            json=(
                {"activities": [{"id": "a1"}], "nextPageToken": "more"}
                if request.url.path.endswith("activities")
                else session
            ),
        )
    )
    result = await call({"action": "status", "session_id": "opaque-123", "page_token": "cursor"})
    assert result["session"] == session
    assert result["activities"] == [{"id": "a1"}] and result["next_page_token"] == "more"
    assert [r.url.path for r in requests] == ["/v1alpha/sessions/opaque-123", "/v1alpha/sessions/opaque-123/activities"]
    assert dict(requests[1].url.params) == {"pageSize": "30", "pageToken": "cursor"}


@pytest.mark.parametrize(
    "session_id", ["", ".", "..", "a/b", "a\\b", "x?y", "x#y", "%2f", "a:approvePlan", "a b", "x\n"]
)
async def test_invalid_ids_never_reach_transport(transport, session_id):
    requests, clients = transport(lambda request: pytest.fail("must validate before I/O"))
    with pytest.raises(ToolExecutionError, match="invalid_input"):
        await call({"action": "status", "session_id": session_id})
    assert not requests and not clients


@pytest.mark.parametrize(
    "args",
    [
        {"action": "unknown"},
        {"action": "create"},
        {**CREATE, "source": "sources/../repo"},
        {**CREATE, "source": "https://example.org"},
        {**CREATE, "starting_branch": " "},
        {**CREATE, "auto_create_pr": "true"},
        {"action": "list_sources", "page_size": 0},
        {"action": "list_sources", "page_size": 101},
        {"action": "list_sources", "page_size": True},
        {"action": "list_sources", "prompt": "secret"},
        {**CREATE, "page_token": "secret"},
    ],
)
async def test_invalid_action_fields_fail_before_client_creation(transport, args):
    requests, clients = transport(lambda request: pytest.fail("unexpected network"))
    with pytest.raises(ToolExecutionError, match="invalid_input") as error:
        await call(args)
    assert "secret" not in str(error.value)
    assert not requests and not clients


@pytest.mark.parametrize("key", ["", "your_jules_key_here", "has\nnewline", "key Ω"])
async def test_bad_configuration_is_actionable_mcp_error(monkeypatch, transport, key):
    requests, clients = transport(lambda request: pytest.fail("unexpected network"))
    monkeypatch.setenv("JULES_API_KEY", key)
    monkeypatch.setattr(server.server, "_tool_cache", {})
    result = await server.server.request_handlers[CallToolRequest](
        CallToolRequest(params=CallToolRequestParams(name="jules", arguments={"action": "list_sources"}))
    )
    assert result.root.isError
    assert "JULES_API_KEY" in result.root.content[0].text
    assert not requests and not clients


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "invalid_input"),
        (401, "authentication"),
        (403, "permission"),
        (404, "not_found"),
        (409, "invalid_state"),
        (429, "rate_limit"),
        (500, "http_error"),
        (302, "http_error"),
    ],
)
async def test_http_errors_are_distinct_safe_and_never_retried(transport, status, code, caplog):
    requests, clients = transport(
        lambda request: httpx.Response(
            status, text="synthetic-jules-key PRIVATE", headers={"location": "https://untrusted.invalid"}
        )
    )
    with pytest.raises(ToolExecutionError) as error:
        await call(CREATE)
    payload = json.loads(str(error.value))
    assert payload["code"] == code
    assert payload["outcome_unknown"] is (status >= 500)
    assert "PRIVATE" not in str(error.value) + caplog.text
    assert len(requests) == 1 and all(client.is_closed for client in clients)


@pytest.mark.parametrize("exception,code", [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "transport")])
async def test_transport_failures_require_reconciliation(transport, exception, code):
    def fail(request):
        raise exception("PRIVATE synthetic-jules-key", request=request)

    requests, clients = transport(fail)
    with pytest.raises(ToolExecutionError) as error:
        await call(CREATE)
    data = json.loads(str(error.value))
    assert data["code"] == code and data["outcome_unknown"]
    assert "PRIVATE" not in str(error.value) and "check Jules" in str(error.value)
    assert len(requests) == 1 and all(client.is_closed for client in clients)


@pytest.mark.parametrize("body", ["not json", "[]", "{}"])
async def test_invalid_create_response_does_not_invite_duplicate_creation(transport, body):
    requests, _ = transport(lambda request: httpx.Response(200, text=body))
    with pytest.raises(ToolExecutionError) as error:
        await call(CREATE)
    assert json.loads(str(error.value))["outcome_unknown"] and len(requests) == 1


async def test_empty_source_page_is_valid_but_malformed_page_is_error(transport):
    transport(lambda request: httpx.Response(200, json={}))
    assert (await call({"action": "list_sources"}))["sources"] == []
    transport(lambda request: httpx.Response(200, json={"sources": "invalid"}))
    with pytest.raises(ToolExecutionError, match="invalid_response"):
        await call({"action": "list_sources"})


async def test_cancellation_closes_owned_client(transport):
    started = asyncio.Event()

    async def block(request):
        started.set()
        await asyncio.Event().wait()

    _, clients = transport(block)
    task = asyncio.create_task(call(CREATE))
    try:
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert all(client.is_closed for client in clients)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_tool_annotations_cover_mutations():
    tool = JulesTool()
    assert not tool.requires_model()
    assert tool.get_annotations() == {
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    }
    assert "model" not in tool.get_input_schema()["properties"]


@pytest.mark.parametrize("action,method", [("message", "sendMessage"), ("approve", "approvePlan")])
@pytest.mark.parametrize("empty_body", [True, False])
async def test_steering_methods_payloads_and_observed_state(transport, action, method, empty_body):
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.method == "POST":
            return httpx.Response(200, content=b"" if empty_body else b"{}")
        state = "IN_PROGRESS" if "POST" in calls else "AWAITING_PLAN_APPROVAL"
        return httpx.Response(200, json={**SESSION, "state": state})

    requests, _ = transport(handler)
    args = {"action": action, "session_id": "opaque-123"}
    if action == "message":
        args["prompt"] = "Please cover Ω"
    result = await call(args)
    assert result["accepted"] and result["session"]["state"] == "IN_PROGRESS"
    posts = [request for request in requests if request.method == "POST"]
    assert len(posts) == 1 and posts[0].url.path == f"/v1alpha/sessions/opaque-123:{method}"
    assert json.loads(posts[0].content) == ({"prompt": "Please cover Ω"} if action == "message" else {})
    assert calls == (["GET", "POST", "GET"] if action == "approve" else ["POST", "GET"])


@pytest.mark.parametrize("state", ["QUEUED", "IN_PROGRESS", "COMPLETED", "FAILED", "FUTURE_STATE", None])
async def test_approve_requires_observed_pending_plan(transport, state):
    requests, _ = transport(lambda request: httpx.Response(200, json={**SESSION, "state": state}))
    with pytest.raises(ToolExecutionError, match="invalid_state"):
        await call({"action": "approve", "session_id": "opaque-123"})
    assert len(requests) == 1 and requests[0].method == "GET"


async def test_approval_state_race_is_remote_error_without_retry(transport):
    requests, _ = transport(
        lambda request: (
            httpx.Response(409, json={})
            if request.method == "POST"
            else httpx.Response(200, json={**SESSION, "state": "AWAITING_PLAN_APPROVAL"})
        )
    )
    with pytest.raises(ToolExecutionError, match="invalid_state"):
        await call({"action": "approve", "session_id": "opaque-123"})
    assert [request.method for request in requests] == ["GET", "POST"]


@pytest.mark.parametrize("action", ["message", "approve"])
async def test_acknowledged_action_survives_status_refresh_failure(transport, action):
    posted = False

    def handler(request):
        nonlocal posted
        if request.method == "POST":
            posted = True
            return httpx.Response(200, json={})
        if posted:
            return httpx.Response(429, json={})
        return httpx.Response(200, json={**SESSION, "state": "AWAITING_PLAN_APPROVAL"})

    requests, _ = transport(handler)
    args = {"action": action, "session_id": "opaque-123"}
    if action == "message":
        args["prompt"] = "Feedback"
    result = await call(args)
    assert result["status"] == "success" and result["accepted"] is True
    assert result["session"] is None and result["warnings"][0]["code"] == "rate_limit"
    assert len([r for r in requests if r.method == "POST"]) == 1


@pytest.mark.parametrize("action,archived", [("archive", True), ("unarchive", False)])
async def test_archive_lifecycle_uses_verified_methods_and_returned_state(transport, action, archived):
    # Discovery revision20261008: empty request, Session response with archived.
    requests, _ = transport(
        lambda request: httpx.Response(200, json={**SESSION, "state": "IN_PROGRESS", "archived": archived})
    )
    result = await call({"action": action, "session_id": "opaque-123"})
    assert result["accepted"] and result["session"]["archived"] is archived
    assert result["session"]["state"] == "IN_PROGRESS"  # No invented cancellation/completion.
    assert len(requests) == 1 and requests[0].method == "POST"
    assert requests[0].url.path == f"/v1alpha/sessions/opaque-123:{action}"
    assert json.loads(requests[0].content) == {}


@pytest.mark.parametrize("action", ["message", "approve", "archive", "unarchive"])
@pytest.mark.parametrize("bad_id", ["../evil", "one/two", "one\\two", "one?x", "one#x", "one%2ftwo", "one:delete"])
async def test_every_mutation_rejects_url_injection_before_io(transport, action, bad_id):
    requests, clients = transport(lambda request: pytest.fail("unexpected I/O"))
    args = {"action": action, "session_id": bad_id}
    if action == "message":
        args["prompt"] = "feedback"
    with pytest.raises(ToolExecutionError, match="invalid_input"):
        await call(args)
    assert not requests and not clients


@pytest.mark.parametrize("action", ["message", "archive", "unarchive"])
@pytest.mark.parametrize("status,code", [(401, "authentication"), (429, "rate_limit"), (500, "http_error")])
async def test_lifecycle_auth_rate_limit_and_unknown_outcome(transport, action, status, code):
    requests, _ = transport(lambda request: httpx.Response(status, json={}))
    args = {"action": action, "session_id": "opaque-123"}
    if action == "message":
        args["prompt"] = "feedback"
    with pytest.raises(ToolExecutionError) as error:
        await call(args)
    assert json.loads(error.value.payload)["code"] == code
    assert json.loads(error.value.payload)["outcome_unknown"] is (status == 500)
    assert len(requests) == 1


async def test_message_requires_content_and_does_not_accept_approval_flags(transport):
    requests, _ = transport(lambda request: pytest.fail("unexpected I/O"))
    for fields in ({}, {"prompt": " "}, {"prompt": "ok", "auto_create_pr": True}):
        with pytest.raises(ToolExecutionError, match="invalid_input"):
            await call({"action": "message", "session_id": "opaque-123", **fields})
    assert not requests
