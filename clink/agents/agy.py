"""Antigravity's single-turn structured stdin adapter."""

from __future__ import annotations

import json
from collections.abc import Sequence

from clink.constants import INTERNAL_DEFAULTS
from clink.models import ResolvedCLIRole
from clink.parsers.base import ParserError

from .base import BaseCLIAgent, CLIAgentError


class AgyAgent(BaseCLIAgent):
    """Reuse the shared lifecycle while preserving agy's saved permission policy."""

    # Native 1.3.5 options/aliases that conflict with PAL-owned transport or mode.
    # Compare complete names: --print-timeout is a supported operator choice.
    _RESERVED_OPTIONS = frozenset(
        {
            "input-format",
            "output-format",
            "print",
            "prompt",
            "p",
            "prompt-interactive",
            "i",
            "continue",
            "c",
            "conversation",
            "mode",
            "dangerously-skip-permissions",
        }
    )

    def _encode_stdin(self, prompt: str) -> bytes:
        event = {"event": "user", "message": {"content": prompt}}
        return (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")

    def _build_command(
        self,
        *,
        role: ResolvedCLIRole,
        system_prompt: str | None,
        allow_edits: bool = False,
        editable_paths: Sequence[str] = (),
    ) -> list[str]:
        if editable_paths:
            raise CLIAgentError("agy does not support editable_paths")
        if self.client.output_to_file is not None:
            raise CLIAgentError("agy does not support output_to_file; results must use stdout")
        if self.client.internal_args != INTERNAL_DEFAULTS["agy"].additional_args:
            raise CLIAgentError("agy requires the built-in stream-json input/output arguments")

        configured_args = (
            self.client.executable[1:]
            + self.client.config_args
            + self.client.safe_args
            + self.client.edit_args
            + role.role_args
        )
        for argument in configured_args:
            option = argument.partition("=")[0]
            if option == "--" or (option.startswith("-") and option.lstrip("-") in self._RESERVED_OPTIONS):
                # Do not echo configured prompt/flag values into command diagnostics.
                raise CLIAgentError(
                    "agy configuration contains a reserved protocol, prompt, continuation, or mode option"
                )

        command = super()._build_command(
            role=role,
            system_prompt=system_prompt,
            allow_edits=allow_edits,
            editable_paths=editable_paths,
        )
        if allow_edits:
            command.extend(["--mode", "accept-edits"])
        return command

    def _recover_from_error(
        self,
        *,
        returncode: int,
        stdout: str,
        stderr: str,
        sanitized_command: list[str],
        duration_seconds: float,
        output_file_content: str | None,
    ) -> None:
        # Extract the parser's status/error description without exposing the stream.
        # Even a valid SUCCESS payload must never rescue a nonzero process exit.
        try:
            self._parser.parse(stdout, stderr)
        except ParserError as exc:
            raise CLIAgentError(
                f"CLI '{self.client.name}' exited with status {returncode}: {exc}",
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
            ) from exc
