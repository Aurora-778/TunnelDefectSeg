"""Read-only, Run-local artifact integrity and freshness resolution.

This module is deliberately a projection layer.  It does not create or repair
State, Journal, locks, recovery markers, transactions, staging directories, or
formal outputs.  The StateStore, A1 artifact contract, and A2 publication
validator remain the authorities for their respective documents.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import stat
from typing import Any
from types import MappingProxyType

from orchestrator.dag.builder import build_dag
from orchestrator.inspection_workflow import a1_artifacts, publication
from orchestrator.inspection_workflow.contracts import (
    load_workflow_policy,
    validate_task_request,
)
from orchestrator.inspection_workflow.locking import (
    ACTIVE_RUN_RELEASE_PREFIX,
    ACTIVE_RUN_PHASES,
    _assert_plain_entry,
    _assert_project_path,
    _read_lock,
)
from orchestrator.inspection_workflow.planning import (
    build_required_task_plan,
    task_plan_fingerprint,
)


INVENTORY_SCHEMA_VERSION = "inspection_artifact_inventory_v1"
RESOLVED_INPUT_SCHEMA_VERSION = "phase_a3_3_2_resolved_input_v1"
PHASE_A_EXECUTION_PROFILE = "phase_a_agent_sandbox"
RUN_ID_RE = re.compile(r"run_[0-9]{3,}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
CHUNK_SIZE = 1024 * 1024

_A1_MARKER = ".a1_recovery_required.json"
_A2_MARKER = ".publication_recovery_required.json"
_STATE_MARKERS = (
    ".state_initialization_recovery_required.json",
    ".state_lock_recovery_required.json",
)
_A2_TX_PHASES = {
    "backup_ready",
    "publishing",
    "files_replaced",
    "final_summary_ready",
    "manifest_commit_intent",
    "manifest_committed",
    "cleanup_pending",
    "cleanup_complete",
}
_STABLE_TX_PHASE = "cleanup_complete"
_KNOWN_DECLARED_PATH_KEYS = {
    "path",
    "artifact_path",
    "claim_decision_path",
    "comparison_evidence_path",
    "comparison_evidence_manifest_path",
    "projection_receipt_path",
    "association_manifest_path",
    "association_records_path",
    "report_path",
    "report_paths",
    "manifest_path",
    "visualization_report_path",
    "visualization_summary_path",
    "final_report_path",
}


class ArtifactResolverInputError(ValueError):
    """Raised when the public resolver boundary receives unsafe input."""


class ArtifactResolutionError(RuntimeError):
    """Raised only for an internal resolver contract failure."""


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        value = _jsonable(value)
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ArtifactResolutionError("resolver result is not canonically encodable") from exc


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_reparse(entry: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
    return bool(getattr(entry, "st_file_attributes", 0) & flag)


def _lstat(path: Path, *, label: str) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ArtifactResolutionError(f"unable to inspect {label}") from exc


def _assert_plain(path: Path, *, label: str, directory: bool | None = None) -> os.stat_result:
    entry = _lstat(path, label=label)
    if entry is None:
        raise ArtifactResolutionError(f"{label} is missing")
    if stat.S_ISLNK(entry.st_mode) or _is_reparse(entry):
        raise ArtifactResolutionError(f"{label} must not be a symlink or reparse point")
    if directory is True and not stat.S_ISDIR(entry.st_mode):
        raise ArtifactResolutionError(f"{label} must be a directory")
    if directory is False and not stat.S_ISREG(entry.st_mode):
        raise ArtifactResolutionError(f"{label} must be a regular file")
    if directory is None and not (stat.S_ISDIR(entry.st_mode) or stat.S_ISREG(entry.st_mode)):
        raise ArtifactResolutionError(f"{label} must be a directory or regular file")
    return entry


def _safe_run_path(root: Path, run_id: str, relative: Any, *, require_run: bool = True) -> str:
    """Validate and normalize a POSIX path without resolving a link."""

    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ArtifactResolutionError("artifact path must be a relative POSIX path")
    if relative.startswith("/") or relative.startswith("//"):
        raise ArtifactResolutionError("artifact path must not be absolute")
    parts = relative.split("/")
    if any(not item or item in {".", ".."} for item in parts):
        raise ArtifactResolutionError("artifact path contains an unsafe component")
    expected = ("runs", run_id)
    if tuple(parts[:2]) != expected:
        raise ArtifactResolutionError("artifact path must be inside the requested Run")
    if len(parts) < 3 and require_run:
        raise ArtifactResolutionError("artifact path must name a Run-local artifact")
    candidate = root.joinpath(*parts)
    try:
        _assert_project_path(root, candidate, include_leaf=True, label="Run-local artifact")
    except Exception as exc:
        raise ArtifactResolutionError("artifact path is outside the controlled project root") from exc
    return "/".join(parts)


def _path_for(root: Path, relative: str) -> Path:
    return root.joinpath(*relative.split("/"))


def _snapshot_file(root: Path, relative: str) -> dict[str, Any]:
    """Read one guarded regular file once and hash that same byte snapshot."""

    normalized = relative
    path = _path_for(root, normalized)
    _safe_run_path(root, normalized.split("/", 2)[1], normalized)
    before = _assert_plain(path, label=f"artifact {normalized}", directory=False)
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
    except (OSError, ValueError) as exc:
        raise ArtifactResolutionError(f"unable to read artifact {normalized}") from exc
    after = _assert_plain(path, label=f"artifact {normalized}", directory=False)
    if before.st_ino != after.st_ino or before.st_dev != after.st_dev:
        raise ArtifactResolutionError(f"artifact {normalized} changed during snapshot")
    return {
        "path": normalized,
        "size_bytes": size,
        "sha256": digest.hexdigest(),
    }


def _current_plan_fingerprint(descriptor: Mapping[str, Any]) -> str:
    """Rebuild the fixed lifecycle plan from repository authorities."""

    from orchestrator.inspection_workflow import lifecycle

    profile = descriptor.get("execution_profile")
    if profile != PHASE_A_EXECUTION_PROFILE:
        raise ArtifactResolutionError("resolved descriptor execution profile is invalid")
    tasks, _ = build_dag(
        lifecycle._DAG_CONFIG_PATH,
        profile=lifecycle._PHASE_A_EXECUTION_PROFILE,
    )
    return task_plan_fingerprint(
        tasks,
        resolved_input_descriptor_sha256=descriptor.get("descriptor_sha256"),
        execution_profile=profile,
    )


def _descriptor_sha(descriptor: Mapping[str, Any]) -> str:
    return _sha256(_canonical_json_bytes(dict(descriptor)))


def _status_priority(
    recovery: set[str], invalid: set[str], stale: set[str], incomplete: set[str]
) -> str:
    if recovery:
        return "recovery_required"
    if invalid:
        return "invalid"
    if stale:
        return "stale"
    if incomplete:
        return "incomplete"
    return "complete"


def _freeze_item(item: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({key: item[key] for key in sorted(item)})


@dataclass(frozen=True)
class ArtifactResolution:
    """Deterministic result returned by :class:`ArtifactResolver`."""

    run_id: str
    status: str
    inventory: tuple[Mapping[str, Any], ...]
    inventory_bytes: bytes
    inventory_sha256: str
    state_version: int | None
    plan_fingerprint: str | None
    input_descriptor_sha256: str | None
    issue_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status not in {
            "complete",
            "incomplete",
            "stale",
            "recovery_required",
            "invalid",
        }:
            raise ValueError("unknown ArtifactResolution status")
        if _sha256(self.inventory_bytes) != self.inventory_sha256:
            raise ValueError("inventory SHA-256 does not match inventory bytes")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "inventory": [dict(item) for item in self.inventory],
            "inventory_bytes": self.inventory_bytes,
            "inventory_sha256": self.inventory_sha256,
            "state_version": self.state_version,
            "plan_fingerprint": self.plan_fingerprint,
            "input_descriptor_sha256": self.input_descriptor_sha256,
            "issue_codes": list(self.issue_codes),
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


def _empty_inventory(run_id: str) -> tuple[bytes, str]:
    data = _canonical_json_bytes(
        {"schema_version": INVENTORY_SCHEMA_VERSION, "run_id": run_id, "artifacts": []}
    )
    return data, _sha256(data)


class _ResolverPass:
    def __init__(self, root: Path, run_id: str) -> None:
        self.root = root
        self.run_id = run_id
        self.recovery: set[str] = set()
        self.invalid: set[str] = set()
        self.stale: set[str] = set()
        self.incomplete: set[str] = set()
        self.state: Mapping[str, Any] | None = None
        self.descriptor: Mapping[str, Any] | None = None
        self.descriptor_sha256: str | None = None
        self.descriptor_bindings: dict[str, tuple[int, str]] = {}
        self.plan_fingerprint: str | None = None
        self.task_operations: dict[str, dict[str, Any]] = {}
        self.declared_paths: dict[str, tuple[str, str, int]] = {}
        self.a1_result: Mapping[str, Any] | None = None
        self.a2_result: Mapping[str, Any] | None = None
        self.a2_manifest: Mapping[str, Any] | None = None
        self.transaction: Mapping[str, Any] | None = None
        self.active_lock: Mapping[str, Any] | None = None
        self.inventory_candidates: dict[str, dict[str, Any]] = {}

    def add(self, bucket: set[str], code: str) -> None:
        bucket.add(code)

    def result(self) -> ArtifactResolution:
        inventory = tuple(
            _freeze_item(self.inventory_candidates[path])
            for path in sorted(self.inventory_candidates)
        )
        data = _canonical_json_bytes(
            {
                "schema_version": INVENTORY_SCHEMA_VERSION,
                "run_id": self.run_id,
                "artifacts": [dict(item) for item in inventory],
            }
        )
        status = _status_priority(self.recovery, self.invalid, self.stale, self.incomplete)
        return ArtifactResolution(
            run_id=self.run_id,
            status=status,
            inventory=inventory,
            inventory_bytes=data,
            inventory_sha256=_sha256(data),
            state_version=(self.state.get("state_version") if self.state else None),
            plan_fingerprint=(self.state.get("plan_fingerprint") if self.state else self.plan_fingerprint),
            input_descriptor_sha256=self.descriptor_sha256,
            issue_codes=tuple(sorted(self.recovery | self.invalid | self.stale | self.incomplete)),
        )

    def _known(self, relative: str) -> bool:
        try:
            path = _path_for(self.root, relative)
            return _lstat(path, label=relative) is not None
        except ArtifactResolutionError:
            return True

    def preflight(self) -> bool:
        runs = self.root / "runs"
        try:
            _assert_plain(runs, label="runs directory", directory=True)
            run_dir = _path_for(self.root, f"runs/{self.run_id}")
            _assert_plain(run_dir, label="Run directory", directory=True)
        except ArtifactResolutionError:
            self.add(self.invalid, "run_missing_or_unsafe")
            return False

        marker_paths = [
            *(f"runs/{self.run_id}/{area}/{_A1_MARKER}" for area in ("work", "artifacts", "staging")),
            f"runs/{self.run_id}/{_A2_MARKER}",
            *(f"runs/{self.run_id}/{name}" for name in _STATE_MARKERS),
            "runs/.active_run.recovery.lock",
            f"runs/{self.run_id}/.state.lock",
        ]
        for relative in marker_paths:
            try:
                if _lstat(_path_for(self.root, relative), label=relative) is not None:
                    self.add(
                        self.recovery,
                        "state_lock_recovery" if relative.endswith("/.state.lock") else "recovery_marker",
                    )
            except ArtifactResolutionError:
                self.add(self.recovery, "recovery_marker")
        try:
            for entry in runs.iterdir():
                if entry.name.startswith(ACTIVE_RUN_RELEASE_PREFIX):
                    self.add(self.recovery, "release_tombstone")
                if entry.name == ".active_run.lock":
                    if stat.S_ISLNK(entry.lstat().st_mode) or _is_reparse(entry.lstat()):
                        self.add(self.recovery, "active_lock_recovery")
                    else:
                        try:
                            lock, _ = _read_lock(entry)
                            self.active_lock = lock
                            if lock.get("phase") == "recovering":
                                self.add(self.recovery, "active_lock_recovery")
                            elif lock.get("phase") not in ACTIVE_RUN_PHASES:
                                self.add(self.invalid, "active_lock_invalid")
                            elif lock.get("reserved_run_id") not in {None, self.run_id}:
                                self.add(self.invalid, "active_lock_run_mismatch")
                        except Exception:
                            self.add(self.invalid, "active_lock_invalid")
        except (OSError, ValueError):
            self.add(self.recovery, "active_lock_recovery")

        tx_relative = publication.PUBLICATION_TRANSACTION_PATH_TEMPLATE.format(run_id=self.run_id)
        tx_path = _path_for(self.root, tx_relative)
        manifest_path = _path_for(self.root, publication.PUBLICATION_MANIFEST_PATH)
        tx_exists = _lstat(tx_path, label="publication transaction") is not None
        manifest_exists = _lstat(manifest_path, label="publication Manifest") is not None
        try:
            publication._reject_workspace_residue(self.root, self.run_id)
        except Exception:
            self.add(self.recovery, "publication_cleanup_residue")
        if tx_exists:
            try:
                self.transaction = publication._load_transaction(self.root, tx_relative)
                if self.transaction.get("phase") != _STABLE_TX_PHASE:
                    self.add(self.recovery, "transaction_recovery")
            except Exception:
                # A transaction can be in a durable intermediate phase even
                # when an external observer rewrote its JSON non-canonically.
                # Preserve the recovery barrier before reporting its schema
                # failure; never treat such a phase as a usable publication.
                try:
                    raw = tx_path.read_bytes()
                    peek = json.loads(raw.decode("utf-8"))
                except Exception:
                    peek = None
                if isinstance(peek, Mapping) and peek.get("phase") in _A2_TX_PHASES and peek.get("phase") != _STABLE_TX_PHASE:
                    self.add(self.recovery, "transaction_recovery")
                else:
                    self.add(self.invalid, "transaction_invalid")
        elif manifest_exists:
            self.add(self.invalid, "publication_transaction_missing")
        if self.recovery:
            return False
        return True

    def load_state(self) -> bool:
        try:
            from orchestrator.state.store import (
                StateRecoveryDeferredError,
                StateRecoveryRequiredError,
                StateStore,
                StateStoreError,
            )

            store = StateStore(self.root)
            snapshot = store.load(run_id=self.run_id)
            state = snapshot.get("canonical_state")
            if not isinstance(state, Mapping):
                raise StateStoreError("StateStore snapshot lacks canonical_state")
            state = _jsonable(dict(state))
            if not isinstance(state, dict):
                raise StateStoreError("StateStore canonical_state is not JSON-compatible")
            self.state = state
            records, _, _ = store._journal_state(  # type: ignore[attr-defined]
                self.run_id,
                state["allocation_token"],
                repair_unanchored=False,
            )
            store._validate_recovery_audit_bindings(state, records)  # type: ignore[attr-defined]
            store._validate_committed_state_binding(state, records)  # type: ignore[attr-defined]
        except (StateRecoveryRequiredError, StateRecoveryDeferredError):
            self.add(self.recovery, "state_recovery")
            return False
        except (StateStoreError, OSError, ValueError, KeyError, TypeError):
            self.add(self.invalid, "state_invalid")
            return False
        self._read_checkpoint_provenance(records)
        self._validate_lock_state_binding()
        return True

    def _validate_lock_state_binding(self) -> None:
        if self.state is None or self.active_lock is None:
            return
        phase = self.active_lock.get("phase")
        if phase == "allocating" and self.active_lock.get("reserved_run_id") == self.run_id:
            self.add(self.recovery, "active_lock_recovery")
        if phase == "running" and self.state.get("status") != "RUNNING":
            self.add(self.recovery, "active_lock_state_conflict")

    def _read_checkpoint_provenance(self, records: Sequence[Mapping[str, Any]]) -> None:
        context_events = self.state.get("context", {}).get("phase_a3_checkpoint_events", {}) if self.state else {}
        for record in records:
            if record.get("phase") != "committed" or record.get("mutation_kind") != "context_checkpoint":
                continue
            if str(record.get("operation_id", "")).endswith(":checkpoint:run_initialized"):
                continue
            payload = record.get("payload")
            if not isinstance(payload, Mapping):
                payload = context_events.get(record.get("operation_id")) if isinstance(context_events, Mapping) else None
            if not isinstance(payload, Mapping):
                self.add(self.invalid, "checkpoint_provenance_missing")
                continue
            kind = payload.get("checkpoint_kind")
            if kind == "task_cache_hit":
                self.add(self.invalid, "cache_checkpoint_not_reusable")
                continue
            if kind != "task_succeeded":
                continue
            task_id = payload.get("task_id")
            if not isinstance(task_id, str):
                self.add(self.invalid, "checkpoint_task_invalid")
                continue
            task_output = payload.get("controlled_context_delta", {}).get("task_output")
            if not isinstance(task_output, Mapping):
                self.add(self.invalid, "checkpoint_output_missing")
                continue
            result = task_output.get("result")
            if not isinstance(result, Mapping):
                self.add(self.invalid, "checkpoint_result_invalid")
                continue
            operation_id = record.get("operation_id")
            state_version = record.get("resulting_state_version")
            attempt = payload.get("attempt_number")
            expected_operation_id = (
                f"run:{self.run_id}:task:{task_id}:attempt:{attempt}:succeeded"
                if type(attempt) is int and attempt >= 1
                else None
            )
            if (
                not isinstance(operation_id, str)
                or operation_id != expected_operation_id
                or type(state_version) is not int
            ):
                self.add(self.invalid, "checkpoint_provenance_invalid")
                continue
            operation = {
                "task_id": task_id,
                "operation_id": operation_id,
                "state_version": state_version,
                "paths": [],
            }
            try:
                operation["paths"] = self._extract_paths(result)
            except ArtifactResolutionError:
                self.add(self.invalid, "checkpoint_path_invalid")
                continue
            if task_id in self.task_operations:
                self.add(self.invalid, "duplicate_task_success")
            self.task_operations[task_id] = operation
            for path in operation["paths"]:
                previous = self.declared_paths.get(path)
                if previous is not None and previous[0] != task_id:
                    self.add(self.invalid, "duplicate_producer_path")
                self.declared_paths[path] = (task_id, operation_id, state_version)

    def _extract_paths(self, result: Mapping[str, Any]) -> list[str]:
        found: list[str] = []

        def visit(value: Any) -> None:
            if isinstance(value, Mapping):
                for key, nested in value.items():
                    if not isinstance(key, str):
                        raise ArtifactResolutionError("checkpoint result key is invalid")
                    if key in _KNOWN_DECLARED_PATH_KEYS:
                        values = nested if key.endswith("_paths") else [nested]
                        if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
                            raise ArtifactResolutionError("checkpoint path list is invalid")
                        for candidate in values:
                            if not isinstance(candidate, str):
                                raise ArtifactResolutionError("checkpoint path is not a string")
                            normalized = self._normalize_declared_path(candidate)
                            if normalized not in found:
                                found.append(normalized)
                    elif key == "path" or key.endswith("_path") or key.endswith("_paths"):
                        raise ArtifactResolutionError("checkpoint contains an unsupported path field")
                    else:
                        visit(nested)
            elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                for nested in value:
                    visit(nested)

        visit(result)
        return sorted(found)

    def _normalize_declared_path(self, value: str) -> str:
        if "\\" in value or ":" in value or value.startswith(("/", "//")):
            # The existing association producer records absolute paths.  Accept
            # those only when they are the exact current controlled Run path.
            candidate = Path(value)
            try:
                _assert_project_path(self.root, candidate, include_leaf=True, label="checkpoint path")
                value = candidate.absolute().relative_to(self.root.absolute()).as_posix()
            except Exception as exc:
                raise ArtifactResolutionError("checkpoint path is outside the sandbox") from exc
        return _safe_run_path(self.root, self.run_id, value)

    def validate_descriptor(self) -> bool:
        if self.state is None:
            return False
        context = self.state.get("context")
        if not isinstance(context, Mapping):
            self.add(self.invalid, "descriptor_missing")
            return False
        descriptor = context.get("resolved_input_descriptor")
        descriptor_sha = context.get("resolved_input_descriptor_sha256")
        if not isinstance(descriptor, Mapping) or not isinstance(descriptor_sha, str) or not SHA256_RE.fullmatch(descriptor_sha):
            self.add(self.invalid, "descriptor_missing")
            return False
        descriptor = _jsonable(dict(descriptor))
        if not isinstance(descriptor, dict):
            self.add(self.invalid, "descriptor_schema_invalid")
            return False
        self.descriptor = descriptor
        self.descriptor_sha256 = descriptor_sha
        if _descriptor_sha(descriptor) != descriptor_sha:
            self.add(self.invalid, "descriptor_hash_invalid")
        expected_fields = {
            "schema_version", "run_id", "input_mode", "workflow_task_id",
            "execution_profile", "requested_outputs", "workflow_policy_sha256",
            "task_request", "source_artifacts", "origin_artifacts",
        }
        if set(descriptor) != expected_fields:
            self.add(self.invalid, "descriptor_schema_invalid")
            return False
        if descriptor.get("schema_version") != RESOLVED_INPUT_SCHEMA_VERSION or descriptor.get("run_id") != self.run_id:
            self.add(self.invalid, "descriptor_identity_invalid")
        if descriptor.get("execution_profile") != PHASE_A_EXECUTION_PROFILE:
            self.add(self.invalid, "descriptor_profile_invalid")
        if not isinstance(descriptor.get("workflow_policy_sha256"), str) or not SHA256_RE.fullmatch(descriptor["workflow_policy_sha256"]):
            self.add(self.invalid, "descriptor_policy_hash_invalid")
        policy_stale = False
        try:
            policy = load_workflow_policy()
            if descriptor.get("workflow_policy_sha256") != _sha256(_canonical_json_bytes(policy)):
                self.add(self.stale, "workflow_policy_stale")
                policy_stale = True
        except Exception:
            self.add(self.invalid, "workflow_policy_invalid")
        task_request = descriptor.get("task_request")
        if task_request is not None and not policy_stale:
            try:
                validate_task_request(task_request, workflow_policy=policy)
            except Exception:
                self.add(self.invalid, "descriptor_task_request_invalid")
        source = descriptor.get("source_artifacts")
        origin = descriptor.get("origin_artifacts")
        if not isinstance(source, list) or not source or not isinstance(origin, list):
            self.add(self.invalid, "descriptor_sources_invalid")
            return False
        if len(source) != len(origin):
            self.add(self.invalid, "descriptor_origin_invalid")
        seen: set[str] = set()
        for item in source:
            if not isinstance(item, Mapping) or set(item) != {"path", "size_bytes", "sha256"}:
                self.add(self.invalid, "descriptor_source_schema_invalid")
                continue
            try:
                path = _safe_run_path(self.root, self.run_id, item["path"])
            except ArtifactResolutionError:
                self.add(self.invalid, "descriptor_source_path_invalid")
                continue
            if path in seen:
                self.add(self.invalid, "descriptor_source_duplicate")
            seen.add(path)
            if type(item["size_bytes"]) is not int or item["size_bytes"] < 0 or not isinstance(item["sha256"], str) or not SHA256_RE.fullmatch(item["sha256"]):
                self.add(self.invalid, "descriptor_source_binding_invalid")
                continue
            self.descriptor_bindings[path] = (item["size_bytes"], item["sha256"])
            try:
                snapshot = _snapshot_file(self.root, path)
            except Exception:
                self.add(self.invalid, "descriptor_source_unreadable")
                continue
            if snapshot["size_bytes"] != item["size_bytes"] or snapshot["sha256"] != item["sha256"]:
                self.add(self.stale, "descriptor_source_stale")
            self.inventory_candidates[path] = self._inventory_item(
                path=path,
                role="resolved_input",
                producer_operation=f"resolved_input_descriptor:{self.descriptor_sha256}",
                task_id=descriptor.get("workflow_task_id") if isinstance(descriptor.get("workflow_task_id"), str) else "resolved_input",
                state_version=0,
                snapshot=snapshot,
            )
        origin_paths: dict[str, str] = {}
        for item in origin:
            if not isinstance(item, Mapping) or set(item) != {"path", "sha256"}:
                self.add(self.invalid, "descriptor_origin_invalid")
                continue
            try:
                path = _safe_run_path(self.root, self.run_id, item["path"])
            except ArtifactResolutionError:
                self.add(self.invalid, "descriptor_origin_invalid")
                continue
            if path in origin_paths or not isinstance(item["sha256"], str) or not SHA256_RE.fullmatch(item["sha256"]):
                self.add(self.invalid, "descriptor_origin_invalid")
            origin_paths[path] = item.get("sha256")
        for item in source:
            if isinstance(item, Mapping) and isinstance(item.get("path"), str):
                try:
                    normalized = _safe_run_path(self.root, self.run_id, item["path"])
                except ArtifactResolutionError:
                    continue
                if origin_paths.get(normalized) != item.get("sha256"):
                    self.add(self.invalid, "descriptor_origin_mismatch")
        try:
            current = _current_plan_fingerprint({**descriptor, "descriptor_sha256": descriptor_sha})
            self.plan_fingerprint = current
            if current != self.state.get("plan_fingerprint"):
                self.add(self.stale, "plan_fingerprint_stale")
        except Exception:
            self.add(self.invalid, "plan_fingerprint_invalid")
        try:
            from orchestrator.inspection_workflow import lifecycle

            expected_plan = build_required_task_plan(
                build_dag(lifecycle._DAG_CONFIG_PATH, profile=lifecycle._PHASE_A_EXECUTION_PROFILE)[0]
            )
            if (
                _jsonable(list(self.state.get("task_plan", []))) != expected_plan
                and self.plan_fingerprint != self.state.get("plan_fingerprint")
            ):
                self.add(self.stale, "task_plan_stale")
        except Exception:
            self.add(self.invalid, "task_plan_invalid")
        return True

    def _inventory_item(
        self,
        *,
        path: str,
        role: str,
        producer_operation: str,
        task_id: str,
        state_version: int,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "task_id": task_id,
            "artifact_role": role,
            "path": path,
            "size_bytes": snapshot["size_bytes"],
            "sha256": snapshot["sha256"],
            "producer_operation": producer_operation,
            "resulting_state_version": state_version,
            "plan_fingerprint": self.state.get("plan_fingerprint") if self.state else self.plan_fingerprint,
            "input_descriptor_sha256": self.descriptor_sha256,
        }

    def validate_a1(self) -> None:
        if self.state is None:
            return
        status = self.state.get("status")
        a1_manifest_path = _path_for(self.root, f"runs/{self.run_id}/artifacts/comparison_evidence_manifest.json")
        evidence_path = _path_for(self.root, f"runs/{self.run_id}/artifacts/comparison_evidence.csv")
        has_a1 = _lstat(a1_manifest_path, label="A1 manifest") is not None or _lstat(evidence_path, label="A1 evidence") is not None
        success_tasks = {task_id for task_id, row in self.task_operations.items() if row.get("task_id") == task_id}
        requires_a1 = bool(success_tasks & {
            "phase_a_comparison_evidence", "phase_a_claim_gate", "phase_a_growth_report",
            "phase_a_memory_report", "phase_a_engineering_claim_report", "phase_a_claim_visualization",
        }) or status == "COMPLETED"
        if not has_a1:
            if requires_a1:
                self.add(self.invalid, "a1_missing")
            else:
                self.add(self.incomplete, "a1_incomplete")
            return
        if self.state.get("plan_fingerprint") is None:
            self.add(self.invalid, "a1_state_binding_missing")
            return
        try:
            self.a1_result = a1_artifacts.validate_comparison_evidence_bundle(
                self.root,
                run_id=self.run_id,
                execution_profile=a1_artifacts.PHASE_A1_EXECUTION_PROFILE,
                plan_fingerprint=self.state["plan_fingerprint"],
            )
        except Exception:
            # A source descriptor byte drift is a freshness observation.  Any
            # other failed A1 contract is an integrity failure.
            try:
                descriptor_only = self._a1_failure_is_descriptor_only()
            except Exception:
                descriptor_only = False
            if (
                "descriptor_source_stale" not in self.stale
                or not descriptor_only
            ):
                self.add(self.invalid, "a1_invalid")
            return
        requires_claim = bool(
            {"phase_a_claim_gate", "phase_a_growth_report", "phase_a_memory_report",
             "phase_a_engineering_claim_report", "phase_a_claim_visualization"}
            & set(self.task_operations)
        ) or status == "COMPLETED"
        if requires_claim:
            try:
                self.a1_result = a1_artifacts.load_validated_claim_artifacts(
                    self.root,
                    run_id=self.run_id,
                    execution_profile=a1_artifacts.PHASE_A1_EXECUTION_PROFILE,
                    plan_fingerprint=self.state["plan_fingerprint"],
                )
            except Exception:
                if "descriptor_source_stale" not in self.stale:
                    self.add(self.invalid, "claim_decision_invalid")
                return
        manifest = self.a1_result.get("manifest") if isinstance(self.a1_result, Mapping) else None
        if isinstance(manifest, Mapping):
            for item in manifest.get("source_artifacts", []):
                if isinstance(item, Mapping) and isinstance(item.get("path"), str):
                    try:
                        path = _safe_run_path(self.root, self.run_id, item["path"])
                        snapshot = _snapshot_file(self.root, path)
                        self._add_manifest_item(path, item, snapshot, "a1")
                    except Exception:
                        self.add(self.invalid, "a1_source_invalid")

    def _binding_mismatch(
        self,
        path_value: Any,
        expected_size: Any,
        expected_sha: Any,
    ) -> str:
        """Classify one declared Run-local binding without trusting its hash."""

        if not isinstance(path_value, str):
            return "other"
        descriptor_binding = self.descriptor_bindings.get(path_value)
        try:
            path = _safe_run_path(self.root, self.run_id, path_value)
        except Exception:
            return "descriptor" if descriptor_binding is not None else "other"
        try:
            snapshot = _snapshot_file(self.root, path)
        except Exception:
            return "descriptor" if descriptor_binding is not None else "other"
        if snapshot["size_bytes"] == expected_size and snapshot["sha256"] == expected_sha:
            return "ok"
        if (
            descriptor_binding is not None
            and descriptor_binding == (expected_size, expected_sha)
        ):
            return "descriptor"
        return "other"

    def _read_loose_object(self, path: Path) -> dict[str, Any] | None:
        try:
            _assert_plain(path, label="diagnostic manifest", directory=False)
            data = path.read_bytes()
            value = json.loads(data.decode("utf-8"))
            if not isinstance(value, Mapping) or data != _canonical_json_bytes(dict(value)) + b"\n":
                return None
        except Exception:
            return None
        return dict(value) if isinstance(value, Mapping) else None

    def _claim_contract_intact(self, manifest: Mapping[str, Any]) -> bool:
        """Recheck ClaimDecision independently when A1 source bytes drift."""

        try:
            evidence_path = _path_for(self.root, manifest["comparison_evidence_path"])
            decision_path = _path_for(
                self.root, f"runs/{self.run_id}/artifacts/claim_decision.json"
            )
            _assert_plain(evidence_path, label="comparison evidence", directory=False)
            _assert_plain(decision_path, label="ClaimDecision", directory=False)
            evidence_bytes = evidence_path.read_bytes()
            decision_bytes = decision_path.read_bytes()
            records = a1_artifacts.parse_comparison_evidence_csv(evidence_bytes)
            decision = json.loads(decision_bytes.decode("utf-8"))
            if not isinstance(decision, Mapping):
                return False
            if a1_artifacts._canonical_json_bytes(dict(decision)) != decision_bytes:
                return False
            from orchestrator.inspection_workflow.claim_decision import validate_claim_decision_document

            validate_claim_decision_document(
                decision,
                records,
                expected_run_id=self.run_id,
                expected_plan_fingerprint=self.state["plan_fingerprint"],
                expected_source_comparison_evidence_sha256=hashlib.sha256(evidence_bytes).hexdigest(),
            )
        except Exception:
            return False
        return True

    def _a1_failure_is_descriptor_only(self) -> bool:
        manifest = self._read_loose_object(
            _path_for(self.root, f"runs/{self.run_id}/artifacts/comparison_evidence_manifest.json")
        )
        if manifest is None or self.state is None:
            return False
        if set(manifest) != getattr(a1_artifacts, "_MANIFEST_FIELDS", set()):
            return False
        if (
            manifest.get("run_id") != self.run_id
            or manifest.get("plan_fingerprint") != self.state.get("plan_fingerprint")
            or manifest.get("execution_profile") != a1_artifacts.PHASE_A1_EXECUTION_PROFILE
            or manifest.get("schema_version") != a1_artifacts.COMPARISON_EVIDENCE_MANIFEST_SCHEMA_VERSION
        ):
            return False
        mismatch = self._binding_mismatch(
            manifest.get("comparison_evidence_path"),
            manifest.get("comparison_evidence_size_bytes"),
            manifest.get("comparison_evidence_sha256"),
        )
        if mismatch == "other":
            return False
        source_entries = manifest.get("source_artifacts")
        if not isinstance(source_entries, list):
            return False
        seen: set[str] = set()
        descriptor_mismatch = mismatch == "descriptor"
        for item in source_entries:
            if not isinstance(item, Mapping):
                return False
            path_value = item.get("path")
            if not isinstance(path_value, str) or path_value in seen:
                return False
            seen.add(path_value)
            item_mismatch = self._binding_mismatch(
                path_value, item.get("size_bytes"), item.get("sha256")
            )
            if item_mismatch == "other":
                return False
            descriptor_mismatch = descriptor_mismatch or item_mismatch == "descriptor"
        requires_claim = bool(
            self.state.get("status") == "COMPLETED"
            or {"phase_a_claim_gate", "phase_a_growth_report", "phase_a_memory_report",
                "phase_a_engineering_claim_report", "phase_a_claim_visualization"}
            & set(self.task_operations)
        )
        return descriptor_mismatch and (not requires_claim or self._claim_contract_intact(manifest))

    def _add_manifest_item(self, path: str, item: Mapping[str, Any], snapshot: Mapping[str, Any], source: str) -> None:
        expected_size = item.get("size_bytes")
        expected_sha = item.get("sha256")
        if snapshot["size_bytes"] != expected_size or snapshot["sha256"] != expected_sha:
            self.add(self.invalid, f"{source}_source_hash_mismatch")
        role = item.get("role") or item.get("kind") or "run_local_artifact"
        operation = self.declared_paths.get(path)
        if operation is None:
            operation = self._infer_producer(path)
        if operation is None:
            self.add(self.invalid, "producer_missing")
            return
        task_id, producer_operation, state_version = operation
        candidate = self._inventory_item(
            path=path,
            role=str(role),
            producer_operation=producer_operation,
            task_id=task_id,
            state_version=state_version,
            snapshot=snapshot,
        )
        previous = self.inventory_candidates.get(path)
        if previous is not None and previous["sha256"] != candidate["sha256"]:
            self.add(self.invalid, "inventory_binding_conflict")
        self.inventory_candidates[path] = candidate

    def _infer_producer(self, path: str) -> tuple[str, str, int] | None:
        if path in self.declared_paths:
            return self.declared_paths[path]
        name = PurePosixPath(path).name
        if name == "final_summary.md":
            tx = self.transaction or {}
            transaction_id = tx.get("transaction_id", "unknown")
            return ("publication", f"publication:{transaction_id}", int(self.state.get("state_version", 0)))
        if "/raw_prepared/" in path:
            if self.descriptor_sha256 and self.descriptor:
                return (
                    str(self.descriptor.get("workflow_task_id", "resolved_input")),
                    f"resolved_input_descriptor:{self.descriptor_sha256}",
                    0,
                )
        if "/artifacts/" in path:
            mapping = {
                "comparison_evidence.csv": "phase_a_comparison_evidence",
                "comparison_evidence_manifest.json": "phase_a_comparison_evidence",
                "claim_decision.json": "phase_a_claim_gate",
            }
            task_id = mapping.get(name)
            if task_id and task_id in self.task_operations:
                row = self.task_operations[task_id]
                return task_id, row["operation_id"], row["state_version"]
        if "/staging/" in path:
            for suffix, task_id in (
                ("disease_growth_analysis_report.md", "phase_a_growth_report"),
                ("disease_growth_analysis_summary.md", "phase_a_growth_report"),
                ("memory_agent_report.md", "phase_a_memory_report"),
                ("disease_memory_bank_summary.md", "phase_a_memory_report"),
                ("disease_engineering_report.md", "phase_a_engineering_claim_report"),
                ("disease_engineering_report_summary.md", "phase_a_engineering_claim_report"),
                ("priority_recheck_list.csv", "phase_a_claim_visualization"),
                ("visualization_report.md", "phase_a_claim_visualization"),
                ("visualization_summary.md", "phase_a_claim_visualization"),
                ("recheck_list_report.md", "phase_a_claim_visualization"),
            ):
                if name == suffix and task_id in self.task_operations:
                    row = self.task_operations[task_id]
                    return task_id, row["operation_id"], row["state_version"]
            if "/visualizations/" in path and "phase_a_claim_visualization" in self.task_operations:
                row = self.task_operations["phase_a_claim_visualization"]
                return "phase_a_claim_visualization", row["operation_id"], row["state_version"]
        if "phase_a_association" in self.task_operations and (
            "/raw_history/" in path
            or "/main_progressive/" in path
            or name.startswith("association_")
            or name.startswith("progressive_")
        ):
            row = self.task_operations["phase_a_association"]
            return "phase_a_association", row["operation_id"], row["state_version"]
        if "phase_a_comparison_evidence" in self.task_operations and (
            name in {"frame_records.csv", "engineering_records.csv", "projection_receipt.json"}
            or "comparison" in name
        ):
            row = self.task_operations["phase_a_comparison_evidence"]
            return "phase_a_comparison_evidence", row["operation_id"], row["state_version"]
        return None

    def validate_a2(self) -> None:
        if self.state is None:
            return
        manifest_path = _path_for(self.root, publication.PUBLICATION_MANIFEST_PATH)
        tx_path = _path_for(
            self.root,
            publication.PUBLICATION_TRANSACTION_PATH_TEMPLATE.format(run_id=self.run_id),
        )
        manifest_exists = _lstat(manifest_path, label="publication Manifest") is not None
        tx_exists = _lstat(tx_path, label="publication transaction") is not None
        status = self.state.get("status")
        if not manifest_exists and not tx_exists:
            if status == "COMPLETED":
                self.add(self.invalid, "publication_missing_for_completed_state")
            else:
                self.add(self.incomplete, "publication_incomplete")
            return
        if manifest_exists != tx_exists:
            self.add(self.invalid, "publication_pair_invalid")
            return
        try:
            self.a2_result = publication.validate_publication(
                self.root,
                run_id=self.run_id,
                execution_profile=publication.PUBLICATION_EXECUTION_PROFILE,
                plan_fingerprint=self.state["plan_fingerprint"],
            )
        except Exception:
            try:
                descriptor_only = self._a2_failure_is_descriptor_only()
            except Exception:
                descriptor_only = False
            if (
                "descriptor_source_stale" not in self.stale
                or not descriptor_only
            ):
                self.add(self.invalid, "publication_invalid")
            return
        self.a2_manifest = self.a2_result.get("manifest") if isinstance(self.a2_result, Mapping) else None
        if not isinstance(self.a2_manifest, Mapping):
            self.add(self.invalid, "publication_manifest_invalid")
            return
        if self.transaction is None:
            self.add(self.invalid, "transaction_missing")
            return
        if self.transaction.get("phase") != _STABLE_TX_PHASE:
            self.add(self.recovery, "transaction_recovery")
        sources = self.a2_manifest.get("source_artifacts")
        if not isinstance(sources, list):
            self.add(self.invalid, "publication_sources_invalid")
            return
        seen: set[str] = set()
        for item in sources:
            if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
                self.add(self.invalid, "publication_source_invalid")
                continue
            try:
                path = _safe_run_path(self.root, self.run_id, item["path"])
                if path in seen:
                    self.add(self.invalid, "publication_source_duplicate")
                seen.add(path)
                snapshot = _snapshot_file(self.root, path)
                self._add_manifest_item(path, item, snapshot, "publication")
            except Exception:
                self.add(self.invalid, "publication_source_invalid")
        if status == "COMPLETED":
            if self.transaction.get("phase") != _STABLE_TX_PHASE:
                self.add(self.recovery, "transaction_recovery")
            if not self.a2_result:
                self.add(self.invalid, "completed_publication_invalid")
        else:
            self.add(self.invalid, "nonterminal_publication_committed")
        if self.a2_manifest is not None and not self.invalid:
            self._check_committed_scope_closure()

    def _a2_failure_is_descriptor_only(self) -> bool:
        manifest_path = _path_for(self.root, publication.PUBLICATION_MANIFEST_PATH)
        manifest = self._read_loose_object(manifest_path)
        if manifest is None or self.state is None or self.transaction is None:
            return False
        if (
            manifest.get("run_id") != self.run_id
            or manifest.get("plan_fingerprint") != self.state.get("plan_fingerprint")
            or manifest.get("execution_profile") != publication.PUBLICATION_EXECUTION_PROFILE
            or manifest.get("schema_version") != publication.PUBLICATION_MANIFEST_SCHEMA_VERSION
            or manifest.get("publication_manifest_path") != publication.PUBLICATION_MANIFEST_PATH
            or manifest.get("publication_transaction_path")
            != publication.PUBLICATION_TRANSACTION_PATH_TEMPLATE.format(run_id=self.run_id)
            or manifest.get("final_summary_path")
            != publication.FINAL_SUMMARY_PATH_TEMPLATE.format(run_id=self.run_id)
        ):
            return False
        if set(manifest) != getattr(publication, "_MANIFEST_FIELDS", set()):
            return False
        if (
            self.transaction.get("run_id") != self.run_id
            or self.transaction.get("plan_fingerprint") != self.state.get("plan_fingerprint")
            or self.transaction.get("execution_profile") != publication.PUBLICATION_EXECUTION_PROFILE
            or self.transaction.get("manifest_path") != publication.PUBLICATION_MANIFEST_PATH
        ):
            return False
        try:
            manifest_bytes = manifest_path.read_bytes()
        except OSError:
            return False
        if self.transaction.get("manifest_sha256") != _sha256(manifest_bytes):
            return False
        source_entries = manifest.get("source_artifacts")
        publication_files = manifest.get("publication_files")
        if not isinstance(source_entries, list) or not isinstance(publication_files, list):
            return False
        expected_source_paths = [
            item.get("path") for item in source_entries if isinstance(item, Mapping)
        ]
        if (
            manifest.get("expected_source_artifact_paths") != expected_source_paths
            or expected_source_paths != sorted(expected_source_paths)
        ):
            return False
        seen: set[str] = set()
        descriptor_mismatch = False
        for item in source_entries:
            if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
                return False
            if item["path"] in seen:
                return False
            seen.add(item["path"])
            mismatch = self._binding_mismatch(
                item["path"], item.get("size_bytes"), item.get("sha256")
            )
            if mismatch == "other":
                return False
            descriptor_mismatch = descriptor_mismatch or mismatch == "descriptor"
        for item in publication_files:
            if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
                return False
            try:
                path = _path_for(self.root, item["path"])
                snapshot = _snapshot_file(self.root, f"runs/{self.run_id}/final_summary.md") if item["path"] == publication.FINAL_SUMMARY_PATH_TEMPLATE.format(run_id=self.run_id) else None
                if snapshot is None:
                    _assert_plain(path, label="publication file", directory=False)
                    data = path.read_bytes()
                    size, digest = len(data), _sha256(data)
                else:
                    size, digest = snapshot["size_bytes"], snapshot["sha256"]
                if size != item.get("size_bytes") or digest != item.get("sha256"):
                    return False
            except Exception:
                return False
        expected_publication_paths = sorted(
            [*getattr(publication, "_FINAL_PATHS", ()), publication.FINAL_SUMMARY_PATH_TEMPLATE.format(run_id=self.run_id)]
        )
        if [item.get("path") for item in publication_files] != expected_publication_paths:
            return False
        return descriptor_mismatch

    def _check_committed_scope_closure(self) -> None:
        """Reject extra files in committed artifact/publication scopes.

        The resolver intentionally does not walk arbitrary ``work`` content,
        caches, videos, models, or Web WIP.  A1/A2 committed scopes are the
        bounded directories whose complete sets are authoritative.
        """

        allowed = {
            item.get("path")
            for item in self.a2_manifest.get("source_artifacts", [])
            if isinstance(item, Mapping)
        }
        allowed.update(
            item.get("path")
            for item in self.a2_manifest.get("publication_files", [])
            if isinstance(item, Mapping)
        )
        allowed.add(publication.PUBLICATION_MANIFEST_PATH)
        for relative_root in (
            f"runs/{self.run_id}/artifacts",
            f"runs/{self.run_id}/staging",
            "outputs",
        ):
            path_root = _path_for(self.root, relative_root)
            entry = _lstat(path_root, label=relative_root)
            if entry is None:
                continue
            if stat.S_ISLNK(entry.st_mode) or _is_reparse(entry) or not stat.S_ISDIR(entry.st_mode):
                self.add(self.invalid, "committed_scope_unsafe")
                continue
            try:
                candidates = sorted(path_root.rglob("*"), key=lambda item: item.as_posix())
            except (OSError, ValueError):
                self.add(self.invalid, "committed_scope_unreadable")
                continue
            for candidate in candidates:
                candidate_relative = candidate.relative_to(self.root).as_posix()
                try:
                    item = _lstat(candidate, label=candidate_relative)
                except ArtifactResolutionError:
                    self.add(self.invalid, "committed_scope_unreadable")
                    continue
                if item is None:
                    self.add(self.invalid, "committed_scope_unreadable")
                elif stat.S_ISLNK(item.st_mode) or _is_reparse(item):
                    self.add(self.invalid, "committed_scope_unsafe")
                elif stat.S_ISREG(item.st_mode) and candidate_relative not in allowed:
                    self.add(self.invalid, "unlisted_committed_artifact")
                elif not stat.S_ISREG(item.st_mode) and not stat.S_ISDIR(item.st_mode):
                    self.add(self.invalid, "committed_scope_unsafe")

    def finish(self) -> ArtifactResolution:
        if self.state is not None:
            required = {item.get("task_id") for item in self.state.get("task_plan", []) if item.get("required")}
            statuses = self.state.get("task_status", {})
            if not isinstance(statuses, Mapping):
                self.add(self.invalid, "task_status_invalid")
            else:
                missing = sorted(task for task in required if statuses.get(task) not in {"success", "skipped"})
                if missing:
                    self.add(self.incomplete, "required_task_incomplete")
                if self.state.get("status") == "COMPLETED" and missing:
                    self.add(self.invalid, "completed_required_task_incomplete")
                if self.state.get("status") == "COMPLETED" and any(
                    status not in {"success", "skipped"} for status in statuses.values()
                ):
                    self.add(self.invalid, "completed_task_status_invalid")
            if self.state.get("status") == "COMPLETED":
                self._check_completion_evidence()
        # Every committed success output must be represented by the validated
        # complete A2 source set.  This catches omitted Manifest entries and
        # producer swaps without scanning arbitrary WIP directories.
        if self.a2_manifest is not None:
            manifest_paths = {
                item.get("path") for item in self.a2_manifest.get("source_artifacts", [])
                if isinstance(item, Mapping)
            }
            for operation in self.task_operations.values():
                for path in operation.get("paths", []):
                    if path not in manifest_paths:
                        self.add(self.invalid, "checkpoint_output_not_manifest")
        return self.result()

    def _check_completion_evidence(self) -> None:
        context = self.state.get("context", {}) if self.state else {}
        evidence = context.get("completion_evidence") if isinstance(context, Mapping) else None
        if not isinstance(evidence, Mapping):
            # StateStore validates the completion evidence at the committing
            # transition.  Current canonical State intentionally keeps only
            # the resulting binding, while A2 below revalidates its bytes.
            return
        if self.a2_result is None:
            self.add(self.invalid, "completion_publication_missing")
            return
        manifest_bytes = self.a2_result.get("manifest_bytes")
        tx = self.a2_result.get("transaction")
        if not isinstance(manifest_bytes, bytes) or not isinstance(tx, Mapping):
            self.add(self.invalid, "completion_evidence_invalid")
            return
        expected = {
            "publication_manifest_path": publication.PUBLICATION_MANIFEST_PATH,
            "publication_transaction_path": publication.PUBLICATION_TRANSACTION_PATH_TEMPLATE.format(run_id=self.run_id),
            "final_summary_path": publication.FINAL_SUMMARY_PATH_TEMPLATE.format(run_id=self.run_id),
        }
        for field, value in expected.items():
            if evidence.get(field) != value:
                self.add(self.invalid, "completion_evidence_binding_invalid")
        if evidence.get("publication_manifest_sha256") != _sha256(manifest_bytes):
            self.add(self.invalid, "completion_manifest_hash_mismatch")
        if evidence.get("transaction_id") != tx.get("transaction_id"):
            self.add(self.invalid, "completion_transaction_mismatch")
        try:
            tx_bytes = _path_for(self.root, expected["publication_transaction_path"]).read_bytes()
            summary_bytes = _path_for(self.root, expected["final_summary_path"]).read_bytes()
            if evidence.get("publication_transaction_sha256") != _sha256(tx_bytes):
                self.add(self.invalid, "completion_transaction_hash_mismatch")
            if evidence.get("final_summary_sha256") != _sha256(summary_bytes):
                self.add(self.invalid, "completion_summary_hash_mismatch")
        except (OSError, ValueError):
            self.add(self.invalid, "completion_summary_missing")


class ArtifactResolver:
    """Resolve one existing canonical Run without mutating the sandbox."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).absolute()

    def resolve(self, *, run_id: str) -> ArtifactResolution:
        if not isinstance(run_id, str) or RUN_ID_RE.fullmatch(run_id) is None:
            raise ArtifactResolverInputError("run_id must use canonical run_NNN format")
        try:
            _assert_plain_entry(self.project_root, label="project_root", directory=True)
            root = a1_artifacts._controlled_temporary_root(self.project_root)
        except Exception as exc:
            raise ArtifactResolverInputError(
                "ArtifactResolver requires an existing controlled temporary sandbox"
            ) from exc
        run = _ResolverPass(root, run_id)
        if not run.preflight():
            return run.result()
        if not run.load_state():
            return run.result()
        run.validate_descriptor()
        run.validate_a1()
        run.validate_a2()
        return run.finish()

    def resolve_run(self, run_id: str) -> ArtifactResolution:
        return self.resolve(run_id=run_id)


def resolve_run_artifacts(project_root: Path, *, run_id: str) -> ArtifactResolution:
    """Convenience API with the same narrow, read-only input boundary."""

    return ArtifactResolver(project_root).resolve(run_id=run_id)


__all__ = [
    "ArtifactResolution",
    "ArtifactResolver",
    "ArtifactResolverInputError",
    "ArtifactResolutionError",
    "INVENTORY_SCHEMA_VERSION",
    "resolve_run_artifacts",
]
