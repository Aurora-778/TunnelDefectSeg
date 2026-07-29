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
from orchestrator.state.store import StateConflictError, StateStore


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


def test_controller_rejects_uncontrolled_skip_and_cache_provenance(
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
    with pytest.raises(InspectionWorkflowControllerError, match="cache provenance"):
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
        (
            "early_cache",
            {
                "checkpoint_kind": "task_cache_hit",
                "task_id": "beta",
                "task_output": {"value": "premature"},
                "cache_provenance": {"cache_key_sha256": "0" * 64},
            },
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

    with pytest.raises(InspectionWorkflowControllerError, match="transition"):
        asyncio.run(stale.prepare_execution(tasks))
    assert controller.snapshot["state_version"] == 0


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
