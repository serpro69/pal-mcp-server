"""Single-turn prompt delivery and bounded output for declarative clink clients."""

from __future__ import annotations

import asyncio
import errno
import os
from collections.abc import Sequence

from clink.models import (
    FlagPrompt,
    JSONOutput,
    ResolvedCLIClient,
    ResolvedCLIRole,
    is_write_enabling_option,
    validate_generic_safe_args,
)
from clink.parsers.json_path import JSONPathParser
from clink.parsers.text import TextParser

from .base import BaseCLIAgent, CLIAgentError, PreparedLaunch, is_windows_batch_launcher

_CHUNK_SIZE = 64 * 1024


async def _read_limited(stream: asyncio.StreamReader, name: str, limit: int) -> bytes:
    data = bytearray()
    while chunk := await stream.read(min(_CHUNK_SIZE, limit - len(data) + 1)):
        if len(data) + len(chunk) > limit:
            raise CLIAgentError(f"{name} exceeded the {limit}-byte limit", stage="output_limit")
        data.extend(chunk)
    return bytes(data)


async def _feed_stdin(writer: asyncio.StreamWriter, data: bytes) -> None:
    try:
        for start in range(0, len(data), _CHUNK_SIZE):
            writer.write(data[start : start + _CHUNK_SIZE])
            await writer.drain()
    except (BrokenPipeError, ConnectionResetError):
        # A CLI may exit early. Its exit code and output determine the outcome.
        pass
    finally:
        writer.close()


class GenericAgent(BaseCLIAgent):
    bounded_output = True

    def __init__(self, client: ResolvedCLIClient) -> None:
        if client.custom is None:
            raise CLIAgentError("Generic clients require a custom protocol")
        self.protocol = client.custom
        output = self.protocol.output
        super().__init__(client, parser=JSONPathParser(output.path) if isinstance(output, JSONOutput) else TextParser())

    def _build_command(
        self,
        *,
        role: ResolvedCLIRole,
        system_prompt: str | None,
        allow_edits: bool = False,
        editable_paths: Sequence[str] = (),
    ) -> list[str]:
        if self.protocol.workspace == "project" and self.client.working_dir is None:
            raise CLIAgentError("This CLI requires a project directory")
        if editable_paths:
            raise CLIAgentError("Generic clients do not support editable_paths")
        if allow_edits and not self.client.edit_args:
            raise CLIAgentError("This client is read-only (edit_args is empty)")
        command = super()._build_command(
            role=role, system_prompt=system_prompt, allow_edits=allow_edits, editable_paths=editable_paths
        )
        if not allow_edits:
            try:
                validate_generic_safe_args(command[1:])
            except ValueError as exc:
                raise CLIAgentError(str(exc)) from None
        return command

    def _prepare_launch(self, command: list[str], prompt: str) -> PreparedLaunch:
        display_command = [os.path.basename(command[0])]
        transport = self.protocol.prompt
        try:
            if not isinstance(transport, FlagPrompt):
                return PreparedLaunch(argv=command, display_argv=display_command, stdin=self._encode_stdin(prompt))
            prompt.encode("utf-8")
        except UnicodeEncodeError:
            raise CLIAgentError("The prepared prompt is not valid UTF-8") from None
        if "\x00" in prompt:
            raise CLIAgentError(
                "The prepared flag prompt contains U+0000. Remove it from the request or prompt file. "
                "If it is in saved history, start a new conversation with a summary."
            )
        if is_windows_batch_launcher(command[0]):
            raise CLIAgentError(
                "Flag prompts cannot use a Windows batch launcher (.bat/.cmd). Configure a native executable."
            )
        if os.name == "posix":
            try:
                os.fsencode(prompt)
            except UnicodeEncodeError:
                raise CLIAgentError(
                    "The host filesystem encoding cannot represent this flag prompt. Use a UTF-8 locale or stdin transport."
                ) from None
        if is_write_enabling_option(transport.flag, prompt):
            raise CLIAgentError(
                "The prompt flag and value select a write-enabling option. Configure the CLI's prompt-input flag."
            )
        return PreparedLaunch(argv=[*command, transport.flag, prompt], display_argv=display_command, stdin=b"")

    def _launch_error(self, exc: OSError) -> CLIAgentError:
        if not isinstance(self.protocol.prompt, FlagPrompt):
            return super()._launch_error(exc)
        os_error = getattr(exc, "winerror", None)
        if exc.errno == errno.E2BIG or os_error == 206:
            return CLIAgentError(
                "The OS rejected the length of the assembled command. Shorten the current request or start a new "
                "conversation with a summary. For future sessions, lower history_token_budget and restart PAL. "
                "Restarting discards existing continuation IDs.",
                stage="launch",
            )
        # A launcher error can include its arguments. Only expose the OS error category and code.
        reason = (
            "Executable or interpreter not found"
            if isinstance(exc, FileNotFoundError)
            else "Could not launch executable"
        )
        code = os_error if os_error is not None else exc.errno
        category = type(exc).__name__ if code is None else f"OS error {code}"
        return CLIAgentError(f"{reason} ({category})", stage="launch")

    async def _communicate(self, process: asyncio.subprocess.Process, data: bytes, limit: int) -> tuple[bytes, bytes]:
        # All three pipes were requested at spawn. Drain both while feeding stdin
        # so a child that writes its response first can still make progress.
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        stdout = asyncio.create_task(_read_limited(process.stdout, "stdout", limit))
        stderr = asyncio.create_task(_read_limited(process.stderr, "stderr", limit))
        writer = asyncio.create_task(_feed_stdin(process.stdin, data))
        tasks = (stdout, stderr, writer)
        try:
            await asyncio.gather(*tasks)
            await process.wait()
            return stdout.result(), stderr.result()
        except CLIAgentError as exc:
            exc.returncode = process.returncode
            raise
        except OSError:
            raise CLIAgentError(
                "Communication with the CLI failed", stage="io", returncode=process.returncode
            ) from None
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def _decode_output(self, stdout: bytes, stderr: bytes, returncode: int) -> tuple[str, str]:
        if returncode != 0:
            raise CLIAgentError(f"CLI exited with status {returncode}", stage="exit", returncode=returncode)
        try:
            return stdout.decode("utf-8"), ""
        except UnicodeDecodeError:
            reason = "stdout is not valid UTF-8"
            if isinstance(self._parser, JSONPathParser):
                reason = f"{self._parser.location}: {reason}"
            raise CLIAgentError(reason, stage="parse", returncode=returncode) from None
