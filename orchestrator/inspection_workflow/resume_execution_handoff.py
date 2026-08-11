"""Phase B.8 controlled Resume execution handoff.

This boundary may commit only the successor CREATED@0 -> PLANNED@1
transition.  It never executes or selects work.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
import types
import uuid
import weakref

from . import a1_artifacts, controlled_fs
from .explicit_resume_activation import (
    ExplicitResumeActivation,
    ExplicitResumeActivationResult,
    _B1_READ_GUARDED,
    _RUN_ID_RE,
)
from .locking import (
    canonical_utc_now,
    validate_active_run_control_entries,
    validate_active_run_lock_snapshot,
)
from .resume_execution_preparation import (
    ResumeExecutionPreparation,
    ResumeExecutionPreparer,
)
from orchestrator.state.store import StateStore


RESUME_EXECUTION_HANDOFF_SCHEMA_VERSION = "inspection_resume_execution_handoff_v1"
RESUME_EXECUTION_HANDOFF_INTENT_SCHEMA_VERSION = (
    "inspection_resume_execution_handoff_intent_v1"
)
_READY = "resume_execution_handed_off"
_DENIED = "resume_execution_not_handed_off"
_SHA_RE = __import__("re").compile(r"[0-9a-f]{64}")
_SUCCESSOR_ENTRIES = frozenset(
    {
        "resume_execution_handoff.intent.json",
        "state.json",
        "state_journal.jsonl",
        "state_journal_tail.json",
    }
)


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


def _is_uuid(value: Any) -> bool:
    try:
        return type(value) is str and str(uuid.UUID(value)) == value
    except (AttributeError, ValueError):
        return False


def _result_bindings(result: "ResumeExecutionHandoffResult") -> dict[str, Any]:
    return {
        "successor_run_id": result.successor_run_id,
        "source_run_id": result.source_run_id,
        "handoff_intent_sha256": result.handoff_intent_sha256,
        "preparation_sha256": result.preparation_sha256,
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
        "required_task_ids": list(result.required_task_ids),
        "state_sha256": result.state_sha256,
        "journal_sha256": result.journal_sha256,
        "journal_anchor_sha256": result.journal_anchor_sha256,
        "lock_sha256": result.lock_sha256,
    }


def _new_result_registry() -> tuple[Any, Any, Any]:
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
class ResumeExecutionHandoffResult:
    """B.8 result frozen for the supported API; successes are internally issued."""

    status: str
    handoff_bytes: bytes
    handoff_sha256: str
    successor_run_id: str | None
    source_run_id: str | None
    handoff_intent_sha256: str | None
    preparation_sha256: str | None
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
    state_sha256: str | None
    journal_sha256: str | None
    journal_anchor_sha256: str | None
    lock_sha256: str | None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("ResumeExecutionHandoffResult is factory-only")

    def __post_init__(self) -> None:
        authority = ResumeExecutionHandoffResult
        if not authority._is_official_internal(self):
            raise ValueError("handoff result was not officially issued")
        if (
            self.status not in {authority._ready_internal, authority._denied_status_internal}
            or type(self.handoff_bytes) is not bytes
            or type(self.handoff_sha256) is not str
            or authority._sha256_internal(self.handoff_bytes) != self.handoff_sha256
            or type(self.required_task_ids) is not tuple
        ):
            raise ValueError("handoff result envelope is invalid")
        bindings = authority._bindings_internal(self)
        expected = authority._canonical_internal(
            {"schema_version": authority._schema_version_internal, "status": self.status}
            if self.status == authority._denied_status_internal
            else {
                "schema_version": authority._schema_version_internal,
                "status": self.status,
                **bindings,
            }
        )
        if self.handoff_bytes != expected:
            raise ValueError("handoff canonical bytes do not match fields")
        if self.status == authority._denied_status_internal:
            if self.required_task_ids or any(
                value is not None
                for name, value in bindings.items()
                if name != "required_task_ids"
            ):
                raise ValueError("denied handoff leaks authority bindings")
            return
        if (
            type(self.successor_run_id) is not str
            or authority._run_id_re_internal.fullmatch(self.successor_run_id) is None
            or type(self.source_run_id) is not str
            or authority._run_id_re_internal.fullmatch(self.source_run_id) is None
            or self.state_version != 1
            or type(self.source_state_version) is not int
            or self.source_state_version < 0
            or not authority._uuid_check_internal(self.allocation_token)
            or not authority._uuid_check_internal(self.lock_token)
            or not self.required_task_ids
            or tuple(sorted(set(self.required_task_ids))) != self.required_task_ids
            or any(
                type(getattr(self, name)) is not str
                or authority._sha_re_internal.fullmatch(getattr(self, name)) is None
                for name in (
                    "handoff_intent_sha256",
                    "preparation_sha256",
                    "activation_sha256",
                    "activation_intent_sha256",
                    "source_admission_sha256",
                    "plan_fingerprint",
                    "input_descriptor_sha256",
                    "task_plan_sha256",
                    "state_sha256",
                    "journal_sha256",
                    "journal_anchor_sha256",
                    "lock_sha256",
                )
            )
        ):
            raise ValueError("successful handoff bindings are invalid")

    @property
    def resume_execution_handed_off(self) -> bool:
        # Keep the public predicate independent of mutable module symbols and
        # do not let an object.__new__ shell with copied fields self-assert as
        # an official success without passing the sealed envelope check.
        try:
            self.__post_init__()
        except Exception:
            return False
        return self.status == "resume_execution_handed_off"


def _new_result_issuer(
    register: Any,
    discard: Any,
) -> Any:
    result_type = ResumeExecutionHandoffResult
    post_init = ResumeExecutionHandoffResult.__post_init__
    fields = tuple(result_type.__dataclass_fields__)
    object_new = object.__new__
    object_setattr = object.__setattr__

    def issue(values: Mapping[str, Any]) -> ResumeExecutionHandoffResult:
        result = object_new(result_type)
        for name in fields:
            object_setattr(
                result,
                name,
                values.get(name, () if name == "required_task_ids" else None),
            )
        register(result)
        try:
            post_init(result)
        except Exception:
            discard(result)
            raise
        return result

    return issue


# Bind the result envelope's authority before creating the issuer.  None of
# these capabilities is a dataclass field or a function default.
ResumeExecutionHandoffResult._is_official_internal = _RESULT_IS_OFFICIAL
ResumeExecutionHandoffResult._sha256_internal = _sha
ResumeExecutionHandoffResult._bindings_internal = _result_bindings
ResumeExecutionHandoffResult._canonical_internal = _canonical
ResumeExecutionHandoffResult._ready_internal = "resume_execution_handed_off"
ResumeExecutionHandoffResult._denied_status_internal = "resume_execution_not_handed_off"
ResumeExecutionHandoffResult._schema_version_internal = RESUME_EXECUTION_HANDOFF_SCHEMA_VERSION
ResumeExecutionHandoffResult._run_id_re_internal = _RUN_ID_RE
ResumeExecutionHandoffResult._sha_re_internal = _SHA_RE
ResumeExecutionHandoffResult._uuid_check_internal = _is_uuid


_RESULT_ISSUER = _new_result_issuer(_REGISTER_RESULT, _DISCARD_RESULT)
ResumeExecutionHandoffResult._issue_internal = _RESULT_ISSUER


def _new_denied_factory(issue_result: Any) -> Any:
    data = _canonical(
        {"schema_version": RESUME_EXECUTION_HANDOFF_SCHEMA_VERSION, "status": _DENIED}
    )
    sha256 = _sha

    def denied() -> ResumeExecutionHandoffResult:
        return issue_result(
            {"status": _DENIED, "handoff_bytes": data, "handoff_sha256": sha256(data)}
        )

    return denied


_DENIED_FACTORY = _new_denied_factory(_RESULT_ISSUER)
ResumeExecutionHandoffResult._denied_internal = _DENIED_FACTORY


def _issue(_values: Mapping[str, Any]) -> ResumeExecutionHandoffResult:
    """Unsupported visible compatibility surface; never an authority."""

    raise TypeError("handoff result issuance is internal")


def _denied() -> ResumeExecutionHandoffResult:
    """Unsupported visible compatibility surface; never an authority."""

    raise TypeError("handoff denial issuance is internal")


def _seal_result_issuer(*_args: Any, **_kwargs: Any) -> Any:
    """Unsupported visible compatibility surface; never an authority."""

    raise TypeError("handoff result issuer sealing is internal")


def _publish_intent(
    root: Path,
    relative: str,
    data: bytes,
    precondition: Any,
    write_exclusive: Any,
) -> tuple[int, int]:
    """Run the final observable authority fence at publication entry."""

    if not callable(precondition):
        raise ValueError("intent publication precondition is unavailable")
    precondition()
    return write_exclusive(root, relative, data)


class ResumeExecutionHandoff:
    """Durably admit a prepared successor to PLANNED without executing tasks."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).absolute()

    def handoff(
        self,
        *,
        successor_run_id: str,
        activation: ExplicitResumeActivationResult,
        preparation: ResumeExecutionPreparation,
        _controlled_root=a1_artifacts._controlled_temporary_root,
        _activation_type=ExplicitResumeActivationResult,
        _activation_validate=ExplicitResumeActivationResult.__post_init__,
        _preparation_type=ResumeExecutionPreparation,
        _preparation_validate=ResumeExecutionPreparation.__post_init__,
        _preparer_type=ResumeExecutionPreparer,
        _prepare=ResumeExecutionPreparer.prepare,
        _prepare_current=ResumeExecutionPreparer._prepare_current,
        _directory_chain=ExplicitResumeActivation._directory_chain,
        _assert_directory_chain=ExplicitResumeActivation._assert_directory_chain,
        _read_guarded=_B1_READ_GUARDED,
        _write_exclusive=controlled_fs.write_exclusive,
        _publish_authority=_publish_intent,
        _bind_directories=controlled_fs.bind_directory_identities,
        _store_type=StateStore,
        _store_load=StateStore.load,
        _store_transition=StateStore.transition_status,
        _store_validate_snapshot=StateStore.validate_authority_snapshot_bytes,
        _active_entries=validate_active_run_control_entries,
        _validate_lock=validate_active_run_lock_snapshot,
        _now=canonical_utc_now,
        _intent_factory=None,
        _intent_validate=None,
        _source_validate=None,
        _transition_authority=None,
        _result_authority=None,
        _evidence_authority=None,
        _denied_factory=None,
        _canonical_bytes=_canonical,
    ) -> ResumeExecutionHandoffResult:
        try:
            if _denied_factory is None:
                _denied_factory = ResumeExecutionHandoffResult._denied_internal
            if any(
                item is None
                for item in (
                    _intent_factory,
                    _intent_validate,
                    _source_validate,
                    _transition_authority,
                    _result_authority,
                    _evidence_authority,
                )
            ):
                raise ValueError("internal handoff authority is unavailable")
            root = _controlled_root(self.project_root)
            if type(successor_run_id) is not str or _RUN_ID_RE.fullmatch(successor_run_id) is None:
                raise ValueError("successor run id is invalid")
            if type(activation) is not _activation_type or type(preparation) is not _preparation_type:
                raise ValueError("handoff evidence is not exact")
            _activation_validate(activation)
            _preparation_validate(preparation)
            if (
                not activation.resume_activated
                or not preparation.resume_execution_prepared
                or activation.successor_run_id != successor_run_id
                or preparation.successor_run_id != successor_run_id
                or preparation.activation_sha256 != activation.activation_sha256
            ):
                raise ValueError("handoff evidence does not bind successor")

            successor = root / "runs" / successor_run_id
            chain = _directory_chain(root, successor, label="B.8 successor authority")

            # B.7 is the only authority permitted to prove that the supplied
            # preparation is current.  Run it on every supported invocation,
            # including a call made after B.8 has already transitioned the
            # successor.  B.7 intentionally accepts only pristine CREATED@0;
            # therefore a PLANNED@1 replay fails closed instead of falling
            # back to a resolver-local approximation of source freshness.
            # ``ResumeExecutionPreparer.prepare`` dispatches its core through
            # an instance attribute.  Cross-check the public result against
            # the definition-time-captured B.7 core so replacing that visible
            # class attribute (or B.7's denied-result helper) cannot replay an
            # old successful preparation after the successor is PLANNED.  Both
            # authorities are attempted before either result is evaluated, so
            # a public denial cannot bypass the captured core fence.
            fresh = None
            core_fresh = None
            public_failed = False
            core_failed = False
            try:
                fresh = _prepare(
                    _preparer_type(root),
                    successor_run_id=successor_run_id,
                    activation=activation,
                )
            except Exception:
                public_failed = True
            try:
                core_fresh = _prepare_current(
                    _preparer_type(root), root, activation
                )
            except Exception:
                core_failed = True
            if (
                public_failed
                or core_failed
                or type(fresh) is not _preparation_type
                or type(core_fresh) is not _preparation_type
                or not fresh.resume_execution_prepared
                or not core_fresh.resume_execution_prepared
                or fresh.preparation_bytes != preparation.preparation_bytes
                or fresh.preparation_sha256 != preparation.preparation_sha256
                or core_fresh.preparation_bytes != preparation.preparation_bytes
                or core_fresh.preparation_sha256 != preparation.preparation_sha256
                or core_fresh.preparation_bytes != fresh.preparation_bytes
                or core_fresh.preparation_sha256 != fresh.preparation_sha256
            ):
                raise ValueError("preparation authorities drifted")
            _preparation_validate(fresh)
            _preparation_validate(core_fresh)
            _assert_directory_chain(chain, label="B.8 post-preparation")

            intent_rel = f"runs/{successor_run_id}/resume_execution_handoff.intent.json"
            try:
                (root / intent_rel).lstat()
            except FileNotFoundError:
                intent_bytes = None
            else:
                intent_bytes, _ = _read_guarded(root, intent_rel)
            intent_was_present = intent_bytes is not None

            if intent_bytes is None:
                source = _store_load(
                    _store_type(root), run_id=activation.source_run_id
                )["canonical_state"]
                _source_validate(source, activation)
                intent = _intent_factory(
                    successor_run_id,
                    preparation,
                    _now(),
                    source["state_version"],
                )
                intent_bytes = _canonical_bytes(intent)
                with _bind_directories(chain):
                    _assert_directory_chain(chain, label="B.8 pre-intent")
                    # Reuse the sealed B.7 core as the last observable
                    # successor-authority fence before the first B.8 write.
                    # It re-reads and validates the canonical State, Journal,
                    # tail anchor, running Lock, activation intent, directory
                    # identities, and complete preparation bindings.
                    def validate_publication_precondition() -> None:
                        prewrite_fresh = _prepare_current(
                            _preparer_type(root), root, activation
                        )
                        if (
                            type(prewrite_fresh) is not _preparation_type
                            or not prewrite_fresh.resume_execution_prepared
                            or prewrite_fresh.preparation_bytes
                            != preparation.preparation_bytes
                            or prewrite_fresh.preparation_sha256
                            != preparation.preparation_sha256
                            or prewrite_fresh.preparation_bytes != fresh.preparation_bytes
                            or prewrite_fresh.preparation_sha256
                            != fresh.preparation_sha256
                            or prewrite_fresh.preparation_bytes
                            != core_fresh.preparation_bytes
                            or prewrite_fresh.preparation_sha256
                            != core_fresh.preparation_sha256
                        ):
                            raise ValueError("pre-intent successor authority drifted")
                        _preparation_validate(prewrite_fresh)
                        _assert_directory_chain(
                            chain, label="B.8 pre-intent authority"
                        )

                    # Establish the baseline, then require the sealed publisher
                    # to run the same complete authority proof as its immediate
                    # observable write precondition.
                    validate_publication_precondition()
                    _publish_authority(
                        root,
                        intent_rel,
                        intent_bytes,
                        validate_publication_precondition,
                        _write_exclusive,
                    )
                    _assert_directory_chain(chain, label="B.8 post-intent")
            else:
                intent = _intent_validate(
                    intent_bytes, successor_run_id, activation, preparation
                )

            current_intent, _ = _read_guarded(root, intent_rel)
            if current_intent != intent_bytes:
                raise ValueError("handoff intent changed")
            intent = _intent_validate(
                current_intent, successor_run_id, activation, preparation
            )
            source = _store_load(
                _store_type(root), run_id=activation.source_run_id
            )["canonical_state"]
            _source_validate(source, activation)
            if source.get("state_version") != intent["source_state_version"]:
                raise ValueError("source State version drifted")
            _assert_directory_chain(chain, label="B.8 pre-transition")

            store = _store_type(root)
            state = _store_load(store, run_id=successor_run_id)["canonical_state"]
            if state.get("status") == "CREATED" and state.get("state_version") == 0:
                if intent_was_present:
                    raise ValueError("intent-only handoff requires explicit recovery")
                _active_entries(root)
                with _bind_directories(chain):
                    _assert_directory_chain(chain, label="B.8 transition")
                    _transition_authority(store, intent, intent_bytes, _store_transition)
                    _assert_directory_chain(chain, label="B.8 post-transition")
            return _result_authority(
                root,
                intent,
                intent_bytes,
                chain,
                _store_type,
                _store_load,
                _read_guarded,
                _assert_directory_chain,
                _active_entries,
                _validate_lock,
                _store_validate_snapshot,
                _evidence_authority,
                _intent_validate,
                activation,
                _source_validate,
            )
        except Exception:
            return _denied_factory()

    @staticmethod
    def _make_intent(
        run_id: str,
        preparation: ResumeExecutionPreparation,
        timestamp: str,
        source_state_version: int,
    ) -> dict[str, Any]:
        return {
            "schema_version": RESUME_EXECUTION_HANDOFF_INTENT_SCHEMA_VERSION,
            "successor_run_id": run_id,
            "source_run_id": preparation.source_run_id,
            "preparation_sha256": preparation.preparation_sha256,
            "activation_sha256": preparation.activation_sha256,
            "activation_intent_sha256": preparation.intent_sha256,
            "source_admission_sha256": preparation.source_admission_sha256,
            "source_state_version": source_state_version,
            "allocation_token": preparation.allocation_token,
            "lock_token": preparation.lock_token,
            "predecessor_state_version": 0,
            "target_state_version": 1,
            "target_status": "PLANNED",
            "plan_fingerprint": preparation.plan_fingerprint,
            "input_descriptor_sha256": preparation.input_descriptor_sha256,
            "task_plan_sha256": preparation.task_plan_sha256,
            "required_task_ids": list(preparation.required_task_ids),
            "mutation_timestamp": timestamp,
        }

    @classmethod
    def _validate_intent(
        cls,
        data: bytes,
        run_id: str,
        activation: ExplicitResumeActivationResult,
        preparation: ResumeExecutionPreparation,
        canonical_bytes=_canonical,
    ) -> dict[str, Any]:
        value = json.loads(data.decode("utf-8"))
        if not isinstance(value, dict) or canonical_bytes(value) != data:
            raise ValueError("handoff intent is not canonical")
        timestamp = value.get("mutation_timestamp")
        if type(timestamp) is not str:
            raise ValueError("handoff timestamp is invalid")
        source_state_version = value.get("source_state_version")
        if type(source_state_version) is not int or source_state_version < 0:
            raise ValueError("source State version is invalid")
        expected = cls._make_intent(
            run_id, preparation, timestamp, source_state_version
        )
        if value != expected or value["activation_sha256"] != activation.activation_sha256:
            raise ValueError("handoff intent bindings drifted")
        return value

    @staticmethod
    def _validate_source(
        state: Mapping[str, Any], activation: ExplicitResumeActivationResult
    ) -> None:
        context = state.get("context")
        if (
            state.get("run_id") != activation.source_run_id
            or state.get("status") != "COMPLETED"
            or state.get("plan_fingerprint") != activation.plan_fingerprint
            or not isinstance(context, Mapping)
            or context.get("resolved_input_descriptor_sha256")
            != activation.input_descriptor_sha256
        ):
            raise ValueError("source State does not bind activation")

    @staticmethod
    def _transition(
        store: StateStore,
        intent: Mapping[str, Any],
        intent_bytes: bytes,
        transition=StateStore.transition_status,
        sha256=_sha,
    ) -> None:
        run_id = intent["successor_run_id"]
        token = intent["lock_token"]
        transition(
            store,
            run_id=run_id,
            expected_lock_token=token,
            expected_status="CREATED",
            expected_state_version=0,
            operation_id=(
                f"run:{run_id}:transition:v0:CREATED:PLANNED:decision:{token}"
            ),
            mutation_timestamp=intent["mutation_timestamp"],
            payload={
                "next_status": "PLANNED",
                "metadata": {
                    "resume_execution_handoff_intent_sha256": sha256(intent_bytes),
                    "resume_execution_preparation_sha256": intent["preparation_sha256"],
                },
                "completion_evidence": None,
                "transition_kind": "decision",
                "decision_token": token,
            },
        )

    @classmethod
    def _evidence(
        cls,
        root: Path,
        intent: Mapping[str, Any],
        intent_bytes: bytes,
        chain: tuple[tuple[Path, int, int], ...],
        store_type=StateStore,
        store_load=StateStore.load,
        read_guarded=_B1_READ_GUARDED,
        assert_directory_chain=ExplicitResumeActivation._assert_directory_chain,
        active_entries=validate_active_run_control_entries,
        validate_lock=validate_active_run_lock_snapshot,
        validate_snapshot=StateStore.validate_authority_snapshot_bytes,
        intent_validate=None,
        canonical_bytes=_canonical,
        sha256=_sha,
    ) -> dict[str, Any]:
        run_id = intent["successor_run_id"]
        assert_directory_chain(chain, label="B.8 evidence")
        if set(entry.name for entry in (root / "runs" / run_id).iterdir()) != _SUCCESSOR_ENTRIES:
            raise ValueError("successor contains non-handoff entries")
        active_before = active_entries(root)
        current_intent, _ = read_guarded(
            root, f"runs/{run_id}/resume_execution_handoff.intent.json"
        )
        if current_intent != intent_bytes:
            raise ValueError("handoff intent changed at evidence")
        if intent_validate is None:
            raise ValueError("intent authority is unavailable")
        intent_validate(current_intent, run_id, _ActivationView(intent), _PreparationView(intent))
        store = store_type(root)
        authority = store_load(store, run_id=run_id)
        state_bytes, _ = read_guarded(root, f"runs/{run_id}/state.json")
        journal_bytes, _ = read_guarded(root, f"runs/{run_id}/state_journal.jsonl")
        anchor_bytes, _ = read_guarded(root, f"runs/{run_id}/state_journal_tail.json")
        byte_authority = validate_snapshot(
            store,
            run_id=run_id,
            state_bytes=state_bytes,
            journal_bytes=journal_bytes,
            anchor_bytes=anchor_bytes,
        )
        state = byte_authority["canonical_state"]
        if authority["canonical_state"] != state:
            raise ValueError("StateStore and guarded bytes disagree")
        context = state.get("context")
        resume_activation = context.get("resume_activation") if isinstance(context, Mapping) else None
        transition_metadata = context.get("last_transition_metadata") if isinstance(context, Mapping) else None
        expected_operation = (
            f"run:{run_id}:transition:v0:CREATED:PLANNED:decision:{intent['lock_token']}"
        )
        if (
            state.get("status") != "PLANNED"
            or state.get("state_version") != 1
            or state.get("allocation_token") != intent["allocation_token"]
            or state.get("plan_fingerprint") != intent["plan_fingerprint"]
            or not isinstance(resume_activation, Mapping)
            or resume_activation.get("input_descriptor_sha256") != intent["input_descriptor_sha256"]
            or resume_activation.get("intent_sha256") != intent["activation_intent_sha256"]
            or resume_activation.get("source_admission_sha256") != intent["source_admission_sha256"]
            or resume_activation.get("source_run_id") != intent["source_run_id"]
            or not isinstance(transition_metadata, Mapping)
            or transition_metadata.get("resume_execution_handoff_intent_sha256") != sha256(intent_bytes)
            or transition_metadata.get("resume_execution_preparation_sha256") != intent["preparation_sha256"]
            or state.get("last_operation_id") != expected_operation
            or state.get("last_operation_kind") != "status_transition"
            or sha256(canonical_bytes(_plain(state.get("task_plan"))))
            != intent["task_plan_sha256"]
            or any(
                state.get(name)
                for name in (
                    "task_status",
                    "task_attempts",
                    "completed_tasks",
                    "failed_tasks",
                )
            )
        ):
            raise ValueError("handoff successor State is invalid")
        lock_bytes, _ = read_guarded(root, "runs/.active_run.lock")
        lock = validate_lock(lock_bytes)
        if (
            lock.get("phase") != "running"
            or lock.get("run_id") != run_id
            or lock.get("reserved_run_id") != run_id
            or lock.get("allocation_token") != intent["allocation_token"]
            or lock.get("lock_token") != intent["lock_token"]
            or lock.get("task_id") != "explicit_resume_activation"
        ):
            raise ValueError("running Lock does not bind handoff")
        if active_entries(root) != active_before:
            raise ValueError("Active Run authority changed during evidence")
        assert_directory_chain(chain, label="B.8 evidence")
        return {
            "state_sha256": sha256(state_bytes),
            "journal_sha256": sha256(journal_bytes),
            "journal_anchor_sha256": sha256(anchor_bytes),
            "lock_sha256": sha256(lock_bytes),
        }

    @classmethod
    def _result(
        cls,
        root: Path,
        intent: Mapping[str, Any],
        intent_bytes: bytes,
        chain: tuple[tuple[Path, int, int], ...],
        store_type=StateStore,
        store_load=StateStore.load,
        read_guarded=_B1_READ_GUARDED,
        assert_directory_chain=ExplicitResumeActivation._assert_directory_chain,
        active_entries=validate_active_run_control_entries,
        validate_lock=validate_active_run_lock_snapshot,
        validate_snapshot=StateStore.validate_authority_snapshot_bytes,
        evidence_authority=None,
        intent_validate=None,
        activation: ExplicitResumeActivationResult | None = None,
        source_validate=None,
        issue_result=None,
        canonical_bytes=_canonical,
        sha256=_sha,
        schema_version=RESUME_EXECUTION_HANDOFF_SCHEMA_VERSION,
        ready_status="resume_execution_handed_off",
    ) -> ResumeExecutionHandoffResult:
        if (
            evidence_authority is None
            or intent_validate is None
            or type(activation) is not ExplicitResumeActivationResult
            or source_validate is None
        ):
            raise ValueError("result authority is unavailable")
        if issue_result is None:
            issue_result = ResumeExecutionHandoffResult._issue_internal
        source_before = store_load(
            store_type(root), run_id=intent["source_run_id"]
        )["canonical_state"]
        source_validate(source_before, activation)
        if source_before.get("state_version") != intent["source_state_version"]:
            raise ValueError("source State drifted before result")
        first = evidence_authority(
            root, intent, intent_bytes, chain, store_type, store_load, read_guarded,
            assert_directory_chain, active_entries, validate_lock,
            validate_snapshot, intent_validate,
        )
        source_middle = store_load(
            store_type(root), run_id=intent["source_run_id"]
        )["canonical_state"]
        source_validate(source_middle, activation)
        if source_middle != source_before:
            raise ValueError("source State changed during result")
        second = evidence_authority(
            root, intent, intent_bytes, chain, store_type, store_load, read_guarded,
            assert_directory_chain, active_entries, validate_lock,
            validate_snapshot, intent_validate,
        )
        if first != second:
            raise ValueError("handoff authority changed before result")
        source_after = store_load(
            store_type(root), run_id=intent["source_run_id"]
        )["canonical_state"]
        source_validate(source_after, activation)
        if source_after != source_middle:
            raise ValueError("source State changed before result issuance")
        bindings = {
            "successor_run_id": intent["successor_run_id"],
            "source_run_id": intent["source_run_id"],
            "handoff_intent_sha256": sha256(intent_bytes),
            "preparation_sha256": intent["preparation_sha256"],
            "activation_sha256": intent["activation_sha256"],
            "activation_intent_sha256": intent["activation_intent_sha256"],
            "source_admission_sha256": intent["source_admission_sha256"],
            "source_state_version": intent["source_state_version"],
            "allocation_token": intent["allocation_token"],
            "lock_token": intent["lock_token"],
            "state_version": 1,
            "plan_fingerprint": intent["plan_fingerprint"],
            "input_descriptor_sha256": intent["input_descriptor_sha256"],
            "task_plan_sha256": intent["task_plan_sha256"],
            "required_task_ids": tuple(intent["required_task_ids"]),
            **first,
        }
        data = canonical_bytes(
            {"schema_version": schema_version, "status": ready_status,
             **{**bindings, "required_task_ids": list(bindings["required_task_ids"])}}
        )
        return issue_result(
            {"status": ready_status, "handoff_bytes": data, "handoff_sha256": sha256(data), **bindings}
        )


class _ActivationView:
    """Internal immutable shape used only to revalidate persisted intent fields."""

    def __init__(self, intent: Mapping[str, Any]) -> None:
        self.activation_sha256 = intent["activation_sha256"]


class _PreparationView:
    def __init__(self, intent: Mapping[str, Any]) -> None:
        self.source_run_id = intent["source_run_id"]
        self.preparation_sha256 = intent["preparation_sha256"]
        self.activation_sha256 = intent["activation_sha256"]
        self.intent_sha256 = intent["activation_intent_sha256"]
        self.source_admission_sha256 = intent["source_admission_sha256"]
        self.source_state_version = intent["source_state_version"]
        self.allocation_token = intent["allocation_token"]
        self.lock_token = intent["lock_token"]
        self.plan_fingerprint = intent["plan_fingerprint"]
        self.input_descriptor_sha256 = intent["input_descriptor_sha256"]
        self.task_plan_sha256 = intent["task_plan_sha256"]
        self.required_task_ids = tuple(intent["required_task_ids"])


def _seal_public_handoff(
    authority: Any,
    intent_factory: Any,
    intent_validate: Any,
    source_validate: Any,
    transition_authority: Any,
    result_authority: Any,
    evidence_authority: Any,
) -> Any:
    """Expose an exact three-evidence API while retaining fixed authorities."""

    def handoff(
        self: ResumeExecutionHandoff,
        *,
        successor_run_id: str,
        activation: ExplicitResumeActivationResult,
        preparation: ResumeExecutionPreparation,
    ) -> ResumeExecutionHandoffResult:
        return authority(
            self,
            successor_run_id=successor_run_id,
            activation=activation,
            preparation=preparation,
            _intent_factory=intent_factory,
            _intent_validate=intent_validate,
            _source_validate=source_validate,
            _transition_authority=transition_authority,
            _result_authority=result_authority,
            _evidence_authority=evidence_authority,
        )

    return handoff


def _copy_function_defaults(function: Any) -> Any:
    """Copy a function so later mutation of the source kwdefaults is inert."""

    copied = types.FunctionType(
        function.__code__,
        function.__globals__,
        name=function.__name__,
        argdefs=function.__defaults__,
        closure=function.__closure__,
    )
    copied.__kwdefaults__ = dict(function.__kwdefaults__ or {})
    copied.__annotations__ = dict(getattr(function, "__annotations__", {}))
    return copied


_HANDOFF_AUTHORITY = ResumeExecutionHandoff.handoff
_PUBLIC_HANDOFF_AUTHORITY = _copy_function_defaults(_HANDOFF_AUTHORITY)

ResumeExecutionHandoff.handoff = _seal_public_handoff(  # type: ignore[method-assign]
    _PUBLIC_HANDOFF_AUTHORITY,
    ResumeExecutionHandoff._make_intent,
    ResumeExecutionHandoff._validate_intent,
    ResumeExecutionHandoff._validate_source,
    ResumeExecutionHandoff._transition,
    ResumeExecutionHandoff._result,
    ResumeExecutionHandoff._evidence,
)


def _seal_public_function(handoff_type: Any, handoff_authority: Any) -> Any:
    def handoff_resume_execution(
        project_root: Path,
        *,
        successor_run_id: str,
        activation: ExplicitResumeActivationResult,
        preparation: ResumeExecutionPreparation,
    ) -> ResumeExecutionHandoffResult:
        return handoff_authority(
            handoff_type(project_root),
            successor_run_id=successor_run_id,
            activation=activation,
            preparation=preparation,
        )

    return handoff_resume_execution


handoff_resume_execution = _seal_public_function(
    ResumeExecutionHandoff, ResumeExecutionHandoff.handoff
)

# The supported callables above retain the authority closures they need.  Do
# not leave the registries, register/discard capabilities, or universal issuer
# reachable as module attributes that a caller could repurpose.
del _RESULT_IS_OFFICIAL
del _REGISTER_RESULT
del _DISCARD_RESULT
del _RESULT_ISSUER
del _DENIED_FACTORY
del _PUBLIC_HANDOFF_AUTHORITY
del _new_result_registry
del _new_result_issuer
del _new_denied_factory
del _copy_function_defaults
