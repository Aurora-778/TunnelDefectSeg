import json

from inspection_report import build_inspection_report


def test_build_inspection_report_writes_structured_contract(tmp_path):
    report = {
        "stem": "sample",
        "mask_source": {
            "name": "segformer_b1",
            "type": "mmsegmentation",
            "probability_tta": True,
        },
        "tta_specs": ["identity", "hflip"],
        "artifacts": {
            "report": "sample_report.json",
            "overlay": "sample_overlay.png",
            "selected_overlay": "sample_selected_overlay.png",
        },
        "views": [{"label": "Original", "url": "/outputs/job/sample.jpg"}],
        "adaptive_selection": {
            "mode": "fused",
            "reasons": ["stable single/fused foreground IoU 0.977"],
            "consistency": {"foreground_iou": 0.977},
        },
        "self_consistency": {
            "foreground_iou": 0.977,
            "single_fused_mIoU": 0.977,
        },
        "uncertainty_summary": {
            "available": True,
            "mean": 0.0012,
            "defect_mean": 0.084,
        },
        "disagreement_summary": {
            "available": True,
            "mean": 0.0002,
            "defect_mean": 0.01,
        },
        "morphology": {
            "defect_area_pixels": 1003,
            "defect_component_count": 1,
        },
        "morphology_delta": {
            "fused_to_selected": {"explanations": ["selected preserves morphology close to fused"]}
        },
        "risk": {
            "risk_level": "low",
            "score": 1.75,
            "review_required": False,
            "suggestions": ["Low image-based defect risk; routine review is sufficient."],
        },
        "review_priority": {
            "priority": "low",
            "score": 0.5,
            "review_required": False,
            "reasons": ["routine review priority from image-based risk evidence"],
            "note": "Image-based manual-review priority; not a structural safety diagnosis or maintenance decision.",
        },
        "report_url": "/outputs/job/sample_report.json",
        "elapsed_ms": 17,
    }

    export = build_inspection_report(report, job_id="job", original_name="sample.jpg", output_dir=tmp_path)

    report_path = tmp_path / "sample_inspection_report.json"
    assert report_path.exists()

    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == "inspection-report.v1"
    assert saved["source_image"]["name"] == "sample.jpg"
    assert saved["artifacts"]["inspection_report"] == "sample_inspection_report.json"
    assert saved["source_report_url"] == "/outputs/job/sample_report.json"
    assert saved["inspection_report_url"] == "/outputs/job/sample_inspection_report.json"
    assert saved["summary_cards"][0]["label"] == "Risk"
    assert saved["summary_cards"][1]["label"] == "Review"
    assert saved["summary_cards"][2]["label"] == "Selection"
    assert saved["summary_cards"][3]["label"] == "Self IoU"
    assert saved["elapsed_ms"] == 17
    assert export["inspection_report_path"] == "sample_inspection_report.json"
    assert export["assessment"]["review_priority"]["priority"] == "low"
    assert export["evidence"]["morphology_delta"]["fused_to_selected"]["explanations"][0].startswith("selected")
    assert "C:\\" not in json.dumps(saved, ensure_ascii=False)


def test_build_inspection_report_includes_evaluation_and_spatial_summary(tmp_path):
    report = {
        "stem": "case",
        "mask_source": {"name": "segformer_b1", "type": "mmsegmentation"},
        "tta_specs": ["identity"],
        "artifacts": {"report": "case_report.json"},
        "risk": {"risk_level": "medium", "score": 2.25, "review_required": True},
        "review_priority": {"priority": "medium", "score": 1.5, "review_required": True, "note": "review later"},
        "adaptive_selection": {"mode": "single", "reasons": [], "consistency": {}},
        "self_consistency": {"foreground_iou": 0.82},
        "uncertainty_summary": {"available": True},
        "disagreement_summary": {"available": True},
        "morphology": {},
        "morphology_delta": {},
        "evaluation": {"selected_mIoU": 0.33, "fused_mIoU": 0.31},
        "spatial_summary": {"available": True, "estimated": 1},
    }

    export = build_inspection_report(report, job_id="job-2", original_name="case.png", output_dir=tmp_path)

    assert export["evaluation"]["selected_mIoU"] == 0.33
    assert export["spatial_summary"]["estimated"] == 1
    assert any(card["label"] == "GT mIoU" for card in export["summary_cards"])
