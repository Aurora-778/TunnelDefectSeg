import numpy as np

from morphology_adapter import MorphologyConfig, measure_class, measure_mask, skeleton_mask


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
