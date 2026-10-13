"""Opt-in live acceptance with isolated PAL state and the operator's CLI login.

PAL_CLINK_LIVE_TEST=codex|claude|gemini selects the installed CLI. PAL provider
settings and dotenv files are excluded; runtime/auth locations and the selected
CLI's remaining environment allowlist are preserved. No login files are changed
by this harness. A successful reply does not establish a particular billing mode.
"""

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from mcp import StdioServerParameters

from clink.constants import INTERNAL_DEFAULTS, SHARED_ENV_PASSTHROUGH
from providers.configuration import provider_configurations

from .base_test import BaseSimulatorTest
from .server_session import SimulatorSession

LIVE_SELECTOR_ENV = "PAL_CLINK_LIVE_TEST"
LIVE_CLI_NAMES = ("codex", "claude", "gemini")
REPO = Path(__file__).resolve().parents[1]


def selected_live_cli() -> str | None:
    """Absence is a runner/pytest skip; any nonempty invalid selector is an error."""
    selector = os.environ.get(LIVE_SELECTOR_ENV, "")
    if not selector:
        return None
    if selector not in LIVE_CLI_NAMES:
        raise ValueError(f"Invalid {LIVE_SELECTOR_ENV}; expected one of {', '.join(LIVE_CLI_NAMES)}")
    return selector


def live_server_environment(root: Path, vendor: str) -> dict[str, str]:
    """Keep CLI auth access without allowing parent PAL settings into bootstrap."""
    provider_settings = {
        name for declaration in provider_configurations().values() for name in declaration.environment_names()
    }
    allowed = (SHARED_ENV_PASSTHROUGH | set(INTERNAL_DEFAULTS[vendor].env_passthrough)) - provider_settings
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


def _payload(result) -> dict:
    text = "\n".join(item.text for item in result.content if item.type == "text")
    if result.isError:
        raise AssertionError(f"Live clink MCP request failed: {text}")
    payload = json.loads(text)
    if payload["status"] not in {"success", "continuation_available"}:
        raise AssertionError(f"Live clink request failed: {text}")
    return payload


def check_live_conversation(session: SimulatorSession, vendor: str) -> None:
    """Require both prior input and the CLI's own answer in a dependent follow-up."""
    diagnostics = _payload(session.call_tool("listmodels", {}))["metadata"]
    assert diagnostics["mode"] == "provider-free" and diagnostics["configured_providers"] == 0
    assert all(provider["status"] == "absent" for provider in diagnostics["provider_setup"])
    selected = next(client for client in diagnostics["cli_clients"] if client["name"] == vendor)
    assert selected["executable_found"], f"Selected {vendor} executable unavailable in the PAL environment"

    label = uuid4().hex
    first = _payload(
        session.call_tool(
            "clink",
            {
                "cli_name": vendor,
                "prompt": (
                    f"Remember the label {label}. Invent a code of exactly twelve lowercase ASCII letters. "
                    "Calculate 2 + 2. Reply with <CLINK_ANSWER>label|code|sum</CLINK_ANSWER>, "
                    "substituting the label, your invented code, and the numeric sum. Do not use tools."
                ),
                "allow_edits": False,
            },
        )
    )
    match = re.search(rf"<CLINK_ANSWER>{label}\|([a-z]{{12}})\|4</CLINK_ANSWER>", first["content"])
    assert match, f"Expected the label, invented code, and arithmetic answer in first reply: {first['content']}"
    assert first["metadata"]["history_saved"] is True
    thread = first["continuation_offer"]["continuation_id"]
    followup = _payload(
        session.call_tool(
            "clink",
            {
                "continuation_id": thread,
                "prompt": (
                    "Repeat the remembered label, the code YOU invented in your previous answer, and the sum, "
                    "using the same CLINK_ANSWER tag and pipe-separated format. Do not invent a new code or use tools."
                ),
            },
        )
    )
    assert match.group(0) in followup["content"], f"Follow-up lost prior input/reply: {followup['content']}"
    assert followup["metadata"]["history_saved"] is True
    assert followup["metadata"]["history"]["retained_messages"] == 2
    assert followup["continuation_offer"]["continuation_id"] == thread


def run_live_clink(vendor: str, logger: logging.Logger) -> None:
    """Run shipped arguments in an owned workspace/session, failing unmet prerequisites."""
    if vendor not in LIVE_CLI_NAMES:
        raise ValueError(f"Unsupported live CLI: {vendor}")
    with tempfile.TemporaryDirectory(prefix="pal_clink_live_") as directory:
        root = Path(directory)
        env = live_server_environment(root, vendor)
        executable = shutil.which(vendor, path=env.get("PATH", os.defpath))
        if executable is None:
            raise RuntimeError(
                f"Selected {vendor} CLI is not installed/on PATH; install and authenticate it outside PAL"
            )
        version = subprocess.run(
            [executable, "--version"], cwd=root, env=env, capture_output=True, text=True, timeout=15, check=True
        ).stdout.strip()
        if not version:
            raise RuntimeError(f"Selected {vendor} CLI returned no version information")
        logger.info("Live CLI version: %s", version)
        logger.info(
            "Using existing CLI authentication; login, compatible shipped flags, and inference access are required"
        )

        manifests = root / "clients"
        manifests.mkdir()
        config = json.loads((REPO / "conf" / "cli_clients" / f"{vendor}.json").read_text(encoding="utf-8"))
        config["command"] = shlex.join([executable])
        config["timeout_seconds"] = 120
        (manifests / f"{vendor}.json").write_text(json.dumps(config), encoding="utf-8")
        # Reuse Task 12's launcher: no pytest fixtures, personal PAL manifests,
        # dotenv reads, provider registration/inference, or API ModelContext.
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(REPO / "tests" / "fixtures" / "cli_only_server.py"), str(root)],
            cwd=str(root),
            env=env,
        )
        with SimulatorSession(timeout=150, parameters=parameters) as session:
            check_live_conversation(session, vendor)
        assert (root / "server-stopped").exists(), "Live server did not shut down cleanly"
        assert not (root / "forbidden-calls.txt").exists(), "Live check attempted forbidden provider/dotenv access"
        logger.info("Live %s first request and dependent follow-up completed with provider-free PAL", vendor)


class ClinkCLIOnlyTest(BaseSimulatorTest):
    """Optional live scenario; excluded from the existing API-backed CI selection."""

    live_selector_env = LIVE_SELECTOR_ENV
    live_selector_values = LIVE_CLI_NAMES

    @property
    def test_description(self):
        return "Provider-free live CLI conversation and dependent follow-up"

    def run_test(self) -> bool:
        vendor = selected_live_cli()
        if vendor is None:
            raise ValueError(f"Set {LIVE_SELECTOR_ENV} before executing this live scenario")
        run_live_clink(vendor, self.logger)
        return True
