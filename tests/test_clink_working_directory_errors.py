"""Directory diagnostics preserve selection identity and never mutate failed threads."""

import errno
import json
from unittest.mock import Mock

import pytest

from clink.agents import CLIAgentError
from tests import test_clink_working_directory_requests as requests
from tests.test_clink_working_directory_requests import output, record, seed
from tools.clink import MAX_RESPONSE_CHARS, CLinkTool
from tools.shared.exceptions import ToolExecutionError
from utils import conversation_coordination as coordination
from utils import conversation_memory as memory

# Reuse the request suite's isolated fixtures without changing their scope.
projects = requests.projects
runtime = requests.runtime


def validation_metadata(error_type, *, bound=None, requested=None, normalized=None, canonical=None, new=False):
    return {
        "tool_name": "clink",
        "error_type": error_type,
        "stage": "validation",
        "execution_started": False,
        "retryable": False,
        "bound_working_directory": bound,
        "requested_working_directory": requested,
        "normalized_requested_path": normalized,
        "canonical_requested_path": canonical,
        "new_conversation_required": new,
    }


async def rejected(runtime, arguments):
    snapshot = runtime.storage._store.copy()
    with pytest.raises(ToolExecutionError) as error:
        await CLinkTool().execute(arguments)
    result = json.loads(error.value.payload)
    assert result["status"] == "error"
    assert result["continuation_offer"] is None
    assert "working_directory" not in result["metadata"]
    assert runtime.storage._store == snapshot  # Serialized history, defaults, binding and TTL.
    runtime.factory.assert_not_called()
    assert not coordination._entries
    return result


@pytest.mark.parametrize("state", ["new", "scratch", "directory"])
@pytest.mark.parametrize("kind", ["empty", "blank", "nul", "relative", "missing", "file", "denied"])
async def test_invalid_request_exact_envelope(runtime, projects, monkeypatch, kind, state):
    a, b, _, alias = projects
    file = b / "file"
    file.write_text("not a directory")
    requested = {
        "empty": "",
        "blank": "  ",
        "nul": "bad\x00path",
        "relative": "relative/./project",
        "missing": str(b / "missing"),
        "file": str(file),
        "denied": str(b),
    }[kind]
    normalized = None if kind in ("empty", "blank", "nul") else "relative/project" if kind == "relative" else requested
    canonical = str(file.resolve()) if kind == "file" else str(b.resolve()) if kind == "denied" else None
    if kind == "denied":
        monkeypatch.setattr("clink.working_directory.os.access", Mock(return_value=False))
    arguments = {"cli_name": "gemini", "prompt": "test", "working_directory_absolute_path": requested}
    if state != "new":
        arguments["continuation_id"] = seed(
            {"clink_working_directory": record(alias if state == "directory" else None)}
        )
    result = await rejected(runtime, arguments)
    assert result["metadata"] == validation_metadata(
        "InvalidWorkingDirectoryError",
        bound=record(alias) if state == "directory" else None,
        requested=requested,
        normalized=normalized,
        canonical=canonical,
    )


async def test_missing_configuration_has_no_invented_request_paths(runtime, projects):
    runtime.registry.get_client("gemini").working_dir = projects[0] / "missing"
    result = await rejected(runtime, {"cli_name": "gemini", "prompt": "test"})
    assert result["metadata"] == validation_metadata("InvalidWorkingDirectoryError")


async def test_conflict_instructs_new_conversation_with_both_identities(runtime, projects):
    _, b, _, alias = projects
    thread_id = seed({"clink_working_directory": record(alias)})
    result = await rejected(
        runtime,
        {
            "cli_name": "gemini",
            "prompt": "test",
            "continuation_id": thread_id,
            "working_directory_absolute_path": f"{b}/./",
        },
    )
    assert "start a new conversation" in result["content"]
    assert result["metadata"] == validation_metadata(
        "WorkingDirectoryConflictError",
        bound=record(alias),
        requested=f"{b}/./",
        normalized=str(b),
        canonical=str(b.resolve()),
        new=True,
    )


@pytest.mark.parametrize("kind", ["missing-launch", "missing-canonical", "retargeted", "file", "denied", "stat-error"])
@pytest.mark.parametrize("explicit", [False, True])
async def test_unavailable_binding_retains_saved_paths(runtime, projects, monkeypatch, kind, explicit):
    a, b, _, alias = projects
    binding = record(alias)
    thread_id = seed({"clink_working_directory": binding})
    if kind == "missing-launch":
        alias.unlink()
    elif kind == "missing-canonical":
        a.rmdir()
    elif kind == "retargeted":
        alias.unlink()
        alias.symlink_to(b, target_is_directory=True)
    elif kind == "file":
        a.rmdir()
        a.write_text("not a directory")
    elif kind == "denied":
        monkeypatch.setattr("clink.working_directory.os.access", lambda path, *args, **kwargs: str(path) != str(alias))
    else:
        monkeypatch.setattr(
            "clink.working_directory.os.path.samefile", Mock(side_effect=OSError(errno.EIO, "stat failed"))
        )
    requested = str(b) if explicit else None
    result = await rejected(
        runtime,
        {
            "cli_name": "gemini",
            "prompt": "test",
            "continuation_id": thread_id,
            "working_directory_absolute_path": requested,
        },
    )
    assert result["metadata"] == validation_metadata(
        "BoundWorkingDirectoryUnavailableError",
        bound=binding,
        requested=requested,
        normalized=requested,
        canonical=str(b.resolve()) if explicit else None,
    )


@pytest.mark.parametrize(
    "binding", [None, {}, {"mode": "directory", "launch_path": "/missing"}, {"mode": "scratch", "extra": 1}]
)
async def test_corrupt_binding_is_not_echoed_or_migrated(runtime, projects, binding):
    thread_id = seed({"clink_working_directory": binding})
    requested = str(projects[0])
    result = await rejected(
        runtime,
        {
            "cli_name": "gemini",
            "prompt": "test",
            "continuation_id": thread_id,
            "working_directory_absolute_path": requested,
        },
    )
    assert result["metadata"] == validation_metadata(
        "InvalidWorkingDirectoryBindingError", requested=requested, new=True
    )


@pytest.mark.parametrize("source", ["request", "configuration", "scratch", "conversation", "scratch-conversation"])
@pytest.mark.parametrize(
    "presentation", ["normal", "summary", "truncated", "error", "nonzero", "exception", "commit-failure"]
)
async def test_pal_selection_survives_native_metadata_presentation_and_errors(runtime, projects, source, presentation):
    a, _, _, alias = projects
    scratch = source in ("scratch", "scratch-conversation")
    arguments = {"cli_name": "gemini", "prompt": "test"}
    if source == "request":
        arguments["working_directory_absolute_path"] = str(alias)
    elif source == "configuration":
        runtime.registry.get_client("gemini").working_dir = alias
    elif source in ("conversation", "scratch-conversation"):
        arguments["continuation_id"] = seed({"clink_working_directory": record(None if scratch else alias)})
        # Equivalent explicit requests still report the conversation's launch spelling.
        if not scratch:
            arguments["working_directory_absolute_path"] = str(a)
    result = output()
    result.parsed.metadata.update(working_directory={"mode": "spoofed", "launch_path": "fake"}, tool_name="spoofed")
    if presentation in ("summary", "truncated"):
        result.parsed.content = "x" * (MAX_RESPONSE_CHARS + 1)
        if presentation == "summary":
            result.parsed.content += "<SUMMARY>Short answer</SUMMARY>"
    elif presentation == "error":
        result.parsed.is_error = True
    elif presentation == "nonzero":
        result.returncode = 2
    elif presentation == "exception":
        runtime.run.side_effect = CLIAgentError("execution failed", stderr="native diagnostic")
    elif presentation == "commit-failure":
        runtime.storage.compare_and_setex.side_effect = memory.ConversationMemoryError("cannot save")
    runtime.run.return_value = result
    snapshot = runtime.storage._store.copy()
    if presentation in ("error", "nonzero", "exception"):
        with pytest.raises(ToolExecutionError) as error:
            await CLinkTool().execute(arguments)
        response = json.loads(error.value.payload)
        assert response["continuation_offer"] is None
        assert runtime.storage._store == snapshot
    else:
        response = json.loads((await CLinkTool().execute(arguments))[0].text)
    if presentation == "commit-failure":
        assert response["metadata"]["history_saved"] is False
        assert response["continuation_offer"] is None
        assert runtime.storage._store == snapshot
    if presentation == "summary":
        assert response["content"] == "Short answer"
        assert response["metadata"]["output_summarized"] is True
    if presentation == "truncated":
        assert response["metadata"]["output_truncated"] is True
    assert response["metadata"]["tool_name"] == "clink"
    assert response["metadata"]["working_directory"] == {
        "mode": "scratch" if scratch else "directory",
        "launch_path": None if scratch else str(alias),
        "canonical_path": None if scratch else str(a.resolve()),
        "source": "conversation" if source == "scratch-conversation" else source,
    }
