"""Tests for the Gemini CLI JSON parser."""

import json

import pytest

from clink.parsers.gemini import GeminiJSONParser, ParserError


def _build_rate_limit_stdout() -> str:
    return (
        "{\n"
        '  "response": "",\n'
        '  "stats": {\n'
        '    "models": {\n'
        '      "gemini-2.5-pro": {\n'
        '        "api": {\n'
        '          "totalRequests": 5,\n'
        '          "totalErrors": 5,\n'
        '          "totalLatencyMs": 13319\n'
        "        },\n"
        '        "tokens": {"prompt": 0, "candidates": 0, "total": 0, "cached": 0, "thoughts": 0, "tool": 0}\n'
        "      }\n"
        "    },\n"
        '    "tools": {"totalCalls": 0},\n'
        '    "files": {"totalLinesAdded": 0, "totalLinesRemoved": 0}\n'
        "  }\n"
        "}"
    )


def test_gemini_parser_handles_rate_limit_empty_response():
    parser = GeminiJSONParser()
    stdout = _build_rate_limit_stdout()
    stderr = "Attempt 1 failed with status 429. Retrying with backoff... ApiError: quota exceeded"

    parsed = parser.parse(stdout, stderr)

    assert "429" in parsed.content
    assert parsed.metadata.get("rate_limit_status") == 429
    assert parsed.metadata.get("empty_response") is True
    assert "Attempt 1 failed" in parsed.metadata.get("stderr", "")


def test_gemini_parser_still_errors_when_no_fallback_available():
    parser = GeminiJSONParser()
    stdout = '{"response": "", "stats": {}}'

    with pytest.raises(ParserError):
        parser.parse(stdout, stderr="")


@pytest.mark.parametrize(
    "stdout,stderr",
    [
        ("", "Error: UNSUPPORTED_CLIENT"),
        ("Error: UNSUPPORTED_CLIENT", ""),
        ("not JSON", "Error: UNSUPPORTED_CLIENT"),
        ('{"response": ""}', "Error: UNSUPPORTED_CLIENT"),
        ('{"error": {"code": "UNSUPPORTED_CLIENT"}}', ""),
        ('{"error": {"message": "UNSUPPORTED_CLIENT"}, "response": "partial"}', ""),
        ("null", "Error: UNSUPPORTED_CLIENT"),
    ],
)
def test_unsupported_client_failure_guidance(stdout, stderr):
    result = GeminiJSONParser().parse(stdout, stderr)
    assert result.metadata["cli_error_code"] == "UNSUPPORTED_CLIENT"
    assert "https://geminicli.com/docs/get-started/installation/" in result.content
    assert "https://geminicli.com/docs/resources/troubleshooting/" in result.content


@pytest.mark.parametrize("stderr", ["", "Previous attempt: UNSUPPORTED_CLIENT"])
def test_successful_answer_quoting_marker_is_preserved(stderr):
    answer = "The error code UNSUPPORTED_CLIENT means the client is unsupported."
    result = GeminiJSONParser().parse(json.dumps({"response": answer}), stderr)
    assert result.content == answer
    assert "unsupported_client" not in result.metadata


@pytest.mark.parametrize("stdout", ["null", "[]", '"UNSUPPORTED_CLIENT"'])
def test_non_object_json_is_a_parser_error(stdout):
    with pytest.raises(ParserError, match="must be an object"):
        GeminiJSONParser().parse(stdout, "")
