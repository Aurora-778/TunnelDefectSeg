import csv
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest


cv2 = pytest.importorskip("cv2")


def write_test_video(path: Path, frame_count: int = 6, fps: float = 10.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (16, 12))
    if not writer.isOpened():
        pytest.skip("OpenCV VideoWriter mp4v is unavailable in this environment")
    for index in range(frame_count):
        frame = np.zeros((12, 16, 3), dtype=np.uint8)
        frame[:, :] = (index * 20, 80, 120)
        writer.write(frame)
    writer.release()


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_extract_video_frames_writes_images_and_manifest(tmp_path):
    video_path = tmp_path / "video" / "tunnel_demo.mp4"
    output_dir = tmp_path / "frames"
    write_test_video(video_path)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/extract_video_frames.py",
            "--video_path",
            str(video_path),
            "--output_dir",
            str(output_dir),
            "--sample_interval",
            "2",
            "--max_frames",
            "2",
            "--video_id",
            "tunnel_demo",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "video frame extraction completed" in result.stdout
    assert "saved_frame_count: 2" in result.stdout
    assert (output_dir / "frame_000001.jpg").is_file()
    assert (output_dir / "frame_000002.jpg").is_file()

    rows = read_manifest(output_dir / "frames_manifest.csv")
    assert len(rows) == 2
    assert rows[0]["video_id"] == "tunnel_demo"
    assert rows[0]["frame_id"] == "frame_000001"
    assert rows[0]["frame_index"] == "0"
    assert rows[0]["sample_interval"] == "2"
    assert rows[1]["frame_index"] == "2"
    assert float(rows[1]["video_time_sec"]) == pytest.approx(0.2, abs=0.02)


def test_extract_video_frames_rejects_missing_video(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "scripts/extract_video_frames.py",
            "--video_path",
            str(tmp_path / "missing.mp4"),
            "--output_dir",
            str(tmp_path / "frames"),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "video file not found" in result.stderr


def test_extract_video_frames_rejects_invalid_sample_interval(tmp_path):
    video_path = tmp_path / "video" / "tunnel_demo.mp4"
    write_test_video(video_path)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/extract_video_frames.py",
            "--video_path",
            str(video_path),
            "--output_dir",
            str(tmp_path / "frames"),
            "--sample_interval",
            "0",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "sample_interval must be a positive integer" in result.stderr
