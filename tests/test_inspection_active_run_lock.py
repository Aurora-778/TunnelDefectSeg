from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
import uuid

import pytest

from orchestrator.inspection_workflow import locking
from orchestrator.inspection_workflow import a1_artifacts
from orchestrator.inspection_workflow.locking import (
    ActiveRunLockError,
    acquire_active_run_lock,
    mark_active_run_running,
    read_active_run_lock,
    recover_stale_active_run,
    release_active_run_lock,
    reserve_active_run_id,
    validate_active_run_lock_snapshot,
    validate_active_run_lock,
)
from orchestrator.state import store as state_module
from orchestrator.state.store import StateStore


TIME = "2026-07-27T00:00:00.000000Z"
TASK_PLAN = [{"task_id": "core", "deps": [], "required": True}]
PLAN_SHA = "a" * 64


def _tokens() -> tuple[str, str]:
    return str(uuid.uuid4()), str(uuid.uuid4())


def _acquire(root: Path) -> tuple[str, str]:
    allocation_token, lock_token = _tokens()
    acquire_active_run_lock(
        root,
        task_id="task_001",
        allocation_token=allocation_token,
        lock_token=lock_token,
        created_at=TIME,
        pid=12345,
        hostname="test-host",
    )
    return allocation_token, lock_token


def _running(root: Path) -> tuple[str, str]:
    allocation_token, lock_token = _acquire(root)
    reserve_active_run_id(
        root,
        run_id="run_001",
        expected_allocation_token=allocation_token,
        expected_lock_token=lock_token,
    )
    (root / "runs" / "run_001").mkdir()
    StateStore(root).initialize_run(
        run_id="run_001",
        allocation_token=allocation_token,
        plan_fingerprint=PLAN_SHA,
        task_plan=TASK_PLAN,
        expected_lock_token=lock_token,
        created_at=TIME,
    )
    mark_active_run_running(
        root,
        run_id="run_001",
        expected_allocation_token=allocation_token,
        expected_lock_token=lock_token,
    )
    return allocation_token, lock_token


def test_lock_snapshot_validator_binds_canonical_bytes_and_semantics(tmp_path: Path) -> None:
    _acquire(tmp_path)
    data = (tmp_path / "runs" / ".active_run.lock").read_bytes()
    assert validate_active_run_lock_snapshot(data) == read_active_run_lock(tmp_path)
    noncanonical = json.dumps(json.loads(data), indent=2).encode("utf-8")
    with pytest.raises(ActiveRunLockError, match="canonical JSON"):
        validate_active_run_lock_snapshot(noncanonical)
    with pytest.raises(ActiveRunLockError, match="exact bytes"):
        validate_active_run_lock_snapshot(bytearray(data))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "residue_name",
    [".active_run.unknown.tmp", "..active_run.lock.crashed.tmp"],
)
def test_active_run_control_entry_authority_rejects_unknown_transaction_residue(
    tmp_path: Path, residue_name: str,
) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    residue = runs / residue_name
    residue.write_bytes(b"residue")
    with pytest.raises(ActiveRunLockError, match="unknown Active Run transaction residue"):
        locking.validate_active_run_control_entries(tmp_path)
    allocation_token, lock_token = _tokens()
    with pytest.raises(ActiveRunLockError, match="unknown Active Run transaction residue"):
        acquire_active_run_lock(
            tmp_path,
            task_id="task_001",
            allocation_token=allocation_token,
            lock_token=lock_token,
            created_at=TIME,
        )
    assert residue.read_bytes() == b"residue"
    assert not (runs / ".active_run.lock").exists()


def _transition_to_failed(root: Path, lock_token: str) -> None:
    StateStore(root).transition_status(
        run_id="run_001",
        expected_lock_token=lock_token,
        expected_status="CREATED",
        expected_state_version=0,
        operation_id="run:run_001:transition:v0:CREATED:FAILED:auto",
        mutation_timestamp="2026-07-27T00:00:01.000000Z",
        payload={
            "next_status": "FAILED",
            "metadata": None,
            "completion_evidence": None,
            "transition_kind": "auto",
            "decision_token": None,
        },
    )


def _recovery_sandbox(root: Path) -> None:
    a1_artifacts.initialize_phase_a1_sandbox(root, run_id="run_001")


def _assert_audit_rejected_before_recovery_mutex(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    match: str,
) -> None:
    lock_path = root / "runs" / ".active_run.lock"
    audit_dir = root / "runs" / "run_001" / "lock_recovery_audit"
    lock_before = lock_path.read_bytes()
    entries_before = sorted(path.name for path in audit_dir.iterdir())
    monkeypatch.setattr(
        locking,
        "_acquire_recovery_mutex",
        lambda *_args, **_kwargs: pytest.fail(
            "recovery audit residue must block before recovery mutex acquisition"
        ),
    )

    with pytest.raises(ActiveRunLockError, match=match):
        recover_stale_active_run(
            root,
            run_id="run_001",
            recovery_token=str(uuid.uuid4()),
            new_lock_token=str(uuid.uuid4()),
        )

    assert lock_path.read_bytes() == lock_before
    assert sorted(path.name for path in audit_dir.iterdir()) == entries_before
    assert not (root / "runs" / ".active_run.recovery.lock").exists()


def test_active_lock_uses_exclusive_creation_and_canonical_bytes(tmp_path: Path) -> None:
    allocation_token, lock_token = _acquire(tmp_path)
    lock_path = tmp_path / "runs" / ".active_run.lock"
    raw = lock_path.read_bytes()
    assert raw.endswith(b"\n")
    assert b"\r\n" not in raw
    assert json.loads(raw)["reserved_run_id"] is None

    with pytest.raises(ActiveRunLockError, match="already exists"):
        acquire_active_run_lock(
            tmp_path,
            task_id="task_002",
            allocation_token=str(uuid.uuid4()),
            lock_token=str(uuid.uuid4()),
            created_at=TIME,
        )

    lock = read_active_run_lock(tmp_path)
    assert lock["allocation_token"] == allocation_token
    assert lock["lock_token"] == lock_token


def test_reserve_precedes_run_directory_and_running_identity(tmp_path: Path) -> None:
    allocation_token, lock_token = _acquire(tmp_path)
    reserved = reserve_active_run_id(
        tmp_path,
        run_id="run_001",
        expected_allocation_token=allocation_token,
        expected_lock_token=lock_token,
    )
    assert reserved["phase"] == "allocating"
    assert reserved["reserved_run_id"] == "run_001"
    assert reserved["run_id"] is None
    run_dir = tmp_path / "runs" / "run_001"
    assert not run_dir.exists()

    with pytest.raises(ActiveRunLockError, match="valid CREATED state and genesis anchor"):
        mark_active_run_running(
            tmp_path,
            run_id="run_001",
            expected_allocation_token=allocation_token,
            expected_lock_token=lock_token,
        )
    run_dir.mkdir()
    StateStore(tmp_path).initialize_run(
        run_id="run_001",
        allocation_token=allocation_token,
        plan_fingerprint=PLAN_SHA,
        task_plan=TASK_PLAN,
        expected_lock_token=lock_token,
        created_at=TIME,
    )
    running = mark_active_run_running(
        tmp_path,
        run_id="run_001",
        expected_allocation_token=allocation_token,
        expected_lock_token=lock_token,
    )
    assert running["phase"] == "running"
    assert running["run_id"] == "run_001"


def test_running_transition_rejects_non_genesis_journal(tmp_path: Path) -> None:
    allocation_token, lock_token = _acquire(tmp_path)
    reserve_active_run_id(
        tmp_path,
        run_id="run_001",
        expected_allocation_token=allocation_token,
        expected_lock_token=lock_token,
    )
    run_dir = tmp_path / "runs" / "run_001"
    run_dir.mkdir()
    StateStore(tmp_path).initialize_run(
        run_id="run_001",
        allocation_token=allocation_token,
        plan_fingerprint=PLAN_SHA,
        task_plan=TASK_PLAN,
        expected_lock_token=lock_token,
        created_at=TIME,
    )
    (run_dir / "state_journal.jsonl").write_bytes(b"unexpected\n")

    with pytest.raises(ActiveRunLockError, match="valid CREATED state and genesis anchor"):
        mark_active_run_running(
            tmp_path,
            run_id="run_001",
            expected_allocation_token=allocation_token,
            expected_lock_token=lock_token,
        )
    assert read_active_run_lock(tmp_path)["phase"] == "allocating"


@pytest.mark.parametrize(
    "mutation",
    [
        (
            lambda state: state.update(
                {
                    "task_status": {"core": "success"},
                    "task_attempts": {"core": 1},
                    "completed_tasks": ["core"],
                    "failed_tasks": [],
                }
            )
        ),
        (
            lambda state: state.update(
                {
                    "task_status": {"core": "failed"},
                    "task_attempts": {"core": 1},
                    "completed_tasks": [],
                    "failed_tasks": ["core"],
                }
            )
        ),
        lambda state: state.update({"updated_at": "2026-07-27T00:00:01.000000Z"}),
        (
            lambda state: state.update(
                {
                    "last_operation_kind": "status_transition",
                    "last_operation_id": "run:run_001:transition:v0:CREATED:PLANNED:auto",
                    "last_operation_payload_sha256": "b" * 64,
                }
            )
        ),
    ],
    ids=("task-status-and-attempts", "failed-index", "timestamp", "last-operation"),
)
def test_running_transition_rejects_forged_empty_journal_baseline(
    tmp_path: Path, mutation: Callable[[dict[str, object]], None]
) -> None:
    allocation_token, lock_token = _acquire(tmp_path)
    reserve_active_run_id(
        tmp_path,
        run_id="run_001",
        expected_allocation_token=allocation_token,
        expected_lock_token=lock_token,
    )
    run_dir = tmp_path / "runs" / "run_001"
    run_dir.mkdir()
    StateStore(tmp_path).initialize_run(
        run_id="run_001",
        allocation_token=allocation_token,
        plan_fingerprint=PLAN_SHA,
        task_plan=TASK_PLAN,
        expected_lock_token=lock_token,
        created_at=TIME,
    )
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_bytes())
    mutation(state)
    state_path.write_bytes(state_module._canonical_json_bytes(state))

    with pytest.raises(ActiveRunLockError, match="valid CREATED state and genesis anchor"):
        mark_active_run_running(
            tmp_path,
            run_id="run_001",
            expected_allocation_token=allocation_token,
            expected_lock_token=lock_token,
        )
    assert read_active_run_lock(tmp_path)["phase"] == "allocating"


def test_lock_token_fencing_rejects_wrong_owner(tmp_path: Path) -> None:
    allocation_token, lock_token = _running(tmp_path)
    with pytest.raises(ActiveRunLockError, match="fencing"):
        validate_active_run_lock(
            tmp_path,
            run_id="run_001",
            allocation_token=allocation_token,
            expected_lock_token=str(uuid.uuid4()),
            allowed_phases={"running"},
        )
    with pytest.raises(ActiveRunLockError, match="another caller"):
        release_active_run_lock(
            tmp_path,
            run_id="run_001",
            expected_allocation_token=allocation_token,
            expected_lock_token=str(uuid.uuid4()),
        )
    assert read_active_run_lock(tmp_path)["lock_token"] == lock_token


def test_lock_fencing_rejects_malformed_phase_collection(tmp_path: Path) -> None:
    allocation_token, lock_token = _running(tmp_path)
    with pytest.raises(ActiveRunLockError, match="allowed Active Run Lock phases"):
        validate_active_run_lock(
            tmp_path,
            run_id="run_001",
            allocation_token=allocation_token,
            expected_lock_token=lock_token,
            allowed_phases=[[]],  # type: ignore[arg-type,list-item]
        )


def test_release_is_token_scoped_and_removes_tombstone(tmp_path: Path) -> None:
    allocation_token, lock_token = _running(tmp_path)
    result = release_active_run_lock(
        tmp_path,
        run_id="run_001",
        expected_allocation_token=allocation_token,
        expected_lock_token=lock_token,
    )
    assert result == {"released": True, "run_id": "run_001", "lock_token": lock_token}
    assert not (tmp_path / "runs" / ".active_run.lock").exists()
    assert not list((tmp_path / "runs").glob(".active_run.release.*"))


def test_release_paths_use_controlled_rename_and_unlink_barriers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allocation_token, lock_token = _running(tmp_path)
    original_rename = locking.controlled_fs.rename
    original_unlink = locking.controlled_fs.unlink
    calls: list[tuple[str, str]] = []

    def record_rename(
        root: Path,
        source_relative: str,
        target_relative: str,
        *,
        replace: bool = False,
        expected_source_identity: tuple[int, int] | None = None,
    ) -> None:
        calls.append(("rename", target_relative))
        original_rename(
            root,
            source_relative,
            target_relative,
            replace=replace,
            expected_source_identity=expected_source_identity,
        )

    def record_unlink(
        root: Path,
        relative: str,
        *,
        expected_identity: tuple[int, int] | None = None,
    ) -> None:
        calls.append(("unlink", relative))
        original_unlink(root, relative, expected_identity=expected_identity)

    monkeypatch.setattr(locking.controlled_fs, "rename", record_rename)
    monkeypatch.setattr(locking.controlled_fs, "unlink", record_unlink)
    assert release_active_run_lock(
        tmp_path,
        run_id="run_001",
        expected_allocation_token=allocation_token,
        expected_lock_token=lock_token,
    )["released"]
    release_calls = [
        (kind, relative)
        for kind, relative in calls
        if relative.startswith("runs/.active_run.release.")
    ]
    assert [kind for kind, _ in release_calls] == ["rename", "unlink"]

    runs = tmp_path / "runs"
    recovery = runs / ".active_run.recovery.lock"
    recovery_bytes = b"recovery-owner"
    recovery.write_bytes(recovery_bytes)
    calls.clear()
    locking._remove_owned_recovery_mutex(recovery, runs, recovery_bytes)
    assert ("unlink", "runs/.active_run.recovery.lock") in calls


def test_release_cleanup_failure_preserves_tombstone_and_blocks_new_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allocation_token, lock_token = _running(tmp_path)
    real_unlink = locking.controlled_fs.unlink

    def fail_tombstone(
        root: Path,
        relative: str,
        *,
        expected_identity: tuple[int, int] | None = None,
    ) -> None:
        if Path(relative).name.startswith(".active_run.release."):
            raise OSError("injected tombstone cleanup failure")
        real_unlink(root, relative, expected_identity=expected_identity)

    monkeypatch.setattr(locking.controlled_fs, "unlink", fail_tombstone)
    with pytest.raises(ActiveRunLockError, match="release is incomplete"):
        release_active_run_lock(
            tmp_path,
            run_id="run_001",
            expected_allocation_token=allocation_token,
            expected_lock_token=lock_token,
        )
    tombstones = list((tmp_path / "runs").glob(".active_run.release.*"))
    assert len(tombstones) == 1
    with pytest.raises(ActiveRunLockError, match="release tombstone"):
        acquire_active_run_lock(
            tmp_path,
            task_id="task_002",
            allocation_token=str(uuid.uuid4()),
            lock_token=str(uuid.uuid4()),
            created_at=TIME,
        )


def test_recovery_lock_blocks_acquisition_without_takeover(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / ".active_run.recovery.lock").write_text("blocked", encoding="utf-8")
    with pytest.raises(ActiveRunLockError, match="A3.2 recovery"):
        _acquire(tmp_path)


def test_same_host_dead_owner_takeover_writes_audited_exact_successor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allocation_token, old_lock_token = _running(tmp_path)
    _recovery_sandbox(tmp_path)
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

    assert result["replayed"] is False
    assert result["successor_phase"] == "running"
    lock = read_active_run_lock(tmp_path)
    assert lock["phase"] == "running"
    assert lock["lock_token"] == new_lock_token
    assert lock["recovery_of_lock_token"] is None
    audit_dir = tmp_path / "runs" / "run_001" / "lock_recovery_audit"
    intent_paths = list(audit_dir.glob("*.intent.json"))
    outcome_paths = list(audit_dir.glob("*.outcome.1.json"))
    assert len(intent_paths) == len(outcome_paths) == 1
    intent = json.loads(intent_paths[0].read_text(encoding="utf-8"))
    outcome = json.loads(outcome_paths[0].read_text(encoding="utf-8"))
    assert intent["old_lock_token"] == old_lock_token
    assert intent["new_lock_token"] == new_lock_token
    assert outcome["intent_sha256"] == hashlib.sha256(intent_paths[0].read_bytes()).hexdigest()
    assert not (tmp_path / "runs" / ".active_run.recovery.lock").exists()
    with pytest.raises(ActiveRunLockError, match="fencing"):
        validate_active_run_lock(
            tmp_path,
            run_id="run_001",
            allocation_token=allocation_token,
            expected_lock_token=old_lock_token,
            allowed_phases={"running"},
        )
    monkeypatch.setattr(
        locking,
        "_atomic_update_lock",
        lambda *_args, **_kwargs: pytest.fail("completed recovery replay must not update the lock"),
    )
    replay = recover_stale_active_run(
        tmp_path,
        run_id="run_001",
        recovery_token=recovery_token,
        new_lock_token=new_lock_token,
    )
    assert replay == {"replayed": True, "successor_phase": "running"}


def test_completed_outcome_replay_revalidates_frozen_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _running(tmp_path)
    _recovery_sandbox(tmp_path)
    monkeypatch.setattr(locking.socket, "gethostname", lambda: "test-host")
    monkeypatch.setattr(locking, "_owner_pid_is_confirmed_dead", lambda _pid: True)
    recovery_token = str(uuid.uuid4())
    new_lock_token = str(uuid.uuid4())
    recover_stale_active_run(
        tmp_path,
        run_id="run_001",
        recovery_token=recovery_token,
        new_lock_token=new_lock_token,
    )
    state_path = tmp_path / "runs" / "run_001" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["context"] = {"external_change": True}
    state_path.write_bytes(state_module._canonical_json_bytes(state))

    with pytest.raises(ActiveRunLockError, match="State does not match frozen outcome"):
        recover_stale_active_run(
            tmp_path,
            run_id="run_001",
            recovery_token=recovery_token,
            new_lock_token=new_lock_token,
        )


@pytest.mark.parametrize("successor_phase", ["running", "released"])
@pytest.mark.parametrize(
    ("residue_kind", "match"),
    [
        ("recovery_mutex", "recovery lock"),
        ("release_tombstone", "release tombstone"),
        ("state_lock", "state transition"),
    ],
)
def test_completed_outcome_replay_rejects_active_run_residue_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    successor_phase: str,
    residue_kind: str,
    match: str,
) -> None:
    _, old_lock_token = _running(tmp_path)
    if successor_phase == "released":
        _transition_to_failed(tmp_path, old_lock_token)
    _recovery_sandbox(tmp_path)
    monkeypatch.setattr(locking.socket, "gethostname", lambda: "test-host")
    monkeypatch.setattr(locking, "_owner_pid_is_confirmed_dead", lambda _pid: True)
    recovery_token = str(uuid.uuid4())
    new_lock_token = str(uuid.uuid4())
    assert recover_stale_active_run(
        tmp_path,
        run_id="run_001",
        recovery_token=recovery_token,
        new_lock_token=new_lock_token,
    ) == {"replayed": False, "successor_phase": successor_phase}

    runs = tmp_path / "runs"
    if residue_kind == "recovery_mutex":
        residue = runs / ".active_run.recovery.lock"
    elif residue_kind == "release_tombstone":
        residue = runs / f".active_run.release.{uuid.uuid4()}.json"
    else:
        residue = runs / locking._ACTIVE_RUN_STATE_LOCK_NAME
    residue.write_bytes(b"preserved recovery residue")

    active_path = runs / ".active_run.lock"
    active_before = active_path.read_bytes() if active_path.exists() else None
    audit_dir = runs / "run_001" / "lock_recovery_audit"
    audit_before = {
        path.name: path.read_bytes()
        for path in audit_dir.iterdir()
    }
    residue_before = residue.read_bytes()

    with pytest.raises(ActiveRunLockError, match=match):
        recover_stale_active_run(
            tmp_path,
            run_id="run_001",
            recovery_token=recovery_token,
            new_lock_token=new_lock_token,
        )

    assert (active_path.read_bytes() if active_path.exists() else None) == active_before
    assert {
        path.name: path.read_bytes()
        for path in audit_dir.iterdir()
    } == audit_before
    assert residue.read_bytes() == residue_before


def test_completed_released_outcome_replays_read_only_without_residue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, old_lock_token = _running(tmp_path)
    _transition_to_failed(tmp_path, old_lock_token)
    _recovery_sandbox(tmp_path)
    monkeypatch.setattr(locking.socket, "gethostname", lambda: "test-host")
    monkeypatch.setattr(locking, "_owner_pid_is_confirmed_dead", lambda _pid: True)
    recovery_token = str(uuid.uuid4())
    new_lock_token = str(uuid.uuid4())
    assert recover_stale_active_run(
        tmp_path,
        run_id="run_001",
        recovery_token=recovery_token,
        new_lock_token=new_lock_token,
    ) == {"replayed": False, "successor_phase": "released"}
    audit_dir = tmp_path / "runs" / "run_001" / "lock_recovery_audit"
    audit_before = {
        path.name: path.read_bytes()
        for path in audit_dir.iterdir()
    }
    monkeypatch.setattr(
        locking,
        "release_active_run_lock",
        lambda *_args, **_kwargs: pytest.fail(
            "completed released outcome replay must not release the lock again"
        ),
    )

    assert recover_stale_active_run(
        tmp_path,
        run_id="run_001",
        recovery_token=recovery_token,
        new_lock_token=new_lock_token,
    ) == {"replayed": True, "successor_phase": "released"}
    assert {
        path.name: path.read_bytes()
        for path in audit_dir.iterdir()
    } == audit_before
    assert not (tmp_path / "runs" / ".active_run.lock").exists()


def test_takeover_rejects_live_or_nonlocal_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _running(tmp_path)
    _recovery_sandbox(tmp_path)
    monkeypatch.setattr(locking.socket, "gethostname", lambda: "test-host")
    monkeypatch.setattr(locking, "_owner_pid_is_confirmed_dead", lambda _pid: False)
    with pytest.raises(ActiveRunLockError, match="not confirmed dead"):
        recover_stale_active_run(
            tmp_path,
            run_id="run_001",
            recovery_token=str(uuid.uuid4()),
            new_lock_token=str(uuid.uuid4()),
        )
    lock_path = tmp_path / "runs" / ".active_run.lock"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["hostname"] = "other-host"
    lock_path.write_bytes(locking._canonical_json_bytes(lock))
    monkeypatch.setattr(locking, "_owner_pid_is_confirmed_dead", lambda _pid: True)
    with pytest.raises(ActiveRunLockError, match="same host"):
        recover_stale_active_run(
            tmp_path,
            run_id="run_001",
            recovery_token=str(uuid.uuid4()),
            new_lock_token=str(uuid.uuid4()),
        )


def test_takeover_requires_controlled_phase_a1_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _running(tmp_path)
    monkeypatch.setattr(locking.socket, "gethostname", lambda: "test-host")
    monkeypatch.setattr(locking, "_owner_pid_is_confirmed_dead", lambda _pid: True)

    with pytest.raises(ActiveRunLockError, match="controlled Phase A1 sandbox"):
        recover_stale_active_run(
            tmp_path,
            run_id="run_001",
            recovery_token=str(uuid.uuid4()),
            new_lock_token=str(uuid.uuid4()),
        )
    assert not (tmp_path / "runs" / "run_001" / "lock_recovery_audit").exists()


def test_takeover_requires_a_distinct_successor_lock_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, old_lock_token = _running(tmp_path)
    _recovery_sandbox(tmp_path)
    monkeypatch.setattr(locking.socket, "gethostname", lambda: "test-host")
    monkeypatch.setattr(locking, "_owner_pid_is_confirmed_dead", lambda _pid: True)

    with pytest.raises(ActiveRunLockError, match="distinct from the old owner"):
        recover_stale_active_run(
            tmp_path,
            run_id="run_001",
            recovery_token=str(uuid.uuid4()),
            new_lock_token=old_lock_token,
        )
    assert not (tmp_path / "runs" / "run_001" / "lock_recovery_audit").exists()


def test_takeover_rejects_a2_recovery_marker_before_writing_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _running(tmp_path)
    _recovery_sandbox(tmp_path)
    marker = tmp_path / "runs" / "run_001" / ".publication_recovery_required.json"
    marker.write_bytes(b"{}\n")
    monkeypatch.setattr(locking.socket, "gethostname", lambda: "test-host")
    monkeypatch.setattr(locking, "_owner_pid_is_confirmed_dead", lambda _pid: True)

    with pytest.raises(ActiveRunLockError, match="A2 Publication preflight"):
        recover_stale_active_run(
            tmp_path,
            run_id="run_001",
            recovery_token=str(uuid.uuid4()),
            new_lock_token=str(uuid.uuid4()),
        )
    audit_dir = tmp_path / "runs" / "run_001" / "lock_recovery_audit"
    assert not list(audit_dir.glob("*.intent.json"))


def test_takeover_rejects_existing_a1_recovery_marker_before_writing_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _running(tmp_path)
    _recovery_sandbox(tmp_path)
    work = tmp_path / "runs" / "run_001" / "work"
    work.mkdir()
    (work / ".a1_recovery_required.json").write_bytes(b"{}\n")
    monkeypatch.setattr(locking.socket, "gethostname", lambda: "test-host")
    monkeypatch.setattr(locking, "_owner_pid_is_confirmed_dead", lambda _pid: True)

    with pytest.raises(ActiveRunLockError, match="controlled Phase A1 sandbox"):
        recover_stale_active_run(
            tmp_path,
            run_id="run_001",
            recovery_token=str(uuid.uuid4()),
            new_lock_token=str(uuid.uuid4()),
        )
    assert not (tmp_path / "runs" / "run_001" / "lock_recovery_audit").exists()


def test_manual_takeover_uses_the_same_audit_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _running(tmp_path)
    _recovery_sandbox(tmp_path)
    monkeypatch.setattr(locking.socket, "gethostname", lambda: "test-host")
    monkeypatch.setattr(locking, "_owner_pid_is_confirmed_dead", lambda _pid: True)
    recovery_token = str(uuid.uuid4())
    recover_stale_active_run(
        tmp_path,
        run_id="run_001",
        recovery_token=recovery_token,
        new_lock_token=str(uuid.uuid4()),
        actor_kind="manual",
        operator_identity="local-owner",
        reason="resume a verified local stale run",
    )
    intent_path = next(
        (tmp_path / "runs" / "run_001" / "lock_recovery_audit").glob("*.intent.json")
    )
    intent = json.loads(intent_path.read_text(encoding="utf-8"))
    assert intent["actor_kind"] == "manual"
    assert intent["operator_identity"] == "local-owner"
    assert intent["reason"] == "resume a verified local stale run"


def test_takeover_target_change_after_intent_fails_closed_with_audit_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _running(tmp_path)
    _recovery_sandbox(tmp_path)
    monkeypatch.setattr(locking.socket, "gethostname", lambda: "test-host")
    monkeypatch.setattr(locking, "_owner_pid_is_confirmed_dead", lambda _pid: True)
    original_update = locking._atomic_update_lock

    def replace_target_before_takeover(root: Path, **kwargs: object) -> dict[str, object]:
        lock_path = root / "runs" / ".active_run.lock"
        changed = json.loads(lock_path.read_text(encoding="utf-8"))
        changed["task_id"] = "task_002"
        lock_path.write_bytes(locking._canonical_json_bytes(changed))
        return original_update(root, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(locking, "_atomic_update_lock", replace_target_before_takeover)
    with pytest.raises(ActiveRunLockError, match="bytes changed"):
        recover_stale_active_run(
            tmp_path,
            run_id="run_001",
            recovery_token=str(uuid.uuid4()),
            new_lock_token=str(uuid.uuid4()),
        )
    assert list((tmp_path / "runs" / "run_001" / "lock_recovery_audit").glob("*.intent.json"))
    assert not (tmp_path / "runs" / ".active_run.recovery.lock").exists()
    monkeypatch.setattr(locking, "_atomic_update_lock", original_update)
    with pytest.raises(ActiveRunLockError, match="incomplete recovery intent"):
        recover_stale_active_run(
            tmp_path,
            run_id="run_001",
            recovery_token=str(uuid.uuid4()),
            new_lock_token=str(uuid.uuid4()),
        )


@pytest.mark.parametrize(
    ("entry_names", "match"),
    [
        (["orphan.outcome.1.json"], "orphan recovery outcome"),
        (
            ["orphan.intent.json", "orphan.outcome.2.json"],
            "unknown recovery audit entry",
        ),
        (
            ["first.intent.json", "second.intent.json", "first.outcome.1.json"],
            "exactly one intent/outcome pair",
        ),
        (
            ["first.intent.json", "first.outcome.1.json", "second.outcome.1.json"],
            "exactly one intent/outcome pair",
        ),
        (
            ["first.intent.json", "second.outcome.1.json"],
            "basenames do not match",
        ),
    ],
)
def test_recovery_audit_residue_blocks_before_mutex_and_lock_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry_names: list[str],
    match: str,
) -> None:
    _running(tmp_path)
    _recovery_sandbox(tmp_path)
    audit_dir = tmp_path / "runs" / "run_001" / "lock_recovery_audit"
    audit_dir.mkdir()
    for name in entry_names:
        (audit_dir / name).write_bytes(b"{}\n")

    _assert_audit_rejected_before_recovery_mutex(
        tmp_path,
        monkeypatch,
        match=match,
    )


def test_completed_recovery_with_an_extra_intent_fails_closed_on_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _running(tmp_path)
    _recovery_sandbox(tmp_path)
    monkeypatch.setattr(locking.socket, "gethostname", lambda: "test-host")
    monkeypatch.setattr(locking, "_owner_pid_is_confirmed_dead", lambda _pid: True)
    recovery_token = str(uuid.uuid4())
    new_lock_token = str(uuid.uuid4())
    recover_stale_active_run(
        tmp_path,
        run_id="run_001",
        recovery_token=recovery_token,
        new_lock_token=new_lock_token,
    )
    audit_dir = tmp_path / "runs" / "run_001" / "lock_recovery_audit"
    (audit_dir / "orphan.intent.json").write_bytes(b"{}\n")
    lock_path = tmp_path / "runs" / ".active_run.lock"
    lock_before = lock_path.read_bytes()
    entries_before = sorted(path.name for path in audit_dir.iterdir())
    monkeypatch.setattr(
        locking,
        "_validate_completed_outcome_runtime",
        lambda *_args, **_kwargs: pytest.fail(
            "ambiguous audit collection must block before completed outcome replay"
        ),
    )

    with pytest.raises(ActiveRunLockError, match="exactly one intent/outcome pair"):
        recover_stale_active_run(
            tmp_path,
            run_id="run_001",
            recovery_token=recovery_token,
            new_lock_token=new_lock_token,
        )

    assert lock_path.read_bytes() == lock_before
    assert sorted(path.name for path in audit_dir.iterdir()) == entries_before


def test_recovery_audit_directory_entry_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _running(tmp_path)
    _recovery_sandbox(tmp_path)
    audit_dir = tmp_path / "runs" / "run_001" / "lock_recovery_audit"
    audit_dir.mkdir()
    (audit_dir / "blocked.intent.json").mkdir()

    _assert_audit_rejected_before_recovery_mutex(
        tmp_path,
        monkeypatch,
        match="must be a regular file",
    )


def test_recovery_audit_reparse_entry_fails_closed_without_link_privileges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _running(tmp_path)
    _recovery_sandbox(tmp_path)
    audit_dir = tmp_path / "runs" / "run_001" / "lock_recovery_audit"
    audit_dir.mkdir()
    reparse_entry = audit_dir / "blocked.intent.json"
    reparse_entry.write_bytes(b"{}\n")
    original_lstat = locking._lstat

    class FakeReparseStat:
        st_mode = stat.S_IFREG
        st_file_attributes = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

    def fake_lstat(path: Path, *, label: str) -> os.stat_result | FakeReparseStat | None:
        if path == reparse_entry:
            return FakeReparseStat()
        return original_lstat(path, label=label)

    monkeypatch.setattr(locking, "_lstat", fake_lstat)
    _assert_audit_rejected_before_recovery_mutex(
        tmp_path,
        monkeypatch,
        match="symlink or reparse point",
    )


def test_recovery_audit_symlink_entry_fails_closed_when_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _running(tmp_path)
    _recovery_sandbox(tmp_path)
    audit_dir = tmp_path / "runs" / "run_001" / "lock_recovery_audit"
    audit_dir.mkdir()
    target = tmp_path / "external-intent.json"
    target.write_bytes(b"{}\n")
    link = audit_dir / "blocked.intent.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("file symlink creation is unavailable")

    _assert_audit_rejected_before_recovery_mutex(
        tmp_path,
        monkeypatch,
        match="symlink or reparse point",
    )


def test_malformed_existing_lock_fails_closed(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / ".active_run.lock").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ActiveRunLockError, match="fields are invalid"):
        read_active_run_lock(tmp_path)


def test_runs_symlink_is_rejected_when_supported(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    runs = tmp_path / "runs"
    try:
        runs.symlink_to(external, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlink creation is unavailable")
    with pytest.raises(ActiveRunLockError, match="symlink or reparse"):
        _acquire(tmp_path)


def test_reparse_detection_does_not_require_link_creation() -> None:
    class FakeStat:
        st_file_attributes = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

    assert locking._is_reparse(FakeStat()) is True


def test_directory_sync_failure_preserves_uncertain_owned_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    original = locking.controlled_fs.write_exclusive

    def fail_barrier(root: Path, relative: str, data: bytes) -> None:
        nonlocal calls
        original(root, relative, data)
        calls += 1
        raise locking.controlled_fs.ControlledFilesystemError(
            "injected parent durability failure"
        )

    monkeypatch.setattr(locking.controlled_fs, "write_exclusive", fail_barrier)
    with pytest.raises(ActiveRunLockError, match="safely acquire") as exc_info:
        _acquire(tmp_path)
    assert "injected parent durability failure" in str(exc_info.value.__cause__)
    assert calls >= 1
    assert (tmp_path / "runs" / ".active_run.lock").is_file()


def test_failed_acquisition_delete_then_sync_error_restores_blocking_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controlled = locking.controlled_fs
    original_unlink = controlled.unlink
    original_read = locking._read_lock
    verification_failed = False

    def fail_after_published_verification(path: Path):
        nonlocal verification_failed
        value = original_read(path)
        if path.name == ".active_run.lock" and not verification_failed:
            verification_failed = True
            raise ActiveRunLockError("injected post-publication verification failure")
        return value

    def report_after_tombstone_delete(
        root: Path,
        relative: str,
        *,
        expected_identity: tuple[int, int] | None = None,
    ) -> None:
        original_unlink(root, relative, expected_identity=expected_identity)
        if Path(relative).name.startswith(locking.ACTIVE_RUN_RELEASE_PREFIX):
            raise OSError("injected post-delete directory sync report")

    monkeypatch.setattr(locking, "_read_lock", fail_after_published_verification)
    monkeypatch.setattr(controlled, "unlink", report_after_tombstone_delete)
    with pytest.raises(ActiveRunLockError, match="post-publication verification"):
        _acquire(tmp_path)
    runs = tmp_path / "runs"
    blocking = tuple(runs.glob(f"{locking.ACTIVE_RUN_RELEASE_PREFIX}*.json"))
    recovery = runs / ".active_run.recovery.lock"
    assert blocking or recovery.is_file()
    assert not (runs / ".active_run.lock").exists()


def test_failed_acquisition_identity_fence_preserves_same_byte_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_read = locking._read_lock
    replaced = False

    def replace_then_fail(path: Path):
        nonlocal replaced
        value = original_read(path)
        if path.name == ".active_run.lock" and not replaced:
            path.unlink()
            path.write_bytes(value[1])
            replaced = True
            raise ActiveRunLockError("injected post-publication verification failure")
        return value

    monkeypatch.setattr(locking, "_read_lock", replace_then_fail)
    with pytest.raises(ActiveRunLockError, match="post-publication verification") as exc_info:
        _acquire(tmp_path)
    path = tmp_path / "runs" / ".active_run.lock"
    assert replaced and path.is_file()
    assert any("cleanup was incomplete" in note for note in exc_info.value.__notes__)


def test_failed_acquisition_preserves_replaced_lock_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replacement = b'{"owner":"external"}\n'
    original = locking.controlled_fs.write_exclusive

    def replace_then_fail(root: Path, relative: str, data: bytes) -> None:
        original(root, relative, data)
        lock_path = tmp_path / "runs" / ".active_run.lock"
        lock_path.write_bytes(replacement)
        raise locking.controlled_fs.ControlledFilesystemError(
            "injected acquisition barrier failure"
        )

    monkeypatch.setattr(locking.controlled_fs, "write_exclusive", replace_then_fail)
    with pytest.raises(ActiveRunLockError, match="safely acquire") as exc_info:
        _acquire(tmp_path)
    assert (tmp_path / "runs" / ".active_run.lock").read_bytes() == replacement
    assert any("publication identity is uncertain" in note for note in exc_info.value.__notes__)


def test_windows_directory_barrier_failure_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.skip("Windows directory durability regression")
    controlled = locking.controlled_fs

    ctypes_module = __import__("ctypes")

    def fail_flush(handle: object) -> bool:
        ctypes_module.set_last_error(5)
        return False

    monkeypatch.setattr(controlled._kernel32, "FlushFileBuffers", fail_flush)
    with controlled._win_parent(tmp_path, ()) as parent:
        with pytest.raises(controlled.ControlledFilesystemError, match="flush controlled directory"):
            controlled._win_flush_directory(parent)
    assert not tuple(tmp_path.glob(".controlled-sync-*.tmp"))


def test_windows_fd_conversion_failure_removes_created_controlled_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.skip("Windows handle-to-fd cleanup regression")
    controlled = locking.controlled_fs

    def fail_fd(handle: int) -> int:
        raise OSError("injected handle conversion failure")

    monkeypatch.setattr(controlled, "_win_file_fd", fail_fd)
    exclusive = tmp_path / "exclusive.bin"
    with pytest.raises(OSError, match="handle conversion failure"):
        controlled.write_exclusive(tmp_path, "exclusive.bin", b"new")
    assert not exclusive.exists()

    target = tmp_path / "target.bin"
    target.write_bytes(b"old")
    with pytest.raises(OSError, match="handle conversion failure"):
        controlled.atomic_replace(tmp_path, "target.bin", b"new")
    assert target.read_bytes() == b"old"
    assert not tuple(tmp_path.glob(".target.bin.*.tmp"))



def test_windows_every_controlled_mutation_invokes_parent_write_through_barrier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.skip("Windows write-through barrier regression")
    controlled = locking.controlled_fs
    original = controlled._win_flush_directory
    calls = 0

    def record(handle: int) -> None:
        nonlocal calls
        calls += 1
        original(handle)

    monkeypatch.setattr(controlled, "_win_flush_directory", record)
    controlled.write_exclusive(tmp_path, "exclusive.bin", b"one")
    controlled.atomic_replace(tmp_path, "exclusive.bin", b"two")
    controlled.write_exclusive(tmp_path, "source.bin", b"three")
    controlled.rename(tmp_path, "source.bin", "renamed.bin", replace=False)
    controlled.unlink(tmp_path, "renamed.bin")
    assert calls == 5


def test_identity_bound_unlink_preserves_replacement_leaf(tmp_path: Path) -> None:
    controlled = locking.controlled_fs
    identity = controlled.write_exclusive(tmp_path, "owned.lock", b"owner-a")
    path = tmp_path / "owned.lock"
    path.unlink()
    path.write_bytes(b"owner-b")

    with pytest.raises(
        controlled.ControlledFilesystemError, match="leaf identity changed"
    ):
        controlled.unlink(tmp_path, "owned.lock", expected_identity=identity)

    assert path.read_bytes() == b"owner-b"


def test_nested_directory_binding_rejects_identity_override(tmp_path: Path) -> None:
    controlled = locking.controlled_fs
    original = tmp_path / "original"
    substitute = tmp_path / "substitute"
    original.mkdir()
    substitute.mkdir()
    original_identity = controlled.directory_identity(original)
    substitute_identity = controlled.directory_identity(substitute)
    outer = (
        (tmp_path, *controlled.directory_identity(tmp_path)),
        (original, *original_identity),
    )
    conflicting = (
        (tmp_path, *controlled.directory_identity(tmp_path)),
        (original, *substitute_identity),
    )

    with controlled.bind_directory_identities(outer):
        with pytest.raises(
            controlled.ControlledFilesystemError, match="binding conflicts"
        ):
            with controlled.bind_directory_identities(conflicting):
                pytest.fail("conflicting nested binding must not be entered")


def test_windows_directory_creation_retries_child_handle_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.skip("Windows child directory handle cleanup regression")
    controlled = locking.controlled_fs
    original = controlled._close_handle
    attempts = 0

    def fail_first_close(handle: int) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise controlled.ControlledFilesystemError(
                "injected first child handle close failure"
            )
        original(handle)

    monkeypatch.setattr(controlled, "_close_handle", fail_first_close)
    with pytest.raises(
        controlled.ControlledFilesystemError,
        match="first child handle close failure",
    ):
        controlled.make_directory(tmp_path, "created")
    assert attempts >= 2
    assert (tmp_path / "created").is_dir()


def test_windows_atomic_replace_preserves_target_when_rename_reports_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.skip("Windows rename-outcome regression")
    controlled = locking.controlled_fs
    target = tmp_path / "target.bin"
    target.write_bytes(b"old")
    original = controlled._win_rename

    def rename_then_report(*args: object, **kwargs: object) -> None:
        original(*args, **kwargs)
        raise OSError("injected post-rename report")

    monkeypatch.setattr(controlled, "_win_rename", rename_then_report)
    with pytest.raises(OSError, match="post-rename"):
        controlled.atomic_replace(tmp_path, "target.bin", b"new")
    assert target.read_bytes() == b"new"
    assert not tuple(tmp_path.glob(".target.bin.*.tmp"))


def test_exclusive_partial_write_with_cleanup_failure_never_publishes_final_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controlled = locking.controlled_fs
    original_write_all = controlled._write_all

    def partial(descriptor: int, data: bytes) -> None:
        if data == b"authority":
            os.write(descriptor, b"partial")
            raise OSError("injected exclusive partial write")
        original_write_all(descriptor, data)

    monkeypatch.setattr(controlled, "_write_all", partial)
    if os.name == "nt":
        def reject_dispose(handle: int) -> None:
            raise controlled.ControlledFilesystemError("injected exclusive cleanup failure")

        monkeypatch.setattr(controlled, "_win_dispose", reject_dispose)
    else:
        original_unlink = controlled.os.unlink

        def reject_unlink(path: object, *args: object, **kwargs: object) -> None:
            if str(path).startswith(".exclusive.bin."):
                raise OSError("injected exclusive cleanup failure")
            original_unlink(path, *args, **kwargs)

        monkeypatch.setattr(controlled.os, "unlink", reject_unlink)

    with pytest.raises(OSError, match="exclusive partial write") as captured:
        controlled.write_exclusive(tmp_path, "exclusive.bin", b"authority")
    assert not (tmp_path / "exclusive.bin").exists()
    assert tuple(tmp_path.glob(".exclusive.bin.*.tmp"))
    assert any("exclusive publication cleanup failed" in note for note in captured.value.__notes__)


@pytest.mark.parametrize("stage", ["partial_write", "file_fsync", "directory_sync"])
def test_windows_atomic_replace_real_stage_failures_preserve_unambiguous_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    if os.name != "nt":
        pytest.skip("Windows controlled replacement stage regression")
    controlled = locking.controlled_fs
    target = tmp_path / "target.bin"
    target.write_bytes(b"old")

    if stage == "partial_write":
        original_write_all = controlled._write_all

        def partial(descriptor: int, data: bytes) -> None:
            if data == b"new":
                os.write(descriptor, b"n")
                raise OSError("injected partial write")
            original_write_all(descriptor, data)

        monkeypatch.setattr(controlled, "_write_all", partial)
    elif stage == "file_fsync":
        original_fsync = controlled.os.fsync
        failed = False

        def fail_first_fsync(descriptor: int) -> None:
            nonlocal failed
            if not failed:
                failed = True
                raise OSError("injected file fsync")
            original_fsync(descriptor)

        monkeypatch.setattr(controlled.os, "fsync", fail_first_fsync)
    else:
        def fail_directory_sync(handle: int) -> None:
            raise OSError("injected directory sync")

        monkeypatch.setattr(controlled, "_win_flush_directory", fail_directory_sync)

    with pytest.raises(OSError):
        controlled.atomic_replace(tmp_path, "target.bin", b"new")
    expected = b"new" if stage == "directory_sync" else b"old"
    assert target.read_bytes() == expected
    assert not tuple(tmp_path.glob(".target.bin.*.tmp"))


def test_windows_cleanup_preserves_primary_error_and_closes_all_parent_handles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.skip("Windows cleanup diagnostics regression")
    controlled = locking.controlled_fs

    def fail_conversion(handle: int) -> int:
        raise OSError("primary conversion failure")

    def fail_cleanup(handle: int) -> None:
        raise controlled.ControlledFilesystemError("cleanup failure")

    monkeypatch.setattr(controlled, "_win_file_fd", fail_conversion)
    monkeypatch.setattr(controlled, "_win_dispose", fail_cleanup)
    monkeypatch.setattr(controlled, "_close_handle", fail_cleanup)
    with pytest.raises(OSError, match="primary conversion") as captured:
        controlled._win_fd_or_dispose(123)
    assert len(getattr(captured.value, "__notes__", ())) == 2

    monkeypatch.undo()
    child = tmp_path / "child"
    child.mkdir()
    original_close = controlled._close_handle
    closed: list[int] = []

    def close_then_report(handle: int) -> None:
        original_close(handle)
        closed.append(handle)
        if len(closed) == 1:
            raise controlled.ControlledFilesystemError("first close report")

    monkeypatch.setattr(controlled, "_close_handle", close_then_report)
    with pytest.raises(controlled.ControlledFilesystemError, match="first close"):
        with controlled._win_parent(tmp_path, ("child",)):
            pass
    assert len(closed) == 2


def test_posix_no_replace_rename_cannot_clobber_racing_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX no-clobber regression")
    controlled = locking.controlled_fs
    source = tmp_path / "source.bin"
    target = tmp_path / "target.bin"
    source.write_bytes(b"source")
    original_link = controlled.os.link

    def racing_link(*args: object, **kwargs: object) -> None:
        target.write_bytes(b"racer")
        original_link(*args, **kwargs)

    monkeypatch.setattr(controlled.os, "link", racing_link)
    with pytest.raises(FileExistsError):
        controlled.rename(tmp_path, "source.bin", "target.bin", replace=False)
    assert source.read_bytes() == b"source"
    assert target.read_bytes() == b"racer"


def test_posix_exclusive_publication_syncs_final_name_before_temp_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX exclusive publication ordering regression")
    controlled = locking.controlled_fs
    original_link = controlled.os.link
    original_unlink = controlled.os.unlink
    original_fsync = controlled.os.fsync
    events: list[str] = []

    def record_link(*args: object, **kwargs: object) -> object:
        events.append("link")
        return original_link(*args, **kwargs)

    def record_unlink(path: object, *args: object, **kwargs: object) -> object:
        if str(path).startswith(".exclusive.bin."):
            events.append("unlink_temp")
        return original_unlink(path, *args, **kwargs)

    def record_fsync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            events.append("directory_fsync")
        original_fsync(descriptor)

    monkeypatch.setattr(controlled.os, "link", record_link)
    monkeypatch.setattr(controlled.os, "unlink", record_unlink)
    monkeypatch.setattr(controlled.os, "fsync", record_fsync)
    controlled.write_exclusive(tmp_path, "exclusive.bin", b"authority")

    first_barrier = events.index("directory_fsync")
    unlink_index = events.index("unlink_temp")
    second_barrier = events.index("directory_fsync", first_barrier + 1)
    assert events.index("link") < first_barrier < unlink_index < second_barrier
    assert (tmp_path / "exclusive.bin").read_bytes() == b"authority"


def test_state_transition_barrier_failure_preserves_uncertain_state_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allocation_token, lock_token = _acquire(tmp_path)
    original = locking.controlled_fs.write_exclusive

    def fail_after_write(root: Path, relative: str, data: bytes) -> None:
        original(root, relative, data)
        if relative.endswith("/.active_run.state.lock"):
            raise locking.controlled_fs.ControlledFilesystemError(
                "injected state-transition durability failure"
            )

    monkeypatch.setattr(locking.controlled_fs, "write_exclusive", fail_after_write)
    with pytest.raises(ActiveRunLockError, match="state lock"):
        reserve_active_run_id(
            tmp_path,
            run_id="run_001",
            expected_allocation_token=allocation_token,
            expected_lock_token=lock_token,
        )
    assert (tmp_path / "runs" / ".active_run.state.lock").is_file()
    with pytest.raises(ActiveRunLockError, match="another worker"):
        reserve_active_run_id(
            tmp_path,
            run_id="run_001",
            expected_allocation_token=allocation_token,
            expected_lock_token=lock_token,
        )
    assert read_active_run_lock(tmp_path)["reserved_run_id"] is None


def test_lock_document_rejects_unknown_phase_and_noncanonical_uuid(tmp_path: Path) -> None:
    allocation_token, lock_token = _acquire(tmp_path)
    path = tmp_path / "runs" / ".active_run.lock"
    document = json.loads(path.read_bytes())
    document["phase"] = "stale"
    path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
    with pytest.raises(ActiveRunLockError, match="phase"):
        read_active_run_lock(tmp_path)

    path.unlink()
    with pytest.raises(ActiveRunLockError, match="canonical UUID"):
        acquire_active_run_lock(
            tmp_path,
            task_id="task_001",
            allocation_token=allocation_token.upper(),
            lock_token=lock_token,
            created_at=TIME,
        )


def test_concurrent_reservations_allow_only_one_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allocation_token, lock_token = _acquire(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    original = locking._read_lock

    def blocking_read(path: Path):
        if threading.current_thread().name.startswith("reservation-owner") and not entered.is_set():
            entered.set()
            assert release.wait(timeout=5)
        return original(path)

    monkeypatch.setattr(locking, "_read_lock", blocking_read)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="reservation-owner") as pool:
        first = pool.submit(
            reserve_active_run_id,
            tmp_path,
            run_id="run_001",
            expected_allocation_token=allocation_token,
            expected_lock_token=lock_token,
        )
        assert entered.wait(timeout=5)
        with pytest.raises(ActiveRunLockError, match="another worker"):
            reserve_active_run_id(
                tmp_path,
                run_id="run_002",
                expected_allocation_token=allocation_token,
                expected_lock_token=lock_token,
            )
        release.set()
        assert first.result(timeout=5)["reserved_run_id"] == "run_001"
    assert read_active_run_lock(tmp_path)["reserved_run_id"] == "run_001"


def test_running_update_and_release_cannot_both_commit_while_overlapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allocation_token, lock_token = _acquire(tmp_path)
    reserve_active_run_id(
        tmp_path,
        run_id="run_001",
        expected_allocation_token=allocation_token,
        expected_lock_token=lock_token,
    )
    run_dir = tmp_path / "runs" / "run_001"
    run_dir.mkdir()
    StateStore(tmp_path).initialize_run(
        run_id="run_001",
        allocation_token=allocation_token,
        plan_fingerprint=PLAN_SHA,
        task_plan=TASK_PLAN,
        expected_lock_token=lock_token,
        created_at=TIME,
    )
    entered = threading.Event()
    release = threading.Event()
    original = locking._active_run_state_lock

    @contextmanager
    def blocking_state_lock(project_root: Path):
        with original(project_root):
            if threading.current_thread().name.startswith("running-owner"):
                entered.set()
                assert release.wait(timeout=5)
            yield

    monkeypatch.setattr(locking, "_active_run_state_lock", blocking_state_lock)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="running-owner") as pool:
        update = pool.submit(
            mark_active_run_running,
            tmp_path,
            run_id="run_001",
            expected_allocation_token=allocation_token,
            expected_lock_token=lock_token,
        )
        assert entered.wait(timeout=5)
        with pytest.raises(ActiveRunLockError, match="another worker"):
            release_active_run_lock(
                tmp_path,
                run_id="run_001",
                expected_allocation_token=allocation_token,
                expected_lock_token=lock_token,
            )
        release.set()
        assert update.result(timeout=5)["phase"] == "running"
    assert read_active_run_lock(tmp_path)["phase"] == "running"


def test_final_release_sync_failure_restores_blocking_tombstone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allocation_token, lock_token = _running(tmp_path)
    original = locking.controlled_fs.unlink

    def remove_then_fail(
        root: Path,
        relative: str,
        *,
        expected_identity: tuple[int, int] | None = None,
    ) -> None:
        original(root, relative, expected_identity=expected_identity)
        if Path(relative).name.startswith(".active_run.release."):
            raise locking.controlled_fs.ControlledFilesystemError(
                "injected final release barrier failure"
            )

    monkeypatch.setattr(locking.controlled_fs, "unlink", remove_then_fail)
    with pytest.raises(ActiveRunLockError, match="release is incomplete"):
        release_active_run_lock(
            tmp_path,
            run_id="run_001",
            expected_allocation_token=allocation_token,
            expected_lock_token=lock_token,
        )
    assert list((tmp_path / "runs").glob(".active_run.release.*"))
    with pytest.raises(ActiveRunLockError, match="release tombstone"):
        acquire_active_run_lock(
            tmp_path,
            task_id="task_002",
            allocation_token=str(uuid.uuid4()),
            lock_token=str(uuid.uuid4()),
            created_at=TIME,
        )


def test_active_run_state_lock_disappearance_restores_blocking_residue(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    state_lock = runs / ".active_run.state.lock"

    with pytest.raises(ActiveRunLockError, match="state lock"):
        with locking._active_run_state_lock(tmp_path):
            state_lock.unlink()

    assert state_lock.is_file()
    with pytest.raises(ActiveRunLockError, match="another worker"):
        with locking._active_run_state_lock(tmp_path):
            pass


def test_tombstone_restore_failure_leaves_active_recovery_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allocation_token, lock_token = _running(tmp_path)
    original_unlink = locking.controlled_fs.unlink
    original_write = locking.controlled_fs.write_exclusive

    def remove_then_fail(
        root: Path,
        relative: str,
        *,
        expected_identity: tuple[int, int] | None = None,
    ) -> None:
        original_unlink(root, relative, expected_identity=expected_identity)
        if Path(relative).name.startswith(".active_run.release."):
            raise OSError("injected final release barrier failure")

    def fail_tombstone_restore(root: Path, relative: str, data: bytes) -> None:
        if Path(relative).name.startswith(".active_run.release."):
            raise OSError("injected tombstone restoration failure")
        return original_write(root, relative, data)

    monkeypatch.setattr(locking.controlled_fs, "unlink", remove_then_fail)
    monkeypatch.setattr(locking.controlled_fs, "write_exclusive", fail_tombstone_restore)
    with pytest.raises(ActiveRunLockError, match="release is incomplete"):
        release_active_run_lock(
            tmp_path,
            run_id="run_001",
            expected_allocation_token=allocation_token,
            expected_lock_token=lock_token,
        )

    assert (tmp_path / "runs" / ".active_run.recovery.lock").is_file()
    with pytest.raises(ActiveRunLockError, match="A3.2 recovery"):
        _acquire(tmp_path)
