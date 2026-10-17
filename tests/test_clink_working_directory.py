"""Directory selection contracts, without storage, subprocesses, or model calls."""

import os
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import Mock

import pytest

from clink.working_directory import (
    MISSING_BINDING,
    BoundWorkingDirectoryUnavailableError,
    InvalidWorkingDirectoryBindingError,
    InvalidWorkingDirectoryError,
    WorkingDirectoryBinding,
    WorkingDirectoryConflictError,
    WorkingDirectorySelection,
    decode_binding,
    resolve_working_directory,
)


@pytest.fixture
def projects(tmp_path):
    root = tmp_path.resolve()
    a, b, alias = root / "project A", root / "project B", root / "alias"
    a.mkdir()
    b.mkdir()
    alias.symlink_to(a, target_is_directory=True)
    return a, b, alias


def binding_record(launch, canonical):
    return {"mode": "directory", "launch_path": str(launch), "canonical_path": str(canonical)}


def test_records_are_immutable_and_serialization_cannot_mutate_them(projects):
    a, _, alias = projects
    binding = WorkingDirectoryBinding("directory", str(alias), str(a))
    selection = WorkingDirectorySelection(binding, "request")
    assert decode_binding(binding.to_record()) == binding
    with pytest.raises(FrozenInstanceError):
        binding.launch_path = str(a)
    with pytest.raises(FrozenInstanceError):
        selection.source = "conversation"
    record = binding.to_record()
    record["launch_path"] = "changed"
    assert selection.to_metadata() == {
        "mode": "directory",
        "launch_path": str(alias),
        "canonical_path": str(a),
        "source": "request",
    }
    assert "source" not in binding.to_record()


@pytest.mark.parametrize(
    "args",
    [
        ("unknown",),
        (None,),
        ("directory",),
        ("directory", "/tmp", None),
        ("directory", "relative", "/tmp"),
        ("directory", "/tmp", "relative"),
        ("directory", "/tmp\x00", "/tmp"),
        ("directory", Path("/tmp"), "/tmp"),
        ("scratch", "/tmp", None),
        ("scratch", None, "/tmp"),
    ],
)
def test_invalid_binding_construction(args):
    with pytest.raises(ValueError):
        WorkingDirectoryBinding(*args)


@pytest.mark.parametrize(
    "mode,source", [("directory", "scratch"), ("scratch", "request"), ("scratch", "configuration"), ("scratch", "bad")]
)
def test_invalid_selection_source(projects, mode, source):
    a, _, _ = projects
    binding = (
        WorkingDirectoryBinding("directory", str(a), str(a))
        if mode == "directory"
        else WorkingDirectoryBinding("scratch")
    )
    with pytest.raises(ValueError, match="source"):
        WorkingDirectorySelection(binding, source)
    with pytest.raises(ValueError, match="binding"):
        WorkingDirectorySelection({}, "request")


def test_only_missing_state_is_unbound():
    assert decode_binding() is None
    assert decode_binding(MISSING_BINDING) is None
    assert decode_binding({"mode": "scratch"}) == WorkingDirectoryBinding("scratch")


@pytest.mark.parametrize(
    "record",
    [
        None,
        False,
        "scratch",
        [],
        {},
        {"mode": "unknown"},
        {"mode": "scratch", "launch_path": None},
        {"mode": "scratch", "canonical_path": "/tmp"},
        {"mode": "scratch", "source": "conversation"},
        {"mode": "directory", "launch_path": "/tmp"},
        {"mode": "directory", "launch_path": "/tmp", "canonical_path": None},
        {"mode": "directory", "launch_path": 42, "canonical_path": "/tmp"},
        {"mode": "directory", "launch_path": "relative", "canonical_path": "/tmp"},
        {"mode": "directory", "launch_path": "/tmp", "canonical_path": "relative"},
        {"mode": "directory", "launch_path": "/tmp\x00", "canonical_path": "/tmp"},
        {"mode": "directory", "launch_path": "/tmp", "canonical_path": "/tmp", "source": "request"},
    ],
)
def test_malformed_binding_never_migrates_or_echoes_invalid_state(record, projects):
    a, _, _ = projects
    with pytest.raises(InvalidWorkingDirectoryBindingError) as error:
        resolve_working_directory(requested=str(a), configured=a, saved_binding=record)
    assert error.value.to_details() == {
        "error_type": "InvalidWorkingDirectoryBindingError",
        "new_conversation_required": True,
        "bound_working_directory": None,
        "requested_working_directory": str(a),
        "normalized_requested_path": None,
        "canonical_requested_path": None,
    }


@pytest.mark.parametrize(
    "saved,requested,configured,expected,source",
    [
        ("missing", "a", "b", "a", "request"),
        ("missing", "a", "missing", "a", "request"),
        ("missing", None, "alias", "alias", "configuration"),
        ("missing", None, None, "scratch", "scratch"),
        ("directory", None, "b", "alias", "conversation"),
        ("directory", "a", "b", "alias", "conversation"),
        ("scratch", None, "b", "scratch", "conversation"),
        ("scratch", "a", "b", "a", "request"),
    ],
)
def test_selection_table(projects, saved, requested, configured, expected, source):
    a, b, alias = projects
    paths = {"a": a, "b": b, "alias": alias, "missing": a / "missing", None: None}
    record = {"missing": MISSING_BINDING, "directory": binding_record(alias, a), "scratch": {"mode": "scratch"}}[saved]
    original = record.copy() if isinstance(record, dict) else record
    result = resolve_working_directory(
        requested=str(paths[requested]) if requested else None,
        configured=paths[configured],
        saved_binding=record,
    )
    assert result.source == source
    assert record == original  # Selection alone never commits or promotes state.
    if expected == "scratch":
        assert result.binding.to_record() == {"mode": "scratch"}
        assert result.to_metadata() == {
            "mode": "scratch",
            "launch_path": None,
            "canonical_path": None,
            "source": source,
        }
    else:
        assert result.binding.to_record() == binding_record(paths[expected], a)


def test_omission_and_null_select_identically(projects):
    a, _, _ = projects
    assert resolve_working_directory(configured=a) == resolve_working_directory(configured=a, requested=None)


def test_conflict_returns_both_identities_and_new_conversation_instruction(projects):
    a, b, alias = projects
    bound = binding_record(alias, a)
    requested = f"{b}/./"
    with pytest.raises(WorkingDirectoryConflictError, match="start a new conversation") as error:
        resolve_working_directory(requested=requested, saved_binding=bound)
    assert error.value.to_details() == {
        "error_type": "WorkingDirectoryConflictError",
        "new_conversation_required": True,
        "bound_working_directory": bound,
        "requested_working_directory": requested,
        "normalized_requested_path": str(b),
        "canonical_requested_path": str(b),
    }


@pytest.mark.parametrize("value", ["", " \t\n", "relative", "/tmp\x00bad", 1, False, [], {}, Path("/tmp")])
@pytest.mark.parametrize("saved", ["missing", "scratch", "directory"])
def test_invalid_explicit_values_do_not_fall_back(projects, value, saved):
    a, _, alias = projects
    record = {"missing": MISSING_BINDING, "scratch": {"mode": "scratch"}, "directory": binding_record(alias, a)}[saved]
    with pytest.raises(InvalidWorkingDirectoryError) as error:
        resolve_working_directory(requested=value, configured=a, saved_binding=record)
    details = error.value.to_details()
    assert details["new_conversation_required"] is False
    assert details["requested_working_directory"] == (value if isinstance(value, str) else None)
    assert details["canonical_requested_path"] is None
    assert details["normalized_requested_path"] == ("relative" if value == "relative" else None)
    assert details["bound_working_directory"] == (record if saved == "directory" else None)


def test_home_expansion_and_spaces(monkeypatch, projects):
    a, _, _ = projects
    expand = Mock(return_value=str(a) + "/./")
    monkeypatch.setattr(os.path, "expanduser", expand)
    result = resolve_working_directory(requested="~/project A/./")
    expand.assert_called_once_with("~/project A/./")
    assert result.binding.to_record() == binding_record(a, a)


def test_real_host_home_expansion():
    home = Path.home()
    result = resolve_working_directory(requested="~")
    assert result.binding.launch_path == str(home)
    assert os.path.samefile(result.binding.canonical_path, home)


def test_meaningful_whitespace_punctuation_and_environment_tokens_are_literal(tmp_path, monkeypatch):
    name = tmp_path / " $PAL_TEST_DIRECTORY ; [project] "
    name.mkdir()
    monkeypatch.setenv("PAL_TEST_DIRECTORY", "unexpected-expansion")
    result = resolve_working_directory(requested=str(name))
    assert result.binding.launch_path == str(name)
    assert os.path.samefile(result.binding.canonical_path, name)


def test_request_normalization_precedes_resolution_but_manifest_preserves_dotdot(tmp_path):
    root = tmp_path.resolve()
    target = root / "target"
    nested = target / "nested"
    nested.mkdir(parents=True)
    alias = root / "alias"
    alias.symlink_to(nested, target_is_directory=True)
    spelling = f"{alias}/.."
    requested = resolve_working_directory(requested=spelling)
    configured = resolve_working_directory(configured=Path(spelling))
    assert requested.binding.to_record() == binding_record(root, root)
    assert configured.binding.to_record() == binding_record(spelling, target)
    continued = resolve_working_directory(requested=str(target), saved_binding=configured.binding.to_record())
    assert continued.binding == configured.binding
    assert continued.source == "conversation"


@pytest.mark.parametrize("kind", ["missing", "file", "broken-link", "loop"])
@pytest.mark.parametrize("source", ["request", "configuration"])
def test_non_directories_and_resolution_failures(tmp_path, kind, source):
    path = tmp_path / kind
    if kind == "file":
        path.write_text("file")
    elif kind == "broken-link":
        path.symlink_to(tmp_path / "absent")
    elif kind == "loop":
        path.symlink_to(path)
    kwargs = {"requested": str(path)} if source == "request" else {"configured": path}
    with pytest.raises(InvalidWorkingDirectoryError) as error:
        resolve_working_directory(**kwargs)
    assert error.value.__cause__ is not None
    details = error.value.to_details()
    assert details["normalized_requested_path"] == (str(path) if source == "request" else None)
    assert details["canonical_requested_path"] == (
        str(path.resolve()) if kind == "file" and source == "request" else None
    )
    if kind == "missing":
        assert not path.exists()


@pytest.mark.parametrize("path", [Path("relative"), Path("/tmp\x00bad")])
def test_invalid_configured_path_is_an_explicit_error(path):
    with pytest.raises(InvalidWorkingDirectoryError, match="configured"):
        resolve_working_directory(configured=path)


@pytest.mark.parametrize("change", ["deleted-launch", "deleted-canonical", "file", "retargeted"])
def test_bound_path_revalidation_never_uses_another_alias(projects, change):
    a, b, alias = projects
    record = binding_record(alias, a)
    if change == "deleted-launch":
        alias.unlink()
    elif change == "deleted-canonical":
        a.rename(a.with_name("moved"))
        alias.unlink()
        alias.symlink_to(a.with_name("moved"), target_is_directory=True)
    elif change == "file":
        a.rmdir()
        a.write_text("no longer a directory")
    else:
        alias.unlink()
        alias.symlink_to(b, target_is_directory=True)
    with pytest.raises(BoundWorkingDirectoryUnavailableError) as error:
        resolve_working_directory(requested=str(b), configured=b, saved_binding=record)
    assert error.value.to_details()["bound_working_directory"] == record
    assert error.value.new_conversation_required is False
    assert error.value.__cause__ is not None


@pytest.mark.parametrize("source", ["request", "configuration", "conversation"])
@pytest.mark.parametrize("operation", ["stat", "access"])
def test_permission_failures_are_explicit(monkeypatch, projects, source, operation):
    a, _, _ = projects
    kwargs = {
        "request": {"requested": str(a)},
        "configuration": {"configured": a},
        "conversation": {"saved_binding": binding_record(a, a)},
    }[source]
    failure = BoundWorkingDirectoryUnavailableError if source == "conversation" else InvalidWorkingDirectoryError
    with monkeypatch.context() as patch:
        if operation == "stat":
            patch.setattr(os, "stat", Mock(side_effect=PermissionError("stat denied")))
        else:
            patch.setattr(os, "access", Mock(return_value=False))
        with pytest.raises(failure) as error:
            resolve_working_directory(**kwargs)
    assert isinstance(error.value.__cause__, PermissionError)


@pytest.mark.parametrize("comparison", ["saved", "request"])
def test_samefile_failure_never_accepts_lexically_identical_paths(monkeypatch, projects, comparison):
    a, _, _ = projects
    cause = PermissionError("comparison denied")
    samefile = Mock(side_effect=[cause] if comparison == "saved" else [True, cause])
    monkeypatch.setattr(os.path, "samefile", samefile)
    failure = BoundWorkingDirectoryUnavailableError if comparison == "saved" else InvalidWorkingDirectoryError
    with pytest.raises(failure) as error:
        resolve_working_directory(requested=str(a), saved_binding=binding_record(a, a))
    assert error.value.__cause__ is cause
    assert samefile.call_count == (1 if comparison == "saved" else 2)


@pytest.mark.parametrize("equivalent", [False, True])
def test_request_comparison_follows_filesystem_result(monkeypatch, projects, equivalent):
    a, b, alias = projects
    samefile = Mock(side_effect=[True, equivalent])
    monkeypatch.setattr(os.path, "samefile", samefile)
    # A false result must reject even equal spellings; a true result must accept
    # different spellings (as a case-insensitive filesystem can report).
    requested = str(b if equivalent else alias)
    if equivalent:
        result = resolve_working_directory(requested=requested, saved_binding=binding_record(alias, a))
        assert result.binding.launch_path == str(alias)
        assert result.source == "conversation"
    else:
        with pytest.raises(WorkingDirectoryConflictError):
            resolve_working_directory(requested=requested, saved_binding=binding_record(alias, a))
    assert samefile.call_args_list[0].args == (str(alias), str(a))
    assert samefile.call_args_list[1].args == (str(alias), requested)


def test_real_case_insensitive_alias_when_supported(tmp_path):
    project = tmp_path / "CaseProject"
    project.mkdir()
    alias = tmp_path / "caseproject"
    if not alias.exists():
        pytest.skip("Host filesystem is case-sensitive; case-insensitive alias coverage unavailable")
    result = resolve_working_directory(requested=str(alias), saved_binding=binding_record(project, project.resolve()))
    assert result.binding.launch_path == str(project)
    assert result.source == "conversation"


def test_distinct_case_sensitive_directories_conflict_when_supported(tmp_path):
    project, other = tmp_path / "CaseProject", tmp_path / "caseproject"
    project.mkdir()
    if other.exists():
        pytest.skip("Host filesystem is case-insensitive; distinct case-only directories unavailable")
    other.mkdir()
    with pytest.raises(WorkingDirectoryConflictError):
        resolve_working_directory(requested=str(other), saved_binding=binding_record(project, project.resolve()))
