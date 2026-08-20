from __future__ import annotations

import csv
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess

import pytest

import orchestrator.inspection_workflow.a1_artifacts as a1_artifacts
from orchestrator.agents.claim_audit_report_agent import ClaimAuditReportAgent
from orchestrator.agents.claim_gate_agent import ClaimGateAgent
from orchestrator.agents.comparison_evidence_agent import ComparisonEvidenceAgent
from orchestrator.dag.builder import build_dag
from orchestrator.inspection_workflow import (
    COMPARISON_EVIDENCE_FIELDS,
    COMPARISON_EVIDENCE_MANIFEST_SCHEMA_VERSION,
    COMPARISON_EVIDENCE_SCHEMA_VERSION,
    PhaseA1ArtifactError,
    initialize_phase_a1_sandbox,
    load_validated_claim_artifacts,
    parse_comparison_evidence_csv,
)
from conftest import (
    artifact_manifest_diff,
    artifact_snapshot,
)
from orchestrator.registry import build_default_registry


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
    initialize_phase_a1_sandbox(project_root, run_id=RUN_ID)
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


SUCCESSOR_RUN_ID = "run_002"
SOURCE_RUN_ID = RUN_ID
SUCCESSOR_MARKER_BINDINGS = {
    "source_admission_sha256": "a" * 64,
    "activation_intent_sha256": "b" * 64,
    "plan_fingerprint": "c" * 64,
}


def _initialize_successor_sandbox(project_root: Path) -> Path:
    return a1_artifacts.initialize_phase_b10_successor_a1_sandbox(
        project_root,
        successor_run_id=SUCCESSOR_RUN_ID,
        source_run_id=SOURCE_RUN_ID,
        execution_profile="phase_a1_sandbox",
        evidence_source_mode="normalized_records",
        **SUCCESSOR_MARKER_BINDINGS,
    )


def _run_evidence_and_claim(context):
    evidence_result = ComparisonEvidenceAgent().run(context)
    claim_result = ClaimGateAgent().run(context)
    return evidence_result, claim_result


def _rewrite_csv_field(data: bytes, *, field: str, raw_value: str) -> bytes:
    reader = csv.DictReader(io.StringIO(data.decode("utf-8"), newline=""))
    rows = list(reader)
    rows[0][field] = raw_value
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=list(COMPARISON_EVIDENCE_FIELDS),
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _make_verified_evidence(project_root, files, context):
    work = project_root / "runs" / RUN_ID / "work"
    memory = work / "memory.csv"
    registration = work / "registration.json"
    memory.write_bytes(b"memory_id,last_area_px\nMEM-001,100\n")
    registration.write_bytes(b'{"registration_status":"registered"}\n')
    memory_hash = _sha256(memory.read_bytes())
    registration_hash = _sha256(registration.read_bytes())
    context["inputs"]["comparison_evidence"]["source_artifacts"].extend(
        [
            {
                "role": "memory_snapshot",
                "path": memory.relative_to(project_root).as_posix(),
            },
            {
                "role": "registration_evidence",
                "path": registration.relative_to(project_root).as_posix(),
            },
        ]
    )
    context["inputs"]["comparison_evidence"]["records"][0].update(
        {
            "evidence_id": "EVD-I002-obs-01-area",
            "current_inspection_id": "I002",
            "current_image_id": "I002_000040",
            "current_observation_id": "I002::obs_01",
            "current_timestamp": "2026-07-02T10:00:00.000000Z",
            "current_comparability_status": "verified_comparable",
            "previous_entity_type": "memory_snapshot",
            "previous_memory_id": "MEM-001",
            "previous_memory_version": "2",
            "previous_last_seen_inspection": "I001",
            "previous_last_seen_timestamp": "2026-07-01T10:00:00.000000Z",
            "previous_source_inspection_ids": ["I001"],
            "previous_source_record_count": 1,
            "previous_observation_sources": ["real_inspection_mask_input"],
            "previous_comparability_status": "verified_comparable",
            "comparison_group_id": "MEM-001::inspection_level_max_mask_area_px",
            "previous_memory_snapshot_value": 100,
            "absolute_difference": 20,
            "relative_difference": "0.2",
            "association_id": "ASSOC-I002-obs-01",
            "association_status": "matched",
            "association_mode": "no_id",
            "use_disease_id_score": False,
            "association_score": "0.82",
            "match_type": "soft",
            "candidate_count": 2,
            "score_margin": "0.18",
            "association_supported_pair": True,
            "identity_evidence_state": "association_supported",
            "valid_timepoint_count": 2,
            "temporal_order_valid": True,
            "difference_valid": True,
            "relative_difference_valid": True,
            "comparison_comparability_status": "verified_comparable",
            "comparability_reason": "dual_verified_sources",
            "registration_status": "registered",
            "registration_evidence_source": "fixture_registration",
            "registration_evidence_sha256": registration_hash,
            "source_memory_snapshot_sha256": memory_hash,
            "source_association_artifact_sha256": _sha256(
                files["association_artifact"].read_bytes()
            ),
        }
    )


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
    assert manifest["source_bundle_kind"] == "normalized_records"
    assert manifest["record_count"] == 1
    assert manifest["source_validation_scope"] == "byte_binding_only"
    assert "prepared_readiness_and_history_contract" not in json.dumps(manifest)
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


@pytest.mark.parametrize("raw_value", ["+120", "0120", " 120", "-0"])
def test_evidence_csv_rejects_noncanonical_integer_encoding(tmp_path, raw_value):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    result = ComparisonEvidenceAgent().run(context)
    data = (project_root / result["comparison_evidence_path"]).read_bytes()

    with pytest.raises(PhaseA1ArtifactError, match="canonical integer"):
        parse_comparison_evidence_csv(
            _rewrite_csv_field(data, field="current_value", raw_value=raw_value)
        )


def test_evidence_csv_rejects_noncanonical_list_json(tmp_path):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    result = ComparisonEvidenceAgent().run(context)
    data = (project_root / result["comparison_evidence_path"]).read_bytes()

    with pytest.raises(PhaseA1ArtifactError, match="canonical JSON"):
        parse_comparison_evidence_csv(
            _rewrite_csv_field(
                data,
                field="previous_source_inspection_ids",
                raw_value="[ ]",
            )
        )


@pytest.mark.parametrize("raw_value", ["[NaN]", "[Infinity]", "[-Infinity]"])
def test_evidence_csv_rejects_non_finite_list_json(tmp_path, raw_value):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    result = ComparisonEvidenceAgent().run(context)
    data = (project_root / result["comparison_evidence_path"]).read_bytes()

    with pytest.raises(PhaseA1ArtifactError, match="non-finite JSON"):
        parse_comparison_evidence_csv(
            _rewrite_csv_field(
                data,
                field="previous_source_inspection_ids",
                raw_value=raw_value,
            )
        )


def test_byte_binding_only_sources_cannot_enable_verified_directional_claim(tmp_path):
    project_root, files, _, context = _sandbox_fixture(tmp_path)
    _make_verified_evidence(project_root, files, context)

    with pytest.raises(
        PhaseA1ArtifactError,
        match="verified_comparable.*trusted semantic validation receipt",
    ):
        ComparisonEvidenceAgent().run(context)

    assert not (project_root / "runs/run_001/artifacts").exists()


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
        a1_artifacts._atomic_write_idempotent(
            target,
            b"{}\n",
            allowed_root=tmp_path,
        )

    assert isinstance(captured.value.__cause__, OSError)
    assert "replace failed" in str(captured.value.__cause__)


def test_clean_first_write_failure_does_not_leave_recovery_marker_and_can_retry(
    tmp_path,
    monkeypatch,
):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    original_named_temporary_file = a1_artifacts.tempfile.NamedTemporaryFile

    def fail_before_temp_creation(*args, **kwargs):
        raise OSError("temporary create failed")

    monkeypatch.setattr(
        a1_artifacts.tempfile,
        "NamedTemporaryFile",
        fail_before_temp_creation,
    )

    with pytest.raises(PhaseA1ArtifactError, match="atomically write") as captured:
        ComparisonEvidenceAgent().run(context)

    artifacts = project_root / "runs/run_001/artifacts"
    assert isinstance(captured.value.__cause__, OSError)
    assert not (artifacts / "comparison_evidence.csv").exists()
    assert not (artifacts / "comparison_evidence_manifest.json").exists()
    assert not (artifacts / ".a1_recovery_required.json").exists()

    monkeypatch.setattr(
        a1_artifacts.tempfile,
        "NamedTemporaryFile",
        original_named_temporary_file,
    )
    result = ComparisonEvidenceAgent().run(context)
    assert (project_root / result["comparison_evidence_path"]).is_file()


def test_first_replace_failure_with_no_commit_leaves_recovery_marker(
    tmp_path,
    monkeypatch,
):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    original_replace = a1_artifacts.os.replace

    def fail_evidence_replace(source, destination):
        if Path(destination).name == "comparison_evidence.csv":
            raise OSError("evidence replace state uncertain")
        return original_replace(source, destination)

    monkeypatch.setattr(a1_artifacts.os, "replace", fail_evidence_replace)

    with pytest.raises(PhaseA1ArtifactError, match="recovery is required") as captured:
        ComparisonEvidenceAgent().run(context)

    artifacts = project_root / "runs/run_001/artifacts"
    marker = json.loads(
        (artifacts / ".a1_recovery_required.json").read_text(encoding="utf-8")
    )
    assert marker["committed_paths"] == []
    assert not (artifacts / "comparison_evidence.csv").exists()
    assert not list(artifacts.glob(".comparison_evidence.csv.*.tmp"))
    assert isinstance(captured.value.__cause__, OSError)
    assert "replace state uncertain" in str(captured.value.__cause__)


def test_pre_replace_fsync_and_cleanup_failure_requires_recovery(
    tmp_path,
    monkeypatch,
):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    original_fsync = a1_artifacts.os.fsync
    original_replace = a1_artifacts.os.replace
    original_unlink = Path.unlink
    fsync_calls = {"count": 0}
    replace_destinations: list[str] = []

    def fail_first_fsync(file_descriptor):
        fsync_calls["count"] += 1
        if fsync_calls["count"] == 1:
            raise OSError("evidence fsync failed before replace")
        return original_fsync(file_descriptor)

    def track_replace(source, destination):
        replace_destinations.append(Path(destination).name)
        return original_replace(source, destination)

    def fail_evidence_temp_cleanup(self, *args, **kwargs):
        if self.name.startswith(".comparison_evidence.csv.") and self.suffix == ".tmp":
            raise OSError("evidence temp cleanup failed")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(a1_artifacts.os, "fsync", fail_first_fsync)
    monkeypatch.setattr(a1_artifacts.os, "replace", track_replace)
    monkeypatch.setattr(Path, "unlink", fail_evidence_temp_cleanup)

    with pytest.raises(
        PhaseA1ArtifactError,
        match="temporary cleanup also failed.*recovery is required",
    ) as captured:
        ComparisonEvidenceAgent().run(context)

    artifacts = project_root / "runs/run_001/artifacts"
    marker = json.loads(
        (artifacts / ".a1_recovery_required.json").read_text(encoding="utf-8")
    )
    assert marker["committed_paths"] == []
    assert "comparison_evidence.csv" not in replace_destinations
    assert ".a1_recovery_required.json" in replace_destinations
    assert not (artifacts / "comparison_evidence.csv").exists()
    failed_temporary_files = list(
        artifacts.glob(".comparison_evidence.csv.*.tmp")
    )
    assert len(failed_temporary_files) == 1
    assert failed_temporary_files[0].is_file()
    assert isinstance(captured.value.__cause__, OSError)
    assert "evidence fsync failed before replace" in str(captured.value.__cause__)
    assert "evidence temp cleanup failed" in str(captured.value)


def test_manifest_failure_after_evidence_commit_leaves_recovery_marker(
    tmp_path,
    monkeypatch,
):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    original_write = a1_artifacts._atomic_write_idempotent

    def fail_manifest(path, data, *, allowed_root):
        if path.name == "comparison_evidence_manifest.json":
            try:
                raise OSError("manifest replace failed")
            except OSError as exc:
                raise PhaseA1ArtifactError("manifest write failed") from exc
        return original_write(path, data, allowed_root=allowed_root)

    monkeypatch.setattr(a1_artifacts, "_atomic_write_idempotent", fail_manifest)

    with pytest.raises(PhaseA1ArtifactError, match="recovery is required") as captured:
        ComparisonEvidenceAgent().run(context)

    artifacts = project_root / "runs/run_001/artifacts"
    assert (artifacts / "comparison_evidence.csv").is_file()
    assert not (artifacts / "comparison_evidence_manifest.json").exists()
    marker = json.loads(
        (artifacts / ".a1_recovery_required.json").read_text(encoding="utf-8")
    )
    assert marker["committed_paths"] == [
        "runs/run_001/artifacts/comparison_evidence.csv"
    ]
    assert isinstance(captured.value.__cause__, OSError)

    with pytest.raises(PhaseA1ArtifactError, match="recovery marker exists"):
        ComparisonEvidenceAgent().run(context)


def test_recovery_marker_directory_entry_blocks_rerun(tmp_path):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    marker = (
        project_root
        / "runs"
        / RUN_ID
        / "artifacts"
        / ".a1_recovery_required.json"
    )
    marker.mkdir(parents=True)

    with pytest.raises(PhaseA1ArtifactError, match="recovery marker exists"):
        ComparisonEvidenceAgent().run(context)


@pytest.mark.parametrize(
    "inspection_error",
    [
        PermissionError("marker permission denied"),
        OSError("marker lstat failed"),
        ValueError("marker path is invalid"),
    ],
)
def test_recovery_marker_inspection_failure_fails_closed(
    tmp_path,
    monkeypatch,
    inspection_error,
):
    project_root, _, _, _ = _sandbox_fixture(tmp_path)
    marker = (
        project_root
        / "runs"
        / RUN_ID
        / "artifacts"
        / ".a1_recovery_required.json"
    )
    real_lstat = Path.lstat

    def fail_marker_inspection(path, *args, **kwargs):
        if path == marker:
            raise inspection_error
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", fail_marker_inspection)

    with pytest.raises(
        PhaseA1ArtifactError,
        match="unable to inspect A1 artifacts recovery marker",
    ) as captured:
        a1_artifacts._reject_recovery_marker(
            project_root,
            RUN_ID,
            "artifacts",
        )
    assert captured.value.__cause__ is inspection_error


def test_broken_recovery_marker_symlink_blocks_when_supported(tmp_path):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    marker = (
        project_root
        / "runs"
        / RUN_ID
        / "artifacts"
        / ".a1_recovery_required.json"
    )
    try:
        marker.symlink_to(marker.with_name("missing-recovery-marker-target.json"))
    except (NotImplementedError, OSError):
        pytest.skip("filesystem does not permit creating a test symlink")

    assert marker.is_symlink()
    assert not marker.exists()
    with pytest.raises(PhaseA1ArtifactError, match="recovery marker exists"):
        ComparisonEvidenceAgent().run(context)


def test_claim_post_write_validation_failure_leaves_recovery_marker(
    tmp_path,
    monkeypatch,
):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    ComparisonEvidenceAgent().run(context)

    def fail_validation(*args, **kwargs):
        raise PhaseA1ArtifactError("post-write validation failed")

    monkeypatch.setattr(
        a1_artifacts,
        "load_validated_claim_artifacts",
        fail_validation,
    )

    with pytest.raises(PhaseA1ArtifactError, match="recovery is required"):
        ClaimGateAgent().run(context)

    artifacts = project_root / "runs/run_001/artifacts"
    assert (artifacts / "claim_decision.json").is_file()
    marker = json.loads(
        (artifacts / ".a1_recovery_required.json").read_text(encoding="utf-8")
    )
    assert marker["committed_paths"] == [
        "runs/run_001/artifacts/claim_decision.json"
    ]


def test_report_mirror_failure_leaves_staging_recovery_marker(
    tmp_path,
    monkeypatch,
):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    _run_evidence_and_claim(context)
    original_write = a1_artifacts._atomic_write_idempotent

    def fail_mirror(path, data, *, allowed_root):
        if path.name == "claim_decision.json" and path.parent.name == "staging":
            raise PhaseA1ArtifactError("mirror write failed")
        return original_write(path, data, allowed_root=allowed_root)

    monkeypatch.setattr(a1_artifacts, "_atomic_write_idempotent", fail_mirror)

    with pytest.raises(PhaseA1ArtifactError, match="recovery is required"):
        ClaimAuditReportAgent().run(context)

    staging = project_root / "runs/run_001/staging"
    assert (staging / "claim_audit_report.md").is_file()
    assert not (staging / "claim_decision.json").exists()
    marker = json.loads(
        (staging / ".a1_recovery_required.json").read_text(encoding="utf-8")
    )
    assert marker["committed_paths"] == [
        "runs/run_001/staging/claim_audit_report.md"
    ]


def test_report_detects_authoritative_decision_change_after_mirror_write(
    tmp_path,
    monkeypatch,
):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    _run_evidence_and_claim(context)
    ClaimAuditReportAgent().run(context)
    authoritative = project_root / "runs/run_001/artifacts/claim_decision.json"
    original_write = a1_artifacts._atomic_write_idempotent

    def mutate_authoritative_after_mirror(path, data, *, allowed_root):
        committed = original_write(path, data, allowed_root=allowed_root)
        if path.name == "claim_decision.json" and path.parent.name == "staging":
            authoritative.write_bytes(authoritative.read_bytes() + b" ")
        return committed

    monkeypatch.setattr(
        a1_artifacts,
        "_atomic_write_idempotent",
        mutate_authoritative_after_mirror,
    )

    with pytest.raises(
        PhaseA1ArtifactError,
        match="not byte-identical.*recovery is required",
    ):
        ClaimAuditReportAgent().run(context)

    staging = project_root / "runs/run_001/staging"
    assert (staging / "claim_decision.json").is_file()
    marker = json.loads(
        (staging / ".a1_recovery_required.json").read_text(encoding="utf-8")
    )
    assert marker["committed_paths"] == []


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
        f'{{"schema_version":"{COMPARISON_EVIDENCE_MANIFEST_SCHEMA_VERSION}",'
        f'"schema_version":"{COMPARISON_EVIDENCE_MANIFEST_SCHEMA_VERSION}"}}',
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
    assert "仅绑定声明来源字节" in report
    assert "未验证来源文件的业务语义" in report
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


def test_phase_a1_requires_explicit_sandbox_marker(tmp_path):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    (project_root / ".phase_a1_sandbox.json").unlink()

    with pytest.raises(PhaseA1ArtifactError, match="sandbox marker is missing"):
        ComparisonEvidenceAgent().run(context)


def test_phase_a1_legacy_root_marker_remains_the_baseline(tmp_path):
    project_root = tmp_path / "legacy"
    project_root.mkdir()
    marker_path = initialize_phase_a1_sandbox(project_root, run_id=RUN_ID)

    assert a1_artifacts.validate_phase_a1_sandbox(
        project_root,
        run_id=RUN_ID,
        execution_profile="phase_a1_sandbox",
    ) == project_root
    assert marker_path == project_root / ".phase_a1_sandbox.json"


def test_phase_b10_successor_marker_validates_for_exact_run(tmp_path):
    project_root = tmp_path / "successor"
    project_root.mkdir()

    marker_path = _initialize_successor_sandbox(project_root)

    assert marker_path == project_root / "runs" / SUCCESSOR_RUN_ID / ".phase_a1_sandbox.json"
    assert a1_artifacts.validate_phase_a1_sandbox(
        project_root,
        run_id=SUCCESSOR_RUN_ID,
        execution_profile="phase_a1_sandbox",
        source_run_id=SOURCE_RUN_ID,
        **SUCCESSOR_MARKER_BINDINGS,
    ) == project_root


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("successor_run_id", "run_003", "run binding"),
        ("source_run_id", "run_003", "source_run_id binding"),
        ("source_admission_sha256", "d" * 64, "source_admission_sha256 binding"),
        ("activation_intent_sha256", "e" * 64, "activation_intent_sha256 binding"),
        ("plan_fingerprint", "f" * 64, "plan_fingerprint binding"),
    ],
)
def test_phase_b10_rejects_wrong_run_source_or_binding(
    tmp_path,
    field,
    value,
    message,
):
    project_root = tmp_path / "wrong-binding"
    project_root.mkdir()
    marker_path = _initialize_successor_sandbox(project_root)
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker[field] = value
    marker_path.write_bytes(a1_artifacts._canonical_json_bytes(marker))

    with pytest.raises(PhaseA1ArtifactError, match=message):
        a1_artifacts.validate_phase_a1_sandbox(
            project_root,
            run_id=SUCCESSOR_RUN_ID,
            execution_profile="phase_a1_sandbox",
            source_run_id=SOURCE_RUN_ID,
            **SUCCESSOR_MARKER_BINDINGS,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("successor_run_id", "run_2", "canonical run_NNN"),
        ("source_run_id", "source", "canonical run_NNN"),
        ("source_admission_sha256", "A" * 64, "lowercase SHA-256"),
        ("activation_intent_sha256", "not-a-sha", "lowercase SHA-256"),
        ("plan_fingerprint", "0" * 63, "lowercase SHA-256"),
    ],
)
def test_phase_b10_initializer_rejects_noncanonical_bindings(
    tmp_path,
    field,
    value,
    message,
):
    project_root = tmp_path / "invalid-binding"
    project_root.mkdir()
    kwargs = {
        "successor_run_id": SUCCESSOR_RUN_ID,
        "source_run_id": SOURCE_RUN_ID,
        "execution_profile": "phase_a1_sandbox",
        "evidence_source_mode": "normalized_records",
        **SUCCESSOR_MARKER_BINDINGS,
    }
    kwargs[field] = value

    with pytest.raises(PhaseA1ArtifactError, match=message):
        a1_artifacts.initialize_phase_b10_successor_a1_sandbox(
            project_root,
            **kwargs,
        )


def test_phase_b10_conflicting_marker_fails_closed_and_same_bytes_are_idempotent(tmp_path):
    project_root = tmp_path / "conflict"
    project_root.mkdir()
    marker_path = _initialize_successor_sandbox(project_root)
    marker_bytes = marker_path.read_bytes()

    assert _initialize_successor_sandbox(project_root) == marker_path
    assert marker_path.read_bytes() == marker_bytes

    marker_path.write_bytes(marker_bytes.replace(b"run_001", b"run_003"))
    with pytest.raises(PhaseA1ArtifactError, match="overwrite changed"):
        _initialize_successor_sandbox(project_root)


def test_phase_b10_exclusive_write_does_not_replace_a_concurrent_marker(
    tmp_path,
    monkeypatch,
):
    project_root = tmp_path / "exclusive-race"
    project_root.mkdir()
    marker_path = project_root / "runs" / SUCCESSOR_RUN_ID / ".phase_a1_sandbox.json"
    concurrent_bytes = b"concurrent-marker\n"

    def create_competing_marker(_root, _relative, _data):
        marker_path.write_bytes(concurrent_bytes)
        raise FileExistsError(marker_path)

    monkeypatch.setattr(
        a1_artifacts.controlled_fs,
        "write_exclusive",
        create_competing_marker,
    )

    with pytest.raises(PhaseA1ArtifactError, match="atomically write"):
        _initialize_successor_sandbox(project_root)

    assert marker_path.read_bytes() == concurrent_bytes
    assert not list(marker_path.parent.glob("*.tmp"))


def test_phase_b10_marker_publication_rejects_same_bytes_parent_aba(
    tmp_path,
    monkeypatch,
):
    project_root = tmp_path / "marker-parent-aba"
    project_root.mkdir()
    run_dir = project_root / "runs" / SUCCESSOR_RUN_ID
    displaced = project_root / "displaced-successor"
    original_write = a1_artifacts.controlled_fs.write_exclusive
    attacked = {"done": False}

    def replace_parent_then_write(root, relative, data):
        assert run_dir.is_dir()
        run_dir.rename(displaced)
        run_dir.mkdir()
        attacked["done"] = True
        return original_write(root, relative, data)

    monkeypatch.setattr(
        a1_artifacts.controlled_fs,
        "write_exclusive",
        replace_parent_then_write,
    )

    with pytest.raises((PhaseA1ArtifactError, ValueError, OSError)):
        _initialize_successor_sandbox(project_root)

    assert attacked["done"]
    assert not (run_dir / ".phase_a1_sandbox.json").exists()
    assert not (displaced / ".phase_a1_sandbox.json").exists()


def test_phase_b10_marker_fallback_rejects_same_bytes_replaced_parent(
    tmp_path,
    monkeypatch,
):
    project_root = tmp_path / "marker-parent-same-bytes"
    project_root.mkdir()
    run_dir = project_root / "runs" / SUCCESSOR_RUN_ID
    displaced = project_root / "displaced-successor"
    original_write = a1_artifacts.controlled_fs.write_exclusive

    def replace_parent_with_same_marker(root, relative, data):
        run_dir.rename(displaced)
        run_dir.mkdir()
        (run_dir / ".phase_a1_sandbox.json").write_bytes(data)
        return original_write(root, relative, data)

    monkeypatch.setattr(
        a1_artifacts.controlled_fs,
        "write_exclusive",
        replace_parent_with_same_marker,
    )

    with pytest.raises(PhaseA1ArtifactError, match="atomically write"):
        _initialize_successor_sandbox(project_root)

    assert (run_dir / ".phase_a1_sandbox.json").is_file()
    assert not (displaced / ".phase_a1_sandbox.json").exists()


def test_phase_b10_rejects_conflicting_legacy_root_marker(tmp_path):
    project_root = tmp_path / "root-conflict"
    project_root.mkdir()
    initialize_phase_a1_sandbox(project_root, run_id="run_003")

    with pytest.raises(PhaseA1ArtifactError, match="conflicts with the legacy root marker"):
        _initialize_successor_sandbox(project_root)


def test_phase_b10_marker_is_canonical_and_legacy_root_bytes_remain_unchanged(tmp_path):
    project_root = tmp_path / "canonical"
    project_root.mkdir()
    root_marker = initialize_phase_a1_sandbox(project_root, run_id=SOURCE_RUN_ID)
    root_bytes = root_marker.read_bytes()

    marker_path = _initialize_successor_sandbox(project_root)
    marker = json.loads(marker_path.read_text(encoding="utf-8"))

    assert marker_path.read_bytes() == a1_artifacts._canonical_json_bytes(marker)
    assert marker_path.read_bytes().endswith(b"\n")
    assert root_marker.read_bytes() == root_bytes
    assert a1_artifacts.validate_phase_a1_sandbox(
        project_root,
        run_id=SUCCESSOR_RUN_ID,
        execution_profile="phase_a1_sandbox",
    ) == project_root


def test_phase_b10_rejects_same_byte_hardlinked_successor_marker(tmp_path):
    project_root = tmp_path / "hardlink-marker"
    project_root.mkdir()
    marker_path = _initialize_successor_sandbox(project_root)
    data = marker_path.read_bytes()
    backing = project_root / "marker-backing.json"
    backing.write_bytes(data)
    marker_path.unlink()
    try:
        a1_artifacts.os.link(backing, marker_path)
    except (OSError, NotImplementedError):
        pytest.skip("hard links are unavailable")

    with pytest.raises(PhaseA1ArtifactError, match="isolated regular file"):
        _initialize_successor_sandbox(project_root)
    with pytest.raises(PhaseA1ArtifactError, match="missing or unsafe"):
        a1_artifacts.validate_phase_a1_sandbox(
            project_root,
            run_id=SUCCESSOR_RUN_ID,
            execution_profile="phase_a1_sandbox",
            source_run_id=SOURCE_RUN_ID,
            **SUCCESSOR_MARKER_BINDINGS,
        )


def test_phase_b10_rejects_symlinked_successor_marker_path(tmp_path):
    project_root = tmp_path / "symlink"
    project_root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    run_dir = project_root / "runs" / SUCCESSOR_RUN_ID
    run_dir.parent.mkdir()
    try:
        run_dir.symlink_to(external, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("directory symlinks are unavailable on this platform")
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(run_dir), str(external)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            pytest.skip("directory junctions are unavailable on this filesystem")
    try:
        with pytest.raises(PhaseA1ArtifactError, match="symlink|reparse point"):
            _initialize_successor_sandbox(project_root)
    finally:
        run_dir.rmdir()


def test_phase_a1_rejects_existing_directory_outside_configured_temp_root(
    tmp_path,
    monkeypatch,
):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    configured_temp = tmp_path / "different-controlled-temp"
    configured_temp.mkdir()
    monkeypatch.setattr(
        a1_artifacts.tempfile,
        "gettempdir",
        lambda: str(configured_temp),
    )

    with pytest.raises(PhaseA1ArtifactError, match="inside the process temporary directory"):
        ComparisonEvidenceAgent().run(context)


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
            match="outside|symlink|reparse point",
        ):
            ComparisonEvidenceAgent().run(context)
        assert not (external / "comparison_evidence.csv").exists()
    finally:
        artifacts.rmdir()


def test_source_in_tree_junction_or_symlink_is_rejected(tmp_path):
    project_root, _, _, context = _sandbox_fixture(tmp_path)
    run_root = project_root / "runs" / RUN_ID
    work = run_root / "work"
    real_work = run_root / "work-real"
    work.rename(real_work)
    try:
        work.symlink_to(real_work, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("directory symlinks are unavailable on this platform")
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(work), str(real_work)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            pytest.skip("directory junctions are unavailable on this filesystem")
    try:
        with pytest.raises(PhaseA1ArtifactError, match="symlink|reparse point"):
            ComparisonEvidenceAgent().run(context)
    finally:
        work.rmdir()


def test_atomic_write_rechecks_path_after_preflight(tmp_path, monkeypatch):
    allowed_root = tmp_path / "controlled"
    parent = allowed_root / "runs/run_001/artifacts"
    parent.mkdir(parents=True)
    target = parent / "comparison_evidence.csv"
    original_preflight = a1_artifacts._preflight_idempotent_target
    state = {"preflight_complete": False}

    def mark_preflight_complete(path, data):
        original_preflight(path, data)
        state["preflight_complete"] = True

    def become_reparse_point(path):
        return state["preflight_complete"] and path == parent

    monkeypatch.setattr(
        a1_artifacts,
        "_preflight_idempotent_target",
        mark_preflight_complete,
    )
    monkeypatch.setattr(
        a1_artifacts,
        "_path_is_reparse_point",
        become_reparse_point,
    )

    with pytest.raises(PhaseA1ArtifactError, match="reparse point"):
        a1_artifacts._atomic_write_idempotent(
            target,
            b"header\n",
            allowed_root=allowed_root,
        )

    assert not target.exists()


def test_atomic_write_cleans_temp_when_final_path_recheck_fails(
    tmp_path,
    monkeypatch,
):
    allowed_root = tmp_path / "controlled"
    parent = allowed_root / "runs/run_001/artifacts"
    parent.mkdir(parents=True)
    target = parent / "comparison_evidence.csv"
    original_assert = a1_artifacts._assert_path_is_contained_and_plain
    calls = {"count": 0}

    def fail_after_temp_write(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 4:
            raise PhaseA1ArtifactError("path became a reparse point")
        return original_assert(*args, **kwargs)

    monkeypatch.setattr(
        a1_artifacts,
        "_assert_path_is_contained_and_plain",
        fail_after_temp_write,
    )

    with pytest.raises(PhaseA1ArtifactError, match="atomically write"):
        a1_artifacts._atomic_write_idempotent(
            target,
            b"header\n",
            allowed_root=allowed_root,
        )

    assert not target.exists()
    assert not list(parent.glob("*.tmp"))


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


def test_phase_a1_agents_are_registered_only_in_the_sandbox_profile():
    legacy_tasks, _ = build_dag(PROJECT_ROOT / "config/dag.yaml")
    phase_a_tasks, _ = build_dag(
        PROJECT_ROOT / "config/dag.yaml", profile="phase_a_agent_sandbox"
    )

    assert {"comparison_evidence", "claim_gate"} <= set(build_default_registry().list())
    assert all(not task_id.startswith("phase_a_") for task_id in legacy_tasks)
    assert {"phase_a_comparison_evidence", "phase_a_claim_gate"} <= set(phase_a_tasks)


def test_input_records_are_not_mutated(tmp_path):
    _, _, _, context = _sandbox_fixture(tmp_path)
    original = deepcopy(context["inputs"]["comparison_evidence"]["records"])

    ComparisonEvidenceAgent().run(context)

    assert context["inputs"]["comparison_evidence"]["records"] == original
