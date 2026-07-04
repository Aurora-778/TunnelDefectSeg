from __future__ import annotations

import argparse
import csv
import shutil
from pathlib import Path

from extract_video_frames import extract_video_frames, safe_video_id
from generate_video_frame_metadata import generate_video_frame_metadata
from extract_video_mask_features import extract_video_mask_features


INSPECTION_SEQUENCE_FIELDS = [
    "inspection_id",
    "frame_id",
    "image_id",
    "image_path",
    "mask_path",
    "timestamp",
    "video_time_sec",
    "mileage",
    "mileage_m",
    "mileage_text",
    "ring_id",
    "position_angle",
    "camera_id",
    "robot_pose",
    "disease_type",
    "disease_area",
    "bbox_x",
    "bbox_y",
    "bbox_w",
    "bbox_h",
    "center_x",
    "center_y",
    "risk_level",
    "metadata_source",
    "metadata_limit_note",
    "feature_source",
    "feature_limit_note",
]
REQUIRED_METADATA_FIELDS = [
    "inspection_id",
    "frame_id",
    "timestamp",
    "video_time_sec",
    "image_path",
    "mileage_m",
    "mileage_text",
    "ring_id",
    "position_angle",
    "camera_id",
    "robot_pose",
    "metadata_source",
    "metadata_limit_note",
]
REQUIRED_FEATURE_FIELDS = [
    "frame_id",
    "mask_path",
    "disease_type",
    "disease_area",
    "bbox_x",
    "bbox_y",
    "bbox_w",
    "bbox_h",
    "center_x",
    "center_y",
    "risk_level",
    "feature_source",
    "feature_limit_note",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the video inspection CSV pipeline for mask-input demo videos.")
    parser.add_argument("--video_path", "--video-path", dest="video_path", type=Path, required=True)
    parser.add_argument("--video_id", "--video-id", dest="video_id", required=True)
    parser.add_argument("--sample_interval", "--sample-interval", dest="sample_interval", type=int, default=10)
    parser.add_argument("--max_frames", "--max-frames", dest="max_frames", type=int, default=None)
    parser.add_argument("--mode", choices=["mask_input", "model_inference"], default="mask_input")
    parser.add_argument("--masks_dir", "--masks-dir", dest="masks_dir", type=Path, default=None)
    parser.add_argument("--output_root", "--output-root", dest="output_root", type=Path, default=Path("data/video_inspection"))
    parser.add_argument("--frames_root", "--frames-root", dest="frames_root", type=Path, default=Path("data/video_frames"))
    parser.add_argument("--start_timestamp", "--start-timestamp", dest="start_timestamp", default="2026-07-03T00:00:00")
    parser.add_argument("--start_mileage_m", "--start-mileage-m", dest="start_mileage_m", type=float, default=0.0)
    parser.add_argument("--robot_speed_mps", "--robot-speed-mps", dest="robot_speed_mps", type=float, default=0.0)
    parser.add_argument("--ring_length_m", "--ring-length-m", dest="ring_length_m", type=float, default=1.2)
    parser.add_argument("--camera_id", "--camera-id", dest="camera_id", default="camera_01")
    parser.add_argument("--position_angle", "--position-angle", dest="position_angle", default="unknown")
    return parser.parse_args()


def read_csv_rows(path: Path, required_fields: list[str], label: str) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"{label} is empty or missing header")
        missing = [field for field in required_fields if field not in reader.fieldnames]
        if missing:
            raise ValueError(f"{label} missing required fields: {', '.join(missing)}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"{label} contains no rows")
    return rows


def ensure_clean_file(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"output file already exists: {path}")


def ensure_clean_dir(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise FileExistsError(f"output directory is not empty: {path}")


def source_mask_for_frame(masks_dir: Path, frame_index: int) -> Path:
    source_frame_id = f"frame_{frame_index + 1:06d}"
    for suffix in [".png", ".jpg", ".jpeg", ".bmp"]:
        candidate = masks_dir / f"{source_frame_id}{suffix}"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"mask not found for source frame_id: {source_frame_id} in {masks_dir}")


def align_sampled_masks(frames_manifest: Path, source_masks_dir: Path, output_masks_dir: Path) -> Path:
    if not source_masks_dir.is_dir():
        raise FileNotFoundError(f"masks_dir not found: {source_masks_dir}")
    ensure_clean_dir(output_masks_dir)
    output_masks_dir.mkdir(parents=True, exist_ok=True)
    rows = read_csv_rows(frames_manifest, ["frame_id", "frame_index"], "frames_manifest")
    for row_number, row in enumerate(rows, start=1):
        try:
            frame_index = int(row["frame_index"])
        except ValueError as exc:
            raise ValueError(f"frames_manifest row {row_number} has invalid frame_index: {row['frame_index']}") from exc
        source_mask = source_mask_for_frame(source_masks_dir, frame_index)
        # Preserve the original mask suffix so file names do not imply a different image encoding.
        target_mask = output_masks_dir / f"{row['frame_id']}{source_mask.suffix.lower()}"
        shutil.copyfile(source_mask, target_mask)
    return output_masks_dir


def index_rows(rows: list[dict[str, str]], label: str) -> dict[str, dict[str, str]]:
    indexed: dict[str, dict[str, str]] = {}
    for row in rows:
        frame_id = row.get("frame_id", "").strip()
        if not frame_id:
            raise ValueError(f"{label} row missing frame_id")
        if frame_id in indexed:
            raise ValueError(f"{label} duplicate frame_id: {frame_id}")
        indexed[frame_id] = row
    return indexed


def build_inspection_sequence(metadata_csv: Path, disease_features_csv: Path, output_csv: Path) -> list[dict[str, str]]:
    ensure_clean_file(output_csv)
    metadata_rows = read_csv_rows(metadata_csv, REQUIRED_METADATA_FIELDS, "metadata.csv")
    feature_rows = read_csv_rows(disease_features_csv, REQUIRED_FEATURE_FIELDS, "disease_features.csv")
    features_by_frame = index_rows(feature_rows, "disease_features.csv")
    output_rows: list[dict[str, str]] = []

    for metadata in metadata_rows:
        frame_id = metadata["frame_id"]
        feature = features_by_frame.get(frame_id)
        if feature is None:
            raise ValueError(f"disease_features.csv missing frame_id: {frame_id}")
        mileage_m = metadata["mileage_m"]
        output_rows.append(
            {
                "inspection_id": metadata["inspection_id"],
                "frame_id": frame_id,
                "image_id": f"{metadata['inspection_id']}_{frame_id}",
                "image_path": metadata["image_path"],
                "mask_path": feature["mask_path"],
                "timestamp": metadata["timestamp"],
                "video_time_sec": metadata["video_time_sec"],
                "mileage": mileage_m,
                "mileage_m": mileage_m,
                "mileage_text": metadata["mileage_text"],
                "ring_id": metadata["ring_id"],
                "position_angle": metadata["position_angle"],
                "camera_id": metadata["camera_id"],
                "robot_pose": metadata["robot_pose"],
                "disease_type": feature["disease_type"],
                "disease_area": feature["disease_area"],
                "bbox_x": feature["bbox_x"],
                "bbox_y": feature["bbox_y"],
                "bbox_w": feature["bbox_w"],
                "bbox_h": feature["bbox_h"],
                "center_x": feature["center_x"],
                "center_y": feature["center_y"],
                "risk_level": feature["risk_level"],
                "metadata_source": metadata["metadata_source"],
                "metadata_limit_note": metadata["metadata_limit_note"],
                "feature_source": feature["feature_source"],
                "feature_limit_note": feature["feature_limit_note"],
            }
        )

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=INSPECTION_SEQUENCE_FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)
    return output_rows


def run_video_inspection_pipeline(
    video_path: Path,
    video_id: str,
    sample_interval: int = 10,
    max_frames: int | None = None,
    mode: str = "mask_input",
    masks_dir: Path | None = None,
    output_root: Path = Path("data/video_inspection"),
    frames_root: Path = Path("data/video_frames"),
    start_timestamp: str = "2026-07-03T00:00:00",
    start_mileage_m: float = 0.0,
    robot_speed_mps: float = 0.0,
    ring_length_m: float = 1.2,
    camera_id: str = "camera_01",
    position_angle: str = "unknown",
) -> dict[str, Path]:
    if mode == "model_inference":
        raise ValueError("model_inference is reserved for future work")
    if mode != "mask_input":
        raise ValueError(f"unsupported mode: {mode}")
    if masks_dir is None:
        raise ValueError("masks_dir is required when mode=mask_input")

    resolved_video_id = safe_video_id(video_id)
    frames_dir = frames_root / resolved_video_id
    inspection_dir = output_root / resolved_video_id
    metadata_csv = inspection_dir / "metadata.csv"
    disease_features_csv = inspection_dir / "disease_features.csv"
    inspection_sequence_csv = inspection_dir / "inspection_sequence.csv"
    sampled_masks_dir = inspection_dir / "sampled_masks"

    _, frames_manifest = extract_video_frames(
        video_path=video_path,
        output_dir=frames_dir,
        sample_interval=sample_interval,
        max_frames=max_frames,
        video_id=resolved_video_id,
    )
    generate_video_frame_metadata(
        frames_manifest=frames_manifest,
        output_csv=metadata_csv,
        inspection_id=resolved_video_id,
        start_timestamp=start_timestamp,
        start_mileage_m=start_mileage_m,
        robot_speed_mps=robot_speed_mps,
        ring_length_m=ring_length_m,
        camera_id=camera_id,
        position_angle=position_angle,
    )
    aligned_masks_dir = align_sampled_masks(frames_manifest, masks_dir, sampled_masks_dir)
    extract_video_mask_features(
        frames_manifest=frames_manifest,
        metadata_csv=metadata_csv,
        masks_dir=aligned_masks_dir,
        output_csv=disease_features_csv,
    )
    build_inspection_sequence(metadata_csv, disease_features_csv, inspection_sequence_csv)

    return {
        "frames_manifest": frames_manifest,
        "metadata_csv": metadata_csv,
        "disease_features_csv": disease_features_csv,
        "inspection_sequence_csv": inspection_sequence_csv,
        "sampled_masks_dir": sampled_masks_dir,
    }


def main() -> None:
    args = parse_args()
    try:
        outputs = run_video_inspection_pipeline(
            video_path=args.video_path,
            video_id=args.video_id,
            sample_interval=args.sample_interval,
            max_frames=args.max_frames,
            mode=args.mode,
            masks_dir=args.masks_dir,
            output_root=args.output_root,
            frames_root=args.frames_root,
            start_timestamp=args.start_timestamp,
            start_mileage_m=args.start_mileage_m,
            robot_speed_mps=args.robot_speed_mps,
            ring_length_m=args.ring_length_m,
            camera_id=args.camera_id,
            position_angle=args.position_angle,
        )
    except Exception as exc:
        raise SystemExit(f"error: {exc}") from exc

    print("video inspection pipeline completed")
    for name, path in outputs.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
