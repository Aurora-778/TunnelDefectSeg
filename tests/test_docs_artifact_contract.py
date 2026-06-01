from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_lists_runner_artifacts():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    runner = (ROOT / "run_confidence_risk.py").read_text(encoding="utf-8")
    artifact_names = [
        "_single_mask.png",
        "_fused_mask.png",
        "_overlay.png",
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

    for phrase in ["多姿态", "不确定性", "骨架", "风险分级", "复核建议"]:
        assert phrase in note


def test_patent_note_does_not_claim_structural_safety_diagnosis():
    note = (ROOT / "docs" / "patent-notes" / "tunnel-defect-confidence-risk.md").read_text(encoding="utf-8")

    assert "does **not** provide structural safety diagnosis" in note
    assert "final maintenance decisions" in note
