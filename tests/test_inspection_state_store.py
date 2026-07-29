from __future__ import annotations

from collections.abc import Mapping
import csv
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

import pytest
from PIL import Image

from orchestrator.agents.association_agent import AssociationAgent
from orchestrator.agents.claim_gate_agent import ClaimGateAgent
from orchestrator.agents.claim_visualization_agent import ClaimVisualizationAgent
from orchestrator.agents.comparison_evidence_agent import ComparisonEvidenceAgent
from orchestrator.agents.engineering_claim_report_agent import EngineeringClaimReportAgent
from orchestrator.agents.growth_report_agent import GrowthReportAgent
from orchestrator.agents.memory_report_agent import MemoryReportAgent
from orchestrator.inspection_workflow import initialize_phase_a1_sandbox
from orchestrator.inspection_workflow import publication

from orchestrator.inspection_workflow.locking import (
    ActiveRunLockError,
    acquire_active_run_lock,
    mark_active_run_running,
    recover_stale_active_run,
    reserve_active_run_id,
)
from orchestrator.inspection_workflow import locking
from orchestrator.state import store as state_module
from orchestrator.state.store import (
    COMPLETION_EVIDENCE_SCHEMA_VERSION,
    StateConflictError,
    StateRecoveryDeferredError,
    StateRecoveryRequiredError,
    StateStore,
    StateStoreError,
)
from scripts import prepare_real_inspection_pilot as preparation


T0 = "2026-07-27T00:00:00.000000Z"
TASK_PLAN = [{"task_id": "core", "deps": [], "required": True}]
PLAN_SHA = "a" * 64


def _new_allocation(root: Path) -> tuple[StateStore, str, str]:
    allocation_token = str(uuid.uuid4())
    lock_token = str(uuid.uuid4())
    acquire_active_run_lock(
        root,
        task_id="task_001",
        allocation_token=allocation_token,
        lock_token=lock_token,
        created_at=T0,
        pid=12345,
        hostname="test-host",
    )
    reserve_active_run_id(
        root,
        run_id="run_001",
        expected_allocation_token=allocation_token,
        expected_lock_token=lock_token,
    )
    (root / "runs" / "run_001").mkdir()
    return StateStore(root), allocation_token, lock_token


def _initialized(root: Path) -> tuple[StateStore, str, str]:
    store, allocation_token, lock_token = _new_allocation(root)
    store.initialize_run(
        run_id="run_001",
        allocation_token=allocation_token,
        plan_fingerprint=PLAN_SHA,
        task_plan=TASK_PLAN,
        expected_lock_token=lock_token,
        created_at=T0,
    )
    mark_active_run_running(
        root,
        run_id="run_001",
        expected_allocation_token=allocation_token,
        expected_lock_token=lock_token,
    )
    return store, allocation_token, lock_token


def _transition(
    store: StateStore,
    lock_token: str,
    *,
    version: int,
    current: str,
    next_status: str,
    second: int,
    completion_evidence: Mapping[str, object] | None = None,
) -> Mapping[str, object]:
    timestamp = f"2026-07-27T00:00:{second:02d}.000000Z"
    operation_id = f"run:run_001:transition:v{version}:{current}:{next_status}:auto"
    return store.transition_status(
        run_id="run_001",
        expected_lock_token=lock_token,
        expected_status=current,
        expected_state_version=version,
        operation_id=operation_id,
        mutation_timestamp=timestamp,
        payload={
            "next_status": next_status,
            "metadata": None,
            "completion_evidence": completion_evidence,
            "transition_kind": "auto",
            "decision_token": None,
        },
    )


def _checkpoint(
    store: StateStore,
    lock_token: str,
    *,
    version: int,
    status: str,
    kind: str,
    second: int,
    task_id: str | None = "core",
    attempt: int | None = 0,
    retry_disposition: str | None = None,
    payload_override: Mapping[str, object] | None = None,
) -> Mapping[str, object]:
    timestamp = f"2026-07-27T00:00:{second:02d}.000000Z"
    if kind == "run_initialized":
        operation_id = "run:run_001:checkpoint:run_initialized"
        payload: dict[str, object] = {
            "checkpoint_kind": kind,
            "task_id": None,
            "attempt_number": None,
            "expected_task_status": None,
            "next_task_status": None,
            "retry_disposition": "none",
            "next_attempt_number": None,
            "controlled_context_delta": {
                "task_plan": TASK_PLAN,
                "plan_fingerprint": PLAN_SHA,
            },
            "error_summary": None,
            "created_at": timestamp,
        }
    else:
        suffix = {
            "task_skipped": "skipped",
            "task_cache_hit": "cache_hit",
            "task_started": "started",
            "task_succeeded": "succeeded",
            "task_failed": "failed",
            "task_retry_scheduled": "retry_scheduled",
        }[kind]
        operation_id = f"run:run_001:task:{task_id}:attempt:{attempt}:{suffix}"
        payload = {
            "checkpoint_kind": kind,
            "task_id": task_id,
            "attempt_number": attempt,
            "expected_task_status": (
                "pending" if kind in {"task_skipped", "task_cache_hit"} else
                ("pending" if kind == "task_started" and attempt == 1 else
                 "retry_scheduled" if kind == "task_started" else
                 "running" if kind in {"task_succeeded", "task_failed"} else
                 "retry_pending")
            ),
            "next_task_status": {
                "task_skipped": "skipped",
                "task_cache_hit": "success",
                "task_started": "running",
                "task_succeeded": "success",
                "task_failed": "retry_pending" if retry_disposition == "retry" else "failed",
                "task_retry_scheduled": "retry_scheduled",
            }[kind],
            "retry_disposition": retry_disposition if kind == "task_failed" else (
                "retry" if kind == "task_retry_scheduled" else "none"
            ),
            "next_attempt_number": None,
            "controlled_context_delta": {},
            "error_summary": None,
            "created_at": timestamp,
        }
        if kind == "task_skipped":
            payload["controlled_context_delta"] = {"skip_reason": "test skip"}
        elif kind == "task_cache_hit":
            payload["controlled_context_delta"] = {
                "task_output": {"artifact": "cache"},
                "cache_provenance": {"source": "test"},
            }
        elif kind == "task_succeeded":
            payload["controlled_context_delta"] = {"task_output": {"artifact": "test"}}
        elif kind == "task_failed":
            payload["controlled_context_delta"] = {"failure_provenance": {"source": "test"}}
            payload["error_summary"] = "test failure"
        elif kind == "task_retry_scheduled":
            payload["controlled_context_delta"] = {
                "failed_operation_id": f"run:run_001:task:{task_id}:attempt:{attempt}:failed",
                "retry_policy_sha256": "b" * 64,
                "backoff_seconds": 0,
            }
        if kind == "task_failed":
            payload["retry_disposition"] = retry_disposition
            payload["next_attempt_number"] = (
                attempt + 1 if retry_disposition == "retry" and isinstance(attempt, int) else None
            )
        if kind == "task_retry_scheduled":
            payload["next_attempt_number"] = attempt + 1 if isinstance(attempt, int) else None
    if payload_override is not None:
        payload.update(deepcopy(dict(payload_override)))
    return store.checkpoint_context(
        run_id="run_001",
        expected_lock_token=lock_token,
        expected_status=status,
        expected_state_version=version,
        operation_id=operation_id,
        mutation_timestamp=timestamp,
        payload=payload,
    )


def _planned_with_tasks(root: Path) -> tuple[StateStore, str, int]:
    store, _, lock_token = _initialized(root)
    _transition(store, lock_token, version=0, current="CREATED", next_status="PLANNED", second=1)
    result = _checkpoint(
        store,
        lock_token,
        version=1,
        status="PLANNED",
        kind="run_initialized",
        second=2,
        task_id=None,
        attempt=None,
    )
    return store, lock_token, result["resulting_state_version"]


def _write_canonical(path: Path, value: object) -> bytes:
    data = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def _completion_fixture(
    root: Path,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    dataset = root / "dataset"
    work = root / "runs" / "run_001" / "work"
    (dataset / "images").mkdir(parents=True)
    (dataset / "masks").mkdir()
    Image.new("RGB", (4, 4), color=(90, 100, 110)).save(dataset / "images/a.jpg")
    mask = Image.new("L", (4, 4), color=0)
    mask.putpixel((1, 1), 255)
    mask.save(dataset / "masks/a.png")
    Image.new("RGB", (4, 4), color=(40, 50, 60)).save(dataset / "images/b.jpg")
    Image.new("L", (4, 4), color=255).save(dataset / "masks/b.png")
    rows = [
        {
            "sequence_id": "S01", "source_inspection_id": "visit_1", "frame_id": "1",
            "timestamp": "2026-07-01T10:00:00Z", "mileage_m": "12.0", "ring_id": "1",
            "clock_direction": "12点", "image_file": "images/a.jpg", "mask_file": "masks/a.png",
            "local_observation_id": "obs_01", "disease_type": "crack",
        },
        {
            "sequence_id": "S01", "source_inspection_id": "visit_2", "frame_id": "2",
            "timestamp": "2026-07-02T10:00:00Z", "mileage_m": "10000.0", "ring_id": "9000",
            "clock_direction": "6点", "image_file": "images/b.jpg", "mask_file": "masks/b.png",
            "local_observation_id": "obs_02", "disease_type": "crack",
        },
    ]
    with (dataset / "metadata.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=preparation.REQUIRED_METADATA_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    initialize_phase_a1_sandbox(
        root, run_id="run_001", evidence_source_mode="run_local_projection"
    )
    prepared = work / "raw_prepared"
    preparation.prepare_real_inspection_pilot(dataset, prepared)
    history = work / "raw_history"
    AssociationAgent().run(
        {
            "inputs": {"association": {
                "history_only": "true",
                "frame_records": str(prepared / "frame_records.csv"),
                "output_path": str(history / "association_records.csv"),
                "history_output_dir": str(history / "main_progressive"),
                "manifest_path": str(history / "association_manifest.json"),
                "use_disease_id_score": "false",
                "association_mode": "no_id",
            }},
            "outputs": {},
            "shared": {"project_root": str(root)},
        }
    )
    context = {
        "shared": {
            "project_root": str(root), "run_id": "run_001",
            "execution_profile": "phase_a1_sandbox", "plan_fingerprint": PLAN_SHA,
        },
        "inputs": {"comparison_evidence": {
            "projection_mode": "prepared_history_sources",
            "prepared_manifest_path": "runs/run_001/work/raw_prepared/preparation_manifest.json",
            "history_association_path": "runs/run_001/work/raw_history/association_records.csv",
            "history_manifest_path": "runs/run_001/work/raw_history/association_manifest.json",
        }},
    }
    ComparisonEvidenceAgent().run(context)
    ClaimGateAgent().run(context)
    report_context = {"shared": deepcopy(context["shared"]), "inputs": {}}
    GrowthReportAgent().run(report_context)
    MemoryReportAgent().run(report_context)
    EngineeringClaimReportAgent().run(report_context)
    ClaimVisualizationAgent().run(report_context)
    published = publication.publish_run_local_artifacts(
        root, run_id="run_001", plan_fingerprint=PLAN_SHA
    )
    manifest = published["manifest"]
    transaction = published["transaction"]
    manifest_data = (root / publication.PUBLICATION_MANIFEST_PATH).read_bytes()
    transaction_path = root / f"runs/run_001/publication_transaction.json"
    transaction_data = transaction_path.read_bytes()
    summary_data = (root / "runs/run_001/final_summary.md").read_bytes()
    evidence = {
        "schema_version": COMPLETION_EVIDENCE_SCHEMA_VERSION,
        "run_id": "run_001",
        "plan_fingerprint": PLAN_SHA,
        "required_task_ids": ["core"],
        "publication_manifest_path": "outputs/current_publication_manifest.json",
        "publication_manifest_sha256": hashlib.sha256(manifest_data).hexdigest(),
        "publication_transaction_path": "runs/run_001/publication_transaction.json",
        "publication_transaction_sha256": hashlib.sha256(transaction_data).hexdigest(),
        "transaction_id": manifest["transaction_id"],
        "final_summary_path": "runs/run_001/final_summary.md",
        "final_summary_sha256": hashlib.sha256(summary_data).hexdigest(),
    }
    return evidence, transaction, manifest


def test_initialize_creates_state_and_genesis_anchor(tmp_path: Path) -> None:
    store, allocation_token, lock_token = _new_allocation(tmp_path)
    snapshot = store.initialize_run(
        run_id="run_001",
        allocation_token=allocation_token,
        plan_fingerprint=PLAN_SHA,
        task_plan=TASK_PLAN,
        expected_lock_token=lock_token,
        created_at=T0,
    )
    assert snapshot["status"] == "CREATED"
    assert snapshot["state_version"] == 0
    assert "operation_id" not in snapshot
    state = snapshot["canonical_state"]
    assert state["last_operation_id"] is None
    anchor = json.loads((tmp_path / "runs" / "run_001" / "state_journal_tail.json").read_bytes())
    assert anchor["tail_record_index"] is None
    assert anchor["tail_file_size_bytes"] == 0


@pytest.mark.parametrize("forged_status", ["RUNNING", "COMPLETED"])
def test_empty_journal_accepts_only_the_created_baseline(
    tmp_path: Path, forged_status: str
) -> None:
    store, _, lock_token = _initialized(tmp_path)
    state_path = tmp_path / "runs" / "run_001" / "state.json"
    state = json.loads(state_path.read_bytes())
    state["status"] = forged_status
    _write_canonical(state_path, state)

    with pytest.raises(StateConflictError, match="valid uncommitted CREATED baseline"):
        store.load(run_id="run_001")
    with pytest.raises(StateConflictError, match="valid uncommitted CREATED baseline"):
        store.recover(run_id="run_001", expected_lock_token=lock_token)
    with pytest.raises(StateConflictError, match="valid uncommitted CREATED baseline"):
        _transition(
            store,
            lock_token,
            version=0,
            current=forged_status,
            next_status="FAILED",
            second=1,
        )


def test_initialize_rejects_bool_version_fields_and_optional_core_task(tmp_path: Path) -> None:
    store, allocation_token, lock_token = _new_allocation(tmp_path)
    with pytest.raises(StateStoreError, match="all be required"):
        store.initialize_run(
            run_id="run_001",
            allocation_token=allocation_token,
            plan_fingerprint=PLAN_SHA,
            task_plan=[{"task_id": "core", "deps": [], "required": False}],
            expected_lock_token=lock_token,
            created_at=T0,
        )


def test_state_only_partial_initialization_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, allocation_token, lock_token = _new_allocation(tmp_path)

    def fail_anchor(run_id: str, anchor: Mapping[str, object]) -> None:
        raise StateStoreError("injected anchor failure")

    monkeypatch.setattr(store, "_write_anchor", fail_anchor)
    with pytest.raises(StateStoreError, match="anchor failure"):
        store.initialize_run(
            run_id="run_001",
            allocation_token=allocation_token,
            plan_fingerprint=PLAN_SHA,
            task_plan=TASK_PLAN,
            expected_lock_token=lock_token,
            created_at=T0,
        )
    assert (tmp_path / "runs" / "run_001" / "state.json").is_file()
    assert not (tmp_path / "runs" / "run_001" / "state_journal_tail.json").exists()
    monkeypatch.undo()
    with pytest.raises(StateConflictError, match="existing state"):
        store.initialize_run(
            run_id="run_001",
            allocation_token=allocation_token,
            plan_fingerprint=PLAN_SHA,
            task_plan=TASK_PLAN,
            expected_lock_token=lock_token,
            created_at=T0,
        )


def test_anchor_only_and_non_genesis_anchor_fail_closed(tmp_path: Path) -> None:
    for non_genesis in (False, True):
        root = tmp_path / ("non-genesis" if non_genesis else "anchor-only")
        root.mkdir()
        store, allocation_token, lock_token = _new_allocation(root)
        anchor = store._anchor_document(
            run_id="run_001",
            allocation_token=allocation_token,
            tail_record_index=0 if non_genesis else None,
            tail_record_checksum="b" * 64 if non_genesis else None,
            tail_file_size_bytes=10 if non_genesis else 0,
        )
        _write_canonical(root / "runs" / "run_001" / "state_journal_tail.json", anchor)
        with pytest.raises(StateConflictError, match="existing anchor"):
            store.initialize_run(
                run_id="run_001",
                allocation_token=allocation_token,
                plan_fingerprint=PLAN_SHA,
                task_plan=TASK_PLAN,
                expected_lock_token=lock_token,
                created_at=T0,
            )


def test_nonempty_initial_journal_is_rejected(tmp_path: Path) -> None:
    store, allocation_token, lock_token = _new_allocation(tmp_path)
    (tmp_path / "runs" / "run_001" / "state_journal.jsonl").write_bytes(b"{}\n")
    with pytest.raises(StateConflictError, match="must be empty"):
        store.initialize_run(
            run_id="run_001",
            allocation_token=allocation_token,
            plan_fingerprint=PLAN_SHA,
            task_plan=TASK_PLAN,
            expected_lock_token=lock_token,
            created_at=T0,
        )


def test_transition_and_checkpoint_use_cas_and_lock_fencing(tmp_path: Path) -> None:
    store, _, lock_token = _initialized(tmp_path)
    with pytest.raises(StateConflictError, match="fencing"):
        store.transition_status(
            run_id="run_001",
            expected_lock_token=str(uuid.uuid4()),
            expected_status="CREATED",
            expected_state_version=0,
            operation_id="run:run_001:transition:v0:CREATED:PLANNED:auto",
            mutation_timestamp="2026-07-27T00:00:01.000000Z",
            payload={
                "next_status": "PLANNED",
                "metadata": None,
                "completion_evidence": None,
                "transition_kind": "auto",
                "decision_token": None,
            },
        )
    _transition(store, lock_token, version=0, current="CREATED", next_status="PLANNED", second=1)
    with pytest.raises(StateConflictError, match="CAS"):
        _checkpoint(
            store,
            lock_token,
            version=0,
            status="PLANNED",
            kind="run_initialized",
            second=2,
            task_id=None,
            attempt=None,
        )


def test_checkpoint_attempts_are_canonical_and_retry_is_contiguous(tmp_path: Path) -> None:
    store, lock_token, version = _planned_with_tasks(tmp_path)
    _transition(store, lock_token, version=version, current="PLANNED", next_status="RUNNING", second=3)
    version += 1
    _checkpoint(store, lock_token, version=version, status="RUNNING", kind="task_started", second=4, attempt=1)
    version += 1
    _checkpoint(
        store,
        lock_token,
        version=version,
        status="RUNNING",
        kind="task_failed",
        second=5,
        attempt=1,
        retry_disposition="retry",
    )
    version += 1
    _checkpoint(
        store,
        lock_token,
        version=version,
        status="RUNNING",
        kind="task_retry_scheduled",
        second=6,
        attempt=1,
    )
    version += 1
    with pytest.raises(StateConflictError, match="next canonical attempt"):
        _checkpoint(
            store,
            lock_token,
            version=version,
            status="RUNNING",
            kind="task_started",
            second=7,
            attempt=3,
        )
    result = _checkpoint(
        store,
        lock_token,
        version=version,
        status="RUNNING",
        kind="task_started",
        second=8,
        attempt=2,
    )
    assert result["canonical_state"]["task_attempts"]["core"] == 2


def test_checkpoint_lifecycle_rejects_wrong_run_status(tmp_path: Path) -> None:
    store, _, lock_token = _initialized(tmp_path)
    with pytest.raises(StateConflictError, match="only allowed in PLANNED"):
        _checkpoint(
            store,
            lock_token,
            version=0,
            status="CREATED",
            kind="run_initialized",
            second=1,
            task_id=None,
            attempt=None,
        )

    _transition(store, lock_token, version=0, current="CREATED", next_status="PLANNED", second=2)
    initialized = _checkpoint(
        store,
        lock_token,
        version=1,
        status="PLANNED",
        kind="run_initialized",
        second=3,
        task_id=None,
        attempt=None,
    )
    with pytest.raises(StateConflictError, match="only allowed in RUNNING"):
        _checkpoint(
            store,
            lock_token,
            version=initialized["resulting_state_version"],
            status="PLANNED",
            kind="task_started",
            second=4,
            attempt=1,
        )


def test_checkpoint_rejects_free_context_and_missing_success_provenance(tmp_path: Path) -> None:
    store, lock_token, version = _planned_with_tasks(tmp_path)
    _transition(store, lock_token, version=version, current="PLANNED", next_status="RUNNING", second=3)
    version += 1
    _checkpoint(
        store, lock_token, version=version, status="RUNNING", kind="task_started", second=4, attempt=1
    )
    version += 1
    with pytest.raises(StateStoreError, match="controlled_context_delta fields"):
        _checkpoint(
            store,
            lock_token,
            version=version,
            status="RUNNING",
            kind="task_succeeded",
            second=5,
            attempt=1,
            payload_override={
                "controlled_context_delta": {
                    "task_output": {"artifact": "test"},
                    "task_status": {"other": "success"},
                }
            },
        )
    with pytest.raises(StateStoreError, match="non-empty task_output"):
        _checkpoint(
            store,
            lock_token,
            version=version,
            status="RUNNING",
            kind="task_succeeded",
            second=6,
            attempt=1,
            payload_override={"controlled_context_delta": {"task_output": {}}},
        )


def test_checkpoint_failed_error_summary_is_bounded_and_path_neutral(tmp_path: Path) -> None:
    store, lock_token, version = _planned_with_tasks(tmp_path)
    _transition(store, lock_token, version=version, current="PLANNED", next_status="RUNNING", second=3)
    version += 1
    _checkpoint(
        store, lock_token, version=version, status="RUNNING", kind="task_started", second=4, attempt=1
    )
    version += 1
    with pytest.raises(StateStoreError, match="path-neutral"):
        _checkpoint(
            store,
            lock_token,
            version=version,
            status="RUNNING",
            kind="task_failed",
            second=5,
            attempt=1,
            retry_disposition="terminal",
            payload_override={"error_summary": r"failed at C:\Users\developer\secret.csv"},
        )


@pytest.mark.parametrize(
    ("kind", "expected_status"),
    [("task_skipped", "skipped"), ("task_cache_hit", "success")],
)
def test_zero_attempt_terminal_checkpoints_follow_closed_contract(
    tmp_path: Path, kind: str, expected_status: str
) -> None:
    store, lock_token, version = _planned_with_tasks(tmp_path)
    _transition(store, lock_token, version=version, current="PLANNED", next_status="RUNNING", second=3)
    result = _checkpoint(
        store,
        lock_token,
        version=version + 1,
        status="RUNNING",
        kind=kind,
        second=4,
        attempt=0,
    )
    assert result["canonical_state"]["task_status"]["core"] == expected_status


def test_pending_state_write_failure_requires_recovery_and_aborts_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _, lock_token = _initialized(tmp_path)
    original = store._atomic_replace

    def fail_state(path: Path, data: bytes, *, label: str) -> None:
        if label == "canonical state" and path.exists():
            raise StateStoreError("injected state replace failure")
        original(path, data, label=label)

    monkeypatch.setattr(store, "_atomic_replace", fail_state)
    with pytest.raises(StateStoreError, match="state replace failure"):
        _transition(store, lock_token, version=0, current="CREATED", next_status="PLANNED", second=1)
    monkeypatch.setattr(store, "_atomic_replace", original)
    with pytest.raises(StateRecoveryRequiredError, match="unresolved pending"):
        store.load(run_id="run_001")
    snapshot = store.recover_state_journal(run_id="run_001", expected_lock_token=lock_token)
    assert snapshot["state_version"] == 0
    rows = [json.loads(line) for line in (tmp_path / "runs" / "run_001" / "state_journal.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["phase"] for row in rows] == ["pending", "aborted"]
    with pytest.raises(StateConflictError, match="aborted operation_id"):
        _transition(store, lock_token, version=0, current="CREATED", next_status="PLANNED", second=1)


def test_takeover_terminal_row_preserves_owner_and_binds_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _, old_lock_token = _initialized(tmp_path)
    original = store._atomic_replace

    def fail_state(path: Path, data: bytes, *, label: str) -> None:
        if label == "canonical state" and path.exists():
            raise StateStoreError("injected stale owner state failure")
        original(path, data, label=label)

    monkeypatch.setattr(store, "_atomic_replace", fail_state)
    with pytest.raises(StateStoreError, match="stale owner state failure"):
        _transition(store, old_lock_token, version=0, current="CREATED", next_status="PLANNED", second=1)
    monkeypatch.setattr(store, "_atomic_replace", original)
    initialize_phase_a1_sandbox(tmp_path, run_id="run_001")
    new_lock_token = str(uuid.uuid4())
    recovery_token = str(uuid.uuid4())
    monkeypatch.setattr(locking.socket, "gethostname", lambda: "test-host")
    monkeypatch.setattr(locking, "_owner_pid_is_confirmed_dead", lambda _pid: True)

    result = recover_stale_active_run(
        tmp_path,
        run_id="run_001",
        recovery_token=recovery_token,
        new_lock_token=new_lock_token,
    )

    assert result["successor_phase"] == "running"
    rows = [
        json.loads(line)
        for line in (tmp_path / "runs" / "run_001" / "state_journal.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert [row["phase"] for row in rows] == ["pending", "aborted"]
    assert rows[1]["operation_owner_lock_token"] == old_lock_token
    assert rows[1]["append_actor_lock_token"] == new_lock_token
    assert rows[1]["recovery_audit_ref"] is not None
    with pytest.raises(StateConflictError, match="fencing"):
        _transition(store, old_lock_token, version=0, current="CREATED", next_status="PLANNED", second=2)


def test_recovery_terminal_requires_immutable_matching_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _, old_lock_token = _initialized(tmp_path)
    original = store._atomic_replace

    def fail_state(path: Path, data: bytes, *, label: str) -> None:
        if label == "canonical state" and path.exists():
            raise StateStoreError("injected stale owner state failure")
        original(path, data, label=label)

    monkeypatch.setattr(store, "_atomic_replace", fail_state)
    with pytest.raises(StateStoreError, match="stale owner state failure"):
        _transition(store, old_lock_token, version=0, current="CREATED", next_status="PLANNED", second=1)
    monkeypatch.setattr(store, "_atomic_replace", original)
    initialize_phase_a1_sandbox(tmp_path, run_id="run_001")
    monkeypatch.setattr(locking.socket, "gethostname", lambda: "test-host")
    monkeypatch.setattr(locking, "_owner_pid_is_confirmed_dead", lambda _pid: True)
    recover_stale_active_run(
        tmp_path,
        run_id="run_001",
        recovery_token=str(uuid.uuid4()),
        new_lock_token=str(uuid.uuid4()),
    )
    assert store.load(run_id="run_001")["state_version"] == 0
    journal_row = json.loads(
        (tmp_path / "runs" / "run_001" / "state_journal.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()[-1]
    )
    intent_path = tmp_path / Path(*journal_row["recovery_audit_ref"]["path"].split("/"))
    intent = json.loads(intent_path.read_text(encoding="utf-8"))
    intent["old_lock_token"] = str(uuid.uuid4())
    intent["intent_checksum"] = locking._sha256(
        locking._canonical_json_bytes(
            {key: value for key, value in intent.items() if key != "intent_checksum"}
        )
    )
    intent_path.write_bytes(locking._canonical_json_bytes(intent))

    with pytest.raises(StateConflictError, match="recovery_audit_ref SHA-256 does not match"):
        store.load(run_id="run_001")


def test_terminal_state_takeover_freezes_and_releases_the_active_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _, old_lock_token = _initialized(tmp_path)
    _transition(store, old_lock_token, version=0, current="CREATED", next_status="FAILED", second=1)
    initialize_phase_a1_sandbox(tmp_path, run_id="run_001")
    monkeypatch.setattr(locking.socket, "gethostname", lambda: "test-host")
    monkeypatch.setattr(locking, "_owner_pid_is_confirmed_dead", lambda _pid: True)
    recovery_token = str(uuid.uuid4())
    new_lock_token = str(uuid.uuid4())

    result = recover_stale_active_run(
        tmp_path,
        run_id="run_001",
        recovery_token=recovery_token,
        new_lock_token=new_lock_token,
    )

    assert result == {"replayed": False, "successor_phase": "released"}
    assert not (tmp_path / "runs" / ".active_run.lock").exists()
    outcome_path = next(
        (tmp_path / "runs" / "run_001" / "lock_recovery_audit").glob("*.outcome.1.json")
    )
    assert json.loads(outcome_path.read_text(encoding="utf-8"))["successor_phase"] == "released"
    tombstone = tmp_path / "runs" / f".active_run.release.{new_lock_token}.json"
    tombstone.write_bytes(b"preserved recovery residue")
    with pytest.raises(ActiveRunLockError, match="release tombstone"):
        recover_stale_active_run(
            tmp_path,
            run_id="run_001",
            recovery_token=recovery_token,
            new_lock_token=new_lock_token,
        )


def test_committed_append_before_anchor_is_recovered_without_new_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _, lock_token = _initialized(tmp_path)
    original = store._write_anchor
    calls = 0

    def fail_second_anchor(run_id: str, anchor: Mapping[str, object]) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise StateStoreError("injected committed anchor failure")
        original(run_id, anchor)

    monkeypatch.setattr(store, "_write_anchor", fail_second_anchor)
    with pytest.raises(StateStoreError, match="committed anchor failure"):
        _transition(store, lock_token, version=0, current="CREATED", next_status="PLANNED", second=1)
    monkeypatch.setattr(store, "_write_anchor", original)
    snapshot = store.recover_state_journal(run_id="run_001", expected_lock_token=lock_token)
    assert snapshot["status"] == "PLANNED"
    assert snapshot["canonical_state"]["updated_at"] == "2026-07-27T00:00:01.000000Z"
    assert store.load(run_id="run_001")["state_version"] == 1


@pytest.mark.parametrize(
    ("failed_anchor_call", "expected_phases", "expected_status"),
    [
        (1, ["pending", "aborted"], "CREATED"),
        (2, ["pending", "committed"], "PLANNED"),
    ],
)
def test_stale_takeover_recovers_one_unanchored_direct_successor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_anchor_call: int,
    expected_phases: list[str],
    expected_status: str,
) -> None:
    store, _, old_lock_token = _initialized(tmp_path)
    original_write_anchor = store._write_anchor
    calls = 0

    def fail_one_anchor(run_id: str, anchor: Mapping[str, object]) -> None:
        nonlocal calls
        calls += 1
        if calls == failed_anchor_call:
            raise StateStoreError("injected unanchored direct successor")
        original_write_anchor(run_id, anchor)

    monkeypatch.setattr(store, "_write_anchor", fail_one_anchor)
    with pytest.raises(StateStoreError, match="unanchored direct successor"):
        _transition(
            store,
            old_lock_token,
            version=0,
            current="CREATED",
            next_status="PLANNED",
            second=1,
        )
    monkeypatch.setattr(store, "_write_anchor", original_write_anchor)

    initialize_phase_a1_sandbox(tmp_path, run_id="run_001")
    monkeypatch.setattr(locking.socket, "gethostname", lambda: "test-host")
    monkeypatch.setattr(locking, "_owner_pid_is_confirmed_dead", lambda _pid: True)
    original_recover_taken_over = StateStore.recover_taken_over_state_journal
    recovery_order_checked = False

    def verify_recovery_order(self, **kwargs):
        nonlocal recovery_order_checked
        run_dir = tmp_path / "runs" / "run_001"
        active = json.loads((tmp_path / "runs" / ".active_run.lock").read_bytes())
        anchor = json.loads((run_dir / "state_journal_tail.json").read_bytes())
        journal = (run_dir / "state_journal.jsonl").read_bytes()
        intents = list((run_dir / "lock_recovery_audit").glob("*.intent.json"))
        assert active["phase"] == "recovering"
        assert len(intents) == 1
        assert anchor["tail_file_size_bytes"] < len(journal)
        recovery_order_checked = True
        return original_recover_taken_over(self, **kwargs)

    monkeypatch.setattr(
        StateStore,
        "recover_taken_over_state_journal",
        verify_recovery_order,
    )

    result = recover_stale_active_run(
        tmp_path,
        run_id="run_001",
        recovery_token=str(uuid.uuid4()),
        new_lock_token=str(uuid.uuid4()),
    )

    assert result == {"replayed": False, "successor_phase": "running"}
    assert recovery_order_checked is True
    journal_path = tmp_path / "runs" / "run_001" / "state_journal.jsonl"
    anchor = json.loads(
        (tmp_path / "runs" / "run_001" / "state_journal_tail.json").read_bytes()
    )
    rows = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
    assert [row["phase"] for row in rows] == expected_phases
    assert anchor["tail_file_size_bytes"] == len(journal_path.read_bytes())
    assert store.load(run_id="run_001")["status"] == expected_status


@pytest.mark.parametrize("failed_anchor_call", [1, 2])
def test_ordinary_recovery_cannot_advance_takeover_unanchored_successor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_anchor_call: int,
) -> None:
    store, _, old_lock_token = _initialized(tmp_path)
    original_write_anchor = store._write_anchor
    calls = 0

    def fail_one_anchor(run_id: str, anchor: Mapping[str, object]) -> None:
        nonlocal calls
        calls += 1
        if calls == failed_anchor_call:
            raise StateStoreError("injected unanchored direct successor")
        original_write_anchor(run_id, anchor)

    monkeypatch.setattr(store, "_write_anchor", fail_one_anchor)
    with pytest.raises(StateStoreError, match="unanchored direct successor"):
        _transition(
            store,
            old_lock_token,
            version=0,
            current="CREATED",
            next_status="PLANNED",
            second=1,
        )
    monkeypatch.setattr(store, "_write_anchor", original_write_anchor)

    initialize_phase_a1_sandbox(tmp_path, run_id="run_001")
    monkeypatch.setattr(locking.socket, "gethostname", lambda: "test-host")
    monkeypatch.setattr(locking, "_owner_pid_is_confirmed_dead", lambda _pid: True)
    original_recover_taken_over = StateStore.recover_taken_over_state_journal
    ordinary_recovery_rejected = False

    def verify_exclusive_recovery(self, **kwargs):
        nonlocal ordinary_recovery_rejected
        run_dir = tmp_path / "runs" / "run_001"
        paths = {
            "active": tmp_path / "runs" / ".active_run.lock",
            "state": run_dir / "state.json",
            "journal": run_dir / "state_journal.jsonl",
            "anchor": run_dir / "state_journal_tail.json",
        }
        before = {name: path.read_bytes() for name, path in paths.items()}
        with pytest.raises(
            StateRecoveryDeferredError,
            match="recover_taken_over_state_journal",
        ):
            self.recover_state_journal(
                run_id="run_001",
                expected_lock_token=kwargs["expected_lock_token"],
            )
        assert {name: path.read_bytes() for name, path in paths.items()} == before
        ordinary_recovery_rejected = True
        return original_recover_taken_over(self, **kwargs)

    monkeypatch.setattr(
        StateStore,
        "recover_taken_over_state_journal",
        verify_exclusive_recovery,
    )

    result = recover_stale_active_run(
        tmp_path,
        run_id="run_001",
        recovery_token=str(uuid.uuid4()),
        new_lock_token=str(uuid.uuid4()),
    )

    assert result == {"replayed": False, "successor_phase": "running"}
    assert ordinary_recovery_rejected is True


def test_stale_takeover_rejects_multiple_unanchored_records_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, allocation_token, old_lock_token = _initialized(tmp_path)
    _transition(
        store,
        old_lock_token,
        version=0,
        current="CREATED",
        next_status="PLANNED",
        second=1,
    )
    genesis = store._anchor_document(
        run_id="run_001",
        allocation_token=allocation_token,
        tail_record_index=None,
        tail_record_checksum=None,
        tail_file_size_bytes=0,
    )
    anchor_path = tmp_path / "runs" / "run_001" / "state_journal_tail.json"
    _write_canonical(anchor_path, genesis)
    initialize_phase_a1_sandbox(tmp_path, run_id="run_001")
    active_path = tmp_path / "runs" / ".active_run.lock"
    state_path = tmp_path / "runs" / "run_001" / "state.json"
    journal_path = tmp_path / "runs" / "run_001" / "state_journal.jsonl"
    before = {
        "active": active_path.read_bytes(),
        "state": state_path.read_bytes(),
        "journal": journal_path.read_bytes(),
        "anchor": anchor_path.read_bytes(),
    }
    monkeypatch.setattr(locking.socket, "gethostname", lambda: "test-host")
    monkeypatch.setattr(locking, "_owner_pid_is_confirmed_dead", lambda _pid: True)

    with pytest.raises(ActiveRunLockError, match="State/Journal preflight"):
        recover_stale_active_run(
            tmp_path,
            run_id="run_001",
            recovery_token=str(uuid.uuid4()),
            new_lock_token=str(uuid.uuid4()),
        )

    assert active_path.read_bytes() == before["active"]
    assert state_path.read_bytes() == before["state"]
    assert journal_path.read_bytes() == before["journal"]
    assert anchor_path.read_bytes() == before["anchor"]
    assert not list((tmp_path / "runs" / "run_001" / "lock_recovery_audit").glob("*.intent.json"))


@pytest.mark.parametrize(
    "suffix_kind", ["torn", "noncanonical", "checksum", "sequence"]
)
def test_stale_takeover_rejects_invalid_direct_successor_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix_kind: str
) -> None:
    store, _, old_lock_token = _initialized(tmp_path)
    original_write_anchor = store._write_anchor

    def fail_pending_anchor(run_id: str, anchor: Mapping[str, object]) -> None:
        raise StateStoreError("injected pending anchor failure")

    monkeypatch.setattr(store, "_write_anchor", fail_pending_anchor)
    with pytest.raises(StateStoreError, match="pending anchor failure"):
        _transition(
            store,
            old_lock_token,
            version=0,
            current="CREATED",
            next_status="PLANNED",
            second=1,
        )
    monkeypatch.setattr(store, "_write_anchor", original_write_anchor)

    run_dir = tmp_path / "runs" / "run_001"
    journal_path = run_dir / "state_journal.jsonl"
    anchor_path = run_dir / "state_journal_tail.json"
    anchor = json.loads(anchor_path.read_bytes())
    journal = journal_path.read_bytes()
    prefix = journal[: anchor["tail_file_size_bytes"]]
    suffix = journal[anchor["tail_file_size_bytes"] :]
    if suffix_kind == "torn":
        journal_path.write_bytes(prefix + suffix[:-1])
    elif suffix_kind == "noncanonical":
        journal_path.write_bytes(prefix + b" " + suffix)
    elif suffix_kind == "checksum":
        record = json.loads(suffix)
        record["record_checksum"] = "0" * 64
        journal_path.write_bytes(prefix + state_module._canonical_json_bytes(record))
    else:
        record = json.loads(suffix)
        record["record_index"] = 999
        journal_path.write_bytes(prefix + state_module._canonical_json_bytes(record))

    initialize_phase_a1_sandbox(tmp_path, run_id="run_001")
    active_path = tmp_path / "runs" / ".active_run.lock"
    state_path = run_dir / "state.json"
    before = {
        "active": active_path.read_bytes(),
        "state": state_path.read_bytes(),
        "journal": journal_path.read_bytes(),
        "anchor": anchor_path.read_bytes(),
    }
    monkeypatch.setattr(locking.socket, "gethostname", lambda: "test-host")
    monkeypatch.setattr(locking, "_owner_pid_is_confirmed_dead", lambda _pid: True)

    with pytest.raises(ActiveRunLockError, match="State/Journal preflight"):
        recover_stale_active_run(
            tmp_path,
            run_id="run_001",
            recovery_token=str(uuid.uuid4()),
            new_lock_token=str(uuid.uuid4()),
        )

    assert active_path.read_bytes() == before["active"]
    assert state_path.read_bytes() == before["state"]
    assert journal_path.read_bytes() == before["journal"]
    assert anchor_path.read_bytes() == before["anchor"]
    assert not list((run_dir / "lock_recovery_audit").glob("*.intent.json"))


def test_two_unanchored_records_are_rejected(tmp_path: Path) -> None:
    store, _, lock_token = _initialized(tmp_path)
    _transition(store, lock_token, version=0, current="CREATED", next_status="PLANNED", second=1)
    state = store.load(run_id="run_001")["canonical_state"]
    genesis = store._anchor_document(
        run_id="run_001",
        allocation_token=state["allocation_token"],
        tail_record_index=None,
        tail_record_checksum=None,
        tail_file_size_bytes=0,
    )
    _write_canonical(tmp_path / "runs" / "run_001" / "state_journal_tail.json", genesis)
    with pytest.raises(StateConflictError, match="more than one unconfirmed"):
        store.recover_state_journal(run_id="run_001", expected_lock_token=lock_token)


@pytest.mark.parametrize("anchored_phase", ["pending", "aborted", "committed"])
def test_deleting_anchored_tail_record_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    anchored_phase: str,
) -> None:
    store, _, lock_token = _initialized(tmp_path)
    original = store._atomic_replace
    if anchored_phase in {"pending", "aborted"}:
        def fail_state(path: Path, data: bytes, *, label: str) -> None:
            if label == "canonical state" and path.exists():
                raise StateStoreError("injected state replace failure")
            original(path, data, label=label)

        monkeypatch.setattr(store, "_atomic_replace", fail_state)
        with pytest.raises(StateStoreError, match="state replace failure"):
            _transition(
                store,
                lock_token,
                version=0,
                current="CREATED",
                next_status="PLANNED",
                second=1,
            )
        monkeypatch.setattr(store, "_atomic_replace", original)
        if anchored_phase == "aborted":
            store.recover_state_journal(
                run_id="run_001", expected_lock_token=lock_token
            )
    else:
        _transition(
            store,
            lock_token,
            version=0,
            current="CREATED",
            next_status="PLANNED",
            second=1,
        )
    journal_path = tmp_path / "runs" / "run_001" / "state_journal.jsonl"
    lines = journal_path.read_bytes().splitlines(keepends=True)
    assert json.loads(lines[-1])["phase"] == anchored_phase
    journal_path.write_bytes(b"".join(lines[:-1]))
    with pytest.raises(StateConflictError, match="shorter than its confirmed tail anchor"):
        store.load(run_id="run_001")


@pytest.mark.parametrize("damage", ["middle", "reorder", "truncate"])
def test_journal_damage_fails_closed(tmp_path: Path, damage: str) -> None:
    store, _, lock_token = _initialized(tmp_path)
    _transition(store, lock_token, version=0, current="CREATED", next_status="PLANNED", second=1)
    path = tmp_path / "runs" / "run_001" / "state_journal.jsonl"
    lines = path.read_bytes().splitlines(keepends=True)
    if damage == "middle":
        lines[0] = lines[0].replace(b'"phase":"pending"', b'"phase":"aborted"')
        path.write_bytes(b"".join(lines))
    elif damage == "reorder":
        path.write_bytes(b"".join(reversed(lines)))
    else:
        path.write_bytes(lines[0])
    with pytest.raises(StateConflictError):
        store.load(run_id="run_001")


def test_committed_state_tamper_blocks_load_recover_and_mutation(tmp_path: Path) -> None:
    store, _, lock_token = _initialized(tmp_path)
    _transition(store, lock_token, version=0, current="CREATED", next_status="PLANNED", second=1)
    state_path = tmp_path / "runs" / "run_001" / "state.json"
    tampered = json.loads(state_path.read_bytes())
    tampered["status"] = "BLOCKED"
    _write_canonical(state_path, tampered)

    with pytest.raises(StateConflictError, match="committed journal tail"):
        store.load(run_id="run_001")
    with pytest.raises(StateConflictError, match="committed journal tail"):
        store.recover(run_id="run_001", expected_lock_token=lock_token)
    with pytest.raises(StateConflictError, match="committed journal tail"):
        _transition(
            store,
            lock_token,
            version=1,
            current="BLOCKED",
            next_status="FAILED",
            second=2,
        )


def test_journal_actor_token_must_match_operation_owner(tmp_path: Path) -> None:
    store, allocation_token, lock_token = _initialized(tmp_path)
    _transition(store, lock_token, version=0, current="CREATED", next_status="PLANNED", second=1)
    journal_path = tmp_path / "runs" / "run_001" / "state_journal.jsonl"
    rows = [json.loads(line) for line in journal_path.read_bytes().splitlines()]
    rows[-1]["append_actor_lock_token"] = str(uuid.uuid4())
    rows[-1]["record_checksum"] = store._record_checksum(rows[-1])
    journal_data = b"".join(_write_canonical(tmp_path / f"row-{index}.json", row) for index, row in enumerate(rows))
    journal_path.write_bytes(journal_data)
    anchor = store._anchor_document(
        run_id="run_001",
        allocation_token=allocation_token,
        tail_record_index=rows[-1]["record_index"],
        tail_record_checksum=rows[-1]["record_checksum"],
        tail_file_size_bytes=len(journal_data),
    )
    _write_canonical(tmp_path / "runs" / "run_001" / "state_journal_tail.json", anchor)

    with pytest.raises(StateConflictError, match="append actor must match"):
        store.load(run_id="run_001")


def test_journal_record_limit_fails_closed_before_full_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _, lock_token = _initialized(tmp_path)
    _transition(store, lock_token, version=0, current="CREATED", next_status="PLANNED", second=1)
    monkeypatch.setattr(state_module, "MAX_STATE_JOURNAL_RECORDS", 1)
    with pytest.raises(StateConflictError, match="pilot record limit"):
        store.load(run_id="run_001")


def test_deep_json_returns_domain_error(tmp_path: Path) -> None:
    store, _, lock_token = _initialized(tmp_path)
    nested: dict[str, object] = {}
    cursor = nested
    for _ in range(40):
        child: dict[str, object] = {}
        cursor["child"] = child
        cursor = child
    with pytest.raises(StateStoreError, match="nesting depth"):
        store.transition_status(
            run_id="run_001",
            expected_lock_token=lock_token,
            expected_status="CREATED",
            expected_state_version=0,
            operation_id="run:run_001:transition:v0:CREATED:PLANNED:auto",
            mutation_timestamp="2026-07-27T00:00:01.000000Z",
            payload={
                "next_status": "PLANNED",
                "metadata": nested,
                "completion_evidence": None,
                "transition_kind": "auto",
                "decision_token": None,
            },
        )


def test_terminal_state_rejects_further_mutation(tmp_path: Path) -> None:
    store, _, lock_token = _initialized(tmp_path)
    _transition(store, lock_token, version=0, current="CREATED", next_status="FAILED", second=1)
    with pytest.raises(StateConflictError, match="terminal"):
        store.checkpoint_context(
            run_id="run_001",
            expected_lock_token=lock_token,
            expected_status="FAILED",
            expected_state_version=1,
            operation_id="run:run_001:checkpoint:run_initialized",
            mutation_timestamp="2026-07-27T00:00:02.000000Z",
            payload={
                "checkpoint_kind": "run_initialized",
                "task_id": None,
                "attempt_number": None,
                "created_at": "2026-07-27T00:00:02.000000Z",
                "expected_task_status": None,
                "next_task_status": None,
                "retry_disposition": "none",
                "next_attempt_number": None,
                "controlled_context_delta": {
                    "task_plan": TASK_PLAN,
                    "plan_fingerprint": PLAN_SHA,
                },
                "error_summary": None,
            },
        )


def test_completed_requires_successful_required_tasks(tmp_path: Path) -> None:
    store, lock_token, version = _planned_with_tasks(tmp_path)
    _transition(store, lock_token, version=version, current="PLANNED", next_status="RUNNING", second=3)
    with pytest.raises(StateConflictError, match="required tasks"):
        _transition(
            store,
            lock_token,
            version=version + 1,
            current="RUNNING",
            next_status="COMPLETED",
            second=4,
            completion_evidence={
                "schema_version": COMPLETION_EVIDENCE_SCHEMA_VERSION,
                "run_id": "run_001",
                "plan_fingerprint": PLAN_SHA,
                "required_task_ids": ["core"],
                "publication_manifest_path": "outputs/current_publication_manifest.json",
                "publication_manifest_sha256": "1" * 64,
                "publication_transaction_path": "runs/run_001/publication_transaction.json",
                "publication_transaction_sha256": "2" * 64,
                "transaction_id": "tx",
                "final_summary_path": "runs/run_001/final_summary.md",
                "final_summary_sha256": "3" * 64,
            },
        )


def test_completed_rejects_publication_hash_or_transaction_mismatch(tmp_path: Path) -> None:
    store, lock_token, version = _planned_with_tasks(tmp_path)
    _transition(store, lock_token, version=version, current="PLANNED", next_status="RUNNING", second=3)
    version += 1
    _checkpoint(store, lock_token, version=version, status="RUNNING", kind="task_started", second=4, attempt=1)
    version += 1
    _checkpoint(store, lock_token, version=version, status="RUNNING", kind="task_succeeded", second=5, attempt=1)
    version += 1

    evidence, transaction, _ = _completion_fixture(tmp_path)
    bad = dict(evidence)
    bad["publication_manifest_sha256"] = "f" * 64
    with pytest.raises(StateConflictError, match="SHA-256"):
        _transition(
            store,
            lock_token,
            version=version,
            current="RUNNING",
            next_status="COMPLETED",
            second=6,
            completion_evidence=bad,
        )

    transaction["phase"] = "files_replaced"
    transaction_data = _write_canonical(
        tmp_path / "runs" / "run_001" / "publication_transaction.json", transaction
    )
    evidence["publication_transaction_sha256"] = hashlib.sha256(transaction_data).hexdigest()
    with pytest.raises(StateConflictError, match="validate_publication"):
        _transition(
            store,
            lock_token,
            version=version,
            current="RUNNING",
            next_status="COMPLETED",
            second=7,
            completion_evidence=evidence,
        )


def test_completed_accepts_complete_bound_publication_evidence(tmp_path: Path) -> None:
    store, lock_token, version = _planned_with_tasks(tmp_path)
    _transition(
        store,
        lock_token,
        version=version,
        current="PLANNED",
        next_status="RUNNING",
        second=3,
    )
    version += 1
    _checkpoint(
        store,
        lock_token,
        version=version,
        status="RUNNING",
        kind="task_started",
        second=4,
        attempt=1,
    )
    version += 1
    _checkpoint(
        store,
        lock_token,
        version=version,
        status="RUNNING",
        kind="task_succeeded",
        second=5,
        attempt=1,
    )
    version += 1
    evidence, _, _ = _completion_fixture(tmp_path)
    result = _transition(
        store,
        lock_token,
        version=version,
        current="RUNNING",
        next_status="COMPLETED",
        second=6,
        completion_evidence=evidence,
    )
    assert result["canonical_state"]["status"] == "COMPLETED"
    assert result["resulting_state_version"] == version + 1


def test_state_or_journal_symlink_is_rejected_when_supported(tmp_path: Path) -> None:
    store, _, _ = _initialized(tmp_path)
    state_path = tmp_path / "runs" / "run_001" / "state.json"
    target = tmp_path / "outside.json"
    target.write_bytes(state_path.read_bytes())
    state_path.unlink()
    try:
        state_path.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("file symlink creation is unavailable")
    with pytest.raises(StateConflictError, match="symlink"):
        store.load(run_id="run_001")


def test_journal_symlink_is_rejected_when_supported(tmp_path: Path) -> None:
    store, _, _ = _initialized(tmp_path)
    journal_path = tmp_path / "runs" / "run_001" / "state_journal.jsonl"
    target = tmp_path / "outside-journal.jsonl"
    target.write_bytes(b"")
    try:
        journal_path.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("file symlink creation is unavailable")
    with pytest.raises(StateConflictError, match="non-link, non-reparse"):
        store.load(run_id="run_001")


def test_journal_reparse_entry_is_rejected_for_read_append_and_genesis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, allocation_token, lock_token = _initialized(tmp_path)
    journal_path = tmp_path / "runs" / "run_001" / "state_journal.jsonl"
    original_lstat = state_module._lstat

    class FakeReparseStat:
        st_mode = stat.S_IFREG
        st_size = 0
        st_file_attributes = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

    def reparse_journal(path: Path, *, label: str):
        if Path(path) == journal_path:
            return FakeReparseStat()
        return original_lstat(path, label=label)

    monkeypatch.setattr(state_module, "_lstat", reparse_journal)
    with pytest.raises(StateConflictError, match="non-reparse"):
        store.load(run_id="run_001")
    with pytest.raises(StateConflictError, match="non-reparse"):
        store._append_record("run_001", allocation_token, [], {})

    fresh_root = tmp_path / "genesis"
    fresh_root.mkdir()
    fresh, fresh_allocation_token, fresh_lock_token = _new_allocation(fresh_root)
    fresh.initialize_run(
        run_id="run_001",
        allocation_token=fresh_allocation_token,
        plan_fingerprint=PLAN_SHA,
        task_plan=TASK_PLAN,
        expected_lock_token=fresh_lock_token,
        created_at=T0,
    )
    fresh_journal = fresh_root / "runs" / "run_001" / "state_journal.jsonl"

    def reparse_genesis_journal(path: Path, *, label: str):
        if Path(path) == fresh_journal:
            return FakeReparseStat()
        return original_lstat(path, label=label)

    monkeypatch.setattr(state_module, "_lstat", reparse_genesis_journal)
    with pytest.raises(StateConflictError, match="non-reparse"):
        fresh.validate_initialized_run(
            run_id="run_001", allocation_token=fresh_allocation_token
        )


def test_state_lock_is_not_deleted_when_exclusive_acquire_fails(tmp_path: Path) -> None:
    store, _, _ = _initialized(tmp_path)
    lock_path = tmp_path / "runs" / "run_001" / ".state.lock"
    lock_path.write_text("owned elsewhere", encoding="utf-8")
    with pytest.raises(StateConflictError, match="already exists"):
        with store._state_lock("run_001"):
            pass
    assert lock_path.read_text(encoding="utf-8") == "owned elsewhere"


def test_state_lock_replacement_is_preserved_and_fails_closed(tmp_path: Path) -> None:
    store, _, _ = _initialized(tmp_path)
    lock_path = tmp_path / "runs" / "run_001" / ".state.lock"
    replacement = b'{"owner":"external"}\n'
    with pytest.raises(StateStoreError, match="state lock cleanup failed"):
        with store._state_lock("run_001"):
            lock_path.write_bytes(replacement)
    assert lock_path.read_bytes() == replacement


def test_state_lock_deleted_during_mutation_prevents_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _, lock_token = _initialized(tmp_path)
    original = store._reduce_transition

    def delete_lock(state, payload):
        result = original(state, payload)
        (tmp_path / "runs" / "run_001" / ".state.lock").unlink()
        return result

    monkeypatch.setattr(store, "_reduce_transition", delete_lock)
    with pytest.raises(StateConflictError, match="state lock"):
        _transition(
            store,
            lock_token,
            version=0,
            current="CREATED",
            next_status="PLANNED",
            second=1,
        )
    lock_path = tmp_path / "runs" / "run_001" / ".state.lock"
    assert lock_path.is_file()
    with pytest.raises(StateConflictError, match="state lock already exists"):
        _transition(
            store,
            lock_token,
            version=1,
            current="PLANNED",
            next_status="RUNNING",
            second=2,
        )


def test_state_lock_final_sync_failure_restores_blocking_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _, lock_token = _initialized(tmp_path)
    lock_path = tmp_path / "runs" / "run_001" / ".state.lock"
    original_sync = state_module._sync_directory
    failed = False

    def fail_release_sync(path: Path, *, label: str) -> None:
        nonlocal failed
        if label == "Run directory after state lock release" and not failed:
            failed = True
            raise OSError("release directory sync failed")
        original_sync(path, label=label)

    monkeypatch.setattr(state_module, "_sync_directory", fail_release_sync)
    with pytest.raises(
        StateStoreError,
        match="state lock release is uncertain; blocking evidence was restored",
    ) as exc_info:
        _transition(
            store,
            lock_token,
            version=0,
            current="CREATED",
            next_status="PLANNED",
            second=1,
        )

    assert isinstance(exc_info.value.__cause__, OSError)
    assert lock_path.is_file()
    with pytest.raises(StateConflictError, match="state lock already exists"):
        _transition(
            store,
            lock_token,
            version=1,
            current="PLANNED",
            next_status="RUNNING",
            second=2,
        )


def test_state_lock_restore_failure_leaves_recovery_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _, lock_token = _initialized(tmp_path)
    original_sync = state_module._sync_directory
    original_open = state_module.os.open
    lock_open_count = 0

    def fail_release_sync(path: Path, *, label: str) -> None:
        if label == "Run directory after state lock release":
            raise OSError("release directory sync failed")
        original_sync(path, label=label)

    def fail_restore_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes], *args: object
    ) -> int:
        nonlocal lock_open_count
        if Path(path).name == ".state.lock":
            lock_open_count += 1
            if lock_open_count == 2:
                raise OSError("injected state lock restoration failure")
        return original_open(path, *args)

    monkeypatch.setattr(state_module, "_sync_directory", fail_release_sync)
    monkeypatch.setattr(state_module.os, "open", fail_restore_open)
    with pytest.raises(StateStoreError, match="state lock release is uncertain"):
        _transition(
            store,
            lock_token,
            version=0,
            current="CREATED",
            next_status="PLANNED",
            second=1,
        )

    marker = tmp_path / "runs" / "run_001" / ".state_lock_recovery_required.json"
    assert marker.is_file()
    with pytest.raises(StateRecoveryRequiredError, match="recovery marker"):
        _transition(
            store,
            lock_token,
            version=1,
            current="PLANNED",
            next_status="RUNNING",
            second=2,
        )


def test_task_status_and_attempt_keys_must_match(tmp_path: Path) -> None:
    store, _, _ = _initialized(tmp_path)
    state_path = tmp_path / "runs" / "run_001" / "state.json"
    state = json.loads(state_path.read_bytes())
    state["task_status"] = {"core": "pending"}
    _write_canonical(state_path, state)
    with pytest.raises(StateConflictError, match="must match exactly"):
        store.load(run_id="run_001")


def test_state_snapshot_is_deeply_immutable(tmp_path: Path) -> None:
    store, _, _ = _initialized(tmp_path)
    snapshot = store.load(run_id="run_001")
    with pytest.raises(TypeError):
        snapshot["status"] = "FAILED"  # type: ignore[index]
    with pytest.raises(TypeError):
        snapshot["canonical_state"]["context"]["injected"] = True  # type: ignore[index]
    with pytest.raises(TypeError):
        snapshot["canonical_state"]["task_plan"][0]["required"] = False  # type: ignore[index]


def test_legacy_checkpoint_api_behavior_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "legacy.json"
    assert state_module.load_checkpoint(path) == {}
    state_module.save_checkpoint(path, {"value": object()})
    assert "object at" in path.read_text(encoding="utf-8")
    context = {"task_status": {"a": "success", "b": "failed"}}
    state = state_module.make_state("run_legacy", context)
    assert state["completed_tasks"] == ["a"]
    assert state["failed_tasks"] == ["b"]
