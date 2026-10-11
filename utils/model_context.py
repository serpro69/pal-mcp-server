"""
Model context management for dynamic token allocation.

This module provides a clean abstraction for model-specific token management,
ensuring that token limits are properly calculated based on the current model
being used, not global constants.

CONVERSATION MEMORY INTEGRATION:
This module works closely with the conversation memory system to provide
optimal token allocation for multi-turn conversations:

1. DUAL PRIORITIZATION STRATEGY SUPPORT:
   - Provides separate token budgets for conversation history vs. files
   - Enables the conversation memory system to apply newest-first prioritization
   - Ensures optimal balance between context preservation and new content

2. MODEL-SPECIFIC ALLOCATION:
   - Dynamic allocation based on model capabilities (context window size)
   - Conservative allocation for smaller models (O3: 200K context)
   - Generous allocation for larger models (Gemini: 1M+ context)
   - Adapts token distribution ratios based on model capacity

3. CROSS-TOOL CONSISTENCY:
   - Provides consistent token budgets across different tools
   - Enables seamless conversation continuation between tools
   - Supports conversation reconstruction with proper budget management
"""

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from config import DEFAULT_MODEL
from providers import ModelCapabilities, ModelProviderRegistry

if TYPE_CHECKING:
    from providers.base import ModelProvider

logger = logging.getLogger(__name__)


@dataclass
class TokenAllocation:
    """Token allocation strategy for a model."""

    total_tokens: int
    content_tokens: int
    response_tokens: int
    file_tokens: int
    history_tokens: int

    @property
    def available_for_prompt(self) -> int:
        """Tokens available for the actual prompt after allocations."""
        return self.content_tokens - self.file_tokens - self.history_tokens


class ModelContext:
    """
    Encapsulates model-specific information and token calculations.

    This class provides a single source of truth for all model-related
    token calculations, ensuring consistency across the system.
    """

    def __init__(
        self, model_name: str, model_option: Optional[str] = None, *, provider: Optional["ModelProvider"] = None
    ):
        self.model_name = model_name
        self.model_option = model_option  # Store optional model option (e.g., "for", "against", etc.)
        self._provider = provider
        self._capabilities = None
        self._token_allocation = None

    @property
    def provider(self):
        """Get the model provider lazily."""
        if self._provider is None:
            self._provider = ModelProviderRegistry.get_provider_for_model(self.model_name)
            if not self._provider:
                available_models = ModelProviderRegistry.get_available_model_names()
                if available_models:
                    available_text = ", ".join(available_models)
                else:
                    available_text = (
                        "No models detected. Configure provider credentials or set DEFAULT_MODEL to a valid option."
                    )

                raise ValueError(
                    f"Model '{self.model_name}' is not available with current API keys. Available models: {available_text}."
                )
        return self._provider

    @property
    def capabilities(self) -> ModelCapabilities:
        """Get model capabilities lazily."""
        if self._capabilities is None:
            self._capabilities = self.provider.get_capabilities(self.model_name)
        return self._capabilities

    def calculate_token_allocation(self, reserved_for_response: Optional[int] = None) -> TokenAllocation:
        """
        Calculate token allocation based on model capacity and conversation requirements.

        This method implements the core token budget calculation that supports the
        dual prioritization strategy used in conversation memory and file processing:

        TOKEN ALLOCATION STRATEGY:
        1. CONTENT vs RESPONSE SPLIT:
           - Smaller models (< 300K): 60% content, 40% response (conservative)
           - Larger models (≥ 300K): 80% content, 20% response (generous)

        2. CONTENT SUB-ALLOCATION:
           - File tokens: 30-40% of content budget for newest file versions
           - History tokens: 40-50% of content budget for conversation context
           - Remaining: Available for tool-specific prompt content

        3. CONVERSATION MEMORY INTEGRATION:
           - History allocation enables conversation reconstruction in reconstruct_thread_context()
           - File allocation supports newest-first file prioritization in tools
           - Remaining budget passed to tools via _remaining_tokens parameter

        Args:
            reserved_for_response: Optional positive caller limit, bounded by policy and model ceiling

        Returns:
            TokenAllocation with calculated budgets for dual prioritization strategy
        """
        capabilities = self.capabilities
        total_tokens = capabilities.context_window
        ceiling = capabilities.max_output_tokens
        if getattr(capabilities, "_is_generic", False) is True:
            raise ValueError(f"Unverified token capacities for {self.model_name}; configure explicit model metadata")
        for name, value in (("context_window", total_tokens), ("max_output_tokens", ceiling)):
            if type(value) is not int or value <= 0:
                raise ValueError(f"Invalid {name} for {self.model_name}: a positive integer is required")
        if reserved_for_response is not None and (type(reserved_for_response) is not int or reserved_for_response <= 0):
            raise ValueError("Caller max_output_tokens must be a positive integer")

        response_ratio = 0.4 if total_tokens < 300_000 else 0.2
        file_ratio = 0.3 if total_tokens < 300_000 else 0.4
        history_ratio = 0.5 if total_tokens < 300_000 else 0.4
        response_tokens = min(int(total_tokens * response_ratio), ceiling)
        if reserved_for_response is not None:
            response_tokens = min(response_tokens, reserved_for_response)
        content_tokens = total_tokens - response_tokens
        if response_tokens <= 0 or content_tokens <= 0:
            raise ValueError(f"Invalid request budget for {self.model_name}")
        content_ratio = content_tokens / total_tokens

        # Sub-allocations within content budget
        file_tokens = int(content_tokens * file_ratio)
        history_tokens = int(content_tokens * history_ratio)

        allocation = TokenAllocation(
            total_tokens=total_tokens,
            content_tokens=content_tokens,
            response_tokens=response_tokens,
            file_tokens=file_tokens,
            history_tokens=history_tokens,
        )

        logger.debug(f"Token allocation for {self.model_name}:")
        logger.debug(f"  Total: {allocation.total_tokens:,}")
        logger.debug(f"  Content: {allocation.content_tokens:,} ({content_ratio:.0%})")
        logger.debug(f"  Response: {allocation.response_tokens:,} ({response_tokens / total_tokens:.0%})")
        logger.debug(f"  Files: {allocation.file_tokens:,} ({file_ratio:.0%} of content)")
        logger.debug(f"  History: {allocation.history_tokens:,} ({history_ratio:.0%} of content)")

        return allocation

    def request_budget(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        images: Optional[list[str]] = None,
        max_output_tokens: Optional[int] = None,
    ) -> TokenAllocation:
        """Admit the assembled request against the exact output reservation.

        Estimates use UTF-8 bytes/3, a 10% margin, 64 protocol tokens and 8192
        tokens per image. This is a conservative local policy, not a tokenizer
        guarantee. Files/history/headers already in prompt are counted once.
        Oversize requests fail explicitly; no content is silently discarded.
        """
        allocation = self.calculate_token_allocation(max_output_tokens)
        text_tokens = self.estimate_tokens(prompt) + self.estimate_tokens(system_prompt or "")
        estimated_input = math.ceil(text_tokens * 1.1) + 64 + 8192 * len(images or [])
        if estimated_input > allocation.content_tokens:
            raise ValueError(
                f"Request for {self.model_name} exceeds input budget: estimated {estimated_input} tokens "
                f"including system/image/protocol overhead, capacity {allocation.content_tokens} "
                f"after reserving {allocation.response_tokens} output tokens. Reduce input or attachments."
            )
        return allocation

    def estimate_tokens(self, text: str) -> int:
        """
        Estimate token count for text using model-specific tokenizer.

        For now, uses simple estimation. Can be enhanced with model-specific
        tokenizers (tiktoken for OpenAI, etc.) in the future.
        """
        # TODO: Integrate model-specific tokenizers
        # For now, use conservative estimation
        return (len(text.encode("utf-8")) + 2) // 3  # Conservative estimate

    @classmethod
    def from_arguments(cls, arguments: dict[str, Any]) -> "ModelContext":
        """Create ModelContext from tool arguments."""
        model_name = arguments.get("model") or DEFAULT_MODEL
        return cls(model_name)


def get_request_budget(
    provider: "ModelProvider",
    model_name: str,
    *,
    prompt: str,
    system_prompt: Optional[str] = None,
    images: Optional[list[str]] = None,
    max_output_tokens: Optional[int] = None,
) -> TokenAllocation:
    """Recompute for the selected provider/model and actual payload on every call."""
    return ModelContext(model_name, provider=provider).request_budget(prompt, system_prompt, images, max_output_tokens)
