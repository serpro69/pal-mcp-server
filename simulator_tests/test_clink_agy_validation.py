"""Opt-in agy roles and PAL history using disposable files and the existing login."""

import hashlib
import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from mcp import StdioServerParameters

from clink.constants import INTERNAL_DEFAULTS, SHARED_ENV_PASSTHROUGH

from .base_test import BaseSimulatorTest
from .server_session import SimulatorSession

LIVE_ENV = "PAL_RUN_AGY_INTEGRATION"
REPO = Path(__file__).resolve().parents[1]

# Isolate PAL manifests/state while preserving the native CLI's home/login.
# Observe cwd without changing the production process or conversation lifecycle.
SERVER_BOOTSTRAP = """
import asyncio
import json
import os
import sys
from pathlib import Path
import clink.registry as registry
from clink.agents.base import BaseCLIAgent
root = Path(sys.argv[1])
registry.USER_CONFIG_DIR = root / 'unused-user-clients'
original = BaseCLIAgent._run_in_directory
async def observe(self, **kwargs):
    with (root / 'invocations.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'cwd': kwargs['cwd'], 'allow_edits': kwargs['allow_edits']}) + '\\n')
    return await original(self, **kwargs)
BaseCLIAgent._run_in_directory = observe
spawn = asyncio.create_subprocess_exec
async def observe_child(*args, **kwargs):
    process = await spawn(*args, **kwargs)
    record = {'launch_path': kwargs.get('cwd'), 'actual_cwd': None, 'actual_matches_launch': None}
    try:
        record['actual_cwd'] = os.readlink('/proc/%d/cwd' % process.pid)
        record['actual_matches_launch'] = os.path.samefile(record['actual_cwd'], kwargs['cwd'])
    except OSError as exc:
        record['observation_unavailable'] = type(exc).__name__
    with (root / 'children.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(record) + '\\n')
    return process
asyncio.create_subprocess_exec = observe_child
import server
try:
    server.run()
finally:
    (root / 'server-stopped').write_text('stopped', encoding='utf-8')
"""


def agy_server_environment(root: Path) -> dict[str, str]:
    allowed = SHARED_ENV_PASSTHROUGH | set(INTERNAL_DEFAULTS["agy"].env_passthrough)
    env = {name: os.environ[name] for name in allowed if name in os.environ}
    env.update(
        PAL_SKIP_DOTENV="true",
        PAL_STATE_DIR=str(root / "state"),
        PAL_LOG_DIR=str(root / "logs"),
        PAL_WORKSPACE_ROOT=str(root),
        PAL_TRANSCRIPTS="false",
        CLI_CLIENTS_CONFIG_PATH=str(root / "clients"),
        DEFAULT_MODEL="auto",
        DISABLED_TOOLS="",
        LOG_LEVEL="WARNING",
        TMPDIR=str(root),
        TMP=str(root),
        TEMP=str(root),
    )
    return env


def successful_payload(result) -> dict:
    text = "\n".join(item.text for item in result.content if item.type == "text")
    if result.isError:
        raise AssertionError(f"agy MCP call failed: {text}")
    payload = json.loads(text)
    assert payload["status"] in {"success", "continuation_available"}, f"agy request failed: {text}"
    return payload


def run_agy_validation(
    logger: logging.Logger, *, role: str = "codereviewer", configured_cwd: bool = False, requested_cwd: bool = False
) -> dict:
    """Run unchanged roles over real MCP; missing prerequisites and CLI errors fail."""
    if os.environ.get(LIVE_ENV) != "1":
        raise ValueError(f"Set {LIVE_ENV}=1 to opt in to the installed agy check")
    if role not in {"default", "planner", "codereviewer"}:
        raise ValueError(f"Unsupported agy validation role: {role}")
    if configured_cwd and requested_cwd:
        raise ValueError("Select either configured_cwd or requested_cwd for the live scenario")
    with tempfile.TemporaryDirectory(prefix="pal_agy_validation_") as directory:
        root = Path(directory).resolve()
        env = agy_server_environment(root)
        executable = shutil.which("agy", path=env.get("PATH", os.defpath))
        if executable is None:
            raise RuntimeError("agy is missing; install and authenticate native agy 1.3.5 outside PAL")
        version = subprocess.run(
            [executable, "--version"], cwd=root, env=env, capture_output=True, text=True, timeout=15, check=True
        ).stdout.strip()
        assert version == "1.3.5", f"Live acceptance requires agy 1.3.5; got {version!r}"
        project = root / "project"
        project.mkdir()
        launch = root / "requested project" if requested_cwd else project
        if requested_cwd:
            launch.symlink_to(project, target_is_directory=True)
        source = project / "fixture.py"
        marker, label = "AGY_FILE_" + uuid4().hex, "AGY_LABEL_" + uuid4().hex
        source.write_text(f'def fixture_value():\n    return "{marker}"\n', encoding="utf-8")
        manifests = root / "clients"
        manifests.mkdir()
        config = json.loads((REPO / "conf/cli_clients/agy.json").read_text(encoding="utf-8"))
        config.update(command=shlex.join([executable]), timeout_seconds=90)
        if configured_cwd:
            config["working_dir"] = str(project)
        elif requested_cwd:
            fallback = root / "manifest fallback"
            fallback.mkdir()
            config["working_dir"] = str(fallback)
        (manifests / "agy.json").write_text(json.dumps(config), encoding="utf-8")
        settings = Path(env.get("HOME", str(Path.home()))) / ".gemini/antigravity-cli/settings.json"

        def settings_hash():
            return hashlib.sha256(settings.read_bytes()).hexdigest() if settings.exists() else None

        before = settings_hash()
        parameters = StdioServerParameters(
            command=sys.executable, args=["-c", SERVER_BOOTSTRAP, str(root)], cwd=str(REPO), env=env
        )
        try:
            with SimulatorSession(timeout=120, parameters=parameters) as session:
                directory_fields = {"working_directory_absolute_path": f"{launch}/./"} if requested_cwd else {}
                first = successful_payload(
                    session.call_tool(
                        "clink",
                        {
                            "cli_name": "agy",
                            "role": role,
                            "prompt": (
                                f"Read {'fixture.py in your current project directory' if requested_cwd else 'the referenced Python fixture'} "
                                f"using a native file-reading tool. Its tracking label is {label}. "
                                "Report the exact string returned by fixture_value and include the tracking label in your answer. "
                                "Give a concise review or plan appropriate to your role. Do not edit files or run commands."
                            ),
                            "absolute_file_paths": [] if requested_cwd else [str(source)],
                            **directory_fields,
                        },
                    )
                )
                assert (
                    marker in first["content"] and label in first["content"]
                ), "First answer did not prove fixture access"
                assert first["metadata"]["history_saved"] is True
                thread_id = first["continuation_offer"]["continuation_id"]
                # The original marker is no longer in the referenced file. Only
                # PAL's preceding exchange can supply it to the next fresh CLI.
                source.write_text('def fixture_value():\n    return "REPLACED_AFTER_FIRST_CALL"\n', encoding="utf-8")
                followup = successful_payload(
                    session.call_tool(
                        "clink",
                        {
                            "continuation_id": thread_id,
                            "prompt": (
                                "From the previous exchange, repeat the ORIGINAL return string and tracking label. "
                                "The fixture has since changed; use the earlier answer, not its current contents. "
                                "Keep your role's response format. Do not edit files or run commands."
                            ),
                        },
                    )
                )
                assert marker in followup["content"] and label in followup["content"], "Follow-up lost original history"
                assert followup["metadata"]["history_saved"] is True
                assert followup["metadata"]["history"]["retained_messages"] == 2
                assert followup["continuation_offer"]["continuation_id"] == thread_id
                for index, result in enumerate((first, followup)):
                    assert result["metadata"]["cli_name"] == "agy" and result["metadata"]["role"] == role
                    command = result["metadata"]["command"]
                    assert not {"--mode", "--continue", "-c", "--conversation"}.intersection(command)
                    assert not any(marker in argument or label in argument for argument in command)
                    assert result["metadata"]["working_directory"] == {
                        "mode": "directory" if configured_cwd or requested_cwd else "scratch",
                        "launch_path": str(launch) if configured_cwd or requested_cwd else None,
                        "canonical_path": str(project) if configured_cwd or requested_cwd else None,
                        "source": (
                            "conversation"
                            if index
                            else "request" if requested_cwd else "configuration" if configured_cwd else "scratch"
                        ),
                    }
            assert (root / "server-stopped").exists(), "MCP server did not shut down cleanly"
            invocations = [json.loads(line) for line in (root / "invocations.jsonl").read_text().splitlines()]
            assert len(invocations) == 2
            children = [json.loads(line) for line in (root / "children.jsonl").read_text().splitlines()]
            assert len(children) == 2
            for invocation in invocations:
                cwd = Path(invocation["cwd"])
                if configured_cwd or requested_cwd:
                    assert cwd == launch and os.path.samefile(cwd, project)
                else:
                    assert not cwd.exists(), "Scratch directory survived completed invocation"
                assert invocation["allow_edits"] is False
                if not configured_cwd and not requested_cwd:
                    assert not project.is_relative_to(cwd)
            if not configured_cwd and not requested_cwd:
                assert invocations[0]["cwd"] != invocations[1]["cwd"]
            for child, invocation in zip(children, invocations):
                assert child["launch_path"] == invocation["cwd"]
                if child["actual_matches_launch"] is not None:
                    # Filesystem identity was checked while the child and its
                    # directory existed, without assuming native path spelling.
                    assert child["actual_matches_launch"] is True
            assert settings_hash() == before, "Native agy changed saved user settings"
            evidence = {
                "version": version,
                "role": role,
                "cwd_mode": "project" if configured_cwd or requested_cwd else "scratch",
                "cwd_source": "request" if requested_cwd else "configuration" if configured_cwd else "scratch",
                "working_directory": first["metadata"]["working_directory"],
                "policy": {
                    "allow_edits": False,
                    "native_mode": "inherited saved policy",
                    "project_configuration": "disposable fixture without project hooks",
                },
                "project": str(project),
                "invocations": invocations,
                "children": children,
                "actual_locations_observed": all(child["actual_matches_launch"] is not None for child in children),
                "file_read_verified": True,
                "history_verified": True,
                "settings_sha256": before,
                "settings_unchanged": True,
                "auth_environment_names": [name for name in INTERNAL_DEFAULTS["agy"].env_passthrough if name in env],
            }
            logger.info("agy live role/history check passed: %s", json.dumps(evidence, sort_keys=True))
            return evidence
        finally:
            if settings_hash() != before:
                logger.error("Native agy user settings changed during the live check")


class ClinkAgyValidationTest(BaseSimulatorTest):
    live_selector_env = LIVE_ENV
    live_selector_values = ("1",)

    @property
    def test_description(self):
        return "Agy codereviewer request-selected project read and omitted-directory follow-up"

    def run_test(self) -> bool:
        run_agy_validation(self.logger, requested_cwd=True)
        return True
