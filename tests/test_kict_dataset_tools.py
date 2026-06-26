import csv
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def write_image(path: Path, array: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(array).save(path)


def test_kict_inspect_and_feature_extraction(tmp_path):
    dataset = tmp_path / "kict"
    image = np.zeros((8, 10, 3), dtype=np.uint8)
    image[:, :] = (120, 120, 120)
    mask = np.zeros((8, 10), dtype=np.uint8)
    mask[2:5, 3:7] = 255
    empty_mask = np.zeros((8, 10), dtype=np.uint8)

    write_image(dataset / "images" / "0001.jpg", image)
    write_image(dataset / "images" / "missing_mask.jpg", image)
    write_image(dataset / "masks" / "0001.png", mask)
    write_image(dataset / "masks" / "empty.png", empty_mask)

    preview_dir = tmp_path / "preview"
    inspect_result = subprocess.run(
        [
            sys.executable,
            "scripts/inspect_kict_dataset.py",
            "--dataset-root",
            str(dataset),
            "--preview-dir",
            str(preview_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "image_count: 2" in inspect_result.stdout
    assert "mask_count: 2" in inspect_result.stdout
    assert "matched_count: 1" in inspect_result.stdout
    assert "missing_mask_image_count: 1" in inspect_result.stdout
    assert "missing_image_mask_count: 1" in inspect_result.stdout
    assert len(list(preview_dir.glob("*_preview.jpg"))) == 1

    output_csv = tmp_path / "features.csv"
    subprocess.run(
        [
            sys.executable,
            "scripts/extract_kict_mask_features.py",
            "--dataset-root",
            str(dataset),
            "--output-csv",
            str(output_csv),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    rows = list(csv.DictReader(output_csv.open(encoding="utf-8-sig", newline="")))
    rows_by_mask = {row["mask_file"]: row for row in rows}
    feature = rows_by_mask["masks/0001.png"]
    assert feature["image_file"] == "images/0001.jpg"
    assert int(feature["area_px"]) == 12
    assert (int(feature["bbox_x1"]), int(feature["bbox_y1"]), int(feature["bbox_x2"]), int(feature["bbox_y2"])) == (
        3,
        2,
        7,
        5,
    )
    assert (float(feature["center_x"]), float(feature["center_y"])) == (4.5, 3.0)
    assert (int(feature["mask_width"]), int(feature["mask_height"])) == (4, 3)

    empty = rows_by_mask["masks/empty.png"]
    assert empty["image_file"] == ""
    assert int(empty["area_px"]) == 0
    assert int(empty["bbox_x1"]) == -1
    assert int(empty["mask_width"]) == 0
