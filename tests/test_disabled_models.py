"""Global denies use full provider identities across calls and discovery."""

import json
import logging

import pytest

from providers.custom import CustomProvider
from providers.openrouter import OpenRouterProvider
from providers.registries.custom import CustomEndpointModelRegistry
from providers.registries.openrouter import OpenRouterModelRegistry
from providers.registry import ModelProviderRegistry
from providers.shared import ProviderType
from tools.listmodels import ListModelsTool
from utils.model_restrictions import get_restriction_service

pytestmark = pytest.mark.no_mock_provider


@pytest.fixture
def policy_catalog(monkeypatch, tmp_path, isolated_provider_environment):
    path = tmp_path / "models.json"
    path.write_text(
        json.dumps(
            {
                "models": [
                    {"model_name": name, "aliases": aliases, "context_window": 100000, "max_output_tokens": 8000}
                    for name, aliases in [
                        ("local:tag", ["tagged", "second"]),
                        ("local:for", ["stance-name"]),
                        ("local", []),
                        ("openai:model:tag", []),
                        ("spare", []),
                    ]
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


@pytest.mark.parametrize("deny", ["local:tag", "tagged", "second", "CUSTOM: TaGgEd ", "custom:local:tag"])
def test_deny_overrides_allowlist_for_all_aliases(monkeypatch, policy_catalog, deny):
    monkeypatch.setenv("CUSTOM_ALLOWED_MODELS", "tagged,spare")
    monkeypatch.setenv("DISABLED_MODELS", deny)
    provider = policy_catalog[ProviderType.CUSTOM]
    for requested in ["local:tag", "tagged", "second"]:
        assert not provider.validate_model_name(requested)
        assert ModelProviderRegistry.get_provider_for_model(requested) is None
        assert requested not in provider.list_models()
        assert requested not in ModelProviderRegistry.get_available_models()
    assert ModelProviderRegistry.get_preferred_fallback_model() == "spare"
    assert get_restriction_service().filter_models(ProviderType.CUSTOM, ["second", "spare"]) == ["spare"]


@pytest.mark.parametrize(
    "deny,custom,router",
    [
        ("local:tag", False, False),
        ("tagged", False, False),
        ("custom:local:tag", False, True),
        ("openrouter:tagged", True, False),
        ("google:local:tag", True, True),
    ],
)
def test_scope_limits_duplicate_names(monkeypatch, policy_catalog, deny, custom, router):
    monkeypatch.setenv("DISABLED_MODELS", deny)
    assert policy_catalog[ProviderType.CUSTOM].validate_model_name("second") is custom
    assert policy_catalog[ProviderType.OPENROUTER].validate_model_name("tagged") is router


def test_only_one_scope_prefix_and_no_legacy_options(monkeypatch, policy_catalog):
    monkeypatch.setenv("DISABLED_MODELS", "custom:openai:model:tag,local:for,local:tag:against")
    provider = policy_catalog[ProviderType.CUSTOM]
    assert not provider.validate_model_name("openai:model:tag")
    assert not provider.validate_model_name("stance-name")
    assert provider.validate_model_name("local")
    assert provider.validate_model_name("local:tag")
    assert policy_catalog[ProviderType.OPENROUTER].validate_model_name("openai:model:tag")


@pytest.mark.parametrize("prefix", ["", "custom:", "openrouter:"])
def test_unknown_denies_warn_without_echoing_input(monkeypatch, policy_catalog, caplog, prefix):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("DISABLED_MODELS", f"tagged,{prefix}missing-secret,local:tag:against")
    service = get_restriction_service()
    service.validate_against_known_models(policy_catalog)
    assert "DISABLED_MODELS entry 2" in caplog.text
    assert "DISABLED_MODELS entry 3" in caplog.text
    assert "missing-secret" not in caplog.text
    assert "entry 1" not in caplog.text


async def test_listing_and_summary_exclude_denied_models(monkeypatch, policy_catalog):
    monkeypatch.setenv("DISABLED_MODELS", "local:tag")
    response = await ListModelsTool().execute({})
    payload = json.loads(response[0].text)
    assert "`local:tag`" not in payload["content"]
    assert "`tagged`" not in payload["content"]
    assert "`second`" not in payload["content"]
    count = len(ModelProviderRegistry.get_available_models())
    assert f"**Total Available Models**: {count}" in payload["content"]


def test_all_denied_has_actionable_fallback_error(monkeypatch, policy_catalog):
    monkeypatch.setenv(
        "DISABLED_MODELS", "local:tag,local:for,local,openrouter:openai:model:tag,custom:openai:model:tag,spare"
    )
    assert ModelProviderRegistry.get_available_models() == {}
    with pytest.raises(ValueError, match="DISABLED_MODELS"):
        ModelProviderRegistry.get_preferred_fallback_model()


def test_policy_resolution_uses_recreated_provider_catalog(monkeypatch, policy_catalog):
    monkeypatch.setenv("DISABLED_MODELS", "tagged")
    provider = policy_catalog[ProviderType.CUSTOM]
    assert not provider.validate_model_name("local:tag")
    path = provider._registry.config_path
    data = json.loads(path.read_text())
    data["models"][0]["aliases"].remove("tagged")
    data["models"][-1]["aliases"].append("tagged")
    path.write_text(json.dumps(data))
    provider._registry.reload()
    provider = ModelProviderRegistry.get_provider(ProviderType.CUSTOM, force_new=True)
    assert provider.validate_model_name("local:tag")
    assert not provider.validate_model_name("spare")


def test_direct_provider_enforces_alias_denies_without_registration(monkeypatch, policy_catalog):
    monkeypatch.setenv("DISABLED_MODELS", "custom:tagged")
    provider = policy_catalog[ProviderType.CUSTOM]
    ModelProviderRegistry.unregister_provider(ProviderType.CUSTOM)
    assert not provider.validate_model_name("local:tag")
    assert "local:tag" not in provider.list_models()
