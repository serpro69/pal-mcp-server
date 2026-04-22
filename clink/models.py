"""Pydantic models for clink configuration and runtime structures."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, PositiveInt, field_validator, model_validator


class OutputCaptureConfig(BaseModel):
    """Optional configuration for CLIs that write output to disk."""

    flag_template: str = Field(..., description="Template used to inject the output path, e.g. '--output {path}'.")
    cleanup: bool = Field(
        default=True,
        description="Whether the temporary file should be removed after reading.",
    )


class CLIRoleConfig(BaseModel):
    """Role-specific configuration loaded from JSON manifests."""

    prompt_path: str | None = Field(
        default=None,
        description="Path to the prompt file that seeds this role.",
    )
    role_args: list[str] = Field(default_factory=list)
    description: str | None = Field(default=None)

    @field_validator("role_args", mode="before")
    @classmethod
    def _ensure_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            return [value]
        raise TypeError("role_args must be a list of strings or a single string")


class CLIClientConfig(BaseModel):
    """Raw CLI client configuration before internal defaults are applied."""

    name: str
    command: str | None = None
    working_dir: str | None = None
    additional_args: list[str] = Field(default_factory=list)
    safe_args: list[str] = Field(
        default_factory=list,
        description="Args appended when allow_edits=false (the default). Keep write-disabling flags here.",
    )
    edit_args: list[str] = Field(
        default_factory=list,
        description="Args appended when allow_edits=true. Keep write-enabling flags here.",
    )
    env: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: PositiveInt | None = Field(default=None)
    roles: dict[str, CLIRoleConfig] = Field(default_factory=dict)
    output_to_file: OutputCaptureConfig | None = None

    # Flags known to grant the CLI unrestricted filesystem-write capability.
    # They MUST NOT appear in `additional_args` (which is always applied) — only in
    # `edit_args`, which is gated behind the caller's explicit `allow_edits=true`.
    # Keep this set conservative — false positives block config authors from writing
    # legitimate configs; false negatives let a dangerous default slip through.
    _DANGEROUS_DEFAULT_FLAGS: frozenset[str] = frozenset(
        {
            "--yolo",
            "--dangerously-bypass-approvals-and-sandbox",
            # Claude's --permission-mode is only dangerous at value=acceptEdits; we
            # detect that combination below rather than flagging the flag itself.
        }
    )

    @field_validator("additional_args", "safe_args", "edit_args", mode="before")
    @classmethod
    def _ensure_args_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            return [value]
        raise TypeError("arg list fields must be a list of strings or a single string")

    @model_validator(mode="after")
    def _enforce_arg_bucket_invariants(self) -> CLIClientConfig:
        """Enforce structural invariants on the arg buckets.

        - `safe_args` and `edit_args` must differ — otherwise `allow_edits=true`
          is a no-op and a config author has almost certainly made a mistake.
        - `additional_args` must not contain known write-enabling flags —
          those belong in `edit_args` so they're gated behind `allow_edits`.

        Note: arg overlap at the string level is allowed — e.g. Claude puts
        `--permission-mode default` in safe_args and `--permission-mode
        acceptEdits` in edit_args. The flag name appears in both; the value
        differs. We intentionally don't enforce disjoint-by-token.
        """
        if (self.safe_args or self.edit_args) and self.safe_args == self.edit_args:
            raise ValueError(
                "safe_args and edit_args must differ — identical buckets make "
                "allow_edits=true a no-op. Move shared args to additional_args."
            )

        dangerous_in_defaults = set(self.additional_args) & self._DANGEROUS_DEFAULT_FLAGS
        if dangerous_in_defaults:
            raise ValueError(
                f"additional_args must not contain write-enabling flags "
                f"{sorted(dangerous_in_defaults)}; move them to edit_args "
                "so they are gated behind allow_edits=true."
            )
        # Detect Claude's --permission-mode acceptEdits as a flag/value pair in
        # additional_args (the only combination that grants unrestricted writes).
        for i, arg in enumerate(self.additional_args[:-1]):
            if arg == "--permission-mode" and self.additional_args[i + 1] == "acceptEdits":
                raise ValueError(
                    "additional_args must not contain '--permission-mode acceptEdits'; "
                    "place it in edit_args so it's gated behind allow_edits=true."
                )
        return self


class ResolvedCLIRole(BaseModel):
    """Runtime representation of a CLI role with resolved prompt path."""

    name: str
    prompt_path: Path
    role_args: list[str] = Field(default_factory=list)
    description: str | None = None


class ResolvedCLIClient(BaseModel):
    """Runtime configuration after merging defaults and validating paths."""

    name: str
    executable: list[str]
    working_dir: Path | None
    internal_args: list[str] = Field(default_factory=list)
    config_args: list[str] = Field(default_factory=list)
    safe_args: list[str] = Field(default_factory=list)
    edit_args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int
    parser: str
    runner: str | None = None
    roles: dict[str, ResolvedCLIRole]
    output_to_file: OutputCaptureConfig | None = None

    def list_roles(self) -> list[str]:
        return list(self.roles.keys())

    def get_role(self, role_name: str | None) -> ResolvedCLIRole:
        key = role_name or "default"
        if key not in self.roles:
            available = ", ".join(sorted(self.roles.keys()))
            raise KeyError(f"Role '{role_name}' not configured for CLI '{self.name}'. Available roles: {available}")
        return self.roles[key]
