"""
Client Information Utility for MCP Server

This module provides utilities to extract and format client information
from the MCP request context.

It also provides friendly name mapping and caching for consistent client
identification across the application.
"""

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Optional

from mcp.server import ServerRequestContext

logger = logging.getLogger(__name__)

_client_info_cache: ContextVar[dict[str, Any] | None] = ContextVar("pal_client_info", default=None)

# Mapping of known client names to friendly names
# This is case-insensitive and checks if the key is contained in the client name
CLIENT_NAME_MAPPINGS = {
    # Claude variants
    "claude-ai": "Claude",
    "claude": "Claude",
    "claude-desktop": "Claude",
    "claude-code": "Claude",
    "anthropic": "Claude",
    # Gemini variants
    "gemini-cli-mcp-client": "Gemini",
    "gemini-cli": "Gemini",
    "gemini": "Gemini",
    "google": "Gemini",
    # Other known clients
    "cursor": "Cursor",
    "vscode": "VS Code",
    "codeium": "Codeium",
    "copilot": "GitHub Copilot",
    # Generic MCP clients
    "mcp-client": "MCP Client",
    "test-client": "Test Client",
}

# Default friendly name when no match is found
DEFAULT_FRIENDLY_NAME = "Claude"


def get_friendly_name(client_name: str) -> str:
    """
    Map a client name to a friendly name.

    Args:
        client_name: The raw client name from clientInfo

    Returns:
        A friendly name for display (e.g., "Claude", "Gemini")
    """
    if not client_name:
        return DEFAULT_FRIENDLY_NAME

    # Convert to lowercase for case-insensitive matching
    client_name_lower = client_name.lower()

    # Check each mapping - using 'in' to handle partial matches
    for key, friendly_name in CLIENT_NAME_MAPPINGS.items():
        if key.lower() in client_name_lower:
            return friendly_name

    # If no match found, return the default
    return DEFAULT_FRIENDLY_NAME


def get_cached_client_info() -> Optional[dict[str, Any]]:
    """
    Get cached client information if available.

    Returns:
        Cached client info dictionary or None
    """
    return _client_info_cache.get()


def get_client_info_from_context(context: ServerRequestContext | None) -> Optional[dict[str, Any]]:
    """Read the SDK's public per-request identity for the current stdio client."""
    params = context.session.client_params if context is not None else None
    if params is None:
        return None
    client = params.client_info
    return {
        "name": client.name,
        "version": client.version,
        "friendly_name": get_friendly_name(client.name),
    }


@contextmanager
def request_client_info(context: ServerRequestContext):
    """Expose this caller's identity during tool execution, including nested awaits."""
    token = _client_info_cache.set(get_client_info_from_context(context))
    try:
        yield
    finally:
        _client_info_cache.reset(token)


def format_client_info(client_info: Optional[dict[str, Any]], use_friendly_name: bool = True) -> str:
    """
    Format client information for display.

    Args:
        client_info: Dictionary with client info or None
        use_friendly_name: If True, use the friendly name instead of raw name

    Returns:
        Formatted string like "Claude v1.0.0" or "Claude"
    """
    if not client_info:
        return DEFAULT_FRIENDLY_NAME

    if use_friendly_name:
        name = client_info.get("friendly_name", client_info.get("name", DEFAULT_FRIENDLY_NAME))
    else:
        name = client_info.get("name", "Unknown")

    version = client_info.get("version", "")

    if version and not use_friendly_name:
        return f"{name} v{version}"
    else:
        # For friendly names, we just return the name without version
        return name


def get_client_friendly_name() -> str:
    """
    Get the cached client's friendly name.

    This is a convenience function that returns just the friendly name
    from the cached client info, defaulting to "Claude" if not available.

    Returns:
        The friendly name (e.g., "Claude", "Gemini")
    """
    cached_info = get_cached_client_info()
    if cached_info:
        return cached_info.get("friendly_name", DEFAULT_FRIENDLY_NAME)
    return DEFAULT_FRIENDLY_NAME


def log_client_info(context: ServerRequestContext, logger_instance: Optional[logging.Logger] = None) -> None:
    """
    Log client information extracted from the server.

    Args:
        context: The MCP request context
        logger_instance: Optional logger to use (defaults to module logger)
    """
    log = logger_instance or logger

    client_info = get_client_info_from_context(context)
    if client_info:
        # Log with both raw and friendly names for debugging
        raw_name = client_info.get("name", "Unknown")
        friendly_name = client_info.get("friendly_name", DEFAULT_FRIENDLY_NAME)
        version = client_info.get("version", "")

        if raw_name != friendly_name:
            log.info(f"MCP Client Connected: {friendly_name} (raw: {raw_name} v{version})")
        else:
            log.info(f"MCP Client Connected: {friendly_name} v{version}")

        # Log to activity logger as well
        try:
            activity_logger = logging.getLogger("mcp_activity")
            activity_logger.info(f"CLIENT_IDENTIFIED: {friendly_name} (name={raw_name}, version={version})")
        except (OSError, UnicodeError):
            log.warning("Activity log write failed for CLIENT_IDENTIFIED", exc_info=True)
    else:
        log.debug("Could not extract client info from MCP protocol")


# Example usage in tools:
#
# from utils.client_info import get_client_friendly_name, get_cached_client_info
#
# # In a tool's execute method:
# def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
#     # Get the friendly name of the connected client
#     client_name = get_client_friendly_name()  # Returns "Claude" or "Gemini" etc.
#
#     # Or get full cached info if needed
#     client_info = get_cached_client_info()
#     if client_info:
#         raw_name = client_info['name']        # e.g., "claude-ai"
#         version = client_info['version']      # e.g., "1.0.0"
#         friendly = client_info['friendly_name'] # e.g., "Claude"
#
#     # Customize response based on client
#     if client_name == "Claude":
#         response = f"Hello from PAL MCP Server to {client_name}!"
#     elif client_name == "Gemini":
#         response = f"Greetings {client_name}, welcome to PAL MCP Server!"
#     else:
#         response = f"Welcome {client_name}!"
