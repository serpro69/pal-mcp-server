import asyncio
import shutil
from pathlib import Path

import pytest

from clink.agents.base import CLIAgentError
from clink.agents.gemini import GeminiAgent
from clink.models import ResolvedCLIClient, ResolvedCLIRole


class DummyProcess:
    def __init__(self, *, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self, _input):
        return self._stdout, self._stderr


@pytest.fixture()
def gemini_agent():
    prompt_path = Path("systemprompts/clink/gemini_default.txt").resolve()
    role = ResolvedCLIRole(name="default", prompt_path=prompt_path, role_args=[])
    client = ResolvedCLIClient(
        name="gemini",
        executable=["gemini"],
        internal_args=[],
        config_args=[],
        env={},
        timeout_seconds=30,
        parser="gemini_json",
        roles={"default": role},
        output_to_file=None,
        working_dir=None,
    )
    return GeminiAgent(client), role


async def _run_agent_with_process(monkeypatch, agent, role, process):
    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return process

    def fake_which(executable_name, **_kwargs):
        return f"/usr/bin/{executable_name}"

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(shutil, "which", fake_which)
    return await agent.run(role=role, prompt="do something", files=[], images=[])


@pytest.mark.asyncio
async def test_gemini_agent_recovers_tool_error(monkeypatch, gemini_agent):
    agent, role = gemini_agent
    error_json = """{
  "error": {
    "type": "FatalToolExecutionError",
    "message": "Error executing tool replace: Failed to edit",
    "code": "edit_expected_occurrence_mismatch"
  }
}"""
    stderr = ("Error: Failed to edit, expected 1 occurrence but found 2.\n" + error_json).encode()
    process = DummyProcess(stderr=stderr, returncode=54)

    result = await _run_agent_with_process(monkeypatch, agent, role, process)

    assert result.returncode == 54
    assert result.parsed.metadata["cli_error_recovered"] is True
    assert result.parsed.metadata["cli_error_code"] == "edit_expected_occurrence_mismatch"
    assert "Gemini CLI reported a tool failure" in result.parsed.content


@pytest.mark.asyncio
async def test_gemini_agent_propagates_unrecoverable_error(monkeypatch, gemini_agent):
    agent, role = gemini_agent
    stderr = b"Plain failure without structured payload"
    process = DummyProcess(stderr=stderr, returncode=54)

    with pytest.raises(CLIAgentError):
        await _run_agent_with_process(monkeypatch, agent, role, process)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stdout,stderr,returncode",
    [
        (b"", b"Error: UNSUPPORTED_CLIENT", 41),
        (b"Error: UNSUPPORTED_CLIENT", b"", 41),
        (b'{"error":{"code":"UNSUPPORTED_CLIENT"}}', b"", 41),
        (b'{"response":""}', b"UNSUPPORTED_CLIENT", 0),
        (b"", b"UNSUPPORTED_CLIENT", 0),
        (b"UNSUPPORTED_CLIENT", b"", 0),
        (b'{"error":{"code":"UNSUPPORTED_CLIENT"}}', b"", 0),
    ],
)
async def test_gemini_agent_reports_unsupported_client(monkeypatch, gemini_agent, stdout, stderr, returncode):
    agent, role = gemini_agent
    process = DummyProcess(stdout=stdout, stderr=stderr, returncode=returncode)
    result = await _run_agent_with_process(monkeypatch, agent, role, process)
    assert result.returncode == returncode
    assert result.parsed.metadata["cli_error_code"] == "UNSUPPORTED_CLIENT"
    assert "https://geminicli.com/" in result.parsed.content
    assert result.stdout == stdout.decode()
    assert result.stderr == stderr.decode()


@pytest.mark.asyncio
async def test_gemini_agent_preserves_successful_marker_quote(monkeypatch, gemini_agent):
    agent, role = gemini_agent
    process = DummyProcess(
        stdout=b'{"response":"Explanation of UNSUPPORTED_CLIENT"}',
        stderr=b"Previous attempt: UNSUPPORTED_CLIENT",
    )
    result = await _run_agent_with_process(monkeypatch, agent, role, process)
    assert result.parsed.content == "Explanation of UNSUPPORTED_CLIENT"
    assert "unsupported_client" not in result.parsed.metadata


@pytest.mark.asyncio
async def test_partial_answer_marker_does_not_mask_unrelated_failure(monkeypatch, gemini_agent):
    agent, role = gemini_agent
    process = DummyProcess(
        stdout=(
            b'{"response":"Explain UNSUPPORTED_CLIENT",'
            b'"error":{"code":"edit_expected_occurrence_mismatch","message":"Failed to edit"}}'
        ),
        returncode=54,
    )
    result = await _run_agent_with_process(monkeypatch, agent, role, process)
    assert result.parsed.metadata["cli_error_code"] == "edit_expected_occurrence_mismatch"
    assert "Failed to edit" in result.parsed.content
    assert "unsupported_client" not in result.parsed.metadata
