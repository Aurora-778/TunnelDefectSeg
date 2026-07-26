"""Disabled-by-default Phase A1 claim-gated Memory report Agent."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from orchestrator.agents.base import BaseAgent
from orchestrator.inspection_workflow.a1_artifacts import PhaseA1ArtifactError
from orchestrator.inspection_workflow.a1_reports import write_gated_memory_reports


class MemoryReportAgent(BaseAgent):
    """Render fixed Run-local Memory reports from validated candidate snapshots."""

    name = "memory_report"

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(context, Mapping):
            raise PhaseA1ArtifactError("MemoryReportAgent context must be an object")
        inputs = context.get("inputs", {})
        shared = context.get("shared", {})
        if not isinstance(inputs, Mapping) or not isinstance(shared, Mapping):
            raise PhaseA1ArtifactError("MemoryReportAgent inputs/shared must be objects")
        agent_inputs = inputs.get(self.name, {})
        if not isinstance(agent_inputs, Mapping) or agent_inputs:
            raise PhaseA1ArtifactError("MemoryReportAgent does not accept path or source overrides")
        return write_gated_memory_reports(
            self.project_root(dict(context)),
            run_id=shared.get("run_id"),
            execution_profile=shared.get("execution_profile"),
            plan_fingerprint=shared.get("plan_fingerprint"),
        )


__all__ = ["MemoryReportAgent"]
