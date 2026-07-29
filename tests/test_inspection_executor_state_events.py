from __future__ import annotations

import json
from pathlib import Path
import uuid

import pytest

from orchestrator.base_agent import BaseAgent
from orchestrator.dag.builder import Task
from orchestrator.executor import DAGExecutor
from orchestrator.inspection_workflow.controller import (
    InspectionWorkflowController,
    InspectionWorkflowControllerError,
)
from orchestrator.inspection_workflow import locking as locking_module
from orchestrator.inspection_workflow.locking import (
    acquire_active_run_lock,
    mark_active_run_running,
    reserve_active_run_id,
)
from orchestrator.inspection_workflow.planning import (
    build_required_task_plan,
    task_plan_fingerprint,
)
from orchestrator.registry import AgentRegistry
from orchestrator.state.store import StateConflictError, StateStore


T0 = "2026-07-29T00:00:00.000000Z"


class _SuccessAgent(BaseAgent):
    def __init__(self, name: str, root: Path, calls: list[str]) -> None:
        self.name = name
        self.root = root
        self.calls = calls

    def run(self, context):
        state = json.loads(
            (self.root / "runs" / "run_001" / "state.json").read_text(
                encoding="utf-8"
            )
        )
        assert state["task_status"][self.name] == "running"
        self.calls.append(self.name)
        return {"value": self.name}


class _FlakyAgent(BaseAgent):
    name = "flaky"

    def __init__(self) -> None:
        self.calls = 0

    def run(self, context):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError(r"do not leak C:\Users\secret\input.csv")
        return {"value": "recovered"}


class _AlwaysFailAgent(BaseAgent):
    name = "fail"

    def __init__(self) -> None:
        self.calls = 0

    def run(self, context):
        self.calls += 1
        raise ValueError("/home/private/source.csv")


class _IsolatedOutputAgent(BaseAgent):
    name = "beta"

    def __init__(self) -> None:
        self.seen_outputs = None

    def run(self, context):
        self.seen_outputs = context["outputs"]
        return {"value": "isolated"}


def _managed_fixture(
    root: Path,
    tasks: dict[str, Task],
) -> tuple[InspectionWorkflowController, str, bytes]:
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
    metadata = b'{"sentinel":"legacy metadata must remain unchanged"}\n'
    (run_dir / "metadata.json").write_bytes(metadata)
    fingerprint = task_plan_fingerprint(tasks)
    StateStore(root).initialize_run(
        run_id="run_001",
        allocation_token=allocation_token,
        plan_fingerprint=fingerprint,
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
            plan_fingerprint=fingerprint,
        ),
        lock_token,
        metadata,
    )


def _checkpoint_kinds(root: Path) -> list[str]:
    rows = [
        json.loads(line)
        for line in (
            root / "runs" / "run_001" / "state_journal.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    return [
        row["payload"]["checkpoint_kind"]
        for row in rows
        if row["phase"] == "pending"
        and row["mutation_kind"] == "context_checkpoint"
    ]


def test_managed_executor_serializes_concurrent_results_without_legacy_state(
    tmp_path: Path,
) -> None:
    tasks = {
        "alpha": Task("alpha", "alpha", [], retries=0, cache=False),
        "beta": Task("beta", "beta", [], retries=0, cache=False),
    }
    controller, _, metadata = _managed_fixture(tmp_path, tasks)
    calls: list[str] = []
    registry = AgentRegistry()
    registry.register(_SuccessAgent("alpha", tmp_path, calls))
    registry.register(_SuccessAgent("beta", tmp_path, calls))
    executor = DAGExecutor(
        registry,
        tmp_path,
        run_id="run_001",
        checkpoint_event_sink=controller,
    )

    result = executor.run(
        tasks,
        {"inputs": {}, "outputs": {}, "shared": {}, "task_status": {}},
    )

    assert sorted(calls) == ["alpha", "beta"]
    assert result["task_status"] == {"alpha": "success", "beta": "success"}
    assert result["outputs"] == {
        "alpha": {"value": "alpha"},
        "beta": {"value": "beta"},
    }
    assert not (tmp_path / "orchestrator" / "state" / "run_state.json").exists()
    assert not list((tmp_path / "runs" / "run_001").glob("context_v*.json"))
    assert (tmp_path / "runs" / "run_001" / "metadata.json").read_bytes() == metadata
    assert not (tmp_path / "logs" / "dag_execution.json").exists()
    assert not (tmp_path / "logs" / "execution_trace.json").exists()
    assert not (tmp_path / "logs" / "dag_cache.json").exists()
    assert not (tmp_path / "runs" / "run_001" / "dag.json").exists()
    assert not (tmp_path / "runs" / "run_001" / "dag_execution.json").exists()
    assert not (tmp_path / "runs" / "run_001" / "execution_trace.json").exists()
    assert not (tmp_path / "runs" / "run_001" / "timeline.json").exists()
    kinds = _checkpoint_kinds(tmp_path)
    assert kinds[0] == "run_initialized"
    assert kinds.count("task_started") == 2
    assert kinds.count("task_succeeded") == 2


def test_managed_executor_commits_retry_failure_and_schedule_adjacent(
    tmp_path: Path,
) -> None:
    tasks = {"flaky": Task("flaky", "flaky", [], retries=1, cache=False)}
    controller, _, _ = _managed_fixture(tmp_path, tasks)
    agent = _FlakyAgent()
    registry = AgentRegistry()
    registry.register(agent)

    result = DAGExecutor(
        registry,
        tmp_path,
        run_id="run_001",
        checkpoint_event_sink=controller,
    ).run(tasks, {"inputs": {}, "outputs": {}, "shared": {}, "task_status": {}})

    assert agent.calls == 2
    assert result["outputs"]["flaky"] == {"value": "recovered"}
    kinds = _checkpoint_kinds(tmp_path)
    failed_index = kinds.index("task_failed")
    assert kinds[failed_index : failed_index + 3] == [
        "task_failed",
        "task_retry_scheduled",
        "task_started",
    ]
    assert (
        controller.snapshot["canonical_state"]["task_attempts"]["flaky"] == 2
    )
    journal = (
        tmp_path / "runs" / "run_001" / "state_journal.jsonl"
    ).read_text(encoding="utf-8")
    assert "secret" not in journal
    assert "C:\\\\" not in journal


def test_managed_executor_terminal_failure_then_dependency_skip(
    tmp_path: Path,
) -> None:
    tasks = {
        "fail": Task("fail", "fail", [], retries=0, cache=False),
        "tail": Task("tail", "tail", ["fail"], retries=0, cache=False),
    }
    controller, _, _ = _managed_fixture(tmp_path, tasks)
    agent = _AlwaysFailAgent()
    registry = AgentRegistry()
    registry.register(agent)

    result = DAGExecutor(
        registry,
        tmp_path,
        run_id="run_001",
        checkpoint_event_sink=controller,
    ).run(tasks, {"inputs": {}, "outputs": {}, "shared": {}, "task_status": {}})

    assert agent.calls == 1
    assert result["task_status"] == {"fail": "failed", "tail": "skipped"}
    assert _checkpoint_kinds(tmp_path)[-2:] == ["task_failed", "task_skipped"]
    journal = (
        tmp_path / "runs" / "run_001" / "state_journal.jsonl"
    ).read_text(encoding="utf-8")
    assert "/home/private" not in journal


def test_managed_executor_ignores_untrusted_global_cache(
    tmp_path: Path,
) -> None:
    tasks = {"cached": Task("cached", "cached", [], retries=0, cache=True)}
    controller, _, _ = _managed_fixture(tmp_path, tasks)
    calls: list[str] = []
    registry = AgentRegistry()
    registry.register(_SuccessAgent("cached", tmp_path, calls))
    context = {"inputs": {}, "outputs": {}, "shared": {}, "task_status": {}}
    executor = DAGExecutor(
        registry,
        tmp_path,
        run_id="run_001",
        checkpoint_event_sink=controller,
    )
    key = executor._cache_key(tasks["cached"], context)
    executor._write_json(
        executor.cache_path,
        {"cached": {"key": key, "result": {"value": "from-cache"}}},
    )
    cache_bytes = executor.cache_path.read_bytes()

    result = executor.run(tasks, context)

    assert calls == ["cached"]
    assert result["outputs"]["cached"] == {"value": "cached"}
    assert _checkpoint_kinds(tmp_path)[-2:] == ["task_started", "task_succeeded"]
    assert controller.snapshot["canonical_state"]["task_attempts"]["cached"] == 1
    assert executor.cache_path.read_bytes() == cache_bytes


def test_managed_executor_resume_continues_from_canonical_attempt(
    tmp_path: Path,
) -> None:
    tasks = {"alpha": Task("alpha", "alpha", [], retries=1, cache=True)}
    controller, lock_token, _ = _managed_fixture(tmp_path, tasks)
    import asyncio

    asyncio.run(controller.prepare_execution(tasks))
    asyncio.run(
        controller.submit_checkpoint_event(
            {"checkpoint_kind": "task_started", "task_id": "alpha"}
        )
    )
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
    resumed = InspectionWorkflowController(
        tmp_path,
        run_id="run_001",
        expected_lock_token=lock_token,
        plan_fingerprint=task_plan_fingerprint(tasks),
        resume=True,
    )
    calls: list[str] = []
    registry = AgentRegistry()
    registry.register(_SuccessAgent("alpha", tmp_path, calls))
    (tmp_path / "logs").mkdir(exist_ok=True)
    (tmp_path / "logs" / "dag_cache.json").write_text(
        '{"alpha":{"key":"forged","result":{"value":"stale"}}}',
        encoding="utf-8",
    )

    result = DAGExecutor(
        registry,
        tmp_path,
        resume=True,
        run_id="run_001",
        checkpoint_event_sink=resumed,
    ).run(tasks, {"inputs": {}, "outputs": {}, "shared": {}, "task_status": {}})

    assert calls == ["alpha"]
    assert result["outputs"]["alpha"] == {"value": "alpha"}
    assert resumed.snapshot["canonical_state"]["task_attempts"]["alpha"] == 2
    kinds = _checkpoint_kinds(tmp_path)
    assert kinds[-1] == "task_succeeded"
    assert kinds[-2] == "task_started"


def test_managed_executor_requires_matching_explicit_run_before_selection(
    tmp_path: Path,
) -> None:
    tasks = {"alpha": Task("alpha", "alpha", [], retries=0, cache=False)}
    controller, _, metadata = _managed_fixture(tmp_path, tasks)
    runs_before = sorted(path.name for path in (tmp_path / "runs").iterdir())

    with pytest.raises(ValueError, match="explicit run_id"):
        DAGExecutor(
            AgentRegistry(),
            tmp_path,
            checkpoint_event_sink=controller,
        )
    with pytest.raises(ValueError, match="does not match"):
        DAGExecutor(
            AgentRegistry(),
            tmp_path,
            run_id="run_999",
            checkpoint_event_sink=controller,
        )

    assert sorted(path.name for path in (tmp_path / "runs").iterdir()) == runs_before
    assert (tmp_path / "runs" / "run_001" / "metadata.json").read_bytes() == metadata
    assert not (tmp_path / "runs" / "run_002").exists()


def test_managed_resume_preserves_terminal_tasks_and_runs_pending_branch(
    tmp_path: Path,
) -> None:
    tasks = {
        "done": Task("done", "done", [], retries=0, cache=False),
        "fail": Task("fail", "fail", [], retries=0, cache=False),
        "tail": Task("tail", "tail", ["fail"], retries=0, cache=False),
        "independent": Task(
            "independent", "independent", [], retries=0, cache=False
        ),
    }
    controller, lock_token, _ = _managed_fixture(tmp_path, tasks)
    import asyncio

    asyncio.run(controller.prepare_execution(tasks))
    asyncio.run(
        controller.submit_checkpoint_event(
            {"checkpoint_kind": "task_started", "task_id": "done"}
        )
    )
    asyncio.run(
        controller.submit_checkpoint_event(
            {
                "checkpoint_kind": "task_succeeded",
                "task_id": "done",
                "task_output": {"value": "already-committed"},
            }
        )
    )
    asyncio.run(
        controller.submit_checkpoint_event(
            {"checkpoint_kind": "task_started", "task_id": "fail"}
        )
    )
    asyncio.run(
        controller.submit_checkpoint_event(
            {
                "checkpoint_kind": "task_failed",
                "task_id": "fail",
                "retryable": False,
                "failure_provenance": {"error_type": "RuntimeError"},
                "error_summary": "RuntimeError: managed task execution failed",
            }
        )
    )
    asyncio.run(
        controller.submit_checkpoint_event(
            {
                "checkpoint_kind": "task_skipped",
                "task_id": "tail",
                "skip_reason": "dependency_failed",
            }
        )
    )
    resumed = InspectionWorkflowController(
        tmp_path,
        run_id="run_001",
        expected_lock_token=lock_token,
        plan_fingerprint=task_plan_fingerprint(tasks),
        resume=True,
    )
    calls: list[str] = []
    registry = AgentRegistry()
    registry.register(_SuccessAgent("independent", tmp_path, calls))

    result = DAGExecutor(
        registry,
        tmp_path,
        resume=True,
        run_id="run_001",
        checkpoint_event_sink=resumed,
    ).run(tasks, {"inputs": {}, "outputs": {}, "shared": {}, "task_status": {}})

    assert calls == ["independent"]
    assert result["task_status"] == {
        "done": "success",
        "fail": "failed",
        "independent": "success",
        "tail": "skipped",
    }


def test_managed_executor_uses_only_committed_declared_dependency_outputs(
    tmp_path: Path,
) -> None:
    tasks = {
        "alpha": Task("alpha", "alpha", [], retries=0, cache=False),
        "beta": Task("beta", "beta", [], retries=0, cache=False),
    }
    controller, _, _ = _managed_fixture(tmp_path, tasks)
    import asyncio

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
                "task_output": {"value": "committed-alpha"},
            }
        )
    )
    beta = _IsolatedOutputAgent()
    registry = AgentRegistry()
    registry.register(beta)

    result = DAGExecutor(
        registry,
        tmp_path,
        run_id="run_001",
        checkpoint_event_sink=controller,
    ).run(
        tasks,
        {
            "inputs": {},
            "outputs": {
                "forged": {"value": "caller-controlled"},
                "beta": {"value": "premature"},
            },
            "shared": {},
            "task_status": {},
        },
    )

    assert beta.seen_outputs == {}
    assert set(result["outputs"]) == {"alpha", "beta"}
    assert result["outputs"]["alpha"] == {"value": "committed-alpha"}
    assert result["outputs"]["beta"] == {"value": "isolated"}


def test_managed_executor_does_not_run_agent_after_state_sink_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = {"alpha": Task("alpha", "alpha", [], retries=0, cache=False)}
    controller, _, _ = _managed_fixture(tmp_path, tasks)
    calls: list[str] = []
    registry = AgentRegistry()
    registry.register(_SuccessAgent("alpha", tmp_path, calls))
    executor = DAGExecutor(
        registry,
        tmp_path,
        run_id="run_001",
        checkpoint_event_sink=controller,
    )
    # Prepare first so the injected failure targets task_started, not run initialization.
    import asyncio

    asyncio.run(controller.prepare_execution(tasks))
    monkeypatch.setattr(
        controller._store,
        "checkpoint_context",
        lambda **_kwargs: (_ for _ in ()).throw(
            StateConflictError("injected stale cursor")
        ),
    )

    with pytest.raises(InspectionWorkflowControllerError, match="checkpoint"):
        executor.run(
            tasks,
            {"inputs": {}, "outputs": {}, "shared": {}, "task_status": {}},
        )
    assert calls == []


def test_managed_executor_does_not_rewrite_diagnostics_after_lock_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = {"alpha": Task("alpha", "alpha", [], retries=0, cache=False)}
    controller, _, metadata = _managed_fixture(tmp_path, tasks)
    calls: list[str] = []
    registry = AgentRegistry()
    registry.register(_SuccessAgent("alpha", tmp_path, calls))
    executor = DAGExecutor(
        registry,
        tmp_path,
        run_id="run_001",
        checkpoint_event_sink=controller,
    )
    diagnostic_paths = [
        executor.log_path,
        executor.trace_path,
        executor.cache_path,
        executor.run_log_path,
        executor.run_trace_path,
        executor.run_timeline_path,
        executor.run_dag_path,
    ]
    before: dict[Path, bytes] = {}
    for index, path in enumerate(diagnostic_paths):
        path.parent.mkdir(parents=True, exist_ok=True)
        before[path] = f"diagnostic-sentinel-{index}\n".encode("ascii")
        path.write_bytes(before[path])

    original_prepare = controller.prepare_execution

    async def prepare_then_rotate_lock(prepared_tasks: dict[str, Task]):
        snapshot = await original_prepare(prepared_tasks)
        lock_path = tmp_path / "runs" / ".active_run.lock"
        changed = locking_module.read_active_run_lock(tmp_path)
        changed["lock_token"] = str(uuid.uuid4())
        lock_path.write_bytes(locking_module._canonical_json_bytes(changed))
        return snapshot

    monkeypatch.setattr(controller, "prepare_execution", prepare_then_rotate_lock)

    with pytest.raises(
        InspectionWorkflowControllerError, match="recovery identity"
    ):
        executor.run(
            tasks,
            {"inputs": {}, "outputs": {}, "shared": {}, "task_status": {}},
        )

    assert calls == []
    assert {path: path.read_bytes() for path in diagnostic_paths} == before
    assert (tmp_path / "runs" / "run_001" / "metadata.json").read_bytes() == metadata


def test_legacy_executor_without_sink_keeps_existing_checkpoint_path(
    tmp_path: Path,
) -> None:
    tasks = {"alpha": Task("alpha", "alpha", [], retries=0, cache=False)}
    calls: list[str] = []
    registry = AgentRegistry()
    registry.register(_SuccessAgent("alpha", tmp_path, calls))

    # The legacy agent's state assertion expects a managed Run, so use a simple local agent.
    class _LegacyAgent(BaseAgent):
        name = "alpha"

        def run(self, context):
            return {"value": "legacy"}

    registry = AgentRegistry()
    registry.register(_LegacyAgent())
    result = DAGExecutor(registry, tmp_path).run(
        tasks,
        {"inputs": {}, "outputs": {}, "shared": {}, "task_status": {}},
    )

    assert result["outputs"]["alpha"] == {"value": "legacy"}
    assert (tmp_path / "orchestrator" / "state" / "run_state.json").exists()
    assert list((tmp_path / "runs" / "run_001").glob("context_v*.json"))
    assert (tmp_path / "logs" / "dag_execution.json").is_file()
    assert (tmp_path / "logs" / "execution_trace.json").is_file()
    assert (tmp_path / "runs" / "run_001" / "dag.json").is_file()
    assert (tmp_path / "runs" / "run_001" / "timeline.json").is_file()
