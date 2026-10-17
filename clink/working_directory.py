"""Request-local directory selection, independent of storage and CLI execution.

Launch spelling and canonical identity deliberately remain separate. Validation
does not pin filesystem contents; callers must still handle failures at spawn.
"""

from __future__ import annotations

import errno
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# A present JSON null is corrupt binding data, not an absent legacy binding.
MISSING_BINDING = object()


def _absolute_path(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\x00" not in value and os.path.isabs(value)


@dataclass(frozen=True)
class WorkingDirectoryBinding:
    """Persistable choice, without invocation source or a temporary pathname."""

    mode: Literal["directory", "scratch"]
    launch_path: str | None = None
    canonical_path: str | None = None

    def __post_init__(self) -> None:
        if self.mode == "scratch":
            if self.launch_path is not None or self.canonical_path is not None:
                raise ValueError("Scratch bindings must not contain paths")
        elif self.mode == "directory":
            if not _absolute_path(self.launch_path) or not _absolute_path(self.canonical_path):
                raise ValueError("Directory bindings require absolute launch_path and canonical_path strings")
        else:
            raise ValueError("Working directory mode must be directory or scratch")

    def to_record(self) -> dict[str, str]:
        if self.mode == "scratch":
            return {"mode": "scratch"}
        return {"mode": "directory", "launch_path": self.launch_path, "canonical_path": self.canonical_path}


@dataclass(frozen=True)
class WorkingDirectorySelection:
    """A candidate binding and its source for this invocation only."""

    binding: WorkingDirectoryBinding
    source: Literal["request", "conversation", "configuration", "scratch"]

    def __post_init__(self) -> None:
        if not isinstance(self.binding, WorkingDirectoryBinding):
            raise ValueError("Selection requires a working directory binding")
        allowed = (
            ("scratch", "conversation")
            if self.binding.mode == "scratch"
            else ("request", "conversation", "configuration")
        )
        if self.source not in allowed:
            raise ValueError("Selection source does not match directory mode")

    def to_metadata(self) -> dict[str, str | None]:
        return {
            "mode": self.binding.mode,
            "launch_path": self.binding.launch_path,
            "canonical_path": self.binding.canonical_path,
            "source": self.source,
        }


class WorkingDirectoryError(ValueError):
    """Domain failure with path details; transport envelopes belong to the tool."""

    new_conversation_required = False

    def __init__(
        self,
        message: str,
        *,
        bound: WorkingDirectoryBinding | None = None,
        requested: object = None,
        normalized: str | None = None,
        canonical: str | None = None,
    ) -> None:
        super().__init__(message)
        self.bound = bound if bound is not None and bound.mode == "directory" else None
        self.requested = requested if isinstance(requested, str) else None
        self.normalized = normalized
        self.canonical = canonical

    def to_details(self) -> dict[str, object]:
        return {
            "error_type": type(self).__name__,
            "new_conversation_required": self.new_conversation_required,
            "bound_working_directory": self.bound.to_record() if self.bound is not None else None,
            "requested_working_directory": self.requested,
            "normalized_requested_path": self.normalized,
            "canonical_requested_path": self.canonical,
        }


class InvalidWorkingDirectoryError(WorkingDirectoryError):
    """The selected request or configuration directory is invalid."""


class WorkingDirectoryConflictError(WorkingDirectoryError):
    """A valid requested directory differs from the established project."""

    new_conversation_required = True


class BoundWorkingDirectoryUnavailableError(WorkingDirectoryError):
    """The saved launch/identity pair is unavailable or no longer equivalent."""


class InvalidWorkingDirectoryBindingError(WorkingDirectoryError):
    """Present conversation data is malformed and must not be migrated."""

    new_conversation_required = True


def decode_binding(value: object = MISSING_BINDING) -> WorkingDirectoryBinding | None:
    """Decode an exact persisted shape; only the sentinel means no binding."""
    if value is MISSING_BINDING:
        return None
    if isinstance(value, dict):
        if value == {"mode": "scratch"}:
            return WorkingDirectoryBinding("scratch")
        if set(value) == {"mode", "launch_path", "canonical_path"} and value["mode"] == "directory":
            try:
                return WorkingDirectoryBinding("directory", value["launch_path"], value["canonical_path"])
            except ValueError as exc:
                raise InvalidWorkingDirectoryBindingError("Malformed saved working directory binding") from exc
    raise InvalidWorkingDirectoryBindingError("Malformed saved working directory binding")


def _normalize_request(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError("working_directory_absolute_path must be a nonempty path string without NUL characters")
    return os.path.normpath(os.path.expanduser(value))


def _require_directory(path: str) -> None:
    # stat propagates access and resolution errors instead of treating them as
    # absence. Search permission is required for cwd, but write permission is not.
    if not stat.S_ISDIR(os.stat(path).st_mode):
        raise NotADirectoryError(errno.ENOTDIR, "Selected working directory is not a directory", path)
    if not os.access(path, os.X_OK, effective_ids=os.access in os.supports_effective_ids):
        raise PermissionError(errno.EACCES, "Selected working directory is not accessible", path)


def resolve_working_directory(
    *,
    requested: object = None,
    configured: Path | None = None,
    saved_binding: object = MISSING_BINDING,
) -> WorkingDirectorySelection:
    """Resolve one candidate without mutating configuration or conversation data.

    Pass the current thread's binding, or MISSING_BINDING when its key is absent.
    Do not pass a parent's binding or a historical raw request field. The caller
    owns success-only persistence, including promotion from scratch to a project.
    """
    try:
        bound = decode_binding(saved_binding)
    except InvalidWorkingDirectoryBindingError as exc:
        raise InvalidWorkingDirectoryBindingError(str(exc), requested=requested) from exc

    normalized = canonical = None
    candidate = None
    if requested is not None:
        try:
            normalized = _normalize_request(requested)
            if not os.path.isabs(normalized):
                raise ValueError("working_directory_absolute_path must be absolute after home expansion")
            canonical = str(Path(normalized).resolve(strict=True))
            _require_directory(normalized)
            candidate = WorkingDirectoryBinding("directory", normalized, canonical)
        except (ValueError, OSError, RuntimeError) as exc:
            raise InvalidWorkingDirectoryError(
                f"Invalid requested working directory: {exc}",
                bound=bound,
                requested=requested,
                normalized=normalized,
                canonical=canonical,
            ) from exc

    if bound is not None and bound.mode == "directory":
        try:
            _require_directory(bound.launch_path)
            _require_directory(bound.canonical_path)
            if not os.path.samefile(bound.launch_path, bound.canonical_path):
                raise ValueError("Saved launch path no longer refers to the bound project")
        except (OSError, ValueError) as exc:
            raise BoundWorkingDirectoryUnavailableError(
                f"Bound working directory is unavailable: {exc}",
                bound=bound,
                requested=requested,
                normalized=normalized,
                canonical=canonical,
            ) from exc
        if candidate is not None:
            try:
                equivalent = os.path.samefile(bound.launch_path, candidate.launch_path)
            except OSError as exc:
                raise InvalidWorkingDirectoryError(
                    f"Cannot compare requested working directory with the bound project: {exc}",
                    bound=bound,
                    requested=requested,
                    normalized=normalized,
                    canonical=canonical,
                ) from exc
            if not equivalent:
                raise WorkingDirectoryConflictError(
                    "Working directory differs from the bound project; start a new conversation to change projects.",
                    bound=bound,
                    requested=requested,
                    normalized=normalized,
                    canonical=canonical,
                )
        return WorkingDirectorySelection(bound, "conversation")

    if candidate is not None:
        return WorkingDirectorySelection(candidate, "request")
    if bound is not None:
        return WorkingDirectorySelection(bound, "conversation")
    if configured is not None:
        # Registry resolution has already chosen this spelling. In particular,
        # normalizing '..' here could change an operator's symlink traversal.
        launch = str(configured)
        try:
            if not _absolute_path(launch):
                raise ValueError("Configured working directory must be absolute")
            canonical_configured = str(Path(launch).resolve(strict=True))
            _require_directory(launch)
        except (ValueError, OSError, RuntimeError) as exc:
            raise InvalidWorkingDirectoryError(f"Invalid configured working directory: {exc}") from exc
        return WorkingDirectorySelection(
            WorkingDirectoryBinding("directory", launch, canonical_configured), "configuration"
        )
    return WorkingDirectorySelection(WorkingDirectoryBinding("scratch"), "scratch")
