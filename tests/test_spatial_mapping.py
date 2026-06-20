from __future__ import annotations

import numpy as np

from spatial_mapping import build_spatial_summary


def test_build_spatial_summary_from_mask_emits_clock_and_normalized_position():
    mask = np.zeros((10, 10), dtype=np.uint8)
    mask[1:3, 5:7] = 1

    summary = build_spatial_summary(mask, image_shape=(10, 10), source="simulation", accuracy_level="coarse")

    assert summary["status"] == "available"
    assert summary["available"] is True
    assert summary["source"] == "simulation"
    assert summary["location_source"] == "simulation"
    assert summary["accuracy_level"] == "coarse"
    assert summary["method"] == "mask_centroid_clock_mapping"
    assert summary["pixel_center"] == [6.0, 2.0]
    assert summary["normalized_center"] == [0.6, 0.2]
    assert summary["clock_position"]["hour"] == 1
    assert summary["clock_position"]["label"] == "1点方向"
    assert summary["local_3d"]["status"] == "unavailable"
    assert "local_3d_unavailable" in summary["limitations"]


def test_build_spatial_summary_from_bbox_adds_engineering_fields():
    geometry = {
        "kind": "bbox",
        "bbox": [0.75, 0.4, 0.9, 0.6],
    }

    summary = build_spatial_summary(
        geometry,
        image_shape=(100, 200),
        metadata={
            "source": "calibration",
            "ring_id": "R-0128",
            "mileage": "K12+340.5",
            "camera_intrinsics": {"fx": 100.0, "fy": 100.0, "cx": 150.0, "cy": 50.0},
            "depth": 20.0,
        },
    )

    assert summary["status"] == "available"
    assert summary["source"] == "calibration"
    assert summary["ring_id"] == "R-0128"
    assert summary["mileage"] == "K12+340.5"
    assert summary["clock_position"]["hour"] == 3
    assert summary["clock_position"]["label"] == "3点方向"
    assert summary["local_3d"]["available"] is True
    assert summary["local_3d"]["point"] == [3.0, 0.0, 20.0]
    assert summary["accuracy_level"] == "metric"


def test_build_spatial_summary_returns_unavailable_for_empty_mask():
    mask = np.zeros((6, 6), dtype=np.uint8)

    summary = build_spatial_summary(mask, image_shape=(6, 6))

    assert summary["status"] == "unavailable"
    assert summary["available"] is False
    assert summary["reason"] == "The mask does not contain any foreground pixels."
