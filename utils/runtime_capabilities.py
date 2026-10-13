"""Startup capability snapshot shared by dispatch and discovery without server imports."""

from collections.abc import Iterable
from dataclasses import dataclass

from clink.availability import CLIAvailability
from providers.configuration import ProviderSetupDiagnostics
from providers.shared import ProviderType

PROVIDER_INDEPENDENT_TOOLS = frozenset(
    {"clink", "version", "listmodels", "planner", "tracer", "docgen", "challenge", "apilookup", "jules"}
)


@dataclass(frozen=True)
class RuntimeCapabilities:
    """Actual bootstrap results and startup observations, stable until reinitialized."""

    configured_providers: frozenset[ProviderType]
    provider_setup: tuple[ProviderSetupDiagnostics, ...]
    cli_statuses: tuple[CLIAvailability, ...]
    enabled_tools: frozenset[str]

    @property
    def provider_free(self) -> bool:
        return not self.configured_providers

    @property
    def available_tools(self) -> frozenset[str]:
        return self.enabled_tools & PROVIDER_INDEPENDENT_TOOLS if self.provider_free else self.enabled_tools

    def unavailable_reason(self, name: str) -> str | None:
        """Explain a provider restriction; unknown/disabled names belong to the caller."""
        if name not in self.enabled_tools or name in self.available_tools:
            return None
        message = (
            f"Tool '{name}' requires a PAL model provider. "
            f"Available tools: {', '.join(sorted(self.available_tools))}. "
            "Configure provider credentials and restart PAL to use this tool."
        )
        if "clink" in self.available_tools:
            message += " For CLI conversations, use clink with a configured cli_name."
            if not any(status.executable_found for status in self.cli_statuses):
                message += (
                    " No configured CLI executable was found at startup; install/configure and authenticate your CLI."
                )
        return message


_runtime: RuntimeCapabilities | None = None


def initialize_runtime_capabilities(
    *,
    configured_providers: Iterable[ProviderType],
    enabled_tools: Iterable[str],
    provider_setup: Iterable[ProviderSetupDiagnostics] = (),
    cli_statuses: Iterable[CLIAvailability] = (),
) -> RuntimeCapabilities:
    global _runtime
    _runtime = RuntimeCapabilities(
        frozenset(configured_providers), tuple(provider_setup), tuple(cli_statuses), frozenset(enabled_tools)
    )
    return _runtime


def get_runtime_capabilities() -> RuntimeCapabilities:
    if _runtime is None:
        raise RuntimeError("Runtime capabilities have not been initialized by server startup.")
    return _runtime


def reset_runtime_capabilities() -> None:
    global _runtime
    _runtime = None
