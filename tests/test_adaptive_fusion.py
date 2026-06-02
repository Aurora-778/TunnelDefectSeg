import numpy as np

from adaptive_fusion import (
    AdaptiveFusionConfig,
    build_hybrid_mask,
    mask_iou,
    select_adaptive_mask,
    self_consistency,
)


def test_mask_iou_returns_one_for_two_empty_foregrounds():
    a = np.zeros((4, 4), dtype=np.uint8)
    b = np.zeros((4, 4), dtype=np.uint8)

    assert mask_iou(a, b) == 1.0


def test_identical_masks_select_fused_with_stable_reason():
    single = np.zeros((8, 8), dtype=np.uint8)
    single[2:5, 2:5] = 1
    fused = single.copy()

    result = select_adaptive_mask(single, fused)

    assert result["selection_mode"] == "fused"
    assert np.array_equal(result["selected_mask"], fused)
    assert "stable" in " ".join(result["selection_reasons"])


def test_fused_shrinkage_preserves_low_uncertainty_single_pixels_as_hybrid():
    single = np.zeros((10, 10), dtype=np.uint8)
    single[2:7, 2:7] = 1
    fused = np.zeros((10, 10), dtype=np.uint8)
    fused[4:6, 4:6] = 1
    entropy = np.zeros((10, 10), dtype=np.float32)
    disagreement = np.zeros((10, 10), dtype=np.float32)

    result = select_adaptive_mask(single, fused, entropy, disagreement)

    assert result["selection_mode"] == "hybrid"
    assert int((result["selected_mask"] > 0).sum()) > int((fused > 0).sum())
    assert int((result["selected_mask"] > 0).sum()) == int((single > 0).sum())


def test_high_uncertainty_removed_pixels_fall_back_to_single_not_hybrid():
    single = np.zeros((10, 10), dtype=np.uint8)
    single[2:7, 2:7] = 1
    fused = np.zeros((10, 10), dtype=np.uint8)
    fused[4:6, 4:6] = 1
    entropy = np.ones((10, 10), dtype=np.float32)
    disagreement = np.ones((10, 10), dtype=np.float32)

    result = select_adaptive_mask(single, fused, entropy, disagreement)

    assert result["selection_mode"] == "single"
    assert np.array_equal(result["selected_mask"], single)


def test_fused_only_defect_evidence_is_preserved():
    single = np.zeros((8, 8), dtype=np.uint8)
    fused = np.zeros((8, 8), dtype=np.uint8)
    fused[2:4, 2:4] = 1

    result = select_adaptive_mask(single, fused)

    assert result["selection_mode"] == "fused"
    assert np.array_equal(result["selected_mask"], fused)


def test_tiny_removed_fragment_is_not_recovered_by_hybrid_filter():
    single = np.zeros((8, 8), dtype=np.uint8)
    single[2, 2] = 1
    fused = np.zeros((8, 8), dtype=np.uint8)
    entropy = np.zeros((8, 8), dtype=np.float32)
    disagreement = np.zeros((8, 8), dtype=np.float32)

    hybrid = build_hybrid_mask(
        single,
        fused,
        entropy,
        disagreement,
        AdaptiveFusionConfig(min_recovered_component_pixels=3),
    )

    assert int((hybrid > 0).sum()) == 0


def test_low_self_iou_emits_review_or_instability_reason():
    single = np.zeros((10, 10), dtype=np.uint8)
    fused = np.zeros((10, 10), dtype=np.uint8)
    single[1:4, 1:4] = 1
    fused[6:9, 6:9] = 1

    result = select_adaptive_mask(single, fused)

    assert result["selection_mode"] == "single"
    assert "low single/fused foreground IoU" in " ".join(result["selection_reasons"])


def test_self_consistency_reports_area_ratios_and_agreement():
    single = np.zeros((4, 4), dtype=np.uint8)
    fused = np.zeros((4, 4), dtype=np.uint8)
    single[:2, :2] = 1
    fused[:1, :2] = 1

    result = self_consistency(single, fused)

    assert result["foreground_iou"] == 0.5
    assert result["fused_to_single_area_ratio"] == 0.5
    assert result["pixel_agreement"] < 1.0
