import json

import pytest

from orchestrator.claim_policy import (
    ClaimPolicyError,
    evaluate_claim_evidence,
    load_claim_policy,
)


def valid_evidence(**overrides):
    evidence = {
        "evidence_id": "EVD-I002-0001-area",
        "evidence_valid": True,
        "evidence_schema_valid": True,
        "current_record_valid": True,
        "identity_evidence_state": "association_supported",
        "current_observation_source_declared": True,
        "current_observation_source": "real_inspection_mask_input",
        "current_comparability_status": "verified_comparable",
        "previous_entity_type": "memory_snapshot",
        "previous_observation_sources_declared": True,
        "previous_observation_sources": ["real_inspection_mask_input"],
        "previous_comparability_status": "verified_comparable",
        "comparison_comparability_status": "verified_comparable",
        "valid_timepoint_count": 2,
        "metric_consistent": True,
        "measurement_method_consistent": True,
        "difference_valid": True,
        "registration_status": "registered",
        "temporal_order_valid": True,
        "needs_manual_review": False,
        "execution_profile": "phase_a1_sandbox",
    }
    evidence.update(overrides)
    return evidence


def test_default_policy_loads_with_expected_fixed_capabilities():
    policy = load_claim_policy()

    assert policy["schema_version"] == "claim_policy_v5"
    assert policy["profile"] == "phase_a"
    assert policy["fixed_blocked_capabilities"] == {
        "physical_quantity_change_claim": "PHASE_A_NO_PHYSICAL_QUANTITY_EVIDENCE",
        "multi_timepoint_pattern_claim": "PHASE_A_NO_THREE_TIMEPOINT_EVIDENCE",
        "prediction_claim": "PHASE_A_NO_VALIDATED_PREDICTION_MODEL",
    }


def test_verified_supported_evidence_allows_limited_difference_and_direction():
    decision = evaluate_claim_evidence(valid_evidence())

    assert decision["capabilities"] == {
        "static_descriptive_audit": "allowed",
        "descriptive_difference_claim": "allowed_with_limits",
        "directional_change_claim": "allowed_with_limits",
        "physical_quantity_change_claim": "blocked",
        "multi_timepoint_pattern_claim": "blocked",
        "prediction_claim": "blocked",
    }
    assert decision["template_ids"]["directional_change_claim"] == "directional_change_limited_v1"


def test_noncomparable_evidence_is_static_audit_only_with_required_qualifier():
    decision = evaluate_claim_evidence(
        valid_evidence(
            current_comparability_status="not_longitudinally_comparable",
            previous_comparability_status="not_longitudinally_comparable",
            comparison_comparability_status="not_longitudinally_comparable",
        )
    )

    assert decision["capabilities"]["static_descriptive_audit"] == "allowed"
    assert decision["capabilities"]["descriptive_difference_claim"] == "blocked"
    assert decision["capabilities"]["directional_change_claim"] == "blocked"
    assert "当前证据不可纵向比较，仅允许静态描述审计，不构成方向性变化结论" in decision[
        "required_language_qualifiers"
    ]


@pytest.mark.parametrize("value", [False, None, "true", 1])
def test_evidence_valid_must_be_strict_boolean_true(value):
    decision = evaluate_claim_evidence(valid_evidence(evidence_valid=value))

    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == "EVIDENCE_INVALID"


@pytest.mark.parametrize(
    "identity_state",
    ["association_rejected", "association_pending_review", "association_not_applicable"],
)
def test_non_supported_identity_states_allow_static_audit_only(identity_state):
    evidence = valid_evidence(identity_evidence_state=identity_state)
    if identity_state in {"association_rejected", "association_not_applicable"}:
        evidence.update(
            previous_entity_type="not_applicable",
            previous_observation_sources=[],
            previous_comparability_status="insufficient_history",
            comparison_comparability_status="insufficient_history",
            valid_timepoint_count=1,
        )
    if identity_state == "association_pending_review":
        evidence["needs_manual_review"] = True

    decision = evaluate_claim_evidence(evidence)

    assert decision["capabilities"]["static_descriptive_audit"] == "allowed"
    assert decision["capabilities"]["descriptive_difference_claim"] == "blocked"
    assert decision["capabilities"]["directional_change_claim"] == "blocked"


@pytest.mark.parametrize(
    ("identity_state", "reason"),
    [
        ("association_invalid", "INVALID_ASSOCIATION_EVIDENCE"),
        ("human_verified", "UNTRUSTED_IDENTITY_VERIFICATION"),
        ("ground_truth_verified", "UNTRUSTED_IDENTITY_VERIFICATION"),
    ],
)
def test_rejected_identity_state_blocks_even_valid_evidence(identity_state, reason):
    decision = evaluate_claim_evidence(valid_evidence(identity_evidence_state=identity_state))

    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == reason


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("current_observation_source", "unknown_source"),
        ("current_comparability_status", "maybe_comparable"),
        ("previous_comparability_status", "maybe_comparable"),
        ("comparison_comparability_status", "maybe_comparable"),
    ],
)
def test_unknown_evidence_enums_fail_closed(field, value):
    decision = evaluate_claim_evidence(valid_evidence(**{field: value}))

    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == "EVIDENCE_SCHEMA_INVALID"


def test_inconsistent_comparison_comparability_fails_closed():
    decision = evaluate_claim_evidence(
        valid_evidence(
            current_comparability_status="not_longitudinally_comparable",
            comparison_comparability_status="verified_comparable",
        )
    )

    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == "EVIDENCE_SCHEMA_INVALID"


def test_cyclic_kict_source_cannot_claim_verified_comparability():
    decision = evaluate_claim_evidence(
        valid_evidence(
            current_observation_source="kict_static_mask_cyclic_demo",
            current_comparability_status="verified_comparable",
        )
    )

    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == "EVIDENCE_SCHEMA_INVALID"


def test_verified_fixture_is_rejected_outside_test_profile():
    decision = evaluate_claim_evidence(
        valid_evidence(
            current_observation_source="verified_fixture",
            previous_observation_sources=["verified_fixture"],
            execution_profile="phase_a1_sandbox",
        )
    )

    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == "VERIFIED_FIXTURE_OUTSIDE_TEST"


def test_input_cannot_override_phase_a_profile():
    decision = evaluate_claim_evidence(valid_evidence(profile="custom"))

    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == "INPUT_PROFILE_OVERRIDE"


def test_policy_rejects_unknown_root_fields(tmp_path):
    policy = load_claim_policy()
    policy["unexpected"] = True
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ClaimPolicyError, match="unknown fields"):
        load_claim_policy(path)


def test_policy_rejects_enum_drift_with_same_schema_version(tmp_path):
    policy = load_claim_policy()
    policy["comparability_status_enum"].append("claimed_comparable")
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ClaimPolicyError, match="comparability_status_enum"):
        load_claim_policy(path)


def test_non_object_evidence_fails_closed():
    decision = evaluate_claim_evidence(None)  # type: ignore[arg-type]

    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == "EVIDENCE_NOT_OBJECT"


def test_unknown_runtime_profile_fails_closed():
    decision = evaluate_claim_evidence(valid_evidence(), profile="phase_b")

    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == "INVALID_CLAIM_POLICY_PROFILE"
