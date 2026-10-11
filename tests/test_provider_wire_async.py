"""Wire contracts captured through the locked SDKs across the async migration."""

import inspect
import json

import httpx
import pytest

from providers.azure_openai import AzureOpenAIProvider
from providers.custom import CustomProvider
from providers.openai import OpenAIModelProvider
from providers.openrouter import OpenRouterProvider
from providers.xai import XAIModelProvider
from utils.provider_execution import invoke_provider


def make_provider(kind):
    if kind == "azure":
        return (
            AzureOpenAIProvider(
                "synthetic",
                azure_endpoint="https://example.openai.azure.com",
                api_version="2024-03-15-preview",
                deployments={"gpt-4o": "production"},
            ),
            "gpt-4o",
            "production",
            "/openai/deployments/production/chat/completions",
        )
    if kind == "custom":
        return (
            CustomProvider("synthetic", base_url="http://localhost:11434/v1"),
            "local-llama",
            "llama3.2",
            "/v1/chat/completions",
        )
    if kind == "xai":
        return XAIModelProvider("synthetic"), "grok-4", "grok-4", "/v1/chat/completions"
    if kind == "openrouter":
        return OpenRouterProvider("synthetic"), "openai/gpt-4.1", "openai/gpt-4.1", "/api/v1/chat/completions"
    return OpenAIModelProvider("synthetic"), "gpt4.1", "gpt-4.1", "/v1/chat/completions"


async def close_provider(provider):
    if hasattr(provider, "aclose"):
        await provider.aclose()
    elif provider._client is not None:
        result = provider._client.close()
        if inspect.isawaitable(result):
            await result


def chat_response():
    return httpx.Response(
        200,
        json={
            "id": "wire-1",
            "model": "wire-model",
            "created": 123,
            "choices": [{"message": {"role": "assistant", "content": "réponse ✅"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        },
    )


@pytest.mark.parametrize("kind", ["openai", "azure", "openrouter", "custom", "xai"])
async def test_chat_wire_contract(kind):
    provider, model, wire_model, path = make_provider(kind)
    requests = []

    def handler(request):
        requests.append(request)
        return chat_response()

    provider._test_transport = httpx.MockTransport(handler)
    try:
        result = await invoke_provider(
            provider,
            prompt="héllo",
            model_name=model,
            system_prompt="system",
            temperature=0.4,
            max_output_tokens=32,
            seed=7,
        )
        (request,) = requests
        payload = json.loads(request.content)
        assert request.url.path == path
        assert payload == {
            "model": wire_model,
            "messages": [{"role": "system", "content": "system"}, {"role": "user", "content": "héllo"}],
            "stream": False,
            "temperature": 0.4,
            "max_tokens": 32,
            "seed": 7,
        }
        assert request.extensions["timeout"] == provider.timeout_config.as_dict()
        if kind == "azure":
            assert request.headers["api-key"] == "synthetic"
            assert request.url.params["api-version"] == "2024-03-15-preview"
            assert result.model_name == model
            assert result.metadata["deployment"] == "production"
        else:
            assert request.headers["authorization"] == "Bearer synthetic"
        assert result.content == "réponse ✅"
        assert result.usage == {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}
        assert result.metadata["finish_reason"] == "stop"
    finally:
        await close_provider(provider)


@pytest.mark.parametrize("kind", ["openai", "openrouter"])
async def test_responses_wire_contract(kind):
    provider = OpenAIModelProvider("synthetic") if kind == "openai" else OpenRouterProvider("synthetic")
    model = "o3-pro" if kind == "openai" else "openai/gpt-5.2-pro"
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "r1",
                "model": model,
                "created_at": 123,
                "object": "response",
                "output": [
                    {
                        "type": "message",
                        "id": "m1",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "answer", "annotations": []}],
                    }
                ],
            },
        )

    provider._test_transport = httpx.MockTransport(handler)
    try:
        result = await invoke_provider(provider, prompt="héllo", model_name=model, system_prompt="system")
        (request,) = requests
        payload = json.loads(request.content)
        expected = {
            "model": model,
            "input": [
                {"role": "user", "content": [{"type": "input_text", "text": "system"}]},
                {"role": "user", "content": [{"type": "input_text", "text": "héllo"}]},
            ],
            "reasoning": {"effort": "medium" if kind == "openai" else "high"},
        }
        if kind == "openai":
            expected["store"] = True
        assert payload == expected
        assert request.url.path.endswith("/responses")
        assert result.content == "answer"
        assert result.metadata["endpoint"] == "responses"
    finally:
        await close_provider(provider)
