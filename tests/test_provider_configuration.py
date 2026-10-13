"""Provider declarations, policy and MCP-to-SDK model identity contracts."""

import json
from unittest.mock import Mock

import httpx
import pytest

import config
import server
from providers.azure_openai import AzureOpenAIProvider
from providers.configuration import (
    ProviderConfiguration,
    provider_classes,
    provider_configurations,
    provider_setup_diagnostics,
)
from providers.custom import CustomProvider
from providers.dial import DIALModelProvider
from providers.errors import RegistryConfigurationError
from providers.openrouter import OpenRouterProvider
from providers.registries.custom import CustomEndpointModelRegistry
from providers.registries.openrouter import OpenRouterModelRegistry
from providers.registry import ModelProviderRegistry
from providers.shared import ProviderType
from tests.test_provider_wire_async import chat_response
from tools.shared.exceptions import ToolExecutionError
from utils import env as env_config


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
@pytest.mark.parametrize(
    "form",
    [
        "canonical",
        "alias",
        "legacy",
        "case",
        "unknown",
        "denied",
        "stance-name",
        "disabled",
        "disabled-scoped",
        "disabled-alias",
        "disabled-via-alias",
        "disabled-stance",
        "other-scope",
        "hidden-explicit",
        "hidden-alias",
        "hidden-stance",
        "preferred-auto",
        "preference-explicit",
        "preferred-denied",
        "hidden-denied",
    ],
)
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
    if form.startswith("hidden") or form == "preferred-auto":
        entries[0]["enabled_by_default"] = False
        if form == "hidden-stance":
            entries[1]["enabled_by_default"] = False
            entries[1]["aliases"] = ["stance-alias"]
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
    elif form == "hidden-stance":
        monkeypatch.setenv(declaration.restriction_env, "tagged,stance-alias")
    else:
        monkeypatch.setenv(declaration.restriction_env, "tagged,local:for")
    disabled = {
        "disabled": canonical,
        "disabled-scoped": f"{kind.value}:{canonical}",
        "disabled-alias": "tagged",
        "disabled-via-alias": canonical,
        "disabled-stance": "local:for",
        "other-scope": f"google:{canonical}",
    }
    if form in disabled:
        monkeypatch.setenv("DISABLED_MODELS", disabled[form])
    if form in {"preferred-auto", "preference-explicit", "preferred-denied"}:
        monkeypatch.setenv("PREFERRED_MODELS", canonical)
    if form in {"preferred-denied", "hidden-denied"}:
        monkeypatch.setenv("DISABLED_MODELS", canonical)
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
        "disabled": canonical,
        "disabled-scoped": canonical,
        "disabled-alias": canonical,
        "disabled-via-alias": "tagged",
        "disabled-stance": "local:for",
        "other-scope": canonical,
        "hidden-explicit": canonical,
        "hidden-alias": "tagged",
        "hidden-stance": "local:for",
        "preferred-auto": "auto",
        "preference-explicit": "local:for",
        "preferred-denied": "auto",
        "hidden-denied": canonical,
    }[form]
    monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(tmp_path))
    args = {"model": requested, "prompt": "hello", "working_directory_absolute_path": str(tmp_path)}
    try:
        if form in {"unknown", "denied", "hidden-denied"} or form.startswith("disabled"):
            with pytest.raises(ToolExecutionError):
                await server.handle_call_tool("chat", args)
            assert not requests
        else:
            await server.handle_call_tool("chat", args)
            assert len(requests) == 1
            expected = (
                "local:for"
                if form in {"stance-name", "hidden-stance", "preference-explicit", "preferred-denied"}
                else canonical
            )
            assert json.loads(requests[0].content)["model"] == expected
            assert ModelProviderRegistry.resolve_model_identity(expected) == (provider, expected)
            if form.startswith("hidden"):
                assert expected not in ModelProviderRegistry.get_available_models()
            else:
                assert expected in ModelProviderRegistry.get_available_models()
    finally:
        await provider.aclose()


@pytest.mark.parametrize("deny_higher_priority", [False, True])
async def test_preferred_alias_retains_provider_when_canonical_names_collide(
    monkeypatch, tmp_path, isolated_provider_environment, deny_higher_priority
):
    requests = []
    providers = []
    monkeypatch.setenv("CUSTOM_API_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic")
    monkeypatch.setenv("PREFERRED_MODELS", "router-only")
    if deny_higher_priority:
        monkeypatch.setenv("DISABLED_MODELS", "custom:shared")
    for kind, factory, registry_type, alias in [
        (ProviderType.CUSTOM, CustomProvider, CustomEndpointModelRegistry, "local-only"),
        (ProviderType.OPENROUTER, OpenRouterProvider, OpenRouterModelRegistry, "router-only"),
    ]:
        path = tmp_path / f"{kind.value}.json"
        path.write_text(
            json.dumps(
                {
                    "models": [
                        {
                            "model_name": "shared",
                            "aliases": [alias],
                            "context_window": 100000,
                            "max_output_tokens": 8000,
                        }
                    ]
                }
            )
        )
        monkeypatch.setattr(factory, "_registry", registry_type(str(path)))
        ModelProviderRegistry.register_provider(kind, factory)
        provider = ModelProviderRegistry.get_provider(kind)

        def handler(request, owner=kind):
            requests.append((owner, json.loads(request.content)["model"]))
            return chat_response()

        provider._test_transport = httpx.MockTransport(handler)
        providers.append(provider)
    try:
        assert ModelProviderRegistry.get_preferred_fallback_model() == "router-only"
        await server.handle_call_tool(
            "chat",
            {
                "model": "auto",
                "prompt": "hello",
                "working_directory_absolute_path": str(tmp_path),
            },
        )
        assert requests == [(ProviderType.OPENROUTER, "shared")]
    finally:
        for provider in providers:
            await provider.aclose()


@pytest.mark.parametrize("empty", [None, "", " \t\n"])
def test_setup_diagnostics_classify_absent_inputs_from_all_declarations(
    isolated_provider_environment, monkeypatch, empty
):
    declarations = provider_configurations()
    if empty is not None:
        for declaration in declarations.values():
            for name in (declaration.key_env, *declaration.required_env):
                monkeypatch.setenv(name, empty)
    diagnostics = provider_setup_diagnostics()
    assert diagnostics.keys() == declarations.keys()
    for kind, result in diagnostics.items():
        declaration = declarations[kind]
        assert result.provider_type == kind and result.label == declaration.label
        assert result.status == "absent"
        assert result.supplied_settings == ()
        assert (
            result.missing_settings
            == (() if declaration.key_optional else (declaration.key_env,)) + declaration.required_env
        )
        assert not declaration.is_configured()


@pytest.mark.parametrize("factory", provider_classes())
def test_declared_key_and_required_setting_placeholders_are_absent(isolated_provider_environment, monkeypatch, factory):
    declaration = factory.CONFIGURATION
    cases = [(declaration.key_env, placeholder) for placeholder in declaration.placeholders]
    cases.extend(declaration.required_placeholders)
    for name, placeholder in cases:
        with monkeypatch.context() as patcher:
            patcher.setenv(name, f" \t{placeholder}\n")
            assert declaration.effective_value(name) is None
            assert declaration.setup_diagnostics().status == "absent"
            assert not declaration.is_configured()
            assert placeholder not in repr(declaration.constructor_kwargs())


@pytest.mark.parametrize(
    "kind,key,endpoint,status,supplied,missing",
    [
        (
            ProviderType.AZURE,
            None,
            "https://azure.invalid",
            "incomplete",
            ("AZURE_OPENAI_ENDPOINT",),
            ("AZURE_OPENAI_API_KEY",),
        ),
        (ProviderType.AZURE, "private-key", None, "incomplete", ("AZURE_OPENAI_API_KEY",), ("AZURE_OPENAI_ENDPOINT",)),
        (
            ProviderType.AZURE,
            "your_azure_openai_key_here",
            "https://azure.invalid",
            "incomplete",
            ("AZURE_OPENAI_ENDPOINT",),
            ("AZURE_OPENAI_API_KEY",),
        ),
        (
            ProviderType.AZURE,
            "private-key",
            "https://azure.invalid",
            "complete",
            ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"),
            (),
        ),
        (ProviderType.CUSTOM, "private-key", None, "incomplete", ("CUSTOM_API_KEY",), ("CUSTOM_API_URL",)),
        (
            ProviderType.CUSTOM,
            "private-key",
            "your_custom_api_url_here",
            "incomplete",
            ("CUSTOM_API_KEY",),
            ("CUSTOM_API_URL",),
        ),
        (ProviderType.CUSTOM, None, "http://localhost:11434/v1", "complete", ("CUSTOM_API_URL",), ()),
        (
            ProviderType.CUSTOM,
            "your_custom_api_key_here",
            "http://localhost:11434/v1",
            "complete",
            ("CUSTOM_API_URL",),
            (),
        ),
        (
            ProviderType.CUSTOM,
            "private-key",
            "http://localhost:11434/v1",
            "complete",
            ("CUSTOM_API_KEY", "CUSTOM_API_URL"),
            (),
        ),
    ],
)
def test_setup_diagnostics_distinguish_partial_key_and_endpoint_settings(
    isolated_provider_environment, monkeypatch, kind, key, endpoint, status, supplied, missing
):
    declaration = provider_configurations()[kind]
    if key is not None:
        monkeypatch.setenv(declaration.key_env, key)
    if endpoint is not None:
        monkeypatch.setenv(declaration.required_env[0], endpoint)
    result = provider_setup_diagnostics()[kind]
    assert result.status == status
    assert result.supplied_settings == supplied
    assert result.missing_settings == missing
    assert declaration.is_configured() is (status == "complete")
    assert "private-key" not in repr(result)
    assert "https://azure.invalid" not in repr(result)
    assert "http://localhost:11434/v1" not in repr(result)


def test_opencode_go_only_and_valid_plus_partial_are_diagnosed_independently(
    isolated_provider_environment, monkeypatch
):
    declaration = provider_configurations()[ProviderType.OPENCODE_GO]
    monkeypatch.setenv(declaration.key_env, "private-opencode-key")
    first = provider_setup_diagnostics()
    assert {kind for kind, result in first.items() if result.status == "complete"} == {ProviderType.OPENCODE_GO}
    assert all(result.status == "absent" for kind, result in first.items() if kind != ProviderType.OPENCODE_GO)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://azure.invalid")
    monkeypatch.setenv("CUSTOM_API_KEY", "private-custom-key")
    result = provider_setup_diagnostics()
    assert result[ProviderType.OPENCODE_GO].status == "complete"
    assert {kind for kind, item in result.items() if item.status == "incomplete"} == {
        ProviderType.AZURE,
        ProviderType.CUSTOM,
    }
    assert "private-" not in repr(result)


def test_noncredential_settings_and_child_cli_keys_do_not_configure_providers(
    isolated_provider_environment, monkeypatch
):
    for declaration in provider_configurations().values():
        for name in (declaration.restriction_env, *declaration.restriction_aliases, *declaration.optional_env):
            monkeypatch.setenv(name, "noncredential-setting")
    for name in ("DEFAULT_MODEL", "PREFERRED_MODELS", "DISABLED_MODELS", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "noncredential-setting")
    assert all(result.status == "absent" for result in provider_setup_diagnostics().values())
    assert not any(declaration.is_configured() for declaration in provider_configurations().values())


def test_diagnostics_do_not_construct_providers_or_validate_catalogs(isolated_provider_environment, monkeypatch):
    blocked = Mock(side_effect=AssertionError("Input diagnostics must not initialize clients or catalogs"))
    for factory in provider_classes():
        declaration = factory.CONFIGURATION
        monkeypatch.setenv(declaration.key_env, "private-key")
        for name in declaration.required_env:
            monkeypatch.setenv(name, "https://endpoint.invalid")
        monkeypatch.setattr(factory, "__init__", blocked)
        monkeypatch.setattr(factory.REGISTRY_CLASS, "__init__", blocked)
    result = provider_setup_diagnostics()
    assert all(item.status == "complete" for item in result.values())
    blocked.assert_not_called()


def test_new_declaration_settings_automatically_join_diagnostics(isolated_provider_environment, monkeypatch):
    declaration = ProviderConfiguration(
        ProviderType.CUSTOM,
        "Synthetic provider",
        "SYNTHETIC_KEY",
        "SYNTHETIC_ALLOWED_MODELS",
        required_env=("SYNTHETIC_URL", "SYNTHETIC_REGION"),
        required_placeholders=(("SYNTHETIC_URL", "placeholder-url"), ("SYNTHETIC_URL", "another-placeholder")),
        arguments=(("base_url", "SYNTHETIC_URL"), ("region", "SYNTHETIC_REGION")),
    )
    monkeypatch.setattr("providers.configuration.provider_configurations", lambda: {ProviderType.CUSTOM: declaration})
    monkeypatch.setenv("SYNTHETIC_KEY", "private-key")
    monkeypatch.setenv("SYNTHETIC_URL", "another-placeholder")
    monkeypatch.setenv("SYNTHETIC_REGION", "north")
    result = provider_setup_diagnostics()[ProviderType.CUSTOM]
    assert result.status == "incomplete" and result.missing_settings == ("SYNTHETIC_URL",)
    assert declaration.constructor_kwargs() == {"api_key": "private-key", "region": "north"}
    monkeypatch.setenv("SYNTHETIC_URL", " https://real.invalid/v1 ")
    assert provider_setup_diagnostics()[ProviderType.CUSTOM].status == "complete"
    assert declaration.constructor_kwargs()["base_url"] == " https://real.invalid/v1 "


@pytest.mark.parametrize("factory", provider_classes())
def test_effective_settings_preserve_real_values_exactly(isolated_provider_environment, monkeypatch, factory):
    declaration = factory.CONFIGURATION
    monkeypatch.setenv(declaration.key_env, "  private-key\t")
    for name in declaration.required_env:
        monkeypatch.setenv(name, "  https://endpoint.invalid/v1\t")
    for _, name in declaration.arguments:
        if name not in declaration.required_env:
            monkeypatch.setenv(name, "  optional-setting\t")
    assert declaration.api_key() == "  private-key\t"
    assert declaration.is_configured()
    assert declaration.setup_diagnostics().status == "complete"
    kwargs = declaration.constructor_kwargs()
    assert kwargs["api_key"] == "  private-key\t"
    for argument, name in declaration.arguments:
        assert kwargs[argument] == (
            "  https://endpoint.invalid/v1\t" if name in declaration.required_env else "  optional-setting\t"
        )


@pytest.mark.parametrize("force_override", [False, True])
def test_diagnostics_and_constructor_args_share_dotenv_precedence(
    isolated_provider_environment, monkeypatch, force_override
):
    monkeypatch.setenv("CUSTOM_API_URL", "http://runtime.invalid/v1")
    monkeypatch.setattr(env_config, "_DOTENV_VALUES", {"CUSTOM_API_URL": "your_custom_api_url_here"})
    monkeypatch.setattr(env_config, "_FORCE_ENV_OVERRIDE", force_override)
    declaration = CustomProvider.CONFIGURATION
    result = provider_setup_diagnostics()[ProviderType.CUSTOM]
    assert result.status == ("absent" if force_override else "complete")
    assert declaration.is_configured() is (not force_override)
    assert declaration.constructor_kwargs() == (
        {"api_key": ""} if force_override else {"api_key": "", "base_url": "http://runtime.invalid/v1"}
    )


@pytest.mark.parametrize("placeholder", ["your_custom_api_url_here", " your_custom_api_url_here ", " \t"])
def test_custom_constructor_environment_cannot_restore_absent_endpoint(
    isolated_provider_environment, monkeypatch, placeholder
):
    monkeypatch.setenv("CUSTOM_API_URL", placeholder)
    declaration = CustomProvider.CONFIGURATION
    assert not declaration.is_configured()
    assert declaration.constructor_kwargs() == {"api_key": ""}
    with pytest.raises(ValueError, match="Custom API URL must be provided"):
        CustomProvider(**declaration.constructor_kwargs())


def test_custom_key_placeholders_remain_optional_in_actual_constructor(isolated_provider_environment, monkeypatch):
    monkeypatch.setenv("CUSTOM_API_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("CUSTOM_API_KEY", "your_custom_api_key_here")
    declaration = CustomProvider.CONFIGURATION
    provider = CustomProvider(**declaration.constructor_kwargs())
    assert provider.api_key == "dummy-key-for-unauthenticated-endpoint"
    assert provider.base_url == "http://localhost:11434/v1"
    explicit = CustomProvider(api_key="explicit-private-key", base_url="http://explicit.invalid/v1")
    assert explicit.api_key == "explicit-private-key" and explicit.base_url == "http://explicit.invalid/v1"


@pytest.fixture
def bootstrap_environment(isolated_provider_environment, monkeypatch):
    # Startup refreshes class-level catalogs; restore their original instances.
    for factory in provider_classes():
        monkeypatch.setattr(factory, "_registry", getattr(factory, "_registry", None), raising=False)
        monkeypatch.setattr(factory, "MODEL_CAPABILITIES", factory.MODEL_CAPABILITIES)
    return isolated_provider_environment


@pytest.mark.parametrize("custom_key", [None, "private-key"])
def test_startup_does_not_register_legacy_custom_placeholder(bootstrap_environment, monkeypatch, caplog, custom_key):
    monkeypatch.setenv("CUSTOM_API_URL", "your_custom_api_url_here")
    if custom_key:
        monkeypatch.setenv("CUSTOM_API_KEY", custom_key)
    server.configure_providers()
    assert bootstrap_environment._providers == {}
    assert "No model providers configured" in caplog.text
    assert "private-key" not in caplog.text
    result = provider_setup_diagnostics()[ProviderType.CUSTOM]
    assert result.status == ("incomplete" if custom_key else "absent")


@pytest.mark.parametrize("azure_key", [None, "your_azure_openai_api_key_here", "private-key"])
def test_startup_treats_shipped_azure_endpoint_as_absent(bootstrap_environment, monkeypatch, caplog, azure_key):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://your-resource.openai.azure.com/")
    if azure_key is not None:
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", azure_key)
    result = provider_setup_diagnostics()[ProviderType.AZURE]
    assert result.status == ("incomplete" if azure_key == "private-key" else "absent")
    assert "AZURE_OPENAI_ENDPOINT" in result.missing_settings
    assert "azure_endpoint" not in AzureOpenAIProvider.CONFIGURATION.constructor_kwargs()
    server.configure_providers()
    assert not bootstrap_environment._providers
    assert "No model providers configured" in caplog.text
    assert "private-key" not in caplog.text


def test_azure_constructor_cannot_restore_placeholder_endpoint(isolated_provider_environment, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", " https://your-resource.openai.azure.com/ ")
    with pytest.raises(ValueError, match="Azure OpenAI endpoint is required"):
        AzureOpenAIProvider("synthetic")
    provider = AzureOpenAIProvider(
        "synthetic",
        azure_endpoint="https://real-resource.openai.azure.com/",
        deployments={"canonical": {"deployment": "deployed", "context_window": 100000, "max_output_tokens": 1000}},
    )
    assert provider.azure_endpoint == "https://real-resource.openai.azure.com"


def test_opencode_only_startup_survives_other_partial_settings(bootstrap_environment, monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "private-opencode-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://azure.invalid")
    monkeypatch.setenv("CUSTOM_API_KEY", "private-custom-key")
    server.configure_providers()
    assert set(bootstrap_environment._providers) == {ProviderType.OPENCODE_GO}
    result = provider_setup_diagnostics()
    assert result[ProviderType.OPENCODE_GO].status == "complete"
    assert result[ProviderType.AZURE].status == result[ProviderType.CUSTOM].status == "incomplete"


def test_complete_azure_inputs_preserve_empty_catalog_warning(bootstrap_environment, monkeypatch, tmp_path, caplog):
    path = tmp_path / "azure.json"
    path.write_text('{"models": []}')
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "private-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://azure.invalid")
    monkeypatch.setenv("AZURE_MODELS_CONFIG_PATH", str(path))
    assert provider_setup_diagnostics()[ProviderType.AZURE].status == "complete"
    server.configure_providers()
    assert ProviderType.AZURE not in bootstrap_environment._providers
    assert "Azure OpenAI models configuration is empty" in caplog.text


def test_custom_empty_catalog_preserves_registration_and_auto_mode_warning(
    bootstrap_environment, monkeypatch, tmp_path, caplog
):
    path = tmp_path / "custom.json"
    path.write_text('{"models": []}')
    monkeypatch.setenv("CUSTOM_API_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("CUSTOM_MODELS_CONFIG_PATH", str(path))
    monkeypatch.setattr(config, "IS_AUTO_MODE", True)
    assert provider_setup_diagnostics()[ProviderType.CUSTOM].status == "complete"
    server.configure_providers()
    assert set(bootstrap_environment._providers) == {ProviderType.CUSTOM}
    assert "No models available for auto mode after restrictions" in caplog.text


def test_complete_inputs_do_not_hide_bootstrap_catalog_errors(bootstrap_environment, monkeypatch, tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{broken")
    monkeypatch.setenv("CUSTOM_API_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("CUSTOM_MODELS_CONFIG_PATH", str(path))
    assert provider_setup_diagnostics()[ProviderType.CUSTOM].status == "complete"
    with pytest.raises(RegistryConfigurationError, match="broken.json"):
        server.configure_providers()
