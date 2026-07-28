from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import threading
import uuid

import pytest

from orchestrator.inspection_workflow import locking
from orchestrator.inspection_workflow.locking import (
    ActiveRunLockError,
    acquire_active_run_lock,
    mark_active_run_running,
    read_active_run_lock,
    release_active_run_lock,
    reserve_active_run_id,
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


def test_release_cleanup_failure_preserves_tombstone_and_blocks_new_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allocation_token, lock_token = _running(tmp_path)
    real_unlink = Path.unlink

    def fail_tombstone(self: Path, *args: object, **kwargs: object) -> None:
        if self.name.startswith(".active_run.release."):
            raise OSError("injected tombstone cleanup failure")
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_tombstone)
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


def test_directory_sync_failure_does_not_publish_owned_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def fail_sync(path: Path, *, label: str) -> None:
        nonlocal calls
        calls += 1
        raise ActiveRunLockError("injected parent sync failure")

    monkeypatch.setattr(locking, "_sync_directory", fail_sync)
    with pytest.raises(ActiveRunLockError, match="injected parent sync failure"):
        _acquire(tmp_path)
    assert calls >= 1
    assert not (tmp_path / "runs" / ".active_run.lock").exists()


def test_failed_acquisition_preserves_replaced_lock_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replacement = b'{"owner":"external"}\n'

    def replace_then_fail(path: Path, *, label: str) -> None:
        lock_path = tmp_path / "runs" / ".active_run.lock"
        lock_path.write_bytes(replacement)
        raise ActiveRunLockError("injected acquisition barrier failure")

    monkeypatch.setattr(locking, "_sync_directory", replace_then_fail)
    with pytest.raises(ActiveRunLockError, match="acquisition barrier failure") as exc_info:
        _acquire(tmp_path)
    assert (tmp_path / "runs" / ".active_run.lock").read_bytes() == replacement
    assert any("ownership bytes changed" in note for note in exc_info.value.__notes__)


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
    original = locking._sync_directory

    def fail_final_sync(path: Path, *, label: str) -> None:
        if label == "runs directory after Active Run Lock release":
            raise ActiveRunLockError("injected final release sync failure")
        original(path, label=label)

    monkeypatch.setattr(locking, "_sync_directory", fail_final_sync)
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
    original_sync = locking._sync_directory
    original_open = locking.os.open

    def fail_final_sync(path: Path, *, label: str) -> None:
        if label == "runs directory after Active Run Lock release":
            raise OSError("injected final release sync failure")
        original_sync(path, label=label)

    def fail_tombstone_restore(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes], *args: object
    ) -> int:
        if Path(path).name.startswith(".active_run.release."):
            raise OSError("injected tombstone restoration failure")
        return original_open(path, *args)

    monkeypatch.setattr(locking, "_sync_directory", fail_final_sync)
    monkeypatch.setattr(locking.os, "open", fail_tombstone_restore)
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
