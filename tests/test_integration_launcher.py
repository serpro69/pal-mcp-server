"""The launcher selects an environment and propagates failures without live calls."""

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="Bash launcher requires bash")


@pytest.fixture
def launcher(tmp_path):
    checkout = tmp_path / "checkout with spaces"
    checkout.mkdir()
    script = checkout / "run_integration_tests.sh"
    shutil.copyfile(Path(__file__).resolve().parents[1] / script.name, script)
    capture = tmp_path / "calls.jsonl"
    env = {**os.environ, "VIRTUAL_ENV": "", "PAL_LAUNCHER_CAPTURE": str(capture)}

    def interpreter(directory):
        executable = directory / "bin" / "python"
        executable.parent.mkdir(parents=True)
        recorder = executable.with_name("capture.py")
        recorder.write_text(
            "import json, os, sys\n"
            "with open(os.environ['PAL_LAUNCHER_CAPTURE'], 'a') as stream:\n"
            "    stream.write(json.dumps({'exe': sys.argv[1], 'args': sys.argv[2:], 'cwd': os.getcwd()}) + '\\n')\n"
            "sys.exit(int(os.environ.get('PAL_LAUNCHER_EXIT', '0')))\n"
        )
        executable.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(recorder))} "$0" "$@"\n')
        executable.chmod(0o755)
        return executable

    def run(*args):
        result = subprocess.run(
            [shutil.which("bash"), str(script), *args],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        calls = [json.loads(line) for line in capture.read_text().splitlines()] if capture.exists() else []
        return result, calls

    return checkout, env, interpreter, run


def test_active_environment_wins_and_live_modes_use_it(launcher, tmp_path):
    checkout, env, interpreter, run = launcher
    interpreter(checkout / ".pal_venv")
    active = tmp_path / "active environment"
    python = interpreter(active)
    env["VIRTUAL_ENV"] = str(active)
    result, calls = run("--with-simulator")
    assert result.returncode == 0, result.stderr
    assert [c["args"] for c in calls] == [
        ["-m", "pytest", "tests/", "-v", "-m", "integration", "--tb=short"],
        ["communication_simulator_test.py", "--verbose"],
    ]
    assert all(c["exe"] == str(python) and c["cwd"] == str(checkout) for c in calls)


def test_checkout_fallback_and_stub_never_select_live_tests(launcher):
    checkout, _, interpreter, run = launcher
    python = interpreter(checkout / ".pal_venv")
    result, calls = run("--stub")
    assert result.returncode == 0, result.stderr
    assert calls == [{"exe": str(python), "args": ["-m", "simulator_tests.stub_provider"], "cwd": str(checkout)}]
    assert "API key" not in result.stdout


@pytest.mark.parametrize("mode", ["--stub", "--with-simulator"])
def test_child_failure_is_preserved_and_stops_later_steps(launcher, mode):
    checkout, env, interpreter, run = launcher
    interpreter(checkout / ".pal_venv")
    env["PAL_LAUNCHER_EXIT"] = "7"
    result, calls = run(mode)
    assert result.returncode == 7
    assert len(calls) == 1
    assert "completed!" not in result.stdout


@pytest.mark.parametrize("args,code", [((), 1), (("--unknown",), 2), (("--stub", "--with-simulator"), 2)])
def test_missing_environment_or_invalid_selection_fails_without_running(launcher, args, code):
    _, _, _, run = launcher
    result, calls = run(*args)
    assert result.returncode == code
    assert not calls
