from __future__ import annotations

from pathlib import Path
from typing import Any

from morphology_adapter import CLASS_NAMES as DEFAULT_CLASS_NAMES


SCHEMA_VERSION = "multidomain-result.v1"


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


def _class_name(class_names: dict[str, Any], class_id: int) -> str:
    if str(class_id) in class_names:
        return str(class_names[str(class_id)])
    if class_id in class_names:
        return str(class_names[class_id])
    if class_id in DEFAULT_CLASS_NAMES:
        return str(DEFAULT_CLASS_NAMES[class_id])
    return f"class_{class_id}"


def _confidence_summary(report: dict[str, Any]) -> dict[str, Any]:
    uncertainty_summary = dict(report.get("uncertainty_summary", {}))
    self_consistency = dict(report.get("self_consistency", {}))
    defect_mean = uncertainty_summary.get("defect_mean")
    foreground_iou = self_consistency.get("foreground_iou")

    if defect_mean is not None:
        score = max(0.0, min(1.0, 1.0 - float(defect_mean)))
        basis = "1 - uncertainty_summary.defect_mean"
        available = True
    elif foreground_iou is not None:
        score = float(foreground_iou)
        basis = "self_consistency.foreground_iou"
        available = True
    else:
        score = None
        basis = "image-level confidence unavailable"
        available = False

    return {
        "available": available,
        "score": score,
        "basis": basis,
        "scope": "image_level",
    }


def _location_summary(report: dict[str, Any]) -> dict[str, Any]:
    spatial_summary = report.get("spatial_summary")
    if spatial_summary is None:
        return {
            "status": "unavailable",
            "source": "not_provided",
            "reason": "The current report does not include spatial-mapping metadata.",
        }

    spatial = _json_safe(spatial_summary)
    status = str(spatial.get("status") or ("available" if bool(spatial.get("available", True)) else "unavailable"))
    return {
        "status": status,
        "source": spatial.get("location_source", spatial.get("source", "report")),
        "accuracy_level": spatial.get("accuracy_level"),
        "summary": spatial,
    }


def build_multidomain_payload(report: dict[str, Any], extra_results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    class_names = dict(report.get("class_names", {}))
    selected_stats = dict(report.get("selected_prediction_stats", {}))
    total_pixels = sum(int(count) for count in selected_stats.values())
    artifacts = dict(report.get("artifacts", {}))
    selected_mask_artifact = artifacts.get("selected_mask")
    review_priority = _json_safe(report.get("review_priority", {}))
    adaptive_selection = _json_safe(report.get("adaptive_selection", {}))
    uncertainty_summary = _json_safe(report.get("uncertainty_summary", {}))
    disagreement_summary = _json_safe(report.get("disagreement_summary", {}))
    self_consistency = _json_safe(report.get("self_consistency", {}))
    confidence = _confidence_summary(report)
    location = _location_summary(report)
    evidence = {
        "mask_source": _json_safe(report.get("mask_source", {})),
        "tta_specs": list(report.get("tta_specs", [])),
        "adaptive_selection": adaptive_selection,
        "uncertainty_summary": uncertainty_summary,
        "disagreement_summary": disagreement_summary,
        "self_consistency": self_consistency,
    }

    results: list[dict[str, Any]] = []
    for key, raw_count in sorted(selected_stats.items(), key=lambda item: int(item[0])):
        class_id = int(key)
        pixel_count = int(raw_count)
        if class_id == 0 or pixel_count <= 0:
            continue
        class_fraction = (pixel_count / total_pixels) if total_pixels else None
        results.append(
            {
                "schema_version": SCHEMA_VERSION,
                "domain": "civil",
                "domain_label": "civil structure",
                "defect_type": _class_name(class_names, class_id),
                "defect_type_id": class_id,
                "source": "selected_mask",
                "status": "measured",
                "geometry": {
                    "kind": "segmentation_mask",
                    "artifact": selected_mask_artifact,
                    "class_id": class_id,
                    "class_name": _class_name(class_names, class_id),
                    "pixel_count": pixel_count,
                    "pixel_fraction": class_fraction,
                },
                "confidence": confidence,
                "location": location,
                "review_priority": review_priority,
                "evidence": evidence,
                "explanation": (
                    f"Selected mask retains {pixel_count} pixels for {_class_name(class_names, class_id)}."
                ),
            }
        )

    if extra_results:
        for item in extra_results:
            results.append(_json_safe(item))

    if not results:
        results.append(
            {
                "schema_version": SCHEMA_VERSION,
                "domain": "civil",
                "domain_label": "civil structure",
                "defect_type": "no_defect",
                "defect_type_id": 0,
                "source": "selected_mask",
                "status": "background_only",
                "geometry": {
                    "kind": "segmentation_mask",
                    "artifact": selected_mask_artifact,
                    "class_id": 0,
                    "class_name": "background",
                    "pixel_count": 0,
                    "pixel_fraction": 0.0,
                },
                "confidence": confidence,
                "location": location,
                "review_priority": review_priority,
                "evidence": evidence,
                "explanation": "The selected mask does not contain a foreground defect class.",
            }
        )

    domains = sorted({str(item.get("domain", "unknown")) for item in results})
    civil_classes = [item["defect_type"] for item in results if item.get("domain") == "civil"]
    return {
        "schema_version": SCHEMA_VERSION,
        "source_model": _json_safe(report.get("mask_source", {})).get("name", "unknown"),
        "domains": domains,
        "results": results,
        "summary": {
            "civil_result_count": len(civil_classes),
            "civil_defect_types": civil_classes,
            "has_spatial_summary": report.get("spatial_summary") is not None,
            "location_status": location.get("status"),
            "location_source": location.get("source"),
            "location_accuracy_level": location.get("accuracy_level"),
            "review_priority": review_priority.get("priority"),
        },
    }
