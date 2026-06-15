import numpy as np

from morphology_adapter import MorphologyConfig, compare_mask_morphology, measure_class, measure_mask, skeleton_mask


def test_empty_mask_returns_zero_measurements():
    mask = np.zeros((12, 12), dtype=np.uint8)

    result = measure_class(mask, 1, MorphologyConfig(min_component_area=1))

    assert result["area_pixels"] == 0
    assert result["component_count"] == 0
    assert result["skeleton_length"] == 0
    assert result["dominant_direction"] is None


def test_horizontal_defect_reports_direction_and_length():
    mask = np.zeros((20, 20), dtype=np.uint8)
    mask[10, 3:17] = 5

    result = measure_class(mask, 5, MorphologyConfig(min_component_area=1))

    assert result["component_count"] == 1
    assert result["skeleton_length"] > 5
    assert result["dominant_direction"] == "horizontal"


def test_two_components_report_fragmentation():
    mask = np.zeros((20, 20), dtype=np.uint8)
    mask[2:5, 2:5] = 2
    mask[12:15, 12:15] = 2

    result = measure_class(mask, 2, MorphologyConfig(min_component_area=1))

    assert result["component_count"] == 2
    assert result["fragmentation_index"] == 1
    assert result["fragmented"] is True


def test_measure_mask_excludes_background_from_defect_totals():
    mask = np.zeros((10, 10), dtype=np.uint8)
    mask[1:4, 1:4] = 1
    mask[6:8, 6:8] = 0

    result = measure_mask(mask, config=MorphologyConfig(min_component_area=1))

    assert result["defect_area_pixels"] == 9
    assert result["defect_component_count"] == 1


def test_small_components_are_filtered_by_min_area():
    mask = np.zeros((12, 12), dtype=np.uint8)
    mask[1, 1] = 1
    mask[5:8, 5:8] = 1

    result = measure_class(mask, 1, MorphologyConfig(min_component_area=4))

    assert result["raw_area_pixels"] == 10
    assert result["area_pixels"] == 9
    assert result["component_count"] == 1


def test_skeleton_mask_preserves_class_id_on_skeleton():
    mask = np.zeros((10, 10), dtype=np.uint8)
    mask[5, 2:8] = 4

    skel = skeleton_mask(mask, class_ids=[4], min_component_area=1)

    assert set(np.unique(skel).tolist()) == {0, 4}


def test_compare_mask_morphology_reports_area_component_and_skeleton_delta():
    source = np.zeros((20, 20), dtype=np.uint8)
    source[5, 2:16] = 1
    target = np.zeros((20, 20), dtype=np.uint8)
    target[5, 2:6] = 1
    target[5, 12:16] = 1

    result = compare_mask_morphology(
        source,
        target,
        "single",
        "fused",
        config=MorphologyConfig(min_component_area=1),
    )

    assert result["source_defect_area_pixels"] == 14
    assert result["target_defect_area_pixels"] == 8
    assert result["defect_area_delta_pixels"] == -6
    assert result["defect_area_ratio_target_over_source"] == 8 / 14
    assert result["defect_component_delta"] == 1
    assert result["defect_skeleton_length_delta"] < 0
    assert any("reduces measured defect area" in item for item in result["explanations"])
    assert any("more connected components" in item for item in result["explanations"])
    class_delta = result["class_deltas"][0]
    assert class_delta["class_name"] == "simple"
    assert class_delta["area_delta_pixels"] == -6


def test_compare_mask_morphology_handles_empty_masks_without_false_degradation():
    source = np.zeros((8, 8), dtype=np.uint8)
    target = np.zeros((8, 8), dtype=np.uint8)

    result = compare_mask_morphology(source, target, "single", "fused")

    assert result["defect_area_ratio_target_over_source"] == 1.0
    assert result["defect_component_delta"] == 0
    assert result["explanations"] == ["single and fused contain no measured defect foreground"]
