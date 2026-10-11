"""Complete registry names take precedence over legacy stance parsing."""

import json

import pytest

from providers.custom import CustomProvider
from providers.registries.custom import CustomEndpointModelRegistry
from providers.registry import ModelProviderRegistry
from providers.shared import ProviderType
from server import parse_model_option


@pytest.fixture
def tagged_catalog(monkeypatch, tmp_path, isolated_provider_environment):
    path = tmp_path / "models.json"
    path.write_text(
        json.dumps(
            {
                "models": [
                    {"model_name": name, "aliases": aliases, "context_window": 100000, "max_output_tokens": 8000}
                    for name, aliases in [
                        ("local", []),
                        ("local:latest", ["tagged"]),
                        ("local:for", ["stance-name"]),
                        ("vendor/model:free", []),
                        ("deployment:0", []),
                    ]
                ]
            }
        )
    )
    monkeypatch.setattr(CustomProvider, "_registry", CustomEndpointModelRegistry(str(path)))
    monkeypatch.setenv("CUSTOM_API_URL", "http://localhost:11434/v1")
    ModelProviderRegistry.register_provider(ProviderType.CUSTOM, CustomProvider)


@pytest.mark.parametrize("name", ["local:latest", "local:for", "vendor/model:free", "deployment:0", "tagged"])
def test_complete_names_win(tagged_catalog, name):
    assert parse_model_option(f"  {name}  ") == (name, None)


@pytest.mark.parametrize("option", ["for", "AGAINST", " Neutral "])
def test_only_known_legacy_options(tagged_catalog, option):
    assert parse_model_option(f" local:latest : {option} ") == ("local:latest", option.strip().lower())


@pytest.mark.parametrize("name", ["local:unknown", "missing:for", "local:latest:unknown", "http://localhost:8080"])
def test_unregistered_colons_are_errors(tagged_catalog, name):
    with pytest.raises(ValueError, match="Register the exact tagged model"):
        parse_model_option(name)


def test_policy_does_not_reinterpret_known_stance_name(tagged_catalog, monkeypatch):
    monkeypatch.setenv("CUSTOM_ALLOWED_MODELS", "local")
    assert parse_model_option("local:for") == ("local:for", None)
    assert ModelProviderRegistry.get_provider_for_model("local:for") is None
