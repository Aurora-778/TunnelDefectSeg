import numpy as np

from adaptive_fusion import AdaptiveFusionConfig
from evaluate_confidence_risk import (
    aggregate_comparisons,
    candidate_adaptive_configs,
    compare_predictions,
    evaluate_records,
    segmentation_report,
    summarize_class_subset,
    uncertainty_error_overlap,
)


def test_segmentation_report_returns_per_class_metrics():
    target = np.array([[0, 1], [1, 2]], dtype=np.uint8)
    pred = np.array([[0, 1], [2, 2]], dtype=np.uint8)

    report = segmentation_report(pred, target, num_classes=3)

    assert report["supported"] is True
    assert 0.0 <= report["mIoU"] <= 1.0
    assert set(report["class_ious"].keys()) == {"0", "1", "2"}
    assert len(report["confusion_matrix"]) == 3


def test_compare_predictions_reports_single_and_fused_delta():
    target = np.array([[0, 1], [1, 2]], dtype=np.uint8)
    single = np.array([[0, 1], [2, 2]], dtype=np.uint8)
    fused = target.copy()
    selected = target.copy()
    uncertainty = np.zeros((2, 2), dtype=np.float32)

    result = compare_predictions(
        single,
        fused,
        target,
        selected_mask=selected,
        selection_mode="fused",
        uncertainty=uncertainty,
        num_classes=3,
        weak_class_ids=[1, 2],
    )

    assert result["supported"] is True
    assert result["fused"]["mIoU"] > result["single"]["mIoU"]
    assert result["selected"]["mIoU"] == result["fused"]["mIoU"]
    assert result["delta"]["mIoU"] > 0
    assert result["delta"]["selected_vs_single_mIoU"] > 0
    assert result["selection_mode"] == "fused"
    assert result["uncertainty_summary"]["mean"] == 0.0
    assert result["uncertainty_error_overlap"]["error_pixels"] == 0


def test_uncertainty_error_overlap_reports_error_coverage_and_precision():
    target = np.array([[0, 1], [1, 2]], dtype=np.uint8)
    pred = np.array([[0, 0], [0, 2]], dtype=np.uint8)
    uncertainty = np.array([[0.0, 0.9], [0.1, 0.8]], dtype=np.float32)

    result = uncertainty_error_overlap(pred, target, uncertainty, high_threshold=0.35)

    assert result["error_pixels"] == 2
    assert result["high_uncertainty_pixels"] == 2
    assert result["high_uncertainty_error_pixels"] == 1
    assert result["error_high_uncertainty_fraction"] == 0.5
    assert result["high_uncertainty_error_fraction"] == 0.5
    assert np.isclose(result["mean_uncertainty_on_error"], 0.5)
    assert np.isclose(result["mean_uncertainty_on_correct"], 0.4)


def test_compare_predictions_handles_missing_labels():
    single = np.zeros((2, 2), dtype=np.uint8)
    fused = np.zeros((2, 2), dtype=np.uint8)

    result = compare_predictions(single, fused, None)

    assert result["supported"] is False
    assert result["reason"] == "missing target mask"


def test_weak_class_subset_summary():
    report = {
        "class_ious": {
            "2": 0.5,
            "5": 0.25,
        }
    }

    result = summarize_class_subset(report, [2, 5])

    assert result["num_supported"] == 2
    assert result["mean_iou"] == 0.375


def test_aggregate_comparisons_summarizes_supported_samples():
    target = np.array([[0, 1], [1, 2]], dtype=np.uint8)
    single = np.array([[0, 1], [2, 2]], dtype=np.uint8)
    fused = target.copy()
    comparison = compare_predictions(
        single,
        fused,
        target,
        selected_mask=single,
        selection_mode="single",
        uncertainty=np.zeros((2, 2), dtype=np.float32),
        num_classes=3,
    )

    result = aggregate_comparisons([comparison])

    assert result["supported"] is True
    assert result["fused_mIoU"] >= result["single_mIoU"]
    assert result["selected_mIoU"] == result["single_mIoU"]
    assert result["delta_mIoU"] >= 0
    assert result["selection_mode_counts"] == {"single": 1}
    assert result["uncertainty_error_overlap"]["total_error_pixels"] == 1
    assert result["uncertainty_error_overlap"]["micro_error_high_uncertainty_fraction"] == 0.0


def test_candidate_configs_are_available_for_validation_search():
    configs = candidate_adaptive_configs()

    assert configs
    assert all(isinstance(config, AdaptiveFusionConfig) for config in configs)


def test_evaluate_records_applies_supplied_adaptive_config():
    single = np.zeros((10, 10), dtype=np.uint8)
    fused = np.zeros((10, 10), dtype=np.uint8)
    single.flat[:10] = 1
    fused.flat[:7] = 1
    record = {
        "image": "sample.jpg",
        "single_mask": single,
        "fused_mask": fused,
        "target": single,
        "entropy": np.ones((10, 10), dtype=np.float32),
        "disagreement": np.ones((10, 10), dtype=np.float32),
    }

    strict = evaluate_records(
        [record],
        num_classes=2,
        fusion_config=AdaptiveFusionConfig(stable_self_iou=0.95, stable_area_ratio=0.92, shrink_ratio=0.80),
    )[0]
    permissive = evaluate_records(
        [record],
        num_classes=2,
        fusion_config=AdaptiveFusionConfig(stable_self_iou=0.65, stable_area_ratio=0.65, shrink_ratio=0.80),
    )[0]

    assert strict["selection_mode"] == "single"
    assert permissive["selection_mode"] == "fused"
    assert strict["selected"]["mIoU"] > permissive["selected"]["mIoU"]
