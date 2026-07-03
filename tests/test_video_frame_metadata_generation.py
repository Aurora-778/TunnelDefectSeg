import csv
import subprocess
import sys
from pathlib import Path


def write_manifest(path: Path, rows: list[dict[str, str]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = fields or ["video_id", "frame_id", "frame_index", "video_time_sec", "image_path", "fps", "sample_interval"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_generate_video_frame_metadata_writes_estimated_metadata(tmp_path):
    manifest = tmp_path / "frames_manifest.csv"
    output_csv = tmp_path / "metadata.csv"
    write_manifest(
        manifest,
        [
            {
                "video_id": "tunnel_demo",
                "frame_id": "frame_000001",
                "frame_index": "0",
                "video_time_sec": "0.000000",
                "image_path": "data/video_frames/tunnel_demo/frame_000001.jpg",
                "fps": "10.0",
                "sample_interval": "5",
            },
            {
                "video_id": "tunnel_demo",
                "frame_id": "frame_000002",
                "frame_index": "5",
                "video_time_sec": "2.500000",
                "image_path": "data/video_frames/tunnel_demo/frame_000002.jpg",
                "fps": "10.0",
                "sample_interval": "5",
            },
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/generate_video_frame_metadata.py",
            "--frames_manifest",
            str(manifest),
            "--output_csv",
            str(output_csv),
            "--start_timestamp",
            "2026-07-03T10:00:00",
            "--start_mileage_m",
            "12000",
            "--robot_speed_mps",
            "2",
            "--ring_length_m",
            "1.2",
            "--camera_id",
            "front_camera",
            "--position_angle",
            "12点",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "video frame metadata generation completed" in result.stdout
    rows = read_csv(output_csv)
    assert len(rows) == 2
    assert rows[0]["inspection_id"] == "tunnel_demo"
    assert rows[0]["timestamp"] == "2026-07-03T10:00:00"
    assert rows[0]["mileage_m"] == "12000.000"
    assert rows[0]["mileage_text"] == "K12+000.0"
    assert rows[0]["ring_id"] == "10000"
    assert rows[0]["position_angle"] == "12点"
    assert rows[0]["camera_id"] == "front_camera"
    assert rows[0]["metadata_source"] == "estimated_from_video_time"
    assert rows[0]["metadata_limit_note"] == "mileage and ring_id are estimated, not real robot localization"
    assert rows[1]["timestamp"] == "2026-07-03T10:00:02"
    assert rows[1]["video_time_sec"] == "2.500000"
    assert rows[1]["mileage_m"] == "12005.000"
    assert rows[1]["mileage_text"] == "K12+005.0"
    assert rows[1]["ring_id"] == "10004"
    assert "estimated_mileage_m=12005.000" in rows[1]["robot_pose"]


def test_generate_video_frame_metadata_allows_explicit_inspection_id(tmp_path):
    manifest = tmp_path / "frames_manifest.csv"
    output_csv = tmp_path / "metadata.csv"
    write_manifest(
        manifest,
        [
            {
                "video_id": "video_a",
                "frame_id": "frame_000001",
                "frame_index": "0",
                "video_time_sec": "1",
                "image_path": "frame_000001.jpg",
                "fps": "10",
                "sample_interval": "1",
            }
        ],
    )

    subprocess.run(
        [
            sys.executable,
            "scripts/generate_video_frame_metadata.py",
            "--frames-manifest",
            str(manifest),
            "--output-csv",
            str(output_csv),
            "--inspection-id",
            "I_VIDEO_001",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    rows = read_csv(output_csv)
    assert rows[0]["inspection_id"] == "I_VIDEO_001"


def test_generate_video_frame_metadata_rejects_missing_required_field(tmp_path):
    manifest = tmp_path / "frames_manifest.csv"
    output_csv = tmp_path / "metadata.csv"
    write_manifest(
        manifest,
        [{"video_id": "tunnel_demo", "frame_id": "frame_000001", "image_path": "frame.jpg"}],
        fields=["video_id", "frame_id", "image_path"],
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/generate_video_frame_metadata.py",
            "--frames_manifest",
            str(manifest),
            "--output_csv",
            str(output_csv),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "frames_manifest missing required fields: video_time_sec" in result.stderr


def test_generate_video_frame_metadata_rejects_empty_manifest(tmp_path):
    manifest = tmp_path / "frames_manifest.csv"
    output_csv = tmp_path / "metadata.csv"
    write_manifest(manifest, [])

    result = subprocess.run(
        [
            sys.executable,
            "scripts/generate_video_frame_metadata.py",
            "--frames_manifest",
            str(manifest),
            "--output_csv",
            str(output_csv),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "frames_manifest contains no frame rows" in result.stderr


def test_generate_video_frame_metadata_rejects_invalid_ring_length(tmp_path):
    manifest = tmp_path / "frames_manifest.csv"
    output_csv = tmp_path / "metadata.csv"
    write_manifest(
        manifest,
        [
            {
                "video_id": "tunnel_demo",
                "frame_id": "frame_000001",
                "frame_index": "0",
                "video_time_sec": "0",
                "image_path": "frame.jpg",
                "fps": "10",
                "sample_interval": "1",
            }
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/generate_video_frame_metadata.py",
            "--frames_manifest",
            str(manifest),
            "--output_csv",
            str(output_csv),
            "--ring_length_m",
            "0",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "ring_length_m must be greater than 0" in result.stderr


def test_generate_video_frame_metadata_rejects_negative_start_mileage(tmp_path):
    manifest = tmp_path / "frames_manifest.csv"
    output_csv = tmp_path / "metadata.csv"
    write_manifest(
        manifest,
        [
            {
                "video_id": "tunnel_demo",
                "frame_id": "frame_000001",
                "frame_index": "0",
                "video_time_sec": "0",
                "image_path": "frame.jpg",
                "fps": "10",
                "sample_interval": "1",
            }
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/generate_video_frame_metadata.py",
            "--frames_manifest",
            str(manifest),
            "--output_csv",
            str(output_csv),
            "--start_mileage_m",
            "-1",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "start_mileage_m must be non-negative" in result.stderr


def test_generate_video_frame_metadata_rejects_negative_robot_speed(tmp_path):
    manifest = tmp_path / "frames_manifest.csv"
    output_csv = tmp_path / "metadata.csv"
    write_manifest(
        manifest,
        [
            {
                "video_id": "tunnel_demo",
                "frame_id": "frame_000001",
                "frame_index": "0",
                "video_time_sec": "0",
                "image_path": "frame.jpg",
                "fps": "10",
                "sample_interval": "1",
            }
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/generate_video_frame_metadata.py",
            "--frames_manifest",
            str(manifest),
            "--output_csv",
            str(output_csv),
            "--robot_speed_mps",
            "-1",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "robot_speed_mps must be non-negative" in result.stderr


def test_generate_video_frame_metadata_rejects_invalid_start_timestamp(tmp_path):
    manifest = tmp_path / "frames_manifest.csv"
    output_csv = tmp_path / "metadata.csv"
    write_manifest(
        manifest,
        [
            {
                "video_id": "tunnel_demo",
                "frame_id": "frame_000001",
                "frame_index": "0",
                "video_time_sec": "0",
                "image_path": "frame.jpg",
                "fps": "10",
                "sample_interval": "1",
            }
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/generate_video_frame_metadata.py",
            "--frames_manifest",
            str(manifest),
            "--output_csv",
            str(output_csv),
            "--start_timestamp",
            "invalid-time",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "start_timestamp must be ISO-like datetime" in result.stderr


def test_generate_video_frame_metadata_rejects_invalid_video_time(tmp_path):
    manifest = tmp_path / "frames_manifest.csv"
    output_csv = tmp_path / "metadata.csv"
    write_manifest(
        manifest,
        [
            {
                "video_id": "tunnel_demo",
                "frame_id": "frame_000001",
                "frame_index": "0",
                "video_time_sec": "not-a-number",
                "image_path": "frame.jpg",
                "fps": "10",
                "sample_interval": "1",
            }
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/generate_video_frame_metadata.py",
            "--frames_manifest",
            str(manifest),
            "--output_csv",
            str(output_csv),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "row 1 has invalid video_time_sec" in result.stderr


def test_generate_video_frame_metadata_rejects_negative_video_time(tmp_path):
    manifest = tmp_path / "frames_manifest.csv"
    output_csv = tmp_path / "metadata.csv"
    write_manifest(
        manifest,
        [
            {
                "video_id": "tunnel_demo",
                "frame_id": "frame_000001",
                "frame_index": "0",
                "video_time_sec": "-1",
                "image_path": "frame.jpg",
                "fps": "10",
                "sample_interval": "1",
            }
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/generate_video_frame_metadata.py",
            "--frames_manifest",
            str(manifest),
            "--output_csv",
            str(output_csv),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "row 1 video_time_sec must be non-negative" in result.stderr
