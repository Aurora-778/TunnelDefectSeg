"""Phase B.7's read-only handoff from activated Resume to future execution."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping
import uuid

from . import a1_artifacts
from .explicit_resume_activation import (
    ExplicitResumeActivation,
    ExplicitResumeActivationResult,
    _ACTIVATED,
    _B1_READ_GUARDED,
    _RUN_ID_RE,
    _canonical_json_bytes,
)
from orchestrator.state.store import StateStore


RESUME_EXECUTION_PREPARATION_SCHEMA_VERSION = "inspection_resume_execution_preparation_v1"
_READY = "resume_execution_prepared"
_NOT_READY = "resume_execution_not_prepared"
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")

# Preserve the B.6 authority entry points at import time.  A caller may
# replace the public module attribute, but that must not substitute the
# authority revalidation that every B.7 preparation performs.
_B6_ACTIVATION = ExplicitResumeActivation
_B6_RESULT = ExplicitResumeActivationResult
_B6_RESULT_VALIDATE = ExplicitResumeActivationResult.__post_init__
_B6_ACTIVATION_EVIDENCE = ExplicitResumeActivation._activation_evidence
_B6_INTENT_PATH_FOR = ExplicitResumeActivation._intent_path_for
_B6_DIRECTORY_CHAIN = ExplicitResumeActivation._directory_chain
_B6_VALIDATE_INTENT = ExplicitResumeActivation._validate_intent
_STATE_STORE = StateStore
_CONTROLLED_ROOT = a1_artifacts._controlled_temporary_root
_GENESIS_SUCCESSOR_ENTRIES = frozenset({"state.json", "state_journal.jsonl", "state_journal_tail.json"})


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_bytes(status: str, bindings: Mapping[str, Any] | None = None) -> bytes:
    document: dict[str, Any] = {
        "schema_version": RESUME_EXECUTION_PREPARATION_SCHEMA_VERSION,
        "status": status,
    }
    if bindings is not None:
        document.update(bindings)
    return _canonical_json_bytes(document)


def _plain_json(value: Any) -> Any:
    """Thaw the authority snapshot solely for canonical JSON hashing."""
    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value


def _is_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except (AttributeError, ValueError):
        return False


def _pristine_successor_entries(
    root: Path, successor_run_id: str,
) -> tuple[tuple[str, ...], tuple[tuple[Path, int, int], ...]]:
    """Reject every successor entry that B.6's genesis authority does not own."""
    successor = root / "runs" / successor_run_id
    chain = _B6_DIRECTORY_CHAIN(root, successor, label="B.7 successor entry set")
    entries = tuple(sorted(entry.name for entry in successor.iterdir()))
    if not {"state.json", "state_journal_tail.json"}.issubset(entries) or any(
        name not in _GENESIS_SUCCESSOR_ENTRIES for name in entries
    ):
        raise ValueError("successor contains non-genesis entries")
    for name in entries:
        _B1_READ_GUARDED(root, f"runs/{successor_run_id}/{name}")
    if _B6_DIRECTORY_CHAIN(root, successor, label="B.7 successor entry set") != chain:
        raise ValueError("successor directory changed during entry validation")
    return entries, chain


@dataclass(frozen=True, init=False, slots=True)
class ResumeExecutionPreparation:
    """Immutable B.7 handoff; successful values are factory-only."""

    status: str
    preparation_bytes: bytes
    preparation_sha256: str
    successor_run_id: str | None
    source_run_id: str | None
    activation_sha256: str | None
    intent_sha256: str | None
    source_admission_sha256: str | None
    allocation_token: str | None
    lock_token: str | None
    state_version: int | None
    plan_fingerprint: str | None
    input_descriptor_sha256: str | None
    task_plan_sha256: str | None
    required_task_ids: tuple[str, ...]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("ResumeExecutionPreparation is created only by ResumeExecutionPreparer")

    def __post_init__(self) -> None:
        if self.status not in {_READY, _NOT_READY} or type(self.preparation_bytes) is not bytes:
            raise ValueError("preparation status or bytes are invalid")
        if type(self.required_task_ids) is not tuple or not all(
            type(task_id) is str and bool(task_id) for task_id in self.required_task_ids
        ):
            raise ValueError("required task ids must be an immutable string tuple")
        if type(self.preparation_sha256) is not str or _sha256(self.preparation_bytes) != self.preparation_sha256:
            raise ValueError("preparation SHA does not match bytes")
        bindings = {
            "successor_run_id": self.successor_run_id,
            "source_run_id": self.source_run_id,
            "activation_sha256": self.activation_sha256,
            "intent_sha256": self.intent_sha256,
            "source_admission_sha256": self.source_admission_sha256,
            "allocation_token": self.allocation_token,
            "lock_token": self.lock_token,
            "state_version": self.state_version,
            "plan_fingerprint": self.plan_fingerprint,
            "input_descriptor_sha256": self.input_descriptor_sha256,
            "task_plan_sha256": self.task_plan_sha256,
            "required_task_ids": list(self.required_task_ids),
        }
        if self.preparation_bytes != _canonical_bytes(self.status, None if self.status == _NOT_READY else bindings):
            raise ValueError("preparation bytes do not match fields")
        if self.status == _NOT_READY:
            if any(value is not None for key, value in bindings.items() if key != "required_task_ids") or self.required_task_ids:
                raise ValueError("denied preparation must not expose bindings")
            return
        if (
            type(self.successor_run_id) is not str
            or _RUN_ID_RE.fullmatch(self.successor_run_id) is None
            or type(self.source_run_id) is not str
            or _RUN_ID_RE.fullmatch(self.source_run_id) is None
            or type(self.state_version) is not int
            or self.state_version != 0
            or any(
                type(getattr(self, name)) is not str or not _is_uuid(getattr(self, name))
                for name in ("allocation_token", "lock_token")
            )
            or any(
                type(getattr(self, name)) is not str or _SHA256_RE.fullmatch(getattr(self, name)) is None
                for name in (
                    "activation_sha256", "intent_sha256", "source_admission_sha256",
                    "plan_fingerprint", "input_descriptor_sha256", "task_plan_sha256",
                )
            )
            or not self.required_task_ids
            or tuple(sorted(set(self.required_task_ids))) != self.required_task_ids
        ):
            raise ValueError("prepared bindings are invalid")

    @property
    def resume_execution_prepared(self) -> bool:
        return self.status == _READY

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


def _not_prepared() -> ResumeExecutionPreparation:
    result = object.__new__(ResumeExecutionPreparation)
    object.__setattr__(result, "status", _NOT_READY)
    data = _canonical_bytes(_NOT_READY)
    object.__setattr__(result, "preparation_bytes", data)
    object.__setattr__(result, "preparation_sha256", _sha256(data))
    for name in ResumeExecutionPreparation.__dataclass_fields__:
        if name not in {"status", "preparation_bytes", "preparation_sha256"}:
            object.__setattr__(result, name, () if name == "required_task_ids" else None)
    result.__post_init__()
    return result


class ResumeExecutionPreparer:
    """Read-only B.7 preparation boundary; it never executes or mutates a Run."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).absolute()

    def prepare(
        self, *, successor_run_id: str, activation: ExplicitResumeActivationResult
    ) -> ResumeExecutionPreparation:
        try:
            root = _CONTROLLED_ROOT(self.project_root)
            if type(successor_run_id) is not str or _RUN_ID_RE.fullmatch(successor_run_id) is None:
                raise ValueError("successor run id is invalid")
            if type(activation) is not _B6_RESULT:
                raise ValueError("activation is not exact")
            _B6_RESULT_VALIDATE(activation)
            if activation.status != _ACTIVATED or activation.successor_run_id != successor_run_id:
                raise ValueError("activation does not bind successor")
            return self._prepare_current(root, activation)
        except Exception:
            return _not_prepared()

    def _prepare_current(self, root: Path, activation: ExplicitResumeActivationResult) -> ResumeExecutionPreparation:
        verifier = _B6_ACTIVATION(root)
        source = _STATE_STORE(root).load(run_id=activation.source_run_id)["canonical_state"]
        context = source.get("context")
        if (
            source.get("status") != "COMPLETED"
            or source.get("plan_fingerprint") != activation.plan_fingerprint
            or not isinstance(context, Mapping)
            or context.get("resolved_input_descriptor_sha256") != activation.input_descriptor_sha256
        ):
            raise ValueError("source State drifted")
        intent_path = _B6_INTENT_PATH_FOR(
            root, activation.source_run_id or "", activation.source_admission_sha256 or "",
        )
        chain = _B6_DIRECTORY_CHAIN(root, intent_path.parent, label="B.7 activation intent")
        intent_bytes, _ = _B1_READ_GUARDED(root, intent_path.relative_to(root).as_posix())
        intent = _B6_VALIDATE_INTENT(json.loads(intent_bytes.decode("utf-8")))
        expected = {
            "source_run_id": activation.source_run_id,
            "successor_run_id": activation.successor_run_id,
            "allocation_token": activation.allocation_token,
            "lock_token": activation.lock_token,
            "source_admission_sha256": activation.source_admission_sha256,
            "plan_fingerprint": activation.plan_fingerprint,
            "input_descriptor_sha256": activation.input_descriptor_sha256,
        }
        if any(intent.get(name) != value for name, value in expected.items()):
            raise ValueError("intent does not bind activation")
        if source.get("state_version") != intent.get("source_state_version"):
            raise ValueError("source State version drifted")
        entries_before, successor_chain_before = _pristine_successor_entries(
            root, activation.successor_run_id or ""
        )
        first = _B6_ACTIVATION_EVIDENCE(
            verifier, root, intent=intent, intent_bytes=intent_bytes,
            source_state=source, durable_intent_chain=chain,
        )
        second = _B6_ACTIVATION_EVIDENCE(
            verifier, root, intent=intent, intent_bytes=intent_bytes,
            source_state=source, durable_intent_chain=chain,
        )
        if first != second or any(first[name] != getattr(activation, name) for name in (
            "intent_sha256", "state_sha256", "journal_sha256", "journal_anchor_sha256", "lock_sha256"
        )):
            raise ValueError("activation evidence drifted")
        entries_after, successor_chain_after = _pristine_successor_entries(
            root, activation.successor_run_id or ""
        )
        if entries_after != entries_before or successor_chain_after != successor_chain_before:
            raise ValueError("successor entry set changed during preparation")
        state = second["state"]
        if (
            state.get("status") != "CREATED" or state.get("state_version") != 0
            or bool(state.get("task_status")) or bool(state.get("task_attempts"))
            or bool(state.get("completed_tasks")) or bool(state.get("failed_tasks"))
            or any(state.get(name) is not None for name in ("last_operation_kind", "last_operation_id", "last_operation_payload_sha256"))
        ):
            raise ValueError("successor is not a pristine genesis Run")
        plan = state.get("task_plan")
        if not isinstance(plan, (list, tuple)):
            raise ValueError("task plan is invalid")
        required = tuple(sorted(item["task_id"] for item in plan if isinstance(item, Mapping) and item.get("required") is True))
        if not required or len(required) != len(set(required)):
            raise ValueError("required task set is invalid")
        bindings = {
            "successor_run_id": activation.successor_run_id,
            "source_run_id": activation.source_run_id,
            "activation_sha256": activation.activation_sha256,
            "intent_sha256": activation.intent_sha256,
            "source_admission_sha256": activation.source_admission_sha256,
            "allocation_token": activation.allocation_token,
            "lock_token": activation.lock_token,
            "state_version": 0,
            "plan_fingerprint": activation.plan_fingerprint,
            "input_descriptor_sha256": activation.input_descriptor_sha256,
            "task_plan_sha256": _sha256(_canonical_json_bytes(_plain_json(plan))),
            "required_task_ids": list(required),
        }
        data = _canonical_bytes(_READY, bindings)
        result = object.__new__(ResumeExecutionPreparation)
        object.__setattr__(result, "status", _READY)
        object.__setattr__(result, "preparation_bytes", data)
        object.__setattr__(result, "preparation_sha256", _sha256(data))
        for name, value in bindings.items():
            object.__setattr__(result, name, tuple(value) if name == "required_task_ids" else value)
        result.__post_init__()
        return result


def prepare_resume_execution(
    project_root: Path, *, successor_run_id: str, activation: ExplicitResumeActivationResult
) -> ResumeExecutionPreparation:
    return ResumeExecutionPreparer(project_root).prepare(successor_run_id=successor_run_id, activation=activation)
