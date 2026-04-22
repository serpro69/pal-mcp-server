"""Claude-specific CLI agent hooks."""

from __future__ import annotations

from collections.abc import Sequence

from clink.models import ResolvedCLIRole
from clink.parsers.base import ParserError

from .base import AgentOutput, BaseCLIAgent


class ClaudeAgent(BaseCLIAgent):
    """Claude CLI agent with system-prompt injection and scoped-edit allow-listing."""

    supports_path_restrictions = True

    def _extra_command_args(
        self, *, system_prompt: str | None, role: ResolvedCLIRole
    ) -> list[str]:
        if not system_prompt:
            return []
        if any(
            "--append-system-prompt" in bucket
            for bucket in (
                self.client.config_args,
                self.client.safe_args,
                self.client.edit_args,
                role.role_args,
            )
        ):
            return []
        return ["--append-system-prompt", system_prompt]

    def _build_path_restriction_args(
        self,
        editable_paths: Sequence[str],
        *,
        allow_edits: bool,
    ) -> list[str]:
        if not allow_edits or not editable_paths:
            return []
        args: list[str] = []
        for path in editable_paths:
            args.extend(["--allowedTools", f"Edit({path})"])
            args.extend(["--allowedTools", f"Write({path})"])
        return args

    def _recover_from_error(
        self,
        *,
        returncode: int,
        stdout: str,
        stderr: str,
        sanitized_command: list[str],
        duration_seconds: float,
        output_file_content: str | None,
    ) -> AgentOutput | None:
        try:
            parsed = self._parser.parse(stdout, stderr)
        except ParserError:
            return None

        return AgentOutput(
            parsed=parsed,
            sanitized_command=sanitized_command,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration_seconds,
            parser_name=self._parser.name,
            output_file_content=output_file_content,
        )
