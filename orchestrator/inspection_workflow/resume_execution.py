"""Phase B.9 controlled Resume task execution.

The public boundary accepts only the canonical successor id and the exact
official B.8 handoff result.  It re-fences the archived B.8 evidence and the
current B.5 admission before the B.8 intent is used, then delegates every
State/Journal/task mutation to the existing managed Controller and
DAGExecutor.  Publication, cache reuse, task skipping and task selection are
outside this boundary.
"""

from __future__ import annotations

from contextlib import contextmanager
import csv
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import threading
import types
from typing import Any, Mapping
import uuid
import weakref

from . import a1_artifacts, controlled_fs
from .artifact_resolver import _read_guarded_file as _artifact_guarded_read
from .explicit_resume_activation import (
    ExplicitResumeActivation,
    _B1_READ_GUARDED,
    _RUN_ID_RE,
)
from .explicit_resume_admission import (
    ExplicitResumeAdmission,
    ExplicitResumeAdmissionResult,
)
from .locking import (
    validate_active_run_control_entries,
    validate_active_run_lock_snapshot,
)
from .planning import build_required_task_plan, task_plan_fingerprint
from .resume_execution_handoff import (
    ResumeExecutionHandoff,
    ResumeExecutionHandoffResult,
)
from orchestrator.dag.builder import build_dag
from orchestrator.dag.scheduler import execution_layers
from orchestrator.executor import DAGExecutor
from orchestrator.inspection_workflow.controller import InspectionWorkflowController
from orchestrator.inspection_workflow.lifecycle import (
    _DAG_CONFIG_PATH,
    _PHASE_A_EXECUTION_PROFILE,
    _managed_context,
)
from orchestrator.registry import build_default_registry
from orchestrator.state.store import StateStore


_SCHEMA = "inspection_resume_execution_v1"
_INTENT_SCHEMA = "inspection_resume_execution_intent_v1"
_EXECUTED = "resume_execution_executed"
_NOT_EXECUTED = "resume_execution_not_executed"
_SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
_SUCCESSOR_BEFORE_EXECUTION = frozenset(
    {
        "resume_execution_handoff.intent.json",
        "state.json",
        "state_journal.jsonl",
        "state_journal_tail.json",
    }
)
_START_INTENT_NAME = "resume_execution_start.intent.json"
_INPUT_SNAPSHOT_DIR = "work/resume_execution_input"


def _copy_function(function: Any) -> Any:
    """Copy code/defaults/globals at definition time for authority seams."""

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


def _freeze_method(method: Any) -> Any:
    if isinstance(method, types.MethodType):
        return types.MethodType(_copy_function(method), method.__self__)
    return _copy_function(method)


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _uuid(value: Any) -> bool:
    try:
        return type(value) is str and str(uuid.UUID(value)) == value
    except (AttributeError, ValueError):
        return False


def _denied_bytes() -> bytes:
    return _canonical({"schema_version": _SCHEMA, "status": _NOT_EXECUTED})


def _new_result_registry() -> tuple[Any, Any, Any]:
    """Create an issuer-owned registry, without exposing its mapping."""

    issued: weakref.WeakValueDictionary[int, Any] = weakref.WeakValueDictionary()

    def is_official(result: Any) -> bool:
        return issued.get(id(result)) is result

    def register(result: Any) -> None:
        issued[id(result)] = result

    def discard(result: Any) -> None:
        marker = id(result)
        if issued.get(marker) is result:
            issued.pop(marker, None)

    return is_official, register, discard


_RESULT_IS_OFFICIAL, _REGISTER_RESULT, _DISCARD_RESULT = _new_result_registry()


@dataclass(frozen=True, init=False, slots=True, weakref_slot=True)
class ResumeExecutionResult:
    """Official B.9 result; public construction is intentionally disabled."""

    status: str
    execution_bytes: bytes
    execution_sha256: str
    successor_run_id: str | None
    source_run_id: str | None
    handoff_sha256: str | None
    handoff_intent_sha256: str | None
    activation_sha256: str | None
    activation_intent_sha256: str | None
    source_admission_sha256: str | None
    source_state_version: int | None
    allocation_token: str | None
    lock_token: str | None
    state_version: int | None
    plan_fingerprint: str | None
    input_descriptor_sha256: str | None
    task_plan_sha256: str | None
    required_task_ids: tuple[str, ...]
    executed_task_ids: tuple[str, ...]
    start_intent_sha256: str | None
    state_sha256: str | None
    journal_sha256: str | None
    journal_anchor_sha256: str | None
    lock_sha256: str | None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("ResumeExecutionResult is issued only by the B.9 authority")

    def __post_init__(self) -> None:
        if not _RESULT_IS_OFFICIAL(self):
            raise ValueError("execution result was not issued by the B.9 authority")
        if (
            type(self.execution_bytes) is not bytes
            or _sha(self.execution_bytes) != self.execution_sha256
        ):
            raise ValueError("execution result bytes are invalid")
        bindings = _result_bindings(self)
        expected = _canonical(
            {"schema_version": _SCHEMA, "status": self.status}
            if self.status == _NOT_EXECUTED
            else {
                "schema_version": _SCHEMA,
                "status": self.status,
                **bindings,
            }
        )
        if self.execution_bytes != expected:
            raise ValueError("execution result bytes do not match fields")
        if self.status == _NOT_EXECUTED:
            if any(
                value is not None
                for key, value in bindings.items()
                if key not in {"required_task_ids", "executed_task_ids"}
            ):
                raise ValueError("denied execution result leaks bindings")
            if self.required_task_ids != () or self.executed_task_ids != ():
                raise ValueError("denied execution result leaks task ids")
            return
        if self.status != _EXECUTED:
            raise ValueError("execution result status is invalid")
        if (
            type(self.successor_run_id) is not str
            or _RUN_ID_RE.fullmatch(self.successor_run_id) is None
            or type(self.source_run_id) is not str
            or _RUN_ID_RE.fullmatch(self.source_run_id) is None
            or type(self.source_state_version) is not int
            or self.source_state_version < 0
            or type(self.state_version) is not int
            or self.state_version < 3
            or not _uuid(self.allocation_token)
            or not _uuid(self.lock_token)
            or not self.required_task_ids
            or tuple(sorted(set(self.required_task_ids))) != self.required_task_ids
            or not self.executed_task_ids
            or tuple(sorted(set(self.executed_task_ids))) != self.executed_task_ids
            or not set(self.executed_task_ids).issubset(set(self.required_task_ids))
            or any(
                type(getattr(self, name)) is not str
                or _SHA_RE.fullmatch(getattr(self, name)) is None
                for name in (
                    "handoff_sha256", "handoff_intent_sha256", "activation_sha256",
                    "activation_intent_sha256", "source_admission_sha256",
                    "plan_fingerprint", "input_descriptor_sha256", "task_plan_sha256",
                    "start_intent_sha256", "state_sha256", "journal_sha256",
                    "journal_anchor_sha256", "lock_sha256",
                )
            )
        ):
            raise ValueError("successful execution bindings are invalid")

    @property
    def resume_execution_executed(self) -> bool:
        try:
            ResumeExecutionResult._post_init_internal(self)
        except Exception:
            return False
        return self.status == ResumeExecutionResult._executed_internal


def _result_bindings(result: ResumeExecutionResult) -> dict[str, Any]:
    return {
        "successor_run_id": result.successor_run_id,
        "source_run_id": result.source_run_id,
        "handoff_sha256": result.handoff_sha256,
        "handoff_intent_sha256": result.handoff_intent_sha256,
        "activation_sha256": result.activation_sha256,
        "activation_intent_sha256": result.activation_intent_sha256,
        "source_admission_sha256": result.source_admission_sha256,
        "source_state_version": result.source_state_version,
        "allocation_token": result.allocation_token,
        "lock_token": result.lock_token,
        "state_version": result.state_version,
        "plan_fingerprint": result.plan_fingerprint,
        "input_descriptor_sha256": result.input_descriptor_sha256,
        "task_plan_sha256": result.task_plan_sha256,
        "required_task_ids": list(result.required_task_ids or ()),
        "executed_task_ids": list(result.executed_task_ids or ()),
        "start_intent_sha256": result.start_intent_sha256,
        "state_sha256": result.state_sha256,
        "journal_sha256": result.journal_sha256,
        "journal_anchor_sha256": result.journal_anchor_sha256,
        "lock_sha256": result.lock_sha256,
    }


def _new_result_issuer(register: Any, discard: Any, post_init: Any) -> Any:
    result_type = ResumeExecutionResult
    fields = tuple(result_type.__dataclass_fields__)
    object_new = object.__new__
    object_setattr = object.__setattr__

    def issue(values: Mapping[str, Any]) -> ResumeExecutionResult:
        result = object_new(result_type)
        for name in fields:
            object_setattr(
                result,
                name,
                values.get(
                    name,
                    () if name in {"required_task_ids", "executed_task_ids"} else None,
                ),
            )
        register(result)
        try:
            post_init(result)
        except Exception:
            discard(result)
            raise
        return result

    return issue


_RESULT_POST_INIT = _copy_function(ResumeExecutionResult.__post_init__)
ResumeExecutionResult.__post_init__ = _RESULT_POST_INIT
ResumeExecutionResult._is_official_internal = _RESULT_IS_OFFICIAL
ResumeExecutionResult._sha_internal = _sha
ResumeExecutionResult._bindings_internal = _result_bindings
ResumeExecutionResult._canonical_internal = _canonical
ResumeExecutionResult._schema_internal = _SCHEMA
ResumeExecutionResult._executed_internal = _EXECUTED
ResumeExecutionResult._not_executed_internal = _NOT_EXECUTED
ResumeExecutionResult._post_init_internal = _RESULT_POST_INIT
ResumeExecutionResult._run_id_re_internal = _RUN_ID_RE
ResumeExecutionResult._sha_re_internal = _SHA_RE
ResumeExecutionResult._uuid_internal = _uuid


def _sealed_success_property(post_init: Any, executed: str) -> property:
    """Bind success observation to the original official validator."""

    def successful(result: ResumeExecutionResult) -> bool:
        try:
            post_init(result)
        except Exception:
            return False
        return result.status == executed

    return property(successful)


ResumeExecutionResult.resume_execution_executed = _sealed_success_property(
    _RESULT_POST_INIT, _EXECUTED
)

_RESULT_ISSUE_AUTHORITY = _new_result_issuer(
    _REGISTER_RESULT,
    _DISCARD_RESULT,
    ResumeExecutionResult._post_init_internal,
)


def _new_denied_issuer(issue_result: Any) -> Any:
    data = _denied_bytes()
    sha256 = _sha

    def denied() -> ResumeExecutionResult:
        return issue_result(
            {
                "status": _NOT_EXECUTED,
                "execution_bytes": data,
                "execution_sha256": sha256(data),
            }
        )

    return denied


_DENIED_ISSUE_AUTHORITY = _new_denied_issuer(_RESULT_ISSUE_AUTHORITY)


def _issue(_values: Mapping[str, Any]) -> ResumeExecutionResult:
    """Unsupported visible compatibility surface; never an authority."""

    raise TypeError("execution result issuance is internal")


def _denied() -> ResumeExecutionResult:
    """Unsupported visible compatibility surface; never an authority."""

    raise TypeError("execution result issuance is internal")


# Capture B.8's core evidence at definition time.  The exact source admission
# SHA is carried by the official B.8 handoff; B.9 does not copy B.5's
# inventory rules or invent a second admission validator.
_B8_MODULE = __import__(
    "orchestrator.inspection_workflow.resume_execution_handoff",
    fromlist=["x"],
)
_B8_RESULT_TYPE = _B8_MODULE.ResumeExecutionHandoffResult
_B8_RESULT_VALIDATE = _freeze_method(_B8_RESULT_TYPE.__post_init__)
_B8_EVIDENCE = _freeze_method(_B8_MODULE.ResumeExecutionHandoff._evidence)
_B8_INTENT_VALIDATE = _freeze_method(_B8_MODULE.ResumeExecutionHandoff._validate_intent)
_B8_SOURCE_VALIDATE = _freeze_method(_B8_MODULE.ResumeExecutionHandoff._validate_source)
_B8_ACTIVATION_VIEW = _B8_MODULE._ActivationView
_B8_PREPARATION_VIEW = _B8_MODULE._PreparationView
_B8_CANONICAL = _B8_MODULE._canonical
_B8_SHA = _B8_MODULE._sha
_B8_RESULT_ISSUE = _B8_RESULT_TYPE._issue_internal
_B8_STATE_STORE = _B8_MODULE.StateStore
_B8_STATE_LOAD = _B8_MODULE.StateStore.load
_B8_READ = _B8_MODULE._B1_READ_GUARDED
_B8_ASSERT_CHAIN = _B8_MODULE.ExplicitResumeActivation._assert_directory_chain
_B8_ACTIVE_ENTRIES = _B8_MODULE.validate_active_run_control_entries
_B8_VALIDATE_LOCK = _B8_MODULE.validate_active_run_lock_snapshot
_B8_VALIDATE_SNAPSHOT = _B8_MODULE.StateStore.validate_authority_snapshot_bytes
_B8_SUCCESSOR_ENTRIES = _B8_MODULE._SUCCESSOR_ENTRIES
_B8_TYPE = _B8_MODULE.ResumeExecutionHandoff

_B5_TYPE = ExplicitResumeAdmission
_B5_ADMIT = _freeze_method(ExplicitResumeAdmission.admit)
_B5_RESULT_TYPE = ExplicitResumeAdmissionResult
_B5_RESULT_VALIDATE = _freeze_method(ExplicitResumeAdmissionResult.__post_init__)




_CONTROLLED_ROOT = a1_artifacts._controlled_temporary_root
_DIRECTORY_CHAIN = ExplicitResumeActivation._directory_chain
_ASSERT_DIRECTORY_CHAIN = ExplicitResumeActivation._assert_directory_chain
_READ_GUARDED = _B1_READ_GUARDED
_WRITE_EXCLUSIVE = controlled_fs.write_exclusive
_ATOMIC_REPLACE = controlled_fs.atomic_replace
_MAKE_DIRECTORY = controlled_fs.make_directory
_BIND_DIRECTORIES = controlled_fs.bind_directory_identities
_FILE_IDENTITY = controlled_fs.file_identity
_DIRECTORY_IDENTITY = controlled_fs.directory_identity
_WORKER_WRITE_GUARD_LOCK = threading.RLock()
_ARTIFACT_READ = _artifact_guarded_read
_STORE = StateStore
_VALIDATE_SNAPSHOT = _freeze_method(StateStore.validate_authority_snapshot_bytes)
_BUILD_DAG = build_dag
_BUILD_PLAN = build_required_task_plan
_PLAN_FINGERPRINT = task_plan_fingerprint
_BUILD_REGISTRY = build_default_registry
_EXECUTOR = DAGExecutor
_CONTROLLER = InspectionWorkflowController
_MANAGED_CONTEXT = _managed_context
_DAG_PATH = _DAG_CONFIG_PATH
_EXECUTION_PROFILE = _PHASE_A_EXECUTION_PROFILE


def _read_json_guarded(root: Path, relative: str) -> tuple[dict[str, Any], bytes]:
    data, _ = _READ_GUARDED(root, relative)
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict) or _canonical(value) != data:
        raise ValueError("authority document is not canonical")
    return value, data


def _b8_intent_validate(data: bytes, run_id: str, activation: Any, preparation: Any) -> dict[str, Any]:
    # The captured B.8 validator owns the archived intent schema and checksum.
    return _B8_INTENT_VALIDATE(data, run_id, activation, preparation)


def _b8_evidence(root: Path, intent: Mapping[str, Any], intent_bytes: bytes) -> dict[str, Any]:
    activation_view = _B8_ACTIVATION_VIEW(intent)
    preparation_view = _B8_PREPARATION_VIEW(intent)
    # B.8's evidence validator itself reconstructs these exact views and calls
    # the definition-time B.8 intent validator; the local views are used only
    # for the source binding check below.
    return _B8_EVIDENCE(
        root,
        intent,
        intent_bytes,
        _DIRECTORY_CHAIN(root, root / "runs" / intent["successor_run_id"], label="B.9 B.8 evidence"),
        _B8_STATE_STORE,
        _B8_STATE_LOAD,
        _B8_READ,
        _B8_ASSERT_CHAIN,
        _B8_ACTIVE_ENTRIES,
        _B8_VALIDATE_LOCK,
        _B8_VALIDATE_SNAPSHOT,
        _b8_intent_validate,
    )


def _validate_b8_current(
    root: Path, successor_run_id: str, handoff: ResumeExecutionHandoffResult
) -> tuple[dict[str, Any], dict[str, Any], bytes, dict[str, Any]]:
    if type(handoff) is not _B8_RESULT_TYPE:
        raise ValueError("B.8 handoff result is not exact")
    _B8_RESULT_VALIDATE(handoff)
    if handoff.status != "resume_execution_handed_off" or handoff.successor_run_id != successor_run_id:
        raise ValueError("B.8 handoff is not successful")
    relative = f"runs/{successor_run_id}/resume_execution_handoff.intent.json"
    intent, intent_bytes = _read_json_guarded(root, relative)
    if _sha(intent_bytes) != handoff.handoff_intent_sha256:
        raise ValueError("B.8 intent SHA drifted")
    preparation_view = _B8_PREPARATION_VIEW(intent)
    source = _B8_STATE_LOAD(_B8_STATE_STORE(root), run_id=intent["source_run_id"])["canonical_state"]
    _B8_SOURCE_VALIDATE(source, preparation_view)
    if source.get("state_version") != intent["source_state_version"]:
        raise ValueError("source State version drifted")
    first = _b8_evidence(root, intent, intent_bytes)
    second = _b8_evidence(root, intent, intent_bytes)
    if first != second:
        raise ValueError("B.8 evidence drifted")
    for name in ("state_sha256", "journal_sha256", "journal_anchor_sha256", "lock_sha256"):
        if first[name] != getattr(handoff, name):
            raise ValueError("B.8 handoff evidence does not match")
    return intent, source, intent_bytes, first


def _validate_b5_current(root: Path, handoff: ResumeExecutionHandoffResult) -> None:
    result = _B5_ADMIT(_B5_TYPE(root), run_id=handoff.source_run_id)
    if type(result) is not _B5_RESULT_TYPE:
        raise ValueError("B.5 admission result is not exact")
    _B5_RESULT_VALIDATE(result)
    if (
        result.status != "resume_admissible"
        or result.run_id != handoff.source_run_id
        or result.admission_sha256 != handoff.source_admission_sha256
    ):
        raise ValueError("B.5 admission is not current")


def _make_start_intent(
    handoff: ResumeExecutionHandoffResult,
    intent_timestamp: str,
    input_snapshots: tuple[Mapping[str, Any], ...],
    snapshot_directory_chain: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": _INTENT_SCHEMA,
        "successor_run_id": handoff.successor_run_id,
        "source_run_id": handoff.source_run_id,
        "handoff_sha256": handoff.handoff_sha256,
        "handoff_intent_sha256": handoff.handoff_intent_sha256,
        "activation_sha256": handoff.activation_sha256,
        "activation_intent_sha256": handoff.activation_intent_sha256,
        "source_admission_sha256": handoff.source_admission_sha256,
        "source_state_version": handoff.source_state_version,
        "allocation_token": handoff.allocation_token,
        "lock_token": handoff.lock_token,
        "predecessor_state_version": 1,
        "initialized_state_version": 2,
        "running_state_version": 3,
        "plan_fingerprint": handoff.plan_fingerprint,
        "input_descriptor_sha256": handoff.input_descriptor_sha256,
        "task_plan_sha256": handoff.task_plan_sha256,
        "required_task_ids": list(handoff.required_task_ids),
        "input_snapshots": [dict(item) for item in input_snapshots],
        "input_snapshot_directory_chain": [
            dict(item) for item in snapshot_directory_chain
        ],
        "mutation_timestamp": intent_timestamp,
        "intent_checksum": None,
    }
    value["intent_checksum"] = _sha(
        _canonical({name: item for name, item in value.items() if name != "intent_checksum"})
    )
    return value


def _validate_start_intent(
    value: Mapping[str, Any], data: bytes, handoff: ResumeExecutionHandoffResult,
    input_snapshots: tuple[Mapping[str, Any], ...],
    snapshot_directory_chain: tuple[Mapping[str, Any], ...],
) -> None:
    if not isinstance(value, Mapping) or _canonical(dict(value)) != data:
        raise ValueError("execution-start intent is not canonical")
    expected = _make_start_intent(
        handoff,
        value.get("mutation_timestamp"),
        input_snapshots,
        snapshot_directory_chain,
    )
    if dict(value) != expected:
        raise ValueError("execution-start intent bindings drifted")


def _canonical_task_plan(root: Path, source: Mapping[str, Any], handoff: ResumeExecutionHandoffResult):
    tasks, _ = _BUILD_DAG(_DAG_PATH, profile=_EXECUTION_PROFILE)
    task_plan = _BUILD_PLAN(tasks)
    descriptor_sha = handoff.input_descriptor_sha256
    fingerprint = _PLAN_FINGERPRINT(
        tasks,
        resolved_input_descriptor_sha256=descriptor_sha,
        execution_profile=_EXECUTION_PROFILE,
    )
    if fingerprint != handoff.plan_fingerprint:
        raise ValueError("authoritative DAG plan drifted")
    if _sha(_canonical(task_plan)) != handoff.task_plan_sha256:
        raise ValueError("authoritative task plan drifted")
    if tuple(item["task_id"] for item in task_plan) != handoff.required_task_ids:
        raise ValueError("authoritative required task ids drifted")
    if any(task.retries != 0 or task.cache is not False for task in tasks.values()):
        raise ValueError("B.9 canonical tasks must disable retry and cache")
    return tasks, task_plan


def _open_source_read_guard(path: Path):
    """Open one source object while denying concurrent write/delete on Windows."""

    if os.name != "nt":
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        return os.fdopen(os.open(path, flags), "rb", buffering=0)
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        0x80000000,  # GENERIC_READ
        0x00000001,  # FILE_SHARE_READ; deny write and delete while captured
        None,
        3,  # OPEN_EXISTING
        0x00200000,  # FILE_FLAG_OPEN_REPARSE_POINT
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle in (None, invalid):
        raise OSError(ctypes.get_last_error(), "unable to open guarded source object")
    try:
        descriptor = msvcrt.open_osfhandle(int(handle), os.O_RDONLY)
    except BaseException:
        kernel32.CloseHandle(handle)
        raise
    return os.fdopen(descriptor, "rb", buffering=0)


def _held_source_snapshot_bytes(
    root: Path,
    source_paths: tuple[str, ...],
    handoff: ResumeExecutionHandoffResult,
    _open_guard: Any = _open_source_read_guard,
    _file_identity: Any = _FILE_IDENTITY,
    _b5_validate: Any = _validate_b5_current,
):
    @contextmanager
    def held():
        handles: list[Any] = []
        captured: dict[str, bytes] = {}
        primary: BaseException | None = None
        try:
            for relative in source_paths:
                path = root.joinpath(*relative.split("/"))
                before_identity = _file_identity(path)
                handle = _open_guard(path)
                handles.append(handle)
                data = handle.read()
                after_identity = _file_identity(path)
                if before_identity != after_identity:
                    raise ValueError("source execution input identity changed")
                captured[relative] = data
            _b5_validate(root, handoff)
            yield types.MappingProxyType(captured)
        except BaseException as exc:
            primary = exc
            raise
        finally:
            cleanup_errors: list[BaseException] = []
            for handle in reversed(handles):
                try:
                    handle.close()
                except BaseException as exc:
                    cleanup_errors.append(exc)
            if primary is None and cleanup_errors:
                raise cleanup_errors[0]

    return held()


def _source_snapshot_spec(
    root: Path, source: Mapping[str, Any], handoff: ResumeExecutionHandoffResult
) -> tuple[tuple[dict[str, Any], bytes], ...]:
    context = source.get("context")
    descriptor = context.get("resolved_input_descriptor") if isinstance(context, Mapping) else None
    artifacts = descriptor.get("source_artifacts") if isinstance(descriptor, Mapping) else None
    if not isinstance(artifacts, (list, tuple)):
        raise ValueError("source descriptor artifacts are invalid")
    by_name = {
        Path(item["path"]).name: item["path"]
        for item in artifacts
        if isinstance(item, Mapping) and type(item.get("path")) is str
    }
    mode = context.get("workflow_input_mode")
    names = (
        ("frame_records.csv", "preparation_manifest.json")
        if mode == "prepared_dataset"
        else ("robot_kict_frame_records.csv",)
    )
    expected = (
        {
            "frame_records.csv": f"runs/{handoff.source_run_id}/work/raw_prepared/frame_records.csv",
            "preparation_manifest.json": f"runs/{handoff.source_run_id}/work/raw_prepared/preparation_manifest.json",
        }
        if mode == "prepared_dataset"
        else {
            "robot_kict_frame_records.csv": (
                f"runs/{handoff.source_run_id}/work/legacy_simulated/robot_kict_frame_records.csv"
            )
        }
    )
    source_paths: list[str] = []
    for name in names:
        source_path = by_name.get(name)
        if type(source_path) is not str:
            raise ValueError("source execution input is incomplete")
        if source_path != expected[name]:
            raise ValueError("source execution input path is not canonical")
        source_paths.append(source_path)
    result: list[tuple[dict[str, Any], bytes]] = []
    with _held_source_snapshot_bytes(root, tuple(source_paths), handoff) as held:
        for name, source_path in zip(names, source_paths):
            data, snapshot = _ARTIFACT_READ(
                root,
                source_path,
                run_id=handoff.source_run_id,
            )
            if data != held[source_path]:
                raise ValueError("guarded source bytes do not match held source object")
            target_name = "frame_records.csv" if name == "robot_kict_frame_records.csv" else name
            result.append(
                (
                    {
                        "source_path": source_path,
                        "snapshot_path": (
                            f"runs/{handoff.successor_run_id}/{_INPUT_SNAPSHOT_DIR}/{target_name}"
                        ),
                        "size_bytes": snapshot["size_bytes"],
                        "sha256": snapshot["sha256"],
                    },
                    data,
                )
            )
    return tuple(result)


def _validate_input_snapshots(
    root: Path,
    successor_run_id: str,
    input_snapshots: tuple[Mapping[str, Any], ...],
    snapshot_directory_chain: tuple[Mapping[str, Any], ...],
    bound_snapshot_bytes: Mapping[str, bytes] | None = None,
    held_snapshot_objects: Mapping[str, Any] | None = None,
) -> Mapping[str, bytes]:
    current_chain = _DIRECTORY_CHAIN(
        root,
        root / "runs" / successor_run_id / _INPUT_SNAPSHOT_DIR,
        label="B.9 execution snapshot",
    )
    if _directory_chain_binding(root, current_chain) != snapshot_directory_chain:
        raise ValueError("execution input snapshot directory changed")
    expected_names = {Path(item["snapshot_path"]).name for item in input_snapshots}
    snapshot_dir = root / "runs" / successor_run_id / _INPUT_SNAPSHOT_DIR
    if {entry.name for entry in snapshot_dir.iterdir()} != expected_names:
        raise ValueError("execution input snapshot set changed")
    captured: dict[str, bytes] = {}
    for item in input_snapshots:
        path = root.joinpath(*item["snapshot_path"].split("/"))
        if held_snapshot_objects is not None:
            handle = held_snapshot_objects[item["snapshot_path"]]
            handle.seek(0)
            before = os.fstat(handle.fileno())
            data = handle.read()
            after = os.fstat(handle.fileno())
            path_state = path.lstat()
            expected_identity = (item["device"], item["inode"])
            before_handle_identity = (
                before.st_ino == item["inode"]
                and (os.name == "nt" or before.st_dev == item["device"])
            )
            after_handle_identity = (
                after.st_ino == item["inode"]
                and (os.name == "nt" or after.st_dev == item["device"])
            )
            if (
                not stat.S_ISREG(before.st_mode)
                or not stat.S_ISREG(after.st_mode)
                or not stat.S_ISREG(path_state.st_mode)
                or before.st_nlink != 1
                or after.st_nlink != 1
                or path_state.st_nlink != 1
                or not before_handle_identity
                or not after_handle_identity
                or _FILE_IDENTITY(path) != expected_identity
                or before.st_size != item["size_bytes"]
                or after.st_size != item["size_bytes"]
                or len(data) != item["size_bytes"]
                or _sha(data) != item["sha256"]
                or bound_snapshot_bytes is None
                or data != bound_snapshot_bytes[item["snapshot_path"]]
            ):
                raise ValueError("held execution input snapshot changed")
            captured[item["snapshot_path"]] = data
            continue
        if bound_snapshot_bytes is not None:
            data = bound_snapshot_bytes[item["snapshot_path"]]
            state = path.lstat()
            if (
                not stat.S_ISREG(state.st_mode)
                or state.st_nlink != 1
                or state.st_size != item["size_bytes"]
                or len(data) != item["size_bytes"]
                or _sha(data) != item["sha256"]
                or _FILE_IDENTITY(path) != (item["device"], item["inode"])
            ):
                raise ValueError("execution input snapshot changed")
            captured[item["snapshot_path"]] = data
            continue
        data, snapshot = _ARTIFACT_READ(
            root, item["snapshot_path"], run_id=successor_run_id
        )
        path_state = path.lstat()
        if (
            not stat.S_ISREG(path_state.st_mode)
            or path_state.st_nlink != 1
            or snapshot["size_bytes"] != item["size_bytes"]
            or snapshot["sha256"] != item["sha256"]
            or _sha(data) != item["sha256"]
            or _FILE_IDENTITY(path) != (item["device"], item["inode"])
        ):
            raise ValueError("execution input snapshot changed")
        captured[item["snapshot_path"]] = data
    return types.MappingProxyType(captured)


def _directory_chain_binding(
    root: Path, chain: tuple[tuple[Path, int, int], ...]
) -> tuple[Mapping[str, Any], ...]:
    result: list[Mapping[str, Any]] = []
    for path, device, inode in chain:
        relative = path.relative_to(root).as_posix()
        result.append(
            types.MappingProxyType(
                {"path": relative if relative != "." else ".", "device": device, "inode": inode}
            )
        )
    return tuple(result)


def _csv_rows_from_bytes(data: bytes) -> list[dict[str, str]]:
    text = data.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text, newline="")))


def _bind_worker_snapshot_bytes(
    registry: Any,
    root: Path,
    input_snapshots: tuple[Mapping[str, Any], ...],
    snapshot_bytes: Mapping[str, bytes],
) -> None:
    agent = registry.get("association")
    original_read_csv = agent.read_csv
    bound: dict[Path, bytes] = {
        root.joinpath(*relative.split("/")): bytes(data)
        for relative, data in snapshot_bytes.items()
    }

    def read_csv(path: Path) -> list[dict[str, str]]:
        candidate = Path(path).absolute()
        data = bound.get(candidate)
        if data is not None:
            return _csv_rows_from_bytes(data)
        return original_read_csv(path)

    agent.read_csv = read_csv


@contextmanager
def _hold_snapshot_objects(
    root: Path,
    input_snapshots: tuple[Mapping[str, Any], ...],
    _file_identity: Any = _FILE_IDENTITY,
    _os: Any = os,
    _stat: Any = stat,
    _sha256: Any = _sha,
    _mapping_proxy: Any = types.MappingProxyType,
):
    handles: list[tuple[Any, int, bool]] = []
    held: dict[str, Any] = {}
    primary: BaseException | None = None
    try:
        for item in input_snapshots:
            path = root.joinpath(*item["snapshot_path"].split("/"))
            handle = path.open("r+b", buffering=0)
            locked = False
            try:
                state = _os.fstat(handle.fileno())
                if (
                    not _stat.S_ISREG(state.st_mode)
                    or state.st_nlink != 1
                    or state.st_size != item["size_bytes"]
                    or _file_identity(path) != (item["device"], item["inode"])
                ):
                    raise ValueError("execution input snapshot object changed")
                handle.seek(0)
                data = handle.read()
                if len(data) != item["size_bytes"] or _sha256(data) != item["sha256"]:
                    raise ValueError("execution input snapshot bytes changed")
                lock_size = max(1, state.st_size)
                if _os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, lock_size)
                    locked = True
            except BaseException:
                handle.close()
                raise
            handles.append((handle, lock_size, locked))
            held[item["snapshot_path"]] = handle
        yield _mapping_proxy(held)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        cleanup_errors: list[BaseException] = []
        for handle, lock_size, locked in reversed(handles):
            try:
                if locked:
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, lock_size)
            except BaseException as exc:
                cleanup_errors.append(exc)
            finally:
                try:
                    handle.close()
                except BaseException as exc:
                    cleanup_errors.append(exc)
        if primary is None and cleanup_errors:
            raise cleanup_errors[0]


@contextmanager
def _guard_worker_work_writes(
    root: Path,
    successor_run_id: str,
    expected_work_entries: frozenset[str],
    input_snapshots: tuple[Mapping[str, Any], ...],
    _path_open: Any = Path.open,
    _directory_chain: Any = _DIRECTORY_CHAIN,
    _directory_identity: Any = _DIRECTORY_IDENTITY,
    _file_identity: Any = _FILE_IDENTITY,
    _write_exclusive: Any = _WRITE_EXCLUSIVE,
    _atomic_replace: Any = _ATOMIC_REPLACE,
    _make_directory: Any = _MAKE_DIRECTORY,
    _bind_directories: Any = _BIND_DIRECTORIES,
    _path_type: Any = Path,
    _guard_lock: Any = _WORKER_WRITE_GUARD_LOCK,
    _hash_bytes: Any = _sha,
):
    """Buffer official worker output and publish only through controlled FS."""

    successor = root / "runs" / successor_run_id
    immutable = frozenset(
        item["snapshot_path"].split(f"runs/{successor_run_id}/", 1)[1]
        for item in input_snapshots
    )
    directory_entries = frozenset(
        entry
        for entry in expected_work_entries
        if any(other.startswith(entry + "/") for other in expected_work_entries)
    )
    output_leaves = expected_work_entries - directory_entries - immutable
    buffered: dict[str, bytes] = {}
    owned: dict[str, tuple[int, int]] = {}
    parent_identities: dict[str, tuple[int, int] | None] = {}
    original_project_entries = frozenset(
        path.relative_to(root).as_posix()
        for path in root.iterdir()
        if path.name not in {"runs", "outputs", "staging"}
    )
    previous_open: Any = None

    dynamic_directories = frozenset(
        entry
        for entry in directory_entries
        if entry.startswith("work/raw_history/main_progressive/round_")
    )
    stable_directories = directory_entries - dynamic_directories
    for relative in sorted(stable_directories, key=lambda value: (value.count("/"), value)):
        directory = successor.joinpath(*relative.split("/"))
        try:
            directory.lstat()
        except FileNotFoundError:
            parent_chain = _directory_chain(
                root, directory.parent, label="B.9 worker output parent"
            )
            with _bind_directories(parent_chain):
                _make_directory(root, directory.relative_to(root).as_posix())
    for relative in stable_directories:
        directory = successor.joinpath(*relative.split("/"))
        parent_identities[relative] = _directory_identity(directory)

    def save(relative: str, data: bytes) -> None:
        path = successor.joinpath(*relative.split("/"))
        parent_key = path.parent.relative_to(successor).as_posix()
        current_parent_identity = _directory_identity(path.parent)
        if parent_identities[parent_key] is None:
            parent_identities[parent_key] = current_parent_identity
        elif current_parent_identity != parent_identities[parent_key]:
            raise ValueError("worker output parent directory changed")
        chain = _directory_chain(root, path.parent, label="B.9 worker output leaf")
        try:
            state = path.lstat()
        except FileNotFoundError:
            if relative in owned:
                raise ValueError("worker output leaf disappeared")
            with _bind_directories(chain):
                identity = _write_exclusive(
                    root, path.relative_to(root).as_posix(), data
                )
        else:
            if (
                not stat.S_ISREG(state.st_mode)
                or state.st_nlink != 1
                or relative not in owned
                or _file_identity(path) != owned[relative]
            ):
                raise ValueError("worker output leaf was replaced or linked")
            with _bind_directories(chain):
                identity = _atomic_replace(
                    root, path.relative_to(root).as_posix(), data
                )
        owned[relative] = identity
        buffered[relative] = data

    class _CapturedBytes(io.BytesIO):
        def __init__(self, initial: bytes, save: Any) -> None:
            super().__init__(initial)
            self._save = save

        def close(self) -> None:
            if not self.closed:
                self._save(self.getvalue())
            super().close()

    class _CapturedText(io.StringIO):
        def __init__(self, initial: str, save: Any, newline: str | None) -> None:
            super().__init__(initial, newline=newline)
            self._save = save

        def close(self) -> None:
            if not self.closed:
                self._save(self.getvalue())
            super().close()

    def guarded_open(
        path_object: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ):
        path = _path_type(path_object).absolute()
        writing = any(flag in mode for flag in "wax+")
        try:
            relative = path.relative_to(successor).as_posix()
        except ValueError:
            if writing:
                raise ValueError("worker write is outside the authorized successor work set")
            return previous_open(path_object, mode, buffering, encoding, errors, newline)
        if not writing and relative not in buffered:
            return previous_open(path_object, mode, buffering, encoding, errors, newline)
        if not relative.startswith("work/") or relative not in output_leaves:
            raise ValueError("worker write leaf is not authorized")
        binary = "b" in mode
        append = "a" in mode
        exclusive = "x" in mode
        if exclusive and relative in buffered:
            raise FileExistsError(path)
        initial = buffered.get(relative, b"") if append or not writing else b""
        selected_encoding = encoding or "utf-8"
        if not writing:
            if binary:
                return io.BytesIO(initial)
            return io.StringIO(
                initial.decode(selected_encoding, errors or "strict"), newline=newline
            )
        if binary:
            handle = _CapturedBytes(initial, lambda data: save(relative, data))
        else:
            text = initial.decode(selected_encoding, errors or "strict")
            handle = _CapturedText(
                text,
                lambda value: save(
                    relative, value.encode(selected_encoding, errors or "strict")
                ),
                newline,
            )
        if append:
            handle.seek(0, io.SEEK_END)
        return handle

    def guarded_mkdir(
        path_object: Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        path = _path_type(path_object).absolute()
        try:
            relative = path.relative_to(successor).as_posix()
        except ValueError:
            if not path.exists():
                raise ValueError("worker directory is outside the authorized successor work set")
            return previous_mkdir(path_object, mode=mode, parents=parents, exist_ok=exist_ok)
        if relative not in directory_entries:
            raise ValueError("worker output directory is not authorized")
        if relative in parent_identities:
            if _directory_identity(path) != parent_identities[relative]:
                raise ValueError("worker output directory changed")
            if exist_ok:
                return None
            raise FileExistsError(path)
        parent_key = path.parent.relative_to(successor).as_posix()
        expected_parent = parent_identities.get(parent_key)
        if expected_parent is None or _directory_identity(path.parent) != expected_parent:
            raise ValueError("worker output parent directory changed")
        chain = _directory_chain(root, path.parent, label="B.9 worker directory")
        with _bind_directories(chain):
            _make_directory(root, path.relative_to(root).as_posix())
        parent_identities[relative] = _directory_identity(path)
        return None

    def publish() -> Mapping[str, tuple[tuple[Any, ...], ...]]:
        if frozenset(buffered) != output_leaves:
            raise ValueError(
                f"worker output set is incomplete: missing={sorted(output_leaves - frozenset(buffered))} "
                f"extra={sorted(frozenset(buffered) - output_leaves)}"
            )
        for relative, identity in owned.items():
            path = successor.joinpath(*relative.split("/"))
            state = path.lstat()
            if (
                not stat.S_ISREG(state.st_mode)
                or state.st_nlink != 1
                or _file_identity(path) != identity
            ):
                raise ValueError("worker output leaf changed before publication fence")
        if frozenset(
            path.relative_to(root).as_posix()
            for path in root.iterdir()
            if path.name not in {"runs", "outputs", "staging"}
        ) != original_project_entries:
            raise ValueError("worker changed an unauthorized project-root entry")
        return {
            "files": tuple(
                sorted(
                    (
                        relative,
                        identity[0],
                        identity[1],
                        len(buffered[relative]),
                        _hash_bytes(buffered[relative]),
                    )
                    for relative, identity in owned.items()
                )
            ),
            "directories": tuple(
                sorted(
                    (relative, identity[0], identity[1])
                    for relative, identity in parent_identities.items()
                    if identity is not None
                )
            ),
        }

    with _guard_lock:
        previous_open = _path_type.open
        previous_mkdir = _path_type.mkdir
        _path_type.open = guarded_open
        _path_type.mkdir = guarded_mkdir
        try:
            yield publish
        finally:
            _path_type.open = previous_open
            _path_type.mkdir = previous_mkdir


def _entry_exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ValueError("unable to inspect forbidden execution residue") from exc


def _expected_work_entries(
    successor_run_id: str,
    input_snapshots: tuple[Mapping[str, Any], ...],
    snapshot_bytes: Mapping[str, bytes],
) -> frozenset[str]:
    frame_item = next(
        (
            item
            for item in input_snapshots
            if Path(item["snapshot_path"]).name == "frame_records.csv"
        ),
        None,
    )
    if frame_item is None:
        raise ValueError("execution frame snapshot is missing")
    rows = _csv_rows_from_bytes(snapshot_bytes[frame_item["snapshot_path"]])
    inspections = sorted(
        {row.get("inspection_id", "") for row in rows},
        key=lambda value: (
            int("".join(char for char in value if char.isdigit()) or "0"), value
        ),
    )
    if not inspections or "" in inspections:
        raise ValueError("execution frame snapshot is invalid")
    entries = {
        _INPUT_SNAPSHOT_DIR,
        "work/raw_history",
        "work/raw_history/association_manifest.json",
        "work/raw_history/association_records.csv",
        "work/raw_history/main_progressive",
    }
    entries.update(
        str(item["snapshot_path"]).split(f"runs/{successor_run_id}/", 1)[1]
        for item in input_snapshots
    )
    history_files = {
        "association_records.csv",
        "history_engineering_report.csv",
        "history_frames.csv",
        "history_growth_analysis.csv",
        "memory_after_query_report.md",
        "memory_after_query.csv",
        "memory_after_query.log",
        "memory_before_query_report.md",
        "memory_before_query_summary.md",
        "memory_before_query.csv",
        "memory_before_query.log",
    }
    for index, _inspection in enumerate(inspections, start=1):
        prefix = f"work/raw_history/main_progressive/round_{index:03d}"
        entries.add(prefix)
        entries.add(f"{prefix}/query_frames.csv")
        if index > 1:
            entries.update(f"{prefix}/{name}" for name in history_files)
    return frozenset(entries)


def _execution_work_entries(root: Path, successor_run_id: str) -> frozenset[str]:
    successor = root / "runs" / successor_run_id
    work = successor / "work"
    result: set[str] = set()
    pending = [work]
    while pending:
        directory = pending.pop()
        _DIRECTORY_IDENTITY(directory)
        for entry in directory.iterdir():
            relative = entry.relative_to(successor).as_posix()
            state = entry.lstat()
            if stat.S_ISLNK(state.st_mode):
                raise ValueError("execution work contains a link")
            if stat.S_ISDIR(state.st_mode):
                _DIRECTORY_IDENTITY(entry)
                result.add(relative)
                pending.append(entry)
            elif stat.S_ISREG(state.st_mode):
                if state.st_nlink != 1:
                    raise ValueError("execution work contains a hard link")
                _FILE_IDENTITY(entry)
                result.add(relative)
            else:
                raise ValueError("execution work contains an unsupported entry")
    return frozenset(result)


def _external_tree_snapshot(root: Path, name: str) -> tuple[tuple[Any, ...], ...]:
    target = root / name
    if not _entry_exists(target):
        return ()
    result: list[tuple[Any, ...]] = []
    pending = [target]
    while pending:
        directory = pending.pop()
        device, inode = _DIRECTORY_IDENTITY(directory)
        relative_dir = directory.relative_to(root).as_posix()
        result.append(("directory", relative_dir, device, inode))
        for entry in directory.iterdir():
            state = entry.lstat()
            if stat.S_ISLNK(state.st_mode):
                raise ValueError("formal tree contains a link")
            if stat.S_ISDIR(state.st_mode):
                pending.append(entry)
            elif stat.S_ISREG(state.st_mode):
                identity = _FILE_IDENTITY(entry)
                with entry.open("rb") as handle:
                    before = os.fstat(handle.fileno())
                    data = handle.read()
                    after = os.fstat(handle.fileno())
                before_key = (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                )
                after_key = (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                )
                if before_key != after_key:
                    raise ValueError("formal tree file changed during snapshot")
                result.append(
                    (
                        "file",
                        entry.relative_to(root).as_posix(),
                        identity[0],
                        identity[1],
                        len(data),
                        _sha(data),
                    )
                )
            else:
                raise ValueError("formal tree contains an unsupported entry")
    return tuple(sorted(result))


def _validate_execution_write_set(
    root: Path,
    successor_run_id: str,
    expected_work_entries: frozenset[str],
    formal_tree_evidence: Mapping[str, tuple[tuple[Any, ...], ...]],
    worker_write_evidence: Mapping[str, tuple[tuple[Any, ...], ...]],
) -> None:
    if _execution_work_entries(root, successor_run_id) != expected_work_entries:
        raise ValueError("execution work set changed")
    successor = root / "runs" / successor_run_id
    files = worker_write_evidence.get("files")
    directories = worker_write_evidence.get("directories")
    if type(files) is not tuple or type(directories) is not tuple:
        raise ValueError("worker write evidence is invalid")
    evidenced_paths: set[str] = set()
    for row in files:
        if type(row) is not tuple or len(row) != 5 or type(row[0]) is not str:
            raise ValueError("worker file evidence is invalid")
        relative, device, inode, expected_size, expected_sha = row
        path = successor.joinpath(*relative.split("/"))
        state = path.lstat()
        data, snapshot = _ARTIFACT_READ(root, path.relative_to(root).as_posix())
        if (
            not stat.S_ISREG(state.st_mode)
            or state.st_nlink != 1
            or _FILE_IDENTITY(path) != (device, inode)
            or _FILE_IDENTITY(path) != (device, inode)
            or snapshot["size_bytes"] != expected_size
            or len(data) != expected_size
            or _sha(data) != expected_sha
        ):
            raise ValueError("worker output leaf changed")
        evidenced_paths.add(relative)
    for row in directories:
        if type(row) is not tuple or len(row) != 3 or type(row[0]) is not str:
            raise ValueError("worker directory evidence is invalid")
        relative, device, inode = row
        path = successor.joinpath(*relative.split("/"))
        if _DIRECTORY_IDENTITY(path) != (device, inode):
            raise ValueError("worker output directory changed")
        evidenced_paths.add(relative)
    snapshot_paths = {
        entry for entry in expected_work_entries if entry.startswith(_INPUT_SNAPSHOT_DIR + "/")
    }
    if evidenced_paths | snapshot_paths != set(expected_work_entries):
        raise ValueError("worker write evidence is incomplete")
    if any(
        _external_tree_snapshot(root, name) != evidence
        for name, evidence in formal_tree_evidence.items()
    ):
        raise ValueError("formal output or staging tree changed")


def _source_input_context(
    root: Path,
    successor_run_id: str,
    source: Mapping[str, Any],
    handoff: ResumeExecutionHandoffResult,
    input_snapshots: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    context = source.get("context")
    if not isinstance(context, Mapping):
        raise ValueError("source context is invalid")
    input_mode = context.get("workflow_input_mode")
    if input_mode not in {"prepared_dataset", "legacy_simulated"}:
        raise ValueError("source input mode is invalid")
    result = _MANAGED_CONTEXT(
        root,
        run_id=successor_run_id,
        plan_fingerprint=handoff.plan_fingerprint,
        input_mode=input_mode,
    )
    paths = {Path(item["snapshot_path"]).name: item["snapshot_path"] for item in input_snapshots}
    if input_mode == "prepared_dataset":
        frame = paths.get("frame_records.csv")
        manifest = paths.get("preparation_manifest.json")
        if not frame or not manifest:
            raise ValueError("source prepared input set is incomplete")
        expected_prefix = f"runs/{successor_run_id}/{_INPUT_SNAPSHOT_DIR}/"
        if (
            frame != expected_prefix + "frame_records.csv"
            or manifest != expected_prefix + "preparation_manifest.json"
        ):
            raise ValueError("source input path is outside the canonical Run domain")
        result["inputs"]["association"]["frame_records"] = frame
        result["inputs"]["comparison_evidence"]["prepared_manifest_path"] = manifest
    else:
        legacy = paths.get("frame_records.csv")
        expected_legacy = (
            f"runs/{successor_run_id}/{_INPUT_SNAPSHOT_DIR}/frame_records.csv"
        )
        if legacy != expected_legacy:
            raise ValueError("source legacy input path is outside the canonical Run domain")
        result["inputs"]["association"]["frame_records"] = legacy
    return result


def _validate_running_fence(
    root: Path,
    successor_run_id: str,
    source: Mapping[str, Any],
    handoff: ResumeExecutionHandoffResult,
    b8_intent: Mapping[str, Any],
    start_intent: Mapping[str, Any],
    start_bytes: bytes,
    input_snapshots: tuple[Mapping[str, Any], ...],
    snapshot_directory_chain: tuple[Mapping[str, Any], ...],
    successor_chain: tuple[Any, ...],
    *,
    check_admission: bool = False,
    bound_snapshot_bytes: Mapping[str, bytes] | None = None,
    held_snapshot_objects: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    current_intent, current_bytes = _read_json_guarded(
        root, f"runs/{successor_run_id}/{_START_INTENT_NAME}"
    )
    _ASSERT_DIRECTORY_CHAIN(successor_chain, label="B.9 running fence")
    if successor_chain != _DIRECTORY_CHAIN(
        root, root / "runs" / successor_run_id, label="B.9 running fence"
    ):
        raise ValueError("successor directory identity changed")
    _validate_start_intent(
        current_intent,
        current_bytes,
        handoff,
        input_snapshots,
        snapshot_directory_chain,
    )
    if current_bytes != start_bytes:
        raise ValueError("execution-start intent changed")
    _validate_input_snapshots(
        root,
        successor_run_id,
        input_snapshots,
        snapshot_directory_chain,
        bound_snapshot_bytes,
        held_snapshot_objects,
    )
    current_source = _B8_STATE_LOAD(_B8_STATE_STORE(root), run_id=handoff.source_run_id)["canonical_state"]
    _B8_SOURCE_VALIDATE(current_source, _B8_PREPARATION_VIEW(b8_intent))
    if current_source != source:
        raise ValueError("source State changed before task execution")
    validate_active_run_control_entries(root)
    if check_admission:
        _validate_b5_current(root, handoff)
    state_bytes, _ = _READ_GUARDED(root, f"runs/{successor_run_id}/state.json")
    journal_bytes, _ = _READ_GUARDED(root, f"runs/{successor_run_id}/state_journal.jsonl")
    anchor_bytes, _ = _READ_GUARDED(root, f"runs/{successor_run_id}/state_journal_tail.json")
    authority = _VALIDATE_SNAPSHOT(
        _STORE(root),
        run_id=successor_run_id,
        state_bytes=state_bytes,
        journal_bytes=journal_bytes,
        anchor_bytes=anchor_bytes,
    )
    state = authority["canonical_state"]
    context = state.get("context")
    resume_activation = context.get("resume_activation") if isinstance(context, Mapping) else None
    if (
        state.get("status") != "RUNNING"
        or state.get("state_version", 0) < 3
        or state.get("allocation_token") != handoff.allocation_token
        or state.get("plan_fingerprint") != handoff.plan_fingerprint
        or _sha(_canonical(_plain(state.get("task_plan")))) != handoff.task_plan_sha256
        or not isinstance(resume_activation, Mapping)
        or resume_activation.get("input_descriptor_sha256") != handoff.input_descriptor_sha256
        or resume_activation.get("intent_sha256") != handoff.activation_intent_sha256
        or resume_activation.get("source_admission_sha256") != handoff.source_admission_sha256
        or resume_activation.get("source_run_id") != handoff.source_run_id
    ):
        raise ValueError("successor is not running")
    lock_bytes, _ = _READ_GUARDED(root, "runs/.active_run.lock")
    lock = validate_active_run_lock_snapshot(lock_bytes)
    if (
        lock.get("phase") != "running"
        or lock.get("run_id") != successor_run_id
        or lock.get("reserved_run_id") != successor_run_id
        or lock.get("allocation_token") != handoff.allocation_token
        or lock.get("lock_token") != handoff.lock_token
    ):
        raise ValueError("running lock is not bound")
    top_entries = {entry.name for entry in (root / "runs" / successor_run_id).iterdir()}
    if top_entries != _SUCCESSOR_BEFORE_EXECUTION | {_START_INTENT_NAME, "work"}:
        raise ValueError("successor has unexpected execution residue")
    return {
        "state": state,
        "state_bytes": state_bytes,
        "journal_bytes": journal_bytes,
        "anchor_bytes": anchor_bytes,
        "lock_bytes": lock_bytes,
    }


def _validate_planned_fence(
    root: Path,
    successor_run_id: str,
    source: Mapping[str, Any],
    handoff: ResumeExecutionHandoffResult,
    b8_intent: Mapping[str, Any],
    start_intent: Mapping[str, Any],
    start_bytes: bytes,
    input_snapshots: tuple[Mapping[str, Any], ...],
    snapshot_directory_chain: tuple[Mapping[str, Any], ...],
    successor_chain: tuple[Any, ...],
    expected_tasks: Mapping[str, Any],
    expected_task_plan: Any,
    bound_snapshot_bytes: Mapping[str, bytes] | None = None,
) -> Mapping[str, Any]:
    """Fence the exact pre-CAS State before Controller initialization writes."""

    successor = root / "runs" / successor_run_id
    current_chain = _DIRECTORY_CHAIN(root, successor, label="B.9 planned fence")
    _ASSERT_DIRECTORY_CHAIN(successor_chain, label="B.9 planned fence")
    current_intent, current_bytes = _read_json_guarded(
        root, f"runs/{successor_run_id}/{_START_INTENT_NAME}"
    )
    _validate_start_intent(
        current_intent,
        current_bytes,
        handoff,
        input_snapshots,
        snapshot_directory_chain,
    )
    if current_bytes != start_bytes:
        raise ValueError("execution-start intent changed before State CAS")
    _validate_input_snapshots(
        root,
        successor_run_id,
        input_snapshots,
        snapshot_directory_chain,
        bound_snapshot_bytes,
    )
    if current_chain != successor_chain or current_chain != _DIRECTORY_CHAIN(root, successor, label="B.9 planned fence"):
        raise ValueError("successor directory changed before State CAS")

    current_source = _B8_STATE_LOAD(
        _B8_STATE_STORE(root), run_id=handoff.source_run_id
    )["canonical_state"]
    _B8_SOURCE_VALIDATE(current_source, _B8_PREPARATION_VIEW(b8_intent))
    if current_source != source:
        raise ValueError("source State changed before State CAS")
    _validate_b5_current(root, handoff)
    current_tasks, current_plan = _canonical_task_plan(root, current_source, handoff)
    if current_tasks != expected_tasks or current_plan != expected_task_plan:
        raise ValueError("canonical task plan changed before State CAS")

    state_bytes, _ = _READ_GUARDED(root, f"runs/{successor_run_id}/state.json")
    journal_bytes, _ = _READ_GUARDED(root, f"runs/{successor_run_id}/state_journal.jsonl")
    anchor_bytes, _ = _READ_GUARDED(root, f"runs/{successor_run_id}/state_journal_tail.json")
    authority = _VALIDATE_SNAPSHOT(
        _STORE(root),
        run_id=successor_run_id,
        state_bytes=state_bytes,
        journal_bytes=journal_bytes,
        anchor_bytes=anchor_bytes,
    )
    state = authority["canonical_state"]
    if (
        state.get("status") != "PLANNED"
        or state.get("state_version") != 1
        or state.get("allocation_token") != handoff.allocation_token
        or state.get("plan_fingerprint") != handoff.plan_fingerprint
        or _sha(_canonical(_plain(state.get("task_plan")))) != handoff.task_plan_sha256
        or state.get("task_status") != {}
        or state.get("task_attempts") != {}
        or tuple(state.get("completed_tasks", ())) != ()
        or tuple(state.get("failed_tasks", ())) != ()
    ):
        raise ValueError("successor is not the untouched PLANNED State")
    lock_bytes, _ = _READ_GUARDED(root, "runs/.active_run.lock")
    lock = validate_active_run_lock_snapshot(lock_bytes)
    if (
        lock.get("phase") != "running"
        or lock.get("run_id") != successor_run_id
        or lock.get("reserved_run_id") != successor_run_id
        or lock.get("allocation_token") != handoff.allocation_token
        or lock.get("lock_token") != handoff.lock_token
    ):
        raise ValueError("running lock is not bound before State CAS")
    validate_active_run_control_entries(root)
    entries = tuple(sorted(entry.name for entry in successor.iterdir()))
    if frozenset(entries) != _SUCCESSOR_BEFORE_EXECUTION | {_START_INTENT_NAME, "work"}:
        raise ValueError("successor has unexpected pre-CAS residue")
    return {
        "state": state,
        "state_bytes": state_bytes,
        "journal_bytes": journal_bytes,
        "anchor_bytes": anchor_bytes,
        "lock_bytes": lock_bytes,
    }


class _BoundedSink:
    """Controller proxy that fences the authority immediately before workers."""

    def __init__(
        self,
        controller: InspectionWorkflowController,
        canonical_tasks: Mapping[str, Any],
        preflight_before: Any,
        preflight_after: Any,
    ) -> None:
        self._controller = controller
        self._canonical_tasks = dict(canonical_tasks)
        self._preflight_before = preflight_before
        self._preflight_after = preflight_after
        self._preflight_before_task_mutation = preflight_after
        self._preflight_before_task_start = preflight_after
        self._preflight_after_task_start = preflight_after

    @property
    def run_id(self) -> str:
        return self._controller.run_id

    @property
    def snapshot(self) -> Mapping[str, Any]:
        return self._controller.snapshot

    async def prepare_execution(self, tasks: Mapping[str, Any]) -> Any:
        self._preflight_before()
        result = await self._controller.prepare_execution(self._canonical_tasks)
        self._preflight_after()
        return result

    async def submit_checkpoint_event(self, event: Mapping[str, Any]) -> Any:
        if event.get("checkpoint_kind") == "task_started":
            self._preflight_before_task_start()
            result = await self._controller.submit_checkpoint_event(event)
            self._preflight_after_task_start()
            return result
        self._preflight_before_task_mutation()
        return await self._controller.submit_checkpoint_event(event)

    def set_task_mutation_fence(self, fence: Any) -> None:
        self._preflight_before_task_mutation = fence

    def set_task_start_fence(self, fence: Any) -> None:
        self._preflight_before_task_start = fence
        self._preflight_after_task_start = fence

    def project_executor_context(self, context: Mapping[str, Any]) -> dict[str, Any]:
        return self._controller.project_executor_context(context)


def _sealed_sink_type() -> type:
    """Copy the sink implementation so public class mutation is not authority."""

    return type(
        "_SealedBoundedSink",
        (),
        {
            "__init__": _copy_function(_BoundedSink.__init__),
            "run_id": property(_copy_function(_BoundedSink.run_id.fget)),
            "snapshot": property(_copy_function(_BoundedSink.snapshot.fget)),
            "prepare_execution": _copy_function(_BoundedSink.prepare_execution),
            "submit_checkpoint_event": _copy_function(_BoundedSink.submit_checkpoint_event),
            "set_task_mutation_fence": _copy_function(_BoundedSink.set_task_mutation_fence),
            "set_task_start_fence": _copy_function(_BoundedSink.set_task_start_fence),
            "project_executor_context": _copy_function(_BoundedSink.project_executor_context),
        },
    )


_SINK_TYPE = _sealed_sink_type()


def _execute(
    root: Path,
    successor_run_id: str,
    handoff: ResumeExecutionHandoffResult,
) -> ResumeExecutionResult:
    if type(successor_run_id) is not str or _RUN_ID_RE.fullmatch(successor_run_id) is None:
        raise ValueError("successor run id is invalid")
    intent, source, handoff_intent_bytes, b8_evidence = _validate_b8_current(
        root, successor_run_id, handoff
    )
    _validate_b5_current(root, handoff)
    tasks, task_plan = _canonical_task_plan(root, source, handoff)
    successor = root / "runs" / successor_run_id
    chain = _DIRECTORY_CHAIN(root, successor, label="B.9 successor")
    entries = tuple(sorted(entry.name for entry in successor.iterdir()))
    if frozenset(entries) != _SUCCESSOR_BEFORE_EXECUTION:
        raise ValueError("successor is not an untouched B.8 handoff")
    if _START_INTENT_NAME in entries:
        raise ValueError("execution-start intent already exists")
    timestamp = intent.get("mutation_timestamp")
    if type(timestamp) is not str:
        raise ValueError("B.8 mutation timestamp is invalid")
    captured_inputs = _source_snapshot_spec(root, source, handoff)
    formal_tree_evidence = types.MappingProxyType(
        {
            name: _external_tree_snapshot(root, name)
            for name in ("outputs", "staging")
        }
    )
    with _BIND_DIRECTORIES(chain):
        _ASSERT_DIRECTORY_CHAIN(chain, label="B.9 pre-intent")
        _MAKE_DIRECTORY(
            root, f"runs/{successor_run_id}/work"
        )
    work_chain = _DIRECTORY_CHAIN(
        root, successor / "work", label="B.9 execution work"
    )
    with _BIND_DIRECTORIES(work_chain):
        _MAKE_DIRECTORY(
            root, f"runs/{successor_run_id}/{_INPUT_SNAPSHOT_DIR}"
        )
    mutation_chain = _DIRECTORY_CHAIN(
        root, successor / _INPUT_SNAPSHOT_DIR, label="B.9 execution snapshot"
    )
    snapshot_directory_chain = _directory_chain_binding(root, mutation_chain)
    bound_inputs: list[Mapping[str, Any]] = []
    with _BIND_DIRECTORIES(mutation_chain):
        for item, data in captured_inputs:
            device, inode = _WRITE_EXCLUSIVE(root, item["snapshot_path"], data)
            bound_inputs.append({**item, "device": device, "inode": inode})
    input_snapshots = tuple(bound_inputs)
    bound_snapshot_bytes = _validate_input_snapshots(
        root,
        successor_run_id,
        input_snapshots,
        snapshot_directory_chain,
    )
    start_intent = _make_start_intent(
        handoff,
        timestamp,
        input_snapshots,
        snapshot_directory_chain,
    )
    start_bytes = _canonical(start_intent)
    with _BIND_DIRECTORIES(chain):
        _validate_input_snapshots(
            root, successor_run_id, input_snapshots, snapshot_directory_chain
        )
        # Snapshot bytes are immutable successor-owned execution inputs.  The
        # source authority is rechecked after publication, so no stale source
        # can authorize the snapshot consumed by the worker.
        _validate_b5_current(root, handoff)
        _WRITE_EXCLUSIVE(root, f"runs/{successor_run_id}/{_START_INTENT_NAME}", start_bytes)
    current_start, current_start_bytes = _read_json_guarded(
        root, f"runs/{successor_run_id}/{_START_INTENT_NAME}"
    )
    _validate_start_intent(
        current_start,
        current_start_bytes,
        handoff,
        input_snapshots,
        snapshot_directory_chain,
    )
    if current_start_bytes != start_bytes:
        raise ValueError("execution-start intent publication changed")
    # B.8's archived evidence is intentionally closed over the PLANNED@1
    # successor entry set and therefore cannot be called after this B.9 intent
    # is published.  The B.8 fence above is the last observable B.8 authority
    # before B.9 publication; from here the official B.5 admission, the exact
    # start-intent bytes and StateStore's own CAS/lock fence remain binding.
    current_start, current_start_bytes = _read_json_guarded(
        root, f"runs/{successor_run_id}/{_START_INTENT_NAME}"
    )
    _validate_start_intent(
        current_start,
        current_start_bytes,
        handoff,
        input_snapshots,
        snapshot_directory_chain,
    )
    if current_start_bytes != start_bytes:
        raise ValueError("execution-start intent changed before State mutation")

    input_mode = source["context"]["workflow_input_mode"]
    controller = _CONTROLLER(
        root,
        run_id=successor_run_id,
        expected_lock_token=handoff.lock_token or "",
        plan_fingerprint=handoff.plan_fingerprint or "",
        # B.8's successor genesis context intentionally contains only the
        # resume_activation binding.  Construct the existing Controller
        # without its optional context-presence assertion, then bind the
        # already-validated descriptor before its official plan/CAS method.
        resolved_input_descriptor_sha256=None,
        workflow_input_mode=input_mode,
        execution_profile=_EXECUTION_PROFILE,
        resume=False,
        clock=lambda: timestamp,
    )
    controller._resolved_input_descriptor_sha256 = handoff.input_descriptor_sha256
    layers = execution_layers(tasks)
    if not layers:
        raise ValueError("authoritative DAG has no executable layer")
    execution_task_ids = tuple(sorted(layers[0]))
    execution_tasks = {task_id: tasks[task_id] for task_id in execution_task_ids}
    sink = _SINK_TYPE(
        controller,
        tasks,
        lambda: _validate_planned_fence(
            root,
            successor_run_id,
            source,
            handoff,
            intent,
            current_start,
            current_start_bytes,
            input_snapshots,
            snapshot_directory_chain,
            chain,
            tasks,
            task_plan,
            bound_snapshot_bytes,
        ),
        lambda: (
            _validate_running_fence(
                root,
                successor_run_id,
                source,
                handoff,
                intent,
                current_start,
                current_start_bytes,
                input_snapshots,
                snapshot_directory_chain,
                chain,
                bound_snapshot_bytes=bound_snapshot_bytes,
            ),
        ),
    )
    sink.set_task_mutation_fence(
        lambda: _validate_running_fence(
            root,
            successor_run_id,
            source,
            handoff,
            intent,
            current_start,
            current_start_bytes,
            input_snapshots,
            snapshot_directory_chain,
            chain,
            check_admission=True,
            bound_snapshot_bytes=bound_snapshot_bytes,
        )
    )
    sink.set_task_start_fence(
        lambda: _validate_running_fence(
            root,
            successor_run_id,
            source,
            handoff,
            intent,
            current_start,
            current_start_bytes,
            input_snapshots,
            snapshot_directory_chain,
            chain,
            check_admission=True,
            bound_snapshot_bytes=bound_snapshot_bytes,
        )
    )
    context = _source_input_context(
        root, successor_run_id, source, handoff, input_snapshots
    )
    registry = _BUILD_REGISTRY()
    _bind_worker_snapshot_bytes(
        registry, root, input_snapshots, bound_snapshot_bytes
    )
    expected_work_entries = _expected_work_entries(
        successor_run_id, input_snapshots, bound_snapshot_bytes
    )
    executor = _EXECUTOR(
        registry,
        root,
        resume=False,
        run_id=successor_run_id,
        checkpoint_event_sink=sink,
    )
    # Keep the original successor directory identity bound across every
    # official StateStore/CAS/checkpoint mutation.  The controlled filesystem
    # performs its last observable pre-write identity check at each mutation;
    # this does not claim a cross-file kernel transaction.
    with _BIND_DIRECTORIES(mutation_chain), _hold_snapshot_objects(
        root, input_snapshots
    ) as held_snapshot_objects:
        _ASSERT_DIRECTORY_CHAIN(chain, label="B.9 execution mutations")
        with _guard_worker_work_writes(
            root, successor_run_id, expected_work_entries, input_snapshots
        ) as publish_worker_outputs:
            executor.run(execution_tasks, context)
            worker_write_evidence = publish_worker_outputs()
        final = _validate_running_fence(
            root,
            successor_run_id,
            source,
            handoff,
            intent,
            current_start,
            current_start_bytes,
            input_snapshots,
            snapshot_directory_chain,
            chain,
            check_admission=True,
            bound_snapshot_bytes=bound_snapshot_bytes,
            held_snapshot_objects=held_snapshot_objects,
        )
        state = final["state"]
        task_status = state.get("task_status")
        task_attempts = state.get("task_attempts")
        executed_task_ids = tuple(
            sorted(task_id for task_id, status in task_status.items() if status == "success")
        ) if isinstance(task_status, Mapping) else ()
        if (
            not isinstance(task_status, Mapping)
            or executed_task_ids != execution_task_ids
            or any(
                status != ("success" if task_id in execution_task_ids else "pending")
                for task_id, status in task_status.items()
            )
            or not isinstance(task_attempts, Mapping)
            or tuple(sorted(task_attempts)) != tuple(handoff.required_task_ids)
            or any(
                type(value) is not int
                or value != (1 if task_id in execution_task_ids else 0)
                for task_id, value in task_attempts.items()
            )
        ):
            raise ValueError("required task execution did not complete successfully")
        events = state.get("context", {}).get("phase_a3_checkpoint_events", {})
        if not isinstance(events, Mapping) or any(
            isinstance(row, Mapping)
            and row.get("checkpoint_kind") in {"task_skipped", "task_cache_hit"}
            for row in events.values()
        ):
            raise ValueError("task skip or cache checkpoint is not permitted")
        if any(
            _entry_exists(successor / name)
            for name in ("publication_transaction.json", "final_summary.md", "publication_backup")
        ):
            raise ValueError("publication residue is not permitted")
        _validate_execution_write_set(
            root,
            successor_run_id,
            expected_work_entries,
            formal_tree_evidence,
            worker_write_evidence,
        )
        # Keep the actual snapshot objects live through issuance and make the
        # last observable input check read those handles, not their pathnames.
        _validate_input_snapshots(
            root,
            successor_run_id,
            input_snapshots,
            snapshot_directory_chain,
            bound_snapshot_bytes,
            held_snapshot_objects,
        )
        start_sha = _sha(current_start_bytes)
        bindings = {
            "successor_run_id": successor_run_id,
            "source_run_id": handoff.source_run_id,
            "handoff_sha256": handoff.handoff_sha256,
            "handoff_intent_sha256": handoff.handoff_intent_sha256,
            "activation_sha256": handoff.activation_sha256,
            "activation_intent_sha256": handoff.activation_intent_sha256,
            "source_admission_sha256": handoff.source_admission_sha256,
            "source_state_version": handoff.source_state_version,
            "allocation_token": handoff.allocation_token,
            "lock_token": handoff.lock_token,
            "state_version": state["state_version"],
            "plan_fingerprint": handoff.plan_fingerprint,
            "input_descriptor_sha256": handoff.input_descriptor_sha256,
            "task_plan_sha256": handoff.task_plan_sha256,
            "required_task_ids": list(handoff.required_task_ids),
            "executed_task_ids": list(executed_task_ids),
            "start_intent_sha256": start_sha,
            "state_sha256": _sha(final["state_bytes"]),
            "journal_sha256": _sha(final["journal_bytes"]),
            "journal_anchor_sha256": _sha(final["anchor_bytes"]),
            "lock_sha256": _sha(final["lock_bytes"]),
        }
        data = _canonical({"schema_version": _SCHEMA, "status": _EXECUTED, **bindings})
        return _RESULT_ISSUE_AUTHORITY(
            {
                "status": _EXECUTED,
                "execution_bytes": data,
                "execution_sha256": _sha(data),
                **bindings,
                "required_task_ids": tuple(handoff.required_task_ids),
                "executed_task_ids": executed_task_ids,
            }
        )


def _seal_execution_authority() -> Any:
    """Freeze the complete B.9 call graph before exposing its public API."""

    names = (
        "_read_json_guarded",
        "_b8_intent_validate",
        "_b8_evidence",
        "_validate_b8_current",
        "_validate_b5_current",
        "_make_start_intent",
        "_validate_start_intent",
        "_canonical_task_plan",
        "_open_source_read_guard",
        "_held_source_snapshot_bytes",
        "_source_snapshot_spec",
        "_directory_chain_binding",
        "_csv_rows_from_bytes",
        "_validate_input_snapshots",
        "_bind_worker_snapshot_bytes",
        "_entry_exists",
        "_expected_work_entries",
        "_execution_work_entries",
        "_external_tree_snapshot",
        "_validate_execution_write_set",
        "_source_input_context",
        "_validate_running_fence",
        "_validate_planned_fence",
        "_execute",
    )
    frozen = dict(globals())
    frozen["_SINK_TYPE"] = _sealed_sink_type()
    copies: dict[str, Any] = {}
    for name in names:
        function = frozen[name]
        copies[name] = types.FunctionType(
            function.__code__,
            frozen,
            name=function.__name__,
            argdefs=function.__defaults__,
            closure=function.__closure__,
        )
        copies[name].__kwdefaults__ = dict(function.__kwdefaults__ or {})
    copies["_held_source_snapshot_bytes"].__defaults__ = (
        copies["_open_source_read_guard"],
        frozen["_FILE_IDENTITY"],
        copies["_validate_b5_current"],
    )
    frozen.update(copies)
    return copies["_execute"]


_SEALED_EXECUTE = _seal_execution_authority()
_SEALED_DENIED = _DENIED_ISSUE_AUTHORITY


def _seal_public_executor(execute: Any, denied: Any, controlled_root: Any) -> Any:
    """Return a public callable whose authority handles are closure-bound."""

    path_type = Path

    def public(
        project_root: Path,
        *,
        successor_run_id: str,
        handoff: ResumeExecutionHandoffResult,
    ) -> ResumeExecutionResult:
        try:
            root = controlled_root(path_type(project_root).absolute())
            return execute(root, successor_run_id, handoff)
        except BaseException:
            return denied()

    return public


execute_resume_execution = _seal_public_executor(
    _SEALED_EXECUTE,
    _SEALED_DENIED,
    _CONTROLLED_ROOT,
)


def _unsupported_issuance(*_args: Any, **_kwargs: Any) -> Any:
    raise TypeError("B.9 result issuance is internal")


# Keep only the closure-bound public API reachable from the module surface.
# The sealed callable retains the original authority graph in its private
# globals/closures; these names cannot be used as a universal success factory.
_RESULT_ISSUE_AUTHORITY = _unsupported_issuance
_DENIED_ISSUE_AUTHORITY = _unsupported_issuance
del _RESULT_IS_OFFICIAL, _REGISTER_RESULT, _DISCARD_RESULT
del _new_result_registry, _new_result_issuer, _new_denied_issuer, _RESULT_POST_INIT
del _sealed_success_property
del _seal_execution_authority, _seal_public_executor, _SEALED_EXECUTE, _SEALED_DENIED
del _execute, _unsupported_issuance


__all__ = ["ResumeExecutionResult", "execute_resume_execution"]
