"""Phase C-3 review-decision preparation adapter.

This module is the first Phase C layer allowed to cross into the existing
StateStore and Active Run Lock contracts.  It deliberately keeps the C-2
admission boundary unchanged: C-2 remains the only code that can turn signed
bytes into an authority-bearing review validation.  The current adapter can
create or reuse an audit artifact, but always returns zero authority before a
StateStore CAS until a mandatory control-entry exclusion is available.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
import ctypes
from ctypes import wintypes
import hashlib
import json
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
from typing import Any
import uuid

from orchestrator.inspection_review_admission import (
    ReviewDecisionValidation,
    admit_review_decision,
)
from orchestrator.state.store import StateStore

from . import controlled_fs
from .locking import (
    acquire_waiting_review_lock,
    canonical_utc_now,
    release_active_run_lock,
    validate_active_run_lock,
)


REVIEW_DECISION_ARTIFACT_SCHEMA_VERSION = "inspection_review_decision_artifact_v1"
REVIEW_DECISION_ARTIFACT_PATH_TEMPLATE = "runs/{run_id}/artifacts/review_decision.json"

_RUN_ID_RE = re.compile(r"run_[0-9]{3,}\Z")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_ACCEPTED_AT_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_MAX_REVIEW_ARTIFACT_BYTES = 128 * 1024


@dataclass(frozen=True, slots=True)
class _ArtifactSnapshot:
    data: bytes
    identity: tuple[int, int]


class _ArtifactCommitFenceError(ValueError):
    """Raised when the artifact cannot be bound at the StateStore commit fence."""


class _ControlEntryCommitFenceError(ValueError):
    """Raised when the Active Run control-entry set cannot remain excluded."""


class _ControlEntryCommitFence:
    """A retained control-entry exclusion lease for a future capable backend.

    Acquisition must happen before ``StateStore.transition_status`` so the
    lease also covers StateStore recovery.  A concrete backend must retain the
    exclusion until ``close`` returns after all journal, State, and CAS checks.
    """

    def verify(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


def _canonical_json_bytes(value: Any) -> bytes:
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


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _state_sha256(state: Mapping[str, Any]) -> str:
    canonical = state.get("canonical_state")
    if not isinstance(canonical, Mapping):
        raise ValueError("StateStore did not return a canonical state mapping")
    return _sha256(_canonical_json_bytes(_plain(canonical)))


def _artifact_relative_path(run_id: str) -> str:
    if type(run_id) is not str or _RUN_ID_RE.fullmatch(run_id) is None:
        raise ValueError("run_id is not canonical")
    return REVIEW_DECISION_ARTIFACT_PATH_TEMPLATE.format(run_id=run_id)


def _zero(status: str, code: str) -> "ReviewCommitResult":
    result = object.__new__(ReviewCommitResult)
    object.__setattr__(result, "status", status)
    object.__setattr__(result, "denial_codes", (code,))
    for name in (
        "artifact_path",
        "artifact_sha256",
        "decision_status",
        "decision_sha256",
        "association_snapshot_sha256",
        "authority_evidence_sha256",
        "run_id",
        "association_id",
        "reviewer_id",
        "accepted_at",
        "next_status",
        "state_version",
        "state_sha256",
    ):
        object.__setattr__(result, name, None)
    result.__post_init__()
    return result


@dataclass(frozen=True, init=False, slots=True)
class ReviewCommitResult:
    """Immutable C-3 result; every non-committed result has zero bindings."""

    status: str
    denial_codes: tuple[str, ...]
    artifact_path: str | None
    artifact_sha256: str | None
    decision_status: str | None
    decision_sha256: str | None
    association_snapshot_sha256: str | None
    authority_evidence_sha256: str | None
    run_id: str | None
    association_id: str | None
    reviewer_id: str | None
    accepted_at: str | None
    next_status: str | None
    state_version: int | None
    state_sha256: str | None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("ReviewCommitResult is created only by commit_review_decision")

    def __post_init__(self) -> None:
        if self.status not in {
            "review_committed",
            "review_commit_invalid",
            "review_commit_conflict",
        }:
            raise ValueError("unknown review commit status")
        fields = (
            self.artifact_path,
            self.artifact_sha256,
            self.decision_status,
            self.decision_sha256,
            self.association_snapshot_sha256,
            self.authority_evidence_sha256,
            self.run_id,
            self.association_id,
            self.reviewer_id,
            self.accepted_at,
            self.next_status,
            self.state_version,
            self.state_sha256,
        )
        if self.status != "review_committed":
            if not self.denial_codes or any(value is not None for value in fields):
                raise ValueError("failed review commits must carry zero authority")
            return
        if self.denial_codes:
            raise ValueError("committed review must not carry denial codes")
        if (
            type(self.artifact_path) is not str
            or self.artifact_path != _artifact_relative_path(self.run_id)
            or type(self.artifact_sha256) is not str
            or _SHA256_RE.fullmatch(self.artifact_sha256) is None
            or self.decision_status not in {"human_verified", "human_rejected"}
            or any(
                type(value) is not str or _SHA256_RE.fullmatch(value) is None
                for value in (
                    self.decision_sha256,
                    self.association_snapshot_sha256,
                    self.authority_evidence_sha256,
                    self.state_sha256,
                )
            )
            or type(self.run_id) is not str
            or _RUN_ID_RE.fullmatch(self.run_id) is None
            or type(self.association_id) is not str
            or _SAFE_ID_RE.fullmatch(self.association_id) is None
            or type(self.reviewer_id) is not str
            or _SAFE_ID_RE.fullmatch(self.reviewer_id) is None
            or type(self.accepted_at) is not str
            or _ACCEPTED_AT_RE.fullmatch(self.accepted_at) is None
            or self.next_status not in {"RUNNING", "BLOCKED"}
            or type(self.state_version) is not int
            or self.state_version < 1
        ):
            raise ValueError("committed review result bindings are invalid")


def _authority_bindings(
    validation: ReviewDecisionValidation,
    *,
    decision_bytes: bytes,
    authority_evidence_bytes: bytes,
    run_id: str,
    association_id: str,
    allocation_token: str,
    expected_state_version: int,
    expected_state_sha256: str,
    decision_token: str,
) -> dict[str, Any]:
    if type(validation) is not ReviewDecisionValidation:
        raise ValueError("C-2 returned an unexpected validation object")
    if validation.status not in {"human_verified", "human_rejected"}:
        raise ValueError("C-2 did not return authority")
    if validation.run_id != run_id or validation.association_id != association_id:
        raise ValueError("C-2 subject binding does not match State")
    if (
        type(validation.decision_sha256) is not str
        or _SHA256_RE.fullmatch(validation.decision_sha256) is None
        or _sha256(decision_bytes) != validation.decision_sha256
        or type(validation.authority_evidence_sha256) is not str
        or _SHA256_RE.fullmatch(validation.authority_evidence_sha256) is None
        or _sha256(authority_evidence_bytes) != validation.authority_evidence_sha256
        or type(validation.association_snapshot_sha256) is not str
        or _SHA256_RE.fullmatch(validation.association_snapshot_sha256) is None
        or type(validation.reviewer_id) is not str
        or _SAFE_ID_RE.fullmatch(validation.reviewer_id) is None
        or type(validation.accepted_at) is not str
        or _ACCEPTED_AT_RE.fullmatch(validation.accepted_at) is None
    ):
        raise ValueError("C-2 returned incomplete or inconsistent authority bindings")
    next_status = "RUNNING" if validation.status == "human_verified" else "BLOCKED"
    return {
        "schema_version": REVIEW_DECISION_ARTIFACT_SCHEMA_VERSION,
        "run_id": run_id,
        "association_id": association_id,
        "reviewer_id": validation.reviewer_id,
        "decision_status": validation.status,
        "next_status": next_status,
        "decision_bytes_base64": base64.b64encode(decision_bytes).decode("ascii"),
        "decision_sha256": validation.decision_sha256,
        "association_snapshot_sha256": validation.association_snapshot_sha256,
        "authority_evidence_sha256": validation.authority_evidence_sha256,
        "accepted_at": validation.accepted_at,
        "allocation_token": allocation_token,
        "expected_state_version": expected_state_version,
        "expected_state_sha256": expected_state_sha256,
        "decision_token": decision_token,
    }


def _same_validation(left: ReviewDecisionValidation, right: ReviewDecisionValidation) -> bool:
    if type(left) is not ReviewDecisionValidation or type(right) is not ReviewDecisionValidation:
        return False
    return all(
        getattr(left, field) == getattr(right, field)
        for field in (
            "status",
            "denial_codes",
            "decision_sha256",
            "association_snapshot_sha256",
            "authority_evidence_sha256",
            "run_id",
            "association_id",
            "reviewer_id",
            "accepted_at",
        )
    )


def _artifact_parts(relative: str, *, run_id: str) -> tuple[str, ...]:
    parts = controlled_fs._parts(relative)
    if (
        len(parts) != 4
        or parts[0] != "runs"
        or parts[1] != run_id
        or parts[2:] != ("artifacts", "review_decision.json")
    ):
        raise ValueError("review artifact path is not fixed")
    return parts


def _read_artifact_descriptor(descriptor: int) -> _ArtifactSnapshot:
    """Read one bounded regular-file snapshot through an already-open handle."""

    os.lseek(descriptor, 0, os.SEEK_SET)
    state = os.fstat(descriptor)
    if not stat.S_ISREG(state.st_mode):
        raise ValueError("review artifact is not a regular file")
    if os.name == "nt":
        identity = controlled_fs._win_handle_identity(
            controlled_fs.msvcrt.get_osfhandle(descriptor)
        )
    else:
        identity = (state.st_dev, state.st_ino)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, 64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_REVIEW_ARTIFACT_BYTES:
            raise ValueError("review artifact exceeds the bounded size")
        chunks.append(chunk)
    after = os.fstat(descriptor)
    if (after.st_dev, after.st_ino) != (state.st_dev, state.st_ino):
        raise ValueError("review artifact identity changed during read")
    if os.name == "nt":
        after_identity = controlled_fs._win_handle_identity(
            controlled_fs.msvcrt.get_osfhandle(descriptor)
        )
        if after_identity != identity:
            raise ValueError("review artifact handle identity changed during read")
    return _ArtifactSnapshot(b"".join(chunks), identity)


def _require_artifact_name_binding(
    parent: int, leaf: str, expected_identity: tuple[int, int]
) -> None:
    """Reject a leaf replacement that raced an otherwise stable read."""

    if os.name == "nt":
        handle = controlled_fs._nt_open(
            parent,
            leaf,
            disposition=controlled_fs._FILE_OPEN,
            directory=False,
            access=controlled_fs._GENERIC_READ | controlled_fs._FILE_READ_ATTRIBUTES,
        )
        named_descriptor = controlled_fs._win_fd_or_dispose(handle)
        try:
            named_identity = controlled_fs._win_handle_identity(
                controlled_fs.msvcrt.get_osfhandle(named_descriptor)
            )
        finally:
            os.close(named_descriptor)
        if named_identity != expected_identity:
            raise ValueError("review artifact name changed during read")
        return
    entry = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
    if (
        not stat.S_ISREG(entry.st_mode)
        or (entry.st_dev, entry.st_ino) != expected_identity
    ):
        raise ValueError("review artifact name changed during read")


def _win_open_artifact_exclusion(parent: int, leaf: str) -> int:
    """Open one leaf relative to ``parent`` while denying write/delete sharing.

    This deliberately lives in C-3 rather than extending C-2's controlled
    filesystem import surface.  It mirrors the already-reviewed relative
    ``NtCreateFile`` primitive and never falls back to a path-based open.
    """

    if os.name != "nt":
        raise OSError("Windows artifact exclusion is unavailable")
    buffer = ctypes.create_unicode_buffer(leaf)
    encoded = leaf.encode("utf-16-le")
    unicode_name = controlled_fs._UNICODE_STRING(
        len(encoded), len(encoded), ctypes.cast(buffer, wintypes.LPWSTR)
    )
    attributes = controlled_fs._OBJECT_ATTRIBUTES(
        ctypes.sizeof(controlled_fs._OBJECT_ATTRIBUTES),
        wintypes.HANDLE(parent),
        ctypes.pointer(unicode_name),
        controlled_fs._OBJ_CASE_INSENSITIVE,
        None,
        None,
    )
    status_block = controlled_fs._IO_STATUS_BLOCK()
    handle = wintypes.HANDLE()
    options = (
        controlled_fs._FILE_NON_DIRECTORY_FILE
        | controlled_fs._FILE_SYNCHRONOUS_IO_NONALERT
        | controlled_fs._FILE_OPEN_REPARSE_POINT
    )
    status = controlled_fs._ntdll.NtCreateFile(
        ctypes.byref(handle),
        controlled_fs._GENERIC_READ
        | controlled_fs._FILE_READ_ATTRIBUTES
        | controlled_fs._SYNCHRONIZE,
        ctypes.byref(attributes),
        ctypes.byref(status_block),
        None,
        controlled_fs._FILE_ATTRIBUTE_NORMAL,
        0x00000001,  # FILE_SHARE_READ: deny writes and deletes until release.
        controlled_fs._FILE_OPEN,
        options,
        None,
        0,
    )
    if status < 0:
        error = int(controlled_fs._ntdll.RtlNtStatusToDosError(status))
        if error in {2, 3}:
            raise FileNotFoundError(error, os.strerror(error), leaf)
        raise OSError(error, os.strerror(error), leaf)
    value = ctypes.cast(handle, ctypes.c_void_p).value
    if value is None:
        raise OSError("Windows artifact exclusion returned no handle")
    try:
        controlled_fs._assert_plain_handle(int(value), directory=False)
    except BaseException:
        controlled_fs._close_handle(int(value))
        raise
    try:
        # ``controlled_fs._win_fd_or_dispose`` is deliberately write-oriented:
        # its failure cleanup can mark an unpublished temporary file for
        # deletion.  This retained exclusion handle is read-only, so transfer
        # it directly to a read-only CRT descriptor instead.
        return controlled_fs.msvcrt.open_osfhandle(
            int(value), os.O_BINARY | os.O_RDONLY
        )
    except BaseException:
        controlled_fs._close_handle(int(value))
        raise


def _read_existing_artifact(
    root: Path, relative: str, *, run_id: str
) -> _ArtifactSnapshot | None:
    parts = _artifact_parts(relative, run_id=run_id)

    def read_bound_descriptor(descriptor: int, parent: int) -> _ArtifactSnapshot:
        first = _read_artifact_descriptor(descriptor)
        _require_artifact_name_binding(parent, parts[-1], first.identity)
        second = _read_artifact_descriptor(descriptor)
        _require_artifact_name_binding(parent, parts[-1], second.identity)
        if second != first:
            raise ValueError("review artifact content changed during read")
        return second

    if os.name == "nt":
        try:
            with controlled_fs._win_parent(Path(root), parts[:-1]) as parent:
                handle = controlled_fs._nt_open(
                    parent,
                    parts[-1],
                    disposition=controlled_fs._FILE_OPEN,
                    directory=False,
                    access=controlled_fs._GENERIC_READ | controlled_fs._FILE_READ_ATTRIBUTES,
                )
                descriptor = controlled_fs._win_fd_or_dispose(handle)
                try:
                    data = read_bound_descriptor(descriptor, parent)
                finally:
                    os.close(descriptor)
        except FileNotFoundError:
            return None
    else:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            with controlled_fs._posix_parent(Path(root), parts[:-1]) as parent:
                descriptor = os.open(parts[-1], flags, dir_fd=parent)
                try:
                    data = read_bound_descriptor(descriptor, parent)
                finally:
                    os.close(descriptor)
        except FileNotFoundError:
            return None
    return data


def _artifact_snapshot_matches(
    snapshot: _ArtifactSnapshot | None,
    *,
    expected_bytes: bytes,
    expected_identity: tuple[int, int],
    expected_sha256: str,
) -> bool:
    return (
        snapshot is not None
        and snapshot.data == expected_bytes
        and snapshot.identity == expected_identity
        and _sha256(snapshot.data) == expected_sha256
    )


def _artifact_snapshot_has_expected_token(
    snapshot: _ArtifactSnapshot,
    *,
    expected_token: str,
) -> bool:
    try:
        payload = json.loads(snapshot.data.decode("utf-8"))
        return (
            type(payload) is dict
            and _canonical_json_bytes(payload) == snapshot.data
            and payload.get("decision_token") == expected_token
        )
    except (UnicodeError, ValueError, json.JSONDecodeError):
        return False


class _ArtifactCommitFence:
    """Retain an OS-level exclusion from the pending append through State CAS.

    The first verification occurs *inside* the StateStore journal append
    primitive.  Windows retains a read-only, no-write/no-delete handle and
    rechecks every canonical ancestor.  POSIX has no equivalent mandatory
    directory-entry exclusion, so it refuses an authority-bearing commit
    rather than trusting a moved ``artifacts`` descriptor.
    """

    def __init__(
        self,
        root: Path,
        relative: str,
        *,
        run_id: str,
        expected_bytes: bytes,
        expected_identity: tuple[int, int],
        expected_sha256: str,
        expected_token: str,
    ) -> None:
        self._root = Path(root).absolute()
        self._parts = _artifact_parts(relative, run_id=run_id)
        self._expected_bytes = expected_bytes
        self._expected_identity = expected_identity
        self._expected_sha256 = expected_sha256
        self._expected_token = expected_token
        self._parent_context: Any | None = None
        self._parent: int | None = None
        self._descriptor: int | None = None
        current = self._root
        paths = [current]
        for part in self._parts[:-1]:
            current = current / part
            paths.append(current)
        self._ancestor_paths = tuple(paths)
        self._ancestor_identities: tuple[tuple[int, int], ...] | None = None

    def _capture_ancestor_identities(self) -> None:
        self._ancestor_identities = tuple(
            controlled_fs.directory_identity(path) for path in self._ancestor_paths
        )

    def _verify_ancestor_bindings(self) -> None:
        expected = self._ancestor_identities
        if expected is None or len(expected) != len(self._ancestor_paths):
            raise _ArtifactCommitFenceError("review artifact ancestor binding is unavailable")
        observed = tuple(
            controlled_fs.directory_identity(path) for path in self._ancestor_paths
        )
        if observed != expected:
            raise _ArtifactCommitFenceError("review artifact ancestor changed during commit")
        if self._parent is not None and controlled_fs._win_handle_identity(self._parent) != expected[-1]:
            raise _ArtifactCommitFenceError("review artifact directory handle changed during commit")

    def _open(self) -> None:
        try:
            self._capture_ancestor_identities()
            if os.name != "nt":
                raise _ArtifactCommitFenceError(
                    "POSIX lacks mandatory artifact-ancestor exclusion"
                )
            self._parent_context = controlled_fs._win_parent(
                self._root, self._parts[:-1]
            )
            self._parent = self._parent_context.__enter__()
            if controlled_fs._win_handle_identity(self._parent) != self._ancestor_identities[-1]:
                raise _ArtifactCommitFenceError(
                    "review artifact ancestor handle changed during commit"
                )
            self._descriptor = _win_open_artifact_exclusion(
                self._parent, self._parts[-1]
            )
            self._verify_held()
        except BaseException:
            try:
                self.close()
            except Exception:
                pass
            raise

    def _verify_held(self) -> None:
        if self._descriptor is None or self._parent is None:
            raise _ArtifactCommitFenceError("review artifact fence is not open")
        self._verify_ancestor_bindings()
        snapshot = _read_artifact_descriptor(self._descriptor)
        _require_artifact_name_binding(
            self._parent, self._parts[-1], snapshot.identity
        )
        if not _artifact_snapshot_matches(
            snapshot,
            expected_bytes=self._expected_bytes,
            expected_identity=self._expected_identity,
            expected_sha256=self._expected_sha256,
        ) or not _artifact_snapshot_has_expected_token(
            snapshot, expected_token=self._expected_token
        ):
            raise _ArtifactCommitFenceError(
                "review artifact changed before StateStore commit"
            )

    def verify(self) -> None:
        try:
            if self._descriptor is None:
                self._open()
            else:
                self._verify_held()
        except _ArtifactCommitFenceError:
            raise
        except (
            OSError,
            UnicodeError,
            ValueError,
            json.JSONDecodeError,
            controlled_fs.ControlledFilesystemError,
        ) as exc:
            raise _ArtifactCommitFenceError(
                "review artifact cannot be bound at StateStore commit"
            ) from exc

    def close(self) -> None:
        errors: list[BaseException] = []
        descriptor = self._descriptor
        self._descriptor = None
        self._parent = None
        self._ancestor_identities = None
        if descriptor is not None:
            try:
                os.close(descriptor)
            except BaseException as exc:
                errors.append(exc)
        context = self._parent_context
        self._parent_context = None
        if context is not None:
            try:
                context.__exit__(None, None, None)
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise _ArtifactCommitFenceError(
                f"review artifact fence cleanup failed: {errors[0]}"
            ) from errors[0]


def _acquire_mandatory_control_entry_exclusion(
    project_root: Path,
    *,
    run_id: str,
    allocation_token: str,
    lock_token: str,
) -> _ControlEntryCommitFence:
    """Acquire a retained exclusion before any StateStore mutation work.

    A recovery lock or release tombstone is authority-blocking evidence.  The
    available portable filesystem APIs cannot make a sibling-entry snapshot
    atomic with a StateStore journal append: POSIX has no mandatory sibling
    entry lock, while Windows directory sharing does not stop sibling creates.
    Until a platform-specific mandatory primitive is supplied, this capability
    gate fails closed before ``StateStore.transition_status`` can begin
    recovery or open a pending record.
    """

    del project_root, run_id, allocation_token, lock_token

    raise _ControlEntryCommitFenceError(
        "mandatory Active Run control-entry exclusion is unavailable"
    )


def _reuse_existing_decision_token(
    existing: bytes,
    candidate: Mapping[str, Any],
) -> tuple[bytes, str] | None:
    """Accept only a canonical crash residue with identical authority inputs."""

    try:
        parsed = json.loads(existing.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if type(parsed) is not dict or set(parsed) != set(candidate):
        return None
    token = parsed.get("decision_token")
    try:
        if type(token) is not str or str(uuid.UUID(token)) != token:
            return None
    except (ValueError, AttributeError):
        return None
    without_token = dict(parsed)
    without_token.pop("decision_token", None)
    expected_without_token = dict(candidate)
    expected_without_token.pop("decision_token", None)
    if without_token != expected_without_token:
        return None
    try:
        if _canonical_json_bytes(parsed) != existing:
            return None
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return None
    return existing, token


def _success(
    validation: ReviewDecisionValidation,
    *,
    relative: str,
    artifact_sha256: str,
    state_version: int,
    state_sha256: str,
) -> ReviewCommitResult:
    result = object.__new__(ReviewCommitResult)
    object.__setattr__(result, "status", "review_committed")
    object.__setattr__(result, "denial_codes", ())
    object.__setattr__(result, "artifact_path", relative)
    object.__setattr__(result, "artifact_sha256", artifact_sha256)
    object.__setattr__(result, "decision_status", validation.status)
    object.__setattr__(result, "decision_sha256", validation.decision_sha256)
    object.__setattr__(result, "association_snapshot_sha256", validation.association_snapshot_sha256)
    object.__setattr__(result, "authority_evidence_sha256", validation.authority_evidence_sha256)
    object.__setattr__(result, "run_id", validation.run_id)
    object.__setattr__(result, "association_id", validation.association_id)
    object.__setattr__(result, "reviewer_id", validation.reviewer_id)
    object.__setattr__(result, "accepted_at", validation.accepted_at)
    object.__setattr__(result, "next_status", "RUNNING" if validation.status == "human_verified" else "BLOCKED")
    object.__setattr__(result, "state_version", state_version)
    object.__setattr__(result, "state_sha256", state_sha256)
    result.__post_init__()
    return result


def commit_review_decision(
    *,
    project_root: Path,
    project_context: object,
    run_id: str,
    association_id: str,
    waiting_lock_token: str,
    decision_bytes: bytes,
    authority_evidence_bytes: bytes,
    trusted_authority_sha256: object,
) -> ReviewCommitResult:
    """Prepare one admitted review; commit only with a retained control lease.

    ``accepted_at`` is intentionally absent from this signature.  It is
    produced by the isolated C-2 admission clock and copied only after that
    result has been independently checked here.  With the currently available
    filesystem primitives, this function returns zero authority before any
    StateStore CAS.
    """

    fresh_lock_token: str | None = None
    fresh_allocation_token: str | None = None
    result: ReviewCommitResult | None = None
    try:
        store = StateStore(project_root)
        state = store.load(run_id=run_id)
        if state["status"] != "WAITING_FOR_REVIEW":
            return _zero("review_commit_conflict", "review_state_not_waiting")
        canonical_state = state.get("canonical_state")
        if not isinstance(canonical_state, Mapping):
            return _zero("review_commit_conflict", "review_state_malformed")
        allocation_token = canonical_state.get("allocation_token")
        if type(allocation_token) is not str:
            return _zero("review_commit_conflict", "review_allocation_binding_missing")
        context = canonical_state.get("context")
        if not isinstance(context, Mapping) or type(context.get("workflow_task_id")) is not str:
            return _zero("review_commit_conflict", "review_task_binding_missing")
        task_id = context["workflow_task_id"]
        validate_active_run_lock(
            project_root,
            run_id=run_id,
            allocation_token=allocation_token,
            expected_lock_token=waiting_lock_token,
            allowed_phases={"running"},
        )
        expected_state_version = state["state_version"]
        expected_state_sha256 = _state_sha256(state)

        validation = admit_review_decision(
            project_context=project_context,
            expected_run_id=run_id,
            expected_association_id=association_id,
            decision_bytes=decision_bytes,
            authority_evidence_bytes=authority_evidence_bytes,
            trusted_authority_sha256=trusted_authority_sha256,
        )
        if type(validation) is not ReviewDecisionValidation or validation.status == "review_invalid":
            return _zero("review_commit_invalid", "review_decision_invalid")

        decision_token = str(uuid.uuid4())
        artifact = _authority_bindings(
            validation,
            decision_bytes=decision_bytes,
            authority_evidence_bytes=authority_evidence_bytes,
            run_id=run_id,
            association_id=association_id,
            allocation_token=allocation_token,
            expected_state_version=expected_state_version,
            expected_state_sha256=expected_state_sha256,
            decision_token=decision_token,
        )
        artifact_bytes = _canonical_json_bytes(artifact)
        relative = _artifact_relative_path(run_id)
        existing = _read_existing_artifact(project_root, relative, run_id=run_id)
        if existing is not None and existing.data != artifact_bytes:
            reused = _reuse_existing_decision_token(existing.data, artifact)
            if reused is None:
                return _zero("review_commit_conflict", "review_artifact_conflict")
            _, decision_token = reused
            artifact["decision_token"] = decision_token
            artifact_bytes = _canonical_json_bytes(artifact)
            if existing.data != artifact_bytes:
                return _zero("review_commit_conflict", "review_artifact_conflict")
        if existing is None:
            try:
                controlled_fs.write_exclusive(project_root, relative, artifact_bytes)
            except FileExistsError:
                existing = _read_existing_artifact(project_root, relative, run_id=run_id)
                if existing is None or existing.data != artifact_bytes:
                    reused = (
                        _reuse_existing_decision_token(existing.data, artifact)
                        if existing is not None
                        else None
                    )
                    if reused is None:
                        return _zero("review_commit_conflict", "review_artifact_conflict")
                    _, decision_token = reused
                    artifact["decision_token"] = decision_token
                    artifact_bytes = _canonical_json_bytes(artifact)
                    if existing.data != artifact_bytes:
                        return _zero("review_commit_conflict", "review_artifact_conflict")
            except Exception:
                return _zero("review_commit_conflict", "review_artifact_write_failed")
        committed_snapshot = _read_existing_artifact(project_root, relative, run_id=run_id)
        if committed_snapshot is None:
            raise ValueError("review artifact disappeared after publication")
        if committed_snapshot.data != artifact_bytes:
            return _zero("review_commit_conflict", "review_artifact_changed")
        if _canonical_json_bytes(json.loads(committed_snapshot.data.decode("utf-8"))) != committed_snapshot.data:
            return _zero("review_commit_conflict", "review_artifact_noncanonical")
        committed_payload = json.loads(committed_snapshot.data.decode("utf-8"))
        if (
            type(committed_payload) is not dict
            or committed_payload.get("decision_token") != decision_token
        ):
            return _zero("review_commit_conflict", "review_artifact_token_changed")
        artifact_sha256 = _sha256(committed_snapshot.data)
        artifact_identity = committed_snapshot.identity

        # The waiting lock is released only after the immutable artifact has
        # been verified.  Release uncertainty intentionally stops here.
        release_active_run_lock(
            project_root,
            run_id=run_id,
            expected_allocation_token=allocation_token,
            expected_lock_token=waiting_lock_token,
        )

        fresh_lock_token = str(uuid.uuid4())
        fresh_allocation_token = allocation_token
        acquire_waiting_review_lock(
            project_root,
            run_id=run_id,
            allocation_token=allocation_token,
            lock_token=fresh_lock_token,
            expected_state_version=expected_state_version,
            task_id=task_id,
        )
        fresh_state = store.load(run_id=run_id)
        if (
            fresh_state["status"] != "WAITING_FOR_REVIEW"
            or fresh_state["state_version"] != expected_state_version
            or not isinstance(fresh_state.get("canonical_state"), Mapping)
            or fresh_state["canonical_state"].get("allocation_token") != allocation_token
            or _state_sha256(fresh_state) != expected_state_sha256
        ):
            result = _zero("review_commit_conflict", "review_state_changed")
        else:
            revalidated = admit_review_decision(
                project_context=project_context,
                expected_run_id=run_id,
                expected_association_id=association_id,
                decision_bytes=decision_bytes,
                authority_evidence_bytes=authority_evidence_bytes,
                trusted_authority_sha256=trusted_authority_sha256,
            )
            if not _same_validation(validation, revalidated):
                result = _zero("review_commit_conflict", "review_authority_changed")
            else:
                fresh_snapshot = _read_existing_artifact(
                    project_root, relative, run_id=run_id
                )
                if not _artifact_snapshot_matches(
                    fresh_snapshot,
                    expected_bytes=artifact_bytes,
                    expected_identity=artifact_identity,
                    expected_sha256=artifact_sha256,
                ):
                    result = _zero("review_commit_conflict", "review_artifact_changed")
                else:
                    try:
                        control_fence = _acquire_mandatory_control_entry_exclusion(
                            project_root,
                            run_id=run_id,
                            allocation_token=allocation_token,
                            lock_token=fresh_lock_token,
                        )
                    except _ControlEntryCommitFenceError:
                        result = _zero(
                            "review_commit_conflict",
                            "review_control_entry_unavailable",
                        )
                    else:
                        fence: _ArtifactCommitFence | None = None
                        try:
                            next_status = "RUNNING" if validation.status == "human_verified" else "BLOCKED"
                            operation_id = (
                                f"run:{run_id}:transition:v{expected_state_version}:"
                                f"WAITING_FOR_REVIEW:{next_status}:decision:{decision_token}"
                            )
                            fence = _ArtifactCommitFence(
                                project_root,
                                relative,
                                run_id=run_id,
                                expected_bytes=artifact_bytes,
                                expected_identity=artifact_identity,
                                expected_sha256=artifact_sha256,
                                expected_token=decision_token,
                            )

                            def pre_commit_guard() -> None:
                                # The retained control lease covers StateStore
                                # recovery and this final pending-record boundary.
                                control_fence.verify()
                                fence.verify()

                            try:
                                # Fail before StateStore can reconcile an
                                # unresolved pending record.  The guard repeats
                                # this check at the pending-write boundary.
                                control_fence.verify()
                                fence.verify()
                                mutation = store.transition_status(
                                    run_id=run_id,
                                    expected_lock_token=fresh_lock_token,
                                    expected_status="WAITING_FOR_REVIEW",
                                    expected_state_version=expected_state_version,
                                    operation_id=operation_id,
                                    mutation_timestamp=canonical_utc_now(),
                                    payload={
                                        "next_status": next_status,
                                        "metadata": {
                                            "review_decision_artifact_path": relative,
                                            "review_decision_artifact_sha256": artifact_sha256,
                                            "decision_status": validation.status,
                                            "decision_sha256": validation.decision_sha256,
                                            "association_snapshot_sha256": validation.association_snapshot_sha256,
                                            "authority_evidence_sha256": validation.authority_evidence_sha256,
                                            "reviewer_id": validation.reviewer_id,
                                            "accepted_at": validation.accepted_at,
                                            "expected_state_sha256": expected_state_sha256,
                                        },
                                        "completion_evidence": None,
                                        "transition_kind": "decision",
                                        "decision_token": decision_token,
                                    },
                                    pre_commit_guard=pre_commit_guard,
                                )
                            except _ArtifactCommitFenceError:
                                result = _zero("review_commit_conflict", "review_artifact_changed")
                            except _ControlEntryCommitFenceError:
                                result = _zero("review_commit_conflict", "review_control_entry_unavailable")
                            else:
                                try:
                                    # Both retained exclusions remain live through
                                    # journal, State, and canonical-State checks.
                                    control_fence.verify()
                                    fence.verify()
                                    persisted = store.load(run_id=run_id)
                                    expected_result = mutation.get("canonical_state")
                                    if (
                                        not isinstance(expected_result, Mapping)
                                        or _plain(expected_result) != _plain(persisted.get("canonical_state"))
                                        or persisted["state_version"] != expected_state_version + 1
                                        or persisted["status"] != next_status
                                    ):
                                        result = _zero("review_commit_conflict", "review_state_commit_unverified")
                                    else:
                                        result = _success(
                                            validation,
                                            relative=relative,
                                            artifact_sha256=artifact_sha256,
                                            state_version=persisted["state_version"],
                                            state_sha256=_state_sha256(persisted),
                                        )
                                except _ArtifactCommitFenceError:
                                    result = _zero("review_commit_conflict", "review_artifact_changed")
                                except _ControlEntryCommitFenceError:
                                    result = _zero("review_commit_conflict", "review_control_entry_unavailable")
                        finally:
                            cleanup_failed = False
                            if fence is not None:
                                try:
                                    fence.close()
                                except Exception:
                                    cleanup_failed = True
                            try:
                                control_fence.close()
                            except Exception:
                                cleanup_failed = True
                            # A lease release failure before CAS is a zero-authority
                            # failure.  After a verified durable CAS, however, the
                            # state cannot be rolled back; reporting a zero result
                            # would lie about the committed decision.  A future
                            # lease backend must therefore make post-CAS release
                            # idempotent and recoverable without changing the
                            # already committed result.
                            if cleanup_failed and (
                                result is None or result.status != "review_committed"
                            ):
                                result = _zero("review_commit_conflict", "review_commit_guard_cleanup_failed")
    except Exception:
        if result is None:
            result = _zero("review_commit_conflict", "review_commit_unavailable")
    finally:
        if fresh_lock_token is not None and fresh_allocation_token is not None:
            try:
                release_active_run_lock(
                    project_root,
                    run_id=run_id,
                    expected_allocation_token=fresh_allocation_token,
                    expected_lock_token=fresh_lock_token,
                )
            except Exception:
                # The StateStore transition is already durable.  Do not
                # disguise an authority-bearing committed state as a retryable
                # zero-authority failure merely because its post-CAS lock
                # release is uncertain.
                if result is None or result.status != "review_committed":
                    result = _zero("review_commit_conflict", "review_lock_release_uncertain")
    if result is None:
        return _zero("review_commit_conflict", "review_commit_unavailable")
    return result


__all__ = [
    "REVIEW_DECISION_ARTIFACT_PATH_TEMPLATE",
    "REVIEW_DECISION_ARTIFACT_SCHEMA_VERSION",
    "ReviewCommitResult",
    "commit_review_decision",
]
