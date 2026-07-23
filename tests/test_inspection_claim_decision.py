from __future__ import annotations

from copy import deepcopy

import pytest

import orchestrator.inspection_workflow.claim_decision as claim_decision_module
from orchestrator.inspection_workflow import (
    CLAIM_DECISION_RECORD_FIELDS,
    CLAIM_DECISION_SCHEMA_VERSION,
    CLAIM_DECISION_SUMMARY_FIELDS,
    ClaimDecisionContractError,
    build_claim_decision_document,
    validate_claim_decision_document,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64


def baseline_evidence(**overrides):
    record = {
        "comparison_evidence_schema_version": "comparison_evidence_v1",
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
        source_memory_snapshot_sha256=SHA_E,
    )
    record.update(overrides)
    return record


def build_document(records=None):
    return build_claim_decision_document(
        records if records is not None else [baseline_evidence()],
        run_id="run_012",
        plan_fingerprint=SHA_A,
        source_comparison_evidence_sha256=SHA_E,
    )


def validate_document(document, records=None):
    return validate_claim_decision_document(
        document,
        records if records is not None else [baseline_evidence()],
        expected_run_id="run_012",
        expected_plan_fingerprint=SHA_A,
        expected_source_comparison_evidence_sha256=SHA_E,
    )


def test_baseline_builds_static_only_claim_decision_document():
    document = build_document()
    decision = document["record_decisions"][0]

    assert document["schema_version"] == CLAIM_DECISION_SCHEMA_VERSION
    assert set(decision) == set(CLAIM_DECISION_RECORD_FIELDS)
    assert set(document["summary"]) == set(CLAIM_DECISION_SUMMARY_FIELDS)
    assert decision["identity_evidence_state"] == "association_not_applicable"
    assert decision["capabilities"]["static_descriptive_audit"] == "allowed"
    assert decision["capabilities"]["descriptive_difference_claim"] == "blocked"
    assert decision["capabilities"]["directional_change_claim"] == "blocked"
    assert document["summary"] == {
        "total_records": 1,
        "static_audit_allowed": 1,
        "difference_allowed_with_limits": 0,
        "difference_blocked": 1,
        "directional_allowed": 0,
        "physical_allowed": 0,
        "pattern_allowed": 0,
        "prediction_allowed": 0,
    }
    assert validate_document(document) == document


def test_verified_matched_evidence_builds_limited_difference_and_directional_decision():
    document = build_document([matched_evidence()])
    decision = document["record_decisions"][0]

    assert decision["current_record_id"] == "ASSOC-I002-obs-01"
    assert decision["capabilities"]["descriptive_difference_claim"] == "allowed_with_limits"
    assert decision["capabilities"]["directional_change_claim"] == "allowed_with_limits"
    assert decision["capabilities"]["physical_quantity_change_claim"] == "blocked"
    assert document["summary"]["difference_allowed_with_limits"] == 1
    assert document["summary"]["directional_allowed"] == 1


def test_nonidentity_provenance_failure_is_blocked_without_rewriting_identity_state():
    evidence = matched_evidence(
        source_engineering_artifact_sha256=None,
        evidence_valid=False,
        invalid_reason="source_engineering_artifact_missing",
    )

    document = build_document([evidence])
    decision = document["record_decisions"][0]

    assert decision["identity_evidence_state"] == "association_supported"
    assert decision["evidence_valid"] is False
    assert decision["invalid_reason"] == "source_engineering_artifact_missing"
    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == "EVIDENCE_INVALID"


def test_association_invalid_is_blocked_and_retains_identity_state():
    evidence = baseline_evidence(
        identity_evidence_state="association_invalid",
        evidence_valid=False,
        invalid_reason="association_artifact_missing",
        source_association_artifact_sha256=None,
        source_association_manifest_sha256=None,
    )

    document = build_document([evidence])
    decision = document["record_decisions"][0]

    assert decision["identity_evidence_state"] == "association_invalid"
    assert decision["evidence_valid"] is False
    assert decision["invalid_reason"] == "association_artifact_missing"
    assert set(decision["capabilities"].values()) == {"blocked"}
    assert decision["reason_codes"][0] == "INVALID_ASSOCIATION_EVIDENCE"


def test_record_order_is_stable_and_input_is_not_mutated():
    source = [matched_evidence(), baseline_evidence()]
    original = deepcopy(source)

    document = build_document(source)

    assert source == original
    assert [row["evidence_id"] for row in document["record_decisions"]] == [
        "EVD-I001-obs-01-area",
        "EVD-I002-obs-01-area",
    ]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda document: document["record_decisions"][0]["capabilities"].update(
                {"directional_change_claim": "allowed_with_limits"}
            ),
            "record_decisions",
        ),
        (
            lambda document: document["record_decisions"][0][
                "required_language_qualifiers"
            ].clear(),
            "record_decisions",
        ),
        (
            lambda document: document["record_decisions"][0]["template_ids"].update(
                {"static_descriptive_audit": "tampered_template"}
            ),
            "record_decisions",
        ),
        (
            lambda document: document["record_decisions"][0][
                "capability_reasons"
            ].update({"static_descriptive_audit": "TAMPERED_REASON"}),
            "record_decisions",
        ),
        (
            lambda document: document["record_decisions"][0]["reason_codes"].append(
                "TAMPERED_REASON"
            ),
            "record_decisions",
        ),
        (
            lambda document: document["record_decisions"][0].update(
                {"source_current_record_fingerprint": SHA_E}
            ),
            "record_decisions",
        ),
        (
            lambda document: document["summary"].update({"static_audit_allowed": 0}),
            "summary",
        ),
        (
            lambda document: document.update({"claim_policy_sha256": SHA_D}),
            "claim_policy_sha256",
        ),
        (
            lambda document: document.update({"claim_evaluator_sha256": SHA_D}),
            "claim_evaluator_sha256",
        ),
        (
            lambda document: document.update(
                {"claim_evaluator_contract_version": "tampered_evaluator"}
            ),
            "claim_evaluator_contract_version",
        ),
        (
            lambda document: document.update(
                {"source_association_manifest_sha256": SHA_D}
            ),
            "source_association_manifest_sha256",
        ),
    ],
)
def test_validator_rejects_tampered_decisions_and_summary(mutate, message):
    evidence = [baseline_evidence()]
    document = build_document(evidence)
    mutate(document)

    with pytest.raises(ClaimDecisionContractError, match=message):
        validate_document(document, evidence)


def test_validator_rejects_unknown_fields_and_wrong_source_hash():
    evidence = [baseline_evidence()]
    document = build_document(evidence)
    document["unexpected"] = True

    with pytest.raises(ClaimDecisionContractError, match="unknown fields"):
        validate_document(document, evidence)

    document = build_document(evidence)
    document["source_comparison_evidence_sha256"] = SHA_D
    with pytest.raises(ClaimDecisionContractError, match="source comparison evidence"):
        validate_document(document, evidence)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("run_id", "run_999", "run_id"),
        ("plan_fingerprint", SHA_D, "plan_fingerprint"),
    ],
)
def test_validator_uses_external_run_and_plan_identity(field, value, message):
    document = build_document()
    document[field] = value

    with pytest.raises(ClaimDecisionContractError, match=message):
        validate_document(document)


def test_builder_rejects_mixed_association_artifact_provenance():
    records = [
        baseline_evidence(),
        matched_evidence(source_association_artifact_sha256=SHA_E),
    ]

    with pytest.raises(ClaimDecisionContractError, match="single association artifact"):
        build_document(records)


def test_builder_exposes_evidence_failure_as_claim_decision_contract_error():
    malformed = baseline_evidence()
    malformed.pop("current_observation_id")

    with pytest.raises(
        ClaimDecisionContractError,
        match="comparison evidence validation failed.*current_observation_id",
    ) as captured:
        build_document([malformed])

    assert captured.value.__cause__ is not None


def test_empty_evidence_fails_closed_instead_of_substituting_baseline():
    with pytest.raises(ClaimDecisionContractError, match="must not be empty"):
        build_document([])


def test_builder_rejects_evaluator_schema_drift(monkeypatch):
    real_evaluator = claim_decision_module.evaluate_claim_evidence

    def drifted_evaluator(evidence):
        decision = real_evaluator(evidence)
        decision["schema_version"] = "claim_decision_v999"
        return decision

    monkeypatch.setattr(
        claim_decision_module,
        "evaluate_claim_evidence",
        drifted_evaluator,
    )

    with pytest.raises(ClaimDecisionContractError, match="schema_version"):
        build_document()


@pytest.mark.parametrize("run_id", ["../run_012", "run-012", "RUN_012", "run_12"])
def test_builder_rejects_noncanonical_run_id(run_id):
    with pytest.raises(ClaimDecisionContractError, match="run_id"):
        build_claim_decision_document(
            [baseline_evidence()],
            run_id=run_id,
            plan_fingerprint=SHA_A,
            source_comparison_evidence_sha256=SHA_E,
        )


def test_claim_decision_contract_has_no_artifact_side_effects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    document = build_document()
    validate_document(document)

    assert list(tmp_path.iterdir()) == []
