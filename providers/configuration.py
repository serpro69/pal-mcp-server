"""Declarative provider setup, shared by startup, discovery and test isolation."""

from dataclasses import dataclass

from utils.env import get_env

from .shared import ProviderType


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

    def allowed_models_value(self) -> str | None:
        for name in (*self.restriction_aliases, self.restriction_env):
            value = get_env(name)
            if value:
                return value
        return None

    def api_key(self) -> str | None:
        value = get_env(self.key_env)
        return value if value and value.strip() and value.strip() not in self.placeholders else None

    def is_configured(self) -> bool:
        return (self.key_optional or self.api_key() is not None) and all(
            (get_env(name) or "").strip() for name in self.required_env
        )

    def constructor_kwargs(self) -> dict[str, str]:
        kwargs = {"api_key": self.api_key() or ""}
        for argument, env_name in self.arguments:
            value = get_env(env_name)
            if value:
                kwargs[argument] = value
        return kwargs

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
    from .openrouter import OpenRouterProvider
    from .xai import XAIModelProvider

    return (
        GeminiModelProvider,
        OpenAIModelProvider,
        AzureOpenAIProvider,
        XAIModelProvider,
        DIALModelProvider,
        CustomProvider,
        OpenRouterProvider,
    )


def provider_configurations() -> dict[ProviderType, ProviderConfiguration]:
    return {provider.CONFIGURATION.provider_type: provider.CONFIGURATION for provider in provider_classes()}
