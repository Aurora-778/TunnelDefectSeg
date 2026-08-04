"""Phase 0 contract for deterministic ClaimDecision documents."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
import re
from typing import Any

from orchestrator.claim_policy import (
    CLAIM_DECISION_SCHEMA_VERSION,
    evaluate_claim_evidence,
    get_claim_policy_provenance,
    load_claim_policy,
)

from .comparison_evidence import (
    ComparisonEvidenceContractError,
    validate_comparison_evidence_records,
)


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

_EVALUATOR_DECISION_FIELDS = (
    "schema_version",
    "profile",
    "decision_id",
    "evidence_id",
    "claim_policy_sha256",
    "claim_evaluator_contract_version",
    "claim_evaluator_sha256",
    "capabilities",
    "capability_reasons",
    "template_ids",
    "required_language_qualifiers",
    "reason_codes",
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
        # Python 3.8 compatible equivalent of
        # field.removeprefix("source_").removesuffix("_sha256")
        label = field
        if label.startswith("source_"):
            label = label[len("source_"):]
        if label.endswith("_sha256"):
            label = label[: -len("_sha256")]
        label = label.replace("_", " ")
        raise ClaimDecisionContractError(
            f"claim decision records must reference a single {label}"
        )
    return next(iter(values), None)


def _require_string_list(value: Any, *, field: str, row_number: int) -> None:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ClaimDecisionContractError(
            f"claim evaluator {field} must be a string list at evidence row {row_number}"
        )


def _validate_evaluator_decision(
    decision: Any,
    evidence: Mapping[str, Any],
    *,
    capability_names: tuple[str, ...],
    expected_provenance: Mapping[str, str],
    expected_profile: str,
    row_number: int,
) -> None:
    label = f"claim evaluator decision at evidence row {row_number}"
    if not isinstance(decision, Mapping):
        raise ClaimDecisionContractError(f"{label} must be an object")
    _require_exact_fields(decision, _EVALUATOR_DECISION_FIELDS, label=label)
    if decision["schema_version"] != CLAIM_DECISION_SCHEMA_VERSION:
        raise ClaimDecisionContractError(f"{label} schema_version mismatch")
    if decision["profile"] != expected_profile:
        raise ClaimDecisionContractError(f"{label} profile mismatch")

    evidence_id = evidence["evidence_id"]
    if decision["evidence_id"] != evidence_id:
        raise ClaimDecisionContractError(f"{label} evidence_id mismatch")
    if decision["decision_id"] != f"CD-{evidence_id}":
        raise ClaimDecisionContractError(f"{label} decision_id mismatch")

    _require_sha256(decision["claim_policy_sha256"], field=f"{label} claim_policy_sha256")
    _require_sha256(
        decision["claim_evaluator_sha256"],
        field=f"{label} claim_evaluator_sha256",
    )
    if (
        not isinstance(decision["claim_evaluator_contract_version"], str)
        or not decision["claim_evaluator_contract_version"]
    ):
        raise ClaimDecisionContractError(
            f"{label} claim_evaluator_contract_version must be a non-empty string"
        )
    for field, expected_value in expected_provenance.items():
        if decision[field] != expected_value:
            raise ClaimDecisionContractError(f"{label} {field} mismatch")

    for field in ("capabilities", "capability_reasons", "template_ids"):
        value = decision[field]
        if not isinstance(value, Mapping):
            raise ClaimDecisionContractError(f"{label} {field} must be an object")
        if set(value) != set(capability_names):
            raise ClaimDecisionContractError(
                f"{label} {field} keys must match the Claim Policy capabilities"
            )

    if any(
        not isinstance(status, str)
        or status not in {"allowed", "allowed_with_limits", "blocked"}
        for status in decision["capabilities"].values()
    ):
        raise ClaimDecisionContractError(f"{label} capabilities contain an invalid status")
    if any(
        not isinstance(reason, str) or not reason
        for reason in decision["capability_reasons"].values()
    ):
        raise ClaimDecisionContractError(
            f"{label} capability_reasons must contain non-empty strings"
        )
    if any(
        template_id is not None
        and (not isinstance(template_id, str) or not template_id)
        for template_id in decision["template_ids"].values()
    ):
        raise ClaimDecisionContractError(
            f"{label} template_ids must contain strings or null"
        )
    _require_string_list(
        decision["required_language_qualifiers"],
        field="required_language_qualifiers",
        row_number=row_number,
    )
    _require_string_list(
        decision["reason_codes"],
        field="reason_codes",
        row_number=row_number,
    )


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
    policy = load_claim_policy()
    capability_names = tuple(policy["capability_order"])
    expected_provenance = get_claim_policy_provenance()
    evaluated = [evaluate_claim_evidence(record) for record in evidence]
    if len(evidence) != len(evaluated):  # Python 3.8 zip() has no strict=
        raise ClaimDecisionContractError("evidence and evaluated lists must have equal length")
    for row_number, (record, decision) in enumerate(
        zip(evidence, evaluated),
        start=1,
    ):
        _validate_evaluator_decision(
            decision,
            record,
            capability_names=capability_names,
            expected_provenance=expected_provenance,
            expected_profile=policy["profile"],
            row_number=row_number,
        )
    record_decisions = [
        _decision_record(record, decision)
        for record, decision in zip(evidence, evaluated)  # len already checked above
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
