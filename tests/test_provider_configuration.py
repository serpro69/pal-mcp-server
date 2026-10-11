"""Provider declarations, policy and MCP-to-SDK model identity contracts."""

import json

import httpx
import pytest

import server
from providers.azure_openai import AzureOpenAIProvider
from providers.configuration import provider_classes, provider_configurations
from providers.custom import CustomProvider
from providers.dial import DIALModelProvider
from providers.openrouter import OpenRouterProvider
from providers.registries.custom import CustomEndpointModelRegistry
from providers.registries.openrouter import OpenRouterModelRegistry
from providers.registry import ModelProviderRegistry
from providers.shared import ProviderType
from tests.test_provider_wire_async import chat_response
from tools.shared.exceptions import ToolExecutionError


@pytest.mark.parametrize("collision", ["alias", "canonical", "deployment"])
def test_azure_rejects_ambiguous_deployment_identity(isolated_provider_environment, collision):
    deployments = {
        "first": {"deployment": "x", "aliases": ["y"], "context_window": 100000, "max_output_tokens": 1000},
        "second": {
            "deployment": {"alias": "y", "canonical": "first", "deployment": "x"}[collision],
            "context_window": 100000,
            "max_output_tokens": 2000,
        },
    }
    with pytest.raises(ValueError, match="Ambiguous Azure deployment"):
        AzureOpenAIProvider("synthetic", azure_endpoint="https://azure.invalid", deployments=deployments)


def test_discovery_obeys_same_priority_as_invocation(monkeypatch, isolated_provider_environment):
    from providers.openai import OpenAIModelProvider

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic")
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic")
    monkeypatch.setenv("OPENAI_ALLOWED_MODELS", "o3")
    # Reverse registration order must not change routing priority.
    ModelProviderRegistry.register_provider(ProviderType.OPENROUTER, OpenRouterProvider)
    ModelProviderRegistry.register_provider(ProviderType.OPENAI, OpenAIModelProvider)
    models = ModelProviderRegistry.get_available_models()
    assert "gpt5" not in models
    assert ModelProviderRegistry.get_provider_for_model("gpt5") is None
    assert models["o3"] == ProviderType.OPENAI
    for name, kind in models.items():
        assert ModelProviderRegistry.get_provider_for_model(name).get_provider_type() == kind


@pytest.mark.parametrize("restriction", ["AZURE_ALLOWED_MODELS", "AZURE_OPENAI_ALLOWED_MODELS"])
@pytest.mark.parametrize("allowed", ["deployed", "alias", "canonical", "unrelated"])
def test_azure_discovery_and_validation_agree(
    restriction, allowed, monkeypatch, tmp_path, isolated_provider_environment
):
    path = tmp_path / "azure.json"
    path.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "model_name": "canonical",
                        "deployment": "deployed",
                        "aliases": ["alias"],
                        "context_window": 100000,
                        "max_output_tokens": 8000,
                    }
                ]
            }
        )
    )
    monkeypatch.setenv("AZURE_MODELS_CONFIG_PATH", str(path))
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "synthetic")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://azure.invalid")
    monkeypatch.setenv(restriction, allowed)
    ModelProviderRegistry.register_provider(ProviderType.AZURE, AzureOpenAIProvider)
    provider = ModelProviderRegistry.get_provider(ProviderType.AZURE)
    for requested in ["canonical", "alias", "deployed"]:
        assert provider.validate_model_name(requested) is (allowed != "unrelated")
        assert (ModelProviderRegistry.get_provider_for_model(requested) is provider) is (allowed != "unrelated")
    assert bool(provider.list_models()) is (allowed != "unrelated")


@pytest.mark.parametrize("factory", provider_classes())
def test_metadata_drives_configuration(factory, monkeypatch, isolated_provider_environment):
    declaration = factory.CONFIGURATION
    assert not declaration.is_configured()
    assert declaration.restriction_env
    for key in declaration.required_env:
        monkeypatch.setenv(key, "https://endpoint.invalid")
    if not declaration.key_optional:
        for placeholder in declaration.placeholders:
            monkeypatch.setenv(declaration.key_env, placeholder)
            assert not declaration.is_configured()
        monkeypatch.setenv(declaration.key_env, "synthetic-secret")
    assert declaration.is_configured()
    assert "synthetic-secret" not in declaration.setup_hint()


@pytest.mark.parametrize("kind", [ProviderType.CUSTOM, ProviderType.DIAL, ProviderType.OPENROUTER])
@pytest.mark.parametrize("form", ["canonical", "alias", "legacy", "case", "unknown", "denied", "stance-name"])
async def test_dispatch_identity_reaches_real_serializer(
    kind, form, monkeypatch, tmp_path, isolated_provider_environment
):
    canonical = {
        ProviderType.CUSTOM: "local:latest",
        ProviderType.DIAL: "deployment:0",
        ProviderType.OPENROUTER: "vendor/model:free",
    }[kind]
    path = tmp_path / "catalog.json"
    entries = [
        {
            "model_name": name,
            "aliases": aliases,
            "context_window": 100000,
            "max_output_tokens": 8000,
            "output_token_parameter": "max_tokens",
        }
        for name, aliases in [(canonical, ["tagged"]), ("local:for", []), ("local", [])]
    ]
    path.write_text(json.dumps({"models": entries}))
    declaration = provider_configurations()[kind]
    monkeypatch.setenv(declaration.key_env, "synthetic-secret")
    if kind == ProviderType.CUSTOM:
        monkeypatch.setenv("CUSTOM_API_URL", "http://localhost:11434/v1")
        monkeypatch.setattr(CustomProvider, "_registry", CustomEndpointModelRegistry(str(path)))
        factory = CustomProvider
    elif kind == ProviderType.OPENROUTER:
        monkeypatch.setattr(OpenRouterProvider, "_registry", OpenRouterModelRegistry(str(path)))
        factory = OpenRouterProvider
    else:
        from providers.registries.dial import DialModelRegistry

        registry = DialModelRegistry(str(path))
        monkeypatch.setattr(DIALModelProvider, "_registry", registry)
        monkeypatch.setattr(DIALModelProvider, "MODEL_CAPABILITIES", dict(registry.model_map))
        factory = DIALModelProvider
    if form == "denied":
        monkeypatch.setenv(declaration.restriction_env, "local")
    else:
        monkeypatch.setenv(declaration.restriction_env, "tagged,local:for")
    ModelProviderRegistry.register_provider(kind, factory)
    provider = ModelProviderRegistry.get_provider(kind)
    requests = []

    def handler(request):
        requests.append(request)
        return chat_response()

    if kind == ProviderType.DIAL:
        monkeypatch.setattr(
            provider, "_create_dial_http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )
    else:
        provider._test_transport = httpx.MockTransport(handler)
    requested = {
        "canonical": canonical,
        "alias": "tagged",
        "legacy": canonical + ": AgAiNsT ",
        "case": "  " + canonical.upper() + "  ",
        "unknown": "local:unregistered",
        "denied": canonical,
        "stance-name": "local:for",
    }[form]
    monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(tmp_path))
    args = {"model": requested, "prompt": "hello", "working_directory_absolute_path": str(tmp_path)}
    try:
        if form in {"unknown", "denied"}:
            with pytest.raises(ToolExecutionError):
                await server.handle_call_tool("chat", args)
            assert not requests
        else:
            await server.handle_call_tool("chat", args)
            assert len(requests) == 1
            expected = "local:for" if form == "stance-name" else canonical
            assert json.loads(requests[0].content)["model"] == expected
            assert ModelProviderRegistry.resolve_model_identity(expected) == (provider, expected)
            assert expected in ModelProviderRegistry.get_available_models()
    finally:
        await provider.aclose()
