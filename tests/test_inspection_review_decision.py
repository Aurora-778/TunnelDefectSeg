from __future__ import annotations

import base64
import hashlib
import json
from copy import deepcopy

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from orchestrator.inspection_workflow.review_decision import (
    REVIEW_ACTION_SCOPE,
    REVIEW_AUTHORITY_SCHEMA_VERSION,
    ReviewDecisionContractError,
    canonical_review_authority_bytes,
    sign_review_decision,
    validate_review_decision,
)


def test_phase_c_contract_is_available_from_package_root():
    from orchestrator import inspection_workflow

    assert inspection_workflow.REVIEW_ACTION_SCOPE == REVIEW_ACTION_SCOPE
    assert (
        inspection_workflow.REVIEW_AUTHORITY_SCHEMA_VERSION
        == REVIEW_AUTHORITY_SCHEMA_VERSION
    )
    assert inspection_workflow.sign_review_decision is sign_review_decision
    assert inspection_workflow.validate_review_decision is validate_review_decision


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _snapshot(run_id: str = "run_001", association_id: str = "assoc_001") -> bytes:
    return json.dumps(
        {
            "schema_version": "test_association_snapshot_v1",
            "run_id": run_id,
            "association_ids": [association_id],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _binding(snapshot: bytes, run_id: str, association_id: str) -> bool:
    value = json.loads(snapshot)
    return (
        value == {
            "association_ids": value["association_ids"],
            "run_id": run_id,
            "schema_version": "test_association_snapshot_v1",
        }
        and association_id in value["association_ids"]
        and len(value["association_ids"]) == len(set(value["association_ids"]))
    )


@pytest.fixture()
def signed_review():
    key = Ed25519PrivateKey.generate()
    public_bytes = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    authority = {
        "schema_version": REVIEW_AUTHORITY_SCHEMA_VERSION,
        "project_id": "tunnel-inspection",
        "reviewer_id": "reviewer-001",
        "key_id": "reviewer-001-key-01",
        "public_key_base64url": _b64url(public_bytes),
        "scopes": [REVIEW_ACTION_SCOPE],
        "valid_from": "2026-08-01T00:00:00Z",
        "valid_until": "2026-09-01T00:00:00Z",
    }
    authority_bytes = canonical_review_authority_bytes(authority)
    snapshot = _snapshot()

    def sign(**overrides):
        values = {
            "private_key": key,
            "run_id": "run_001",
            "association_id": "assoc_001",
            "association_snapshot_sha256": _sha256(snapshot),
            "reviewer_id": "reviewer-001",
            "authority_evidence_sha256": _sha256(authority_bytes),
            "decided_at": "2026-08-12T08:00:00Z",
            "decision": "accept_association",
            "rationale": "The same lining defect is visible in both bound observations.",
            "key_id": "reviewer-001-key-01",
        }
        values.update(overrides)
        return sign_review_decision(**values)

    def validate(decision_bytes, **overrides):
        values = {
            "decision_bytes": decision_bytes,
            "association_snapshot_bytes": snapshot,
            "authority_evidence_bytes": authority_bytes,
            "trusted_authority_sha256": {_sha256(authority_bytes)},
            "expected_project_id": "tunnel-inspection",
            "expected_run_id": "run_001",
            "expected_association_id": "assoc_001",
            "accepted_at": "2026-08-12T08:01:00Z",
            "association_binding_validator": _binding,
        }
        values.update(overrides)
        return validate_review_decision(**values)

    return {
        "authority": authority,
        "authority_bytes": authority_bytes,
        "snapshot": snapshot,
        "sign": sign,
        "validate": validate,
    }


def test_signed_accept_yields_human_verified_with_complete_bindings(signed_review):
    decision_bytes = signed_review["sign"]()
    result = signed_review["validate"](decision_bytes)

    assert result.status == "human_verified"
    assert result.identity_evidence_state == "human_verified"
    assert result.denial_codes == ()
    assert result.decision_sha256 == _sha256(decision_bytes)
    assert result.association_snapshot_sha256 == _sha256(signed_review["snapshot"])
    assert result.authority_evidence_sha256 == _sha256(signed_review["authority_bytes"])
    assert result.run_id == "run_001"
    assert result.association_id == "assoc_001"
    assert result.reviewer_id == "reviewer-001"
    assert result.accepted_at == "2026-08-12T08:01:00Z"


def test_json_layout_does_not_change_canonical_decision_identity(signed_review):
    canonical = signed_review["sign"]()
    value = json.loads(canonical)
    reordered = json.dumps(value, ensure_ascii=False, indent=4).encode("utf-8")

    canonical_result = signed_review["validate"](canonical)
    reordered_result = signed_review["validate"](reordered)

    assert reordered_result.status == "human_verified"
    assert reordered_result.decision_sha256 == canonical_result.decision_sha256

    escaped = canonical.replace(b'"run_id":"run_001"', b'"run_id":"ru\\u006e_001"')
    escaped_result = signed_review["validate"](escaped)
    assert escaped_result.status == "human_verified"
    assert escaped_result.decision_sha256 == canonical_result.decision_sha256


def test_reject_is_authenticated_but_never_promotes_identity(signed_review):
    result = signed_review["validate"](
        signed_review["sign"](decision="reject_association")
    )

    assert result.status == "human_rejected"
    assert result.identity_evidence_state is None
    assert result.denial_codes == ()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", "run_999"),
        ("association_id", "assoc_999"),
        ("decision", "reject_association"),
        ("reviewer_id", "reviewer-999"),
        ("decided_at", "2026-08-12T08:00:01Z"),
        ("rationale", "Changed after signing."),
    ],
)
def test_one_field_tampering_invalidates_signature(signed_review, field, value):
    document = json.loads(signed_review["sign"]())
    document[field] = value
    tampered = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()

    result = signed_review["validate"](tampered)

    assert result.status == "review_invalid"
    assert result.identity_evidence_state is None
    assert result.decision_sha256 is None


def test_cross_run_and_cross_association_replay_fail_closed(signed_review):
    decision = signed_review["sign"]()

    cross_run = signed_review["validate"](decision, expected_run_id="run_002")
    cross_association = signed_review["validate"](
        decision, expected_association_id="assoc_002"
    )

    assert cross_run.status == "review_invalid"
    assert cross_association.status == "review_invalid"


def test_snapshot_change_and_false_subject_binding_fail_closed(signed_review):
    decision = signed_review["sign"]()

    changed = signed_review["validate"](
        decision,
        association_snapshot_bytes=_snapshot(association_id="assoc_002"),
    )
    false_binding = signed_review["validate"](
        decision,
        association_binding_validator=lambda *_: False,
    )

    assert changed.status == "review_invalid"
    assert false_binding.status == "review_invalid"


def test_untrusted_wrong_identity_and_expired_authority_fail_closed(signed_review):
    decision = signed_review["sign"]()
    untrusted = signed_review["validate"](
        decision, trusted_authority_sha256={"0" * 64}
    )

    changed_authority = deepcopy(signed_review["authority"])
    changed_authority["reviewer_id"] = "reviewer-999"
    changed_authority_bytes = canonical_review_authority_bytes(changed_authority)
    wrong_identity = signed_review["validate"](
        decision,
        authority_evidence_bytes=changed_authority_bytes,
        trusted_authority_sha256={_sha256(changed_authority_bytes)},
    )
    expired = signed_review["validate"](
        decision, accepted_at="2026-09-01T00:00:01Z"
    )

    assert untrusted.status == "review_invalid"
    assert wrong_identity.status == "review_invalid"
    assert expired.status == "review_invalid"


def test_wrong_project_missing_scope_and_not_yet_valid_authority_fail_closed(signed_review):
    decision = signed_review["sign"]()
    wrong_project = signed_review["validate"](
        decision, expected_project_id="another-project"
    )

    authority = deepcopy(signed_review["authority"])
    authority["scopes"] = ["unrelated_scope"]
    authority_bytes = canonical_review_authority_bytes(authority)
    wrong_scope_decision = signed_review["sign"](
        authority_evidence_sha256=_sha256(authority_bytes)
    )
    wrong_scope = signed_review["validate"](
        wrong_scope_decision,
        authority_evidence_bytes=authority_bytes,
        trusted_authority_sha256={_sha256(authority_bytes)},
    )
    not_yet_valid = signed_review["validate"](
        signed_review["sign"](decided_at="2026-07-31T23:59:59Z"),
        accepted_at="2026-08-01T00:00:00Z",
    )
    decision_after_acceptance = signed_review["validate"](
        decision, accepted_at="2026-08-12T07:59:59Z"
    )

    assert wrong_project.status == "review_invalid"
    assert wrong_scope.status == "review_invalid"
    assert not_yet_valid.status == "review_invalid"
    assert decision_after_acceptance.status == "review_invalid"


def test_authority_time_boundaries_are_inclusive(signed_review):
    decision = signed_review["sign"](decided_at="2026-08-01T00:00:00Z")

    result = signed_review["validate"](
        decision, accepted_at="2026-09-01T00:00:00Z"
    )

    assert result.status == "human_verified"


@pytest.mark.parametrize(
    ("target", "field", "value"),
    [
        ("proof", "algorithm", "none"),
        ("proof", "key_id", "another-key"),
        ("proof", "signature", "A" * 86),
        ("proof", "unknown", "value"),
        ("decision", "unknown", "value"),
    ],
)
def test_proof_and_nested_schema_tampering_fail_closed(
    signed_review, target, field, value
):
    document = json.loads(signed_review["sign"]())
    subject = document["proof"] if target == "proof" else document
    subject[field] = value
    tampered = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()

    result = signed_review["validate"](tampered)

    assert result.status == "review_invalid"


def test_malformed_public_key_and_duplicate_authority_field_fail_closed(signed_review):
    malformed = deepcopy(signed_review["authority"])
    malformed["public_key_base64url"] = "A" * 42
    malformed_bytes = json.dumps(malformed, sort_keys=True, separators=(",", ":")).encode()
    malformed_hash = "0" * 64
    malformed_result = signed_review["validate"](
        signed_review["sign"](authority_evidence_sha256=malformed_hash),
        authority_evidence_bytes=malformed_bytes,
        trusted_authority_sha256={malformed_hash},
    )

    duplicate_bytes = signed_review["authority_bytes"].replace(
        b'"project_id":"tunnel-inspection"',
        b'"project_id":"tunnel-inspection","project_id":"tunnel-inspection"',
    )
    duplicate_result = signed_review["validate"](
        signed_review["sign"](), authority_evidence_bytes=duplicate_bytes
    )

    assert malformed_result.status == "review_invalid"
    assert duplicate_result.status == "review_invalid"


def test_truthy_non_boolean_binding_result_is_not_authority(signed_review):
    result = signed_review["validate"](
        signed_review["sign"](),
        association_binding_validator=lambda *_: 1,
    )

    assert result.status == "review_invalid"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw[:-1] + b',"unknown":true}',
        lambda raw: raw.replace(b'"run_id":"run_001"', b'"run_id":"run_001","run_id":"run_001"'),
        lambda raw: raw.replace(b'"decided_at":"2026-08-12T08:00:00Z"', b'"decided_at":"2026-08-12T08:00:00+00:00"'),
        lambda raw: raw.replace(b'"association_snapshot_sha256":"', b'"association_snapshot_sha256":"A'),
    ],
)
def test_noncanonical_or_ambiguous_documents_fail_closed(signed_review, mutate):
    result = signed_review["validate"](mutate(signed_review["sign"]()))
    assert result.status == "review_invalid"
    assert result.denial_codes == ("review_decision_invalid",)


def test_signer_rejects_unsupported_outcome_and_oversized_rationale(signed_review):
    with pytest.raises(ReviewDecisionContractError, match="decision is not allowed"):
        signed_review["sign"](decision="human_verified")
    with pytest.raises(ReviewDecisionContractError, match="canonical NFC string"):
        signed_review["sign"](rationale="x" * 2_001)


def test_valid_signature_from_unbound_key_fails_closed(signed_review):
    other_key = Ed25519PrivateKey.generate()
    decision = signed_review["sign"](private_key=other_key)
    binding_called = False

    def record_binding(*args):
        nonlocal binding_called
        binding_called = True
        return _binding(*args)

    result = signed_review["validate"](
        decision, association_binding_validator=record_binding
    )

    assert result.status == "review_invalid"
    assert binding_called is False


def test_validation_is_pure_and_exceptions_from_binding_fail_closed(signed_review):
    decision = signed_review["sign"]()
    called = 0

    def broken_binding(*_):
        nonlocal called
        called += 1
        raise RuntimeError("simulated binding failure")

    first = signed_review["validate"](
        decision, association_binding_validator=broken_binding
    )
    second = signed_review["validate"](
        decision, association_binding_validator=broken_binding
    )

    assert called == 2
    assert first == second
    assert first.status == "review_invalid"
    assert first.denial_codes == ("review_decision_invalid",)
    assert first.identity_evidence_state is None
    assert first.decision_sha256 is None
    assert first.association_snapshot_sha256 is None
    assert first.authority_evidence_sha256 is None
    assert first.run_id is None
    assert first.association_id is None
    assert first.reviewer_id is None
    assert first.accepted_at is None
