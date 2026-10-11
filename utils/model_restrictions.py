"""
Model Restriction Service

This module provides centralized management of model usage restrictions
based on environment variables. It allows organizations to limit which
models can be used from each provider for cost control, compliance, or
standardization purposes.

Environment Variables:
- OPENAI_ALLOWED_MODELS: Comma-separated list of allowed OpenAI models
- GOOGLE_ALLOWED_MODELS: Comma-separated list of allowed Gemini models
- XAI_ALLOWED_MODELS: Comma-separated list of allowed X.AI GROK models
- OPENROUTER_ALLOWED_MODELS: Comma-separated list of allowed OpenRouter models
- DIAL_ALLOWED_MODELS: Comma-separated list of allowed DIAL models
- DISABLED_MODELS: Global canonical/alias deny override, optionally provider-scoped

Example:
    OPENAI_ALLOWED_MODELS=o3-mini,o4-mini
    GOOGLE_ALLOWED_MODELS=flash
    XAI_ALLOWED_MODELS=grok-4,grok-4.1-fast-reasoning
    OPENROUTER_ALLOWED_MODELS=opus,sonnet,mistral
"""

import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Optional

from providers.configuration import provider_configurations
from providers.shared import ProviderType
from utils.env import get_env

if TYPE_CHECKING:
    from providers.base import ModelProvider
    from providers.shared import ModelCapabilities

logger = logging.getLogger(__name__)


class ModelRestrictionService:
    """Central authority for environment-driven model allowlists and denies.

    Role
        Interpret ``*_ALLOWED_MODELS`` environment variables, keep their
        entries normalised (lowercase), and answer whether a provider/model
        pairing is permitted.

    Responsibilities
        * Parse, cache, and expose per-provider restriction sets
        * Validate configuration by cross-checking each entry against the
          provider’s alias-aware model list
        * Offer helper methods such as ``is_allowed`` and ``filter_models`` to
          enforce policy everywhere model names appear (tool selection, CLI
          commands, etc.).
    """

    # Environment variable names
    ENV_VARS = {kind: config.restriction_env for kind, config in provider_configurations().items()}

    def __init__(self):
        """Initialize the restriction service by loading from environment."""
        self.restrictions: dict[ProviderType, set[str]] = {}
        self.disabled_models: list[tuple[Optional[ProviderType], str]] = []
        self._alias_resolution_cache: dict[ProviderType, dict[str, str]] = defaultdict(dict)
        self._load_from_env()
        # Keep operator opt-ins separate from canonical names cached by the
        # legacy alias-aware allowlist matcher.
        self._configured_allowlists = {kind: frozenset(names) for kind, names in self.restrictions.items()}
        self.preferred_models = tuple(
            entry.strip().lower() for entry in (get_env("PREFERRED_MODELS") or "").split(",") if entry.strip()
        )

    def _load_from_env(self) -> None:
        """Load restrictions from environment variables."""
        scopes = {kind.value: kind for kind in provider_configurations()}
        for entry in (get_env("DISABLED_MODELS") or "").split(","):
            entry = entry.strip().lower()
            if not entry:
                continue
            prefix, separator, remainder = entry.partition(":")
            scope = scopes.get(prefix) if separator else None
            # Only the first recognized provider prefix is syntax. Every other
            # colon belongs to the complete model identity, including :for.
            self.disabled_models.append((scope, remainder.strip() if scope else entry))

        for provider_type, env_var in self.ENV_VARS.items():
            env_value = provider_configurations()[provider_type].allowed_models_value()

            if env_value is None or env_value == "":
                # Not set or empty - no restrictions (allow all models)
                logger.debug(f"{env_var} not set or empty - all {provider_type.value} models allowed")
                continue

            # Parse comma-separated list
            models = set()
            for model in env_value.split(","):
                cleaned = model.strip().lower()
                if cleaned:
                    models.add(cleaned)

            if models:
                self.restrictions[provider_type] = models
                self._alias_resolution_cache[provider_type] = {}
                logger.info(f"{provider_type.value} allowed models: {sorted(models)}")
            else:
                # All entries were empty after cleaning - treat as no restrictions
                logger.debug(f"{env_var} contains only whitespace - all {provider_type.value} models allowed")

    def validate_against_known_models(self, provider_instances: dict[ProviderType, any]) -> None:
        """
        Validate restrictions against known models from providers.

        This should be called after providers are initialized to warn about
        typos or invalid model names in the restriction lists.

        Args:
            provider_instances: Dictionary of provider type to provider instance
        """
        for position, (scope, entry) in enumerate(self.disabled_models, start=1):
            if not any(
                provider.resolve_model_identity(entry)
                for kind, provider in provider_instances.items()
                if scope is None or scope == kind
            ):
                # A typo may actually be a pasted credential; identify the
                # entry by position instead of echoing unrecognized input.
                logger.warning(
                    "DISABLED_MODELS entry %d does not resolve in the configured provider catalogs; "
                    "check its provider scope and complete model name",
                    position,
                )

        for provider_type, allowed_models in self.restrictions.items():
            provider = provider_instances.get(provider_type)
            if not provider:
                continue

            # Get all supported models using the clean polymorphic interface
            try:
                # Gather canonical models and aliases with consistent formatting
                all_models = provider.list_models(
                    respect_restrictions=False,
                    include_aliases=True,
                    lowercase=True,
                    unique=True,
                )
                supported_models = set(all_models)
            except Exception as e:
                logger.debug(f"Could not get model list from {provider_type.value} provider: {e}")
                supported_models = set()

            # Check each allowed model
            for allowed_model in allowed_models:
                if allowed_model not in supported_models:
                    logger.warning(
                        f"Model '{allowed_model}' in {self.ENV_VARS[provider_type]} "
                        f"is not a recognized {provider_type.value} model. "
                        f"Please check for typos. Known models: {sorted(supported_models)}"
                    )

    def is_allowed(
        self,
        provider_type: ProviderType,
        model_name: str,
        original_name: Optional[str] = None,
        *,
        provider: Optional["ModelProvider"] = None,
    ) -> bool:
        """Apply the provider allowlist, then the global canonical deny override."""
        return self._is_allowlisted(
            provider_type, model_name, original_name, provider=provider
        ) and not self.is_disabled(provider_type, model_name, provider=provider)

    def is_disabled(
        self,
        provider_type: ProviderType,
        model_name: str,
        *,
        provider: Optional["ModelProvider"] = None,
    ) -> bool:
        """Match denies against this provider's full catalog, without option parsing."""
        entries = [entry for scope, entry in self.disabled_models if scope is None or scope == provider_type]
        if not entries:
            return False

        from providers.registry import ModelProviderRegistry

        if provider is None:
            provider = ModelProviderRegistry.get_provider(provider_type)
        canonical = provider.resolve_model_identity(model_name) if provider else None
        target = (canonical or model_name).strip().lower()
        for entry in entries:
            resolved = provider.resolve_model_identity(entry) if provider else None
            if target == (resolved or entry).lower():
                return True
        return False

    def is_discoverable(self, provider: "ModelProvider", capabilities: "ModelCapabilities") -> bool:
        """Apply discovery defaults and explicit canonical opt-ins, independently of denies."""
        if capabilities.enabled_by_default:
            return True
        canonical = capabilities.model_name.lower()
        if canonical in self._configured_allowlists.get(provider.get_provider_type(), ()):
            return True
        if canonical not in self.preferred_models:
            return False

        from providers.registry import ModelProviderRegistry

        identity = ModelProviderRegistry.resolve_model_identity(canonical, catalog_only=True)
        return identity is not None and identity[0] is provider and identity[1].lower() == canonical

    def _is_allowlisted(
        self,
        provider_type: ProviderType,
        model_name: str,
        original_name: Optional[str] = None,
        *,
        provider: Optional["ModelProvider"] = None,
    ) -> bool:
        """
        Check if a model is allowed for a specific provider.

        Args:
            provider_type: The provider type (OPENAI, GOOGLE, etc.)
            model_name: The canonical model name (after alias resolution)
            original_name: The original model name before alias resolution (optional)

        Returns:
            True if allowed (or no restrictions), False if restricted
        """
        if provider_type not in self.restrictions:
            # No restrictions for this provider
            return True

        allowed_set = self.restrictions[provider_type]

        if len(allowed_set) == 0:
            # Empty set - allowed
            return True

        # Check both the resolved name and original name (if different)
        names_to_check = {model_name.lower()}
        if original_name and original_name.lower() != model_name.lower():
            names_to_check.add(original_name.lower())

        # If any of the names is in the allowed set, it's allowed
        if any(name in allowed_set for name in names_to_check):
            return True

        # Attempt to resolve canonical names for allowed aliases using provider metadata.
        try:
            from providers.registry import ModelProviderRegistry

            if provider is None:
                provider = ModelProviderRegistry.get_provider(provider_type)
        except Exception:  # pragma: no cover - registry lookup failure shouldn't break validation
            provider = None

        if provider:
            cache = self._alias_resolution_cache.setdefault(provider_type, {})

            for allowed_entry in list(allowed_set):
                normalized_resolved = cache.get(allowed_entry)

                if not normalized_resolved:
                    try:
                        resolved = provider.resolve_model_identity(allowed_entry)
                    except Exception:  # pragma: no cover - resolution failures are treated as non-matches
                        continue

                    if not resolved:
                        continue

                    normalized_resolved = resolved.lower()
                    cache[allowed_entry] = normalized_resolved

                if normalized_resolved in names_to_check:
                    allowed_set.add(normalized_resolved)
                    cache[normalized_resolved] = normalized_resolved
                    return True

        return False

    def get_allowed_models(self, provider_type: ProviderType) -> Optional[set[str]]:
        """
        Get the set of allowed models for a provider.

        Args:
            provider_type: The provider type

        Returns:
            Set of allowed model names, or None if no restrictions
        """
        return self.restrictions.get(provider_type)

    def has_restrictions(self, provider_type: ProviderType) -> bool:
        """
        Check if a provider has any restrictions.

        Args:
            provider_type: The provider type

        Returns:
            True if restrictions exist, False otherwise
        """
        return provider_type in self.restrictions or any(
            scope is None or scope == provider_type for scope, _ in self.disabled_models
        )

    def filter_models(self, provider_type: ProviderType, models: list[str]) -> list[str]:
        """
        Filter a list of models based on restrictions.

        Args:
            provider_type: The provider type
            models: List of model names to filter

        Returns:
            Filtered list containing only allowed models
        """
        if not self.has_restrictions(provider_type):
            return models

        return [m for m in models if self.is_allowed(provider_type, m)]

    def get_restriction_summary(self) -> dict[str, any]:
        """
        Get a summary of all restrictions for logging/debugging.

        Returns:
            Dictionary with provider names and their restrictions
        """
        summary = {}
        for provider_type, allowed_set in self.restrictions.items():
            if allowed_set:
                summary[provider_type.value] = sorted(allowed_set)
            else:
                summary[provider_type.value] = "none (provider disabled)"

        return summary


# Global instance (singleton pattern)
_restriction_service: Optional[ModelRestrictionService] = None


def get_restriction_service() -> ModelRestrictionService:
    """
    Get the global restriction service instance.

    Returns:
        The singleton ModelRestrictionService instance
    """
    global _restriction_service
    if _restriction_service is None:
        _restriction_service = ModelRestrictionService()
    return _restriction_service
