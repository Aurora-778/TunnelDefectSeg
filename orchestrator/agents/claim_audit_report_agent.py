"""Controlled Phase A1 Staging renderer for ClaimDecision."""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import BaseAgent
from orchestrator.inspection_workflow.a1_artifacts import (
    PhaseA1ArtifactError,
    write_gated_claim_audit_report,
)


class ClaimAuditReportAgent(BaseAgent):
    """Render one non-published report from validated Run-local claim artifacts."""

    name = "claim_audit_report"

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        inputs = self.agent_inputs(context)
        if inputs:
            raise PhaseA1ArtifactError(
                "ClaimAuditReportAgent does not accept path or output overrides"
            )
        shared = self.shared(context)
        return write_gated_claim_audit_report(
            self.project_root(context),
            run_id=shared.get("run_id"),
            execution_profile=shared.get("execution_profile"),
            plan_fingerprint=shared.get("plan_fingerprint"),
        )


__all__ = ["ClaimAuditReportAgent"]
