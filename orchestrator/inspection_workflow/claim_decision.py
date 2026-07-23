"""Phase 0 contract for deterministic ClaimDecision documents."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
import re
from typing import Any

from orchestrator.claim_policy import evaluate_claim_evidence

from .comparison_evidence import (
    ComparisonEvidenceContractError,
    validate_comparison_evidence_records,
)


CLAIM_DECISION_SCHEMA_VERSION = "claim_decision_v4"
CLAIM_GATE_VERSION = "1.0.0"

CLAIM_DECISION_DOCUMENT_FIELDS = (
    "schema_version",
    "run_id",
    "plan_fingerprint",
    "claim_policy_sha256",
    "claim_gate_version",
    "claim_evaluator_contract_version",
    "claim_evaluator_sha256",
    "source_comparison_evidence_sha256",
    "source_association_artifact_sha256",
    "source_association_manifest_sha256",
    "record_decisions",
    "summary",
)

CLAIM_DECISION_RECORD_FIELDS = (
    "decision_id",
    "evidence_id",
    "current_record_id",
    "current_observation_id",
    "previous_entity_type",
    "previous_memory_id",
    "comparison_group_id",
    "identity_evidence_state",
    "evidence_valid",
    "invalid_reason",
    "current_observation_source",
    "current_comparability_status",
    "previous_observation_sources",
    "previous_comparability_status",
    "comparison_comparability_status",
    "source_record_fingerprint",
    "source_memory_snapshot_sha256",
    "capabilities",
    "capability_reasons",
    "template_ids",
    "required_language_qualifiers",
    "reason_codes",
)

CLAIM_DECISION_SUMMARY_FIELDS = (
    "total_records",
    "static_audit_allowed",
    "difference_allowed_with_limits",
    "difference_blocked",
    "directional_allowed",
    "physical_allowed",
    "pattern_allowed",
    "prediction_allowed",
)

_RUN_ID_RE = re.compile(r"run_[0-9]{3,}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class ClaimDecisionContractError(ValueError):
    """Raised when a ClaimDecision document contradicts its Evidence."""


def _require_sha256(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ClaimDecisionContractError(f"{field} must be a lowercase SHA-256")
    return value


def _require_exact_fields(
    value: Mapping[str, Any],
    expected_fields: tuple[str, ...],
    *,
    label: str,
) -> None:
    if any(not isinstance(field, str) for field in value):
        raise ClaimDecisionContractError(f"{label} field names must be strings")
    expected = set(expected_fields)
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        raise ClaimDecisionContractError(f"{label} missing fields: {', '.join(missing)}")
    if unknown:
        raise ClaimDecisionContractError(f"{label} has unknown fields: {', '.join(unknown)}")


def _single_source_hash(records: list[dict[str, Any]], field: str) -> str | None:
    values = {record[field] for record in records}
    if len(values) > 1:
        label = field.removeprefix("source_").removesuffix("_sha256").replace("_", " ")
        raise ClaimDecisionContractError(
            f"claim decision records must reference a single {label}"
        )
    return next(iter(values), None)


def _decision_record(evidence: Mapping[str, Any], decision: Mapping[str, Any]) -> dict[str, Any]:
    current_record_id = evidence["association_id"] or evidence["current_observation_id"]
    return {
        "decision_id": decision["decision_id"],
        "evidence_id": evidence["evidence_id"],
        "current_record_id": current_record_id,
        "current_observation_id": evidence["current_observation_id"],
        "previous_entity_type": evidence["previous_entity_type"],
        "previous_memory_id": evidence["previous_memory_id"],
        "comparison_group_id": evidence["comparison_group_id"],
        "identity_evidence_state": evidence["identity_evidence_state"],
        "evidence_valid": evidence["evidence_valid"],
        "invalid_reason": evidence["invalid_reason"],
        "current_observation_source": evidence["current_observation_source"],
        "current_comparability_status": evidence["current_comparability_status"],
        "previous_observation_sources": deepcopy(evidence["previous_observation_sources"]),
        "previous_comparability_status": evidence["previous_comparability_status"],
        "comparison_comparability_status": evidence["comparison_comparability_status"],
        "source_record_fingerprint": evidence["source_current_record_fingerprint"],
        "source_memory_snapshot_sha256": evidence["source_memory_snapshot_sha256"],
        "capabilities": deepcopy(decision["capabilities"]),
        "capability_reasons": deepcopy(decision["capability_reasons"]),
        "template_ids": deepcopy(decision["template_ids"]),
        "required_language_qualifiers": deepcopy(
            decision["required_language_qualifiers"]
        ),
        "reason_codes": deepcopy(decision["reason_codes"]),
    }


def _summary(record_decisions: list[dict[str, Any]]) -> dict[str, int]:
    def allowed(capability: str) -> int:
        return sum(
            record["capabilities"][capability] in {"allowed", "allowed_with_limits"}
            for record in record_decisions
        )

    return {
        "total_records": len(record_decisions),
        "static_audit_allowed": allowed("static_descriptive_audit"),
        "difference_allowed_with_limits": sum(
            record["capabilities"]["descriptive_difference_claim"]
            == "allowed_with_limits"
            for record in record_decisions
        ),
        "difference_blocked": sum(
            record["capabilities"]["descriptive_difference_claim"] == "blocked"
            for record in record_decisions
        ),
        "directional_allowed": allowed("directional_change_claim"),
        "physical_allowed": allowed("physical_quantity_change_claim"),
        "pattern_allowed": allowed("multi_timepoint_pattern_claim"),
        "prediction_allowed": allowed("prediction_claim"),
    }


def build_claim_decision_document(
    evidence_records: Iterable[Mapping[str, Any]],
    *,
    run_id: str,
    plan_fingerprint: str,
    source_comparison_evidence_sha256: str,
) -> dict[str, Any]:
    """Build a deterministic in-memory ClaimDecision from validated Evidence."""

    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        raise ClaimDecisionContractError("run_id must use canonical run_NNN format")
    _require_sha256(plan_fingerprint, field="plan_fingerprint")
    _require_sha256(
        source_comparison_evidence_sha256,
        field="source_comparison_evidence_sha256",
    )

    try:
        evidence = validate_comparison_evidence_records(evidence_records)
    except ComparisonEvidenceContractError as exc:
        raise ClaimDecisionContractError(
            f"comparison evidence validation failed: {exc}"
        ) from exc
    evidence.sort(
        key=lambda record: (
            record["current_inspection_id"],
            record["current_observation_id"],
            record["evidence_id"],
        )
    )
    evaluated = [evaluate_claim_evidence(record) for record in evidence]
    record_decisions = [
        _decision_record(record, decision)
        for record, decision in zip(evidence, evaluated, strict=True)
    ]
    first_decision = evaluated[0]
    if any(
        decision[field] != first_decision[field]
        for decision in evaluated[1:]
        for field in (
            "claim_policy_sha256",
            "claim_evaluator_contract_version",
            "claim_evaluator_sha256",
        )
    ):
        raise ClaimDecisionContractError("claim evaluator provenance changed within one document")

    return {
        "schema_version": CLAIM_DECISION_SCHEMA_VERSION,
        "run_id": run_id,
        "plan_fingerprint": plan_fingerprint,
        "claim_policy_sha256": first_decision["claim_policy_sha256"],
        "claim_gate_version": CLAIM_GATE_VERSION,
        "claim_evaluator_contract_version": first_decision[
            "claim_evaluator_contract_version"
        ],
        "claim_evaluator_sha256": first_decision["claim_evaluator_sha256"],
        "source_comparison_evidence_sha256": source_comparison_evidence_sha256,
        "source_association_artifact_sha256": _single_source_hash(
            evidence, "source_association_artifact_sha256"
        ),
        "source_association_manifest_sha256": _single_source_hash(
            evidence, "source_association_manifest_sha256"
        ),
        "record_decisions": record_decisions,
        "summary": _summary(record_decisions),
    }


def validate_claim_decision_document(
    document: Mapping[str, Any],
    evidence_records: Iterable[Mapping[str, Any]],
    *,
    expected_run_id: str,
    expected_plan_fingerprint: str,
    expected_source_comparison_evidence_sha256: str,
) -> dict[str, Any]:
    """Recompute a ClaimDecision and reject any structural or semantic drift."""

    if not isinstance(document, Mapping):
        raise ClaimDecisionContractError("claim decision document must be an object")
    _require_exact_fields(
        document,
        CLAIM_DECISION_DOCUMENT_FIELDS,
        label="claim decision document",
    )
    if document["schema_version"] != CLAIM_DECISION_SCHEMA_VERSION:
        raise ClaimDecisionContractError(
            f"claim decision schema_version must be {CLAIM_DECISION_SCHEMA_VERSION}"
        )
    if document["run_id"] != expected_run_id:
        raise ClaimDecisionContractError("claim decision run_id does not match expected Run")
    if document["plan_fingerprint"] != _require_sha256(
        expected_plan_fingerprint,
        field="expected_plan_fingerprint",
    ):
        raise ClaimDecisionContractError(
            "claim decision plan_fingerprint does not match expected plan"
        )
    if document["source_comparison_evidence_sha256"] != _require_sha256(
        expected_source_comparison_evidence_sha256,
        field="expected_source_comparison_evidence_sha256",
    ):
        raise ClaimDecisionContractError(
            "claim decision source comparison evidence SHA-256 does not match"
        )

    expected = build_claim_decision_document(
        evidence_records,
        run_id=expected_run_id,
        plan_fingerprint=expected_plan_fingerprint,
        source_comparison_evidence_sha256=expected_source_comparison_evidence_sha256,
    )
    for field in CLAIM_DECISION_DOCUMENT_FIELDS:
        if document[field] != expected[field]:
            raise ClaimDecisionContractError(
                f"claim decision {field} does not match validated Evidence and Policy"
            )
    return deepcopy(dict(document))


__all__ = [
    "CLAIM_DECISION_DOCUMENT_FIELDS",
    "CLAIM_DECISION_RECORD_FIELDS",
    "CLAIM_DECISION_SCHEMA_VERSION",
    "CLAIM_DECISION_SUMMARY_FIELDS",
    "ClaimDecisionContractError",
    "build_claim_decision_document",
    "validate_claim_decision_document",
]
