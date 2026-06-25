from __future__ import annotations

import math
from dataclasses import asdict, is_dataclass
from typing import Any, Iterable

from robot_sequence import FrameRecord, parse_mileage


def _as_frame_dict(frame: FrameRecord | dict[str, Any]) -> dict[str, Any]:
    if isinstance(frame, FrameRecord):
        return frame.to_dict()
    if is_dataclass(frame):
        return asdict(frame)
    return dict(frame)


def _get(data: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _limitations(*items: Iterable[str] | None) -> list[str]:
    merged: list[str] = []
    for item in items:
        if item:
            merged.extend(str(value) for value in item if value)
    return sorted(set(merged))


def _measurement_basis(spatial_summary: dict[str, Any], frame: dict[str, Any]) -> str:
    local_3d = spatial_summary.get("local_3d") or {}
    if local_3d.get("available"):
        return "calibrated_3d"
    if frame.get("mileage") or frame.get("ring_id"):
        return "route_metadata"
    if spatial_summary.get("normalized_center") or spatial_summary.get("pixel_center"):
        return "pixel_only"
    return "unknown"


def _main_class(report: dict[str, Any]) -> tuple[int | None, str]:
    morphology = _get(report, "evidence.morphology", {}) or report.get("morphology", {}) or {}
    for key in ("main_class", "dominant_class", "class_name"):
        if morphology.get(key):
            return morphology.get("class_id"), str(morphology[key])
    multidomain = report.get("multidomain_results", {})
    results = multidomain.get("results") if isinstance(multidomain, dict) else None
    if results:
        first = results[0]
        if isinstance(first, dict):
            return first.get("class_id"), str(first.get("type") or first.get("class_name") or "foreground")
    return morphology.get("class_id"), "foreground"


def _area(report: dict[str, Any], spatial_summary: dict[str, Any]) -> float | None:
    morphology = _get(report, "evidence.morphology", {}) or report.get("morphology", {}) or {}
    for value in (
        spatial_summary.get("pixel_area"),
        morphology.get("defect_area_pixels"),
        morphology.get("area_pixels"),
    ):
        if value is not None:
            return float(value)
    return None


def _skeleton_length(report: dict[str, Any]) -> float | None:
    morphology = _get(report, "evidence.morphology", {}) or report.get("morphology", {}) or {}
    for key in ("skeleton_length", "defect_skeleton_length", "skeleton_length_pixels"):
        if morphology.get(key) is not None:
            return float(morphology[key])
    return None


def _risk_score(report: dict[str, Any]) -> float:
    value = _get(report, "assessment.risk.score", report.get("risk", {}).get("score", 0.0))
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _review_required(report: dict[str, Any]) -> bool:
    return bool(
        _get(report, "assessment.review_priority.review_required", None)
        or report.get("review_priority", {}).get("review_required")
        or _get(report, "assessment.risk.review_required", None)
        or report.get("risk", {}).get("review_required")
    )


def build_observation(frame: FrameRecord | dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    frame_dict = _as_frame_dict(frame)
    spatial_summary = dict(report.get("spatial_summary") or {})
    class_id, class_name = _main_class(report)
    morphology = _get(report, "evidence.morphology", {}) or report.get("morphology", {}) or {}
    component_count = morphology.get("defect_component_count") or morphology.get("component_count") or 0
    local_3d = spatial_summary.get("local_3d") or {"status": "unavailable", "available": False}
    local_3d_status = local_3d.get("status") or ("available" if local_3d.get("available") else "unavailable")
    limitations = _limitations(frame_dict.get("limitations"), spatial_summary.get("limitations"))

    if not spatial_summary or spatial_summary.get("status") == "unavailable":
        limitations = _limitations(limitations, ["location_unavailable"])
        status = "partial"
    else:
        status = spatial_summary.get("status") or "available"
    if component_count and int(component_count) > 1:
        limitations = _limitations(limitations, ["multi_component_single_foreground_observation"])

    observation = {
        "observation_id": f"obs_{frame_dict['frame_id']}",
        "frame_id": frame_dict["frame_id"],
        "inspection_run_id": frame_dict.get("inspection_run_id"),
        "manifest_index": frame_dict.get("manifest_index"),
        "timestamp": frame_dict.get("timestamp"),
        "mileage": frame_dict.get("mileage"),
        "ring_id": frame_dict.get("ring_id"),
        "camera_id": frame_dict.get("camera_id"),
        "class_id": class_id,
        "class_name": class_name,
        "geometry": {
            "bbox": spatial_summary.get("bbox"),
            "pixel_center": spatial_summary.get("pixel_center"),
            "normalized_center": spatial_summary.get("normalized_center"),
        },
        "area": _area(report, spatial_summary),
        "skeleton_length": _skeleton_length(report),
        "location": {
            "status": spatial_summary.get("status", "unavailable"),
            "mileage": spatial_summary.get("mileage", frame_dict.get("mileage")),
            "ring_id": spatial_summary.get("ring_id", frame_dict.get("ring_id")),
            "clock_position": spatial_summary.get("clock_position"),
            "normalized_center": spatial_summary.get("normalized_center"),
            "local_3d_status": local_3d_status,
            "local_3d": local_3d,
            "source": spatial_summary.get("source") or spatial_summary.get("location_source"),
            "accuracy_level": spatial_summary.get("accuracy_level"),
            "limitations": spatial_summary.get("limitations", []),
        },
        "confidence": {
            "ordering": frame_dict.get("ordering_confidence"),
            "location": frame_dict.get("location_confidence"),
            "uncertainty_mean": _get(report, "evidence.uncertainty_summary.mean", None),
            "uncertainty_defect_mean": _get(report, "evidence.uncertainty_summary.defect_mean", None),
        },
        "risk": {
            "score": _risk_score(report),
            "review_required": _review_required(report),
            "priority": _get(report, "assessment.review_priority.priority", report.get("review_priority", {}).get("priority")),
        },
        "status": status,
        "measurement_basis": _measurement_basis(spatial_summary, frame_dict),
        "claim_level": ["rule_evidence_only", "not_prediction", "not_field_verified"],
        "limitations": limitations,
        "evidence_links": {
            "report_path": frame_dict.get("report_path"),
            "image_path": frame_dict.get("image_path"),
        },
    }
    if observation["risk"]["review_required"]:
        observation["claim_level"].append("requires_manual_review")
    return observation


def build_observations(frames: Iterable[FrameRecord | dict[str, Any]], reports_by_frame_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    observations = []
    for frame in frames:
        frame_dict = _as_frame_dict(frame)
        report = reports_by_frame_id.get(frame_dict["frame_id"])
        if report is None:
            observations.append(
                {
                    "observation_id": f"obs_{frame_dict['frame_id']}",
                    "frame_id": frame_dict["frame_id"],
                    "inspection_run_id": frame_dict.get("inspection_run_id"),
                    "status": "partial",
                    "measurement_basis": "unknown",
                    "claim_level": ["rule_evidence_only", "not_prediction", "not_field_verified", "requires_manual_review"],
                    "limitations": _limitations(frame_dict.get("limitations"), ["report_missing"]),
                }
            )
            continue
        observations.append(build_observation(frame_dict, report))
    return observations


def _center(observation: dict[str, Any]) -> list[float] | None:
    center = _get(observation, "geometry.normalized_center")
    if isinstance(center, list) and len(center) == 2 and all(value is not None for value in center):
        return [float(center[0]), float(center[1])]
    return None


def _center_distance(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    a_center = _center(a)
    b_center = _center(b)
    if a_center is None or b_center is None:
        return None
    return math.sqrt((a_center[0] - b_center[0]) ** 2 + (a_center[1] - b_center[1]) ** 2)


def _mileage_distance(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    a_mileage = parse_mileage(a.get("mileage"))
    b_mileage = parse_mileage(b.get("mileage"))
    if a_mileage is None or b_mileage is None:
        return None
    return abs(a_mileage - b_mileage)


def _comparable(a: dict[str, Any], b: dict[str, Any]) -> tuple[str, list[str]]:
    limitations: list[str] = []
    if a.get("camera_id") and b.get("camera_id") and a.get("camera_id") != b.get("camera_id"):
        return "not_comparable", ["camera_id_changed"]
    if a.get("measurement_basis") == "unknown" or b.get("measurement_basis") == "unknown":
        limitations.append("measurement_basis_unknown")
    if a.get("measurement_basis") == "pixel_only" or b.get("measurement_basis") == "pixel_only":
        limitations.append("pixel_only_comparison")
    return ("weak" if limitations else "comparable", limitations)


def _matches_track(observation: dict[str, Any], track: dict[str, Any]) -> tuple[bool, list[str], str, list[str]]:
    latest = track["observations"][-1]
    if observation.get("class_name") != latest.get("class_name"):
        return False, [], "not_comparable", ["class_changed"]

    comparability, limitations = _comparable(observation, latest)
    if comparability == "not_comparable":
        return False, [], comparability, limitations

    reasons: list[str] = ["class_match"]
    mileage_distance = _mileage_distance(observation, latest)
    center_distance = _center_distance(observation, latest)
    if mileage_distance is not None and mileage_distance <= 3.0:
        reasons.append("mileage_proximity")
    if observation.get("ring_id") and observation.get("ring_id") == latest.get("ring_id"):
        reasons.append("ring_match")
    if center_distance is not None and center_distance <= 0.12:
        reasons.append("normalized_center_proximity")

    return len(reasons) >= 2, reasons, comparability, limitations


def build_defect_tracks(observations: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    sorted_observations = sorted(
        observations,
        key=lambda obs: (parse_mileage(obs.get("mileage")) if parse_mileage(obs.get("mileage")) is not None else float("inf"), obs.get("timestamp") or "", obs.get("manifest_index") or 0),
    )
    tracks: list[dict[str, Any]] = []
    for observation in sorted_observations:
        matched = None
        match_reasons: list[str] = []
        match_comparability = "unknown"
        match_limitations: list[str] = []
        if observation.get("status") != "partial":
            for track in tracks:
                ok, reasons, comparability, limitations = _matches_track(observation, track)
                if ok:
                    matched = track
                    match_reasons = reasons
                    match_comparability = comparability
                    match_limitations = limitations
                    break
        if matched is None:
            track = {
                "track_id": f"track_{len(tracks) + 1:03d}",
                "defect_class": observation.get("class_name", "foreground"),
                "observations": [observation],
                "association_reasons": ["new_track"],
                "track_confidence": "low" if observation.get("status") == "partial" else "medium",
                "comparability_status": "baseline_only",
                "limitations": list(observation.get("limitations", [])),
            }
            tracks.append(track)
        else:
            matched["observations"].append(observation)
            matched["association_reasons"] = sorted(set(matched.get("association_reasons", []) + match_reasons))
            matched["comparability_status"] = "weak" if match_comparability == "weak" else "comparable"
            matched["limitations"] = _limitations(matched.get("limitations"), observation.get("limitations"), match_limitations)
            matched["track_confidence"] = "medium" if matched["comparability_status"] == "weak" else "high"

    for track in tracks:
        track.update(evaluate_track_trend(track))
        track["latest_location"] = track["observations"][-1].get("location", {})
    return tracks


def _relative_delta(first: float | None, last: float | None) -> float | None:
    if first is None or last is None:
        return None
    if first == 0:
        return None if last == 0 else 1.0
    return (last - first) / abs(first)



def _trend_comparability_status(comparability: str, same_run: bool) -> str:
    if same_run:
        return "same-run-not-growth-evidence"
    if comparability == "comparable":
        return "comparable-cross-cycle"
    if comparability == "weak":
        return "weak-cross-cycle"
    return comparability

def evaluate_track_trend(track: dict[str, Any]) -> dict[str, Any]:
    observations = track.get("observations", [])
    if len(observations) <= 1:
        latest = observations[0] if observations else {}
        return {
            "trend": {
                "label": "baseline-only",
                "reason": "Only one observation exists; no growth conclusion is possible.",
                "metrics": {},
                "measurement_basis": latest.get("measurement_basis", "unknown"),
                "claim_level": ["not_field_verified", "not_prediction", "rule_evidence_only"],
                "comparability_status": "single-observation-baseline",
                "limitations": [],
            },
            "review_required": bool(observations and observations[0].get("risk", {}).get("review_required")),
        }

    first = observations[0]
    latest = observations[-1]
    area_delta = None if first.get("area") is None or latest.get("area") is None else latest["area"] - first["area"]
    skeleton_delta = None if first.get("skeleton_length") is None or latest.get("skeleton_length") is None else latest["skeleton_length"] - first["skeleton_length"]
    center_shift = _center_distance(first, latest)
    risk_delta = latest.get("risk", {}).get("score", 0.0) - first.get("risk", {}).get("score", 0.0)
    area_ratio = _relative_delta(first.get("area"), latest.get("area"))
    same_run = first.get("inspection_run_id") == latest.get("inspection_run_id")
    comparability, comparability_limitations = _comparable(first, latest)
    metrics = {
        "area_delta": area_delta,
        "area_relative_delta": area_ratio,
        "skeleton_length_delta": skeleton_delta,
        "centroid_shift": center_shift,
        "risk_delta": risk_delta,
    }

    changed = any(
        value is not None and value > threshold
        for value, threshold in ((area_ratio, 0.2), (skeleton_delta, 5.0), (center_shift, 0.15), (risk_delta, 0.5))
    )
    claim_level = ["rule_evidence_only", "not_prediction", "not_field_verified"]
    review_required = changed or any(obs.get("risk", {}).get("review_required") for obs in observations)
    if review_required:
        claim_level.append("requires_manual_review")

    if not changed:
        label = "stable"
        reason = "Observed changes are below rule thresholds."
    elif same_run:
        label = "apparent-change-evidence"
        reason = "Changes are within the same inspection run, so they are image evidence only and not growth prediction."
    elif comparability == "comparable" and ((area_ratio is not None and area_ratio > 0.2) or (skeleton_delta is not None and skeleton_delta > 5.0)):
        label = "suspected-growth"
        reason = "Comparable cross-cycle observations show increasing image evidence."
    elif comparability == "comparable" and center_shift is not None and center_shift > 0.15:
        label = "suspected-image-shape-change"
        reason = "Comparable observations show image-shape movement; this is not verified structural deformation."
    else:
        label = "uncertain"
        reason = "Observation changes are not comparable enough for a growth or shape-change label."

    return {
        "trend": {
            "label": label,
            "reason": reason,
            "metrics": metrics,
            "measurement_basis": latest.get("measurement_basis", "unknown"),
            "claim_level": sorted(set(claim_level)),
            "comparability_status": _trend_comparability_status(comparability, same_run),
            "limitations": comparability_limitations,
        },
        "review_required": review_required,
    }
