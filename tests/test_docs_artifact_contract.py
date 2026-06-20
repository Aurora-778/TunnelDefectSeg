import json
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
    assert "置信校准" in note
    assert "review_priority" in note
    assert "docs/patent-notes/confidence-review-disclosure.md" in note
    assert "docs/patent-notes/confidence-review-claims-draft.md" in note


def test_patent_disclosure_formalizes_method_steps_and_boundaries():
    disclosure = (ROOT / "docs" / "patent-notes" / "confidence-review-disclosure.md").read_text(encoding="utf-8")

    for step in [f"S{index}." for index in range(1, 9)]:
        assert step in disclosure
    for phrase in [
        "post-inference confidence review",
        "SegFormer B1 作为 `mask source`",
        "融合损伤识别",
        "morphology_delta",
        "evidence_flags.area_shrinkage",
        "algorithmic_evidence",
        "review priority",
        "review_priority.formula.components",
        "defect_uncertainty",
        "fused_foreground_shrinkage",
        "不提供结构安全诊断",
        "不替代人工验收或专家复核",
        "不能输出或暗示",
        "true mIoU",
    ]:
        assert phrase in disclosure


def test_patent_claims_draft_has_method_system_and_storage_claims():
    claims = (ROOT / "docs" / "patent-notes" / "confidence-review-claims-draft.md").read_text(encoding="utf-8")

    for phrase in [
        "独立权利要求 1：方法",
        "独立权利要求 11：系统",
        "独立权利要求 15：计算机可读存储介质",
        "候选预测生成模块",
        "融合损伤识别模块",
        "形态变化分析模块",
        "SegFormer、FCN 或其他语义分割模型作为 mask source",
        "不应作为独立发明点主张",
    ]:
        assert phrase in claims


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
        "morphology_delta",
        "review_queue_summary",
        "experiments/patent_evidence_test_pack.json",
        "Protected pixels vs fused",
        "HU error precision",
        "Pixel high-uncertainty threshold",
        "Review fraction threshold",
        "mIoU 84.33",
    ]:
        assert phrase in note


def test_experiment_summary_documents_gt_only_overlap_boundary():
    summary = (ROOT / "docs" / "experiments" / "enhancement-evidence-summary.md").read_text(encoding="utf-8")
    ablation = (ROOT / "docs" / "experiments" / "patent-ablation-summary.md").read_text(encoding="utf-8")
    case_pack = (ROOT / "docs" / "experiments" / "patent-case-pack.md").read_text(encoding="utf-8")
    patent_pack = json.loads((ROOT / "experiments" / "patent_evidence_test_pack.json").read_text(encoding="utf-8"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "docs/experiments/enhancement-evidence-summary.md" in readme
    assert "docs/experiments/patent-ablation-summary.md" in readme
    assert "docs/experiments/patent-case-pack.md" in readme
    assert "docs/experiments/patent-ablation-summary.md" in summary
    assert "docs/experiments/patent-case-pack.md" in summary
    assert "Error coverage" in summary
    assert "HU error precision" in summary
    assert "pixel_high_uncertainty_threshold" in summary
    assert "review_fraction_threshold" in summary
    assert "uncertainty_calibration" in summary
    assert "review_priority" in summary
    assert "review_queue_summary" in summary
    assert "without GT" in summary
    assert "must not display true mIoU" in summary
    for phrase in [
        "`single` | measured",
        "`fixed_fused` | measured",
        "`selected` | measured",
        "`selected_without_morphology` | pending",
        "`full_review_priority` | measured as review evidence",
        "pending ablations are already measured",
    ]:
        assert phrase in ablation
    for phrase in [
        "C1 | fixed fusion harmed / selected recovered | measured",
        "C2 | model-internal stable fused / GT caution | measured",
        "C4 | top review queue sample | measured",
        "C6 | high disagreement case | pending",
        "C7 | morphology degradation case | pending",
        "artifact_paths_verified: false",
        "experiments/confidence_risk/pos_10_t1_30_*",
        "Selected vs single mIoU: `-0.0776`",
    ]:
        assert phrase in case_pack
    manifest = patent_pack["case_manifest"]
    assert [item["case_id"] for item in manifest] == ["C1", "C2", "C3", "C4", "C5", "C6", "C7"]
    assert manifest[0]["gt_available"] is True
    assert manifest[0]["status"] == "measured"
    assert manifest[-1]["status"] == "pending"


def test_paper_outline_and_claim_matrix_keep_claims_grounded():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    outline = (ROOT / "docs" / "paper" / "confidence-review-outline.md").read_text(encoding="utf-8")
    matrix = (ROOT / "docs" / "paper" / "claim-to-evidence-matrix.md").read_text(encoding="utf-8")

    assert "docs/paper/confidence-review-outline.md" in readme
    assert "docs/paper/claim-to-evidence-matrix.md" in readme
    for phrase in [
        "Research Question",
        "candidate mask generation -> fusion harm detection",
        "selected does not universally beat single",
        "Do not write fabricated citations",
        "Fig. 3",
        "C2 model-internal stable fused / GT caution",
        "C6 and C7 pending",
    ]:
        assert phrase in outline
    for phrase in [
        "C1 | The project uses SegFormer B1 as the current trained 6-class",
        "C10 | Removing morphology_delta would reduce review-priority quality. | pending",
        "C11 | Removing uncertainty/disagreement would reduce review-priority quality. | pending",
        "C12 | High-disagreement and morphology-degradation visual cases are available as generated artifacts. | pending",
        "Do not cite Web screenshots as the only evidence for algorithm claims.",
        "C1 through C9",
    ]:
        assert phrase in matrix


def test_readme_documents_explicit_segformer_mask_source():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "--model-source segformer" in readme
    assert "legacy_resnet50_fcn" in readme
    assert "segformer_b1" in readme
    assert "mask_source" in readme


def test_web_demo_documents_segformer_probability_tta_sample():
    html = (ROOT / "web_demo" / "index.html").read_text(encoding="utf-8")

    assert "uncertainty_summary?.available === false" in html
    assert 'probability_tta: true' in html
    assert "review_priority" in html
    assert "Review priority" in html
    assert "review_queue_summary" in html
    assert "Review queue" in html
    assert "renderReviewQueue" in html
    assert "renderMorphologyDelta" in html
    assert "morphologyDeltaGrid" in html
    assert "escapeHtml" in html
    assert "Calibration gap" in html
    assert 'priority.priority || (risk.review_required ? "medium" : level)' in html
    assert 'tta_specs: ["segformer_identity", "segformer_hflip"]' in html
    assert "assets/segformer_t1_1_uncertainty_heatmap.png" in html
    assert "assets/segformer_t1_1_disagreement_heatmap.png" in html
    assert "当前 SegFormer 接口只输出单个 mask" not in html
    assert "当前 SegFormer 单 mask 模式" not in html


def test_presentation_uses_same_web_demo_segformer_assets():
    deck = (ROOT / "docs" / "presentations" / "tunnel-defect-project" / "index.html").read_text(encoding="utf-8")

    assert "../../../web_demo/assets/segformer_t1_1_overlay.png" in deck
    assert "../../../web_demo/assets/segformer_t1_1_selected_mask.png" in deck
    assert "../../../web_demo/assets/segformer_t1_1_skeleton.png" in deck
    assert "../../../experiments/segformer_b1/confidence_risk_smoke" not in deck


def test_software_copyright_materials_document_scope_and_boundaries():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    guide = (ROOT / "docs" / "software-copyright" / "tunnel-defect-review-system.md").read_text(encoding="utf-8")
    checklist = (ROOT / "docs" / "software-copyright" / "source-material-checklist.md").read_text(encoding="utf-8")

    assert "docs/software-copyright/tunnel-defect-review-system.md" in readme
    assert "隧道病害智能分割与可信复核分析系统 V1.0" in guide
    assert "不提供结构安全诊断" in guide
    assert "第三方依赖" in guide
    assert "web_app.py" in checklist
    assert "third_party/" in checklist
    assert "模型权重" in checklist


def test_competition_materials_indexes_internal_demo_and_pending_external_sources():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    matrix = (ROOT / "docs" / "competition" / "cs-202613-requirements-matrix.md").read_text(encoding="utf-8")
    civil = (ROOT / "docs" / "competition" / "civil-defect-evaluation.md").read_text(encoding="utf-8")
    multidomain = (ROOT / "docs" / "competition" / "multidomain-detector-notes.md").read_text(encoding="utf-8")
    report_template = (ROOT / "docs" / "competition" / "report-template.md").read_text(encoding="utf-8")
    report_outline = (ROOT / "docs" / "competition" / "technical-report-outline.md").read_text(encoding="utf-8")
    demo_script = (ROOT / "docs" / "competition" / "demo-script.md").read_text(encoding="utf-8")
    summary = (ROOT / "docs" / "competition" / "experiment-summary.md").read_text(encoding="utf-8")
    case_pack = (ROOT / "docs" / "competition" / "demo-case-pack.md").read_text(encoding="utf-8")
    spatial_method = (ROOT / "docs" / "competition" / "spatial-mapping-method.md").read_text(encoding="utf-8")
    local_setup = (ROOT / "docs" / "local-setup-windows.md").read_text(encoding="utf-8")
    html = (ROOT / "web_demo" / "index.html").read_text(encoding="utf-8")

    assert "docs/competition/cs-202613-requirements-matrix.md" in readme
    assert "docs/competition/civil-defect-evaluation.md" in readme
    assert "docs/competition/multidomain-detector-notes.md" in readme
    assert "docs/competition/technical-report-outline.md" in readme
    assert "docs/competition/demo-script.md" in readme
    assert "docs/competition/experiment-summary.md" in readme
    assert "docs/competition/demo-case-pack.md" in readme
    assert "docs/competition/report-template.md" in readme
    assert "docs/local-setup-windows.md" in readme
    assert "run_multidomain_inspection.py" in readme
    assert "C:/Users/26822" not in readme
    assert "D:/users/anaconda3" not in readme
    assert "third_party/SegFormer-master" not in readme
    assert "CS-202613 Requirements Matrix" in matrix
    for req_id in [f"R{index}" for index in range(1, 16)]:
        assert req_id in matrix
    for status in ["supported", "planned", "simulation-only", "out-of-scope"]:
        assert status in matrix
    assert "Windows Local Setup" in local_setup
    assert "segformer-phase2" in local_setup
    assert "run_train_segformer_cuda.bat" in local_setup
    assert "run_web_app.bat" in local_setup
    assert "Civil Defect Evaluation" in civil
    assert "simple" in civil
    assert "blocky" in civil
    assert "Multidomain Detector Notes" in multidomain
    assert "fastener_missing" in multidomain
    assert "bracket_loose" in multidomain
    assert "multidomain_results" in report_template
    assert "multidomainPanel" in html
    assert "renderMultidomain" in html
    assert "multidomain_results" in html
    assert "clock_position" in html
    assert "locationParts" in html
    assert "locationDisplayable" in html
    assert "环号" in html
    assert "里程" in html
    assert "Technical Report Outline" in report_outline
    assert "Multidomain Extension" in report_outline
    assert "Demo Script" in demo_script
    assert "track / equipment" in demo_script
    assert "spatial mapping 卡片" in demo_script
    assert "Internal labeled dataset" in summary
    assert "Official competition samples" in summary
    assert "Demo cases" in summary
    assert "C1" in case_pack and "C7" in case_pack
    assert "official samples" in case_pack
    assert "simulation-only" in case_pack
    assert "location_source" in spatial_method
    assert "accuracy_level" in spatial_method
    assert "location_accuracy_level" in spatial_method
    assert "clock position" in spatial_method
