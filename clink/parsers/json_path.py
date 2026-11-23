"""Extract a text reply by following literal object keys in a single JSON document."""

import json

from .base import BaseParser, ParsedCLIResponse, ParserError


def _reject_constant(token: str) -> None:
    raise ValueError("Invalid JSON number")


class JSONPathParser(BaseParser):
    name = "json"

    def __init__(self, path: list[str]) -> None:
        self.path = tuple(path)
        self.location = f"JSON path {json.dumps(self.path)}"

    def parse(self, stdout: str, stderr: str) -> ParsedCLIResponse:
        try:
            # Numbers cannot be reply strings. Avoid integer conversion limits
            # on unrelated fields in an otherwise valid document.
            value = json.loads(stdout, parse_constant=_reject_constant, parse_int=float)
        except (ValueError, RecursionError):
            raise ParserError(f"{self.location}: expected a single valid JSON document") from None

        for key in self.path:
            if not isinstance(value, dict):
                raise ParserError(f"{self.location}: expected an object before key {json.dumps(key)}")
            if key not in value:
                raise ParserError(f"{self.location}: missing key {json.dumps(key)}")
            value = value[key]

        if not isinstance(value, str) or not value.strip():
            raise ParserError(f"{self.location}: expected a nonblank string")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ParserError(f"{self.location}: selected string cannot be encoded as UTF-8") from None
        return ParsedCLIResponse(content=value, metadata={})
