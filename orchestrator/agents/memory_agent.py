"""Build a disease memory bank from engineering and growth records."""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import BaseAgent


class MemoryAgent(BaseAgent):
    """Convert current disease reports into a reusable memory table."""

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        report_path = self.data_dir / "disease_engineering_report.csv"
        growth_path = self.data_dir / "disease_growth_analysis.csv"
        output_path = self.data_dir / "disease_memory_bank.csv"

        report_rows = self.read_csv(report_path)
        growth_rows = self.read_csv(growth_path)
        growth_by_id = {row.get("disease_id", ""): row for row in growth_rows}

        rows: list[dict[str, Any]] = []
        for item in report_rows:
            disease_id = item.get("disease_id", "")
            growth = growth_by_id.get(disease_id, {})
            rows.append(
                {
                    "memory_id": f"MEM-{disease_id}",
                    "disease_id": disease_id,
                    "disease_type": item.get("disease_type", ""),
                    "risk_level": item.get("risk_level", ""),
                    "attention_level": growth.get("attention_level", ""),
                    "growth_trend": growth.get("growth_trend", ""),
                    "main_clock_direction": item.get("main_clock_direction", ""),
                    "mileage_range": self._join_range(
                        item.get("start_mileage_text", ""),
                        item.get("end_mileage_text", ""),
                    ),
                    "max_area_px": item.get("max_area_px", ""),
                    "area_growth_px": growth.get("area_growth_px", ""),
                    "area_growth_rate": growth.get("area_growth_rate", ""),
                    "representative_image_path": item.get("representative_image_path", ""),
                    "representative_mask_path": item.get("representative_mask_path", ""),
                    "engineering_description": item.get("engineering_description", ""),
                }
            )

        fieldnames = [
            "memory_id",
            "disease_id",
            "disease_type",
            "risk_level",
            "attention_level",
            "growth_trend",
            "main_clock_direction",
            "mileage_range",
            "max_area_px",
            "area_growth_px",
            "area_growth_rate",
            "representative_image_path",
            "representative_mask_path",
            "engineering_description",
        ]
        self.write_csv(output_path, rows, fieldnames)

        summary_path = self.outputs_dir / "disease_memory_bank_summary.md"
        self.write_markdown(
            summary_path,
            "Disease Memory Bank Summary",
            [
                f"- 输入工程报告：`{report_path}`",
                f"- 输入增长分析：`{growth_path}`",
                f"- 输出记忆库：`{output_path}`",
                f"- 记忆对象数量：{len(rows)}",
                "",
                "该表把病害编号、类型、风险、增长趋势和代表性图像路径整理为可复用记忆，后续可用于跨巡检关联和复检排序。",
            ],
        )

        return {
            "memory_bank_path": str(output_path),
            "memory_bank_rows": len(rows),
            "memory_summary_path": str(summary_path),
        }

    def _join_range(self, start: str, end: str) -> str:
        if start and end and start != end:
            return f"{start} - {end}"
        return start or end
