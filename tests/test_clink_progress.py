"""CLI progress joins its heartbeat after child reaping, including cancellation."""

import asyncio
import json
from pathlib import Path

import pytest

from clink.agents.base import BaseCLIAgent, CLIAgentError
from tests.test_clink_environment import ControlledProcess, client
from tests.test_progress import Session, assert_no_heartbeat, context
from utils import progress


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
async def test_cli_heartbeat_and_process_cleanup(monkeypatch, outcome):
    monkeypatch.setattr(progress, "HEARTBEAT_SECONDS", 0.001)
    session = Session()
    spawned = asyncio.Event()
    processes = []
    cfg = client()
    records = []

    async def spawn(*args, **kwargs):
        process = ControlledProcess(kwargs["cwd"], outcome)
        processes.append(process)
        if outcome != "cancel":
            process.release_reap.set()
        spawned.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

    async def run():
        async with progress.request_progress(context(session, 0)) as reporter:
            records.append(reporter)
            return await BaseCLIAgent(cfg).run(role=cfg.get_role(None), prompt="SECRET", files=[], images=[])

    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(spawned.wait(), 2)
        process = processes[0]
        if outcome == "cancel":
            await asyncio.wait_for(process.started.wait(), 2)
            while True:
                item = await asyncio.wait_for(session.updated.get(), 2)
                if item["progress"] >= 3:
                    break
            task.cancel()
            await asyncio.wait_for(process.reaping.wait(), 2)
            task.cancel()
            process.release_reap.set()
        if outcome == "success":
            assert (await asyncio.wait_for(task, 2)).parsed.content == "ok"
        else:
            with pytest.raises(asyncio.CancelledError if outcome == "cancel" else CLIAgentError):
                await asyncio.wait_for(task, 2)
    finally:
        for process in processes:
            process.release_reap.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert not Path(process.cwd).exists()
    if outcome == "cancel":
        assert process.reaped and process.killed
    assert records[0].metadata()["operations"][0]["stage"] == "cli"
    assert records[0].metadata()["operations"][0]["usage"] is None
    assert "SECRET" not in str(session.notifications)
    assert_no_heartbeat()


async def test_cli_available_usage_reaches_safe_progress(monkeypatch):
    cfg = client(name="claude", parser="claude_json")
    session = Session()

    async def spawn(*args, **kwargs):
        process = ControlledProcess(kwargs["cwd"])

        async def communicate(data):
            process.returncode = 0
            return (
                json.dumps(
                    {
                        "type": "result",
                        "result": "answer",
                        "usage": {"input_tokens": 0, "output_tokens": 4, "secret": "SECRET"},
                    }
                ).encode(),
                b"",
            )

        process.communicate = communicate
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    async with progress.request_progress(context(session, 0)) as reporter:
        result = await BaseCLIAgent(cfg).run(role=cfg.get_role(None), prompt="SECRET", files=[], images=[])
    assert result.parsed.content == "answer"
    assert reporter.metadata()["operations"][0]["usage"] == {"input_tokens": 0, "output_tokens": 4}
    assert json.loads(session.notifications[-2]["message"])["usage"] == {"input_tokens": 0, "output_tokens": 4}
    assert "SECRET" not in str(session.notifications)
