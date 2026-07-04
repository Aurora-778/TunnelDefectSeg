import csv
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image


cv2 = pytest.importorskip("cv2")


def write_test_video(path: Path, frame_count: int = 4, fps: float = 10.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (16, 12))
    if not writer.isOpened():
        pytest.skip("OpenCV VideoWriter mp4v is unavailable in this environment")
    for index in range(frame_count):
        frame = np.zeros((12, 16, 3), dtype=np.uint8)
        frame[:, :] = (index * 20, 80, 120)
        writer.write(frame)
    writer.release()


def write_mask(path: Path, bbox: tuple[int, int, int, int] | None = (3, 2, 8, 6)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mask = np.zeros((12, 16), dtype=np.uint8)
    if bbox is not None:
        x1, y1, x2, y2 = bbox
        mask[y1:y2, x1:x2] = 255
    Image.fromarray(mask).save(path)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_video_masks(masks_dir: Path, frame_count: int = 4) -> None:
    boxes = [
        (1, 1, 4, 3),
        (3, 2, 8, 6),
        (2, 1, 10, 8),
        (5, 4, 12, 10),
    ]
    for index in range(frame_count):
        write_mask(masks_dir / f"frame_{index + 1:06d}.png", bbox=boxes[index % len(boxes)])


def run_pipeline(
    video_path: Path,
    masks_dir: Path | None,
    output_root: Path,
    frames_root: Path,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    args = [
        sys.executable,
        "scripts/run_video_inspection_pipeline.py",
        "--video_path",
        str(video_path),
        "--video_id",
        "tunnel_demo",
        "--sample_interval",
        "2",
        "--max_frames",
        "2",
        "--mode",
        "mask_input",
        "--output_root",
        str(output_root),
        "--frames_root",
        str(frames_root),
    ]
    if masks_dir is not None:
        args.extend(["--masks_dir", str(masks_dir)])
    if extra_args:
        args.extend(extra_args)
    return subprocess.run(args, capture_output=True, text=True)


def test_run_video_inspection_pipeline_generates_video_inspection_sequence(tmp_path):
    video_path = tmp_path / "data" / "videos" / "tunnel_demo.mp4"
    masks_dir = tmp_path / "data" / "video_masks" / "tunnel_demo"
    output_root = tmp_path / "data" / "video_inspection"
    frames_root = tmp_path / "data" / "video_frames"
    simulated_dir = tmp_path / "data" / "simulated"
    simulated_dir.mkdir(parents=True)
    sentinel = simulated_dir / "inspection_sequence.csv"
    sentinel.write_text("do not touch", encoding="utf-8")
    write_test_video(video_path)
    write_video_masks(masks_dir)

    result = run_pipeline(video_path, masks_dir, output_root, frames_root)

    assert result.returncode == 0, result.stderr
    assert "video inspection pipeline completed" in result.stdout
    inspection_dir = output_root / "tunnel_demo"
    frames_manifest = frames_root / "tunnel_demo" / "frames_manifest.csv"
    metadata_csv = inspection_dir / "metadata.csv"
    features_csv = inspection_dir / "disease_features.csv"
    sequence_csv = inspection_dir / "inspection_sequence.csv"
    assert frames_manifest.is_file()
    assert metadata_csv.is_file()
    assert features_csv.is_file()
    assert sequence_csv.is_file()
    assert sentinel.read_text(encoding="utf-8") == "do not touch"

    rows = read_csv(sequence_csv)
    assert len(rows) == 2
    required = {
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
    }
    assert required.issubset(rows[0])
    assert rows[0]["metadata_source"] == "estimated_from_video_time"
    assert rows[0]["metadata_limit_note"] == "mileage and ring_id are estimated, not real robot localization"
    assert rows[0]["feature_source"] == "mask_nonzero_pixels"
    assert "not model inference" in rows[0]["feature_limit_note"]
    assert (inspection_dir / "sampled_masks" / "frame_000002.png").is_file()
    assert rows[0]["disease_area"] == "6"
    assert rows[1]["disease_area"] == "56"
    assert rows[1]["bbox_x"] == "2"
    assert rows[1]["bbox_y"] == "1"


def test_run_video_inspection_pipeline_sanitizes_video_id_for_output_paths(tmp_path):
    video_path = tmp_path / "data" / "videos" / "tunnel_demo.mp4"
    masks_dir = tmp_path / "data" / "video_masks" / "tunnel_demo"
    output_root = tmp_path / "data" / "video_inspection"
    frames_root = tmp_path / "data" / "video_frames"
    write_test_video(video_path)
    write_video_masks(masks_dir)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_video_inspection_pipeline.py",
            "--video_path",
            str(video_path),
            "--video_id",
            "../../bad id",
            "--sample_interval",
            "2",
            "--max_frames",
            "2",
            "--mode",
            "mask_input",
            "--masks_dir",
            str(masks_dir),
            "--output_root",
            str(output_root),
            "--frames_root",
            str(frames_root),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert (frames_root / "bad_id" / "frames_manifest.csv").is_file()
    sequence_csv = output_root / "bad_id" / "inspection_sequence.csv"
    assert sequence_csv.is_file()
    assert not (tmp_path / "data" / "bad id").exists()
    assert not (tmp_path / "bad id").exists()
    rows = read_csv(sequence_csv)
    assert rows[0]["inspection_id"] == "bad_id"


def test_run_video_inspection_pipeline_rejects_missing_masks_dir(tmp_path):
    video_path = tmp_path / "data" / "videos" / "tunnel_demo.mp4"
    write_test_video(video_path)

    result = run_pipeline(
        video_path=video_path,
        masks_dir=None,
        output_root=tmp_path / "data" / "video_inspection",
        frames_root=tmp_path / "data" / "video_frames",
    )

    assert result.returncode != 0
    assert "masks_dir is required when mode=mask_input" in result.stderr


def test_run_video_inspection_pipeline_rejects_model_inference_mode(tmp_path):
    video_path = tmp_path / "data" / "videos" / "tunnel_demo.mp4"
    masks_dir = tmp_path / "data" / "video_masks" / "tunnel_demo"
    write_test_video(video_path)
    write_video_masks(masks_dir)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_video_inspection_pipeline.py",
            "--video_path",
            str(video_path),
            "--video_id",
            "tunnel_demo",
            "--mode",
            "model_inference",
            "--masks_dir",
            str(masks_dir),
            "--output_root",
            str(tmp_path / "data" / "video_inspection"),
            "--frames_root",
            str(tmp_path / "data" / "video_frames"),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "model_inference is reserved for future work" in result.stderr
