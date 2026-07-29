from __future__ import annotations

import asyncio
import json
from pathlib import Path
import uuid

import pytest

from orchestrator.dag.builder import Task
from orchestrator.inspection_workflow import controller as controller_module
from orchestrator.inspection_workflow.controller import (
    InspectionWorkflowController,
    InspectionWorkflowControllerError,
)
from orchestrator.inspection_workflow.locking import (
    acquire_active_run_lock,
    mark_active_run_running,
    reserve_active_run_id,
)
from orchestrator.inspection_workflow.planning import (
    WorkflowPlanningError,
    build_required_task_plan,
    task_plan_fingerprint,
)
from orchestrator.state.store import (
    MAX_STATE_JOURNAL_RECORDS,
    StateConflictError,
    StateStore,
    StateStoreError,
)


T0 = "2026-07-29T00:00:00.000000Z"


class _Clock:
    def __init__(self) -> None:
        self.tick = 0

    def __call__(self) -> str:
        self.tick += 1
        return f"2026-07-29T00:00:01.{self.tick:06d}Z"


def _tasks() -> dict[str, Task]:
    return {
        "alpha": Task("alpha", "alpha", [], retries=1, cache=False),
        "beta": Task("beta", "beta", ["alpha"], retries=0, cache=True),
    }


def _controller_fixture(
    root: Path,
    tasks: dict[str, Task] | None = None,
    *,
    clock: _Clock | None = None,
) -> tuple[InspectionWorkflowController, str]:
    tasks = _tasks() if tasks is None else tasks
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
    run_dir = root / "runs" / "run_001"
    run_dir.mkdir()
    plan_fingerprint = task_plan_fingerprint(tasks)
    StateStore(root).initialize_run(
        run_id="run_001",
        allocation_token=allocation_token,
        plan_fingerprint=plan_fingerprint,
        task_plan=build_required_task_plan(tasks),
        expected_lock_token=lock_token,
        created_at=T0,
    )
    mark_active_run_running(
        root,
        run_id="run_001",
        expected_allocation_token=allocation_token,
        expected_lock_token=lock_token,
    )
    return (
        InspectionWorkflowController(
            root,
            run_id="run_001",
            expected_lock_token=lock_token,
            plan_fingerprint=plan_fingerprint,
            clock=clock or _Clock(),
        ),
        lock_token,
    )


def _pending_checkpoint_rows(root: Path) -> list[dict[str, object]]:
    path = root / "runs" / "run_001" / "state_journal.jsonl"
    return [
        row
        for row in (
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
        )
        if row["phase"] == "pending"
        and row["mutation_kind"] == "context_checkpoint"
    ]


def test_task_plan_is_stable_and_binds_execution_policy() -> None:
    left = {
        "tail": Task("tail", "tail", ["seed", "middle"], retries=2, cache=True),
        "seed": Task("seed", "seed", [], retries=0, cache=False),
        "middle": Task("middle", "middle", ["seed"], retries=1, cache=True),
    }
    right = {
        "middle": Task("middle", "middle", ["seed"], retries=1, cache=True),
        "seed": Task("seed", "seed", [], retries=0, cache=False),
        "tail": Task("tail", "tail", ["middle", "seed"], retries=2, cache=True),
    }

    assert build_required_task_plan(left) == build_required_task_plan(right)
    assert task_plan_fingerprint(left) == task_plan_fingerprint(right)

    changed = dict(right)
    changed["tail"] = Task("tail", "tail", ["middle", "seed"], retries=3, cache=True)
    assert task_plan_fingerprint(changed) != task_plan_fingerprint(left)


def test_task_plan_rejects_unknown_duplicate_and_cyclic_dependencies() -> None:
    with pytest.raises(WorkflowPlanningError, match="DAG"):
        build_required_task_plan(
            {"alpha": Task("alpha", "alpha", ["missing"], cache=False)}
        )
    with pytest.raises(WorkflowPlanningError, match="dependencies"):
        build_required_task_plan(
            {
                "alpha": Task(
                    "alpha", "alpha", ["beta", "beta"], cache=False
                ),
                "beta": Task("beta", "beta", [], cache=False),
            }
        )
    with pytest.raises(WorkflowPlanningError, match="DAG"):
        build_required_task_plan(
            {
                "alpha": Task("alpha", "alpha", ["beta"], cache=False),
                "beta": Task("beta", "beta", ["alpha"], cache=False),
            }
        )


def test_controller_initializes_one_cursor_and_returns_immutable_snapshot(
    tmp_path: Path,
) -> None:
    tasks = _tasks()
    controller, _ = _controller_fixture(tmp_path, tasks)

    asyncio.run(controller.prepare_execution(tasks))

    assert controller.snapshot["status"] == "RUNNING"
    assert controller.snapshot["state_version"] == 3
    assert dict(controller.snapshot["canonical_state"]["task_status"]) == {
        "alpha": "pending",
        "beta": "pending",
    }
    with pytest.raises(TypeError):
        controller.snapshot["state_version"] = 99  # type: ignore[index]

    rows = _pending_checkpoint_rows(tmp_path)
    assert [row["payload"]["checkpoint_kind"] for row in rows] == [
        "run_initialized"
    ]


def test_controller_derives_stable_operation_ids_and_persisted_timestamps(
    tmp_path: Path,
) -> None:
    tasks = _tasks()
    controller, _ = _controller_fixture(tmp_path, tasks, clock=_Clock())

    asyncio.run(controller.prepare_execution(tasks))
    asyncio.run(
        controller.submit_checkpoint_event(
            {"checkpoint_kind": "task_started", "task_id": "alpha"}
        )
    )

    rows = _pending_checkpoint_rows(tmp_path)
    assert [
        (row["operation_id"], row["payload"]["created_at"]) for row in rows
    ] == [
        (
            "run:run_001:checkpoint:run_initialized",
            "2026-07-29T00:00:01.000002Z",
        ),
        (
            "run:run_001:task:alpha:attempt:1:started",
            "2026-07-29T00:00:01.000004Z",
        ),
    ]


def test_controller_rejects_worker_state_snapshot_fields(tmp_path: Path) -> None:
    tasks = _tasks()
    controller, _ = _controller_fixture(tmp_path, tasks)
    asyncio.run(controller.prepare_execution(tasks))

    with pytest.raises(
        InspectionWorkflowControllerError, match="fields are invalid"
    ):
        asyncio.run(
            controller.submit_checkpoint_event(
                {
                    "checkpoint_kind": "task_started",
                    "task_id": "alpha",
                    "task_status": {"alpha": "success"},
                    "context_snapshot": {},
                }
            )
        )
    assert controller.snapshot["state_version"] == 3


def test_controller_rejects_path_bearing_nested_failure_provenance(
    tmp_path: Path,
) -> None:
    tasks = {"alpha": Task("alpha", "alpha", [], retries=0, cache=False)}
    controller, _ = _controller_fixture(tmp_path, tasks)
    asyncio.run(controller.prepare_execution(tasks))
    asyncio.run(
        controller.submit_checkpoint_event(
            {"checkpoint_kind": "task_started", "task_id": "alpha"}
        )
    )
    version = controller.snapshot["state_version"]

    with pytest.raises(
        InspectionWorkflowControllerError, match="failure provenance"
    ):
        asyncio.run(
            controller.submit_checkpoint_event(
                {
                    "checkpoint_kind": "task_failed",
                    "task_id": "alpha",
                    "retryable": False,
                    "failure_provenance": {
                        "error_type": "RuntimeError",
                        "traceback": r"C:\Users\secret\input.csv",
                    },
                    "error_summary": "RuntimeError: managed task execution failed",
                }
            )
        )
    assert controller.snapshot["state_version"] == version
    journal = (
        tmp_path / "runs" / "run_001" / "state_journal.jsonl"
    ).read_text(encoding="utf-8")
    assert "secret" not in journal


def test_controller_rejects_uncontrolled_skip_and_cache_event(
    tmp_path: Path,
) -> None:
    tasks = _tasks()
    controller, _ = _controller_fixture(tmp_path, tasks)
    asyncio.run(controller.prepare_execution(tasks))

    with pytest.raises(InspectionWorkflowControllerError, match="skip reason"):
        asyncio.run(
            controller.submit_checkpoint_event(
                {
                    "checkpoint_kind": "task_skipped",
                    "task_id": "alpha",
                    "skip_reason": r"C:\local\reason.txt",
                }
            )
        )
    with pytest.raises(InspectionWorkflowControllerError, match="event kind"):
        asyncio.run(
            controller.submit_checkpoint_event(
                {
                    "checkpoint_kind": "task_cache_hit",
                    "task_id": "alpha",
                    "task_output": {"value": "forged"},
                    "cache_provenance": {
                        "cache_key_sha256": "not-a-hash",
                        "path": "/tmp/cache.json",
                    },
                }
            )
        )
    assert controller.snapshot["state_version"] == 3


@pytest.mark.parametrize(
    ("case_name", "event", "error_match"),
    [
        (
            "root_skip",
            {
                "checkpoint_kind": "task_skipped",
                "task_id": "alpha",
                "skip_reason": "dependency_failed",
            },
            "dependency skip",
        ),
        (
            "early_start",
            {"checkpoint_kind": "task_started", "task_id": "beta"},
            "dependencies",
        ),
    ],
)
def test_controller_enforces_canonical_dependency_state_for_worker_events(
    tmp_path: Path,
    case_name: str,
    event: dict[str, object],
    error_match: str,
) -> None:
    tasks = _tasks()
    root = tmp_path / case_name
    root.mkdir()
    controller, _ = _controller_fixture(root, tasks)
    asyncio.run(controller.prepare_execution(tasks))

    with pytest.raises(InspectionWorkflowControllerError, match=error_match):
        asyncio.run(controller.submit_checkpoint_event(event))
    assert controller.snapshot["state_version"] == 3


def test_controller_halts_after_cas_failure_before_worker_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = _tasks()
    controller, _ = _controller_fixture(tmp_path, tasks)
    asyncio.run(controller.prepare_execution(tasks))

    monkeypatch.setattr(
        controller._store,
        "checkpoint_context",
        lambda **_kwargs: (_ for _ in ()).throw(
            StateConflictError("injected CAS conflict")
        ),
    )
    with pytest.raises(InspectionWorkflowControllerError, match="checkpoint"):
        asyncio.run(
            controller.submit_checkpoint_event(
                {"checkpoint_kind": "task_started", "task_id": "alpha"}
            )
        )
    with pytest.raises(InspectionWorkflowControllerError, match="halted"):
        asyncio.run(
            controller.submit_checkpoint_event(
                {"checkpoint_kind": "task_started", "task_id": "alpha"}
            )
        )


def test_controller_rejects_stale_lock_token_even_with_current_version(
    tmp_path: Path,
) -> None:
    tasks = _tasks()
    controller, _ = _controller_fixture(tmp_path, tasks)
    stale = InspectionWorkflowController(
        tmp_path,
        run_id="run_001",
        expected_lock_token=str(uuid.uuid4()),
        plan_fingerprint=task_plan_fingerprint(tasks),
        clock=_Clock(),
    )

    with pytest.raises(
        InspectionWorkflowControllerError, match="Active Run Lock"
    ):
        asyncio.run(stale.prepare_execution(tasks))
    assert controller.snapshot["state_version"] == 0


def test_prepare_rejects_stale_token_without_rewriting_diagnostics(
    tmp_path: Path,
) -> None:
    tasks = {"alpha": Task("alpha", "alpha", [], retries=0, cache=False)}
    controller, _ = _controller_fixture(tmp_path, tasks)
    asyncio.run(controller.prepare_execution(tasks))
    asyncio.run(
        controller.submit_checkpoint_event(
            {"checkpoint_kind": "task_started", "task_id": "alpha"}
        )
    )
    asyncio.run(
        controller.submit_checkpoint_event(
            {
                "checkpoint_kind": "task_succeeded",
                "task_id": "alpha",
                "task_output": {"value": "done"},
            }
        )
    )
    state_before = (
        tmp_path / "runs" / "run_001" / "state.json"
    ).read_bytes()
    diagnostic_paths = [
        tmp_path / "logs" / "dag_execution.json",
        tmp_path / "logs" / "execution_trace.json",
        tmp_path / "runs" / "run_001" / "dag.json",
        tmp_path / "runs" / "run_001" / "timeline.json",
    ]
    before = {
        path: path.read_bytes() if path.exists() else None
        for path in diagnostic_paths
    }
    stale = InspectionWorkflowController(
        tmp_path,
        run_id="run_001",
        expected_lock_token=str(uuid.uuid4()),
        plan_fingerprint=task_plan_fingerprint(tasks),
        clock=_Clock(),
    )

    with pytest.raises(
        InspectionWorkflowControllerError, match="Active Run Lock"
    ):
        asyncio.run(stale.prepare_execution(tasks))

    assert (tmp_path / "runs" / "run_001" / "state.json").read_bytes() == state_before
    assert {
        path: path.read_bytes() if path.exists() else None
        for path in diagnostic_paths
    } == before


def test_resume_rejects_existing_unauthenticated_cache_checkpoint(
    tmp_path: Path,
) -> None:
    tasks = {"alpha": Task("alpha", "alpha", [], retries=0, cache=True)}
    controller, lock_token = _controller_fixture(tmp_path, tasks)
    asyncio.run(controller.prepare_execution(tasks))
    state = controller.snapshot["canonical_state"]
    timestamp = "2026-07-29T00:00:06.000000Z"
    StateStore(tmp_path).checkpoint_context(
        run_id="run_001",
        expected_lock_token=lock_token,
        expected_status="RUNNING",
        expected_state_version=state["state_version"],
        operation_id="run:run_001:task:alpha:attempt:0:cache_hit",
        mutation_timestamp=timestamp,
        payload={
            "checkpoint_kind": "task_cache_hit",
            "task_id": "alpha",
            "attempt_number": 0,
            "expected_task_status": "pending",
            "next_task_status": "success",
            "retry_disposition": "none",
            "next_attempt_number": None,
            "controlled_context_delta": {
                "task_output": {"result": {"value": "legacy-cache"}},
                "cache_provenance": {"cache_key_sha256": "0" * 64},
            },
            "error_summary": None,
            "created_at": timestamp,
        },
    )
    resumed = InspectionWorkflowController(
        tmp_path,
        run_id="run_001",
        expected_lock_token=lock_token,
        plan_fingerprint=task_plan_fingerprint(tasks),
        resume=True,
        clock=_Clock(),
    )

    with pytest.raises(
        InspectionWorkflowControllerError, match="legacy cache"
    ):
        asyncio.run(resumed.prepare_execution(tasks))


def test_resume_repairs_retry_pending_and_uses_next_canonical_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = {"alpha": Task("alpha", "alpha", [], retries=1, cache=False)}
    controller, lock_token = _controller_fixture(tmp_path, tasks)
    asyncio.run(controller.prepare_execution(tasks))
    asyncio.run(
        controller.submit_checkpoint_event(
            {"checkpoint_kind": "task_started", "task_id": "alpha"}
        )
    )
    monkeypatch.setattr(
        controller,
        "_schedule_retry_locked",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("crash after retryable failure")
        ),
    )
    with pytest.raises(RuntimeError, match="crash"):
        asyncio.run(
            controller.submit_checkpoint_event(
                {
                    "checkpoint_kind": "task_failed",
                    "task_id": "alpha",
                    "retryable": True,
                    "failure_provenance": {"error_type": "RuntimeError"},
                    "error_summary": "RuntimeError: managed task execution failed",
                }
            )
        )
    assert (
        StateStore(tmp_path).load(run_id="run_001")["canonical_state"][
            "task_status"
        ]["alpha"]
        == "retry_pending"
    )

    resumed = InspectionWorkflowController(
        tmp_path,
        run_id="run_001",
        expected_lock_token=lock_token,
        plan_fingerprint=task_plan_fingerprint(tasks),
        resume=True,
        clock=_Clock(),
    )
    asyncio.run(resumed.prepare_execution(tasks))
    assert (
        resumed.snapshot["canonical_state"]["task_status"]["alpha"]
        == "retry_scheduled"
    )
    asyncio.run(
        resumed.submit_checkpoint_event(
            {"checkpoint_kind": "task_started", "task_id": "alpha"}
        )
    )
    assert resumed.snapshot["canonical_state"]["task_attempts"]["alpha"] == 2


def test_aborted_task_start_recovers_after_two_deterministic_successors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = {"alpha": Task("alpha", "alpha", [], retries=0, cache=False)}
    controller, lock_token = _controller_fixture(tmp_path, tasks)
    asyncio.run(controller.prepare_execution(tasks))
    store = StateStore(tmp_path)
    current = controller

    for _ in range(2):
        original_replace = current._store._atomic_replace

        def fail_task_start_state(path: Path, data: bytes, *, label: str) -> None:
            if label == "canonical state":
                raise StateStoreError(
                    "injected task_started State replace failure"
                )
            original_replace(path, data, label=label)

        monkeypatch.setattr(
            current._store, "_atomic_replace", fail_task_start_state
        )
        with pytest.raises(
            InspectionWorkflowControllerError, match="task_started"
        ):
            asyncio.run(
                current.submit_checkpoint_event(
                    {"checkpoint_kind": "task_started", "task_id": "alpha"}
                )
            )
        monkeypatch.setattr(current._store, "_atomic_replace", original_replace)

        recovered = store.recover_state_journal(
            run_id="run_001", expected_lock_token=lock_token
        )
        assert recovered["canonical_state"]["task_status"]["alpha"] == "pending"
        assert recovered["canonical_state"]["task_attempts"]["alpha"] == 0
        current = InspectionWorkflowController(
            tmp_path,
            run_id="run_001",
            expected_lock_token=lock_token,
            plan_fingerprint=task_plan_fingerprint(tasks),
            resume=True,
            clock=_Clock(),
        )
        asyncio.run(current.prepare_execution(tasks))

    rows = [
        json.loads(line)
        for line in (
            tmp_path / "runs" / "run_001" / "state_journal.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    aborted_starts = [
        row
        for row in rows
        if row["phase"] == "aborted" and ":started" in row["operation_id"]
    ]
    assert len(aborted_starts) == 2
    base = "run:run_001:task:alpha:attempt:1:started"
    assert aborted_starts[0]["operation_id"] == base
    assert aborted_starts[1]["operation_id"] == (
        f"{base}:after_aborted:{aborted_starts[0]['record_checksum']}"
    )

    timestamp = "2026-07-29T00:00:05.000000Z"
    with pytest.raises(
        StateConflictError, match="canonical aborted-start successor"
    ):
        store.checkpoint_context(
            run_id="run_001",
            expected_lock_token=lock_token,
            expected_status="RUNNING",
            expected_state_version=recovered["state_version"],
            operation_id=aborted_starts[1]["operation_id"],
            mutation_timestamp=timestamp,
            payload={
                "checkpoint_kind": "task_started",
                "task_id": "alpha",
                "attempt_number": 1,
                "expected_task_status": "pending",
                "next_task_status": "running",
                "retry_disposition": "none",
                "next_attempt_number": None,
                "controlled_context_delta": {},
                "error_summary": None,
                "created_at": timestamp,
            },
        )

    resumed = InspectionWorkflowController(
        tmp_path,
        run_id="run_001",
        expected_lock_token=lock_token,
        plan_fingerprint=task_plan_fingerprint(tasks),
        resume=True,
        clock=_Clock(),
    )
    calls: list[str] = []

    class _Agent:
        name = "alpha"

        def run(self, _context):
            calls.append("alpha")
            return {"value": "recovered"}

    from orchestrator.executor import DAGExecutor
    from orchestrator.registry import AgentRegistry

    registry = AgentRegistry()
    registry.register(_Agent())
    result = DAGExecutor(
        registry,
        tmp_path,
        resume=True,
        run_id="run_001",
        checkpoint_event_sink=resumed,
    ).run(tasks, {"inputs": {}, "outputs": {}, "shared": {}, "task_status": {}})

    assert calls == ["alpha"]
    assert result["outputs"]["alpha"] == {"value": "recovered"}
    assert resumed.snapshot["canonical_state"]["task_attempts"]["alpha"] == 1
    rows = [
        json.loads(line)
        for line in (
            tmp_path / "runs" / "run_001" / "state_journal.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    starts = [
        row
        for row in rows
        if (
            row["mutation_kind"] == "context_checkpoint"
            and (row["payload"] or {}).get("checkpoint_kind") == "task_started"
        )
        or (
            row["phase"] in {"committed", "aborted"}
            and ":started" in row["operation_id"]
        )
    ]
    start_ids = list(dict.fromkeys(row["operation_id"] for row in starts))
    assert start_ids[0] == "run:run_001:task:alpha:attempt:1:started"
    assert start_ids[1] == aborted_starts[1]["operation_id"]
    assert start_ids[2] == (
        f"{base}:after_aborted:{aborted_starts[1]['record_checksum']}"
    )
    assert all(":attempt:1:started" in operation_id for operation_id in start_ids)


def test_aborted_task_start_rejects_well_formed_forged_checksum_successor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = {"alpha": Task("alpha", "alpha", [], retries=0, cache=False)}
    controller, lock_token = _controller_fixture(tmp_path, tasks)
    asyncio.run(controller.prepare_execution(tasks))
    original_replace = controller._store._atomic_replace

    def fail_task_start_state(path: Path, data: bytes, *, label: str) -> None:
        if label == "canonical state":
            raise StateStoreError("injected task_started State replace failure")
        original_replace(path, data, label=label)

    monkeypatch.setattr(
        controller._store, "_atomic_replace", fail_task_start_state
    )
    with pytest.raises(InspectionWorkflowControllerError, match="task_started"):
        asyncio.run(
            controller.submit_checkpoint_event(
                {"checkpoint_kind": "task_started", "task_id": "alpha"}
            )
        )
    monkeypatch.setattr(controller._store, "_atomic_replace", original_replace)

    store = StateStore(tmp_path)
    recovered = store.recover_state_journal(
        run_id="run_001", expected_lock_token=lock_token
    )
    journal_path = tmp_path / "runs" / "run_001" / "state_journal.jsonl"
    state_path = tmp_path / "runs" / "run_001" / "state.json"
    rows = [
        json.loads(line)
        for line in journal_path.read_text(encoding="utf-8").splitlines()
    ]
    latest_checksum = next(
        row["record_checksum"]
        for row in reversed(rows)
        if row["phase"] == "aborted" and ":started" in row["operation_id"]
    )
    forged_checksum = (
        ("0" if latest_checksum[0] != "0" else "1") + latest_checksum[1:]
    )
    assert forged_checksum != latest_checksum
    assert len(forged_checksum) == 64
    assert set(forged_checksum).issubset(set("0123456789abcdef"))
    journal_before = journal_path.read_bytes()
    state_before = state_path.read_bytes()
    timestamp = "2026-07-29T00:00:05.000000Z"

    with pytest.raises(
        StateConflictError, match="canonical aborted-start successor"
    ):
        store.checkpoint_context(
            run_id="run_001",
            expected_lock_token=lock_token,
            expected_status="RUNNING",
            expected_state_version=recovered["state_version"],
            operation_id=(
                "run:run_001:task:alpha:attempt:1:started:"
                f"after_aborted:{forged_checksum}"
            ),
            mutation_timestamp=timestamp,
            payload={
                "checkpoint_kind": "task_started",
                "task_id": "alpha",
                "attempt_number": 1,
                "expected_task_status": "pending",
                "next_task_status": "running",
                "retry_disposition": "none",
                "next_attempt_number": None,
                "controlled_context_delta": {},
                "error_summary": None,
                "created_at": timestamp,
            },
        )

    assert journal_path.read_bytes() == journal_before
    assert state_path.read_bytes() == state_before


def test_aborted_task_start_successor_lookup_is_one_pass_near_journal_limit() -> None:
    class _CountingRecords(list[dict[str, object]]):
        iterations = 0

        def __iter__(self):
            self.iterations += 1
            return super().__iter__()

    base = "run:run_001:task:alpha:attempt:1:started"
    records = _CountingRecords(
        {
            "mutation_kind": "status_transition",
            "phase": "committed",
            "operation_id": f"unrelated:{index}",
        }
        for index in range(MAX_STATE_JOURNAL_RECORDS - 200)
    )
    latest_checksum = ""
    for index in range(100):
        operation_id = (
            base
            if index == 0
            else f"{base}:after_aborted:{index:064x}"
        )
        records.append(
            {
                "mutation_kind": "context_checkpoint",
                "phase": "pending",
                "operation_id": operation_id,
                "payload": {
                    "checkpoint_kind": "task_started",
                    "task_id": "alpha",
                    "attempt_number": 1,
                },
            }
        )
        latest_checksum = f"{index + 1:064x}"
        records.append(
            {
                "mutation_kind": "context_checkpoint",
                "phase": "aborted",
                "operation_id": operation_id,
                "record_index": len(records) + 1,
                "record_checksum": latest_checksum,
            }
        )

    result = StateStore._task_start_operation_id_from_records(
        run_id="run_001",
        task_id="alpha",
        attempt_number=1,
        records=records,
    )

    assert len(records) == MAX_STATE_JOURNAL_RECORDS
    assert records.iterations == 1
    assert result == f"{base}:after_aborted:{latest_checksum}"


def test_resume_rejects_retry_provenance_from_a_different_plan_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = {"alpha": Task("alpha", "alpha", [], retries=1, cache=False)}
    controller, lock_token = _controller_fixture(tmp_path, tasks)
    asyncio.run(controller.prepare_execution(tasks))
    asyncio.run(
        controller.submit_checkpoint_event(
            {"checkpoint_kind": "task_started", "task_id": "alpha"}
        )
    )
    original_policy_hash = controller_module.retry_policy_sha256
    monkeypatch.setattr(
        controller_module, "retry_policy_sha256", lambda _task: "0" * 64
    )
    monkeypatch.setattr(
        controller,
        "_schedule_retry_locked",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("crash after mismatched retry failure")
        ),
    )
    with pytest.raises(RuntimeError, match="mismatched"):
        asyncio.run(
            controller.submit_checkpoint_event(
                {
                    "checkpoint_kind": "task_failed",
                    "task_id": "alpha",
                    "retryable": True,
                    "failure_provenance": {"error_type": "RuntimeError"},
                    "error_summary": "RuntimeError: managed task execution failed",
                }
            )
        )
    monkeypatch.setattr(
        controller_module, "retry_policy_sha256", original_policy_hash
    )

    resumed = InspectionWorkflowController(
        tmp_path,
        run_id="run_001",
        expected_lock_token=lock_token,
        plan_fingerprint=task_plan_fingerprint(tasks),
        resume=True,
        clock=_Clock(),
    )
    with pytest.raises(
        InspectionWorkflowControllerError, match="canonical retry policy"
    ):
        asyncio.run(resumed.prepare_execution(tasks))
    assert (
        StateStore(tmp_path).load(run_id="run_001")["canonical_state"][
            "task_status"
        ]["alpha"]
        == "retry_pending"
    )
