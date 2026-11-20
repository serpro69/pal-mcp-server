"""Parser for Codex CLI JSONL output."""

from __future__ import annotations

import json
from typing import Any

from .base import BaseParser, ParsedCLIResponse, ParserError

MAX_ACTIVITY_COMMANDS = 20
MAX_ACTIVITY_COMMAND_CHARS = 256
MAX_ACTIVITY_CHARS = 6_000


def _observed_activity(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Select completed command receipts, never command output or model self-reports."""
    commands: list[dict[str, Any]] = []
    seen: set[str] = set()
    command_count = failed_count = denied_count = 0
    truncated = False
    for event in events:
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "command_execution":
            continue
        item_id = item.get("id")
        if isinstance(item_id, str) and item_id:
            if item_id in seen:
                continue
            seen.add(item_id)
        command_count += 1
        exit_code = item.get("exit_code")
        if type(exit_code) is not int or not -(2**31) <= exit_code < 2**31:
            exit_code = None
        status = item.get("status")
        if status not in ("completed", "failed", "declined"):
            status = "unknown"
        if status in ("failed", "declined") or exit_code not in (None, 0):
            failed_count += 1
        if status == "declined":
            denied_count += 1
        command = item.get("command")
        if not isinstance(command, str):
            command = None
        shortened = command is not None and len(command) > MAX_ACTIVITY_COMMAND_CHARS
        entry = {
            "command": command[:MAX_ACTIVITY_COMMAND_CHARS] if command is not None else None,
            "exit_code": exit_code,
            "status": status,
        }
        if shortened:
            entry["command_truncated"] = True
            truncated = True
        commands.append(entry)
        if len(commands) > MAX_ACTIVITY_COMMANDS:
            # Keep the first ten and latest ten, in stream order.
            del commands[MAX_ACTIVITY_COMMANDS // 2]
            truncated = True

    summary = {
        "source": "codex_jsonl",
        "commands": commands,
        "command_count": command_count,
        "failed_count": failed_count,
        "denied_count": denied_count,
        "truncated": truncated,
    }
    # Escaping can expand strings; cap serialized metadata as well as entry lengths.
    while len(json.dumps(summary)) > MAX_ACTIVITY_CHARS:
        del commands[len(commands) // 2]
        summary["truncated"] = True
    return summary


class CodexJSONLParser(BaseParser):
    """Parse stdout emitted by `codex exec --json`."""

    name = "codex_jsonl"

    def parse(self, stdout: str, stderr: str) -> ParsedCLIResponse:
        lines = [line.strip() for line in (stdout or "").splitlines() if line.strip()]
        events: list[dict[str, Any]] = []
        agent_messages: list[str] = []
        errors: list[str] = []
        turn_failed = False
        usage: dict[str, Any] | None = None

        for line in lines:
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            events.append(event)
            event_type = event.get("type")
            if event_type == "item.completed":
                item = event.get("item")
                if not isinstance(item, dict):
                    raise ParserError("Codex CLI item.completed event must contain an 'item' object")
                if item.get("type") == "agent_message":
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        agent_messages.append(text.strip())
            elif event_type == "error":
                message = event.get("message")
                if isinstance(message, str) and message.strip():
                    errors.append(message.strip())
                else:
                    errors.append("Codex CLI reported an error.")
            elif event_type == "turn.failed":
                turn_failed = True
                error = event.get("error")
                message = error.get("message") if isinstance(error, dict) else None
                if isinstance(message, str) and message.strip():
                    errors.append(message.strip())
                else:
                    errors.append("Codex CLI reported a failed turn.")
            elif event_type == "turn.completed":
                turn_usage = event.get("usage")
                if isinstance(turn_usage, dict):
                    usage = turn_usage

        # A terminal failure wins over partial text. Recoverable error events
        # may accompany a successful answer, so they alone do not reject it.
        is_error = turn_failed or (not agent_messages and bool(errors))
        if is_error:
            agent_messages.extend(errors)

        if not agent_messages:
            raise ParserError("Codex CLI JSONL output did not include an agent_message item")

        content = "\n\n".join(agent_messages).strip()
        metadata: dict[str, Any] = {"events": events, "observed_activity": _observed_activity(events)}
        if errors:
            metadata["errors"] = errors
        if usage:
            metadata["usage"] = usage
        if stderr and stderr.strip():
            metadata["stderr"] = stderr.strip()

        return ParsedCLIResponse(content=content, metadata=metadata, is_error=is_error)
