import numpy as np

from enhancement_evidence import (
    foreground_pixels,
    prediction_class_pixels,
    sample_enhancement_evidence,
    summarize_enhancement_evidence,
)
from evaluate_confidence_risk import compare_predictions


def _comparison(image, single, fused, selected, target, mode="single", uncertainty=None):
    result = compare_predictions(
        np.asarray(single, dtype=np.uint8),
        np.asarray(fused, dtype=np.uint8),
        np.asarray(target, dtype=np.uint8),
        selected_mask=np.asarray(selected, dtype=np.uint8),
        selection_mode=mode,
        uncertainty=np.asarray(uncertainty if uncertainty is not None else np.zeros_like(target), dtype=np.float32),
        num_classes=3,
    )
    result["image"] = image
    result["adaptive_selection"] = {
        "mode": mode,
        "reasons": ["synthetic reason"],
        "consistency": {
            "foreground_iou": 0.75 if mode == "fused" else 0.4,
        },
    }
    return result


def test_foreground_pixels_are_recovered_from_prediction_columns():
    report = {
        "confusion_matrix": [
            [7, 2, 1],
            [3, 4, 5],
            [1, 0, 6],
        ]
    }

    assert prediction_class_pixels(report, 1) == 6
    assert prediction_class_pixels(report, 2) == 12
    assert foreground_pixels(report) == 18


def test_sample_enhancement_evidence_reports_shrinkage_and_protection():
    target = [[0, 1], [1, 1]]
    single = [[0, 1], [1, 1]]
    fused = [[0, 1], [0, 0]]
    selected = single
    sample = _comparison("guard.jpg", single, fused, selected, target, mode="single")

    result = sample_enhancement_evidence(sample)

    assert result["single_foreground_pixels"] == 3
    assert result["fused_foreground_pixels"] == 1
    assert result["selected_foreground_pixels"] == 3
    assert result["foreground_shrink_pixels"] == 2
    assert result["protected_pixels_vs_fused"] == 2
    assert result["selected_recovers_over_fused"] is True
    assert result["error_high_uncertainty_fraction"] == 0.0


def test_summarize_enhancement_evidence_aggregates_modes_and_examples():
    guard = _comparison(
        "guard.jpg",
        single=[[0, 1], [1, 1]],
        fused=[[0, 1], [0, 0]],
        selected=[[0, 1], [1, 1]],
        target=[[0, 1], [1, 1]],
        mode="single",
        uncertainty=[[0.0, 0.8], [0.9, 0.9]],
    )
    stable = _comparison(
        "stable.jpg",
        single=[[0, 1], [0, 1]],
        fused=[[0, 1], [0, 1]],
        selected=[[0, 1], [0, 1]],
        target=[[0, 1], [0, 1]],
        mode="fused",
        uncertainty=[[0.0, 0.1], [0.0, 0.1]],
    )
    limitation = _comparison(
        "limitation.jpg",
        single=[[0, 1], [0, 1]],
        fused=[[0, 1], [1, 1]],
        selected=[[0, 1], [1, 1]],
        target=[[0, 1], [0, 0]],
        mode="fused",
        uncertainty=[[0.0, 0.2], [0.2, 0.2]],
    )
    evaluation = {
        "aggregate": {
            "single_mIoU": 0.6,
            "fused_mIoU": 0.5,
            "selected_mIoU": 0.62,
            "delta_selected_vs_fused_mIoU": 0.12,
            "delta_selected_vs_single_mIoU": 0.02,
        },
        "samples": [guard, stable, limitation],
    }

    result = summarize_enhancement_evidence(evaluation, high_uncertainty_threshold=0.5)

    assert result["supported"] is True
    assert result["selection"]["mode_counts"] == {"single": 1, "fused": 2}
    assert result["foreground_shrinkage"]["event_count"] == 1
    assert result["small_defect_guard"]["total_protected_pixels_vs_fused"] == 2
    assert result["miou_recovery"]["selected_recovers_over_fused_count"] >= 1
    assert result["uncertainty_review"]["high_defect_uncertainty_count"] == 1
    assert result["uncertainty_review"]["total_error_pixels"] == 2
    assert result["uncertainty_review"]["total_high_uncertainty_error_pixels"] == 0
    assert result["uncertainty_review"]["micro_error_high_uncertainty_fraction"] == 0.0
    assert np.isclose(result["uncertainty_review"]["pixel_high_uncertainty_threshold"], 0.35)
    assert result["uncertainty_review"]["review_fraction_threshold"] == 0.5
    assert result["uncertainty_review"]["calibration_expected_error"] is not None
    assert result["uncertainty_review"]["calibration_max_gap"] is not None
    assert result["uncertainty_review"]["calibration_bins"]
    assert result["representative_examples"]["small_defect_guard"]["image"] == "guard.jpg"
    assert result["representative_examples"]["stable_fused"]["image"] == "stable.jpg"
    assert result["representative_examples"]["high_uncertainty_review"]["image"] == "guard.jpg"
    assert result["representative_examples"]["limitation_case"]["image"] == "limitation.jpg"
    assert result["class_iou_summary"][1]["class_name"] == "simple"


def test_stable_fused_example_prefers_non_empty_defect_over_empty_background():
    empty = _comparison(
        "empty.jpg",
        single=[[0, 0], [0, 0]],
        fused=[[0, 0], [0, 0]],
        selected=[[0, 0], [0, 0]],
        target=[[0, 0], [0, 0]],
        mode="fused",
    )
    empty["adaptive_selection"]["consistency"]["foreground_iou"] = 1.0
    defect = _comparison(
        "defect.jpg",
        single=[[0, 1], [0, 1]],
        fused=[[0, 1], [0, 1]],
        selected=[[0, 1], [0, 1]],
        target=[[0, 1], [0, 1]],
        mode="fused",
    )
    defect["adaptive_selection"]["consistency"]["foreground_iou"] = 0.98

    result = summarize_enhancement_evidence({"samples": [empty, defect]})

    assert result["representative_examples"]["stable_fused"]["image"] == "defect.jpg"
