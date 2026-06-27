"""Agent wrapper for cross-inspection disease growth analysis."""

from __future__ import annotations

import shutil
from typing import Any

from orchestrator.agents.base import BaseAgent
from scripts import analyze_disease_growth


class GrowthAnalysisAgent(BaseAgent):
    """Generate growth trends and attention levels from engineering records."""

    name = "growth_analysis"

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        inputs = self.agent_inputs(context)
        engineering_report = self.resolve_path(context, self._required_input(inputs, "engineering_report"))
        output_path = self.resolve_path(context, self._required_input(inputs, "output_path"))
        legacy_output_path = self.resolve_path(context, self._required_input(inputs, "legacy_output_path"))
        markdown_path = self.resolve_path(context, self._required_input(inputs, "markdown_path"))
        summary_path = self.resolve_path(context, self._required_input(inputs, "summary_path"))

        rows = analyze_disease_growth.read_input_rows(engineering_report)
        records = analyze_disease_growth.aggregate_rows(rows)
        analyze_disease_growth.write_csv_report(records, output_path)
        analyze_disease_growth.write_markdown_report(records, rows, engineering_report, markdown_path)
        analyze_disease_growth.write_summary_report(
            engineering_report,
            output_path,
            markdown_path,
            summary_path,
            rows,
            records,
        )
        # 保留旧文件名，避免 Web Dashboard 和历史脚本断链。
        legacy_output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(output_path, legacy_output_path)

        return {
            "growth_results_path": str(output_path),
            "legacy_growth_analysis_path": str(legacy_output_path),
            "growth_markdown_path": str(markdown_path),
            "growth_summary_path": str(summary_path),
            "growth_rows": len(records),
        }

    def _required_input(self, inputs: dict[str, Any], key: str) -> str:
        value = inputs.get(key)
        if not value:
            raise ValueError(f"GrowthAnalysisAgent missing required input: {key}")
        return str(value)
