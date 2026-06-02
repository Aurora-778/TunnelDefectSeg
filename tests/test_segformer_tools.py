from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from segformer_tools import SegFormerExportConfig, prepare_segformer


def _make_sample(root: Path, folder: str, stem: str) -> None:
    image_dir = root / folder / "images"
    label_dir = root / folder / "labels"
    multiclass_dir = root / "multiclass_labels" / folder
    image_dir.mkdir(parents=True, exist_ok=True)
    label_dir.mkdir(parents=True, exist_ok=True)
    multiclass_dir.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.full((8, 10, 3), 128, dtype=np.uint8)).save(image_dir / f"{stem}.jpg")
    mask = np.zeros((8, 10), dtype=np.uint8)
    mask[2:5, 3:7] = 255
    Image.fromarray(mask, mode="L").save(label_dir / f"{stem}_mask.png")
    Image.fromarray(mask, mode="L").save(multiclass_dir / f"{stem}_multi.png")


def test_prepare_segformer_exports_mmseg_dataset_and_config(tmp_path):
    data_root = tmp_path / "data"
    _make_sample(data_root, "1", "a")
    _make_sample(data_root, "2", "b")
    _make_sample(data_root, "3", "c")
    out_dir = tmp_path / "out"

    result = prepare_segformer(
        SegFormerExportConfig(
            data_root=data_root,
            out_dir=out_dir,
            train_n=1,
            val_n=1,
            test_n=1,
            seed=1,
        ),
        segformer_repo_root=tmp_path / "SegFormer-master",
    )

    assert result["split_counts"] == {"train": 1, "val": 1, "test": 1}
    exported_masks = sorted((out_dir / "mmseg" / "masks").glob("*/*.png"))
    assert len(exported_masks) == 3
    exported = np.array(Image.open(exported_masks[0]).convert("L"))
    assert set(np.unique(exported)).issubset({0, 1, 2, 3})

    config_text = Path(result["config_path"]).read_text(encoding="utf-8")
    assert "SegFormer B1 for 6-class tunnel defect segmentation" in config_text
    assert "num_classes=6" in config_text
    assert "img_dir='images/train'" in config_text
    assert Path(result["train_launcher"]).exists()
    assert Path(result["test_launcher"]).exists()
