"""Opt-in private JSONL audit streams, independent of continuation storage.

One process owns a state directory. The lock serializes short synchronous file
operations; callers retain the conversation coordinator through memory + audit.
"""

import json
import logging
import os
import stat
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

from utils.env import get_env, get_env_bool
from utils.state_dir import get_state_dir

logger = logging.getLogger(__name__)
SCHEMA_VERSION = 1
RETENTION_DAYS = 30
TRANSCRIPT_LOCK = threading.RLock()


class TranscriptError(ValueError):
    """Unsafe transcript configuration or unsupported private filesystem access."""


def transcripts_enabled() -> bool:
    return get_env_bool("PAL_TRANSCRIPTS", False)


def canonical_thread_id(thread_id: str) -> str:
    """Accept only lowercase, hyphenated UUIDs; never turn an ID into a path."""
    try:
        if not isinstance(thread_id, str) or str(UUID(thread_id)) != thread_id:
            raise ValueError
    except (ValueError, AttributeError) as exc:
        raise TranscriptError("Transcript ID must be a canonical UUID") from exc
    return thread_id


def _require_private_filesystem() -> None:
    # chmod on Windows only changes the read-only flag, not the access ACL.
    # Fail closed until a native ACL + reparse-point implementation is validated.
    if sys.platform == "win32" or not all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY", "fchmod")):
        raise TranscriptError("Private transcripts require POSIX permissions; Windows ACL support is unavailable")


def _make_private(fd: int, *, directory: bool = False) -> os.stat_result:
    info = os.fstat(fd)
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(info.st_mode) or (not directory and info.st_nlink != 1):
        raise TranscriptError("Transcript path must be a directory or a single-link regular file")
    if info.st_uid != os.getuid():
        raise TranscriptError("Transcript path must belong to the current user")
    mode = 0o700 if directory else 0o600
    os.fchmod(fd, mode)
    if stat.S_IMODE(os.fstat(fd).st_mode) != mode:
        raise TranscriptError("Cannot enforce private transcript permissions")
    return info


@contextmanager
def transcript_directory(*, create: bool = False) -> Iterator[int]:
    """Open the owned directory without following its final path component."""
    _require_private_filesystem()
    # The shared resolver deliberately normalizes platform aliases (e.g. /tmp on
    # macOS). Reject explicitly linked operator roots before that normalization.
    for name in ("PAL_STATE_DIR",) if get_env("PAL_STATE_DIR") else ("XDG_STATE_HOME",):
        configured = get_env(name)
        if configured and Path(configured).expanduser().is_symlink():
            raise TranscriptError(f"{name} must not be a symlink for transcripts")
    root = get_state_dir(create=create, resolve_links=False)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if create:
            try:
                os.mkdir("transcripts", 0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
        fd = os.open("transcripts", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        try:
            _make_private(fd, directory=True)
            yield fd
        finally:
            os.close(fd)
    finally:
        os.close(root_fd)


@contextmanager
def transcript_file(directory_fd: int, thread_id: str, *, append: bool = False) -> Iterator[int]:
    name = canonical_thread_id(thread_id) + ".jsonl"
    flags = os.O_NOFOLLOW | os.O_NONBLOCK
    flags |= (os.O_RDWR | os.O_CREAT | os.O_APPEND) if append else os.O_RDONLY
    fd = os.open(name, flags, 0o600, dir_fd=directory_fd)
    try:
        _make_private(fd)
        yield fd
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(fd, remaining)
        if written <= 0:
            raise OSError("Transcript append made no progress")
        remaining = remaining[written:]


def append_event(thread_id: str, event: dict[str, Any]) -> None:
    """Append one complete event, preserving prior bytes on ordinary I/O failure."""
    canonical_thread_id(thread_id)
    data = (
        json.dumps({**event, "schema_version": SCHEMA_VERSION, "thread_id": thread_id}, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    with TRANSCRIPT_LOCK, transcript_directory(create=True) as directory_fd:
        with transcript_file(directory_fd, thread_id, append=True) as fd:
            original_size = os.fstat(fd).st_size
            if original_size:
                os.lseek(fd, -1, os.SEEK_END)
                if os.read(fd, 1) != b"\n":
                    # Preserve an interrupted record as its own malformed line;
                    # never join the next valid event onto a crash fragment.
                    data = b"\n" + data
            try:
                _write_all(fd, data)
            except OSError:
                os.ftruncate(fd, original_size)
                raise


def record_event(thread_id: str, event: dict[str, Any]) -> None:
    """Best-effort side channel, called only after a successful memory write."""
    if not transcripts_enabled():
        return
    try:
        append_event(thread_id, event)
    except (OSError, ValueError, TypeError) as exc:
        # Do not log serialized content or exception text (it may contain data).
        logger.warning("Transcript append failed (%s); conversation memory is intact", type(exc).__name__)


def prune_transcripts(*, now: float | None = None) -> int:
    """Remove only canonical, expired, owned regular files; diagnose each failure."""
    if not transcripts_enabled():
        return 0
    removed = 0
    cutoff = (time.time() if now is None else now) - RETENTION_DAYS * 86400
    try:
        with TRANSCRIPT_LOCK, transcript_directory() as directory_fd:
            with os.scandir(directory_fd) as entries:
                for entry in entries:
                    if not entry.name.endswith(".jsonl"):
                        continue
                    try:
                        canonical_thread_id(entry.name[:-6])
                    except TranscriptError:
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if (
                            stat.S_ISREG(info.st_mode)
                            and info.st_nlink == 1
                            and info.st_uid == os.getuid()
                            and info.st_mtime < cutoff
                        ):
                            os.unlink(entry.name, dir_fd=directory_fd)
                            removed += 1
                    except OSError as exc:
                        logger.warning("Transcript retention failed (%s)", type(exc).__name__)
    except FileNotFoundError:
        pass  # Opt-in does not create a directory until the first record.
    except (OSError, ValueError) as exc:
        logger.warning("Transcript retention unavailable (%s): %s", type(exc).__name__, exc)
    return removed
