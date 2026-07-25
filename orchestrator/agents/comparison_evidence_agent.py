"""Explicitly disabled-by-default Phase A1 Comparison Evidence Agent."""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import BaseAgent
from orchestrator.inspection_workflow.a1_artifacts import (
    PhaseA1ArtifactError,
    write_comparison_evidence_bundle,
    write_prepared_history_comparison_evidence_bundle,
)


class ComparisonEvidenceAgent(BaseAgent):
    """Project or persist Evidence inside an explicit A1 sandbox Run."""

    name = "comparison_evidence"

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        inputs = self.agent_inputs(context)
        projection_fields = {
            "projection_mode",
            "prepared_manifest_path",
            "history_association_path",
            "history_manifest_path",
        }
        if set(inputs) == projection_fields:
            if inputs["projection_mode"] != "prepared_history_sources":
                raise PhaseA1ArtifactError(
                    "ComparisonEvidenceAgent projection_mode must be prepared_history_sources"
                )
            shared = self.shared(context)
            return write_prepared_history_comparison_evidence_bundle(
                self.project_root(context),
                run_id=shared.get("run_id"),
                execution_profile=shared.get("execution_profile"),
                plan_fingerprint=shared.get("plan_fingerprint"),
                prepared_manifest_path=inputs["prepared_manifest_path"],
                history_association_path=inputs["history_association_path"],
                history_manifest_path=inputs["history_manifest_path"],
            )
        elif set(inputs) == {"records", "source_artifacts"}:
            records = inputs["records"]
            source_artifacts = inputs["source_artifacts"]
            shared = self.shared(context)
        else:
            raise PhaseA1ArtifactError(
                "ComparisonEvidenceAgent inputs must be exactly records and source_artifacts, "
                "or the prepared_history_sources projection fields"
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
