"""Missing credentials are usable state; broken catalogs remain explicit faults."""

import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent

import config
import server
from providers.configuration import provider_classes
from providers.errors import RegistryConfigurationError
from providers.registries.custom import CustomEndpointModelRegistry
from providers.registries.openrouter import OpenRouterModelRegistry
from providers.registry import ModelProviderRegistry
from tools.clink import CLinkTool


@pytest.mark.parametrize("selected", ["argument", "environment"])
@pytest.mark.parametrize(
    "payload,category",
    [
        ("{bad json", "json"),
        ("null", "schema"),
        ("[]", "schema"),
        ("{}", "schema"),
        ('{"models":{}}', "schema"),
        ('{"models":[1]}', "schema"),
        ('{"models":[{}]}', "schema"),
        ('{"models":[{"model_name":5}]}', "schema"),
        ('{"models":[{"model_name":"x","aliases":[5]}]}', "schema"),
        ('{"models":[{"model_name":"x","context_window":"large"}]}', "schema"),
        ('{"models":[{"model_name":"x","output_token_parameter":42}]}', "schema"),
        ('{"models":[{"model_name":"x","supported_thinking_levels":[42]}]}', "schema"),
        ('{"models":[{"model_name":"x","temperature_constraint":"typo"}]}', "schema"),
        ('{"models":[{"model_name":"x","max_image_size_mb":NaN}]}', "schema"),
        ('{"models":[{"model_name":"x","aliases":["y"]},{"model_name":"y"}]}', "schema"),
        ('{"models":[{"model_name":"x"},{"model_name":"X"}]}', "schema"),
    ],
)
def test_invalid_selected_registry_names_file(tmp_path, monkeypatch, selected, payload, category):
    path = tmp_path / "broken.json"
    path.write_text(payload)
    if selected == "environment":
        monkeypatch.setenv("CUSTOM_MODELS_CONFIG_PATH", str(path))
    with pytest.raises(RegistryConfigurationError) as caught:
        CustomEndpointModelRegistry(str(path) if selected == "argument" else None)
    assert str(path) in str(caught.value)
    assert caught.value.category == category
    assert caught.value.__cause__ is not None


@pytest.mark.parametrize("registry_class", [factory.REGISTRY_CLASS for factory in provider_classes()])
def test_explicit_missing_registry_is_never_optional(registry_class, tmp_path):
    path = tmp_path / "missing.json"
    with pytest.raises(RegistryConfigurationError) as caught:
        registry_class(str(path))
    assert caught.value.category == "missing"
    assert str(path) in str(caught.value)


@pytest.mark.parametrize("optional", [False, True])
def test_absent_shipped_registry_differs_from_corruption(monkeypatch, optional):
    class Resource:
        def joinpath(self, name):
            return self

        def read_text(self, **kwargs):
            raise FileNotFoundError("missing")

    monkeypatch.setattr("providers.registries.base.importlib.resources.files", lambda _: Resource())
    if optional:
        assert CustomEndpointModelRegistry().list_models() == []
    else:
        with pytest.raises(RegistryConfigurationError, match="openrouter_models.json"):
            OpenRouterModelRegistry()
    monkeypatch.setattr(Resource, "read_text", lambda self, **kwargs: "broken")
    with pytest.raises(RegistryConfigurationError, match="Invalid JSON"):
        (CustomEndpointModelRegistry if optional else OpenRouterModelRegistry)()


def test_permission_failure_is_not_empty_registry(tmp_path, monkeypatch):
    path = tmp_path / "unreadable.json"
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("denied")))
    with pytest.raises(RegistryConfigurationError) as caught:
        CustomEndpointModelRegistry(str(path))
    assert caught.value.category == "io"
    assert isinstance(caught.value.__cause__, PermissionError)


def test_optional_empty_is_valid_and_failed_reload_keeps_previous_data(tmp_path):
    path = tmp_path / "optional.json"
    path.write_text('{"models":[]}')
    registry = CustomEndpointModelRegistry(str(path))
    assert registry.list_models() == []
    path.write_text('{"models":[{"model_name":"x","aliases":["alias"]}]}')
    registry.reload()
    path.write_text('{"models":[{"model_name":"y"},42]}')
    with pytest.raises(RegistryConfigurationError):
        registry.reload()
    assert registry.resolve("alias").model_name == "x"
    assert registry.get_entry("x") == {}
    assert registry.get_entry("y") is None


def test_huge_numeric_value_is_typed_and_reload_is_atomic(tmp_path):
    path = tmp_path / "overflow.json"
    path.write_text('{"models":[{"model_name":"original"}]}')
    registry = CustomEndpointModelRegistry(str(path))
    path.write_text(json.dumps({"models": [{"model_name": "bad", "max_image_size_mb": 10**400}]}))
    with pytest.raises(RegistryConfigurationError) as caught:
        registry.reload()
    assert caught.value.category == "schema"
    assert str(path) in str(caught.value)
    assert registry.list_models() == ["original"]
    assert registry.get_entry("original") == {}


def test_json_decoder_value_limit_names_file(tmp_path, monkeypatch):
    path = tmp_path / "decoder-limit.json"
    path.write_text('{"models":[]}')

    def fail_decode(text):
        raise ValueError("Exceeds integer string conversion limit")

    monkeypatch.setattr("providers.registries.base.json.loads", fail_decode)
    with pytest.raises(RegistryConfigurationError) as caught:
        CustomEndpointModelRegistry(str(path))
    assert caught.value.category == "json"
    assert str(path) in str(caught.value)
    assert isinstance(caught.value.__cause__, ValueError)


@pytest.mark.parametrize("factory", provider_classes())
def test_startup_validates_selected_registry_without_keys(
    factory, isolated_provider_environment, tmp_path, monkeypatch
):
    declaration = factory.CONFIGURATION
    name = next(name for name in declaration.optional_env if name.endswith("MODELS_CONFIG_PATH"))
    path = tmp_path / "broken.json"
    path.write_text("{broken")
    monkeypatch.setenv(name, str(path))
    # Preserve class catalog state when configure_providers refreshes it.
    for provider_class in provider_classes():
        monkeypatch.setattr(provider_class, "_registry", getattr(provider_class, "_registry", None), raising=False)
        monkeypatch.setattr(provider_class, "MODEL_CAPABILITIES", provider_class.MODEL_CAPABILITIES)
    with pytest.raises(RegistryConfigurationError, match="broken.json"):
        server.configure_providers()


async def test_mcp_without_provider_keys_keeps_local_tools_available(
    isolated_provider_environment, monkeypatch, tmp_path
):
    monkeypatch.setattr(server, "DEFAULT_MODEL", "auto")
    monkeypatch.setattr(config, "IS_AUTO_MODE", True)
    monkeypatch.setenv("PAL_WORKSPACE_ROOT", str(tmp_path))
    server.initialize_runtime()
    local_cli = AsyncMock(return_value=[TextContent(type="text", text="local CLI answer")])
    monkeypatch.setattr(CLinkTool, "execute", local_cli)
    async with create_connected_server_and_client_session(server.server, timedelta(seconds=5)) as client:
        listing = await client.list_tools()
        names = {tool.name for tool in listing.tools}
        assert {"version", "listmodels", "planner", "clink"} <= names
        assert not {"chat", "consensus"} & names
        for name, arguments in [
            ("version", {}),
            ("listmodels", {}),
            ("planner", {"step": "plan", "step_number": 1, "total_steps": 1, "next_step_required": False}),
            ("clink", {"cli_name": "codex", "prompt": "local task"}),
        ]:
            result = await client.call_tool(name, arguments)
            assert not result.isError, (name, result.content)
        for model in ["auto", "gpt-5"]:
            result = await client.call_tool(
                "chat", {"model": model, "prompt": "hello", "working_directory_absolute_path": str(tmp_path)}
            )
            assert result.isError
            assert "Configure provider credentials" in result.content[0].text
            assert json.loads(result.content[0].text)["metadata"]["error_type"] == "model_provider_required"
    local_cli.assert_awaited_once()
    assert not ModelProviderRegistry()._initialized_providers
