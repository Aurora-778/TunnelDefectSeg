import csv
import importlib.util
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image


pytest.importorskip("cv2")


def has_supervision() -> bool:
    return importlib.util.find_spec("supervision") is not None


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


def write_disease_features(path: Path, frames_dir: Path, masks_dir: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_frame(frames_dir / "frame_000001.jpg", (40, 80, 120))
    write_frame(frames_dir / "frame_000002.jpg", (80, 40, 100))
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
            "image_path": (frames_dir / "frame_000001.jpg").as_posix(),
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
            "disease_type": "crack",
            "risk_level": "low",
            "feature_source": "mask_nonzero_pixels",
            "feature_limit_note": "features are extracted from provided masks, not model inference",
        },
        {
            "inspection_id": "demo",
            "frame_id": "frame_000002",
            "image_path": (frames_dir / "frame_000002.jpg").as_posix(),
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
            "disease_type": "crack",
            "risk_level": "low",
            "feature_source": "mask_nonzero_pixels",
            "feature_limit_note": "features are extracted from provided masks, not model inference",
        },
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def prepare_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    frames_dir = tmp_path / "data" / "video_frames" / "demo"
    masks_dir = tmp_path / "data" / "video_masks" / "demo"
    features_csv = tmp_path / "data" / "video_inspection" / "demo" / "disease_features.csv"
    output_root = tmp_path / "outputs" / "video_inspection" / "demo"
    write_disease_features(features_csv, frames_dir, masks_dir)
    return frames_dir, masks_dir, features_csv, output_root


def test_convert_video_features_to_detections_generates_manifest(tmp_path):
    _, _, features_csv, output_root = prepare_inputs(tmp_path)
    output_manifest = output_root / "supervision_detections_manifest.csv"
    simulated_dir = tmp_path / "data" / "simulated"
    simulated_dir.mkdir(parents=True)
    sentinel = simulated_dir / "inspection_sequence.csv"
    sentinel.write_text("do not touch", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/convert_video_features_to_detections.py",
            "--video_id",
            "demo",
            "--features_csv",
            str(features_csv),
            "--output_manifest",
            str(output_manifest),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    rows = read_csv(output_manifest)
    required = {
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
    }
    assert len(rows) == 2
    assert required.issubset(rows[0])
    assert rows[0]["video_id"] == "demo"
    assert rows[0]["bbox_x1"] == "4"
    assert rows[0]["bbox_y1"] == "5"
    assert rows[0]["bbox_x2"] == "14"
    assert rows[0]["bbox_y2"] == "13"
    assert rows[0]["confidence"] == "1.0"
    assert rows[0]["source"] == "mask_input_demo"
    assert "not model inference" in rows[0]["note"]
    assert sentinel.read_text(encoding="utf-8") == "do not touch"


def test_convert_video_features_to_detections_requires_overwrite(tmp_path):
    _, _, features_csv, output_root = prepare_inputs(tmp_path)
    output_manifest = output_root / "supervision_detections_manifest.csv"
    output_manifest.parent.mkdir(parents=True)
    output_manifest.write_text("old", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/convert_video_features_to_detections.py",
            "--video_id",
            "demo",
            "--features_csv",
            str(features_csv),
            "--output_manifest",
            str(output_manifest),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "--overwrite" in result.stderr

    overwrite_result = subprocess.run(
        [
            sys.executable,
            "scripts/convert_video_features_to_detections.py",
            "--video_id",
            "demo",
            "--features_csv",
            str(features_csv),
            "--output_manifest",
            str(output_manifest),
            "--overwrite",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert overwrite_result.returncode == 0, overwrite_result.stderr
    assert len(read_csv(output_manifest)) == 2


def test_supervision_missing_dependency_reports_clear_error(tmp_path):
    if has_supervision():
        pytest.skip("supervision is installed in this environment")
    frames_dir, masks_dir, features_csv, output_root = prepare_inputs(tmp_path)
    detections_manifest = output_root / "supervision_detections_manifest.csv"
    convert_result = subprocess.run(
        [
            sys.executable,
            "scripts/convert_video_features_to_detections.py",
            "--video_id",
            "demo",
            "--features_csv",
            str(features_csv),
            "--output_manifest",
            str(detections_manifest),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert convert_result.returncode == 0, convert_result.stderr
    result = subprocess.run(
        [
            sys.executable,
            "scripts/annotate_video_frames_supervision.py",
            "--video_id",
            "demo",
            "--frames_dir",
            str(frames_dir),
            "--detections_manifest",
            str(detections_manifest),
            "--masks_dir",
            str(masks_dir),
            "--output_dir",
            str(output_root / "supervision_annotated_frames"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "Install it with: python -m pip install -r requirements-video.txt" in result.stderr


@pytest.mark.skipif(not has_supervision(), reason="supervision optional dependency is not installed")
def test_supervision_annotation_and_video_export(tmp_path):
    frames_dir, masks_dir, features_csv, output_root = prepare_inputs(tmp_path)
    detections_manifest = output_root / "supervision_detections_manifest.csv"
    annotated_dir = output_root / "supervision_annotated_frames"
    visualization_manifest = output_root / "supervision_visualization_manifest.csv"
    output_video = output_root / "supervision_annotated_video.mp4"

    convert_result = subprocess.run(
        [
            sys.executable,
            "scripts/convert_video_features_to_detections.py",
            "--video_id",
            "demo",
            "--features_csv",
            str(features_csv),
            "--output_manifest",
            str(detections_manifest),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert convert_result.returncode == 0, convert_result.stderr

    annotation_result = subprocess.run(
        [
            sys.executable,
            "scripts/annotate_video_frames_supervision.py",
            "--video_id",
            "demo",
            "--frames_dir",
            str(frames_dir),
            "--detections_manifest",
            str(detections_manifest),
            "--masks_dir",
            str(masks_dir),
            "--output_dir",
            str(annotated_dir),
            "--visualization_manifest",
            str(visualization_manifest),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert annotation_result.returncode == 0, annotation_result.stderr
    assert (annotated_dir / "frame_000001.jpg").is_file()
    assert (annotated_dir / "frame_000002.jpg").is_file()
    vis_rows = read_csv(visualization_manifest)
    assert len(vis_rows) == 2
    assert vis_rows[0]["visualization_source"] == "supervision_optional_layer"
    assert "not model inference" in vis_rows[0]["note"]

    export_result = subprocess.run(
        [
            sys.executable,
            "scripts/export_supervision_annotated_video.py",
            "--video_id",
            "demo",
            "--frames_dir",
            str(annotated_dir),
            "--output_video",
            str(output_video),
            "--fps",
            "5",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert export_result.returncode == 0, export_result.stderr
    assert output_video.is_file()
    assert output_video.stat().st_size > 0


@pytest.mark.skipif(not has_supervision(), reason="supervision optional dependency is not installed")
def test_supervision_annotation_and_export_overwrite(tmp_path):
    frames_dir, masks_dir, features_csv, output_root = prepare_inputs(tmp_path)
    detections_manifest = output_root / "supervision_detections_manifest.csv"
    annotated_dir = output_root / "supervision_annotated_frames"
    output_video = output_root / "supervision_annotated_video.mp4"
    visualization_manifest = output_root / "supervision_visualization_manifest.csv"

    convert_result = subprocess.run(
        [
            sys.executable,
            "scripts/convert_video_features_to_detections.py",
            "--video_id",
            "demo",
            "--features_csv",
            str(features_csv),
            "--output_manifest",
            str(detections_manifest),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert convert_result.returncode == 0, convert_result.stderr
    annotated_dir.mkdir(parents=True)
    (annotated_dir / "frame_999999.jpg").write_text("old", encoding="utf-8")

    blocked_result = subprocess.run(
        [
            sys.executable,
            "scripts/annotate_video_frames_supervision.py",
            "--video_id",
            "demo",
            "--frames_dir",
            str(frames_dir),
            "--detections_manifest",
            str(detections_manifest),
            "--masks_dir",
            str(masks_dir),
            "--output_dir",
            str(annotated_dir),
            "--visualization_manifest",
            str(visualization_manifest),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert blocked_result.returncode != 0
    assert "--overwrite" in blocked_result.stderr

    overwrite_result = subprocess.run(
        [
            sys.executable,
            "scripts/annotate_video_frames_supervision.py",
            "--video_id",
            "demo",
            "--frames_dir",
            str(frames_dir),
            "--detections_manifest",
            str(detections_manifest),
            "--masks_dir",
            str(masks_dir),
            "--output_dir",
            str(annotated_dir),
            "--visualization_manifest",
            str(visualization_manifest),
            "--overwrite",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert overwrite_result.returncode == 0, overwrite_result.stderr
    assert not (annotated_dir / "frame_999999.jpg").exists()

    first_export = subprocess.run(
        [
            sys.executable,
            "scripts/export_supervision_annotated_video.py",
            "--video_id",
            "demo",
            "--frames_dir",
            str(annotated_dir),
            "--output_video",
            str(output_video),
            "--fps",
            "5",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert first_export.returncode == 0, first_export.stderr
    second_export = subprocess.run(
        [
            sys.executable,
            "scripts/export_supervision_annotated_video.py",
            "--video_id",
            "demo",
            "--frames_dir",
            str(annotated_dir),
            "--output_video",
            str(output_video),
            "--fps",
            "5",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert second_export.returncode != 0
    assert "--overwrite" in second_export.stderr

    overwrite_export = subprocess.run(
        [
            sys.executable,
            "scripts/export_supervision_annotated_video.py",
            "--video_id",
            "demo",
            "--frames_dir",
            str(annotated_dir),
            "--output_video",
            str(output_video),
            "--fps",
            "5",
            "--overwrite",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert overwrite_export.returncode == 0, overwrite_export.stderr


@pytest.mark.skipif(not has_supervision(), reason="supervision optional dependency is not installed")
def test_supervision_annotation_requires_detection_manifest(tmp_path):
    frames_dir, masks_dir, _, output_root = prepare_inputs(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/annotate_video_frames_supervision.py",
            "--video_id",
            "demo",
            "--frames_dir",
            str(frames_dir),
            "--detections_manifest",
            str(output_root / "missing_manifest.csv"),
            "--masks_dir",
            str(masks_dir),
            "--output_dir",
            str(output_root / "supervision_annotated_frames"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "convert_video_features_to_detections.py" in result.stderr
