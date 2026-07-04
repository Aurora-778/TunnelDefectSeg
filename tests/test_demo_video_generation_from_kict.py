import csv
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image


pytest.importorskip("cv2")


def write_image(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = np.zeros((12, 16, 3), dtype=np.uint8)
    image[:, :] = color
    Image.fromarray(image).save(path)


def write_mask(path: Path, nonzero: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mask = np.zeros((12, 16), dtype=np.uint8)
    if nonzero:
        mask[2:6, 3:8] = 255
    Image.fromarray(mask).save(path)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_kict_sample(root: Path) -> tuple[Path, Path]:
    image_root = root / "images"
    mask_root = root / "masks"
    write_image(image_root / "sample_001.jpg", (40, 80, 120))
    write_mask(mask_root / "sample_001.png")
    write_image(image_root / "sample_002.jpg", (90, 120, 40))
    write_mask(mask_root / "sample_002.png")
    return image_root, mask_root


def test_create_demo_tunnel_video_from_kict_writes_video_masks_and_manifest(tmp_path):
    image_root, mask_root = write_kict_sample(tmp_path / "kict")
    output_video = tmp_path / "data" / "videos" / "tunnel_demo.mp4"
    output_masks = tmp_path / "data" / "video_masks" / "tunnel_demo"
    output_manifest = tmp_path / "data" / "video_demo" / "tunnel_demo_source_manifest.csv"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/create_demo_tunnel_video_from_kict.py",
            "--image_root",
            str(image_root),
            "--mask_root",
            str(mask_root),
            "--output_video",
            str(output_video),
            "--output_masks_dir",
            str(output_masks),
            "--output_manifest",
            str(output_manifest),
            "--num_frames",
            "3",
            "--fps",
            "5",
            "--width",
            "32",
            "--height",
            "24",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "KICT demo video generation completed" in result.stdout
    assert output_video.is_file()
    assert (output_masks / "frame_000001.png").is_file()
    assert (output_masks / "frame_000003.png").is_file()
    rows = read_csv(output_manifest)
    assert len(rows) == 3
    assert rows[0]["frame_id"] == "frame_000001"
    assert rows[0]["source_dataset"] == "KICT"
    assert rows[0]["demo_type"] == "synthesized_video_from_static_images"
    assert "not real robot inspection video" in rows[0]["note"]


def test_create_demo_tunnel_video_from_kict_rejects_missing_roots(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "scripts/create_demo_tunnel_video_from_kict.py",
            "--image_root",
            str(tmp_path / "missing_images"),
            "--mask_root",
            str(tmp_path / "missing_masks"),
            "--output_video",
            str(tmp_path / "video.mp4"),
            "--output_masks_dir",
            str(tmp_path / "masks"),
            "--output_manifest",
            str(tmp_path / "manifest.csv"),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "image_root not found" in result.stderr


def test_create_demo_tunnel_video_from_kict_rejects_unmatched_image_mask_stems(tmp_path):
    image_root = tmp_path / "kict" / "images"
    mask_root = tmp_path / "kict" / "masks"
    write_image(image_root / "sample_001.jpg", (40, 80, 120))
    write_mask(mask_root / "other_001.png")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/create_demo_tunnel_video_from_kict.py",
            "--image_root",
            str(image_root),
            "--mask_root",
            str(mask_root),
            "--output_video",
            str(tmp_path / "video.mp4"),
            "--output_masks_dir",
            str(tmp_path / "masks"),
            "--output_manifest",
            str(tmp_path / "manifest.csv"),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "image and mask stems do not match" in result.stderr
