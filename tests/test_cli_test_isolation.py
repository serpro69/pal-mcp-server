"""Exercise the real pytest hooks against synthetic personal CLI configuration."""

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from providers.configuration import provider_configurations
from simulator_tests.server_session import SimulatorSession
from tests.cli_isolation import isolated_server_parameters

ROOT = Path(__file__).resolve().parents[1]


def test_ordinary_stdio_child_ignores_malformed_personal_manifests(tmp_path):
    personal = tmp_path / "personal-clients"
    personal.mkdir()
    malformed = personal / "claude.json"
    invalid_manifest = {"name": "claude", "command": "operator-cli", "history_token_budget": 0}
    malformed.write_text(json.dumps(invalid_manifest), encoding="utf-8")
    env = os.environ.copy()
    for declaration in provider_configurations().values():
        for key in declaration.environment_names():
            env.pop(key, None)
    env.update(
        PAL_SKIP_DOTENV="true",
        PAL_STATE_DIR=str(tmp_path / "state"),
        PAL_LOG_DIR=str(tmp_path / "logs"),
        PAL_TRANSCRIPTS="false",
        CLI_CLIENTS_CONFIG_PATH=str(personal),
        DISABLED_TOOLS="",
    )
    parameters = isolated_server_parameters(env)
    launcher = parameters.args[0]
    # Inject malformed operator state before the actual ordinary test launcher.
    # No HOME/auth directory is changed, and no model call is made.
    bootstrap = (
        "import runpy,sys; from pathlib import Path; import clink.registry as r; "
        "r.USER_CONFIG_DIR=Path(sys.argv[1]); r._REGISTRY=None; "
        "runpy.run_path(sys.argv[2], run_name='__main__')"
    )
    original = subprocess.run(
        [sys.executable, "-c", bootstrap, str(personal), str(ROOT / "server.py")],
        cwd=ROOT,
        env=env,
        input="",
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert original.returncode != 0
    assert "history_token_budget" in original.stderr
    assert "greater_than" in original.stderr
    parameters.args = [
        "-c",
        bootstrap,
        str(personal),
        launcher,
    ]
    with SimulatorSession(timeout=15, parameters=parameters) as session:
        result = session.call_tool("listmodels", {})
        assert not result.is_error
        metadata = json.loads(result.content[0].text)["metadata"]
        assert metadata["configured_providers"] == 0
        assert {client["name"] for client in metadata["cli_clients"]} == {"agy", "claude", "codex", "gemini"}
    assert json.loads(malformed.read_text(encoding="utf-8")) == invalid_manifest


@pytest.mark.parametrize("cached", [False, True], ids=["uncached", "cached"])
@pytest.mark.parametrize("failing_test", [False, True], ids=["success", "test-failure"])
def test_pytest_ignores_operator_manifests_and_restores_state(tmp_path, cached, failing_test):
    suite = tmp_path / "suite"
    suite.mkdir()
    shutil.copyfile(ROOT / "tests" / "conftest.py", suite / "conftest.py")
    personal = tmp_path / "personal-clients"
    configured = tmp_path / "configured-clients"
    for directory in (personal, configured):
        directory.mkdir()
        (directory / "claude.json").write_text(
            json.dumps({"name": "claude", "command": "operator-cli", "history_token_budget": 0}),
            encoding="utf-8",
        )

    probe = suite / "test_cli_probe.py"
    probe.write_text(
        textwrap.dedent("""
            import json
            import os
            from types import SimpleNamespace
            from __main__ import original_environment

            import server
            import clink.registry as registry
            from simulator_tests import test_clink_cli_only as live_clink

            # This must hold at collection, before function-scoped fixtures run.
            isolated_path = registry.USER_CONFIG_DIR
            expected_roles = sorted(json.loads((registry.CONFIG_DIR / "claude.json").read_text())["roles"])
            assert registry.get_registry().list_roles("claude") == expected_roles
            assert server.TOOLS["clink"]._registry.list_roles("claude") == expected_roles
            assert not os.environ.get("CLI_CLIENTS_CONFIG_PATH")
            original_locations = {key: os.environ.get(key) for key in ("HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR")}

            def test_a_explicit_fixture_override(monkeypatch, tmp_path):
                manifest = tmp_path / "fixture.json"
                manifest.write_text(json.dumps({"name": "claude", "command": "synthetic-cli"}))
                monkeypatch.setenv("CLI_CLIENTS_CONFIG_PATH", str(manifest))
                assert registry.get_registry().get_client("claude").executable == ["synthetic-cli"]

            def test_b_next_test_has_builtin_registry():
                assert not os.environ.get("CLI_CLIENTS_CONFIG_PATH")
                assert registry.get_registry().list_roles("claude") == expected_roles
                assert registry.get_registry().get_client("claude").executable == ["claude"]

            def test_c_live_harness_retains_auth_locations(monkeypatch, tmp_path):
                assert {key: os.environ.get(key) for key in original_locations} == original_locations
                for vendor, setting in (("claude", "CLAUDE_CONFIG_DIR"), ("codex", "CODEX_HOME")):
                    with monkeypatch.context() as context:
                        context.setattr(live_clink, "os", SimpleNamespace(environ=original_environment))
                        expected = live_clink.live_server_environment(tmp_path, vendor)
                    child = live_clink.live_server_environment(tmp_path, vendor)
                    assert child.keys() == expected.keys()
                    for key in expected:
                        # Compare without exposing auth values in assertion output.
                        unchanged = child[key] == expected[key]
                        assert unchanged, f"Live child environment changed: {key}"
                    assert child.get("HOME") == original_locations["HOME"]
                    assert child.get(setting) == original_locations[setting]
                    assert child["CLI_CLIENTS_CONFIG_PATH"] == str(tmp_path / "clients")

            def test_d_controlled_failure():
                assert os.environ["PAL_TEST_EXPECT_FAILURE"] == "false", "intentional fixture failure"
            """),
        encoding="utf-8",
    )
    runner = tmp_path / "run_probe.py"
    runner.write_text(
        textwrap.dedent("""
            import os
            import json
            import sys
            from pathlib import Path

            import pytest
            import clink.registry as registry

            suite, personal, configured = map(Path, sys.argv[1:4])
            registry.USER_CONFIG_DIR = personal
            original_cache = object() if sys.argv[4] == "cached" else None
            registry._REGISTRY = original_cache
            original_env = os.environ["CLI_CLIENTS_CONFIG_PATH"]
            original_environment = os.environ.copy()
            locations = {key: os.environ.get(key) for key in ("HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR")}
            result = pytest.main(["-q", "-p", "no:cacheprovider", "--confcutdir", str(suite), str(suite)])
            expected = 1 if os.environ["PAL_TEST_EXPECT_FAILURE"] == "true" else 0
            assert result == expected, f"unexpected nested pytest result: {result}"
            assert registry.USER_CONFIG_DIR == personal
            assert registry._REGISTRY is original_cache
            assert os.environ["CLI_CLIENTS_CONFIG_PATH"] == original_env
            assert {key: os.environ.get(key) for key in locations} == locations
            assert not sys.modules["test_cli_probe"].isolated_path.parent.exists()
            for directory in (personal, configured):
                assert json.loads((directory / "claude.json").read_text()) == {
                    "name": "claude", "command": "operator-cli", "history_token_budget": 0
                }
            print("CLI configuration restored; operator files and authentication locations unchanged")
            """),
        encoding="utf-8",
    )
    env = os.environ.copy()
    for declaration in provider_configurations().values():
        for key in declaration.environment_names():
            env.pop(key, None)
    for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_CURRENT_TEST"):
        env.pop(key, None)
    env.update(
        PYTHONPATH=str(ROOT),
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PAL_SKIP_DOTENV="true",
        PYTHON_DOTENV_DISABLED="1",
        CLI_CLIENTS_CONFIG_PATH=str(configured),
        PAL_TEST_EXPECT_FAILURE="true" if failing_test else "false",
    )
    result = subprocess.run(
        [sys.executable, str(runner), str(suite), str(personal), str(configured), "cached" if cached else "uncached"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CLI configuration restored" in result.stdout
