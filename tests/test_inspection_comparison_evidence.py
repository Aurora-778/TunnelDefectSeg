from __future__ import annotations

from copy import deepcopy

import pytest

from orchestrator.claim_policy import evaluate_claim_evidence
from orchestrator.inspection_workflow import (
    COMPARISON_EVIDENCE_FIELDS,
    COMPARISON_EVIDENCE_SCHEMA_VERSION,
    ComparisonEvidenceContractError,
    validate_comparison_evidence_records,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def baseline_evidence(**overrides):
    record = {
        "comparison_evidence_schema_version": COMPARISON_EVIDENCE_SCHEMA_VERSION,
        "evidence_id": "EVD-I001-obs-01-area",
        "execution_profile": "phase_a1_sandbox",
        "evidence_schema_valid": True,
        "current_record_valid": True,
        "current_inspection_id": "I001",
        "current_frame_id": "40",
        "current_image_id": "I001_000040",
        "current_observation_id": "I001::obs_01",
        "current_timestamp": "2026-07-01T10:00:00.000000Z",
        "current_observation_source_declared": True,
        "current_observation_source": "real_inspection_mask_input",
        "current_comparability_status": "insufficient_history",
        "previous_entity_type": "not_applicable",
        "previous_memory_id": None,
        "previous_memory_version": None,
        "previous_last_seen_inspection": None,
        "previous_last_seen_timestamp": None,
        "previous_source_inspection_ids": [],
        "previous_source_record_count": 0,
        "previous_observation_sources_declared": True,
        "previous_observation_sources": [],
        "previous_comparability_status": "insufficient_history",
        "comparison_group_id": "I001::obs_01::inspection_level_max_mask_area_px",
        "metric_name": "inspection_level_max_mask_area_px",
        "metric_type": "area",
        "value_domain": "mask_pixel_area",
        "measurement_method": "inspection_level_max_mask_area_px",
        "measurement_unit": "pixel²",
        "current_value": 120,
        "previous_memory_snapshot_value": None,
        "absolute_difference": None,
        "relative_difference": None,
        "association_id": None,
        "association_status": None,
        "association_mode": None,
        "use_disease_id_score": None,
        "association_score": None,
        "match_type": None,
        "candidate_count": 0,
        "score_margin": None,
        "conflict_reason": None,
        "needs_manual_review": False,
        "association_supported_pair": False,
        "identity_evidence_state": "association_not_applicable",
        "valid_timepoint_count": 1,
        "metric_consistent": True,
        "measurement_method_consistent": True,
        "temporal_order_valid": False,
        "difference_valid": False,
        "relative_difference_valid": False,
        "evidence_valid": True,
        "invalid_reason": None,
        "comparison_comparability_status": "insufficient_history",
        "comparability_reason": "baseline_current_only",
        "registration_status": "not_verified",
        "registration_evidence_source": "none",
        "registration_evidence_sha256": None,
        "physical_scale_calibrated": False,
        "scale_calibration_source": "none",
        "scale_calibration_sha256": None,
        "measurement_uncertainty": None,
        "uncertainty_source": "not_available",
        "uncertainty_unit": None,
        "source_current_record_fingerprint": SHA_A,
        "source_association_artifact_sha256": SHA_B,
        "source_association_manifest_sha256": SHA_C,
        "source_memory_snapshot_sha256": None,
        "source_engineering_artifact_sha256": SHA_D,
    }
    record.update(overrides)
    return record


def matched_evidence(**overrides):
    record = baseline_evidence(
        evidence_id="EVD-I002-obs-01-area",
        current_inspection_id="I002",
        current_image_id="I002_000040",
        current_observation_id="I002::obs_01",
        current_timestamp="2026-07-02T10:00:00.000000Z",
        current_comparability_status="verified_comparable",
        previous_entity_type="memory_snapshot",
        previous_memory_id="MEM-001",
        previous_memory_version="2",
        previous_last_seen_inspection="I001",
        previous_last_seen_timestamp="2026-07-01T10:00:00.000000Z",
        previous_source_inspection_ids=["I001"],
        previous_source_record_count=1,
        previous_observation_sources=["real_inspection_mask_input"],
        previous_comparability_status="verified_comparable",
        comparison_group_id="MEM-001::inspection_level_max_mask_area_px",
        current_value=120,
        previous_memory_snapshot_value=100,
        absolute_difference=20,
        relative_difference="0.2",
        association_id="ASSOC-I002-obs-01",
        association_status="matched",
        association_mode="no_id",
        use_disease_id_score=False,
        association_score="0.82",
        match_type="soft",
        candidate_count=2,
        score_margin="0.18",
        needs_manual_review=False,
        association_supported_pair=True,
        identity_evidence_state="association_supported",
        valid_timepoint_count=2,
        temporal_order_valid=True,
        difference_valid=True,
        relative_difference_valid=True,
        comparison_comparability_status="verified_comparable",
        comparability_reason="dual_verified_sources",
        registration_status="registered",
        registration_evidence_source="fixture_registration",
        registration_evidence_sha256=SHA_A,
        source_memory_snapshot_sha256=SHA_B,
    )
    record.update(overrides)
    return record


def rejected_evidence(**overrides):
    record = baseline_evidence(
        evidence_id="EVD-I002-obs-01-area",
        current_inspection_id="I002",
        current_image_id="I002_000040",
        current_observation_id="I002::obs_01",
        current_timestamp="2026-07-02T10:00:00.000000Z",
        association_id="ASSOC-I002-obs-01",
        association_status="unmatched",
        association_mode="no_id",
        use_disease_id_score=False,
        association_score="0",
        match_type="uncertain",
        candidate_count=0,
        needs_manual_review=True,
        identity_evidence_state="association_rejected",
        comparability_reason="no_history_match",
    )
    record.update(overrides)
    return record


def test_schema_field_set_covers_the_versioned_phase_zero_record():
    assert set(baseline_evidence()) == set(COMPARISON_EVIDENCE_FIELDS)


def test_schema_version_missing_field_and_hash_drift_fail_closed():
    with pytest.raises(ComparisonEvidenceContractError, match="schema version"):
        validate_comparison_evidence_records(
            [baseline_evidence(comparison_evidence_schema_version="comparison_evidence_v2")]
        )

    missing = baseline_evidence()
    missing.pop("current_image_id")
    with pytest.raises(ComparisonEvidenceContractError, match="missing fields: current_image_id"):
        validate_comparison_evidence_records([missing])

    with pytest.raises(ComparisonEvidenceContractError, match="lowercase SHA-256"):
        validate_comparison_evidence_records(
            [baseline_evidence(source_current_record_fingerprint="A" * 64)]
        )


def test_baseline_current_only_evidence_validates_and_allows_static_audit_only():
    source = baseline_evidence()

    validated = validate_comparison_evidence_records([source])
    decision = evaluate_claim_evidence(validated[0])

    assert validated == [source]
    assert decision["capabilities"]["static_descriptive_audit"] == "allowed"
    assert decision["capabilities"]["descriptive_difference_claim"] == "blocked"
    assert decision["capabilities"]["directional_change_claim"] == "blocked"


def test_rejected_query_is_static_only_and_does_not_fabricate_memory():
    validated = validate_comparison_evidence_records([rejected_evidence()])[0]
    decision = evaluate_claim_evidence(validated)

    assert validated["previous_entity_type"] == "not_applicable"
    assert validated["previous_memory_id"] is None
    assert decision["capabilities"]["static_descriptive_audit"] == "allowed"
    assert decision["capabilities"]["descriptive_difference_claim"] == "blocked"


def test_verified_matched_evidence_passes_the_existing_claim_policy():
    validated = validate_comparison_evidence_records([matched_evidence()])[0]
    decision = evaluate_claim_evidence(validated)

    assert decision["capabilities"]["descriptive_difference_claim"] == "allowed_with_limits"
    assert decision["capabilities"]["directional_change_claim"] == "allowed_with_limits"
    assert decision["capabilities"]["physical_quantity_change_claim"] == "blocked"


def test_pending_review_requires_matched_memory_and_remains_static_only():
    record = matched_evidence(
        identity_evidence_state="association_pending_review",
        needs_manual_review=True,
    )

    decision = evaluate_claim_evidence(validate_comparison_evidence_records([record])[0])

    assert decision["capabilities"]["static_descriptive_audit"] == "allowed"
    assert decision["capabilities"]["descriptive_difference_claim"] == "blocked"
    assert any("人工复核" in item for item in decision["required_language_qualifiers"])


def test_noncomparable_kict_memory_keeps_area_audit_but_cannot_claim_direction():
    record = matched_evidence(
        current_observation_source="kict_static_mask_cyclic_demo",
        current_comparability_status="not_longitudinally_comparable",
        previous_observation_sources=["kict_static_mask_cyclic_demo"],
        previous_comparability_status="not_longitudinally_comparable",
        comparison_comparability_status="not_longitudinally_comparable",
        difference_valid=False,
        registration_status="not_verified",
        registration_evidence_source="none",
        registration_evidence_sha256=None,
        comparability_reason="cyclic_static_masks",
    )

    validated = validate_comparison_evidence_records([record])[0]
    decision = evaluate_claim_evidence(validated)

    assert validated["absolute_difference"] == 20
    assert validated["relative_difference"] == "0.2"
    assert decision["capabilities"]["static_descriptive_audit"] == "allowed"
    assert decision["capabilities"]["directional_change_claim"] == "blocked"


@pytest.mark.parametrize("side", ["current", "previous"])
def test_insufficient_history_has_priority_over_other_comparability(side):
    overrides = {
        "comparison_comparability_status": "insufficient_history",
        "difference_valid": False,
        "comparability_reason": f"{side}_insufficient_history",
    }
    if side == "current":
        overrides["current_comparability_status"] = "insufficient_history"
    else:
        overrides["previous_comparability_status"] = "insufficient_history"

    validated = validate_comparison_evidence_records([matched_evidence(**overrides)])[0]

    assert validated["comparison_comparability_status"] == "insufficient_history"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("previous_memory_id", "MEM-001"),
        ("previous_memory_version", "1"),
        ("previous_source_inspection_ids", ["I000"]),
        ("previous_source_record_count", 1),
        ("previous_observation_sources", ["real_inspection_mask_input"]),
        ("previous_memory_snapshot_value", 0),
        ("absolute_difference", 120),
        ("relative_difference", "1"),
        ("source_memory_snapshot_sha256", SHA_A),
        ("valid_timepoint_count", 2),
        ("difference_valid", True),
    ],
)
def test_not_applicable_previous_fields_have_one_canonical_representation(field, value):
    with pytest.raises(
        ComparisonEvidenceContractError,
        match="not_applicable|one valid timepoint|requires difference_valid=false",
    ):
        validate_comparison_evidence_records([baseline_evidence(**{field: value})])


def test_zero_previous_area_uses_null_relative_difference_without_losing_absolute_audit():
    record = matched_evidence(
        previous_memory_snapshot_value=0,
        absolute_difference=120,
        relative_difference=None,
        relative_difference_valid=False,
        difference_valid=False,
    )

    validated = validate_comparison_evidence_records([record])[0]

    assert validated["absolute_difference"] == 120
    assert validated["relative_difference"] is None


def test_memory_area_difference_and_decimal_representation_are_canonical():
    with pytest.raises(ComparisonEvidenceContractError, match="absolute_difference does not match"):
        validate_comparison_evidence_records([matched_evidence(absolute_difference=19)])

    for value in ("0.20", "1e-1", "-0"):
        with pytest.raises(ComparisonEvidenceContractError, match="canonical decimal"):
            validate_comparison_evidence_records([matched_evidence(relative_difference=value)])

    with pytest.raises(ComparisonEvidenceContractError, match="relative_difference must equal 0.2"):
        validate_comparison_evidence_records([matched_evidence(relative_difference="9")])


def test_relative_difference_uses_six_digit_round_half_up_contract():
    rounded = matched_evidence(
        current_value=4,
        previous_memory_snapshot_value=3,
        absolute_difference=1,
        relative_difference="0.333333",
    )
    assert validate_comparison_evidence_records([rounded])[0]["relative_difference"] == "0.333333"

    with pytest.raises(ComparisonEvidenceContractError, match="relative_difference must equal 0.333333"):
        validate_comparison_evidence_records(
            [dict(rounded, relative_difference="0.333334")]
        )

    negative = matched_evidence(
        current_value=5,
        previous_memory_snapshot_value=6,
        absolute_difference=-1,
        relative_difference="-0.166667",
    )
    assert validate_comparison_evidence_records([negative])[0]["relative_difference"] == "-0.166667"

    half_up = matched_evidence(
        current_value=2_000_001,
        previous_memory_snapshot_value=2_000_000,
        absolute_difference=1,
        relative_difference="0.000001",
    )
    assert validate_comparison_evidence_records([half_up])[0]["relative_difference"] == "0.000001"

    large = matched_evidence(
        current_value=10**40 + 1,
        previous_memory_snapshot_value=1,
        absolute_difference=10**40,
        relative_difference="1" + "0" * 40,
    )
    assert validate_comparison_evidence_records([large])[0]["relative_difference"] == "1" + "0" * 40


@pytest.mark.parametrize("comparison_status", ["insufficient_history", "not_longitudinally_comparable"])
def test_nonverified_comparison_cannot_mark_difference_valid(comparison_status):
    overrides = {
        "difference_valid": True,
        "comparison_comparability_status": comparison_status,
    }
    if comparison_status == "insufficient_history":
        overrides["current_comparability_status"] = "insufficient_history"
    else:
        overrides.update(
            current_observation_source="kict_static_mask_cyclic_demo",
            current_comparability_status="not_longitudinally_comparable",
            previous_observation_sources=["kict_static_mask_cyclic_demo"],
            previous_comparability_status="not_longitudinally_comparable",
        )

    with pytest.raises(ComparisonEvidenceContractError, match="requires difference_valid=false"):
        validate_comparison_evidence_records([matched_evidence(**overrides)])


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("identity_evidence_state", "association_supported", "concrete no-id Association row"),
        ("association_id", "ASSOC-baseline", "canonical null"),
        ("association_status", "matched", "canonical null"),
        ("association_supported_pair", True, "cannot claim Association support"),
        ("needs_manual_review", True, "cannot claim Association support or review"),
    ],
)
def test_baseline_branch_rejects_association_or_supported_state_leakage(field, value, message):
    with pytest.raises(ComparisonEvidenceContractError, match=message):
        validate_comparison_evidence_records([baseline_evidence(**{field: value})])


def test_unmatched_query_cannot_carry_memory_or_supported_pair():
    with pytest.raises(ComparisonEvidenceContractError, match="without Memory"):
        validate_comparison_evidence_records(
            [
                matched_evidence(
                    identity_evidence_state="association_rejected",
                    association_status="unmatched",
                    association_supported_pair=False,
                    needs_manual_review=True,
                    difference_valid=False,
                    registration_status="not_verified",
                    registration_evidence_source="none",
                    registration_evidence_sha256=None,
                )
            ]
        )

    with pytest.raises(ComparisonEvidenceContractError, match="cannot claim a supported pair"):
        validate_comparison_evidence_records(
            [rejected_evidence(association_supported_pair=True)]
        )


def test_supported_and_pending_review_flags_cannot_be_swapped():
    with pytest.raises(ComparisonEvidenceContractError, match="cannot require manual review"):
        validate_comparison_evidence_records([matched_evidence(needs_manual_review=True)])

    with pytest.raises(ComparisonEvidenceContractError, match="must require manual review"):
        validate_comparison_evidence_records(
            [matched_evidence(identity_evidence_state="association_pending_review")]
        )


@pytest.mark.parametrize("source", ["kict_static_mask_cyclic_demo", "mixed_sources", "legacy_unverified_source"])
def test_unverified_sources_cannot_claim_verified_comparable(source):
    with pytest.raises(ComparisonEvidenceContractError, match="current source cannot"):
        validate_comparison_evidence_records(
            [matched_evidence(current_observation_source=source)]
        )

    with pytest.raises(ComparisonEvidenceContractError, match="previous sources cannot"):
        validate_comparison_evidence_records(
            [matched_evidence(previous_observation_sources=[source])]
        )


def test_comparison_status_must_follow_shared_claim_policy_composition():
    with pytest.raises(ComparisonEvidenceContractError, match="must be insufficient_history"):
        validate_comparison_evidence_records(
            [baseline_evidence(comparison_comparability_status="not_longitudinally_comparable")]
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("current_observation_source", []),
        ("previous_observation_sources", {}),
        ("current_comparability_status", None),
        ("previous_entity_type", []),
        ("evidence_valid", "true"),
        ("candidate_count", True),
        ("current_value", "120"),
        ("association_score", []),
    ],
)
def test_malformed_types_fail_with_contract_error_not_builtin_type_error(field, value):
    with pytest.raises(ComparisonEvidenceContractError):
        validate_comparison_evidence_records([baseline_evidence(**{field: value})])


def test_unknown_fields_and_evaluation_labels_are_rejected():
    for field in ("disease_id", "label_disease_id", "ground_truth_match"):
        with pytest.raises(ComparisonEvidenceContractError, match=f"unknown fields: {field}"):
            validate_comparison_evidence_records([baseline_evidence(**{field: "forbidden"})])


def test_non_string_field_name_fails_closed_without_sorting_type_error():
    record = baseline_evidence()
    record[1] = "invalid"  # type: ignore[index]

    with pytest.raises(ComparisonEvidenceContractError, match="field names must be strings"):
        validate_comparison_evidence_records([record])


def test_duplicate_evidence_id_and_current_observation_are_rejected_independently():
    first = baseline_evidence()
    duplicate_id = baseline_evidence(
        current_frame_id="41",
        current_image_id="I001_000041",
        current_observation_id="I001::obs_02",
        comparison_group_id="I001::obs_02::inspection_level_max_mask_area_px",
    )
    with pytest.raises(ComparisonEvidenceContractError, match="duplicate evidence_id"):
        validate_comparison_evidence_records([first, duplicate_id])

    duplicate_observation = baseline_evidence(evidence_id="EVD-I001-obs-02-area")
    with pytest.raises(ComparisonEvidenceContractError, match="duplicate comparison evidence observation"):
        validate_comparison_evidence_records([first, duplicate_observation])


def test_invalid_association_may_be_retained_only_as_blocked_invalid_evidence():
    record = baseline_evidence(
        identity_evidence_state="association_invalid",
        evidence_valid=False,
        invalid_reason="association_artifact_missing",
        source_association_artifact_sha256=None,
        source_association_manifest_sha256=None,
    )

    validated = validate_comparison_evidence_records([record])[0]
    decision = evaluate_claim_evidence(validated)

    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == "INVALID_ASSOCIATION_EVIDENCE"


def test_non_identity_evidence_failure_preserves_supported_identity_but_blocks_claims():
    record = matched_evidence(
        evidence_valid=False,
        invalid_reason="source_hash_validation_failed",
    )

    validated = validate_comparison_evidence_records([record])[0]
    decision = evaluate_claim_evidence(validated)

    assert validated["identity_evidence_state"] == "association_supported"
    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == "EVIDENCE_INVALID"


def test_missing_engineering_hash_is_retained_as_invalid_without_rewriting_identity():
    record = matched_evidence(
        evidence_valid=False,
        difference_valid=False,
        invalid_reason="source_engineering_artifact_missing",
        source_engineering_artifact_sha256=None,
    )

    validated = validate_comparison_evidence_records([record])[0]
    decision = evaluate_claim_evidence(validated)

    assert validated["identity_evidence_state"] == "association_supported"
    assert validated["source_engineering_artifact_sha256"] is None
    assert set(decision["capabilities"].values()) == {"blocked"}

    with pytest.raises(ComparisonEvidenceContractError, match="requires evidence_valid=false"):
        validate_comparison_evidence_records(
            [matched_evidence(source_engineering_artifact_sha256=None)]
        )

    with pytest.raises(ComparisonEvidenceContractError, match="requires a null"):
        validate_comparison_evidence_records(
            [
                matched_evidence(
                    evidence_valid=False,
                    difference_valid=False,
                    invalid_reason="source_engineering_artifact_missing",
                )
            ]
        )


def test_false_schema_or_source_prerequisite_requires_invalid_evidence():
    for field in (
        "evidence_schema_valid",
        "current_record_valid",
        "current_observation_source_declared",
        "previous_observation_sources_declared",
    ):
        with pytest.raises(ComparisonEvidenceContractError, match="evidence_valid=false"):
            validate_comparison_evidence_records([baseline_evidence(**{field: False})])

        record = baseline_evidence(
            **{field: False},
            evidence_valid=False,
            invalid_reason=f"{field}_failed",
        )
        decision = evaluate_claim_evidence(validate_comparison_evidence_records([record])[0])
        assert set(decision["capabilities"].values()) == {"blocked"}


def test_observation_id_requires_nonempty_suffix_and_timestamps_are_canonical_utc():
    with pytest.raises(ComparisonEvidenceContractError, match="inspection prefix"):
        validate_comparison_evidence_records(
            [baseline_evidence(current_observation_id="I001::")]
        )

    with pytest.raises(ComparisonEvidenceContractError, match="canonical UTC"):
        validate_comparison_evidence_records(
            [baseline_evidence(current_timestamp="2026-07-01 10:00:00")]
        )

    with pytest.raises(ComparisonEvidenceContractError, match="canonical UTC"):
        validate_comparison_evidence_records(
            [matched_evidence(previous_last_seen_timestamp="2026-07-01T10:00:00Z")]
        )

    with pytest.raises(ComparisonEvidenceContractError, match="valid canonical UTC"):
        validate_comparison_evidence_records(
            [baseline_evidence(current_timestamp="2026-02-31T10:00:00.000000Z")]
        )


def test_temporal_order_true_requires_current_after_previous():
    with pytest.raises(ComparisonEvidenceContractError, match="temporal_order_valid conflicts"):
        validate_comparison_evidence_records(
            [
                matched_evidence(
                    current_timestamp="2026-07-01T10:00:00.000000Z",
                    previous_last_seen_timestamp="2026-07-02T10:00:00.000000Z",
                )
            ]
        )


def test_matched_association_requires_at_least_one_candidate():
    with pytest.raises(ComparisonEvidenceContractError, match="at least one candidate"):
        validate_comparison_evidence_records([matched_evidence(candidate_count=0)])


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("association_score", "1.1", "between 0 and 1"),
        ("association_score", "-0.1", "between 0 and 1"),
        ("score_margin", "-0.1", "between 0 and 1"),
        ("match_type", "hard", "soft or uncertain"),
    ],
)
def test_no_id_association_metadata_stays_within_production_bounds(field, value, message):
    with pytest.raises(ComparisonEvidenceContractError, match=message):
        validate_comparison_evidence_records([matched_evidence(**{field: value})])


def test_validator_returns_isolated_copies_and_has_no_output_side_effect(tmp_path):
    source = baseline_evidence()
    original = deepcopy(source)

    result = validate_comparison_evidence_records([source])
    result[0]["current_value"] = 999

    assert source == original
    assert list(tmp_path.iterdir()) == []
