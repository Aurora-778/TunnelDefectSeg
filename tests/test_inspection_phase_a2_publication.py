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
    final_report = (root / "outputs/final_project_report.md").read_text(encoding="utf-8")
    assert "byte_binding_only" in final_report
    assert "不构成方向性变化结论" in final_report
    assert "growth_trend" not in final_report
    assert "area_growth_rate" not in final_report
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

    def track(path, data, *, root, label):
        relative = path.relative_to(root).as_posix()
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
        if path.name == "system_summary.md":
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
    assert not (root / "outputs/final_project_report.md").exists()
    assert not (root / "outputs/key_insights.md").exists()
    assert not (root / "outputs/system_summary.md").exists()
    assert not (root / publication.PUBLICATION_MANIFEST_PATH).exists()

    recovered = publication.recover_publication(
        root,
        run_id=RUN_ID,
        plan_fingerprint=PLAN_FINGERPRINT,
    )
    assert recovered["rolled_back"] is True
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
    invalidated = marker["invalidated_final_summary_path"]
    assert invalidated.startswith(f"runs/{RUN_ID}/staging/invalidated_final_summary.pub_")
    assert (root / invalidated).is_file()
    assert not (root / f"runs/{RUN_ID}/final_summary.md").exists()
    assert invalidated not in marker["committed_paths"]


def test_clean_zero_commit_failure_removes_transaction_and_can_retry(tmp_path, monkeypatch):
    root, _ = _fixture(tmp_path)
    original = publication._atomic_replace
    failed = {"value": False}

    def fail_first_final(path, data, *, root, label):
        if path.name == "final_project_report.md" and not failed["value"]:
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
        if Path(target).name == "final_project_report.md":
            raise OSError("replace result unknown")
        return original(source, target)

    monkeypatch.setattr(publication.os, "replace", fail_first_formal)
    with pytest.raises(publication.PublicationTransactionError):
        _publish(root)
    marker = _marker(root)
    assert marker["stage"] == "publication_zero_commit_uncertain"
    assert marker["committed_paths"] == []
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
