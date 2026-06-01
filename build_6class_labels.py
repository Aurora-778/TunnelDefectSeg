from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
from PIL import Image


DATA_ROOT = Path(r"C:\Users\26822\Downloads\data")
SOURCE_CLASS_FOLDERS = {
    "1": (1, "simple"),
    "2": (2, "blocky"),
    "3": (3, "pipeline"),
    "4": (4, "vertical"),
    "5": (5, "horizontal"),
}


def discover_pairs():
    pairs = []
    for folder_name, (class_id, class_name) in SOURCE_CLASS_FOLDERS.items():
        img_dir = DATA_ROOT / folder_name / "images"
        mask_dir = DATA_ROOT / folder_name / "labels"
        if not img_dir.exists() or not mask_dir.exists():
            continue

        for img_path in sorted(
            list(img_dir.glob("*.jpg"))
            + list(img_dir.glob("*.jpeg"))
            + list(img_dir.glob("*.png"))
        ):
            mask_path = mask_dir / f"{img_path.stem}_mask.png"
            if mask_path.exists():
                pairs.append((folder_name, class_id, class_name, img_path, mask_path))
    return pairs


def load_mask(path: Path) -> np.ndarray:
    return np.array(Image.open(path))


def to_multiclass(mask: np.ndarray, class_id: int) -> np.ndarray:
    """
    Convert a source mask to 6-class labels.

    Supported source formats:
    - binary mask: background=0, defect>127 -> assigned to folder class_id
    - existing multiclass mask: the defect region is still forced to folder class_id
    """
    if mask.ndim == 3:
        mask = mask[..., 0]

    out = np.zeros(mask.shape, dtype=np.uint8)
    out[mask > 0] = np.uint8(class_id)
    return out


def main():
    out_root = DATA_ROOT / "multiclass_labels"
    manifest_path = DATA_ROOT / "class_manifest.csv"
    summary_path = DATA_ROOT / "class_summary.json"

    pairs = discover_pairs()
    if not pairs:
        print("[ERROR] No image/mask pairs found.")
        return

    out_root.mkdir(parents=True, exist_ok=True)

    class_pixel_counts = {i: 0 for i in range(6)}
    class_image_counts = {i: 0 for i in range(6)}
    rows = []

    for folder_name, class_id, class_name, img_path, mask_path in pairs:
        src_mask = load_mask(mask_path)
        dst_mask = to_multiclass(src_mask, class_id)

        dst_dir = out_root / folder_name
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst_mask_path = dst_dir / f"{img_path.stem}_multi.png"
        Image.fromarray(dst_mask, mode="L").save(dst_mask_path)

        uniq, counts = np.unique(dst_mask, return_counts=True)
        class_image_counts[class_id] += 1
        for u, c in zip(uniq.tolist(), counts.tolist()):
            class_pixel_counts[int(u)] += int(c)

        rows.append(
            {
                "folder": folder_name,
                "class_id": class_id,
                "class_name": class_name,
                "image_path": str(img_path),
                "source_mask_path": str(mask_path),
                "output_mask_path": str(dst_mask_path),
            }
        )

    with manifest_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "folder",
                "class_id",
                "class_name",
                "image_path",
                "source_mask_path",
                "output_mask_path",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "num_classes": 6,
        "class_names": {
            0: "background",
            1: "simple",
            2: "blocky",
            3: "pipeline",
            4: "vertical",
            5: "horizontal",
        },
        "image_counts": class_image_counts,
        "pixel_counts": class_pixel_counts,
        "total_pairs": len(pairs),
        "source_root": str(DATA_ROOT),
        "output_root": str(out_root),
    }

    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print("=" * 60)
    print("6-class label conversion complete")
    print(f"Pairs: {len(pairs)}")
    print(f"Manifest: {manifest_path}")
    print(f"Summary : {summary_path}")
    print(f"Output  : {out_root}")
    print("=" * 60)
    for cls_id in range(6):
        print(
            f"[{cls_id}] {summary['class_names'][cls_id]:<12s} "
            f"images={class_image_counts[cls_id]:>4d} "
            f"pixels={class_pixel_counts[cls_id]}"
        )


if __name__ == "__main__":
    main()
