"""Explicitly disabled-by-default Phase A1 Claim Gate Agent."""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import BaseAgent
from orchestrator.inspection_workflow.a1_artifacts import (
    PhaseA1ArtifactError,
    write_claim_decision_artifact,
)


class ClaimGateAgent(BaseAgent):
    """Build the Run-local ClaimDecision from the committed Evidence bundle."""

    name = "claim_gate"

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        inputs = self.agent_inputs(context)
        if inputs:
            raise PhaseA1ArtifactError("ClaimGateAgent does not accept path or output overrides")
        shared = self.shared(context)
        return write_claim_decision_artifact(
            self.project_root(context),
            run_id=shared.get("run_id"),
            execution_profile=shared.get("execution_profile"),
            plan_fingerprint=shared.get("plan_fingerprint"),
        )


__all__ = ["ClaimGateAgent"]
