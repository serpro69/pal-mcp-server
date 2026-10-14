"""Strict single-result parser for Antigravity's newline-delimited JSON stream."""

from __future__ import annotations

import json
from typing import NoReturn

from .base import BaseParser, ParsedCLIResponse, ParserError


def _reject_non_json_constant(value: str) -> NoReturn:
    raise ValueError("Non-JSON numeric constant")


class AgyJSONLParser(BaseParser):
    """Accept one successful result, ignoring well-formed informational events."""

    name = "agy_jsonl"

    def parse(self, stdout: str, stderr: str) -> ParsedCLIResponse:
        result = None
        model_used = None
        # splitlines() would also split valid Unicode separators inside JSON strings.
        for line_number, line in enumerate(stdout.split("\n"), start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line, parse_constant=_reject_non_json_constant)
            except ValueError as exc:
                raise ParserError(f"Invalid agy JSON event on line {line_number}") from exc
            if not isinstance(event, dict) or not isinstance(event.get("event"), str):
                raise ParserError(f"Invalid agy event envelope on line {line_number}")
            if event["event"] == "init":
                init = event.get("init")
                if isinstance(init, dict):
                    model = init.get("model")
                    if isinstance(model, str) and model.strip():
                        model_used = model
            if event["event"] != "result":
                continue
            if result is not None:
                raise ParserError("Expected exactly one agy result; received duplicate results")
            result = event.get("result")
            if not isinstance(result, dict):
                raise ParserError("agy result payload must be an object")

        if result is None:
            raise ParserError("Missing agy result event")
        status = result.get("status")
        if status != "SUCCESS":
            status_label = repr(status) if isinstance(status, str) else "<missing or invalid>"
            message = f"agy result status is not SUCCESS: {status_label}"
            error = result.get("error")
            if isinstance(error, str) and error.strip():
                message += f": {error.strip()}"
            raise ParserError(message)
        response = result.get("response")
        if not isinstance(response, str) or not response.strip():
            raise ParserError("agy result response must be a nonempty string")
        # Select documented fields, never copying whole events or result envelopes.
        # Native conversation IDs and durations do not belong to PAL's namespace.
        metadata: dict[str, object] = {"agy_status": status}
        conversation_id = result.get("conversation_id")
        if isinstance(conversation_id, str) and conversation_id.strip():
            metadata["agy_conversation_id"] = conversation_id
        duration = result.get("duration_seconds")
        if type(duration) in (int, float) and 0 <= duration < float("inf"):
            metadata["agy_duration_seconds"] = duration
        num_turns = result.get("num_turns")
        if type(num_turns) is int and num_turns >= 0:
            metadata["num_turns"] = num_turns
        usage = result.get("usage")
        if isinstance(usage, dict):
            counts = {
                key: usage[key]
                for key in ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens")
                if type(usage.get(key)) is int and usage[key] >= 0
            }
            if counts:
                metadata["usage"] = counts
        if model_used is not None:
            metadata["model_used"] = model_used
        return ParsedCLIResponse(content=response, metadata=metadata)
