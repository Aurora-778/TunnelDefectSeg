from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "inspection-report.v1"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    return value


def _summary_card(label: str, value: Any, detail: str, tone: str = "neutral") -> dict[str, Any]:
    return {
        "label": label,
        "value": value,
        "detail": detail,
        "tone": tone,
    }


def build_inspection_report(
    report: dict[str, Any],
    *,
    job_id: str,
    original_name: str,
    output_dir: Path,
) -> dict[str, Any]:
    stem = str(report.get("stem") or Path(original_name).stem or job_id)
    inspection_name = f"{stem}_inspection_report.json"
    artifacts = dict(report.get("artifacts", {}))
    artifacts["inspection_report"] = inspection_name

    risk = dict(report.get("risk", {}))
    review_priority = dict(report.get("review_priority", {}))
    adaptive_selection = dict(report.get("adaptive_selection", {}))
    self_consistency = dict(report.get("self_consistency", {}))
    uncertainty_summary = dict(report.get("uncertainty_summary", {}))
    disagreement_summary = dict(report.get("disagreement_summary", {}))
    morphology = dict(report.get("morphology", {}))
    morphology_delta = _json_safe(report.get("morphology_delta", {}))
    evaluation = _json_safe(report.get("evaluation")) if report.get("evaluation") is not None else None
    views = _json_safe(report.get("views", []))

    source_report_url = report.get("report_url") or (
        f"/outputs/{job_id}/{artifacts['report']}" if artifacts.get("report") else None
    )
    inspection_report_url = f"/outputs/{job_id}/{inspection_name}"

    summary_cards = [
        _summary_card(
            "Risk",
            risk.get("risk_level", "none"),
            f"score {risk.get('score', 0.0)}",
            "danger" if risk.get("risk_level") == "high" else "warning" if risk.get("risk_level") == "medium" else "neutral",
        ),
        _summary_card(
            "Review",
            review_priority.get("priority", "none"),
            review_priority.get("note", "manual-review priority signal"),
            "warning" if review_priority.get("review_required") else "neutral",
        ),
        _summary_card(
            "Selection",
            adaptive_selection.get("mode", "fused"),
            ", ".join(str(item) for item in adaptive_selection.get("reasons", [])[:2]) or "adaptive selection result",
            "neutral",
        ),
        _summary_card(
            "Self IoU",
            self_consistency.get("foreground_iou"),
            "Single/Fused 前景一致性，只是模型自一致，不是真实 GT mIoU。",
            "neutral",
        ),
    ]

    if evaluation:
        selected_miou = evaluation.get("selected_mIoU")
        fused_miou = evaluation.get("fused_mIoU")
        summary_cards.append(
            _summary_card(
                "GT mIoU",
                selected_miou if selected_miou is not None else fused_miou,
                "有标注样本时的真实评估结果。",
                "neutral",
            )
        )

    export: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "job_id": job_id,
        "source_image": {
            "name": original_name,
            "stem": stem,
        },
        "model": {
            "mask_source": _json_safe(report.get("mask_source", {})),
            "tta_specs": list(report.get("tta_specs", [])),
        },
        "assessment": {
            "risk": _json_safe(risk),
            "review_priority": _json_safe(review_priority),
            "adaptive_selection": _json_safe(adaptive_selection),
        },
        "evidence": {
            "self_consistency": _json_safe(self_consistency),
            "uncertainty_summary": _json_safe(uncertainty_summary),
            "disagreement_summary": _json_safe(disagreement_summary),
            "morphology": _json_safe(morphology),
            "morphology_delta": morphology_delta,
        },
        "artifacts": artifacts,
        "views": views,
        "summary_cards": summary_cards,
        "source_report_url": source_report_url,
        "inspection_report_url": inspection_report_url,
    }

    if evaluation is not None:
        export["evaluation"] = evaluation
    if report.get("spatial_summary") is not None:
        export["spatial_summary"] = _json_safe(report["spatial_summary"])
    if report.get("elapsed_ms") is not None:
        export["elapsed_ms"] = report["elapsed_ms"]

    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / inspection_name
    report_path.write_text(json.dumps(export, ensure_ascii=False, indent=2), encoding="utf-8")
    export["inspection_report_path"] = report_path.name
    return export
