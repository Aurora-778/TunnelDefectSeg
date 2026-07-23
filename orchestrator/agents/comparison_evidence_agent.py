"""Explicitly disabled-by-default Phase A1 Comparison Evidence Agent."""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import BaseAgent
from orchestrator.inspection_workflow.a1_artifacts import (
    PhaseA1ArtifactError,
    write_comparison_evidence_bundle,
)


class ComparisonEvidenceAgent(BaseAgent):
    """Persist already normalized Evidence inside an explicit A1 sandbox Run."""

    name = "comparison_evidence"

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        inputs = self.agent_inputs(context)
        if set(inputs) != {"records", "source_artifacts"}:
            raise PhaseA1ArtifactError(
                "ComparisonEvidenceAgent inputs must be exactly records and source_artifacts"
            )
        shared = self.shared(context)
        return write_comparison_evidence_bundle(
            self.project_root(context),
            run_id=shared.get("run_id"),
            execution_profile=shared.get("execution_profile"),
            plan_fingerprint=shared.get("plan_fingerprint"),
            records=inputs["records"],
            source_artifacts=inputs["source_artifacts"],
        )


__all__ = ["ComparisonEvidenceAgent"]
