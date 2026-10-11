"""Codex-specific CLI agent hooks."""

from __future__ import annotations

from clink.models import ResolvedCLIClient, ResolvedCLIRole
from clink.parsers.base import ParserError

from .base import AgentOutput, BaseCLIAgent


class CodexAgent(BaseCLIAgent):
    """Codex CLI agent with JSONL recovery support."""

    def __init__(self, client: ResolvedCLIClient):
        super().__init__(client)

    def _extra_command_args(self, *, system_prompt: str | None, role: ResolvedCLIRole) -> list[str]:
        # PAL-created scratch directories intentionally have no Git repository.
        # Keep the CLI's existing repository policy for operator-configured cwd.
        if self.client.working_dir is None:
            return ["--skip-git-repo-check"]
        return []

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
