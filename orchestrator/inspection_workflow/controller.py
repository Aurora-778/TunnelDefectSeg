"""Opt-in Phase A3.3.1 owner of one Run-local StateStore version cursor."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path
import re
from typing import Any

from orchestrator.dag.builder import Task
from orchestrator.inspection_workflow.locking import (
    ActiveRunLockError,
    canonical_utc_now,
    validate_active_run_lock,
)
from orchestrator.inspection_workflow.models import freeze_json
from orchestrator.inspection_workflow.planning import (
    build_required_task_plan,
    retry_policy_sha256,
    task_plan_fingerprint,
)
from orchestrator.state.store import StateStore, StateStoreError


class InspectionWorkflowControllerError(RuntimeError):
    """Raised when the opt-in managed-state workflow must stop."""


_EVENT_FIELDS = {
    "task_skipped": {"checkpoint_kind", "task_id", "skip_reason"},
    "task_started": {"checkpoint_kind", "task_id"},
    "task_succeeded": {"checkpoint_kind", "task_id", "task_output"},
    "task_failed": {
        "checkpoint_kind",
        "task_id",
        "retryable",
        "failure_provenance",
        "error_summary",
    },
}
_ERROR_TYPE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class InspectionWorkflowController:
    """The sole StateStore mutation owner for one opt-in Executor run."""

    def __init__(
        self,
        project_root: Path,
        *,
        run_id: str,
        expected_lock_token: str,
        plan_fingerprint: str,
        resume: bool = False,
        clock: Callable[[], str] = canonical_utc_now,
    ) -> None:
        self.run_id = run_id
        self._expected_lock_token = expected_lock_token
        self._plan_fingerprint = plan_fingerprint
        self._clock = clock
        self._store = StateStore(project_root)
        try:
            self._snapshot = (
                self._store.recover(
                    run_id=run_id,
                    expected_lock_token=expected_lock_token,
                )
                if resume
                else self._store.load(run_id=run_id)
            )
        except StateStoreError as exc:
            raise InspectionWorkflowControllerError(
                "unable to attach the managed State Coordinator"
            ) from exc
        state = self._snapshot["canonical_state"]
        if (
            self._snapshot["run_id"] != run_id
            or state["plan_fingerprint"] != plan_fingerprint
        ):
            raise InspectionWorkflowControllerError(
                "managed State does not match the requested Run plan"
            )
        self._mutation_queue_lock = asyncio.Lock()
        self._halted = False
        self._tasks: dict[str, Task] | None = None

    @property
    def snapshot(self) -> Mapping[str, Any]:
        """Return the immutable StateStore snapshot owned by this Controller."""

        return self._snapshot

    async def prepare_execution(self, tasks: Mapping[str, Task]) -> Mapping[str, Any]:
        """Initialize or resume the canonical task map before any worker starts."""

        task_plan = build_required_task_plan(tasks)
        fingerprint = task_plan_fingerprint(tasks)
        if fingerprint != self._plan_fingerprint:
            raise InspectionWorkflowControllerError(
                "DAG execution policy does not match the Run plan fingerprint"
            )

        async with self._mutation_queue_lock:
            self._ensure_accepting()
            try:
                self._tasks = dict(tasks)
                state = self._state()
                try:
                    validate_active_run_lock(
                        self._store.project_root,
                        run_id=self.run_id,
                        allocation_token=state["allocation_token"],
                        expected_lock_token=self._expected_lock_token,
                        allowed_phases={"running"},
                    )
                except ActiveRunLockError as exc:
                    raise InspectionWorkflowControllerError(
                        "managed State Coordinator Active Run Lock is invalid"
                    ) from exc
                if _thaw_json(state["task_plan"]) != task_plan:
                    raise InspectionWorkflowControllerError(
                        "DAG task plan does not match canonical State"
                    )
                if state["status"] == "CREATED":
                    self._transition_locked("PLANNED")
                    state = self._state()
                if state["status"] == "PLANNED":
                    if not state["task_status"]:
                        self._checkpoint_locked(
                            {
                                "checkpoint_kind": "run_initialized",
                                "task_id": None,
                                "attempt_number": None,
                                "expected_task_status": None,
                                "next_task_status": None,
                                "retry_disposition": "none",
                                "next_attempt_number": None,
                                "controlled_context_delta": {
                                    "task_plan": task_plan,
                                    "plan_fingerprint": fingerprint,
                                },
                                "error_summary": None,
                            }
                        )
                    self._transition_locked("RUNNING")
                    state = self._state()
                if state["status"] != "RUNNING":
                    raise InspectionWorkflowControllerError(
                        "managed DAG execution requires canonical RUNNING State"
                    )
                checkpoint_events = state["context"].get(
                    "phase_a3_checkpoint_events", {}
                )
                if (
                    isinstance(checkpoint_events, Mapping)
                    and any(
                        isinstance(value, Mapping)
                        and value.get("checkpoint_kind") == "task_cache_hit"
                        for value in checkpoint_events.values()
                    )
                ):
                    raise InspectionWorkflowControllerError(
                        "managed State contains an unauthenticated legacy cache checkpoint"
                    )
                blocked_resume = sorted(
                    task_id
                    for task_id, status in state["task_status"].items()
                    if status == "running"
                )
                if blocked_resume:
                    raise InspectionWorkflowControllerError(
                        "managed resume cannot continue in-flight tasks: "
                        + ", ".join(blocked_resume)
                    )
                self._resume_retry_schedules_locked()
            except BaseException:
                self._halted = True
                raise
            return self._snapshot

    async def submit_checkpoint_event(
        self, event: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Serialize one Executor event through the current CAS version cursor."""

        if not isinstance(event, Mapping):
            raise InspectionWorkflowControllerError(
                "managed checkpoint event must be an object"
            )
        try:
            event = deepcopy(dict(event))
        except (TypeError, ValueError, RecursionError) as exc:
            raise InspectionWorkflowControllerError(
                "managed checkpoint event is not safely copyable"
            ) from exc
        kind = event.get("checkpoint_kind")
        if not isinstance(kind, str) or kind not in _EVENT_FIELDS:
            raise InspectionWorkflowControllerError(
                "managed checkpoint event kind is invalid"
            )
        if set(event) != _EVENT_FIELDS[kind]:
            raise InspectionWorkflowControllerError(
                f"managed checkpoint event fields are invalid for {kind}"
            )
        self._validate_executor_event(event)

        async with self._mutation_queue_lock:
            self._ensure_accepting()
            try:
                self._submit_event_locked(event)
            except BaseException:
                self._halted = True
                raise
            return self._snapshot

    def project_executor_context(
        self, context: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Project canonical task status and committed outputs into local execution context."""

        if not isinstance(context, Mapping):
            raise InspectionWorkflowControllerError(
                "managed Executor context must be an object"
            )
        try:
            projected = deepcopy(dict(context))
        except (TypeError, ValueError, RecursionError) as exc:
            raise InspectionWorkflowControllerError(
                "managed Executor context is not safely copyable"
            ) from exc
        state = self._state()
        projected["task_status"] = dict(state["task_status"])
        if not isinstance(projected.get("outputs", {}), Mapping):
            raise InspectionWorkflowControllerError(
                "managed Executor outputs must be an object"
            )
        outputs: dict[str, Any] = {}
        events = state["context"].get("phase_a3_checkpoint_events", {})
        if isinstance(events, Mapping):
            for row in events.values():
                if (
                    isinstance(row, Mapping)
                    and row.get("checkpoint_kind")
                    in {"task_cache_hit", "task_succeeded"}
                ):
                    delta = row.get("controlled_context_delta")
                    task_output = (
                        delta.get("task_output")
                        if isinstance(delta, Mapping)
                        else None
                    )
                    if (
                        isinstance(task_output, Mapping)
                        and set(task_output) == {"result"}
                        and isinstance(row.get("task_id"), str)
                    ):
                        outputs[row["task_id"]] = _thaw_json(task_output["result"])
        projected["outputs"] = outputs
        if not isinstance(projected.get("shared", {}), Mapping):
            raise InspectionWorkflowControllerError(
                "managed Executor shared context must be an object"
            )
        projected["shared"] = deepcopy(dict(projected.get("shared", {})))
        projected["shared"]["run_id"] = self.run_id
        return projected

    @staticmethod
    def _validate_executor_event(event: Mapping[str, Any]) -> None:
        kind = event["checkpoint_kind"]
        if kind == "task_skipped":
            if event["skip_reason"] != "dependency_failed":
                raise InspectionWorkflowControllerError(
                    "managed task skip reason is not a controlled code"
                )
            return
        if kind == "task_succeeded":
            if not isinstance(event["task_output"], Mapping):
                raise InspectionWorkflowControllerError(
                    "managed task output must be an object"
                )
            return
        if kind == "task_failed":
            provenance = event["failure_provenance"]
            if (
                type(event["retryable"]) is not bool
                or not isinstance(provenance, Mapping)
                or set(provenance) != {"error_type"}
                or not isinstance(provenance["error_type"], str)
                or not _ERROR_TYPE_RE.fullmatch(provenance["error_type"])
                or event["error_summary"]
                != f"{provenance['error_type']}: managed task execution failed"
            ):
                raise InspectionWorkflowControllerError(
                    "managed task failure provenance is invalid"
                )

    def _ensure_accepting(self) -> None:
        if self._halted:
            raise InspectionWorkflowControllerError(
                "managed State Coordinator is halted after a mutation failure"
            )

    def _state(self) -> Mapping[str, Any]:
        return self._snapshot["canonical_state"]

    def _timestamp(self) -> str:
        value = self._clock()
        if not isinstance(value, str):
            raise InspectionWorkflowControllerError(
                "managed State Coordinator clock returned an invalid timestamp"
            )
        return value

    def _transition_locked(self, next_status: str) -> None:
        state = self._state()
        timestamp = self._timestamp()
        operation_id = (
            f"run:{self.run_id}:transition:v{state['state_version']}:"
            f"{state['status']}:{next_status}:auto"
        )
        try:
            result = self._store.transition_status(
                run_id=self.run_id,
                expected_lock_token=self._expected_lock_token,
                expected_status=state["status"],
                expected_state_version=state["state_version"],
                operation_id=operation_id,
                mutation_timestamp=timestamp,
                payload={
                    "next_status": next_status,
                    "metadata": None,
                    "completion_evidence": None,
                    "transition_kind": "auto",
                    "decision_token": None,
                },
            )
        except StateStoreError as exc:
            raise InspectionWorkflowControllerError(
                f"managed State transition to {next_status} failed"
            ) from exc
        self._accept_result(result, operation_id)

    def _checkpoint_locked(self, payload: dict[str, Any]) -> None:
        state = self._state()
        timestamp = self._timestamp()
        payload["created_at"] = timestamp
        kind = payload["checkpoint_kind"]
        if kind == "run_initialized":
            operation_id = f"run:{self.run_id}:checkpoint:run_initialized"
        else:
            suffix = {
                "task_skipped": "skipped",
                "task_cache_hit": "cache_hit",
                "task_started": "started",
                "task_succeeded": "succeeded",
                "task_failed": "failed",
                "task_retry_scheduled": "retry_scheduled",
            }[kind]
            operation_id = (
                f"run:{self.run_id}:task:{payload['task_id']}:"
                f"attempt:{payload['attempt_number']}:{suffix}"
            )
            if kind == "task_started":
                try:
                    operation_id = self._store.next_task_start_operation_id(
                        run_id=self.run_id,
                        task_id=payload["task_id"],
                        attempt_number=payload["attempt_number"],
                        expected_lock_token=self._expected_lock_token,
                    )
                except StateStoreError as exc:
                    raise InspectionWorkflowControllerError(
                        "managed task start recovery identity is invalid"
                    ) from exc
        try:
            result = self._store.checkpoint_context(
                run_id=self.run_id,
                expected_lock_token=self._expected_lock_token,
                expected_status=state["status"],
                expected_state_version=state["state_version"],
                operation_id=operation_id,
                mutation_timestamp=timestamp,
                payload=payload,
            )
        except StateStoreError as exc:
            raise InspectionWorkflowControllerError(
                f"managed checkpoint {kind} failed"
            ) from exc
        self._accept_result(result, operation_id)

    def _accept_result(
        self, result: Mapping[str, Any], expected_operation_id: str
    ) -> None:
        state = result.get("canonical_state")
        version = result.get("resulting_state_version")
        if (
            result.get("run_id") != self.run_id
            or result.get("operation_id") != expected_operation_id
            or not isinstance(state, Mapping)
            or type(version) is not int
            or state.get("state_version") != version
            or version != self._snapshot["state_version"] + 1
        ):
            raise InspectionWorkflowControllerError(
                "StateStore returned an invalid mutation result"
            )
        self._snapshot = freeze_json(
            {
                "run_id": self.run_id,
                "status": state["status"],
                "state_version": version,
                "canonical_state": state,
            }
        )

    def _submit_event_locked(self, event: Mapping[str, Any]) -> None:
        kind = event["checkpoint_kind"]
        task_id = event["task_id"]
        state = self._state()
        if not isinstance(task_id, str) or task_id not in state["task_status"]:
            raise InspectionWorkflowControllerError(
                "managed checkpoint task_id is not in canonical State"
            )
        current_status = state["task_status"][task_id]
        current_attempt = state["task_attempts"][task_id]
        tasks = self._tasks
        if tasks is None or task_id not in tasks:
            raise InspectionWorkflowControllerError(
                "managed checkpoint event arrived before task-plan preparation"
            )
        task = tasks[task_id]
        dependency_statuses = [
            state["task_status"][dependency] for dependency in task.deps
        ]

        if kind == "task_skipped":
            if (
                not dependency_statuses
                or any(
                    status not in {"success", "failed", "skipped"}
                    for status in dependency_statuses
                )
                or not any(
                    status in {"failed", "skipped"}
                    for status in dependency_statuses
                )
            ):
                raise InspectionWorkflowControllerError(
                    "managed dependency skip is not supported by canonical State"
                )
            self._checkpoint_locked(
                self._task_payload(
                    kind,
                    task_id,
                    0,
                    "pending",
                    "skipped",
                    {"skip_reason": event["skip_reason"]},
                )
            )
        elif kind == "task_started":
            self._require_successful_dependencies(
                task_id, dependency_statuses
            )
            if (
                current_status == "retry_scheduled"
                and current_attempt >= task.retries + 1
            ):
                raise InspectionWorkflowControllerError(
                    "managed task retry exceeds the canonical retry policy"
                )
            attempt = 1 if current_status == "pending" else current_attempt + 1
            self._checkpoint_locked(
                self._task_payload(
                    kind,
                    task_id,
                    attempt,
                    current_status,
                    "running",
                    {},
                )
            )
        elif kind == "task_succeeded":
            self._checkpoint_locked(
                self._task_payload(
                    kind,
                    task_id,
                    current_attempt,
                    "running",
                    "success",
                    {"task_output": {"result": event["task_output"]}},
                )
            )
        else:
            retryable = event["retryable"]
            if type(retryable) is not bool:
                raise InspectionWorkflowControllerError(
                    "managed task failure retryable must be boolean"
                )
            expected_retryable = current_attempt <= task.retries
            if retryable is not expected_retryable:
                raise InspectionWorkflowControllerError(
                    "managed task failure conflicts with the canonical retry policy"
                )
            provenance = event["failure_provenance"]
            if not isinstance(provenance, Mapping):
                raise InspectionWorkflowControllerError(
                    "managed task failure provenance must be an object"
                )
            failure_provenance = deepcopy(dict(provenance))
            if retryable:
                policy_sha256 = retry_policy_sha256(task)
                failure_provenance.update(
                    {
                        "retryable": True,
                        "retry_policy_sha256": policy_sha256,
                        "backoff_seconds": 0,
                    }
                )
            self._checkpoint_locked(
                self._task_payload(
                    kind,
                    task_id,
                    current_attempt,
                    "running",
                    "retry_pending" if retryable else "failed",
                    {"failure_provenance": failure_provenance},
                    retry_disposition="retry" if retryable else "terminal",
                    next_attempt=current_attempt + 1 if retryable else None,
                    error_summary=event["error_summary"],
                )
            )
            if retryable:
                self._schedule_retry_locked(
                    task_id,
                    current_attempt,
                    policy_sha256,
                    0,
                )

    @staticmethod
    def _require_successful_dependencies(
        task_id: str, dependency_statuses: list[str]
    ) -> None:
        if any(status != "success" for status in dependency_statuses):
            raise InspectionWorkflowControllerError(
                f"managed task {task_id} dependencies are not canonically successful"
            )

    def _resume_retry_schedules_locked(self) -> None:
        state = self._state()
        for task_id in sorted(state["task_status"]):
            if state["task_status"][task_id] != "retry_pending":
                continue
            attempt = state["task_attempts"][task_id]
            failed_id = (
                f"run:{self.run_id}:task:{task_id}:attempt:{attempt}:failed"
            )
            events = state["context"].get("phase_a3_checkpoint_events", {})
            failed = events.get(failed_id) if isinstance(events, Mapping) else None
            delta = (
                failed.get("controlled_context_delta")
                if isinstance(failed, Mapping)
                else None
            )
            provenance = (
                delta.get("failure_provenance")
                if isinstance(delta, Mapping)
                else None
            )
            if (
                not isinstance(provenance, Mapping)
                or set(provenance)
                != {
                    "error_type",
                    "retryable",
                    "retry_policy_sha256",
                    "backoff_seconds",
                }
                or provenance.get("retryable") is not True
                or provenance.get("retry_policy_sha256")
                != retry_policy_sha256(self._tasks[task_id])
                or provenance.get("backoff_seconds") != 0
                or not isinstance(provenance.get("error_type"), str)
                or not _ERROR_TYPE_RE.fullmatch(provenance["error_type"])
            ):
                raise InspectionWorkflowControllerError(
                    "retry_pending State does not match the canonical retry policy"
                )
            self._schedule_retry_locked(
                task_id,
                attempt,
                provenance["retry_policy_sha256"],
                provenance["backoff_seconds"],
            )
            state = self._state()

    def _schedule_retry_locked(
        self,
        task_id: str,
        attempt: int,
        retry_policy_sha256: Any,
        backoff_seconds: Any,
    ) -> None:
        self._checkpoint_locked(
            self._task_payload(
                "task_retry_scheduled",
                task_id,
                attempt,
                "retry_pending",
                "retry_scheduled",
                {
                    "failed_operation_id": (
                        f"run:{self.run_id}:task:{task_id}:attempt:{attempt}:failed"
                    ),
                    "retry_policy_sha256": retry_policy_sha256,
                    "backoff_seconds": backoff_seconds,
                },
                retry_disposition="retry",
                next_attempt=attempt + 1,
            )
        )

    @staticmethod
    def _task_payload(
        kind: str,
        task_id: str,
        attempt: int,
        expected_status: str,
        next_status: str,
        delta: Mapping[str, Any],
        *,
        retry_disposition: str = "none",
        next_attempt: int | None = None,
        error_summary: str | None = None,
    ) -> dict[str, Any]:
        return {
            "checkpoint_kind": kind,
            "task_id": task_id,
            "attempt_number": attempt,
            "expected_task_status": expected_status,
            "next_task_status": next_status,
            "retry_disposition": retry_disposition,
            "next_attempt_number": next_attempt,
            "controlled_context_delta": deepcopy(dict(delta)),
            "error_summary": error_summary,
        }


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_json(item) for item in value]
    return value
