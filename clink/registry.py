"""Configuration registry for clink CLI integrations."""

from __future__ import annotations

import json
import logging
import re
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from clink.constants import (
    BUILTIN_PROMPTS_DIR,
    CONFIG_DIR,
    INTERNAL_DEFAULTS,
    PROJECT_ROOT,
    USER_CONFIG_DIR,
    CLIInternalDefaults,
)
from clink.models import (
    CLIClientConfig,
    CLIRoleConfig,
    GenericCLIClientConfig,
    ResolvedCLIClient,
    ResolvedCLIRole,
)
from utils.env import get_env

logger = logging.getLogger("clink.registry")

CONFIG_ENV_VAR = "CLI_CLIENTS_CONFIG_PATH"


class RegistryLoadError(RuntimeError):
    """Raised when configuration files are invalid or missing critical data."""


def _member_label(name: str) -> str:
    """Keep a field identifiable when an operator pasted a NAME=value assignment."""
    key, separator, _ = name.partition("=")
    return f"{key}=[redacted]" if separator else key


def _unique_members(pairs: list[tuple[str, Any]]) -> dict:
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise RegistryLoadError(f"duplicate member {ascii(_member_label(key))}")
        obj[key] = value
    return obj


def _decode_manifest(document: str) -> Any:
    def reject_constant(token: str) -> None:
        # The decoder supplies no offset for nonstandard constants. Skip quoted
        # strings (including escaped quotes) to locate the actual token.
        tokens = re.finditer(r'"(?:\\.|[^"\\])*"|NaN|-?Infinity', document)
        position = next(match.start() for match in tokens if document.startswith(token, match.start()))
        raise json.JSONDecodeError("non-finite number", document, position)

    return json.loads(document, object_pairs_hook=_unique_members, parse_constant=reject_constant)


def _validation_reason(exc: ValidationError) -> str:
    # Pydantic's rendered exception, and even some built-in messages, contain input values.
    errors = exc.errors(include_input=False, include_url=False)
    if all(error["type"] == "missing" for error in errors):
        fields = ", ".join(".".join(_member_label(str(part)) for part in error["loc"]) for error in errors)
        return f"missing required fields: {fields}"
    error = errors[0]
    parts = list(error["loc"])
    if parts[-1:] == ["[key]"]:
        # An invalid environment key can contain a complete NAME=credential assignment.
        parts[-2:] = ["[key]"]
    elif error["type"] == "extra_forbidden" and not str(parts[-1]).isidentifier():
        parts[-1] = "[unknown field]"
    location = ".".join(_member_label(str(part)) for part in parts) or "manifest"
    reason = error["msg"] if error["type"] == "value_error" else error["type"]
    return f"invalid field {ascii(location)} ({reason})"


class ClinkRegistry:
    """Loads CLI client definitions and exposes them for schema generation/runtime use."""

    def __init__(self) -> None:
        self._clients: dict[str, ResolvedCLIClient] = {}
        self._load()

    def _load(self) -> None:
        candidate = {}
        try:
            for path in self._iter_config_files():
                client = self._load_client(path)
                key = client.name.casefold()
                if key in candidate:
                    logger.info("Overriding CLI configuration for %r from %r", _member_label(client.name), str(path))
                else:
                    logger.debug("Loaded CLI configuration for %r from %r", _member_label(client.name), str(path))
                candidate[key] = client
            if not candidate:
                raise RegistryLoadError(f"No CLI clients configured in {CONFIG_DIR} or {CONFIG_ENV_VAR}.")
        except RegistryLoadError as exc:
            logger.critical("Failed to load clink configuration: %s", exc)
            raise
        self._clients = candidate

    def reload(self) -> None:
        """Reload configurations from disk."""
        self._load()

    def list_clients(self) -> list[str]:
        return sorted(client.name for client in self._clients.values())

    def list_roles(self, cli_name: str) -> list[str]:
        config = self.get_client(cli_name)
        return sorted(config.roles.keys())

    def get_client(self, cli_name: str) -> ResolvedCLIClient:
        key = cli_name.casefold()
        if key not in self._clients:
            available = ", ".join(self.list_clients())
            raise KeyError(f"CLI '{cli_name}' is not configured. Available clients: {available}")
        return self._clients[key]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _iter_config_files(self) -> Iterable[Path]:
        sources = [(CONFIG_DIR, False)]
        env_path_raw = get_env(CONFIG_ENV_VAR)
        if env_path_raw:
            try:
                sources.append((Path(env_path_raw).expanduser(), False))
            except RuntimeError:
                raise RegistryLoadError(f"Cannot expand {CONFIG_ENV_VAR} path {ascii(env_path_raw)}") from None
        sources.append((USER_CONFIG_DIR, True))
        seen: set[Path] = set()
        for base, optional in sources:
            if base in seen:
                continue
            seen.add(base)
            location = base
            try:
                try:
                    mode = base.stat().st_mode
                except FileNotFoundError:
                    if optional and not base.is_symlink():
                        continue
                    raise RegistryLoadError(f"Configuration source missing: {ascii(str(base))}") from None
                if stat.S_ISDIR(mode):
                    # glob/is_file can silently suppress enumeration and broken-link errors.
                    for location in sorted(base.iterdir()):
                        if location.match("*.json") and not stat.S_ISDIR(location.stat().st_mode):
                            yield location
                elif stat.S_ISREG(mode) and base.suffix.lower() == ".json":
                    yield base
                else:
                    raise RegistryLoadError(f"Expected a JSON file or directory at {ascii(str(base))}")
            except OSError as exc:
                raise RegistryLoadError(
                    f"Cannot read configuration source {ascii(str(location))}: {type(exc).__name__}"
                ) from None

    def _load_client(self, path: Path) -> ResolvedCLIClient:
        try:
            if not stat.S_ISREG(path.stat().st_mode):
                raise RegistryLoadError("expected a regular JSON file")
            data = _decode_manifest(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not data:
                raise RegistryLoadError("expected a nonempty JSON object")
            name = data.get("name")
            if isinstance(name, str) and name.strip().casefold() in INTERNAL_DEFAULTS:
                kind = "shipped"
                if "custom" in data:
                    raise RegistryLoadError("field 'custom' cannot replace a shipped adapter")
                raw = CLIClientConfig.model_validate(data)
            else:
                kind = "generic"
                raw = GenericCLIClientConfig.model_validate(data)
            return self._resolve_config(raw, source_path=path)
        except json.JSONDecodeError as exc:
            reason = f"invalid JSON at line {exc.lineno}, column {exc.colno}"
            if exc.msg.startswith("Unexpected UTF-8 BOM"):
                reason += " (remove the UTF-8 byte-order mark)"
        except UnicodeError:
            reason = "invalid text encoding (expected UTF-8)"
        except OSError as exc:
            reason = f"cannot read file ({type(exc).__name__})"
        except ValidationError as exc:
            reason = f"{kind} manifest: {_validation_reason(exc)}"
        except RegistryLoadError as exc:
            reason = str(exc)
        except (RecursionError, ValueError):
            reason = "invalid JSON value"
        raise RegistryLoadError(f"{ascii(str(path))}: {reason}") from None

    def _resolve_config(self, raw: CLIClientConfig | GenericCLIClientConfig, *, source_path: Path) -> ResolvedCLIClient:
        normalized_name = raw.name.strip()
        generic = isinstance(raw, GenericCLIClientConfig)
        defaults = (
            CLIInternalDefaults(parser="generic", runner="generic")
            if generic
            else INTERNAL_DEFAULTS[normalized_name.casefold()]
        )
        try:
            executable = raw.executable_tokens()
        except ValueError as exc:
            raise RegistryLoadError(str(exc)) from None
        try:
            working_dir = self._resolve_path(raw.working_dir, source_path.parent) if raw.working_dir else None
        except (OSError, RuntimeError, ValueError):
            raise RegistryLoadError("cannot resolve field 'working_dir'") from None

        return ResolvedCLIClient(
            name=normalized_name,
            executable=executable,
            model_args=list(raw.model_args),
            default_model=raw.default_model,
            internal_args=defaults.additional_args,
            config_args=raw.additional_args,
            safe_args=list(raw.safe_args),
            edit_args=list(raw.edit_args),
            env={**defaults.env, **raw.env},
            env_passthrough=list(dict.fromkeys((*defaults.env_passthrough, *raw.env_passthrough))),
            timeout_seconds=raw.timeout_seconds or defaults.timeout_seconds,
            history_token_budget=raw.history_token_budget,
            parser=defaults.parser,
            runner=defaults.runner,
            custom=raw.custom if generic else None,
            roles=self._resolve_roles(raw, defaults, source_path),
            output_to_file=None if generic else raw.output_to_file,
            working_dir=working_dir,
        )

    def _resolve_roles(
        self,
        raw: CLIClientConfig | GenericCLIClientConfig,
        defaults: CLIInternalDefaults,
        source_path: Path,
    ) -> dict[str, ResolvedCLIRole]:
        resolved: dict[str, ResolvedCLIRole] = {}
        for role_name, role_config in {"default": CLIRoleConfig(), **raw.roles}.items():
            prompt = role_config.prompt_path or defaults.default_role_prompt
            try:
                path = (
                    self._resolve_path(prompt, source_path.parent)
                    if prompt
                    else BUILTIN_PROMPTS_DIR / "generic_default.txt"
                )
                if not path.is_file():
                    raise FileNotFoundError
            except (OSError, RuntimeError, ValueError):
                raise RegistryLoadError(
                    f"cannot resolve file for roles.{ascii(_member_label(role_name))}.prompt_path"
                ) from None
            resolved[role_name] = ResolvedCLIRole(
                name=role_name,
                prompt_path=path,
                role_args=list(role_config.role_args),
                description=role_config.description,
            )
        return resolved

    def _resolve_path(self, candidate: str, base_dir: Path) -> Path:
        path = Path(candidate)
        if path.is_absolute():
            return path

        candidate_path = (base_dir / path).resolve()
        if candidate_path.exists():
            return candidate_path

        project_relative = (PROJECT_ROOT / path).resolve()
        return project_relative


_REGISTRY: ClinkRegistry | None = None


def get_registry() -> ClinkRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ClinkRegistry()
    return _REGISTRY
