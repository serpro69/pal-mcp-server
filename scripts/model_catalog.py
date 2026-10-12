"""Validated, bounded inputs shared by offline model maintenance commands."""

import argparse
import json
from datetime import date
from http.client import HTTPException
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

from providers.configuration import provider_classes
from providers.errors import RegistryConfigurationError
from providers.registries.base import CAPABILITY_FIELD_NAMES
from providers.shared import ProviderType

MAX_INPUT_BYTES = 16 * 1024 * 1024
MODELS_DEV_URL = "https://models.dev/api.json"
EVIDENCE_FIELDS = CAPABILITY_FIELD_NAMES - {
    "provider",
    "model_name",
    "friendly_name",
    "description",
    "aliases",
    "intelligence_score",
    "enabled_by_default",
    "allow_code_generation",
}
EVIDENCE_FIELDS.add("api_protocol")  # Provider-specific registries validate supported values.


class CatalogInputError(ValueError):
    """An input is missing, malformed or explicitly incomplete."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise CatalogInputError("Duplicate JSON object key")
        result[key] = value
    return result


def decode_document(raw: bytes) -> dict:
    if len(raw) > MAX_INPUT_BYTES:
        raise CatalogInputError("Input exceeds the 16 MiB limit")
    try:
        data = json.loads(raw, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise CatalogInputError("Input is not valid unambiguous JSON") from exc
    if not isinstance(data, dict):
        raise CatalogInputError("Expected a JSON object")
    return data


def read_document(path: Path) -> dict:
    try:
        with path.open("rb") as stream:
            return decode_document(stream.read(MAX_INPUT_BYTES + 1))
    except OSError as exc:
        raise CatalogInputError(f"Unable to read {path.name}") from exc


def fetch_models_dev() -> dict:
    """Fetch only the public catalog, with a timeout and an actual-byte bound."""
    try:
        with urlopen(MODELS_DEV_URL, timeout=15) as response:
            return decode_document(response.read(MAX_INPUT_BYTES + 1))
    except (OSError, URLError, HTTPException) as exc:
        raise CatalogInputError("models.dev fetch failed; audit is incomplete") from exc


def validate_registry(document: dict, provider: str) -> dict:
    """Use PAL's provider-specific schema and alias validation without modifying input."""
    factory = next(cls for cls in provider_classes() if cls.CONFIGURATION.provider_type.value == provider)
    try:
        with TemporaryDirectory(prefix="pal-catalog-") as directory:
            path = Path(directory) / "registry.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            registry = factory.REGISTRY_CLASS(str(path))
            return dict(registry.model_map)
    except RegistryConfigurationError as exc:
        detail = str(exc).replace(str(path), "input registry")
        raise CatalogInputError(f"Invalid {provider} registry: {detail}") from exc


def _iso_date(value: object, label: str) -> date:
    if not isinstance(value, str):
        raise CatalogInputError(f"{label} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise CatalogInputError(f"{label} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise CatalogInputError(f"{label} must be an ISO date")
    return parsed


def read_evidence(document: dict, provider: str, as_of: date) -> dict[str, dict]:
    """Read an operator-reviewed provider snapshot, never community data as authority."""
    required = {"schema_version", "provider", "source_url", "observed_on", "complete", "models"}
    if set(document) != required or type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise CatalogInputError("Provider evidence must use the documented version 1 fields")
    if document["provider"] != provider or document["complete"] is not True:
        raise CatalogInputError("Provider evidence is mismatched or incomplete")
    source = document["source_url"]
    if not isinstance(source, str):
        raise CatalogInputError("Provider evidence needs an HTTPS source_url")
    try:
        parsed = urlsplit(source)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Invalid evidence URL")
        if parsed.hostname == "models.dev" or parsed.hostname.endswith(".models.dev"):
            raise ValueError("Community data is not provider evidence")
    except ValueError as exc:
        raise CatalogInputError("Provider evidence needs a credential-free provider HTTPS source_url") from exc
    if _iso_date(document["observed_on"], "observed_on") > as_of:
        raise CatalogInputError("Provider evidence is newer than the audit date")
    records = document["models"]
    if not isinstance(records, list) or not records:
        raise CatalogInputError("Provider evidence needs a nonempty models array")
    models = {}
    for record in records:
        if not isinstance(record, dict) or set(record) - {"id", "status", "expires_on", "capabilities"}:
            raise CatalogInputError("Unknown provider evidence model fields")
        name = record.get("id")
        if not isinstance(name, str) or not name.strip() or name != name.strip() or name.lower() in models:
            raise CatalogInputError("Provider evidence model IDs must be nonempty and unique")
        if not isinstance(record.get("status"), str) or record["status"] not in {"active", "deprecated", "retired"}:
            raise CatalogInputError("Provider evidence status must be active, deprecated or retired")
        if "expires_on" in record:
            _iso_date(record["expires_on"], "expires_on")
        capabilities = record.get("capabilities", {})
        if not isinstance(capabilities, dict) or set(capabilities) - EVIDENCE_FIELDS:
            raise CatalogInputError("Provider evidence contains unknown or human-curated capability fields")
        # Validate declared types/field names with the same runtime parser.
        entry = {"model_name": name, **capabilities}
        if provider == "azure":
            entry["deployment"] = name  # Schema-only placeholder; never emitted.
        validate_registry({"models": [entry]}, provider)
        models[name.lower()] = record
    return models


def read_community(document: dict, provider: str) -> dict[str, dict]:
    """Consume the documented models.dev api.json provider/model mapping."""
    section = document.get(provider)
    if not isinstance(section, dict) or section.get("id") != provider:
        raise CatalogInputError("models.dev provider is missing or malformed; audit is incomplete")
    models = section.get("models")
    if not isinstance(models, dict) or not models:
        raise CatalogInputError("models.dev model mapping is missing or empty; audit is incomplete")
    result = {}
    for name, record in models.items():
        if not isinstance(record, dict) or record.get("id") != name or name.lower() in result:
            raise CatalogInputError("models.dev model IDs are malformed or duplicated")
        if "status" in record and (
            not isinstance(record["status"], str) or record["status"] not in {"alpha", "beta", "deprecated"}
        ):
            raise CatalogInputError("Unknown models.dev status; update the reader before accepting this audit")
        limits = record.get("limit", {})
        if not isinstance(limits, dict):
            raise CatalogInputError("models.dev limits must be an object")
        for key in ("context", "output"):
            if key in limits and (type(limits[key]) is not int or limits[key] <= 0):
                raise CatalogInputError("models.dev capacities must be positive integers")
        result[name.lower()] = record
    return result


def retirement(record: dict, as_of: date) -> str | None:
    if record["status"] in {"deprecated", "retired"}:
        return record["status"]
    if "expires_on" in record and _iso_date(record["expires_on"], "expires_on") <= as_of:
        return "expired"
    return None


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--provider", required=True, choices=[kind.value for kind in ProviderType])
    parser.add_argument("--evidence", required=True, type=Path, help="Reviewed provider evidence snapshot")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today(), help="Reproducible YYYY-MM-DD date")


def incomplete_report(error: CatalogInputError) -> dict:
    return {"schema_version": 1, "status": "incomplete", "complete": False, "errors": [str(error)]}
