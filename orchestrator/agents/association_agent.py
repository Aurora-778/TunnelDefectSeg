"""Associate frame records with memory objects through explainable scores."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator.agents.base import BaseAgent


class AssociationAgent(BaseAgent):
    """Create frame-to-memory association records with similarity scores."""

    name = "association"

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        inputs = self.agent_inputs(context)
        frame_path = self.resolve_path(context, self._required_input(inputs, "frame_records"))
        memory_path = self._memory_path(context, inputs)
        output_path = self.resolve_path(context, self._required_input(inputs, "output_path"))

        frame_rows = self.read_csv(frame_path)
        memory_rows = self.read_csv(memory_path)
        memory_by_id = {row.get("disease_id", ""): row for row in memory_rows}

        rows: list[dict[str, Any]] = []
        for frame in frame_rows:
            disease_id = frame.get("disease_id", "")
            memory, scores = self._best_memory_match(frame, memory_rows, memory_by_id)
            matched = bool(memory)
            association_score = scores["association_score"]
            confidence_level = self._confidence_level(association_score, matched)
            match_type = self._match_type(frame, memory, association_score)
            rows.append(
                {
                    "association_id": f"ASSOC-{frame.get('inspection_id', '')}-{frame.get('frame_id', '')}-{disease_id}",
                    "inspection_id": frame.get("inspection_id", ""),
                    "frame_id": frame.get("frame_id", ""),
                    "image_id": frame.get("image_id", ""),
                    "disease_id": disease_id,
                    "memory_id": memory.get("memory_id", ""),
                    "association_status": "matched" if matched else "unmatched",
                    "rule_basis": self._rule_basis(frame, memory, match_type),
                    "association_score": f"{association_score:.4f}",
                    "spatial_distance_score": f"{scores['spatial_distance_score']:.4f}",
                    "area_similarity_score": f"{scores['area_similarity_score']:.4f}",
                    "temporal_continuity_score": f"{scores['temporal_continuity_score']:.4f}",
                    "risk_similarity_score": f"{scores['risk_similarity_score']:.4f}",
                    "confidence_level": confidence_level,
                    "match_type": match_type,
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

        fieldnames = [
            "association_id",
            "inspection_id",
            "frame_id",
            "image_id",
            "disease_id",
            "memory_id",
            "association_status",
            "rule_basis",
            "association_score",
            "spatial_distance_score",
            "area_similarity_score",
            "temporal_continuity_score",
            "risk_similarity_score",
            "confidence_level",
            "match_type",
            "mileage_text",
            "clock_direction",
            "disease_type",
            "kict_area_px",
            "risk_level",
            "growth_trend",
            "kict_image_path",
            "kict_mask_path",
        ]
        self.write_csv(output_path, rows, fieldnames)

        matched_count = sum(1 for row in rows if row["association_status"] == "matched")
        result = {
            "association_records_path": str(output_path),
            "association_rows": len(rows),
            "association_matched_rows": matched_count,
        }
        context.setdefault("outputs", {})[self.name] = result
        return result

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
        memory_by_id: dict[str, dict[str, str]],
    ) -> tuple[dict[str, str], dict[str, float]]:
        same_id_memory = memory_by_id.get(frame.get("disease_id", ""))
        if same_id_memory:
            return same_id_memory, self._scores(frame, same_id_memory, same_id=True)

        scored = [(memory, self._scores(frame, memory, same_id=False)) for memory in memory_rows]
        if not scored:
            return {}, self._empty_scores()
        memory, scores = max(scored, key=lambda item: item[1]["association_score"])
        if scores["association_score"] < 0.45:
            return {}, scores
        return memory, scores

    def _scores(self, frame: dict[str, str], memory: dict[str, str], *, same_id: bool) -> dict[str, float]:
        spatial = self._spatial_distance_score(frame, memory)
        area = self._area_similarity_score(frame, memory)
        temporal = self._temporal_continuity_score(frame, memory)
        risk = self._risk_similarity_score(frame, memory)
        score = 0.35 * spatial + 0.3 * area + 0.2 * temporal + 0.15 * risk
        if same_id:
            score = max(score, 0.9)
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

    def _confidence_level(self, score: float, matched: bool) -> str:
        if not matched:
            return "low"
        if score >= 0.8:
            return "high"
        if score >= 0.6:
            return "medium"
        return "low"

    def _match_type(self, frame: dict[str, str], memory: dict[str, str], score: float) -> str:
        if not memory:
            return "uncertain"
        if frame.get("disease_id", "") == memory.get("disease_id", ""):
            return "hard"
        if score >= 0.65:
            return "soft"
        return "uncertain"

    def _rule_basis(self, frame: dict[str, str], memory: dict[str, str], match_type: str) -> str:
        if not memory:
            return "no candidate above score threshold"
        if match_type == "hard":
            return "same disease_id plus spatial/area/temporal/risk consistency"
        if match_type == "soft":
            return "best scored candidate by spatial/area/temporal/risk similarity"
        return "low confidence candidate; requires review"

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

    def _path_from_context(self, context: dict[str, Any], key: str):
        # Compatibility helper for older callers that passed flat context paths.
        value = context.get(key)
        if not value:
            return None
        path = Path(str(value))
        return path if path.is_absolute() else self.project_root(context) / path
