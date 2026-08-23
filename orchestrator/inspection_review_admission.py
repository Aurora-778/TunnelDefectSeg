"""Windows-x64 Phase C-2 fixed-snapshot review admission.

This module deliberately does not mutate workflow State, locks, Manifests, or
Claim Policy.  It validates immutable bytes and returns a zero-authority result
on every failure.  A caller may use ``human_verified`` only from a successful
result produced here; input JSON can never assert that state directly.
Unsupported platforms fail closed before any project-file access.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from orchestrator.inspection_review_root_capability import (
    CONTEXT_UNAVAILABLE,
    _ConfiguredProjectContext,
    _PendingProjectContext,
    _bootstrap_review_project_context,
)

if sys.platform == "win32":
    from orchestrator import _inspection_review_fs_windows as _fs
else:
    _fs = None

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


ASSOCIATION_PROJECTION_FIELDS = (
    "source_reference_schema_version",
    "association_id",
    "inspection_id",
    "current_observation_id",
    "frame_id",
    "image_id",
    "memory_id",
    "association_status",
    "association_mode",
    "use_disease_id_score",
    "association_score",
    "match_type",
    "candidate_count",
    "score_margin",
    "conflict_reason",
    "needs_manual_review",
)
_MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
_MAX_RECORD_BYTES = 1024 * 1024
_MAX_ROWS = 65_536
_MAX_FIELD_CODEPOINTS = 65_536
_MAX_AUTHORITIES = 256


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
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ReviewDecisionContractError(f"{field} must be valid UTF-8") from exc
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
    decision = _require_nfc_string(value["decision"], field="decision", maximum=32)
    if decision not in _OUTCOMES:
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
        "decision": decision,
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
        raise TypeError("ReviewDecisionValidation is created only by admit_review_decision")

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


def _authority_result(
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
        (),
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
    result = object.__new__(ReviewDecisionValidation)
    object.__setattr__(result, "status", "review_invalid")
    object.__setattr__(result, "denial_codes", ("review_decision_invalid",))
    object.__setattr__(result, "decision_sha256", None)
    object.__setattr__(result, "association_snapshot_sha256", None)
    object.__setattr__(result, "authority_evidence_sha256", None)
    object.__setattr__(result, "run_id", None)
    object.__setattr__(result, "association_id", None)
    object.__setattr__(result, "reviewer_id", None)
    object.__setattr__(result, "accepted_at", None)
    result.__post_init__()
    return result


def _parse_association_chunks(chunks: object, target_association_id: str) -> bool:
    if type(chunks) not in (tuple, list) or not chunks or any(
        type(chunk) not in (bytes, memoryview) for chunk in chunks
    ):
        raise ReviewDecisionContractError("Association snapshot chunks are invalid")
    snapshot_size = sum(len(chunk) for chunk in chunks)
    if snapshot_size == 0 or snapshot_size > _MAX_SNAPSHOT_BYTES:
        raise ReviewDecisionContractError("Association snapshot is invalid")
    prefix_parts: list[bytes] = []
    prefix_size = 0
    for chunk in chunks:
        if prefix_size >= 3:
            break
        part = chunk[: 3 - prefix_size]
        prefix_parts.append(part)
        prefix_size += len(part)
    prefix = b"".join(prefix_parts)
    if prefix == b"\xef\xbb\xbf":
        raise ReviewDecisionContractError("Association snapshot must not contain a BOM")

    rows = 0
    target_count = 0
    seen: set[str] = set()
    fields: list[str] = []
    field = bytearray()
    record_bytes = 0
    quoted = False
    after_quote = False
    pending_quote = False
    pending_cr = False

    def finish_field() -> None:
        nonlocal field
        try:
            value = bytes(field).decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ReviewDecisionContractError("Association CSV is not strict UTF-8") from exc
        if len(value) > _MAX_FIELD_CODEPOINTS:
            raise ReviewDecisionContractError("Association CSV field is too long")
        fields.append(value)
        field = bytearray()
        if len(fields) > len(ASSOCIATION_PROJECTION_FIELDS):
            raise ReviewDecisionContractError("Association CSV has too many columns")

    def finish_record() -> None:
        nonlocal fields, rows, target_count, record_bytes
        finish_field()
        if len(fields) != len(ASSOCIATION_PROJECTION_FIELDS):
            raise ReviewDecisionContractError("Association CSV has the wrong column count")
        if rows == 0:
            if tuple(fields) != ASSOCIATION_PROJECTION_FIELDS:
                raise ReviewDecisionContractError("Association CSV header is invalid")
        else:
            association_id = _require_safe_id(fields[1], field="association_id")
            if association_id in seen:
                raise ReviewDecisionContractError("Association CSV contains duplicate IDs")
            seen.add(association_id)
            if association_id == target_association_id:
                target_count += 1
            if rows > _MAX_ROWS:
                raise ReviewDecisionContractError("Association CSV has too many rows")
        rows += 1
        fields = []
        record_bytes = 0

    for chunk in chunks:
      for byte in chunk:
        if pending_cr:
            pending_cr = False
            if byte == 0x0A:
                record_bytes += 1
                if record_bytes > _MAX_RECORD_BYTES:
                    raise ReviewDecisionContractError("Association CSV record is too large")
                finish_record()
                continue
            finish_record()
        record_bytes += 1
        if record_bytes > _MAX_RECORD_BYTES:
            raise ReviewDecisionContractError("Association CSV record is too large")
        if quoted:
            if pending_quote:
                if byte == 0x22:
                    field.append(byte)
                    pending_quote = False
                    continue
                quoted = False
                after_quote = True
                pending_quote = False
                # Reprocess the current byte under the after-quote state.
            if byte == 0x22:
                pending_quote = True
                continue
            if quoted:
                field.append(byte)
                continue
        if after_quote:
            if byte == 0x2C:
                if len(fields) >= len(ASSOCIATION_PROJECTION_FIELDS) - 1:
                    raise ReviewDecisionContractError("Association CSV has too many columns")
                finish_field()
                after_quote = False
                continue
            if byte in (0x0A, 0x0D):
                if byte == 0x0D:
                    pending_cr = True
                else:
                    finish_record()
                after_quote = False
                continue
            raise ReviewDecisionContractError("characters follow a closing CSV quote")
        if byte == 0x22:
            if field:
                raise ReviewDecisionContractError("CSV quote appears inside an unquoted field")
            quoted = True
        elif byte == 0x2C:
            if len(fields) >= len(ASSOCIATION_PROJECTION_FIELDS) - 1:
                raise ReviewDecisionContractError("Association CSV has too many columns")
            finish_field()
        elif byte in (0x0A, 0x0D):
            if byte == 0x0D:
                pending_cr = True
            else:
                finish_record()
        else:
            field.append(byte)

    if pending_cr:
        finish_record()
    if quoted and not pending_quote:
        raise ReviewDecisionContractError("Association CSV has an unclosed quote")
    if pending_quote:
        quoted = False
        after_quote = True
    if fields or field or after_quote:
        finish_record()
    if rows < 2 or target_count != 1:
        raise ReviewDecisionContractError("Association target is missing")
    return True


def _parse_association_snapshot(snapshot: bytes, target_association_id: str) -> bool:
    if type(snapshot) is not bytes:
        raise ReviewDecisionContractError("Association snapshot is invalid")
    view = memoryview(snapshot)
    chunks = tuple(view[offset : offset + 65_536] for offset in range(0, len(view), 65_536))
    return _parse_association_chunks(chunks, target_association_id)


def _trusted_allowlist(value: object) -> frozenset[str]:
    if type(value) not in (set, frozenset):
        raise ReviewDecisionContractError("trusted authority allowlist must be a built-in set")
    if not 1 <= len(value) <= _MAX_AUTHORITIES:
        raise ReviewDecisionContractError("trusted authority allowlist has invalid size")
    if any(type(item) is not str or _HASH_RE.fullmatch(item) is None for item in value):
        raise ReviewDecisionContractError("trusted authority allowlist is malformed")
    return frozenset(value)


def _time_is_valid(
    *, decided_at: datetime, accepted_at: datetime, valid_from: datetime, valid_until: datetime
) -> bool:
    return (
        valid_from <= decided_at <= accepted_at <= valid_until
        and accepted_at - decided_at <= timedelta(minutes=15)
    )


def _read_fixed_snapshot(context: _ConfiguredProjectContext, run_id: str) -> tuple[bytes, str]:
    if _fs is None:
        raise ReviewDecisionContractError("filesystem backend is unavailable")
    duplicate = context._duplicate_for_admission()
    if duplicate is None:
        raise ReviewDecisionContractError("project context is unavailable")
    root, expected_identity, project_id, _project_root = duplicate
    ancestors: list[int] = []
    leaf: int | None = None
    snapshot: bytes | None = None
    cleanup_failed = False

    def close_for_cleanup(handle: int) -> bool:
        _fs.close_capability(handle)
        return False

    try:
        _fs.require_directory(root, expected_identity)
        parent = root
        for component in ("runs", run_id, "work"):
            handle = _fs.open_directory(parent, component)
            ancestors.append(handle)
            parent = handle
        leaf = _fs.open_regular(parent, "association_records.csv")
        snapshot = _fs.read_bounded(leaf, _MAX_SNAPSHOT_BYTES)
    finally:
        if leaf is not None:
            cleanup_failed = close_for_cleanup(leaf) or cleanup_failed
        for handle in reversed(ancestors):
            cleanup_failed = close_for_cleanup(handle) or cleanup_failed
        cleanup_failed = close_for_cleanup(root) or cleanup_failed
    if cleanup_failed or snapshot is None:
        raise ReviewDecisionContractError("snapshot capability cleanup failed")
    return snapshot, project_id


@dataclass(frozen=True, slots=True)
class _VerifiedEvidence:
    decision: dict[str, Any]
    decision_sha256: str
    snapshot_sha256: str
    authority_sha256: str
    decided_at: datetime
    valid_from: datetime
    valid_until: datetime


def _verify_non_time_evidence(
    *,
    decision_bytes: bytes,
    association_snapshot_bytes: bytes,
    authority_evidence_bytes: bytes,
    trusted_authority_sha256: object,
    expected_project_id: str,
    expected_run_id: str,
    expected_association_id: str,
) -> _VerifiedEvidence:
        project_id = _require_safe_id(expected_project_id, field="expected_project_id")
        run_id = _require_safe_id(expected_run_id, field="expected_run_id")
        association_id = _require_safe_id(expected_association_id, field="expected_association_id")
        trusted = _trusted_allowlist(trusted_authority_sha256)

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
        _parse_association_snapshot(association_snapshot_bytes, association_id)
        canonical_decision = _canonical_json_bytes(decision)
        return _VerifiedEvidence(
            decision=decision,
            decision_sha256=_sha256(canonical_decision),
            snapshot_sha256=snapshot_hash,
            authority_sha256=authority_hash,
            decided_at=_require_timestamp(decision["decided_at"], field="decided_at"),
            valid_from=_require_timestamp(authority["valid_from"], field="valid_from"),
            valid_until=_require_timestamp(authority["valid_until"], field="valid_until"),
        )


def admit_review_decision(
    *,
    project_context: object,
    expected_run_id: str,
    expected_association_id: str,
    decision_bytes: bytes,
    authority_evidence_bytes: bytes,
    trusted_authority_sha256: object,
) -> ReviewDecisionValidation:
    """Admit a signed decision against the fixed Run-local CSV snapshot."""

    try:
        if project_context is CONTEXT_UNAVAILABLE or type(project_context) is not _ConfiguredProjectContext:
            return _invalid()
        run_id = _require_safe_id(expected_run_id, field="expected_run_id")
        association_id = _require_safe_id(expected_association_id, field="expected_association_id")
        snapshot, expected_project_id = _read_fixed_snapshot(project_context, run_id)
        evidence = _verify_non_time_evidence(
            decision_bytes=decision_bytes,
            association_snapshot_bytes=snapshot,
            authority_evidence_bytes=authority_evidence_bytes,
            trusted_authority_sha256=trusted_authority_sha256,
            expected_project_id=expected_project_id,
            expected_run_id=run_id,
            expected_association_id=association_id,
        )
        accepted_time = datetime.now(timezone.utc)
        if not _time_is_valid(
            decided_at=evidence.decided_at,
            accepted_at=accepted_time,
            valid_from=evidence.valid_from,
            valid_until=evidence.valid_until,
        ):
            return _invalid()
        accepted_at = accepted_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        decision = evidence.decision
        return _authority_result(
            "human_verified" if decision["decision"] == "accept_association" else "human_rejected",
            decision_sha256=evidence.decision_sha256,
            association_snapshot_sha256=evidence.snapshot_sha256,
            authority_evidence_sha256=evidence.authority_sha256,
            run_id=decision["run_id"],
            association_id=decision["association_id"],
            reviewer_id=decision["reviewer_id"],
            accepted_at=accepted_at,
        )
    except Exception:
        return _invalid()


def _read_control_message() -> dict[str, object]:
    raw = sys.stdin.buffer.readline(1024 * 1024 + 1)
    if not raw or len(raw) > 1024 * 1024:
        raise ValueError("control message is missing or oversized")
    value = json.loads(raw.decode("utf-8", errors="strict"))
    if type(value) is not dict:
        raise ValueError("control message must be an object")
    return value


def _write_control_message(value: dict[str, object]) -> None:
    sys.stdout.buffer.write(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    )
    sys.stdout.buffer.flush()


def _decode_identity(value: object) -> object:
    if sys.platform != "win32" or _fs is None:
        raise ValueError("Phase C-2 production admission requires Windows x64")
    if type(value) is not dict or set(value) != {"volume_serial", "file_id"}:
        raise ValueError("Windows handle identity is invalid")
    if type(value["volume_serial"]) is not int or type(value["file_id"]) is not str:
        raise ValueError("Windows handle identity is invalid")
    return _fs.HandleIdentity(value["volume_serial"], bytes.fromhex(value["file_id"]))


def _validation_payload(result: ReviewDecisionValidation) -> dict[str, object]:
    return {
        "status": result.status,
        "denial_codes": list(result.denial_codes),
        "decision_sha256": result.decision_sha256,
        "association_snapshot_sha256": result.association_snapshot_sha256,
        "authority_evidence_sha256": result.authority_evidence_sha256,
        "run_id": result.run_id,
        "association_id": result.association_id,
        "reviewer_id": result.reviewer_id,
        "accepted_at": result.accepted_at,
    }


def _child_main() -> int:
    pending: object = CONTEXT_UNAVAILABLE
    context: object = CONTEXT_UNAVAILABLE
    try:
        setup = _read_control_message()
        if set(setup) != {"handle", "identity", "project_id", "project_root"}:
            raise ValueError("setup message is invalid")
        source_handle = setup["handle"]
        if type(source_handle) is not int:
            raise ValueError("transferred handle is invalid")
        pending = _bootstrap_review_project_context(
            source_handle=source_handle,
            expected_identity=_decode_identity(setup["identity"]),
            expected_project_id=setup["project_id"],
            project_root=setup["project_root"],
        )
        if type(pending) is not _PendingProjectContext:
            raise ValueError("bootstrap failed")
        _write_control_message({"state": "READY"})
        if _read_control_message() != {"state": "COMMIT"}:
            raise ValueError("COMMIT message is invalid")
        context = pending._commit()
        request = _read_control_message()
        required = {
            "run_id", "association_id", "decision_base64", "authority_base64",
            "trusted_authority_sha256",
        }
        if context is CONTEXT_UNAVAILABLE or set(request) != required:
            raise ValueError("admission request is invalid")
        hashes = request["trusted_authority_sha256"]
        if type(hashes) is not list:
            raise ValueError("authority hash list is invalid")
        result = admit_review_decision(
            project_context=context,
            expected_run_id=request["run_id"],
            expected_association_id=request["association_id"],
            decision_bytes=base64.b64decode(request["decision_base64"], validate=True),
            authority_evidence_bytes=base64.b64decode(request["authority_base64"], validate=True),
            trusted_authority_sha256=set(hashes),
        )
        _write_control_message({"state": "RESULT", "validation": _validation_payload(result)})
        return 0
    except Exception:
        return 2
    finally:
        if context is not CONTEXT_UNAVAILABLE:
            context.close()
        elif type(pending) is _PendingProjectContext:
            pending.close()


__all__ = [
    "ASSOCIATION_PROJECTION_FIELDS",
    "REVIEW_ACTION_SCOPE",
    "REVIEW_AUTHORITY_SCHEMA_VERSION",
    "REVIEW_DECISION_SCHEMA_VERSION",
    "ReviewDecisionContractError",
    "ReviewDecisionValidation",
    "canonical_review_authority_bytes",
    "sign_review_decision",
    "admit_review_decision",
]


if __name__ == "__main__":
    raise SystemExit(_child_main())
