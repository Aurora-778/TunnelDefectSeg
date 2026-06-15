import numpy as np

from enhancement_evidence import (
    build_patent_evidence_pack,
    build_review_queue_summary,
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
    assert result["review_queue_summary"]["supported"] is True
    assert result["review_queue_summary"]["top_k"][0]["image"] == "guard.jpg"
    assert "GT-derived flags" in result["review_queue_summary"]["gt_usage"]
    assert result["review_queue_summary"]["bucket_metrics"]["high"]["count"] >= 1
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


def test_build_patent_evidence_pack_separates_backbone_and_enhancement_evidence():
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
    evaluation = {
        "aggregate": {
            "single_mIoU": 0.6,
            "fused_mIoU": 0.5,
            "selected_mIoU": 0.62,
            "delta_selected_vs_fused_mIoU": 0.12,
            "delta_selected_vs_single_mIoU": 0.02,
        },
        "samples": [guard, stable],
    }

    result = build_patent_evidence_pack(evaluation, artifact_root="experiments/patent_cases")

    assert result["supported"] is True
    assert result["backbone_evidence"]["model"] == "SegFormer B1 6-class"
    assert "enhancement gain" in result["backbone_evidence"]["note"]
    assert result["enhancement_evidence"]["metric_summary"]["selected_vs_fused_mIoU"] == 0.12
    assert result["enhancement_evidence"]["review_queue_summary"]["supported"] is True
    assert "true_mIoU" in result["gt_boundary"]["gt_required_metrics"]
    assert "self_consistency" in result["gt_boundary"]["no_gt_allowed_metrics"]
    assert any("not structural safety diagnosis" in item for item in result["claim_boundaries"])
    stable_example = result["representative_examples"]["stable_fused"]
    assert stable_example["image"] == "stable.jpg"
    assert stable_example["artifacts"]["selected_mask"] == "experiments/patent_cases/stable_selected_mask.png"
    assert stable_example["artifacts"]["report"] == "experiments/patent_cases/stable_report.json"


def test_build_patent_evidence_pack_marks_unsupported_without_gt_metrics():
    result = build_patent_evidence_pack({"samples": []})

    assert result["supported"] is False
    assert result["enhancement_evidence"] == {"supported": False, "gt_required": True}
    assert result["representative_examples"] == {}
    assert "true_mIoU" in result["gt_boundary"]["gt_required_metrics"]


def test_build_review_queue_summary_ranks_without_gt_flags_but_evaluates_buckets():
    samples = [{"image": "stable.jpg"}, {"image": "needs_review.jpg"}]
    stable = {
        "image": "stable.jpg",
        "selection_mode": "fused",
        "foreground_shrink_fraction": 0.0,
        "protected_pixels_vs_fused": 0,
        "defect_high_uncertainty_fraction": 0.0,
        "defect_mean_uncertainty": 0.01,
        "self_foreground_iou": 0.99,
        "fixed_fusion_harmed_mIoU": True,
        "selected_recovers_over_fused": False,
        "selected_matches_or_beats_single": False,
        "error_high_uncertainty_fraction": 0.1,
        "high_uncertainty_error_fraction": 0.2,
    }
    needs_review = {
        "image": "needs_review.jpg",
        "selection_mode": "single",
        "foreground_shrink_fraction": 0.5,
        "protected_pixels_vs_fused": 12,
        "defect_high_uncertainty_fraction": 0.8,
        "defect_mean_uncertainty": 0.6,
        "self_foreground_iou": 0.3,
        "fixed_fusion_harmed_mIoU": False,
        "selected_recovers_over_fused": True,
        "selected_matches_or_beats_single": True,
        "error_high_uncertainty_fraction": 0.9,
        "high_uncertainty_error_fraction": 0.7,
    }

    result = build_review_queue_summary(samples, [stable, needs_review], high_uncertainty_threshold=0.5)

    assert result["top_k"][0]["image"] == "needs_review.jpg"
    assert result["top_k"][0]["priority"] == "high"
    assert any("uncertainty" in reason for reason in result["top_k"][0]["reasons"])
    assert result["top_k"][0]["gt_flags"]["selected_recovers_over_fused"] is True
    assert result["bucket_metrics"]["high"]["selected_recovers_over_fused_count"] == 1
    assert result["bucket_metrics"]["none"]["fixed_fusion_harmed_count"] == 1
