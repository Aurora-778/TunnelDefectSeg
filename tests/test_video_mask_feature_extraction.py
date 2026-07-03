import csv
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_mask(path: Path, array: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(array.astype(np.uint8)).save(path)


def manifest_fields() -> list[str]:
    return ["video_id", "frame_id", "frame_index", "video_time_sec", "image_path", "fps", "sample_interval"]


def metadata_fields() -> list[str]:
    return [
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


def base_manifest_rows() -> list[dict[str, str]]:
    return [
        {
            "video_id": "video_001",
            "frame_id": "frame_000001",
            "frame_index": "0",
            "video_time_sec": "0.000000",
            "image_path": "data/video_frames/video_001/frame_000001.jpg",
            "fps": "10.000000",
            "sample_interval": "5",
        },
        {
            "video_id": "video_001",
            "frame_id": "frame_000002",
            "frame_index": "5",
            "video_time_sec": "0.500000",
            "image_path": "data/video_frames/video_001/frame_000002.jpg",
            "fps": "10.000000",
            "sample_interval": "5",
        },
    ]


def base_metadata_rows() -> list[dict[str, str]]:
    return [
        {
            "inspection_id": "video_001",
            "frame_id": "frame_000001",
            "timestamp": "2026-07-03T10:00:00",
            "video_time_sec": "0.000000",
            "image_path": "data/video_frames/video_001/frame_000001.jpg",
            "mileage_m": "12000.000",
            "mileage_text": "K12+000.0",
            "ring_id": "10000",
            "position_angle": "12点",
            "camera_id": "camera_01",
            "robot_pose": "estimated_mileage_m=12000.000;position_angle=12点",
            "metadata_source": "estimated_from_video_time",
            "metadata_limit_note": "mileage and ring_id are estimated, not real robot localization",
        },
        {
            "inspection_id": "video_001",
            "frame_id": "frame_000002",
            "timestamp": "2026-07-03T10:00:01",
            "video_time_sec": "0.500000",
            "image_path": "data/video_frames/video_001/frame_000002.jpg",
            "mileage_m": "12001.000",
            "mileage_text": "K12+001.0",
            "ring_id": "10001",
            "position_angle": "12点",
            "camera_id": "camera_01",
            "robot_pose": "estimated_mileage_m=12001.000;position_angle=12点",
            "metadata_source": "estimated_from_video_time",
            "metadata_limit_note": "mileage and ring_id are estimated, not real robot localization",
        },
    ]


def run_extract(manifest: Path, metadata: Path, masks_dir: Path, output_csv: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/extract_video_mask_features.py",
            "--frames_manifest",
            str(manifest),
            "--metadata_csv",
            str(metadata),
            "--masks_dir",
            str(masks_dir),
            "--output_csv",
            str(output_csv),
        ],
        capture_output=True,
        text=True,
    )


def write_valid_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    manifest = tmp_path / "frames_manifest.csv"
    metadata = tmp_path / "metadata.csv"
    masks_dir = tmp_path / "masks"
    output_csv = tmp_path / "disease_features.csv"
    write_csv(manifest, base_manifest_rows(), manifest_fields())
    write_csv(metadata, base_metadata_rows(), metadata_fields())

    non_empty = np.zeros((8, 10), dtype=np.uint8)
    non_empty[2:5, 3:7] = 255
    write_mask(masks_dir / "frame_000001.png", non_empty)
    write_mask(masks_dir / "frame_000002.png", np.zeros((6, 7), dtype=np.uint8))
    return manifest, metadata, masks_dir, output_csv


def test_extract_video_mask_features_writes_geometry_rows(tmp_path):
    manifest, metadata, masks_dir, output_csv = write_valid_inputs(tmp_path)

    result = run_extract(manifest, metadata, masks_dir, output_csv)

    assert result.returncode == 0, result.stderr
    assert "video mask feature extraction completed" in result.stdout
    rows = read_csv(output_csv)
    assert len(rows) == 2
    first = rows[0]
    assert first["inspection_id"] == "video_001"
    assert first["frame_id"] == "frame_000001"
    assert first["image_path"] == "data/video_frames/video_001/frame_000001.jpg"
    assert first["mask_path"].endswith("frame_000001.png")
    assert first["video_time_sec"] == "0.000000"
    assert first["mileage_text"] == "K12+000.0"
    assert first["disease_area"] == "12"
    assert first["bbox_x"] == "3"
    assert first["bbox_y"] == "2"
    assert first["bbox_w"] == "4"
    assert first["bbox_h"] == "3"
    assert first["center_x"] == "4.500"
    assert first["center_y"] == "3.000"
    assert first["mask_width"] == "10"
    assert first["mask_height"] == "8"
    assert first["disease_type"] == "unknown"
    assert first["risk_level"] == "low"
    assert first["feature_source"] == "mask_nonzero_pixels"
    assert "features are extracted from provided masks, not model inference" in first["feature_limit_note"]
    assert "rule_based_area_only" in first["feature_limit_note"]


def test_extract_video_mask_features_handles_empty_mask(tmp_path):
    manifest, metadata, masks_dir, output_csv = write_valid_inputs(tmp_path)

    result = run_extract(manifest, metadata, masks_dir, output_csv)

    assert result.returncode == 0, result.stderr
    second = read_csv(output_csv)[1]
    assert second["disease_area"] == "0"
    assert second["bbox_x"] == "-1"
    assert second["bbox_y"] == "-1"
    assert second["bbox_w"] == "-1"
    assert second["bbox_h"] == "-1"
    assert second["center_x"] == "-1"
    assert second["center_y"] == "-1"
    assert second["mask_width"] == "7"
    assert second["mask_height"] == "6"


def test_extract_video_mask_features_rejects_missing_mask(tmp_path):
    manifest, metadata, masks_dir, output_csv = write_valid_inputs(tmp_path)
    (masks_dir / "frame_000002.png").unlink()

    result = run_extract(manifest, metadata, masks_dir, output_csv)

    assert result.returncode != 0
    assert "mask not found for frame_id: frame_000002" in result.stderr


def test_extract_video_mask_features_accepts_dash_cli_aliases(tmp_path):
    manifest, metadata, masks_dir, output_csv = write_valid_inputs(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/extract_video_mask_features.py",
            "--frames-manifest",
            str(manifest),
            "--metadata-csv",
            str(metadata),
            "--masks-dir",
            str(masks_dir),
            "--output-csv",
            str(output_csv),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert len(read_csv(output_csv)) == 2


def test_extract_video_mask_features_rejects_duplicate_manifest_frame_id(tmp_path):
    manifest, metadata, masks_dir, output_csv = write_valid_inputs(tmp_path)
    rows = base_manifest_rows()
    rows[1]["frame_id"] = "frame_000001"
    write_csv(manifest, rows, manifest_fields())

    result = run_extract(manifest, metadata, masks_dir, output_csv)

    assert result.returncode != 0
    assert "frames_manifest duplicate frame_id: frame_000001" in result.stderr


def test_extract_video_mask_features_rejects_empty_manifest_required_value(tmp_path):
    manifest, metadata, masks_dir, output_csv = write_valid_inputs(tmp_path)
    rows = base_manifest_rows()
    rows[0]["image_path"] = ""
    write_csv(manifest, rows, manifest_fields())

    result = run_extract(manifest, metadata, masks_dir, output_csv)

    assert result.returncode != 0
    assert "frames_manifest row 1 missing image_path" in result.stderr


def test_extract_video_mask_features_rejects_metadata_missing_frame_id(tmp_path):
    manifest, metadata, masks_dir, output_csv = write_valid_inputs(tmp_path)
    fields = [field for field in metadata_fields() if field != "frame_id"]
    rows = [{key: value for key, value in row.items() if key != "frame_id"} for row in base_metadata_rows()]
    write_csv(metadata, rows, fields)

    result = run_extract(manifest, metadata, masks_dir, output_csv)

    assert result.returncode != 0
    assert "metadata_csv missing required fields: frame_id" in result.stderr


def test_extract_video_mask_features_rejects_empty_metadata_required_value(tmp_path):
    manifest, metadata, masks_dir, output_csv = write_valid_inputs(tmp_path)
    rows = base_metadata_rows()
    rows[0]["timestamp"] = ""
    write_csv(metadata, rows, metadata_fields())

    result = run_extract(manifest, metadata, masks_dir, output_csv)

    assert result.returncode != 0
    assert "metadata_csv row 1 missing timestamp" in result.stderr


def test_extract_video_mask_features_rejects_duplicate_metadata_frame_id(tmp_path):
    manifest, metadata, masks_dir, output_csv = write_valid_inputs(tmp_path)
    rows = base_metadata_rows()
    rows[1]["frame_id"] = "frame_000001"
    write_csv(metadata, rows, metadata_fields())

    result = run_extract(manifest, metadata, masks_dir, output_csv)

    assert result.returncode != 0
    assert "metadata_csv duplicate frame_id: frame_000001" in result.stderr


def test_extract_video_mask_features_rejects_manifest_frame_missing_from_metadata(tmp_path):
    manifest, metadata, masks_dir, output_csv = write_valid_inputs(tmp_path)
    write_csv(metadata, [base_metadata_rows()[0]], metadata_fields())

    result = run_extract(manifest, metadata, masks_dir, output_csv)

    assert result.returncode != 0
    assert "metadata not found for frame_id: frame_000002" in result.stderr


def test_extract_video_mask_features_rejects_manifest_metadata_image_path_mismatch(tmp_path):
    manifest, metadata, masks_dir, output_csv = write_valid_inputs(tmp_path)
    rows = base_metadata_rows()
    rows[0]["image_path"] = "data/video_frames/video_001/other_frame.jpg"
    write_csv(metadata, rows, metadata_fields())

    result = run_extract(manifest, metadata, masks_dir, output_csv)

    assert result.returncode != 0
    assert "frame_id frame_000001 image_path mismatch" in result.stderr


def test_extract_video_mask_features_rejects_manifest_metadata_video_time_mismatch(tmp_path):
    manifest, metadata, masks_dir, output_csv = write_valid_inputs(tmp_path)
    rows = base_metadata_rows()
    rows[0]["video_time_sec"] = "0.250000"
    write_csv(metadata, rows, metadata_fields())

    result = run_extract(manifest, metadata, masks_dir, output_csv)

    assert result.returncode != 0
    assert "frame_id frame_000001 video_time_sec mismatch" in result.stderr
