"""Agent wrapper for visualization and priority recheck outputs."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt

from orchestrator.agents.base import BaseAgent
from scripts import generate_visualization_and_recheck_list


class VisualizationAgent(BaseAgent):
    """Generate charts, association graph, and priority recheck list."""

    name = "visualization"

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        inputs = self.agent_inputs(context)
        engineering_report = self.resolve_path(context, self._required_input(inputs, "engineering_report"))
        growth_results = self.resolve_path(context, self._required_input(inputs, "growth_results"))
        association_records = self.resolve_path(context, self._required_input(inputs, "association_records"))
        recheck_list = self.resolve_path(context, self._required_input(inputs, "recheck_list"))
        visualization_dir = self.resolve_path(context, self._required_input(inputs, "visualization_dir"))
        visualization_report = self.resolve_path(context, self._required_input(inputs, "visualization_report"))
        recheck_report = self.resolve_path(context, self._required_input(inputs, "recheck_report"))
        visualization_summary = self.resolve_path(context, self._required_input(inputs, "visualization_summary"))
        association_graph = self.resolve_path(context, self._required_input(inputs, "association_graph"))

        font_note = generate_visualization_and_recheck_list.configure_matplotlib_font()
        growth_rows = generate_visualization_and_recheck_list.read_growth_csv(growth_results)
        engineering_rows, engineering_note = generate_visualization_and_recheck_list.read_engineering_csv(engineering_report)
        generate_visualization_and_recheck_list.ensure_output_dir(visualization_dir)

        chart_paths = generate_visualization_and_recheck_list.generate_standard_visualizations(growth_rows, visualization_dir)
        mileage_rows = generate_visualization_and_recheck_list.build_mileage_risk_table(engineering_rows, growth_rows)
        chart_paths.append(generate_visualization_and_recheck_list.generate_mileage_visualization(mileage_rows, visualization_dir))

        recheck_rows = generate_visualization_and_recheck_list.build_priority_recheck_list(growth_rows)
        generate_visualization_and_recheck_list.write_recheck_csv(recheck_rows, recheck_list)
        generate_visualization_and_recheck_list.write_visualization_report(
            growth_rows,
            engineering_report,
            growth_results,
            chart_paths,
            font_note,
            engineering_note,
            visualization_report,
        )
        generate_visualization_and_recheck_list.write_recheck_report(recheck_rows, recheck_report, recheck_list)
        generate_visualization_and_recheck_list.write_summary_report(
            growth_results,
            engineering_report,
            recheck_list,
            chart_paths,
            visualization_report,
            recheck_report,
            visualization_summary,
            growth_rows,
            recheck_rows,
        )
        generate_visualization_and_recheck_list.validate_generated_outputs(
            chart_paths,
            recheck_list,
            [visualization_report, recheck_report, visualization_summary],
        )
        self._write_association_graph(association_records, association_graph)

        return {
            "recheck_list_path": str(recheck_list),
            "visualization_report_path": str(visualization_report),
            "recheck_report_path": str(recheck_report),
            "visualization_summary_path": str(visualization_summary),
            "association_graph_path": str(association_graph),
            "chart_count": len(chart_paths) + 1,
            "recheck_rows": len(recheck_rows),
        }

    def _write_association_graph(self, association_csv: Path, output_path: Path) -> Path:
        rows = self._read_csv(association_csv)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        counts = Counter(row.get("disease_id", "") for row in rows if row.get("association_status") == "matched")
        labels = list(counts)[:12] or ["no_match"]
        values = [counts[label] for label in labels] or [0]

        # 关系图保留英文坐标，减少测试和无中文字体环境下的图形 warning。
        fig, ax = plt.subplots(figsize=(max(7, len(labels) * 0.8), 4.8), dpi=120)
        bars = ax.bar(labels, values, color="#14b8a6", edgecolor="#0f172a")
        ax.set_title("Disease Association Graph")
        ax.set_xlabel("disease_id")
        ax.set_ylabel("matched frame records")
        ax.grid(axis="y", linestyle="--", alpha=0.25)
        ax.bar_label(bars, padding=3)
        fig.tight_layout()
        fig.savefig(output_path)
        plt.close(fig)
        return output_path

    def _read_csv(self, path: Path) -> list[dict[str, str]]:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def _required_input(self, inputs: dict[str, Any], key: str) -> str:
        value = inputs.get(key)
        if not value:
            raise ValueError(f"VisualizationAgent missing required input: {key}")
        return str(value)
