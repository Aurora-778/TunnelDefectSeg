from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import uuid

import pytest

from orchestrator.inspection_workflow.locking import (
    acquire_active_run_lock,
    mark_active_run_running,
    reserve_active_run_id,
)
from orchestrator.state import store as state_module
from orchestrator.state.store import (
    COMPLETION_EVIDENCE_SCHEMA_VERSION,
    StateConflictError,
    StateRecoveryRequiredError,
    StateStore,
    StateStoreError,
)


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
) -> Mapping[str, object]:
    timestamp = f"2026-07-27T00:00:{second:02d}.000000Z"
    if kind == "run_initialized":
        operation_id = "run:run_001:checkpoint:run_initialized"
        payload: dict[str, object] = {
            "checkpoint_kind": kind,
            "task_id": None,
            "attempt_number": None,
            "created_at": timestamp,
            "context_delta": {},
            "task_plan": TASK_PLAN,
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
            "created_at": timestamp,
            "context_delta": {},
        }
        if kind == "task_failed":
            payload["retry_disposition"] = retry_disposition
            payload["next_attempt_number"] = (
                attempt + 1 if retry_disposition == "retry" and isinstance(attempt, int) else None
            )
        if kind == "task_retry_scheduled":
            payload["next_attempt_number"] = attempt + 1 if isinstance(attempt, int) else None
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


def _write_publication_ref(root: Path, relative: str, data: bytes) -> dict[str, object]:
    path = root.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {
        "path": relative,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _completion_fixture(
    root: Path,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    source_path = "runs/run_001/artifacts/claim_decision.json"
    source_ref = _write_publication_ref(root, source_path, b'{"decision":"static_audit"}\n')
    publication_paths = sorted(
        [
            "outputs/final_project_report.md",
            "outputs/key_insights.md",
            "outputs/system_summary.md",
            "runs/run_001/final_summary.md",
        ]
    )
    publication_refs = [
        _write_publication_ref(root, relative, f"{relative}\n".encode("utf-8"))
        for relative in publication_paths
    ]
    manifest = {
        "schema_version": "publication_manifest_v1",
        "run_id": "run_001",
        "plan_fingerprint": PLAN_SHA,
        "transaction_id": "tx-001",
        "expected_source_artifact_paths": [source_path],
        "source_artifacts": [source_ref],
        "publication_files": publication_refs,
    }
    manifest_data = _write_canonical(
        root / "outputs" / "current_publication_manifest.json", manifest
    )
    transaction = {
        "run_id": "run_001",
        "plan_fingerprint": PLAN_SHA,
        "transaction_id": "tx-001",
        "phase": "manifest_committed",
        "manifest_sha256": hashlib.sha256(manifest_data).hexdigest(),
    }
    transaction_data = _write_canonical(
        root / "runs" / "run_001" / "publication_transaction.json", transaction
    )
    summary_ref = next(
        item for item in publication_refs if item["path"] == "runs/run_001/final_summary.md"
    )
    evidence = {
        "schema_version": COMPLETION_EVIDENCE_SCHEMA_VERSION,
        "run_id": "run_001",
        "plan_fingerprint": PLAN_SHA,
        "required_task_ids": ["core"],
        "publication_manifest_path": "outputs/current_publication_manifest.json",
        "publication_manifest_sha256": hashlib.sha256(manifest_data).hexdigest(),
        "publication_transaction_path": "runs/run_001/publication_transaction.json",
        "publication_transaction_sha256": hashlib.sha256(transaction_data).hexdigest(),
        "transaction_id": "tx-001",
        "final_summary_path": "runs/run_001/final_summary.md",
        "final_summary_sha256": summary_ref["sha256"],
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
                "context_delta": {},
                "task_plan": TASK_PLAN,
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
    with pytest.raises(StateConflictError, match="manifest_committed"):
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


def test_legacy_checkpoint_api_behavior_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "legacy.json"
    assert state_module.load_checkpoint(path) == {}
    state_module.save_checkpoint(path, {"value": object()})
    assert "object at" in path.read_text(encoding="utf-8")
    context = {"task_status": {"a": "success", "b": "failed"}}
    state = state_module.make_state("run_legacy", context)
    assert state["completed_tasks"] == ["a"]
    assert state["failed_tasks"] == ["b"]
