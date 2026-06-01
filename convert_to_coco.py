"""
Convert binary crack masks to COCO instance annotations.

This utility now uses the same standardized split as the baseline training
code:
  - train: 700
  - val  : 150
  - test : 150

It writes:
  annotations/train.json
  annotations/val.json
  annotations/test.json
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import cv2
import numpy as np


DATA_ROOT = Path(r"C:\Users\26822\Downloads\data")
SEED = 42
TRAIN_N = 700
VAL_N = 150
TEST_N = 150
MIN_AREA = 100
CATEGORIES = [{"id": 1, "name": "crack", "supercategory": "defect"}]


def mask_to_polygons(mask: np.ndarray):
    binary = (mask > 127).astype(np.uint8) * 255
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    polygons = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < MIN_AREA:
            continue
        approx = cv2.approxPolyDP(cnt, 1.0, True)
        if len(approx) < 3:
            continue
        polygons.append(approx.flatten().tolist())
    return polygons


def collect_samples():
    samples = []
    for sub in sorted([p for p in DATA_ROOT.iterdir() if p.is_dir() and p.name.isdigit()]):
        img_dir = sub / "images"
        label_dir = sub / "labels"
        if not img_dir.exists() or not label_dir.exists():
            continue
        for jpg_path in sorted(list(img_dir.glob("*.jpg")) + list(img_dir.glob("*.jpeg")) + list(img_dir.glob("*.png"))):
            mask_path = label_dir / f"{jpg_path.stem}_mask.png"
            if mask_path.exists():
                samples.append((jpg_path, mask_path))
    return samples


def split_samples(samples):
    rng = random.Random(SEED)
    samples = list(samples)
    rng.shuffle(samples)
    train = samples[:TRAIN_N]
    val = samples[TRAIN_N:TRAIN_N + VAL_N]
    test = samples[TRAIN_N + VAL_N:TRAIN_N + VAL_N + TEST_N]
    return train, val, test


def build_coco(samples, start_img_id=1, start_ann_id=1):
    images = []
    annotations = []
    img_id = start_img_id
    ann_id = start_ann_id

    for jpg_path, mask_path in samples:
        img = cv2.imread(str(jpg_path))
        if img is None:
            continue
        h, w = img.shape[:2]
        images.append({
            "id": img_id,
            "file_name": str(jpg_path.relative_to(DATA_ROOT)).replace("\\", "/"),
            "width": w,
            "height": h,
        })

        mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        if mask is None:
            img_id += 1
            continue

        polygons = mask_to_polygons(mask)
        for poly in polygons:
            xs = poly[0::2]
            ys = poly[1::2]
            x_min = float(min(xs))
            y_min = float(min(ys))
            x_max = float(max(xs))
            y_max = float(max(ys))
            bbox_w = x_max - x_min
            bbox_h = y_max - y_min
            annotations.append({
                "id": ann_id,
                "image_id": img_id,
                "category_id": 1,
                "segmentation": [poly],
                "area": float(bbox_w * bbox_h),
                "bbox": [x_min, y_min, bbox_w, bbox_h],
                "iscrowd": 0,
            })
            ann_id += 1

        img_id += 1

    return {
        "info": {"description": "Crack Instance Segmentation Dataset", "version": "1.0"},
        "licenses": [],
        "categories": CATEGORIES,
        "images": images,
        "annotations": annotations,
    }


def write_json(obj, path: Path):
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


def main():
    ann_dir = DATA_ROOT / "annotations"
    ann_dir.mkdir(exist_ok=True)

    samples = collect_samples()
    train_samples, val_samples, test_samples = split_samples(samples)

    print("=" * 60)
    print(f"Total samples: {len(samples)}")
    print(f"Train/Val/Test: {len(train_samples)}/{len(val_samples)}/{len(test_samples)}")

    train_coco = build_coco(train_samples, 1, 1)
    val_coco = build_coco(val_samples, len(train_coco["images"]) + 1, len(train_coco["annotations"]) + 1)
    test_coco = build_coco(
        test_samples,
        len(train_coco["images"]) + len(val_coco["images"]) + 1,
        len(train_coco["annotations"]) + len(val_coco["annotations"]) + 1,
    )

    write_json(train_coco, ann_dir / "train.json")
    write_json(val_coco, ann_dir / "val.json")
    write_json(test_coco, ann_dir / "test.json")

    print(f"Saved: {ann_dir / 'train.json'}")
    print(f"Saved: {ann_dir / 'val.json'}")
    print(f"Saved: {ann_dir / 'test.json'}")


if __name__ == "__main__":
    main()
