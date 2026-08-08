"""Read-only admission proof for a future explicit Resume."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from . import safe_reuse, safe_reuse_consumer, safe_reuse_staleness
from .safe_reuse import SafeReuseDecision
from .safe_reuse_consumer import SafeReuseConsumption
from .safe_reuse_staleness import SafeReuseStalenessObservation


_SCHEMA = "inspection_explicit_resume_admission_v1"
_ADMISSIBLE = "resume_admissible"
_NOT_ADMISSIBLE = "resume_not_admissible"
_RUN_ID_RE = re.compile(r"run_[0-9]{3,}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_B2_AUTHORIZER_TYPE = safe_reuse.SafeReuseAuthorizer
_B2_AUTHORIZE = _B2_AUTHORIZER_TYPE.authorize
_B3_CONSUMER_TYPE = safe_reuse_consumer.SafeReuseConsumer
_B3_CONSUME = _B3_CONSUMER_TYPE.consume
_B4_OBSERVER_TYPE = safe_reuse_staleness.SafeReuseStalenessObserver
_B4_OBSERVE = _B4_OBSERVER_TYPE.observe


def _canonical_bytes(status: str, bindings: Mapping[str, Any] | None = None) -> bytes:
    document: dict[str, Any] = {"schema_version": _SCHEMA, "status": status}
    if status == _ADMISSIBLE:
        if bindings is None:
            raise ValueError("admissible results require bindings")
        document["bindings"] = {
            "run_id": bindings.get("run_id"),
            "decision_bytes_hex": bytes(bindings.get("decision_bytes", b"")).hex(),
            "decision_sha256": bindings.get("decision_sha256"),
            "inventory_sha256": bindings.get("inventory_sha256"),
            "state_version": bindings.get("state_version"),
            "plan_fingerprint": bindings.get("plan_fingerprint"),
            "input_descriptor_sha256": bindings.get("input_descriptor_sha256"),
            "observations_sha256": bindings.get("observations_sha256"),
        }
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _not_admissible() -> "ExplicitResumeAdmissionResult":
    data = _canonical_bytes(_NOT_ADMISSIBLE)
    result = object.__new__(ExplicitResumeAdmissionResult)
    object.__setattr__(result, "status", _NOT_ADMISSIBLE)
    object.__setattr__(result, "admission_bytes", data)
    object.__setattr__(result, "admission_sha256", hashlib.sha256(data).hexdigest())
    for name in (
        "run_id",
        "decision_bytes",
        "decision_sha256",
        "inventory_sha256",
        "state_version",
        "plan_fingerprint",
        "input_descriptor_sha256",
        "observations_sha256",
    ):
        object.__setattr__(result, name, None)
    result.__post_init__()
    return result


def _observation_digest(observations: list[tuple[str, str, str]]) -> str:
    payload = json.dumps(observations, ensure_ascii=True, separators=(",", ":"), sort_keys=False).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, init=False, slots=True)
class ExplicitResumeAdmissionResult:
    status: str
    admission_bytes: bytes
    admission_sha256: str
    run_id: str | None
    decision_bytes: bytes | None
    decision_sha256: str | None
    inventory_sha256: str | None
    state_version: int | None
    plan_fingerprint: str | None
    input_descriptor_sha256: str | None
    observations_sha256: str | None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("ExplicitResumeAdmissionResult is created only by ExplicitResumeAdmission")

    def __post_init__(self) -> None:
        if self.status not in {_ADMISSIBLE, _NOT_ADMISSIBLE}:
            raise ValueError("unknown admission status")
        if type(self.admission_bytes) is not bytes or type(self.admission_sha256) is not str:
            raise ValueError("admission bytes must be immutable bytes")
        if hashlib.sha256(self.admission_bytes).hexdigest() != self.admission_sha256:
            raise ValueError("admission SHA does not match bytes")
        bindings = {
            "run_id": self.run_id,
            "decision_bytes": self.decision_bytes,
            "decision_sha256": self.decision_sha256,
            "inventory_sha256": self.inventory_sha256,
            "state_version": self.state_version,
            "plan_fingerprint": self.plan_fingerprint,
            "input_descriptor_sha256": self.input_descriptor_sha256,
            "observations_sha256": self.observations_sha256,
        }
        if self.admission_bytes != _canonical_bytes(self.status, bindings):
            raise ValueError("admission bytes do not match status or bindings")
        fields = (
            self.run_id,
            self.decision_bytes,
            self.decision_sha256,
            self.inventory_sha256,
            self.state_version,
            self.plan_fingerprint,
            self.input_descriptor_sha256,
            self.observations_sha256,
        )
        if self.status == _NOT_ADMISSIBLE and any(value is not None for value in fields):
            raise ValueError("not admissible result must not expose bindings")
        if self.status == _ADMISSIBLE and (
            type(self.run_id) is not str
            or _RUN_ID_RE.fullmatch(self.run_id) is None
            or type(self.decision_bytes) is not bytes
            or type(self.decision_sha256) is not str
            or _SHA256_RE.fullmatch(self.decision_sha256) is None
            or hashlib.sha256(self.decision_bytes).hexdigest() != self.decision_sha256
            or type(self.inventory_sha256) is not str
            or _SHA256_RE.fullmatch(self.inventory_sha256) is None
            or type(self.state_version) is not int
            or self.state_version < 0
            or type(self.plan_fingerprint) is not str
            or _SHA256_RE.fullmatch(self.plan_fingerprint) is None
            or type(self.input_descriptor_sha256) is not str
            or _SHA256_RE.fullmatch(self.input_descriptor_sha256) is None
            or type(self.observations_sha256) is not str
            or _SHA256_RE.fullmatch(self.observations_sha256) is None
        ):
            raise ValueError("admissible result requires complete canonical bindings")

    @property
    def resume_admissible(self) -> bool:
        return self.status == _ADMISSIBLE

    def to_dict(self) -> dict[str, Any]:
        return {
            name: getattr(self, name)
            for name in (
                "status", "admission_bytes", "admission_sha256", "run_id", "decision_bytes",
                "decision_sha256", "inventory_sha256", "state_version", "plan_fingerprint",
                "input_descriptor_sha256", "observations_sha256",
            )
        }


class ExplicitResumeAdmission:
    """Orchestrate B.2 authorization, B.3 consumption, and B.4 observation only."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).absolute()

    def admit(self, *, run_id: str) -> ExplicitResumeAdmissionResult:
        try:
            decision = _B2_AUTHORIZE(
                _B2_AUTHORIZER_TYPE(self.project_root),
                run_id=run_id,
            )
            if type(run_id) is not str or _RUN_ID_RE.fullmatch(run_id) is None:
                return _not_admissible()
            if (
                type(decision) is not SafeReuseDecision
                or not decision.reuse_allowed
                or decision.run_id != run_id
            ):
                return _not_admissible()
            decision.__post_init__()
            inventory = tuple(decision.inventory)
            paths = tuple(item.get("path") for item in inventory)
            if not inventory or any(type(path) is not str for path in paths):
                return _not_admissible()
            if len(set(paths)) != len(paths):
                return _not_admissible()
            if paths != tuple(sorted(paths)):
                return _not_admissible()
            consumer = _B3_CONSUMER_TYPE(self.project_root)
            observer = _B4_OBSERVER_TYPE(self.project_root)
            observations: list[tuple[str, str, str]] = []
            for path in paths:
                consumed = _B3_CONSUME(
                    consumer,
                    decision=decision,
                    artifact_path=path,
                )
                if type(consumed) is not SafeReuseConsumption or consumed.status != "reuse_consumed":
                    return _not_admissible()
                observed = _B4_OBSERVE(
                    observer,
                    decision=decision,
                    consumption=consumed,
                    artifact_path=path,
                )
                if type(observed) is not SafeReuseStalenessObservation or observed.status != "reuse_current":
                    return _not_admissible()
                observed.__post_init__()
                observations.append((path, observed.status, observed.observation_sha256))
            if len(observations) != len(inventory):
                return _not_admissible()
            bindings = {
                "run_id": decision.run_id,
                "decision_bytes": decision.decision_bytes,
                "decision_sha256": decision.decision_sha256,
                "inventory_sha256": decision.inventory_sha256,
                "state_version": decision.state_version,
                "plan_fingerprint": decision.plan_fingerprint,
                "input_descriptor_sha256": decision.input_descriptor_sha256,
                "observations_sha256": _observation_digest(observations),
            }
            data = _canonical_bytes(_ADMISSIBLE, bindings)
            result = object.__new__(ExplicitResumeAdmissionResult)
            object.__setattr__(result, "status", _ADMISSIBLE)
            object.__setattr__(result, "admission_bytes", data)
            object.__setattr__(result, "admission_sha256", hashlib.sha256(data).hexdigest())
            for name, value in bindings.items():
                object.__setattr__(result, name, value)
            result.__post_init__()
            return result
        except Exception:
            return _not_admissible()


__all__ = ["ExplicitResumeAdmission", "ExplicitResumeAdmissionResult"]
