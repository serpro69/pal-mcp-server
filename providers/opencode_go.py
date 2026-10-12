"""Optional OpenCode Go adapter; per-request headers never mutate shared clients."""

from uuid import UUID

from config import __version__

from .configuration import ProviderConfiguration
from .openai_compatible import OpenAICompatibleProvider
from .registries.opencode_go import OpenCodeGoModelRegistry
from .registry_provider_mixin import RegistryBackedProviderMixin
from .shared import ModelCapabilities, ProviderType


class OpenCodeGoProvider(RegistryBackedProviderMixin, OpenAICompatibleProvider):
    CONFIGURATION = ProviderConfiguration(
        ProviderType.OPENCODE_GO,
        "OpenCode Go",
        "OPENCODE_GO_API_KEY",
        "OPENCODE_GO_ALLOWED_MODELS",
        placeholders=("your_opencode_go_api_key_here",),
        optional_env=("OPENCODE_GO_MODELS_CONFIG_PATH",),
    )
    FRIENDLY_NAME = "OpenCode Go"
    REGISTRY_CLASS = OpenCodeGoModelRegistry

    def __init__(self, api_key: str, **kwargs):
        kwargs.setdefault("base_url", "https://opencode.ai/zen/go/v1")
        self._ensure_registry()
        super().__init__(api_key, **kwargs)

    def get_provider_type(self) -> ProviderType:
        return ProviderType.OPENCODE_GO

    def _prepare_request_params(self, params: dict, capabilities: ModelCapabilities) -> dict:
        # Lazy import avoids coupling provider discovery to tool initialization.
        from utils.conversation_context import current_conversation_id

        thread_id = current_conversation_id()
        if not isinstance(thread_id, str):
            raise ValueError("OpenCode Go requires a PAL conversation; invoke it through a PAL tool")
        try:
            valid = str(UUID(thread_id)) == thread_id
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("OpenCode Go requires a canonical conversation UUID")

        prepared = dict(params)
        # PAL owns history locally. Do not request remote Responses storage.
        prepared.pop("store", None)
        prepared["extra_headers"] = {
            "x-opencode-session": thread_id,
            "User-Agent": f"pal-mcp-server/{__version__}",
        }
        return prepared
