from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_lists_runner_artifacts():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    runner = (ROOT / "run_confidence_risk.py").read_text(encoding="utf-8")
    artifact_names = [
        "_single_mask.png",
        "_fused_mask.png",
        "_hybrid_mask.png",
        "_selected_mask.png",
        "_overlay.png",
        "_selected_overlay.png",
        "_uncertainty_heatmap.png",
        "_disagreement_heatmap.png",
        "_skeleton.png",
        "_report.json",
    ]

    for name in artifact_names:
        assert name in readme
        assert name in runner


def test_patent_note_mentions_full_method_chain():
    note = (ROOT / "docs" / "patent-notes" / "tunnel-defect-confidence-risk.md").read_text(encoding="utf-8")

    for phrase in ["多姿态", "自适应", "selected mask", "不确定性", "骨架", "风险分级", "复核建议"]:
        assert phrase in note


def test_docs_do_not_treat_self_iou_as_ground_truth_accuracy():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    note = (ROOT / "docs" / "patent-notes" / "tunnel-defect-confidence-risk.md").read_text(encoding="utf-8")

    assert "not ground-truth mIoU" in readme
    assert "not a substitute for label-based accuracy" in note


def test_patent_note_does_not_claim_structural_safety_diagnosis():
    note = (ROOT / "docs" / "patent-notes" / "tunnel-defect-confidence-risk.md").read_text(encoding="utf-8")

    assert "does **not** provide structural safety diagnosis" in note
    assert "final maintenance decisions" in note


def test_patent_note_includes_aggregate_evidence_and_threshold_semantics():
    note = (ROOT / "docs" / "patent-notes" / "tunnel-defect-confidence-risk.md").read_text(encoding="utf-8")

    for phrase in [
        "SegFormer B1",
        "post-inference enhancement layer",
        "Protected pixels vs fused",
        "HU error precision",
        "Pixel high-uncertainty threshold",
        "Review fraction threshold",
        "mIoU 84.33",
    ]:
        assert phrase in note


def test_experiment_summary_documents_gt_only_overlap_boundary():
    summary = (ROOT / "docs" / "experiments" / "enhancement-evidence-summary.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "docs/experiments/enhancement-evidence-summary.md" in readme
    assert "Error coverage" in summary
    assert "HU error precision" in summary
    assert "pixel_high_uncertainty_threshold" in summary
    assert "review_fraction_threshold" in summary
    assert "without GT" in summary
    assert "must not display true mIoU" in summary
