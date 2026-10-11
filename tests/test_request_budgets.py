"""Shared total-input/output budget boundaries, independent of vendor overflow rules."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from providers.shared import ModelCapabilities, ProviderType
from utils.model_context import ModelContext


def context(capacity=400000, ceiling=272000):
    caps = ModelCapabilities(
        provider=ProviderType.OPENAI,
        model_name="synthetic",
        friendly_name="Synthetic",
        context_window=capacity,
        max_output_tokens=ceiling,
    )
    return ModelContext("synthetic", provider=Mock(get_capabilities=Mock(return_value=caps)))


@pytest.mark.parametrize(
    "capacity,ceiling,caller,output,input_capacity",
    [
        (400000, 272000, None, 80000, 320000),
        (400000, 32000, None, 32000, 368000),
        (400000, 272000, 1000, 1000, 399000),
        (200000, 100000, None, 80000, 120000),
        (200000, 32000, 50000, 32000, 168000),
    ],
)
def test_reservation_and_input_capacity(capacity, ceiling, caller, output, input_capacity):
    budget = context(capacity, ceiling).request_budget("hello", max_output_tokens=caller)
    assert budget.response_tokens == output
    assert budget.content_tokens == input_capacity
    assert budget.response_tokens + budget.content_tokens == capacity


@pytest.mark.parametrize("value", [0, -1, None, True, 1.5, "100", float("nan")])
@pytest.mark.parametrize("field", ["context_window", "max_output_tokens"])
def test_invalid_metadata_is_not_an_unlimited_request(field, value):
    ctx = context()
    ctx._capabilities = replace(ctx.capabilities, **{field: value})
    with pytest.raises(ValueError, match=field):
        ctx.request_budget("hello")


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "100"])
def test_invalid_caller_limit(value):
    with pytest.raises(ValueError, match="Caller max_output_tokens"):
        context().request_budget("hello", max_output_tokens=value)


def test_unknown_capabilities_fail_explicitly():
    ctx = context()
    ctx.capabilities._is_generic = True
    with pytest.raises(ValueError, match="Unverified"):
        ctx.request_budget("hello")


def test_full_request_boundary_includes_system_and_image_overhead(monkeypatch):
    ctx = context(400000, 272000)
    # 283403 + 3 system tokens -> 311747 with margin; +64 +8192 = 320003.
    counts = {"history/files/current/headers": 283400, "system": 3, "": 0}
    monkeypatch.setattr(ctx, "estimate_tokens", counts.__getitem__)
    budget = ctx.request_budget("history/files/current/headers", "system", ["image"])
    assert budget.content_tokens == 320000
    counts["system"] = 6
    with pytest.raises(ValueError, match="including system/image/protocol overhead"):
        ctx.request_budget("history/files/current/headers", "system", ["image"])
    # The same text fits without the image, or with a lower output reservation.
    ctx.request_budget("history/files/current/headers", "system")
    ctx.request_budget("history/files/current/headers", "system", ["image"], 1000)


def test_usage_schemas_preserve_zero_and_reject_malformed_counts():
    from providers.gemini import GeminiModelProvider
    from providers.openai import OpenAIModelProvider

    openai = OpenAIModelProvider("synthetic")
    for value in (None, -1, True, "12", 2.5):
        usage = SimpleNamespace(input_tokens=value, output_tokens=0, total_tokens=None, prompt_tokens=999)
        assert openai._extract_usage(SimpleNamespace(usage=usage), responses_api=True) == {"output_tokens": 0}
    usage = SimpleNamespace(input_tokens=17, output_tokens=5, total_tokens=22, prompt_tokens=999)
    assert openai._extract_usage(SimpleNamespace(usage=usage), responses_api=True) == {
        "input_tokens": 17,
        "output_tokens": 5,
        "total_tokens": 22,
    }
    gemini = GeminiModelProvider("synthetic")
    assert gemini._extract_usage(
        SimpleNamespace(
            usage_metadata=SimpleNamespace(
                prompt_token_count=7,
                candidates_token_count=3,
                total_token_count=15,
                thoughts_token_count=5,
            )
        )
    ) == {"input_tokens": 7, "output_tokens": 3, "total_tokens": 15}
