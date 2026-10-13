"""Request-local CLI preparation and success-only conversation recording.

MCP callers retain the server's waiting coordinator. Direct concurrent callers
must still coordinate themselves until the non-waiting admission slice lands.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from tools.models import ToolOutput
from tools.shared.exceptions import ToolExecutionError
from utils import conversation_memory as memory
from utils.conversation_context import CLIHistoryBudget
from utils.file_utils import expand_paths
from utils.storage_backend import InMemoryStorage
from utils.transcripts import record_event

if TYPE_CHECKING:
    from tools.clink import CLinkRequest, CLinkTool


class ConversationConflictError(memory.ConversationStorageError):
    """The prepared thread changed or expired before the reply was recorded."""


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

    @classmethod
    def prepare(cls, request: CLinkRequest) -> CLIConversation:
        now = datetime.now(timezone.utc).isoformat()
        thread_id = str(uuid4())
        if request.continuation_id is not None:
            try:
                thread_id = str(UUID(request.continuation_id))
            except ValueError as exc:
                raise ValueError("continuation_id must be a valid, nonempty UUID") from exc

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
        combined = thread.model_copy(update={"turns": turns + [user_turn], "parent_thread_id": None})
        files = memory.get_conversation_file_list(combined)
        images = memory.get_conversation_image_list(combined)

        user_content = request.prompt.strip()
        if expected is not None:
            # Task 4 replaces this renderer with complete-exchange selection.
            # Keep the current input outside its truncation budget, as before.
            history_turns = [turn.model_copy(update={"files": None}) for turn in combined.turns]
            history_turns[-1] = history_turns[-1].model_copy(update={"content": ""})
            history, _ = memory.build_conversation_history(
                combined.model_copy(update={"turns": history_turns}), CLIHistoryBudget()
            )
            user_content = f"{history}\n\n=== NEW USER INPUT ===\n{request.prompt}"
        return cls(storage, thread, expected, user_turn, files, images, user_content)

    @staticmethod
    def _check_capacity(thread: memory.ThreadContext) -> None:
        if memory.MAX_CONVERSATION_TURNS - len(thread.turns) < 2:
            raise memory.ConversationLimitError("Conversation requires two free message slots for an input and reply")

    def commit(self, reply: str, *, provider: str, model_name: str | None, model_metadata: dict[str, Any]) -> int:
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
            update={"turns": self.thread.turns + [self.user_turn, assistant], "last_updated_at": now}
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
