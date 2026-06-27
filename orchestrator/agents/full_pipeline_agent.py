"""Run the application-level closed-loop pipeline as a DAG task."""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import BaseAgent


class FullPipelineAgent(BaseAgent):
    """Thin agent wrapper around the robot inspection application pipeline."""

    name = "full_pipeline"

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        from run import run_full_pipeline_direct

        result = run_full_pipeline_direct(self.project_root(context))
        context.setdefault("outputs", {})[self.name] = result
        return result
