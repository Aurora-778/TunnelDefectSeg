import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from run_confidence_risk import collect_images, write_result_artifacts


def test_write_result_artifacts_creates_expected_files(tmp_path):
    raw = np.zeros((12, 12, 3), dtype=np.uint8)
    raw[..., 0] = 80
    single = np.zeros((12, 12), dtype=np.uint8)
    fused = np.zeros((12, 12), dtype=np.uint8)
    fused[5, 2:10] = 5
    entropy = np.zeros((12, 12), dtype=np.float32)
    entropy[5, 2:10] = 0.2
    disagreement = np.zeros((12, 12), dtype=np.float32)

    report = write_result_artifacts(
        stem="sample",
        raw_resized=raw,
        single_mask=single,
        fused_mask=fused,
        entropy_uncertainty=entropy,
        disagreement_uncertainty=disagreement,
        output_dir=tmp_path,
        tta_specs=["identity", "hflip"],
    )

    expected = [
        "sample_single_mask.png",
        "sample_fused_mask.png",
        "sample_hybrid_mask.png",
        "sample_selected_mask.png",
        "sample_overlay.png",
        "sample_selected_overlay.png",
        "sample_uncertainty_heatmap.png",
        "sample_disagreement_heatmap.png",
        "sample_skeleton.png",
        "sample_report.json",
    ]
    for name in expected:
        assert (tmp_path / name).exists()

    saved = json.loads((tmp_path / "sample_report.json").read_text(encoding="utf-8"))
    assert saved["tta_specs"] == ["identity", "hflip"]
    assert saved["fused_prediction_stats"]["5"] == 8
    assert "selected_prediction_stats" in saved
    assert saved["artifacts"]["selected_mask"] == "sample_selected_mask.png"
    assert saved["artifacts"]["selected_overlay"] == "sample_selected_overlay.png"
    assert saved["adaptive_selection"]["mode"] in {"single", "fused", "hybrid"}
    assert saved["adaptive_selection"]["reasons"]
    assert saved["self_consistency"]["single_fused_mIoU"] == 0.0
    assert saved["self_consistency"]["foreground_iou"] == 0.0
    assert saved["self_consistency"]["pixel_agreement"] < 1.0
    assert saved["morphology"]["defect_area_pixels"] > 0
    assert saved["risk"]["risk_level"] in {"low", "medium", "high"}
    assert report["artifacts"]["overlay"] == "sample_overlay.png"


def test_collect_images_accepts_file_and_folder(tmp_path):
    img_path = tmp_path / "a.jpg"
    Image.fromarray(np.zeros((4, 4, 3), dtype=np.uint8)).save(img_path)
    nested = tmp_path / "nested"
    nested.mkdir()
    Image.fromarray(np.zeros((4, 4, 3), dtype=np.uint8)).save(nested / "b.png")

    assert collect_images(img_path) == [img_path]
    assert collect_images(tmp_path) == [img_path, nested / "b.png"]


def test_collect_images_rejects_missing_input(tmp_path):
    with pytest.raises(FileNotFoundError):
        collect_images(tmp_path / "missing.jpg")


def test_collect_images_rejects_folder_without_images(tmp_path):
    (tmp_path / "notes.txt").write_text("not an image", encoding="utf-8")

    with pytest.raises(ValueError, match="No supported images"):
        collect_images(tmp_path)
