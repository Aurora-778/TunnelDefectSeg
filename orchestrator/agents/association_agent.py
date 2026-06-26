"""Associate frame records with memory objects through context inputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator.agents.base import BaseAgent


class AssociationAgent(BaseAgent):
    """Create rule-based frame-to-memory association records."""

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
            memory = memory_by_id.get(disease_id, {})
            matched = bool(memory)
            rows.append(
                {
                    "association_id": f"ASSOC-{frame.get('inspection_id', '')}-{frame.get('frame_id', '')}-{disease_id}",
                    "inspection_id": frame.get("inspection_id", ""),
                    "frame_id": frame.get("frame_id", ""),
                    "image_id": frame.get("image_id", ""),
                    "disease_id": disease_id,
                    "memory_id": memory.get("memory_id", ""),
                    "association_status": "matched" if matched else "unmatched",
                    "rule_basis": "same disease_id" if matched else "missing memory object",
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

    def _path_from_context(self, context: dict[str, Any], key: str):
        # Compatibility helper for older callers that passed flat context paths.
        value = context.get(key)
        if not value:
            return None
        path = Path(str(value))
        return path if path.is_absolute() else self.project_root(context) / path
