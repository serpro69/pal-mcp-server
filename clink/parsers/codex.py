"""Parser for Codex CLI JSONL output."""

from __future__ import annotations

import json
from typing import Any

from .base import BaseParser, ParsedCLIResponse, ParserError


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
                item = event.get("item") or {}
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
        metadata: dict[str, Any] = {"events": events}
        if errors:
            metadata["errors"] = errors
        if usage:
            metadata["usage"] = usage
        if stderr and stderr.strip():
            metadata["stderr"] = stderr.strip()

        return ParsedCLIResponse(content=content, metadata=metadata, is_error=is_error)
