"""Agent wrapper for engineering disease report generation."""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import BaseAgent
from scripts import generate_engineering_report


class EngineeringReportAgent(BaseAgent):
    """Generate object-level engineering records from robot frame records."""

    name = "engineering_report"

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        inputs = self.agent_inputs(context)
        frame_records = self.resolve_path(context, self._required_input(inputs, "frame_records"))
        output_path = self.resolve_path(context, self._required_input(inputs, "output_path"))
        markdown_path = self.resolve_path(context, self._required_input(inputs, "markdown_path"))
        summary_path = self.resolve_path(context, self._required_input(inputs, "summary_path"))

        rows = generate_engineering_report.read_input_rows(frame_records)
        records = generate_engineering_report.aggregate_rows(rows)
        generate_engineering_report.write_csv_report(records, output_path)
        generate_engineering_report.write_markdown_report(records, frame_records, markdown_path)
        generate_engineering_report.write_summary_report(
            frame_records,
            output_path,
            markdown_path,
            summary_path,
            rows,
            records,
        )

        return {
            "engineering_report_path": str(output_path),
            "engineering_markdown_path": str(markdown_path),
            "engineering_summary_path": str(summary_path),
            "engineering_rows": len(records),
        }

    def _required_input(self, inputs: dict[str, Any], key: str) -> str:
        value = inputs.get(key)
        if not value:
            raise ValueError(f"EngineeringReportAgent missing required input: {key}")
        return str(value)
