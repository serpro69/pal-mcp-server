"""Deterministic provider-boundary captures; no live calls or cassette rewriting."""

from providers.gemini import GeminiModelProvider
from providers.openai import OpenAIModelProvider
from providers.registry import ModelProviderRegistry
from providers.shared import ModelResponse, ProviderType
from utils import model_restrictions


def capture_providers(monkeypatch, *, google_answer="Gemini answer", openai_answer="OpenAI answer"):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-google")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-openai")
    monkeypatch.setattr(model_restrictions, "_restriction_service", None)
    registry = ModelProviderRegistry()
    monkeypatch.setattr(
        registry, "_providers", {ProviderType.GOOGLE: GeminiModelProvider, ProviderType.OPENAI: OpenAIModelProvider}
    )
    monkeypatch.setattr(registry, "_initialized_providers", {})
    calls = []

    def generate(provider_name, answer, kwargs):
        calls.append({"provider": provider_name, **kwargs})
        content = answer(kwargs) if callable(answer) else answer
        return ModelResponse(content=content, model_name=kwargs["model_name"], provider=provider_name)

    async def google_generate(self, **kwargs):
        return generate("google", google_answer, kwargs)

    async def openai_generate(self, **kwargs):
        return generate("openai", openai_answer, kwargs)

    monkeypatch.setattr(GeminiModelProvider, "generate_content", google_generate)
    monkeypatch.setattr(OpenAIModelProvider, "generate_content", openai_generate)
    return calls
