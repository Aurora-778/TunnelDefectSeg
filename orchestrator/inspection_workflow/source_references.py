"""Strict Phase 0 cross-table contracts for neutral observation references."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from typing import Any

SOURCE_REFERENCE_SCHEMA_VERSION = "inspection_source_references_v1"
FRAME_REFERENCE_FIELDS = (
    "source_reference_schema_version",
    "inspection_id",
    "current_observation_id",
    "frame_id",
    "image_id",
)
ASSOCIATION_REFERENCE_FIELDS = (
    "source_reference_schema_version",
    "association_id",
    "inspection_id",
    "current_observation_id",
    "frame_id",
    "image_id",
)
ENGINEERING_REFERENCE_FIELDS = (
    "source_reference_schema_version",
    "inspection_id",
    "source_observation_ids",
)


class SourceReferenceContractError(ValueError):
    """Raised when neutral observation references are malformed or disconnected."""


def _require_records(value: Any, *, label: str) -> list[Mapping[str, Any]]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        raise SourceReferenceContractError(f"{label} must be an iterable of objects")
    records: list[Mapping[str, Any]] = []
    for row_number, record in enumerate(value, start=1):
        if not isinstance(record, Mapping):
            raise SourceReferenceContractError(f"{label} row {row_number} must be an object")
        records.append(record)
    return records


def _require_canonical_string(value: Any, *, field: str, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise SourceReferenceContractError(f"{label} {field} must be a canonical non-empty string")
    return value


def _validate_row_fields(
    record: Mapping[str, Any],
    required_fields: tuple[str, ...],
    *,
    label: str,
) -> None:
    missing = [field for field in required_fields if field not in record]
    if missing:
        raise SourceReferenceContractError(f"{label} missing required fields: {', '.join(missing)}")
    version = record["source_reference_schema_version"]
    if version != SOURCE_REFERENCE_SCHEMA_VERSION:
        raise SourceReferenceContractError(
            f"{label} source reference schema version must be {SOURCE_REFERENCE_SCHEMA_VERSION}"
        )


def _validate_fieldnames(
    fieldnames: Any,
    required_fields: tuple[str, ...],
    *,
    label: str,
) -> tuple[str, ...]:
    if isinstance(fieldnames, (str, bytes)) or not isinstance(fieldnames, Sequence):
        raise SourceReferenceContractError(f"{label} fieldnames must be a sequence of strings")
    names = list(fieldnames)
    if any(not isinstance(name, str) or not name for name in names):
        raise SourceReferenceContractError(f"{label} fieldnames must contain non-empty strings")
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise SourceReferenceContractError(f"{label} fieldnames contain duplicates: {', '.join(duplicates)}")
    missing = [field for field in required_fields if field not in names]
    if missing:
        raise SourceReferenceContractError(f"{label} missing required fields: {', '.join(missing)}")
    return tuple(names)


def _validate_current_observation_id(
    record: Mapping[str, Any],
    *,
    inspection_id: str,
    label: str,
) -> str:
    current_id = _require_canonical_string(
        record.get("current_observation_id"),
        field="current_observation_id",
        label=label,
    )
    prefix = f"{inspection_id}::"
    if not current_id.startswith(prefix) or current_id == prefix:
        raise SourceReferenceContractError(
            f"{label} current_observation_id must use the inspection prefix {prefix}"
        )
    if "local_observation_id" in record:
        local_id = _require_canonical_string(
            record["local_observation_id"],
            field="local_observation_id",
            label=label,
        )
        if current_id != f"{inspection_id}::{local_id}":
            raise SourceReferenceContractError(
                f"{label} current_observation_id conflicts with local_observation_id"
            )
    return current_id


def validate_frame_observation_references(
    records: Iterable[Mapping[str, Any]],
    *,
    fieldnames: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Validate one non-empty observation-grain Frame relation."""

    rows = _require_records(records, label="frame records")
    if fieldnames is not None:
        _validate_fieldnames(fieldnames, FRAME_REFERENCE_FIELDS, label="frame records")
    if not rows:
        raise SourceReferenceContractError("frame records must contain at least one current observation")

    seen_observations: set[tuple[str, str]] = set()
    seen_composites: set[tuple[str, str, str]] = set()
    for row_number, row in enumerate(rows, start=1):
        label = f"frame records row {row_number}"
        _validate_row_fields(row, FRAME_REFERENCE_FIELDS, label=label)
        inspection_id = _require_canonical_string(row["inspection_id"], field="inspection_id", label=label)
        if "association_inspection_id" in row:
            association_inspection_id = _require_canonical_string(
                row["association_inspection_id"],
                field="association_inspection_id",
                label=label,
            )
            if association_inspection_id != inspection_id:
                raise SourceReferenceContractError(
                    f"{label} association_inspection_id conflicts with inspection_id"
                )
        current_id = _validate_current_observation_id(row, inspection_id=inspection_id, label=label)
        frame_id = _require_canonical_string(row["frame_id"], field="frame_id", label=label)
        image_id = _require_canonical_string(row["image_id"], field="image_id", label=label)

        observation_key = (inspection_id, current_id)
        if observation_key in seen_observations:
            raise SourceReferenceContractError(
                f"duplicate frame observation key: {inspection_id} / {current_id}"
            )
        seen_observations.add(observation_key)

        composite_key = (inspection_id, frame_id, image_id)
        if composite_key in seen_composites:
            raise SourceReferenceContractError(
                f"duplicate frame composite key: {inspection_id} / {frame_id} / {image_id}"
            )
        seen_composites.add(composite_key)
    return deepcopy([dict(row) for row in rows])


def _normalize_baseline_ids(value: Any) -> set[str]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        raise SourceReferenceContractError("baseline_inspection_ids must be an iterable of strings")
    result: set[str] = set()
    for item in value:
        normalized = _require_canonical_string(
            item,
            field="baseline_inspection_id",
            label="baseline_inspection_ids",
        )
        if normalized in result:
            raise SourceReferenceContractError(f"duplicate baseline_inspection_id: {normalized}")
        result.add(normalized)
    return result


def validate_association_observation_references(
    frame_records: Iterable[Mapping[str, Any]],
    association_records: Iterable[Mapping[str, Any]],
    *,
    baseline_inspection_ids: Iterable[str],
    fieldnames: Iterable[str] | None = None,
    artifact_schema_version: Any = None,
) -> list[dict[str, Any]]:
    """Validate exact query references while permitting a proven header-only baseline."""

    frames = validate_frame_observation_references(frame_records)
    associations = _require_records(association_records, label="Association records")
    baseline_ids = _normalize_baseline_ids(baseline_inspection_ids)
    frame_inspections = {row["inspection_id"] for row in frames}
    unknown_baselines = sorted(baseline_ids - frame_inspections)
    if unknown_baselines:
        raise SourceReferenceContractError(
            "baseline_inspection_ids do not exist in frame records: " + ", ".join(unknown_baselines)
        )
    if fieldnames is not None:
        _validate_fieldnames(fieldnames, ASSOCIATION_REFERENCE_FIELDS, label="Association records")
    elif not associations:
        raise SourceReferenceContractError("Association fieldnames are required for a header-only artifact")
    if artifact_schema_version is not None and artifact_schema_version != SOURCE_REFERENCE_SCHEMA_VERSION:
        raise SourceReferenceContractError(
            f"Association artifact schema version must be {SOURCE_REFERENCE_SCHEMA_VERSION}"
        )
    if not associations and artifact_schema_version is None:
        raise SourceReferenceContractError(
            "Association artifact schema version is required for a header-only artifact"
        )

    frame_index = {
        (row["inspection_id"], row["current_observation_id"]): row
        for row in frames
    }
    seen_queries: set[tuple[str, str]] = set()
    seen_association_ids: set[str] = set()
    for row_number, row in enumerate(associations, start=1):
        label = f"Association records row {row_number}"
        _validate_row_fields(row, ASSOCIATION_REFERENCE_FIELDS, label=label)
        association_id = _require_canonical_string(
            row["association_id"],
            field="association_id",
            label=label,
        )
        if association_id in seen_association_ids:
            raise SourceReferenceContractError(f"duplicate association_id: {association_id}")
        seen_association_ids.add(association_id)
        inspection_id = _require_canonical_string(row["inspection_id"], field="inspection_id", label=label)
        current_id = _validate_current_observation_id(row, inspection_id=inspection_id, label=label)
        frame_id = _require_canonical_string(row["frame_id"], field="frame_id", label=label)
        image_id = _require_canonical_string(row["image_id"], field="image_id", label=label)
        query_key = (inspection_id, current_id)

        if query_key in seen_queries:
            raise SourceReferenceContractError(
                f"duplicate Association query key: {inspection_id} / {current_id}"
            )
        seen_queries.add(query_key)
        source = frame_index.get(query_key)
        if source is None:
            raise SourceReferenceContractError(
                f"orphan Association query: {inspection_id} / {current_id}"
            )
        if inspection_id in baseline_ids:
            raise SourceReferenceContractError(
                f"baseline inspection {inspection_id} must not contain Association rows"
            )
        if frame_id != source["frame_id"]:
            raise SourceReferenceContractError(
                f"Association query {current_id} frame_id does not match source Frame"
            )
        if image_id != source["image_id"]:
            raise SourceReferenceContractError(
                f"Association query {current_id} image_id does not match source Frame"
            )

    expected_queries = {
        key for key in frame_index if key[0] not in baseline_ids
    }
    missing_queries = sorted(expected_queries - seen_queries)
    if missing_queries:
        inspection_id, current_id = missing_queries[0]
        raise SourceReferenceContractError(
            f"missing Association query for non-baseline observation: {inspection_id} / {current_id}"
        )
    return deepcopy([dict(row) for row in associations])


def validate_engineering_observation_references(
    frame_records: Iterable[Mapping[str, Any]],
    engineering_records: Iterable[Mapping[str, Any]],
    *,
    fieldnames: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Require stable, non-overlapping Engineering coverage of every current observation."""

    frames = validate_frame_observation_references(frame_records)
    engineering_rows = _require_records(engineering_records, label="Engineering records")
    if fieldnames is not None:
        _validate_fieldnames(fieldnames, ENGINEERING_REFERENCE_FIELDS, label="Engineering records")
    if not engineering_rows:
        raise SourceReferenceContractError("Engineering records must contain at least one aggregate")

    frame_keys = {
        (row["inspection_id"], row["current_observation_id"])
        for row in frames
    }
    referenced_by: dict[tuple[str, str], int] = {}
    for row_number, row in enumerate(engineering_rows, start=1):
        label = f"Engineering records row {row_number}"
        _validate_row_fields(row, ENGINEERING_REFERENCE_FIELDS, label=label)
        inspection_id = _require_canonical_string(row["inspection_id"], field="inspection_id", label=label)
        source_ids = row["source_observation_ids"]
        if not isinstance(source_ids, list):
            raise SourceReferenceContractError(f"{label} source_observation_ids must be a list")
        if not source_ids:
            raise SourceReferenceContractError(f"{label} source_observation_ids must not be empty")
        if any(not isinstance(item, str) or not item or item != item.strip() for item in source_ids):
            raise SourceReferenceContractError(
                f"{label} source_observation_ids must contain canonical non-empty strings"
            )
        if len(source_ids) != len(set(source_ids)):
            raise SourceReferenceContractError(f"{label} source_observation_ids must be unique")
        if source_ids != sorted(source_ids):
            raise SourceReferenceContractError(f"{label} source_observation_ids must use stable sorted order")

        for current_id in source_ids:
            key = (inspection_id, current_id)
            if key not in frame_keys:
                raise SourceReferenceContractError(
                    f"{label} has orphan source_observation_id: {inspection_id} / {current_id}"
                )
            if key in referenced_by:
                raise SourceReferenceContractError(
                    f"{inspection_id} / {current_id} is referenced by multiple Engineering rows"
                )
            referenced_by[key] = row_number

    missing = sorted(frame_keys - set(referenced_by))
    if missing:
        inspection_id, current_id = missing[0]
        raise SourceReferenceContractError(
            f"missing Engineering source reference: {inspection_id} / {current_id}"
        )
    return deepcopy([dict(row) for row in engineering_rows])


def validate_source_reference_contract(
    frame_records: Iterable[Mapping[str, Any]],
    association_records: Iterable[Mapping[str, Any]],
    engineering_records: Iterable[Mapping[str, Any]],
    *,
    baseline_inspection_ids: Iterable[str],
    artifact_schema_version: Any,
    frame_fieldnames: Iterable[str],
    association_fieldnames: Iterable[str],
    engineering_fieldnames: Iterable[str],
) -> dict[str, Any]:
    """Validate the three Run-local relations without reading or writing artifacts."""

    if artifact_schema_version != SOURCE_REFERENCE_SCHEMA_VERSION:
        raise SourceReferenceContractError(
            f"source reference artifact schema version must be {SOURCE_REFERENCE_SCHEMA_VERSION}"
        )
    baseline_ids = _normalize_baseline_ids(baseline_inspection_ids)
    if len(baseline_ids) != 1:
        raise SourceReferenceContractError(
            "inspection_source_references_v1 requires exactly one baseline_inspection_id"
        )
    frames = validate_frame_observation_references(frame_records, fieldnames=frame_fieldnames)
    associations = validate_association_observation_references(
        frames,
        association_records,
        baseline_inspection_ids=baseline_ids,
        fieldnames=association_fieldnames,
        artifact_schema_version=artifact_schema_version,
    )
    engineering = validate_engineering_observation_references(
        frames,
        engineering_records,
        fieldnames=engineering_fieldnames,
    )
    return {
        "source_reference_schema_version": SOURCE_REFERENCE_SCHEMA_VERSION,
        "baseline_inspection_ids": sorted(baseline_ids),
        "frame_records": frames,
        "association_records": associations,
        "engineering_records": engineering,
    }


__all__ = [
    "ASSOCIATION_REFERENCE_FIELDS",
    "ENGINEERING_REFERENCE_FIELDS",
    "FRAME_REFERENCE_FIELDS",
    "SOURCE_REFERENCE_SCHEMA_VERSION",
    "SourceReferenceContractError",
    "validate_association_observation_references",
    "validate_engineering_observation_references",
    "validate_frame_observation_references",
    "validate_source_reference_contract",
]
