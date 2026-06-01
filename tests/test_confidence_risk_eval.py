import numpy as np

from evaluate_confidence_risk import aggregate_comparisons, compare_predictions, segmentation_report, summarize_class_subset


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
    uncertainty = np.zeros((2, 2), dtype=np.float32)

    result = compare_predictions(single, fused, target, uncertainty=uncertainty, num_classes=3, weak_class_ids=[1, 2])

    assert result["supported"] is True
    assert result["fused"]["mIoU"] > result["single"]["mIoU"]
    assert result["delta"]["mIoU"] > 0
    assert result["uncertainty_summary"]["mean"] == 0.0


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
    comparison = compare_predictions(single, fused, target, uncertainty=np.zeros((2, 2), dtype=np.float32), num_classes=3)

    result = aggregate_comparisons([comparison])

    assert result["supported"] is True
    assert result["fused_mIoU"] >= result["single_mIoU"]
    assert result["delta_mIoU"] >= 0
