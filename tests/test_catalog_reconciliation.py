"""Freeze Task 28's evidence, identity preservation and real SDK wire contracts."""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from providers.configuration import provider_classes
from providers.gemini import GeminiModelProvider
from providers.openai import OpenAIModelProvider
from providers.openrouter import OpenRouterProvider
from scripts.audit_model_registry import audit_registry
from scripts.model_catalog import CatalogInputError, read_document, validate_registry
from tests.test_provider_wire_async import chat_response

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "tests/fixtures/catalog_evidence"
FILES = {"openai": "openai", "google": "gemini", "openrouter": "openrouter", "xai": "xai"}


@pytest.mark.parametrize("provider", FILES)
def test_curated_canonical_names_and_aliases_survive(provider):
    registry = read_document(ROOT / f"conf/{FILES[provider]}_models.json")
    validate_registry(registry, provider)
    entries = {entry["model_name"]: entry for entry in registry["models"]}
    for old in read_document(EVIDENCE / "preserved-identities.json")[provider]:
        assert entries[old["model_name"]].get("aliases", []) == old["aliases"]


@pytest.mark.parametrize("provider", FILES)
def test_frozen_drift_report_is_reproducible(provider):
    args = (
        read_document(ROOT / f"conf/{FILES[provider]}_models.json"),
        read_document(EVIDENCE / f"{provider}.json"),
        read_document(EVIDENCE / "models-dev.json"),
        provider,
        date(2026, 10, 12),
    )
    if provider == "xai":
        with pytest.raises(CatalogInputError, match="missing or empty"):
            audit_registry(*args)
        return
    report = audit_registry(*args)
    assert report == read_document(EVIDENCE / f"{provider}-audit.json")
    assert report["status"] == "review_needed"
    if provider == "openrouter":
        assert not report["capability_drift"]
    if provider == "google":
        assert {item["model"] for item in report["confirmed_deprecations"]} == {
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
            "gemini-3-pro-preview",
        }


def test_retired_and_exploratory_entries_are_hidden_but_resolvable(isolated_provider_environment):
    for factory in provider_classes():
        isolated_provider_environment.register_provider(factory.CONFIGURATION.provider_type, factory)
    for provider, names in [
        (OpenAIModelProvider("synthetic"), ["gpt-6.1-sol"]),
        (GeminiModelProvider("synthetic"), ["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-3-pro-preview"]),
    ]:
        visible = provider.list_models()
        for name in names:
            caps = provider.get_capabilities(name)
            assert caps.model_name == name
            assert name not in visible
            for alias in caps.aliases:
                assert alias not in visible
                assert provider.get_capabilities(alias).model_name == name


@pytest.mark.parametrize(
    "model,expected_limit",
    [
        ("anthropic/claude-opus-4.1", 32000),
        ("google/gemini-2.5-flash", 65535),
        ("deepseek/deepseek-r1-0528", 32768),
        ("anthropic/claude-sonnet-4.5", 64000),
    ],
)
async def test_reconciled_openrouter_limits_reach_chat_wire(model, expected_limit, isolated_provider_environment):
    provider = OpenRouterProvider("synthetic")
    captured = []

    def handle(request):
        captured.append(json.loads(request.content))
        return chat_response()

    provider._test_transport = httpx.MockTransport(handle)
    try:
        await provider.generate_content("hello", model)
        assert captured[0]["model"] == model
        assert captured[0]["max_tokens"] == expected_limit
    finally:
        await provider.aclose()


@pytest.mark.parametrize("kind", ["sol", "openrouter"])
@pytest.mark.parametrize("with_image", [False, True])
async def test_responses_candidate_and_reconciled_model_wire(kind, with_image, isolated_provider_environment):
    provider = OpenAIModelProvider("synthetic") if kind == "sol" else OpenRouterProvider("synthetic")
    model = "gpt-6.1-sol" if kind == "sol" else "openai/gpt-5.2-pro"
    image = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII="
    captured = []

    def handle(request):
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "id": "r1",
                "object": "response",
                "created_at": 123,
                "model": model,
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

    provider._test_transport = httpx.MockTransport(handle)
    try:
        result = await provider.generate_content(
            "hello", model, temperature=0.4, images=[image] if with_image else None
        )
        assert result.content == "answer"
        assert captured[0].url.path.endswith("/v1/responses")
        payload = json.loads(captured[0].content)
        assert payload["model"] == model
        assert payload["max_output_tokens"] == (128000 if kind == "sol" else 80000)
        assert payload["reasoning"] == {"effort": "medium" if kind == "sol" else "high"}
        assert "temperature" not in payload
        expected = [{"type": "input_text", "text": "hello"}]
        if with_image:
            expected.append({"type": "input_image", "image_url": image, "detail": "auto"})
        assert payload["input"] == [{"role": "user", "content": expected}]
        assert image not in json.dumps(provider._sanitize_for_logging(payload))
    finally:
        await provider.aclose()
