import csv
import importlib.util
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest


cv2 = pytest.importorskip("cv2")
SPEC = importlib.util.spec_from_file_location("extract_video_frames", Path("scripts/extract_video_frames.py"))
extract_video_frames_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(extract_video_frames_module)


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


def test_extract_video_frames_supports_hyphenated_cli_aliases(tmp_path):
    video_path = tmp_path / "video" / "tunnel_demo.mp4"
    output_dir = tmp_path / "frames"
    write_test_video(video_path)

    subprocess.run(
        [
            sys.executable,
            "scripts/extract_video_frames.py",
            "--video-path",
            str(video_path),
            "--output-dir",
            str(output_dir),
            "--sample-interval",
            "3",
            "--max-frames",
            "1",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    rows = read_manifest(output_dir / "frames_manifest.csv")
    assert len(rows) == 1
    assert (output_dir / "frame_000001.jpg").is_file()


def test_extract_video_frames_sanitizes_video_id_for_default_output_dir(tmp_path, monkeypatch):
    video_path = tmp_path / "video" / "tunnel_demo.mp4"
    write_test_video(video_path)
    monkeypatch.chdir(tmp_path)

    saved_paths, manifest_path = extract_video_frames_module.extract_video_frames(
        video_path=video_path,
        video_id="../../bad id",
        sample_interval=2,
        max_frames=1,
    )

    expected_dir = tmp_path / "data" / "video_frames" / "bad_id"
    assert manifest_path.resolve() == expected_dir / "frames_manifest.csv"
    assert saved_paths[0].resolve().is_relative_to(expected_dir)
    assert not (tmp_path / "bad id").exists()
    rows = read_manifest(manifest_path)
    assert rows[0]["video_id"] == "bad_id"


def test_extract_video_frames_rejects_empty_safe_video_id(tmp_path):
    video_path = tmp_path / "video" / "tunnel_demo.mp4"
    write_test_video(video_path)

    with pytest.raises(ValueError, match="video_id must contain"):
        extract_video_frames_module.extract_video_frames(video_path=video_path, video_id="///")


def test_extract_video_frames_rejects_existing_manifest(tmp_path):
    video_path = tmp_path / "video" / "tunnel_demo.mp4"
    output_dir = tmp_path / "frames"
    write_test_video(video_path)
    output_dir.mkdir()
    (output_dir / "frames_manifest.csv").write_text("old", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/extract_video_frames.py",
            "--video_path",
            str(video_path),
            "--output_dir",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "frames_manifest.csv already exists" in result.stderr


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


@pytest.mark.parametrize(
    ("flag", "value", "message"),
    [
        ("--sample_interval", "0", "sample_interval must be a positive integer"),
        ("--max_frames", "0", "max_frames must be a positive integer"),
        ("--jpg_quality", "0", "jpg_quality must be between 1 and 100"),
        ("--jpg_quality", "101", "jpg_quality must be between 1 and 100"),
    ],
)
def test_extract_video_frames_rejects_invalid_numeric_args(tmp_path, flag, value, message):
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
            flag,
            value,
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert message in result.stderr


def test_extract_video_frames_rejects_missing_fps(tmp_path, monkeypatch):
    video_path = tmp_path / "video" / "tunnel_demo.mp4"
    write_test_video(video_path)

    class FakeCapture:
        def __init__(self, path):
            self.path = path

        def isOpened(self):
            return True

        def get(self, prop):
            return 0

        def release(self):
            pass

    class FakeCv2:
        CAP_PROP_FPS = 5

        @staticmethod
        def VideoCapture(path):
            return FakeCapture(path)

    monkeypatch.setattr(extract_video_frames_module, "load_cv2", lambda: FakeCv2)

    with pytest.raises(ValueError, match="unable to determine video fps"):
        extract_video_frames_module.extract_video_frames(video_path=video_path, output_dir=tmp_path / "frames")
