"""Portable usage counts. Missing or malformed counts remain unknown, not zero."""

from collections.abc import Mapping
from typing import Any

USAGE_FIELDS = ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "cache_write_tokens")


def known_usage(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {key: value[key] for key in USAGE_FIELDS if type(value.get(key)) is int and value[key] >= 0}
