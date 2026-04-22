"""Agent factory for clink CLI integrations."""

from __future__ import annotations

from clink.models import ResolvedCLIClient

from .base import AgentOutput, BaseCLIAgent, CLIAgentError
from .claude import ClaudeAgent
from .codex import CodexAgent
from .gemini import GeminiAgent

_AGENTS: dict[str, type[BaseCLIAgent]] = {
    "gemini": GeminiAgent,
    "codex": CodexAgent,
    "claude": ClaudeAgent,
}


def get_agent_class(client: ResolvedCLIClient) -> type[BaseCLIAgent]:
    """Look up the agent class for a client without instantiating it.

    Use this when you need to check class-level attributes (e.g.
    ``supports_path_restrictions``) during request validation — before any
    I/O or object construction has happened.
    """
    agent_key = (client.runner or client.name).lower()
    return _AGENTS.get(agent_key, BaseCLIAgent)


def create_agent(client: ResolvedCLIClient) -> BaseCLIAgent:
    return get_agent_class(client)(client)


__all__ = [
    "AgentOutput",
    "BaseCLIAgent",
    "CLIAgentError",
    "create_agent",
    "get_agent_class",
]
