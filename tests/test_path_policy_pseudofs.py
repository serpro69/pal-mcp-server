"""File-reader byte bounds and pseudofilesystem policy; only synthetic data is read."""

import io
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from utils.file_utils import estimate_file_tokens, read_file_content, read_file_safely, resolve_and_validate_path
from utils.security_config import is_dangerous_path


@pytest.mark.parametrize("root", ["/proc", "/sys", "/dev", "/run"])
@pytest.mark.parametrize("suffix", ["", "/synthetic", "/nested/synthetic"])
def test_pseudofilesystem_paths_denied_without_stat_or_open(root, suffix, monkeypatch):
    path = root + suffix
    with monkeypatch.context() as scoped:
        # Keep this entirely synthetic, including platforms with different mounts.
        scoped.setattr(Path, "resolve", lambda self, **kwargs: self)
        stat = Mock(side_effect=AssertionError("must validate before stat"))
        read = Mock(side_effect=AssertionError("must not open denied path"))
        scoped.setattr(Path, "stat", stat)
        scoped.setattr("utils.file_utils.open", read, raising=False)
        assert is_dangerous_path(Path(path))
        with pytest.raises(PermissionError):
            resolve_and_validate_path(path)
        assert estimate_file_tokens(path) == 0
        assert "ERROR ACCESSING FILE" in read_file_content(path)[0]
        assert read_file_safely(path) is None
        stat.assert_not_called()
        read.assert_not_called()


@pytest.mark.parametrize("root", ["/proc", "/sys", "/dev", "/run"])
def test_symlink_to_denied_path(tmp_path, root):
    link = tmp_path / "linked-file"
    link.symlink_to(root + "/pal-synthetic-file")
    assert is_dangerous_path(link)
    assert "ERROR ACCESSING FILE" in read_file_content(str(link))[0]
    assert read_file_safely(str(link)) is None
    assert estimate_file_tokens(str(link)) == 0


def test_descriptor_path_cannot_resolve_out_of_deny_policy(tmp_path, monkeypatch):
    safe = tmp_path / "ordinary.txt"
    safe.write_text("ordinary")
    descriptor = Path("/proc/self/fd/999999")
    original_resolve = Path.resolve

    def resolve(path, **kwargs):
        return safe if path == descriptor else original_resolve(path, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert is_dangerous_path(descriptor)
    with pytest.raises(PermissionError):
        resolve_and_validate_path(str(descriptor))


class TrackedBytes(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        assert size >= 0, "unbounded read"
        return super().read(size)


@pytest.mark.parametrize("reader", [read_file_content, read_file_safely])
@pytest.mark.parametrize(
    "data,limit,overflow",
    [
        (b"abc", 3, False),
        (b"abcd", 3, True),
        ("éé".encode(), 3, True),
        ("éé".encode(), 4, False),
        (b"", 0, False),
        (b"a", 0, True),
    ],
)
@pytest.mark.parametrize("reported_size", [0, 10_000_000])
def test_actual_bytes_determine_overflow(tmp_path, monkeypatch, reader, data, limit, overflow, reported_size):
    path = tmp_path / "sample.txt"
    path.write_bytes(data)
    original_stat = Path.stat

    def stat(p, *args, **kwargs):
        result = original_stat(p, *args, **kwargs)
        if p == path:
            fields = list(result)
            fields[6] = reported_size
            return os.stat_result(fields)
        return result

    monkeypatch.setattr(Path, "stat", stat)
    stream = TrackedBytes(data)
    opener = Mock(return_value=stream)
    monkeypatch.setattr("utils.file_utils.open", opener, raising=False)
    result = reader(str(path), max_size=limit)
    assert stream.read_sizes == [limit + 1]
    assert stream.closed
    opener.assert_called_once_with(path, "rb")
    if reader is read_file_content:
        content, tokens = result
        assert ("FILE TOO LARGE" in content) is overflow
        assert tokens > 0
        if not overflow:
            assert data.decode() in content
    elif overflow:
        assert result is None
    else:
        assert result == data.decode()


def test_successful_rendering_and_decode_contracts(tmp_path):
    path = tmp_path / "module.py"
    path.write_bytes(b"one\r\ntwo\rthree\xff")
    content, _ = read_file_content(str(path), include_line_numbers=True)
    assert "   1│ one\n   2│ two\n   3│ three�" in content
    assert content.startswith(f"\n--- BEGIN FILE: {path} (Last modified: ")
    assert content.endswith(f"\n--- END FILE: {path} ---\n")
    assert read_file_safely(str(path)) == "one\ntwo\nthree"


@pytest.mark.parametrize("reader", [read_file_content, read_file_safely])
def test_negative_limit_does_not_read(tmp_path, monkeypatch, reader):
    path = tmp_path / "sample.txt"
    path.write_text("content")
    opener = Mock(side_effect=AssertionError("must not read"))
    monkeypatch.setattr("utils.file_utils.open", opener, raising=False)
    result = reader(str(path), max_size=-2)
    assert result is None if reader is read_file_safely else "ERROR READING FILE" in result[0]
    opener.assert_not_called()


def test_ordinary_home_code_and_similarly_named_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for directory in ("project", "proc-backup", "sys", "dev", "run"):
        path = tmp_path / directory / "source.py"
        path.parent.mkdir()
        path.write_text("print('normal')")
        assert not is_dangerous_path(path)
        assert read_file_safely(str(path)) == "print('normal')"
        assert estimate_file_tokens(str(path)) > 0


def test_macos_aliases_keep_system_denies_and_temporary_paths(monkeypatch):
    def resolve(path, **kwargs):
        for alias in (Path("/etc"), Path("/var"), Path("/tmp")):
            if path.is_relative_to(alias):
                return Path("/private") / alias.name / path.relative_to(alias)
        return path

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "resolve", resolve)
        for denied in ("/etc/synthetic", "/private/etc/synthetic", "/var/synthetic", "/private/var/synthetic"):
            assert is_dangerous_path(Path(denied))
        for allowed in (
            "/tmp/project/source.py",
            "/private/tmp/project/source.py",
            "/Users/developer/project/source.py",
        ):
            assert not is_dangerous_path(Path(allowed))


def test_validation_returns_the_same_target_it_checked(tmp_path, monkeypatch):
    link = tmp_path / "changing-link"
    safe = tmp_path / "safe.txt"
    safe.write_text("safe")
    original_resolve = Path.resolve
    resolutions = []

    def resolve(path, **kwargs):
        if path == link:
            resolutions.append(path)
            return safe if len(resolutions) == 1 else Path("/proc/pal-synthetic-file")
        return original_resolve(path, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert resolve_and_validate_path(str(link)) == safe
    assert len(resolutions) == 1
