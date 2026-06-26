from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np
from PIL import Image


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}
MASK_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}
OUTPUT_COLUMNS = [
    "image_file",
    "mask_file",
    "area_px",
    "bbox_x1",
    "bbox_y1",
    "bbox_x2",
    "bbox_y2",
    "center_x",
    "center_y",
    "mask_width",
    "mask_height",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract geometry features from KICT crack masks.")
    parser.add_argument("--dataset-root", type=Path, required=True, help="KICT dataset root containing images/ and masks/.")
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=Path("data/simulated/kict_mask_features.csv"),
        help="CSV path for extracted mask geometry features.",
    )
    return parser.parse_args()


def find_dataset_dirs(dataset_root: Path) -> tuple[Path, Path]:
    image_dir = dataset_root / "images"
    mask_dir = dataset_root / "masks"
    if not image_dir.is_dir():
        raise FileNotFoundError(f"images folder not found: {image_dir}")
    if not mask_dir.is_dir():
        raise FileNotFoundError(f"masks folder not found: {mask_dir}")
    return image_dir, mask_dir


def collect_files(folder: Path, suffixes: set[str]) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for path in sorted(folder.iterdir()):
        if path.is_file() and path.suffix.lower() in suffixes:
            files.setdefault(path.stem, path)
    return files


def relative_text(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def extract_mask_features(mask_path: Path) -> dict[str, int | float]:
    mask = np.array(Image.open(mask_path).convert("L")) > 0
    area = int(mask.sum())
    if area == 0:
        return {
            "area_px": 0,
            "bbox_x1": -1,
            "bbox_y1": -1,
            "bbox_x2": -1,
            "bbox_y2": -1,
            "center_x": -1,
            "center_y": -1,
            "mask_width": 0,
            "mask_height": 0,
        }

    ys, xs = np.nonzero(mask)
    x1 = int(xs.min())
    y1 = int(ys.min())
    x2 = int(xs.max()) + 1
    y2 = int(ys.max()) + 1
    return {
        "area_px": area,
        "bbox_x1": x1,
        "bbox_y1": y1,
        "bbox_x2": x2,
        "bbox_y2": y2,
        "center_x": round((x1 + x2 - 1) / 2, 2),
        "center_y": round((y1 + y2 - 1) / 2, 2),
        "mask_width": x2 - x1,
        "mask_height": y2 - y1,
    }


def build_rows(dataset_root: Path) -> list[dict]:
    image_dir, mask_dir = find_dataset_dirs(dataset_root)
    image_files = collect_files(image_dir, IMAGE_SUFFIXES)
    mask_files = collect_files(mask_dir, MASK_SUFFIXES)
    rows = []

    for stem, mask_path in sorted(mask_files.items()):
        image_path = image_files.get(stem)
        features = extract_mask_features(mask_path)
        rows.append(
            {
                "image_file": relative_text(image_path, dataset_root) if image_path else "",
                "mask_file": relative_text(mask_path, dataset_root),
                **features,
            }
        )
    return rows


def write_csv(rows: list[dict], output_csv: Path) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    rows = build_rows(args.dataset_root)
    write_csv(rows, args.output_csv)
    empty_count = sum(1 for row in rows if row["area_px"] == 0)
    print(f"mask_count: {len(rows)}")
    print(f"empty_mask_count: {empty_count}")
    print(f"output_csv: {args.output_csv}")


if __name__ == "__main__":
    main()
