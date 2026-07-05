import csv
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image


pytest.importorskip("cv2")


def write_frame(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = np.zeros((32, 48, 3), dtype=np.uint8)
    image[:, :] = color
    Image.fromarray(image).save(path)


def write_mask(path: Path, bbox: tuple[int, int, int, int] | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mask = np.zeros((32, 48), dtype=np.uint8)
    if bbox is not None:
        x1, y1, x2, y2 = bbox
        mask[y1:y2, x1:x2] = 255
    Image.fromarray(mask).save(path)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_disease_features(path: Path, masks_dir: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_mask(masks_dir / "frame_000001.png", (4, 5, 14, 13))
    write_mask(masks_dir / "frame_000002.png", None)
    fields = [
        "inspection_id",
        "frame_id",
        "image_path",
        "mask_path",
        "video_time_sec",
        "timestamp",
        "mileage_m",
        "mileage_text",
        "ring_id",
        "position_angle",
        "camera_id",
        "disease_area",
        "bbox_x",
        "bbox_y",
        "bbox_w",
        "bbox_h",
        "center_x",
        "center_y",
        "mask_width",
        "mask_height",
        "disease_type",
        "risk_level",
        "feature_source",
        "feature_limit_note",
    ]
    rows = [
        {
            "inspection_id": "demo",
            "frame_id": "frame_000001",
            "image_path": "frame_000001.jpg",
            "mask_path": (masks_dir / "frame_000001.png").as_posix(),
            "video_time_sec": "0.0",
            "timestamp": "2026-07-03T00:00:00",
            "mileage_m": "0.0",
            "mileage_text": "K0+000.0",
            "ring_id": "0",
            "position_angle": "unknown",
            "camera_id": "camera_01",
            "disease_area": "80",
            "bbox_x": "4",
            "bbox_y": "5",
            "bbox_w": "10",
            "bbox_h": "8",
            "center_x": "8.5",
            "center_y": "8.5",
            "mask_width": "48",
            "mask_height": "32",
            "disease_type": "unknown",
            "risk_level": "low",
            "feature_source": "mask_nonzero_pixels",
            "feature_limit_note": "features are extracted from provided masks, not model inference",
        },
        {
            "inspection_id": "demo",
            "frame_id": "frame_000002",
            "image_path": "frame_000002.jpg",
            "mask_path": (masks_dir / "frame_000002.png").as_posix(),
            "video_time_sec": "0.1",
            "timestamp": "2026-07-03T00:00:01",
            "mileage_m": "0.0",
            "mileage_text": "K0+000.0",
            "ring_id": "0",
            "position_angle": "unknown",
            "camera_id": "camera_01",
            "disease_area": "0",
            "bbox_x": "-1",
            "bbox_y": "-1",
            "bbox_w": "-1",
            "bbox_h": "-1",
            "center_x": "-1",
            "center_y": "-1",
            "mask_width": "48",
            "mask_height": "32",
            "disease_type": "unknown",
            "risk_level": "low",
            "feature_source": "mask_nonzero_pixels",
            "feature_limit_note": "features are extracted from provided masks, not model inference",
        },
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def prepare_visualization_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    frames_dir = tmp_path / "data" / "video_frames" / "demo"
    masks_dir = tmp_path / "data" / "video_inspection" / "demo" / "sampled_masks"
    features_csv = tmp_path / "data" / "video_inspection" / "demo" / "disease_features.csv"
    output_dir = tmp_path / "outputs" / "video_inspection" / "demo" / "annotated_frames"
    output_manifest = tmp_path / "outputs" / "video_inspection" / "demo" / "video_visualization_manifest.csv"
    write_frame(frames_dir / "frame_000001.jpg", (40, 80, 120))
    write_frame(frames_dir / "frame_000002.jpg", (80, 40, 100))
    write_disease_features(features_csv, masks_dir)
    return frames_dir, features_csv, output_dir, output_manifest, tmp_path / "outputs" / "video_inspection" / "demo"


def test_annotate_video_frames_generates_frames_and_manifest(tmp_path):
    frames_dir, features_csv, output_dir, output_manifest, _ = prepare_visualization_inputs(tmp_path)
    simulated_dir = tmp_path / "data" / "simulated"
    simulated_dir.mkdir(parents=True)
    sentinel = simulated_dir / "inspection_sequence.csv"
    sentinel.write_text("do not touch", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/annotate_video_frames.py",
            "--video_id",
            "demo",
            "--frames_dir",
            str(frames_dir),
            "--features_csv",
            str(features_csv),
            "--output_dir",
            str(output_dir),
            "--output_manifest",
            str(output_manifest),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert (output_dir / "frame_000001.jpg").is_file()
    assert (output_dir / "frame_000002.jpg").is_file()
    rows = read_csv(output_manifest)
    assert len(rows) == 2
    required = {
        "video_id",
        "frame_id",
        "source_frame_path",
        "mask_path",
        "annotated_frame_path",
        "risk_level",
        "disease_area",
        "bbox_x",
        "bbox_y",
        "bbox_w",
        "bbox_h",
        "overlay_available",
        "visualization_note",
    }
    assert required.issubset(rows[0])
    assert rows[0]["overlay_available"] == "true"
    assert rows[1]["overlay_available"] == "false"
    assert "not model inference" in rows[0]["visualization_note"]
    assert sentinel.read_text(encoding="utf-8") == "do not touch"


def test_export_annotated_video_generates_mp4(tmp_path):
    frames_dir, features_csv, output_dir, output_manifest, output_root = prepare_visualization_inputs(tmp_path)
    subprocess.run(
        [
            sys.executable,
            "scripts/annotate_video_frames.py",
            "--video_id",
            "demo",
            "--frames_dir",
            str(frames_dir),
            "--features_csv",
            str(features_csv),
            "--output_dir",
            str(output_dir),
            "--output_manifest",
            str(output_manifest),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    output_video = output_root / "annotated_video.mp4"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/export_annotated_video.py",
            "--video_id",
            "demo",
            "--frames_dir",
            str(output_dir),
            "--output_video",
            str(output_video),
            "--fps",
            "5",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert output_video.is_file()
    assert output_video.stat().st_size > 0


def test_annotate_video_frames_reports_missing_features_csv(tmp_path):
    frames_dir = tmp_path / "data" / "video_frames" / "demo"
    write_frame(frames_dir / "frame_000001.jpg", (40, 80, 120))

    result = subprocess.run(
        [
            sys.executable,
            "scripts/annotate_video_frames.py",
            "--video_id",
            "demo",
            "--frames_dir",
            str(frames_dir),
            "--features_csv",
            str(tmp_path / "missing" / "disease_features.csv"),
            "--output_dir",
            str(tmp_path / "outputs" / "frames"),
            "--output_manifest",
            str(tmp_path / "outputs" / "manifest.csv"),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "disease_features.csv not found" in result.stderr
