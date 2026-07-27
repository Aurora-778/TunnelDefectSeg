"""Phase A3.1 Active Run Lock foundation.

The lock is intentionally local and conservative.  It serializes one Run on a
single local filesystem, never guesses that an owner is stale, and leaves
recovery/tombstone evidence in place when cleanup cannot be proven complete.
Automatic takeover is deferred to A3.2.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import stat
import tempfile
from typing import Any
import uuid


ACTIVE_RUN_LOCK_SCHEMA_VERSION = "active_run_lock_v2"
ACTIVE_RUN_LOCK_PATH = "runs/.active_run.lock"
ACTIVE_RUN_RECOVERY_LOCK_PATH = "runs/.active_run.recovery.lock"
ACTIVE_RUN_RELEASE_PREFIX = ".active_run.release."
ACTIVE_RUN_PHASES = frozenset({"allocating", "running", "recovering"})
_BINARY_FLAG = getattr(os, "O_BINARY", 0)

_LOCK_FIELDS = frozenset(
    {
        "schema_version",
        "phase",
        "allocation_token",
        "reserved_run_id",
        "run_id",
        "task_id",
        "pid",
        "hostname",
        "lock_token",
        "recovery_of_lock_token",
        "recovery_token",
        "recovery_intent_path",
        "recovery_intent_sha256",
        "created_at",
    }
)
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")


class ActiveRunLockError(RuntimeError):
    """Raised when Active Run Lock ownership or durability is uncertain."""


def canonical_utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _validate_timestamp(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not _TIMESTAMP_RE.fullmatch(value):
        raise ActiveRunLockError(f"{label} must be a canonical UTC timestamp")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise ActiveRunLockError(f"{label} must be a canonical UTC timestamp") from exc
    return value


def _validate_identifier(value: Any, *, label: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ActiveRunLockError(f"{label} is invalid")
    return value


def _validate_uuid(value: Any, *, label: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise ActiveRunLockError(f"{label} must be a canonical UUID")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ActiveRunLockError(f"{label} must be a canonical UUID") from exc
    if str(parsed) != value:
        raise ActiveRunLockError(f"{label} must be a canonical UUID")
    return value


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ActiveRunLockError("Active Run Lock JSON is not canonicalizable") from exc
    return (text + "\n").encode("utf-8")


def _is_reparse(entry: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(entry, "st_file_attributes", 0)
    return bool(attributes & flag)


def _lstat(path: Path, *, label: str) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ActiveRunLockError(f"unable to inspect {label}: {path}") from exc


def _assert_plain_entry(path: Path, *, label: str, directory: bool | None = None) -> None:
    entry = _lstat(path, label=label)
    if entry is None:
        raise ActiveRunLockError(f"missing {label}: {path}")
    if stat.S_ISLNK(entry.st_mode) or _is_reparse(entry):
        raise ActiveRunLockError(f"{label} must not be a symlink or reparse point: {path}")
    if directory is True and not stat.S_ISDIR(entry.st_mode):
        raise ActiveRunLockError(f"{label} must be a directory: {path}")
    if directory is False and not stat.S_ISREG(entry.st_mode):
        raise ActiveRunLockError(f"{label} must be a regular file: {path}")


def _assert_project_path(project_root: Path, target: Path, *, include_leaf: bool, label: str) -> None:
    root = Path(project_root).absolute()
    target = Path(target).absolute()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ActiveRunLockError(f"{label} must stay inside project_root") from exc
    _assert_plain_entry(root, label="project_root", directory=True)
    relative = target.relative_to(root)
    current = root
    parts = relative.parts if include_leaf else relative.parts[:-1]
    for part in parts:
        current = current / part
        entry = _lstat(current, label=label)
        if entry is None:
            continue
        if stat.S_ISLNK(entry.st_mode) or _is_reparse(entry):
            raise ActiveRunLockError(f"{label} crosses a symlink or reparse point: {current}")
        if current != target and not stat.S_ISDIR(entry.st_mode):
            raise ActiveRunLockError(f"{label} parent must be a directory: {current}")


def _sync_directory(path: Path, *, label: str) -> None:
    """Apply a local metadata barrier without claiming multi-host durability."""

    if os.name != "nt":
        descriptor: int | None = None
        try:
            descriptor = os.open(path, os.O_RDONLY)
            os.fsync(descriptor)
        except OSError as exc:
            raise ActiveRunLockError(f"unable to sync {label}: {path}") from exc
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        return

    # CPython has no portable Windows directory fsync.  A same-directory,
    # write-through rename sentinel is the strongest available local barrier.
    import ctypes
    from ctypes import wintypes

    descriptor: int | None = None
    source: Path | None = None
    destination: Path | None = None
    primary: BaseException | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".a3-dirsync-", dir=path)
        source = Path(name)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        destination = source.with_suffix(source.suffix + ".synced")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        kernel32.MoveFileExW.restype = wintypes.BOOL
        MOVEFILE_REPLACE_EXISTING = 0x1
        MOVEFILE_WRITE_THROUGH = 0x8
        if not kernel32.MoveFileExW(
            str(source), str(destination), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH
        ):
            raise OSError(ctypes.get_last_error(), "MoveFileExW failed")
        source = None
    except OSError as exc:
        error = ActiveRunLockError(f"unable to sync {label}: {path}")
        primary = error
        raise error from exc
    except BaseException as exc:
        primary = exc
        raise
    finally:
        cleanup_errors: list[OSError] = []
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as exc:
                cleanup_errors.append(exc)
        for candidate in (source, destination):
            if candidate is None:
                continue
            try:
                candidate.unlink(missing_ok=True)
            except OSError as exc:
                cleanup_errors.append(exc)
        if cleanup_errors:
            message = f"unable to clean directory sync sentinel for {label}: {path}"
            if primary is not None:
                primary.add_note(f"{message}: {cleanup_errors[0]}")
            else:
                raise ActiveRunLockError(message) from cleanup_errors[0]


def _prepare_runs_dir(project_root: Path) -> tuple[Path, Path]:
    root = Path(project_root).absolute()
    _assert_plain_entry(root, label="project_root", directory=True)
    runs = root / "runs"
    _assert_project_path(root, runs, include_leaf=False, label="runs directory")
    try:
        runs.mkdir(parents=False, exist_ok=True)
    except OSError as exc:
        raise ActiveRunLockError("unable to create runs directory") from exc
    _assert_plain_entry(runs, label="runs directory", directory=True)
    return root, runs


def _lock_path(project_root: Path) -> tuple[Path, Path, Path]:
    root, runs = _prepare_runs_dir(project_root)
    path = runs / ".active_run.lock"
    _assert_project_path(root, path, include_leaf=False, label="Active Run Lock")
    return root, runs, path


def _validate_lock_document(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _LOCK_FIELDS:
        raise ActiveRunLockError("Active Run Lock fields are invalid")
    lock = dict(value)
    if lock["schema_version"] != ACTIVE_RUN_LOCK_SCHEMA_VERSION:
        raise ActiveRunLockError("Active Run Lock schema_version is invalid")
    phase = lock["phase"]
    if not isinstance(phase, str) or phase not in ACTIVE_RUN_PHASES:
        raise ActiveRunLockError("Active Run Lock phase is invalid")
    _validate_uuid(lock["allocation_token"], label="allocation_token")
    _validate_uuid(lock["lock_token"], label="lock_token")
    _validate_identifier(lock["task_id"], label="task_id")
    _validate_identifier(lock["reserved_run_id"], label="reserved_run_id", nullable=True)
    _validate_identifier(lock["run_id"], label="run_id", nullable=True)
    if type(lock["pid"]) is not int or lock["pid"] <= 0:
        raise ActiveRunLockError("Active Run Lock pid is invalid")
    if not isinstance(lock["hostname"], str) or not lock["hostname"]:
        raise ActiveRunLockError("Active Run Lock hostname is invalid")
    _validate_timestamp(lock["created_at"], label="created_at")
    recovery_fields = (
        "recovery_of_lock_token",
        "recovery_token",
        "recovery_intent_path",
        "recovery_intent_sha256",
    )
    if phase != "recovering" and any(lock[name] is not None for name in recovery_fields):
        raise ActiveRunLockError("ordinary Active Run Lock recovery fields must be null")
    if phase == "recovering":
        _validate_uuid(lock["recovery_of_lock_token"], label="recovery_of_lock_token")
        _validate_uuid(lock["recovery_token"], label="recovery_token")
        if not isinstance(lock["recovery_intent_path"], str) or not lock["recovery_intent_path"]:
            raise ActiveRunLockError("recovering lock requires recovery_intent_path")
        if not isinstance(lock["recovery_intent_sha256"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", lock["recovery_intent_sha256"]
        ):
            raise ActiveRunLockError("recovering lock requires recovery_intent_sha256")
    if lock["run_id"] is not None and lock["reserved_run_id"] != lock["run_id"]:
        raise ActiveRunLockError("Active Run Lock run_id must match reserved_run_id")
    return lock


def _read_lock(path: Path) -> tuple[dict[str, Any], bytes]:
    _assert_plain_entry(path, label="Active Run Lock", directory=False)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ActiveRunLockError("unable to read Active Run Lock") from exc
    try:
        value = json.loads(data.decode("utf-8"), parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)))
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ActiveRunLockError("Active Run Lock is invalid JSON") from exc
    lock = _validate_lock_document(value)
    if _canonical_json_bytes(lock) != data:
        raise ActiveRunLockError("Active Run Lock must use canonical JSON")
    return lock, data


def _reject_recovery_or_release_entries(runs: Path) -> None:
    recovery = runs / ".active_run.recovery.lock"
    if _lstat(recovery, label="Active Run recovery lock") is not None:
        raise ActiveRunLockError("Active Run recovery lock exists; A3.2 recovery is required")
    try:
        entries = list(runs.iterdir())
    except OSError as exc:
        raise ActiveRunLockError("unable to inspect Active Run release tombstones") from exc
    if any(entry.name.startswith(ACTIVE_RUN_RELEASE_PREFIX) for entry in entries):
        raise ActiveRunLockError("Active Run release tombstone exists; cleanup recovery is required")


def read_active_run_lock(project_root: Path) -> dict[str, Any]:
    _, _, path = _lock_path(project_root)
    lock, _ = _read_lock(path)
    return deepcopy(lock)


def _cleanup_failed_acquisition(path: Path, runs: Path, expected_bytes: bytes) -> list[str]:
    diagnostics: list[str] = []
    try:
        entry = _lstat(path, label="failed Active Run Lock acquisition")
        if entry is None:
            return diagnostics
        if stat.S_ISLNK(entry.st_mode) or _is_reparse(entry) or not stat.S_ISREG(entry.st_mode):
            return ["failed Active Run Lock entry changed type and was preserved"]
        current_bytes = path.read_bytes()
        if current_bytes != expected_bytes:
            return ["failed Active Run Lock ownership bytes changed and were preserved"]
        path.unlink()
        _sync_directory(runs, label="runs directory after failed lock acquisition")
    except Exception as exc:
        diagnostics.append(f"failed Active Run Lock cleanup was incomplete: {exc}")
    return diagnostics


def acquire_active_run_lock(
    project_root: Path,
    *,
    task_id: str,
    allocation_token: str,
    lock_token: str,
    created_at: str | None = None,
    pid: int | None = None,
    hostname: str | None = None,
) -> dict[str, Any]:
    root, runs, path = _lock_path(project_root)
    _reject_recovery_or_release_entries(runs)
    document = {
        "schema_version": ACTIVE_RUN_LOCK_SCHEMA_VERSION,
        "phase": "allocating",
        "allocation_token": allocation_token,
        "reserved_run_id": None,
        "run_id": None,
        "task_id": task_id,
        "pid": os.getpid() if pid is None else pid,
        "hostname": socket.gethostname() if hostname is None else hostname,
        "lock_token": lock_token,
        "recovery_of_lock_token": None,
        "recovery_token": None,
        "recovery_intent_path": None,
        "recovery_intent_sha256": None,
        "created_at": canonical_utc_now() if created_at is None else created_at,
    }
    document = _validate_lock_document(document)
    data = _canonical_json_bytes(document)
    descriptor: int | None = None
    created = False
    primary: BaseException | None = None
    try:
        descriptor = os.open(
            path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | _BINARY_FLAG, 0o600
        )
        created = True
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short Active Run Lock write")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        _sync_directory(runs, label="runs directory after Active Run Lock acquisition")
        current, current_bytes = _read_lock(path)
        if current_bytes != data or current["lock_token"] != lock_token:
            raise ActiveRunLockError("Active Run Lock changed during acquisition")
        _reject_recovery_or_release_entries(runs)
        return deepcopy(current)
    except FileExistsError as exc:
        raise ActiveRunLockError("an Active Run Lock already exists") from exc
    except OSError as exc:
        error = ActiveRunLockError("unable to acquire Active Run Lock")
        primary = error
        if created:
            for diagnostic in _cleanup_failed_acquisition(path, runs, data):
                error.add_note(diagnostic)
        raise error from exc
    except BaseException as exc:
        primary = exc
        if created:
            for diagnostic in _cleanup_failed_acquisition(path, runs, data):
                exc.add_note(diagnostic)
        raise
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as exc:
                if primary is not None:
                    primary.add_note(f"Active Run Lock descriptor cleanup failed: {exc}")
                else:
                    raise ActiveRunLockError(
                        "Active Run Lock descriptor cleanup failed"
                    ) from exc


def _atomic_update_lock(
    project_root: Path,
    *,
    expected_allocation_token: str,
    expected_lock_token: str,
    update: Mapping[str, Any],
) -> dict[str, Any]:
    root, runs, path = _lock_path(project_root)
    current, _ = _read_lock(path)
    if current["allocation_token"] != expected_allocation_token or current["lock_token"] != expected_lock_token:
        raise ActiveRunLockError("Active Run Lock ownership does not match")
    next_value = dict(current)
    next_value.update(update)
    next_value = _validate_lock_document(next_value)
    data = _canonical_json_bytes(next_value)
    temporary: Path | None = None
    primary: BaseException | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".active-run-lock-", suffix=".tmp", dir=runs)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        latest, _ = _read_lock(path)
        if latest["allocation_token"] != expected_allocation_token or latest["lock_token"] != expected_lock_token:
            raise ActiveRunLockError("Active Run Lock changed before update")
        os.replace(temporary, path)
        temporary = None
        _sync_directory(runs, label="runs directory after Active Run Lock update")
        persisted, persisted_bytes = _read_lock(path)
        if persisted_bytes != data:
            raise ActiveRunLockError("Active Run Lock update did not persist expected bytes")
        return deepcopy(persisted)
    except OSError as exc:
        error = ActiveRunLockError("unable to update Active Run Lock")
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
                    primary.add_note(f"Active Run Lock update temporary cleanup failed: {exc}")
                else:
                    raise ActiveRunLockError(
                        "Active Run Lock update temporary cleanup failed"
                    ) from exc


def reserve_active_run_id(
    project_root: Path,
    *,
    run_id: str,
    expected_allocation_token: str,
    expected_lock_token: str,
) -> dict[str, Any]:
    _validate_identifier(run_id, label="run_id")
    current = read_active_run_lock(project_root)
    if current["phase"] != "allocating" or current["reserved_run_id"] is not None:
        raise ActiveRunLockError("Active Run Lock is not ready to reserve a Run ID")
    return _atomic_update_lock(
        project_root,
        expected_allocation_token=expected_allocation_token,
        expected_lock_token=expected_lock_token,
        update={"reserved_run_id": run_id},
    )


def mark_active_run_running(
    project_root: Path,
    *,
    run_id: str,
    expected_allocation_token: str,
    expected_lock_token: str,
) -> dict[str, Any]:
    current = read_active_run_lock(project_root)
    if current["phase"] != "allocating" or current["reserved_run_id"] != run_id:
        raise ActiveRunLockError("Active Run Lock cannot enter running for this Run")
    if (
        current["allocation_token"] != expected_allocation_token
        or current["lock_token"] != expected_lock_token
    ):
        raise ActiveRunLockError("Active Run Lock ownership does not match")
    try:
        # Local import avoids making StateStore module initialization cyclic.
        from orchestrator.state.store import StateStore, StateStoreError

        StateStore(project_root).validate_initialized_run(
            run_id=run_id,
            allocation_token=expected_allocation_token,
        )
    except StateStoreError as exc:
        raise ActiveRunLockError(
            "Active Run Lock requires a valid CREATED state and genesis anchor"
        ) from exc
    return _atomic_update_lock(
        project_root,
        expected_allocation_token=expected_allocation_token,
        expected_lock_token=expected_lock_token,
        update={"phase": "running", "run_id": run_id},
    )


def validate_active_run_lock(
    project_root: Path,
    *,
    run_id: str,
    allocation_token: str,
    expected_lock_token: str,
    allowed_phases: set[str] | frozenset[str],
) -> dict[str, Any]:
    if (
        not isinstance(allowed_phases, (set, frozenset))
        or not allowed_phases
        or not all(isinstance(phase, str) for phase in allowed_phases)
        or not allowed_phases.issubset(ACTIVE_RUN_PHASES)
    ):
        raise ActiveRunLockError("allowed Active Run Lock phases are invalid")
    lock = read_active_run_lock(project_root)
    if (
        lock["allocation_token"] != allocation_token
        or lock["lock_token"] != expected_lock_token
        or lock["reserved_run_id"] != run_id
        or lock["phase"] not in allowed_phases
    ):
        raise ActiveRunLockError("Active Run Lock fencing check failed")
    if lock["phase"] == "running" and lock["run_id"] != run_id:
        raise ActiveRunLockError("running Active Run Lock does not identify the requested Run")
    return lock


def release_active_run_lock(
    project_root: Path,
    *,
    run_id: str,
    expected_allocation_token: str,
    expected_lock_token: str,
) -> dict[str, Any]:
    root, runs, path = _lock_path(project_root)
    current, current_bytes = _read_lock(path)
    if (
        current["allocation_token"] != expected_allocation_token
        or current["lock_token"] != expected_lock_token
        or current["reserved_run_id"] != run_id
    ):
        raise ActiveRunLockError("cannot release an Active Run Lock owned by another caller")
    tombstone = runs / f"{ACTIVE_RUN_RELEASE_PREFIX}{expected_lock_token}.json"
    if _lstat(tombstone, label="Active Run release tombstone") is not None:
        raise ActiveRunLockError("Active Run release tombstone already exists")
    try:
        os.rename(path, tombstone)
        _sync_directory(runs, label="runs directory after Active Run Lock isolation")
        isolated_lock, isolated_bytes = _read_lock(tombstone)
        if isolated_bytes != current_bytes or isolated_lock["lock_token"] != expected_lock_token:
            raise ActiveRunLockError("Active Run release tombstone does not match the owned lock")
        tombstone.unlink()
        _sync_directory(runs, label="runs directory after Active Run Lock release")
    except OSError as exc:
        raise ActiveRunLockError(
            f"Active Run Lock release is incomplete; inspect tombstone {tombstone.name}"
        ) from exc
    return {"released": True, "run_id": run_id, "lock_token": expected_lock_token}
