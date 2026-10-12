"""Explicit, provider-free actions against the optional Jules v1alpha service."""

import asyncio
import json
import re
from typing import Any, Literal

import httpx
from mcp.types import TextContent
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from config import __version__
from systemprompts.jules_prompt import JULES_PROMPT
from tools.shared.base_tool import BaseTool
from tools.shared.exceptions import ToolExecutionError
from utils.env import get_env
from utils.progress import progress_operation

API_ROOT = "https://jules.googleapis.com/v1alpha"
REQUEST_TIMEOUT = 30.0
_COMPONENT = re.compile(r"[A-Za-z0-9._~-]+\Z")


def valid_component(value: str) -> bool:
    """IDs are opaque; disallow URL syntax, encoded separators and dot traversal."""
    return value not in (".", "..") and _COMPONENT.fullmatch(value) is not None


class JulesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["list_sources", "create", "status", "message", "approve", "archive", "unarchive"]
    session_id: str | None = Field(default=None, max_length=512, description="Opaque ID, without sessions/ prefix.")
    prompt: str | None = Field(default=None, max_length=100_000, description="Task for create or feedback for message.")
    source: str | None = Field(default=None, max_length=1024, description="Exact sources/... name from list_sources.")
    starting_branch: str | None = Field(
        default=None, max_length=1024, description="Explicit repository branch for create."
    )
    title: str | None = Field(default=None, max_length=1024)
    require_plan_approval: bool = Field(default=True, description="Require approval before work; create only.")
    auto_create_pr: bool = Field(
        default=False, description="Explicit opt-in to automatic remote PR creation; create only."
    )
    page_size: int = Field(default=30, ge=1, le=100, description="Sources or activities per page.")
    page_token: str | None = Field(
        default=None, max_length=8192, description="Continuation token from the previous page."
    )

    @model_validator(mode="after")
    def validate_action(self):
        allowed = {
            "list_sources": {"page_size", "page_token"},
            "create": {"prompt", "source", "starting_branch", "title", "require_plan_approval", "auto_create_pr"},
            "status": {"session_id", "page_size", "page_token"},
            "message": {"session_id", "prompt"},
            "approve": {"session_id"},
            "archive": {"session_id"},
            "unarchive": {"session_id"},
        }
        if self.model_fields_set - allowed[self.action] - {"action"}:
            raise ValueError("Fields supplied do not apply to the selected action")
        if self.action in ("status", "message", "approve", "archive", "unarchive") and (
            self.session_id is None or not valid_component(self.session_id)
        ):
            raise ValueError("session_id must be one nonempty URL-safe opaque component")
        if self.action in ("create", "message") and (not self.prompt or not self.prompt.strip()):
            raise ValueError("create and message require a nonempty prompt")
        if self.action == "create":
            if not self.starting_branch or not self.starting_branch.strip():
                raise ValueError("create requires an explicit starting_branch")
            if not self.source or not self.source.startswith("sources/"):
                raise ValueError("create requires a source name from list_sources")
            if not all(valid_component(part) for part in self.source.split("/")[1:]):
                raise ValueError("source must contain safe, nonempty resource-name components")
        return self


def _error(code: str, message: str, *, outcome_unknown: bool = False) -> ToolExecutionError:
    return ToolExecutionError(
        json.dumps({"status": "error", "code": code, "content": message, "outcome_unknown": outcome_unknown})
    )


class JulesTool(BaseTool):
    def get_name(self) -> str:
        return "jules"

    def get_description(self) -> str:
        return JULES_PROMPT

    def get_system_prompt(self) -> str:
        return JULES_PROMPT

    def get_request_model(self):
        return JulesRequest

    def get_input_schema(self) -> dict[str, Any]:
        return JulesRequest.model_json_schema()

    def requires_model(self) -> bool:
        return False

    def get_annotations(self) -> dict[str, Any]:
        # MCP annotations describe the entire mixed-action tool, not one request.
        return {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}

    async def prepare_prompt(self, request: JulesRequest) -> str:
        return ""  # Service actions never invoke a model.

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            request = JulesRequest(**arguments)
        except ValidationError as exc:
            # Do not include request values (task text, tokens) in diagnostics.
            details = "; ".join(error["msg"] for error in exc.errors(include_input=False, include_context=False))
            raise _error("invalid_input", f"Invalid Jules request: {details}") from exc
        key = (get_env("JULES_API_KEY", "") or "").strip()
        if not key or key.lower().startswith(("your_", "your-")):
            raise _error("configuration", "Set JULES_API_KEY from Jules settings; no model provider key is required.")
        if not key.isascii() or any(ord(char) < 33 or ord(char) == 127 for char in key):
            raise _error(
                "configuration", "JULES_API_KEY must be a valid API key without whitespace or control characters."
            )

        async with httpx.AsyncClient(
            headers={"X-Goog-Api-Key": key, "User-Agent": f"pal-mcp-server/{__version__}"},
            timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=10.0),
            follow_redirects=False,
        ) as client:
            async with progress_operation("jules_request"):
                result = await self._dispatch(client, request)
        return [TextContent(type="text", text=json.dumps({"status": "success", "action": request.action, **result}))]

    async def _request(
        self, client: httpx.AsyncClient, method: str, path: str, *, allow_empty: bool = False, **kwargs
    ) -> dict[str, Any]:
        mutating = method != "GET"
        reconcile = " Remote outcome is unknown; check Jules before repeating this action." if mutating else ""
        try:
            response = await asyncio.wait_for(client.request(method, API_ROOT + path, **kwargs), REQUEST_TIMEOUT)
        except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
            raise _error("timeout", "Jules request timed out." + reconcile, outcome_unknown=mutating) from exc
        except httpx.RequestError as exc:
            raise _error("transport", "Jules connection failed." + reconcile, outcome_unknown=mutating) from exc
        if not response.is_success:
            codes = {
                400: ("invalid_input", "Jules rejected the request."),
                401: ("authentication", "Jules authentication failed; check JULES_API_KEY."),
                403: ("permission", "Jules denied access; check the key and connected repository permissions."),
                404: ("not_found", "Jules session or source was not found."),
                409: ("invalid_state", "Jules cannot perform this action in the current session state."),
                429: ("rate_limit", "Jules rate limit reached; wait before another request."),
            }
            code, message = codes.get(
                response.status_code, ("http_error", f"Jules returned HTTP {response.status_code}.")
            )
            unknown = mutating and response.status_code >= 500
            raise _error(code, message + (reconcile if unknown else ""), outcome_unknown=unknown)
        if allow_empty and not response.content:
            return {}
        try:
            data = response.json()
        except (ValueError, RecursionError) as exc:
            raise _error(
                "invalid_response", "Jules returned invalid JSON." + reconcile, outcome_unknown=mutating
            ) from exc
        if not isinstance(data, dict):
            raise _error(
                "invalid_response", "Jules returned a non-object response." + reconcile, outcome_unknown=mutating
            )
        return data

    @staticmethod
    def _page(data: dict[str, Any], field: str) -> dict[str, Any]:
        items, token = data.get(field, []), data.get("nextPageToken", "")
        if (
            not isinstance(items, list)
            or not all(isinstance(item, dict) for item in items)
            or not isinstance(token, str)
        ):
            raise _error("invalid_response", "Jules returned an invalid page.")
        return {field: items, "next_page_token": token or None}

    @staticmethod
    def _session(data: dict[str, Any], *, mutated: bool = False) -> dict[str, Any]:
        name = data.get("name")
        if not isinstance(name, str) or not name.startswith("sessions/") or not valid_component(name[9:]):
            message = "Jules returned an invalid session."
            if mutated:
                message += " Remote outcome is unknown; check Jules before repeating the action."
            raise _error("invalid_response", message, outcome_unknown=mutated)
        return data

    @staticmethod
    def _next_action(session: dict[str, Any]) -> str:
        state = session.get("state")
        if session.get("archived") is True:
            return "Session is archived. Use unarchive to restore its visibility; archiving is not cancellation."
        if state == "AWAITING_PLAN_APPROVAL":
            return "Inspect the plan with status; call approve only after explicit approval, or message with feedback."
        if state == "AWAITING_USER_FEEDBACK":
            return "Inspect activities with status, then send the requested feedback with message."
        if state in ("COMPLETED", "FAILED"):
            return "Inspect session outputs and activities with status."
        return "Call status again to inspect the observed state and activities; no completion is assumed."

    async def _steer(self, client: httpx.AsyncClient, request: JulesRequest, path: str) -> dict[str, Any]:
        if request.action == "approve":
            before = self._session(await self._request(client, "GET", path))
            if before.get("state") != "AWAITING_PLAN_APPROVAL":
                raise _error("invalid_state", "Session is not awaiting plan approval; inspect status before approving.")
        methods = {"message": "sendMessage", "approve": "approvePlan", "archive": "archive", "unarchive": "unarchive"}
        payload = {"prompt": request.prompt} if request.action == "message" else {}
        data = await self._request(
            client,
            "POST",
            path + ":" + methods[request.action],
            json=payload,
            allow_empty=request.action in ("message", "approve"),
        )
        if request.action in ("archive", "unarchive"):
            session = self._session(data, mutated=True)
        else:
            try:
                session = self._session(await self._request(client, "GET", path))
            except ToolExecutionError as exc:
                # The POST succeeded. Do not turn a subsequent read failure into
                # a failed mutation that invites a duplicate message/approval.
                return {
                    "accepted": True,
                    "session": None,
                    "warnings": [
                        {
                            "code": json.loads(exc.payload)["code"],
                            "content": "Action accepted, but status refresh failed. Call status; do not repeat the action.",
                        }
                    ],
                    "next_action": "Call status to inspect the session.",
                }
        return {"accepted": True, "session": session, "next_action": self._next_action(session)}

    async def _dispatch(self, client: httpx.AsyncClient, request: JulesRequest) -> dict[str, Any]:
        params = {"pageSize": request.page_size}
        if request.page_token is not None:
            params["pageToken"] = request.page_token
        if request.action == "list_sources":
            return self._page(await self._request(client, "GET", "/sources", params=params), "sources")
        if request.action == "create":
            payload = {
                "prompt": request.prompt,
                "sourceContext": {
                    "source": request.source,
                    "githubRepoContext": {"startingBranch": request.starting_branch},
                },
                "requirePlanApproval": request.require_plan_approval,
                "automationMode": "AUTO_CREATE_PR" if request.auto_create_pr else "AUTOMATION_MODE_UNSPECIFIED",
            }
            if request.title is not None:
                payload["title"] = request.title
            data = await self._request(client, "POST", "/sessions", json=payload)
            return {"session": self._session(data, mutated=True), "next_action": "Call status to inspect progress."}
        path = f"/sessions/{request.session_id}"
        if request.action != "status":
            return await self._steer(client, request, path)
        session = self._session(await self._request(client, "GET", path))
        activities = self._page(await self._request(client, "GET", path + "/activities", params=params), "activities")
        return {
            "session": session,
            **activities,
            "next_action": self._next_action(session) + " Pass next_page_token as page_token for more activities.",
        }
