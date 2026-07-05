import csv
import subprocess
import sys
from pathlib import Path

import pytest


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_valid_artifacts(root: Path, video_id: str = "demo") -> None:
    (root / "data" / "videos").mkdir(parents=True)
    (root / "data" / "videos" / f"{video_id}.mp4").write_bytes(b"mp4")
    (root / "data" / "video_frames" / video_id).mkdir(parents=True)
    (root / "data" / "video_frames" / video_id / "frame_000001.jpg").write_bytes(b"jpg")
    write_csv(
        root / "data" / "video_frames" / video_id / "frames_manifest.csv",
        [{"video_id": video_id, "frame_id": "frame_000001", "frame_index": "0", "video_time_sec": "0", "image_path": "x.jpg"}],
    )
    write_csv(
        root / "data" / "video_inspection" / video_id / "metadata.csv",
        [{"inspection_id": video_id, "frame_id": "frame_000001", "timestamp": "2026-07-03T00:00:00", "video_time_sec": "0", "image_path": "x.jpg"}],
    )
    write_csv(
        root / "data" / "video_inspection" / video_id / "disease_features.csv",
        [{"inspection_id": video_id, "frame_id": "frame_000001", "image_path": "x.jpg", "mask_path": "m.png", "disease_area": "1"}],
    )
    write_csv(
        root / "data" / "video_inspection" / video_id / "inspection_sequence.csv",
        [{"inspection_id": video_id, "frame_id": "frame_000001", "image_id": "demo_frame_000001", "image_path": "x.jpg", "mask_path": "m.png"}],
    )
    write_csv(
        root / "outputs" / "video_inspection" / video_id / "video_visualization_manifest.csv",
        [{"video_id": video_id, "frame_id": "frame_000001", "annotated_frame_path": "a.jpg", "overlay_available": "true"}],
    )
    (root / "outputs" / "video_inspection" / video_id / "annotated_frames").mkdir(parents=True)
    (root / "outputs" / "video_inspection" / video_id / "annotated_frames" / "frame_000001.jpg").write_bytes(b"jpg")
    (root / "outputs" / "video_inspection" / video_id / "annotated_video.mp4").write_bytes(b"annotated")
    write_csv(
        root / "outputs" / "video_inspection" / video_id / "supervision_detections_manifest.csv",
        [{"video_id": video_id, "frame_id": "frame_000001", "bbox_x1": "1", "bbox_y1": "1", "bbox_x2": "2", "bbox_y2": "2"}],
    )
    write_csv(
        root / "outputs" / "video_inspection" / video_id / "supervision_visualization_manifest.csv",
        [{"video_id": video_id, "frame_id": "frame_000001", "output_annotated_frame_path": "s.jpg", "visualization_source": "supervision_optional_layer"}],
    )
    (root / "outputs" / "video_inspection" / video_id / "supervision_annotated_frames").mkdir(parents=True)
    (root / "outputs" / "video_inspection" / video_id / "supervision_annotated_frames" / "frame_000001.jpg").write_bytes(b"jpg")
    (root / "outputs" / "video_inspection" / video_id / "supervision_annotated_video.mp4").write_bytes(b"supervision")


def run_validator(root: Path, video_id: str = "demo") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/validate_video_artifacts.py",
            "--video_id",
            video_id,
            "--video_root",
            str(root / "data" / "videos"),
            "--frames_root",
            str(root / "data" / "video_frames"),
            "--inspection_root",
            str(root / "data" / "video_inspection"),
            "--output_root",
            str(root / "outputs" / "video_inspection"),
        ],
        capture_output=True,
        text=True,
    )


def run_validator_allowing_different_inspection_id(root: Path, video_id: str = "demo") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/validate_video_artifacts.py",
            "--video_id",
            video_id,
            "--video_root",
            str(root / "data" / "videos"),
            "--frames_root",
            str(root / "data" / "video_frames"),
            "--inspection_root",
            str(root / "data" / "video_inspection"),
            "--output_root",
            str(root / "outputs" / "video_inspection"),
            "--allow_different_inspection_id",
        ],
        capture_output=True,
        text=True,
    )


def test_validate_video_artifacts_passes_for_complete_outputs(tmp_path):
    write_valid_artifacts(tmp_path)

    result = run_validator(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "video artifact validation: passed" in result.stdout
    assert "supervision_annotated_video: ok" in result.stdout


def test_validate_video_artifacts_reports_missing_outputs(tmp_path):
    write_valid_artifacts(tmp_path)
    (tmp_path / "outputs" / "video_inspection" / "demo" / "annotated_video.mp4").unlink()

    result = run_validator(tmp_path)

    assert result.returncode != 0
    assert "video artifact validation: failed" in result.stdout
    assert "annotated_video: missing/error" in result.stdout


def test_validate_video_artifacts_reports_missing_source_frames(tmp_path):
    write_valid_artifacts(tmp_path)
    (tmp_path / "data" / "video_frames" / "demo" / "frame_000001.jpg").unlink()

    result = run_validator(tmp_path)

    assert result.returncode != 0
    assert "source_frames: missing/error" in result.stdout


def test_validate_video_artifacts_reports_missing_annotated_frames(tmp_path):
    write_valid_artifacts(tmp_path)
    (tmp_path / "outputs" / "video_inspection" / "demo" / "annotated_frames" / "frame_000001.jpg").unlink()

    result = run_validator(tmp_path)

    assert result.returncode != 0
    assert "annotated_frames: missing/error" in result.stdout


def test_validate_video_artifacts_reports_bad_csv_schema(tmp_path):
    write_valid_artifacts(tmp_path)
    write_csv(tmp_path / "data" / "video_inspection" / "demo" / "disease_features.csv", [{"frame_id": "frame_000001"}])

    result = run_validator(tmp_path)

    assert result.returncode != 0
    assert "disease_features missing required fields" in result.stdout


def test_validate_video_artifacts_reports_video_id_mismatch(tmp_path):
    write_valid_artifacts(tmp_path)
    write_csv(
        tmp_path / "data" / "video_frames" / "demo" / "frames_manifest.csv",
        [{"video_id": "other", "frame_id": "frame_000001", "frame_index": "0", "video_time_sec": "0", "image_path": "x.jpg"}],
    )

    result = run_validator(tmp_path)

    assert result.returncode != 0
    assert "frames_manifest row 2 video_id mismatch" in result.stdout
    assert "actual=other, expected=demo" in result.stdout


def test_validate_video_artifacts_reports_inspection_id_mismatch(tmp_path):
    write_valid_artifacts(tmp_path)
    write_csv(
        tmp_path / "data" / "video_inspection" / "demo" / "metadata.csv",
        [
            {
                "inspection_id": "other",
                "frame_id": "frame_000001",
                "timestamp": "2026-07-03T00:00:00",
                "video_time_sec": "0",
                "image_path": "x.jpg",
            }
        ],
    )

    result = run_validator(tmp_path)

    assert result.returncode != 0
    assert "metadata row 2 inspection_id mismatch" in result.stdout
    assert "actual=other, expected=demo" in result.stdout


def test_validate_video_artifacts_reports_inspection_sequence_id_mismatch(tmp_path):
    write_valid_artifacts(tmp_path)
    write_csv(
        tmp_path / "data" / "video_inspection" / "demo" / "inspection_sequence.csv",
        [{"inspection_id": "other", "frame_id": "frame_000001", "image_id": "demo_frame_000001", "image_path": "x.jpg", "mask_path": "m.png"}],
    )

    result = run_validator(tmp_path)

    assert result.returncode != 0
    assert "inspection_sequence row 2 inspection_id mismatch" in result.stdout
    assert "actual=other, expected=demo" in result.stdout


def test_validate_video_artifacts_can_allow_different_inspection_id_via_cli(tmp_path):
    write_valid_artifacts(tmp_path)
    write_csv(
        tmp_path / "data" / "video_inspection" / "demo" / "metadata.csv",
        [
            {
                "inspection_id": "inspection_20260705_A",
                "frame_id": "frame_000001",
                "timestamp": "2026-07-03T00:00:00",
                "video_time_sec": "0",
                "image_path": "x.jpg",
            }
        ],
    )
    write_csv(
        tmp_path / "data" / "video_inspection" / "demo" / "inspection_sequence.csv",
        [
            {
                "inspection_id": "inspection_20260705_A",
                "frame_id": "frame_000001",
                "image_id": "inspection_20260705_A_frame_000001",
                "image_path": "x.jpg",
                "mask_path": "m.png",
            }
        ],
    )

    result = run_validator_allowing_different_inspection_id(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "video artifact validation: passed" in result.stdout


def test_validate_video_artifacts_can_allow_different_inspection_id_via_function(tmp_path, monkeypatch):
    write_valid_artifacts(tmp_path)
    write_csv(
        tmp_path / "data" / "video_inspection" / "demo" / "metadata.csv",
        [
            {
                "inspection_id": "inspection_20260705_A",
                "frame_id": "frame_000001",
                "timestamp": "2026-07-03T00:00:00",
                "video_time_sec": "0",
                "image_path": "x.jpg",
            }
        ],
    )
    write_csv(
        tmp_path / "data" / "video_inspection" / "demo" / "inspection_sequence.csv",
        [
            {
                "inspection_id": "inspection_20260705_A",
                "frame_id": "frame_000001",
                "image_id": "inspection_20260705_A_frame_000001",
                "image_path": "x.jpg",
                "mask_path": "m.png",
            }
        ],
    )
    monkeypatch.syspath_prepend(str(Path.cwd() / "scripts"))
    from validate_video_artifacts import validate_video_artifacts

    result = validate_video_artifacts(
        "demo",
        video_root=tmp_path / "data" / "videos",
        frames_root=tmp_path / "data" / "video_frames",
        inspection_root=tmp_path / "data" / "video_inspection",
        output_root=tmp_path / "outputs" / "video_inspection",
        strict_inspection_id=False,
    )

    assert result["ok"] is True


def test_validate_video_artifacts_still_rejects_video_id_mismatch_when_inspection_id_is_allowed(tmp_path):
    write_valid_artifacts(tmp_path)
    write_csv(
        tmp_path / "outputs" / "video_inspection" / "demo" / "video_visualization_manifest.csv",
        [{"video_id": "other", "frame_id": "frame_000001", "annotated_frame_path": "a.jpg", "overlay_available": "true"}],
    )

    result = run_validator_allowing_different_inspection_id(tmp_path)

    assert result.returncode != 0
    assert "video_visualization_manifest row 2 video_id mismatch" in result.stdout
    assert "actual=other, expected=demo" in result.stdout


def test_validate_video_artifacts_allow_flag_still_requires_inspection_fields(tmp_path):
    write_valid_artifacts(tmp_path)
    write_csv(tmp_path / "data" / "video_inspection" / "demo" / "metadata.csv", [{"frame_id": "frame_000001"}])

    result = run_validator_allowing_different_inspection_id(tmp_path)

    assert result.returncode != 0
    assert "metadata missing required fields" in result.stdout
    assert "inspection_id" in result.stdout


@pytest.mark.parametrize("flag", ["--allow_different_inspection_id", "--allow-different-inspection-id"])
def test_validate_video_artifacts_accepts_allow_inspection_id_flag_aliases(tmp_path, flag):
    write_valid_artifacts(tmp_path)
    write_csv(
        tmp_path / "data" / "video_inspection" / "demo" / "metadata.csv",
        [
            {
                "inspection_id": "inspection_20260705_A",
                "frame_id": "frame_000001",
                "timestamp": "2026-07-03T00:00:00",
                "video_time_sec": "0",
                "image_path": "x.jpg",
            }
        ],
    )
    write_csv(
        tmp_path / "data" / "video_inspection" / "demo" / "inspection_sequence.csv",
        [
            {
                "inspection_id": "inspection_20260705_A",
                "frame_id": "frame_000001",
                "image_id": "inspection_20260705_A_frame_000001",
                "image_path": "x.jpg",
                "mask_path": "m.png",
            }
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/validate_video_artifacts.py",
            "--video_id",
            "demo",
            "--video_root",
            str(tmp_path / "data" / "videos"),
            "--frames_root",
            str(tmp_path / "data" / "video_frames"),
            "--inspection_root",
            str(tmp_path / "data" / "video_inspection"),
            "--output_root",
            str(tmp_path / "outputs" / "video_inspection"),
            flag,
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
