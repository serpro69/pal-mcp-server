"""Invocation-owned conversation admission, separate from untrusted request JSON.

The MCP boundary holds its conversation coordinator through this lifecycle.
Direct callers must acquire that coordinator explicitly for same-thread concurrency.
The context is reset on every exit, including cancellation and provider failure.
"""

import json
from contextvars import ContextVar
from functools import wraps
from typing import Any

from tools.models import ToolOutput
from tools.shared.exceptions import ToolExecutionError
from utils import conversation_memory as memory


class ConversationInvocation:
    def __init__(self, tool, arguments, thread_id: str, is_new: bool, raw_input: str):
        self.tool = tool
        self.arguments = arguments
        self.thread_id = thread_id
        self.is_new = is_new
        self.raw_input = raw_input
        self.reply_saved = False
        self.reply_content: str | None = None
        self.model_name: str | None = None
        self.model_provider: str | None = None
        self.provider_failed = False


_active: ContextVar[ConversationInvocation | None] = ContextVar("conversation_invocation", default=None)


def current_invocation(tool) -> ConversationInvocation | None:
    context = _active.get()
    return context if context is not None and context.tool is tool else None


def continuation_offer(thread_id: str) -> dict[str, Any]:
    thread = memory.get_thread(thread_id)
    if thread is None:
        raise memory.ConversationNotFoundError("Conversation thread was not found or has expired")
    remaining = max(0, memory.MAX_CONVERSATION_TURNS - len(thread.turns))
    exchanges = remaining // 2
    return {
        "continuation_id": thread_id,
        "remaining_turns": remaining,
        "note": (
            f"You can continue this conversation for {exchanges} more exchanges."
            if exchanges
            else "Conversation limit reached; start a new conversation with full context."
        ),
    }


class CLIHistoryBudget:
    """Bound CLI history independently of optional PAL model providers."""

    model_name = "clink-history"

    def calculate_token_allocation(self):
        from utils.model_context import TokenAllocation

        return TokenAllocation(
            total_tokens=20_000, content_tokens=20_000, response_tokens=0, file_tokens=0, history_tokens=20_000
        )

    @staticmethod
    def estimate_tokens(text: str) -> int:
        from utils.token_utils import estimate_tokens

        return estimate_tokens(text)


def invocation_thread(tool):
    """Read the invocation's saved input, including parent-chain audit turns."""
    context = current_invocation(tool)
    if context is None:
        return None
    thread = memory.get_thread(context.thread_id)
    if thread is None:
        raise memory.ConversationNotFoundError("Conversation thread was not found or has expired")
    if thread.parent_thread_id:
        turns = [turn for parent in memory.get_thread_chain(thread.thread_id) for turn in parent.turns]
        thread = thread.model_copy(update={"turns": turns, "parent_thread_id": None})
    return thread


def invocation_attachments(tool) -> tuple[list[str], list[str]]:
    thread = invocation_thread(tool)
    if thread is None:
        return [], []
    return memory.get_conversation_file_list(thread), memory.get_conversation_image_list(thread)


def invocation_history(tool, model_context=None, *, text_only=False, expert=False, represented_context="") -> str:
    """Budget older history while keeping the admitted current input exactly once."""
    context = current_invocation(tool)
    thread = invocation_thread(tool)
    if thread is None:
        return ""
    turns = list(thread.turns)
    # Keep current attachments available to the renderer, but put the current input
    # outside its truncation budget. This is a rendering copy, never a stored turn.
    if turns and not context.reply_saved:
        turns[-1] = turns[-1].model_copy(update={"content": ""})
    if expert and represented_context:
        turns = [
            (
                turn.model_copy(update={"content": "[Input included in the analysis context below.]"})
                if turn.role == "user"
                and turn.tool_name == tool.get_name()
                and turn.content
                and turn.content in represented_context
                else turn
            )
            for turn in turns
        ]
    if text_only or expert:
        # Files travel through the tool's separate embedding/reference route.
        turns = [turn.model_copy(update={"files": None}) for turn in turns]
    thread = thread.model_copy(update={"turns": turns})
    if text_only:
        model_context = CLIHistoryBudget()
    history, _ = memory.build_conversation_history(thread, model_context)
    if expert and context.raw_input in represented_context:
        return history
    return f"{history}\n\n=== NEW USER INPUT ===\n{context.raw_input}"


def conversation_request(*, workflow=False):
    """Give MCP and direct execute calls the same single input-persistence owner."""

    def decorate(execute):
        @wraps(execute)
        async def wrapped(tool, arguments):
            existing = current_invocation(tool)
            if existing is not None and existing.arguments is arguments:
                return await execute(tool, arguments)
            # Never accept caller claims of completed admission or reconstructed history.
            args = {k: v for k, v in arguments.items() if not k.startswith("_")}
            # ModelContext remains an existing in-process API; JSON values cannot supply it.
            model_context = arguments.get("_model_context")
            if model_context is not None and not isinstance(model_context, (dict, list, str, int, float, bool)):
                args["_model_context"] = model_context
                if isinstance(getattr(model_context, "model_name", None), str):
                    args["_resolved_model_name"] = model_context.model_name
            try:
                request_model = tool.get_workflow_request_model() if workflow else tool.get_request_model()
                request = request_model(**args)
                path_error = tool.validate_file_paths(request) if workflow else tool._validate_file_paths(request)
                if path_error:
                    raise ToolExecutionError(ToolOutput(status="error", content=path_error).model_dump_json())
                raw = request.step if workflow else request.prompt
                size_error = tool.check_prompt_size(raw)
                if size_error:
                    if workflow:
                        size_error["content"] = (
                            "Step instructions are too long. Please use shorter instructions and provide detailed context via file paths instead."
                        )
                    raise ToolExecutionError(ToolOutput(**size_error).model_dump_json())
                if workflow:
                    files = tool.get_request_relevant_files(request)
                else:
                    # Load prompt files once before the raw input is persisted.
                    files = tool.get_request_files(request)
                    prompt_file, updated_files = tool.handle_prompt_file(files) if files else (None, None)
                    if prompt_file:
                        raw = prompt_file
                    if updated_files is not None:
                        tool.set_request_files(request, updated_files)
                    request.prompt = raw
                    files = tool.get_request_files(request)
                from utils.file_utils import expand_paths

                files = list(dict.fromkeys(expanded for path in files for expanded in (expand_paths([path]) or [path])))
                if not workflow:
                    tool.set_request_files(request, files)
                args.update(request.model_dump(exclude_unset=True))
                thread_id = request.continuation_id
                is_new = not thread_id
                thread = memory.get_thread(thread_id) if thread_id else None
                if thread_id and thread is None:
                    raise memory.ConversationNotFoundError("Conversation thread was not found or has expired")
                free = memory.MAX_CONVERSATION_TURNS - (len(thread.turns) if thread else 0)
                if free < 2:
                    raise memory.ConversationLimitError(
                        "Conversation requires two free message slots for an input and reply"
                    )
                if thread is not None and tool.requires_model() and not args.get("model"):
                    for turn in reversed(thread.turns):
                        if turn.role == "assistant" and turn.model_name:
                            args["model"] = turn.model_name
                            break
                if is_new:
                    thread_id = memory.create_thread(tool.get_name(), request.model_dump(exclude_none=True))
                memory.add_turn(
                    thread_id,
                    "user",
                    raw,
                    files=files,
                    images=tool.get_request_images(request),
                    tool_name=tool.get_name(),
                    required=True,
                )
            except ToolExecutionError as exc:
                if not workflow:
                    raise
                try:
                    payload = json.loads(exc.payload)
                except json.JSONDecodeError:
                    raise exc
                if not isinstance(payload, dict):
                    raise
                metadata = payload.get("metadata") or {}
                metadata.update(tool_name=tool.get_name(), model_used=None, provider_used=None)
                payload["metadata"] = metadata
                raise ToolExecutionError(json.dumps(payload)) from exc
            except (memory.ConversationMemoryError, ValueError) as exc:
                status = f"{tool.get_name()}_failed" if workflow else "error"
                payload = {
                    "status": status,
                    "content": str(exc),
                    "error": str(exc),
                    "metadata": {
                        "tool_name": tool.get_name(),
                        "error_type": type(exc).__name__,
                        **({"model_used": None, "provider_used": None} if workflow else {}),
                    },
                }
                raise ToolExecutionError(json.dumps(payload)) from exc
            context = ConversationInvocation(tool, args, thread_id, is_new, raw)
            token = _active.set(context)
            try:
                return await execute(tool, args)
            except ToolExecutionError as exc:
                # Report capacity consumed by real writes, without a fallible recovery
                # read replacing the primary error. MCP holds the coordinator here.
                try:
                    payload = json.loads(exc.payload)
                except json.JSONDecodeError:
                    raise exc
                if not isinstance(payload, dict):
                    raise
                remaining = free - 1 - int(context.reply_saved)
                metadata = payload.get("metadata") or {}
                payload["metadata"] = metadata
                metadata.update(
                    continuation_id=thread_id,
                    remaining_turns=remaining,
                    remaining_exchanges=remaining // 2,
                )
                raise ToolExecutionError(json.dumps(payload)) from exc
            finally:
                _active.reset(token)

        return wrapped

    return decorate
