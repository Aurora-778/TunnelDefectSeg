"""Plugin registry for orchestrator agents."""

from __future__ import annotations

from orchestrator.base_agent import BaseAgent


class AgentRegistry:
    """Manage named agent plugins."""

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        if not agent.name:
            raise ValueError("Agent must define a non-empty name")
        self._agents[agent.name] = agent

    def get(self, name: str) -> BaseAgent:
        if name not in self._agents:
            raise KeyError(f"Agent is not registered: {name}")
        return self._agents[name]

    def list(self) -> list[str]:
        return sorted(self._agents)


def build_default_registry() -> AgentRegistry:
    """Register the project agents without coupling Orchestrator to business code."""
    from orchestrator.agents.association_agent import AssociationAgent
    from orchestrator.agents.doc_agent import DocAgent
    from orchestrator.agents.memory_agent import MemoryAgent
    from orchestrator.agents.web_agent import WebAgent

    registry = AgentRegistry()
    for agent in (MemoryAgent(), AssociationAgent(), WebAgent(), DocAgent()):
        registry.register(agent)
    return registry
