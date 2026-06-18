from run_multidomain_inspection import build_combined_report


def test_build_combined_report_adds_multidomain_demo_entries():
    report = {
        "mask_source": {"name": "segformer_b1"},
        "selected_prediction_stats": {"0": 10, "1": 5},
        "class_names": {"0": "background", "1": "simple"},
        "review_priority": {"priority": "low", "score": 0.5},
        "artifacts": {"selected_mask": "demo_selected_mask.png"},
    }

    payload = build_combined_report(report)

    assert payload["mask_source"]["name"] == "segformer_b1"
    assert payload["multidomain_results"]["summary"]["civil_result_count"] == 1
    assert set(payload["multidomain_results"]["domains"]) == {"civil", "track", "equipment"}
    assert payload["multidomain_results"]["results"][0]["schema_version"] == "multidomain-result.v1"
