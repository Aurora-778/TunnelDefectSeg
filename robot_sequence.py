from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


_MILEAGE_RE = re.compile(r"[Kk]?(?P<km>\d+)\s*\+\s*(?P<m>\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class FrameRecord:
    frame_id: str
    manifest_index: int
    image_path: str | None = None
    report_path: str | None = None
    timestamp: str | None = None
    mileage: Any = None
    ring_id: str | None = None
    camera_id: str | None = None
    inspection_run_id: str | None = None
    pose: dict[str, Any] | None = None
    calibration: dict[str, Any] | None = None
    depth: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    ordering_confidence: str = "low"
    location_confidence: str = "low"
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_manifest(manifest: str | Path | dict[str, Any]) -> tuple[dict[str, Any], Path | None]:
    if isinstance(manifest, (str, Path)):
        path = Path(manifest)
        return json.loads(path.read_text(encoding="utf-8")), path.parent
    return dict(manifest), None


def _parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_mileage(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    match = _MILEAGE_RE.search(text)
    if not match:
        return None
    return float(match.group("km")) * 1000.0 + float(match.group("m"))


def _path_text(value: Any, base_dir: Path | None) -> str | None:
    if value in (None, ""):
        return None
    path = Path(str(value))
    if base_dir is not None and not path.is_absolute():
        path = base_dir / path
    return str(path).replace("\\", "/")


def _frame_limitations(frame: dict[str, Any]) -> list[str]:
    limitations: list[str] = []
    if parse_mileage(frame.get("mileage")) is None:
        limitations.append("mileage_missing")
    if not frame.get("ring_id"):
        limitations.append("ring_id_missing")
    if not frame.get("timestamp"):
        limitations.append("timestamp_missing")
    if not frame.get("camera_id"):
        limitations.append("camera_id_missing")
    if not frame.get("pose"):
        limitations.append("pose_missing")
    if not frame.get("calibration"):
        limitations.append("calibration_missing")
    if frame.get("depth") in (None, ""):
        limitations.append("depth_missing")
    return sorted(set(limitations))


def _confidence(frame: dict[str, Any]) -> tuple[str, str]:
    has_mileage = parse_mileage(frame.get("mileage")) is not None
    has_timestamp = _parse_timestamp(frame.get("timestamp")) is not None
    has_ring = bool(frame.get("ring_id"))
    ordering = "high" if has_mileage and has_timestamp else "medium" if has_mileage or has_timestamp else "low"
    location = "high" if has_mileage and has_ring else "medium" if has_mileage or has_ring else "low"
    return ordering, location


def load_sequence_manifest(manifest: str | Path | dict[str, Any]) -> list[FrameRecord]:
    data, base_dir = _read_manifest(manifest)
    frames = data.get("frames")
    if not isinstance(frames, list):
        raise ValueError("robot sequence manifest must contain a frames list")

    default_run_id = data.get("inspection_run_id") or data.get("run_id")
    seen: set[str] = set()
    records: list[FrameRecord] = []
    for index, raw_frame in enumerate(frames):
        if not isinstance(raw_frame, dict):
            raise ValueError(f"frame at index {index} must be an object")
        frame_id = str(raw_frame.get("frame_id") or "").strip()
        if not frame_id:
            raise ValueError(f"frame at index {index} is missing frame_id")
        if frame_id in seen:
            raise ValueError(f"duplicate frame_id: {frame_id}")
        seen.add(frame_id)

        merged = dict(raw_frame)
        ordering_confidence, location_confidence = _confidence(merged)
        limitations = _frame_limitations(merged)
        records.append(
            FrameRecord(
                frame_id=frame_id,
                manifest_index=index,
                image_path=_path_text(merged.get("image_path"), base_dir),
                report_path=_path_text(merged.get("report_path"), base_dir),
                timestamp=str(merged["timestamp"]) if merged.get("timestamp") not in (None, "") else None,
                mileage=merged.get("mileage"),
                ring_id=merged.get("ring_id"),
                camera_id=merged.get("camera_id"),
                inspection_run_id=merged.get("inspection_run_id") or default_run_id,
                pose=merged.get("pose"),
                calibration=merged.get("calibration") or merged.get("camera_intrinsics"),
                depth=merged.get("depth"),
                metadata={k: v for k, v in merged.items() if k not in {
                    "frame_id", "image_path", "report_path", "timestamp", "mileage", "ring_id",
                    "camera_id", "inspection_run_id", "pose", "calibration", "camera_intrinsics", "depth",
                }},
                ordering_confidence=ordering_confidence,
                location_confidence=location_confidence,
                limitations=limitations,
            )
        )
    return sort_frame_records(records)


def sort_frame_records(frames: Iterable[FrameRecord]) -> list[FrameRecord]:
    records = list(frames)
    all_have_mileage = all(parse_mileage(frame.mileage) is not None for frame in records)
    any_have_timestamp = any(_parse_timestamp(frame.timestamp) is not None for frame in records)

    if all_have_mileage:
        return sorted(
            records,
            key=lambda frame: (
                parse_mileage(frame.mileage) if parse_mileage(frame.mileage) is not None else float("inf"),
                _parse_timestamp(frame.timestamp) or datetime.max.replace(tzinfo=timezone.utc),
                frame.manifest_index,
            ),
        )
    if any_have_timestamp:
        return sorted(records, key=lambda frame: (_parse_timestamp(frame.timestamp) or datetime.max.replace(tzinfo=timezone.utc), frame.manifest_index))
    return sorted(records, key=lambda frame: frame.manifest_index)


def records_to_dicts(frames: Iterable[FrameRecord]) -> list[dict[str, Any]]:
    return [frame.to_dict() for frame in frames]
