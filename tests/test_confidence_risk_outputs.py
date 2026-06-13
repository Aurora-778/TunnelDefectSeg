import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from run_confidence_risk import collect_images, load_model, process_image, write_result_artifacts


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
        mask_source={"name": "unit_test_source", "type": "synthetic", "probability_tta": False},
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
    assert saved["mask_source"]["name"] == "unit_test_source"
    assert saved["mask_source"]["probability_tta"] is False
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
    assert saved["review_priority"]["priority"] in {"low", "medium", "high"}
    assert saved["review_priority"]["review_required"] is True
    assert any("foreground IoU" in reason or "shrinkage" in reason for reason in saved["review_priority"]["reasons"])
    assert report["artifacts"]["overlay"] == "sample_overlay.png"


def test_write_result_artifacts_keeps_unavailable_review_priority_reason(tmp_path):
    raw = np.zeros((8, 8, 3), dtype=np.uint8)
    single = np.zeros((8, 8), dtype=np.uint8)
    fused = np.zeros((8, 8), dtype=np.uint8)
    single[2, 2:5] = 1
    fused[2:5, 2] = 1
    entropy = np.zeros((8, 8), dtype=np.float32)
    disagreement = np.zeros((8, 8), dtype=np.float32)

    report = write_result_artifacts(
        stem="unavailable",
        raw_resized=raw,
        single_mask=single,
        fused_mask=fused,
        entropy_uncertainty=entropy,
        disagreement_uncertainty=disagreement,
        output_dir=tmp_path,
        tta_specs=["identity"],
        mask_source={
            "name": "argmax_only_source",
            "type": "synthetic",
            "probability_tta": False,
            "uncertainty_available": False,
            "disagreement_available": False,
        },
    )

    assert report["uncertainty_summary"]["available"] is False
    assert report["disagreement_summary"]["available"] is False
    assert any("uncertainty unavailable" in reason for reason in report["review_priority"]["reasons"])
    assert report["review_priority"]["evidence"]["uncertainty_available"] is False


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


class _FakeSegFormerConfig:
    INPUT_SIZE = (12, 12)
    NUM_CLASSES = 6
    DEVICE = "cpu"
    SAVE_DIR = "unused"


class _FakeSegFormerSource:
    def predict_confidence_inputs(self, image_path, input_size, tta_mode="light"):
        mask = np.zeros(input_size, dtype=np.uint8)
        mask[5, 2:10] = 1
        uncertainty = np.zeros(input_size, dtype=np.float32)
        uncertainty[5, 2:10] = 0.42
        disagreement = np.zeros(input_size, dtype=np.float32)
        disagreement[5, 4:6] = 0.5
        raw = np.zeros((*input_size, 3), dtype=np.uint8)
        raw[..., 1] = 80
        return {
            "raw_resized": raw,
            "single_mask": mask,
            "fused_mask": mask.copy(),
            "entropy_uncertainty": uncertainty,
            "disagreement_uncertainty": disagreement,
            "tta_specs": ["segformer_identity", "segformer_hflip"],
            "mask_source": {
                "name": "segformer_b1",
                "type": "mmsegmentation",
                "probability_tta": True,
                "uncertainty_available": True,
                "disagreement_available": True,
            },
        }


def test_process_image_accepts_segformer_like_mask_source(tmp_path):
    image_path = tmp_path / "segformer_input.jpg"
    Image.fromarray(np.zeros((12, 12, 3), dtype=np.uint8)).save(image_path)

    report = process_image(_FakeSegFormerSource(), _FakeSegFormerConfig, image_path, tmp_path)

    assert report["mask_source"]["name"] == "segformer_b1"
    assert report["mask_source"]["probability_tta"] is True
    assert report["uncertainty_summary"]["available"] is True
    assert report["disagreement_summary"]["available"] is True
    assert report["risk"]["uncertainty_available"] is True
    assert report["review_priority"]["priority"] in {"low", "medium", "high"}
    assert report["review_priority"]["note"].startswith("Image-based manual-review priority")
    assert all("unavailable" not in item for item in report["risk"]["suggestions"])
    assert report["tta_specs"] == ["segformer_identity", "segformer_hflip"]
    assert report["single_prediction_stats"]["1"] == 8
    assert report["fused_prediction_stats"]["1"] == 8
    assert report["uncertainty_summary"]["defect_mean"] == pytest.approx(0.42)
    assert report["disagreement_summary"]["max"] == pytest.approx(0.5)
    for section in [
        "selected_prediction_stats",
        "self_consistency",
        "adaptive_selection",
        "uncertainty_summary",
        "disagreement_summary",
        "morphology",
        "risk",
        "review_priority",
        "artifacts",
    ]:
        assert section in report
    assert (tmp_path / "segformer_input_report.json").exists()


def test_segformer_source_missing_checkpoint_fails_before_mmseg_import(tmp_path):
    config_path = tmp_path / "segformer.py"
    repo_root = tmp_path / "SegFormer-master"
    config_path.write_text("model = dict()\n", encoding="utf-8")
    repo_root.mkdir()

    with pytest.raises(FileNotFoundError, match="Missing SegFormer checkpoint"):
        load_model(
            model_source="segformer",
            segformer_config=config_path,
            segformer_checkpoint=tmp_path / "missing.pth",
            segformer_repo_root=repo_root,
        )
