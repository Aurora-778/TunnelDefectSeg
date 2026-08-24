"""Phase B.10 controlled one-layer Resume progression.

The supported boundary accepts only an official B.9 execution result.  It
derives the unique next DAG layer from the committed plan and canonical
checkpoints, persists one intent, and delegates task mutations to the existing
Controller/DAGExecutor path.  Completion, publication, retry, cache and lock
ownership changes are intentionally outside this module.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import types
from typing import Any, Mapping
import uuid
import weakref

from orchestrator.agents.base import BaseAgent
from orchestrator.dag.scheduler import execution_layers
from orchestrator.executor import DAGExecutor
from orchestrator.inspection_workflow import a1_artifacts, controlled_fs
from orchestrator.inspection_workflow.controller import InspectionWorkflowController
from orchestrator.inspection_workflow.explicit_resume_activation import _RUN_ID_RE
from orchestrator.inspection_workflow.locking import (
    validate_active_run_control_entries,
    validate_active_run_lock_snapshot,
)
from orchestrator.inspection_workflow.resume_execution import ResumeExecutionResult
from orchestrator.registry import build_default_registry
from orchestrator.state.store import StateStore

from . import resume_execution as _b9


RESUME_LAYER_PROGRESSION_SCHEMA_VERSION = "inspection_resume_layer_progression_v1"
RESUME_LAYER_PROGRESSION_INTENT_SCHEMA_VERSION = (
    "inspection_resume_layer_progression_intent_v1"
)
_PROGRESSED = "resume_layer_progressed"
_NOT_PROGRESSED = "resume_layer_not_progressed"
_INTENT_DIRECTORY = "resume_layer_progression"
_START_INTENT_NAME = "resume_execution_start.intent.json"


def _copy_function(function: Any) -> Any:
    if isinstance(function, types.MethodType):
        function = function.__func__
    copied = types.FunctionType(
        function.__code__,
        dict(function.__globals__),
        name=function.__name__,
        argdefs=function.__defaults__,
        closure=function.__closure__,
    )
    copied.__kwdefaults__ = dict(function.__kwdefaults__ or {})
    return copied


_CANONICAL = _copy_function(_b9._canonical)
_SHA = _copy_function(_b9._sha)
_READ_JSON = _copy_function(_b9._read_json_guarded)
_READ_GUARDED = _b9._READ_GUARDED
_B9_RESULT_VALIDATE = ResumeExecutionResult._post_init_internal
_B9_EXECUTED = ResumeExecutionResult._executed_internal
_B9_VALIDATE_B5 = _copy_function(_b9._validate_b5_current)
_B9_VALIDATE_HANDOFF_INTENT = _copy_function(_b9._validate_handoff_intent_current)
_B9_VALIDATE_ACTIVATION_INTENT = _copy_function(_b9._validate_activation_intent_current)
_B9_VALIDATE_START_INTENT = _copy_function(_b9._validate_start_intent)
_B9_CANONICAL_TASK_PLAN = _copy_function(_b9._canonical_task_plan)
_B9_VALIDATE_INPUT_SNAPSHOTS = _copy_function(_b9._validate_input_snapshots)
_B9_HOLD_SNAPSHOT_OBJECTS = _b9._hold_snapshot_objects
_B9_SNAPSHOT_BYTES = _copy_function(_b9._snapshot_bytes_from_held_handles)
_B9_OPEN_READ_GUARD = _copy_function(_b9._open_source_read_guard)
_B9_SOURCE_CONTEXT = _copy_function(_b9._source_input_context)
_B9_DECODE_WORKER_PATH = _copy_function(_b9._decode_worker_path_token)
_B9_EXTERNAL_TREE = _copy_function(_b9._external_tree_snapshot)
_B9_OTHER_RUN_TREE = _copy_function(_b9._worker_tree_metadata)
_B9_DIRECT_TREE = _copy_function(_b9._worker_direct_metadata)
_B9_EXPECTED_WORK = _copy_function(_b9._expected_work_entries)
_DIRECTORY_CHAIN = _b9._DIRECTORY_CHAIN
_ASSERT_DIRECTORY_CHAIN = _b9._ASSERT_DIRECTORY_CHAIN
_DIRECTORY_IDENTITY = _b9._DIRECTORY_IDENTITY
_FILE_IDENTITY = _b9._FILE_IDENTITY
_CONTROLLED_ROOT = _b9._CONTROLLED_ROOT
_WRITE_EXCLUSIVE = controlled_fs.write_exclusive
_MAKE_DIRECTORY = controlled_fs.make_directory
_BIND_DIRECTORIES = controlled_fs.bind_directory_identities
_STORE_TYPE = StateStore
_VALIDATE_AUTHORITY = StateStore.validate_authority_snapshot_bytes
_ANCHOR_DOCUMENT = StateStore._anchor_document
_CONTROLLER_TYPE = InspectionWorkflowController
_EXECUTOR_TYPE = DAGExecutor
_EXECUTION_LAYERS = execution_layers
_A1_PROFILE = a1_artifacts.PHASE_A1_EXECUTION_PROFILE
_VALIDATE_COMPARISON_BUNDLE = a1_artifacts.validate_comparison_evidence_bundle


def _new_registry() -> tuple[Any, Any, Any]:
    issued: weakref.WeakValueDictionary[int, Any] = weakref.WeakValueDictionary()

    def contains(value: Any) -> bool:
        return issued.get(id(value)) is value

    def add(value: Any) -> None:
        issued[id(value)] = value

    def discard(value: Any) -> None:
        if issued.get(id(value)) is value:
            issued.pop(id(value), None)

    return contains, add, discard


_IS_OFFICIAL, _REGISTER, _DISCARD = _new_registry()


@dataclass(frozen=True, init=False, slots=True, weakref_slot=True)
class ResumeLayerProgressionResult:
    status: str
    progression_bytes: bytes
    progression_sha256: str
    successor_run_id: str | None
    source_run_id: str | None
    execution_sha256: str | None
    source_admission_sha256: str | None
    activation_sha256: str | None
    activation_intent_sha256: str | None
    handoff_sha256: str | None
    handoff_intent_sha256: str | None
    allocation_token: str | None
    lock_token: str | None
    predecessor_state_version: int | None
    state_version: int | None
    layer_index: int | None
    plan_fingerprint: str | None
    input_descriptor_sha256: str | None
    task_plan_sha256: str | None
    progression_intent_sha256: str | None
    required_task_ids: tuple[str, ...]
    completed_task_ids: tuple[str, ...]
    executed_task_ids: tuple[str, ...]
    state_sha256: str | None
    journal_sha256: str | None
    journal_anchor_sha256: str | None
    lock_sha256: str | None
    source_state_sha256: str | None
    source_journal_sha256: str | None
    source_journal_anchor_sha256: str | None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("ResumeLayerProgressionResult is issued only by B.10")

    def __post_init__(self) -> None:
        if not _IS_OFFICIAL(self):
            raise ValueError("progression result is not official")
        if type(self.progression_bytes) is not bytes:
            raise ValueError("progression bytes are invalid")
        if _SHA(self.progression_bytes) != self.progression_sha256:
            raise ValueError("progression SHA is invalid")
        bindings = _result_bindings(self)
        expected = _CANONICAL(
            {"schema_version": RESUME_LAYER_PROGRESSION_SCHEMA_VERSION, "status": self.status}
            if self.status == _NOT_PROGRESSED
            else {
                "schema_version": RESUME_LAYER_PROGRESSION_SCHEMA_VERSION,
                "status": self.status,
                **bindings,
            }
        )
        if self.progression_bytes != expected:
            raise ValueError("progression bytes do not match fields")
        if self.status == _NOT_PROGRESSED:
            if any(
                value is not None
                for key, value in bindings.items()
                if key not in {"required_task_ids", "completed_task_ids", "executed_task_ids"}
            ) or any(
                getattr(self, name) != ()
                for name in ("required_task_ids", "completed_task_ids", "executed_task_ids")
            ):
                raise ValueError("denied progression leaks authority")
            return
        if self.status != _PROGRESSED:
            raise ValueError("progression status is invalid")
        if (
            type(self.successor_run_id) is not str
            or _RUN_ID_RE.fullmatch(self.successor_run_id) is None
            or type(self.source_run_id) is not str
            or _RUN_ID_RE.fullmatch(self.source_run_id) is None
            or type(self.predecessor_state_version) is not int
            or type(self.state_version) is not int
            or self.state_version <= self.predecessor_state_version
            or type(self.layer_index) is not int
            or self.layer_index < 2
            or not self.required_task_ids
            or not self.completed_task_ids
            or not self.executed_task_ids
            or tuple(sorted(set(self.required_task_ids))) != self.required_task_ids
            or tuple(sorted(set(self.completed_task_ids))) != self.completed_task_ids
            or tuple(sorted(set(self.executed_task_ids))) != self.executed_task_ids
            or not set(self.executed_task_ids) <= set(self.completed_task_ids) <= set(self.required_task_ids)
            or not _uuid(self.allocation_token)
            or not _uuid(self.lock_token)
            or any(
                type(getattr(self, name)) is not str
                or len(getattr(self, name)) != 64
                or any(char not in "0123456789abcdef" for char in getattr(self, name))
                for name in (
                    "execution_sha256", "source_admission_sha256", "activation_sha256",
                    "activation_intent_sha256", "handoff_sha256", "handoff_intent_sha256",
                    "plan_fingerprint", "input_descriptor_sha256", "task_plan_sha256",
                    "progression_intent_sha256", "state_sha256", "journal_sha256",
                    "journal_anchor_sha256", "lock_sha256",
                    "source_state_sha256", "source_journal_sha256",
                    "source_journal_anchor_sha256",
                )
            )
        ):
            raise ValueError("successful progression bindings are invalid")

    @property
    def resume_layer_progressed(self) -> bool:
        try:
            ResumeLayerProgressionResult._post_init_internal(self)
        except Exception:
            return False
        return self.status == ResumeLayerProgressionResult._progressed_internal


def _uuid(value: Any) -> bool:
    try:
        return type(value) is str and str(uuid.UUID(value)) == value
    except (AttributeError, ValueError):
        return False


def _result_bindings(value: ResumeLayerProgressionResult) -> dict[str, Any]:
    return {
        "successor_run_id": value.successor_run_id,
        "source_run_id": value.source_run_id,
        "execution_sha256": value.execution_sha256,
        "source_admission_sha256": value.source_admission_sha256,
        "activation_sha256": value.activation_sha256,
        "activation_intent_sha256": value.activation_intent_sha256,
        "handoff_sha256": value.handoff_sha256,
        "handoff_intent_sha256": value.handoff_intent_sha256,
        "allocation_token": value.allocation_token,
        "lock_token": value.lock_token,
        "predecessor_state_version": value.predecessor_state_version,
        "state_version": value.state_version,
        "layer_index": value.layer_index,
        "plan_fingerprint": value.plan_fingerprint,
        "input_descriptor_sha256": value.input_descriptor_sha256,
        "task_plan_sha256": value.task_plan_sha256,
        "progression_intent_sha256": value.progression_intent_sha256,
        "required_task_ids": list(value.required_task_ids),
        "completed_task_ids": list(value.completed_task_ids),
        "executed_task_ids": list(value.executed_task_ids),
        "state_sha256": value.state_sha256,
        "journal_sha256": value.journal_sha256,
        "journal_anchor_sha256": value.journal_anchor_sha256,
        "lock_sha256": value.lock_sha256,
        "source_state_sha256": value.source_state_sha256,
        "source_journal_sha256": value.source_journal_sha256,
        "source_journal_anchor_sha256": value.source_journal_anchor_sha256,
    }


_POST_INIT = _copy_function(ResumeLayerProgressionResult.__post_init__)
ResumeLayerProgressionResult._post_init_internal = _POST_INIT
ResumeLayerProgressionResult._progressed_internal = _PROGRESSED


def _issue(values: Mapping[str, Any]) -> ResumeLayerProgressionResult:
    result = object.__new__(ResumeLayerProgressionResult)
    for name in ResumeLayerProgressionResult.__dataclass_fields__:
        object.__setattr__(
            result,
            name,
            values.get(
                name,
                () if name in {"required_task_ids", "completed_task_ids", "executed_task_ids"} else None,
            ),
        )
    _REGISTER(result)
    try:
        _POST_INIT(result)
    except Exception:
        _DISCARD(result)
        raise
    return result


def _denied() -> ResumeLayerProgressionResult:
    data = _CANONICAL(
        {"schema_version": RESUME_LAYER_PROGRESSION_SCHEMA_VERSION, "status": _NOT_PROGRESSED}
    )
    return _issue(
        {
            "status": _NOT_PROGRESSED,
            "progression_bytes": data,
            "progression_sha256": _SHA(data),
        }
    )


class _ExecutionView:
    """Exact B.9-bound view consumed by archived B.6–B.9 validators."""

    def __init__(self, result: ResumeExecutionResult) -> None:
        for name in (
            "successor_run_id", "source_run_id", "handoff_sha256",
            "handoff_intent_sha256", "activation_sha256", "activation_intent_sha256",
            "source_admission_sha256", "source_state_version", "allocation_token",
            "lock_token", "plan_fingerprint", "input_descriptor_sha256",
            "task_plan_sha256", "required_task_ids",
        ):
            setattr(self, name, getattr(result, name))


def _canonical_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_json(item) for item in value]
    return value


def _read_successor_authority(root: Path, run_id: str) -> dict[str, Any]:
    state_bytes, _ = _READ_GUARDED(root, f"runs/{run_id}/state.json")
    journal_bytes, _ = _READ_GUARDED(root, f"runs/{run_id}/state_journal.jsonl")
    anchor_bytes, _ = _READ_GUARDED(root, f"runs/{run_id}/state_journal_tail.json")
    authority = _VALIDATE_AUTHORITY(
        _STORE_TYPE(root),
        run_id=run_id,
        state_bytes=state_bytes,
        journal_bytes=journal_bytes,
        anchor_bytes=anchor_bytes,
    )
    return {
        "state": authority["canonical_state"],
        "state_bytes": state_bytes,
        "journal_bytes": journal_bytes,
        "anchor_bytes": anchor_bytes,
    }


def _journal_records(data: bytes) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in data.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            raise ValueError("Journal line is incomplete")
        value = json.loads(line.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Journal record is invalid")
        records.append(value)
    return records


def _validate_b9_lineage(
    root: Path,
    result: ResumeExecutionResult,
    authority: Mapping[str, Any],
) -> None:
    journal = authority["journal_bytes"]
    records = _journal_records(journal)
    prefix = b""
    matched = False
    for row, line in zip(records, journal.splitlines(keepends=True)):
        prefix += line
        if _SHA(prefix) == result.journal_sha256:
            if (
                row.get("resulting_state_version") != result.state_version
                or row.get("resulting_state_sha256") != result.state_sha256
            ):
                raise ValueError("B.9 State/Journal lineage drifted")
            anchor = _ANCHOR_DOCUMENT(
                _STORE_TYPE(root),
                run_id=result.successor_run_id,
                allocation_token=result.allocation_token,
                tail_record_index=row.get("record_index"),
                tail_record_checksum=row.get("record_checksum"),
                tail_file_size_bytes=len(prefix),
            )
            if _SHA(_CANONICAL(anchor)) != result.journal_anchor_sha256:
                raise ValueError("B.9 anchor lineage drifted")
            matched = True
            break
    if not matched:
        raise ValueError("B.9 lineage is missing")


def _completed_prefix(
    state: Mapping[str, Any],
    layers: list[list[str]],
    result: ResumeExecutionResult,
) -> tuple[int, tuple[str, ...]]:
    statuses = state.get("task_status")
    attempts = state.get("task_attempts")
    if not isinstance(statuses, Mapping) or not isinstance(attempts, Mapping):
        raise ValueError("task status is invalid")
    required = set(result.required_task_ids)
    if set(statuses) != required or set(attempts) != required:
        raise ValueError("task closure drifted")
    completed: set[str] = set()
    completed_layers = 0
    for layer in layers:
        values = {statuses.get(task_id) for task_id in layer}
        if values == {"success"}:
            completed.update(layer)
            completed_layers += 1
            continue
        if values != {"pending"}:
            raise ValueError("task layer is partial or failed")
        break
    if any(
        statuses.get(task_id) != ("success" if task_id in completed else "pending")
        or attempts.get(task_id) != (1 if task_id in completed else 0)
        for task_id in result.required_task_ids
    ):
        raise ValueError("task attempts or statuses drifted")
    if set(state.get("completed_tasks", ())) != completed or state.get("failed_tasks"):
        raise ValueError("task completion projection drifted")
    if completed_layers < 1 or set(result.executed_task_ids) != set(layers[0]):
        raise ValueError("B.9 first layer is not committed")
    return completed_layers, tuple(sorted(completed))


def _intent_name(layer_index: int) -> str:
    return f"layer_{layer_index:03d}.intent.json"


def _intent_checksum(value: Mapping[str, Any]) -> str:
    return _SHA(_CANONICAL({key: item for key, item in value.items() if key != "intent_checksum"}))


def _dependency_checkpoint_binding(
    predecessor: Mapping[str, Any], task_ids: tuple[str, ...]
) -> list[dict[str, str]]:
    tasks = predecessor.get("tasks")
    state = predecessor.get("state")
    if not isinstance(tasks, Mapping) or not isinstance(state, Mapping):
        raise ValueError("dependency checkpoint authority is invalid")
    dependencies = sorted(
        {
            dependency
            for task_id in task_ids
            for dependency in tuple(tasks[task_id].deps)
        }
    )
    context = state.get("context")
    events = context.get("phase_a3_checkpoint_events") if isinstance(context, Mapping) else None
    if not isinstance(events, Mapping):
        raise ValueError("dependency checkpoint set is missing")
    result: list[dict[str, str]] = []
    for task_id in dependencies:
        operation_id = (
            f"run:{state.get('run_id')}:task:{task_id}:attempt:1:succeeded"
        )
        event = events.get(operation_id)
        if (
            not isinstance(event, Mapping)
            or event.get("checkpoint_kind") != "task_succeeded"
            or event.get("task_id") != task_id
            or event.get("attempt_number") != 1
        ):
            raise ValueError("dependency checkpoint provenance is invalid")
        result.append(
            {
                "task_id": task_id,
                "operation_id": operation_id,
                "checkpoint_sha256": _SHA(_CANONICAL(_canonical_json(event))),
            }
        )
    return result


def _make_intent(
    execution: ResumeExecutionResult,
    *,
    predecessor: Mapping[str, Any],
    layer_index: int,
    task_ids: tuple[str, ...],
    completed_task_ids: tuple[str, ...],
    marker_bytes: bytes,
    mutation_timestamp: str,
) -> dict[str, Any]:
    value = {
        "schema_version": RESUME_LAYER_PROGRESSION_INTENT_SCHEMA_VERSION,
        "successor_run_id": execution.successor_run_id,
        "source_run_id": execution.source_run_id,
        "execution_sha256": execution.execution_sha256,
        "source_admission_sha256": execution.source_admission_sha256,
        "activation_sha256": execution.activation_sha256,
        "activation_intent_sha256": execution.activation_intent_sha256,
        "handoff_sha256": execution.handoff_sha256,
        "handoff_intent_sha256": execution.handoff_intent_sha256,
        "allocation_token": execution.allocation_token,
        "lock_token": execution.lock_token,
        "predecessor_state_version": predecessor["state"]["state_version"],
        "target_state_version": predecessor["state"]["state_version"] + 2 * len(task_ids),
        "layer_index": layer_index,
        "task_ids": list(task_ids),
        "dependency_checkpoints": _dependency_checkpoint_binding(
            predecessor, task_ids
        ),
        "completed_task_ids": list(completed_task_ids),
        "required_task_ids": list(execution.required_task_ids),
        "plan_fingerprint": execution.plan_fingerprint,
        "input_descriptor_sha256": execution.input_descriptor_sha256,
        "task_plan_sha256": execution.task_plan_sha256,
        "predecessor_state_sha256": _SHA(predecessor["state_bytes"]),
        "predecessor_journal_sha256": _SHA(predecessor["journal_bytes"]),
        "predecessor_anchor_sha256": _SHA(predecessor["anchor_bytes"]),
        "source_state_sha256": _SHA(predecessor["source_state_bytes"]),
        "source_journal_sha256": _SHA(predecessor["source_journal_bytes"]),
        "source_journal_anchor_sha256": _SHA(predecessor["source_anchor_bytes"]),
        "lock_sha256": execution.lock_sha256,
        "successor_marker_sha256": _SHA(marker_bytes),
        "mutation_timestamp": mutation_timestamp,
        "intent_checksum": None,
    }
    value["intent_checksum"] = _intent_checksum(value)
    return value


def _validate_intent(value: Any, data: bytes, expected: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or _CANONICAL(dict(value)) != data:
        raise ValueError("progression intent is not canonical")
    if dict(value) != dict(expected) or value.get("intent_checksum") != _intent_checksum(value):
        raise ValueError("progression intent binding drifted")


def _marker_bytes(execution: ResumeExecutionResult, source: Mapping[str, Any]) -> bytes:
    context = source.get("context")
    mode = context.get("workflow_input_mode") if isinstance(context, Mapping) else None
    evidence_mode = (
        "run_local_projection" if mode == "prepared_dataset" else "normalized_records"
    )
    return _CANONICAL(
        {
            "schema_version": a1_artifacts.PHASE_B10_SUCCESSOR_A1_MARKER_SCHEMA_VERSION,
            "successor_run_id": execution.successor_run_id,
            "source_run_id": execution.source_run_id,
            "execution_profile": _A1_PROFILE,
            "evidence_source_mode": evidence_mode,
            "source_admission_sha256": execution.source_admission_sha256,
            "activation_intent_sha256": execution.activation_intent_sha256,
            "plan_fingerprint": execution.plan_fingerprint,
        }
    )


def _validate_progression_intents(
    root: Path,
    execution: ResumeExecutionResult,
    authority: Mapping[str, Any],
    layers: list[list[str]],
    completed_layers: int,
) -> None:
    successor = root / "runs" / execution.successor_run_id
    directory = successor / _INTENT_DIRECTORY
    expected_names = {_intent_name(index) for index in range(2, completed_layers + 1)}
    if not expected_names:
        if directory.exists():
            raise ValueError("unexpected progression intent residue")
        return
    state = directory.lstat()
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode):
        raise ValueError("progression intent directory is unsafe")
    names = {entry.name for entry in directory.iterdir()}
    if names != expected_names:
        raise ValueError("progression intent set is incomplete")
    # Every historical intent remains canonical and bound to the immutable B.9
    # lineage.  The StateStore Journal remains the authority for its resulting
    # task checkpoints and versions.
    for index in range(2, completed_layers + 1):
        value, data = _READ_JSON(
            root,
            f"runs/{execution.successor_run_id}/{_INTENT_DIRECTORY}/{_intent_name(index)}",
        )
        predecessor_version = execution.state_version + 2 * sum(
            len(layer) for layer in layers[1 : index - 1]
        )
        target_version = predecessor_version + 2 * len(layers[index - 1])
        prefix = b""
        predecessor_row: Mapping[str, Any] | None = None
        predecessor_prefix = b""
        for row, line in zip(
            _journal_records(authority["journal_bytes"]),
            authority["journal_bytes"].splitlines(keepends=True),
        ):
            prefix += line
            version = row.get("resulting_state_version")
            if version == predecessor_version:
                predecessor_row = row
                predecessor_prefix = prefix
            elif type(version) is int and version > predecessor_version:
                break
        if predecessor_row is None:
            raise ValueError("historical progression predecessor is missing")
        predecessor_anchor = _ANCHOR_DOCUMENT(
            _STORE_TYPE(root),
            run_id=execution.successor_run_id,
            allocation_token=execution.allocation_token,
            tail_record_index=predecessor_row.get("record_index"),
            tail_record_checksum=predecessor_row.get("record_checksum"),
            tail_file_size_bytes=len(predecessor_prefix),
        )
        expected_completed = tuple(
            sorted(task_id for layer in layers[: index - 1] for task_id in layer)
        )
        expected_fixed = {
            "source_admission_sha256": execution.source_admission_sha256,
            "activation_sha256": execution.activation_sha256,
            "activation_intent_sha256": execution.activation_intent_sha256,
            "handoff_sha256": execution.handoff_sha256,
            "handoff_intent_sha256": execution.handoff_intent_sha256,
            "allocation_token": execution.allocation_token,
            "lock_token": execution.lock_token,
            "predecessor_state_version": predecessor_version,
            "target_state_version": target_version,
            "completed_task_ids": list(expected_completed),
            "required_task_ids": list(execution.required_task_ids),
            "plan_fingerprint": execution.plan_fingerprint,
            "input_descriptor_sha256": execution.input_descriptor_sha256,
            "task_plan_sha256": execution.task_plan_sha256,
            "predecessor_state_sha256": predecessor_row.get(
                "resulting_state_sha256"
            ),
            "predecessor_journal_sha256": _SHA(predecessor_prefix),
            "predecessor_anchor_sha256": _SHA(_CANONICAL(predecessor_anchor)),
            "source_state_sha256": _SHA(authority["source_state_bytes"]),
            "source_journal_sha256": _SHA(authority["source_journal_bytes"]),
            "source_journal_anchor_sha256": _SHA(authority["source_anchor_bytes"]),
            "lock_sha256": execution.lock_sha256,
            "successor_marker_sha256": _SHA(_marker_bytes(execution, authority["source"])),
            "mutation_timestamp": authority["start"].get("mutation_timestamp"),
        }
        if (
            value.get("schema_version") != RESUME_LAYER_PROGRESSION_INTENT_SCHEMA_VERSION
            or value.get("successor_run_id") != execution.successor_run_id
            or value.get("source_run_id") != execution.source_run_id
            or value.get("execution_sha256") != execution.execution_sha256
            or value.get("layer_index") != index
            or tuple(value.get("task_ids", ())) != tuple(sorted(layers[index - 1]))
            or value.get("dependency_checkpoints")
            != _dependency_checkpoint_binding(
                authority, tuple(sorted(layers[index - 1]))
            )
            or any(value.get(field) != expected for field, expected in expected_fixed.items())
            or value.get("intent_checksum") != _intent_checksum(value)
            or _CANONICAL(value) != data
        ):
            raise ValueError("historical progression intent drifted")


def _expected_successor_entries(
    execution: ResumeExecutionResult,
    authority: Mapping[str, Any],
    completed_layers: int,
) -> frozenset[str]:
    expected = {
        "resume_execution_handoff.intent.json",
        _START_INTENT_NAME,
        "state.json",
        "state_journal.jsonl",
        "state_journal_tail.json",
        "work",
    }
    expected.update(
        _B9_EXPECTED_WORK(
            execution.successor_run_id,
            authority["snapshots"],
            authority["snapshot_bytes"],
        )
    )
    if completed_layers >= 2:
        expected.update(
            {
                ".phase_a1_sandbox.json",
                _INTENT_DIRECTORY,
                "artifacts",
                "artifacts/comparison_evidence.csv",
                "artifacts/comparison_evidence_manifest.json",
                "work/association_manifest.json",
                "work/association_records.csv",
                "work/engineering_records.csv",
                "work/frame_records.csv",
                "work/projection_receipt.json",
                "work/main_progressive",
            }
        )
        for path in tuple(expected):
            prefix = "work/raw_history/main_progressive/"
            if not path.startswith(prefix):
                continue
            tail = path[len(prefix) :]
            if "/" not in tail or Path(tail).name in {
                "query_frames.csv",
                "association_records.csv",
                "memory_before_query.csv",
                "memory_after_query.csv",
            }:
                expected.add("work/main_progressive/" + tail)
        expected.update(
            f"{_INTENT_DIRECTORY}/{_intent_name(index)}"
            for index in range(2, completed_layers + 1)
        )
    if completed_layers >= 3:
        expected.add("artifacts/claim_decision.json")
    if completed_layers >= 4:
        expected.update(
            {
                "staging",
                "staging/disease_engineering_report.md",
                "staging/disease_engineering_report_summary.md",
                "staging/disease_growth_analysis_report.md",
                "staging/disease_growth_analysis_summary.md",
                "staging/memory_agent_report.md",
                "staging/disease_memory_bank_summary.md",
            }
        )
    if completed_layers >= 5:
        expected.update(
            {
                "staging/priority_recheck_list.csv",
                "staging/visualizations",
                "staging/visualizations/static_audit_status_distribution.png",
                "staging/visualizations/comparability_status_distribution.png",
                "staging/visualizations/static_area_audit.png",
                "staging/visualization_report.md",
                "staging/visualization_summary.md",
                "staging/recheck_list_report.md",
            }
        )
    return frozenset(expected)


def _validate_successor_entry_set(
    root: Path,
    execution: ResumeExecutionResult,
    authority: Mapping[str, Any],
    completed_layers: int,
) -> None:
    successor = root / "runs" / execution.successor_run_id
    observed: set[str] = set()
    pending = [successor]
    while pending:
        directory = pending.pop()
        _DIRECTORY_IDENTITY(directory)
        for entry in directory.iterdir():
            relative = entry.relative_to(successor).as_posix()
            state = entry.lstat()
            reparse = bool(
                getattr(state, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
            )
            if reparse or stat.S_ISLNK(state.st_mode):
                raise ValueError("successor entry set contains a link")
            if stat.S_ISDIR(state.st_mode):
                _DIRECTORY_IDENTITY(entry)
                pending.append(entry)
            elif not stat.S_ISREG(state.st_mode) or state.st_nlink != 1:
                raise ValueError("successor entry set contains an unsafe leaf")
            observed.add(relative)
    if observed != set(
        _expected_successor_entries(execution, authority, completed_layers)
    ):
        raise ValueError("successor entry set is not canonical")


@contextmanager
def _hold_readable_input_snapshots(
    root: Path, snapshots: tuple[Mapping[str, Any], ...]
):
    handles: list[Any] = []
    captured: dict[str, bytes] = {}
    primary: BaseException | None = None
    try:
        for item in snapshots:
            path = root.joinpath(*item["snapshot_path"].split("/"))
            if _FILE_IDENTITY(path) != (item["device"], item["inode"]):
                raise ValueError("progression input identity changed")
            handle = _B9_OPEN_READ_GUARD(path)
            handles.append(handle)
            state = os.fstat(handle.fileno())
            handle.seek(0)
            data = handle.read()
            if (
                not stat.S_ISREG(state.st_mode)
                or state.st_nlink != 1
                or state.st_size != item["size_bytes"]
                or len(data) != item["size_bytes"]
                or _SHA(data) != item["sha256"]
                or _FILE_IDENTITY(path) != (item["device"], item["inode"])
            ):
                raise ValueError("progression input snapshot changed")
            captured[item["snapshot_path"]] = bytes(data)
        yield types.MappingProxyType(dict(captured))
        for item, handle in zip(snapshots, handles):
            path = root.joinpath(*item["snapshot_path"].split("/"))
            handle.seek(0)
            data = handle.read()
            state = os.fstat(handle.fileno())
            if (
                data != captured[item["snapshot_path"]]
                or state.st_nlink != 1
                or state.st_size != item["size_bytes"]
                or _FILE_IDENTITY(path) != (item["device"], item["inode"])
            ):
                raise ValueError("progression input changed during worker execution")
    except BaseException as exc:
        primary = exc
        raise
    finally:
        errors: list[BaseException] = []
        for handle in reversed(handles):
            try:
                handle.close()
            except BaseException as exc:
                errors.append(exc)
        if primary is None and errors:
            raise errors[0]


def _worker_entry_authority(
    root: Path,
    execution: ResumeExecutionResult,
    authority: Mapping[str, Any],
    completed_layers: int,
) -> tuple[frozenset[str], frozenset[str], tuple[Mapping[str, Any], ...]]:
    current = _expected_successor_entries(execution, authority, completed_layers)
    following = _expected_successor_entries(execution, authority, completed_layers + 1)
    prefixes = ("work", "artifacts", "staging")

    def worker_entry(value: str) -> bool:
        return any(value == prefix or value.startswith(prefix + "/") for prefix in prefixes)

    expected = frozenset(item for item in following if worker_entry(item))
    committed: set[str] = set()
    descriptors: list[Mapping[str, Any]] = []
    snapshot_by_path = {item["snapshot_path"]: item for item in authority["snapshots"]}
    successor = root / "runs" / execution.successor_run_id
    for relative in sorted(item for item in current if worker_entry(item)):
        path = successor.joinpath(*relative.split("/"))
        state = path.lstat()
        if stat.S_ISDIR(state.st_mode):
            continue
        if stat.S_ISLNK(state.st_mode) or not stat.S_ISREG(state.st_mode) or state.st_nlink != 1:
            raise ValueError("committed worker input is unsafe")
        identity = _FILE_IDENTITY(path)
        full_relative = f"runs/{execution.successor_run_id}/{relative}"
        item = snapshot_by_path.get(full_relative)
        if item is None:
            data, metadata = _READ_GUARDED(root, full_relative)
            if _FILE_IDENTITY(path) != identity:
                raise ValueError("committed worker input identity changed")
            item = {
                "snapshot_path": full_relative,
                "device": identity[0],
                "inode": identity[1],
                "size_bytes": len(data),
                "sha256": metadata["sha256"],
            }
        descriptors.append(item)
        committed.add(relative)
    return expected, frozenset(committed), tuple(descriptors)


def _completed_artifact_evidence(
    root: Path,
    execution: ResumeExecutionResult,
    authority: Mapping[str, Any],
    completed_layers: int,
) -> tuple[tuple[str, int, str], ...]:
    expected = _expected_successor_entries(execution, authority, completed_layers)
    prefixes = ("work/", "artifacts/", "staging/")
    rows: list[tuple[str, int, str]] = []
    for relative in sorted(item for item in expected if item.startswith(prefixes)):
        path = root / "runs" / execution.successor_run_id / Path(relative)
        state = path.lstat()
        if stat.S_ISDIR(state.st_mode):
            continue
        identity = _FILE_IDENTITY(path)
        data, metadata = _READ_GUARDED(
            root, f"runs/{execution.successor_run_id}/{relative}"
        )
        if (
            stat.S_ISLNK(state.st_mode)
            or not stat.S_ISREG(state.st_mode)
            or state.st_nlink != 1
            or _FILE_IDENTITY(path) != identity
            or len(data) != metadata["size_bytes"]
        ):
            raise ValueError("completed artifact evidence is unsafe")
        rows.append((relative, len(data), metadata["sha256"]))
    return tuple(rows)


def _core_authority(
    root: Path,
    execution: ResumeExecutionResult,
    *,
    bound_snapshot_bytes: Mapping[str, bytes] | None = None,
    held_snapshot_objects: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    view = _ExecutionView(execution)
    source_authority = _read_successor_authority(root, execution.source_run_id)
    source = source_authority["state"]
    handoff_intent, handoff_bytes = _READ_JSON(
        root, f"runs/{execution.successor_run_id}/resume_execution_handoff.intent.json"
    )
    _B9_VALIDATE_HANDOFF_INTENT(
        root,
        execution.successor_run_id,
        view,
        handoff_intent,
        handoff_bytes,
        source,
    )
    _activation, activation_bytes, activation_chain = _B9_VALIDATE_ACTIVATION_INTENT(
        root, view, source
    )
    _B9_VALIDATE_B5(root, view)
    start, start_bytes = _READ_JSON(
        root, f"runs/{execution.successor_run_id}/{_START_INTENT_NAME}"
    )
    if _SHA(start_bytes) != execution.start_intent_sha256:
        raise ValueError("B.9 start intent drifted")
    snapshots = tuple(start.get("input_snapshots", ()))
    snapshot_chain = tuple(start.get("input_snapshot_directory_chain", ()))
    _B9_VALIDATE_START_INTENT(start, start_bytes, view, snapshots, snapshot_chain)
    snapshot_bytes = _B9_VALIDATE_INPUT_SNAPSHOTS(
        root,
        execution.successor_run_id,
        snapshots,
        snapshot_chain,
        bound_snapshot_bytes,
        held_snapshot_objects,
    )
    tasks, task_plan = _B9_CANONICAL_TASK_PLAN(root, source, view)
    marker_path = root / "runs" / execution.successor_run_id / ".phase_a1_sandbox.json"
    try:
        marker_path.lstat()
    except FileNotFoundError:
        pass
    else:
        marker_data, _ = _READ_GUARDED(
            root, f"runs/{execution.successor_run_id}/.phase_a1_sandbox.json"
        )
        if marker_data != _marker_bytes(execution, source):
            raise ValueError("successor A1 marker binding drifted")
    authority = _read_successor_authority(root, execution.successor_run_id)
    state = authority["state"]
    context = state.get("context")
    activation = context.get("resume_activation") if isinstance(context, Mapping) else None
    if (
        state.get("status") != "RUNNING"
        or state.get("state_version", -1) < execution.state_version
        or state.get("allocation_token") != execution.allocation_token
        or state.get("plan_fingerprint") != execution.plan_fingerprint
        or _SHA(_CANONICAL(_canonical_json(state.get("task_plan")))) != execution.task_plan_sha256
        or not isinstance(activation, Mapping)
        or activation.get("source_run_id") != execution.source_run_id
        or activation.get("source_state_version") != execution.source_state_version
        or activation.get("source_admission_sha256") != execution.source_admission_sha256
        or activation.get("intent_sha256") != execution.activation_intent_sha256
        or activation.get("plan_fingerprint") != execution.plan_fingerprint
        or activation.get("input_descriptor_sha256") != execution.input_descriptor_sha256
    ):
        raise ValueError("successor authority drifted")
    validate_active_run_control_entries(root)
    lock_bytes, _ = _READ_GUARDED(root, "runs/.active_run.lock")
    lock = validate_active_run_lock_snapshot(lock_bytes)
    if (
        lock.get("phase") != "running"
        or lock.get("run_id") != execution.successor_run_id
        or lock.get("reserved_run_id") != execution.successor_run_id
        or lock.get("allocation_token") != execution.allocation_token
        or lock.get("lock_token") != execution.lock_token
        or _SHA(lock_bytes) != execution.lock_sha256
    ):
        raise ValueError("running lock drifted")
    authority["lock_bytes"] = lock_bytes
    authority.update(
        {
            "source": source,
            "source_state_bytes": source_authority["state_bytes"],
            "source_journal_bytes": source_authority["journal_bytes"],
            "source_anchor_bytes": source_authority["anchor_bytes"],
            "view": view,
            "handoff_intent": handoff_intent,
            "handoff_bytes": handoff_bytes,
            "activation_bytes": activation_bytes,
            "activation_chain": activation_chain,
            "start": start,
            "start_bytes": start_bytes,
            "snapshots": snapshots,
            "snapshot_chain": snapshot_chain,
            "snapshot_bytes": snapshot_bytes,
            "tasks": tasks,
            "task_plan": task_plan,
        }
    )
    return authority


def _strict_predecessor(
    root: Path,
    execution: ResumeExecutionResult,
    *,
    allow_complete: bool = False,
) -> tuple[dict[str, Any], list[list[str]], int, tuple[str, ...]]:
    authority = _core_authority(root, execution)
    _validate_b9_lineage(root, execution, authority)
    layers = _EXECUTION_LAYERS(authority["tasks"])
    completed_layers, completed = _completed_prefix(authority["state"], layers, execution)
    _validate_progression_intents(
        root, execution, authority, layers, completed_layers
    )
    _validate_successor_entry_set(
        root, execution, authority, completed_layers
    )
    if completed_layers >= len(layers) and not allow_complete:
        raise ValueError("all required task layers are complete")
    return authority, layers, completed_layers, completed


class _FrozenAgent:
    def __init__(self, agent: Any, run: Any, helpers: Mapping[str, Any]) -> None:
        self.name = agent.name
        self._agent = agent
        self._run = run
        self._helpers = helpers

    def run(self, context: Mapping[str, Any]) -> dict[str, Any]:
        proxy = copy.copy(self._agent)
        for name, function in self._helpers.items():
            setattr(proxy, name, types.MethodType(function, proxy))
        return self._run(proxy, copy.deepcopy(dict(context)))


class _FrozenRegistry:
    def __init__(self, agents: Mapping[str, _FrozenAgent]) -> None:
        self._agents = dict(agents)

    def get(self, name: str) -> _FrozenAgent:
        if name not in self._agents:
            raise KeyError(name)
        return self._agents[name]

    def list(self) -> list[str]:
        return sorted(self._agents)


def _freeze_registry() -> _FrozenRegistry:
    registry = build_default_registry()
    helpers = {
        name: _copy_function(getattr(BaseAgent, name))
        for name in (
            "agent_inputs", "agent_outputs", "shared", "project_root",
            "resolve_path", "read_csv", "write_csv", "write_markdown",
        )
    }
    return _FrozenRegistry(
        {
            name: _FrozenAgent(registry.get(name), _copy_function(type(registry.get(name)).run), helpers)
            for name in registry.list()
        }
    )


_FROZEN_REGISTRY = _freeze_registry()


class _ProgressionSink:
    def __init__(self, controller: Any, canonical_tasks: Mapping[str, Any], fence: Any) -> None:
        self._controller = controller
        self._canonical_tasks = dict(canonical_tasks)
        self._fence = fence

    @property
    def run_id(self) -> str:
        return self._controller.run_id

    @property
    def snapshot(self) -> Mapping[str, Any]:
        return self._controller.snapshot

    async def prepare_execution(self, _tasks: Mapping[str, Any]) -> Any:
        self._fence()
        value = await self._controller.prepare_execution(self._canonical_tasks)
        self._fence()
        return value

    async def submit_checkpoint_event(self, event: Mapping[str, Any]) -> Any:
        self._fence()
        value = await self._controller.submit_checkpoint_event(event)
        self._fence()
        return value

    def project_executor_context(self, context: Mapping[str, Any]) -> dict[str, Any]:
        projected = self._controller.project_executor_context(context)

        def restore(value: Any) -> Any:
            if isinstance(value, Mapping):
                return {key: restore(item) for key, item in value.items()}
            if isinstance(value, list):
                return [restore(item) for item in value]
            if isinstance(value, tuple):
                return tuple(restore(item) for item in value)
            decoded = _B9_DECODE_WORKER_PATH(value) if type(value) is str else None
            return decoded if decoded is not None else value

        return restore(projected)


def _sealed_progression_sink_type() -> type:
    return type(
        "_SealedProgressionSink",
        (),
        {
            "__init__": _copy_function(_ProgressionSink.__init__),
            "run_id": property(_copy_function(_ProgressionSink.run_id.fget)),
            "snapshot": property(_copy_function(_ProgressionSink.snapshot.fget)),
            "prepare_execution": _copy_function(_ProgressionSink.prepare_execution),
            "submit_checkpoint_event": _copy_function(
                _ProgressionSink.submit_checkpoint_event
            ),
            "project_executor_context": _copy_function(
                _ProgressionSink.project_executor_context
            ),
        },
    )


def _boundary_snapshot(root: Path, successor_run_id: str) -> dict[str, Any]:
    source_run_id = _read_successor_authority(
        root, successor_run_id
    )["state"].get("context", {}).get("resume_activation", {}).get("source_run_id")
    if type(source_run_id) is not str or _RUN_ID_RE.fullmatch(source_run_id) is None:
        raise ValueError("source Run binding is invalid")
    return {
        "other_runs": _B9_OTHER_RUN_TREE(root, exclude_successor_run_id=successor_run_id),
        "project": _B9_DIRECT_TREE(root, exclude=frozenset({"runs", "outputs", "staging"})),
        "parent": _B9_DIRECT_TREE(root.parent, exclude=frozenset({root.name})),
        "outputs": _B9_EXTERNAL_TREE(root, "outputs"),
        "staging": _B9_EXTERNAL_TREE(root, "staging"),
        "source_tree": _run_tree_evidence(root, source_run_id),
    }


def _run_tree_evidence(root: Path, run_id: str) -> tuple[tuple[Any, ...], ...]:
    run = root / "runs" / run_id
    rows: list[tuple[Any, ...]] = []
    pending = [run]
    while pending:
        directory = pending.pop()
        directory_identity = _DIRECTORY_IDENTITY(directory)
        relative_directory = directory.relative_to(run).as_posix()
        rows.append(("directory", relative_directory, *directory_identity))
        for entry in directory.iterdir():
            state = entry.lstat()
            relative = entry.relative_to(run).as_posix()
            reparse = bool(
                getattr(state, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
            )
            if reparse or stat.S_ISLNK(state.st_mode):
                raise ValueError("source Run tree contains a link")
            if stat.S_ISDIR(state.st_mode):
                pending.append(entry)
                continue
            if not stat.S_ISREG(state.st_mode) or state.st_nlink != 1:
                raise ValueError("source Run tree contains an unsafe leaf")
            identity = _FILE_IDENTITY(entry)
            data, metadata = _READ_GUARDED(root, f"runs/{run_id}/{relative}")
            if _FILE_IDENTITY(entry) != identity:
                raise ValueError("source Run leaf identity changed")
            rows.append(
                ("file", relative, *identity, len(data), metadata["sha256"])
            )
    return tuple(sorted(rows))


def _assert_boundary(root: Path, successor_run_id: str, before: Mapping[str, Any]) -> None:
    if _boundary_snapshot(root, successor_run_id) != dict(before):
        raise ValueError("execution crossed the B.10 write boundary")


def _execute_layer_mutating(
    root: Path,
    execution: ResumeExecutionResult,
    authority: Mapping[str, Any],
    layers: list[list[str]],
    completed_layers: int,
    completed: tuple[str, ...],
    held_worker_bytes: Mapping[str, bytes],
) -> ResumeLayerProgressionResult:
    next_index = completed_layers + 1
    task_ids = tuple(sorted(layers[completed_layers]))
    predecessor_version = authority["state"]["state_version"]
    mutation_timestamp = authority["start"].get("mutation_timestamp")
    if type(mutation_timestamp) is not str:
        raise ValueError("B.9 timestamp is invalid")
    marker_bytes = _marker_bytes(execution, authority["source"])
    intent = _make_intent(
        execution,
        predecessor=authority,
        layer_index=next_index,
        task_ids=task_ids,
        completed_task_ids=completed,
        marker_bytes=marker_bytes,
        mutation_timestamp=mutation_timestamp,
    )
    intent_bytes = _CANONICAL(intent)
    successor = root / "runs" / execution.successor_run_id
    successor_chain = _DIRECTORY_CHAIN(root, successor, label="B.10 successor")
    boundary = _boundary_snapshot(root, execution.successor_run_id)
    intent_dir = successor / _INTENT_DIRECTORY
    if not intent_dir.exists():
        with _BIND_DIRECTORIES(successor_chain):
            _MAKE_DIRECTORY(root, f"runs/{execution.successor_run_id}/{_INTENT_DIRECTORY}")
    intent_chain = _DIRECTORY_CHAIN(root, intent_dir, label="B.10 progression intent")
    with _BIND_DIRECTORIES(intent_chain):
        _WRITE_EXCLUSIVE(
            root,
            f"runs/{execution.successor_run_id}/{_INTENT_DIRECTORY}/{_intent_name(next_index)}",
            intent_bytes,
        )
    current_intent, current_bytes = _READ_JSON(
        root,
        f"runs/{execution.successor_run_id}/{_INTENT_DIRECTORY}/{_intent_name(next_index)}",
    )
    _validate_intent(current_intent, current_bytes, intent)
    # The official A1 extension is definition-bound when this module loads.
    _INITIALIZE_SUCCESSOR_SANDBOX(
        root,
        successor_run_id=execution.successor_run_id,
        source_run_id=execution.source_run_id,
        execution_profile=_A1_PROFILE,
        evidence_source_mode=(
            "run_local_projection"
            if authority["source"]["context"]["workflow_input_mode"] == "prepared_dataset"
            else "normalized_records"
        ),
        source_admission_sha256=execution.source_admission_sha256,
        activation_intent_sha256=execution.activation_intent_sha256,
        plan_fingerprint=execution.plan_fingerprint,
    )

    def fence() -> None:
        _ASSERT_DIRECTORY_CHAIN(successor_chain, label="B.10 task fence")
        current = _core_authority(root, execution)
        current_value, current_data = _READ_JSON(
            root,
            f"runs/{execution.successor_run_id}/{_INTENT_DIRECTORY}/{_intent_name(next_index)}",
        )
        _validate_intent(current_value, current_data, intent)
        if current["start_bytes"] != authority["start_bytes"]:
            raise ValueError("B.9 start evidence changed")

    fence()
    controller = _CONTROLLER_TYPE(
        root,
        run_id=execution.successor_run_id,
        expected_lock_token=execution.lock_token,
        plan_fingerprint=execution.plan_fingerprint,
        resolved_input_descriptor_sha256=None,
        workflow_input_mode=authority["source"]["context"]["workflow_input_mode"],
        execution_profile=_b9._EXECUTION_PROFILE,
        resume=False,
        clock=lambda: mutation_timestamp,
    )
    controller._resolved_input_descriptor_sha256 = execution.input_descriptor_sha256
    sink = _ProgressionSink(controller, authority["tasks"], fence)
    execution_ids = {
        task_id for layer in layers[:next_index] for task_id in layer
    }
    execution_tasks = {
        task_id: authority["tasks"][task_id] for task_id in sorted(execution_ids)
    }
    context = _B9_SOURCE_CONTEXT(
        root,
        execution.successor_run_id,
        authority["source"],
        authority["view"],
        authority["snapshots"],
    )
    with _BIND_DIRECTORIES(successor_chain):
        if any(
            held_worker_bytes[item["snapshot_path"]]
            != authority["snapshot_bytes"][item["snapshot_path"]]
            for item in authority["snapshots"]
        ):
            raise ValueError("held progression inputs differ from authority")
        executor = _EXECUTOR_TYPE(
            _FROZEN_REGISTRY,
            root,
            resume=False,
            run_id=execution.successor_run_id,
            checkpoint_event_sink=sink,
        )
        executor.run(execution_tasks, context)
    fence()

    final, final_layers, final_count, final_completed = _strict_predecessor(
        root, execution, allow_complete=True
    )
    if (
        final_layers != layers
        or final_count != next_index
        or set(final_completed) != set(completed) | set(task_ids)
        or final["state"]["state_version"] != predecessor_version + 2 * len(task_ids)
    ):
        raise ValueError("progression did not commit exactly one layer")
    _assert_boundary(root, execution.successor_run_id, boundary)
    if next_index >= 2:
        _VALIDATE_COMPARISON_BUNDLE(
            root,
            run_id=execution.successor_run_id,
            execution_profile=_A1_PROFILE,
            plan_fingerprint=execution.plan_fingerprint,
        )
    validated_artifacts = _completed_artifact_evidence(
        root, execution, final, final_count
    )
    issuance, issuance_layers, issuance_count, issuance_completed = _strict_predecessor(
        root, execution, allow_complete=True
    )
    if (
        issuance_layers != final_layers
        or issuance_count != final_count
        or issuance_completed != final_completed
        or any(
            issuance[name] != final[name]
            for name in (
                "state_bytes",
                "journal_bytes",
                "anchor_bytes",
                "lock_bytes",
                "handoff_bytes",
                "activation_bytes",
                "start_bytes",
                "snapshot_bytes",
                "task_plan",
                "source_state_bytes",
                "source_journal_bytes",
                "source_anchor_bytes",
            )
        )
        or issuance["source"] != final["source"]
    ):
        raise ValueError("final progression authority changed before issuance")
    if next_index >= 2:
        _VALIDATE_COMPARISON_BUNDLE(
            root,
            run_id=execution.successor_run_id,
            execution_profile=_A1_PROFILE,
            plan_fingerprint=execution.plan_fingerprint,
        )
    if _completed_artifact_evidence(
        root, execution, issuance, issuance_count
    ) != validated_artifacts:
        raise ValueError("completed artifacts changed before issuance")
    final = issuance
    _assert_boundary(root, execution.successor_run_id, boundary)
    bindings = {
        "successor_run_id": execution.successor_run_id,
        "source_run_id": execution.source_run_id,
        "execution_sha256": execution.execution_sha256,
        "source_admission_sha256": execution.source_admission_sha256,
        "activation_sha256": execution.activation_sha256,
        "activation_intent_sha256": execution.activation_intent_sha256,
        "handoff_sha256": execution.handoff_sha256,
        "handoff_intent_sha256": execution.handoff_intent_sha256,
        "allocation_token": execution.allocation_token,
        "lock_token": execution.lock_token,
        "predecessor_state_version": predecessor_version,
        "state_version": final["state"]["state_version"],
        "layer_index": next_index,
        "plan_fingerprint": execution.plan_fingerprint,
        "input_descriptor_sha256": execution.input_descriptor_sha256,
        "task_plan_sha256": execution.task_plan_sha256,
        "progression_intent_sha256": _SHA(intent_bytes),
        "required_task_ids": list(execution.required_task_ids),
        "completed_task_ids": list(final_completed),
        "executed_task_ids": list(task_ids),
        "state_sha256": _SHA(final["state_bytes"]),
        "journal_sha256": _SHA(final["journal_bytes"]),
        "journal_anchor_sha256": _SHA(final["anchor_bytes"]),
        "lock_sha256": _SHA(final["lock_bytes"]),
        "source_state_sha256": _SHA(final["source_state_bytes"]),
        "source_journal_sha256": _SHA(final["source_journal_bytes"]),
        "source_journal_anchor_sha256": _SHA(final["source_anchor_bytes"]),
    }
    data = _CANONICAL(
        {"schema_version": RESUME_LAYER_PROGRESSION_SCHEMA_VERSION, "status": _PROGRESSED, **bindings}
    )
    return _issue(
        {
            "status": _PROGRESSED,
            "progression_bytes": data,
            "progression_sha256": _SHA(data),
            **bindings,
            "required_task_ids": tuple(execution.required_task_ids),
            "completed_task_ids": tuple(final_completed),
            "executed_task_ids": task_ids,
        }
    )


def _execute_layer(
    root: Path,
    execution: ResumeExecutionResult,
    authority: Mapping[str, Any],
    layers: list[list[str]],
    completed_layers: int,
    completed: tuple[str, ...],
) -> ResumeLayerProgressionResult:
    _expected, _committed, worker_descriptors = _worker_entry_authority(
        root, execution, authority, completed_layers
    )
    with _hold_readable_input_snapshots(root, worker_descriptors) as held_worker_bytes:
        return _execute_layer_mutating(
            root,
            execution,
            authority,
            layers,
            completed_layers,
            completed,
            held_worker_bytes,
        )


def _progress(
    root: Path, successor_run_id: str, execution: ResumeExecutionResult
) -> ResumeLayerProgressionResult:
    if type(successor_run_id) is not str or _RUN_ID_RE.fullmatch(successor_run_id) is None:
        raise ValueError("successor run id is invalid")
    if type(execution) is not ResumeExecutionResult:
        raise ValueError("B.9 execution result is not exact")
    _B9_RESULT_VALIDATE(execution)
    if (
        execution.status != _B9_EXECUTED
        or execution.successor_run_id != successor_run_id
    ):
        raise ValueError("B.9 execution result does not bind the successor")
    if os.name != "nt":
        raise ValueError(
            "B.10 task progression requires Windows deny-write/delete snapshot handles"
        )
    authority, layers, completed_layers, completed = _strict_predecessor(root, execution)
    return _execute_layer(
        root, execution, authority, layers, completed_layers, completed
    )


def _public(
    project_root: Path,
    *,
    successor_run_id: str,
    execution: ResumeExecutionResult,
) -> ResumeLayerProgressionResult:
    try:
        root = _CONTROLLED_ROOT(Path(project_root).absolute())
        return _progress(root, successor_run_id, execution)
    except BaseException:
        return _denied()


# Patched to the definition-bound official initializer immediately after the
# backward-compatible A1 extension is imported.  Keeping the name private also
# makes an unavailable authority a fail-closed import error during development.
_INITIALIZE_SUCCESSOR_SANDBOX = getattr(
    a1_artifacts, "initialize_phase_b10_successor_a1_sandbox", None
)

def _seal_progression_authority() -> tuple[Any, Any]:
    """Freeze the supported B.10 call graph and result issuers."""

    frozen = dict(globals())
    frozen["_ProgressionSink"] = _sealed_progression_sink_type()
    names = (
        "_canonical_json",
        "_read_successor_authority",
        "_journal_records",
        "_validate_b9_lineage",
        "_completed_prefix",
        "_intent_name",
        "_intent_checksum",
        "_dependency_checkpoint_binding",
        "_make_intent",
        "_validate_intent",
        "_marker_bytes",
        "_validate_progression_intents",
        "_expected_successor_entries",
        "_validate_successor_entry_set",
        "_hold_readable_input_snapshots",
        "_worker_entry_authority",
        "_completed_artifact_evidence",
        "_core_authority",
        "_strict_predecessor",
        "_run_tree_evidence",
        "_boundary_snapshot",
        "_assert_boundary",
        "_execute_layer_mutating",
        "_execute_layer",
        "_progress",
    )
    issue = _copy_function(_issue)
    frozen["_issue"] = issue
    denied = types.FunctionType(
        _denied.__code__, frozen, name=_denied.__name__, argdefs=_denied.__defaults__
    )
    denied.__kwdefaults__ = dict(_denied.__kwdefaults__ or {})
    frozen["_denied"] = denied
    copies: dict[str, Any] = {}
    for name in names:
        function = frozen[name]
        if name == "_hold_readable_input_snapshots":
            wrapped = function.__wrapped__
            copied_generator = types.FunctionType(
                wrapped.__code__,
                frozen,
                name=wrapped.__name__,
                argdefs=wrapped.__defaults__,
                closure=wrapped.__closure__,
            )
            copied_generator.__kwdefaults__ = dict(wrapped.__kwdefaults__ or {})
            copies[name] = contextmanager(copied_generator)
            continue
        copies[name] = types.FunctionType(
            function.__code__,
            frozen,
            name=function.__name__,
            argdefs=function.__defaults__,
            closure=function.__closure__,
        )
        copies[name].__kwdefaults__ = dict(function.__kwdefaults__ or {})
    frozen.update(copies)
    return copies["_progress"], denied


def _seal_public_progression(authority: Any, denied: Any, controlled_root: Any) -> Any:
    path_type = Path

    def public(
        project_root: Path,
        *,
        successor_run_id: str,
        execution: ResumeExecutionResult,
    ) -> ResumeLayerProgressionResult:
        try:
            root = controlled_root(path_type(project_root).absolute())
            return authority(root, successor_run_id, execution)
        except BaseException:
            return denied()

    return public


_SEALED_PROGRESS, _SEALED_DENIED = _seal_progression_authority()
progress_resume_layer = _seal_public_progression(
    _SEALED_PROGRESS, _SEALED_DENIED, _CONTROLLED_ROOT
)


def _unsupported_internal(*_args: Any, **_kwargs: Any) -> Any:
    raise TypeError("B.10 authority is internal")


_issue = _unsupported_internal
_denied = _unsupported_internal
_progress = _unsupported_internal
del _IS_OFFICIAL, _REGISTER, _DISCARD
del _new_registry, _seal_progression_authority, _seal_public_progression
del _SEALED_PROGRESS, _SEALED_DENIED


__all__ = [
    "RESUME_LAYER_PROGRESSION_SCHEMA_VERSION",
    "RESUME_LAYER_PROGRESSION_INTENT_SCHEMA_VERSION",
    "ResumeLayerProgressionResult",
    "progress_resume_layer",
]
