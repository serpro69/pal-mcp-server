"""Read-only CLI executable discovery using the invocation environment."""

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass

from clink.constants import SHARED_ENV_PASSTHROUGH
from clink.models import ResolvedCLIClient
from clink.registry import ClinkRegistry


@dataclass(frozen=True)
class CLIAvailability:
    """Executable observation only; arguments and authentication are unverified."""

    name: str
    executable_found: bool


def build_cli_environment(client: ResolvedCLIClient) -> dict[str, str]:
    """Build the same filtered, override-aware environment used by the runner."""
    allowed = SHARED_ENV_PASSTHROUGH | set(client.env_passthrough)
    if os.name == "nt":
        allowed = {name.upper() for name in allowed}
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
    return shutil.which(client.executable[0], path=env.get("PATH", os.defpath))


def discover_cli_availability(registry: ClinkRegistry) -> tuple[CLIAvailability, ...]:
    return tuple(
        CLIAvailability(name, resolve_executable(registry.get_client(name)) is not None)
        for name in registry.list_clients()
    )
