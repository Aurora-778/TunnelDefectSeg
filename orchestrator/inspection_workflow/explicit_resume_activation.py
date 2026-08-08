"""Bounded activation of a successor Run after explicit resume admission.

This module deliberately stops before controller or executor construction.  It
turns one current B.5 proof for a completed source Run into one fenced,
``CREATED`` successor Run owned by the existing Active Run Lock machinery.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid
from typing import Any, Mapping

from . import a1_artifacts, artifact_resolver, explicit_resume_admission
from .explicit_resume_admission import ExplicitResumeAdmissionResult
from .locking import (
    ActiveRunLockError,
    _sync_directory,
    acquire_active_run_lock,
    mark_active_run_running,
    read_existing_active_run_lock,
    reserve_active_run_id,
)
from orchestrator.state.store import StateStore, StateStoreError


_SCHEMA = "inspection_explicit_resume_activation_v1"
_INTENT_SCHEMA = "inspection_explicit_resume_activation_intent_v1"
_ACTIVATED = "resume_activated"
_NOT_ACTIVATED = "resume_not_activated"
_RUN_ID_RE = re.compile(r"run_[0-9]{3,}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_INTENT_FIELDS = frozenset(
    {
        "schema_version",
        "source_run_id",
        "successor_run_id",
        "source_admission_sha256",
        "source_state_version",
        "plan_fingerprint",
        "input_descriptor_sha256",
        "allocation_token",
        "lock_token",
        "intent_checksum",
    }
)
_B5_TYPE = explicit_resume_admission.ExplicitResumeAdmission
_B5_ADMIT = _B5_TYPE.admit
_B1_READ_GUARDED = artifact_resolver._read_guarded_file
_ACQUIRE_ACTIVE_RUN_LOCK = acquire_active_run_lock
_RESERVE_ACTIVE_RUN_ID = reserve_active_run_id
_MARK_ACTIVE_RUN_RUNNING = mark_active_run_running


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
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


def _thaw_json(value: Any) -> Any:
    """Copy a StateStore frozen snapshot without accepting caller objects."""

    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_json(item) for item in value]
    return value


def _is_uuid(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _is_plain_directory(path: Path) -> bool:
    try:
        entry = path.lstat()
    except OSError:
        return False
    attributes = getattr(entry, "st_file_attributes", 0)
    return (
        stat.S_ISDIR(entry.st_mode)
        and not stat.S_ISLNK(entry.st_mode)
        and not bool(attributes & 0x400)
    )


def _is_plain_regular(path: Path) -> bool:
    try:
        entry = path.lstat()
    except OSError:
        return False
    attributes = getattr(entry, "st_file_attributes", 0)
    return (
        stat.S_ISREG(entry.st_mode)
        and not stat.S_ISLNK(entry.st_mode)
        and not bool(attributes & 0x400)
    )


def _canonical_bytes(status: str, bindings: Mapping[str, Any] | None = None) -> bytes:
    document: dict[str, Any] = {"schema_version": _SCHEMA, "status": status}
    if status == _ACTIVATED:
        if bindings is None:
            raise ValueError("activated result requires bindings")
        document["bindings"] = dict(bindings)
    return _canonical_json_bytes(document)


def _not_activated() -> "ExplicitResumeActivationResult":
    data = _canonical_bytes(_NOT_ACTIVATED)
    result = object.__new__(ExplicitResumeActivationResult)
    object.__setattr__(result, "status", _NOT_ACTIVATED)
    object.__setattr__(result, "activation_bytes", data)
    object.__setattr__(result, "activation_sha256", _sha256(data))
    for name in _SUCCESS_FIELDS:
        object.__setattr__(result, name, None)
    result.__post_init__()
    return result


_SUCCESS_FIELDS = (
    "source_run_id",
    "successor_run_id",
    "intent_sha256",
    "allocation_token",
    "lock_token",
    "source_admission_sha256",
    "state_version",
    "plan_fingerprint",
    "input_descriptor_sha256",
    "state_sha256",
    "journal_anchor_sha256",
    "lock_sha256",
)


@dataclass(frozen=True, init=False, slots=True)
class ExplicitResumeActivationResult:
    """Immutable result; a success can only be made by ``activate``."""

    status: str
    activation_bytes: bytes
    activation_sha256: str
    source_run_id: str | None
    successor_run_id: str | None
    intent_sha256: str | None
    allocation_token: str | None
    lock_token: str | None
    source_admission_sha256: str | None
    state_version: int | None
    plan_fingerprint: str | None
    input_descriptor_sha256: str | None
    state_sha256: str | None
    journal_anchor_sha256: str | None
    lock_sha256: str | None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("ExplicitResumeActivationResult is created only by ExplicitResumeActivation")

    def __post_init__(self) -> None:
        if self.status not in {_ACTIVATED, _NOT_ACTIVATED}:
            raise ValueError("activation status is invalid")
        if type(self.activation_bytes) is not bytes or type(self.activation_sha256) is not str:
            raise ValueError("activation bytes must be immutable bytes")
        if _sha256(self.activation_bytes) != self.activation_sha256:
            raise ValueError("activation SHA does not match bytes")
        bindings = {name: getattr(self, name) for name in _SUCCESS_FIELDS}
        if self.activation_bytes != _canonical_bytes(self.status, bindings):
            raise ValueError("activation bytes do not match status or bindings")
        if self.status == _NOT_ACTIVATED:
            if any(value is not None for value in bindings.values()):
                raise ValueError("denied activation must not expose bindings")
            return
        if (
            any(type(bindings[name]) is not str for name in _SUCCESS_FIELDS if name != "state_version")
            or type(self.state_version) is not int
            or self.state_version != 0
            or _RUN_ID_RE.fullmatch(self.source_run_id or "") is None
            or _RUN_ID_RE.fullmatch(self.successor_run_id or "") is None
            or not _is_uuid(self.allocation_token)
            or not _is_uuid(self.lock_token)
            or any(
                _SHA256_RE.fullmatch(getattr(self, name) or "") is None
                for name in (
                    "intent_sha256",
                    "source_admission_sha256",
                    "plan_fingerprint",
                    "input_descriptor_sha256",
                    "state_sha256",
                    "journal_anchor_sha256",
                    "lock_sha256",
                )
            )
        ):
            raise ValueError("activated result bindings are invalid")

    @property
    def resume_activated(self) -> bool:
        return self.status == _ACTIVATED

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in ("status", "activation_bytes", "activation_sha256", *_SUCCESS_FIELDS)}


class ExplicitResumeActivation:
    """Persist and complete the non-executing successor-Run activation transaction."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).absolute()

    def activate(
        self,
        *,
        run_id: str,
        admission: ExplicitResumeAdmissionResult,
    ) -> ExplicitResumeActivationResult:
        try:
            root = a1_artifacts._controlled_temporary_root(self.project_root)
            fresh = self._fresh_admission(root, run_id=run_id)
            if not self._same_admission(run_id=run_id, supplied=admission, fresh=fresh):
                return _not_activated()
            source_state = self._source_state(root, run_id=run_id, admission=fresh)
            intent, intent_bytes = self._load_or_persist_intent(
                root, source_state=source_state, admission=fresh
            )
            # Intent is the durable boundary.  Re-observe afterward so a source
            # mutation in the pre-intent window never acquires the Active Run Lock.
            current = self._fresh_admission(root, run_id=run_id)
            if not self._same_admission(run_id=run_id, supplied=fresh, fresh=current):
                return _not_activated()
            source_state = self._source_state(root, run_id=run_id, admission=current)
            self._complete_activation(root, intent=intent, source_state=source_state)
            result = self._activated_result(
                root, intent=intent, intent_bytes=intent_bytes, source_state=source_state
            )
            # The result is intentionally built before the final B.5 observation:
            # a mutation injected during any activation/result seam is therefore
            # observed before a success can escape this boundary.
            final = self._fresh_admission(root, run_id=run_id)
            if not self._same_admission(run_id=run_id, supplied=current, fresh=final):
                return _not_activated()
            source_state = self._source_state(root, run_id=run_id, admission=final)
            self._assert_result_is_current(
                root, intent=intent, intent_bytes=intent_bytes,
                source_state=source_state, result=result,
            )
            return result
        except Exception:
            return _not_activated()

    def _fresh_admission(self, root: Path, *, run_id: object) -> ExplicitResumeAdmissionResult:
        result = _B5_ADMIT(_B5_TYPE(root), run_id=run_id)  # type: ignore[arg-type]
        if type(result) is not ExplicitResumeAdmissionResult:
            raise ValueError("B.5 returned a non-exact admission")
        result.__post_init__()
        return result

    @staticmethod
    def _same_admission(
        *,
        run_id: object,
        supplied: object,
        fresh: ExplicitResumeAdmissionResult,
    ) -> bool:
        if type(run_id) is not str or _RUN_ID_RE.fullmatch(run_id) is None:
            return False
        if type(supplied) is not ExplicitResumeAdmissionResult:
            return False
        supplied.__post_init__()
        if not supplied.resume_admissible or not fresh.resume_admissible:
            return False
        return all(
            getattr(supplied, name) == getattr(fresh, name)
            for name in (
                "run_id",
                "admission_bytes",
                "admission_sha256",
                "decision_bytes",
                "decision_sha256",
                "inventory_sha256",
                "state_version",
                "plan_fingerprint",
                "input_descriptor_sha256",
                "observations_sha256",
            )
        ) and fresh.run_id == run_id

    @staticmethod
    def _source_state(
        root: Path,
        *,
        run_id: str,
        admission: ExplicitResumeAdmissionResult,
    ) -> Mapping[str, Any]:
        state = StateStore(root).load(run_id=run_id)["canonical_state"]
        context = state.get("context")
        if (
            state.get("status") != "COMPLETED"
            or state.get("state_version") != admission.state_version
            or state.get("plan_fingerprint") != admission.plan_fingerprint
            or not isinstance(context, Mapping)
            or context.get("resolved_input_descriptor_sha256") != admission.input_descriptor_sha256
            or not isinstance(state.get("task_plan"), (list, tuple))
        ):
            raise StateStoreError("source State does not match B.5 admission")
        return state

    @staticmethod
    def _intent_directory(root: Path, source_run_id: str) -> Path:
        runs = root / "runs"
        source = runs / source_run_id
        if not _is_plain_directory(runs) or not _is_plain_directory(source):
            raise ValueError("activation audit path is not controlled")
        return source / "resume_activation"

    @staticmethod
    def _directory_identity(path: Path, *, label: str) -> tuple[int, int]:
        if not _is_plain_directory(path):
            raise ValueError(f"{label} is not a plain controlled directory")
        try:
            entry = path.lstat()
        except OSError as exc:
            raise ValueError(f"unable to inspect {label}") from exc
        return (entry.st_dev, entry.st_ino)

    @classmethod
    def _directory_chain(cls, root: Path, directory: Path, *, label: str) -> tuple[tuple[Path, int, int], ...]:
        try:
            relative = directory.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"{label} escapes controlled root") from exc
        current = root
        result: list[tuple[Path, int, int]] = []
        for index, part in enumerate((".", *relative.parts)):
            if index:
                if part in {"", ".", ".."} or "\\" in part or ":" in part:
                    raise ValueError(f"{label} has unsafe path component")
                current = current / part
            device, inode = cls._directory_identity(
                current, label=f"{label} directory component"
            )
            result.append((current, device, inode))
        return tuple(result)

    @classmethod
    def _assert_directory_chain(
        cls, chain: tuple[tuple[Path, int, int], ...], *, label: str
    ) -> None:
        for path, device, inode in chain:
            current = cls._directory_identity(path, label=label)
            if current != (device, inode):
                raise ValueError(f"{label} directory identity changed")

    @classmethod
    def _ensure_controlled_directory(cls, root: Path, directory: Path, *, label: str) -> tuple[tuple[Path, int, int], ...]:
        parent_chain = cls._directory_chain(root, directory.parent, label=label)
        try:
            directory.lstat()
            exists = True
        except FileNotFoundError:
            exists = False
        except OSError as exc:
            raise ValueError(f"unable to inspect {label}") from exc
        if not exists:
            cls._assert_directory_chain(parent_chain, label=label)
            try:
                directory.mkdir(mode=0o700)
                _sync_directory(directory.parent, label=f"{label} parent")
            except OSError as exc:
                raise ValueError(f"unable to create {label}") from exc
            cls._assert_directory_chain(parent_chain, label=label)
        if not _is_plain_directory(directory):
            raise ValueError(f"{label} is unsafe")
        return cls._directory_chain(root, directory, label=label)

    @staticmethod
    def _successor_run_id(admission: ExplicitResumeAdmissionResult) -> str:
        # The successor id is derived from B.5's immutable proof, avoiding a
        # caller-selected id and cross-source allocation races.
        return f"run_{int(admission.admission_sha256[:16], 16):020d}"

    def _intent_path(self, root: Path, admission: ExplicitResumeAdmissionResult) -> Path:
        return self._intent_path_for(root, admission.run_id or "", admission.admission_sha256)

    @staticmethod
    def _intent_path_for(root: Path, source_run_id: str, admission_sha256: str) -> Path:
        return ExplicitResumeActivation._intent_directory(root, source_run_id) / f"{admission_sha256}.intent.json"

    @staticmethod
    def _intent_document(
        *, source_state: Mapping[str, Any], admission: ExplicitResumeAdmissionResult,
        allocation_token: str, lock_token: str,
    ) -> dict[str, Any]:
        document: dict[str, Any] = {
            "schema_version": _INTENT_SCHEMA,
            "source_run_id": admission.run_id,
            "successor_run_id": ExplicitResumeActivation._successor_run_id(admission),
            "source_admission_sha256": admission.admission_sha256,
            "source_state_version": admission.state_version,
            "plan_fingerprint": admission.plan_fingerprint,
            "input_descriptor_sha256": admission.input_descriptor_sha256,
            "allocation_token": allocation_token,
            "lock_token": lock_token,
            "intent_checksum": None,
        }
        document["intent_checksum"] = _sha256(
            _canonical_json_bytes({name: value for name, value in document.items() if name != "intent_checksum"})
        )
        return document

    @staticmethod
    def _validate_intent(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping) or set(value) != _INTENT_FIELDS:
            raise ValueError("activation intent fields are invalid")
        intent = dict(value)
        if (
            intent["schema_version"] != _INTENT_SCHEMA
            or _RUN_ID_RE.fullmatch(intent["source_run_id"]) is None
            or _RUN_ID_RE.fullmatch(intent["successor_run_id"]) is None
            or not _is_uuid(intent["allocation_token"])
            or not _is_uuid(intent["lock_token"])
            or type(intent["source_state_version"]) is not int
            or intent["source_state_version"] < 0
            or any(
                type(intent[name]) is not str or _SHA256_RE.fullmatch(intent[name]) is None
                for name in (
                    "source_admission_sha256", "plan_fingerprint",
                    "input_descriptor_sha256", "intent_checksum",
                )
            )
        ):
            raise ValueError("activation intent values are invalid")
        expected = _sha256(_canonical_json_bytes({name: value for name, value in intent.items() if name != "intent_checksum"}))
        if intent["intent_checksum"] != expected:
            raise ValueError("activation intent checksum is invalid")
        return intent

    @classmethod
    def _write_exclusive(
        cls, root: Path, path: Path, data: bytes, *, directory_chain: tuple[tuple[Path, int, int], ...]
    ) -> None:
        descriptor: int | None = None
        try:
            cls._assert_directory_chain(directory_chain, label="activation intent")
            descriptor = os.open(
                path,
                os.O_CREAT
                | os.O_EXCL
                | os.O_WRONLY
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short activation intent write")
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            _sync_directory(path.parent, label="activation intent parent")
            cls._assert_directory_chain(directory_chain, label="activation intent")
            if not _is_plain_regular(path):
                raise OSError("activation intent changed after persistence")
            relative = path.relative_to(root).as_posix()
            persisted, _ = _B1_READ_GUARDED(root, relative)
            if persisted != data:
                raise OSError("activation intent bytes changed after persistence")
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _load_or_persist_intent(
        self, root: Path, *, source_state: Mapping[str, Any], admission: ExplicitResumeAdmissionResult
    ) -> tuple[dict[str, Any], bytes]:
        directory = self._intent_directory(root, admission.run_id or "")
        directory_chain = self._ensure_controlled_directory(
            root, directory, label="activation intent directory"
        )
        path = self._intent_path(root, admission)
        entries = list(directory.iterdir())
        if any(not _is_plain_regular(entry) for entry in entries) or any(entry != path for entry in entries):
            raise ValueError("activation intent directory is not unique")
        self._assert_directory_chain(directory_chain, label="activation intent directory")
        try:
            path.lstat()
            exists = True
        except FileNotFoundError:
            exists = False
        if not exists:
            intent = self._intent_document(
                source_state=source_state,
                admission=admission,
                allocation_token=str(uuid.uuid4()),
                lock_token=str(uuid.uuid4()),
            )
            self._write_exclusive(
                root, path, _canonical_json_bytes(intent), directory_chain=directory_chain
            )
        self._assert_directory_chain(directory_chain, label="activation intent directory")
        if not _is_plain_regular(path):
            raise ValueError("activation intent is unsafe")
        data, _ = _B1_READ_GUARDED(
            root, path.relative_to(root).as_posix()
        )
        try:
            intent = self._validate_intent(json.loads(data.decode("utf-8")))
        except (UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            raise ValueError("activation intent is invalid") from exc
        if _canonical_json_bytes(intent) != data:
            raise ValueError("activation intent is not canonical")
        expected = self._intent_document(
            source_state=source_state,
            admission=admission,
            allocation_token=intent["allocation_token"],
            lock_token=intent["lock_token"],
        )
        if intent != expected:
            raise ValueError("activation intent does not bind current source")
        return intent, data

    @staticmethod
    def _existing_lock(root: Path) -> Mapping[str, Any] | None:
        path = root / "runs" / ".active_run.lock"
        try:
            path.lstat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ActiveRunLockError("unable to inspect Active Run Lock") from exc
        return read_existing_active_run_lock(root)

    def _complete_activation(
        self, root: Path, *, intent: Mapping[str, Any], source_state: Mapping[str, Any]
    ) -> None:
        lock = self._existing_lock(root)
        if lock is None:
            _ACQUIRE_ACTIVE_RUN_LOCK(
                root,
                task_id="explicit_resume_activation",
                allocation_token=intent["allocation_token"],
                lock_token=intent["lock_token"],
            )
            lock = self._existing_lock(root)
        if (
            not isinstance(lock, Mapping)
            or lock.get("allocation_token") != intent["allocation_token"]
            or lock.get("lock_token") != intent["lock_token"]
            or lock.get("task_id") != "explicit_resume_activation"
            or lock.get("reserved_run_id") not in {None, intent["successor_run_id"]}
            or lock.get("run_id") not in {None, intent["successor_run_id"]}
            or lock.get("phase") not in {"allocating", "running"}
        ):
            raise ActiveRunLockError("Active Run Lock conflicts with activation intent")
        successor_dir = root / "runs" / intent["successor_run_id"]
        if lock["phase"] == "allocating":
            if lock["reserved_run_id"] is None:
                _RESERVE_ACTIVE_RUN_ID(
                    root,
                    run_id=intent["successor_run_id"],
                    expected_allocation_token=intent["allocation_token"],
                    expected_lock_token=intent["lock_token"],
                )
            self._ensure_controlled_directory(
                root, successor_dir, label="successor Run directory"
            )
            store = StateStore(root)
            state = self._load_or_repair_successor_state(
                store, intent=intent, source_state=source_state
            )
            if state is None:
                store.initialize_run(
                    run_id=intent["successor_run_id"],
                    allocation_token=intent["allocation_token"],
                    plan_fingerprint=intent["plan_fingerprint"],
                    task_plan=_thaw_json(source_state["task_plan"]),
                    expected_lock_token=intent["lock_token"],
                    initial_context=self._resume_context(intent),
                )
            self._validate_successor_state(root, intent=intent, source_state=source_state)
            _MARK_ACTIVE_RUN_RUNNING(
                root,
                run_id=intent["successor_run_id"],
                expected_allocation_token=intent["allocation_token"],
                expected_lock_token=intent["lock_token"],
            )
        self._validate_successor_state(root, intent=intent, source_state=source_state)
        final = self._existing_lock(root)
        if (
            not isinstance(final, Mapping)
            or final.get("phase") != "running"
            or final.get("run_id") != intent["successor_run_id"]
            or final.get("reserved_run_id") != intent["successor_run_id"]
            or final.get("allocation_token") != intent["allocation_token"]
            or final.get("lock_token") != intent["lock_token"]
        ):
            raise ActiveRunLockError("activation did not reach a bound running lock")

    @staticmethod
    def _resume_context(intent: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "resume_activation": {
                "source_run_id": intent["source_run_id"],
                "source_admission_sha256": intent["source_admission_sha256"],
                "source_state_version": intent["source_state_version"],
                "plan_fingerprint": intent["plan_fingerprint"],
                "input_descriptor_sha256": intent["input_descriptor_sha256"],
                "intent_sha256": _sha256(_canonical_json_bytes(intent)),
            }
        }

    @classmethod
    def _load_or_repair_successor_state(
        cls, store: StateStore, *, intent: Mapping[str, Any], source_state: Mapping[str, Any]
    ) -> Mapping[str, Any] | None:
        """Accept only absence, a valid state, or the one exact state-only crash seam."""

        paths = store._paths(intent["successor_run_id"])
        entries: dict[str, os.stat_result | None] = {}
        for name in ("state", "anchor", "journal", "recovery", "lock_recovery", "lock"):
            try:
                entries[name] = paths[name].lstat()
            except FileNotFoundError:
                entries[name] = None
            except OSError as exc:
                raise StateStoreError("unable to inspect successor State evidence") from exc
        if any(entries[name] is not None for name in ("recovery", "lock_recovery", "lock")):
            raise StateStoreError("successor State recovery residue blocks activation")
        if entries["state"] is None:
            if any(entries[name] is not None for name in ("anchor", "journal")):
                raise StateStoreError("successor has partial State evidence")
            return None
        if not _is_plain_regular(paths["state"]):
            raise StateStoreError("successor State is unsafe")
        if entries["anchor"] is not None:
            if not _is_plain_regular(paths["anchor"]):
                raise StateStoreError("successor Journal anchor is unsafe")
            return store.load(run_id=intent["successor_run_id"])["canonical_state"]
        # StateStore.initialize_run commits State before its genesis anchor.  A
        # retry may complete only this precise, authenticated state-only seam.
        if entries["journal"] is not None:
            raise StateStoreError("successor State-only recovery evidence conflicts")
        state, _ = store._read_state(intent["successor_run_id"])
        cls._validate_successor_mapping(state, intent=intent, source_state=source_state)
        anchor = store._anchor_document(
            run_id=intent["successor_run_id"],
            allocation_token=intent["allocation_token"],
            tail_record_index=None,
            tail_record_checksum=None,
            tail_file_size_bytes=0,
        )
        store._write_anchor(intent["successor_run_id"], anchor)
        return store.load(run_id=intent["successor_run_id"])["canonical_state"]

    @classmethod
    def _validate_successor_mapping(
        cls, state: Mapping[str, Any], *, intent: Mapping[str, Any], source_state: Mapping[str, Any]
    ) -> None:
        context = state.get("context")
        expected_context = cls._resume_context(intent)
        if (
            state.get("status") != "CREATED"
            or state.get("state_version") != 0
            or state.get("allocation_token") != intent["allocation_token"]
            or state.get("plan_fingerprint") != intent["plan_fingerprint"]
            or _thaw_json(state.get("task_plan")) != _thaw_json(source_state.get("task_plan"))
            or state.get("task_status") != {}
            or state.get("task_attempts") != {}
            or tuple(state.get("completed_tasks", ())) != ()
            or tuple(state.get("failed_tasks", ())) != ()
            or state.get("last_operation_kind") is not None
            or state.get("last_operation_id") is not None
            or state.get("last_operation_payload_sha256") is not None
            or _thaw_json(context) != expected_context
        ):
            raise StateStoreError("successor State does not bind activation intent")

    @classmethod
    def _validate_successor_state(
        cls, root: Path, *, intent: Mapping[str, Any], source_state: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        state = StateStore(root).load(run_id=intent["successor_run_id"])["canonical_state"]
        cls._validate_successor_mapping(state, intent=intent, source_state=source_state)
        return state

    def _activation_evidence(
        self, root: Path, *, intent: Mapping[str, Any], intent_bytes: bytes,
        source_state: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Read every activation authority through one guarded, stable snapshot."""

        intent_path = self._intent_path_for(
            root, intent["source_run_id"], intent["source_admission_sha256"]
        )
        intent_data, _ = _B1_READ_GUARDED(root, intent_path.relative_to(root).as_posix())
        if intent_data != intent_bytes:
            raise ValueError("activation intent changed after persistence")
        try:
            parsed_intent = self._validate_intent(json.loads(intent_data.decode("utf-8")))
        except (UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            raise ValueError("activation intent is invalid at result") from exc
        if parsed_intent != dict(intent) or _canonical_json_bytes(parsed_intent) != intent_data:
            raise ValueError("activation intent does not match result binding")
        store = StateStore(root)
        state, state_bytes = store._read_state(intent["successor_run_id"])
        guarded_state, _ = _B1_READ_GUARDED(
            root, f"runs/{intent['successor_run_id']}/state.json"
        )
        if guarded_state != state_bytes:
            raise StateStoreError("successor State changed during result")
        self._validate_successor_mapping(state, intent=intent, source_state=source_state)
        _, anchor_bytes = store._read_anchor(
            intent["successor_run_id"], intent["allocation_token"]
        )
        guarded_anchor, _ = _B1_READ_GUARDED(
            root, f"runs/{intent['successor_run_id']}/state_journal_tail.json"
        )
        if guarded_anchor != anchor_bytes:
            raise StateStoreError("successor Journal anchor changed during result")
        lock = self._existing_lock(root)
        if not isinstance(lock, Mapping):
            raise ActiveRunLockError("active lock disappeared after activation")
        lock_bytes, _ = _B1_READ_GUARDED(root, "runs/.active_run.lock")
        if self._existing_lock(root) != lock:
            raise ActiveRunLockError("Active Run Lock changed during activation result")
        if (
            lock.get("phase") != "running"
            or lock.get("run_id") != intent["successor_run_id"]
            or lock.get("reserved_run_id") != intent["successor_run_id"]
            or lock.get("allocation_token") != intent["allocation_token"]
            or lock.get("lock_token") != intent["lock_token"]
            or lock.get("task_id") != "explicit_resume_activation"
        ):
            raise ActiveRunLockError("activation result lock does not bind intent")
        return {
            "intent_sha256": _sha256(intent_data),
            "state": state,
            "state_sha256": _sha256(state_bytes),
            "journal_anchor_sha256": _sha256(anchor_bytes),
            "lock_sha256": _sha256(lock_bytes),
        }

    def _activated_result(
        self, root: Path, *, intent: Mapping[str, Any], intent_bytes: bytes,
        source_state: Mapping[str, Any],
    ) -> ExplicitResumeActivationResult:
        first = self._activation_evidence(
            root, intent=intent, intent_bytes=intent_bytes, source_state=source_state
        )
        second = self._activation_evidence(
            root, intent=intent, intent_bytes=intent_bytes, source_state=source_state
        )
        if first != second:
            raise ValueError("activation evidence changed during result")
        state = second["state"]
        bindings = {
            "source_run_id": intent["source_run_id"],
            "successor_run_id": intent["successor_run_id"],
            "intent_sha256": second["intent_sha256"],
            "allocation_token": intent["allocation_token"],
            "lock_token": intent["lock_token"],
            "source_admission_sha256": intent["source_admission_sha256"],
            "state_version": state["state_version"],
            "plan_fingerprint": intent["plan_fingerprint"],
            "input_descriptor_sha256": intent["input_descriptor_sha256"],
            "state_sha256": second["state_sha256"],
            "journal_anchor_sha256": second["journal_anchor_sha256"],
            "lock_sha256": second["lock_sha256"],
        }
        data = _canonical_bytes(_ACTIVATED, bindings)
        result = object.__new__(ExplicitResumeActivationResult)
        object.__setattr__(result, "status", _ACTIVATED)
        object.__setattr__(result, "activation_bytes", data)
        object.__setattr__(result, "activation_sha256", _sha256(data))
        for name, value in bindings.items():
            object.__setattr__(result, name, value)
        result.__post_init__()
        return result

    def _assert_result_is_current(
        self, root: Path, *, intent: Mapping[str, Any], intent_bytes: bytes,
        source_state: Mapping[str, Any], result: ExplicitResumeActivationResult,
    ) -> None:
        evidence = self._activation_evidence(
            root, intent=intent, intent_bytes=intent_bytes, source_state=source_state
        )
        if (
            result.intent_sha256 != evidence["intent_sha256"]
            or result.state_sha256 != evidence["state_sha256"]
            or result.journal_anchor_sha256 != evidence["journal_anchor_sha256"]
            or result.lock_sha256 != evidence["lock_sha256"]
        ):
            raise ValueError("activation result no longer matches authority evidence")


__all__ = ["ExplicitResumeActivation", "ExplicitResumeActivationResult"]
