"""Disabled-by-default Phase A1 Claim-gated visualization Agent."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from orchestrator.agents.base import BaseAgent
from orchestrator.inspection_workflow.a1_artifacts import PhaseA1ArtifactError
from orchestrator.inspection_workflow.a1_visualization import write_gated_visualization_outputs


class ClaimVisualizationAgent(BaseAgent):
    name = "claim_visualization"

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(context, Mapping):
            raise PhaseA1ArtifactError("ClaimVisualizationAgent context must be an object")
        inputs = context.get("inputs", {})
        shared = context.get("shared", {})
        if not isinstance(inputs, Mapping) or not isinstance(shared, Mapping):
            raise PhaseA1ArtifactError("ClaimVisualizationAgent inputs/shared must be objects")
        agent_inputs = inputs.get(self.name, {})
        if not isinstance(agent_inputs, Mapping) or agent_inputs:
            raise PhaseA1ArtifactError("ClaimVisualizationAgent does not accept path or source overrides")
        return write_gated_visualization_outputs(
            self.project_root(dict(context)),
            run_id=shared.get("run_id"),
            execution_profile=shared.get("execution_profile"),
            plan_fingerprint=shared.get("plan_fingerprint"),
        )


__all__ = ["ClaimVisualizationAgent"]
