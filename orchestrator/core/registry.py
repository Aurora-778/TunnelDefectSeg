"""Default agent registry."""

from pathlib import Path

from orchestrator.agents.association_agent import AssociationAgent
from orchestrator.agents.doc_agent import DocAgent
from orchestrator.agents.memory_agent import MemoryAgent
from orchestrator.agents.web_agent import WebAgent


def build_default_registry(project_root: Path) -> dict[str, object]:
    """Create the v1 goal-to-agent mapping."""
    return {
        "memory": MemoryAgent(project_root),
        "association": AssociationAgent(project_root),
        "web": WebAgent(project_root),
        "doc": DocAgent(project_root),
    }
