"""Read-only currentness observation over one Safe Reuse consumption."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .safe_reuse import SafeReuseDecision
from .safe_reuse_consumer import SafeReuseConsumer, SafeReuseConsumption


SAFE_REUSE_STALENESS_OBSERVATION_SCHEMA_VERSION = (
    "inspection_safe_reuse_staleness_observation_v1"
)
_NOT_CURRENT = "reuse_not_current"
_CURRENT = "reuse_current"


def _canonical_json_bytes(value: Mapping[str, str]) -> bytes:
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


def _observation_bytes(status: str) -> bytes:
    return _canonical_json_bytes(
        {
            "schema_version": SAFE_REUSE_STALENESS_OBSERVATION_SCHEMA_VERSION,
            "status": status,
        }
    )


def _is_deeply_frozen(value: Any) -> bool:
    if isinstance(value, MappingProxyType):
        return all(isinstance(key, str) and _is_deeply_frozen(item) for key, item in value.items())
    if isinstance(value, tuple):
        return all(_is_deeply_frozen(item) for item in value)
    if isinstance(value, frozenset):
        return all(_is_deeply_frozen(item) for item in value)
    return value is None or isinstance(value, (bool, int, float, str, bytes))


def _not_current() -> "SafeReuseStalenessObservation":
    """Return the single zero-authority failure shape."""

    result = object.__new__(SafeReuseStalenessObservation)
    data = _observation_bytes(_NOT_CURRENT)
    object.__setattr__(result, "status", _NOT_CURRENT)
    object.__setattr__(result, "observation_bytes", data)
    object.__setattr__(result, "observation_sha256", hashlib.sha256(data).hexdigest())
    result.__post_init__()
    return result


@dataclass(frozen=True, init=False, slots=True)
class SafeReuseStalenessObservation:
    """An immutable, non-authorizing currentness observation result."""

    status: str
    observation_bytes: bytes
    observation_sha256: str

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError(
            "SafeReuseStalenessObservation is created only by SafeReuseStalenessObserver"
        )

    def __post_init__(self) -> None:
        if self.status not in {_CURRENT, _NOT_CURRENT}:
            raise ValueError("unknown SafeReuseStalenessObservation status")
        if type(self.status) is not str or type(self.observation_bytes) is not bytes:
            raise ValueError("observation bytes must be immutable bytes")
        if (
            type(self.observation_sha256) is not str
            or hashlib.sha256(self.observation_bytes).hexdigest() != self.observation_sha256
        ):
            raise ValueError("observation SHA-256 does not match observation bytes")
        if self.observation_bytes != _observation_bytes(self.status):
            raise ValueError("observation bytes do not match status")

    @property
    def reuse_current(self) -> bool:
        return self.status == _CURRENT

    def to_dict(self) -> dict[str, str | bytes]:
        return {
            "status": self.status,
            "observation_bytes": self.observation_bytes,
            "observation_sha256": self.observation_sha256,
        }


def _canonical_item(decision: SafeReuseDecision, artifact_path: object) -> Mapping[str, Any] | None:
    if type(artifact_path) is not str:
        return None
    matches = tuple(item for item in decision.inventory if item.get("path") == artifact_path)
    return matches[0] if len(matches) == 1 else None


def _valid_consumption(
    consumption: object,
    *,
    decision: SafeReuseDecision,
    artifact_path: object,
) -> bool:
    if (
        type(consumption) is not SafeReuseConsumption
        or type(artifact_path) is not str
        or type(decision.inventory_bytes) is not bytes
        or type(decision.decision_bytes) is not bytes
        or type(decision.inventory_sha256) is not str
        or type(decision.decision_sha256) is not str
        or type(decision.state_version) is not int
        or type(decision.plan_fingerprint) is not str
        or type(decision.input_descriptor_sha256) is not str
    ):
        return False
    if consumption.status != "reuse_consumed" or consumption.denial_codes != ():
        return False
    item = _canonical_item(decision, artifact_path)
    if item is None:
        return False
    if type(consumption.content) is not bytes:
        return False
    if not isinstance(consumption.artifact, MappingProxyType) or not _is_deeply_frozen(
        consumption.artifact
    ):
        return False
    if consumption.artifact != item:
        return False
    if (
        type(consumption.inventory_sha256) is not str
        or type(consumption.state_version) is not int
        or type(consumption.plan_fingerprint) is not str
        or type(consumption.input_descriptor_sha256) is not str
        or consumption.inventory_sha256 != decision.inventory_sha256
        or consumption.state_version != decision.state_version
        or consumption.plan_fingerprint != decision.plan_fingerprint
        or consumption.input_descriptor_sha256 != decision.input_descriptor_sha256
    ):
        return False
    return (
        len(consumption.content) == item.get("size_bytes")
        and hashlib.sha256(consumption.content).hexdigest() == item.get("sha256")
    )


def _same_consumption(left: SafeReuseConsumption, right: SafeReuseConsumption) -> bool:
    return (
        left.status == right.status == "reuse_consumed"
        and left.denial_codes == right.denial_codes == ()
        and left.content == right.content
        and left.artifact == right.artifact
        and left.inventory_sha256 == right.inventory_sha256
        and left.state_version == right.state_version
        and left.plan_fingerprint == right.plan_fingerprint
        and left.input_descriptor_sha256 == right.input_descriptor_sha256
    )


class SafeReuseStalenessObserver:
    """Observe whether a B.3 consumption still matches the current snapshot."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).absolute()

    def observe(
        self,
        *,
        decision: SafeReuseDecision,
        consumption: SafeReuseConsumption,
        artifact_path: str,
    ) -> SafeReuseStalenessObservation:
        try:
            # Re-consume first. B.4 deliberately owns neither authorization nor file IO.
            current = SafeReuseConsumer(self.project_root).consume(
                decision=decision,
                artifact_path=artifact_path,
            )
            if type(decision) is not SafeReuseDecision or not decision.reuse_allowed:
                return _not_current()
            decision.__post_init__()
            if not _valid_consumption(
                consumption,
                decision=decision,
                artifact_path=artifact_path,
            ) or not _valid_consumption(
                current,
                decision=decision,
                artifact_path=artifact_path,
            ):
                return _not_current()
            if not _same_consumption(consumption, current):
                return _not_current()
            result = object.__new__(SafeReuseStalenessObservation)
            data = _observation_bytes(_CURRENT)
            object.__setattr__(result, "status", _CURRENT)
            object.__setattr__(result, "observation_bytes", data)
            object.__setattr__(result, "observation_sha256", hashlib.sha256(data).hexdigest())
            result.__post_init__()
            return result
        except Exception:
            return _not_current()


__all__ = [
    "SAFE_REUSE_STALENESS_OBSERVATION_SCHEMA_VERSION",
    "SafeReuseStalenessObservation",
    "SafeReuseStalenessObserver",
]
