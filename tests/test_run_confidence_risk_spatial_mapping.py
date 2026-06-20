from __future__ import annotations

import numpy as np

from run_confidence_risk import write_result_artifacts


def test_write_result_artifacts_emits_spatial_summary_and_multidomain_location(tmp_path):
    raw = np.zeros((10, 10, 3), dtype=np.uint8)
    single_mask = np.zeros((10, 10), dtype=np.uint8)
    fused_mask = np.zeros((10, 10), dtype=np.uint8)
    entropy = np.zeros((10, 10), dtype=np.float32)
    disagreement = np.zeros((10, 10), dtype=np.float32)
    single_mask[2:4, 7:9] = 1
    fused_mask[2:4, 7:9] = 1

    report = write_result_artifacts(
        stem="case",
        raw_resized=raw,
        single_mask=single_mask,
        fused_mask=fused_mask,
        entropy_uncertainty=entropy,
        disagreement_uncertainty=disagreement,
        output_dir=tmp_path,
        mask_source={
            "name": "segformer_b1",
            "spatial_source": "simulation",
            "spatial_accuracy_level": "coarse",
        },
    )

    spatial = report["spatial_summary"]
    multidomain = report["multidomain_results"]

    assert spatial["status"] == "available"
    assert spatial["source"] == "simulation"
    assert spatial["clock_position"]["hour"] == 2
    assert spatial["available"] is True
    assert multidomain["summary"]["location_status"] == "available"
    assert multidomain["results"][0]["location"]["summary"]["clock_position"]["hour"] == 2


def test_write_result_artifacts_preserves_spatial_metadata_source(tmp_path):
    raw = np.zeros((10, 10, 3), dtype=np.uint8)
    single_mask = np.zeros((10, 10), dtype=np.uint8)
    fused_mask = np.zeros((10, 10), dtype=np.uint8)
    entropy = np.zeros((10, 10), dtype=np.float32)
    disagreement = np.zeros((10, 10), dtype=np.float32)
    single_mask[4:6, 4:6] = 1
    fused_mask[4:6, 4:6] = 1

    report = write_result_artifacts(
        stem="case_calibrated",
        raw_resized=raw,
        single_mask=single_mask,
        fused_mask=fused_mask,
        entropy_uncertainty=entropy,
        disagreement_uncertainty=disagreement,
        output_dir=tmp_path,
        mask_source={
            "name": "segformer_b1",
            "spatial_metadata": {
                "source": "calibration",
                "accuracy_level": "calibrated",
                "ring_id": "R-001",
            },
        },
    )

    spatial = report["spatial_summary"]
    multidomain = report["multidomain_results"]

    assert spatial["source"] == "calibration"
    assert spatial["location_source"] == "calibration"
    assert spatial["accuracy_level"] == "calibrated"
    assert multidomain["summary"]["location_source"] == "calibration"
    assert multidomain["results"][0]["location"]["source"] == "calibration"
