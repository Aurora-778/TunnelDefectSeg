from __future__ import annotations

import argparse
import csv
from pathlib import Path

from extract_video_frames import safe_video_id


REQUIRED_FEATURE_FIELDS = [
    "frame_id",
    "image_path",
    "mask_path",
    "bbox_x",
    "bbox_y",
    "bbox_w",
    "bbox_h",
    "center_x",
    "center_y",
    "disease_area",
    "risk_level",
]
OUTPUT_FIELDS = [
    "video_id",
    "frame_id",
    "image_path",
    "mask_path",
    "bbox_x",
    "bbox_y",
    "bbox_w",
    "bbox_h",
    "bbox_x1",
    "bbox_y1",
    "bbox_x2",
    "bbox_y2",
    "center_x",
    "center_y",
    "disease_area",
    "risk_level",
    "class_id",
    "class_name",
    "confidence",
    "source",
    "note",
]
SOURCE = "mask_input_demo"
NOTE = "confidence is fixed at 1.0 because this record comes from provided masks, not model inference"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert video disease features to a supervision-style detection manifest.")
    parser.add_argument("--video_id", "--video-id", dest="video_id", required=True)
    parser.add_argument("--features_csv", "--features-csv", dest="features_csv", type=Path, default=None)
    parser.add_argument("--output_manifest", "--output-manifest", dest="output_manifest", type=Path, default=None)
    parser.add_argument("--overwrite", action="store_true", help="Overwrite an existing detection manifest.")
    return parser.parse_args()


def default_paths(video_id: str) -> tuple[Path, Path]:
    resolved_video_id = safe_video_id(video_id)
    return (
        Path("data") / "video_inspection" / resolved_video_id / "disease_features.csv",
        Path("outputs") / "video_inspection" / resolved_video_id / "supervision_detections_manifest.csv",
    )


def read_feature_rows(features_csv: Path) -> list[dict[str, str]]:
    if not features_csv.is_file():
        raise FileNotFoundError(f"disease_features.csv not found: {features_csv}")
    with features_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("disease_features.csv is empty or missing header")
        missing = [field for field in REQUIRED_FEATURE_FIELDS if field not in reader.fieldnames]
        if missing:
            raise ValueError(f"disease_features.csv missing required fields: {', '.join(missing)}")
        rows = list(reader)
    if not rows:
        raise ValueError("disease_features.csv contains no rows")
    return rows


def parse_number(row: dict[str, str], field: str, frame_id: str) -> float:
    value = (row.get(field) or "").strip()
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"frame_id {frame_id} has invalid {field}: {value}") from exc


def normalize_number(value: float) -> str:
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return f"{value:.3f}"


def class_name_for_row(row: dict[str, str]) -> str:
    class_name = (row.get("disease_type") or "").strip()
    return class_name if class_name else "defect"


def convert_rows(feature_rows: list[dict[str, str]], video_id: str) -> list[dict[str, str]]:
    class_ids: dict[str, int] = {}
    output_rows: list[dict[str, str]] = []
    for row in feature_rows:
        frame_id = (row.get("frame_id") or "").strip()
        if not frame_id:
            raise ValueError("disease_features.csv row missing frame_id")
        class_name = class_name_for_row(row)
        if class_name not in class_ids:
            class_ids[class_name] = len(class_ids)
        bbox_x = parse_number(row, "bbox_x", frame_id)
        bbox_y = parse_number(row, "bbox_y", frame_id)
        bbox_w = parse_number(row, "bbox_w", frame_id)
        bbox_h = parse_number(row, "bbox_h", frame_id)
        bbox_x2 = bbox_x + bbox_w if bbox_x >= 0 and bbox_w > 0 else -1
        bbox_y2 = bbox_y + bbox_h if bbox_y >= 0 and bbox_h > 0 else -1
        output_rows.append(
            {
                "video_id": video_id,
                "frame_id": frame_id,
                "image_path": (row.get("image_path") or "").strip(),
                "mask_path": (row.get("mask_path") or "").strip(),
                "bbox_x": normalize_number(bbox_x),
                "bbox_y": normalize_number(bbox_y),
                "bbox_w": normalize_number(bbox_w),
                "bbox_h": normalize_number(bbox_h),
                "bbox_x1": normalize_number(bbox_x if bbox_x >= 0 else -1),
                "bbox_y1": normalize_number(bbox_y if bbox_y >= 0 else -1),
                "bbox_x2": normalize_number(bbox_x2),
                "bbox_y2": normalize_number(bbox_y2),
                "center_x": normalize_number(parse_number(row, "center_x", frame_id)),
                "center_y": normalize_number(parse_number(row, "center_y", frame_id)),
                "disease_area": normalize_number(parse_number(row, "disease_area", frame_id)),
                "risk_level": (row.get("risk_level") or "unknown").strip() or "unknown",
                "class_id": str(class_ids[class_name]),
                "class_name": class_name,
                "confidence": "1.0",
                "source": SOURCE,
                "note": NOTE,
            }
        )
    return output_rows


def write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def convert_video_features_to_detections(
    video_id: str,
    features_csv: Path | None = None,
    output_manifest: Path | None = None,
    overwrite: bool = False,
) -> list[dict[str, str]]:
    resolved_video_id = safe_video_id(video_id)
    default_features, default_manifest = default_paths(resolved_video_id)
    features_csv = features_csv or default_features
    output_manifest = output_manifest or default_manifest
    if output_manifest.exists() and not overwrite:
        raise FileExistsError(f"detection manifest already exists; rerun with --overwrite: {output_manifest}")
    rows = convert_rows(read_feature_rows(features_csv), resolved_video_id)
    write_manifest(output_manifest, rows)
    return rows


def main() -> None:
    args = parse_args()
    try:
        rows = convert_video_features_to_detections(
            video_id=args.video_id,
            features_csv=args.features_csv,
            output_manifest=args.output_manifest,
            overwrite=args.overwrite,
        )
    except Exception as exc:
        raise SystemExit(f"error: {exc}") from exc

    _, output_manifest = default_paths(safe_video_id(args.video_id))
    print("video features converted to detection manifest")
    print(f"video_id: {safe_video_id(args.video_id)}")
    print(f"output_manifest: {args.output_manifest or output_manifest}")
    print(f"detection_rows: {len(rows)}")


if __name__ == "__main__":
    main()
