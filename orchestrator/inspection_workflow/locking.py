"""Phase A3.2 Active Run Lock and explicit stale-owner recovery.

The lock is intentionally local and conservative.  It serializes one Run on a
single local filesystem, permits only an explicit same-host dead-PID takeover,
and leaves recovery/tombstone evidence in place when cleanup cannot be proven
complete. It is not wired into legacy execution or a background controller.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import tempfile
from typing import Any
import uuid

from . import controlled_fs

ACTIVE_RUN_LOCK_SCHEMA_VERSION = "active_run_lock_v2"
ACTIVE_RUN_LOCK_PATH = "runs/.active_run.lock"
ACTIVE_RUN_RECOVERY_LOCK_PATH = "runs/.active_run.recovery.lock"
ACTIVE_RUN_RELEASE_PREFIX = ".active_run.release."
ACTIVE_RUN_RECOVERY_LOCK_SCHEMA_VERSION = "active_run_recovery_lock_v1"
ACTIVE_RUN_RECOVERY_AUDIT_SCHEMA_VERSION = "active_run_recovery_audit_v1"
ACTIVE_RUN_PHASES = frozenset({"allocating", "running", "recovering"})
_BINARY_FLAG = getattr(os, "O_BINARY", 0)
_ACTIVE_RUN_STATE_LOCK_NAME = ".active_run.state.lock"
_PROCESS_OWNED_ACTIVE_RUN_LOCK_IDENTITIES: set[tuple[str, str, str]] = set()
_PROCESS_OWNED_ACTIVE_RUN_LOCK_SNAPSHOTS: dict[
    tuple[str, str, str], tuple[bytes, tuple[int, int]]
] = {}

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
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")

_RECOVERY_LOCK_FIELDS = frozenset(
    {
        "schema_version",
        "recovery_token",
        "pid",
        "hostname",
        "target_lock_token",
        "target_lock_sha256",
        "run_id",
        "created_at",
    }
)
_RECOVERY_INTENT_FIELDS = frozenset(
    {
        "schema_version",
        "run_id",
        "allocation_token",
        "old_lock_token",
        "new_lock_token",
        "target_lock_sha256",
        "recovery_token",
        "hostname",
        "pid",
        "actor_kind",
        "operator_identity",
        "reason",
        "created_at",
        "validation_results",
        "intent_checksum",
    }
)
_RECOVERY_OUTCOME_FIELDS = frozenset(
    {
        "schema_version",
        "run_id",
        "allocation_token",
        "intent_path",
        "intent_sha256",
        "recovery_token",
        "actor_kind",
        "operator_identity",
        "state_sha256",
        "state_version",
        "state_status",
        "journal_anchor_sha256",
        "publication_status",
        "publication_transaction_id",
        "publication_manifest_sha256",
        "recovering_lock_sha256",
        "successor_lock_sha256",
        "successor_phase",
        "created_at",
        "outcome_checksum",
    }
)


class ActiveRunLockError(RuntimeError):
    """Raised when Active Run Lock ownership or durability is uncertain."""


class ActiveRunRecoveryRequiredError(ActiveRunLockError):
    """Raised when Active Run recovery residue blocks ordinary acquisition."""


def _add_exception_note(error: BaseException, message: str) -> None:
    """Retain cleanup diagnostics on Python versions before add_note()."""

    add_note = getattr(error, "add_note", None)
    if callable(add_note):
        add_note(message)
        return
    try:
        notes = getattr(error, "__notes__", None)
        if notes is None:
            notes = []
            setattr(error, "__notes__", notes)
        notes.append(message)
    except (AttributeError, TypeError):
        error.args = (*error.args, message)


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


def _validate_sha256(value: Any, *, label: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ActiveRunLockError(f"{label} must be a lowercase SHA-256")
    return value


def _owner_pid_is_confirmed_dead(pid: int) -> bool:
    """Return true only for a local PID the OS confirms no longer exists."""

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except OSError:
        return False
    return False


def _safe_recovery_basename(recovery_token: str, created_at: str) -> str:
    _validate_uuid(recovery_token, label="recovery_token")
    _validate_timestamp(created_at, label="recovery created_at")
    stamp = created_at.replace("-", "").replace(":", "").replace(".", "")
    return f"{stamp}.{recovery_token}"


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ActiveRunLockError("Active Run Lock JSON is not canonicalizable") from exc
    return (text + "\n").encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
        primary: BaseException | None = None
        try:
            descriptor = os.open(path, os.O_RDONLY)
            os.fsync(descriptor)
        except OSError as exc:
            error = ActiveRunLockError(f"unable to sync {label}: {path}")
            primary = error
            raise error from exc
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError as exc:
                    if primary is not None:
                        _add_exception_note(primary, f"directory sync descriptor close failed: {exc}")
                    else:
                        raise ActiveRunLockError(
                            f"unable to close directory sync descriptor for {label}"
                        ) from exc
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
                _add_exception_note(primary, f"{message}: {cleanup_errors[0]}")
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


def _restore_blocking_evidence(
    path: Path, runs: Path, data: bytes, *, label: str
) -> list[str]:
    """Best-effort restoration after a removal barrier becomes uncertain."""

    diagnostics: list[str] = []
    root = runs.parent
    try:
        if _lstat(path, label=label) is None:
            controlled_fs.write_exclusive(root, path.relative_to(root).as_posix(), data)
    except FileExistsError:
        pass
    except Exception as exc:
        diagnostics.append(f"unable to restore blocking {label}: {exc}")
        diagnostics.extend(_write_active_recovery_marker(runs, data))
    return diagnostics


def _write_active_recovery_marker(runs: Path, data: bytes) -> list[str]:
    """Leave the existing A3.2 recovery sentinel when tombstone restore fails."""

    marker = runs / ".active_run.recovery.lock"
    diagnostics: list[str] = []
    root = runs.parent
    try:
        if _lstat(marker, label="Active Run recovery lock") is not None:
            return diagnostics
        controlled_fs.write_exclusive(root, marker.relative_to(root).as_posix(), data)
    except Exception as exc:
        diagnostics.append(f"unable to write Active Run recovery marker: {exc}")
    return diagnostics


def _remove_owned_state_lock(
    path: Path,
    runs: Path,
    data: bytes,
    expected_identity: tuple[int, int],
) -> None:
    try:
        entry = _lstat(path, label="Active Run state lock")
        if entry is None:
            error = ActiveRunLockError(
                "Active Run state lock disappeared before release; "
                "blocking residue was restored"
            )
            for diagnostic in _restore_blocking_evidence(
                path, runs, data, label="Active Run state lock"
            ):
                _add_exception_note(error, diagnostic)
            raise error
        _assert_plain_entry(path, label="Active Run state lock", directory=False)
        if path.read_bytes() != data:
            raise ActiveRunLockError(
                "Active Run state lock ownership changed before release; lock preserved"
            )
        try:
            controlled_fs.unlink(
                runs.parent,
                path.relative_to(runs.parent).as_posix(),
                expected_identity=expected_identity,
            )
        except Exception as exc:
            error = ActiveRunLockError(
                "Active Run state lock release is uncertain; blocking residue was restored"
            )
            _add_exception_note(error, f"primary state lock cleanup failure: {exc}")
            for diagnostic in _restore_blocking_evidence(
                path, runs, data, label="Active Run state lock"
            ):
                _add_exception_note(error, diagnostic)
            raise error from exc
    except ActiveRunLockError:
        raise
    except (OSError, ValueError) as exc:
        raise ActiveRunLockError("unable to release Active Run state lock") from exc


@contextmanager
def _active_run_state_lock(project_root: Path):
    """Serialize Active Run document transitions without stale-lock takeover."""

    root, runs, _ = _lock_path(project_root)
    path = runs / _ACTIVE_RUN_STATE_LOCK_NAME
    _assert_project_path(root, path, include_leaf=False, label="Active Run state lock")
    data = _canonical_json_bytes(
        {
            "created_at": canonical_utc_now(),
            "lock_token": str(uuid.uuid4()),
            "pid": os.getpid(),
        }
    )
    established = False
    established_identity: tuple[int, int] | None = None
    try:
        established_identity = controlled_fs.write_exclusive(
            root, path.relative_to(root).as_posix(), data
        )
        _assert_plain_entry(path, label="Active Run state lock", directory=False)
        if path.read_bytes() != data:
            raise ActiveRunLockError("Active Run state lock changed during acquisition")
        established = True
    except FileExistsError as exc:
        raise ActiveRunLockError(
            "Active Run Lock state transition is handled by another worker"
        ) from exc
    except (OSError, controlled_fs.ControlledFilesystemError) as exc:
        error = ActiveRunLockError("unable to acquire Active Run state lock")
        try:
            established = (
                _lstat(path, label="Active Run state lock") is not None
                and path.read_bytes() == data
            )
        except Exception:
            established = False
        if established:
            try:
                if established_identity is not None:
                    _remove_owned_state_lock(
                        path, runs, data, established_identity
                    )
                    established = False
            except Exception as cleanup_exc:
                _add_exception_note(error, f"Active Run state lock cleanup failed: {cleanup_exc}")
        raise error from exc

    primary: BaseException | None = None
    try:
        yield
        _assert_plain_entry(path, label="Active Run state lock", directory=False)
        if path.read_bytes() != data:
            raise ActiveRunLockError(
                "Active Run state lock ownership changed during the critical section"
            )
    except BaseException as exc:
        primary = exc
        raise
    finally:
        if established:
            try:
                if established_identity is None:
                    raise ActiveRunLockError(
                        "Active Run state lock publication identity is uncertain; lock preserved"
                    )
                _remove_owned_state_lock(
                    path, runs, data, established_identity
                )
            except Exception as exc:
                if primary is not None:
                    _add_exception_note(primary, f"Active Run state lock cleanup failed: {exc}")
                    for note in getattr(exc, "__notes__", ()):
                        _add_exception_note(primary, str(note))
                else:
                    raise


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
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ActiveRunLockError("Active Run Lock is invalid JSON") from exc
    lock = _validate_lock_document(value)
    if _canonical_json_bytes(lock) != data:
        raise ActiveRunLockError("Active Run Lock must use canonical JSON")
    return lock, data


def _write_exclusive_document(path: Path, data: bytes, *, label: str) -> None:
    """Persist one immutable recovery document without following a link."""

    try:
        runs = next((parent for parent in path.parents if parent.name == "runs"), None)
        if runs is None:
            raise ActiveRunLockError(f"{label} is outside the controlled runs directory")
        root = runs.parent
        controlled_fs.write_exclusive(root, path.relative_to(root).as_posix(), data)
        _assert_plain_entry(path, label=label, directory=False)
        if path.read_bytes() != data:
            raise ActiveRunLockError(f"{label} changed after persistence")
    except FileExistsError as exc:
        raise ActiveRunLockError(f"{label} already exists") from exc
    except OSError as exc:
        raise ActiveRunLockError(f"unable to persist {label}") from exc
    except controlled_fs.ControlledFilesystemError as exc:
        raise ActiveRunLockError(f"unable to safely persist {label}") from exc


def _read_canonical_document(
    path: Path,
    *,
    label: str,
    fields: frozenset[str],
) -> tuple[dict[str, Any], bytes]:
    _assert_plain_entry(path, label=label, directory=False)
    try:
        data = path.read_bytes()
        value = json.loads(
            data.decode("utf-8"),
            parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ActiveRunLockError(f"{label} is invalid") from exc
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ActiveRunLockError(f"{label} fields are invalid")
    document = dict(value)
    if _canonical_json_bytes(document) != data:
        raise ActiveRunLockError(f"{label} must use canonical JSON")
    return document, data


def _validate_recovery_lock_document(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _RECOVERY_LOCK_FIELDS:
        raise ActiveRunLockError("Active Run recovery lock fields are invalid")
    document = dict(value)
    if document["schema_version"] != ACTIVE_RUN_RECOVERY_LOCK_SCHEMA_VERSION:
        raise ActiveRunLockError("Active Run recovery lock schema_version is invalid")
    _validate_uuid(document["recovery_token"], label="recovery_token")
    _validate_uuid(document["target_lock_token"], label="target_lock_token")
    _validate_sha256(document["target_lock_sha256"], label="target_lock_sha256")
    _validate_identifier(document["run_id"], label="run_id")
    if type(document["pid"]) is not int or document["pid"] <= 0:
        raise ActiveRunLockError("Active Run recovery lock pid is invalid")
    if not isinstance(document["hostname"], str) or not document["hostname"]:
        raise ActiveRunLockError("Active Run recovery lock hostname is invalid")
    _validate_timestamp(document["created_at"], label="recovery created_at")
    return document


def _validate_recovery_intent_document(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _RECOVERY_INTENT_FIELDS:
        raise ActiveRunLockError("Active Run recovery intent fields are invalid")
    document = dict(value)
    if document["schema_version"] != ACTIVE_RUN_RECOVERY_AUDIT_SCHEMA_VERSION:
        raise ActiveRunLockError("Active Run recovery intent schema_version is invalid")
    for field in ("old_lock_token", "new_lock_token", "recovery_token", "allocation_token"):
        _validate_uuid(document[field], label=field)
    _validate_identifier(document["run_id"], label="run_id")
    _validate_sha256(document["target_lock_sha256"], label="target_lock_sha256")
    _validate_timestamp(document["created_at"], label="recovery created_at")
    if type(document["pid"]) is not int or document["pid"] <= 0:
        raise ActiveRunLockError("Active Run recovery intent pid is invalid")
    if not isinstance(document["hostname"], str) or not document["hostname"]:
        raise ActiveRunLockError("Active Run recovery intent hostname is invalid")
    actor_kind = document["actor_kind"]
    if actor_kind not in {"automatic", "manual"}:
        raise ActiveRunLockError("Active Run recovery intent actor_kind is invalid")
    if actor_kind == "automatic":
        if document["operator_identity"] is not None or document["reason"] is not None:
            raise ActiveRunLockError("automatic recovery intent actor fields must be null")
    else:
        if (
            not isinstance(document["operator_identity"], str)
            or not document["operator_identity"].strip()
            or len(document["operator_identity"]) > 128
            or not isinstance(document["reason"], str)
            or not document["reason"].strip()
            or len(document["reason"]) > 512
        ):
            raise ActiveRunLockError("manual recovery intent actor fields are invalid")
    expected_results = {"state_journal", "publication"}
    if (
        not isinstance(document["validation_results"], Mapping)
        or set(document["validation_results"]) != expected_results
        or not all(type(document["validation_results"][name]) is bool for name in expected_results)
    ):
        raise ActiveRunLockError("Active Run recovery intent validation_results are invalid")
    checksum = document["intent_checksum"]
    _validate_sha256(checksum, label="recovery intent checksum")
    payload = dict(document)
    payload.pop("intent_checksum")
    if _sha256(_canonical_json_bytes(payload)) != checksum:
        raise ActiveRunLockError("Active Run recovery intent checksum does not match")
    return document


def _validate_recovery_outcome_document(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _RECOVERY_OUTCOME_FIELDS:
        raise ActiveRunLockError("Active Run recovery outcome fields are invalid")
    document = dict(value)
    if document["schema_version"] != ACTIVE_RUN_RECOVERY_AUDIT_SCHEMA_VERSION:
        raise ActiveRunLockError("Active Run recovery outcome schema_version is invalid")
    _validate_identifier(document["run_id"], label="run_id")
    _validate_uuid(document["allocation_token"], label="allocation_token")
    _validate_uuid(document["recovery_token"], label="recovery_token")
    if document["actor_kind"] not in {"automatic", "manual"}:
        raise ActiveRunLockError("Active Run recovery outcome actor_kind is invalid")
    if document["actor_kind"] == "automatic" and document["operator_identity"] is not None:
        raise ActiveRunLockError("automatic recovery outcome operator_identity must be null")
    if document["actor_kind"] == "manual" and (
        not isinstance(document["operator_identity"], str)
        or not document["operator_identity"].strip()
        or len(document["operator_identity"]) > 128
    ):
        raise ActiveRunLockError("manual recovery outcome operator_identity is invalid")
    for field in (
        "intent_sha256",
        "state_sha256",
        "journal_anchor_sha256",
        "recovering_lock_sha256",
    ):
        _validate_sha256(document[field], label=field)
    if document["publication_status"] not in {"absent", "validated"}:
        raise ActiveRunLockError("Active Run recovery outcome publication_status is invalid")
    if document["publication_status"] == "absent":
        if (
            document["publication_transaction_id"] is not None
            or document["publication_manifest_sha256"] is not None
        ):
            raise ActiveRunLockError(
                "absent recovery outcome publication fields must be null"
            )
    else:
        _validate_identifier(
            document["publication_transaction_id"], label="publication_transaction_id"
        )
        _validate_sha256(
            document["publication_manifest_sha256"],
            label="publication_manifest_sha256",
        )
    if document["successor_phase"] not in {"running", "released"}:
        raise ActiveRunLockError("Active Run recovery outcome successor_phase is invalid")
    _validate_sha256(
        document["successor_lock_sha256"],
        label="successor_lock_sha256",
        nullable=document["successor_phase"] == "released",
    )
    if document["successor_phase"] == "released" and document["successor_lock_sha256"] is not None:
        raise ActiveRunLockError("released recovery outcome must use a null successor hash")
    if type(document["state_version"]) is not int or document["state_version"] < 0:
        raise ActiveRunLockError("Active Run recovery outcome state_version is invalid")
    if not isinstance(document["state_status"], str) or not document["state_status"]:
        raise ActiveRunLockError("Active Run recovery outcome state_status is invalid")
    if not isinstance(document["intent_path"], str) or not document["intent_path"]:
        raise ActiveRunLockError("Active Run recovery outcome intent_path is invalid")
    _validate_timestamp(document["created_at"], label="recovery outcome created_at")
    checksum = document["outcome_checksum"]
    _validate_sha256(checksum, label="recovery outcome checksum")
    payload = dict(document)
    payload.pop("outcome_checksum")
    if _sha256(_canonical_json_bytes(payload)) != checksum:
        raise ActiveRunLockError("Active Run recovery outcome checksum does not match")
    return document


def _reject_recovery_or_release_entries(
    runs: Path, *, allow_owned_state_lock: bool = False
) -> None:
    """Reject recovery/control residue outside one owned state-lock section.

    ``_active_run_state_lock`` is the publication mutex for cooperating Active
    Run operations.  A caller holding that mutex needs to inspect the rest of
    the closed control-entry set without treating its own mutex as foreign
    residue.  The context manager still verifies the mutex bytes and identity
    before it releases it, so this exemption never authorizes a replaced or
    leaked state lock.
    """

    state_lock = runs / _ACTIVE_RUN_STATE_LOCK_NAME
    if (
        not allow_owned_state_lock
        and _lstat(state_lock, label="Active Run state lock") is not None
    ):
        raise ActiveRunLockError(
            "Active Run Lock state transition is handled by another worker"
        )
    recovery = runs / ".active_run.recovery.lock"
    if _lstat(recovery, label="Active Run recovery lock") is not None:
        raise ActiveRunRecoveryRequiredError(
            "Active Run recovery lock exists; A3.2 recovery is required"
        )
    try:
        entries = list(runs.iterdir())
    except OSError as exc:
        raise ActiveRunLockError("unable to inspect Active Run release tombstones") from exc
    if any(entry.name.startswith(ACTIVE_RUN_RELEASE_PREFIX) for entry in entries):
        raise ActiveRunRecoveryRequiredError(
            "Active Run release tombstone exists; cleanup recovery is required"
        )
    known_entries = {Path(ACTIVE_RUN_LOCK_PATH).name}
    if allow_owned_state_lock:
        known_entries.add(_ACTIVE_RUN_STATE_LOCK_NAME)
    unknown = {
        entry.name
        for entry in entries
        if (
            entry.name.startswith(".active_run.")
            or entry.name.startswith("..active_run.")
        )
        and entry.name not in known_entries
    }
    if unknown:
        raise ActiveRunRecoveryRequiredError(
            "unknown Active Run transaction residue exists; explicit recovery is required"
        )


def read_active_run_lock(project_root: Path) -> dict[str, Any]:
    _, _, path = _lock_path(project_root)
    lock, _ = _read_lock(path)
    return deepcopy(lock)


def read_existing_active_run_lock(project_root: Path) -> dict[str, Any]:
    """Read the Active Run Lock without creating ``runs/`` as a side effect."""

    root = Path(project_root).absolute()
    _assert_plain_entry(root, label="project_root", directory=True)
    runs = root / "runs"
    _assert_project_path(root, runs, include_leaf=True, label="runs directory")
    _assert_plain_entry(runs, label="runs directory", directory=True)
    path = runs / ".active_run.lock"
    _assert_project_path(root, path, include_leaf=True, label="Active Run Lock")
    lock, _ = _read_lock(path)
    return deepcopy(lock)


def validate_active_run_lock_snapshot(data: bytes) -> dict[str, Any]:
    """Validate one immutable canonical Active Run Lock byte snapshot."""

    if type(data) is not bytes:
        raise ActiveRunLockError("Active Run Lock snapshot must be exact bytes")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ActiveRunLockError("Active Run Lock is not valid JSON") from exc
    document = _validate_lock_document(value)
    if _canonical_json_bytes(document) != data:
        raise ActiveRunLockError("Active Run Lock must use canonical JSON")
    return deepcopy(document)


def validate_active_run_control_entries(project_root: Path) -> tuple[str, ...]:
    """Validate the closed global Active Run transaction-entry set."""

    _, runs, _ = _lock_path(project_root)
    _reject_recovery_or_release_entries(runs)
    try:
        return tuple(
            sorted(
                entry.name
                for entry in runs.iterdir()
                if entry.name.startswith(".active_run.")
                or entry.name.startswith("..active_run.")
            )
        )
    except OSError as exc:
        raise ActiveRunLockError("unable to enumerate Active Run control entries") from exc


def validate_current_process_active_run_owner(
    project_root: Path,
    *,
    run_id: str,
    allocation_token: str,
    expected_lock_token: str,
) -> dict[str, Any]:
    """Fence ordinary Resume to a lock acquired by this process instance."""

    lock = validate_existing_active_run_lock(
        project_root,
        run_id=run_id,
        allocation_token=allocation_token,
        expected_lock_token=expected_lock_token,
        allowed_phases={"running"},
    )
    process_identity = (
        os.path.normcase(str(Path(project_root).absolute())),
        allocation_token,
        expected_lock_token,
    )
    if (
        lock["hostname"] != socket.gethostname()
        or lock["pid"] != os.getpid()
        or process_identity not in _PROCESS_OWNED_ACTIVE_RUN_LOCK_IDENTITIES
    ):
        raise ActiveRunLockError(
            "Active Run Lock is not owned by the current process instance"
        )
    return lock


def _cleanup_failed_acquisition(
    path: Path,
    runs: Path,
    expected_bytes: bytes,
    expected_identity: tuple[int, int] | None,
    lock_token: str,
) -> list[str]:
    diagnostics: list[str] = []
    if expected_identity is None:
        return [
            "failed Active Run Lock publication identity is uncertain; lock was preserved"
        ]
    tombstone = runs / f"{ACTIVE_RUN_RELEASE_PREFIX}{lock_token}.json"
    isolation_attempted = False
    try:
        entry = _lstat(path, label="failed Active Run Lock acquisition")
        if entry is None:
            return diagnostics
        if stat.S_ISLNK(entry.st_mode) or _is_reparse(entry) or not stat.S_ISREG(entry.st_mode):
            return ["failed Active Run Lock entry changed type and was preserved"]
        current_bytes = path.read_bytes()
        if current_bytes != expected_bytes:
            return ["failed Active Run Lock ownership bytes changed and were preserved"]
        if _lstat(tombstone, label="failed Active Run Lock tombstone") is not None:
            return ["failed Active Run Lock tombstone already exists; lock was preserved"]
        root = runs.parent
        isolation_attempted = True
        controlled_fs.rename(
            root,
            path.relative_to(root).as_posix(),
            tombstone.relative_to(root).as_posix(),
            expected_source_identity=expected_identity,
        )
        controlled_fs.unlink(
            root,
            tombstone.relative_to(root).as_posix(),
            expected_identity=expected_identity,
        )
    except Exception as exc:
        diagnostics.append(f"failed Active Run Lock cleanup was incomplete: {exc}")
        if isolation_attempted:
            try:
                lock_exists = _lstat(path, label="failed Active Run Lock") is not None
                tombstone_exists = (
                    _lstat(tombstone, label="failed Active Run Lock tombstone") is not None
                )
            except Exception as inspect_exc:
                diagnostics.append(
                    f"unable to inspect failed Active Run Lock cleanup evidence: {inspect_exc}"
                )
                diagnostics.extend(_write_active_recovery_marker(runs, expected_bytes))
            else:
                if not lock_exists and not tombstone_exists:
                    diagnostics.extend(
                        _restore_blocking_evidence(
                            tombstone,
                            runs,
                            expected_bytes,
                            label="failed Active Run Lock tombstone",
                        )
                    )
    return diagnostics


def _owned_lock_key(
    project_root: Path, allocation_token: str, lock_token: str
) -> tuple[str, str, str]:
    return (
        os.path.normcase(str(Path(project_root).absolute())),
        allocation_token,
        lock_token,
    )


def _remember_process_owned_lock_snapshot(
    project_root: Path,
    *,
    allocation_token: str,
    lock_token: str,
    lock_bytes: bytes,
    lock_identity: tuple[int, int],
) -> None:
    key = _owned_lock_key(project_root, allocation_token, lock_token)
    _PROCESS_OWNED_ACTIVE_RUN_LOCK_IDENTITIES.add(key)
    _PROCESS_OWNED_ACTIVE_RUN_LOCK_SNAPSHOTS[key] = (lock_bytes, lock_identity)


def read_process_owned_active_run_lock_snapshot(
    project_root: Path,
    *,
    allocation_token: str,
    lock_token: str,
) -> tuple[bytes, tuple[int, int]]:
    """Return the exact lock bytes/identity created by this process, or fail closed."""

    snapshot = _PROCESS_OWNED_ACTIVE_RUN_LOCK_SNAPSHOTS.get(
        _owned_lock_key(project_root, allocation_token, lock_token)
    )
    if snapshot is None:
        raise ActiveRunLockError("Active Run Lock ownership snapshot is unavailable")
    return snapshot


def forget_process_owned_active_run_lock_snapshot(
    project_root: Path,
    *,
    allocation_token: str,
    lock_token: str,
) -> None:
    """Drop local ownership evidence after a terminal cleanup attempt."""

    key = _owned_lock_key(project_root, allocation_token, lock_token)
    _PROCESS_OWNED_ACTIVE_RUN_LOCK_IDENTITIES.discard(key)
    _PROCESS_OWNED_ACTIVE_RUN_LOCK_SNAPSHOTS.pop(key, None)


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
    created = False
    created_identity: tuple[int, int] | None = None
    try:
        created_identity = controlled_fs.write_exclusive(
            root, path.relative_to(root).as_posix(), data
        )
        created = True
        current, current_bytes = _read_lock(path)
        if (
            current_bytes != data
            or current["lock_token"] != lock_token
            or controlled_fs.file_identity(path) != created_identity
        ):
            raise ActiveRunLockError("Active Run Lock changed during acquisition")
        _reject_recovery_or_release_entries(runs)
        if (
            current["pid"] == os.getpid()
            and current["hostname"] == socket.gethostname()
        ):
            _remember_process_owned_lock_snapshot(
                root,
                allocation_token=current["allocation_token"],
                lock_token=lock_token,
                lock_bytes=current_bytes,
                lock_identity=created_identity,
            )
        return deepcopy(current)
    except FileExistsError as exc:
        raise ActiveRunLockError("an Active Run Lock already exists") from exc
    except controlled_fs.ControlledFilesystemError as exc:
        error = ActiveRunLockError("unable to safely acquire Active Run Lock")
        if not created:
            try:
                created = _lstat(path, label="failed Active Run Lock acquisition") is not None
            except Exception:
                created = False
        if created:
            for diagnostic in _cleanup_failed_acquisition(
                path, runs, data, created_identity, lock_token
            ):
                _add_exception_note(error, diagnostic)
        raise error from exc
    except OSError as exc:
        error = ActiveRunLockError("unable to acquire Active Run Lock")
        if not created:
            try:
                created = _lstat(path, label="failed Active Run Lock acquisition") is not None
            except Exception:
                created = False
        if created:
            for diagnostic in _cleanup_failed_acquisition(
                path, runs, data, created_identity, lock_token
            ):
                _add_exception_note(error, diagnostic)
        raise error from exc
    except BaseException as exc:
        if not created:
            try:
                created = _lstat(path, label="failed Active Run Lock acquisition") is not None
            except Exception:
                created = False
        if created:
            for diagnostic in _cleanup_failed_acquisition(
                path, runs, data, created_identity, lock_token
            ):
                _add_exception_note(exc, diagnostic)
        raise


def acquire_waiting_review_lock(
    project_root: Path,
    *,
    run_id: str,
    allocation_token: str,
    lock_token: str,
    expected_state_version: int,
    task_id: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Reacquire a running lock for a still-waiting review Run.

    Ordinary ``mark_active_run_running`` is intentionally limited to the
    genesis ``CREATED`` state.  A review decision is the one later workflow
    operation that may reacquire a released lock, so it has a separate
    preflight that binds the fresh lock to the immutable WAITING state cursor.
    """

    _validate_identifier(run_id, label="run_id")
    _validate_identifier(task_id, label="task_id")
    _validate_uuid(allocation_token, label="allocation_token")
    _validate_uuid(lock_token, label="lock_token")
    if type(expected_state_version) is not int or expected_state_version < 0:
        raise ActiveRunLockError("expected review state version is invalid")
    timestamp = canonical_utc_now() if created_at is None else created_at
    _validate_timestamp(timestamp, label="review lock created_at")

    root, runs, path = _lock_path(project_root)
    document = _validate_lock_document(
        {
            "schema_version": ACTIVE_RUN_LOCK_SCHEMA_VERSION,
            "phase": "running",
            "allocation_token": allocation_token,
            "reserved_run_id": run_id,
            "run_id": run_id,
            "task_id": task_id,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "lock_token": lock_token,
            "recovery_of_lock_token": None,
            "recovery_token": None,
            "recovery_intent_path": None,
            "recovery_intent_sha256": None,
            "created_at": timestamp,
        }
    )
    data = _canonical_json_bytes(document)
    created_identity: tuple[int, int] | None = None
    created = False
    acquired: dict[str, Any] | None = None
    current_bytes: bytes | None = None
    try:
        # Hold the same transaction mutex used by release and recovery-marker
        # publication until the new active lock and the closed residue set have
        # been observed together.  A cooperating writer cannot add a recovery
        # marker or release tombstone after this check without first observing
        # the freshly published lock.
        with _active_run_state_lock(root):
            _reject_recovery_or_release_entries(
                runs, allow_owned_state_lock=True
            )
            try:
                from orchestrator.state.store import StateStore

                state = StateStore(root).load(run_id=run_id)
            except Exception as exc:
                raise ActiveRunLockError(
                    "waiting review lock requires a readable canonical State"
                ) from exc
            canonical_state = state.get("canonical_state")
            if (
                state["run_id"] != run_id
                or not isinstance(canonical_state, Mapping)
                or canonical_state.get("allocation_token") != allocation_token
                or state["status"] != "WAITING_FOR_REVIEW"
                or state["state_version"] != expected_state_version
            ):
                raise ActiveRunLockError(
                    "waiting review lock preflight does not match canonical State"
                )
            context = (
                canonical_state.get("context")
                if isinstance(canonical_state, Mapping)
                else None
            )
            if (
                not isinstance(context, Mapping)
                or context.get("workflow_task_id") != task_id
            ):
                raise ActiveRunLockError(
                    "waiting review lock task identity does not match canonical State"
                )

            created_identity = controlled_fs.write_exclusive(
                root, path.relative_to(root).as_posix(), data
            )
            created = True
            current, current_bytes = _read_lock(path)
            if (
                current_bytes != data
                or current["lock_token"] != lock_token
                or controlled_fs.file_identity(path) != created_identity
            ):
                raise ActiveRunLockError(
                    "waiting review lock changed during acquisition"
                )
            latest = StateStore(root).load(run_id=run_id)
            if (
                latest["status"] != "WAITING_FOR_REVIEW"
                or latest["state_version"] != expected_state_version
                or not isinstance(latest.get("canonical_state"), Mapping)
                or latest["canonical_state"].get("allocation_token") != allocation_token
            ):
                raise ActiveRunLockError(
                    "canonical State changed during waiting review lock acquisition"
                )
            _reject_recovery_or_release_entries(
                runs, allow_owned_state_lock=True
            )
            acquired = deepcopy(current)

        if acquired is None or created_identity is None or current_bytes is None:
            raise ActiveRunLockError(
                "waiting review lock acquisition did not retain publication evidence"
            )
        _remember_process_owned_lock_snapshot(
            root,
            allocation_token=allocation_token,
            lock_token=lock_token,
            lock_bytes=current_bytes,
            lock_identity=created_identity,
        )
        # ``_active_run_state_lock`` can only serialize cooperating writers
        # while it is held.  Fence once more after its release and after local
        # ownership registration so residue injected after the in-mutex check
        # cannot turn into a successful reacquisition.  The exception handler
        # below identity-cleans the newly created lock before propagating that
        # failure.
        _reject_recovery_or_release_entries(runs)
        return acquired
    except FileExistsError as exc:
        raise ActiveRunLockError(
            "a competing Active Run Lock already exists"
        ) from exc
    except BaseException as exc:
        if created:
            # The publication mutex can still fail while leaving its critical
            # section.  Do not retain process-local ownership evidence when
            # that turns the fresh lock into a failed acquisition.
            forget_process_owned_active_run_lock_snapshot(
                root,
                allocation_token=allocation_token,
                lock_token=lock_token,
            )
            for diagnostic in _cleanup_failed_acquisition(
                path, runs, data, created_identity, lock_token
            ):
                _add_exception_note(exc, diagnostic)
        raise


def _atomic_update_lock(
    project_root: Path,
    *,
    expected_allocation_token: str,
    expected_lock_token: str,
    update: Mapping[str, Any],
    expected_state: Mapping[str, Any] | None = None,
    expected_bytes: bytes | None = None,
    state_error: str = "Active Run Lock state changed before update",
) -> dict[str, Any]:
    with _active_run_state_lock(project_root):
        root, runs, path = _lock_path(project_root)
        current, current_bytes = _read_lock(path)
        if (
            current["allocation_token"] != expected_allocation_token
            or current["lock_token"] != expected_lock_token
        ):
            raise ActiveRunLockError("Active Run Lock ownership does not match")
        if expected_bytes is not None and current_bytes != expected_bytes:
            raise ActiveRunLockError("Active Run Lock bytes changed before update")
        if expected_state is not None and any(
            current.get(name) != value for name, value in expected_state.items()
        ):
            raise ActiveRunLockError(state_error)
        next_value = dict(current)
        next_value.update(update)
        next_value = _validate_lock_document(next_value)
        data = _canonical_json_bytes(next_value)
        try:
            _, latest_bytes = _read_lock(path)
            if latest_bytes != current_bytes:
                raise ActiveRunLockError("Active Run Lock changed before update")
            persisted_identity = controlled_fs.atomic_replace(
                root, path.relative_to(root).as_posix(), data
            )
            # Existing fault-injection seams historically model this helper as
            # a no-return mutation.  A seam that completed the write but
            # dropped its return value still needs the same guarded identity
            # validation; the production helper always supplies the exact
            # descriptor identity directly.
            if persisted_identity is None:
                persisted_identity = controlled_fs.file_identity(path)
            persisted, persisted_bytes = _read_lock(path)
            if (
                persisted_bytes != data
                or controlled_fs.file_identity(path) != persisted_identity
            ):
                raise ActiveRunLockError(
                    "Active Run Lock update did not persist expected bytes"
                )
            if (
                persisted["pid"] == os.getpid()
                and persisted["hostname"] == socket.gethostname()
                and _owned_lock_key(
                    root, persisted["allocation_token"], persisted["lock_token"]
                ) in _PROCESS_OWNED_ACTIVE_RUN_LOCK_IDENTITIES
            ):
                _remember_process_owned_lock_snapshot(
                    root,
                    allocation_token=persisted["allocation_token"],
                    lock_token=persisted["lock_token"],
                    lock_bytes=persisted_bytes,
                    lock_identity=persisted_identity,
                )
            return deepcopy(persisted)
        except OSError as exc:
            error = ActiveRunLockError("unable to update Active Run Lock")
            primary = error
            raise error from exc
        except controlled_fs.ControlledFilesystemError as exc:
            raise ActiveRunLockError("unable to safely update Active Run Lock") from exc


def reserve_active_run_id(
    project_root: Path,
    *,
    run_id: str,
    expected_allocation_token: str,
    expected_lock_token: str,
) -> dict[str, Any]:
    _validate_identifier(run_id, label="run_id")
    return _atomic_update_lock(
        project_root,
        expected_allocation_token=expected_allocation_token,
        expected_lock_token=expected_lock_token,
        update={"reserved_run_id": run_id},
        expected_state={"phase": "allocating", "reserved_run_id": None, "run_id": None},
        state_error="Active Run Lock is not ready to reserve a Run ID",
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
        from orchestrator.state.store import StateConflictError, StateStore, StateStoreError

        StateStore(project_root).validate_initialized_run(
            run_id=run_id,
            allocation_token=expected_allocation_token,
        )
    except StateConflictError as exc:
        if "state lock already exists" in str(exc):
            raise ActiveRunLockError(
                "Run state transition is handled by another worker"
            ) from exc
        raise ActiveRunLockError(
            "Active Run Lock requires a valid CREATED state and genesis anchor"
        ) from exc
    except StateStoreError as exc:
        raise ActiveRunLockError(
            "Active Run Lock requires a valid CREATED state and genesis anchor"
        ) from exc
    return _atomic_update_lock(
        project_root,
        expected_allocation_token=expected_allocation_token,
        expected_lock_token=expected_lock_token,
        update={"phase": "running", "run_id": run_id},
        expected_state={
            "phase": "allocating",
            "reserved_run_id": run_id,
            "run_id": None,
        },
        state_error="Active Run Lock cannot enter running for this Run",
    )


def validate_active_run_lock(
    project_root: Path,
    *,
    run_id: str,
    allocation_token: str,
    expected_lock_token: str,
    allowed_phases: set[str] | frozenset[str],
) -> dict[str, Any]:
    lock = read_active_run_lock(project_root)
    return _validate_active_run_lock_identity(
        lock,
        run_id=run_id,
        allocation_token=allocation_token,
        expected_lock_token=expected_lock_token,
        allowed_phases=allowed_phases,
    )


def validate_existing_active_run_lock(
    project_root: Path,
    *,
    run_id: str,
    allocation_token: str,
    expected_lock_token: str,
    allowed_phases: set[str] | frozenset[str],
) -> dict[str, Any]:
    """Validate an existing lock without creating a missing ``runs/`` directory."""

    lock = read_existing_active_run_lock(project_root)
    return _validate_active_run_lock_identity(
        lock,
        run_id=run_id,
        allocation_token=allocation_token,
        expected_lock_token=expected_lock_token,
        allowed_phases=allowed_phases,
    )


def _validate_active_run_lock_identity(
    lock: Mapping[str, Any],
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
    if (
        lock["allocation_token"] != allocation_token
        or lock["lock_token"] != expected_lock_token
        or lock["reserved_run_id"] != run_id
        or lock["phase"] not in allowed_phases
    ):
        raise ActiveRunLockError("Active Run Lock fencing check failed")
    if lock["phase"] in {"running", "recovering"} and lock["run_id"] != run_id:
        raise ActiveRunLockError("Active Run Lock does not identify the requested Run")
    return deepcopy(dict(lock))


def _recovery_mutex_path(runs: Path) -> Path:
    return runs / ".active_run.recovery.lock"


def _acquire_recovery_mutex(
    runs: Path,
    *,
    run_id: str,
    recovery_token: str,
    target_lock_token: str,
    target_lock_sha256: str,
    created_at: str,
) -> tuple[Path, bytes]:
    """Publish recovery residue only while the Active Run mutex is held.

    The target lock is checked again inside the mutex so a stale-recovery
    caller that lost a race to a fresh waiting-review lock cannot leave a
    recovery marker beside that newly published lock.
    """

    document = _validate_recovery_lock_document(
        {
            "schema_version": ACTIVE_RUN_RECOVERY_LOCK_SCHEMA_VERSION,
            "recovery_token": recovery_token,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "target_lock_token": target_lock_token,
            "target_lock_sha256": target_lock_sha256,
            "run_id": run_id,
            "created_at": created_at,
        }
    )
    data = _canonical_json_bytes(document)
    path = _recovery_mutex_path(runs)
    root = runs.parent
    with _active_run_state_lock(root):
        _, protected_runs, active_path = _lock_path(root)
        if protected_runs != runs:
            raise ActiveRunLockError("Active Run recovery lock path is not stable")
        current, current_bytes = _read_lock(active_path)
        if (
            current["run_id"] != run_id
            or current["lock_token"] != target_lock_token
            or _sha256(current_bytes) != target_lock_sha256
        ):
            raise ActiveRunLockError(
                "Active Run Lock changed before recovery mutex publication"
            )
        _write_exclusive_document(path, data, label="Active Run recovery lock")
    return path, data


def _remove_owned_recovery_mutex(path: Path, runs: Path, data: bytes) -> None:
    entry = _lstat(path, label="Active Run recovery lock")
    if entry is None:
        raise ActiveRunLockError("Active Run recovery lock disappeared before release")
    _assert_plain_entry(path, label="Active Run recovery lock", directory=False)
    try:
        current = path.read_bytes()
    except OSError as exc:
        raise ActiveRunLockError("unable to read Active Run recovery lock before release") from exc
    if current != data:
        raise ActiveRunLockError("Active Run recovery lock ownership changed before release")
    try:
        identity = controlled_fs.file_identity(path)
    except (OSError, controlled_fs.ControlledFilesystemError) as exc:
        raise ActiveRunLockError(
            "unable to bind Active Run recovery lock identity before release"
        ) from exc
    try:
        controlled_fs.unlink(
            runs.parent,
            path.relative_to(runs.parent).as_posix(),
            expected_identity=identity,
        )
    except (OSError, controlled_fs.ControlledFilesystemError) as exc:
        error = ActiveRunLockError(
            "Active Run recovery lock cleanup is uncertain; preserve recovery evidence"
        )
        _add_exception_note(error, f"primary recovery lock cleanup failure: {exc}")
        for diagnostic in _restore_blocking_evidence(
            path, runs, data, label="Active Run recovery lock"
        ):
            _add_exception_note(error, diagnostic)
        raise error from exc


def _audit_directory(root: Path, run_id: str, *, create: bool = True) -> Path:
    target = root / "runs" / run_id / "lock_recovery_audit"
    _assert_project_path(root, target, include_leaf=False, label="recovery audit directory")
    entry = _lstat(target, label="recovery audit directory")
    if entry is None:
        if not create:
            return target
        try:
            target.mkdir(parents=False, exist_ok=False)
        except OSError as exc:
            raise ActiveRunLockError("unable to create recovery audit directory") from exc
    _assert_plain_entry(target, label="recovery audit directory", directory=True)
    return target


def _audit_relative_path(run_id: str, path: Path) -> str:
    return f"runs/{run_id}/lock_recovery_audit/{path.name}"


def _intent_reference(path: Path, run_id: str, data: bytes) -> dict[str, str]:
    return {"path": _audit_relative_path(run_id, path), "sha256": _sha256(data)}


def validate_recovery_audit_reference(
    project_root: Path,
    *,
    run_id: str,
    allocation_token: str,
    operation_owner_lock_token: str,
    append_actor_lock_token: str,
    recovery_audit_ref: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the immutable intent that authorizes a takeover terminal row."""

    root, _, _ = _lock_path(project_root)
    if not isinstance(recovery_audit_ref, Mapping) or set(recovery_audit_ref) != {
        "path",
        "sha256",
    }:
        raise ActiveRunLockError("recovery_audit_ref is invalid")
    path_text = recovery_audit_ref["path"]
    if (
        not isinstance(path_text, str)
        or "\\" in path_text
        or not re.fullmatch(
            rf"runs/{re.escape(run_id)}/lock_recovery_audit/[^/]+\.intent\.json",
            path_text,
        )
    ):
        raise ActiveRunLockError("recovery_audit_ref path is invalid")
    _validate_sha256(recovery_audit_ref["sha256"], label="recovery_audit_ref SHA-256")
    path = root / Path(*path_text.split("/"))
    _assert_project_path(root, path, include_leaf=True, label="recovery audit intent")
    document, data = _read_canonical_document(
        path,
        label="Active Run recovery intent",
        fields=_RECOVERY_INTENT_FIELDS,
    )
    document = _validate_recovery_intent_document(document)
    if _sha256(data) != recovery_audit_ref["sha256"]:
        raise ActiveRunLockError("recovery_audit_ref SHA-256 does not match intent")
    if (
        document["run_id"] != run_id
        or document["allocation_token"] != allocation_token
        or document["old_lock_token"] != operation_owner_lock_token
        or document["new_lock_token"] != append_actor_lock_token
    ):
        raise ActiveRunLockError("recovery audit intent ownership does not match terminal row")
    return deepcopy(document)


def _recovery_audit_pair(audit_dir: Path) -> tuple[Path, Path] | None:
    """Return the one complete immutable audit pair or fail closed on residue."""

    entry = _lstat(audit_dir, label="recovery audit directory")
    if entry is None:
        return None
    _assert_plain_entry(audit_dir, label="recovery audit directory", directory=True)
    try:
        entries = sorted(audit_dir.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise ActiveRunLockError("unable to inspect recovery audit entries") from exc
    if not entries:
        return None

    intents: list[Path] = []
    outcomes: list[Path] = []
    for path in entries:
        _assert_plain_entry(path, label="recovery audit entry", directory=False)
        if path.name.endswith(".intent.json"):
            intents.append(path)
        elif path.name.endswith(".outcome.1.json"):
            outcomes.append(path)
        else:
            raise ActiveRunLockError(
                f"unknown recovery audit entry requires manual review: {path.name}"
            )

    if len(intents) == 1 and not outcomes:
        raise ActiveRunLockError(
            "incomplete recovery intent exists; explicit manual recovery is required"
        )
    if len(outcomes) == 1 and not intents:
        raise ActiveRunLockError(
            "orphan recovery outcome exists; explicit manual recovery is required"
        )
    if len(intents) != 1 or len(outcomes) != 1:
        raise ActiveRunLockError(
            "recovery audit must contain exactly one intent/outcome pair"
        )

    intent_basename = intents[0].name[: -len(".intent.json")]
    outcome_basename = outcomes[0].name[: -len(".outcome.1.json")]
    if intent_basename != outcome_basename:
        raise ActiveRunLockError(
            "recovery audit intent and outcome basenames do not match"
        )
    return intents[0], outcomes[0]


def _read_completed_outcome(audit_dir: Path) -> tuple[dict[str, Any], bytes] | None:
    pair = _recovery_audit_pair(audit_dir)
    if pair is None:
        return None
    _, path = pair
    document, data = _read_canonical_document(
        path,
        label="Active Run recovery outcome",
        fields=_RECOVERY_OUTCOME_FIELDS,
    )
    return _validate_recovery_outcome_document(document), data


def _validate_completed_outcome_binding(
    root: Path, outcome: Mapping[str, Any]
) -> dict[str, Any]:
    run_id = outcome["run_id"]
    path_text = outcome["intent_path"]
    if (
        not isinstance(path_text, str)
        or "\\" in path_text
        or not re.fullmatch(
            rf"runs/{re.escape(run_id)}/lock_recovery_audit/[^/]+\.intent\.json",
            path_text,
        )
    ):
        raise ActiveRunLockError("completed recovery outcome intent_path is invalid")
    path = root / Path(*path_text.split("/"))
    _assert_project_path(root, path, include_leaf=True, label="completed recovery intent")
    intent, data = _read_canonical_document(
        path,
        label="completed Active Run recovery intent",
        fields=_RECOVERY_INTENT_FIELDS,
    )
    intent = _validate_recovery_intent_document(intent)
    if (
        _sha256(data) != outcome["intent_sha256"]
        or intent["run_id"] != run_id
        or intent["allocation_token"] != outcome["allocation_token"]
        or intent["recovery_token"] != outcome["recovery_token"]
    ):
        raise ActiveRunLockError("completed recovery outcome does not bind its intent")
    return intent


def _validate_completed_outcome_successor(
    root: Path,
    runs: Path,
    active_path: Path,
    *,
    outcome: Mapping[str, Any],
    intent: Mapping[str, Any],
) -> None:
    """Accept only the exact outcome-frozen successor, never merely its hash."""

    _reject_recovery_or_release_entries(runs)
    entry = _lstat(active_path, label="Active Run Lock")
    if outcome["successor_phase"] == "released":
        if entry is not None:
            raise ActiveRunLockError("released recovery outcome conflicts with an Active Run Lock")
        return
    if entry is None:
        raise ActiveRunLockError("recovery outcome successor Active Run Lock is missing")
    active, active_bytes = _read_lock(active_path)
    if _sha256(active_bytes) != outcome["successor_lock_sha256"]:
        raise ActiveRunLockError("Active Run Lock does not match completed recovery outcome")
    if (
        active["phase"] != "running"
        or active["run_id"] != outcome["run_id"]
        or active["reserved_run_id"] != outcome["run_id"]
        or active["allocation_token"] != outcome["allocation_token"]
        or active["lock_token"] != intent["new_lock_token"]
        or any(
            active[name] is not None
            for name in (
                "recovery_of_lock_token",
                "recovery_token",
                "recovery_intent_path",
                "recovery_intent_sha256",
            )
        )
    ):
        raise ActiveRunLockError(
            "Active Run Lock successor fields do not match completed recovery outcome"
        )


def _validate_a3_recovery_sandbox(root: Path, *, run_id: str) -> None:
    """Keep the opt-in A3.2 primitive inside the existing A1 sandbox boundary."""

    try:
        from orchestrator.inspection_workflow.a1_artifacts import (
            PHASE_A1_EXECUTION_PROFILE,
            PhaseA1ArtifactError,
            _reject_recovery_marker,
            validate_phase_a1_sandbox,
        )

        validate_phase_a1_sandbox(
            root,
            run_id=run_id,
            execution_profile=PHASE_A1_EXECUTION_PROFILE,
        )
        for area in ("work", "artifacts", "staging"):
            _reject_recovery_marker(root, run_id, area)
    except PhaseA1ArtifactError as exc:
        raise ActiveRunLockError(
            "A3.2 recovery requires a controlled Phase A1 sandbox without recovery markers"
        ) from exc


def _publication_recovery_binding(
    root: Path,
    *,
    run_id: str,
    plan_fingerprint: str,
) -> tuple[str, str | None, str | None]:
    """Use the A2 authority for a frozen publication preflight/replay binding."""

    try:
        from orchestrator.inspection_workflow.publication import (
            PUBLICATION_MANIFEST_PATH,
            PUBLICATION_TRANSACTION_PATH_TEMPLATE,
            PublicationTransactionError,
            _reject_recovery_marker,
            validate_publication,
        )

        _reject_recovery_marker(root, run_id)
        transaction_path = root / Path(
            *PUBLICATION_TRANSACTION_PATH_TEMPLATE.format(run_id=run_id).split("/")
        )
        manifest_path = root / Path(*PUBLICATION_MANIFEST_PATH.split("/"))
        transaction_exists = _lstat(
            transaction_path, label="publication transaction"
        ) is not None
        manifest_exists = _lstat(
            manifest_path, label="publication Manifest"
        ) is not None
        if not transaction_exists and not manifest_exists:
            return "absent", None, None
        if transaction_exists != manifest_exists:
            raise ActiveRunLockError(
                "publication transaction and Manifest must either both exist or both be absent"
            )
        validated = validate_publication(
            root,
            run_id=run_id,
            plan_fingerprint=plan_fingerprint,
        )
    except PublicationTransactionError as exc:
        raise ActiveRunLockError("A2 Publication preflight rejected stale takeover") from exc
    transaction = validated["transaction"]
    manifest_bytes = validated["manifest_bytes"]
    return "validated", transaction["transaction_id"], _sha256(manifest_bytes)


def _validate_completed_outcome_runtime(root: Path, outcome: Mapping[str, Any]) -> None:
    """Re-read the frozen State, Journal and A2 publication conditions on replay."""

    from orchestrator.state.store import StateStore, StateStoreError

    run_id = outcome["run_id"]
    store = StateStore(root)
    try:
        # load() preserves the A3.1 unresolved-pending and canonical binding checks.
        store.load(run_id=run_id)
        state, state_bytes = store._read_state(run_id)
    except StateStoreError as exc:
        raise ActiveRunLockError("completed recovery State/Journal binding is invalid") from exc
    if (
        _sha256(state_bytes) != outcome["state_sha256"]
        or state["state_version"] != outcome["state_version"]
        or state["status"] != outcome["state_status"]
    ):
        raise ActiveRunLockError("completed recovery State does not match frozen outcome")
    anchor_path = root / "runs" / run_id / "state_journal_tail.json"
    _assert_plain_entry(anchor_path, label="completed recovery journal anchor", directory=False)
    try:
        anchor_bytes = anchor_path.read_bytes()
    except OSError as exc:
        raise ActiveRunLockError("unable to read completed recovery journal anchor") from exc
    if _sha256(anchor_bytes) != outcome["journal_anchor_sha256"]:
        raise ActiveRunLockError("completed recovery Journal anchor does not match frozen outcome")
    publication_status, transaction_id, manifest_sha256 = _publication_recovery_binding(
        root,
        run_id=run_id,
        plan_fingerprint=state["plan_fingerprint"],
    )
    if (
        publication_status != outcome["publication_status"]
        or transaction_id != outcome["publication_transaction_id"]
        or manifest_sha256 != outcome["publication_manifest_sha256"]
    ):
        raise ActiveRunLockError(
            "completed recovery Publication does not match frozen outcome"
        )


def recover_stale_active_run(
    project_root: Path,
    *,
    run_id: str,
    recovery_token: str,
    new_lock_token: str,
    created_at: str | None = None,
    actor_kind: str = "automatic",
    operator_identity: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Explicitly take over one same-host dead owner and recover its WAL tail.

    This is intentionally a single-host, point-in-time recovery primitive.  It
    is not wired into legacy execution or an automatic controller.
    """

    _validate_identifier(run_id, label="run_id")
    _validate_uuid(recovery_token, label="recovery_token")
    _validate_uuid(new_lock_token, label="new_lock_token")
    timestamp = canonical_utc_now() if created_at is None else created_at
    _validate_timestamp(timestamp, label="recovery created_at")
    if actor_kind not in {"automatic", "manual"}:
        raise ActiveRunLockError("recovery actor_kind is invalid")
    if actor_kind == "automatic":
        if operator_identity is not None or reason is not None:
            raise ActiveRunLockError("automatic recovery actor fields must be null")
    elif (
        not isinstance(operator_identity, str)
        or not operator_identity.strip()
        or len(operator_identity) > 128
        or not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > 512
    ):
        raise ActiveRunLockError("manual recovery requires operator_identity and reason")
    root, runs, active_path = _lock_path(project_root)
    _validate_a3_recovery_sandbox(root, run_id=run_id)
    audit_dir = _audit_directory(root, run_id, create=False)
    completed = _read_completed_outcome(audit_dir)
    if completed is not None:
        outcome, _ = completed
        intent = _validate_completed_outcome_binding(root, outcome)
        if outcome["run_id"] != run_id or outcome["recovery_token"] != recovery_token:
            raise ActiveRunLockError("completed recovery outcome does not match this request")
        _validate_completed_outcome_runtime(root, outcome)
        _validate_completed_outcome_successor(
            root,
            runs,
            active_path,
            outcome=outcome,
            intent=intent,
        )
        return {"replayed": True, "successor_phase": outcome["successor_phase"]}

    old_lock, old_bytes = _read_lock(active_path)
    if old_lock["run_id"] != run_id or old_lock["reserved_run_id"] != run_id:
        raise ActiveRunLockError("Active Run Lock does not identify the requested Run")
    if old_lock["phase"] not in {"allocating", "running"}:
        raise ActiveRunLockError("Active Run Lock phase is not eligible for stale takeover")
    if new_lock_token == old_lock["lock_token"]:
        raise ActiveRunLockError(
            "stale takeover requires a new lock_token distinct from the old owner"
        )
    if old_lock["hostname"] != socket.gethostname():
        raise ActiveRunLockError("Active Run Lock owner is not on the same host")
    if not _owner_pid_is_confirmed_dead(old_lock["pid"]):
        raise ActiveRunLockError("Active Run Lock owner PID is not confirmed dead")
    if _lstat(_recovery_mutex_path(runs), label="Active Run recovery lock") is not None:
        raise ActiveRunLockError("Active Run recovery lock already exists; manual recovery is required")

    audit_dir = _audit_directory(root, run_id)
    target_hash = _sha256(old_bytes)
    mutex_path, mutex_bytes = _acquire_recovery_mutex(
        runs,
        run_id=run_id,
        recovery_token=recovery_token,
        target_lock_token=old_lock["lock_token"],
        target_lock_sha256=target_hash,
        created_at=timestamp,
    )
    primary: BaseException | None = None
    try:
        # The Store preflight reads the canonical state and anchored journal
        # without repairing them.  Publication is absent for an unfinished Run.
        from orchestrator.state.store import StateStore, StateStoreError

        try:
            preflight = StateStore(root).validate_takeover_preflight(
                run_id=run_id,
                allocation_token=old_lock["allocation_token"],
            )
        except StateStoreError as exc:
            raise ActiveRunLockError("State/Journal preflight rejected stale takeover") from exc
        publication_status, publication_transaction_id, publication_manifest_sha256 = (
            _publication_recovery_binding(
                root,
                run_id=run_id,
                plan_fingerprint=preflight["canonical_state"]["plan_fingerprint"],
            )
        )
        intent_payload = {
            "schema_version": ACTIVE_RUN_RECOVERY_AUDIT_SCHEMA_VERSION,
            "run_id": run_id,
            "allocation_token": old_lock["allocation_token"],
            "old_lock_token": old_lock["lock_token"],
            "new_lock_token": new_lock_token,
            "target_lock_sha256": target_hash,
            "recovery_token": recovery_token,
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "actor_kind": actor_kind,
            "operator_identity": operator_identity,
            "reason": reason,
            "created_at": timestamp,
            "validation_results": {
                "state_journal": True,
                "publication": True,
            },
            "intent_checksum": None,
        }
        intent_payload["intent_checksum"] = _sha256(
            _canonical_json_bytes({k: v for k, v in intent_payload.items() if k != "intent_checksum"})
        )
        intent = _validate_recovery_intent_document(intent_payload)
        basename = _safe_recovery_basename(recovery_token, timestamp)
        intent_path = audit_dir / f"{basename}.intent.json"
        intent_bytes = _canonical_json_bytes(intent)
        _write_exclusive_document(intent_path, intent_bytes, label="Active Run recovery intent")
        _sync_directory(root / "runs" / run_id, label="Run directory after recovery intent")
        intent_ref = _intent_reference(intent_path, run_id, intent_bytes)

        recovering = _atomic_update_lock(
            root,
            expected_allocation_token=old_lock["allocation_token"],
            expected_lock_token=old_lock["lock_token"],
            expected_bytes=old_bytes,
            update={
                "phase": "recovering",
                "lock_token": new_lock_token,
                "pid": os.getpid(),
                "hostname": socket.gethostname(),
                "recovery_of_lock_token": old_lock["lock_token"],
                "recovery_token": recovery_token,
                "recovery_intent_path": intent_ref["path"],
                "recovery_intent_sha256": intent_ref["sha256"],
            },
            expected_state={"phase": old_lock["phase"], "run_id": run_id},
            state_error="Active Run Lock changed before stale takeover",
        )
        recovering_bytes = _canonical_json_bytes(recovering)
        _remove_owned_recovery_mutex(mutex_path, runs, mutex_bytes)

        recovered_store = StateStore(root)
        recovered_store.recover_taken_over_state_journal(
            run_id=run_id,
            expected_lock_token=new_lock_token,
            recovery_audit_ref=intent_ref,
        )
        state, state_bytes = recovered_store._read_state(run_id)
        anchor_path = root / "runs" / run_id / "state_journal_tail.json"
        _assert_plain_entry(
            anchor_path,
            label="recovery journal anchor",
            directory=False,
        )
        try:
            anchor_bytes = anchor_path.read_bytes()
        except OSError as exc:
            raise ActiveRunLockError("unable to read recovery journal anchor") from exc
        final_publication_status, final_publication_transaction_id, final_publication_manifest_sha256 = (
            _publication_recovery_binding(
                root,
                run_id=run_id,
                plan_fingerprint=state["plan_fingerprint"],
            )
        )
        if (
            (final_publication_status, final_publication_transaction_id, final_publication_manifest_sha256)
            != (publication_status, publication_transaction_id, publication_manifest_sha256)
        ):
            raise ActiveRunLockError(
                "Publication changed between stale takeover preflight and recovery outcome"
            )
        successor_phase = "released" if state["status"] in {"FAILED", "COMPLETED"} else "running"
        successor_bytes: bytes | None = None
        if successor_phase == "running":
            successor = dict(recovering)
            successor.update(
                {
                    "phase": "running",
                    "recovery_of_lock_token": None,
                    "recovery_token": None,
                    "recovery_intent_path": None,
                    "recovery_intent_sha256": None,
                }
            )
            successor_bytes = _canonical_json_bytes(_validate_lock_document(successor))
        outcome_payload = {
            "schema_version": ACTIVE_RUN_RECOVERY_AUDIT_SCHEMA_VERSION,
            "run_id": run_id,
            "allocation_token": old_lock["allocation_token"],
            "intent_path": intent_ref["path"],
            "intent_sha256": intent_ref["sha256"],
            "recovery_token": recovery_token,
            "actor_kind": actor_kind,
            "operator_identity": operator_identity,
            "state_sha256": _sha256(state_bytes),
            "state_version": state["state_version"],
            "state_status": state["status"],
            "journal_anchor_sha256": _sha256(anchor_bytes),
            "publication_status": publication_status,
            "publication_transaction_id": publication_transaction_id,
            "publication_manifest_sha256": publication_manifest_sha256,
            "recovering_lock_sha256": _sha256(recovering_bytes),
            "successor_lock_sha256": None if successor_bytes is None else _sha256(successor_bytes),
            "successor_phase": successor_phase,
            "created_at": timestamp,
            "outcome_checksum": None,
        }
        outcome_payload["outcome_checksum"] = _sha256(
            _canonical_json_bytes({k: v for k, v in outcome_payload.items() if k != "outcome_checksum"})
        )
        outcome = _validate_recovery_outcome_document(outcome_payload)
        outcome_path = audit_dir / f"{basename}.outcome.1.json"
        _write_exclusive_document(
            outcome_path,
            _canonical_json_bytes(outcome),
            label="Active Run recovery outcome",
        )
        _sync_directory(root / "runs" / run_id, label="Run directory after recovery outcome")
        if successor_phase == "running":
            persisted = _atomic_update_lock(
                root,
                expected_allocation_token=old_lock["allocation_token"],
                expected_lock_token=new_lock_token,
                expected_bytes=recovering_bytes,
                update={
                    "phase": "running",
                    "recovery_of_lock_token": None,
                    "recovery_token": None,
                    "recovery_intent_path": None,
                    "recovery_intent_sha256": None,
                },
                expected_state={"phase": "recovering", "run_id": run_id},
                state_error="recovering Active Run Lock changed before successor",
            )
            if _canonical_json_bytes(persisted) != successor_bytes:
                raise ActiveRunLockError("recovery successor bytes do not match frozen outcome")
            if (
                persisted["pid"] == os.getpid()
                and persisted["hostname"] == socket.gethostname()
            ):
                _PROCESS_OWNED_ACTIVE_RUN_LOCK_IDENTITIES.add(
                    (
                        os.path.normcase(str(root)),
                        persisted["allocation_token"],
                        new_lock_token,
                    )
                )
        else:
            current_recovering, current_recovering_bytes = _read_lock(active_path)
            if (
                _sha256(current_recovering_bytes) != _sha256(recovering_bytes)
                or current_recovering["phase"] != "recovering"
                or current_recovering["lock_token"] != new_lock_token
            ):
                raise ActiveRunLockError(
                    "recovering Active Run Lock changed before terminal release"
                )
            release_active_run_lock(
                root,
                run_id=run_id,
                expected_allocation_token=old_lock["allocation_token"],
                expected_lock_token=new_lock_token,
            )
        return {"replayed": False, "successor_phase": successor_phase}
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            mutex_remaining = _lstat(mutex_path, label="Active Run recovery lock") is not None
        except BaseException as inspect_exc:
            if primary is not None:
                _add_exception_note(primary, f"recovery mutex inspection failed: {inspect_exc}")
                mutex_remaining = False
            else:
                raise
        if mutex_remaining:
            try:
                _remove_owned_recovery_mutex(mutex_path, runs, mutex_bytes)
            except BaseException as cleanup_exc:
                if primary is not None:
                    _add_exception_note(primary, f"recovery mutex cleanup failed: {cleanup_exc}")
                else:
                    raise


def release_active_run_lock(
    project_root: Path,
    *,
    run_id: str,
    expected_allocation_token: str,
    expected_lock_token: str,
) -> dict[str, Any]:
    with _active_run_state_lock(project_root):
        root, runs, path = _lock_path(project_root)
        current, current_bytes = _read_lock(path)
        try:
            current_identity = controlled_fs.file_identity(path)
        except (OSError, controlled_fs.ControlledFilesystemError) as exc:
            raise ActiveRunLockError(
                "unable to bind Active Run Lock identity before release"
            ) from exc
        if (
            current["allocation_token"] != expected_allocation_token
            or current["lock_token"] != expected_lock_token
            or current["reserved_run_id"] != run_id
        ):
            raise ActiveRunLockError(
                "cannot release an Active Run Lock owned by another caller"
            )
        tombstone = runs / f"{ACTIVE_RUN_RELEASE_PREFIX}{expected_lock_token}.json"
        if _lstat(tombstone, label="Active Run release tombstone") is not None:
            raise ActiveRunLockError("Active Run release tombstone already exists")
        tombstone_removed = False
        try:
            controlled_fs.rename(
                root,
                path.relative_to(root).as_posix(),
                tombstone.relative_to(root).as_posix(),
                replace=False,
                expected_source_identity=current_identity,
            )
            isolated_lock, isolated_bytes = _read_lock(tombstone)
            if (
                isolated_bytes != current_bytes
                or isolated_lock["lock_token"] != expected_lock_token
            ):
                raise ActiveRunLockError(
                    "Active Run release tombstone does not match the owned lock"
                )
            try:
                controlled_fs.unlink(
                    root,
                    tombstone.relative_to(root).as_posix(),
                    expected_identity=current_identity,
                )
                tombstone_removed = True
            except Exception:
                tombstone_removed = _lstat(
                    tombstone, label="Active Run release tombstone"
                ) is None
                raise
        except Exception as exc:
            error = ActiveRunLockError(
                f"Active Run Lock release is incomplete; inspect tombstone {tombstone.name}"
            )
            _add_exception_note(error, f"primary Active Run Lock release failure: {exc}")
            if tombstone_removed:
                for diagnostic in _restore_blocking_evidence(
                    tombstone,
                    runs,
                    current_bytes,
                    label="Active Run release tombstone",
                ):
                    _add_exception_note(error, diagnostic)
            raise error from exc
    forget_process_owned_active_run_lock_snapshot(
        root,
        allocation_token=expected_allocation_token,
        lock_token=expected_lock_token,
    )
    return {"released": True, "run_id": run_id, "lock_token": expected_lock_token}
