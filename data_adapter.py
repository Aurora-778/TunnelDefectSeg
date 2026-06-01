from __future__ import annotations

import random
from pathlib import Path

import numpy as np
from PIL import Image


SOURCE_CLASS_FOLDERS = {
    "1": (1, "simple"),
    "2": (2, "blocky"),
    "3": (3, "pipeline"),
    "4": (4, "vertical"),
    "5": (5, "horizontal"),
}


def resolve_mask_path(data_root: str | Path, subfolder: str, stem: str, label_mode: str = "multiclass") -> str | None:
    """Resolve the preferred mask path for a sample."""
    data_root = Path(data_root)
    multiclass_dir = data_root / "multiclass_labels" / subfolder
    binary_dir = data_root / subfolder / "labels"

    candidates = [
        multiclass_dir / f"{stem}_multi.png",
        multiclass_dir / f"{stem}_mask.png",
        multiclass_dir / f"{stem}.png",
    ]
    if label_mode != "multiclass":
        candidates.extend([
            binary_dir / f"{stem}_mask.png",
            binary_dir / f"{stem}.png",
        ])

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def discover_samples(data_root: str | Path, label_mode: str = "multiclass") -> list[tuple[str, str]]:
    """Discover image/mask pairs under numeric subfolders."""
    data_root = Path(data_root)
    all_pairs: list[tuple[str, str]] = []

    for subfolder in sorted(p.name for p in data_root.iterdir() if p.is_dir() and p.name.isdigit()):
        img_dir = data_root / subfolder / "images"
        if not img_dir.is_dir():
            continue

        for fname in sorted(list(img_dir.glob("*.jpg")) + list(img_dir.glob("*.jpeg")) + list(img_dir.glob("*.png"))):
            mask_path = resolve_mask_path(data_root, subfolder, fname.stem, label_mode=label_mode)
            if mask_path:
                all_pairs.append((str(fname), mask_path))

    return all_pairs


def split_dataset(pairs, seed: int, train_n: int, val_n: int, test_n: int):
    """Deterministically split pairs into train/val/test."""
    pairs = list(pairs)
    rng = random.Random(seed)
    rng.shuffle(pairs)
    train = pairs[:train_n]
    val = pairs[train_n:train_n + val_n]
    test = pairs[train_n + val_n:train_n + val_n + test_n]
    return train, val, test


def load_label_mask(mask_path: str, folder_name: str, num_classes: int = 6, label_mode: str = "multiclass") -> np.ndarray:
    """Load a mask and return class ids in [0, num_classes - 1]."""
    mask = np.array(Image.open(mask_path).convert("L"))
    unique_values = np.unique(mask)

    if label_mode == "multiclass":
        class_id = int(folder_name)
        if class_id not in (1, 2, 3, 4, 5):
            raise ValueError(f"[标签] folder={folder_name} 不在 1~5 的病害类范围内")
        # 统一采用文件夹级类别映射，避免历史遗留的多类 mask 混入训练集。
        # 当前数据是二值掩码时，非零区域全部映射到对应 folder 的类 id。
        out = np.zeros_like(mask, dtype=np.int64)
        out[mask > 0] = class_id
        return out

    if unique_values.size > 0 and int(unique_values.max()) <= num_classes - 1:
        return mask.astype(np.int64)

    out = np.zeros_like(mask, dtype=np.int64)
    out[mask > 0] = 1
    return out
