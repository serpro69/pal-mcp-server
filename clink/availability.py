"""Read-only CLI executable discovery using the invocation environment."""

import ntpath
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass

from clink.constants import SHARED_ENV_PASSTHROUGH
from clink.models import ResolvedCLIClient
from clink.registry import ClinkRegistry

# Child working directories are selected independently of the server's launch directory.
STARTUP_DIRECTORY = os.getcwd()


@dataclass(frozen=True)
class CLIAvailability:
    """Executable observation only; arguments and authentication are unverified."""

    name: str
    executable_found: bool


def build_cli_environment(client: ResolvedCLIClient) -> dict[str, str]:
    """Build the same filtered, override-aware environment used by the runner."""
    allowed = SHARED_ENV_PASSTHROUGH | set(client.env_passthrough)
    if os.name == "nt":
        allowed = {name.upper() for name in allowed} | {"NODEFAULTCURRENTDIRECTORYINEXEPATH"}
        env = {name.upper(): value for name, value in os.environ.items() if name.upper() in allowed}
        env.update({name.upper(): value for name, value in client.env.items()})
    else:
        env = {name: value for name, value in os.environ.items() if name in allowed}
        env.update(client.env)
    return env


def resolve_executable(client: ResolvedCLIClient, *, environment: Mapping[str, str] | None = None) -> str | None:
    """Resolve the first configured token without executing it or inspecting login."""
    if not client.executable or not client.executable[0]:
        raise ValueError(f"CLI '{client.name}' has an empty executable configuration.")
    env = build_cli_environment(client) if environment is None else environment
    program = client.executable[0]
    if os.name == "nt":
        return _windows_executable(program, env)

    path = env.get("PATH", os.defpath)
    if os.path.dirname(program):
        program = os.path.join(STARTUP_DIRECTORY, program)
    elif path:
        # An empty PATH disables bare-name discovery. Empty entries within a
        # nonempty search path (e.g. "bin:") denote the startup directory.
        path = os.pathsep.join(os.path.join(STARTUP_DIRECTORY, entry) for entry in path.split(os.pathsep))
    return shutil.which(program, path=path)


def _windows_executable(program: str, env: Mapping[str, str]) -> str | None:
    # shutil.which reads PATHEXT from the parent process even with an explicit path.
    folded = {key.upper(): value for key, value in env.items()}
    extensions = [ext for ext in folded.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if ext]
    explicit_path = bool(ntpath.dirname(program))
    if explicit_path:
        directories = [""]
    else:
        search_path = folded.get("PATH", ntpath.defpath)
        if not search_path:
            return None
        directories = search_path.split(";")
        # Windows normally searches cwd first. Honor the effective environment's
        # presence-based opt-out, and bind cwd to the server at startup.
        if "NODEFAULTCURRENTDIRECTORYINEXEPATH" not in folded:
            directories.insert(0, "")
    names = [program] if explicit_path or any(program.lower().endswith(ext.lower()) for ext in extensions) else []
    if not explicit_path or not ntpath.splitext(program)[1]:
        names.extend(program + ext for ext in extensions)
    for directory in directories:
        for name in names:
            candidate = ntpath.join(STARTUP_DIRECTORY, directory, name)
            if ntpath.isabs(candidate) and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return None


def discover_cli_availability(registry: ClinkRegistry) -> tuple[CLIAvailability, ...]:
    return tuple(
        CLIAvailability(name, resolve_executable(registry.get_client(name)) is not None)
        for name in registry.list_clients()
    )
