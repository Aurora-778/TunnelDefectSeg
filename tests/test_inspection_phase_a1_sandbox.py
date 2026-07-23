from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

import orchestrator.inspection_workflow.a1_artifacts as a1_artifacts
from orchestrator.agents.claim_audit_report_agent import ClaimAuditReportAgent
from orchestrator.agents.claim_gate_agent import ClaimGateAgent
from orchestrator.agents.comparison_evidence_agent import ComparisonEvidenceAgent
from orchestrator.inspection_workflow import (
    COMPARISON_EVIDENCE_FIELDS,
    COMPARISON_EVIDENCE_MANIFEST_SCHEMA_VERSION,
    COMPARISON_EVIDENCE_SCHEMA_VERSION,
    PhaseA1ArtifactError,
    load_validated_claim_artifacts,
    parse_comparison_evidence_csv,
)
from conftest import (
    artifact_manifest_diff,
    artifact_snapshot,
)


PLAN_FINGERPRINT = "f" * 64
RUN_ID = "run_001"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _baseline_evidence(*, hashes: dict[str, str], **overrides):
    record = {
        "comparison_evidence_schema_version": COMPARISON_EVIDENCE_SCHEMA_VERSION,
        "evidence_id": "EVD-I001-obs-01-area",
        "execution_profile": "phase_a1_sandbox",
        "evidence_schema_valid": True,
        "current_record_valid": True,
        "current_inspection_id": "I001",
        "current_frame_id": "40",
        "current_image_id": "I001_000040",
        "current_observation_id": "I001::obs_01",
        "current_timestamp": "2026-07-01T10:00:00.000000Z",
        "current_observation_source_declared": True,
        "current_observation_source": "real_inspection_mask_input",
        "current_comparability_status": "insufficient_history",
        "previous_entity_type": "not_applicable",
        "previous_memory_id": None,
        "previous_memory_version": None,
        "previous_last_seen_inspection": None,
        "previous_last_seen_timestamp": None,
        "previous_source_inspection_ids": [],
        "previous_source_record_count": 0,
        "previous_observation_sources_declared": True,
        "previous_observation_sources": [],
        "previous_comparability_status": "insufficient_history",
        "comparison_group_id": "I001::obs_01::inspection_level_max_mask_area_px",
        "metric_name": "inspection_level_max_mask_area_px",
        "metric_type": "area",
        "value_domain": "mask_pixel_area",
        "measurement_method": "inspection_level_max_mask_area_px",
        "measurement_unit": "pixel²",
        "current_value": 120,
        "previous_memory_snapshot_value": None,
        "absolute_difference": None,
        "relative_difference": None,
        "association_id": None,
        "association_status": None,
        "association_mode": None,
        "use_disease_id_score": None,
        "association_score": None,
        "match_type": None,
        "candidate_count": 0,
        "score_margin": None,
        "conflict_reason": None,
        "needs_manual_review": False,
        "association_supported_pair": False,
        "identity_evidence_state": "association_not_applicable",
        "valid_timepoint_count": 1,
        "metric_consistent": True,
        "measurement_method_consistent": True,
        "temporal_order_valid": False,
        "difference_valid": False,
        "relative_difference_valid": False,
        "evidence_valid": True,
        "invalid_reason": None,
        "comparison_comparability_status": "insufficient_history",
        "comparability_reason": "baseline_current_only",
        "registration_status": "not_verified",
        "registration_evidence_source": "none",
        "registration_evidence_sha256": None,
        "physical_scale_calibrated": False,
        "scale_calibration_source": "none",
        "scale_calibration_sha256": None,
        "measurement_uncertainty": None,
        "uncertainty_source": "not_available",
        "uncertainty_unit": None,
        "source_current_record_fingerprint": "a" * 64,
        "source_association_artifact_sha256": hashes["association_artifact"],
        "source_association_manifest_sha256": hashes["association_manifest"],
        "source_memory_snapshot_sha256": None,
        "source_engineering_artifact_sha256": hashes["engineering_artifact"],
    }
    record.update(overrides)
    return record


def _sandbox_fixture(tmp_path: Path):
    project_root = tmp_path / "phase-a1-project"
    work = project_root / "runs" / RUN_ID / "work"
    work.mkdir(parents=True)
    files = {
        "association_artifact": work / "association.csv",
        "association_manifest": work / "association_manifest.json",
        "engineering_artifact": work / "engineering.csv",
    }
    contents = {
        "association_artifact": b"association_id\n",
        "association_manifest": b'{"mode":"history_only"}\n',
        "engineering_artifact": b"inspection_id,current_value\nI001,120\n",
    }
    for role, path in files.items():
        path.write_bytes(contents[role])
    hashes = {role: _sha256(contents[role]) for role in files}
    source_artifacts = [
        {
            "role": role,
            "path": path.relative_to(project_root).as_posix(),
        }
        for role, path in reversed(list(files.items()))
    ]
    context = {
        "shared": {
            "project_root": str(project_root),
            "run_id": RUN_ID,
            "execution_profile": "phase_a1_sandbox",
            "plan_fingerprint": PLAN_FINGERPRINT,
        },
        "inputs": {
            "comparison_evidence": {
                "records": [_baseline_evidence(hashes=hashes)],
                "source_artifacts": source_artifacts,
            },
            "claim_gate": {},
            "claim_audit_report": {},
        },
    }
    return project_root, files, hashes, context


def _run_evidence_and_claim(context):
    evidence_result = ComparisonEvidenceAgent().run(context)
    claim_result = ClaimGateAgent().run(context)
    return evidence_result, claim_result


def test_phase_a1_agents_create_only_fixed_run_local_artifacts(tmp_path):
    before = artifact_snapshot()
    project_root, _, _, context = _sandbox_fixture(tmp_path)

    evidence_result, claim_result = _run_evidence_and_claim(context)
    report_result = ClaimAuditReportAgent().run(context)

    assert evidence_result["comparison_evidence_path"] == (
        "runs/run_001/artifacts/comparison_evidence.csv"
    )
    assert evidence_result["comparison_evidence_manifest_path"].endswith(
        "comparison_evidence_manifest.json"
    )
    assert claim_result["claim_decision_path"].endswith("claim_decision.json")
    assert report_result["claim_audit_report_path"].endswith("claim_audit_report.md")
    assert (project_root / evidence_result["comparison_evidence_path"]).is_file()
    assert (project_root / claim_result["claim_decision_path"]).is_file()
    assert (project_root / report_result["claim_audit_report_path"]).is_file()
    assert not (project_root / "data").exists()
    assert not (project_root / "outputs").exists()
    assert artifact_manifest_diff(before, artifact_snapshot()) == (set(), set(), set())


def test_manifest_is_last_and_binds_exact_source_bytes(tmp_path):
    project_root, files, _, context = _sandbox_fixture(tmp_path)
    evidence_result = ComparisonEvidenceAgent().run(context)
    manifest_path = project_root / evidence_result["comparison_evidence_manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["schema_version"] == COMPARISON_EVIDENCE_MANIFEST_SCHEMA_VERSION
    assert manifest["record_count"] == 1
    assert manifest["source_artifacts"] == sorted(
        manifest["source_artifacts"],
        key=lambda item: (item["role"], item["path"]),
    )
    source_by_role = {item["role"]: item for item in manifest["source_artifacts"]}
    for role, path in files.items():
        assert source_by_role[role]["sha256"] == _sha256(path.read_bytes())
        assert source_by_role[role]["size_bytes"] == path.stat().st_size


def test_evidence_csv_round_trip_preserves_canonical_types(tmp_path):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    result = ComparisonEvidenceAgent().run(context)
    data = (project_root / result["comparison_evidence_path"]).read_bytes()

    records = parse_comparison_evidence_csv(data)

    assert tuple(records[0]) == COMPARISON_EVIDENCE_FIELDS
    assert records[0]["previous_source_inspection_ids"] == []
    assert records[0]["use_disease_id_score"] is None
    assert records[0]["evidence_valid"] is True
    assert records[0]["current_value"] == 120


def test_identical_rerun_is_idempotent_but_changed_evidence_is_rejected(tmp_path):
    _, _, _, context = _sandbox_fixture(tmp_path)
    first = ComparisonEvidenceAgent().run(context)
    assert ComparisonEvidenceAgent().run(context) == first

    context["inputs"]["comparison_evidence"]["records"][0]["current_value"] = 121
    with pytest.raises(PhaseA1ArtifactError, match="refusing to overwrite changed"):
        ComparisonEvidenceAgent().run(context)


def test_changed_existing_manifest_is_rejected_before_missing_evidence_is_written(
    tmp_path,
):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    artifacts = project_root / "runs/run_001/artifacts"
    artifacts.mkdir(parents=True)
    manifest = artifacts / "comparison_evidence_manifest.json"
    manifest.write_text('{"stale":true}\n', encoding="utf-8")

    with pytest.raises(PhaseA1ArtifactError, match="refusing to overwrite changed"):
        ComparisonEvidenceAgent().run(context)

    assert not (artifacts / "comparison_evidence.csv").exists()
    assert manifest.read_text(encoding="utf-8") == '{"stale":true}\n'


def test_atomic_write_failure_reports_temp_cleanup_failure_without_losing_cause(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "artifact.json"

    def fail_replace(source, destination):
        raise OSError("replace failed")

    def fail_unlink(self, *args, **kwargs):
        raise OSError("cleanup failed")

    monkeypatch.setattr(a1_artifacts.os, "replace", fail_replace)
    monkeypatch.setattr(Path, "unlink", fail_unlink)

    with pytest.raises(
        PhaseA1ArtifactError,
        match="temporary cleanup also failed.*cleanup failed",
    ) as captured:
        a1_artifacts._atomic_write_idempotent(target, b"{}\n")

    assert isinstance(captured.value.__cause__, OSError)
    assert "replace failed" in str(captured.value.__cause__)


def test_successful_bundle_leaves_no_temporary_artifacts(tmp_path):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    _run_evidence_and_claim(context)
    ClaimAuditReportAgent().run(context)

    assert not list((project_root / "runs").rglob("*.tmp"))


def test_evidence_hash_must_match_validated_source_artifact(tmp_path):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    context["inputs"]["comparison_evidence"]["records"][0][
        "source_engineering_artifact_sha256"
    ] = "0" * 64

    with pytest.raises(PhaseA1ArtifactError, match="not bound"):
        ComparisonEvidenceAgent().run(context)

    assert not (project_root / "runs/run_001/artifacts").exists()


def test_unreferenced_optional_source_artifact_is_rejected(tmp_path):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    extra = project_root / "runs/run_001/work/unused-memory.csv"
    extra.write_text("memory_id\nMEM-001\n", encoding="utf-8")
    context["inputs"]["comparison_evidence"]["source_artifacts"].append(
        {
            "role": "memory_snapshot",
            "path": "runs/run_001/work/unused-memory.csv",
        }
    )

    with pytest.raises(PhaseA1ArtifactError, match="must exactly match Evidence references"):
        ComparisonEvidenceAgent().run(context)

    assert not (project_root / "runs/run_001/artifacts").exists()


@pytest.mark.parametrize(
    "path_value",
    [
        "./runs/run_001/work/engineering.csv",
        "runs//run_001/work/engineering.csv",
        "runs/run_001/work/../engineering.csv",
        "runs\\run_001\\work\\engineering.csv",
        "C:/outside/engineering.csv",
    ],
)
def test_source_artifact_paths_must_use_canonical_run_local_posix_form(
    tmp_path,
    path_value,
):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    engineering = next(
        item
        for item in context["inputs"]["comparison_evidence"]["source_artifacts"]
        if item["role"] == "engineering_artifact"
    )
    engineering["path"] = path_value

    with pytest.raises(PhaseA1ArtifactError, match="Run-local POSIX path"):
        ComparisonEvidenceAgent().run(context)

    assert not (project_root / "runs/run_001/artifacts").exists()


def test_invalid_evidence_is_reported_as_a1_artifact_error_with_cause(tmp_path):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    context["inputs"]["comparison_evidence"]["records"] = []

    with pytest.raises(PhaseA1ArtifactError, match="records are invalid") as captured:
        ComparisonEvidenceAgent().run(context)

    assert captured.value.__cause__ is not None
    assert not (project_root / "runs/run_001/artifacts").exists()


def test_claim_gate_rejects_source_tampering_after_manifest_commit(tmp_path):
    project_root, files, _, context = _sandbox_fixture(tmp_path)
    ComparisonEvidenceAgent().run(context)
    files["engineering_artifact"].write_bytes(
        b"inspection_id,current_value\nI001,121\n"
    )

    with pytest.raises(PhaseA1ArtifactError, match="SHA-256 does not match"):
        ClaimGateAgent().run(context)

    assert not (project_root / "runs/run_001/artifacts/claim_decision.json").exists()


def test_duplicate_manifest_json_key_is_rejected(tmp_path):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    result = ComparisonEvidenceAgent().run(context)
    manifest_path = project_root / result["comparison_evidence_manifest_path"]
    manifest_path.write_text(
        '{"schema_version":"comparison_evidence_manifest_v1",'
        '"schema_version":"comparison_evidence_manifest_v1"}',
        encoding="utf-8",
    )

    with pytest.raises(PhaseA1ArtifactError, match="duplicate key: schema_version"):
        ClaimGateAgent().run(context)


def test_report_revalidates_claim_decision_and_rejects_tampering(tmp_path):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    _, claim = _run_evidence_and_claim(context)
    decision_path = project_root / claim["claim_decision_path"]
    document = json.loads(decision_path.read_text(encoding="utf-8"))
    document["summary"]["static_audit_allowed"] = 0
    decision_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(PhaseA1ArtifactError, match="claim_decision.json is invalid"):
        ClaimAuditReportAgent().run(context)

    assert not (project_root / "runs/run_001/staging").exists()


def test_static_only_report_uses_claim_gate_qualifier_and_no_formal_publish(tmp_path):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    _run_evidence_and_claim(context)
    result = ClaimAuditReportAgent().run(context)
    report = (project_root / result["claim_audit_report_path"]).read_text(
        encoding="utf-8"
    )

    assert "当前静态面积审计：120 px²" in report
    assert "方向性变化结论：未授权" in report
    assert "不构成方向性变化结论" in report
    assert "不是正式发布物" in report
    authoritative = project_root / "runs/run_001/artifacts/claim_decision.json"
    mirror = project_root / result["claim_decision_mirror_path"]
    assert mirror.read_bytes() == authoritative.read_bytes()
    assert not (project_root / "outputs").exists()


@pytest.mark.parametrize(
    ("shared_override", "message"),
    [
        ({"execution_profile": "phase_a"}, "execution_profile"),
        ({"run_id": "../../bad"}, "run_id"),
        ({"plan_fingerprint": "bad"}, "plan_fingerprint"),
    ],
)
def test_activation_contract_fails_closed(tmp_path, shared_override, message):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    context["shared"].update(shared_override)

    with pytest.raises(PhaseA1ArtifactError, match=message):
        ComparisonEvidenceAgent().run(context)

    assert not (project_root / "runs/run_001/artifacts").exists()


def test_live_repository_root_is_rejected():
    context = {
        "shared": {
            "project_root": str(PROJECT_ROOT),
            "run_id": RUN_ID,
            "execution_profile": "phase_a1_sandbox",
            "plan_fingerprint": PLAN_FINGERPRINT,
        },
        "inputs": {
            "comparison_evidence": {
                "records": [],
                "source_artifacts": [],
            }
        },
    }

    with pytest.raises(PhaseA1ArtifactError, match="outside the live repository"):
        ComparisonEvidenceAgent().run(context)


def test_fixed_output_parent_symlink_cannot_escape_sandbox(tmp_path):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    artifacts = project_root / "runs" / RUN_ID / "artifacts"
    try:
        artifacts.symlink_to(external, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("directory symlinks are unavailable on this platform")
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(artifacts), str(external)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            pytest.skip("directory junctions are unavailable on this filesystem")
    try:
        with pytest.raises(
            PhaseA1ArtifactError,
            match="outside its fixed Run path|symbolic",
        ):
            ComparisonEvidenceAgent().run(context)
        assert not (external / "comparison_evidence.csv").exists()
    finally:
        artifacts.rmdir()


def test_agents_reject_arbitrary_output_overrides(tmp_path):
    _, _, _, context = _sandbox_fixture(tmp_path)
    context["inputs"]["comparison_evidence"]["output_path"] = "outputs/bad.csv"
    with pytest.raises(PhaseA1ArtifactError, match="exactly records and source_artifacts"):
        ComparisonEvidenceAgent().run(context)

    context["inputs"]["comparison_evidence"].pop("output_path")
    ComparisonEvidenceAgent().run(context)
    context["inputs"]["claim_gate"]["output_path"] = "outputs/bad.json"
    with pytest.raises(PhaseA1ArtifactError, match="does not accept"):
        ClaimGateAgent().run(context)


def test_claim_artifacts_loader_returns_isolated_validated_data(tmp_path):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    _run_evidence_and_claim(context)

    first = load_validated_claim_artifacts(
        project_root,
        run_id=RUN_ID,
        execution_profile="phase_a1_sandbox",
        plan_fingerprint=PLAN_FINGERPRINT,
    )
    first["records"][0]["current_value"] = 999
    second = load_validated_claim_artifacts(
        project_root,
        run_id=RUN_ID,
        execution_profile="phase_a1_sandbox",
        plan_fingerprint=PLAN_FINGERPRINT,
    )

    assert second["records"][0]["current_value"] == 120


def test_phase_a1_agents_are_not_registered_or_scheduled():
    registry = (PROJECT_ROOT / "orchestrator/registry.py").read_text(encoding="utf-8")
    dag = (PROJECT_ROOT / "config/dag.yaml").read_text(encoding="utf-8")

    assert "ComparisonEvidenceAgent" not in registry
    assert "ClaimGateAgent" not in registry
    assert "ClaimAuditReportAgent" not in registry
    assert "comparison_evidence" not in dag
    assert "claim_gate" not in dag


def test_input_records_are_not_mutated(tmp_path):
    _, _, _, context = _sandbox_fixture(tmp_path)
    original = deepcopy(context["inputs"]["comparison_evidence"]["records"])

    ComparisonEvidenceAgent().run(context)

    assert context["inputs"]["comparison_evidence"]["records"] == original
