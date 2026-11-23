"""Plain text replies from a config-only client."""

from .base import BaseParser, ParsedCLIResponse, ParserError


class TextParser(BaseParser):
    name = "text"

    def parse(self, stdout: str, stderr: str) -> ParsedCLIResponse:
        if not stdout.strip():
            raise ParserError("expected nonblank text on stdout")
        return ParsedCLIResponse(content=stdout, metadata={})
