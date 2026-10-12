"""Private audit records follow successful memory commits without owning them."""

import json
import os
import stat
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

import server
from utils import conversation_memory as memory
from utils import transcripts


@pytest.fixture
def audit(tmp_path, monkeypatch):
    monkeypatch.setenv("PAL_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PAL_TRANSCRIPTS", "true")
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    return tmp_path / "transcripts"


def test_disabled_has_no_filesystem_effect(tmp_path, monkeypatch):
    monkeypatch.setenv("PAL_STATE_DIR", str(tmp_path / "absent"))
    monkeypatch.delenv("PAL_TRANSCRIPTS", raising=False)
    thread = memory.create_thread("chat", {})
    assert memory.add_turn(thread, "user", "private prompt")
    assert transcripts.prune_transcripts() == 0
    assert not (tmp_path / "absent").exists()


def test_windows_fails_closed_without_affecting_memory(audit, monkeypatch, caplog):
    monkeypatch.setattr(transcripts.sys, "platform", "win32")
    thread = memory.create_thread("chat", {})
    assert memory.add_turn(thread, "assistant", "answer")
    assert memory.get_thread(thread).turns[0].content == "answer"
    assert not audit.exists()
    assert "Transcript append failed" in caplog.text
    transcripts.prune_transcripts()
    assert "Windows ACL support is unavailable" in caplog.text


@pytest.mark.skipif(sys.platform == "win32", reason="Private transcripts fail closed pending Windows ACL validation")
class TestPosixTranscripts:
    @pytest.mark.parametrize("source", ["xdg", "home"])
    def test_implicit_state_root_symlink_cannot_write_or_prune(self, audit, tmp_path, monkeypatch, caplog, source):
        monkeypatch.delenv("PAL_STATE_DIR")
        if source == "xdg":
            monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
            root = tmp_path / "xdg" / "pal"
        else:
            monkeypatch.setattr(Path, "home", lambda: tmp_path)
            root = tmp_path / ".local/state/pal"
        target = tmp_path / "outside"
        target.mkdir()
        root.parent.mkdir(parents=True)
        root.symlink_to(target, target_is_directory=True)
        transcripts.record_event(str(uuid4()), {"event": "thread"})
        assert not list(target.iterdir())
        assert transcripts.prune_transcripts(now=10**12) == 0
        assert "Transcript append failed" in caplog.text
        assert "Transcript retention unavailable" in caplog.text

    @pytest.mark.asyncio
    async def test_dispatch_records_raw_reply_and_survives_audit_failure(self, audit, monkeypatch, tmp_path, caplog):
        from tests.request_capture import capture_providers

        capture_providers(monkeypatch)
        monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(tmp_path))
        args = {"prompt": "first input", "model": "gemini-2.5-flash", "working_directory_absolute_path": str(tmp_path)}
        first = json.loads((await server.handle_call_tool("chat", args))[0].text)
        thread = first["continuation_offer"]["continuation_id"]
        records = [json.loads(line) for line in (audit / f"{thread}.jsonl").read_text().splitlines()]
        assert [r["content"] for r in records[1:]] == ["first input", "Gemini answer"]

        def fail(*args, **kwargs):
            raise OSError("simulated audit failure")

        monkeypatch.setattr(transcripts, "append_event", fail)
        second = json.loads((await server.handle_call_tool("chat", {**args, "continuation_id": thread}))[0].text)
        assert "Gemini answer" in second["content"]
        assert len(memory.get_thread(thread).turns) == 4
        assert "Transcript append failed" in caplog.text

    def test_records_order_raw_content_unicode_and_provenance(self, audit):
        thread = memory.create_thread("chat", {"prompt": "héllo"})
        memory.add_turn(thread, "user", "héllo 👋", tool_name="chat")
        memory.add_turn(
            thread,
            "assistant",
            "raw reply\nπ",
            tool_name="chat",
            model_name="test-model",
            model_provider="test",
            model_metadata={"usage": {"input_tokens": None, "output_tokens": 0}},
        )
        records = [json.loads(line) for line in (audit / f"{thread}.jsonl").read_text().splitlines()]
        assert [r["event"] for r in records] == ["thread", "turn", "turn"]
        assert [r["turn"] for r in records[1:]] == [1, 2]
        assert [r["content"] for r in records[1:]] == ["héllo 👋", "raw reply\nπ"]
        assert all(r["schema_version"] == 1 and r["thread_id"] == thread for r in records)
        assert records[1]["model_name"] is None
        assert records[2]["model_metadata"]["usage"] == {"input_tokens": None, "output_tokens": 0}
        assert stat.S_IMODE(audit.stat().st_mode) == 0o700
        assert stat.S_IMODE((audit / f"{thread}.jsonl").stat().st_mode) == 0o600

    def test_memory_failure_does_not_record(self, audit, monkeypatch):
        thread = memory.create_thread("chat", {})
        before = (audit / f"{thread}.jsonl").read_bytes()
        monkeypatch.setattr(memory.get_storage(), "setex", lambda *args: False)
        with pytest.raises(memory.ConversationStorageError):
            memory.add_turn(thread, "user", "not saved")
        assert (audit / f"{thread}.jsonl").read_bytes() == before
        with pytest.raises(memory.ConversationStorageError):
            memory.create_thread("chat", {})
        assert len(list(audit.iterdir())) == 1

    def test_private_modes_tightened(self, audit):
        thread = memory.create_thread("chat", {})
        path = audit / f"{thread}.jsonl"
        audit.chmod(0o777)
        path.chmod(0o666)
        memory.add_turn(thread, "user", "secret")
        assert stat.S_IMODE(audit.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    @pytest.mark.parametrize(
        "value",
        [
            "../escape",
            "",
            "not-a-uuid",
            "{00000000-0000-0000-0000-000000000000}",
            "ABCDEFAB-1234-1234-1234-ABCDEFABCDEF",
            "0" * 32,
        ],
    )
    def test_noncanonical_ids_rejected_before_creation(self, audit, value):
        with pytest.raises(transcripts.TranscriptError):
            transcripts.append_event(value, {})
        assert not audit.exists()

    @pytest.mark.parametrize("boundary", ["state", "directory", "file", "hardlink", "fifo"])
    def test_unsafe_paths_preserve_targets_and_answers(self, audit, tmp_path, monkeypatch, caplog, boundary):
        thread = str(uuid4())
        target = tmp_path / "target"
        target.mkdir()
        secret = target / "secret"
        secret.write_text("unchanged")
        if boundary == "state":
            linked = tmp_path / "linked"
            linked.symlink_to(target, target_is_directory=True)
            monkeypatch.setenv("PAL_STATE_DIR", str(linked))
        elif boundary == "directory":
            audit.symlink_to(target, target_is_directory=True)
        else:
            audit.mkdir()
            path = audit / f"{thread}.jsonl"
            if boundary == "file":
                path.symlink_to(secret)
            elif boundary == "hardlink":
                os.link(secret, path)
            else:
                os.mkfifo(path)
        transcripts.record_event(thread, {"event": "turn", "content": "private"})
        assert "Transcript append failed" in caplog.text
        assert secret.read_text() == "unchanged"
        assert not (target / f"{thread}.jsonl").exists()

    def test_append_failure_preserves_saved_answer_and_prior_records(self, audit, monkeypatch, caplog):
        thread = memory.create_thread("chat", {})
        path = audit / f"{thread}.jsonl"
        before = path.read_bytes()
        real_write = os.write

        def partial_then_fail(fd, data):
            real_write(fd, data[:7])
            raise OSError("simulated full disk")

        monkeypatch.setattr(transcripts.os, "write", partial_then_fail)
        assert memory.add_turn(thread, "assistant", "successful answer")
        assert memory.get_thread(thread).turns[-1].content == "successful answer"
        assert path.read_bytes() == before
        assert "Transcript append failed" in caplog.text

    def test_short_writes_and_interrupted_line(self, audit, monkeypatch):
        thread = memory.create_thread("chat", {})
        path = audit / f"{thread}.jsonl"
        with path.open("ab") as stream:
            stream.write(b'{"interrupted":')
        real_write = os.write
        monkeypatch.setattr(transcripts.os, "write", lambda fd, data: real_write(fd, data[:3]))
        memory.add_turn(thread, "assistant", "answer after interruption")
        lines = path.read_bytes().splitlines()
        assert lines[1] == b'{"interrupted":'
        assert json.loads(lines[2])["content"] == "answer after interruption"

    def test_concurrent_appends_do_not_interleave(self, audit):
        thread = memory.create_thread("chat", {})
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(
                executor.map(
                    lambda n: transcripts.append_event(thread, {"event": "turn", "turn": n, "content": str(n) * 1000}),
                    range(40),
                )
            )
        records = [json.loads(line) for line in (audit / f"{thread}.jsonl").read_text().splitlines()]
        assert len(records) == 41
        assert {r["turn"] for r in records[1:]} == set(range(40))

    def test_retention_only_expired_canonical_regular_files(self, audit, tmp_path):
        expired = memory.create_thread("chat", {})
        recent = memory.create_thread("chat", {})
        path = audit / f"{expired}.jsonl"
        now = 40 * 86400
        os.utime(path, (0, 0))
        unknown = audit / "keep.jsonl"
        unknown.write_text("unrelated")
        os.utime(unknown, (0, 0))
        outside = tmp_path / "outside"
        outside.write_text("unrelated")
        os.utime(outside, (0, 0))
        (audit / f"{uuid4()}.jsonl").symlink_to(outside)
        (audit / f"{uuid4()}.jsonl").mkdir()
        os.link(outside, audit / f"{uuid4()}.jsonl")
        assert transcripts.prune_transcripts(now=now) == 1
        assert not path.exists()
        assert (audit / f"{recent}.jsonl").exists()
        assert unknown.exists() and outside.exists()

    def test_retention_errors_warn(self, audit, monkeypatch, caplog):
        thread = memory.create_thread("chat", {})
        os.utime(audit / f"{thread}.jsonl", (0, 0))

        def fail(*args, **kwargs):
            raise PermissionError("denied")

        monkeypatch.setattr(transcripts.os, "unlink", fail)
        assert transcripts.prune_transcripts() == 0
        assert "Transcript retention failed" in caplog.text

    def test_disabled_preserves_retained_files(self, audit, monkeypatch):
        thread = memory.create_thread("chat", {})
        path = audit / f"{thread}.jsonl"
        before = path.read_bytes()
        monkeypatch.setenv("PAL_TRANSCRIPTS", "false")
        memory.add_turn(thread, "user", "not recorded")
        assert transcripts.prune_transcripts(now=10**12) == 0
        assert path.read_bytes() == before
