"""Fail-closed Phase A claim policy validation and evaluation.

This module is deliberately independent from the DAG and artifact writers. It
turns one already-built comparison evidence mapping into one decision mapping;
it does not infer identity, recompute association scores, or publish outputs.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
from typing import Any


DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[1] / "config" / "inspection_claim_policy.json"

CAPABILITY_ORDER = (
    "static_descriptive_audit",
    "descriptive_difference_claim",
    "directional_change_claim",
    "physical_quantity_change_claim",
    "multi_timepoint_pattern_claim",
    "prediction_claim",
)

FIXED_BLOCKED_CAPABILITIES = {
    "physical_quantity_change_claim": "PHASE_A_NO_PHYSICAL_QUANTITY_EVIDENCE",
    "multi_timepoint_pattern_claim": "PHASE_A_NO_THREE_TIMEPOINT_EVIDENCE",
    "prediction_claim": "PHASE_A_NO_VALIDATED_PREDICTION_MODEL",
}

OBSERVATION_SOURCES = (
    "kict_static_mask_cyclic_demo",
    "real_inspection_mask_input",
    "verified_fixture",
    "mixed_sources",
    "legacy_unverified_source",
)

COMPARABILITY_STATUSES = (
    "verified_comparable",
    "not_longitudinally_comparable",
    "insufficient_history",
    "simulated_metadata_comparable",
)

ACCEPTED_IDENTITY_STATES = (
    "association_supported",
    "association_rejected",
    "association_pending_review",
    "association_not_applicable",
)

REJECTED_IDENTITY_STATES = {
    "association_invalid": "INVALID_ASSOCIATION_EVIDENCE",
    "human_verified": "UNTRUSTED_IDENTITY_VERIFICATION",
    "ground_truth_verified": "UNTRUSTED_IDENTITY_VERIFICATION",
}

POLICY_ROOT_FIELDS = {
    "schema_version",
    "profile",
    "observation_source_enum",
    "comparability_status_enum",
    "accepted_identity_evidence_states",
    "rejected_identity_evidence_states",
    "global_precondition",
    "capability_order",
    "fixed_blocked_capabilities",
    "required_language_qualifiers",
}

EVIDENCE_BOOL_FIELDS = (
    "evidence_schema_valid",
    "current_record_valid",
    "current_observation_source_declared",
    "previous_observation_sources_declared",
    "metric_consistent",
    "measurement_method_consistent",
    "difference_valid",
    "temporal_order_valid",
    "needs_manual_review",
)


class ClaimPolicyError(ValueError):
    """Raised when the trusted machine policy itself is malformed."""


def load_claim_policy(path: Path | None = None) -> dict[str, Any]:
    """Load and strictly validate the fixed Phase A machine policy."""

    policy_path = Path(path) if path is not None else DEFAULT_POLICY_PATH
    try:
        raw = policy_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ClaimPolicyError(f"unable to read claim policy: {policy_path}") from exc

    try:
        policy = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ClaimPolicyError(f"claim policy is not valid JSON: {policy_path}") from exc

    _validate_policy_data(policy)
    return policy


def evaluate_claim_evidence(
    evidence: Mapping[str, Any],
    *,
    policy: Mapping[str, Any] | None = None,
    profile: str = "phase_a",
) -> dict[str, Any]:
    """Evaluate one evidence record without reading or writing pipeline artifacts."""

    trusted_policy = dict(policy) if policy is not None else load_claim_policy()
    _validate_policy_mapping(trusted_policy)

    if not isinstance(evidence, Mapping):
        return _blocked_decision("", trusted_policy, "EVIDENCE_NOT_OBJECT")

    evidence_id = evidence.get("evidence_id", "")
    normalized_id = evidence_id.strip() if isinstance(evidence_id, str) else ""

    if "profile" in evidence:
        return _blocked_decision(normalized_id, trusted_policy, "INPUT_PROFILE_OVERRIDE")
    if profile != trusted_policy["profile"]:
        return _blocked_decision(normalized_id, trusted_policy, "INVALID_CLAIM_POLICY_PROFILE")

    identity_state = evidence.get("identity_evidence_state")
    rejected_reason = trusted_policy["rejected_identity_evidence_states"].get(identity_state)
    if rejected_reason:
        return _blocked_decision(normalized_id, trusted_policy, rejected_reason)
    if identity_state not in trusted_policy["accepted_identity_evidence_states"]:
        return _blocked_decision(normalized_id, trusted_policy, "INVALID_IDENTITY_EVIDENCE_STATE")

    if evidence.get("evidence_valid") is not True:
        return _blocked_decision(
            normalized_id,
            trusted_policy,
            trusted_policy["global_precondition"]["failure_reason"],
        )

    schema_error = _validate_evidence_schema(evidence, trusted_policy)
    if schema_error:
        return _blocked_decision(normalized_id, trusted_policy, schema_error)

    capabilities = {name: "blocked" for name in trusted_policy["capability_order"]}
    capability_reasons = {
        name: "CAPABILITY_REQUIREMENTS_NOT_MET" for name in trusted_policy["capability_order"]
    }
    template_ids: dict[str, str | None] = {name: None for name in trusted_policy["capability_order"]}

    capabilities["static_descriptive_audit"] = "allowed"
    capability_reasons["static_descriptive_audit"] = "STATIC_DESCRIPTIVE_AUDIT_ALLOWED"
    template_ids["static_descriptive_audit"] = "static_descriptive_audit_v1"

    difference_allowed = all(
        (
            identity_state == "association_supported",
            evidence["previous_entity_type"] == "memory_snapshot",
            evidence["current_comparability_status"] == "verified_comparable",
            evidence["previous_comparability_status"] == "verified_comparable",
            evidence["comparison_comparability_status"] == "verified_comparable",
            evidence["valid_timepoint_count"] >= 2,
            evidence["metric_consistent"] is True,
            evidence["measurement_method_consistent"] is True,
            evidence["difference_valid"] is True,
        )
    )
    if difference_allowed:
        capabilities["descriptive_difference_claim"] = "allowed_with_limits"
        capability_reasons["descriptive_difference_claim"] = "DESCRIPTIVE_DIFFERENCE_ALLOWED_WITH_LIMITS"
        template_ids["descriptive_difference_claim"] = "descriptive_difference_limited_v1"

    directional_allowed = all(
        (
            difference_allowed,
            evidence["registration_status"] == "registered",
            evidence["temporal_order_valid"] is True,
            evidence["needs_manual_review"] is False,
        )
    )
    if directional_allowed:
        capabilities["directional_change_claim"] = "allowed_with_limits"
        capability_reasons["directional_change_claim"] = "DIRECTIONAL_CHANGE_ALLOWED_WITH_LIMITS"
        template_ids["directional_change_claim"] = "directional_change_limited_v1"

    for capability, reason in trusted_policy["fixed_blocked_capabilities"].items():
        capability_reasons[capability] = reason

    qualifiers = [trusted_policy["required_language_qualifiers"]["identity_boundary"]]
    if identity_state != "association_not_applicable":
        qualifiers.append(trusted_policy["required_language_qualifiers"]["rule_association"])
    if any(
        evidence[field] != "verified_comparable"
        for field in (
            "current_comparability_status",
            "previous_comparability_status",
            "comparison_comparability_status",
        )
    ):
        qualifiers.append(trusted_policy["required_language_qualifiers"]["static_only"])

    reason_codes = _deduplicate(
        [
            capability_reasons[name]
            for name in trusted_policy["capability_order"]
            if capability_reasons[name] != "CAPABILITY_REQUIREMENTS_NOT_MET"
        ]
    )
    return {
        "schema_version": "claim_decision_v4",
        "profile": trusted_policy["profile"],
        "decision_id": f"CD-{normalized_id}" if normalized_id else "",
        "evidence_id": normalized_id,
        "claim_policy_sha256": _policy_sha256(trusted_policy),
        "capabilities": capabilities,
        "capability_reasons": capability_reasons,
        "template_ids": template_ids,
        "required_language_qualifiers": qualifiers,
        "reason_codes": reason_codes,
    }


def _validate_policy_mapping(policy: Mapping[str, Any]) -> None:
    """Apply the same validation to an injected policy without trusting it."""

    if not isinstance(policy, dict):
        raise ClaimPolicyError("claim policy root must be an object")
    serialized = json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    parsed = json.loads(serialized)
    _validate_policy_data(parsed)


def _validate_policy_data(policy: dict[str, Any]) -> None:
    """Validate the one supported, closed Phase A policy schema."""

    fields = set(policy)
    unknown = sorted(fields - POLICY_ROOT_FIELDS)
    missing = sorted(POLICY_ROOT_FIELDS - fields)
    if unknown:
        raise ClaimPolicyError(f"claim policy has unknown fields: {', '.join(unknown)}")
    if missing:
        raise ClaimPolicyError(f"claim policy is missing fields: {', '.join(missing)}")
    if policy["schema_version"] != "claim_policy_v5" or policy["profile"] != "phase_a":
        raise ClaimPolicyError("claim policy identity is invalid")
    _require_exact_string_list(policy, "observation_source_enum", OBSERVATION_SOURCES)
    _require_exact_string_list(policy, "comparability_status_enum", COMPARABILITY_STATUSES)
    _require_exact_string_list(policy, "accepted_identity_evidence_states", ACCEPTED_IDENTITY_STATES)
    _require_exact_string_list(policy, "capability_order", CAPABILITY_ORDER)
    if policy["rejected_identity_evidence_states"] != REJECTED_IDENTITY_STATES:
        raise ClaimPolicyError("claim policy rejected identity states do not match Phase A")
    if policy["fixed_blocked_capabilities"] != FIXED_BLOCKED_CAPABILITIES:
        raise ClaimPolicyError("claim policy fixed blocked capabilities do not match Phase A")
    if policy["global_precondition"] != {
        "field": "evidence_valid",
        "operator": "strict_equals",
        "value": True,
        "failure_reason": "EVIDENCE_INVALID",
    }:
        raise ClaimPolicyError("claim policy global precondition is invalid")
    qualifiers = policy["required_language_qualifiers"]
    if not isinstance(qualifiers, dict) or set(qualifiers) != {
        "identity_boundary",
        "rule_association",
        "static_only",
    }:
        raise ClaimPolicyError("claim policy language qualifier fields are invalid")
    if any(not isinstance(value, str) or not value.strip() for value in qualifiers.values()):
        raise ClaimPolicyError("claim policy language qualifiers must be non-empty strings")


def _validate_evidence_schema(evidence: Mapping[str, Any], policy: Mapping[str, Any]) -> str | None:
    if not isinstance(evidence.get("evidence_id"), str) or not evidence["evidence_id"].strip():
        return "EVIDENCE_SCHEMA_INVALID"
    for field in EVIDENCE_BOOL_FIELDS:
        if type(evidence.get(field)) is not bool:
            return "EVIDENCE_SCHEMA_INVALID"

    if evidence["evidence_schema_valid"] is not True or evidence["current_record_valid"] is not True:
        return "EVIDENCE_SCHEMA_INVALID"
    if evidence["current_observation_source_declared"] is not True:
        return "EVIDENCE_SCHEMA_INVALID"
    if evidence["previous_observation_sources_declared"] is not True:
        return "EVIDENCE_SCHEMA_INVALID"

    current_source = evidence.get("current_observation_source")
    previous_sources = evidence.get("previous_observation_sources")
    if current_source not in policy["observation_source_enum"]:
        return "EVIDENCE_SCHEMA_INVALID"
    if not isinstance(previous_sources, list):
        return "EVIDENCE_SCHEMA_INVALID"
    if previous_sources != sorted(set(previous_sources)):
        return "EVIDENCE_SCHEMA_INVALID"
    if any(source not in policy["observation_source_enum"] for source in previous_sources):
        return "EVIDENCE_SCHEMA_INVALID"

    for field in (
        "current_comparability_status",
        "previous_comparability_status",
        "comparison_comparability_status",
    ):
        if evidence.get(field) not in policy["comparability_status_enum"]:
            return "EVIDENCE_SCHEMA_INVALID"

    if (
        current_source == "kict_static_mask_cyclic_demo"
        and evidence["current_comparability_status"] == "verified_comparable"
    ):
        return "EVIDENCE_SCHEMA_INVALID"
    if (
        "kict_static_mask_cyclic_demo" in previous_sources
        and evidence["previous_comparability_status"] == "verified_comparable"
    ):
        return "EVIDENCE_SCHEMA_INVALID"

    previous_entity_type = evidence.get("previous_entity_type")
    identity_state = evidence["identity_evidence_state"]
    if previous_entity_type == "not_applicable":
        if identity_state not in {"association_rejected", "association_not_applicable"}:
            return "EVIDENCE_SCHEMA_INVALID"
        if previous_sources:
            return "EVIDENCE_SCHEMA_INVALID"
        if evidence["previous_comparability_status"] != "insufficient_history":
            return "EVIDENCE_SCHEMA_INVALID"
        if evidence["comparison_comparability_status"] != "insufficient_history":
            return "EVIDENCE_SCHEMA_INVALID"
    elif previous_entity_type == "memory_snapshot":
        if identity_state not in {"association_supported", "association_pending_review"}:
            return "EVIDENCE_SCHEMA_INVALID"
        if not previous_sources:
            return "EVIDENCE_SCHEMA_INVALID"
    else:
        return "EVIDENCE_SCHEMA_INVALID"

    expected_comparison_status = _compose_comparability_status(
        previous_entity_type=previous_entity_type,
        current_status=evidence["current_comparability_status"],
        previous_status=evidence["previous_comparability_status"],
    )
    if evidence["comparison_comparability_status"] != expected_comparison_status:
        return "EVIDENCE_SCHEMA_INVALID"

    valid_timepoint_count = evidence.get("valid_timepoint_count")
    if type(valid_timepoint_count) is not int or valid_timepoint_count < 1:
        return "EVIDENCE_SCHEMA_INVALID"
    if previous_entity_type == "not_applicable" and valid_timepoint_count != 1:
        return "EVIDENCE_SCHEMA_INVALID"

    if evidence.get("registration_status") not in {"registered", "not_verified"}:
        return "EVIDENCE_SCHEMA_INVALID"
    if identity_state == "association_pending_review" and evidence["needs_manual_review"] is not True:
        return "EVIDENCE_SCHEMA_INVALID"
    if identity_state == "association_supported" and evidence["needs_manual_review"] is not False:
        return "EVIDENCE_SCHEMA_INVALID"

    all_sources = [current_source, *previous_sources]
    if "verified_fixture" in all_sources and evidence.get("execution_profile") != "test":
        return "VERIFIED_FIXTURE_OUTSIDE_TEST"
    return None


def _blocked_decision(
    evidence_id: str,
    policy: Mapping[str, Any],
    reason: str,
) -> dict[str, Any]:
    capabilities = {name: "blocked" for name in policy["capability_order"]}
    capability_reasons = {
        name: policy["fixed_blocked_capabilities"].get(name, reason)
        for name in policy["capability_order"]
    }
    reasons = _deduplicate([reason, *policy["fixed_blocked_capabilities"].values()])
    return {
        "schema_version": "claim_decision_v4",
        "profile": policy["profile"],
        "decision_id": f"CD-{evidence_id}" if evidence_id else "",
        "evidence_id": evidence_id,
        "claim_policy_sha256": _policy_sha256(policy),
        "capabilities": capabilities,
        "capability_reasons": capability_reasons,
        "template_ids": {name: None for name in policy["capability_order"]},
        "required_language_qualifiers": [],
        "reason_codes": reasons,
    }


def _policy_sha256(policy: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        policy,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _compose_comparability_status(
    *,
    previous_entity_type: str,
    current_status: str,
    previous_status: str,
) -> str:
    if (
        previous_entity_type == "not_applicable"
        or current_status == "insufficient_history"
        or previous_status == "insufficient_history"
    ):
        return "insufficient_history"
    if current_status == previous_status == "verified_comparable":
        return "verified_comparable"
    return "not_longitudinally_comparable"


def _require_exact_string_list(
    mapping: Mapping[str, Any],
    field: str,
    expected: tuple[str, ...],
) -> None:
    value = mapping.get(field)
    if not isinstance(value, list) or tuple(value) != expected:
        raise ClaimPolicyError(f"claim policy {field} does not match Phase A")


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
