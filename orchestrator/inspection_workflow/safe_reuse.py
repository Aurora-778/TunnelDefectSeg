from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType, SimpleNamespace
from typing import Any, Mapping, Optional

from .artifact_resolver import (
    INVENTORY_SCHEMA_VERSION,
    ArtifactResolver,
)


SAFE_REUSE_DECISION_SCHEMA_VERSION = "inspection_safe_reuse_decision_v1"
_RUN_ID_RE = re.compile(r"run_[0-9]{3,}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_PUBLICATION_OPERATION_RE = re.compile(r"publication:pub_[0-9a-f]{24}")
_ALLOWED_RESOLUTION_STATUSES = {
    "complete",
    "incomplete",
    "stale",
    "recovery_required",
    "invalid",
}
_INVENTORY_FIELDS = {
    "task_id",
    "artifact_role",
    "path",
    "size_bytes",
    "sha256",
    "producer_operation",
    "resulting_state_version",
    "plan_fingerprint",
    "input_descriptor_sha256",
}
_FIXED_ARTIFACT_BINDINGS = {
    "artifacts/claim_decision.json": ("claim_decision", "phase_a_claim_gate"),
    "artifacts/comparison_evidence.csv": (
        "comparison_evidence",
        "phase_a_comparison_evidence",
    ),
    "artifacts/comparison_evidence_manifest.json": (
        "comparison_evidence_manifest",
        "phase_a_comparison_evidence",
    ),
    "staging/disease_growth_analysis_report.md": (
        "staging_report",
        "phase_a_growth_report",
    ),
    "staging/disease_growth_analysis_summary.md": (
        "staging_report",
        "phase_a_growth_report",
    ),
    "staging/memory_agent_report.md": ("staging_report", "phase_a_memory_report"),
    "staging/disease_memory_bank_summary.md": (
        "staging_report",
        "phase_a_memory_report",
    ),
    "staging/disease_engineering_report.md": (
        "staging_report",
        "phase_a_engineering_claim_report",
    ),
    "staging/disease_engineering_report_summary.md": (
        "staging_report",
        "phase_a_engineering_claim_report",
    ),
    "staging/priority_recheck_list.csv": (
        "staging_report",
        "phase_a_claim_visualization",
    ),
    "staging/visualization_report.md": (
        "staging_report",
        "phase_a_claim_visualization",
    ),
    "staging/visualization_summary.md": (
        "staging_report",
        "phase_a_claim_visualization",
    ),
    "staging/recheck_list_report.md": (
        "staging_report",
        "phase_a_claim_visualization",
    ),
}
_REQUIRED_FIXED_ARTIFACT_PATHS = frozenset(_FIXED_ARTIFACT_BINDINGS)
_FIXED_ROLE_PATHS = {
    role: path
    for path, (role, _) in _FIXED_ARTIFACT_BINDINGS.items()
    if role != "staging_report"
}
_STAGING_REPORT_PATHS = {
    path
    for path, (role, _) in _FIXED_ARTIFACT_BINDINGS.items()
    if role == "staging_report"
}
_ROLE_PRODUCER_TASKS = {
    "association_artifact": "phase_a_association",
    "association_manifest": "phase_a_association",
    "association_round_artifact": "phase_a_association",
    "claim_decision": "phase_a_claim_gate",
    "comparison_evidence": "phase_a_comparison_evidence",
    "comparison_evidence_manifest": "phase_a_comparison_evidence",
    "engineering_artifact": "phase_a_comparison_evidence",
    "frame_artifact": "phase_a_comparison_evidence",
    "history_memory_context": "phase_a_association",
    "history_round_context": "phase_a_association",
    "projection_receipt": "phase_a_comparison_evidence",
    "staging_visualization": "phase_a_claim_visualization",
}
_ALLOWED_ARTIFACT_ROLES = set(_ROLE_PRODUCER_TASKS) | {
    "final_summary",
    "projection_input",
    "staging_report",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _jsonable(value[key]) for key in sorted(value)}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, frozenset):
        return sorted(_jsonable(item) for item in value)
    return value


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            _jsonable(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(value[key]) for key in sorted(value)})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return value


def _is_deeply_frozen(value: Any) -> bool:
    if isinstance(value, MappingProxyType):
        return all(_is_deeply_frozen(item) for item in value.values())
    if isinstance(value, tuple):
        return all(_is_deeply_frozen(item) for item in value)
    if isinstance(value, frozenset):
        return all(_is_deeply_frozen(item) for item in value)
    return not isinstance(value, (Mapping, list, set, bytearray))


def _decision_document(
    *,
    run_id: str,
    decision: str,
    denial_codes: tuple[str, ...],
    inventory_sha256: Optional[str],
    state_version: Optional[int],
    plan_fingerprint: Optional[str],
    input_descriptor_sha256: Optional[str],
) -> dict[str, Any]:
    return {
        "schema_version": SAFE_REUSE_DECISION_SCHEMA_VERSION,
        "run_id": run_id,
        "decision": decision,
        "denial_codes": list(denial_codes),
        "inventory_sha256": inventory_sha256,
        "state_version": state_version,
        "plan_fingerprint": plan_fingerprint,
        "input_descriptor_sha256": input_descriptor_sha256,
    }


@dataclass(frozen=True, init=False)
class SafeReuseDecision:
    """Immutable Phase B.2 authorization result.

    Instances are created only by :class:`SafeReuseAuthorizer`. A denied
    decision intentionally carries no inventory or reusable binding fields.
    """

    run_id: str
    decision: str
    denial_codes: tuple[str, ...]
    inventory: tuple[Mapping[str, Any], ...]
    inventory_bytes: Optional[bytes]
    inventory_sha256: Optional[str]
    state_version: Optional[int]
    plan_fingerprint: Optional[str]
    input_descriptor_sha256: Optional[str]
    decision_bytes: bytes
    decision_sha256: str

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("SafeReuseDecision is created only by SafeReuseAuthorizer")

    def __post_init__(self) -> None:
        if self.decision not in {"reuse_allowed", "reuse_denied"}:
            raise ValueError("unknown SafeReuseDecision decision")
        if tuple(sorted(set(self.denial_codes))) != self.denial_codes:
            raise ValueError("denial_codes must be unique and canonically ordered")
        if not isinstance(self.inventory, tuple) or not all(
            isinstance(item, MappingProxyType) and _is_deeply_frozen(item)
            for item in self.inventory
        ):
            raise ValueError("inventory must be deeply frozen")
        if _sha256(self.decision_bytes) != self.decision_sha256:
            raise ValueError("decision SHA-256 does not match decision bytes")
        expected_decision_bytes = _canonical_json_bytes(
            _decision_document(
                run_id=self.run_id,
                decision=self.decision,
                denial_codes=self.denial_codes,
                inventory_sha256=self.inventory_sha256,
                state_version=self.state_version,
                plan_fingerprint=self.plan_fingerprint,
                input_descriptor_sha256=self.input_descriptor_sha256,
            )
        )
        if self.decision_bytes != expected_decision_bytes:
            raise ValueError("decision bytes do not match decision fields")
        if self.decision == "reuse_denied":
            if not self.denial_codes:
                raise ValueError("denied decisions require a denial code")
            if self.inventory or self.inventory_bytes is not None or self.inventory_sha256 is not None:
                raise ValueError("denied decisions must not expose an inventory")
            if any(
                value is not None
                for value in (
                    self.state_version,
                    self.plan_fingerprint,
                    self.input_descriptor_sha256,
                )
            ):
                raise ValueError("denied decisions must not expose reusable bindings")
            return
        if self.denial_codes:
            raise ValueError("allowed decisions must not have denial codes")
        if not self.inventory or self.inventory_bytes is None or self.inventory_sha256 is None:
            raise ValueError("allowed decisions require a non-empty inventory")
        if _sha256(self.inventory_bytes) != self.inventory_sha256:
            raise ValueError("inventory SHA-256 does not match inventory bytes")
        canonical_inventory = json.dumps(
            _jsonable(
                {
                    "schema_version": INVENTORY_SCHEMA_VERSION,
                    "run_id": self.run_id,
                    "artifacts": [_jsonable(item) for item in self.inventory],
                }
            ),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if canonical_inventory != self.inventory_bytes:
            raise ValueError("inventory bytes do not match inventory")

    @property
    def reuse_allowed(self) -> bool:
        return self.decision == "reuse_allowed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "decision": self.decision,
            "denial_codes": list(self.denial_codes),
            "inventory": [_jsonable(item) for item in self.inventory],
            "inventory_bytes": self.inventory_bytes,
            "inventory_sha256": self.inventory_sha256,
            "state_version": self.state_version,
            "plan_fingerprint": self.plan_fingerprint,
            "input_descriptor_sha256": self.input_descriptor_sha256,
            "decision_bytes": self.decision_bytes,
            "decision_sha256": self.decision_sha256,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


def _make_decision(
    *,
    run_id: str,
    decision: str,
    denial_codes: tuple[str, ...],
    inventory: tuple[Mapping[str, Any], ...],
    inventory_bytes: Optional[bytes],
    inventory_sha256: Optional[str],
    state_version: Optional[int],
    plan_fingerprint: Optional[str],
    input_descriptor_sha256: Optional[str],
) -> SafeReuseDecision:
    frozen_inventory = tuple(_freeze(item) for item in inventory)
    canonical_codes = tuple(sorted(set(denial_codes)))
    decision_bytes = _canonical_json_bytes(
        _decision_document(
            run_id=run_id,
            decision=decision,
            denial_codes=canonical_codes,
            inventory_sha256=inventory_sha256,
            state_version=state_version,
            plan_fingerprint=plan_fingerprint,
            input_descriptor_sha256=input_descriptor_sha256,
        )
    )
    result = object.__new__(SafeReuseDecision)
    object.__setattr__(result, "run_id", run_id)
    object.__setattr__(result, "decision", decision)
    object.__setattr__(result, "denial_codes", canonical_codes)
    object.__setattr__(result, "inventory", frozen_inventory)
    object.__setattr__(result, "inventory_bytes", inventory_bytes)
    object.__setattr__(result, "inventory_sha256", inventory_sha256)
    object.__setattr__(result, "state_version", state_version)
    object.__setattr__(result, "plan_fingerprint", plan_fingerprint)
    object.__setattr__(result, "input_descriptor_sha256", input_descriptor_sha256)
    object.__setattr__(result, "decision_bytes", decision_bytes)
    object.__setattr__(result, "decision_sha256", _sha256(decision_bytes))
    result.__post_init__()
    return result


def _denied(run_id: Any, code: str) -> SafeReuseDecision:
    safe_run_id = (
        run_id
        if isinstance(run_id, str) and _RUN_ID_RE.fullmatch(run_id) is not None
        else ""
    )
    return _make_decision(
        run_id=safe_run_id,
        decision="reuse_denied",
        denial_codes=(code,),
        inventory=(),
        inventory_bytes=None,
        inventory_sha256=None,
        state_version=None,
        plan_fingerprint=None,
        input_descriptor_sha256=None,
    )


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _validate_complete_resolution(resolution: Any, run_id: str) -> Optional[str]:
    if getattr(resolution, "run_id", None) != run_id:
        return "resolution_run_id_mismatch"
    status = getattr(resolution, "status", None)
    if status not in _ALLOWED_RESOLUTION_STATUSES:
        return "resolver_unknown_status"
    if status != "complete":
        return "resolver_" + status
    if getattr(resolution, "issue_codes", None) != ():
        return "resolution_issue_codes_invalid"
    inventory = getattr(resolution, "inventory", None)
    inventory_bytes = getattr(resolution, "inventory_bytes", None)
    inventory_sha256 = getattr(resolution, "inventory_sha256", None)
    state_version = getattr(resolution, "state_version", None)
    plan_fingerprint = getattr(resolution, "plan_fingerprint", None)
    descriptor_sha256 = getattr(resolution, "input_descriptor_sha256", None)
    if not isinstance(inventory, tuple) or not inventory:
        return "resolution_inventory_empty"
    if not isinstance(inventory_bytes, bytes) or not _valid_sha256(inventory_sha256):
        return "resolution_inventory_binding_invalid"
    if _sha256(inventory_bytes) != inventory_sha256:
        return "resolution_inventory_binding_invalid"
    if type(state_version) is not int or state_version < 0:
        return "resolution_state_binding_invalid"
    if not _valid_sha256(plan_fingerprint) or not _valid_sha256(descriptor_sha256):
        return "resolution_authority_binding_invalid"
    expected_inventory_bytes = json.dumps(
        _jsonable(
            {
                "schema_version": INVENTORY_SCHEMA_VERSION,
                "run_id": run_id,
                "artifacts": [_jsonable(item) for item in inventory],
            }
        ),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if inventory_bytes != expected_inventory_bytes:
        return "resolution_inventory_binding_invalid"
    paths: list[str] = []
    fixed_artifact_paths: set[str] = set()
    for item in inventory:
        if not isinstance(item, Mapping) or set(item) != _INVENTORY_FIELDS:
            return "resolution_inventory_item_invalid"
        path = item.get("path")
        if (
            not isinstance(path, str)
            or not path.startswith("runs/" + run_id + "/")
            or "\\" in path
            or ":" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))
        ):
            return "resolution_inventory_path_invalid"
        if path in paths:
            return "resolution_inventory_path_invalid"
        paths.append(path)
        if item.get("artifact_role") not in _ALLOWED_ARTIFACT_ROLES:
            return "resolution_inventory_item_invalid"
        if item.get("plan_fingerprint") != plan_fingerprint:
            return "resolution_plan_binding_invalid"
        if item.get("input_descriptor_sha256") != descriptor_sha256:
            return "resolution_descriptor_binding_invalid"
        item_state_version = item.get("resulting_state_version")
        if (
            type(item_state_version) is not int
            or item_state_version < 0
            or item_state_version > state_version
        ):
            return "resolution_producer_binding_invalid"
        task_id = item.get("task_id")
        producer_operation = item.get("producer_operation")
        if not isinstance(task_id, str) or not task_id:
            return "resolution_producer_binding_invalid"
        if not isinstance(producer_operation, str) or not producer_operation:
            return "resolution_producer_binding_invalid"
        run_prefix = "runs/" + run_id + "/"
        run_relative_path = path[len(run_prefix) :]
        artifact_role = item.get("artifact_role")
        final_summary_path = "runs/" + run_id + "/final_summary.md"
        if path == final_summary_path or artifact_role == "final_summary":
            if path != final_summary_path or artifact_role != "final_summary":
                return "resolution_publication_binding_invalid"
        raw_prepared_prefix = run_prefix + "work/raw_prepared/"
        is_raw_prepared = path.startswith(raw_prepared_prefix)
        if "/raw_prepared/" in path and not is_raw_prepared:
            return "resolution_inventory_path_invalid"
        if is_raw_prepared and artifact_role != "projection_input":
            return "resolution_inventory_item_invalid"
        fixed_binding = _FIXED_ARTIFACT_BINDINGS.get(run_relative_path)
        if fixed_binding is not None:
            fixed_artifact_paths.add(run_relative_path)
        fixed_role_path = _FIXED_ROLE_PATHS.get(artifact_role)
        role_task_id = _ROLE_PRODUCER_TASKS.get(artifact_role)
        if artifact_role == "projection_input" and not is_raw_prepared:
            role_task_id = "phase_a_association"
        if (
            (fixed_binding is not None and (artifact_role, task_id) != fixed_binding)
            or (fixed_role_path is not None and run_relative_path != fixed_role_path)
            or (
                artifact_role == "staging_report"
                and run_relative_path not in _STAGING_REPORT_PATHS
            )
            or (role_task_id is not None and task_id != role_task_id)
        ):
            return "resolution_producer_binding_invalid"
        if is_raw_prepared:
            if (
                producer_operation != "resolved_input_descriptor:" + descriptor_sha256
                or item_state_version != 0
            ):
                return "resolution_producer_binding_invalid"
        elif path != final_summary_path:
            operation_re = re.compile(
                r"run:"
                + re.escape(run_id)
                + r":task:"
                + re.escape(task_id)
                + r":attempt:[1-9][0-9]*:succeeded"
            )
            if operation_re.fullmatch(producer_operation) is None:
                return "resolution_producer_binding_invalid"
        size_bytes = item.get("size_bytes")
        if (
            not _valid_sha256(item.get("sha256"))
            or type(size_bytes) is not int
            or size_bytes < 0
        ):
            return "resolution_inventory_item_invalid"
    if paths != sorted(paths):
        return "resolution_inventory_order_invalid"
    # `paths` is duplicate-checked above, so exact set equality also requires
    # every contract-fixed path to occur exactly once.
    if fixed_artifact_paths != _REQUIRED_FIXED_ARTIFACT_PATHS:
        return "resolution_fixed_artifact_set_invalid"
    final_summary_path = "runs/" + run_id + "/final_summary.md"
    final_summaries = [item for item in inventory if item.get("path") == final_summary_path]
    if len(final_summaries) != 1:
        return "resolution_publication_binding_invalid"
    final_summary = final_summaries[0]
    if final_summary.get("resulting_state_version") != state_version:
        return "resolution_state_binding_invalid"
    if (
        final_summary.get("task_id") != "publication"
        or not isinstance(final_summary.get("producer_operation"), str)
        or _PUBLICATION_OPERATION_RE.fullmatch(final_summary["producer_operation"]) is None
    ):
        return "resolution_publication_binding_invalid"
    return None


class SafeReuseAuthorizer:
    """Authorize current-snapshot reuse without consuming or mutating it."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).absolute()

    def authorize(self, *, run_id: str) -> SafeReuseDecision:
        if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
            return _denied(run_id, "invalid_run_id")
        try:
            resolution = ArtifactResolver(self.project_root).resolve(run_id=run_id)
        except Exception:
            return _denied(run_id, "resolver_error")
        try:
            snapshot = SimpleNamespace(
                run_id=resolution.run_id,
                status=resolution.status,
                inventory=resolution.inventory,
                inventory_bytes=resolution.inventory_bytes,
                inventory_sha256=resolution.inventory_sha256,
                state_version=resolution.state_version,
                plan_fingerprint=resolution.plan_fingerprint,
                input_descriptor_sha256=resolution.input_descriptor_sha256,
                issue_codes=resolution.issue_codes,
            )
            denial_code = _validate_complete_resolution(snapshot, run_id)
            if denial_code is not None:
                return _denied(run_id, denial_code)
            return _make_decision(
                run_id=run_id,
                decision="reuse_allowed",
                denial_codes=(),
                inventory=snapshot.inventory,
                inventory_bytes=snapshot.inventory_bytes,
                inventory_sha256=snapshot.inventory_sha256,
                state_version=snapshot.state_version,
                plan_fingerprint=snapshot.plan_fingerprint,
                input_descriptor_sha256=snapshot.input_descriptor_sha256,
            )
        except Exception:
            return _denied(run_id, "resolution_contract_invalid")


def authorize_safe_reuse(project_root: Path, *, run_id: str) -> SafeReuseDecision:
    return SafeReuseAuthorizer(project_root).authorize(run_id=run_id)


__all__ = [
    "SAFE_REUSE_DECISION_SCHEMA_VERSION",
    "SafeReuseAuthorizer",
    "SafeReuseDecision",
    "authorize_safe_reuse",
]
