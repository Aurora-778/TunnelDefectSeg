from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from robot_sequence import FrameRecord, load_sequence_manifest
from spatiotemporal_monitoring import build_defect_tracks, build_observations

SCHEMA_VERSION = "robot-inspection-report.v1"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _frame_dict(frame: FrameRecord | dict[str, Any]) -> dict[str, Any]:
    return frame.to_dict() if isinstance(frame, FrameRecord) else dict(frame)


def _collect_limitations(frames: list[dict[str, Any]], tracks: list[dict[str, Any]]) -> list[str]:
    limitations: list[str] = []
    for frame in frames:
        limitations.extend(frame.get("limitations", []))
    for track in tracks:
        limitations.extend(track.get("limitations", []))
        trend = track.get("trend", {})
        limitations.extend(trend.get("limitations", []))
    return sorted(set(str(item) for item in limitations if item))


def _review_score(track: dict[str, Any]) -> tuple[int, str]:
    trend_label = track.get("trend", {}).get("label", "baseline-only")
    latest = track.get("observations", [{}])[-1]
    location_confidence = latest.get("confidence", {}).get("location")
    risk_delta = track.get("trend", {}).get("metrics", {}).get("risk_delta") or 0
    score = 0
    reasons: list[str] = []
    if trend_label == "suspected-growth":
        score += 100
        reasons.append("suspected growth evidence")
    if trend_label in {"apparent-change-evidence", "suspected-image-shape-change"}:
        score += 70
        reasons.append("image change evidence")
    if risk_delta > 0:
        score += 30
        reasons.append("risk rising")
    if location_confidence == "low":
        score += 20
        reasons.append("low location confidence")
    if track.get("review_required"):
        score += 10
        reasons.append("manual review required")
    if not reasons:
        reasons.append("routine track")
    return score, ", ".join(reasons)


def _build_review_queue(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    queue = []
    for track in tracks:
        score, reason = _review_score(track)
        queue.append(
            {
                "track_id": track["track_id"],
                "priority_score": score,
                "reason": reason,
                "trend_label": track.get("trend", {}).get("label"),
                "latest_location": track.get("latest_location", {}),
                "claim_level": track.get("trend", {}).get("claim_level", []),
                "requires_manual_review": bool(track.get("review_required") or score >= 50),
            }
        )
    return sorted(queue, key=lambda item: item["priority_score"], reverse=True)


def build_route_inspection_report(
    frames: Iterable[FrameRecord | dict[str, Any]],
    reports_by_frame_id: dict[str, dict[str, Any]] | None = None,
    *,
    tracks: list[dict[str, Any]] | None = None,
    route_id: str | None = None,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    frame_dicts = [_frame_dict(frame) for frame in frames]
    if tracks is None:
        observations = build_observations(frame_dicts, reports_by_frame_id or {})
        tracks = build_defect_tracks(observations)
    review_queue = _build_review_queue(tracks)
    inspection_run_ids = sorted({str(frame.get("inspection_run_id")) for frame in frame_dicts if frame.get("inspection_run_id")})
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "route": {
            "route_id": route_id,
            "inspection_run_ids": inspection_run_ids,
        },
        "summary": {
            "frame_count": len(frame_dicts),
            "track_count": len(tracks),
            "review_count": sum(1 for item in review_queue if item["requires_manual_review"]),
            "limitations": _collect_limitations(frame_dicts, tracks),
        },
        "frames": frame_dicts,
        "tracks": tracks,
        "review_queue": review_queue,
        "claim_guard": {
            "claim_level_required": True,
            "not_prediction_unless_cross_cycle_comparable_or_manual_verified": True,
            "prediction_claim": "not_prediction_unless_cross_cycle_comparable_or_manual_verified",
            "field_verification": "not_field_verified",
            "structural_deformation": "not_certified_structural_deformation",
        },
    }
    safe_report = _json_safe(report)
    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_report["report_path"] = str(path).replace("\\", "/")
        path.write_text(json.dumps(safe_report, ensure_ascii=False, indent=2), encoding="utf-8")
    return safe_report


def build_route_inspection_report_from_manifest(
    manifest: str | Path | dict[str, Any],
    reports_by_frame_id: dict[str, dict[str, Any]],
    *,
    route_id: str | None = None,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    frames = load_sequence_manifest(manifest)
    return build_route_inspection_report(frames, reports_by_frame_id, route_id=route_id, output_path=output_path)
