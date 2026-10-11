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

    monkeypatch.setattr(
        GeminiModelProvider, "generate_content", lambda self, **kwargs: generate("google", google_answer, kwargs)
    )
    monkeypatch.setattr(
        OpenAIModelProvider, "generate_content", lambda self, **kwargs: generate("openai", openai_answer, kwargs)
    )
    return calls
