"""Verified per-model thinking controls through the locked GenAI serializer."""

import json
from dataclasses import replace

import httpx
import pytest
from google import genai
from google.genai import types

from providers.gemini import GeminiModelProvider

MODES = ["minimal", "low", "medium", "high", "max"]


@pytest.fixture
def wire(monkeypatch):
    requests = []
    original = genai.Client

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": "answer"}]}, "finishReason": "STOP"}],
            },
        )

    def constructor(**kwargs):
        options = kwargs.setdefault("http_options", types.HttpOptions())
        options.client_args = {"transport": httpx.MockTransport(handle)}
        options.async_client_args = {"transport": httpx.MockTransport(handle)}
        return original(**kwargs)

    monkeypatch.setattr("providers.gemini.genai.Client", constructor)
    return requests


@pytest.mark.parametrize(
    "model,expected",
    [
        ("gemini-3.8-flash", ["LOW", "LOW", "MEDIUM", "HIGH", "HIGH"]),
        ("gemini-3.7-flash", ["LOW", "LOW", "MEDIUM", "HIGH", "HIGH"]),
        ("gemini-3.6-flash", ["MINIMAL", "LOW", "MEDIUM", "HIGH", "HIGH"]),
        ("gemini-3.5-flash", ["MINIMAL", "LOW", "MEDIUM", "HIGH", "HIGH"]),
        ("gemini-3.1-pro-preview", ["LOW", "LOW", "MEDIUM", "HIGH", "HIGH"]),
        ("gemini-3-pro-preview", ["LOW", "LOW", "LOW", "HIGH", "HIGH"]),
        ("gemini-3-flash-preview", ["MINIMAL", "LOW", "MEDIUM", "HIGH", "HIGH"]),
        ("gemini-3.1-flash-lite", ["MINIMAL", "LOW", "MEDIUM", "HIGH", "HIGH"]),
        ("flash", ["LOW", "LOW", "MEDIUM", "HIGH", "HIGH"]),
    ],
)
@pytest.mark.parametrize("mode_index", range(5))
async def test_level_wire_contract(wire, model, expected, mode_index):
    provider = GeminiModelProvider("synthetic", base_url="https://gemini.invalid")
    try:
        result = await provider.generate_content("hello", model, thinking_mode=MODES[mode_index])
        (request,) = wire
        config = json.loads(request.content)["generationConfig"]["thinkingConfig"]
        assert config == {"thinking_level": expected[mode_index]}
        assert result.metadata["thinking_mode"] == expected[mode_index].lower()
        assert request.headers["x-goog-api-key"] == "synthetic"
        if model == "flash":
            assert request.url.path.endswith("/gemini-3.8-flash:generateContent")
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    "model,budgets",
    [
        ("gemini-2.5-pro", [163, 2621, 10813, 21954, 32768]),
        ("gemini-2.5-flash", [122, 1966, 8110, 16465, 24576]),
    ],
)
@pytest.mark.parametrize("mode_index", range(5))
async def test_budget_wire_contract(wire, model, budgets, mode_index):
    provider = GeminiModelProvider("synthetic", base_url="https://gemini.invalid")
    try:
        result = await provider.generate_content("hello", model, thinking_mode=MODES[mode_index])
        config = json.loads(wire[0].content)["generationConfig"]["thinkingConfig"]
        assert config == {"thinking_budget": budgets[mode_index]}
        assert result.metadata["thinking_mode"] == MODES[mode_index]
    finally:
        await provider.aclose()


@pytest.mark.parametrize("levels", [None, [], "low", ["invalid"], [None], [1], ["low", "low"], ["LOW"]])
async def test_invalid_level_declarations_fail_before_client_allocation(monkeypatch, levels):
    provider = GeminiModelProvider("synthetic")
    caps = replace(provider.get_capabilities("flash"), supported_thinking_levels=levels)
    monkeypatch.setattr(provider, "get_capabilities", lambda _: caps)
    with pytest.raises(ValueError, match="supported_thinking_levels"):
        await provider.generate_content("hello", "flash")
    assert provider._client is None


@pytest.mark.parametrize("mode", ["unknown", "HIGH", "", None, 1, []])
async def test_invalid_request_mode_fails_before_client_allocation(mode):
    provider = GeminiModelProvider("synthetic")
    with pytest.raises(ValueError, match="Invalid thinking_mode"):
        await provider.generate_content("hello", "flash", thinking_mode=mode)
    assert provider._client is None


@pytest.mark.parametrize("parameter", [None, "unknown", "thinkingBudget", []])
async def test_unknown_control_is_an_explicit_configuration_error(monkeypatch, parameter):
    provider = GeminiModelProvider("synthetic")
    caps = replace(provider.get_capabilities("flash"), thinking_parameter=parameter)
    monkeypatch.setattr(provider, "get_capabilities", lambda _: caps)
    with pytest.raises(ValueError, match="Unverified thinking_parameter"):
        await provider.generate_content("hello", "flash")
    assert provider._client is None


@pytest.mark.parametrize(
    "levels,requested,expected",
    [
        (["high", "low"], "medium", "LOW"),
        (["high"], "minimal", "HIGH"),
        (["low"], "max", "LOW"),
    ],
)
async def test_clamp_is_order_independent_and_prefers_lower(wire, monkeypatch, levels, requested, expected):
    provider = GeminiModelProvider("synthetic")
    caps = replace(provider.get_capabilities("flash"), supported_thinking_levels=levels)
    monkeypatch.setattr(provider, "get_capabilities", lambda _: caps)
    try:
        await provider.generate_content("hello", "flash", thinking_mode=requested)
        assert json.loads(wire[0].content)["generationConfig"]["thinkingConfig"] == {"thinking_level": expected}
    finally:
        await provider.aclose()


@pytest.mark.parametrize("ceiling,levels", [(0, None), (-1, None), (True, None), ("100", None), (100, []), (1, None)])
async def test_invalid_budget_metadata_is_not_silently_ignored(monkeypatch, ceiling, levels):
    provider = GeminiModelProvider("synthetic")
    caps = replace(
        provider.get_capabilities("gemini-2.5-pro"), max_thinking_tokens=ceiling, supported_thinking_levels=levels
    )
    monkeypatch.setattr(provider, "get_capabilities", lambda _: caps)
    with pytest.raises(ValueError, match="thinking budget"):
        await provider.generate_content("hello", "gemini-2.5-pro", thinking_mode="minimal")
    assert provider._client is None


async def test_non_thinking_model_omits_controls(wire):
    provider = GeminiModelProvider("synthetic")
    try:
        result = await provider.generate_content("hello", "gemini-2.0-flash-lite", thinking_mode=None)
        assert "thinkingConfig" not in json.loads(wire[0].content)["generationConfig"]
        assert result.metadata["thinking_mode"] is None
    finally:
        await provider.aclose()


async def test_unverified_legacy_thinking_configuration_fails_explicitly():
    provider = GeminiModelProvider("synthetic")
    with pytest.raises(ValueError, match="Unverified thinking_parameter for gemini-2.0-flash"):
        await provider.generate_content("hello", "gemini-2.0-flash")
    assert provider._client is None
