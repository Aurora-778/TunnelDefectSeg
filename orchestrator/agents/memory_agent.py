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
        mode = str(inputs.get("mode", "batch_rebuild"))
        if mode == "incremental_update":
            return self._run_incremental_update(context, inputs)
        if mode != "batch_rebuild":
            raise ValueError(f"Unsupported memory update mode: {mode}")

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
                    "requires_manual_review": "false",
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
            "requires_manual_review",
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

    def _run_incremental_update(self, context: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        previous_path = self.resolve_path(context, self._required_input(inputs, "previous_memory"))
        frame_path = self.resolve_path(context, self._required_input(inputs, "frame_records"))
        association_path = self.resolve_path(context, self._required_input(inputs, "association_records"))
        output_path = self.resolve_path(context, self._required_input(inputs, "output_path"))
        report_path = self.resolve_path(context, self._required_input(inputs, "report_path"))
        log_path = self.resolve_path(context, self._required_input(inputs, "log_path"))

        self._log(log_path, "start incremental_update")
        memory_rows = self.read_csv(previous_path)
        frame_rows = self.read_csv(frame_path)
        association_rows = self.read_csv(association_path)
        if not memory_rows:
            raise ValueError("incremental_update requires a non-empty previous memory bank")

        fieldnames = list(memory_rows[0].keys())
        for extra in ["requires_manual_review"]:
            if extra not in fieldnames:
                fieldnames.append(extra)

        memory_by_id = {row.get("memory_id", ""): dict(row) for row in memory_rows}
        frame_by_key = self._unique_rows_by_key(frame_rows, row_label="frame")
        self._ensure_unique_association_keys(association_rows)
        frames_by_image: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in frame_rows:
            frames_by_image[row.get("image_id", "")].append(row)
        version = self._next_version(memory_rows)
        skipped_associations: list[str] = []

        for association in association_rows:
            frame = self._find_frame_for_association(association, frame_by_key, frames_by_image)
            if not frame:
                skipped_associations.append(association.get("association_id") or self._key_label(self._frame_key(association)))
                continue
            memory_id = association.get("memory_id", "")
            matched = association.get("association_status") == "matched"
            needs_review = association.get("needs_manual_review") == "true"
            if matched and memory_id in memory_by_id:
                memory_by_id[memory_id] = self._update_memory_row(
                    memory_by_id[memory_id],
                    frame,
                    version,
                    requires_review=needs_review,
                )
            else:
                provisional = self._new_provisional_memory(frame, version, requires_review=True)
                memory_by_id[provisional["memory_id"]] = provisional

        rows = [memory_by_id[key] for key in sorted(memory_by_id)]
        self.write_csv(output_path, rows, fieldnames)
        self.write_markdown(
            report_path,
            "Incremental Memory Update Report",
            [
                f"- previous memory: `{previous_path}`",
                f"- frame records: `{frame_path}`",
                f"- association records: `{association_path}`",
                f"- output memory: `{output_path}`",
                f"- memory rows: {len(rows)}",
                f"- skipped associations without frame: {len(skipped_associations)}",
                "",
                "说明：该模式只使用历史 memory 与当前巡检关联结果更新记忆库，避免匹配阶段读取未来巡检聚合结果。",
            ],
        )
        self._log(log_path, f"row count: {len(rows)}")
        self._log(log_path, f"skipped associations without frame: {len(skipped_associations)}")
        self._log(log_path, "end incremental_update")
        return {
            "disease_memory_bank_path": str(output_path),
            "memory_bank_path": str(output_path),
            "memory_bank_rows": len(rows),
            "memory_agent_report_path": str(report_path),
            "memory_agent_log_path": str(log_path),
        }

    def _join_range(self, start: str, end: str) -> str:
        if start and end and start != end:
            return f"{start} - {end}"
        return start or end

    def _update_memory_row(
        self,
        memory: dict[str, str],
        frame: dict[str, str],
        version: str,
        *,
        requires_review: bool = False,
    ) -> dict[str, str]:
        updated = dict(memory)
        unresolved_review = requires_review or self._as_bool(updated.get("requires_manual_review"))
        inspection_id = frame.get("inspection_id", "")
        source_ids = [value for value in updated.get("source_inspection_ids", "").split("|") if value]
        if inspection_id and inspection_id not in source_ids:
            source_ids.append(inspection_id)
        first_area = self._to_float(updated.get("first_area_px"))
        last_area = self._to_float(frame.get("kict_area_px"))
        max_area = max(self._to_float(updated.get("max_area_px")), last_area)
        area_growth = last_area - first_area
        area_rate = area_growth / first_area if first_area else 0.0
        last_risk = self._risk_from_area(last_area)
        first_risk = updated.get("first_risk_level", last_risk)

        updated.update(
            {
                "memory_version": version,
                "memory_update_mode": "incremental_update",
                "memory_confidence": self._confidence_from_inspection_count(len(source_ids), requires_review=unresolved_review),
                "memory_limit_note": "Incremental CSV memory from historical records and current association; rule evidence only",
                "last_seen_inspection": inspection_id,
                "inspection_count": str(len(source_ids)),
                "source_record_count": str(int(self._to_float(updated.get("source_record_count"))) + 1),
                "source_inspection_ids": "|".join(sorted(source_ids, key=self._inspection_id_sort_key)),
                "total_seen_frames": str(int(self._to_float(updated.get("total_seen_frames"))) + 1),
                "last_area_px": self._format_number(last_area),
                "max_area_px": self._format_number(max_area),
                "area_growth_px": self._format_number(area_growth),
                "area_growth_rate": f"{area_rate:.6f}",
                "last_risk_level": last_risk,
                "risk_level_change": str(self._risk_score(last_risk) - self._risk_score(first_risk)),
                "growth_trend": self._growth_trend(area_rate),
                "attention_level": self._attention_level(area_rate, last_risk),
                "main_clock_direction": frame.get("clock_direction", updated.get("main_clock_direction", "")),
                "mileage_range": self._join_range(updated.get("mileage_range", ""), frame.get("mileage_text", "")),
                "representative_image_path": frame.get("kict_image_path", ""),
                "representative_mask_path": frame.get("kict_mask_path", ""),
                "requires_manual_review": "true" if unresolved_review else "false",
            }
        )
        updated["memory_description"] = self._memory_description(
            disease_id=updated.get("disease_id", ""),
            disease_type=updated.get("disease_type", ""),
            first_seen=updated.get("first_seen_inspection", ""),
            last_seen=updated.get("last_seen_inspection", ""),
            growth_trend=updated.get("growth_trend", ""),
            area_growth_rate=area_rate,
            last_risk=last_risk,
            attention_level=updated.get("attention_level", ""),
        )
        return updated

    def _new_provisional_memory(self, frame: dict[str, str], version: str, *, requires_review: bool) -> dict[str, str]:
        inspection_id = frame.get("inspection_id", "")
        disease_id = frame.get("disease_id") or frame.get("image_id", "unknown")
        area = self._to_float(frame.get("kict_area_px"))
        risk = self._risk_from_area(area)
        memory_id = f"MEM-PROV-{inspection_id}-{frame.get('frame_id', '')}-{disease_id}"
        return {
            "memory_id": memory_id,
            "memory_version": version,
            "disease_id": disease_id,
            "disease_type": frame.get("disease_type", ""),
            "source_record_count": "1",
            "source_inspection_ids": inspection_id,
            "memory_update_mode": "incremental_update",
            "memory_confidence": "very_low",
            "memory_limit_note": "Provisional memory from unmatched or uncertain association; requires manual review",
            "first_seen_inspection": inspection_id,
            "last_seen_inspection": inspection_id,
            "inspection_count": "1",
            "total_seen_frames": "1",
            "first_area_px": self._format_number(area),
            "last_area_px": self._format_number(area),
            "max_area_px": self._format_number(area),
            "area_growth_px": "0",
            "area_growth_rate": "0.000000",
            "first_risk_level": risk,
            "last_risk_level": risk,
            "risk_level_change": "0",
            "growth_trend": "数据不足",
            "attention_level": "待补充巡检",
            "main_clock_direction": frame.get("clock_direction", ""),
            "mileage_range": frame.get("mileage_text", ""),
            "representative_image_path": frame.get("kict_image_path", ""),
            "representative_mask_path": frame.get("kict_mask_path", ""),
            "requires_manual_review": "true" if requires_review else "false",
            "memory_description": f"病害{disease_id}为当前巡检新增候选，需要人工复核后确认是否并入既有记忆。",
        }

    def _frame_key(self, row: dict[str, str]) -> tuple[str, str, str]:
        # image_id alone is not unique when one frame contains multiple diseases.
        return (row.get("image_id", ""), row.get("frame_id", ""), row.get("disease_id", ""))

    def _unique_rows_by_key(self, rows: list[dict[str, str]], *, row_label: str) -> dict[tuple[str, str, str], dict[str, str]]:
        keyed_rows: dict[tuple[str, str, str], dict[str, str]] = {}
        for row in rows:
            key = self._frame_key(row)
            if key in keyed_rows:
                raise ValueError(f"Duplicate {row_label} composite key: {self._key_label(key)}")
            keyed_rows[key] = row
        return keyed_rows

    def _ensure_unique_association_keys(self, association_rows: list[dict[str, str]]) -> None:
        seen: set[tuple[str, str, str]] = set()
        for association in association_rows:
            key = self._frame_key(association)
            if key in seen:
                raise ValueError(f"Duplicate association composite key: {self._key_label(key)}")
            seen.add(key)

    def _key_label(self, key: tuple[str, str, str]) -> str:
        return "|".join(key)

    def _find_frame_for_association(
        self,
        association: dict[str, str],
        frame_by_key: dict[tuple[str, str, str], dict[str, str]],
        frames_by_image: dict[str, list[dict[str, str]]],
    ) -> dict[str, str] | None:
        key = self._frame_key(association)
        if key in frame_by_key:
            return frame_by_key[key]
        same_image_frames = frames_by_image.get(association.get("image_id", ""), [])
        if len(same_image_frames) == 1:
            return same_image_frames[0]
        if len(same_image_frames) > 1:
            raise ValueError(
                "Association cannot be matched to a unique frame; "
                f"missing or inconsistent frame_id/disease_id for image_id={association.get('image_id', '')}"
            )
        return None

    def _risk_from_area(self, area: float) -> str:
        if area < 1500:
            return "低"
        if area < 3000:
            return "中"
        return "高"

    def _next_version(self, memory_rows: list[dict[str, str]]) -> str:
        versions = []
        for row in memory_rows:
            text = str(row.get("memory_version", "")).lstrip("v")
            if text.isdigit():
                versions.append(int(text))
        return f"v{(max(versions) if versions else 1) + 1}"

    def _confidence_from_inspection_count(self, inspection_count: int, *, requires_review: bool = False) -> str:
        if requires_review:
            return "low" if inspection_count >= 2 else "very_low"
        if inspection_count >= 3:
            return "medium"
        if inspection_count == 2:
            return "low"
        return "very_low"

    def _as_bool(self, value: object) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _inspection_id_sort_key(self, inspection_id: str) -> tuple[int, str]:
        digits = "".join(ch for ch in str(inspection_id) if ch.isdigit())
        return (int(digits) if digits else 0, inspection_id)

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
