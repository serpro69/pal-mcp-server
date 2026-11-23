"""Pydantic models for clink configuration and runtime structures."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PositiveInt, field_validator, model_validator

from clink.constants import DEFAULT_HISTORY_TOKEN_BUDGET

MAX_CLI_MODEL_CHARS = 512


def validate_cli_model(value: str | None) -> str | None:
    """Validate an opaque CLI model identifier without interpreting it as argv."""
    if value is not None and (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_CLI_MODEL_CHARS
        or value != value.strip()
        or value.startswith("-")
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(
            f"model must be a nonempty string of at most {MAX_CLI_MODEL_CHARS} characters "
            "without surrounding whitespace, control characters, or a leading '-'"
        )
    return value


def validate_model_args(value: list[str]) -> list[str]:
    """Only model-selector options may carry caller-controlled model values."""
    if value and value not in (["--model", "{model}"], ["-m", "{model}"]):
        raise ValueError("model_args must be [] or ['--model', '{model}'] or ['-m', '{model}']")
    return value


class OutputCaptureConfig(BaseModel):
    """Optional configuration for CLIs that write output to disk."""

    flag_template: str = Field(..., description="Template used to inject the output path, e.g. '--output {path}'.")
    cleanup: bool = Field(
        default=True,
        description="Whether the temporary file should be removed after reading.",
    )


class _RoleFields(BaseModel):
    """Role-specific configuration loaded from JSON manifests."""

    prompt_path: str | None = Field(
        default=None,
        description="Path to the prompt file that seeds this role.",
    )
    role_args: list[str] = Field(default_factory=list)
    description: str | None = Field(default=None)


class CLIRoleConfig(_RoleFields):
    """Shipped clients accept the historical role argument shorthand."""

    @field_validator("role_args", mode="before")
    @classmethod
    def _ensure_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            return [value]
        raise ValueError("role_args must be a list of strings or a single string")


class _ClientFields(BaseModel):
    """Fields and permission invariants shared by native and generic clients."""

    name: str
    command: str | None = None
    model_args: list[str] = Field(default_factory=list)
    default_model: str | None = Field(default=None, strict=True)
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
    env_passthrough: list[str] = Field(
        default_factory=list,
        description="Additional inherited environment names; explicit env values take precedence.",
    )
    timeout_seconds: PositiveInt | None = Field(default=None)
    history_token_budget: int = Field(
        default=DEFAULT_HISTORY_TOKEN_BUDGET,
        strict=True,
        gt=0,
        description="Estimated-token limit for rendered CLI conversation history, excluding the current request.",
    )

    @field_validator("model_args")
    @classmethod
    def _validate_model_args(cls, value: list[str]) -> list[str]:
        return validate_model_args(value)

    @field_validator("default_model")
    @classmethod
    def _validate_default_model(cls, value: str | None) -> str | None:
        return validate_cli_model(value)

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

    @model_validator(mode="after")
    def _enforce_arg_bucket_invariants(self) -> _ClientFields:
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
        if self.default_model is not None and not self.model_args:
            raise ValueError("default_model requires a model_args template")
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

    def executable_tokens(self) -> list[str]:
        try:
            tokens = shlex.split(self.command or "")
        except ValueError:
            raise ValueError("command has invalid quoting or escaping") from None
        if not tokens or not tokens[0]:
            raise ValueError("command must specify an executable")
        return tokens


class CLIClientConfig(_ClientFields):
    """Raw configuration for a client with a shipped adapter."""

    roles: dict[str, CLIRoleConfig] = Field(default_factory=dict)
    output_to_file: OutputCaptureConfig | None = None

    @field_validator("additional_args", "safe_args", "edit_args", mode="before")
    @classmethod
    def _ensure_args_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            return [value]
        raise ValueError("arg list fields must be a list of strings or a single string")


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("must contain non-whitespace text")
    return value


# Validate individual JSON values before they can reach a subprocess or a diagnostic.
ProcessString = Annotated[str, Field(pattern=r"^[^\x00]*$")]
EnvironmentName = Annotated[str, Field(pattern=r"^[^=\x00]+$")]
ManifestName = Annotated[str, Field(pattern=r"^[^\p{Cc}]*$"), AfterValidator(_nonblank)]


class GenericRoleConfig(_RoleFields):
    model_config = ConfigDict(extra="forbid", strict=True)

    prompt_path: Annotated[ProcessString, Field(min_length=1)] | None = None
    role_args: list[ProcessString] = Field(default_factory=list)


class _ProtocolObject(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class StdinPrompt(_ProtocolObject):
    mode: Literal["stdin"]


class FlagPrompt(_ProtocolObject):
    mode: Literal["flag"]
    flag: str = Field(pattern=r"^-{1,2}[A-Za-z][A-Za-z0-9_-]*$")


class TextOutput(_ProtocolObject):
    format: Literal["text"]


class JSONOutput(_ProtocolObject):
    format: Literal["json"]
    path: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1)


class GenericProtocol(_ProtocolObject):
    prompt: StdinPrompt | FlagPrompt = Field(discriminator="mode")
    output: TextOutput | JSONOutput = Field(discriminator="format")
    workspace: Literal["any", "project"]
    failure_hint: Annotated[str, Field(max_length=240, pattern=r"^[^\p{Cc}\p{Zl}\p{Zp}]*$"), AfterValidator(_nonblank)]
    log_stderr_on_failure: bool = False


class GenericCLIClientConfig(_ClientFields):
    """Complete operator manifest, with no native runner or parser hooks."""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: ManifestName
    command: ProcessString
    safe_args: list[ProcessString]
    edit_args: list[ProcessString]
    env_passthrough: list[EnvironmentName]
    custom: GenericProtocol
    env: dict[EnvironmentName, ProcessString] = Field(default_factory=dict)
    working_dir: ProcessString | None = None
    additional_args: list[ProcessString] = Field(default_factory=list)
    roles: dict[ManifestName, GenericRoleConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_prompt_arguments(self) -> GenericCLIClientConfig:
        prefix = self.executable_tokens()[1:]
        if isinstance(self.custom.prompt, FlagPrompt):
            name = self.custom.prompt.flag.lstrip("-")
            reserved = {f"-{name}", f"--{name}"}
            buckets = [
                ("command", prefix),
                ("additional_args", self.additional_args),
                ("safe_args", self.safe_args),
                ("edit_args", self.edit_args),
                ("model_args", self.model_args),
                *(("roles.role_args", role.role_args) for role in self.roles.values()),
            ]
            for field, tokens in buckets:
                if any(token == "--" or token.partition("=")[0] in reserved for token in tokens):
                    raise ValueError(f"{field} conflicts with custom.prompt.flag")
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
    model_args: list[str] = Field(default_factory=list)
    default_model: str | None = Field(default=None, strict=True)
    requested_model: str | None = Field(default=None, strict=True)
    working_dir: Path | None
    internal_args: list[str] = Field(default_factory=list)
    config_args: list[str] = Field(default_factory=list)
    safe_args: list[str] = Field(default_factory=list)
    edit_args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    env_passthrough: list[str] = Field(default_factory=list)
    timeout_seconds: int
    history_token_budget: int = Field(default=DEFAULT_HISTORY_TOKEN_BUDGET, strict=True, gt=0)
    parser: str
    custom: GenericProtocol | None = None
    runner: str | None = None
    roles: dict[str, ResolvedCLIRole]
    output_to_file: OutputCaptureConfig | None = None

    @field_validator("model_args")
    @classmethod
    def _validate_model_args(cls, value: list[str]) -> list[str]:
        return validate_model_args(value)

    @field_validator("default_model", "requested_model")
    @classmethod
    def _validate_model(cls, value: str | None) -> str | None:
        return validate_cli_model(value)

    def list_roles(self) -> list[str]:
        return list(self.roles.keys())

    def get_role(self, role_name: str | None) -> ResolvedCLIRole:
        key = role_name or "default"
        if key not in self.roles:
            available = ", ".join(sorted(self.roles.keys()))
            raise KeyError(f"Role '{role_name}' not configured for CLI '{self.name}'. Available roles: {available}")
        return self.roles[key]
