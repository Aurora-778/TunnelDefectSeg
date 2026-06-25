import json

from robot_inspection_report import build_route_inspection_report_from_manifest


def _report(area, *, risk=1.0, mileage="K1+001", center=(0.5, 0.5)):
    return {
        "assessment": {
            "risk": {"score": risk, "review_required": risk >= 2.0},
            "review_priority": {"priority": "medium" if risk >= 2.0 else "low", "review_required": risk >= 2.0},
        },
        "evidence": {
            "morphology": {
                "main_class": "simple",
                "class_id": 1,
                "defect_area_pixels": area,
                "skeleton_length": area / 5,
                "defect_component_count": 1,
            },
            "uncertainty_summary": {"mean": 0.1, "defect_mean": 0.2},
        },
        "spatial_summary": {
            "status": "available",
            "bbox": [center[0] - 0.05, center[1] - 0.05, center[0] + 0.05, center[1] + 0.05],
            "pixel_area": area,
            "normalized_center": [center[0], center[1]],
            "pixel_center": [center[0] * 100, center[1] * 100],
            "mileage": mileage,
            "ring_id": "R1",
            "clock_position": {"hour": 12, "label": "12点方向"},
            "local_3d": {"status": "unavailable", "available": False},
            "source": "test-fixture",
            "accuracy_level": "coarse",
            "limitations": ["local_3d_unavailable"],
        },
    }


def test_build_route_inspection_report_exports_summary_tracks_and_review_queue(tmp_path):
    manifest = {
        "frames": [
            {"frame_id": "old", "inspection_run_id": "run-old", "mileage": "K1+001", "ring_id": "R1", "camera_id": "cam-a"},
            {"frame_id": "new", "inspection_run_id": "run-new", "mileage": "K1+002", "ring_id": "R1", "camera_id": "cam-a"},
        ]
    }
    output_path = tmp_path / "route_report.json"

    report = build_route_inspection_report_from_manifest(
        manifest,
        {"old": _report(100), "new": _report(180, risk=2.5, mileage="K1+002")},
        route_id="route-a",
        output_path=output_path,
    )

    assert output_path.exists()
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == "robot-inspection-report.v1"
    assert saved["report_path"].endswith("route_report.json")
    assert report["summary"]["frame_count"] == 2
    assert report["summary"]["track_count"] == 1
    assert report["summary"]["review_count"] == 1
    assert report["tracks"][0]["trend"]["label"] == "suspected-growth"
    assert report["tracks"][0]["latest_location"]["normalized_center"] == [0.5, 0.5]
    assert report["tracks"][0]["latest_location"]["local_3d_status"] == "unavailable"
    assert report["tracks"][0]["latest_location"]["source"] == "test-fixture"
    assert report["review_queue"][0]["track_id"] == report["tracks"][0]["track_id"]
    assert report["review_queue"][0]["priority_score"] >= 100
    assert "not_prediction_unless_cross_cycle_comparable_or_manual_verified" == report["claim_guard"]["prediction_claim"]


def test_build_route_inspection_report_summarizes_missing_metadata_limitations():
    manifest = {"frames": [{"frame_id": "f1"}]}

    report = build_route_inspection_report_from_manifest(manifest, {"f1": _report(10)})

    assert report["summary"]["frame_count"] == 1
    assert "mileage_missing" in report["summary"]["limitations"]
    assert report["tracks"][0]["trend"]["label"] == "baseline-only"
