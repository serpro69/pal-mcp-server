"""Request-local CLI preparation and success-only conversation recording.

CLinkTool owns non-waiting coordination for direct and MCP callers, retaining
the shared lock through snapshot preparation, execution, commit, and audit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from clink.constants import DEFAULT_HISTORY_TOKEN_BUDGET
from utils import conversation_memory as memory
from utils.file_utils import expand_paths
from utils.storage_backend import InMemoryStorage
from utils.token_utils import estimate_tokens
from utils.transcripts import record_event

if TYPE_CHECKING:
    from tools.clink import CLinkRequest, CLinkTool


class ConversationConflictError(memory.ConversationStorageError):
    """The prepared thread changed or expired before the reply was recorded."""


def canonical_continuation_id(value: Any) -> str | None:
    """Validate a CLI continuation before using it as a coordination key."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("continuation_id must be a valid, nonempty UUID")
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise ValueError("continuation_id must be a valid, nonempty UUID") from exc


@dataclass
class CLIHistory:
    """A bounded text projection and the complete messages it retains."""

    text: str
    turns: list[memory.ConversationTurn]
    metadata: dict[str, Any]


def _render_turn(number: int, turn: memory.ConversationTurn) -> str:
    label = "Agent" if turn.role == "user" else turn.model_name or "Assistant"
    if turn.tool_name:
        label += f" using {turn.tool_name}"
    if turn.model_provider:
        label += f" via {turn.model_provider}"
    parts = [f"--- Turn {number} ({label}) ---"]
    if turn.files:
        parts.append("Files: " + ", ".join(turn.files))
    if turn.images:
        parts.append("Images: " + ", ".join(turn.images))
    content = turn.content
    if turn.role == "assistant":
        clink_metadata = (turn.model_metadata or {}).get("clink")
        if isinstance(clink_metadata, dict) and isinstance(clink_metadata.get("visible_reply"), str):
            content = clink_metadata["visible_reply"]
    parts.append(content)
    return "\n".join(parts)


def render_history(thread: memory.ThreadContext, *, token_budget: int = DEFAULT_HISTORY_TOKEN_BUDGET) -> CLIHistory:
    """Retain the newest suffix of complete exchanges, measuring the final text.

    The caller supplies a bounded, flattened parent snapshot. Incomplete legacy
    groups are omitted without mutating storage. Paths remain references only.
    """
    groups: list[list[tuple[int, memory.ConversationTurn]]] = []
    pending: list[tuple[int, memory.ConversationTurn]] = []
    for number, turn in enumerate(thread.turns, start=1):
        if turn.role == "assistant" and pending:
            pending.append((number, turn))
            continue
        if len(pending) >= 2:
            groups.append(pending)
        pending = [(number, turn)] if turn.role == "user" else []
    if len(pending) >= 2:
        groups.append(pending)

    total = len(thread.turns)
    incomplete = total - sum(len(group) for group in groups)
    if total and not groups:
        raise ValueError("Conversation history has no complete exchange; start a new conversation.")

    metadata = {
        "estimated_tokens": 0,
        "token_budget": token_budget,
        "retained_messages": 0,
        "omitted_messages": total,
        "incomplete_messages": incomplete,
        "incomplete_legacy_omitted": bool(incomplete),
    }
    if not groups:
        return CLIHistory("", [], metadata)

    rendered = ["\n\n".join(_render_turn(number, turn) for number, turn in group) for group in groups]
    # Drop oldest whole groups until even the wrappers and omission notice fit.
    # Checking every suffix also handles changing notice length at the boundary.
    for start in range(len(groups)):
        retained = [turn for group in groups[start:] for _, turn in group]
        omitted = total - len(retained)
        parts = [
            "=== CONVERSATION HISTORY (CONTINUATION) ===",
            f"Thread: {thread.thread_id}",
            f"Tool: {thread.tool_name}",
        ]
        if omitted:
            parts.append(
                f"[Omitted {omitted} messages: {incomplete} from incomplete legacy groups; "
                f"{omitted - incomplete} older messages outside the history budget.]"
            )
        parts.extend(rendered[start:])
        parts.extend(
            [
                "=== END CONVERSATION HISTORY ===",
                "Build on the exchanges above. Provide new insights or answers to the current request; "
                "do not repeat earlier analysis.",
            ]
        )
        text = "\n\n".join(parts)
        tokens = estimate_tokens(text)
        if tokens <= token_budget:
            metadata.update(estimated_tokens=tokens, retained_messages=len(retained), omitted_messages=omitted)
            return CLIHistory(text, retained, metadata)

    raise ValueError(
        f"The newest complete exchange exceeds the history-only budget of {token_budget} estimated tokens "
        "including wrappers and references. Start a shorter conversation or configure a larger history_token_budget "
        "for a future server session. This budget excludes the current request and CLI-generated context."
    )


def prepare_request(tool: CLinkTool, arguments: dict[str, Any]) -> CLinkRequest:
    """Validate current input and expand prompt files once, without writing history."""
    request = tool.get_request_model()(**{key: value for key, value in arguments.items() if not key.startswith("_")})
    path_error = tool._validate_file_paths(request)
    if path_error:
        tool._raise_tool_error(path_error)
    if request.editable_paths and not request.allow_edits:
        tool._raise_tool_error("editable_paths can only be used when allow_edits=true.")
    editable_path_error = tool._validate_editable_paths(request)
    if editable_path_error:
        tool._raise_tool_error(editable_path_error)
    size_error = tool.check_prompt_size(request.prompt)
    if size_error:
        # Import lazily: tools.__init__ registers CLinkTool, which imports this
        # module. The history renderer must also be importable on its own.
        from tools.models import ToolOutput
        from tools.shared.exceptions import ToolExecutionError

        raise ToolExecutionError(ToolOutput(**size_error).model_dump_json())

    files = tool.get_request_files(request)
    if files:
        prompt, remaining_files = tool.handle_prompt_file(files)
        if prompt:
            request.prompt = prompt
        # The helper returns None when prompt.txt was the only file. Do not
        # retain it as an attachment and accidentally read it a second time.
        files = remaining_files or []
    request.absolute_file_paths = list(
        dict.fromkeys(expanded for path in files for expanded in (expand_paths([path]) or [path]))
    )
    return request


@dataclass
class CLIConversation:
    """A storage snapshot and prepared current input owned by one invocation."""

    storage: InMemoryStorage
    thread: memory.ThreadContext
    expected: str | None
    user_turn: memory.ConversationTurn
    files: list[str]
    images: list[str]
    user_content: str
    history_metadata: dict[str, Any]

    @classmethod
    def prepare(cls, request: CLinkRequest) -> CLIConversation:
        now = datetime.now(timezone.utc).isoformat()
        thread_id = canonical_continuation_id(request.continuation_id) or str(uuid4())

        try:
            storage = memory.get_storage()
            expected = storage.get(f"thread:{thread_id}") if request.continuation_id is not None else None
        except OSError as exc:
            raise memory.ConversationStorageError("Conversation storage read failed") from exc

        if request.continuation_id is not None:
            if expected is None:
                raise memory.ConversationNotFoundError("Conversation thread was not found or has expired")
            try:
                thread = memory.ThreadContext.model_validate_json(expected)
            except ValueError as exc:
                raise memory.ConversationDecodeError("Stored conversation could not be decoded") from exc
            if thread.thread_id != thread_id:
                raise memory.ConversationDecodeError("Stored conversation identity does not match continuation_id")
        else:
            thread = memory.ThreadContext(
                thread_id=thread_id,
                created_at=now,
                last_updated_at=now,
                tool_name="clink",
                turns=[],
                initial_context=request.model_dump(exclude_none=True, exclude={"continuation_id"}),
            )
        cls._check_capacity(thread)

        user_turn = memory.ConversationTurn(
            role="user",
            content=request.prompt,
            timestamp=now,
            files=list(request.absolute_file_paths),
            images=list(request.images),
            tool_name="clink",
        )
        turns = list(thread.turns)
        if thread.parent_thread_id:
            parents = memory.get_thread_chain(thread.parent_thread_id, max_depth=19)
            # Retain the loaded child snapshot and the existing depth/cycle bound.
            for index, parent in enumerate(parents):
                if parent.thread_id == thread.thread_id:
                    parents = parents[index + 1 :]
                    break
            turns = [turn for parent in parents for turn in parent.turns] + turns
        history = render_history(thread.model_copy(update={"turns": turns, "parent_thread_id": None}))
        combined = thread.model_copy(update={"turns": history.turns + [user_turn], "parent_thread_id": None})
        files = memory.get_conversation_file_list(combined)
        images = memory.get_conversation_image_list(combined)

        user_content = request.prompt.strip()
        if history.text:
            user_content = f"{history.text}\n\n=== NEW USER INPUT ===\n{request.prompt}"
        return cls(storage, thread, expected, user_turn, files, images, user_content, history.metadata)

    def resolve_selection(self, request: CLinkRequest, cli_names: list[str]) -> tuple[str, str]:
        """Inherit only successful CLI/role defaults, never request permissions."""
        defaults = self.thread.initial_context.get("clink_defaults", {})
        if not isinstance(defaults, dict):
            defaults = {}
        cli_name = request.cli_name if request.cli_name is not None else defaults.get("cli_name")
        role = request.role if request.role is not None else defaults.get("role", "default")
        if cli_name is None:
            if not cli_names:
                raise ValueError("No CLI clients are configured for clink.")
            if len(cli_names) != 1:
                raise ValueError(
                    "cli_name is required when multiple CLI clients are configured and no selection is saved."
                )
            cli_name = cli_names[0]
        if not isinstance(cli_name, str) or cli_name not in cli_names:
            raise ValueError(f"Invalid cli_name {cli_name!r}. Select a configured CLI: {', '.join(cli_names)}.")
        if not isinstance(role, str) or not role:
            raise ValueError("Invalid role; specify a valid role for the selected CLI.")
        return cli_name, role

    @staticmethod
    def _check_capacity(thread: memory.ThreadContext) -> None:
        if memory.MAX_CONVERSATION_TURNS - len(thread.turns) < 2:
            raise memory.ConversationLimitError("Conversation requires two free message slots for an input and reply")

    def commit(
        self, reply: str, *, provider: str, role: str, model_name: str | None, model_metadata: dict[str, Any]
    ) -> int:
        """Commit both raw turns once, then emit compatible audit events in order.

        There is no await between CAS and audit emission. The caller owns any
        coordinator through this entire method. Failed CAS never emits events.
        """
        self._check_capacity(self.thread)
        now = datetime.now(timezone.utc).isoformat()
        assistant = memory.ConversationTurn(
            role="assistant",
            content=reply,
            timestamp=now,
            files=self.files,
            images=self.images,
            tool_name="clink",
            model_provider=provider,
            model_name=model_name,
            model_metadata=model_metadata,
        )
        committed = self.thread.model_copy(
            update={
                "turns": self.thread.turns + [self.user_turn, assistant],
                "last_updated_at": now,
                "initial_context": {
                    **self.thread.initial_context,
                    "clink_defaults": {"cli_name": provider, "role": role},
                },
            }
        )
        data = committed.model_dump_json()
        try:
            saved = self.storage.compare_and_setex(
                f"thread:{self.thread.thread_id}", memory.CONVERSATION_TIMEOUT_SECONDS, self.expected, data
            )
        except OSError as exc:
            raise memory.ConversationStorageError("Conversation storage write failed") from exc
        if not saved:
            raise ConversationConflictError("Conversation changed or expired before the exchange could be saved")

        if self.expected is None:
            record_event(
                self.thread.thread_id,
                {
                    "event": "thread",
                    "timestamp": self.thread.created_at,
                    "tool_name": self.thread.tool_name,
                    "parent_thread_id": self.thread.parent_thread_id,
                },
            )
        for number, turn in enumerate((self.user_turn, assistant), start=len(self.thread.turns) + 1):
            record_event(self.thread.thread_id, {"event": "turn", "turn": number, **turn.model_dump(mode="json")})
        return max(0, memory.MAX_CONVERSATION_TURNS - len(committed.turns))
