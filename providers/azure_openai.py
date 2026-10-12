"""Azure OpenAI provider built on the OpenAI-compatible implementation."""

from __future__ import annotations

import logging
from dataclasses import asdict, replace

try:  # pragma: no cover - optional dependency
    from openai import AsyncAzureOpenAI
except ImportError:  # pragma: no cover
    AsyncAzureOpenAI = None  # type: ignore[assignment]

from utils.env import get_env

from .configuration import ProviderConfiguration
from .openai import OpenAIModelProvider
from .openai_compatible import OpenAICompatibleProvider
from .registries.azure import AzureModelRegistry
from .shared import ModelCapabilities, ModelResponse, ProviderType, TemperatureConstraint

logger = logging.getLogger(__name__)


class AzureOpenAIProvider(OpenAICompatibleProvider):
    """Thin Azure wrapper that reuses the OpenAI-compatible request pipeline."""

    CONFIGURATION = ProviderConfiguration(
        ProviderType.AZURE,
        "Azure OpenAI",
        "AZURE_OPENAI_API_KEY",
        "AZURE_ALLOWED_MODELS",
        placeholders=("your_azure_openai_key_here", "your_azure_openai_api_key_here"),
        required_env=("AZURE_OPENAI_ENDPOINT",),
        optional_env=("AZURE_OPENAI_API_VERSION", "AZURE_MODELS_CONFIG_PATH"),
        arguments=(("azure_endpoint", "AZURE_OPENAI_ENDPOINT"), ("api_version", "AZURE_OPENAI_API_VERSION")),
        restriction_aliases=("AZURE_OPENAI_ALLOWED_MODELS",),
    )

    FRIENDLY_NAME = "Azure OpenAI"
    REGISTRY_CLASS = AzureModelRegistry
    DEFAULT_API_VERSION = "2024-02-15-preview"

    # The OpenAI-compatible base expects subclasses to expose capabilities via
    # ``get_all_model_capabilities``.  Azure deployments are user-defined, so we
    # build the catalogue dynamically from environment configuration instead of
    # relying on a static ``MODEL_CAPABILITIES`` map.
    MODEL_CAPABILITIES: dict[str, ModelCapabilities] = {}

    def __init__(
        self,
        api_key: str,
        *,
        azure_endpoint: str | None = None,
        api_version: str | None = None,
        deployments: dict[str, object] | None = None,
        **kwargs,
    ) -> None:
        # Let the OpenAI-compatible base handle shared configuration such as
        # timeouts, restriction-aware allowlists, and logging. ``base_url`` maps
        # directly onto Azure's endpoint URL.
        super().__init__(api_key, base_url=azure_endpoint, **kwargs)

        if not azure_endpoint:
            azure_endpoint = get_env("AZURE_OPENAI_ENDPOINT")
        if not azure_endpoint:
            raise ValueError("Azure OpenAI endpoint is required via parameter or AZURE_OPENAI_ENDPOINT")

        self.azure_endpoint = azure_endpoint.rstrip("/")
        self.api_version = api_version or get_env("AZURE_OPENAI_API_VERSION", self.DEFAULT_API_VERSION)

        registry_specs = self._load_registry_entries()
        override_specs = self._normalise_deployments(deployments or {}) if deployments else {}

        self._model_specs = self._merge_specs(registry_specs, override_specs)
        if not self._model_specs:
            raise ValueError(
                "Azure OpenAI provider requires at least one configured deployment. "
                "Populate conf/azure_models.json or set AZURE_MODELS_CONFIG_PATH."
            )

        self._capabilities = self._build_capabilities_map()
        self._deployment_map = {name: spec["deployment"] for name, spec in self._model_specs.items()}
        deployments_seen: set[str] = set()
        for canonical, deployment in self._deployment_map.items():
            lowered = deployment.lower()
            registered = super()._resolve_model_name(deployment)
            if lowered in deployments_seen or (registered in self._capabilities and registered != canonical):
                raise ValueError(
                    f"Ambiguous Azure deployment '{deployment}': deployment IDs must not collide "
                    "with another model's canonical name, alias or deployment."
                )
            deployments_seen.add(lowered)
        self._deployment_alias_lookup = {
            deployment.lower(): canonical for canonical, deployment in self._deployment_map.items()
        }
        self._canonical_lookup = {name.lower(): name for name in self._model_specs.keys()}
        self._invalidate_capability_cache()

    # ------------------------------------------------------------------
    # Capability helpers
    # ------------------------------------------------------------------
    def get_all_model_capabilities(self) -> dict[str, ModelCapabilities]:
        return dict(self._capabilities)

    def get_provider_type(self) -> ProviderType:
        return ProviderType.AZURE

    def get_capabilities(self, model_name: str) -> ModelCapabilities:  # type: ignore[override]
        lowered = model_name.lower()
        if lowered in self._deployment_alias_lookup:
            canonical = self._deployment_alias_lookup[lowered]
            return super().get_capabilities(canonical)
        canonical = self._canonical_lookup.get(lowered)
        if canonical:
            return super().get_capabilities(canonical)
        return super().get_capabilities(model_name)

    def resolve_model_identity(self, model_name: str) -> str | None:
        lowered = model_name.strip().lower()
        if lowered in self._deployment_alias_lookup:
            return self._deployment_alias_lookup[lowered]
        return super().resolve_model_identity(model_name.strip())

    def _resolve_model_name(self, model_name: str) -> str:
        # The shared serializer receives deployment IDs. Preserve them even if
        # one is also an alias/canonical name of a different configured model.
        lowered = model_name.strip().lower()
        if lowered in self._deployment_alias_lookup:
            return self._deployment_map[self._deployment_alias_lookup[lowered]]
        return super()._resolve_model_name(model_name.strip())

    def _build_capabilities_map(self) -> dict[str, ModelCapabilities]:
        capabilities: dict[str, ModelCapabilities] = {}

        for canonical_name, spec in self._model_specs.items():
            template_capability: ModelCapabilities | None = spec.get("capability")
            overrides = spec.get("overrides", {})

            if template_capability:
                cloned = replace(template_capability)
            else:
                template = OpenAIModelProvider.MODEL_CAPABILITIES.get(canonical_name)

                if template:
                    friendly = template.friendly_name.replace("OpenAI", "Azure OpenAI", 1)
                    cloned = replace(
                        template,
                        provider=ProviderType.AZURE,
                        friendly_name=friendly,
                        aliases=list(template.aliases),
                    )
                else:
                    deployment_name = spec.get("deployment", "")
                    cloned = ModelCapabilities(
                        provider=ProviderType.AZURE,
                        model_name=canonical_name,
                        friendly_name=f"Azure OpenAI ({canonical_name})",
                        description=f"Azure deployment '{deployment_name}' for {canonical_name}",
                        aliases=[],
                    )

            if overrides:
                overrides = dict(overrides)
                temp_override = overrides.get("temperature_constraint")
                if isinstance(temp_override, str):
                    overrides["temperature_constraint"] = TemperatureConstraint.create(temp_override)

                aliases_override = overrides.get("aliases")
                if isinstance(aliases_override, str):
                    overrides["aliases"] = [alias.strip() for alias in aliases_override.split(",") if alias.strip()]
                provider_override = overrides.get("provider")
                if provider_override:
                    overrides.pop("provider", None)

                try:
                    cloned = replace(cloned, **overrides)
                except TypeError:
                    base_data = asdict(cloned)
                    base_data.update(overrides)
                    base_data["provider"] = ProviderType.AZURE
                    temp_value = base_data.get("temperature_constraint")
                    if isinstance(temp_value, str):
                        base_data["temperature_constraint"] = TemperatureConstraint.create(temp_value)
                    cloned = ModelCapabilities(**base_data)

            if cloned.provider != ProviderType.AZURE:
                cloned.provider = ProviderType.AZURE

            capabilities[canonical_name] = cloned

        return capabilities

    def _load_registry_entries(self) -> dict[str, dict]:
        registry = AzureModelRegistry()

        entries: dict[str, dict] = {}
        for model_name, capability, extra in registry.iter_entries():
            deployment = extra.get("deployment")
            if not deployment:
                logger.warning("Azure model '%s' missing deployment in registry", model_name)
                continue
            entries[model_name] = {"deployment": deployment, "capability": capability}

        return entries

    @staticmethod
    def _merge_specs(
        registry_specs: dict[str, dict],
        override_specs: dict[str, dict],
    ) -> dict[str, dict]:
        specs: dict[str, dict] = {}

        for canonical, entry in registry_specs.items():
            specs[canonical] = {
                "deployment": entry.get("deployment"),
                "capability": entry.get("capability"),
                "overrides": {},
            }

        for canonical, entry in override_specs.items():
            spec = specs.get(canonical, {"deployment": None, "capability": None, "overrides": {}})
            deployment = entry.get("deployment")
            if deployment:
                spec["deployment"] = deployment
            overrides = {k: v for k, v in entry.items() if k not in {"deployment"}}
            overrides.pop("capability", None)
            if overrides:
                spec["overrides"].update(overrides)
            specs[canonical] = spec

        return {k: v for k, v in specs.items() if v.get("deployment")}

    @staticmethod
    def _normalise_deployments(mapping: dict[str, object]) -> dict[str, dict]:
        normalised: dict[str, dict] = {}
        for canonical, spec in mapping.items():
            canonical_name = (canonical or "").strip()
            if not canonical_name:
                continue

            deployment_name: str | None = None
            overrides: dict[str, object] = {}

            if isinstance(spec, str):
                deployment_name = spec.strip()
            elif isinstance(spec, dict):
                deployment_name = spec.get("deployment") or spec.get("deployment_name")
                overrides = {k: v for k, v in spec.items() if k not in {"deployment", "deployment_name"}}

            if not deployment_name:
                continue

            normalised[canonical_name] = {"deployment": deployment_name.strip(), **overrides}

        return normalised

    # ------------------------------------------------------------------
    # Azure-specific configuration
    # ------------------------------------------------------------------
    async def _create_client(self):
        """Use the base initialization guard and explicit HTTP transport policy."""
        if AsyncAzureOpenAI is None:
            raise ImportError(
                "Azure OpenAI support requires the 'openai' package. Install it with `pip install openai`."
            )
        http_client = await self._create_http_client()
        client_kwargs = {
            "api_key": self.api_key,
            "azure_endpoint": self.azure_endpoint,
            "api_version": self.api_version,
            "http_client": http_client,
        }
        if self.DEFAULT_HEADERS:
            client_kwargs["default_headers"] = self.DEFAULT_HEADERS.copy()
        try:
            return AsyncAzureOpenAI(**client_kwargs)
        except Exception:
            await http_client.aclose()
            raise

    # ------------------------------------------------------------------
    # Request delegation
    # ------------------------------------------------------------------
    async def generate_content(
        self,
        prompt: str,
        model_name: str,
        system_prompt: str | None = None,
        temperature: float = 0.3,
        max_output_tokens: int | None = None,
        images: list[str] | None = None,
        **kwargs,
    ) -> ModelResponse:
        canonical_name, deployment_name = self._resolve_canonical_and_deployment(model_name)

        # Delegate to the shared OpenAI-compatible implementation using the
        # deployment name – Azure requires the deployment identifier in the
        # ``model`` field.  The returned ``ModelResponse`` is normalised so
        # downstream consumers continue to see the canonical model name.
        raw_response = await super().generate_content(
            prompt=prompt,
            model_name=deployment_name,
            system_prompt=system_prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            images=images,
            **kwargs,
        )

        capabilities = self._capabilities.get(canonical_name)
        friendly_name = capabilities.friendly_name if capabilities else self.FRIENDLY_NAME

        return ModelResponse(
            content=raw_response.content,
            usage=raw_response.usage,
            model_name=canonical_name,
            friendly_name=friendly_name,
            provider=ProviderType.AZURE,
            metadata={**raw_response.metadata, "deployment": deployment_name},
        )

    def _resolve_canonical_and_deployment(self, model_name: str) -> tuple[str, str]:
        resolved_canonical = self.resolve_model_identity(model_name)
        if resolved_canonical is None:
            raise ValueError(f"Model '{model_name}' is not configured for Azure OpenAI")

        if resolved_canonical not in self._deployment_map:
            # The base resolver may hand back the deployment alias. Try to map it
            # back to a canonical entry.
            for canonical, deployment in self._deployment_map.items():
                if deployment.lower() == resolved_canonical.lower():
                    return canonical, deployment
            raise ValueError(f"Model '{model_name}' is not configured for Azure OpenAI")

        return resolved_canonical, self._deployment_map[resolved_canonical]
