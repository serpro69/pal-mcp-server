"""Stable exceptions for conversation storage and capacity outcomes."""


class ConversationMemoryError(Exception):
    """Expected conversation failures, distinct from programming errors."""


class ConversationStorageError(ConversationMemoryError):
    """The storage backend could not complete an operation."""


class ConversationDecodeError(ConversationStorageError):
    """A stored thread is corrupt or does not match the thread schema."""


class ConversationNotFoundError(ConversationMemoryError):
    """A required conversation is absent or expired."""


class ConversationLimitError(ConversationMemoryError):
    """The conversation has no capacity for the requested messages."""
