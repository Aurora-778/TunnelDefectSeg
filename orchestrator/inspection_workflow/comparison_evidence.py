"""Strict Phase 0 contract for normalized Comparison Evidence records."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, DecimalException, InvalidOperation, ROUND_HALF_UP, localcontext
import re
from typing import Any

from orchestrator.claim_policy import compose_comparability_status, load_claim_policy


COMPARISON_EVIDENCE_SCHEMA_VERSION = "comparison_evidence_v1"

COMPARISON_EVIDENCE_FIELDS = (
    "comparison_evidence_schema_version",
    "evidence_id",
    "execution_profile",
    "evidence_schema_valid",
    "current_record_valid",
    "current_inspection_id",
    "current_frame_id",
    "current_image_id",
    "current_observation_id",
    "current_timestamp",
    "current_observation_source_declared",
    "current_observation_source",
    "current_comparability_status",
    "previous_entity_type",
    "previous_memory_id",
    "previous_memory_version",
    "previous_last_seen_inspection",
    "previous_last_seen_timestamp",
    "previous_source_inspection_ids",
    "previous_source_record_count",
    "previous_observation_sources_declared",
    "previous_observation_sources",
    "previous_comparability_status",
    "comparison_group_id",
    "metric_name",
    "metric_type",
    "value_domain",
    "measurement_method",
    "measurement_unit",
    "current_value",
    "previous_memory_snapshot_value",
    "absolute_difference",
    "relative_difference",
    "association_id",
    "association_status",
    "association_mode",
    "use_disease_id_score",
    "association_score",
    "match_type",
    "candidate_count",
    "score_margin",
    "conflict_reason",
    "needs_manual_review",
    "association_supported_pair",
    "identity_evidence_state",
    "valid_timepoint_count",
    "metric_consistent",
    "measurement_method_consistent",
    "temporal_order_valid",
    "difference_valid",
    "relative_difference_valid",
    "evidence_valid",
    "invalid_reason",
    "comparison_comparability_status",
    "comparability_reason",
    "registration_status",
    "registration_evidence_source",
    "registration_evidence_sha256",
    "physical_scale_calibrated",
    "scale_calibration_source",
    "scale_calibration_sha256",
    "measurement_uncertainty",
    "uncertainty_source",
    "uncertainty_unit",
    "source_current_record_fingerprint",
    "source_association_artifact_sha256",
    "source_association_manifest_sha256",
    "source_memory_snapshot_sha256",
    "source_engineering_artifact_sha256",
)

_ALLOWED_IDENTITY_STATES = {
    "association_supported",
    "association_rejected",
    "association_pending_review",
    "association_invalid",
    "association_not_applicable",
}
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_DECIMAL_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?\Z")
_RELATIVE_DIFFERENCE_QUANTUM = Decimal("0.000001")
_MAX_MASK_AREA_PX = (1 << 63) - 1
_UTC_TIMESTAMP_RE = re.compile(
    r"[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]\.[0-9]{6}Z\Z"
)


class ComparisonEvidenceContractError(ValueError):
    """Raised when a normalized Comparison Evidence record is contradictory."""


def _require_records(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        raise ComparisonEvidenceContractError("comparison evidence records must be an iterable of objects")
    rows: list[Mapping[str, Any]] = []
    for row_number, record in enumerate(value, start=1):
        if not isinstance(record, Mapping):
            raise ComparisonEvidenceContractError(
                f"comparison evidence row {row_number} must be an object"
            )
        rows.append(record)
    if not rows:
        raise ComparisonEvidenceContractError("comparison evidence records must not be empty")
    return rows


def _require_exact_fields(record: Mapping[str, Any], *, label: str) -> None:
    if any(not isinstance(field, str) for field in record):
        raise ComparisonEvidenceContractError(f"{label} field names must be strings")
    expected = set(COMPARISON_EVIDENCE_FIELDS)
    actual = set(record)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        raise ComparisonEvidenceContractError(f"{label} missing fields: {', '.join(missing)}")
    if unknown:
        raise ComparisonEvidenceContractError(f"{label} has unknown fields: {', '.join(unknown)}")


def _require_string(value: Any, *, field: str, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ComparisonEvidenceContractError(f"{label} {field} must be a canonical non-empty string")
    return value


def _require_optional_string(value: Any, *, field: str, label: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, field=field, label=label)


def _require_timestamp(value: Any, *, field: str, label: str, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or _UTC_TIMESTAMP_RE.fullmatch(value) is None:
        raise ComparisonEvidenceContractError(
            f"{label} {field} must use canonical UTC microsecond format"
        )
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise ComparisonEvidenceContractError(
            f"{label} {field} must be a valid canonical UTC timestamp"
        ) from exc
    return value


def _require_bool(value: Any, *, field: str, label: str) -> bool:
    if type(value) is not bool:
        raise ComparisonEvidenceContractError(f"{label} {field} must be a boolean")
    return value


def _require_nonnegative_int(value: Any, *, field: str, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ComparisonEvidenceContractError(f"{label} {field} must be a non-negative integer")
    return value


def _require_mask_area_int(
    value: Any,
    *,
    field: str,
    label: str,
    allow_negative: bool = False,
) -> int:
    if type(value) is not int or (value < 0 and not allow_negative):
        qualifier = "an integer" if allow_negative else "a non-negative integer"
        raise ComparisonEvidenceContractError(f"{label} {field} must be {qualifier}")
    if value.bit_length() > 63:
        raise ComparisonEvidenceContractError(
            f"{label} {field} magnitude must not exceed {_MAX_MASK_AREA_PX} pixels"
        )
    return value


def _require_string_list(value: Any, *, field: str, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ComparisonEvidenceContractError(f"{label} {field} must be a list")
    if any(not isinstance(item, str) or not item or item != item.strip() for item in value):
        raise ComparisonEvidenceContractError(
            f"{label} {field} must contain canonical non-empty strings"
        )
    if value != sorted(set(value)):
        raise ComparisonEvidenceContractError(f"{label} {field} must be unique and stably sorted")
    return value


def _require_hash(value: Any, *, field: str, label: str, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise ComparisonEvidenceContractError(f"{label} {field} must be a lowercase SHA-256")
    return value


def _require_decimal(value: Any, *, field: str, label: str, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or _DECIMAL_RE.fullmatch(value) is None:
        raise ComparisonEvidenceContractError(f"{label} {field} must be a canonical decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ComparisonEvidenceContractError(
            f"{label} {field} must be a canonical decimal string"
        ) from exc
    if not parsed.is_finite() or (parsed.is_zero() and value.startswith("-")):
        raise ComparisonEvidenceContractError(f"{label} {field} must be a canonical decimal string")
    return value


def _canonical_relative_difference(absolute_difference: int, previous_value: int) -> str:
    absolute_decimal = Decimal(abs(absolute_difference))
    previous_decimal = Decimal(previous_value)
    integer_digits = max(
        1 if absolute_decimal.is_zero() else absolute_decimal.adjusted() + 1,
        previous_decimal.adjusted() + 1,
    )
    with localcontext() as context:
        context.prec = integer_digits + 8
        rounded = (Decimal(absolute_difference) / previous_decimal).quantize(
            _RELATIVE_DIFFERENCE_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
    value = format(rounded, "f").rstrip("0").rstrip(".")
    return "0" if value in {"", "-0"} else value


def _validate_common_fields(
    record: Mapping[str, Any],
    *,
    label: str,
    policy: Mapping[str, Any],
) -> None:
    if record["comparison_evidence_schema_version"] != COMPARISON_EVIDENCE_SCHEMA_VERSION:
        raise ComparisonEvidenceContractError(
            f"{label} comparison evidence schema version must be {COMPARISON_EVIDENCE_SCHEMA_VERSION}"
        )
    evidence_id = _require_string(record["evidence_id"], field="evidence_id", label=label)
    execution_profile = _require_string(
        record["execution_profile"], field="execution_profile", label=label
    )
    if execution_profile not in policy["execution_profile_enum"]:
        raise ComparisonEvidenceContractError(f"{label} execution_profile is not allowed")

    for field in (
        "evidence_schema_valid",
        "current_record_valid",
        "current_observation_source_declared",
        "previous_observation_sources_declared",
        "needs_manual_review",
        "association_supported_pair",
        "metric_consistent",
        "measurement_method_consistent",
        "temporal_order_valid",
        "difference_valid",
        "relative_difference_valid",
        "evidence_valid",
    ):
        _require_bool(record[field], field=field, label=label)
    prerequisite_flags = (
        "evidence_schema_valid",
        "current_record_valid",
        "current_observation_source_declared",
        "previous_observation_sources_declared",
    )
    if any(record[field] is not True for field in prerequisite_flags) and record["evidence_valid"] is not False:
        raise ComparisonEvidenceContractError(
            f"{label} failed evidence prerequisites require evidence_valid=false"
        )

    inspection_id = _require_string(
        record["current_inspection_id"], field="current_inspection_id", label=label
    )
    _require_string(record["current_frame_id"], field="current_frame_id", label=label)
    _require_string(record["current_image_id"], field="current_image_id", label=label)
    _require_timestamp(record["current_timestamp"], field="current_timestamp", label=label)
    current_observation_id = _require_string(
        record["current_observation_id"], field="current_observation_id", label=label
    )
    observation_prefix = f"{inspection_id}::"
    if not current_observation_id.startswith(observation_prefix) or current_observation_id == observation_prefix:
        raise ComparisonEvidenceContractError(
            f"{label} current_observation_id must use the current inspection prefix"
        )

    current_source = _require_string(
        record["current_observation_source"], field="current_observation_source", label=label
    )
    previous_sources = _require_string_list(
        record["previous_observation_sources"],
        field="previous_observation_sources",
        label=label,
    )
    if current_source not in policy["observation_source_enum"] or any(
        source not in policy["observation_source_enum"] for source in previous_sources
    ):
        raise ComparisonEvidenceContractError(f"{label} contains an unknown observation source")
    if "verified_fixture" in [current_source, *previous_sources] and execution_profile != "test":
        raise ComparisonEvidenceContractError(f"{label} verified_fixture is only allowed in test")

    for field in (
        "current_comparability_status",
        "previous_comparability_status",
        "comparison_comparability_status",
    ):
        status = _require_string(record[field], field=field, label=label)
        if status not in policy["comparability_status_enum"]:
            raise ComparisonEvidenceContractError(f"{label} {field} is not allowed")

    verified_sources = set(
        policy["source_comparability_rules"]["verified_comparable_allowed_sources"]
    )
    if record["current_comparability_status"] == "verified_comparable" and current_source not in verified_sources:
        raise ComparisonEvidenceContractError(
            f"{label} current source cannot declare verified_comparable"
        )
    if record["previous_comparability_status"] == "verified_comparable" and (
        not previous_sources or any(source not in verified_sources for source in previous_sources)
    ):
        raise ComparisonEvidenceContractError(
            f"{label} previous sources cannot declare verified_comparable"
        )

    expected_status = compose_comparability_status(
        previous_entity_type=record["previous_entity_type"],
        current_status=record["current_comparability_status"],
        previous_status=record["previous_comparability_status"],
    )
    if record["comparison_comparability_status"] != expected_status:
        raise ComparisonEvidenceContractError(
            f"{label} comparison_comparability_status must be {expected_status}"
        )
    if expected_status != "verified_comparable" and record["difference_valid"] is not False:
        raise ComparisonEvidenceContractError(
            f"{label} {expected_status} comparison requires difference_valid=false"
        )

    _require_string(record["comparison_group_id"], field="comparison_group_id", label=label)
    if record["metric_name"] != "inspection_level_max_mask_area_px":
        raise ComparisonEvidenceContractError(
            f"{label} metric_name must be inspection_level_max_mask_area_px"
        )
    if record["measurement_method"] != "inspection_level_max_mask_area_px":
        raise ComparisonEvidenceContractError(
            f"{label} measurement_method must be inspection_level_max_mask_area_px"
        )
    if record["measurement_unit"] != "pixel²":
        raise ComparisonEvidenceContractError(f"{label} measurement_unit must be pixel²")
    _require_string(record["metric_type"], field="metric_type", label=label)
    _require_string(record["value_domain"], field="value_domain", label=label)
    _require_mask_area_int(record["current_value"], field="current_value", label=label)
    _require_nonnegative_int(record["valid_timepoint_count"], field="valid_timepoint_count", label=label)

    identity_state = _require_string(
        record["identity_evidence_state"], field="identity_evidence_state", label=label
    )
    if identity_state not in _ALLOWED_IDENTITY_STATES:
        raise ComparisonEvidenceContractError(f"{label} identity_evidence_state is not allowed in Phase 0")
    invalid_reason = _require_optional_string(record["invalid_reason"], field="invalid_reason", label=label)
    if record["evidence_valid"] is True and invalid_reason is not None:
        raise ComparisonEvidenceContractError(f"{label} valid evidence must not carry invalid_reason")
    if record["evidence_valid"] is False and invalid_reason is None:
        raise ComparisonEvidenceContractError(f"{label} invalid evidence requires invalid_reason")
    if identity_state == "association_invalid" and record["evidence_valid"] is not False:
        raise ComparisonEvidenceContractError(f"{label} association_invalid evidence must be invalid")

    registration_status = _require_string(
        record["registration_status"], field="registration_status", label=label
    )
    if registration_status not in policy["registration_status_enum"]:
        raise ComparisonEvidenceContractError(f"{label} registration_status is not allowed")

    registration_source = _require_string(
        record["registration_evidence_source"], field="registration_evidence_source", label=label
    )
    registration_hash = _require_hash(
        record["registration_evidence_sha256"],
        field="registration_evidence_sha256",
        label=label,
        optional=True,
    )
    if registration_status == "not_verified" and (registration_source != "none" or registration_hash is not None):
        raise ComparisonEvidenceContractError(
            f"{label} unverified registration must use none/null provenance"
        )
    if registration_status == "registered" and (registration_source == "none" or registration_hash is None):
        raise ComparisonEvidenceContractError(
            f"{label} registered evidence requires source and SHA-256"
        )

    physical_scale = _require_bool(
        record["physical_scale_calibrated"], field="physical_scale_calibrated", label=label
    )
    scale_source = _require_string(
        record["scale_calibration_source"], field="scale_calibration_source", label=label
    )
    scale_hash = _require_hash(
        record["scale_calibration_sha256"],
        field="scale_calibration_sha256",
        label=label,
        optional=True,
    )
    if not physical_scale and (scale_source != "none" or scale_hash is not None):
        raise ComparisonEvidenceContractError(
            f"{label} uncalibrated physical scale must use none/null provenance"
        )
    if physical_scale and (scale_source == "none" or scale_hash is None):
        raise ComparisonEvidenceContractError(
            f"{label} calibrated physical scale requires source and SHA-256"
        )

    uncertainty = _require_decimal(
        record["measurement_uncertainty"],
        field="measurement_uncertainty",
        label=label,
        optional=True,
    )
    uncertainty_source = _require_string(
        record["uncertainty_source"], field="uncertainty_source", label=label
    )
    uncertainty_unit = _require_optional_string(
        record["uncertainty_unit"], field="uncertainty_unit", label=label
    )
    if uncertainty is None and (uncertainty_source != "not_available" or uncertainty_unit is not None):
        raise ComparisonEvidenceContractError(
            f"{label} unavailable uncertainty must use not_available/null provenance"
        )
    if uncertainty is not None and (uncertainty_source == "not_available" or uncertainty_unit is None):
        raise ComparisonEvidenceContractError(
            f"{label} measurement uncertainty requires source and unit"
        )
    if uncertainty is not None and Decimal(uncertainty) < 0:
        raise ComparisonEvidenceContractError(f"{label} measurement_uncertainty must be non-negative")

    _require_string(record["comparability_reason"], field="comparability_reason", label=label)
    _require_hash(
        record["source_current_record_fingerprint"],
        field="source_current_record_fingerprint",
        label=label,
    )
    engineering_hash = _require_hash(
        record["source_engineering_artifact_sha256"],
        field="source_engineering_artifact_sha256",
        label=label,
        optional=True,
    )
    if engineering_hash is None:
        if record["evidence_valid"] is not False or invalid_reason != "source_engineering_artifact_missing":
            raise ComparisonEvidenceContractError(
                f"{label} missing source_engineering_artifact_sha256 requires "
                "evidence_valid=false and invalid_reason=source_engineering_artifact_missing"
            )
    elif invalid_reason == "source_engineering_artifact_missing":
        raise ComparisonEvidenceContractError(
            f"{label} source_engineering_artifact_missing requires a null "
            "source_engineering_artifact_sha256"
        )
    association_hashes = (
        "source_association_artifact_sha256",
        "source_association_manifest_sha256",
    )
    if identity_state == "association_invalid":
        for field in association_hashes:
            _require_hash(record[field], field=field, label=label, optional=True)
    else:
        for field in association_hashes:
            _require_hash(record[field], field=field, label=label)
    _require_string(evidence_id, field="evidence_id", label=label)


def _validate_previous_branch(record: Mapping[str, Any], *, label: str) -> None:
    previous_entity_type = record["previous_entity_type"]
    if not isinstance(previous_entity_type, str) or previous_entity_type not in {
        "not_applicable",
        "memory_snapshot",
    }:
        raise ComparisonEvidenceContractError(f"{label} previous_entity_type is not allowed")

    previous_ids = _require_string_list(
        record["previous_source_inspection_ids"],
        field="previous_source_inspection_ids",
        label=label,
    )
    previous_sources = record["previous_observation_sources"]
    previous_count = _require_nonnegative_int(
        record["previous_source_record_count"],
        field="previous_source_record_count",
        label=label,
    )
    previous_memory_fields = (
        "previous_memory_id",
        "previous_memory_version",
        "previous_last_seen_inspection",
    )
    for field in previous_memory_fields:
        _require_optional_string(record[field], field=field, label=label)
    _require_timestamp(
        record["previous_last_seen_timestamp"],
        field="previous_last_seen_timestamp",
        label=label,
        optional=True,
    )
    all_previous_scalar_fields = (*previous_memory_fields, "previous_last_seen_timestamp")

    if previous_entity_type == "not_applicable":
        if any(record[field] is not None for field in all_previous_scalar_fields):
            raise ComparisonEvidenceContractError(
                f"{label} not_applicable previous scalar fields must use canonical null"
            )
        if previous_ids or previous_sources or previous_count != 0:
            raise ComparisonEvidenceContractError(
                f"{label} not_applicable previous lists/count must be empty/zero"
            )
        if record["previous_comparability_status"] != "insufficient_history":
            raise ComparisonEvidenceContractError(
                f"{label} not_applicable previous comparability must be insufficient_history"
            )
        if record["valid_timepoint_count"] != 1:
            raise ComparisonEvidenceContractError(
                f"{label} not_applicable evidence must have one valid timepoint"
            )
        for field in (
            "previous_memory_snapshot_value",
            "absolute_difference",
            "relative_difference",
            "source_memory_snapshot_sha256",
        ):
            if record[field] is not None:
                raise ComparisonEvidenceContractError(
                    f"{label} not_applicable {field} must use canonical null"
                )
        if record["difference_valid"] or record["relative_difference_valid"] or record["temporal_order_valid"]:
            raise ComparisonEvidenceContractError(
                f"{label} not_applicable evidence cannot claim temporal or difference validity"
            )
        return

    for field in all_previous_scalar_fields:
        if record[field] is None:
            raise ComparisonEvidenceContractError(f"{label} memory_snapshot requires {field}")
    if record["previous_last_seen_inspection"] not in previous_ids:
        raise ComparisonEvidenceContractError(
            f"{label} previous_last_seen_inspection must be in previous source inspections"
        )
    if not previous_ids or not previous_sources or previous_count < 1:
        raise ComparisonEvidenceContractError(
            f"{label} memory_snapshot requires previous source provenance"
        )
    if record["valid_timepoint_count"] < 2:
        raise ComparisonEvidenceContractError(
            f"{label} memory_snapshot requires at least two valid timepoints"
        )
    previous_value = _require_mask_area_int(
        record["previous_memory_snapshot_value"],
        field="previous_memory_snapshot_value",
        label=label,
    )
    absolute_difference = _require_mask_area_int(
        record["absolute_difference"],
        field="absolute_difference",
        label=label,
        allow_negative=True,
    )
    if absolute_difference != record["current_value"] - previous_value:
        raise ComparisonEvidenceContractError(
            f"{label} absolute_difference does not match current and previous values"
        )
    if previous_value == 0:
        if record["relative_difference"] is not None or record["relative_difference_valid"]:
            raise ComparisonEvidenceContractError(
                f"{label} zero previous value requires null/false relative difference"
            )
    else:
        relative_difference = _require_decimal(
            record["relative_difference"], field="relative_difference", label=label
        )
        if record["relative_difference_valid"] is not True:
            raise ComparisonEvidenceContractError(
                f"{label} non-zero previous value requires a retained relative difference"
            )
        try:
            expected_relative_difference = _canonical_relative_difference(
                absolute_difference,
                previous_value,
            )
        except DecimalException as exc:
            raise ComparisonEvidenceContractError(
                f"{label} relative_difference exceeds the supported Decimal range"
            ) from exc
        if relative_difference != expected_relative_difference:
            raise ComparisonEvidenceContractError(
                f"{label} relative_difference must equal {expected_relative_difference} "
                "using six fractional digits and ROUND_HALF_UP"
            )
    _require_hash(record["source_memory_snapshot_sha256"], field="source_memory_snapshot_sha256", label=label)
    if record["temporal_order_valid"] and record["current_timestamp"] <= record["previous_last_seen_timestamp"]:
        raise ComparisonEvidenceContractError(
            f"{label} temporal_order_valid conflicts with current/previous timestamps"
        )


def _validate_association_branch(record: Mapping[str, Any], *, label: str) -> None:
    state = record["identity_evidence_state"]
    association_id = _require_optional_string(record["association_id"], field="association_id", label=label)
    association_status = _require_optional_string(
        record["association_status"], field="association_status", label=label
    )
    association_mode = _require_optional_string(
        record["association_mode"], field="association_mode", label=label
    )
    use_disease_id_score = record["use_disease_id_score"]
    if use_disease_id_score is not None and type(use_disease_id_score) is not bool:
        raise ComparisonEvidenceContractError(f"{label} use_disease_id_score must be boolean or null")
    association_score = _require_decimal(
        record["association_score"], field="association_score", label=label, optional=True
    )
    match_type = _require_optional_string(record["match_type"], field="match_type", label=label)
    candidate_count = _require_nonnegative_int(
        record["candidate_count"], field="candidate_count", label=label
    )
    score_margin = _require_decimal(
        record["score_margin"], field="score_margin", label=label, optional=True
    )
    conflict_reason = _require_optional_string(
        record["conflict_reason"], field="conflict_reason", label=label
    )

    if state == "association_invalid":
        return
    if state == "association_not_applicable":
        if any(
            value is not None
            for value in (
                association_id,
                association_status,
                association_mode,
                use_disease_id_score,
                association_score,
                match_type,
                score_margin,
                conflict_reason,
            )
        ) or candidate_count != 0:
            raise ComparisonEvidenceContractError(
                f"{label} association_not_applicable fields must use canonical null"
            )
        if record["previous_entity_type"] != "not_applicable":
            raise ComparisonEvidenceContractError(
                f"{label} association_not_applicable cannot reference Memory"
            )
        if record["needs_manual_review"] or record["association_supported_pair"]:
            raise ComparisonEvidenceContractError(
                f"{label} association_not_applicable cannot claim Association support or review"
            )
        return

    if association_id is None or association_mode != "no_id" or use_disease_id_score is not False:
        raise ComparisonEvidenceContractError(
            f"{label} Association evidence must use a concrete no-id Association row"
        )
    if match_type is None:
        raise ComparisonEvidenceContractError(f"{label} Association evidence requires match_type")
    if match_type not in {"soft", "uncertain"}:
        raise ComparisonEvidenceContractError(
            f"{label} no-id Association match_type must be soft or uncertain"
        )
    if association_score is not None and not (Decimal("0") <= Decimal(association_score) <= Decimal("1")):
        raise ComparisonEvidenceContractError(f"{label} association_score must be between 0 and 1")
    if score_margin is not None and not (Decimal("0") <= Decimal(score_margin) <= Decimal("1")):
        raise ComparisonEvidenceContractError(f"{label} score_margin must be between 0 and 1")
    if state == "association_rejected":
        if association_status != "unmatched" or record["previous_entity_type"] != "not_applicable":
            raise ComparisonEvidenceContractError(
                f"{label} association_rejected must be unmatched without Memory"
            )
        if record["association_supported_pair"]:
            raise ComparisonEvidenceContractError(
                f"{label} association_rejected cannot claim a supported pair"
            )
        return

    if association_status != "matched" or record["previous_entity_type"] != "memory_snapshot":
        raise ComparisonEvidenceContractError(
            f"{label} supported/pending Association requires matched Memory evidence"
        )
    if association_score is None:
        raise ComparisonEvidenceContractError(f"{label} matched Association requires association_score")
    if candidate_count < 1:
        raise ComparisonEvidenceContractError(f"{label} matched Association requires at least one candidate")
    if record["association_supported_pair"] is not True:
        raise ComparisonEvidenceContractError(f"{label} matched Association must mark supported pair")
    if state == "association_supported" and record["needs_manual_review"] is not False:
        raise ComparisonEvidenceContractError(
            f"{label} association_supported cannot require manual review"
        )
    if state == "association_pending_review" and record["needs_manual_review"] is not True:
        raise ComparisonEvidenceContractError(
            f"{label} association_pending_review must require manual review"
        )


def validate_comparison_evidence_records(
    records: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Validate normalized Evidence relations without reading or writing artifacts."""

    rows = _require_records(records)
    policy = load_claim_policy()
    seen_evidence_ids: set[str] = set()
    seen_observations: set[tuple[str, str]] = set()
    for row_number, record in enumerate(rows, start=1):
        label = f"comparison evidence row {row_number}"
        _require_exact_fields(record, label=label)
        _validate_common_fields(record, label=label, policy=policy)
        _validate_previous_branch(record, label=label)
        _validate_association_branch(record, label=label)

        evidence_id = record["evidence_id"]
        if evidence_id in seen_evidence_ids:
            raise ComparisonEvidenceContractError(f"duplicate evidence_id: {evidence_id}")
        seen_evidence_ids.add(evidence_id)
        observation_key = (record["current_inspection_id"], record["current_observation_id"])
        if observation_key in seen_observations:
            raise ComparisonEvidenceContractError(
                "duplicate comparison evidence observation: " + " / ".join(observation_key)
            )
        seen_observations.add(observation_key)
    return deepcopy([dict(record) for record in rows])


__all__ = [
    "COMPARISON_EVIDENCE_FIELDS",
    "COMPARISON_EVIDENCE_SCHEMA_VERSION",
    "ComparisonEvidenceContractError",
    "validate_comparison_evidence_records",
]
