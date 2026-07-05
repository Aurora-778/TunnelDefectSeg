from __future__ import annotations

import argparse
import csv
from pathlib import Path

from extract_video_frames import safe_video_id


MISSING_MESSAGE = "尚未生成该视频分析产物，请先运行 Step 1 / Step 2 / Step 3。"
CSV_REQUIREMENTS = {
    "frames_manifest": ["video_id", "frame_id", "frame_index", "video_time_sec", "image_path"],
    "metadata": ["inspection_id", "frame_id", "timestamp", "video_time_sec", "image_path"],
    "disease_features": ["inspection_id", "frame_id", "image_path", "mask_path", "disease_area"],
    "inspection_sequence": ["inspection_id", "frame_id", "image_id", "image_path", "mask_path"],
    "video_visualization_manifest": ["video_id", "frame_id", "annotated_frame_path", "overlay_available"],
    "supervision_detections_manifest": ["video_id", "frame_id", "bbox_x1", "bbox_y1", "bbox_x2", "bbox_y2"],
    "supervision_visualization_manifest": ["video_id", "frame_id", "output_annotated_frame_path", "visualization_source"],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate generated video inspection demo artifacts.")
    parser.add_argument("--video_id", "--video-id", dest="video_id", default="tunnel_demo")
    parser.add_argument("--video_root", "--video-root", dest="video_root", type=Path, default=Path("data/videos"))
    parser.add_argument("--frames_root", "--frames-root", dest="frames_root", type=Path, default=Path("data/video_frames"))
    parser.add_argument(
        "--inspection_root",
        "--inspection-root",
        dest="inspection_root",
        type=Path,
        default=Path("data/video_inspection"),
    )
    parser.add_argument("--output_root", "--output-root", dest="output_root", type=Path, default=Path("outputs/video_inspection"))
    parser.add_argument("--skip_supervision", "--skip-supervision", dest="skip_supervision", action="store_true")
    return parser.parse_args()


def read_csv_rows(path: Path, label: str, required_fields: list[str]) -> tuple[list[dict[str, str]], list[str]]:
    errors: list[str] = []
    if not path.is_file():
        return [], [f"{label} missing: {path}"]
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                return [], [f"{label} is empty or missing header: {path}"]
            missing = [field for field in required_fields if field not in reader.fieldnames]
            if missing:
                errors.append(f"{label} missing required fields: {', '.join(missing)}")
            rows = list(reader)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        return [], [f"{label} unreadable: {path} ({exc})"]
    if not rows:
        errors.append(f"{label} contains no rows: {path}")
    return rows, errors


def file_status(path: Path, label: str) -> dict[str, str | bool | int]:
    exists = path.is_file()
    size = path.stat().st_size if exists else 0
    ok = exists and size > 0
    return {
        "label": label,
        "path": path.as_posix(),
        "exists": exists,
        "size_bytes": size,
        "ok": ok,
        "message": "" if ok else f"{label} missing or empty: {path}",
    }


def csv_status(path: Path, label: str, required_fields: list[str]) -> dict[str, str | bool | int | list[str]]:
    rows, errors = read_csv_rows(path, label, required_fields)
    return {
        "label": label,
        "path": path.as_posix(),
        "exists": path.is_file(),
        "row_count": len(rows),
        "ok": not errors,
        "errors": errors,
    }


def validate_video_artifacts(
    video_id: str,
    video_root: Path = Path("data/videos"),
    frames_root: Path = Path("data/video_frames"),
    inspection_root: Path = Path("data/video_inspection"),
    output_root: Path = Path("outputs/video_inspection"),
    require_supervision: bool = True,
) -> dict:
    resolved_video_id = safe_video_id(video_id)
    inspection_dir = inspection_root / resolved_video_id
    output_dir = output_root / resolved_video_id
    checks = [
        file_status(video_root / f"{resolved_video_id}.mp4", "demo_video"),
        csv_status(frames_root / resolved_video_id / "frames_manifest.csv", "frames_manifest", CSV_REQUIREMENTS["frames_manifest"]),
        csv_status(inspection_dir / "metadata.csv", "metadata", CSV_REQUIREMENTS["metadata"]),
        csv_status(inspection_dir / "disease_features.csv", "disease_features", CSV_REQUIREMENTS["disease_features"]),
        csv_status(inspection_dir / "inspection_sequence.csv", "inspection_sequence", CSV_REQUIREMENTS["inspection_sequence"]),
        csv_status(
            output_dir / "video_visualization_manifest.csv",
            "video_visualization_manifest",
            CSV_REQUIREMENTS["video_visualization_manifest"],
        ),
        file_status(output_dir / "annotated_video.mp4", "annotated_video"),
    ]
    if require_supervision:
        checks.extend(
            [
                csv_status(
                    output_dir / "supervision_detections_manifest.csv",
                    "supervision_detections_manifest",
                    CSV_REQUIREMENTS["supervision_detections_manifest"],
                ),
                csv_status(
                    output_dir / "supervision_visualization_manifest.csv",
                    "supervision_visualization_manifest",
                    CSV_REQUIREMENTS["supervision_visualization_manifest"],
                ),
                file_status(output_dir / "supervision_annotated_video.mp4", "supervision_annotated_video"),
            ]
        )
    ok = all(bool(check["ok"]) for check in checks)
    return {
        "ok": ok,
        "video_id": resolved_video_id,
        "missing_message": "" if ok else MISSING_MESSAGE,
        "checks": checks,
    }


def main() -> None:
    args = parse_args()
    result = validate_video_artifacts(
        video_id=args.video_id,
        video_root=args.video_root,
        frames_root=args.frames_root,
        inspection_root=args.inspection_root,
        output_root=args.output_root,
        require_supervision=not args.skip_supervision,
    )
    print(f"video artifact validation: {'passed' if result['ok'] else 'failed'}")
    print(f"video_id: {result['video_id']}")
    for check in result["checks"]:
        status = "ok" if check["ok"] else "missing/error"
        detail = check.get("errors") or check.get("message") or ""
        print(f"- {check['label']}: {status} ({check['path']}) {detail}")
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
