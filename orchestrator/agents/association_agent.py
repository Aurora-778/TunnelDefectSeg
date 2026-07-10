"""Associate frame records with memory objects through explainable scores."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from orchestrator.agents.base import BaseAgent


# Keep no-id threshold semantics in one place so evaluation baselines cannot
# silently drift from the production Association policy.
NO_ID_WEIGHT_SUM = 0.85
WITH_ID_MATCH_THRESHOLD = 0.45
NO_ID_MATCH_THRESHOLD = WITH_ID_MATCH_THRESHOLD / NO_ID_WEIGHT_SUM
WITH_ID_SOFT_THRESHOLD = 0.65
NO_ID_SOFT_THRESHOLD = 0.5 / NO_ID_WEIGHT_SUM
WITH_ID_MARGIN_THRESHOLD = 0.15
NO_ID_MARGIN_THRESHOLD = WITH_ID_MARGIN_THRESHOLD / NO_ID_WEIGHT_SUM


class AssociationAgent(BaseAgent):
    """Create frame-to-memory association records with similarity scores."""

    name = "association"

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        inputs = self.agent_inputs(context)
        if self._as_bool(inputs.get("history_only", False)):
            # Keep scoring in this agent while the coordinator owns only time ordering.
            from orchestrator.history_only_association import run_history_only_association

            return run_history_only_association(self, context, inputs)
        frame_path = self.resolve_path(context, self._required_input(inputs, "frame_records"))
        memory_path = self._memory_path(context, inputs)
        output_path = self.resolve_path(context, self._required_input(inputs, "output_path"))
        legacy_output_path = self.resolve_path(context, inputs["legacy_output_path"]) if inputs.get("legacy_output_path") else None
        use_disease_id_score = self._as_bool(inputs.get("use_disease_id_score", False))
        association_mode = self._association_mode(inputs.get("association_mode"), use_disease_id_score)

        frame_rows = self.read_csv(frame_path)
        memory_rows = self.read_csv(memory_path)

        rows: list[dict[str, Any]] = []
        for row_index, frame in enumerate(frame_rows, start=1):
            disease_id = frame.get("disease_id", "")
            memory, scores, candidates = self._best_memory_match(frame, memory_rows, use_disease_id_score=use_disease_id_score)
            matched = bool(memory)
            association_score = scores["association_score"]
            conflict_reason = self._conflict_reason(scores)
            score_margin = self._score_margin(candidates)
            geometry_available, geometry_note = self._geometry_summary(frame)
            # 当前未接入 bbox 几何评分，两字段共用此变量，未来动态化时只改一处
            geometry_score_applied = False
            match_type = self._match_type(
                frame,
                memory,
                association_score,
                conflict_reason,
                use_disease_id_score=use_disease_id_score,
            )
            needs_manual_review = self._needs_manual_review(
                matched, match_type, conflict_reason, score_margin, use_disease_id_score=use_disease_id_score
            )
            confidence_level = self._confidence_level(
                association_score, matched, needs_manual_review, use_disease_id_score=use_disease_id_score
            )
            rows.append(
                {
                    "association_id": "ASSOC-{}-{}-{}-{}".format(
                        frame.get("inspection_id", ""),
                        frame.get("frame_id", ""),
                        frame.get("image_id", ""),
                        row_index,
                    ),
                    "inspection_id": frame.get("inspection_id", ""),
                    "frame_id": frame.get("frame_id", ""),
                    "image_id": frame.get("image_id", ""),
                    "label_disease_id": disease_id,
                    "history_inspection_ids": str(inputs.get("history_inspection_ids", "")),
                    "memory_id": memory.get("memory_id", ""),
                    "association_status": "matched" if matched else "unmatched",
                    "rule_basis": self._rule_basis(frame, memory, match_type, use_disease_id_score=use_disease_id_score),
                    "use_disease_id_score": "true" if use_disease_id_score else "false",
                    "association_mode": association_mode,
                    "association_score": f"{association_score:.4f}",
                    "spatial_distance_score": f"{scores['spatial_distance_score']:.4f}",
                    "area_similarity_score": f"{scores['area_similarity_score']:.4f}",
                    "temporal_continuity_score": f"{scores['temporal_continuity_score']:.4f}",
                    "risk_similarity_score": f"{scores['risk_similarity_score']:.4f}",
                    "confidence_level": confidence_level,
                    "match_type": match_type,
                    "candidate_count": len(candidates),
                    "top_candidate_ids": self._top_candidate_ids(candidates),
                    "score_margin": f"{score_margin:.4f}",
                    "conflict_reason": conflict_reason,
                    "needs_manual_review": "true" if needs_manual_review else "false",
                    "bbox_fields_present": "true" if geometry_available else "false",
                    "geometry_score_applied": "true" if geometry_score_applied else "false",
                    "geometry_feature_available": "true" if geometry_score_applied else "false",
                    "geometry_limit_note": geometry_note,
                    "mileage_text": frame.get("mileage_text", ""),
                    "clock_direction": frame.get("clock_direction", ""),
                    "disease_type": frame.get("disease_type", ""),
                    "kict_area_px": frame.get("kict_area_px", ""),
                    "risk_level": memory.get("last_risk_level", memory.get("risk_level", "")),
                    "growth_trend": memory.get("growth_trend", ""),
                    "kict_image_path": frame.get("kict_image_path", ""),
                    "kict_mask_path": frame.get("kict_mask_path", ""),
                }
            )

        self.write_csv(output_path, rows, self.fieldnames())
        if legacy_output_path:
            legacy_output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(output_path, legacy_output_path)

        matched_count = sum(1 for row in rows if row["association_status"] == "matched")
        result = {
            "association_records_path": str(output_path),
            "association_rows": len(rows),
            "association_matched_rows": matched_count,
        }
        return result

    @staticmethod
    def fieldnames() -> list[str]:
        return [
            "association_id",
            "inspection_id",
            "frame_id",
            "image_id",
            "label_disease_id",
            "history_inspection_ids",
            "memory_id",
            "association_status",
            "rule_basis",
            "use_disease_id_score",
            "association_mode",
            "association_score",
            "spatial_distance_score",
            "area_similarity_score",
            "temporal_continuity_score",
            "risk_similarity_score",
            "confidence_level",
            "match_type",
            "candidate_count",
            "top_candidate_ids",
            "score_margin",
            "conflict_reason",
            "needs_manual_review",
            "bbox_fields_present",
            "geometry_score_applied",
            "geometry_feature_available",
            "geometry_limit_note",
            "mileage_text",
            "clock_direction",
            "disease_type",
            "kict_area_px",
            "risk_level",
            "growth_trend",
            "kict_image_path",
            "kict_mask_path",
        ]

    def _memory_path(self, context: dict[str, Any], inputs: dict[str, Any]) -> Path:
        if inputs.get("memory_bank"):
            return self.resolve_path(context, inputs["memory_bank"])
        memory_output = context.get("outputs", {}).get("memory", {})
        if memory_output.get("disease_memory_bank_path"):
            return self.resolve_path(context, memory_output["disease_memory_bank_path"])
        raise ValueError("AssociationAgent missing memory_bank input or memory output")

    def _required_input(self, inputs: dict[str, Any], key: str) -> str:
        value = inputs.get(key)
        if not value:
            raise ValueError(f"AssociationAgent missing required input: {key}")
        return str(value)

    def _best_memory_match(
        self,
        frame: dict[str, str],
        memory_rows: list[dict[str, str]],
        *,
        use_disease_id_score: bool = False,
    ) -> tuple[dict[str, str], dict[str, float], list[tuple[dict[str, str], dict[str, float]]]]:
        scored = []
        for memory in memory_rows:
            # no-id 模式下完全不读取 disease_id 计算 same_id
            same_id = (
                frame.get("disease_id", "") == memory.get("disease_id", "")
                if use_disease_id_score
                else False
            )
            scored.append(
                (memory, self._scores(frame, memory, same_id=same_id, use_disease_id_score=use_disease_id_score))
            )
        scored.sort(key=lambda item: item[1]["association_score"], reverse=True)
        if not scored:
            return {}, self._empty_scores(), []
        memory, scores = scored[0]
        # no-id 归一化后阈值同步上调，保持匹配行为与归一化前一致
        threshold = NO_ID_MATCH_THRESHOLD if not use_disease_id_score else WITH_ID_MATCH_THRESHOLD
        if scores["association_score"] < threshold:
            return {}, scores, scored
        return memory, scores, scored

    def _scores(
        self,
        frame: dict[str, str],
        memory: dict[str, str],
        *,
        same_id: bool,
        use_disease_id_score: bool = False,
    ) -> dict[str, float]:
        spatial = self._spatial_distance_score(frame, memory)
        area = self._area_similarity_score(frame, memory)
        temporal = self._temporal_continuity_score(frame, memory)
        risk = self._risk_similarity_score(frame, memory)
        id_score = 1.0 if same_id and use_disease_id_score else 0.0
        if use_disease_id_score:
            score = 0.25 * spatial + 0.25 * area + 0.20 * temporal + 0.15 * risk + 0.15 * id_score
        else:
            # no-id 模式：四项特征权重和为 0.85，归一化到 1.0 使完美匹配可达满分
            score = (0.25 * spatial + 0.25 * area + 0.20 * temporal + 0.15 * risk) / NO_ID_WEIGHT_SUM
        return {
            "association_score": min(score, 1.0),
            "spatial_distance_score": spatial,
            "area_similarity_score": area,
            "temporal_continuity_score": temporal,
            "risk_similarity_score": risk,
        }

    def _empty_scores(self) -> dict[str, float]:
        return {
            "association_score": 0.0,
            "spatial_distance_score": 0.0,
            "area_similarity_score": 0.0,
            "temporal_continuity_score": 0.0,
            "risk_similarity_score": 0.0,
        }

    def _spatial_distance_score(self, frame: dict[str, str], memory: dict[str, str]) -> float:
        mileage_score = self._mileage_score(frame.get("mileage_text", ""), memory.get("mileage_range", ""))
        clock_score = 1.0 if frame.get("clock_direction", "") == memory.get("main_clock_direction", "") else 0.4
        return 0.75 * mileage_score + 0.25 * clock_score

    def _mileage_score(self, mileage_text: str, memory_range: str) -> float:
        frame_mileage = self._parse_mileage(mileage_text)
        memory_mileages = [self._parse_mileage(part.strip()) for part in memory_range.replace("至", "-").split("-")]
        memory_mileages = [value for value in memory_mileages if value is not None]
        if frame_mileage is None or not memory_mileages:
            return 0.5
        distance = min(abs(frame_mileage - value) for value in memory_mileages)
        return max(0.0, 1.0 - min(distance / 30.0, 1.0))

    def _area_similarity_score(self, frame: dict[str, str], memory: dict[str, str]) -> float:
        frame_area = self._to_float(frame.get("kict_area_px"))
        memory_area = self._to_float(memory.get("last_area_px") or memory.get("max_area_px"))
        if frame_area <= 0 or memory_area <= 0:
            return 0.5
        return min(frame_area, memory_area) / max(frame_area, memory_area)

    def _temporal_continuity_score(self, frame: dict[str, str], memory: dict[str, str]) -> float:
        frame_index = self._inspection_index(frame.get("inspection_id", ""))
        first_index = self._inspection_index(memory.get("first_seen_inspection", ""))
        last_index = self._inspection_index(memory.get("last_seen_inspection", ""))
        if frame_index == 0 or (first_index == 0 and last_index == 0):
            return 0.5
        if first_index <= frame_index <= max(last_index, first_index):
            return 1.0
        gap = min(abs(frame_index - first_index), abs(frame_index - last_index))
        return max(0.0, 1.0 - min(gap / 3.0, 1.0))

    def _risk_similarity_score(self, frame: dict[str, str], memory: dict[str, str]) -> float:
        frame_risk = self._risk_from_area(frame.get("kict_area_px", ""))
        memory_risk = memory.get("last_risk_level") or memory.get("first_risk_level") or memory.get("risk_level", "")
        if not memory_risk:
            return 0.5
        return 1.0 - min(abs(self._risk_score(frame_risk) - self._risk_score(memory_risk)) / 2.0, 1.0)

    def _confidence_level(
        self, score: float, matched: bool, needs_manual_review: bool, *, use_disease_id_score: bool = False
    ) -> str:
        if not matched:
            return "low"
        # no-id 归一化后 high 阈值同步调整，保持与 with-id 同尺度
        # with-id high 需 raw4+id>=0.8，id=1 时 raw4>=0.65；no-id 归一化后 0.65/0.85
        high_threshold = 0.65 / 0.85 if not use_disease_id_score else 0.8
        if score >= high_threshold and not needs_manual_review:
            return "high"
        # no-id 归一化后 medium 阈值同步调整
        # with-id medium 需 raw4+id>=0.6，id=1 时 raw4>=0.45；no-id 归一化后 0.45/0.85
        medium_threshold = 0.45 / 0.85 if not use_disease_id_score else 0.6
        if score >= medium_threshold:
            return "medium"
        return "low"

    def _match_type(
        self,
        frame: dict[str, str],
        memory: dict[str, str],
        score: float,
        conflict_reason: str,
        *,
        use_disease_id_score: bool = False,
    ) -> str:
        if not memory:
            return "uncertain"
        same_id_allowed = use_disease_id_score and frame.get("disease_id", "") == memory.get("disease_id", "")
        if same_id_allowed and not conflict_reason and score >= 0.75:
            return "hard"
        # no-id 归一化后 soft 阈值同步调整，保持与 with-id 同尺度
        # with-id soft 需 raw4+id>=0.65，id=1 时 raw4>=0.5；no-id 归一化后 0.5/0.85
        soft_threshold = NO_ID_SOFT_THRESHOLD if not use_disease_id_score else WITH_ID_SOFT_THRESHOLD
        if score >= soft_threshold:
            return "soft"
        return "uncertain"

    def _rule_basis(
        self,
        frame: dict[str, str],
        memory: dict[str, str],
        match_type: str,
        *,
        use_disease_id_score: bool = False,
    ) -> str:
        if not memory:
            return "no candidate above score threshold"
        same_id_allowed = use_disease_id_score and frame.get("disease_id", "") == memory.get("disease_id", "")
        if match_type == "hard" and same_id_allowed:
            return "same disease_id plus spatial/area/temporal/risk consistency"
        if match_type == "soft":
            return "best scored candidate by spatial/area/temporal/risk similarity"
        return "low confidence candidate; requires review"

    def _conflict_reason(self, scores: dict[str, float]) -> str:
        reasons = []
        if scores["spatial_distance_score"] < 0.4:
            reasons.append("spatial mismatch")
        if scores["area_similarity_score"] < 0.3:
            reasons.append("area mismatch")
        if scores["temporal_continuity_score"] < 0.3:
            reasons.append("temporal mismatch")
        if scores["risk_similarity_score"] < 0.4:
            reasons.append("risk mismatch")
        return "; ".join(reasons)

    def _needs_manual_review(
        self,
        matched: bool,
        match_type: str,
        conflict_reason: str,
        score_margin: float,
        *,
        use_disease_id_score: bool = False,
    ) -> bool:
        if not matched:
            return True
        if conflict_reason:
            return True
        if match_type == "uncertain":
            return True
        # no-id 归一化后候选差值被放大 1/0.85 倍，阈值同步放大保持等价
        margin_threshold = NO_ID_MARGIN_THRESHOLD if not use_disease_id_score else WITH_ID_MARGIN_THRESHOLD
        return score_margin < margin_threshold

    def _score_margin(self, candidates: list[tuple[dict[str, str], dict[str, float]]]) -> float:
        if len(candidates) < 2:
            return 1.0 if candidates else 0.0
        return max(0.0, candidates[0][1]["association_score"] - candidates[1][1]["association_score"])

    def _top_candidate_ids(self, candidates: list[tuple[dict[str, str], dict[str, float]]]) -> str:
        labels = []
        for memory, scores in candidates[:3]:
            labels.append(f"{memory.get('memory_id', '')}:{scores['association_score']:.4f}")
        return "|".join(labels)

    def _risk_from_area(self, value: str) -> str:
        area = self._to_float(value)
        if area < 1500:
            return "低"
        if area < 3000:
            return "中"
        return "高"

    def _risk_score(self, risk_level: str) -> int:
        return {"低": 1, "low": 1, "中": 2, "medium": 2, "高": 3, "high": 3}.get(str(risk_level).strip().lower(), 0)

    def _inspection_index(self, inspection_id: str) -> int:
        digits = "".join(ch for ch in str(inspection_id) if ch.isdigit())
        return int(digits) if digits else 0

    def _parse_mileage(self, value: str) -> float | None:
        value = str(value).strip()
        if not value:
            return None
        try:
            if value.upper().startswith("K") and "+" in value:
                km_text, meter_text = value.upper().removeprefix("K").split("+", 1)
                return float(km_text) * 1000 + float(meter_text)
            return float(value)
        except ValueError:
            return None

    def _to_float(self, value: object) -> float:
        try:
            if value in (None, ""):
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _as_bool(self, value: object) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() not in {"0", "false", "no", "off"}

    def _association_mode(self, value: object, use_disease_id_score: bool) -> str:
        if not use_disease_id_score:
            return "no_id"
        requested = str(value).strip() if value else ""
        return requested if requested and requested != "no_id" else "with_id_upper_bound"

    def _geometry_summary(self, frame: dict[str, str]) -> tuple[bool, str]:
        # Round1 records whether bbox/mask geometry is present; scoring still uses the existing coarse signals.
        required_fields = [
            "kict_bbox_x1",
            "kict_bbox_y1",
            "kict_bbox_x2",
            "kict_bbox_y2",
            "kict_mask_width",
            "kict_mask_height",
        ]
        has_geometry = all(str(frame.get(field, "")).strip() for field in required_fields)
        if has_geometry:
            return True, "bbox fields present but not used in scoring"
        return False, "missing bbox/mask shape fields in current artifacts"

    def _path_from_context(self, context: dict[str, Any], key: str):
        # Compatibility helper for older callers that passed flat context paths.
        value = context.get(key)
        if not value:
            return None
        path = Path(str(value))
        return path if path.is_absolute() else self.project_root(context) / path
