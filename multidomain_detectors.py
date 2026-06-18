from __future__ import annotations

from typing import Any

from multidomain_schema import build_multidomain_payload


SCHEMA_VERSION = "multidomain-result.v1"

DOMAIN_LABELS = {
    "civil": "civil structure",
    "track": "track system",
    "equipment": "equipment system",
}


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _default_location(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report or not report.get("spatial_summary"):
        return {
            "status": "unavailable",
            "source": "demo",
            "reason": "No spatial summary supplied for this detector result.",
        }
    spatial = report["spatial_summary"]
    if isinstance(spatial, dict):
        return {
            "status": "available" if spatial.get("available", True) else "unavailable",
            "source": spatial.get("source", "report"),
            "accuracy_level": spatial.get("accuracy_level"),
            "summary": _json_safe(spatial),
        }
    return {
        "status": "available",
        "source": "report",
        "summary": _json_safe(spatial),
    }


def build_detector_result(
    *,
    domain: str,
    defect_type: str,
    defect_type_id: int | None = None,
    source: str,
    status: str,
    geometry: dict[str, Any],
    confidence_score: float | None = None,
    confidence_basis: str = "demo rule",
    review_priority: dict[str, Any] | None = None,
    location: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    explanation: str = "",
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "domain": domain,
        "domain_label": DOMAIN_LABELS.get(domain, domain),
        "defect_type": defect_type,
        "defect_type_id": defect_type_id,
        "source": source,
        "status": status,
        "geometry": _json_safe(geometry),
        "confidence": {
            "available": confidence_score is not None,
            "score": confidence_score,
            "basis": confidence_basis,
            "scope": "object_level" if geometry.get("kind") == "bbox" else "image_level",
        },
        "location": _json_safe(location or {}),
        "review_priority": _json_safe(review_priority or {}),
        "evidence": _json_safe(evidence or {}),
        "explanation": explanation,
    }


def build_demo_track_equipment_results(report: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    location = _default_location(report)
    review_priority = dict(report.get("review_priority", {})) if report else {}
    common_evidence = {
        "source": "demo_detector",
        "supports": "U4 track/equipment adapter example",
    }
    return [
        build_detector_result(
            domain="track",
            defect_type="fastener_missing",
            defect_type_id=1,
            source="rule_based_demo",
            status="simulation-only",
            geometry={
                "kind": "bbox",
                "bbox": [0.16, 0.28, 0.28, 0.46],
                "pixel_hint": "right rail region",
            },
            confidence_score=0.64,
            confidence_basis="demo fixture confidence",
            review_priority={
                "priority": review_priority.get("priority", "medium"),
                "score": review_priority.get("score", 1.2),
                "review_required": True,
                "note": "Demo track adapter result for competition wording.",
            },
            location=location,
            evidence={
                **common_evidence,
                "fixture_id": "track_fastener_missing_demo",
            },
            explanation="Demo rule marks a missing fastener on the track system.",
        ),
        build_detector_result(
            domain="equipment",
            defect_type="bracket_loose",
            defect_type_id=2,
            source="rule_based_demo",
            status="simulation-only",
            geometry={
                "kind": "bbox",
                "bbox": [0.61, 0.19, 0.75, 0.39],
                "pixel_hint": "upper right equipment bracket",
            },
            confidence_score=0.59,
            confidence_basis="demo fixture confidence",
            review_priority={
                "priority": "medium",
                "score": 1.0,
                "review_required": True,
                "note": "Demo equipment adapter result for competition wording.",
            },
            location=location,
            evidence={
                **common_evidence,
                "fixture_id": "equipment_bracket_loose_demo",
            },
            explanation="Demo rule marks a loose equipment bracket on the tunnel wall.",
        ),
    ]


def build_multidomain_demo_report(report: dict[str, Any], extra_results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    combined_results = build_demo_track_equipment_results(report)
    if extra_results:
        combined_results.extend(extra_results)
    return build_multidomain_payload(report, extra_results=combined_results)
