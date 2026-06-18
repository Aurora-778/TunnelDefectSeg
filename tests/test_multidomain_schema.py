from multidomain_schema import build_multidomain_payload


def test_build_multidomain_payload_emits_civil_results_and_summary():
    report = {
        "mask_source": {"name": "segformer_b1"},
        "tta_specs": ["identity", "hflip"],
        "selected_prediction_stats": {"0": 20, "1": 8, "2": 4},
        "uncertainty_summary": {"available": True, "defect_mean": 0.25},
        "disagreement_summary": {"available": True, "defect_mean": 0.1},
        "self_consistency": {"foreground_iou": 0.83},
        "review_priority": {"priority": "medium", "score": 1.2},
        "artifacts": {"selected_mask": "sample_selected_mask.png"},
        "spatial_summary": {"available": True, "source": "simulation", "accuracy_level": "coarse"},
    }

    payload = build_multidomain_payload(report)

    assert payload["schema_version"] == "multidomain-result.v1"
    assert payload["source_model"] == "segformer_b1"
    assert payload["domains"] == ["civil"]
    assert payload["summary"]["civil_result_count"] == 2
    assert payload["summary"]["location_status"] == "available"
    assert payload["summary"]["review_priority"] == "medium"
    assert [item["defect_type"] for item in payload["results"]] == ["simple", "blocky"]
    assert payload["results"][0]["geometry"]["artifact"] == "sample_selected_mask.png"
    assert payload["results"][0]["confidence"]["available"] is True
    assert payload["results"][0]["location"]["source"] == "simulation"
    assert payload["results"][0]["review_priority"]["priority"] == "medium"


def test_build_multidomain_payload_falls_back_to_background_only_when_no_foreground():
    report = {
        "mask_source": {"name": "legacy_resnet50_fcn"},
        "selected_prediction_stats": {"0": 64},
        "review_priority": {"priority": "low"},
        "artifacts": {"selected_mask": "empty_selected_mask.png"},
    }

    payload = build_multidomain_payload(report)

    assert payload["summary"]["civil_result_count"] == 1
    assert payload["results"][0]["defect_type"] == "no_defect"
    assert payload["results"][0]["status"] == "background_only"
