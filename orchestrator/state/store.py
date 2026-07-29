"""Legacy checkpoint helpers plus the Phase A3.1 canonical StateStore.

The legacy functions at the top retain their existing behavior.  ``StateStore``
is an opt-in Run-local foundation; it is not connected to the current executor,
registry, CLI, or publication flow in A3.1.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Iterator
import uuid

from orchestrator.inspection_workflow.locking import (
    ActiveRunLockError,
    _assert_plain_entry,
    _assert_project_path,
    _is_reparse,
    _lstat,
    _sync_directory,
    _validate_identifier,
    _validate_timestamp,
    _validate_uuid,
    canonical_utc_now,
    validate_active_run_lock,
)
from orchestrator.inspection_workflow.models import (
    StateMutationResult,
    StateSnapshot,
    freeze_json,
)


# Existing compatibility API.  Its behavior intentionally remains unchanged.
def save_checkpoint(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def make_state(run_id: str, context: dict[str, Any]) -> dict[str, Any]:
    status = context.get("task_status", {})
    return {
        "run_id": run_id,
        "task_status": status,
        "completed_tasks": [name for name, value in status.items() if value == "success"],
        "failed_tasks": [name for name, value in status.items() if value == "failed"],
        "context_snapshot": deepcopy(context),
    }


STATE_SCHEMA_VERSION = "inspection_state_v1"
STATE_JOURNAL_SCHEMA_VERSION = "state_journal_v1"
STATE_JOURNAL_TAIL_SCHEMA_VERSION = "state_journal_tail_v1"
STATE_LOCK_SCHEMA_VERSION = "state_lock_v1"
COMPLETION_EVIDENCE_SCHEMA_VERSION = "completion_evidence_v1"

STATE_PATH_TEMPLATE = "runs/{run_id}/state.json"
STATE_JOURNAL_PATH_TEMPLATE = "runs/{run_id}/state_journal.jsonl"
STATE_JOURNAL_TAIL_PATH_TEMPLATE = "runs/{run_id}/state_journal_tail.json"
STATE_LOCK_PATH_TEMPLATE = "runs/{run_id}/.state.lock"
STATE_INITIALIZATION_RECOVERY_MARKER_TEMPLATE = (
    "runs/{run_id}/.state_initialization_recovery_required.json"
)
STATE_LOCK_RECOVERY_MARKER_TEMPLATE = "runs/{run_id}/.state_lock_recovery_required.json"

STATUSES = frozenset(
    {
        "CREATED",
        "PLANNED",
        "RUNNING",
        "WAITING_FOR_REVIEW",
        "BLOCKED",
        "FAILED",
        "COMPLETED",
    }
)
TERMINAL_STATUSES = frozenset({"FAILED", "COMPLETED"})
TASK_STATUSES = frozenset(
    {"pending", "running", "retry_pending", "retry_scheduled", "success", "failed", "skipped"}
)
CHECKPOINT_KINDS = frozenset(
    {
        "run_initialized",
        "task_skipped",
        "task_cache_hit",
        "task_started",
        "task_succeeded",
        "task_failed",
        "task_retry_scheduled",
    }
)
ALLOWED_TRANSITIONS = frozenset(
    {
        ("CREATED", "PLANNED"),
        ("CREATED", "FAILED"),
        ("PLANNED", "RUNNING"),
        ("PLANNED", "BLOCKED"),
        ("PLANNED", "FAILED"),
        ("RUNNING", "FAILED"),
        ("RUNNING", "COMPLETED"),
        ("RUNNING", "WAITING_FOR_REVIEW"),
        ("RUNNING", "BLOCKED"),
        ("WAITING_FOR_REVIEW", "RUNNING"),
        ("WAITING_FOR_REVIEW", "BLOCKED"),
        ("WAITING_FOR_REVIEW", "FAILED"),
        ("BLOCKED", "PLANNED"),
        ("BLOCKED", "FAILED"),
    }
)

_STATE_FIELDS = frozenset(
    {
        "schema_version",
        "run_id",
        "allocation_token",
        "plan_fingerprint",
        "status",
        "state_version",
        "created_at",
        "updated_at",
        "task_plan",
        "task_status",
        "task_attempts",
        "completed_tasks",
        "failed_tasks",
        "context",
        "last_operation_kind",
        "last_operation_id",
        "last_operation_payload_sha256",
    }
)
_TASK_PLAN_FIELDS = frozenset({"task_id", "deps", "required"})
_ANCHOR_FIELDS = frozenset(
    {
        "schema_version",
        "run_id",
        "allocation_token",
        "tail_record_index",
        "tail_record_checksum",
        "tail_file_size_bytes",
        "anchor_checksum",
    }
)
_JOURNAL_FIELDS = frozenset(
    {
        "schema_version",
        "run_id",
        "allocation_token",
        "record_index",
        "previous_record_checksum",
        "record_checksum",
        "operation_id",
        "operation_owner_lock_token",
        "append_actor_lock_token",
        "recovery_audit_ref",
        "phase",
        "mutation_kind",
        "mutation_timestamp",
        "journal_timestamp",
        "expected_state_version",
        "resulting_state_version",
        "expected_status",
        "resulting_status",
        "payload",
        "payload_sha256",
        "resulting_state_sha256",
    }
)
_COMPLETION_FIELDS = frozenset(
    {
        "schema_version",
        "run_id",
        "plan_fingerprint",
        "required_task_ids",
        "publication_manifest_path",
        "publication_manifest_sha256",
        "publication_transaction_path",
        "publication_transaction_sha256",
        "transaction_id",
        "final_summary_path",
        "final_summary_sha256",
    }
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_OPERATION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,511}\Z")
_ABORTED_START_SUFFIX_RE = re.compile(r":after_aborted:([0-9a-f]{64})\Z")
_BINARY_FLAG = getattr(os, "O_BINARY", 0)
MAX_STATE_JOURNAL_BYTES = 8 * 1024 * 1024
MAX_STATE_JOURNAL_RECORDS = 10_000
MAX_CHECKPOINT_ERROR_SUMMARY_CHARS = 1_000
_ABSOLUTE_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|(?:^|\s)/(?:home|Users|tmp|opt)(?:/|\s|$)|\\\\)"
)


class StateStoreError(RuntimeError):
    """Base error for canonical StateStore contract failures."""


class StateConflictError(StateStoreError):
    """Raised for CAS, fencing, journal, or state identity conflicts."""


class StateRecoveryRequiredError(StateStoreError):
    """Raised when explicit recovery is required before a read or mutation."""


class StateRecoveryDeferredError(StateStoreError):
    """Raised when recovery needs the A3.2 lock-takeover protocol."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_json_value(value: Any, *, label: str, depth: int = 0) -> None:
    if depth > 32:
        raise StateStoreError(f"{label} exceeds the maximum nesting depth")
    if value is None or isinstance(value, (str, bool)) or type(value) is int:
        return
    if isinstance(value, float):
        raise StateStoreError(f"{label} must not contain JSON floats")
    if isinstance(value, list):
        if len(value) > 4096:
            raise StateStoreError(f"{label} contains too many list items")
        for item in value:
            _validate_json_value(item, label=label, depth=depth + 1)
        return
    if isinstance(value, Mapping):
        if len(value) > 4096:
            raise StateStoreError(f"{label} contains too many object fields")
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise StateStoreError(f"{label} object keys must be non-empty strings")
            _validate_json_value(item, label=label, depth=depth + 1)
        return
    raise StateStoreError(f"{label} is not JSON-compatible")


def _safe_deepcopy(value: Any, *, label: str) -> Any:
    try:
        return deepcopy(value)
    except (RecursionError, TypeError, ValueError) as exc:
        raise StateStoreError(f"{label} cannot be copied safely") from exc


def _add_cleanup_diagnostic(primary: BaseException, message: str) -> None:
    """Attach diagnostics without requiring Python 3.11 ``add_note``."""

    diagnostics = list(getattr(primary, "cleanup_diagnostics", ()))
    diagnostics.append(message)
    try:
        primary.cleanup_diagnostics = tuple(diagnostics)
    except Exception:
        pass
    add_note = getattr(primary, "add_note", None)
    if callable(add_note):
        try:
            add_note(message)
        except Exception:
            pass


def _restore_state_lock_evidence(path: Path, run_dir: Path, data: bytes) -> list[str]:
    diagnostics: list[str] = []
    descriptor: int | None = None
    try:
        if _lstat(path, label="state lock recovery evidence") is None:
            descriptor = os.open(
                path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | _BINARY_FLAG, 0o600
            )
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short state lock recovery write")
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
    except Exception as exc:
        diagnostics.append(f"unable to restore blocking state lock evidence: {exc}")
        diagnostics.extend(_write_state_lock_recovery_marker(path, run_dir, data))
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as exc:
                diagnostics.append(f"unable to close restored state lock evidence: {exc}")
    try:
        _sync_directory(run_dir, label="Run directory after restoring state lock evidence")
    except Exception as exc:
        diagnostics.append(f"unable to sync restored state lock evidence: {exc}")
    return diagnostics


def _write_state_lock_recovery_marker(path: Path, run_dir: Path, data: bytes) -> list[str]:
    """Leave an independent blocker when the owned lock cannot be restored."""

    marker = run_dir / ".state_lock_recovery_required.json"
    diagnostics: list[str] = []
    descriptor: int | None = None
    try:
        if _lstat(marker, label="state lock recovery marker") is not None:
            return diagnostics
        descriptor = os.open(
            marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY | _BINARY_FLAG, 0o600
        )
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short state lock recovery marker write")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        _sync_directory(run_dir, label="Run directory after state lock recovery marker")
    except Exception as exc:
        diagnostics.append(f"unable to write state lock recovery marker: {exc}")
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as exc:
                diagnostics.append(f"unable to close state lock recovery marker: {exc}")
    return diagnostics


def _canonical_json_bytes(value: Any) -> bytes:
    _validate_json_value(value, label="StateStore JSON")
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise StateStoreError("StateStore JSON is not canonicalizable") from exc


def _json_loads(data: bytes, *, label: str) -> Any:
    try:
        return json.loads(
            data.decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise StateConflictError(f"{label} is invalid JSON") from exc


def _validate_sha(value: Any, *, label: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise StateStoreError(f"{label} must be a lowercase SHA-256")
    return value


def _validate_recovery_audit_ref(value: Any, *, run_id: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"path", "sha256"}:
        raise StateConflictError("state journal recovery_audit_ref is invalid")
    path = value["path"]
    if (
        not isinstance(path, str)
        or "\\" in path
        or not re.fullmatch(
            rf"runs/{re.escape(run_id)}/lock_recovery_audit/[^/]+\.intent\.json",
            path,
        )
    ):
        raise StateConflictError("state journal recovery_audit_ref path is invalid")
    try:
        sha256 = _validate_sha(value["sha256"], label="state journal recovery audit SHA-256")
    except StateStoreError as exc:
        raise StateConflictError(str(exc)) from exc
    assert sha256 is not None
    return {"path": path, "sha256": sha256}


def _validate_operation_id(value: Any) -> str:
    if not isinstance(value, str) or not _OPERATION_RE.fullmatch(value):
        raise StateStoreError("operation_id is invalid")
    return value


def _assert_regular_journal_entry(entry: os.stat_result, *, label: str) -> None:
    if (
        stat.S_ISLNK(entry.st_mode)
        or _is_reparse(entry)
        or not stat.S_ISREG(entry.st_mode)
    ):
        raise StateConflictError(
            f"{label} must be a regular non-link, non-reparse file"
        )


def _validate_task_plan(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise StateStoreError("task_plan must be a sequence")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != _TASK_PLAN_FIELDS:
            raise StateStoreError("task_plan entries must contain task_id, deps, and required")
        try:
            task_id = _validate_identifier(item["task_id"], label="task_plan task_id")
        except ActiveRunLockError as exc:
            raise StateStoreError(str(exc)) from exc
        if task_id in seen:
            raise StateStoreError(f"task_plan contains duplicate task_id: {task_id}")
        seen.add(task_id)
        deps = item["deps"]
        if not isinstance(deps, list) or any(not isinstance(dep, str) for dep in deps):
            raise StateStoreError(f"task_plan deps are invalid for {task_id}")
        try:
            canonical_deps = [
                _validate_identifier(dep, label=f"task_plan dependency for {task_id}") for dep in deps
            ]
        except ActiveRunLockError as exc:
            raise StateStoreError(str(exc)) from exc
        if canonical_deps != sorted(set(canonical_deps)) or task_id in canonical_deps:
            raise StateStoreError(f"task_plan deps must be sorted, unique, and non-self for {task_id}")
        if type(item["required"]) is not bool:
            raise StateStoreError(f"task_plan required must be boolean for {task_id}")
        if item["required"] is not True:
            raise StateStoreError("Phase A3.1 core task_plan entries must all be required")
        normalized.append({"task_id": task_id, "deps": canonical_deps, "required": True})
    if not normalized or [item["task_id"] for item in normalized] != sorted(seen):
        raise StateStoreError("task_plan must be non-empty and sorted by task_id")
    for item in normalized:
        unknown = set(item["deps"]) - seen
        if unknown:
            raise StateStoreError(
                f"task_plan contains unknown dependencies for {item['task_id']}: {sorted(unknown)}"
            )
    return normalized


def _validate_state(value: Any, *, expected_run_id: str | None = None) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _STATE_FIELDS:
        raise StateConflictError("canonical state fields are invalid")
    try:
        state = _safe_deepcopy(dict(value), label="canonical state")
    except StateStoreError as exc:
        raise StateConflictError(str(exc)) from exc
    if state["schema_version"] != STATE_SCHEMA_VERSION:
        raise StateConflictError("canonical state schema_version is invalid")
    try:
        run_id = _validate_identifier(state["run_id"], label="state run_id")
        _validate_uuid(state["allocation_token"], label="state allocation_token")
        _validate_timestamp(state["created_at"], label="state created_at")
        _validate_timestamp(state["updated_at"], label="state updated_at")
    except ActiveRunLockError as exc:
        raise StateConflictError(str(exc)) from exc
    if expected_run_id is not None and run_id != expected_run_id:
        raise StateConflictError("canonical state run_id does not match")
    _validate_sha(state["plan_fingerprint"], label="state plan_fingerprint")
    if not isinstance(state["status"], str) or state["status"] not in STATUSES:
        raise StateConflictError("canonical state status is invalid")
    if type(state["state_version"]) is not int or state["state_version"] < 0:
        raise StateConflictError("canonical state_version is invalid")
    state["task_plan"] = _validate_task_plan(state["task_plan"])
    task_ids = [item["task_id"] for item in state["task_plan"]]
    for field in ("task_status", "task_attempts"):
        if not isinstance(state[field], Mapping):
            raise StateConflictError(f"canonical {field} must be an object")
        state[field] = dict(state[field])
        if set(state[field]) not in (set(), set(task_ids)):
            raise StateConflictError(f"canonical {field} keys do not match task_plan")
    if set(state["task_status"]) != set(state["task_attempts"]):
        raise StateConflictError(
            "canonical task_status and task_attempts keys must match exactly"
        )
    for task_id, status in state["task_status"].items():
        if not isinstance(status, str) or status not in TASK_STATUSES:
            raise StateConflictError(f"canonical task status is invalid for {task_id}")
    for task_id, attempt in state["task_attempts"].items():
        if type(attempt) is not int or attempt < 0:
            raise StateConflictError(f"canonical task attempt is invalid for {task_id}")
    for field in ("completed_tasks", "failed_tasks"):
        if not isinstance(state[field], list) or any(not isinstance(item, str) for item in state[field]):
            raise StateConflictError(f"canonical {field} is invalid")
        if state[field] != sorted(set(state[field])) or not set(state[field]).issubset(task_ids):
            raise StateConflictError(f"canonical {field} must be a stable task subset")
    expected_completed = sorted(
        task_id for task_id, status in state["task_status"].items() if status == "success"
    )
    expected_failed = sorted(
        task_id for task_id, status in state["task_status"].items() if status == "failed"
    )
    if state["completed_tasks"] != expected_completed or state["failed_tasks"] != expected_failed:
        raise StateConflictError("canonical completed/failed task indexes are inconsistent")
    if not isinstance(state["context"], Mapping):
        raise StateConflictError("canonical context must be an object")
    _validate_json_value(state["context"], label="canonical context")
    last_fields = (
        state["last_operation_kind"],
        state["last_operation_id"],
        state["last_operation_payload_sha256"],
    )
    if all(item is None for item in last_fields):
        if state["state_version"] != 0:
            raise StateConflictError("non-genesis state requires last operation metadata")
    elif any(item is None for item in last_fields):
        raise StateConflictError("last operation metadata must be all null or all populated")
    else:
        if (
            not isinstance(state["last_operation_kind"], str)
            or state["last_operation_kind"] not in {"context_checkpoint", "status_transition"}
        ):
            raise StateConflictError("last_operation_kind is invalid")
        _validate_operation_id(state["last_operation_id"])
        _validate_sha(
            state["last_operation_payload_sha256"], label="last operation payload SHA-256"
        )
    return state


def _state_snapshot(state: Mapping[str, Any]) -> StateSnapshot:
    canonical = _safe_deepcopy(dict(state), label="canonical state snapshot")
    return freeze_json(
        {
            "run_id": canonical["run_id"],
            "status": canonical["status"],
            "state_version": canonical["state_version"],
            "canonical_state": canonical,
        }
    )


def _mutation_result(state: Mapping[str, Any], operation_id: str) -> StateMutationResult:
    canonical = _safe_deepcopy(dict(state), label="canonical mutation result")
    return freeze_json(
        {
            "run_id": canonical["run_id"],
            "operation_id": operation_id,
            "resulting_state_version": canonical["state_version"],
            "canonical_state": canonical,
        }
    )


class StateStore:
    """Run-local canonical State, CAS, WAL, and explicit recovery foundation."""

    def __init__(self, project_root: Path):
        self.project_root = Path(project_root).absolute()
        try:
            _assert_plain_entry(self.project_root, label="project_root", directory=True)
        except ActiveRunLockError as exc:
            raise StateStoreError(str(exc)) from exc

    def _run_dir(self, run_id: str, *, require: bool = True) -> Path:
        try:
            _validate_identifier(run_id, label="run_id")
        except ActiveRunLockError as exc:
            raise StateStoreError(str(exc)) from exc
        path = self.project_root / "runs" / run_id
        try:
            _assert_project_path(
                self.project_root, path, include_leaf=require, label="Run directory"
            )
            if require:
                _assert_plain_entry(path, label="Run directory", directory=True)
        except ActiveRunLockError as exc:
            raise StateStoreError(str(exc)) from exc
        return path

    def _paths(self, run_id: str) -> dict[str, Path]:
        run_dir = self._run_dir(run_id)
        return {
            "run_dir": run_dir,
            "state": run_dir / "state.json",
            "journal": run_dir / "state_journal.jsonl",
            "anchor": run_dir / "state_journal_tail.json",
            "lock": run_dir / ".state.lock",
            "recovery": run_dir / ".state_initialization_recovery_required.json",
            "lock_recovery": run_dir / ".state_lock_recovery_required.json",
        }

    @contextmanager
    def _state_lock(self, run_id: str) -> Iterator[None]:
        paths = self._paths(run_id)
        path = paths["lock"]
        token = str(uuid.uuid4())
        data = _canonical_json_bytes(
            {
                "schema_version": STATE_LOCK_SCHEMA_VERSION,
                "run_id": run_id,
                "pid": os.getpid(),
                "lock_token": token,
                "created_at": canonical_utc_now(),
            }
        )
        descriptor: int | None = None
        primary: BaseException | None = None
        created = False
        try:
            if _lstat(paths["lock_recovery"], label="state lock recovery marker") is not None:
                raise StateRecoveryRequiredError(
                    "state lock recovery marker exists; explicit recovery is required"
                )
            descriptor = os.open(
                path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | _BINARY_FLAG, 0o600
            )
            created = True
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short state lock write")
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            _sync_directory(paths["run_dir"], label="Run directory after state lock acquisition")
            if _lstat(paths["lock_recovery"], label="state lock recovery marker") is not None:
                raise StateRecoveryRequiredError(
                    "state lock recovery marker exists; explicit recovery is required"
                )
            yield
            persisted = self._read_regular(path, label="state lock")
            if persisted != data:
                raise StateConflictError(
                    "state lock ownership changed during the critical section; lock preserved"
                )
        except FileExistsError as exc:
            primary = exc
            raise StateConflictError("state lock already exists; automatic cleanup is deferred") from exc
        except BaseException as exc:
            primary = exc
            raise
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError as exc:
                    if primary is not None:
                        _add_cleanup_diagnostic(
                            primary, f"state lock descriptor cleanup failed: {exc}"
                        )
                    else:
                        raise StateStoreError("state lock descriptor cleanup failed") from exc
            if created:
                release_failure: StateStoreError | None = None
                try:
                    entry = _lstat(path, label="state lock")
                    if entry is None:
                        diagnostics = _restore_state_lock_evidence(
                            path, paths["run_dir"], data
                        )
                        failure = StateConflictError(
                            "state lock disappeared before owned release; "
                            "blocking evidence was restored"
                        )
                        for diagnostic in diagnostics:
                            _add_cleanup_diagnostic(failure, diagnostic)
                        raise failure
                    persisted = self._read_regular(path, label="state lock")
                    if persisted != data:
                        raise StateConflictError(
                            "state lock ownership changed before release; lock preserved"
                        )
                    path.unlink()
                    try:
                        _sync_directory(
                            paths["run_dir"],
                            label="Run directory after state lock release",
                        )
                    except Exception as sync_exc:
                        diagnostics = _restore_state_lock_evidence(
                            path, paths["run_dir"], data
                        )
                        if primary is not None:
                            _add_cleanup_diagnostic(
                                primary,
                                f"state lock release directory sync failed: {sync_exc}",
                            )
                            for diagnostic in diagnostics:
                                _add_cleanup_diagnostic(primary, diagnostic)
                        else:
                            release_failure = StateStoreError(
                                "state lock release is uncertain; blocking evidence was restored"
                            )
                            for diagnostic in diagnostics:
                                _add_cleanup_diagnostic(release_failure, diagnostic)
                            raise release_failure from sync_exc
                except Exception as exc:
                    if primary is not None:
                        _add_cleanup_diagnostic(primary, f"state lock cleanup failed: {exc}")
                    elif exc is release_failure:
                        raise
                    else:
                        raise StateStoreError("state lock cleanup failed") from exc

    def _read_regular(self, path: Path, *, label: str) -> bytes:
        try:
            _assert_plain_entry(path, label=label, directory=False)
            return path.read_bytes()
        except ActiveRunLockError as exc:
            raise StateConflictError(str(exc)) from exc
        except OSError as exc:
            raise StateConflictError(f"unable to read {label}: {path}") from exc

    def _atomic_replace(self, path: Path, data: bytes, *, label: str) -> None:
        temporary: Path | None = None
        replace_attempted = False
        primary: BaseException | None = None
        try:
            descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
            temporary = Path(name)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            replace_attempted = True
            os.replace(temporary, path)
            temporary = None
            _sync_directory(path.parent, label=f"{label} parent")
            persisted = self._read_regular(path, label=label)
            if persisted != data:
                raise StateConflictError(f"{label} bytes changed after atomic replace")
        except OSError as exc:
            error = StateStoreError(f"unable to atomically write {label}")
            error.write_state_uncertain = replace_attempted
            primary = error
            raise error from exc
        except BaseException as exc:
            primary = exc
            raise
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as exc:
                    if primary is not None:
                        _add_cleanup_diagnostic(
                            primary, f"temporary {label} cleanup failed: {exc}"
                        )
                    else:
                        error = StateStoreError(f"unable to clean temporary {label}")
                        error.write_state_uncertain = replace_attempted
                        raise error from exc

    def _read_state(self, run_id: str) -> tuple[dict[str, Any], bytes]:
        path = self._paths(run_id)["state"]
        data = self._read_regular(path, label="canonical state")
        value = _json_loads(data, label="canonical state")
        state = _validate_state(value, expected_run_id=run_id)
        if _canonical_json_bytes(state) != data:
            raise StateConflictError("canonical state must use canonical JSON")
        return state, data

    def _anchor_document(
        self,
        *,
        run_id: str,
        allocation_token: str,
        tail_record_index: int | None,
        tail_record_checksum: str | None,
        tail_file_size_bytes: int,
    ) -> dict[str, Any]:
        document = {
            "schema_version": STATE_JOURNAL_TAIL_SCHEMA_VERSION,
            "run_id": run_id,
            "allocation_token": allocation_token,
            "tail_record_index": tail_record_index,
            "tail_record_checksum": tail_record_checksum,
            "tail_file_size_bytes": tail_file_size_bytes,
            "anchor_checksum": None,
        }
        checksum_input = dict(document)
        checksum_input.pop("anchor_checksum")
        document["anchor_checksum"] = _sha256(_canonical_json_bytes(checksum_input))
        return document

    def _validate_anchor(
        self, value: Any, *, run_id: str, allocation_token: str
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping) or set(value) != _ANCHOR_FIELDS:
            raise StateConflictError("state journal tail fields are invalid")
        anchor = dict(value)
        if anchor["schema_version"] != STATE_JOURNAL_TAIL_SCHEMA_VERSION:
            raise StateConflictError("state journal tail schema_version is invalid")
        if anchor["run_id"] != run_id or anchor["allocation_token"] != allocation_token:
            raise StateConflictError("state journal tail identity does not match")
        index = anchor["tail_record_index"]
        checksum = anchor["tail_record_checksum"]
        size = anchor["tail_file_size_bytes"]
        if type(size) is not int or size < 0:
            raise StateConflictError("state journal tail byte size is invalid")
        if index is None:
            if checksum is not None or size != 0:
                raise StateConflictError("genesis state journal tail is invalid")
        else:
            if type(index) is not int or index < 0:
                raise StateConflictError("state journal tail record index is invalid")
            _validate_sha(checksum, label="state journal tail checksum")
            if size <= 0:
                raise StateConflictError("non-genesis state journal tail size is invalid")
        expected = self._anchor_document(
            run_id=run_id,
            allocation_token=allocation_token,
            tail_record_index=index,
            tail_record_checksum=checksum,
            tail_file_size_bytes=size,
        )["anchor_checksum"]
        if anchor["anchor_checksum"] != expected:
            raise StateConflictError("state journal tail anchor checksum does not match")
        return anchor

    def _read_anchor(self, run_id: str, allocation_token: str) -> tuple[dict[str, Any], bytes]:
        path = self._paths(run_id)["anchor"]
        data = self._read_regular(path, label="state journal tail")
        value = _json_loads(data, label="state journal tail")
        anchor = self._validate_anchor(value, run_id=run_id, allocation_token=allocation_token)
        if _canonical_json_bytes(anchor) != data:
            raise StateConflictError("state journal tail must use canonical JSON")
        return anchor, data

    def _write_anchor(self, run_id: str, anchor: Mapping[str, Any]) -> None:
        path = self._paths(run_id)["anchor"]
        data = _canonical_json_bytes(anchor)
        self._atomic_replace(path, data, label="state journal tail")

    def _record_checksum(self, record: Mapping[str, Any]) -> str:
        payload = dict(record)
        payload.pop("record_checksum", None)
        return _sha256(_canonical_json_bytes(payload))

    def _validate_record(
        self,
        value: Any,
        *,
        run_id: str,
        allocation_token: str,
        expected_index: int,
        previous_checksum: str | None,
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping) or set(value) != _JOURNAL_FIELDS:
            raise StateConflictError("state journal record fields are invalid")
        record = dict(value)
        if record["schema_version"] != STATE_JOURNAL_SCHEMA_VERSION:
            raise StateConflictError("state journal schema_version is invalid")
        if record["run_id"] != run_id or record["allocation_token"] != allocation_token:
            raise StateConflictError("state journal record identity does not match")
        if record["record_index"] != expected_index or type(record["record_index"]) is not int:
            raise StateConflictError("state journal record_index is not continuous")
        if record["previous_record_checksum"] != previous_checksum:
            raise StateConflictError("state journal previous checksum does not match")
        _validate_sha(record["record_checksum"], label="state journal record checksum")
        if self._record_checksum(record) != record["record_checksum"]:
            raise StateConflictError("state journal record checksum does not match")
        _validate_operation_id(record["operation_id"])
        try:
            _validate_uuid(record["operation_owner_lock_token"], label="operation owner token")
            _validate_uuid(record["append_actor_lock_token"], label="append actor token")
            _validate_timestamp(record["mutation_timestamp"], label="mutation timestamp")
            _validate_timestamp(record["journal_timestamp"], label="journal timestamp")
        except ActiveRunLockError as exc:
            raise StateConflictError(str(exc)) from exc
        if not isinstance(record["phase"], str) or record["phase"] not in {
            "pending",
            "committed",
            "aborted",
        }:
            raise StateConflictError("state journal phase is invalid")
        if record["append_actor_lock_token"] == record["operation_owner_lock_token"]:
            if record["recovery_audit_ref"] is not None:
                raise StateConflictError(
                    "owner-authored state journal records must not carry recovery audit evidence"
                )
        else:
            if record["recovery_audit_ref"] is None:
                raise StateConflictError(
                    "state journal append actor must match the operation owner without recovery audit evidence"
                )
            if record["phase"] not in {"committed", "aborted"}:
                raise StateConflictError(
                    "recovery actor may append only a terminal state journal record"
                )
            record["recovery_audit_ref"] = _validate_recovery_audit_ref(
                record["recovery_audit_ref"], run_id=run_id
            )
        if (
            not isinstance(record["mutation_kind"], str)
            or record["mutation_kind"] not in {"context_checkpoint", "status_transition"}
        ):
            raise StateConflictError("state journal mutation_kind is invalid")
        for field in ("expected_state_version", "resulting_state_version"):
            if type(record[field]) is not int or record[field] < 0:
                raise StateConflictError(f"state journal {field} is invalid")
        if record["resulting_state_version"] != record["expected_state_version"] + 1:
            raise StateConflictError("state journal resulting version is invalid")
        if (
            not isinstance(record["expected_status"], str)
            or record["expected_status"] not in STATUSES
            or not isinstance(record["resulting_status"], str)
            or record["resulting_status"] not in STATUSES
        ):
            raise StateConflictError("state journal status is invalid")
        if record["phase"] == "pending":
            if not isinstance(record["payload"], Mapping):
                raise StateConflictError("pending state journal record requires payload")
            _validate_json_value(record["payload"], label="state journal payload")
        elif record["payload"] is not None:
            raise StateConflictError("terminal state journal record payload must be null")
        _validate_sha(record["payload_sha256"], label="state journal payload SHA-256")
        _validate_sha(
            record["resulting_state_sha256"], label="state journal resulting state SHA-256"
        )
        if record["phase"] == "pending" and _sha256(
            _canonical_json_bytes(record["payload"])
        ) != record["payload_sha256"]:
            raise StateConflictError("state journal payload SHA-256 does not match")
        return record

    def _parse_journal_prefix(
        self,
        data: bytes,
        *,
        run_id: str,
        allocation_token: str,
    ) -> list[dict[str, Any]]:
        if len(data) > MAX_STATE_JOURNAL_BYTES:
            raise StateConflictError("state journal exceeds the A3.1 pilot byte limit")
        if data and not data.endswith(b"\n"):
            raise StateConflictError("anchored state journal must end with a newline")
        if data.count(b"\n") > MAX_STATE_JOURNAL_RECORDS:
            raise StateConflictError("state journal exceeds the A3.1 pilot record limit")
        records: list[dict[str, Any]] = []
        previous: str | None = None
        for index, line in enumerate(data.splitlines(keepends=True)):
            if not line.endswith(b"\n"):
                raise StateConflictError("state journal contains an incomplete anchored line")
            value = _json_loads(line, label=f"state journal line {index + 1}")
            record = self._validate_record(
                value,
                run_id=run_id,
                allocation_token=allocation_token,
                expected_index=index,
                previous_checksum=previous,
            )
            if _canonical_json_bytes(record) != line:
                raise StateConflictError("state journal record must use canonical JSON")
            records.append(record)
            previous = record["record_checksum"]
        self._validate_operation_sequences(records)
        return records

    def _validate_operation_sequences(self, records: list[dict[str, Any]]) -> None:
        operations: dict[tuple[str, str], list[dict[str, Any]]] = {}
        operation_kinds: dict[str, str] = {}
        for record in records:
            operation_id = record["operation_id"]
            previous_kind = operation_kinds.setdefault(operation_id, record["mutation_kind"])
            if previous_kind != record["mutation_kind"]:
                raise StateConflictError(
                    f"operation_id is reused across mutation kinds: {operation_id}"
                )
            key = (record["mutation_kind"], operation_id)
            operations.setdefault(key, []).append(record)
        for key, rows in operations.items():
            if rows[0]["phase"] != "pending" or len(rows) > 2:
                raise StateConflictError(f"state journal operation sequence is invalid: {key[1]}")
            if len(rows) == 2:
                if rows[1]["phase"] not in {"committed", "aborted"}:
                    raise StateConflictError(f"state journal terminal phase is invalid: {key[1]}")
                if rows[1]["record_index"] != rows[0]["record_index"] + 1:
                    raise StateConflictError("state journal operation rows must be contiguous")
                for field in (
                    "operation_owner_lock_token",
                    "mutation_timestamp",
                    "expected_state_version",
                    "resulting_state_version",
                    "expected_status",
                    "resulting_status",
                    "payload_sha256",
                    "resulting_state_sha256",
                ):
                    if rows[1][field] != rows[0][field]:
                        raise StateConflictError(
                            f"state journal terminal field changed for {key[1]}: {field}"
                        )
            elif rows[0] is not records[-1]:
                raise StateConflictError("unresolved pending operation must be the journal tail")

    def _operation_index(
        self, records: list[dict[str, Any]]
    ) -> dict[tuple[str, str], list[dict[str, Any]]]:
        index: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in records:
            index.setdefault((row["mutation_kind"], row["operation_id"]), []).append(row)
        return index

    def _validate_recovery_audit_bindings(
        self, state: Mapping[str, Any], records: list[dict[str, Any]]
    ) -> None:
        """Bind every recovery-authored terminal row to its immutable intent."""

        if not any(
            row["append_actor_lock_token"] != row["operation_owner_lock_token"]
            for row in records
        ):
            return
        try:
            from orchestrator.inspection_workflow.locking import (
                validate_recovery_audit_reference,
            )

            for row in records:
                if row["append_actor_lock_token"] == row["operation_owner_lock_token"]:
                    continue
                validate_recovery_audit_reference(
                    self.project_root,
                    run_id=state["run_id"],
                    allocation_token=state["allocation_token"],
                    operation_owner_lock_token=row["operation_owner_lock_token"],
                    append_actor_lock_token=row["append_actor_lock_token"],
                    recovery_audit_ref=row["recovery_audit_ref"],
                )
        except ActiveRunLockError as exc:
            raise StateConflictError(str(exc)) from exc

    def _validate_committed_state_binding(
        self, state: Mapping[str, Any], records: list[dict[str, Any]]
    ) -> None:
        committed = [row for row in records if row["phase"] == "committed"]
        if not committed:
            if (
                state["state_version"] != 0
                or state["status"] != "CREATED"
                or state["task_status"]
                or state["task_attempts"]
                or state["completed_tasks"]
                or state["failed_tasks"]
                or state["last_operation_kind"] is not None
                or state["last_operation_id"] is not None
                or state["last_operation_payload_sha256"] is not None
                or state["created_at"] != state["updated_at"]
            ):
                raise StateConflictError(
                    "canonical state is not a valid uncommitted CREATED baseline"
                )
            return
        latest = committed[-1]
        state_hash = _sha256(_canonical_json_bytes(state))
        if (
            state["state_version"] != latest["resulting_state_version"]
            or state["status"] != latest["resulting_status"]
            or state["last_operation_kind"] != latest["mutation_kind"]
            or state["last_operation_id"] != latest["operation_id"]
            or state["last_operation_payload_sha256"] != latest["payload_sha256"]
            or state_hash != latest["resulting_state_sha256"]
        ):
            raise StateConflictError("canonical state does not match committed journal tail")

    def _journal_state(
        self,
        run_id: str,
        allocation_token: str,
        *,
        repair_unanchored: bool,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], bytes]:
        paths = self._paths(run_id)
        anchor, _ = self._read_anchor(run_id, allocation_token)
        entry = _lstat(paths["journal"], label="state journal")
        if entry is None:
            journal = b""
        else:
            _assert_regular_journal_entry(entry, label="state journal")
            if entry.st_size > MAX_STATE_JOURNAL_BYTES:
                raise StateConflictError("state journal exceeds the A3.1 pilot byte limit")
            journal = self._read_regular(paths["journal"], label="state journal")
        anchor_size = anchor["tail_file_size_bytes"]
        if len(journal) < anchor_size:
            raise StateConflictError("state journal is shorter than its confirmed tail anchor")
        prefix = journal[:anchor_size]
        records = self._parse_journal_prefix(
            prefix, run_id=run_id, allocation_token=allocation_token
        )
        if anchor["tail_record_index"] is None:
            if records:
                raise StateConflictError("genesis anchor cannot cover journal records")
        else:
            if not records:
                raise StateConflictError("state journal tail anchor references a missing record")
            tail = records[-1]
            if (
                tail["record_index"] != anchor["tail_record_index"]
                or tail["record_checksum"] != anchor["tail_record_checksum"]
            ):
                raise StateConflictError("state journal tail does not match its anchor")
        suffix = journal[anchor_size:]
        if not suffix:
            return records, anchor, journal
        if not repair_unanchored:
            raise StateRecoveryRequiredError(
                "state journal has unconfirmed bytes; call recover_state_journal()"
            )
        newline_count = suffix.count(b"\n")
        if newline_count == 0:
            try:
                with paths["journal"].open("r+b") as handle:
                    handle.truncate(anchor_size)
                    handle.flush()
                    os.fsync(handle.fileno())
                _sync_directory(paths["run_dir"], label="Run directory after torn journal truncation")
            except OSError as exc:
                raise StateConflictError("unable to truncate torn state journal tail") from exc
            return records, anchor, prefix
        if newline_count != 1 or not suffix.endswith(b"\n"):
            raise StateConflictError("state journal contains more than one unconfirmed record")
        value = _json_loads(suffix, label="unconfirmed state journal record")
        record = self._validate_record(
            value,
            run_id=run_id,
            allocation_token=allocation_token,
            expected_index=len(records),
            previous_checksum=records[-1]["record_checksum"] if records else None,
        )
        if _canonical_json_bytes(record) != suffix:
            raise StateConflictError("unconfirmed state journal record is not canonical")
        candidate = [*records, record]
        self._validate_operation_sequences(candidate)
        next_anchor = self._anchor_document(
            run_id=run_id,
            allocation_token=allocation_token,
            tail_record_index=record["record_index"],
            tail_record_checksum=record["record_checksum"],
            tail_file_size_bytes=len(journal),
        )
        self._write_anchor(run_id, next_anchor)
        persisted_anchor, _ = self._read_anchor(run_id, allocation_token)
        if persisted_anchor != next_anchor:
            raise StateConflictError("state journal recovery anchor changed after advancement")
        return candidate, next_anchor, journal

    def _append_record(
        self,
        run_id: str,
        allocation_token: str,
        records: list[dict[str, Any]],
        record: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if len(records) >= MAX_STATE_JOURNAL_RECORDS:
            raise StateStoreError("state journal exceeds the A3.1 pilot record limit")
        paths = self._paths(run_id)
        entry = _lstat(paths["journal"], label="state journal")
        if entry is not None:
            _assert_regular_journal_entry(entry, label="state journal")
        record = dict(record)
        record["record_index"] = len(records)
        record["previous_record_checksum"] = (
            records[-1]["record_checksum"] if records else None
        )
        record["record_checksum"] = None
        record["record_checksum"] = self._record_checksum(record)
        validated = self._validate_record(
            record,
            run_id=run_id,
            allocation_token=allocation_token,
            expected_index=len(records),
            previous_checksum=record["previous_record_checksum"],
        )
        data = _canonical_json_bytes(validated)
        current_size = sum(len(_canonical_json_bytes(item)) for item in records)
        if current_size + len(data) > MAX_STATE_JOURNAL_BYTES:
            raise StateStoreError("state journal exceeds the A3.1 pilot byte limit")
        descriptor: int | None = None
        primary: BaseException | None = None
        try:
            descriptor = os.open(
                paths["journal"],
                os.O_CREAT | os.O_APPEND | os.O_WRONLY | _BINARY_FLAG,
                0o600,
            )
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short state journal write")
                view = view[written:]
            os.fsync(descriptor)
        except OSError as exc:
            error = StateStoreError("unable to append state journal record")
            primary = error
            raise error from exc
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError as exc:
                    if primary is not None:
                        _add_cleanup_diagnostic(
                            primary, f"state journal descriptor cleanup failed: {exc}"
                        )
                    else:
                        raise StateStoreError(
                            "state journal descriptor cleanup failed after append"
                        ) from exc
        expected_journal = b"".join(_canonical_json_bytes(item) for item in records) + data
        persisted_journal = self._read_regular(paths["journal"], label="state journal")
        if persisted_journal != expected_journal:
            raise StateConflictError(
                "state journal bytes changed before tail anchor advancement"
            )
        size = len(persisted_journal)
        anchor = self._anchor_document(
            run_id=run_id,
            allocation_token=allocation_token,
            tail_record_index=validated["record_index"],
            tail_record_checksum=validated["record_checksum"],
            tail_file_size_bytes=size,
        )
        self._write_anchor(run_id, anchor)
        persisted_anchor, _ = self._read_anchor(run_id, allocation_token)
        if persisted_anchor != anchor:
            raise StateConflictError("state journal tail anchor changed after advancement")
        return [*records, validated]

    def _base_record(
        self,
        *,
        state: Mapping[str, Any],
        operation_id: str,
        expected_lock_token: str,
        phase: str,
        mutation_kind: str,
        mutation_timestamp: str,
        expected_status: str,
        resulting_status: str,
        payload: Mapping[str, Any] | None,
        payload_sha256: str,
        resulting_state_sha256: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": STATE_JOURNAL_SCHEMA_VERSION,
            "run_id": state["run_id"],
            "allocation_token": state["allocation_token"],
            "record_index": 0,
            "previous_record_checksum": None,
            "record_checksum": None,
            "operation_id": operation_id,
            "operation_owner_lock_token": expected_lock_token,
            "append_actor_lock_token": expected_lock_token,
            "recovery_audit_ref": None,
            "phase": phase,
            "mutation_kind": mutation_kind,
            "mutation_timestamp": mutation_timestamp,
            "journal_timestamp": mutation_timestamp,
            "expected_state_version": state["state_version"],
            "resulting_state_version": state["state_version"] + 1,
            "expected_status": expected_status,
            "resulting_status": resulting_status,
            "payload": deepcopy(dict(payload)) if payload is not None else None,
            "payload_sha256": payload_sha256,
            "resulting_state_sha256": resulting_state_sha256,
        }

    def validate_initialized_run(
        self, *, run_id: str, allocation_token: str
    ) -> StateSnapshot:
        """Validate the durable CREATED state and genesis anchor required for running."""

        paths = self._paths(run_id)
        with self._state_lock(run_id):
            state, _ = self._read_state(run_id)
            if (
                state["allocation_token"] != allocation_token
                or state["status"] != "CREATED"
                or state["state_version"] != 0
                or state["last_operation_kind"] is not None
                or state["last_operation_id"] is not None
                or state["last_operation_payload_sha256"] is not None
            ):
                raise StateConflictError(
                    "Active Run Lock may enter running only from the initialized CREATED state"
                )
            anchor, _ = self._read_anchor(run_id, allocation_token)
            if any(
                anchor[name] is not None
                for name in ("tail_record_index", "tail_record_checksum")
            ) or anchor["tail_file_size_bytes"] != 0:
                raise StateConflictError(
                    "Active Run Lock requires a valid genesis state journal anchor"
                )
            journal_entry = _lstat(paths["journal"], label="state journal")
            if journal_entry is not None:
                _assert_regular_journal_entry(journal_entry, label="genesis state journal")
                try:
                    if paths["journal"].stat().st_size != 0:
                        raise StateConflictError("genesis state journal must be empty")
                except OSError as exc:
                    raise StateConflictError("unable to inspect genesis state journal") from exc
            self._validate_committed_state_binding(state, [])
            return _state_snapshot(state)

    def initialize_run(
        self,
        *,
        run_id: str,
        allocation_token: str,
        plan_fingerprint: str,
        task_plan: Sequence[Mapping[str, Any]],
        expected_lock_token: str,
        initial_context: Mapping[str, Any] | None = None,
        created_at: str | None = None,
    ) -> StateSnapshot:
        paths = self._paths(run_id)
        try:
            validate_active_run_lock(
                self.project_root,
                run_id=run_id,
                allocation_token=allocation_token,
                expected_lock_token=expected_lock_token,
                allowed_phases={"allocating"},
            )
        except ActiveRunLockError as exc:
            raise StateConflictError(str(exc)) from exc
        _validate_sha(plan_fingerprint, label="plan_fingerprint")
        try:
            _validate_uuid(allocation_token, label="allocation_token")
            _validate_uuid(expected_lock_token, label="expected_lock_token")
        except ActiveRunLockError as exc:
            raise StateStoreError(str(exc)) from exc
        normalized_plan = _validate_task_plan(task_plan)
        context = (
            {}
            if initial_context is None
            else _safe_deepcopy(dict(initial_context), label="initial_context")
        )
        if {"phase_a3_run_initialization", "phase_a3_checkpoint_events"} & set(context):
            raise StateStoreError("initial_context uses reserved Phase A3 checkpoint fields")
        _validate_json_value(context, label="initial_context")
        timestamp = canonical_utc_now() if created_at is None else created_at
        try:
            _validate_timestamp(timestamp, label="created_at")
        except ActiveRunLockError as exc:
            raise StateStoreError(str(exc)) from exc
        with self._state_lock(run_id):
            for name in ("state", "anchor", "recovery"):
                if _lstat(paths[name], label=name) is not None:
                    raise StateConflictError(f"cannot initialize Run with existing {name}")
            journal_entry = _lstat(paths["journal"], label="state journal")
            if journal_entry is not None:
                _assert_regular_journal_entry(journal_entry, label="initial state journal")
                try:
                    if paths["journal"].stat().st_size != 0:
                        raise StateConflictError("initial state journal must be empty")
                except OSError as exc:
                    raise StateConflictError("unable to inspect initial state journal") from exc
            state = {
                "schema_version": STATE_SCHEMA_VERSION,
                "run_id": run_id,
                "allocation_token": allocation_token,
                "plan_fingerprint": plan_fingerprint,
                "status": "CREATED",
                "state_version": 0,
                "created_at": timestamp,
                "updated_at": timestamp,
                "task_plan": normalized_plan,
                "task_status": {},
                "task_attempts": {},
                "completed_tasks": [],
                "failed_tasks": [],
                "context": context,
                "last_operation_kind": None,
                "last_operation_id": None,
                "last_operation_payload_sha256": None,
            }
            state = _validate_state(state, expected_run_id=run_id)
            anchor = self._anchor_document(
                run_id=run_id,
                allocation_token=allocation_token,
                tail_record_index=None,
                tail_record_checksum=None,
                tail_file_size_bytes=0,
            )
            self._atomic_replace(paths["state"], _canonical_json_bytes(state), label="canonical state")
            self._write_anchor(run_id, anchor)
            persisted, _ = self._read_state(run_id)
            persisted_anchor, _ = self._read_anchor(run_id, allocation_token)
            if persisted != state or persisted_anchor != anchor:
                raise StateConflictError("Run initialization bytes do not match expected state")
            return _state_snapshot(persisted)

    def validate_takeover_preflight(
        self, *, run_id: str, allocation_token: str
    ) -> StateSnapshot:
        """Read canonical State and its anchored Journal before lock replacement."""

        with self._state_lock(run_id):
            state, _ = self._read_state(run_id)
            if state["allocation_token"] != allocation_token:
                raise StateConflictError("stale takeover allocation_token does not match state")
            records, _, _ = self._journal_state(
                run_id, allocation_token, repair_unanchored=False
            )
            self._validate_recovery_audit_bindings(state, records)
            unresolved = [rows for rows in self._operation_index(records).values() if len(rows) == 1]
            if len(unresolved) > 1:
                raise StateConflictError("more than one unresolved state operation exists")
            if not unresolved:
                self._validate_committed_state_binding(state, records)
            return _state_snapshot(state)

    def _recover_unfinished_operations_locked(
        self,
        *,
        run_id: str,
        expected_lock_token: str,
        recovery_audit_ref: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        state, _ = self._read_state(run_id)
        try:
            lock = validate_active_run_lock(
                self.project_root,
                run_id=run_id,
                allocation_token=state["allocation_token"],
                expected_lock_token=expected_lock_token,
                allowed_phases={"running", "recovering"},
            )
        except ActiveRunLockError as exc:
            raise StateConflictError(str(exc)) from exc
        records, _, _ = self._journal_state(
            run_id,
            state["allocation_token"],
            repair_unanchored=True,
        )
        self._validate_recovery_audit_bindings(state, records)
        normalized_recovery_ref: dict[str, str] | None = None
        if recovery_audit_ref is not None:
            normalized_recovery_ref = _validate_recovery_audit_ref(
                recovery_audit_ref, run_id=run_id
            )
            if (
                lock["phase"] != "recovering"
                or lock["recovery_of_lock_token"] is None
                or lock["recovery_intent_path"] != normalized_recovery_ref["path"]
                or lock["recovery_intent_sha256"] != normalized_recovery_ref["sha256"]
            ):
                raise StateConflictError(
                    "recovering Active Run Lock does not bind the recovery audit"
                )
            try:
                from orchestrator.inspection_workflow.locking import (
                    validate_recovery_audit_reference,
                )

                validate_recovery_audit_reference(
                    self.project_root,
                    run_id=run_id,
                    allocation_token=state["allocation_token"],
                    operation_owner_lock_token=lock["recovery_of_lock_token"],
                    append_actor_lock_token=expected_lock_token,
                    recovery_audit_ref=normalized_recovery_ref,
                )
            except ActiveRunLockError as exc:
                raise StateConflictError(str(exc)) from exc
        operation_index = self._operation_index(records)
        pending = [
            rows[0]
            for rows in operation_index.values()
            if len(rows) == 1 and rows[0]["phase"] == "pending"
        ]
        if len(pending) > 1:
            raise StateConflictError("more than one unresolved state operation exists")
        if not pending:
            self._validate_committed_state_binding(state, records)
            return state, records
        row = pending[0]
        if recovery_audit_ref is None:
            if row["operation_owner_lock_token"] != expected_lock_token:
                raise StateRecoveryDeferredError(
                    "pending operation belongs to an older lock token; takeover recovery is required"
                )
            if lock["phase"] == "recovering":
                raise StateRecoveryDeferredError(
                    "recovering lock terminal records require recovery audit evidence"
                )
        else:
            assert normalized_recovery_ref is not None
            if lock["recovery_of_lock_token"] != row["operation_owner_lock_token"]:
                raise StateConflictError(
                    "recovering Active Run Lock does not bind the pending operation audit"
                )
        state_bytes = _canonical_json_bytes(state)
        applied = (
            state["state_version"] == row["resulting_state_version"]
            and state["status"] == row["resulting_status"]
            and state["last_operation_kind"] == row["mutation_kind"]
            and state["last_operation_id"] == row["operation_id"]
            and state["last_operation_payload_sha256"] == row["payload_sha256"]
            and _sha256(state_bytes) == row["resulting_state_sha256"]
        )
        unapplied = (
            state["state_version"] == row["expected_state_version"]
            and state["status"] == row["expected_status"]
            and state["last_operation_id"] != row["operation_id"]
        )
        if applied:
            terminal_phase = "committed"
        elif unapplied:
            terminal_phase = "aborted"
        else:
            raise StateConflictError("pending state operation cannot be reconciled with canonical state")
        terminal = dict(row)
        terminal.update(
            {
                "phase": terminal_phase,
                "payload": None,
                "record_checksum": None,
                "journal_timestamp": row["mutation_timestamp"],
                "append_actor_lock_token": expected_lock_token,
                "recovery_audit_ref": normalized_recovery_ref,
            }
        )
        records = self._append_record(
            run_id, state["allocation_token"], records, terminal
        )
        self._validate_committed_state_binding(state, records)
        return state, records

    def recover_state_journal(
        self, *, run_id: str, expected_lock_token: str
    ) -> StateSnapshot:
        with self._state_lock(run_id):
            state, _ = self._recover_unfinished_operations_locked(
                run_id=run_id, expected_lock_token=expected_lock_token
            )
            return _state_snapshot(state)

    def recover_taken_over_state_journal(
        self,
        *,
        run_id: str,
        expected_lock_token: str,
        recovery_audit_ref: Mapping[str, Any],
    ) -> StateSnapshot:
        """Append one audited terminal row under an already recovered lock."""

        with self._state_lock(run_id):
            state, _ = self._recover_unfinished_operations_locked(
                run_id=run_id,
                expected_lock_token=expected_lock_token,
                recovery_audit_ref=recovery_audit_ref,
            )
            return _state_snapshot(state)

    def recover(self, *, run_id: str, expected_lock_token: str) -> StateSnapshot:
        return self.recover_state_journal(
            run_id=run_id, expected_lock_token=expected_lock_token
        )

    def load(self, *, run_id: str) -> StateSnapshot:
        state, _ = self._read_state(run_id)
        records, _, _ = self._journal_state(
            run_id, state["allocation_token"], repair_unanchored=False
        )
        self._validate_recovery_audit_bindings(state, records)
        operation_index = self._operation_index(records)
        if any(len(rows) == 1 for rows in operation_index.values()):
            raise StateRecoveryRequiredError(
                "state journal has an unresolved pending operation; call recover_state_journal()"
            )
        self._validate_committed_state_binding(state, records)
        return _state_snapshot(state)

    def _validate_checkpoint_payload(
        self,
        *,
        run_id: str,
        operation_id: str,
        mutation_timestamp: str,
        payload: Mapping[str, Any],
        state: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise StateStoreError("checkpoint payload must be an object")
        payload = _safe_deepcopy(dict(payload), label="checkpoint payload")
        kind = payload.get("checkpoint_kind")
        if not isinstance(kind, str) or kind not in CHECKPOINT_KINDS:
            raise StateStoreError("checkpoint_kind is invalid")
        expected_fields = {
            "checkpoint_kind",
            "task_id",
            "attempt_number",
            "expected_task_status",
            "next_task_status",
            "retry_disposition",
            "next_attempt_number",
            "controlled_context_delta",
            "error_summary",
            "created_at",
        }
        if set(payload) != expected_fields:
            raise StateStoreError(f"checkpoint payload fields are invalid for {kind}")
        try:
            _validate_timestamp(payload["created_at"], label="checkpoint created_at")
        except ActiveRunLockError as exc:
            raise StateStoreError(str(exc)) from exc
        if payload["created_at"] != mutation_timestamp:
            raise StateStoreError("checkpoint mutation_timestamp must equal payload created_at")
        delta = payload["controlled_context_delta"]
        if not isinstance(delta, Mapping):
            raise StateStoreError("controlled_context_delta must be an object")
        delta = dict(delta)
        payload["controlled_context_delta"] = delta
        _validate_json_value(delta, label="controlled_context_delta")
        task_id = payload["task_id"]
        attempt = payload["attempt_number"]
        if kind == "run_initialized":
            if task_id is not None or attempt is not None:
                raise StateStoreError("run_initialized task_id and attempt_number must be null")
            if set(delta) != {"task_plan", "plan_fingerprint"}:
                raise StateStoreError("run_initialized controlled_context_delta is invalid")
            if _validate_task_plan(delta["task_plan"]) != state["task_plan"]:
                raise StateConflictError("run_initialized task_plan does not match canonical plan")
            if delta["plan_fingerprint"] != state["plan_fingerprint"]:
                raise StateConflictError(
                    "run_initialized plan_fingerprint does not match canonical state"
                )
            expected_id = f"run:{run_id}:checkpoint:run_initialized"
        else:
            try:
                task_id = _validate_identifier(task_id, label="checkpoint task_id")
            except ActiveRunLockError as exc:
                raise StateStoreError(str(exc)) from exc
            if task_id not in {item["task_id"] for item in state["task_plan"]}:
                raise StateStoreError("checkpoint task_id is not in task_plan")
            if type(attempt) is not int or attempt < 0:
                raise StateStoreError("checkpoint attempt_number is invalid")
            suffix = {
                "task_skipped": "skipped",
                "task_cache_hit": "cache_hit",
                "task_started": "started",
                "task_succeeded": "succeeded",
                "task_failed": "failed",
                "task_retry_scheduled": "retry_scheduled",
            }[kind]
            expected_id = f"run:{run_id}:task:{task_id}:attempt:{attempt}:{suffix}"
        if operation_id != expected_id:
            if not (
                kind == "task_started"
                and operation_id.startswith(expected_id)
                and _ABORTED_START_SUFFIX_RE.search(operation_id)
            ):
                raise StateStoreError(f"operation_id does not match {kind}")

        disposition = payload["retry_disposition"]
        expected_status = payload["expected_task_status"]
        next_status = payload["next_task_status"]
        next_attempt = payload["next_attempt_number"]
        error_summary = payload["error_summary"]
        if kind == "run_initialized":
            expected_contract = (None, None, "none", None)
        elif kind == "task_skipped":
            expected_contract = ("pending", "skipped", "none", None)
        elif kind == "task_cache_hit":
            expected_contract = ("pending", "success", "none", None)
        elif kind == "task_started":
            if expected_status not in {"pending", "retry_scheduled"}:
                raise StateStoreError("task_started expected_task_status is invalid")
            expected_contract = (expected_status, "running", "none", None)
        elif kind == "task_succeeded":
            expected_contract = ("running", "success", "none", None)
        elif kind == "task_failed":
            if disposition not in {"retry", "terminal"}:
                raise StateStoreError("task_failed retry_disposition is invalid")
            expected_contract = (
                "running",
                "retry_pending" if disposition == "retry" else "failed",
                disposition,
                attempt + 1 if disposition == "retry" else None,
            )
        else:
            expected_contract = ("retry_pending", "retry_scheduled", "retry", attempt + 1)
        if (expected_status, next_status, disposition, next_attempt) != expected_contract:
            raise StateStoreError(f"checkpoint lifecycle fields are invalid for {kind}")

        delta_fields = {
            "run_initialized": {"task_plan", "plan_fingerprint"},
            "task_skipped": {"skip_reason"},
            "task_cache_hit": {"task_output", "cache_provenance"},
            "task_started": set(),
            "task_succeeded": {"task_output"},
            "task_failed": {"failure_provenance"},
            "task_retry_scheduled": {
                "failed_operation_id",
                "retry_policy_sha256",
                "backoff_seconds",
            },
        }[kind]
        if set(delta) != delta_fields:
            raise StateStoreError(f"controlled_context_delta fields are invalid for {kind}")
        if kind == "task_skipped" and (
            not isinstance(delta["skip_reason"], str) or not delta["skip_reason"].strip()
        ):
            raise StateStoreError("task_skipped requires a non-empty skip_reason")
        if kind in {"task_cache_hit", "task_succeeded"}:
            if not isinstance(delta["task_output"], Mapping) or not delta["task_output"]:
                raise StateStoreError(f"{kind} requires non-empty task_output provenance")
        if kind == "task_cache_hit" and (
            not isinstance(delta["cache_provenance"], Mapping)
            or not delta["cache_provenance"]
        ):
            raise StateStoreError("task_cache_hit requires non-empty cache_provenance")
        if kind == "task_failed" and (
            not isinstance(delta["failure_provenance"], Mapping)
            or not delta["failure_provenance"]
        ):
            raise StateStoreError("task_failed requires non-empty failure_provenance")
        if kind == "task_retry_scheduled":
            failed_id = delta["failed_operation_id"]
            expected_failed_id = f"run:{run_id}:task:{task_id}:attempt:{attempt}:failed"
            if failed_id != expected_failed_id:
                raise StateStoreError(
                    "task_retry_scheduled must bind the committed task_failed operation"
                )
            _validate_sha(delta["retry_policy_sha256"], label="retry policy SHA-256")
            if type(delta["backoff_seconds"]) is not int or delta["backoff_seconds"] < 0:
                raise StateStoreError("retry backoff_seconds is invalid")
        if kind == "task_failed":
            if (
                not isinstance(error_summary, str)
                or not error_summary.strip()
                or len(error_summary) > MAX_CHECKPOINT_ERROR_SUMMARY_CHARS
                or _ABSOLUTE_PATH_RE.search(error_summary)
            ):
                raise StateStoreError(
                    "task_failed error_summary must be bounded, non-empty, and path-neutral"
                )
        elif error_summary is not None:
            raise StateStoreError(f"{kind} error_summary must be null")
        _validate_json_value(payload, label="checkpoint payload")
        return payload

    def _reduce_checkpoint(self, state: dict[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
        result = _safe_deepcopy(state, label="checkpoint state")
        kind = payload["checkpoint_kind"]
        if kind == "run_initialized":
            if result["status"] != "PLANNED":
                raise StateConflictError("run_initialized is only allowed in PLANNED")
            if result["task_status"] or result["task_attempts"]:
                raise StateConflictError("run_initialized may only initialize an empty task map")
            task_ids = [item["task_id"] for item in result["task_plan"]]
            result["task_status"] = {task_id: "pending" for task_id in task_ids}
            result["task_attempts"] = {task_id: 0 for task_id in task_ids}
            if "phase_a3_run_initialization" in result["context"]:
                raise StateConflictError("run_initialized context already exists")
            result["context"]["phase_a3_run_initialization"] = deepcopy(
                dict(payload["controlled_context_delta"])
            )
        else:
            if result["status"] != "RUNNING":
                raise StateConflictError("task checkpoints are only allowed in RUNNING")
            if not result["task_status"]:
                raise StateConflictError("task checkpoint requires committed run_initialized")
            task_id = payload["task_id"]
            attempt = payload["attempt_number"]
            current_status = result["task_status"][task_id]
            current_attempt = result["task_attempts"][task_id]
            if current_status != payload["expected_task_status"]:
                raise StateConflictError("checkpoint expected_task_status does not match state")
            if kind == "task_skipped":
                if attempt != 0 or current_status != "pending" or current_attempt != 0:
                    raise StateConflictError("task_skipped state or attempt is invalid")
                result["task_status"][task_id] = "skipped"
            elif kind == "task_cache_hit":
                if attempt != 0 or current_status != "pending" or current_attempt != 0:
                    raise StateConflictError("task_cache_hit state or attempt is invalid")
                result["task_status"][task_id] = "success"
            elif kind == "task_started":
                expected_attempt = 1 if current_status == "pending" else current_attempt + 1
                if current_status not in {"pending", "retry_scheduled"} or attempt != expected_attempt:
                    raise StateConflictError("task_started attempt is not the next canonical attempt")
                result["task_status"][task_id] = "running"
                result["task_attempts"][task_id] = attempt
            elif kind == "task_succeeded":
                if current_status != "running" or current_attempt != attempt or attempt < 1:
                    raise StateConflictError("task_succeeded does not match a running attempt")
                result["task_status"][task_id] = "success"
            elif kind == "task_failed":
                if current_status != "running" or current_attempt != attempt or attempt < 1:
                    raise StateConflictError("task_failed does not match a running attempt")
                result["task_status"][task_id] = (
                    "retry_pending"
                    if payload["retry_disposition"] == "retry"
                    else "failed"
                )
            elif kind == "task_retry_scheduled":
                if current_status != "retry_pending" or current_attempt != attempt or attempt < 1:
                    raise StateConflictError("task_retry_scheduled does not follow retryable failure")
                events = result["context"].get("phase_a3_checkpoint_events", {})
                failed = events.get(payload["controlled_context_delta"]["failed_operation_id"])
                if not isinstance(failed, Mapping) or failed.get("checkpoint_kind") != "task_failed":
                    raise StateConflictError(
                        "task_retry_scheduled does not bind a committed task_failed checkpoint"
                    )
                result["task_status"][task_id] = "retry_scheduled"
            if result["task_status"][task_id] != payload["next_task_status"]:
                raise StateConflictError("checkpoint next_task_status does not match reduction")
            events = result["context"].setdefault("phase_a3_checkpoint_events", {})
            if not isinstance(events, dict):
                raise StateConflictError("reserved checkpoint event context is invalid")
            operation_id = (
                f"run:{result['run_id']}:task:{task_id}:attempt:{attempt}:"
                + {
                    "task_skipped": "skipped",
                    "task_cache_hit": "cache_hit",
                    "task_started": "started",
                    "task_succeeded": "succeeded",
                    "task_failed": "failed",
                    "task_retry_scheduled": "retry_scheduled",
                }[kind]
            )
            if operation_id in events:
                raise StateConflictError("checkpoint event already exists in canonical context")
            events[operation_id] = {
                "checkpoint_kind": kind,
                "task_id": task_id,
                "attempt_number": attempt,
                "controlled_context_delta": deepcopy(
                    dict(payload["controlled_context_delta"])
                ),
                "error_summary": payload["error_summary"],
                "created_at": payload["created_at"],
            }
        result["completed_tasks"] = sorted(
            task_id for task_id, status in result["task_status"].items() if status == "success"
        )
        result["failed_tasks"] = sorted(
            task_id for task_id, status in result["task_status"].items() if status == "failed"
        )
        return result

    def _canonical_json_document(self, data: bytes, *, label: str) -> dict[str, Any]:
        value = _json_loads(data, label=label)
        if not isinstance(value, Mapping):
            raise StateConflictError(f"{label} must be a JSON object")
        document = dict(value)
        if _canonical_json_bytes(document) != data:
            raise StateConflictError(f"{label} must use canonical JSON")
        return document

    def _validate_completion_evidence(
        self, state: Mapping[str, Any], evidence: Any
    ) -> dict[str, Any]:
        if not isinstance(evidence, Mapping) or set(evidence) != _COMPLETION_FIELDS:
            raise StateConflictError("completion_evidence fields are invalid")
        result = _safe_deepcopy(dict(evidence), label="completion_evidence")
        if result["schema_version"] != COMPLETION_EVIDENCE_SCHEMA_VERSION:
            raise StateConflictError("completion_evidence schema_version is invalid")
        if result["run_id"] != state["run_id"] or result["plan_fingerprint"] != state["plan_fingerprint"]:
            raise StateConflictError("completion_evidence identity does not match canonical state")
        required = sorted(item["task_id"] for item in state["task_plan"] if item["required"])
        if result["required_task_ids"] != required:
            raise StateConflictError("completion_evidence required_task_ids are not authoritative")
        if set(state["task_status"]) != {item["task_id"] for item in state["task_plan"]}:
            raise StateConflictError("completion requires the committed complete task plan")
        if any(state["task_status"][task_id] != "success" for task_id in required):
            raise StateConflictError("all required tasks must be successful before COMPLETED")
        if any(status not in {"success", "skipped"} for status in state["task_status"].values()):
            raise StateConflictError("completion cannot contain unfinished or failed tasks")
        if state["failed_tasks"] or state["completed_tasks"] != sorted(
            task_id for task_id, status in state["task_status"].items() if status == "success"
        ):
            raise StateConflictError("completion task indexes are inconsistent")
        from orchestrator.inspection_workflow.publication import (
            FINAL_SUMMARY_PATH_TEMPLATE,
            PUBLICATION_MANIFEST_PATH,
            PUBLICATION_TRANSACTION_PATH_TEMPLATE,
            validate_publication,
        )

        fixed = {
            "publication_manifest_path": PUBLICATION_MANIFEST_PATH,
            "publication_transaction_path": PUBLICATION_TRANSACTION_PATH_TEMPLATE.format(
                run_id=state["run_id"]
            ),
            "final_summary_path": FINAL_SUMMARY_PATH_TEMPLATE.format(run_id=state["run_id"]),
        }
        for field, expected in fixed.items():
            if result[field] != expected:
                raise StateConflictError(f"completion_evidence {field} is not fixed")
        for field in (
            "publication_manifest_sha256",
            "publication_transaction_sha256",
            "final_summary_sha256",
        ):
            _validate_sha(result[field], label=field)
        if not isinstance(result["transaction_id"], str) or not result["transaction_id"]:
            raise StateConflictError("completion_evidence transaction_id is invalid")
        try:
            publication = validate_publication(
                self.project_root,
                run_id=state["run_id"],
                plan_fingerprint=state["plan_fingerprint"],
            )
        except Exception as exc:
            raise StateConflictError(
                "A2 validate_publication rejected completion evidence"
            ) from exc
        manifest_bytes = publication["manifest_bytes"]
        if _sha256(manifest_bytes) != result["publication_manifest_sha256"]:
            raise StateConflictError("completion_evidence publication Manifest SHA-256 does not match")
        transaction_path = self.project_root.joinpath(*result["publication_transaction_path"].split("/"))
        summary_path = self.project_root.joinpath(*result["final_summary_path"].split("/"))
        transaction_bytes = self._read_regular(transaction_path, label="publication transaction")
        summary_bytes = self._read_regular(summary_path, label="final_summary")
        if _sha256(transaction_bytes) != result["publication_transaction_sha256"]:
            raise StateConflictError("completion_evidence publication transaction SHA-256 does not match")
        if _sha256(summary_bytes) != result["final_summary_sha256"]:
            raise StateConflictError("completion_evidence final_summary SHA-256 does not match")
        if publication["manifest"].get("transaction_id") != result["transaction_id"]:
            raise StateConflictError("completion_evidence transaction_id does not match Manifest")
        return result

    def _reduce_transition(
        self,
        state: dict[str, Any],
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        result = _safe_deepcopy(state, label="status transition state")
        next_status = payload["next_status"]
        if result["status"] in TERMINAL_STATUSES:
            raise StateConflictError("terminal canonical state cannot transition")
        if (result["status"], next_status) not in ALLOWED_TRANSITIONS:
            raise StateConflictError("canonical state transition is not allowed")
        if next_status == "RUNNING" and result["status"] == "PLANNED":
            if not result["task_status"]:
                raise StateConflictError("PLANNED -> RUNNING requires committed run_initialized")
        if next_status == "COMPLETED":
            self._validate_completion_evidence(result, payload["completion_evidence"])
        elif payload["completion_evidence"] is not None:
            raise StateStoreError("completion_evidence is only allowed for RUNNING -> COMPLETED")
        metadata = payload["metadata"]
        if metadata is not None:
            result["context"]["last_transition_metadata"] = _safe_deepcopy(
                dict(metadata), label="transition metadata"
            )
        result["status"] = next_status
        return result

    def _validate_transition_payload(
        self,
        *,
        state: Mapping[str, Any],
        expected_status: str,
        expected_state_version: int,
        operation_id: str,
        mutation_timestamp: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        fields = {"next_status", "metadata", "completion_evidence", "transition_kind", "decision_token"}
        if not isinstance(payload, Mapping) or set(payload) != fields:
            raise StateStoreError("status transition payload fields are invalid")
        result = _safe_deepcopy(dict(payload), label="status transition payload")
        next_status = result["next_status"]
        if not isinstance(next_status, str) or next_status not in STATUSES:
            raise StateStoreError("status transition next_status is invalid")
        kind = result["transition_kind"]
        decision_token = result["decision_token"]
        if kind == "auto":
            if decision_token is not None:
                raise StateStoreError("automatic transition decision_token must be null")
            expected_id = (
                f"run:{state['run_id']}:transition:v{expected_state_version}:"
                f"{expected_status}:{next_status}:auto"
            )
        elif kind == "decision":
            try:
                decision_token = _validate_uuid(decision_token, label="decision_token")
            except ActiveRunLockError as exc:
                raise StateStoreError(str(exc)) from exc
            expected_id = (
                f"run:{state['run_id']}:transition:v{expected_state_version}:"
                f"{expected_status}:{next_status}:decision:{decision_token}"
            )
        else:
            raise StateStoreError("transition_kind must be auto or decision")
        if operation_id != expected_id:
            raise StateStoreError("transition operation_id is not canonical")
        if result["metadata"] is not None and not isinstance(result["metadata"], Mapping):
            raise StateStoreError("transition metadata must be an object or null")
        _validate_json_value(result, label="status transition payload")
        try:
            _validate_timestamp(mutation_timestamp, label="transition mutation_timestamp")
        except ActiveRunLockError as exc:
            raise StateStoreError(str(exc)) from exc
        return result

    def _mutate(
        self,
        *,
        run_id: str,
        expected_lock_token: str,
        expected_status: str,
        expected_state_version: int,
        operation_id: str,
        mutation_timestamp: str,
        mutation_kind: str,
        payload: Mapping[str, Any],
        reducer: Any,
    ) -> StateMutationResult:
        _validate_operation_id(operation_id)
        if not isinstance(expected_status, str) or expected_status not in STATUSES:
            raise StateStoreError("expected_status is invalid")
        if type(expected_state_version) is not int or expected_state_version < 0:
            raise StateStoreError("expected_state_version is invalid")
        try:
            _validate_uuid(expected_lock_token, label="expected_lock_token")
            _validate_timestamp(mutation_timestamp, label="mutation_timestamp")
        except ActiveRunLockError as exc:
            raise StateStoreError(str(exc)) from exc
        payload_hash = _sha256(_canonical_json_bytes(payload))
        with self._state_lock(run_id):
            state, records = self._recover_unfinished_operations_locked(
                run_id=run_id, expected_lock_token=expected_lock_token
            )
            if (
                mutation_kind == "context_checkpoint"
                and payload.get("checkpoint_kind") == "task_started"
            ):
                canonical_start_id = self._task_start_operation_id_from_records(
                    run_id=run_id,
                    task_id=payload["task_id"],
                    attempt_number=payload["attempt_number"],
                    records=records,
                )
                if operation_id != canonical_start_id:
                    raise StateConflictError(
                        "task_started operation_id is not the canonical aborted-start successor"
                    )
            operation_index = self._operation_index(records)
            rows = operation_index.get((mutation_kind, operation_id), [])
            if rows:
                pending = rows[0]
                if pending["payload_sha256"] != payload_hash:
                    raise StateConflictError("operation_id was reused with a different payload")
                if rows[-1]["phase"] == "aborted":
                    raise StateConflictError("aborted operation_id cannot be reused")
                if rows[-1]["phase"] == "committed":
                    if (
                        state["state_version"] != pending["resulting_state_version"]
                        or _sha256(_canonical_json_bytes(state))
                        != pending["resulting_state_sha256"]
                    ):
                        raise StateConflictError("committed operation does not match canonical state")
                    return _mutation_result(state, operation_id)
            if mutation_kind == "status_transition":
                for row in records:
                    if (
                        row["phase"] == "aborted"
                        and row["mutation_kind"] == "status_transition"
                        and row["expected_state_version"] == expected_state_version
                        and row["expected_status"] == expected_status
                        and row["resulting_status"] == payload.get("next_status")
                    ):
                        raise StateConflictError(
                            "aborted status transition semantics cannot be retried under a new operation_id"
                        )
            if state["status"] in TERMINAL_STATUSES:
                raise StateConflictError("terminal canonical state cannot be mutated")
            if state["status"] != expected_status or state["state_version"] != expected_state_version:
                raise StateConflictError("canonical state CAS version or status does not match")
            try:
                validate_active_run_lock(
                    self.project_root,
                    run_id=run_id,
                    allocation_token=state["allocation_token"],
                    expected_lock_token=expected_lock_token,
                    allowed_phases={"running"},
                )
            except ActiveRunLockError as exc:
                raise StateConflictError(str(exc)) from exc
            result = reducer(_safe_deepcopy(state, label="state mutation input"), payload)
            result["state_version"] = state["state_version"] + 1
            result["updated_at"] = mutation_timestamp
            result["last_operation_kind"] = mutation_kind
            result["last_operation_id"] = operation_id
            result["last_operation_payload_sha256"] = payload_hash
            result = _validate_state(result, expected_run_id=run_id)
            resulting_status = result["status"]
            result_hash = _sha256(_canonical_json_bytes(result))
            pending_record = self._base_record(
                state=state,
                operation_id=operation_id,
                expected_lock_token=expected_lock_token,
                phase="pending",
                mutation_kind=mutation_kind,
                mutation_timestamp=mutation_timestamp,
                expected_status=expected_status,
                resulting_status=resulting_status,
                payload=payload,
                payload_sha256=payload_hash,
                resulting_state_sha256=result_hash,
            )
            records = self._append_record(
                run_id, state["allocation_token"], records, pending_record
            )
            self._atomic_replace(
                self._paths(run_id)["state"],
                _canonical_json_bytes(result),
                label="canonical state",
            )
            persisted, persisted_bytes = self._read_state(run_id)
            if persisted != result or _sha256(persisted_bytes) != result_hash:
                raise StateConflictError("canonical state does not match pending result")
            terminal = dict(pending_record)
            terminal.update(
                {
                    "phase": "committed",
                    "payload": None,
                    "record_checksum": None,
                }
            )
            self._append_record(
                run_id, state["allocation_token"], records, terminal
            )
            return _mutation_result(persisted, operation_id)

    @staticmethod
    def _task_start_operation_id_from_records(
        *,
        run_id: str,
        task_id: str,
        attempt_number: int,
        records: list[dict[str, Any]],
    ) -> str:
        base = (
            f"run:{run_id}:task:{task_id}:attempt:{attempt_number}:started"
        )
        pending_by_operation: dict[str, Mapping[str, Any]] = {}
        latest_aborted: dict[str, Any] | None = None
        for row in records:
            if row["mutation_kind"] != "context_checkpoint":
                continue
            operation_id = row["operation_id"]
            if row["phase"] == "pending":
                payload = row["payload"]
                if isinstance(payload, Mapping):
                    pending_by_operation[operation_id] = payload
                continue
            if row["phase"] != "aborted" or not operation_id.startswith(base):
                continue
            payload = pending_by_operation.get(operation_id)
            if (
                isinstance(payload, Mapping)
                and payload.get("checkpoint_kind") == "task_started"
                and payload.get("task_id") == task_id
                and payload.get("attempt_number") == attempt_number
            ):
                latest_aborted = row
        if latest_aborted is None:
            return base
        return f"{base}:after_aborted:{latest_aborted['record_checksum']}"

    def next_task_start_operation_id(
        self,
        *,
        run_id: str,
        task_id: str,
        attempt_number: int,
        expected_lock_token: str,
    ) -> str:
        """Return the only operation ID allowed after an aborted task start."""

        with self._state_lock(run_id):
            state, _ = self._read_state(run_id)
            try:
                validate_active_run_lock(
                    self.project_root,
                    run_id=run_id,
                    allocation_token=state["allocation_token"],
                    expected_lock_token=expected_lock_token,
                    allowed_phases={"running"},
                )
            except ActiveRunLockError as exc:
                raise StateConflictError(str(exc)) from exc
            records, _, _ = self._journal_state(
                run_id,
                state["allocation_token"],
                repair_unanchored=False,
            )
            self._validate_recovery_audit_bindings(state, records)
            if any(
                len(rows) == 1 for rows in self._operation_index(records).values()
            ):
                raise StateRecoveryRequiredError(
                    "unresolved pending operation must be recovered before task start"
                )
            self._validate_committed_state_binding(state, records)
            return self._task_start_operation_id_from_records(
                run_id=run_id,
                task_id=task_id,
                attempt_number=attempt_number,
                records=records,
            )

    def checkpoint_context(
        self,
        *,
        run_id: str,
        expected_lock_token: str,
        expected_status: str,
        expected_state_version: int,
        operation_id: str,
        mutation_timestamp: str,
        payload: Mapping[str, Any],
    ) -> StateMutationResult:
        state, _ = self._read_state(run_id)
        normalized = self._validate_checkpoint_payload(
            run_id=run_id,
            operation_id=operation_id,
            mutation_timestamp=mutation_timestamp,
            payload=payload,
            state=state,
        )
        return self._mutate(
            run_id=run_id,
            expected_lock_token=expected_lock_token,
            expected_status=expected_status,
            expected_state_version=expected_state_version,
            operation_id=operation_id,
            mutation_timestamp=mutation_timestamp,
            mutation_kind="context_checkpoint",
            payload=normalized,
            reducer=self._reduce_checkpoint,
        )

    def transition_status(
        self,
        *,
        run_id: str,
        expected_lock_token: str,
        expected_status: str,
        expected_state_version: int,
        operation_id: str,
        mutation_timestamp: str,
        payload: Mapping[str, Any],
    ) -> StateMutationResult:
        state, _ = self._read_state(run_id)
        normalized = self._validate_transition_payload(
            state=state,
            expected_status=expected_status,
            expected_state_version=expected_state_version,
            operation_id=operation_id,
            mutation_timestamp=mutation_timestamp,
            payload=payload,
        )
        return self._mutate(
            run_id=run_id,
            expected_lock_token=expected_lock_token,
            expected_status=expected_status,
            expected_state_version=expected_state_version,
            operation_id=operation_id,
            mutation_timestamp=mutation_timestamp,
            mutation_kind="status_transition",
            payload=normalized,
            reducer=self._reduce_transition,
        )
