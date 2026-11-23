"""Opt-in generic CLI failure diagnostics for PAL's main rotating log."""

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

_EXCERPT_LIMIT = 2048
_TRUNCATED = "[truncated] "


class _PrivateStderrRecord(logging.LogRecord):
    """Only the main file handler may emit this record, including on I/O failure."""


class MainLogHandler(RotatingFileHandler):
    def handleError(self, record):
        if not isinstance(record, _PrivateStderrRecord):
            super().handleError(record)


def _sensitive_bytes(prompt: str, env: dict[str, str], *, flag_prompt: bool) -> set[bytes]:
    # UTF-8 is used for stdin and can also be emitted by a CLI printing Unicode
    # argv or environment strings. POSIX can pass different native bytes.
    literals = {value.encode("utf-8", errors="surrogateescape") for value in (prompt, *env.values())}
    if os.name == "posix":
        literals.update(os.fsencode(value) for value in env.values())
        if flag_prompt:
            literals.add(os.fsencode(prompt))
    literals.discard(b"")
    return literals


def _stderr_excerpt(stderr: bytes, prompt: str, env: dict[str, str], *, flag_prompt: bool = False) -> str:
    # surrogateescape keeps the mapping between displayed characters and original
    # bytes reversible, even when an echoed value starts inside a UTF-8 character.
    text = stderr.decode("utf-8", errors="surrogateescape")
    tail = list(text[-_EXCERPT_LIMIT:])
    widths = [len(char.encode("utf-8", errors="surrogateescape")) for char in tail]
    hidden = bytearray(sum(widths))
    start = len(stderr) - len(hidden)
    for value in _sensitive_bytes(prompt, env, flag_prompt=flag_prompt):
        width = len(value)
        # Of the matches crossing the window's left edge, the rightmost covers
        # the visible part of all earlier ones. Search the unmodified bytes.
        match = stderr.rfind(value, max(0, start - width + 1), start + width)
        if match < 0:
            match = stderr.find(value, start)
        while match >= 0:
            left = max(match, start)
            end = match + width
            hidden[left - start : end - start] = b"\x01" * (end - left)
            match = stderr.find(value, max(match, start) + 1)

    # JSON escaping renders controls (including Unicode line separators) on one
    # ASCII line. Expansion can exceed the limit even for short input.
    offset = 0
    for index, width in enumerate(widths):
        if any(hidden[offset : offset + width]):
            tail[index] = "*"
        offset += width
    escaped = [json.dumps(char, ensure_ascii=True)[1:-1] for char in tail]
    size = sum(map(len, escaped))
    if start or size > _EXCERPT_LIMIT:
        first = 0
        while size > _EXCERPT_LIMIT - len(_TRUNCATED):
            size -= len(escaped[first])
            first += 1
        return _TRUNCATED + "".join(escaped[first:])
    return "".join(escaped)


def _write_stderr(client: str, stderr: bytes, prompt: str, env: dict[str, str], flag_prompt: bool) -> None:
    for handler in logging.getLogger().handlers:
        if isinstance(handler, MainLogHandler):
            excerpt = _stderr_excerpt(stderr, prompt, env, flag_prompt=flag_prompt)
            # Manifest names can be long and can contain Unicode line separators.
            label = ascii(client[:80]) + ("..." if len(client) > 80 else "")
            record = _PrivateStderrRecord(
                "clink.stderr", logging.WARNING, __file__, 0, "CLI %s failure stderr: %s", (label, excerpt), None
            )
            # Direct delivery avoids root, activity and logging.lastResort.
            handler.handle(record)
            return


@asynccontextmanager
async def private_stderr_on_failure(
    client: str, stderr: bytes, prompt: str, env: dict[str, str], *, flag_prompt: bool
) -> AsyncIterator[None]:
    try:
        yield
    except Exception:
        try:
            await asyncio.to_thread(_write_stderr, client, stderr, prompt, env, flag_prompt)
        except Exception:
            # Filtering or logging is best effort. Even its exception can contain
            # private data, so preserve the CLI failure without another log route.
            pass
        raise
