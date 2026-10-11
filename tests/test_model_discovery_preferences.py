"""Discovery visibility and ordered auto preferences preserve invocation identity."""

import json

import pytest

from providers.custom import CustomProvider
from providers.errors import RegistryConfigurationError
from providers.openrouter import OpenRouterProvider
from providers.registries.custom import CustomEndpointModelRegistry
from providers.registries.openrouter import OpenRouterModelRegistry
from providers.registry import ModelProviderRegistry
from providers.shared import ModelCapabilities, ProviderType
from tools.chat import ChatTool
from tools.listmodels import ListModelsTool
from tools.models import ToolModelCategory
from utils.model_restrictions import get_restriction_service

pytestmark = pytest.mark.no_mock_provider


@pytest.fixture
def hidden_catalog(monkeypatch, tmp_path, isolated_provider_environment):
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "model_name": "hidden:for",
                        "aliases": ["hidden-alias"],
                        "enabled_by_default": False,
                        "context_window": 100000,
                        "max_output_tokens": 8000,
                        "intelligence_score": 20,
                    },
                    {
                        "model_name": "visible",
                        "aliases": ["short"],
                        "context_window": 100000,
                        "max_output_tokens": 8000,
                    },
                    {
                        "model_name": "other:tag",
                        "aliases": ["tagged"],
                        "enabled_by_default": True,
                        "context_window": 100000,
                        "max_output_tokens": 8000,
                    },
                ]
            }
        )
    )
    monkeypatch.setenv("CUSTOM_API_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic")
    monkeypatch.setattr(CustomProvider, "_registry", CustomEndpointModelRegistry(str(path)))
    monkeypatch.setattr(OpenRouterProvider, "_registry", OpenRouterModelRegistry(str(path)))
    ModelProviderRegistry.register_provider(ProviderType.CUSTOM, CustomProvider)
    ModelProviderRegistry.register_provider(ProviderType.OPENROUTER, OpenRouterProvider)
    return {kind: ModelProviderRegistry.get_provider(kind) for kind in [ProviderType.CUSTOM, ProviderType.OPENROUTER]}


@pytest.mark.parametrize("kind", [ProviderType.CUSTOM, ProviderType.OPENROUTER])
def test_hidden_names_leave_discovery_but_remain_callable(hidden_catalog, kind):
    provider = hidden_catalog[kind]
    assert provider.get_capabilities("visible").enabled_by_default is True
    assert provider.get_capabilities("other:tag").enabled_by_default is True
    assert provider.get_capabilities("hidden:for").enabled_by_default is False
    assert provider.resolve_model_identity("hidden-alias") == "hidden:for"
    assert provider.validate_model_name("hidden:for")
    assert "hidden:for" not in provider.list_models()
    assert "hidden-alias" not in provider.list_models()
    assert "hidden:for" in provider.list_models(respect_restrictions=False)
    assert "hidden:for" not in ModelProviderRegistry.get_available_models()
    assert ModelProviderRegistry.get_preferred_fallback_model() != "hidden:for"


@pytest.mark.parametrize("setting", ["CUSTOM_ALLOWED_MODELS", "PREFERRED_MODELS"])
@pytest.mark.parametrize("opt_in,visible", [("hidden:for", True), ("HIDDEN:FOR", True), ("hidden-alias", False)])
def test_only_canonical_opt_in_exposes_hidden_entry(monkeypatch, hidden_catalog, setting, opt_in, visible):
    monkeypatch.setenv(setting, opt_in)
    provider = hidden_catalog[ProviderType.CUSTOM]
    # Capability lookup may resolve allowlist aliases; it must not manufacture
    # a canonical operator opt-in for subsequent discovery calls.
    assert provider.validate_model_name("hidden:for")
    assert ("hidden:for" in provider.list_models()) is visible
    names = ModelProviderRegistry.get_available_models()
    assert ("hidden:for" in names) is visible
    # Preserve the existing allowlist display convention (only configured names).
    assert ("hidden-alias" in names) is (visible and setting == "PREFERRED_MODELS")


@pytest.mark.parametrize("setting", ["CUSTOM_ALLOWED_MODELS", "PREFERRED_MODELS"])
def test_discovery_opt_in_cannot_override_deny(monkeypatch, hidden_catalog, setting):
    monkeypatch.setenv(setting, "hidden:for")
    monkeypatch.setenv("DISABLED_MODELS", "hidden-alias")
    assert ModelProviderRegistry.get_provider_for_model("hidden:for") is None
    assert "hidden:for" not in ModelProviderRegistry.get_available_models()
    assert "hidden-alias" not in ModelProviderRegistry.get_available_models()
    if setting == "CUSTOM_ALLOWED_MODELS":
        with pytest.raises(ValueError, match="No eligible models"):
            ModelProviderRegistry.get_preferred_fallback_model()
    else:
        assert ModelProviderRegistry.get_preferred_fallback_model() != "hidden:for"


@pytest.mark.parametrize("category", list(ToolModelCategory))
def test_preferences_precede_category_and_deduplicate_by_identity(monkeypatch, hidden_catalog, category):
    monkeypatch.setenv("PREFERRED_MODELS", " missing, TAGGED ,other:tag, short,visible,other:tag:against ")
    assert [name for _, name, _ in ModelProviderRegistry.get_preferred_model_choices()] == ["other:tag", "visible"]
    assert ModelProviderRegistry.get_preferred_fallback_model(category) == "other:tag"


@pytest.mark.parametrize("blocked_by", ["deny", "allowlist"])
def test_unavailable_or_restricted_preferences_are_skipped(monkeypatch, hidden_catalog, blocked_by):
    monkeypatch.setenv("PREFERRED_MODELS", "missing,other:tag,visible")
    if blocked_by == "deny":
        monkeypatch.setenv("DISABLED_MODELS", "custom:tagged")
    else:
        monkeypatch.setenv("CUSTOM_ALLOWED_MODELS", "visible")
    assert ModelProviderRegistry.get_preferred_fallback_model() == "visible"


def test_alias_preference_does_not_unhide_without_canonical_opt_in(monkeypatch, hidden_catalog):
    monkeypatch.setenv("PREFERRED_MODELS", "hidden-alias,visible")
    assert ModelProviderRegistry.get_preferred_fallback_model() == "visible"


def test_unknown_option_preference_does_not_select_base_model(monkeypatch, hidden_catalog):
    monkeypatch.setenv("PREFERRED_MODELS", "visible:against,hidden:for")
    assert ModelProviderRegistry.get_preferred_fallback_model() == "hidden:for"
    assert "hidden:for" in ModelProviderRegistry.get_available_models()


def test_preference_can_select_lower_priority_provider(monkeypatch, hidden_catalog):
    router = hidden_catalog[ProviderType.OPENROUTER]
    router._registry.model_map["vendor/router-only"] = ModelCapabilities(
        provider=ProviderType.OPENROUTER,
        model_name="vendor/router-only",
        friendly_name="Synthetic",
        context_window=100000,
        max_output_tokens=8000,
    )
    monkeypatch.setenv("PREFERRED_MODELS", "vendor/router-only,visible")
    assert ModelProviderRegistry.get_preferred_fallback_model() == "vendor/router-only"
    ModelProviderRegistry.unregister_provider(ProviderType.OPENROUTER)
    assert ModelProviderRegistry.get_preferred_fallback_model() == "visible"


def test_all_hidden_has_no_automatic_fallback(hidden_catalog):
    for provider in hidden_catalog.values():
        for capability in provider.get_all_model_capabilities().values():
            capability.enabled_by_default = False
    assert ModelProviderRegistry.get_available_models() == {}
    with pytest.raises(ValueError, match="No eligible models"):
        ModelProviderRegistry.get_preferred_fallback_model()
    assert ModelProviderRegistry.get_provider_for_model("visible") is not None


async def test_hidden_entries_absent_from_listing_and_tool_schema(hidden_catalog):
    payload = json.loads((await ListModelsTool().execute({}))[0].text)
    assert "hidden:for" not in payload["content"]
    assert "hidden-alias" not in payload["content"]
    assert "hidden:for" not in ChatTool()._format_available_models_list()
    assert "hidden-alias" not in ChatTool()._format_available_models_list()


@pytest.mark.parametrize("value", [None, "false", 0, 1, [], {}])
def test_enabled_by_default_requires_boolean(tmp_path, value):
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps({"models": [{"model_name": "synthetic", "enabled_by_default": value}]}))
    with pytest.raises(RegistryConfigurationError, match="enabled_by_default"):
        CustomEndpointModelRegistry(str(path))


def test_complete_identity_is_not_filtered_by_visibility(monkeypatch, hidden_catalog):
    import server

    monkeypatch.setenv("PREFERRED_MODELS", "visible")
    assert server.parse_model_option("hidden:for") == ("hidden:for", None)
    assert ModelProviderRegistry.resolve_model_identity("hidden:for")[1] == "hidden:for"
    monkeypatch.setenv("DISABLED_MODELS", "hidden:for")
    # Refresh the environment-driven policy only, preserving the same catalog.
    from utils import model_restrictions

    monkeypatch.setattr(model_restrictions, "_restriction_service", None)
    assert not get_restriction_service().is_allowed(ProviderType.CUSTOM, "hidden:for")
    assert server.parse_model_option("hidden:for") == ("hidden:for", None)
