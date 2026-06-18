from multidomain_detectors import build_demo_track_equipment_results, build_multidomain_demo_report


def test_demo_track_equipment_results_cover_track_and_equipment_demo_cases():
    report = {
        "review_priority": {"priority": "medium", "score": 1.2},
        "spatial_summary": {"available": True, "source": "simulation", "accuracy_level": "coarse"},
    }

    results = build_demo_track_equipment_results(report)

    assert [item["domain"] for item in results] == ["track", "equipment"]
    assert results[0]["defect_type"] == "fastener_missing"
    assert results[1]["defect_type"] == "bracket_loose"
    assert results[0]["status"] == "simulation-only"
    assert results[0]["location"]["status"] == "available"
    assert results[0]["location"]["source"] == "simulation"
    assert results[0]["review_priority"]["priority"] == "medium"


def test_build_multidomain_demo_report_merges_civil_and_demo_adapters():
    report = {
        "mask_source": {"name": "segformer_b1"},
        "selected_prediction_stats": {"0": 20, "1": 8, "2": 4},
        "class_names": {"0": "background", "1": "simple", "2": "blocky"},
        "review_priority": {"priority": "medium", "score": 1.2},
        "artifacts": {"selected_mask": "sample_selected_mask.png"},
    }

    payload = build_multidomain_demo_report(report)

    assert payload["schema_version"] == "multidomain-result.v1"
    assert set(payload["domains"]) == {"civil", "track", "equipment"}
    assert payload["summary"]["civil_result_count"] == 2
    assert any(item["defect_type"] == "fastener_missing" for item in payload["results"])
    assert any(item["defect_type"] == "blocky" for item in payload["results"])
