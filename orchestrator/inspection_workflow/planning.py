"""Deterministic Phase A3 task-plan projection from the existing DAG."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from orchestrator.dag.builder import Task
from orchestrator.dag.scheduler import execution_layers
from orchestrator.inspection_workflow.contracts import IDENTIFIER_PATTERN


class WorkflowPlanningError(ValueError):
    """Raised when existing DAG tasks cannot form a canonical Phase A3 plan."""


def build_required_task_plan(tasks: Mapping[str, Task]) -> list[dict[str, Any]]:
    """Return the StateStore task-plan projection without creating another DAG."""

    if not isinstance(tasks, Mapping) or not tasks:
        raise WorkflowPlanningError("workflow tasks must be a non-empty mapping")

    materialized = dict(tasks)
    for task_id, task in materialized.items():
        if (
            not isinstance(task_id, str)
            or not IDENTIFIER_PATTERN.fullmatch(task_id)
            or not isinstance(task, Task)
            or task.name != task_id
        ):
            raise WorkflowPlanningError("workflow task identifiers are invalid")
        if (
            not isinstance(task.agent, str)
            or not IDENTIFIER_PATTERN.fullmatch(task.agent)
            or type(task.retries) is not int
            or task.retries < 0
            or type(task.cache) is not bool
        ):
            raise WorkflowPlanningError(f"workflow execution policy is invalid for {task_id}")
        if (
            not isinstance(task.deps, list)
            or any(
                not isinstance(dep, str) or not IDENTIFIER_PATTERN.fullmatch(dep)
                for dep in task.deps
            )
            or len(task.deps) != len(set(task.deps))
            or task_id in task.deps
        ):
            raise WorkflowPlanningError(f"workflow dependencies are invalid for {task_id}")

    try:
        execution_layers(materialized)
    except (KeyError, ValueError) as exc:
        raise WorkflowPlanningError(f"workflow DAG is invalid: {exc}") from exc

    return [
        {
            "task_id": task_id,
            "deps": sorted(materialized[task_id].deps),
            "required": True,
        }
        for task_id in sorted(materialized)
    ]


def task_plan_fingerprint(tasks: Mapping[str, Task]) -> str:
    """Bind the canonical plan and the execution policy used by the adapter."""

    task_plan = build_required_task_plan(tasks)
    materialized = dict(tasks)
    payload = {
        "schema_version": "phase_a3_task_plan_fingerprint_v1",
        "task_plan": task_plan,
        "execution_policy": [
            {
                "task_id": task_id,
                "agent": materialized[task_id].agent,
                "retries": materialized[task_id].retries,
                "cache": materialized[task_id].cache,
            }
            for task_id in sorted(materialized)
        ],
    }
    data = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def retry_policy_sha256(task: Task) -> str:
    """Return the fixed A3.3.1 retry policy used by managed execution."""

    payload = {
        "schema_version": "phase_a3_retry_policy_v1",
        "task_id": task.name,
        "max_retries": task.retries,
        "backoff_seconds": 0,
    }
    data = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()
