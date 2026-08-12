"""Pure Phase C contract for trusted human Association review decisions.

This module deliberately does not mutate workflow State, locks, Manifests, or
Claim Policy.  It validates immutable bytes and returns a zero-authority result
on every failure.  A caller may use ``human_verified`` only from a successful
result produced here; input JSON can never assert that state directly.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


REVIEW_DECISION_SCHEMA_VERSION = "inspection_association_review_decision_v1"
REVIEW_AUTHORITY_SCHEMA_VERSION = "inspection_review_authority_v1"
REVIEW_ACTION_SCOPE = "association_review"

_DECISION_FIELDS = (
    "schema_version",
    "run_id",
    "association_id",
    "association_snapshot_sha256",
    "decision",
    "reviewer_id",
    "authority_evidence_sha256",
    "decided_at",
    "rationale",
    "proof",
)
_PROOF_FIELDS = ("algorithm", "key_id", "signature")
_AUTHORITY_FIELDS = (
    "schema_version",
    "project_id",
    "reviewer_id",
    "key_id",
    "public_key_base64url",
    "scopes",
    "valid_from",
    "valid_until",
)
_SIGNED_FIELDS = _DECISION_FIELDS[:-1]
_OUTCOMES = {"accept_association", "reject_association"}
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_HASH_RE = re.compile(r"[0-9a-f]{64}")
_BASE64URL_RE = re.compile(r"[A-Za-z0-9_-]+")
_UTC_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_SIGNING_DOMAIN = b"inspection-association-review-decision-v1\x00"
_MAX_ARTIFACT_BYTES = 32 * 1024
_MAX_RATIONALE_CODEPOINTS = 2_000


class ReviewDecisionContractError(ValueError):
    """Raised by builders when a Phase C document is malformed."""


AssociationBindingValidator = Callable[[bytes, str, str], bool]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReviewDecisionContractError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _parse_json_object(data: object, *, label: str) -> dict[str, Any]:
    if type(data) is not bytes or not data or len(data) > _MAX_ARTIFACT_BYTES:
        raise ReviewDecisionContractError(f"{label} must be bounded immutable bytes")
    try:
        text = data.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_float=lambda _: (_ for _ in ()).throw(
                ReviewDecisionContractError(f"{label} must not contain floats")
            ),
            parse_constant=lambda _: (_ for _ in ()).throw(
                ReviewDecisionContractError(f"{label} must not contain non-finite numbers")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewDecisionContractError(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ReviewDecisionContractError(f"{label} must be a JSON object")
    return value


def _require_exact_fields(value: Mapping[str, Any], fields: tuple[str, ...], *, label: str) -> None:
    if any(type(key) is not str for key in value):
        raise ReviewDecisionContractError(f"{label} field names must be strings")
    expected = set(fields)
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        raise ReviewDecisionContractError(f"{label} missing fields: {', '.join(missing)}")
    if unknown:
        raise ReviewDecisionContractError(f"{label} has unknown fields: {', '.join(unknown)}")


def _require_nfc_string(value: Any, *, field: str, maximum: int = 2_000) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > maximum
        or unicodedata.normalize("NFC", value) != value
    ):
        raise ReviewDecisionContractError(f"{field} must be a canonical NFC string")
    return value


def _require_safe_id(value: Any, *, field: str) -> str:
    text = _require_nfc_string(value, field=field, maximum=128)
    if _SAFE_ID_RE.fullmatch(text) is None:
        raise ReviewDecisionContractError(f"{field} must be an unambiguous identifier")
    return text


def _require_hash(value: Any, *, field: str) -> str:
    if type(value) is not str or _HASH_RE.fullmatch(value) is None:
        raise ReviewDecisionContractError(f"{field} must be a lowercase SHA-256")
    return value


def _require_timestamp(value: Any, *, field: str) -> datetime:
    if type(value) is not str or _UTC_TIMESTAMP_RE.fullmatch(value) is None:
        raise ReviewDecisionContractError(f"{field} must use canonical UTC-second format")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ReviewDecisionContractError(f"{field} is not a valid UTC timestamp") from exc
    return parsed


def _require_base64url(value: Any, *, field: str, decoded_size: int) -> bytes:
    if type(value) is not str or _BASE64URL_RE.fullmatch(value) is None or "=" in value:
        raise ReviewDecisionContractError(f"{field} must be unpadded base64url")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, binascii.Error) as exc:
        raise ReviewDecisionContractError(f"{field} is not valid base64url") from exc
    if len(decoded) != decoded_size or _base64url(decoded) != value:
        raise ReviewDecisionContractError(f"{field} has the wrong canonical encoding")
    return decoded


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Return the contract's deterministic UTF-8 JSON representation.

    Input layout (whitespace, key order, or equivalent JSON string escaping)
    is intentionally not authoritative.  Parsed strings must still be NFC.
    """

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _validate_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    _require_exact_fields(value, _AUTHORITY_FIELDS, label="review authority")
    if value["schema_version"] != REVIEW_AUTHORITY_SCHEMA_VERSION:
        raise ReviewDecisionContractError("unsupported review authority schema")
    project_id = _require_safe_id(value["project_id"], field="project_id")
    reviewer_id = _require_safe_id(value["reviewer_id"], field="reviewer_id")
    key_id = _require_safe_id(value["key_id"], field="key_id")
    _require_base64url(value["public_key_base64url"], field="public_key_base64url", decoded_size=32)
    scopes = value["scopes"]
    if (
        type(scopes) is not list
        or not scopes
        or any(type(scope) is not str or _SAFE_ID_RE.fullmatch(scope) is None for scope in scopes)
        or scopes != sorted(set(scopes))
    ):
        raise ReviewDecisionContractError("scopes must be unique, safe, and stably sorted")
    valid_from = _require_timestamp(value["valid_from"], field="valid_from")
    valid_until = _require_timestamp(value["valid_until"], field="valid_until")
    if valid_until <= valid_from:
        raise ReviewDecisionContractError("review authority validity window is empty")
    return {
        "schema_version": REVIEW_AUTHORITY_SCHEMA_VERSION,
        "project_id": project_id,
        "reviewer_id": reviewer_id,
        "key_id": key_id,
        "public_key_base64url": value["public_key_base64url"],
        "scopes": list(scopes),
        "valid_from": value["valid_from"],
        "valid_until": value["valid_until"],
    }


def canonical_review_authority_bytes(authority: Mapping[str, Any]) -> bytes:
    """Validate and canonicalize a configured reviewer authority document."""

    if not isinstance(authority, Mapping):
        raise ReviewDecisionContractError("review authority must be an object")
    return _canonical_json_bytes(_validate_authority(authority))


def _validate_decision(value: Mapping[str, Any]) -> dict[str, Any]:
    _require_exact_fields(value, _DECISION_FIELDS, label="review decision")
    if value["schema_version"] != REVIEW_DECISION_SCHEMA_VERSION:
        raise ReviewDecisionContractError("unsupported review decision schema")
    run_id = _require_safe_id(value["run_id"], field="run_id")
    association_id = _require_safe_id(value["association_id"], field="association_id")
    association_hash = _require_hash(
        value["association_snapshot_sha256"], field="association_snapshot_sha256"
    )
    if value["decision"] not in _OUTCOMES:
        raise ReviewDecisionContractError("decision is not allowed")
    reviewer_id = _require_safe_id(value["reviewer_id"], field="reviewer_id")
    authority_hash = _require_hash(
        value["authority_evidence_sha256"], field="authority_evidence_sha256"
    )
    _require_timestamp(value["decided_at"], field="decided_at")
    rationale = _require_nfc_string(
        value["rationale"], field="rationale", maximum=_MAX_RATIONALE_CODEPOINTS
    )
    proof = value["proof"]
    if not isinstance(proof, Mapping):
        raise ReviewDecisionContractError("proof must be an object")
    _require_exact_fields(proof, _PROOF_FIELDS, label="proof")
    if proof["algorithm"] != "Ed25519":
        raise ReviewDecisionContractError("unsupported proof algorithm")
    key_id = _require_safe_id(proof["key_id"], field="proof.key_id")
    _require_base64url(proof["signature"], field="proof.signature", decoded_size=64)
    return {
        "schema_version": REVIEW_DECISION_SCHEMA_VERSION,
        "run_id": run_id,
        "association_id": association_id,
        "association_snapshot_sha256": association_hash,
        "decision": value["decision"],
        "reviewer_id": reviewer_id,
        "authority_evidence_sha256": authority_hash,
        "decided_at": value["decided_at"],
        "rationale": rationale,
        "proof": {
            "algorithm": "Ed25519",
            "key_id": key_id,
            "signature": proof["signature"],
        },
    }


def _signed_bytes(decision: Mapping[str, Any]) -> bytes:
    return _SIGNING_DOMAIN + _canonical_json_bytes(
        {field: decision[field] for field in _SIGNED_FIELDS}
    )


def sign_review_decision(
    *,
    private_key: Ed25519PrivateKey,
    run_id: str,
    association_id: str,
    association_snapshot_sha256: str,
    reviewer_id: str,
    authority_evidence_sha256: str,
    decided_at: str,
    decision: str,
    rationale: str,
    key_id: str,
) -> bytes:
    """Create canonical signed decision bytes without writing workflow state."""

    if not isinstance(private_key, Ed25519PrivateKey):
        raise ReviewDecisionContractError("private_key must be an Ed25519 private key")
    unsigned: dict[str, Any] = {
        "schema_version": REVIEW_DECISION_SCHEMA_VERSION,
        "run_id": run_id,
        "association_id": association_id,
        "association_snapshot_sha256": association_snapshot_sha256,
        "decision": decision,
        "reviewer_id": reviewer_id,
        "authority_evidence_sha256": authority_evidence_sha256,
        "decided_at": decided_at,
        "rationale": rationale,
    }
    candidate = {**unsigned, "proof": {"algorithm": "Ed25519", "key_id": key_id, "signature": "A" * 86}}
    normalized = _validate_decision(candidate)
    signature = private_key.sign(_signed_bytes(normalized))
    normalized["proof"]["signature"] = _base64url(signature)
    return _canonical_json_bytes(normalized)


@dataclass(frozen=True, init=False, slots=True)
class ReviewDecisionValidation:
    """Immutable trusted result or a zero-authority invalid result."""

    status: str
    denial_codes: tuple[str, ...]
    decision_sha256: str | None
    association_snapshot_sha256: str | None
    authority_evidence_sha256: str | None
    run_id: str | None
    association_id: str | None
    reviewer_id: str | None
    accepted_at: str | None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("ReviewDecisionValidation is created only by validate_review_decision")

    def __post_init__(self) -> None:
        if self.status not in {"human_verified", "human_rejected", "review_invalid"}:
            raise ValueError("unknown review validation status")
        bindings = (
            self.decision_sha256,
            self.association_snapshot_sha256,
            self.authority_evidence_sha256,
            self.run_id,
            self.association_id,
            self.reviewer_id,
            self.accepted_at,
        )
        if self.status == "review_invalid":
            if self.denial_codes != ("review_decision_invalid",) or any(
                value is not None for value in bindings
            ):
                raise ValueError("invalid review results must carry zero authority")
        elif self.denial_codes or any(value is None for value in bindings):
            raise ValueError("verified review results require complete bindings")

    @property
    def identity_evidence_state(self) -> str | None:
        return "human_verified" if self.status == "human_verified" else None


def _result(
    status: str,
    *,
    decision_sha256: str | None = None,
    association_snapshot_sha256: str | None = None,
    authority_evidence_sha256: str | None = None,
    run_id: str | None = None,
    association_id: str | None = None,
    reviewer_id: str | None = None,
    accepted_at: str | None = None,
) -> ReviewDecisionValidation:
    result = object.__new__(ReviewDecisionValidation)
    object.__setattr__(result, "status", status)
    object.__setattr__(
        result,
        "denial_codes",
        ("review_decision_invalid",) if status == "review_invalid" else (),
    )
    object.__setattr__(result, "decision_sha256", decision_sha256)
    object.__setattr__(result, "association_snapshot_sha256", association_snapshot_sha256)
    object.__setattr__(result, "authority_evidence_sha256", authority_evidence_sha256)
    object.__setattr__(result, "run_id", run_id)
    object.__setattr__(result, "association_id", association_id)
    object.__setattr__(result, "reviewer_id", reviewer_id)
    object.__setattr__(result, "accepted_at", accepted_at)
    result.__post_init__()
    return result


def _invalid() -> ReviewDecisionValidation:
    return _result("review_invalid")


def validate_review_decision(
    *,
    decision_bytes: bytes,
    association_snapshot_bytes: bytes,
    authority_evidence_bytes: bytes,
    trusted_authority_sha256: Iterable[str],
    expected_project_id: str,
    expected_run_id: str,
    expected_association_id: str,
    accepted_at: str,
    association_binding_validator: AssociationBindingValidator,
) -> ReviewDecisionValidation:
    """Validate a decision without filesystem reads or workflow mutations.

    ``trusted_authority_sha256`` is the configured trust allowlist.  The
    binding callback must validate that the immutable Association snapshot
    belongs to ``run_id`` and contains exactly ``association_id``.
    """

    try:
        if type(association_snapshot_bytes) is not bytes or not association_snapshot_bytes:
            raise ReviewDecisionContractError("Association snapshot must be immutable bytes")
        if not callable(association_binding_validator):
            raise ReviewDecisionContractError("Association binding validator is required")
        project_id = _require_safe_id(expected_project_id, field="expected_project_id")
        run_id = _require_safe_id(expected_run_id, field="expected_run_id")
        association_id = _require_safe_id(
            expected_association_id, field="expected_association_id"
        )
        accepted_time = _require_timestamp(accepted_at, field="accepted_at")

        trusted = tuple(trusted_authority_sha256)
        if not trusted or any(type(item) is not str or _HASH_RE.fullmatch(item) is None for item in trusted):
            raise ReviewDecisionContractError("trusted authority allowlist is malformed")
        if len(trusted) != len(set(trusted)):
            raise ReviewDecisionContractError("trusted authority allowlist contains duplicates")

        authority = _validate_authority(
            _parse_json_object(authority_evidence_bytes, label="review authority")
        )
        canonical_authority = _canonical_json_bytes(authority)
        authority_hash = _sha256(canonical_authority)
        if authority_hash not in trusted:
            raise ReviewDecisionContractError("review authority is not trusted")
        if authority["project_id"] != project_id or REVIEW_ACTION_SCOPE not in authority["scopes"]:
            raise ReviewDecisionContractError("review authority scope does not permit this review")

        decision = _validate_decision(
            _parse_json_object(decision_bytes, label="review decision")
        )
        if decision["run_id"] != run_id or decision["association_id"] != association_id:
            raise ReviewDecisionContractError("review decision subject is not the expected subject")
        if decision["authority_evidence_sha256"] != authority_hash:
            raise ReviewDecisionContractError("decision authority binding does not match")
        if decision["reviewer_id"] != authority["reviewer_id"]:
            raise ReviewDecisionContractError("reviewer identity does not match authority")
        if decision["proof"]["key_id"] != authority["key_id"]:
            raise ReviewDecisionContractError("proof key does not match authority")

        decided_time = _require_timestamp(decision["decided_at"], field="decided_at")
        valid_from = _require_timestamp(authority["valid_from"], field="valid_from")
        valid_until = _require_timestamp(authority["valid_until"], field="valid_until")
        if not (valid_from <= decided_time <= accepted_time <= valid_until):
            raise ReviewDecisionContractError("review or acceptance time is outside authority validity")

        public_key = Ed25519PublicKey.from_public_bytes(
            _require_base64url(
                authority["public_key_base64url"],
                field="public_key_base64url",
                decoded_size=32,
            )
        )
        signature = _require_base64url(
            decision["proof"]["signature"], field="proof.signature", decoded_size=64
        )
        public_key.verify(signature, _signed_bytes(decision))

        snapshot_hash = _sha256(association_snapshot_bytes)
        if decision["association_snapshot_sha256"] != snapshot_hash:
            raise ReviewDecisionContractError("Association snapshot binding does not match")
        if association_binding_validator(
            association_snapshot_bytes,
            decision["run_id"],
            decision["association_id"],
        ) is not True:
            raise ReviewDecisionContractError("Association snapshot subject binding is invalid")

        canonical_decision = _canonical_json_bytes(decision)
        return _result(
            "human_verified" if decision["decision"] == "accept_association" else "human_rejected",
            decision_sha256=_sha256(canonical_decision),
            association_snapshot_sha256=snapshot_hash,
            authority_evidence_sha256=authority_hash,
            run_id=decision["run_id"],
            association_id=decision["association_id"],
            reviewer_id=decision["reviewer_id"],
            accepted_at=accepted_at,
        )
    except Exception:
        return _invalid()


__all__ = [
    "AssociationBindingValidator",
    "REVIEW_ACTION_SCOPE",
    "REVIEW_AUTHORITY_SCHEMA_VERSION",
    "REVIEW_DECISION_SCHEMA_VERSION",
    "ReviewDecisionContractError",
    "ReviewDecisionValidation",
    "canonical_review_authority_bytes",
    "sign_review_decision",
    "validate_review_decision",
]
