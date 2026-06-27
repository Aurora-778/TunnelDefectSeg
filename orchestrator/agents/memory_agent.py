"""Build a long-term disease memory bank from inspection records."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from orchestrator.agents.base import BaseAgent


class MemoryAgent(BaseAgent):
    """Aggregate disease records into one memory item per disease_id."""

    name = "memory"

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        inputs = self.agent_inputs(context)
        report_path = self.resolve_path(context, self._required_input(inputs, "engineering_report"))
        growth_path = self.resolve_path(context, self._required_input(inputs, "growth_analysis"))
        output_path = self.resolve_path(context, self._required_input(inputs, "output_path"))
        report_path_md = self.resolve_path(context, self._required_input(inputs, "report_path"))
        log_path = self.resolve_path(context, self._required_input(inputs, "log_path"))

        self._log(log_path, "start")
        report_rows = self.read_csv(report_path)
        growth_rows = self.read_csv(growth_path)
        growth_by_id = {row.get("disease_id", ""): row for row in growth_rows}
        grouped_rows = self._group_by_disease(report_rows)

        rows: list[dict[str, Any]] = []
        for disease_id in sorted(grouped_rows):
            disease_rows = sorted(grouped_rows[disease_id], key=self._inspection_sort_key)
            first = disease_rows[0]
            last = disease_rows[-1]
            growth = growth_by_id.get(disease_id, {})
            first_area = self._to_float(first.get("max_area_px"))
            last_area = self._to_float(last.get("max_area_px"))
            area_growth_px = last_area - first_area
            area_growth_rate = area_growth_px / first_area if first_area else 0.0
            max_area = max(self._to_float(row.get("max_area_px")) for row in disease_rows)
            first_risk = first.get("risk_level", "")
            last_risk = last.get("risk_level", "")
            risk_change = self._risk_score(last_risk) - self._risk_score(first_risk)
            growth_trend = growth.get("growth_trend") or self._growth_trend(area_growth_rate)
            attention_level = growth.get("attention_level") or self._attention_level(area_growth_rate, last_risk)
            total_seen_frames = sum(int(self._to_float(row.get("frame_count"))) for row in disease_rows)
            source_inspection_ids = sorted({row.get("inspection_id", "") for row in disease_rows if row.get("inspection_id", "")})
            first_inspection = first.get("inspection_id", "")
            last_inspection = last.get("inspection_id", "")
            disease_type = first.get("disease_type", "")
            main_clock_direction = growth.get("main_clock_direction") or last.get("main_clock_direction", "")
            first_mileage = first.get("start_mileage_text", "")
            last_mileage = last.get("end_mileage_text", "")

            rows.append(
                {
                    "memory_id": f"MEM-{disease_id}",
                    "memory_version": "v1",
                    "disease_id": disease_id,
                    "disease_type": disease_type,
                    "source_record_count": len(disease_rows),
                    "source_inspection_ids": "|".join(source_inspection_ids),
                    "memory_update_mode": "batch_rebuild",
                    "memory_confidence": self._memory_confidence(disease_rows, growth),
                    "memory_limit_note": "KICT static masks + simulated inspection metadata; not real longitudinal evidence",
                    "first_seen_inspection": first_inspection,
                    "last_seen_inspection": last_inspection,
                    "inspection_count": len({row.get("inspection_id", "") for row in disease_rows}),
                    "total_seen_frames": total_seen_frames,
                    "first_area_px": self._format_number(first_area),
                    "last_area_px": self._format_number(last_area),
                    "max_area_px": self._format_number(max_area),
                    "area_growth_px": self._format_number(area_growth_px),
                    "area_growth_rate": f"{area_growth_rate:.6f}",
                    "first_risk_level": first_risk,
                    "last_risk_level": last_risk,
                    "risk_level_change": risk_change,
                    "growth_trend": growth_trend,
                    "attention_level": attention_level,
                    "main_clock_direction": main_clock_direction,
                    "mileage_range": self._join_range(first_mileage, last_mileage),
                    "representative_image_path": last.get("representative_image_path", ""),
                    "representative_mask_path": last.get("representative_mask_path", ""),
                    "memory_description": self._memory_description(
                        disease_id=disease_id,
                        disease_type=disease_type,
                        first_seen=first_inspection,
                        last_seen=last_inspection,
                        growth_trend=growth_trend,
                        area_growth_rate=area_growth_rate,
                        last_risk=last_risk,
                        attention_level=attention_level,
                    ),
                }
            )

        fieldnames = [
            "memory_id",
            "memory_version",
            "disease_id",
            "disease_type",
            "source_record_count",
            "source_inspection_ids",
            "memory_update_mode",
            "memory_confidence",
            "memory_limit_note",
            "first_seen_inspection",
            "last_seen_inspection",
            "inspection_count",
            "total_seen_frames",
            "first_area_px",
            "last_area_px",
            "max_area_px",
            "area_growth_px",
            "area_growth_rate",
            "first_risk_level",
            "last_risk_level",
            "risk_level_change",
            "growth_trend",
            "attention_level",
            "main_clock_direction",
            "mileage_range",
            "representative_image_path",
            "representative_mask_path",
            "memory_description",
        ]
        self.write_csv(output_path, rows, fieldnames)

        summary_path = self.resolve_path(context, inputs.get("summary_path", "outputs/disease_memory_bank_summary.md"))
        self._write_reports(summary_path, report_path_md, report_path, growth_path, output_path, rows)
        self._log(log_path, f"disease count: {len(grouped_rows)}")
        self._log(log_path, f"row count: {len(rows)}")
        self._log(log_path, "end")

        result = {
            "disease_memory_bank_path": str(output_path),
            "memory_bank_path": str(output_path),
            "memory_bank_rows": len(rows),
            "memory_agent_report_path": str(report_path_md),
            "memory_summary_path": str(summary_path),
            "memory_agent_log_path": str(log_path),
        }
        return result

    def _join_range(self, start: str, end: str) -> str:
        if start and end and start != end:
            return f"{start} - {end}"
        return start or end

    def _required_input(self, inputs: dict[str, Any], key: str) -> str:
        value = inputs.get(key)
        if not value:
            raise ValueError(f"MemoryAgent missing required input: {key}")
        return str(value)

    def _group_by_disease(self, report_rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
        grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in report_rows:
            disease_id = row.get("disease_id", "").strip()
            if disease_id:
                grouped[disease_id].append(row)
        if not grouped:
            raise ValueError("No disease_id records found in disease_engineering_report.csv")
        return grouped

    def _inspection_sort_key(self, row: dict[str, str]) -> tuple[int, str]:
        inspection_id = row.get("inspection_id", "")
        digits = "".join(ch for ch in inspection_id if ch.isdigit())
        return (int(digits) if digits else 0, inspection_id)

    def _to_float(self, value: object) -> float:
        try:
            if value in (None, ""):
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _format_number(self, value: float) -> str:
        return str(int(value)) if float(value).is_integer() else f"{value:.2f}"

    def _risk_score(self, risk_level: str) -> int:
        normalized = risk_level.strip().lower()
        mapping = {
            "低": 1,
            "low": 1,
            "中": 2,
            "medium": 2,
            "高": 3,
            "high": 3,
        }
        return mapping.get(normalized, 0)

    def _growth_trend(self, area_growth_rate: float) -> str:
        # A small dead band avoids calling tiny numeric noise real growth.
        if area_growth_rate >= 0.05:
            return "明显增长"
        if area_growth_rate <= -0.05:
            return "面积减小"
        return "基本稳定"

    def _attention_level(self, area_growth_rate: float, risk_level: str) -> str:
        risk_score = self._risk_score(risk_level)
        if risk_score >= 3 or area_growth_rate >= 0.2:
            return "重点关注"
        if risk_score == 2 or area_growth_rate >= 0.05:
            return "持续观察"
        return "常规记录"

    def _memory_confidence(self, disease_rows: list[dict[str, str]], growth: dict[str, str]) -> str:
        inspection_count = len({row.get("inspection_id", "") for row in disease_rows if row.get("inspection_id", "")})
        if inspection_count >= 3:
            return "medium"
        if inspection_count == 2:
            return "low"
        return "very_low"

    def _memory_description(
        self,
        disease_id: str,
        disease_type: str,
        first_seen: str,
        last_seen: str,
        growth_trend: str,
        area_growth_rate: float,
        last_risk: str,
        attention_level: str,
    ) -> str:
        type_name = {
            "crack": "裂缝",
            "spalling": "剥落",
            "water_leakage": "渗水",
        }.get(disease_type, disease_type or "病害")
        trend_name = {
            "increasing": "明显增长",
            "stable": "基本稳定",
            "decreasing": "下降",
        }.get(growth_trend, growth_trend)
        return (
            f"病害{disease_id}为{type_name}，首次出现于{first_seen}，末次出现于{last_seen}，"
            f"面积变化率约为{area_growth_rate * 100:.1f}%，呈{trend_name}趋势，"
            f"末次风险等级为{last_risk}，当前关注等级为{attention_level}。"
        )

    def _write_reports(
        self,
        summary_path: Path,
        report_path: Path,
        source_report_path: Path,
        source_growth_path: Path,
        output_path: Path,
        rows: list[dict[str, Any]],
    ) -> None:
        increasing_count = sum(1 for row in rows if row.get("growth_trend") in {"明显增长", "increasing"})
        high_attention_count = sum(1 for row in rows if row.get("attention_level") in {"重点关注", "high"})
        high_risk_count = sum(1 for row in rows if str(row.get("last_risk_level", "")).lower() in ("高", "high"))
        lines = [
            f"- 输入工程报告：`{source_report_path}`",
            f"- 输入增长分析：`{source_growth_path}`",
            f"- 输出记忆库：`{output_path}`",
            f"- disease总数：{len(rows)}",
            f"- 重点增长病害数量：{increasing_count}",
            f"- 高关注病害数量：{high_attention_count}",
            f"- 高风险病害数量：{high_risk_count}",
            "",
            "统计总结：Memory Agent 已按 disease_id 汇总跨巡检记录，形成首末巡检、面积增长、风险变化、趋势和关注等级等长期记忆字段。",
        ]
        self.write_markdown(summary_path, "Disease Memory Bank Summary", lines)
        self.write_markdown(report_path, "Memory Agent Report", lines)

    def _log(self, log_path: Path, message: str) -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().isoformat(timespec="seconds")
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"{timestamp} {message}\n")
