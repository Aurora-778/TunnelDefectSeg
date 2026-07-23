"""Explicitly disabled-by-default Phase A1 Comparison Evidence Agent."""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import BaseAgent
from orchestrator.inspection_workflow.a1_artifacts import (
    PhaseA1ArtifactError,
    write_comparison_evidence_bundle,
    write_projected_comparison_evidence_bundle,
)


class ComparisonEvidenceAgent(BaseAgent):
    """Project or persist Evidence inside an explicit A1 sandbox Run."""

    name = "comparison_evidence"

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        inputs = self.agent_inputs(context)
        if set(inputs) == {"projection_mode"}:
            if inputs["projection_mode"] != "run_local_sources":
                raise PhaseA1ArtifactError(
                    "ComparisonEvidenceAgent projection_mode must be run_local_sources"
                )
            shared = self.shared(context)
            return write_projected_comparison_evidence_bundle(
                self.project_root(context),
                run_id=shared.get("run_id"),
                execution_profile=shared.get("execution_profile"),
                plan_fingerprint=shared.get("plan_fingerprint"),
            )
        elif set(inputs) == {"records", "source_artifacts"}:
            records = inputs["records"]
            source_artifacts = inputs["source_artifacts"]
            shared = self.shared(context)
        else:
            raise PhaseA1ArtifactError(
                "ComparisonEvidenceAgent inputs must be exactly records and source_artifacts, "
                "or exactly projection_mode=run_local_sources"
            )
        return write_comparison_evidence_bundle(
            self.project_root(context),
            run_id=shared.get("run_id"),
            execution_profile=shared.get("execution_profile"),
            plan_fingerprint=shared.get("plan_fingerprint"),
            records=records,
            source_artifacts=source_artifacts,
        )


__all__ = ["ComparisonEvidenceAgent"]
