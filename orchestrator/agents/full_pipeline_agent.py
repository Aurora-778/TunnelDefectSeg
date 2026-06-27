"""Deprecated compatibility wrapper for the old single-node pipeline task."""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import BaseAgent


class FullPipelineAgent(BaseAgent):
    """Deprecated wrapper.

    The default pipeline uses config/dag.yaml with separate business stages.
    Keep this class only for older callers that still reference full_pipeline.
    """

    name = "full_pipeline"

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        from run import run_full_pipeline_direct

        result = run_full_pipeline_direct(self.project_root(context))
        return result
