"""OpenCode Go catalog with an explicit, validated endpoint contract."""

from ..shared import ProviderType
from .base import CapabilityModelRegistry


class OpenCodeGoModelRegistry(CapabilityModelRegistry):
    def __init__(self, config_path: str | None = None):
        super().__init__(
            env_var_name="OPENCODE_GO_MODELS_CONFIG_PATH",
            default_filename="opencode_go_models.json",
            provider=ProviderType.OPENCODE_GO,
            friendly_prefix="OpenCode Go ({model})",
            config_path=config_path,
        )

    def _extra_keys(self) -> set[str]:
        return {"api_protocol"}

    def _finalise_entry(self, entry):
        protocol = entry.get("api_protocol")
        if protocol not in ("chat_completions", "responses"):
            raise ValueError("OpenCode Go api_protocol must be chat_completions or responses; Messages is unsupported")
        responses = protocol == "responses"
        if "use_openai_response_api" in entry and entry["use_openai_response_api"] != responses:
            raise ValueError("api_protocol contradicts use_openai_response_api")
        expected_field = "max_output_tokens" if responses else "max_tokens"
        if entry.get("output_token_parameter", expected_field) != expected_field:
            raise ValueError("output_token_parameter contradicts api_protocol")
        entry = {**entry, "use_openai_response_api": responses, "output_token_parameter": expected_field}
        entry.setdefault("enabled_by_default", False)
        capability, _ = super()._finalise_entry(entry)
        return capability, {"api_protocol": protocol}
