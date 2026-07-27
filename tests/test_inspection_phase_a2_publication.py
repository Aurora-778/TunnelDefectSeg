"""Adversarial tests for the Phase A2 Run-local publication transaction."""

from __future__ import annotations

import csv
from copy import deepcopy
import json
from pathlib import Path

from PIL import Image
import pytest

from orchestrator.agents.association_agent import AssociationAgent
from orchestrator.agents.claim_gate_agent import ClaimGateAgent
from orchestrator.agents.claim_visualization_agent import ClaimVisualizationAgent
from orchestrator.agents.comparison_evidence_agent import ComparisonEvidenceAgent
from orchestrator.agents.engineering_claim_report_agent import EngineeringClaimReportAgent
from orchestrator.agents.growth_report_agent import GrowthReportAgent
from orchestrator.agents.memory_report_agent import MemoryReportAgent
from orchestrator.inspection_workflow import initialize_phase_a1_sandbox
from orchestrator.inspection_workflow import publication
from scripts import prepare_real_inspection_pilot as preparation


RUN_ID = "run_302"
PLAN_FINGERPRINT = "b" * 64


def _fixture(tmp_path: Path) -> tuple[Path, dict]:
    root = tmp_path / "a2-publication-sandbox"
    dataset = root / "dataset"
    work = root / "runs" / RUN_ID / "work"
    (dataset / "images").mkdir(parents=True)
    (dataset / "masks").mkdir()
    Image.new("RGB", (8, 8), color=(90, 100, 110)).save(dataset / "images/a.jpg")
    mask = Image.new("L", (8, 8), color=0)
    mask.putpixel((1, 2), 255)
    mask.save(dataset / "masks/a.png")
    Image.new("RGB", (8, 8), color=(40, 50, 60)).save(dataset / "images/b.jpg")
    Image.new("L", (8, 8), color=255).save(dataset / "masks/b.png")
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
        },
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
        },
    ]
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
                "prepared_manifest_path": f"runs/{RUN_ID}/work/raw_prepared/preparation_manifest.json",
                "history_association_path": f"runs/{RUN_ID}/work/raw_history/association_records.csv",
                "history_manifest_path": f"runs/{RUN_ID}/work/raw_history/association_manifest.json",
            }
        },
    }
    ComparisonEvidenceAgent().run(context)
    ClaimGateAgent().run(context)
    report_context = {"shared": deepcopy(context["shared"]), "inputs": {}}
    GrowthReportAgent().run(report_context)
    MemoryReportAgent().run(report_context)
    EngineeringClaimReportAgent().run(report_context)
    ClaimVisualizationAgent().run(report_context)
    return root, report_context


def _publish(root: Path) -> dict:
    return publication.publish_run_local_artifacts(
        root,
        run_id=RUN_ID,
        plan_fingerprint=PLAN_FINGERPRINT,
    )


def _marker(root: Path) -> dict:
    return json.loads(
        (root / f"runs/{RUN_ID}/.publication_recovery_required.json").read_text(
            encoding="utf-8"
        )
    )


def test_a2_publication_commits_final_files_and_manifest_last(tmp_path):
    root, _ = _fixture(tmp_path)
    result = _publish(root)

    assert result["idempotent"] is False
    manifest_path = root / publication.PUBLICATION_MANIFEST_PATH
    assert manifest_path.is_file()
    manifest = result["manifest"]
    publication_paths = {item["path"] for item in manifest["publication_files"]}
    assert publication_paths == {
        "outputs/final_project_report.md",
        "outputs/system_summary.md",
        "outputs/key_insights.md",
        f"runs/{RUN_ID}/final_summary.md",
    }
    source_paths = [item["path"] for item in manifest["source_artifacts"]]
    assert source_paths == sorted(source_paths)
    assert f"runs/{RUN_ID}/final_summary.md" in source_paths
    assert any("/visualizations/" in path for path in source_paths)
    assert all(not Path(path).is_absolute() and "\\" not in path for path in source_paths)
    for item in [*manifest["source_artifacts"], *manifest["publication_files"]]:
        data = (root / item["path"]).read_bytes()
        assert item["size_bytes"] == len(data)
        assert item["sha256"] == publication._sha256(data)
    assert all(
        type(item["existed_before"]) is bool
        for item in manifest["publication_files"]
    )
    final_report = (root / "outputs/final_project_report.md").read_text(encoding="utf-8")
    assert "byte_binding_only" in final_report
    assert "不构成方向性变化结论" in final_report
    assert "growth_trend" not in final_report
    assert "area_growth_rate" not in final_report
    for relative in (
        "outputs/final_project_report.md",
        "outputs/system_summary.md",
        "outputs/key_insights.md",
        f"runs/{RUN_ID}/final_summary.md",
    ):
        assert publication._BOUNDARY_QUALIFIER in (
            root / relative
        ).read_text(encoding="utf-8")
    transaction = result["transaction"]
    assert transaction["phase"] == "cleanup_complete"
    assert transaction["committed_paths"][-1] == publication.PUBLICATION_MANIFEST_PATH
    assert not (root / transaction["backup_dir"]).exists()
    assert not (root / transaction["temporary_dir"]).exists()
    assert not (root / "data/simulated").exists()


def test_publication_manifest_is_last_formal_replace(tmp_path, monkeypatch):
    root, _ = _fixture(tmp_path)
    original = publication._atomic_replace
    formal_order: list[str] = []
    staged_final_summary_seen = {"value": False}

    def track(path, data, *, root, label):
        relative = path.relative_to(root).as_posix()
        if label == "publication staged final runs/run_302/final_summary.md":
            staged_final_summary_seen["value"] = True
        if relative in {
            publication.PUBLICATION_MANIFEST_PATH,
            "outputs/final_project_report.md",
            "outputs/system_summary.md",
            "outputs/key_insights.md",
            f"runs/{RUN_ID}/final_summary.md",
        }:
            formal_order.append(relative)
        return original(path, data, root=root, label=label)

    monkeypatch.setattr(publication, "_atomic_replace", track)
    _publish(root)
    assert formal_order[-1] == publication.PUBLICATION_MANIFEST_PATH
    assert set(formal_order[:-1]) == {
        "outputs/final_project_report.md",
        "outputs/system_summary.md",
        "outputs/key_insights.md",
        f"runs/{RUN_ID}/final_summary.md",
    }
    assert staged_final_summary_seen["value"] is True


def test_a2_publication_is_idempotent_for_identical_run(tmp_path):
    root, _ = _fixture(tmp_path)
    first = _publish(root)
    before = {
        item["path"]: (root / item["path"]).read_bytes()
        for item in first["manifest"]["publication_files"]
    }
    second = _publish(root)

    assert second["idempotent"] is True
    assert second["manifest"]["transaction_id"] == first["manifest"]["transaction_id"]
    assert before == {path: (root / path).read_bytes() for path in before}


def test_identical_preexisting_final_targets_are_retained_and_recorded(tmp_path):
    root, _ = _fixture(tmp_path)
    first = _publish(root)
    (root / publication.PUBLICATION_MANIFEST_PATH).unlink()
    (root / f"runs/{RUN_ID}/publication_transaction.json").unlink()

    second = _publish(root)
    records = {
        item["path"]: item
        for item in second["transaction"]["target_records"]
    }
    assert records["outputs/final_project_report.md"]["existed_before"] is True
    assert records[f"runs/{RUN_ID}/final_summary.md"]["existed_before"] is True
    assert second["manifest"]["transaction_id"] == first["manifest"]["transaction_id"]


def test_missing_required_staging_file_fails_without_publication(tmp_path):
    root, _ = _fixture(tmp_path)
    (root / f"runs/{RUN_ID}/staging/memory_agent_report.md").unlink()

    with pytest.raises(publication.PublicationTransactionError, match="staging is incomplete"):
        _publish(root)

    assert not (root / publication.PUBLICATION_MANIFEST_PATH).exists()
    assert not (root / f"runs/{RUN_ID}/publication_transaction.json").exists()


def test_existing_changed_target_fails_closed_before_transaction(tmp_path):
    root, _ = _fixture(tmp_path)
    target = root / "outputs/final_project_report.md"
    target.parent.mkdir(parents=True)
    target.write_text("old publication\n", encoding="utf-8")

    with pytest.raises(publication.PublicationTransactionError, match="refusing to overwrite"):
        _publish(root)

    assert target.read_text(encoding="utf-8") == "old publication\n"
    assert not (root / f"runs/{RUN_ID}/publication_transaction.json").exists()


def test_claim_decision_tampering_fails_before_publication(tmp_path):
    root, _ = _fixture(tmp_path)
    decision = root / f"runs/{RUN_ID}/artifacts/claim_decision.json"
    decision.write_bytes(decision.read_bytes() + b" ")

    with pytest.raises(publication.PublicationTransactionError):
        _publish(root)

    assert not (root / publication.PUBLICATION_MANIFEST_PATH).exists()


def test_unvalidated_legacy_direction_field_in_staging_is_rejected(tmp_path):
    root, _ = _fixture(tmp_path)
    target = root / f"runs/{RUN_ID}/staging/visualization_summary.md"
    target.write_text(target.read_text(encoding="utf-8") + "growth_trend=明显增长\n", encoding="utf-8")

    with pytest.raises(publication.PublicationTransactionError, match="legacy field growth_trend"):
        _publish(root)
    assert not (root / publication.PUBLICATION_MANIFEST_PATH).exists()


def test_empty_visualization_set_is_rejected(tmp_path):
    root, _ = _fixture(tmp_path)
    for path in (root / f"runs/{RUN_ID}/staging/visualizations").iterdir():
        path.unlink()

    with pytest.raises(publication.PublicationTransactionError, match="visualizations are incomplete"):
        _publish(root)
    assert not (root / f"runs/{RUN_ID}/publication_transaction.json").exists()


def test_partial_final_commit_rolls_back_and_records_actual_paths(tmp_path, monkeypatch):
    root, _ = _fixture(tmp_path)
    original = publication._atomic_replace

    def fail_system_summary(path, data, *, root, label):
        if label == "publication target outputs/system_summary.md":
            raise publication.PublicationTransactionError("injected final write failure")
        return original(path, data, root=root, label=label)

    monkeypatch.setattr(publication, "_atomic_replace", fail_system_summary)
    with pytest.raises(publication.PublicationTransactionError, match="injected final write failure"):
        _publish(root)

    marker = _marker(root)
    assert marker["stage"] == "publication_partial_commit"
    assert marker["committed_paths"] == [
        "outputs/final_project_report.md",
        "outputs/key_insights.md",
    ]
    assert (root / "outputs/final_project_report.md").is_file()
    assert (root / "outputs/key_insights.md").is_file()
    assert not (root / "outputs/system_summary.md").exists()
    assert not (root / publication.PUBLICATION_MANIFEST_PATH).exists()

    recovered = publication.recover_publication(
        root,
        run_id=RUN_ID,
        plan_fingerprint=PLAN_FINGERPRINT,
    )
    assert recovered["rolled_back"] is True
    assert not (root / "outputs/final_project_report.md").exists()
    assert not (root / "outputs/key_insights.md").exists()
    assert not (root / f"runs/{RUN_ID}/.publication_recovery_required.json").exists()
    assert not (root / f"runs/{RUN_ID}/publication_transaction.json").exists()


def test_failed_final_summary_is_isolated_with_transaction_scoped_name(tmp_path, monkeypatch):
    root, _ = _fixture(tmp_path)
    original = publication._atomic_replace

    def fail_after_final_summary(path, data, *, root, label):
        if path.name == "current_publication_manifest.json":
            raise publication.PublicationTransactionError("Manifest replace failed after final summary")
        return original(path, data, root=root, label=label)

    monkeypatch.setattr(publication, "_atomic_replace", fail_after_final_summary)
    with pytest.raises(publication.PublicationTransactionError, match="Manifest replace failed"):
        _publish(root)

    marker = _marker(root)
    assert marker["invalidated_final_summary_path"] is None
    assert (root / f"runs/{RUN_ID}/final_summary.md").is_file()
    monkeypatch.setattr(publication, "_atomic_replace", original)
    recovered = publication.recover_publication(
        root,
        run_id=RUN_ID,
        plan_fingerprint=PLAN_FINGERPRINT,
    )
    invalidated = recovered["invalidated_final_summary_path"]
    assert invalidated.startswith(
        f"runs/{RUN_ID}/publication_recovery/invalidated_final_summary.pub_"
    )
    assert (root / invalidated).is_file()
    assert not (root / f"runs/{RUN_ID}/final_summary.md").exists()
    assert invalidated not in marker["committed_paths"]


def test_clean_zero_commit_failure_removes_transaction_and_can_retry(tmp_path, monkeypatch):
    root, _ = _fixture(tmp_path)
    original = publication._atomic_replace
    failed = {"value": False}

    def fail_first_final(path, data, *, root, label):
        if label == "publication target outputs/final_project_report.md" and not failed["value"]:
            failed["value"] = True
            raise publication.PublicationTransactionError("clean zero-commit failure")
        return original(path, data, root=root, label=label)

    monkeypatch.setattr(publication, "_atomic_replace", fail_first_final)
    with pytest.raises(publication.PublicationTransactionError, match="clean zero-commit failure"):
        _publish(root)
    assert not (root / f"runs/{RUN_ID}/.publication_recovery_required.json").exists()
    assert not (root / f"runs/{RUN_ID}/publication_transaction.json").exists()

    monkeypatch.setattr(publication, "_atomic_replace", original)
    assert _publish(root)["manifest"]["run_id"] == RUN_ID


def test_preexisting_transaction_workspace_residue_is_never_reused(tmp_path, monkeypatch):
    root, _ = _fixture(tmp_path)
    monkeypatch.setattr(
        publication,
        "_transaction_id",
        lambda run_id, plan_fingerprint, entries: "pub_residue",
    )
    residue = root / f"runs/{RUN_ID}/publication_backup/pub_residue"
    residue.mkdir(parents=True)
    (residue / "manual-note.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(publication.PublicationTransactionError, match="residue exists"):
        _publish(root)

    assert residue.is_dir()
    assert (residue / "manual-note.txt").read_text(encoding="utf-8") == "keep"


def test_manifest_phase_update_failure_keeps_valid_publication_for_recovery(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original = publication._write_transaction
    failed = {"value": False}

    def fail_manifest_committed(project_root, relative, document):
        if document["phase"] == "manifest_committed" and not failed["value"]:
            failed["value"] = True
            raise publication.PublicationTransactionError("phase update failed")
        return original(project_root, relative, document)

    monkeypatch.setattr(publication, "_write_transaction", fail_manifest_committed)
    with pytest.raises(publication.PublicationTransactionError, match="phase update failed"):
        _publish(root)

    marker = _marker(root)
    assert marker["stage"] == "manifest_commit_recovery"
    assert (root / publication.PUBLICATION_MANIFEST_PATH).is_file()
    monkeypatch.setattr(publication, "_write_transaction", original)
    recovered = publication.recover_publication(
        root,
        run_id=RUN_ID,
        plan_fingerprint=PLAN_FINGERPRINT,
    )
    assert recovered["recovered"] is True
    assert recovered["transaction"]["phase"] == "cleanup_complete"
    assert not (root / f"runs/{RUN_ID}/.publication_recovery_required.json").exists()


def test_cleanup_failure_keeps_manifest_and_requires_cleanup_only_recovery(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original = publication._cleanup_transaction
    failed = {"value": False}

    def fail_once(project_root, transaction):
        if not failed["value"]:
            failed["value"] = True
            raise publication.PublicationTransactionError("cleanup failed")
        return original(project_root, transaction)

    monkeypatch.setattr(publication, "_cleanup_transaction", fail_once)
    with pytest.raises(publication.PublicationTransactionError, match="cleanup requires recovery"):
        _publish(root)
    assert (root / publication.PUBLICATION_MANIFEST_PATH).is_file()
    assert _marker(root)["stage"] == "publication_cleanup"

    recovered = publication.recover_publication(
        root,
        run_id=RUN_ID,
        plan_fingerprint=PLAN_FINGERPRINT,
    )
    assert recovered["transaction"]["phase"] == "cleanup_complete"


def test_recovery_sentinel_directory_blocks_publication(tmp_path):
    root, _ = _fixture(tmp_path)
    marker = root / f"runs/{RUN_ID}/.publication_recovery_required.json"
    marker.mkdir()

    with pytest.raises(publication.PublicationTransactionError, match="recovery marker exists"):
        _publish(root)


def test_recovery_marker_lstat_failure_fails_closed(tmp_path, monkeypatch):
    root, _ = _fixture(tmp_path)
    marker = root / f"runs/{RUN_ID}/.publication_recovery_required.json"
    original = Path.lstat

    def fail_marker_lstat(self):
        if self == marker:
            raise PermissionError("marker inspection denied")
        return original(self)

    monkeypatch.setattr(Path, "lstat", fail_marker_lstat)
    with pytest.raises(publication.PublicationTransactionError, match="unable to inspect") as caught:
        _publish(root)
    assert isinstance(caught.value.__cause__, PermissionError)


def test_zero_commit_replace_uncertainty_creates_recovery_marker(tmp_path, monkeypatch):
    root, _ = _fixture(tmp_path)
    original = publication.os.replace

    def fail_first_formal(source, target):
        if Path(target) == root / "outputs/final_project_report.md":
            raise OSError("replace result unknown")
        return original(source, target)

    monkeypatch.setattr(publication.os, "replace", fail_first_formal)
    with pytest.raises(publication.PublicationTransactionError):
        _publish(root)
    marker = _marker(root)
    assert marker["stage"] == "publication_write_uncertain"
    assert marker["committed_paths"] == []
    assert marker["uncertain_paths"] == ["outputs/final_project_report.md"]
    assert not (root / publication.PUBLICATION_MANIFEST_PATH).exists()


def test_publication_validation_rejects_source_change_after_commit(tmp_path):
    root, _ = _fixture(tmp_path)
    _publish(root)
    source = root / f"runs/{RUN_ID}/work/frame_records.csv"
    source.write_bytes(source.read_bytes() + b"changed")

    with pytest.raises(publication.PublicationTransactionError):
        publication.validate_publication(
            root,
            run_id=RUN_ID,
            plan_fingerprint=PLAN_FINGERPRINT,
        )


def test_missing_memory_snapshot_source_fails_closed(tmp_path):
    root, _ = _fixture(tmp_path)
    manifest = json.loads(
        (root / f"runs/{RUN_ID}/artifacts/comparison_evidence_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    source = next(
        item
        for item in manifest["source_artifacts"]
        if item["role"] == "history_memory_context"
    )
    (root / source["path"]).unlink()

    with pytest.raises(publication.PublicationTransactionError, match="A1 publication inputs are invalid"):
        _publish(root)
    assert not (root / publication.PUBLICATION_MANIFEST_PATH).exists()


def test_manifest_semantic_tampering_is_rejected_even_when_json_is_canonical(tmp_path):
    root, _ = _fixture(tmp_path)
    _publish(root)
    manifest_path = root / publication.PUBLICATION_MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_validation_scope"] = "source_authenticated"
    manifest_path.write_bytes(
        (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    )

    with pytest.raises(publication.PublicationTransactionError, match="source_validation_scope|manifest"):
        publication.validate_publication(
            root,
            run_id=RUN_ID,
            plan_fingerprint=PLAN_FINGERPRINT,
        )


def test_staging_source_change_during_commit_creates_recovery_marker(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original = publication._snapshot_staging
    calls = {"count": 0}

    def mutate_on_second_snapshot(project_root, run_id):
        calls["count"] += 1
        if calls["count"] == 2:
            target = root / f"runs/{RUN_ID}/staging/visualization_summary.md"
            target.write_bytes(target.read_bytes() + b"changed")
        return original(project_root, run_id)

    monkeypatch.setattr(publication, "_snapshot_staging", mutate_on_second_snapshot)
    with pytest.raises(publication.PublicationTransactionError):
        _publish(root)
    assert _marker(root)["stage"] == "publication_partial_commit"
    assert not (root / publication.PUBLICATION_MANIFEST_PATH).exists()


def test_staging_free_text_must_match_controlled_renderer(tmp_path):
    root, _ = _fixture(tmp_path)
    target = root / f"runs/{RUN_ID}/staging/visualization_summary.md"
    target.write_text(
        target.read_text(encoding="utf-8") + "\n该病害持续恶化。\n",
        encoding="utf-8",
    )

    with pytest.raises(
        publication.PublicationTransactionError,
        match="controlled renderer output",
    ):
        _publish(root)
    assert not (root / publication.PUBLICATION_MANIFEST_PATH).exists()
    assert not (root / "outputs/final_project_report.md").exists()


def test_replace_success_then_error_is_recovered_from_actual_target_hash(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original = publication.os.replace
    failed = {"value": False}
    target = root / "outputs/system_summary.md"

    def replace_then_fail(source, destination):
        result = original(source, destination)
        if Path(destination) == target and not failed["value"]:
            failed["value"] = True
            raise OSError("replace completed before injected error")
        return result

    monkeypatch.setattr(publication.os, "replace", replace_then_fail)
    with pytest.raises(publication.PublicationTransactionError):
        _publish(root)
    marker = _marker(root)
    assert marker["stage"] == "publication_write_uncertain"
    assert marker["uncertain_paths"] == ["outputs/system_summary.md"]
    assert target.is_file()

    monkeypatch.setattr(publication.os, "replace", original)
    recovered = publication.recover_publication(
        root,
        run_id=RUN_ID,
        plan_fingerprint=PLAN_FINGERPRINT,
    )
    assert recovered["rolled_back"] is True
    assert not (root / "outputs/system_summary.md").exists()
    assert not (root / f"runs/{RUN_ID}/publication_transaction.json").exists()


def test_manifest_replace_success_then_error_catches_up_without_rollback(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original = publication.os.replace
    failed = {"value": False}
    manifest_path = root / publication.PUBLICATION_MANIFEST_PATH

    def replace_then_fail(source, destination):
        result = original(source, destination)
        if Path(destination) == manifest_path and not failed["value"]:
            failed["value"] = True
            raise OSError("Manifest replace completed before injected error")
        return result

    monkeypatch.setattr(publication.os, "replace", replace_then_fail)
    with pytest.raises(publication.PublicationTransactionError):
        _publish(root)
    assert manifest_path.is_file()
    assert _marker(root)["uncertain_paths"] == [
        publication.PUBLICATION_MANIFEST_PATH
    ]

    monkeypatch.setattr(publication.os, "replace", original)
    recovered = publication.recover_publication(
        root,
        run_id=RUN_ID,
        plan_fingerprint=PLAN_FINGERPRINT,
    )
    assert recovered["recovered"] is True
    assert manifest_path.is_file()
    assert (root / "outputs/final_project_report.md").is_file()


def test_recovery_rejects_transaction_target_outside_fixed_publication_set(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original = publication._atomic_replace

    def fail_system_summary(path, data, *, root, label):
        if label == "publication target outputs/system_summary.md":
            raise publication.PublicationTransactionError("partial publication")
        return original(path, data, root=root, label=label)

    monkeypatch.setattr(publication, "_atomic_replace", fail_system_summary)
    with pytest.raises(publication.PublicationTransactionError):
        _publish(root)
    victim = root / f"runs/{RUN_ID}/work/frame_records.csv"
    victim_bytes = victim.read_bytes()
    transaction_path = root / f"runs/{RUN_ID}/publication_transaction.json"
    transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
    transaction["target_records"][0]["path"] = (
        f"runs/{RUN_ID}/work/frame_records.csv"
    )
    transaction["committed_paths"] = [f"runs/{RUN_ID}/work/frame_records.csv"]
    transaction_path.write_bytes(publication._canonical_json_bytes(transaction))

    with pytest.raises(
        publication.PublicationTransactionError,
        match="target set is not fixed",
    ):
        publication.recover_publication(
            root,
            run_id=RUN_ID,
            plan_fingerprint=PLAN_FINGERPRINT,
        )
    assert victim.read_bytes() == victim_bytes


def test_invalidated_summary_is_not_reingested_on_retry(tmp_path, monkeypatch):
    root, _ = _fixture(tmp_path)
    original = publication._atomic_replace

    def fail_manifest(path, data, *, root, label):
        if path.name == "current_publication_manifest.json":
            raise publication.PublicationTransactionError("Manifest failure")
        return original(path, data, root=root, label=label)

    monkeypatch.setattr(publication, "_atomic_replace", fail_manifest)
    with pytest.raises(publication.PublicationTransactionError):
        _publish(root)
    assert _marker(root)["invalidated_final_summary_path"] is None

    monkeypatch.setattr(publication, "_atomic_replace", original)
    recovered = publication.recover_publication(
        root,
        run_id=RUN_ID,
        plan_fingerprint=PLAN_FINGERPRINT,
    )
    invalidated_relative = recovered["invalidated_final_summary_path"]
    invalidated = root / invalidated_relative
    assert invalidated.is_file()
    result = _publish(root)
    final_report = (
        root / "outputs/final_project_report.md"
    ).read_text(encoding="utf-8")
    assert "invalidated_final_summary" not in final_report
    assert invalidated_relative not in {
        item["path"] for item in result["manifest"]["source_artifacts"]
    }


def test_any_transaction_workspace_residue_blocks_idempotent_return(tmp_path):
    root, _ = _fixture(tmp_path)
    _publish(root)
    residue = root / f"runs/{RUN_ID}/publication_backup/pub_dead"
    residue.mkdir(parents=True)
    (residue / "manual.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(
        publication.PublicationTransactionError,
        match="backup residue",
    ):
        _publish(root)
    assert (residue / "manual.txt").read_text(encoding="utf-8") == "keep"


def test_cleanup_and_cleanup_state_failure_preserve_primary_cause(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original_write = publication._write_transaction

    def fail_cleanup(project_root, transaction):
        raise publication.PublicationTransactionError("cleanup-io")

    def fail_cleanup_state(project_root, relative, document):
        if document["phase"] == "cleanup_pending":
            raise publication.PublicationTransactionError("state-io")
        return original_write(project_root, relative, document)

    monkeypatch.setattr(publication, "_cleanup_transaction", fail_cleanup)
    monkeypatch.setattr(publication, "_write_transaction", fail_cleanup_state)
    with pytest.raises(
        publication.PublicationTransactionError,
        match="cleanup requires recovery",
    ) as caught:
        _publish(root)
    assert isinstance(caught.value.__cause__, publication.PublicationTransactionError)
    assert str(caught.value.__cause__) == "cleanup-io"
    marker = _marker(root)
    assert marker["primary_error"] == "cleanup-io"
    assert "state-io" in marker["cleanup_error"]


def test_case_variant_temporary_residue_is_preserved(tmp_path):
    root, _ = _fixture(tmp_path)
    residue = root / f"runs/{RUN_ID}/.Publication_dead.tmp"
    residue.mkdir()
    note = residue / "manual.txt"
    note.write_text("keep", encoding="utf-8")

    with pytest.raises(
        publication.PublicationTransactionError,
        match="temporary residue",
    ):
        _publish(root)
    assert note.read_text(encoding="utf-8") == "keep"


def test_residue_created_after_scan_is_not_cleaned_without_ownership(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    transaction_id = "pub_" + "a" * 24
    monkeypatch.setattr(
        publication,
        "_transaction_id",
        lambda run_id, plan_fingerprint, entries: transaction_id,
    )
    backup = root / f"runs/{RUN_ID}/publication_backup/{transaction_id}"
    temporary = root / f"runs/{RUN_ID}/.publication_{transaction_id}.tmp"
    original_mkdir = Path.mkdir
    injected = {"value": False}

    def create_conflict_after_scan(self, *args, **kwargs):
        result = original_mkdir(self, *args, **kwargs)
        if self == backup and not injected["value"]:
            injected["value"] = True
            original_mkdir(temporary)
            (temporary / "manual.txt").write_text("keep", encoding="utf-8")
        return result

    monkeypatch.setattr(Path, "mkdir", create_conflict_after_scan)
    with pytest.raises(FileExistsError):
        _publish(root)
    assert not backup.exists()
    assert (temporary / "manual.txt").read_text(encoding="utf-8") == "keep"


def test_partial_failure_never_deletes_concurrently_changed_target(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original = publication._atomic_replace
    target = root / "outputs/final_project_report.md"

    def change_then_fail(path, data, *, root, label):
        if label == "publication target outputs/final_project_report.md":
            result = original(path, data, root=root, label=label)
            path.write_text("external writer\n", encoding="utf-8")
            return result
        if label == "publication target outputs/system_summary.md":
            raise publication.PublicationTransactionError("later failure")
        return original(path, data, root=root, label=label)

    monkeypatch.setattr(publication, "_atomic_replace", change_then_fail)
    with pytest.raises(publication.PublicationTransactionError, match="later failure"):
        _publish(root)
    assert target.read_text(encoding="utf-8") == "external writer\n"

    with pytest.raises(
        publication.PublicationTransactionError,
        match="neither old nor new",
    ):
        publication.recover_publication(
            root,
            run_id=RUN_ID,
            plan_fingerprint=PLAN_FINGERPRINT,
        )
    assert target.read_text(encoding="utf-8") == "external writer\n"


def test_repeated_manifest_failure_reuses_matching_invalidated_summary(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original = publication._atomic_replace

    def fail_manifest(path, data, *, root, label):
        if path.name == "current_publication_manifest.json":
            raise publication.PublicationTransactionError("Manifest failure")
        return original(path, data, root=root, label=label)

    monkeypatch.setattr(publication, "_atomic_replace", fail_manifest)
    with pytest.raises(publication.PublicationTransactionError):
        _publish(root)
    monkeypatch.setattr(publication, "_atomic_replace", original)
    first = publication.recover_publication(
        root,
        run_id=RUN_ID,
        plan_fingerprint=PLAN_FINGERPRINT,
    )
    invalidated = root / first["invalidated_final_summary_path"]
    invalidated_bytes = invalidated.read_bytes()

    monkeypatch.setattr(publication, "_atomic_replace", fail_manifest)
    with pytest.raises(publication.PublicationTransactionError):
        _publish(root)
    monkeypatch.setattr(publication, "_atomic_replace", original)
    second = publication.recover_publication(
        root,
        run_id=RUN_ID,
        plan_fingerprint=PLAN_FINGERPRINT,
    )
    assert second["invalidated_final_summary_path"] == first[
        "invalidated_final_summary_path"
    ]
    assert invalidated.read_bytes() == invalidated_bytes
    assert not (root / f"runs/{RUN_ID}/final_summary.md").exists()


def test_final_summary_isolation_replace_success_then_error_is_idempotent(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original_atomic = publication._atomic_replace

    def fail_manifest(path, data, *, root, label):
        if path.name == "current_publication_manifest.json":
            raise publication.PublicationTransactionError("Manifest failure")
        return original_atomic(path, data, root=root, label=label)

    monkeypatch.setattr(publication, "_atomic_replace", fail_manifest)
    with pytest.raises(publication.PublicationTransactionError):
        _publish(root)
    monkeypatch.setattr(publication, "_atomic_replace", original_atomic)

    original_replace = publication.os.replace
    failed = {"value": False}

    def isolate_then_fail(source, destination):
        result = original_replace(source, destination)
        if (
            "invalidated_final_summary." in Path(destination).name
            and not failed["value"]
        ):
            failed["value"] = True
            raise OSError("isolation result unknown")
        return result

    monkeypatch.setattr(publication.os, "replace", isolate_then_fail)
    recovered = publication.recover_publication(
        root,
        run_id=RUN_ID,
        plan_fingerprint=PLAN_FINGERPRINT,
    )
    assert recovered["rolled_back"] is True
    assert (root / recovered["invalidated_final_summary_path"]).is_file()


def test_same_hash_unattempted_target_is_not_treated_as_owned(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original = publication._atomic_replace
    target = root / "outputs/system_summary.md"

    def external_same_bytes_then_fail(path, data, *, root, label):
        if label == "publication target outputs/system_summary.md":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            raise publication.PublicationTransactionError(
                "replace was not attempted"
            )
        return original(path, data, root=root, label=label)

    monkeypatch.setattr(
        publication,
        "_atomic_replace",
        external_same_bytes_then_fail,
    )
    with pytest.raises(
        publication.PublicationTransactionError,
        match="replace was not attempted",
    ):
        _publish(root)
    expected = target.read_bytes()

    with pytest.raises(
        publication.PublicationTransactionError,
        match="lack transaction ownership",
    ):
        publication.recover_publication(
            root,
            run_id=RUN_ID,
            plan_fingerprint=PLAN_FINGERPRINT,
        )
    assert target.read_bytes() == expected


def test_markerless_active_target_crash_fails_closed(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original = publication._atomic_replace
    target = root / "outputs/final_project_report.md"

    def replace_then_exit(path, data, *, root, label):
        result = original(path, data, root=root, label=label)
        if path == target:
            raise KeyboardInterrupt("simulated process exit")
        return result

    monkeypatch.setattr(publication, "_atomic_replace", replace_then_exit)
    with pytest.raises(KeyboardInterrupt):
        _publish(root)
    assert target.is_file()
    assert not (
        root / f"runs/{RUN_ID}/.publication_recovery_required.json"
    ).exists()

    monkeypatch.setattr(publication, "_atomic_replace", original)
    with pytest.raises(
        publication.PublicationTransactionError,
        match="lack transaction ownership",
    ):
        publication.recover_publication(
            root,
            run_id=RUN_ID,
            plan_fingerprint=PLAN_FINGERPRINT,
        )
    assert target.is_file()
    assert (root / f"runs/{RUN_ID}/publication_transaction.json").is_file()


def test_markerless_crash_after_durable_target_commit_can_recover(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original = publication._write_transaction
    crashed = {"value": False}

    def durable_write_then_exit(project_root, relative, document):
        result = original(project_root, relative, document)
        if (
            not crashed["value"]
            and document["phase"] == "publishing"
            and document["active_target_path"] is None
            and document["committed_paths"] == ["outputs/final_project_report.md"]
        ):
            crashed["value"] = True
            raise KeyboardInterrupt("simulated process exit after durable commit")
        return result

    monkeypatch.setattr(publication, "_write_transaction", durable_write_then_exit)
    with pytest.raises(KeyboardInterrupt):
        _publish(root)
    target = root / "outputs/final_project_report.md"
    assert target.is_file()
    monkeypatch.setattr(publication, "_write_transaction", original)

    recovered = publication.recover_publication(
        root,
        run_id=RUN_ID,
        plan_fingerprint=PLAN_FINGERPRINT,
    )
    assert recovered["rolled_back"] is True
    assert not target.exists()
    quarantine = (
        root
        / f"runs/{RUN_ID}/publication_recovery/"
        f"rollback_quarantine.{recovered['transaction_id']}/final_project_report.md"
    )
    assert quarantine.is_file()


def test_markerless_crash_after_durable_manifest_commit_catches_up_cleanup(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original = publication._write_transaction
    manifest = root / publication.PUBLICATION_MANIFEST_PATH
    crashed = {"value": False}

    def commit_then_exit(project_root, relative, document):
        result = original(project_root, relative, document)
        if not crashed["value"] and document["phase"] == "manifest_committed":
            crashed["value"] = True
            raise KeyboardInterrupt("simulated Manifest process exit")
        return result

    monkeypatch.setattr(publication, "_write_transaction", commit_then_exit)
    with pytest.raises(KeyboardInterrupt):
        _publish(root)
    assert manifest.is_file()
    assert not (
        root / f"runs/{RUN_ID}/.publication_recovery_required.json"
    ).exists()

    monkeypatch.setattr(publication, "_write_transaction", original)
    recovered = publication.recover_publication(
        root,
        run_id=RUN_ID,
        plan_fingerprint=PLAN_FINGERPRINT,
    )
    assert recovered["recovered"] is True
    assert recovered["transaction"]["phase"] == "cleanup_complete"


def test_markerless_manifest_replace_before_phase_write_fails_closed(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original = publication._atomic_replace
    manifest = root / publication.PUBLICATION_MANIFEST_PATH

    def replace_manifest_then_exit(path, data, *, root, label):
        result = original(path, data, root=root, label=label)
        if path == manifest:
            raise KeyboardInterrupt(
                "simulated process exit after Manifest replace before phase write"
            )
        return result

    monkeypatch.setattr(
        publication,
        "_atomic_replace",
        replace_manifest_then_exit,
    )
    with pytest.raises(KeyboardInterrupt):
        _publish(root)
    assert manifest.is_file()
    assert not (
        root / f"runs/{RUN_ID}/.publication_recovery_required.json"
    ).exists()

    monkeypatch.setattr(publication, "_atomic_replace", original)
    with pytest.raises(
        publication.PublicationTransactionError,
        match="lack transaction ownership",
    ):
        publication.recover_publication(
            root,
            run_id=RUN_ID,
            plan_fingerprint=PLAN_FINGERPRINT,
        )
    assert manifest.is_file()


def test_transaction_initialization_failure_preserves_unowned_transaction(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    transaction_path = root / f"runs/{RUN_ID}/publication_transaction.json"

    original = publication._create_transaction

    def external_transaction_then_fail(project_root, relative, document):
        transaction_path.write_text("external transaction\n", encoding="utf-8")
        return original(project_root, relative, document)

    monkeypatch.setattr(
        publication,
        "_create_transaction",
        external_transaction_then_fail,
    )
    with pytest.raises(
        publication.PublicationTransactionError,
        match="without overwrite",
    ):
        _publish(root)
    assert transaction_path.read_text(encoding="utf-8") == (
        "external transaction\n"
    )


def test_manifest_sync_failure_keeps_intent_and_recovers(tmp_path, monkeypatch):
    root, _ = _fixture(tmp_path)
    original = publication._sync_directory
    manifest = root / publication.PUBLICATION_MANIFEST_PATH
    failed = {"value": False}

    def fail_after_manifest_replace(path, *, label):
        if path == root / "outputs" and manifest.exists() and not failed["value"]:
            failed["value"] = True
            raise publication.PublicationTransactionError("manifest output sync failed")
        return original(path, label=label)

    monkeypatch.setattr(publication, "_sync_directory", fail_after_manifest_replace)
    with pytest.raises(publication.PublicationTransactionError, match="manifest output sync failed"):
        _publish(root)

    marker = _marker(root)
    transaction = json.loads(
        (root / f"runs/{RUN_ID}/publication_transaction.json").read_text(
            encoding="utf-8"
        )
    )
    assert marker["stage"] == "manifest_commit_recovery"
    assert marker["committed_paths"] == sorted(transaction["committed_paths"])
    assert marker["uncertain_paths"] == [publication.PUBLICATION_MANIFEST_PATH]
    assert transaction["phase"] == "manifest_commit_intent"
    assert transaction["active_target_path"] == publication.PUBLICATION_MANIFEST_PATH

    monkeypatch.setattr(publication, "_sync_directory", original)
    recovered = publication.recover_publication(
        root,
        run_id=RUN_ID,
        plan_fingerprint=PLAN_FINGERPRINT,
    )
    assert recovered["recovered"] is True
    assert recovered["transaction"]["phase"] == "cleanup_complete"


def test_recovery_marker_unlink_failure_preserves_transaction_for_retry(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original_atomic = publication._atomic_replace

    def fail_system_summary(path, data, *, root, label):
        if label == "publication target outputs/system_summary.md":
            raise publication.PublicationTransactionError("partial failure")
        return original_atomic(path, data, root=root, label=label)

    monkeypatch.setattr(publication, "_atomic_replace", fail_system_summary)
    with pytest.raises(publication.PublicationTransactionError, match="partial failure"):
        _publish(root)
    monkeypatch.setattr(publication, "_atomic_replace", original_atomic)

    marker_path = root / f"runs/{RUN_ID}/.publication_recovery_required.json"
    transaction_path = root / f"runs/{RUN_ID}/publication_transaction.json"
    original_unlink = Path.unlink
    failed = {"value": False}

    def fail_marker_unlink(self, *args, **kwargs):
        if self == marker_path and not failed["value"]:
            failed["value"] = True
            raise OSError("marker unlink blocked")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_marker_unlink)
    with pytest.raises(publication.PublicationTransactionError, match="rollback recovery remains incomplete"):
        publication.recover_publication(
            root,
            run_id=RUN_ID,
            plan_fingerprint=PLAN_FINGERPRINT,
        )
    assert marker_path.is_file()
    assert transaction_path.is_file()

    monkeypatch.setattr(Path, "unlink", original_unlink)
    recovered = publication.recover_publication(
        root,
        run_id=RUN_ID,
        plan_fingerprint=PLAN_FINGERPRINT,
    )
    assert recovered["rolled_back"] is True
    assert not marker_path.exists()
    assert not transaction_path.exists()


def test_recovery_quarantines_target_changed_after_hash_classification(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original_atomic = publication._atomic_replace
    target = root / "outputs/final_project_report.md"

    def fail_later_target(path, data, *, root, label):
        if label == "publication target outputs/system_summary.md":
            raise publication.PublicationTransactionError("later failure")
        return original_atomic(path, data, root=root, label=label)

    monkeypatch.setattr(publication, "_atomic_replace", fail_later_target)
    with pytest.raises(publication.PublicationTransactionError, match="later failure"):
        _publish(root)
    monkeypatch.setattr(publication, "_atomic_replace", original_atomic)

    original_replace = publication.os.replace

    def replace_external_bytes_into_quarantine(source, destination):
        if Path(source) == target and "rollback_quarantine." in str(destination):
            target.write_text("external writer\n", encoding="utf-8")
        return original_replace(source, destination)

    monkeypatch.setattr(publication.os, "replace", replace_external_bytes_into_quarantine)
    with pytest.raises(
        publication.PublicationTransactionError,
        match="changed during quarantine",
    ):
        publication.recover_publication(
            root,
            run_id=RUN_ID,
            plan_fingerprint=PLAN_FINGERPRINT,
        )
    quarantine_root = root / f"runs/{RUN_ID}/publication_recovery"
    quarantined = next(quarantine_root.rglob("final_project_report.md"))
    assert quarantined.read_text(encoding="utf-8") == "external writer\n"
    assert not target.exists()
    assert (root / f"runs/{RUN_ID}/publication_transaction.json").is_file()


def test_repeated_recovery_validates_existing_invalidated_summary_hash(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original_atomic = publication._atomic_replace

    def fail_manifest(path, data, *, root, label):
        if path.name == "current_publication_manifest.json":
            raise publication.PublicationTransactionError("Manifest failure")
        return original_atomic(path, data, root=root, label=label)

    monkeypatch.setattr(publication, "_atomic_replace", fail_manifest)
    with pytest.raises(publication.PublicationTransactionError):
        _publish(root)
    monkeypatch.setattr(publication, "_atomic_replace", original_atomic)

    original_cleanup = publication._cleanup_transaction

    def fail_cleanup(project_root, transaction):
        raise publication.PublicationTransactionError("cleanup interrupted")

    monkeypatch.setattr(publication, "_cleanup_transaction", fail_cleanup)
    with pytest.raises(
        publication.PublicationTransactionError,
        match="rollback recovery remains incomplete",
    ):
        publication.recover_publication(
            root,
            run_id=RUN_ID,
            plan_fingerprint=PLAN_FINGERPRINT,
        )
    marker = _marker(root)
    invalidated = (
        root
        / f"runs/{RUN_ID}/publication_recovery/"
        f"invalidated_final_summary.{marker['transaction_id']}.md"
    )
    assert invalidated.is_file()
    invalidated.write_text("tampered\n", encoding="utf-8")

    monkeypatch.setattr(publication, "_cleanup_transaction", original_cleanup)
    with pytest.raises(
        publication.PublicationTransactionError,
        match="rollback recovery remains incomplete",
    ) as caught:
        publication.recover_publication(
            root,
            run_id=RUN_ID,
            plan_fingerprint=PLAN_FINGERPRINT,
        )
    assert "conflicting bytes" in str(caught.value.__cause__)


def test_recovery_marker_cannot_expand_transaction_ownership(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original = publication._atomic_replace

    def fail_system_summary(path, data, *, root, label):
        if label == "publication target outputs/system_summary.md":
            raise publication.PublicationTransactionError("partial failure")
        return original(path, data, root=root, label=label)

    monkeypatch.setattr(publication, "_atomic_replace", fail_system_summary)
    with pytest.raises(publication.PublicationTransactionError):
        _publish(root)
    marker_path = (
        root / f"runs/{RUN_ID}/.publication_recovery_required.json"
    )
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["uncertain_paths"] = ["outputs/system_summary.md"]
    marker_path.write_bytes(publication._canonical_json_bytes(marker))

    with pytest.raises(
        publication.PublicationTransactionError,
        match="cannot expand transaction target ownership",
    ):
        publication.recover_publication(
            root,
            run_id=RUN_ID,
            plan_fingerprint=PLAN_FINGERPRINT,
        )


def test_failed_active_target_clear_does_not_grant_target_ownership(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original_atomic = publication._atomic_replace
    original_write = publication._write_transaction
    target = root / "outputs/system_summary.md"
    clean_failure_seen = {"value": False}

    def same_bytes_without_replace(path, data, *, root, label):
        if label == "publication target outputs/system_summary.md":
            path.write_bytes(data)
            clean_failure_seen["value"] = True
            raise publication.PublicationTransactionError("clean target failure")
        return original_atomic(path, data, root=root, label=label)

    def fail_active_clear(project_root, relative, document):
        if (
            clean_failure_seen["value"]
            and document["active_target_path"] is None
            and document["phase"] == "publishing"
        ):
            raise publication.PublicationTransactionError(
                "active clear state failure"
            )
        return original_write(project_root, relative, document)

    monkeypatch.setattr(publication, "_atomic_replace", same_bytes_without_replace)
    monkeypatch.setattr(publication, "_write_transaction", fail_active_clear)
    with pytest.raises(
        publication.PublicationTransactionError,
        match="active target state clear also failed",
    ):
        _publish(root)
    assert _marker(root)["stage"] == "publication_active_target_clear_failed"
    expected = target.read_bytes()

    monkeypatch.setattr(publication, "_write_transaction", original_write)
    with pytest.raises(
        publication.PublicationTransactionError,
        match="lack transaction ownership",
    ):
        publication.recover_publication(
            root,
            run_id=RUN_ID,
            plan_fingerprint=PLAN_FINGERPRINT,
        )
    assert target.read_bytes() == expected


def test_post_target_state_write_failure_marker_uses_durable_transaction(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original_write = publication._write_transaction
    target_relative = "outputs/final_project_report.md"
    failed = {"value": False}

    def fail_after_target_replace(project_root, relative, document):
        if (
            not failed["value"]
            and document["phase"] == "publishing"
            and document["active_target_path"] is None
            and document["committed_paths"] == [target_relative]
        ):
            failed["value"] = True
            raise publication.PublicationTransactionError(
                "post-target state write failed"
            )
        return original_write(project_root, relative, document)

    monkeypatch.setattr(
        publication,
        "_write_transaction",
        fail_after_target_replace,
    )
    with pytest.raises(
        publication.PublicationTransactionError,
        match="post-target state write failed",
    ):
        _publish(root)

    transaction = json.loads(
        (root / f"runs/{RUN_ID}/publication_transaction.json").read_text(
            encoding="utf-8"
        )
    )
    marker = _marker(root)
    assert transaction["committed_paths"] == []
    assert transaction["active_target_path"] == target_relative
    assert marker["committed_paths"] == sorted(transaction["committed_paths"])
    assert marker["uncertain_paths"] == [target_relative]

    monkeypatch.setattr(publication, "_write_transaction", original_write)
    recovered = publication.recover_publication(
        root,
        run_id=RUN_ID,
        plan_fingerprint=PLAN_FINGERPRINT,
    )
    assert recovered["rolled_back"] is True


def test_post_target_uncertain_transaction_write_attests_durable_active_target(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original_write = publication._write_transaction
    target_relative = "outputs/final_project_report.md"
    failed = {"value": False}

    def fail_uncertain_post_target_write(project_root, relative, document):
        if (
            not failed["value"]
            and document["phase"] == "publishing"
            and document["active_target_path"] is None
            and document["committed_paths"] == [target_relative]
        ):
            failed["value"] = True
            error = publication.PublicationTransactionError(
                "post-target transaction write outcome uncertain"
            )
            error.write_state_uncertain = True
            raise error
        return original_write(project_root, relative, document)

    monkeypatch.setattr(
        publication,
        "_write_transaction",
        fail_uncertain_post_target_write,
    )
    with pytest.raises(
        publication.PublicationTransactionError,
        match="post-target transaction write outcome uncertain",
    ):
        _publish(root)

    transaction = json.loads(
        (root / f"runs/{RUN_ID}/publication_transaction.json").read_text(
            encoding="utf-8"
        )
    )
    marker = _marker(root)
    assert transaction["committed_paths"] == []
    assert transaction["active_target_path"] == target_relative
    assert marker["committed_paths"] == []
    assert marker["uncertain_paths"] == [target_relative]
    assert marker["stage"] == "publication_write_uncertain"

    monkeypatch.setattr(publication, "_write_transaction", original_write)
    recovered = publication.recover_publication(
        root,
        run_id=RUN_ID,
        plan_fingerprint=PLAN_FINGERPRINT,
    )
    assert recovered["rolled_back"] is True
    assert not (root / target_relative).exists()
    quarantine = (
        root
        / f"runs/{RUN_ID}/publication_recovery/"
        f"rollback_quarantine.{recovered['transaction_id']}/"
        "final_project_report.md"
    )
    assert quarantine.is_file()


def test_post_manifest_state_write_failure_marker_uses_durable_transaction(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original_write = publication._write_transaction
    failed = {"value": False}

    def persist_manifest_state_then_fail(project_root, relative, document):
        result = original_write(project_root, relative, document)
        if (
            not failed["value"]
            and document["phase"] == "manifest_committed"
        ):
            failed["value"] = True
            raise publication.PublicationTransactionError(
                "post-Manifest state write failed"
            )
        return result

    monkeypatch.setattr(
        publication,
        "_write_transaction",
        persist_manifest_state_then_fail,
    )
    with pytest.raises(
        publication.PublicationTransactionError,
        match="post-Manifest state write failed",
    ):
        _publish(root)

    transaction = json.loads(
        (root / f"runs/{RUN_ID}/publication_transaction.json").read_text(
            encoding="utf-8"
        )
    )
    marker = _marker(root)
    assert transaction["phase"] == "manifest_committed"
    assert marker["committed_paths"] == sorted(transaction["committed_paths"])
    assert publication.PUBLICATION_MANIFEST_PATH in marker["committed_paths"]

    monkeypatch.setattr(publication, "_write_transaction", original_write)
    recovered = publication.recover_publication(
        root,
        run_id=RUN_ID,
        plan_fingerprint=PLAN_FINGERPRINT,
    )
    assert recovered["recovered"] is True
    assert recovered["transaction"]["phase"] == "cleanup_complete"


def test_cleanup_complete_state_write_failure_uses_durable_marker(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original_write = publication._write_transaction
    failed = {"value": False}

    def fail_cleanup_complete(project_root, relative, document):
        if (
            not failed["value"]
            and document["phase"] == "cleanup_complete"
        ):
            failed["value"] = True
            raise publication.PublicationTransactionError(
                "cleanup_complete state write failed"
            )
        return original_write(project_root, relative, document)

    monkeypatch.setattr(
        publication,
        "_write_transaction",
        fail_cleanup_complete,
    )
    with pytest.raises(
        publication.PublicationTransactionError,
        match="cleanup_complete state write failed",
    ):
        _publish(root)

    transaction = json.loads(
        (root / f"runs/{RUN_ID}/publication_transaction.json").read_text(
            encoding="utf-8"
        )
    )
    marker = _marker(root)
    assert transaction["phase"] == "manifest_committed"
    assert marker["committed_paths"] == sorted(transaction["committed_paths"])

    monkeypatch.setattr(publication, "_write_transaction", original_write)
    recovered = publication.recover_publication(
        root,
        run_id=RUN_ID,
        plan_fingerprint=PLAN_FINGERPRINT,
    )
    assert recovered["recovered"] is True
    assert recovered["transaction"]["phase"] == "cleanup_complete"


def test_committed_recovery_marker_unlink_failure_can_retry(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original_cleanup = publication._cleanup_transaction
    cleanup_failed = {"value": False}

    def fail_cleanup_once(project_root, transaction):
        if not cleanup_failed["value"]:
            cleanup_failed["value"] = True
            raise publication.PublicationTransactionError("cleanup failed")
        return original_cleanup(project_root, transaction)

    monkeypatch.setattr(publication, "_cleanup_transaction", fail_cleanup_once)
    with pytest.raises(
        publication.PublicationTransactionError,
        match="cleanup requires recovery",
    ):
        _publish(root)
    monkeypatch.setattr(publication, "_cleanup_transaction", original_cleanup)

    marker_path = (
        root / f"runs/{RUN_ID}/.publication_recovery_required.json"
    )
    original_unlink = Path.unlink
    unlink_failed = {"value": False}

    def fail_marker_unlink(self, *args, **kwargs):
        if self == marker_path and not unlink_failed["value"]:
            unlink_failed["value"] = True
            raise OSError("marker unlink blocked")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_marker_unlink)
    with pytest.raises(
        publication.PublicationTransactionError,
        match="marker cleanup failed",
    ):
        publication.recover_publication(
            root,
            run_id=RUN_ID,
            plan_fingerprint=PLAN_FINGERPRINT,
        )
    transaction = json.loads(
        (root / f"runs/{RUN_ID}/publication_transaction.json").read_text(
            encoding="utf-8"
        )
    )
    marker = _marker(root)
    assert transaction["phase"] == "cleanup_complete"
    assert marker["committed_paths"] == sorted(transaction["committed_paths"])

    monkeypatch.setattr(Path, "unlink", original_unlink)
    recovered = publication.recover_publication(
        root,
        run_id=RUN_ID,
        plan_fingerprint=PLAN_FINGERPRINT,
    )
    assert recovered["recovered"] is True
    assert not marker_path.exists()


def test_repeated_final_summary_isolation_preserves_concurrent_replacement(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original_atomic = publication._atomic_replace

    def fail_manifest(path, data, *, root, label):
        if path.name == "current_publication_manifest.json":
            raise publication.PublicationTransactionError("Manifest failure")
        return original_atomic(path, data, root=root, label=label)

    monkeypatch.setattr(publication, "_atomic_replace", fail_manifest)
    with pytest.raises(publication.PublicationTransactionError):
        _publish(root)
    monkeypatch.setattr(publication, "_atomic_replace", original_atomic)
    publication.recover_publication(
        root,
        run_id=RUN_ID,
        plan_fingerprint=PLAN_FINGERPRINT,
    )

    monkeypatch.setattr(publication, "_atomic_replace", fail_manifest)
    with pytest.raises(publication.PublicationTransactionError):
        _publish(root)
    monkeypatch.setattr(publication, "_atomic_replace", original_atomic)

    final_summary = root / f"runs/{RUN_ID}/final_summary.md"
    original_replace = publication.os.replace

    def replace_then_recreate_source(source, destination):
        result = original_replace(source, destination)
        if Path(source) == final_summary:
            final_summary.write_text(
                "concurrent external summary\n",
                encoding="utf-8",
            )
        return result

    monkeypatch.setattr(
        publication.os,
        "replace",
        replace_then_recreate_source,
    )
    with pytest.raises(
        publication.PublicationTransactionError,
        match="concurrent",
    ):
        publication.recover_publication(
            root,
            run_id=RUN_ID,
            plan_fingerprint=PLAN_FINGERPRINT,
        )
    assert final_summary.read_text(encoding="utf-8") == (
        "concurrent external summary\n"
    )
    recovery_files = list(
        (root / f"runs/{RUN_ID}/publication_recovery").rglob("*.md")
    )
    assert len(recovery_files) >= 2


def test_initial_final_summary_isolation_preserves_concurrent_replacement(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original_atomic = publication._atomic_replace

    def fail_manifest(path, data, *, root, label):
        if path.name == "current_publication_manifest.json":
            raise publication.PublicationTransactionError("Manifest failure")
        return original_atomic(path, data, root=root, label=label)

    monkeypatch.setattr(publication, "_atomic_replace", fail_manifest)
    with pytest.raises(publication.PublicationTransactionError):
        _publish(root)
    monkeypatch.setattr(publication, "_atomic_replace", original_atomic)

    final_summary = root / f"runs/{RUN_ID}/final_summary.md"
    expected_transaction_bytes = final_summary.read_bytes()
    transaction_path = (
        root / f"runs/{RUN_ID}/publication_transaction.json"
    )
    marker_path = (
        root / f"runs/{RUN_ID}/.publication_recovery_required.json"
    )
    original_replace = publication.os.replace

    def replace_then_recreate_source(source, destination):
        result = original_replace(source, destination)
        if (
            Path(source) == final_summary
            and "invalidated_final_summary." in Path(destination).name
        ):
            final_summary.write_text(
                "concurrent external summary\n",
                encoding="utf-8",
            )
        return result

    monkeypatch.setattr(
        publication.os,
        "replace",
        replace_then_recreate_source,
    )
    with pytest.raises(
        publication.PublicationTransactionError,
        match="concurrent",
    ):
        publication.recover_publication(
            root,
            run_id=RUN_ID,
            plan_fingerprint=PLAN_FINGERPRINT,
        )

    assert final_summary.read_text(encoding="utf-8") == (
        "concurrent external summary\n"
    )
    invalidated = list(
        (root / f"runs/{RUN_ID}/publication_recovery").glob(
            "invalidated_final_summary.*.md"
        )
    )
    assert len(invalidated) == 1
    assert invalidated[0].read_bytes() == expected_transaction_bytes
    assert transaction_path.is_file()
    assert marker_path.is_file()


def test_quarantine_has_no_fixed_retry_capacity(tmp_path):
    root, _ = _fixture(tmp_path)
    data = b"controlled publication bytes\n"
    target_relative = "outputs/final_project_report.md"
    target = root / target_relative
    target.parent.mkdir(parents=True, exist_ok=True)
    transaction = {
        "run_id": RUN_ID,
        "transaction_id": "pub_" + "a" * 24,
    }
    record = {
        "path": target_relative,
        "new_sha256": publication._sha256(data),
    }
    quarantined_paths = []

    for _ in range(40):
        target.write_bytes(data)
        quarantined_paths.append(
            publication._quarantine_rollback_target(
                root,
                transaction,
                record,
            )
        )

    assert len(set(quarantined_paths)) == 40
    assert all((root / path).read_bytes() == data for path in quarantined_paths)


def test_recovery_marker_committed_paths_must_match_durable_transaction(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original_atomic = publication._atomic_replace

    def fail_system_summary(path, data, *, root, label):
        if label == "publication target outputs/system_summary.md":
            raise publication.PublicationTransactionError("partial failure")
        return original_atomic(path, data, root=root, label=label)

    monkeypatch.setattr(publication, "_atomic_replace", fail_system_summary)
    with pytest.raises(publication.PublicationTransactionError):
        _publish(root)
    marker_path = (
        root / f"runs/{RUN_ID}/.publication_recovery_required.json"
    )
    marker = _marker(root)
    marker["committed_paths"] = []
    marker_path.write_bytes(publication._canonical_json_bytes(marker))

    monkeypatch.setattr(publication, "_atomic_replace", original_atomic)
    with pytest.raises(
        publication.PublicationTransactionError,
        match="exactly match",
    ):
        publication.recover_publication(
            root,
            run_id=RUN_ID,
            plan_fingerprint=PLAN_FINGERPRINT,
        )


def test_recovery_accepts_uncertain_path_already_durably_committed(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original_cleanup = publication._cleanup_transaction
    failed = {"value": False}

    def fail_cleanup_once(project_root, transaction):
        if not failed["value"]:
            failed["value"] = True
            raise publication.PublicationTransactionError("cleanup failed")
        return original_cleanup(project_root, transaction)

    monkeypatch.setattr(publication, "_cleanup_transaction", fail_cleanup_once)
    with pytest.raises(publication.PublicationTransactionError):
        _publish(root)
    marker_path = (
        root / f"runs/{RUN_ID}/.publication_recovery_required.json"
    )
    marker = _marker(root)
    marker["uncertain_paths"] = [publication.PUBLICATION_MANIFEST_PATH]
    marker_path.write_bytes(publication._canonical_json_bytes(marker))

    monkeypatch.setattr(publication, "_cleanup_transaction", original_cleanup)
    recovered = publication.recover_publication(
        root,
        run_id=RUN_ID,
        plan_fingerprint=PLAN_FINGERPRINT,
    )
    assert recovered["recovered"] is True
    assert recovered["transaction"]["phase"] == "cleanup_complete"


def test_recovery_marker_parent_sync_failure_preserves_marker_and_transaction(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original_atomic = publication._atomic_replace
    original_sync = publication._sync_directory

    def fail_system_summary(path, data, *, root, label):
        if label == "publication target outputs/system_summary.md":
            raise publication.PublicationTransactionError("partial failure")
        return original_atomic(path, data, root=root, label=label)

    def fail_marker_parent_sync(path, *, label):
        if label == "publication recovery marker parent":
            raise publication.PublicationTransactionError(
                "marker parent sync failed"
            )
        return original_sync(path, label=label)

    monkeypatch.setattr(publication, "_atomic_replace", fail_system_summary)
    monkeypatch.setattr(publication, "_sync_directory", fail_marker_parent_sync)
    with pytest.raises(
        publication.PublicationTransactionError,
        match="recovery marker write also failed",
    ) as caught:
        _publish(root)

    assert "marker parent sync failed" in str(caught.value)
    assert (
        root / f"runs/{RUN_ID}/.publication_recovery_required.json"
    ).is_file()
    assert (
        root / f"runs/{RUN_ID}/publication_transaction.json"
    ).is_file()


def test_workspace_parent_sync_failure_keeps_recovery_authority(
    tmp_path,
    monkeypatch,
):
    root, _ = _fixture(tmp_path)
    original_sync = publication._sync_directory
    backup_parent_syncs = {"count": 0}

    def fail_backup_parent_after_removal(path, *, label):
        if (
            label == "publication backup parent"
            and path.name == "publication_backup"
        ):
            backup_parent_syncs["count"] += 1
            if backup_parent_syncs["count"] == 2:
                raise publication.PublicationTransactionError(
                    "backup parent sync failed"
                )
        return original_sync(path, label=label)

    monkeypatch.setattr(
        publication,
        "_sync_directory",
        fail_backup_parent_after_removal,
    )
    with pytest.raises(
        publication.PublicationTransactionError,
        match="cleanup requires recovery",
    ):
        _publish(root)

    marker = _marker(root)
    transaction_path = (
        root / f"runs/{RUN_ID}/publication_transaction.json"
    )
    transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
    assert marker["stage"] == "publication_cleanup"
    assert marker["committed_paths"] == sorted(
        transaction["committed_paths"]
    )
    assert transaction_path.is_file()
