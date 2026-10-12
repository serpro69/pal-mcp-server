"""Portable state paths, launcher agreement, rotation and visible diagnostics."""

import logging
import os
import re
import shutil
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest.mock import Mock

import pytest

from utils import env, log_dir, state_dir
from utils.state_dir import StateDirectoryError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def paths(monkeypatch, tmp_path):
    for name in ("PAL_STATE_DIR", "PAL_LOG_DIR", "XDG_STATE_HOME", "LOCALAPPDATA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    return tmp_path.resolve()


def test_precedence_spaces_and_no_creation_until_requested(monkeypatch, paths):
    monkeypatch.setattr(state_dir.sys, "platform", "linux")
    assert state_dir.get_state_dir() == paths / "home/.local/state/pal"
    monkeypatch.setenv("XDG_STATE_HOME", str(paths / "xdg state"))
    assert state_dir.get_state_dir() == paths / "xdg state/pal"
    monkeypatch.setenv("PAL_STATE_DIR", str(paths / "explicit state"))
    assert state_dir.get_state_dir() == paths / "explicit state"
    assert log_dir.get_log_dir() == paths / "explicit state/logs"
    assert not (paths / "explicit state").exists()
    monkeypatch.setenv("PAL_LOG_DIR", str(paths / "only logs"))
    assert log_dir.get_log_dir(create=True) == paths / "only logs"
    assert (paths / "only logs").is_dir()
    assert not (paths / "explicit state").exists()


def test_windows_localappdata_and_home_fallback(monkeypatch, paths):
    monkeypatch.setattr(state_dir.sys, "platform", "win32")
    assert state_dir.get_state_dir() == paths / "home/AppData/Local/pal"
    monkeypatch.setenv("LOCALAPPDATA", str(paths / "Local App Data"))
    assert state_dir.get_state_dir() == paths / "Local App Data/pal"
    monkeypatch.setenv("XDG_STATE_HOME", str(paths / "xdg"))
    assert state_dir.get_state_dir() == paths / "xdg/pal"


@pytest.mark.parametrize("setting", ["PAL_STATE_DIR", "XDG_STATE_HOME", "PAL_LOG_DIR"])
@pytest.mark.parametrize("kind", ["relative", "file", "file_parent"])
def test_invalid_explicit_paths_do_not_fall_back(monkeypatch, paths, setting, kind):
    regular = paths / "regular"
    regular.write_text("keep")
    value = "relative/directory" if kind == "relative" else str(regular if kind == "file" else regular / "child")
    monkeypatch.setenv(setting, value)
    with pytest.raises(StateDirectoryError, match=setting):
        log_dir.get_log_dir(create=True)
    assert regular.read_text() == "keep"
    assert not (paths / "home").exists()


def test_home_expansion_and_absent_home(monkeypatch, paths):
    monkeypatch.setenv("HOME" if os.name != "nt" else "USERPROFILE", str(paths / "real home"))
    monkeypatch.setenv("PAL_STATE_DIR", "~/pal state")
    assert state_dir.get_state_dir() == paths / "real home/pal state"
    monkeypatch.delenv("PAL_STATE_DIR")

    def no_home():
        raise RuntimeError("no home")

    monkeypatch.setattr(Path, "home", no_home)
    with pytest.raises(StateDirectoryError, match="set an absolute PAL_STATE_DIR"):
        state_dir.get_state_dir()


def test_no_home_environment_uses_account_home(monkeypatch):
    for name in ("HOME", "USERPROFILE", "PAL_STATE_DIR", "XDG_STATE_HOME", "LOCALAPPDATA"):
        monkeypatch.delenv(name, raising=False)
    if os.name == "nt":
        with pytest.raises(StateDirectoryError):
            state_dir.get_state_dir()
    else:
        assert state_dir.get_state_dir().is_absolute()


def test_dotenv_policy_and_creation_failure_keep_cause(monkeypatch, paths):
    env.reload_env({"PAL_MCP_FORCE_ENV_OVERRIDE": "true", "PAL_STATE_DIR": str(paths / "configured")})
    monkeypatch.setenv("PAL_STATE_DIR", str(paths / "ignored"))
    assert state_dir.get_state_dir() == paths / "configured"
    failure = PermissionError("read-only")
    monkeypatch.setattr(Path, "mkdir", Mock(side_effect=failure))
    with pytest.raises(StateDirectoryError) as error:
        log_dir.get_log_dir(create=True)
    assert error.value.__cause__ is failure


def test_simulator_and_healthcheck_resolve_runtime_paths(monkeypatch, paths):
    from docker.scripts.healthcheck import check_log_directory
    from simulator_tests.log_utils import LogUtils

    monkeypatch.setenv("PAL_STATE_DIR", str(paths / "state"))
    selected = log_dir.get_log_dir(create=True)
    (selected / log_dir.MAIN_LOG_NAME).write_text("first\nlast\n")
    (selected / log_dir.ACTIVITY_LOG_NAME).write_text("activity\n")
    assert LogUtils.get_recent_server_logs(lines=1) == "last\n"
    assert "activity" in LogUtils.get_server_logs_since()
    assert check_log_directory()
    monkeypatch.setenv("PAL_LOG_DIR", str(paths / "other"))
    assert not check_log_directory()
    other = log_dir.get_log_dir(create=True)
    (other / log_dir.MAIN_LOG_NAME).write_text("new location\n")
    assert LogUtils.get_recent_server_logs() == "new location\n"
    assert (selected / log_dir.MAIN_LOG_NAME).read_text() == "first\nlast\n"


def test_python_and_bash_launcher_agree_for_spaces(monkeypatch, paths):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("Bash is unavailable on this platform")
    monkeypatch.setenv("PAL_STATE_DIR", str(paths / "state with spaces"))
    command = [sys.executable, "-m", "utils.log_dir", "--create"]
    python_result = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    bash_result = subprocess.run(
        [bash, "-c", 'source "$1/run-server.sh"; resolve_log_dir "$2"', "pal-log-check", str(ROOT), sys.executable],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert python_result.stdout == bash_result.stdout == str(log_dir.get_log_dir()) + "\n"


@pytest.mark.parametrize("launcher", ["bash", "powershell"])
@pytest.mark.parametrize("setting", ["PAL_STATE_DIR", "PAL_LOG_DIR", "XDG_STATE_HOME"])
@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("inherited_value", [False, True])
def test_launchers_preserve_shared_environment_precedence(
    monkeypatch, paths, launcher, setting, force, inherited_value
):
    executable = shutil.which("bash" if launcher == "bash" else "pwsh")
    if not executable or (launcher == "bash" and os.name == "nt"):
        pytest.skip(f"Native {launcher} runner unavailable; exercise on its supported platform")
    # A disposable copy gives utils.env a synthetic .env, never the user's file.
    shutil.copytree(ROOT / "utils", paths / "utils", ignore=shutil.ignore_patterns("__pycache__"))
    inherited, configured = paths / "inherited path", paths / "configured path"
    settings = paths / ".env"
    settings.write_text(f"{setting}='{configured}'\nPAL_MCP_FORCE_ENV_OVERRIDE={'true' if force else 'false'}\n")
    if inherited_value:
        monkeypatch.setenv(setting, str(inherited))
    monkeypatch.setenv("PYTHONPATH", str(paths))
    expected_root = configured if force or not inherited_value else inherited
    expected = (
        expected_root
        if setting == "PAL_LOG_DIR"
        else expected_root / ("pal/logs" if setting == "XDG_STATE_HOME" else "logs")
    )
    direct = subprocess.run(
        [sys.executable, "-m", "utils.log_dir"], cwd=paths, check=True, capture_output=True, text=True
    )
    if launcher == "bash":
        command = [
            executable,
            "-c",
            'source "$1/run-server.sh"; load_env_preserving_state "$2"; resolve_log_dir "$3"',
            "pal-env-check",
            str(ROOT),
            str(settings),
            sys.executable,
        ]
    else:
        source = (ROOT / "run-server.ps1").read_text()
        names = ("Write-Step", "Write-Success", "Import-EnvFile", "Initialize-Logging")
        definitions = [re.search(r"(?ms)^function " + name + r" \{.*?^\}", source).group() for name in names]
        script = paths / "launcher-test.ps1"
        script.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            + "\n".join(definitions)
            + "\nImport-EnvFile\nInitialize-Logging -PythonPath $env:PAL_TEST_PYTHON\n[Console]::WriteLine($script:LOG_DIR)\n"
        )
        monkeypatch.setenv("PAL_TEST_PYTHON", sys.executable)
        command = [executable, "-NoProfile", "-File", str(script)]
    launched = subprocess.run(command, cwd=paths, check=True, capture_output=True, text=True)
    assert direct.stdout.strip() == launched.stdout.strip().splitlines()[-1] == str(expected)


@pytest.mark.parametrize(
    "name,size,backups,total",
    [
        (log_dir.MAIN_LOG_NAME, log_dir.MAIN_LOG_MAX_BYTES, log_dir.MAIN_LOG_BACKUPS, 120),
        (log_dir.ACTIVITY_LOG_NAME, log_dir.ACTIVITY_LOG_MAX_BYTES, log_dir.ACTIVITY_LOG_BACKUPS, 30),
    ],
)
def test_server_rotation_matches_active_plus_backups(tmp_path, name, size, backups, total):
    import server

    actual = server.file_handler if name == log_dir.MAIN_LOG_NAME else server.mcp_file_handler
    assert actual.maxBytes == size
    assert actual.backupCount == backups
    assert size * (backups + 1) == total * 1024 * 1024
    handler = RotatingFileHandler(tmp_path / name, maxBytes=64, backupCount=backups, encoding="utf-8")
    try:
        for _ in range(backups + 5):
            handler.emit(logging.LogRecord("test", logging.INFO, __file__, 1, "x" * 45, (), None))
    finally:
        handler.close()
    assert len(list(tmp_path.glob(name + "*"))) == backups + 1


@pytest.mark.parametrize("failure", ["stream", "rollover", "encoding"])
def test_activity_logging_diagnoses_real_handler_failures(monkeypatch, caplog, failure):
    import server

    monkeypatch.setattr(logging, "raiseExceptions", False)
    handler = server.mcp_file_handler
    if failure == "rollover":
        monkeypatch.setattr(handler, "shouldRollover", lambda record: True)
        monkeypatch.setattr(handler, "doRollover", Mock(side_effect=OSError("rename failed")))
    else:
        error = UnicodeEncodeError("ascii", "é", 0, 1, "encoding") if failure == "encoding" else OSError("disk full")
        monkeypatch.setattr(handler.stream, "write", Mock(side_effect=error))
    with caplog.at_level(logging.WARNING):
        server.log_activity("TOOL_CALL", "%s", "chat")
    assert "Activity log write failed for TOOL_CALL" in caplog.text


def test_activity_logging_does_not_swallow_formatter_bugs(monkeypatch):
    import server

    monkeypatch.setattr(server.mcp_file_handler, "format", Mock(side_effect=TypeError("programming error")))
    with pytest.raises(TypeError, match="programming error"):
        server.log_activity("TOOL_CALL", "%s", "chat")


def test_invalid_log_path_fails_startup_before_writing_package(monkeypatch, paths):
    monkeypatch.setenv("PAL_STATE_DIR", str(paths / "state"))
    monkeypatch.setenv("PAL_LOG_DIR", "relative-invalid")
    result = subprocess.run([sys.executable, "-B", "-c", "import server"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode != 0
    assert "Invalid PAL_LOG_DIR directory" in result.stderr
    assert not (paths / "state").exists()
