from __future__ import annotations

import csv
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
from types import MappingProxyType, SimpleNamespace
import uuid

from PIL import Image
import pytest

from orchestrator.inspection_workflow import a1_artifacts
from orchestrator.inspection_workflow import artifact_resolver
from orchestrator.inspection_workflow import locking
from orchestrator.inspection_workflow.artifact_resolver import (
    ArtifactResolver,
    ArtifactResolverInputError,
)
from orchestrator.inspection_workflow.controller import InspectionWorkflowController
from orchestrator.inspection_workflow.locking import (
    acquire_active_run_lock,
    mark_active_run_running,
    reserve_active_run_id,
)
from orchestrator.state.store import StateStore
from scripts import prepare_real_inspection_pilot as preparation


RUN_ID = "run_701"


def _prepared_task(root: Path) -> dict[str, object]:
    raw = root / "raw-dataset"
    (raw / "images").mkdir(parents=True)
    (raw / "masks").mkdir()
    Image.new("RGB", (8, 8), color=(80, 90, 100)).save(raw / "images/a.jpg")
    mask = Image.new("L", (8, 8), color=0)
    mask.putpixel((1, 1), 255)
    mask.save(raw / "masks/a.png")
    Image.new("RGB", (8, 8), color=(30, 40, 50)).save(raw / "images/b.jpg")
    Image.new("L", (8, 8), color=255).save(raw / "masks/b.png")
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
    with (raw / "metadata.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=preparation.REQUIRED_METADATA_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    prepared = root / "data" / "prepared_inspections" / "pilot_001"
    preparation.prepare_real_inspection_pilot(raw, prepared)
    return {
        "schema_version": "inspection_task_v1",
        "task_id": "task_701",
        "task_type": "inspection_analysis",
        "input": {"input_mode": "prepared_dataset", "dataset_id": "pilot_001"},
        "requested_outputs": [
            "association",
            "growth_report",
            "visualization",
            "final_report",
        ],
    }


@pytest.fixture
def completed_run(tmp_path: Path) -> Path:
    root = tmp_path / "prepared-run"
    root.mkdir()
    request = _prepared_task(root)
    InspectionWorkflowController.run_prepared_task(root, task_request=request, run_id=RUN_ID)
    return root


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_bytes())


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(_canonical_json_bytes(value))


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


class _RestoringHandle:
    def __init__(self, handle: object, restore: object) -> None:
        self.handle = handle
        self.restore = restore

    def __enter__(self) -> "_RestoringHandle":
        return self

    def __exit__(self, *args: object) -> bool:
        self.handle.close()  # type: ignore[union-attr]
        self.restore()  # type: ignore[operator]
        return False

    def fileno(self) -> int:
        return self.handle.fileno()  # type: ignore[union-attr]

    def read(self, size: int) -> bytes:
        return self.handle.read(size)  # type: ignore[union-attr]


def _rebind_a1_manifest_in_publication(root: Path, manifest_path: Path) -> None:
    publication_path = _a2_manifest_path(root)
    publication_manifest = _read_json(publication_path)
    entry = next(
        item
        for item in publication_manifest["source_artifacts"]
        if item["path"] == f"runs/{RUN_ID}/artifacts/comparison_evidence_manifest.json"
    )
    data = manifest_path.read_bytes()
    entry["size_bytes"] = len(data)
    entry["sha256"] = hashlib.sha256(data).hexdigest()
    _write_json(publication_path, publication_manifest)

    transaction_path = _transaction_path(root)
    transaction = _read_json(transaction_path)
    transaction["manifest_sha256"] = hashlib.sha256(
        publication_path.read_bytes()
    ).hexdigest()
    _write_json(transaction_path, transaction)


def _tree_snapshot(root: Path) -> dict[str, bytes | None]:
    snapshot: dict[str, bytes | None] = {}
    if not root.exists():
        return snapshot
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        snapshot[relative] = None if path.is_dir() else path.read_bytes()
    return snapshot


def _a2_manifest_path(root: Path) -> Path:
    return root / "outputs" / "current_publication_manifest.json"


def _transaction_path(root: Path) -> Path:
    return root / "runs" / RUN_ID / "publication_transaction.json"


def _append_unresolved_pending(root: Path) -> None:
    run_dir = root / "runs" / RUN_ID
    state = _read_json(run_dir / "state.json")
    store = StateStore(root)
    records, _, journal = store._journal_state(  # type: ignore[attr-defined]
        RUN_ID,
        state["allocation_token"],
        repair_unanchored=False,
    )
    last = records[-1]
    pending = dict(last)
    pending["record_index"] = len(records)
    pending["previous_record_checksum"] = last["record_checksum"]
    pending["operation_id"] = f"run:{RUN_ID}:resolver:pending"
    pending["phase"] = "pending"
    pending["payload"] = {"resolver_probe": True}
    pending["payload_sha256"] = hashlib.sha256(
        json.dumps(pending["payload"], sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    pending["record_checksum"] = None
    pending["record_checksum"] = store._record_checksum(pending)  # type: ignore[attr-defined]
    line = (
        json.dumps(pending, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        + b"\n"
    )
    (run_dir / "state_journal.jsonl").write_bytes(journal + line)


def _rebind_checkpoint_report_paths(root: Path, replacements: dict[str, str]) -> None:
    """Keep State, Journal, and tail anchor consistent for a provenance probe."""

    run_dir = root / "runs" / RUN_ID
    state_path = run_dir / "state.json"
    journal_path = run_dir / "state_journal.jsonl"
    anchor_path = run_dir / "state_journal_tail.json"
    state = _read_json(state_path)
    events = state["context"]["phase_a3_checkpoint_events"]
    for operation_id, replacement in replacements.items():
        events[operation_id]["controlled_context_delta"]["task_output"]["result"]["report_paths"][0] = replacement

    rows = [json.loads(line) for line in journal_path.read_bytes().splitlines()]
    for operation_id, replacement in replacements.items():
        pending = next(
            row for row in rows if row["operation_id"] == operation_id and row["phase"] == "pending"
        )
        pending["payload"]["controlled_context_delta"]["task_output"]["result"]["report_paths"][0] = replacement
        payload_sha256 = hashlib.sha256(_canonical_json_bytes(pending["payload"])).hexdigest()
        for row in rows:
            if row["operation_id"] == operation_id:
                row["payload_sha256"] = payload_sha256

    state_bytes = _canonical_json_bytes(state)
    last_committed = next(row for row in reversed(rows) if row["phase"] == "committed")
    for row in rows:
        if row["operation_id"] == last_committed["operation_id"]:
            row["resulting_state_sha256"] = hashlib.sha256(state_bytes).hexdigest()

    store = StateStore(root)
    previous_checksum: str | None = None
    for row in rows:
        row["previous_record_checksum"] = previous_checksum
        row["record_checksum"] = None
        row["record_checksum"] = store._record_checksum(row)  # type: ignore[attr-defined]
        previous_checksum = row["record_checksum"]
    journal_bytes = b"".join(_canonical_json_bytes(row) for row in rows)
    anchor = store._anchor_document(  # type: ignore[attr-defined]
        run_id=RUN_ID,
        allocation_token=state["allocation_token"],
        tail_record_index=rows[-1]["record_index"],
        tail_record_checksum=rows[-1]["record_checksum"],
        tail_file_size_bytes=len(journal_bytes),
    )
    state_path.write_bytes(state_bytes)
    journal_path.write_bytes(journal_bytes)
    anchor_path.write_bytes(_canonical_json_bytes(anchor))


def _transfer_checkpoint_producer(root: Path) -> None:
    _rebind_checkpoint_report_paths(
        root,
        {
            f"run:{RUN_ID}:task:phase_a_growth_report:attempt:1:succeeded": (
                f"runs/{RUN_ID}/staging/disease_engineering_report.md"
            ),
            f"run:{RUN_ID}:task:phase_a_engineering_claim_report:attempt:1:succeeded": (
                f"runs/{RUN_ID}/staging/disease_engineering_report_summary.md"
            ),
        },
    )


def _make_incomplete_run(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = "run_001"
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True)
    source = run_dir / "work" / "input.csv"
    source.parent.mkdir()
    source.write_bytes(b"input\n")
    policy = artifact_resolver.load_workflow_policy()
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    descriptor = {
        "schema_version": "phase_a3_3_2_resolved_input_v1",
        "run_id": run_id,
        "input_mode": "legacy_simulated",
        "workflow_task_id": "task_001",
        "execution_profile": "phase_a_agent_sandbox",
        "requested_outputs": [],
        "workflow_policy_sha256": hashlib.sha256(
            json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "task_request": None,
        "source_artifacts": [
            {"path": f"runs/{run_id}/work/input.csv", "size_bytes": 6, "sha256": source_sha}
        ],
        "origin_artifacts": [{"path": f"runs/{run_id}/work/input.csv", "sha256": source_sha}],
    }
    descriptor_sha = hashlib.sha256(
        json.dumps(descriptor, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    allocation_token = str(uuid.uuid4())
    lock_token = str(uuid.uuid4())
    acquire_active_run_lock(
        root,
        task_id="task_001",
        allocation_token=allocation_token,
        lock_token=lock_token,
        created_at="2026-08-03T00:00:00.000000Z",
        pid=1,
        hostname="resolver-test",
    )
    reserve_active_run_id(
        root,
        run_id=run_id,
        expected_allocation_token=allocation_token,
        expected_lock_token=lock_token,
    )
    store = StateStore(root)
    store.initialize_run(
        run_id=run_id,
        allocation_token=allocation_token,
        plan_fingerprint="a" * 64,
        task_plan=[{"task_id": "core", "deps": [], "required": True}],
        expected_lock_token=lock_token,
        initial_context={
            "workflow_input_mode": "legacy_simulated",
            "workflow_task_id": "task_001",
            "resolved_input_descriptor": descriptor,
            "resolved_input_descriptor_sha256": descriptor_sha,
        },
        created_at="2026-08-03T00:00:00.000000Z",
    )
    mark_active_run_running(
        root,
        run_id=run_id,
        expected_allocation_token=allocation_token,
        expected_lock_token=lock_token,
    )
    store.transition_status(
        run_id=run_id,
        expected_lock_token=lock_token,
        expected_status="CREATED",
        expected_state_version=0,
        operation_id=f"run:{run_id}:transition:v0:CREATED:PLANNED:auto",
        mutation_timestamp="2026-08-03T00:00:01.000000Z",
        payload={
            "next_status": "PLANNED",
            "metadata": None,
            "completion_evidence": None,
            "transition_kind": "auto",
            "decision_token": None,
        },
    )
    store.checkpoint_context(
        run_id=run_id,
        expected_lock_token=lock_token,
        expected_status="PLANNED",
        expected_state_version=1,
        operation_id=f"run:{run_id}:checkpoint:run_initialized",
        mutation_timestamp="2026-08-03T00:00:02.000000Z",
        payload={
            "checkpoint_kind": "run_initialized",
            "task_id": None,
            "attempt_number": None,
            "expected_task_status": None,
            "next_task_status": None,
            "retry_disposition": "none",
            "next_attempt_number": None,
            "controlled_context_delta": {
                "task_plan": [{"task_id": "core", "deps": [], "required": True}],
                "plan_fingerprint": "a" * 64,
            },
            "error_summary": None,
            "created_at": "2026-08-03T00:00:02.000000Z",
        },
    )
    store.transition_status(
        run_id=run_id,
        expected_lock_token=lock_token,
        expected_status="PLANNED",
        expected_state_version=2,
        operation_id=f"run:{run_id}:transition:v2:PLANNED:RUNNING:auto",
        mutation_timestamp="2026-08-03T00:00:03.000000Z",
        payload={
            "next_status": "RUNNING",
            "metadata": None,
            "completion_evidence": None,
            "transition_kind": "auto",
            "decision_token": None,
        },
    )
    monkeypatch.setattr(artifact_resolver, "_current_plan_fingerprint", lambda descriptor: "a" * 64)


def test_completed_prepared_cli_run_resolves_complete_and_binds_inventory(completed_run: Path) -> None:
    result = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)

    assert result.status == "complete"
    assert result.inventory
    assert result.inventory_sha256 == hashlib.sha256(result.inventory_bytes).hexdigest()
    assert result.inventory_bytes == ArtifactResolver(completed_run).resolve(run_id=RUN_ID).inventory_bytes
    with pytest.raises(TypeError):
        result.inventory[0]["path"] = "runs/run_701/forged.json"  # type: ignore[index]
    assert all(item["path"].startswith(f"runs/{RUN_ID}/") for item in result.inventory)
    assert all("\\" not in item["path"] and not item["path"].startswith("/") for item in result.inventory)
    final_summary = next(
        item
        for item in result.inventory
        if item["path"] == f"runs/{RUN_ID}/final_summary.md"
    )
    transaction = _read_json(_transaction_path(completed_run))
    assert final_summary["task_id"] == "publication"
    assert final_summary["producer_operation"] == f"publication:{transaction['transaction_id']}"
    for item in result.inventory:
        assert set(item) == {
            "task_id",
            "artifact_role",
            "path",
            "size_bytes",
            "sha256",
            "producer_operation",
            "resulting_state_version",
            "plan_fingerprint",
            "input_descriptor_sha256",
        }


def test_resolver_is_read_only_and_repeated_result_is_byte_stable(completed_run: Path) -> None:
    before = _tree_snapshot(completed_run)
    resolver = ArtifactResolver(completed_run)
    first = resolver.resolve(run_id=RUN_ID)
    second = resolver.resolve(run_id=RUN_ID)

    assert first.to_dict() == second.to_dict()
    assert _tree_snapshot(completed_run) == before


def test_unfinished_required_task_is_incomplete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "incomplete"
    root.mkdir()
    _make_incomplete_run(root, monkeypatch)

    result = ArtifactResolver(root).resolve(run_id="run_001")

    assert result.status == "incomplete"


def test_unfinished_descriptor_source_drift_is_invalid_without_authority_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "incomplete-source-drift"
    root.mkdir()
    _make_incomplete_run(root, monkeypatch)
    (root / "runs" / "run_001" / "work" / "input.csv").write_bytes(b"changed\n")

    result = ArtifactResolver(root).resolve(run_id="run_001")

    assert result.status == "invalid"
    assert "descriptor_source_stale" in result.issue_codes


def test_recovery_residue_wins_over_every_other_state(completed_run: Path) -> None:
    marker = completed_run / "runs" / RUN_ID / "work" / ".a1_recovery_required.json"
    marker.write_bytes(b"recovery")

    result = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)

    assert result.status == "recovery_required"
    assert "recovery_marker" in result.issue_codes


def test_cleanup_pending_transaction_is_recovery_required(completed_run: Path) -> None:
    transaction = _read_json(_transaction_path(completed_run))
    transaction["phase"] = "cleanup_pending"
    _write_json(_transaction_path(completed_run), transaction)

    result = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)

    assert result.status == "recovery_required"
    assert "transaction_recovery" in result.issue_codes


def test_unresolved_journal_is_recovery_required(completed_run: Path) -> None:
    _append_unresolved_pending(completed_run)

    result = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)

    assert result.status == "recovery_required"
    assert "state_recovery" in result.issue_codes


def test_source_descriptor_drift_is_invalid_without_authority_proof(completed_run: Path) -> None:
    source = completed_run / "runs" / RUN_ID / "work" / "raw_prepared" / "frame_records.csv"
    source.write_bytes(source.read_bytes() + b"\n")

    result = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)

    assert result.status == "invalid"
    assert "descriptor_source_stale" in result.issue_codes


def test_claim_only_corruption_is_invalid(completed_run: Path) -> None:
    claim = completed_run / "runs" / RUN_ID / "artifacts" / "claim_decision.json"
    changed = claim.read_bytes().replace(b"allowed_with_limits", b"blocked_invalid", 1)
    assert changed != claim.read_bytes()
    claim.write_bytes(changed)

    result = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)

    assert result.status == "invalid"
    assert "claim_decision_invalid" in result.issue_codes


def test_source_drift_and_claim_corruption_fail_closed(completed_run: Path) -> None:
    source = completed_run / "runs" / RUN_ID / "work" / "raw_prepared" / "frame_records.csv"
    source.write_bytes(source.read_bytes() + b"\n")
    claim = completed_run / "runs" / RUN_ID / "artifacts" / "claim_decision.json"
    changed = claim.read_bytes().replace(b"allowed_with_limits", b"blocked_invalid", 1)
    assert changed != claim.read_bytes()
    claim.write_bytes(changed)

    result = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)

    assert result.status == "invalid"
    assert "descriptor_source_stale" in result.issue_codes
    assert "a1_invalid" in result.issue_codes


@pytest.mark.parametrize("mutation", ["manifest_field", "source_set_delete"])
def test_source_drift_does_not_mask_a1_manifest_authority_failure(
    completed_run: Path, mutation: str
) -> None:
    source = completed_run / "runs" / RUN_ID / "work" / "raw_prepared" / "frame_records.csv"
    source.write_bytes(source.read_bytes() + b"\n")
    manifest_path = completed_run / "runs" / RUN_ID / "artifacts" / "comparison_evidence_manifest.json"
    manifest = _read_json(manifest_path)
    if mutation == "manifest_field":
        manifest["record_count"] += 1
    else:
        manifest["source_artifacts"] = manifest["source_artifacts"][:-1]
    _write_json(manifest_path, manifest)
    _rebind_a1_manifest_in_publication(completed_run, manifest_path)

    result = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)

    assert result.status == "invalid"
    assert "a1_invalid" in result.issue_codes


def test_source_drift_does_not_mask_publication_transaction_rebinding(completed_run: Path) -> None:
    source = completed_run / "runs" / RUN_ID / "work" / "raw_prepared" / "frame_records.csv"
    source.write_bytes(source.read_bytes() + b"\n")
    manifest_path = _a2_manifest_path(completed_run)
    transaction_path = _transaction_path(completed_run)
    manifest = _read_json(manifest_path)
    transaction = _read_json(transaction_path)
    rebound = "pub_" + "f" * 24
    manifest["transaction_id"] = rebound
    _write_json(manifest_path, manifest)
    transaction["transaction_id"] = rebound
    transaction["manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    _write_json(transaction_path, transaction)

    result = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)

    assert result.status == "invalid"
    assert "publication_invalid" in result.issue_codes


def test_source_drift_does_not_mask_publication_source_set_rebinding(
    completed_run: Path,
) -> None:
    source = completed_run / "runs" / RUN_ID / "work" / "raw_prepared" / "frame_records.csv"
    source.write_bytes(source.read_bytes() + b"\n")
    manifest_path = _a2_manifest_path(completed_run)
    manifest = _read_json(manifest_path)
    removed_path = next(
        item
        for item in manifest["source_artifacts"]
        if item["path"].endswith("/artifacts/claim_decision.json")
    )["path"]
    manifest["source_artifacts"] = [
        item for item in manifest["source_artifacts"] if item["path"] != removed_path
    ]
    manifest["expected_source_artifact_paths"] = [
        item["path"] for item in manifest["source_artifacts"]
    ]
    _write_json(manifest_path, manifest)
    transaction_path = _transaction_path(completed_run)
    transaction = _read_json(transaction_path)
    transaction["manifest_sha256"] = hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()
    _write_json(transaction_path, transaction)

    result = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)

    assert result.status == "invalid"
    assert "publication_invalid" in result.issue_codes


def test_workflow_policy_drift_is_stale(completed_run: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = artifact_resolver.load_workflow_policy

    def changed_policy() -> dict[str, object]:
        policy = deepcopy(original())
        policy["schema_version"] = "inspection_workflow_v1_drift"
        return policy

    monkeypatch.setattr(artifact_resolver, "load_workflow_policy", changed_policy)
    result = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)

    assert result.status == "stale"
    assert "workflow_policy_stale" in result.issue_codes


def test_plan_fingerprint_drift_is_stale(completed_run: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        artifact_resolver,
        "_current_plan_fingerprint",
        lambda descriptor: "f" * 64,
    )

    result = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)

    assert result.status == "stale"
    assert "plan_fingerprint_stale" in result.issue_codes


@pytest.mark.parametrize(
    "mutation",
    [
        "delete",
        "change",
        "wrong_hash",
        "manifest_extra",
        "manifest_duplicate",
        "committed_output_omitted",
    ],
)
def test_manifest_and_artifact_mutations_fail_closed(completed_run: Path, mutation: str) -> None:
    manifest_path = _a2_manifest_path(completed_run)
    manifest = _read_json(manifest_path)
    source_entries = manifest["source_artifacts"]
    target = next(item for item in source_entries if item["path"].endswith("claim_decision.json"))
    target_path = completed_run.joinpath(*target["path"].split("/"))
    if mutation == "delete":
        target_path.unlink()
    elif mutation == "change":
        target_path.write_bytes(target_path.read_bytes() + b"changed")
    elif mutation == "wrong_hash":
        target["sha256"] = "0" * 64
    elif mutation == "manifest_extra":
        extra = deepcopy(target)
        extra["path"] = f"runs/{RUN_ID}/artifacts/extra.json"
        extra_path = completed_run / extra["path"]
        extra_path.write_bytes(b"extra")
        extra["size_bytes"] = 5
        extra["sha256"] = hashlib.sha256(b"extra").hexdigest()
        source_entries.append(extra)
    elif mutation == "manifest_duplicate":
        source_entries.append(deepcopy(target))
    elif mutation == "committed_output_omitted":
        manifest["source_artifacts"] = [
            item for item in source_entries if item["path"] != target["path"]
        ]
    _write_json(manifest_path, manifest)

    result = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)

    assert result.status == "invalid"


def test_wrong_checkpoint_producer_is_invalid(completed_run: Path) -> None:
    _transfer_checkpoint_producer(completed_run)
    StateStore(completed_run).load(run_id=RUN_ID)

    result = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)

    assert result.status == "invalid"
    assert "checkpoint_producer_invalid" in result.issue_codes


def test_checkpoint_cannot_claim_fixed_publication_final_summary(completed_run: Path) -> None:
    _rebind_checkpoint_report_paths(
        completed_run,
        {
            f"run:{RUN_ID}:task:phase_a_growth_report:attempt:1:succeeded": (
                f"runs/{RUN_ID}/final_summary.md"
            )
        },
    )
    StateStore(completed_run).load(run_id=RUN_ID)

    result = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)

    assert result.status == "invalid"
    assert "checkpoint_producer_invalid" in result.issue_codes


def test_visualization_path_keeps_claim_visualization_producer(tmp_path: Path) -> None:
    resolver_pass = artifact_resolver._ResolverPass(tmp_path, RUN_ID)
    resolver_pass.task_operations["phase_a_claim_visualization"] = {
        "operation_id": "operation-visualization",
        "state_version": 7,
    }

    assert resolver_pass._infer_producer(
        f"runs/{RUN_ID}/staging/visualizations/recheck.png"
    ) == ("phase_a_claim_visualization", "operation-visualization", 7)


def test_completed_state_without_publication_is_invalid(completed_run: Path) -> None:
    _a2_manifest_path(completed_run).unlink()

    result = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)

    assert result.status == "invalid"


def test_unlisted_committed_artifact_is_invalid(completed_run: Path) -> None:
    extra = completed_run / "runs" / RUN_ID / "artifacts" / "unlisted.json"
    extra.write_bytes(b"unlisted")

    result = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)

    assert result.status == "invalid"
    assert "unlisted_committed_artifact" in result.issue_codes


def test_unlisted_formal_output_is_invalid(completed_run: Path) -> None:
    extra = completed_run / "outputs" / "unlisted-output.md"
    extra.write_bytes(b"unlisted")

    result = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)

    assert result.status == "invalid"
    assert "unlisted_committed_artifact" in result.issue_codes


def test_running_state_without_current_active_lock_requires_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "running-no-lock"
    root.mkdir()
    _make_incomplete_run(root, monkeypatch)
    (root / "runs" / ".active_run.lock").unlink()

    result = ArtifactResolver(root).resolve(run_id="run_001")

    assert result.status == "recovery_required"
    assert "active_lock_missing" in result.issue_codes


def test_running_state_and_active_lock_allocation_token_split_brain_requires_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "running-split-brain"
    root.mkdir()
    _make_incomplete_run(root, monkeypatch)
    lock_path = root / "runs" / ".active_run.lock"
    lock = _read_json(lock_path)
    lock["allocation_token"] = str(uuid.uuid4())
    lock_path.write_bytes(locking._canonical_json_bytes(lock))
    result = ArtifactResolver(root).resolve(run_id="run_001")

    assert result.status == "recovery_required"
    assert "active_lock_allocation_token_mismatch" in result.issue_codes


def test_other_run_active_lock_does_not_invalidate_historical_run(completed_run: Path) -> None:
    allocation_token = str(uuid.uuid4())
    lock_token = str(uuid.uuid4())
    acquire_active_run_lock(
        completed_run,
        task_id="task_other",
        allocation_token=allocation_token,
        lock_token=lock_token,
        created_at="2026-08-03T00:00:00.000000Z",
        pid=1,
        hostname="other",
    )
    reserve_active_run_id(
        completed_run,
        run_id="run_999",
        expected_allocation_token=allocation_token,
        expected_lock_token=lock_token,
    )
    result = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)

    assert result.status == "complete"


def test_running_state_with_unreserved_allocating_lock_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A RUNNING state with a lock that has reserved_run_id=None and
    phase='allocating' must be recovery_required, not silently pass."""
    root = tmp_path / "running-unreserved"
    root.mkdir()
    _make_incomplete_run(root, monkeypatch)
    lock_path = root / "runs" / ".active_run.lock"
    lock = _read_json(lock_path)
    lock["reserved_run_id"] = None
    lock["run_id"] = None
    lock["phase"] = "allocating"
    lock_path.write_bytes(locking._canonical_json_bytes(lock))
    result = ArtifactResolver(root).resolve(run_id="run_001")

    assert result.status == "recovery_required"
    assert "active_lock_unreserved" in result.issue_codes


def test_running_state_with_unreserved_running_lock_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "running-unreserved-running"
    root.mkdir()
    _make_incomplete_run(root, monkeypatch)
    lock_path = root / "runs" / ".active_run.lock"
    lock = _read_json(lock_path)
    lock["reserved_run_id"] = None
    lock["run_id"] = None
    lock["phase"] = "running"
    lock_path.write_bytes(locking._canonical_json_bytes(lock))

    result = ArtifactResolver(root).resolve(run_id="run_001")

    assert result.status == "recovery_required"
    assert "active_lock_unreserved" in result.issue_codes


def test_public_artifact_resolution_constructor_cannot_forge_complete() -> None:
    assert not hasattr(artifact_resolver.ArtifactResolution, "_from_factory")
    assert not hasattr(artifact_resolver, "_RESOLUTION_FACTORY_TOKEN")
    with pytest.raises(TypeError):
        artifact_resolver.ArtifactResolution()
    with pytest.raises(TypeError):
        artifact_resolver.ArtifactResolution(  # type: ignore[call-arg]
            run_id=RUN_ID,
            status="complete",
            inventory=(),
            inventory_bytes=b"{}",
            inventory_sha256=hashlib.sha256(b"{}").hexdigest(),
            state_version=None,
            plan_fingerprint=None,
            input_descriptor_sha256=None,
            issue_codes=(),
        )


def test_inventory_nested_values_are_deeply_frozen(tmp_path: Path) -> None:
    resolver_pass = artifact_resolver._ResolverPass(tmp_path, RUN_ID)
    resolver_pass.inventory_candidates[f"runs/{RUN_ID}/nested.json"] = {
        "task_id": "task_701",
        "artifact_role": "test",
        "path": f"runs/{RUN_ID}/nested.json",
        "size_bytes": 0,
        "sha256": "0" * 64,
        "producer_operation": "test:operation",
        "resulting_state_version": 0,
        "plan_fingerprint": None,
        "input_descriptor_sha256": None,
        "nested": {"items": [1, 2]},
    }
    result = resolver_pass.result()
    frozen = result.inventory[0]

    assert isinstance(frozen, MappingProxyType)
    assert isinstance(frozen["nested"], MappingProxyType)
    assert frozen["nested"]["items"] == (1, 2)
    with pytest.raises(TypeError):
        frozen["nested"]["forged"] = True  # type: ignore[index]


def test_artifact_resolution_post_init_rejects_status_without_issues() -> None:
    data = artifact_resolver._canonical_json_bytes(
        {
            "schema_version": artifact_resolver.INVENTORY_SCHEMA_VERSION,
            "run_id": RUN_ID,
            "artifacts": [],
        }
    )

    for status in ("invalid", "stale"):
        forged = object.__new__(artifact_resolver.ArtifactResolution)
        object.__setattr__(forged, "run_id", RUN_ID)
        object.__setattr__(forged, "status", status)
        object.__setattr__(forged, "inventory", ())
        object.__setattr__(forged, "inventory_bytes", data)
        object.__setattr__(forged, "inventory_sha256", hashlib.sha256(data).hexdigest())
        object.__setattr__(forged, "state_version", None)
        object.__setattr__(forged, "plan_fingerprint", None)
        object.__setattr__(forged, "input_descriptor_sha256", None)
        object.__setattr__(forged, "issue_codes", ())
        with pytest.raises(ValueError, match="status does not match issue codes"):
            forged.__post_init__()


def test_artifact_resolution_post_init_rejects_mutable_inventory() -> None:
    mutable_inventory = ({"path": f"runs/{RUN_ID}/nested.json", "nested": {"items": [1]}},)
    data = artifact_resolver._canonical_json_bytes(
        {
            "schema_version": artifact_resolver.INVENTORY_SCHEMA_VERSION,
            "run_id": RUN_ID,
            "artifacts": list(mutable_inventory),
        }
    )
    forged = object.__new__(artifact_resolver.ArtifactResolution)
    for field, value in {
        "run_id": RUN_ID,
        "status": "complete",
        "inventory": mutable_inventory,
        "inventory_bytes": data,
        "inventory_sha256": hashlib.sha256(data).hexdigest(),
        "state_version": None,
        "plan_fingerprint": None,
        "input_descriptor_sha256": None,
        "issue_codes": (),
    }.items():
        object.__setattr__(forged, field, value)

    with pytest.raises(ValueError, match="deeply frozen"):
        forged.__post_init__()


def test_guarded_read_uses_one_open_for_fixed_publication_file(
    completed_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = completed_run / "outputs" / "current_publication_manifest.json"
    opens = 0
    original_open = os.open

    def counted_open(path: str | os.PathLike[str], *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        nonlocal opens
        if Path(path) == target:
            opens += 1
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(artifact_resolver.os, "open", counted_open)
    data, snapshot = artifact_resolver._read_guarded_file(
        completed_run, "outputs/current_publication_manifest.json"
    )

    assert opens == 1
    assert snapshot["size_bytes"] == len(data)
    assert snapshot["sha256"] == hashlib.sha256(data).hexdigest()


def test_guarded_read_rejects_aba_leaf_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "aba-leaf"
    target = root / "runs" / RUN_ID / "artifacts" / "sample.bin"
    target.parent.mkdir(parents=True)
    original = b"original"
    replacement = b"replacement"
    target.write_bytes(original)
    backup = target.with_suffix(".backup")
    original_open = os.open
    original_fdopen = os.fdopen
    swapped = False

    def restore() -> None:
        target.unlink()
        backup.replace(target)

    def swapping_open(path: str | os.PathLike[str], *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        nonlocal swapped
        if Path(path) == target:
            target.replace(backup)
            target.write_bytes(replacement)
            swapped = True
        return original_open(path, *args, **kwargs)

    def restoring_fdopen(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        handle = original_fdopen(*args, **kwargs)
        return _RestoringHandle(handle, restore) if swapped else handle

    monkeypatch.setattr(artifact_resolver.os, "open", swapping_open)
    monkeypatch.setattr(artifact_resolver.os, "fdopen", restoring_fdopen)

    with pytest.raises(artifact_resolver.ArtifactResolutionError, match="opened object"):
        artifact_resolver._read_guarded_file(root, f"runs/{RUN_ID}/artifacts/sample.bin", run_id=RUN_ID)
    assert target.read_bytes() == original


def test_guarded_read_rejects_parent_directory_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "aba-parent"
    target = root / "runs" / RUN_ID / "artifacts" / "sample.bin"
    parent = target.parent
    parent.mkdir(parents=True)
    original = b"original"
    replacement = b"replacement"
    target.write_bytes(original)
    backup_parent = parent.with_name("artifacts-backup")
    original_open = os.open
    original_fdopen = os.fdopen
    swapped = False

    def restore() -> None:
        target.unlink()
        parent.rmdir()
        backup_parent.replace(parent)

    def swapping_open(path: str | os.PathLike[str], *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        nonlocal swapped
        if Path(path) == target:
            parent.replace(backup_parent)
            parent.mkdir()
            target.write_bytes(replacement)
            swapped = True
        return original_open(path, *args, **kwargs)

    def restoring_fdopen(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        handle = original_fdopen(*args, **kwargs)
        return _RestoringHandle(handle, restore) if swapped else handle

    monkeypatch.setattr(artifact_resolver.os, "open", swapping_open)
    monkeypatch.setattr(artifact_resolver.os, "fdopen", restoring_fdopen)

    with pytest.raises(artifact_resolver.ArtifactResolutionError, match="opened object"):
        artifact_resolver._read_guarded_file(root, f"runs/{RUN_ID}/artifacts/sample.bin", run_id=RUN_ID)
    assert target.read_bytes() == original


def test_guarded_read_rejects_opened_reparse_handle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "reparse-handle"
    target = root / "runs" / RUN_ID / "artifacts" / "sample.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"contents")
    original_fstat = os.fstat

    def reparse_fstat(fd: int) -> SimpleNamespace:
        entry = original_fstat(fd)
        return SimpleNamespace(
            st_mode=entry.st_mode,
            st_ino=entry.st_ino,
            st_dev=entry.st_dev,
            st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400),
        )

    monkeypatch.setattr(artifact_resolver.os, "fstat", reparse_fstat)

    with pytest.raises(artifact_resolver.ArtifactResolutionError, match="reparse"):
        artifact_resolver._read_guarded_file(root, f"runs/{RUN_ID}/artifacts/sample.bin", run_id=RUN_ID)


def test_guarded_read_rejects_temporary_symlink_when_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "temporary-symlink"
    target = root / "runs" / RUN_ID / "artifacts" / "sample.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"original")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    backup = target.with_suffix(".backup")
    original_open = os.open

    def swapping_open(path: str | os.PathLike[str], *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if Path(path) != target:
            return original_open(path, *args, **kwargs)
        target.replace(backup)
        try:
            target.symlink_to(outside)
        except (OSError, NotImplementedError):
            backup.replace(target)
            pytest.skip("symlink creation is unavailable")
        try:
            return original_open(path, *args, **kwargs)
        finally:
            target.unlink()
            backup.replace(target)

    monkeypatch.setattr(artifact_resolver.os, "open", swapping_open)

    with pytest.raises(artifact_resolver.ArtifactResolutionError):
        artifact_resolver._read_guarded_file(root, f"runs/{RUN_ID}/artifacts/sample.bin", run_id=RUN_ID)
    assert target.read_bytes() == b"original"


def test_release_tombstone_is_recovery_required(completed_run: Path) -> None:
    tombstone = completed_run / "runs" / ".active_run.release.test.json"
    tombstone.write_bytes(b"tombstone")

    result = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)

    assert result.status == "recovery_required"


def test_directory_instead_of_artifact_is_invalid(completed_run: Path) -> None:
    target = completed_run / "runs" / RUN_ID / "staging" / "priority_recheck_list.csv"
    target.unlink()
    target.mkdir()

    result = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)

    assert result.status == "invalid"


def test_reparse_simulation_is_invalid(completed_run: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = completed_run / "runs" / RUN_ID / "artifacts" / "claim_decision.json"
    original = a1_artifacts._path_is_reparse_point

    def simulated(path: Path) -> bool:
        return path == target or original(path)

    monkeypatch.setattr(a1_artifacts, "_path_is_reparse_point", simulated)
    result = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)

    assert result.status == "invalid"


def test_unreadable_known_artifact_is_invalid(completed_run: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = f"runs/{RUN_ID}/artifacts/claim_decision.json"
    original = artifact_resolver._snapshot_file

    def unreadable(root: Path, relative: str) -> object:
        if relative == target:
            raise OSError("permission denied")
        return original(root, relative)

    monkeypatch.setattr(artifact_resolver, "_snapshot_file", unreadable)
    result = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)

    assert result.status == "invalid"


def test_path_injection_is_rejected_before_resolution(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()

    with pytest.raises(ArtifactResolverInputError):
        ArtifactResolver(root).resolve(run_id="../run_001")


def test_symlink_artifact_is_invalid_when_supported(completed_run: Path, tmp_path: Path) -> None:
    target = completed_run / "runs" / RUN_ID / "artifacts" / "claim_decision.json"
    backup = tmp_path / "claim-decision-copy.json"
    backup.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(backup)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")

    result = ArtifactResolver(completed_run).resolve(run_id=RUN_ID)

    assert result.status == "invalid"
