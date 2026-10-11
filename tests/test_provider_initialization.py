"""Cold-start and environment safety for legacy and native async providers."""

import asyncio
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import certifi
import httpx
import pytest

from providers.azure_openai import AzureOpenAIProvider
from providers.dial import DIALModelProvider
from providers.gemini import GeminiModelProvider
from providers.openai import OpenAIModelProvider
from tests.test_provider_retry_logic import _mock_chat_response


def make_provider(kind):
    if kind == "azure":
        return (
            AzureOpenAIProvider(
                "synthetic", azure_endpoint="https://example.openai.azure.com", deployments={"gpt-4o": "prod-gpt4o"}
            ),
            "gpt-4o",
        )
    if kind == "gemini":
        return GeminiModelProvider("synthetic"), "gemini-2.5-flash"
    if kind == "dial":
        return DIALModelProvider("synthetic"), "o3"
    return OpenAIModelProvider("synthetic"), "gpt-4.1"


def fake_sdk(generate, kwargs):
    return SimpleNamespace(
        base_url=kwargs.get("base_url", "https://example.invalid"),
        chat=SimpleNamespace(completions=SimpleNamespace(create=generate)),
        models=SimpleNamespace(generate_content=generate),
    )


def patch_sdk(monkeypatch, kind, constructor):
    if kind == "gemini":
        monkeypatch.setattr("providers.gemini.genai.Client", constructor)
    elif kind == "azure":
        monkeypatch.setattr("providers.azure_openai.AsyncAzureOpenAI", constructor)
    else:
        monkeypatch.setattr("providers.openai_compatible.AsyncOpenAI", constructor)
        if kind == "dial":
            monkeypatch.setattr("openai.OpenAI", constructor)


@pytest.mark.parametrize("kind", ["gemini", "dial"])
def test_concurrent_cold_generation_publishes_once_and_network_waits_overlap(monkeypatch, kind):
    provider, model = make_provider(kind)
    constructing = threading.Event()
    release_constructor = threading.Event()
    second_waiting = threading.Event()
    generation = threading.Barrier(2, timeout=5)
    clients = []

    # Release construction only once the other real generation call contends.
    # A signal before generate_content() would race with its validation work.
    lock_name = "_client_lock" if kind == "dial" else "_client_init_lock"
    guard = getattr(provider, lock_name)

    class ObservedLock:
        def __enter__(self):
            if guard.locked():
                second_waiting.set()
            guard.acquire()

        def __exit__(self, *exc_info):
            guard.release()

    monkeypatch.setattr(provider, lock_name, ObservedLock())

    def generate(**kwargs):
        generation.wait()
        if kind == "gemini":
            return SimpleNamespace(
                text="answer", candidates=[SimpleNamespace(finish_reason="STOP")], usage_metadata=None
            )
        return _mock_chat_response("answer")

    def constructor(**kwargs):
        clients.append(kwargs)
        constructing.set()
        assert release_constructor.wait(5)
        return fake_sdk(generate, kwargs)

    patch_sdk(monkeypatch, kind, constructor)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(provider.generate_content, "first", model)
            try:
                assert constructing.wait(5)
                second = pool.submit(provider.generate_content, "second", model)
                assert second_waiting.wait(5)
            finally:
                release_constructor.set()
            assert first.result(timeout=8).content == "answer"
            assert second.result(timeout=8).content == "answer"
        assert len(clients) == 1
        if kind == "dial":
            assert len(provider._deployment_clients) == 1
            assert clients[-1]["http_client"] is provider._http_client
    finally:
        release_constructor.set()
        for kwargs in clients:
            if "http_client" in kwargs:
                kwargs["http_client"].close()


@pytest.mark.parametrize("kind", ["gemini", "dial"])
def test_failed_initialization_releases_guard_and_can_retry(monkeypatch, kind):
    provider, model = make_provider(kind)
    clients = []
    failing = True

    def constructor(**kwargs):
        clients.append(kwargs)
        if failing:
            raise ValueError("synthetic initialization failure")
        return fake_sdk(lambda **_: _mock_chat_response(), kwargs)

    patch_sdk(monkeypatch, kind, constructor)
    try:
        with pytest.raises(ValueError, match="synthetic initialization failure"):
            _ = provider._get_deployment_client(model) if kind == "dial" else provider.client
        assert provider._client is None
        if kind == "dial":
            assert not provider._http_client.is_closed  # Failed wrapper must not close the borrowed pool.
            assert not provider._deployment_clients
        else:
            assert all(kwargs["http_client"].is_closed for kwargs in clients if "http_client" in kwargs)
        failing = False
        if kind == "dial":
            assert provider._get_deployment_client(model) is provider._get_deployment_client(model)
        else:
            assert provider.client is provider.client
    finally:
        for kwargs in clients:
            if "http_client" in kwargs:
                kwargs["http_client"].close()
        if kind == "dial":
            provider._http_client.close()


@pytest.mark.parametrize("kind,fallback", [("openai", False), ("openai", True), ("azure", False)])
@pytest.mark.parametrize("ca_variable", ["SSL_CERT_FILE", "SSL_CERT_DIR"])
async def test_initialization_keeps_proxy_and_tls_environment_for_other_provider_and_child(
    monkeypatch, tmp_path, kind, fallback, ca_variable
):
    bundle = tmp_path / "ca.pem"
    bundle.write_bytes(Path(certifi.where()).read_bytes())
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    environment = dict.fromkeys(
        ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"),
        "http://synthetic-proxy.invalid:8123",
    )
    environment.update(
        {
            "NO_PROXY": "synthetic-direct.invalid",
            ca_variable: str(bundle if ca_variable == "SSL_CERT_FILE" else tmp_path),
        }
    )
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    import ssl

    original_context = ssl.create_default_context
    contexts = []

    def create_context(*args, **kwargs):
        contexts.append(kwargs)
        return original_context(*args, **kwargs)

    monkeypatch.setattr(ssl, "create_default_context", create_context)
    entered = threading.Event()
    release = threading.Event()
    clients = []

    def constructor(**kwargs):
        clients.append(kwargs["http_client"])
        if fallback and len(clients) == 1:
            raise ValueError("force fallback")
        entered.set()
        assert release.wait(5)
        return fake_sdk(lambda **_: _mock_chat_response(), kwargs)

    patch_sdk(monkeypatch, kind, constructor)
    provider, _ = make_provider(kind)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(lambda: asyncio.run(provider._get_client()))
            try:
                assert entered.wait(5)
                assert {key: os.environ[key] for key in environment} == environment
                # Another provider and an actual CLI child see the unchanged environment.
                monkeypatch.setattr(
                    "providers.gemini.genai.Client", lambda **_: {key: os.environ[key] for key in environment}
                )
                assert GeminiModelProvider("synthetic").client == environment
                child = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "import json,os,sys; print(json.dumps({k:os.environ[k] for k in sys.argv[1:]}))",
                        *environment,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=True,
                )
                assert json.loads(child.stdout) == environment
            finally:
                release.set()
            result.result(timeout=5)
        assert len(clients) == (2 if fallback else 1)
        for client in clients:
            assert client._mounts == {}  # No environment proxy routing.
            assert client.timeout == provider.timeout_config
            assert client.follow_redirects
        assert any(
            context.get("cafile" if ca_variable == "SSL_CERT_FILE" else "capath") == environment[ca_variable]
            for context in contexts
        )
        if fallback:
            assert clients[0].is_closed
    finally:
        release.set()
        for client in clients:
            await client.aclose()


async def test_injected_transport_remains_usable(monkeypatch):
    provider = OpenAIModelProvider("synthetic")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": "wire answer"}, "finish_reason": "stop"}]}
        )
    )
    provider._test_transport = transport
    try:
        assert (await provider.generate_content("prompt", "gpt-4.1")).content == "wire answer"
    finally:
        if provider._client is not None:
            await provider.aclose()


def test_concurrent_alias_checks_preserve_configured_allowlist(monkeypatch):
    monkeypatch.setenv("OPENAI_ALLOWED_MODELS", "gpt4.1")
    provider = OpenAIModelProvider("synthetic")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(provider.get_capabilities, ["gpt-4.1", "gpt4.1"] * 20))
    assert all(result.model_name == "gpt-4.1" for result in results)
    assert provider.allowed_models == {"gpt4.1"}
    with pytest.raises(ValueError, match="restriction"):
        provider.get_capabilities("gpt-5")
