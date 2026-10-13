"""Declarative provider setup, shared by startup, discovery and test isolation."""

from dataclasses import dataclass
from typing import Literal

from utils.env import get_env

from .shared import ProviderType


@dataclass(frozen=True)
class ProviderSetupDiagnostics:
    """Declared input presence, not successful bootstrap or authentication.

    Only setting names are retained; credential and endpoint values stay private.
    """

    provider_type: ProviderType
    label: str
    status: Literal["absent", "incomplete", "complete"]
    supplied_settings: tuple[str, ...]
    missing_settings: tuple[str, ...]


@dataclass(frozen=True)
class ProviderConfiguration:
    provider_type: ProviderType
    label: str
    key_env: str
    restriction_env: str
    placeholders: tuple[str, ...] = ()
    required_env: tuple[str, ...] = ()
    optional_env: tuple[str, ...] = ()
    # Constructor argument -> environment variable. Credentials are handled separately.
    arguments: tuple[tuple[str, str], ...] = ()
    key_optional: bool = False
    restriction_aliases: tuple[str, ...] = ()
    # Required environment variable -> placeholder, with repeated names allowed.
    required_placeholders: tuple[tuple[str, str], ...] = ()

    def allowed_models_value(self) -> str | None:
        for name in (*self.restriction_aliases, self.restriction_env):
            value = get_env(name)
            if value:
                return value
        return None

    def effective_value(self, name: str) -> str | None:
        """Treat missing/blank/placeholder settings as absent; preserve real values."""
        value = get_env(name)
        if value is None or not value.strip():
            return None
        placeholders = self.placeholders if name == self.key_env else ()
        placeholders += tuple(placeholder for setting, placeholder in self.required_placeholders if setting == name)
        return None if value.strip() in placeholders else value

    def api_key(self) -> str | None:
        return self.effective_value(self.key_env)

    def is_configured(self) -> bool:
        return (self.key_optional or self.api_key() is not None) and all(
            self.effective_value(name) is not None for name in self.required_env
        )

    def constructor_kwargs(self) -> dict[str, str]:
        kwargs = {"api_key": self.api_key() or ""}
        for argument, env_name in self.arguments:
            value = self.effective_value(env_name)
            if value is not None:
                kwargs[argument] = value
        return kwargs

    def setup_diagnostics(self) -> ProviderSetupDiagnostics:
        """Classify declared keys/endpoints independently of catalogs and policy."""
        settings = (self.key_env, *self.required_env)
        values = {name: self.effective_value(name) for name in settings}
        required = (() if self.key_optional else (self.key_env,)) + self.required_env
        supplied = tuple(name for name in settings if values[name] is not None)
        missing = tuple(name for name in required if values[name] is None)
        status = "complete" if not missing else "incomplete" if supplied else "absent"
        return ProviderSetupDiagnostics(self.provider_type, self.label, status, supplied, missing)

    def setup_hint(self) -> str:
        required = (() if self.key_optional else (self.key_env,)) + self.required_env
        return " and ".join(required)

    def environment_names(self) -> tuple[str, ...]:
        return (self.key_env, self.restriction_env, *self.restriction_aliases, *self.required_env, *self.optional_env)


def provider_classes():
    """Return built-in declarations in routing priority order without constructing clients."""
    from .azure_openai import AzureOpenAIProvider
    from .custom import CustomProvider
    from .dial import DIALModelProvider
    from .gemini import GeminiModelProvider
    from .openai import OpenAIModelProvider
    from .opencode_go import OpenCodeGoProvider
    from .openrouter import OpenRouterProvider
    from .xai import XAIModelProvider

    return (
        GeminiModelProvider,
        OpenAIModelProvider,
        AzureOpenAIProvider,
        XAIModelProvider,
        DIALModelProvider,
        CustomProvider,
        OpenCodeGoProvider,
        OpenRouterProvider,
    )


def provider_configurations() -> dict[ProviderType, ProviderConfiguration]:
    return {provider.CONFIGURATION.provider_type: provider.CONFIGURATION for provider in provider_classes()}


def provider_setup_diagnostics() -> dict[ProviderType, ProviderSetupDiagnostics]:
    """Inspect the full declaration inventory without constructing provider clients."""
    return {kind: declaration.setup_diagnostics() for kind, declaration in provider_configurations().items()}
