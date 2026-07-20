"""Fail-closed Phase A claim policy validation and evaluation.

This module is deliberately independent from the DAG and artifact writers. It
turns one already-built comparison evidence mapping into one decision mapping;
it does not infer identity, recompute association scores, or publish outputs.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Any


DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[1] / "config" / "inspection_claim_policy.json"
SUPPORTED_POLICY_SCHEMA_VERSION = "claim_policy_v5"
SUPPORTED_EVALUATOR_CONTRACT_VERSION = "phase_a_claim_evaluator_v1"

POLICY_ROOT_FIELDS = {
    "schema_version",
    "profile",
    "evaluator_contract_version",
    "provenance_contract",
    "observation_source_enum",
    "comparability_status_enum",
    "previous_entity_type_enum",
    "registration_status_enum",
    "execution_profile_enum",
    "accepted_identity_evidence_states",
    "rejected_identity_evidence_states",
    "global_precondition",
    "capability_order",
    "fixed_blocked_capabilities",
    "source_comparability_rules",
    "capability_rules",
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
    """Load and strictly validate the fixed Phase A machine policy.

    The default policy is a process-lifetime snapshot loaded on first use.
    Updating the policy or evaluator on disk requires a process restart.
    """

    policy_path = Path(path) if path is not None else DEFAULT_POLICY_PATH
    if policy_path.resolve() == DEFAULT_POLICY_PATH.resolve():
        return _default_policy_copy()

    policy = _read_policy_file(policy_path)
    _require_canonical_policy(policy, _default_policy_copy())
    return policy


@lru_cache(maxsize=1)
def _canonical_default_policy_json() -> str:
    policy = _read_policy_file(DEFAULT_POLICY_PATH)
    return json.dumps(
        policy,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _default_policy_copy() -> dict[str, Any]:
    return json.loads(_canonical_default_policy_json())


def _read_policy_file(policy_path: Path) -> dict[str, Any]:
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
    """Load or validate a policy, then evaluate one evidence record."""

    if policy is None:
        trusted_policy = load_claim_policy()
    else:
        trusted_policy = _validate_policy_mapping(policy)
    return _evaluate_validated_claim_evidence(
        evidence,
        trusted_policy,
        profile=profile,
        evaluator_sha256=_evaluator_source_sha256(),
    )


def _evaluate_validated_claim_evidence(
    evidence: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    profile: str,
    evaluator_sha256: str,
) -> dict[str, Any]:
    """Pure Claim evaluation for an already validated policy mapping."""

    if not isinstance(evidence, Mapping):
        return _blocked_decision("", policy, "EVIDENCE_NOT_OBJECT", evaluator_sha256)

    evidence_id = evidence.get("evidence_id", "")
    normalized_id = evidence_id.strip() if isinstance(evidence_id, str) else ""

    def blocked(reason: str) -> dict[str, Any]:
        return _blocked_decision(normalized_id, policy, reason, evaluator_sha256)

    if "profile" in evidence:
        return blocked("INPUT_PROFILE_OVERRIDE")
    if profile != policy["profile"]:
        return blocked("INVALID_CLAIM_POLICY_PROFILE")

    identity_state = evidence.get("identity_evidence_state")
    if not isinstance(identity_state, str):
        return blocked("INVALID_IDENTITY_EVIDENCE_STATE")
    rejected_reason = policy["rejected_identity_evidence_states"].get(identity_state)
    if rejected_reason:
        return blocked(rejected_reason)
    if identity_state not in policy["accepted_identity_evidence_states"]:
        return blocked("INVALID_IDENTITY_EVIDENCE_STATE")

    global_precondition = policy["global_precondition"]
    if evidence.get(global_precondition["field"]) is not global_precondition["value"]:
        return blocked(global_precondition["failure_reason"])

    schema_error = _validate_evidence_schema(evidence, policy)
    if schema_error:
        return blocked(schema_error)

    capabilities = {name: "blocked" for name in policy["capability_order"]}
    capability_reasons = {
        name: "CAPABILITY_REQUIREMENTS_NOT_MET" for name in policy["capability_order"]
    }
    template_ids: dict[str, str | None] = {name: None for name in policy["capability_order"]}

    static_rule = policy["capability_rules"]["static_descriptive_audit"]
    capabilities["static_descriptive_audit"] = static_rule["allowed_status"]
    capability_reasons["static_descriptive_audit"] = static_rule["allowed_reason"]
    template_ids["static_descriptive_audit"] = static_rule["template_id"]

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
        difference_rule = policy["capability_rules"]["descriptive_difference_claim"]
        capabilities["descriptive_difference_claim"] = difference_rule["allowed_status"]
        capability_reasons["descriptive_difference_claim"] = difference_rule["allowed_reason"]
        template_ids["descriptive_difference_claim"] = difference_rule["template_id"]

    directional_allowed = all(
        (
            difference_allowed,
            evidence["registration_status"] == "registered",
            evidence["temporal_order_valid"] is True,
            evidence["needs_manual_review"] is False,
        )
    )
    if directional_allowed:
        directional_rule = policy["capability_rules"]["directional_change_claim"]
        capabilities["directional_change_claim"] = directional_rule["allowed_status"]
        capability_reasons["directional_change_claim"] = directional_rule["allowed_reason"]
        template_ids["directional_change_claim"] = directional_rule["template_id"]

    for capability, reason in policy["fixed_blocked_capabilities"].items():
        capability_reasons[capability] = reason

    qualifiers = [policy["required_language_qualifiers"]["identity_boundary"]]
    if identity_state != "association_not_applicable":
        qualifiers.append(policy["required_language_qualifiers"]["rule_association"])
    if identity_state == "association_pending_review":
        qualifiers.append(policy["required_language_qualifiers"]["pending_review"])
    if any(
        evidence[field] != "verified_comparable"
        for field in (
            "current_comparability_status",
            "previous_comparability_status",
            "comparison_comparability_status",
        )
    ):
        qualifiers.append(policy["required_language_qualifiers"]["static_only"])

    reason_codes = _deduplicate(
        [
            capability_reasons[name]
            for name in policy["capability_order"]
            if capability_reasons[name] != "CAPABILITY_REQUIREMENTS_NOT_MET"
        ]
    )
    return {
        "schema_version": "claim_decision_v4",
        "profile": policy["profile"],
        "decision_id": f"CD-{normalized_id}" if normalized_id else "",
        "evidence_id": normalized_id,
        "claim_policy_sha256": _policy_sha256(policy),
        "claim_evaluator_contract_version": policy["evaluator_contract_version"],
        "claim_evaluator_sha256": evaluator_sha256,
        "capabilities": capabilities,
        "capability_reasons": capability_reasons,
        "template_ids": template_ids,
        "required_language_qualifiers": qualifiers,
        "reason_codes": reason_codes,
    }


def _validate_policy_mapping(policy: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the same validation to an injected policy without trusting it."""

    if not isinstance(policy, dict):
        raise ClaimPolicyError("claim policy root must be an object")
    try:
        serialized = json.dumps(
            policy,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        parsed = json.loads(serialized)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ClaimPolicyError("claim policy must contain JSON-compatible values") from exc
    _validate_policy_data(parsed)
    _require_canonical_policy(parsed, _default_policy_copy())
    return parsed


def _require_canonical_policy(
    policy: Mapping[str, Any],
    canonical_policy: Mapping[str, Any],
) -> None:
    for field in sorted(POLICY_ROOT_FIELDS):
        if policy[field] != canonical_policy[field]:
            raise ClaimPolicyError(
                f"claim policy {field} does not match the checked-in Phase A policy; "
                "upgrade schema_version for policy changes"
            )


def _validate_policy_data(policy: Any) -> None:
    """Validate the one supported, closed Phase A policy schema."""

    if not isinstance(policy, dict):
        raise ClaimPolicyError("claim policy root must be an object")
    fields = set(policy)
    unknown = sorted(fields - POLICY_ROOT_FIELDS)
    missing = sorted(POLICY_ROOT_FIELDS - fields)
    if unknown:
        raise ClaimPolicyError(f"claim policy has unknown fields: {', '.join(unknown)}")
    if missing:
        raise ClaimPolicyError(f"claim policy is missing fields: {', '.join(missing)}")
    if policy["schema_version"] != SUPPORTED_POLICY_SCHEMA_VERSION or policy["profile"] != "phase_a":
        raise ClaimPolicyError("claim policy identity is invalid")
    if policy["evaluator_contract_version"] != SUPPORTED_EVALUATOR_CONTRACT_VERSION:
        raise ClaimPolicyError("claim policy evaluator contract is unsupported")
    if policy["provenance_contract"] != {
        "claim_evaluator_sha256_scope": "normalized_utf8_source_bytes_lf",
        "claim_evaluator_sha256_is_semantic_hash": False,
        "cache_lifecycle": "process_lifetime_snapshot_restart_required",
    }:
        raise ClaimPolicyError("claim policy provenance contract is invalid")

    observation_sources = _require_closed_string_list(policy, "observation_source_enum")
    comparability_statuses = _require_closed_string_list(policy, "comparability_status_enum")
    _require_closed_string_list(policy, "previous_entity_type_enum")
    _require_closed_string_list(policy, "registration_status_enum")
    _require_closed_string_list(policy, "execution_profile_enum")
    accepted_states = _require_closed_string_list(policy, "accepted_identity_evidence_states")
    capability_order = _require_closed_string_list(policy, "capability_order")
    if "verified_comparable" not in comparability_statuses:
        raise ClaimPolicyError("claim policy comparability statuses are incomplete")

    rejected_states = policy["rejected_identity_evidence_states"]
    if (
        not isinstance(rejected_states, dict)
        or not rejected_states
        or any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or not value
            for key, value in rejected_states.items()
        )
    ):
        raise ClaimPolicyError("claim policy rejected identity states are invalid")
    if set(accepted_states) & set(rejected_states):
        raise ClaimPolicyError("claim policy accepted and rejected identity states overlap")

    if policy["global_precondition"] != {
        "field": "evidence_valid",
        "operator": "strict_equals",
        "value": True,
        "failure_reason": "EVIDENCE_INVALID",
    }:
        raise ClaimPolicyError("claim policy global precondition is invalid")

    fixed_blocked = policy["fixed_blocked_capabilities"]
    if not isinstance(fixed_blocked, dict) or set(fixed_blocked) != set(capability_order[3:]):
        raise ClaimPolicyError("claim policy fixed blocked capabilities are invalid")
    if any(not isinstance(value, str) or not value for value in fixed_blocked.values()):
        raise ClaimPolicyError("claim policy fixed blocked reasons are invalid")

    capability_rules = policy["capability_rules"]
    if not isinstance(capability_rules, dict) or set(capability_rules) != set(capability_order[:3]):
        raise ClaimPolicyError("claim policy capability rules are invalid")
    for capability, rule in capability_rules.items():
        if not isinstance(rule, dict) or set(rule) != {
            "allowed_status",
            "allowed_reason",
            "template_id",
        }:
            raise ClaimPolicyError(f"claim policy rule is invalid: {capability}")
        if rule["allowed_status"] not in {"allowed", "allowed_with_limits"}:
            raise ClaimPolicyError(f"claim policy allowed status is invalid: {capability}")
        if any(
            not isinstance(rule[field], str) or not rule[field]
            for field in ("allowed_reason", "template_id")
        ):
            raise ClaimPolicyError(f"claim policy rule metadata is invalid: {capability}")

    source_rules = policy["source_comparability_rules"]
    if not isinstance(source_rules, dict) or set(source_rules) != {
        "mixed_sources_policy",
        "verified_comparable_allowed_sources",
    }:
        raise ClaimPolicyError("claim policy source comparability rules are invalid")
    if source_rules["mixed_sources_policy"] != "static_only":
        raise ClaimPolicyError("claim policy mixed source rule must remain static_only in Phase A")
    verified_sources = _require_closed_string_list(
        source_rules,
        "verified_comparable_allowed_sources",
    )
    if not set(verified_sources).issubset(observation_sources):
        raise ClaimPolicyError("claim policy verified sources are outside the source enum")
    if (
        source_rules["mixed_sources_policy"] == "static_only"
        and "mixed_sources" in verified_sources
    ):
        raise ClaimPolicyError(
            "claim policy mixed_sources cannot be verified when mixed_sources_policy is static_only"
        )

    qualifiers = policy["required_language_qualifiers"]
    if not isinstance(qualifiers, dict) or set(qualifiers) != {
        "identity_boundary",
        "rule_association",
        "pending_review",
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
    if not isinstance(current_source, str) or current_source not in policy["observation_source_enum"]:
        return "EVIDENCE_SCHEMA_INVALID"
    if not isinstance(previous_sources, list) or any(
        not isinstance(source, str) for source in previous_sources
    ):
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
        value = evidence.get(field)
        if not isinstance(value, str) or value not in policy["comparability_status_enum"]:
            return "EVIDENCE_SCHEMA_INVALID"

    verified_sources = policy["source_comparability_rules"][
        "verified_comparable_allowed_sources"
    ]
    if evidence["current_comparability_status"] == "verified_comparable" and (
        current_source not in verified_sources
    ):
        return "EVIDENCE_SCHEMA_INVALID"
    if evidence["previous_comparability_status"] == "verified_comparable" and any(
        source not in verified_sources for source in previous_sources
    ):
        return "EVIDENCE_SCHEMA_INVALID"

    previous_entity_type = evidence.get("previous_entity_type")
    identity_state = evidence["identity_evidence_state"]
    if (
        not isinstance(previous_entity_type, str)
        or previous_entity_type not in policy["previous_entity_type_enum"]
    ):
        return "EVIDENCE_SCHEMA_INVALID"
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

    registration_status = evidence.get("registration_status")
    if (
        not isinstance(registration_status, str)
        or registration_status not in policy["registration_status_enum"]
    ):
        return "EVIDENCE_SCHEMA_INVALID"
    if identity_state == "association_pending_review" and evidence["needs_manual_review"] is not True:
        return "EVIDENCE_SCHEMA_INVALID"
    if identity_state == "association_supported" and evidence["needs_manual_review"] is not False:
        return "EVIDENCE_SCHEMA_INVALID"

    all_sources = [current_source, *previous_sources]
    execution_profile = evidence.get("execution_profile")
    if (
        not isinstance(execution_profile, str)
        or execution_profile not in policy["execution_profile_enum"]
    ):
        return "EVIDENCE_SCHEMA_INVALID"
    if "verified_fixture" in all_sources and execution_profile != "test":
        return "VERIFIED_FIXTURE_OUTSIDE_TEST"
    return None


def _blocked_decision(
    evidence_id: str,
    policy: Mapping[str, Any],
    reason: str,
    evaluator_sha256: str,
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
        "claim_evaluator_contract_version": policy["evaluator_contract_version"],
        "claim_evaluator_sha256": evaluator_sha256,
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


@lru_cache(maxsize=1)
def _evaluator_source_sha256() -> str:
    """Return the process-lifetime normalized source hash, not a semantic hash."""

    source_path = Path(__file__)
    try:
        source_bytes = source_path.read_bytes()
    except OSError as exc:
        raise ClaimPolicyError(f"unable to read claim evaluator source: {source_path}") from exc
    return _normalized_source_sha256(source_bytes, source_path=source_path)


def _normalized_source_sha256(source_bytes: bytes, *, source_path: Path | None = None) -> str:
    try:
        source_text = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        label = source_path if source_path is not None else "<provided evaluator source>"
        raise ClaimPolicyError(f"claim evaluator source is not UTF-8: {label}") from exc

    normalized_source = source_text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return hashlib.sha256(normalized_source).hexdigest()


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


def _require_closed_string_list(
    mapping: Mapping[str, Any],
    field: str,
) -> list[str]:
    value = mapping.get(field)
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise ClaimPolicyError(f"claim policy {field} must be a non-empty unique string list")
    return value


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
