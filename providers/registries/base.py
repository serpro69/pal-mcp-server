"""Shared infrastructure for JSON-backed model registries."""

from __future__ import annotations

import importlib.resources
import json
import logging
import math
from collections.abc import Iterable
from dataclasses import fields
from pathlib import Path
from typing import Optional

from utils.env import get_env

from ..errors import RegistryConfigurationError
from ..shared import ModelCapabilities, ProviderType, TemperatureConstraint

logger = logging.getLogger(__name__)


CAPABILITY_FIELD_NAMES = {field.name for field in fields(ModelCapabilities)}


class CustomModelRegistryBase:
    """Load and expose capability metadata from a JSON manifest."""

    OPTIONAL = False

    def __init__(
        self,
        *,
        env_var_name: str,
        default_filename: str,
        config_path: str | None = None,
    ) -> None:
        self._env_var_name = env_var_name
        self._default_filename = default_filename
        self._use_resources = False
        self._resource_package = "conf"
        self._default_path = Path(__file__).resolve().parents[3] / "conf" / default_filename
        self._explicit_path = bool(config_path or get_env(env_var_name))

        if config_path:
            self.config_path = Path(config_path)
        else:
            env_path = get_env(env_var_name)
            if env_path:
                self.config_path = Path(env_path)
            else:
                try:
                    resource = importlib.resources.files(self._resource_package).joinpath(default_filename)
                    if hasattr(resource, "read_text"):
                        self._use_resources = True
                        self.config_path = None
                    else:
                        raise AttributeError("resource accessor not available")
                except Exception:
                    self.config_path = Path(__file__).resolve().parents[3] / "conf" / default_filename

        self.alias_map: dict[str, str] = {}
        self.model_map: dict[str, ModelCapabilities] = {}
        self._extras: dict[str, dict] = {}

    def reload(self) -> None:
        data = self._load_config_data()
        previous_extras = self._extras
        previous_models, previous_aliases = self.model_map, self.alias_map
        self._extras = {}
        try:
            if not isinstance(data, dict) or not isinstance(data.get("models"), list):
                raise ValueError("Expected an object containing a 'models' array")
            if not data["models"] and not self.OPTIONAL:
                raise ValueError("Required model registry must not be empty")
            configs = list(self._parse_models(data))
            self._build_maps(configs)
        except (ValueError, TypeError, OverflowError) as exc:
            self._extras = previous_extras
            self.model_map, self.alias_map = previous_models, previous_aliases
            raise RegistryConfigurationError(self.source_name, "schema", str(exc)) from exc

    @property
    def source_name(self) -> str:
        return str(self.config_path) if self.config_path else f"{self._resource_package}/{self._default_filename}"

    def list_models(self) -> list[str]:
        return list(self.model_map.keys())

    def list_aliases(self) -> list[str]:
        return list(self.alias_map.keys())

    def resolve(self, name_or_alias: str) -> ModelCapabilities | None:
        key = name_or_alias.lower()
        canonical = self.alias_map.get(key)
        if canonical:
            return self.model_map.get(canonical)

        for model_name in self.model_map:
            if model_name.lower() == key:
                return self.model_map[model_name]
        return None

    def get_capabilities(self, name_or_alias: str) -> ModelCapabilities | None:
        return self.resolve(name_or_alias)

    def get_entry(self, model_name: str) -> dict | None:
        return self._extras.get(model_name)

    def get_model_config(self, model_name: str) -> ModelCapabilities | None:
        """Backwards-compatible accessor for registries expecting this helper."""

        return self.model_map.get(model_name) or self.resolve(model_name)

    def iter_entries(self) -> Iterable[tuple[str, ModelCapabilities, dict]]:
        for model_name, capability in self.model_map.items():
            yield model_name, capability, self._extras.get(model_name, {})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _load_config_data(self) -> dict:
        try:
            if self._use_resources:
                resource = importlib.resources.files(self._resource_package).joinpath(self._default_filename)
                config_text = resource.read_text(encoding="utf-8")
            else:
                if not self.config_path:
                    raise FileNotFoundError("Registry configuration path is not set")
                if not self._explicit_path and not self.config_path.exists():
                    fallback = Path.cwd() / "conf" / self._default_filename
                    if fallback != self.config_path and fallback.exists():
                        self.config_path = fallback
                config_text = self.config_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            if self.OPTIONAL and not self._explicit_path:
                return {"models": []}
            raise RegistryConfigurationError(self.source_name, "missing", "File not found") from exc
        except (OSError, UnicodeError) as exc:
            raise RegistryConfigurationError(self.source_name, "io", "Unable to read UTF-8 registry") from exc
        try:
            return json.loads(config_text)
        except json.JSONDecodeError as exc:
            raise RegistryConfigurationError(
                self.source_name, "json", f"Invalid JSON at line {exc.lineno}, column {exc.colno}"
            ) from exc
        except ValueError as exc:
            raise RegistryConfigurationError(self.source_name, "json", "JSON value exceeds decoder limits") from exc

    @property
    def use_resources(self) -> bool:
        return self._use_resources

    def _parse_models(self, data: dict) -> Iterable[ModelCapabilities | None]:
        for raw in data.get("models", []):
            if not isinstance(raw, dict):
                raise ValueError("Every model entry must be an object")
            yield self._convert_entry(raw)

    def _convert_entry(self, raw: dict) -> ModelCapabilities | None:
        entry = dict(raw)
        model_name = entry.get("model_name")
        if not isinstance(model_name, str) or not model_name.strip() or model_name != model_name.strip():
            raise ValueError("Every model needs a nonempty model_name without surrounding whitespace")

        aliases = entry.get("aliases")
        if isinstance(aliases, str):
            entry["aliases"] = [alias.strip() for alias in aliases.split(",") if alias.strip()]
        elif aliases is not None and (
            not isinstance(aliases, list)
            or any(not isinstance(alias, str) or not alias.strip() or alias != alias.strip() for alias in aliases)
        ):
            raise ValueError("aliases must be a list of nonempty names or a comma-separated string")
        elif "aliases" in entry and aliases is None:
            raise ValueError("aliases must not be null")

        for field in fields(ModelCapabilities):
            if field.name not in entry or field.name in {"provider", "temperature_constraint", "aliases"}:
                continue
            value = entry[field.name]
            if field.type in (str, int, bool) and type(value) is not field.type:
                raise ValueError(f"Invalid type for {field.name}")
            if field.type == Optional[str] and value is not None and not isinstance(value, str):
                raise ValueError(f"Invalid type for {field.name}")
            if (
                field.type == Optional[list[str]]
                and value is not None
                and (not isinstance(value, list) or any(not isinstance(item, str) for item in value))
            ):
                raise ValueError(f"Invalid type for {field.name}")
            if field.type is int and value < 0:
                raise ValueError(f"Invalid negative value for {field.name}")
            if field.type is float and (type(value) not in (float, int) or not math.isfinite(value) or value < 0):
                raise ValueError(f"Invalid nonnegative number for {field.name}")

        entry.setdefault("friendly_name", self._default_friendly_name(model_name))
        if "provider" in entry:
            ProviderType(entry["provider"])

        temperature_hint = entry.get("temperature_constraint")
        if isinstance(temperature_hint, str):
            if temperature_hint not in {"fixed", "discrete", "range"}:
                raise ValueError("temperature_constraint must be fixed, discrete or range")
            entry["temperature_constraint"] = TemperatureConstraint.create(temperature_hint)
        elif temperature_hint is None:
            entry["temperature_constraint"] = TemperatureConstraint.create("range")
        else:
            raise ValueError("temperature_constraint must be a supported string")

        if "max_tokens" in entry:
            raise ValueError(
                "`max_tokens` is no longer supported. Use `max_output_tokens` in your model configuration."
            )

        unknown_keys = set(entry.keys()) - CAPABILITY_FIELD_NAMES - self._extra_keys()
        if unknown_keys:
            raise ValueError("Unsupported fields in model configuration: " + ", ".join(sorted(unknown_keys)))

        capability, extras = self._finalise_entry(entry)
        capability.provider = self._provider_default()
        self._extras[capability.model_name] = extras or {}
        return capability

    def _default_friendly_name(self, model_name: str) -> str:
        return model_name

    def _extra_keys(self) -> set[str]:
        return set()

    def _provider_default(self) -> ProviderType:
        return ProviderType.OPENROUTER

    def _finalise_entry(self, entry: dict) -> tuple[ModelCapabilities, dict]:
        return ModelCapabilities(**{k: v for k, v in entry.items() if k in CAPABILITY_FIELD_NAMES}), {}

    def _build_maps(self, configs: Iterable[ModelCapabilities]) -> None:
        alias_map: dict[str, str] = {}
        model_map: dict[str, ModelCapabilities] = {}

        for config in configs:
            if not config:
                continue
            lowered = config.model_name.lower()
            if lowered in alias_map:
                raise ValueError(f"Duplicate alias or canonical model name '{config.model_name}'")
            model_map[config.model_name] = config

            model_name_lower = config.model_name.lower()
            if model_name_lower not in alias_map:
                alias_map[model_name_lower] = config.model_name

            for alias in config.aliases:
                alias_lower = alias.lower()
                if alias_lower in alias_map and alias_map[alias_lower] != config.model_name:
                    raise ValueError(
                        f"Duplicate alias '{alias}' found for models '{alias_map[alias_lower]}' and '{config.model_name}'"
                    )
                alias_map[alias_lower] = config.model_name

        self.alias_map = alias_map
        self.model_map = model_map


class CapabilityModelRegistry(CustomModelRegistryBase):
    """Registry that returns :class:`ModelCapabilities` objects with alias support."""

    def __init__(
        self,
        *,
        env_var_name: str,
        default_filename: str,
        provider: ProviderType,
        friendly_prefix: str,
        config_path: str | None = None,
    ) -> None:
        self._provider = provider
        self._friendly_prefix = friendly_prefix
        super().__init__(
            env_var_name=env_var_name,
            default_filename=default_filename,
            config_path=config_path,
        )
        self.reload()

    def _provider_default(self) -> ProviderType:
        return self._provider

    def _default_friendly_name(self, model_name: str) -> str:
        return self._friendly_prefix.format(model=model_name)

    def _finalise_entry(self, entry: dict) -> tuple[ModelCapabilities, dict]:
        filtered = {k: v for k, v in entry.items() if k in CAPABILITY_FIELD_NAMES}
        filtered.setdefault("provider", self._provider_default())
        capability = ModelCapabilities(**filtered)
        return capability, {}
