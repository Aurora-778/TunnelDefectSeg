from __future__ import annotations

from copy import deepcopy
import csv
import json
from pathlib import Path

from PIL import Image
import pytest

from orchestrator.agents.association_agent import AssociationAgent
from orchestrator.agents.claim_gate_agent import ClaimGateAgent
from orchestrator.agents.comparison_evidence_agent import ComparisonEvidenceAgent
from orchestrator.agents.growth_report_agent import GrowthReportAgent
from orchestrator.agents.memory_report_agent import MemoryReportAgent
from orchestrator.inspection_workflow import PhaseA1ArtifactError, initialize_phase_a1_sandbox
from orchestrator.inspection_workflow import a1_artifacts, a1_reports
from scripts import prepare_real_inspection_pilot as preparation


RUN_ID = "run_202"
PLAN_FINGERPRINT = "a" * 64


def _fixture(tmp_path: Path, *, include_query: bool = False, legacy_memory_text: bool = False):
    root = tmp_path / "a1-report-sandbox"
    dataset = root / "dataset"
    work = root / "runs" / RUN_ID / "work"
    (dataset / "images").mkdir(parents=True)
    (dataset / "masks").mkdir()
    Image.new("RGB", (8, 8), color=(90, 100, 110)).save(dataset / "images/a.jpg")
    mask = Image.new("L", (8, 8), color=0)
    mask.putpixel((1, 2), 255)
    mask.save(dataset / "masks/a.png")
    rows = [
        {
            "sequence_id": "S01",
            "source_inspection_id": "visit_1",
            "frame_id": "1",
            "timestamp": "2026-07-01T10:00:00Z",
            "mileage_m": "12.0",
            "ring_id": "1",
            "clock_direction": "12点",
            "image_file": "images/a.jpg",
            "mask_file": "masks/a.png",
            "local_observation_id": "obs_01",
            "disease_type": "crack",
        }
    ]
    if include_query:
        Image.new("RGB", (8, 8), color=(40, 50, 60)).save(dataset / "images/b.jpg")
        Image.new("L", (8, 8), color=255).save(dataset / "masks/b.png")
        rows.append(
            {
                "sequence_id": "S01",
                "source_inspection_id": "visit_2",
                "frame_id": "2",
                "timestamp": "2026-07-02T10:00:00Z",
                "mileage_m": "10000.0",
                "ring_id": "9000",
                "clock_direction": "6点",
                "image_file": "images/b.jpg",
                "mask_file": "masks/b.png",
                "local_observation_id": "obs_02",
                "disease_type": "crack",
            }
        )
    with (dataset / "metadata.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=preparation.REQUIRED_METADATA_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    initialize_phase_a1_sandbox(
        root,
        run_id=RUN_ID,
        evidence_source_mode="run_local_projection",
    )
    prepared = work / "raw_prepared"
    preparation.prepare_real_inspection_pilot(dataset, prepared)
    preparation.require_inference_ready(prepared / "preparation_manifest.json")
    history = work / "raw_history"
    AssociationAgent().run(
        {
            "inputs": {
                "association": {
                    "history_only": "true",
                    "frame_records": str(prepared / "frame_records.csv"),
                    "output_path": str(history / "association_records.csv"),
                    "history_output_dir": str(history / "main_progressive"),
                    "manifest_path": str(history / "association_manifest.json"),
                    "use_disease_id_score": "false",
                    "association_mode": "no_id",
                }
            },
            "outputs": {},
            "shared": {"project_root": str(root)},
        }
    )
    if include_query and legacy_memory_text:
        memory_path = history / "main_progressive/round_002/memory_before_query.csv"
        with memory_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames
            memory_rows = list(reader)
        memory_rows[0]["memory_description"] = "病害明显增长，风险上升"
        memory_rows[0]["risk_level_change"] = "2"
        with memory_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(memory_rows)

    context = {
        "shared": {
            "project_root": str(root),
            "run_id": RUN_ID,
            "execution_profile": "phase_a1_sandbox",
            "plan_fingerprint": PLAN_FINGERPRINT,
        },
        "inputs": {
            "comparison_evidence": {
                "projection_mode": "prepared_history_sources",
                "prepared_manifest_path": (
                    f"runs/{RUN_ID}/work/raw_prepared/preparation_manifest.json"
                ),
                "history_association_path": (
                    f"runs/{RUN_ID}/work/raw_history/association_records.csv"
                ),
                "history_manifest_path": (
                    f"runs/{RUN_ID}/work/raw_history/association_manifest.json"
                ),
            }
        },
    }
    ComparisonEvidenceAgent().run(context)
    ClaimGateAgent().run(context)
    report_context = {"shared": deepcopy(context["shared"]), "inputs": {}}
    return root, report_context


def _text(root: Path, relative: str) -> str:
    return (root / relative).read_text(encoding="utf-8")


def test_baseline_generates_static_growth_and_empty_memory_reports(tmp_path):
    root, context = _fixture(tmp_path, include_query=True)

    growth = GrowthReportAgent().run(context)
    memory = MemoryReportAgent().run(context)

    assert growth["record_count"] == 2
    growth_text = _text(root, growth["report_paths"][0])
    assert "当前静态面积审计" in growth_text
    assert "directional_change_claim：status=blocked" in growth_text
    assert "physical_quantity_change_claim：status=blocked" in growth_text
    assert "multi_timepoint_pattern_claim：status=blocked" in growth_text
    assert "prediction_claim：status=blocked" in growth_text
    assert "不构成方向性变化结论" in growth_text
    memory_text = _text(root, memory["report_paths"][0])
    assert "Round 001 /" in memory_text
    assert "候选 Memory：无" in memory_text
    assert "内部候选 Memory Snapshot，非正式工程结论" in memory_text
    assert not (root / "outputs").exists()


def test_unmatched_query_remains_static_only_and_memory_projection_is_neutral(tmp_path):
    root, context = _fixture(tmp_path, include_query=True, legacy_memory_text=True)

    growth = GrowthReportAgent().run(context)
    memory = MemoryReportAgent().run(context)

    growth_text = "".join(_text(root, path) for path in growth["report_paths"])
    memory_text = "".join(_text(root, path) for path in memory["report_paths"])
    assert "association_rejected" in growth_text
    assert "directional_change_claim：status=blocked" in growth_text
    assert "明显增长" not in growth_text + memory_text
    assert "风险上升" not in growth_text + memory_text
    assert "risk_level_change" not in memory_text
    assert "last_area_px 静态审计：Claim Gate 未授权展示" in memory_text


def test_blocked_renderer_does_not_emit_static_or_directional_claim():
    evidence = {
        "evidence_id": "EV-001",
        "current_value": 10,
        "absolute_difference": None,
        "relative_difference": None,
    }
    decision = {
        "evidence_id": "EV-001",
        "decision_id": "CD-EV-001",
        "current_observation_id": "OBS-001",
        "identity_evidence_state": "association_invalid",
        "comparison_comparability_status": "insufficient_history",
        "capabilities": {
            "static_descriptive_audit": "blocked",
            "descriptive_difference_claim": "blocked",
            "directional_change_claim": "blocked",
            "physical_quantity_change_claim": "blocked",
            "multi_timepoint_pattern_claim": "blocked",
            "prediction_claim": "blocked",
        },
        "capability_reasons": {
            "static_descriptive_audit": "EVIDENCE_INVALID",
            "descriptive_difference_claim": "EVIDENCE_INVALID",
            "directional_change_claim": "EVIDENCE_INVALID",
            "physical_quantity_change_claim": "PHASE_A_NO_PHYSICAL_QUANTITY_EVIDENCE",
            "multi_timepoint_pattern_claim": "PHASE_A_NO_THREE_TIMEPOINT_EVIDENCE",
            "prediction_claim": "PHASE_A_NO_VALIDATED_PREDICTION_MODEL",
        },
        "template_ids": {
            "static_descriptive_audit": None,
            "descriptive_difference_claim": None,
            "directional_change_claim": None,
            "physical_quantity_change_claim": None,
            "multi_timepoint_pattern_claim": None,
            "prediction_claim": None,
        },
        "required_language_qualifiers": [],
        "reason_codes": ["EVIDENCE_INVALID"],
    }

    lines = a1_reports._decision_lines(evidence, decision)

    assert "- 当前静态面积审计：Claim Gate 已阻断" in lines
    assert "- 描述性差值：Claim Gate 未授权" in lines
    assert any("directional_change_claim：status=blocked" in line for line in lines)
    assert any("prediction_claim：status=blocked" in line for line in lines)


@pytest.mark.parametrize(
    ("candidate_field", "evidence_field", "bad_value"),
    [
        ("memory_version", "previous_memory_version", "v999"),
        ("last_seen_inspection", "previous_last_seen_inspection", "I999"),
        ("source_inspection_ids", "previous_source_inspection_ids", ["I999"]),
        ("source_record_count", "previous_source_record_count", 99),
        ("last_area_px", "previous_memory_snapshot_value", 99),
        (
            "comparability_status",
            "previous_comparability_status",
            "verified_comparable",
        ),
    ],
)
def test_memory_evidence_binding_rejects_every_trusted_field_mismatch(
    candidate_field,
    evidence_field,
    bad_value,
):
    candidate = {
        "memory_version": "v1",
        "last_seen_inspection": "I001",
        "source_inspection_ids": ["I001"],
        "source_record_count": 1,
        "last_area_px": 1,
        "comparability_status": "insufficient_history",
    }
    evidence = {
        "evidence_id": "EV-001",
        "previous_memory_version": "v1",
        "previous_last_seen_inspection": "I001",
        "previous_source_inspection_ids": ["I001"],
        "previous_source_record_count": 1,
        "previous_memory_snapshot_value": 1,
        "previous_comparability_status": "insufficient_history",
    }
    evidence[evidence_field] = bad_value

    with pytest.raises(PhaseA1ArtifactError, match=evidence_field):
        a1_reports._require_memory_evidence_binding(evidence, candidate)


def test_report_agents_reject_input_and_output_overrides(tmp_path):
    _, context = _fixture(tmp_path, include_query=True)
    context["inputs"] = {"growth_report": {"output_path": "outputs/bad.md"}}
    with pytest.raises(PhaseA1ArtifactError, match="does not accept"):
        GrowthReportAgent().run(context)
    context["inputs"] = {"memory_report": {"source_path": "data/bad.csv"}}
    with pytest.raises(PhaseA1ArtifactError, match="does not accept"):
        MemoryReportAgent().run(context)


@pytest.mark.parametrize(
    "agent,context",
    [
        (GrowthReportAgent(), {"inputs": []}),
        (MemoryReportAgent(), {"shared": []}),
        (GrowthReportAgent(), []),
    ],
)
def test_report_agents_normalize_malformed_context(agent, context):
    with pytest.raises(PhaseA1ArtifactError, match="context|inputs/shared"):
        agent.run(context)


def test_source_tampering_is_rejected_before_report_write(tmp_path):
    root, context = _fixture(tmp_path, include_query=True)
    source = root / f"runs/{RUN_ID}/work/association_manifest.json"
    source.write_bytes(source.read_bytes() + b" ")

    with pytest.raises(PhaseA1ArtifactError, match="size does not match|SHA-256 does not match"):
        GrowthReportAgent().run(context)

    assert not (root / f"runs/{RUN_ID}/staging").exists()


def test_missing_required_qualifier_is_rejected_before_report_write(tmp_path):
    root, context = _fixture(tmp_path, include_query=True)
    decision_path = root / f"runs/{RUN_ID}/artifacts/claim_decision.json"
    document = json.loads(decision_path.read_text(encoding="utf-8"))
    document["record_decisions"][0]["required_language_qualifiers"] = []
    decision_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(PhaseA1ArtifactError, match="claim_decision.json is invalid"):
        GrowthReportAgent().run(context)

    assert not (root / f"runs/{RUN_ID}/staging").exists()


def test_reports_are_idempotent_and_use_fixed_paths(tmp_path):
    root, context = _fixture(tmp_path, include_query=True)
    first_growth = GrowthReportAgent().run(context)
    first_memory = MemoryReportAgent().run(context)
    before = {
        path: (root / path).read_bytes()
        for path in first_growth["report_paths"] + first_memory["report_paths"]
    }

    second_growth = GrowthReportAgent().run(context)
    second_memory = MemoryReportAgent().run(context)

    assert second_growth == first_growth
    assert second_memory == first_memory
    assert before == {path: (root / path).read_bytes() for path in before}
    assert set(before) == {
        f"runs/{RUN_ID}/staging/disease_growth_analysis_report.md",
        f"runs/{RUN_ID}/staging/disease_growth_analysis_summary.md",
        f"runs/{RUN_ID}/staging/memory_agent_report.md",
        f"runs/{RUN_ID}/staging/disease_memory_bank_summary.md",
    }


def test_partial_growth_report_commit_records_only_real_commit(tmp_path, monkeypatch):
    root, context = _fixture(tmp_path, include_query=True)
    original = a1_artifacts._atomic_write_idempotent

    def fail_second(path, data, *, allowed_root):
        if path.name == "disease_growth_analysis_summary.md":
            raise PhaseA1ArtifactError("summary write failed")
        return original(path, data, allowed_root=allowed_root)

    monkeypatch.setattr(a1_artifacts, "_atomic_write_idempotent", fail_second)
    with pytest.raises(PhaseA1ArtifactError, match="recovery is required"):
        GrowthReportAgent().run(context)

    marker = json.loads(
        (root / f"runs/{RUN_ID}/staging/.a1_recovery_required.json").read_text(
            encoding="utf-8"
        )
    )
    assert marker["committed_paths"] == [
        f"runs/{RUN_ID}/staging/disease_growth_analysis_report.md"
    ]


def test_source_change_after_report_writes_marks_both_commits(tmp_path, monkeypatch):
    root, context = _fixture(tmp_path, include_query=True)
    original = a1_reports._load_report_inputs
    calls = {"count": 0}

    def mutate_before_final_reload(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            source = root / f"runs/{RUN_ID}/work/association_manifest.json"
            source.write_bytes(source.read_bytes() + b" ")
        return original(*args, **kwargs)

    monkeypatch.setattr(a1_reports, "_load_report_inputs", mutate_before_final_reload)
    with pytest.raises(PhaseA1ArtifactError, match="recovery is required"):
        GrowthReportAgent().run(context)

    marker = json.loads(
        (root / f"runs/{RUN_ID}/staging/.a1_recovery_required.json").read_text(
            encoding="utf-8"
        )
    )
    assert marker["committed_paths"] == [
        f"runs/{RUN_ID}/staging/disease_growth_analysis_report.md",
        f"runs/{RUN_ID}/staging/disease_growth_analysis_summary.md",
    ]


def test_uncertain_first_report_write_marks_zero_known_commits(tmp_path, monkeypatch):
    root, context = _fixture(tmp_path, include_query=True)
    original = a1_artifacts._atomic_write_idempotent

    def fail_uncertainly(path, data, **kwargs):
        if path.name == "disease_growth_analysis_report.md":
            error = PhaseA1ArtifactError("first report replace is uncertain")
            error.write_state_uncertain = True
            raise error
        return original(path, data, **kwargs)

    monkeypatch.setattr(a1_artifacts, "_atomic_write_idempotent", fail_uncertainly)
    with pytest.raises(PhaseA1ArtifactError, match="recovery is required") as captured:
        GrowthReportAgent().run(context)

    marker = json.loads(
        (root / f"runs/{RUN_ID}/staging/.a1_recovery_required.json").read_text(
            encoding="utf-8"
        )
    )
    assert marker["committed_paths"] == []
    assert isinstance(captured.value.__cause__, PhaseA1ArtifactError)


def test_clean_zero_commit_failure_allows_retry(tmp_path, monkeypatch):
    root, context = _fixture(tmp_path, include_query=True)
    original = a1_artifacts._atomic_write_idempotent
    calls = {"failed": False}

    def fail_cleanly_once(path, data, *, allowed_root):
        if not calls["failed"]:
            calls["failed"] = True
            raise PhaseA1ArtifactError("clean first write failure")
        return original(path, data, allowed_root=allowed_root)

    monkeypatch.setattr(a1_artifacts, "_atomic_write_idempotent", fail_cleanly_once)
    with pytest.raises(PhaseA1ArtifactError, match="clean first write failure"):
        GrowthReportAgent().run(context)
    assert not (root / f"runs/{RUN_ID}/staging/.a1_recovery_required.json").exists()

    monkeypatch.setattr(a1_artifacts, "_atomic_write_idempotent", original)
    result = GrowthReportAgent().run(context)
    assert all((root / path).is_file() for path in result["report_paths"])


def test_broken_staging_report_leaf_is_rejected_without_following_it(
    tmp_path,
    monkeypatch,
):
    root, context = _fixture(tmp_path, include_query=True)
    target = root / f"runs/{RUN_ID}/staging/disease_growth_analysis_report.md"
    target.parent.mkdir(parents=True)
    real_check = a1_artifacts._path_is_reparse_point

    def mark_target_as_reparse(path):
        if path == target:
            return True
        return real_check(path)

    monkeypatch.setattr(a1_artifacts, "_path_is_reparse_point", mark_target_as_reparse)
    with pytest.raises(PhaseA1ArtifactError, match="symlink or reparse point"):
        GrowthReportAgent().run(context)

    assert not target.exists()


def test_agents_remain_unregistered_and_formal_artifacts_are_untouched(tmp_path):
    root, context = _fixture(tmp_path, include_query=True)
    GrowthReportAgent().run(context)
    MemoryReportAgent().run(context)
    repository = Path(__file__).resolve().parents[1]
    registry = (repository / "orchestrator/registry.py").read_text(encoding="utf-8")
    dag = (repository / "config/dag.yaml").read_text(encoding="utf-8")

    assert "GrowthReportAgent" not in registry
    assert "MemoryReportAgent" not in registry
    assert "growth_report" not in dag
    assert "memory_report" not in dag
    assert not (root / "data/simulated").exists()
    assert not (root / "outputs").exists()
