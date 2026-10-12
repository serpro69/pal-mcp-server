"""Bounded, validated read views over private conversation audit streams."""

import errno
import json
import math
import os
import stat
from datetime import datetime
from typing import Any

from utils.transcripts import (
    SCHEMA_VERSION,
    TRANSCRIPT_LOCK,
    TranscriptError,
    canonical_thread_id,
    transcript_directory,
    transcript_file,
    transcripts_enabled,
)

MAX_LIST_ITEMS = 100
MAX_DIRECTORY_ENTRIES = 10_000
MAX_READ_BYTES = 1024 * 1024
MAX_READ_RECORDS = 1000
MAX_JSON_DEPTH = 64


class ResourceNotFoundError(LookupError):
    """The resource is unknown, unavailable, or disabled."""


class ResourceLimitError(OSError):
    """A complete deterministic index cannot fit the directory scan bound."""


def _require_enabled() -> None:
    if not transcripts_enabled():
        raise ResourceNotFoundError("Conversation resources are disabled")


def list_threads() -> dict[str, Any]:
    """Return deterministic file summaries, without reading prompt contents."""
    _require_enabled()
    summaries = []
    skipped = 0
    try:
        with TRANSCRIPT_LOCK, transcript_directory() as directory_fd:
            with os.scandir(directory_fd) as entries:
                for count, entry in enumerate(entries, 1):
                    if count > MAX_DIRECTORY_ENTRIES:
                        raise ResourceLimitError("Transcript directory exceeds the 10000-entry scan limit")
                    try:
                        if not entry.name.endswith(".jsonl"):
                            raise TranscriptError("Unrecognized filename")
                        thread_id = canonical_thread_id(entry.name[:-6])
                        info = entry.stat(follow_symlinks=False)
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            raise TranscriptError("Unsafe transcript entry")
                    except (TranscriptError, FileNotFoundError):
                        skipped += 1
                        continue
                    if info.st_uid != os.getuid():
                        raise PermissionError("Transcript belongs to another user")
                    summaries.append(
                        {
                            "thread_id": thread_id,
                            "uri": f"pal://threads/{thread_id}",
                            "size_bytes": info.st_size,
                            "modified_at": info.st_mtime,
                        }
                    )
    except FileNotFoundError:
        pass  # Enabled, no transcripts recorded yet.
    summaries.sort(key=lambda item: item["thread_id"])
    return {
        "schema_version": SCHEMA_VERSION,
        "threads": summaries[:MAX_LIST_ITEMS],
        "truncated": len(summaries) > MAX_LIST_ITEMS,
        "total_threads": len(summaries),
        "skipped_files": skipped,
        "limit": MAX_LIST_ITEMS,
    }


def _valid_record(record: Any, thread_id: str) -> bool:
    if not isinstance(record, dict) or type(record.get("schema_version")) is not int:
        return False
    if record["schema_version"] != SCHEMA_VERSION or record.get("thread_id") != thread_id:
        return False
    if not isinstance(record.get("timestamp"), str):
        return False
    try:
        datetime.fromisoformat(record["timestamp"])
    except ValueError:
        return False
    if record.get("event") == "thread":
        return isinstance(record.get("tool_name"), str)
    return (
        record.get("event") == "turn"
        and type(record.get("turn")) is int
        and record["turn"] > 0
        and record.get("role") in ("user", "assistant")
        and isinstance(record.get("content"), str)
    )


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON value")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite JSON number")
    return result


def _check_json_depth(record: dict[str, Any]) -> None:
    pending = [(record, 1)]
    while pending:
        value, depth = pending.pop()
        if isinstance(value, (dict, list)):
            if depth > MAX_JSON_DEPTH:
                raise ValueError("Transcript JSON nesting limit exceeded")
            children = value.values() if isinstance(value, dict) else value
            pending.extend((child, depth + 1) for child in children)


def read_thread(thread_id: str) -> dict[str, Any]:
    _require_enabled()
    try:
        canonical_thread_id(thread_id)
    except TranscriptError as exc:
        raise ResourceNotFoundError("Invalid transcript ID") from exc
    try:
        with TRANSCRIPT_LOCK, transcript_directory() as directory_fd:
            try:
                with transcript_file(directory_fd, thread_id) as fd:
                    with os.fdopen(fd, "rb", closefd=False) as stream:
                        raw = stream.read(MAX_READ_BYTES + 1)
            except TranscriptError as exc:
                raise ResourceNotFoundError("Unsafe transcript file") from exc
    except FileNotFoundError as exc:
        raise ResourceNotFoundError("Transcript not found") from exc
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EISDIR):
            raise ResourceNotFoundError("Unsafe transcript path") from exc
        raise

    byte_limited = len(raw) > MAX_READ_BYTES
    data = raw[:MAX_READ_BYTES]
    lines = data.splitlines(keepends=True)
    records = []
    malformed = 0
    interrupted = 0
    examined = 0
    for line in lines[:MAX_READ_RECORDS]:
        examined += 1
        if not line.endswith(b"\n"):
            if not byte_limited:
                interrupted += 1
            continue  # Never present a partial record as a complete event.
        try:
            record = json.loads(line, parse_constant=_reject_constant, parse_float=_finite_float)
            if not _valid_record(record, thread_id):
                raise ValueError("Malformed event")
            _check_json_depth(record)
            # Escaped lone surrogates cannot be transmitted as UTF-8.
            json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, UnicodeError, RecursionError):
            malformed += 1
            continue
        records.append(record)
    return {
        "schema_version": SCHEMA_VERSION,
        "thread_id": thread_id,
        "records": records,
        "truncated": byte_limited or len(lines) > MAX_READ_RECORDS,
        "diagnostics": {
            "skipped_malformed_records": malformed,
            "interrupted_final_lines": interrupted,
            "examined_records": examined,
            "byte_limit": MAX_READ_BYTES,
            "record_limit": MAX_READ_RECORDS,
        },
    }


def resource_thread_id(uri: str) -> str | None:
    """Validate the raw URI before an SDK URL parser can normalize dot segments."""
    if uri == "pal://threads":
        return None
    prefix = "pal://threads/"
    if uri.startswith(prefix):
        try:
            return canonical_thread_id(uri[len(prefix) :])
        except TranscriptError as exc:
            raise ResourceNotFoundError("Invalid transcript ID") from exc
    raise ResourceNotFoundError("Unknown conversation resource")


def read_resource(uri: str) -> dict[str, Any]:
    """Exact URI grammar; no decoded/traversal paths, queries or fragments."""
    _require_enabled()
    thread_id = resource_thread_id(uri)
    return list_threads() if thread_id is None else read_thread(thread_id)
