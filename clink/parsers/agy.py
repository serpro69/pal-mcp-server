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
            if event["event"] != "result":
                continue
            if result is not None:
                raise ParserError("Expected exactly one agy result; received duplicate results")
            result = event.get("result")
            if not isinstance(result, dict):
                raise ParserError("agy result payload must be an object")

        if result is None:
            raise ParserError("Missing agy result event")
        if result.get("status") != "SUCCESS":
            raise ParserError("agy result status is not SUCCESS")
        response = result.get("response")
        if not isinstance(response, str) or not response.strip():
            raise ParserError("agy result response must be a nonempty string")
        return ParsedCLIResponse(content=response, metadata={})
