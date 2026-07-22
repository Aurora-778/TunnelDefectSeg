"""Pure Phase 0 projection for neutral observation identities."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import PurePosixPath
import re
import unicodedata
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ObservationIdentityError(ValueError):
    """Raised when an observation cannot receive a trustworthy neutral ID."""


LEGACY_FINGERPRINT_FIELDS = (
    "inspection_id",
    "frame_id",
    "image_id",
    "timestamp",
    "mileage_m",
    "ring_id",
    "clock_direction",
    "disease_type",
    "kict_image_path",
    "kict_mask_path",
    "kict_area_px",
    "kict_bbox_x1",
    "kict_bbox_y1",
    "kict_bbox_x2",
    "kict_bbox_y2",
    "kict_center_x",
    "kict_center_y",
    "kict_mask_width",
    "kict_mask_height",
    "has_crack",
    "observation_source",
    "comparability_status",
)

_STRING_FIELDS = {
    "inspection_id",
    "frame_id",
    "image_id",
    "clock_direction",
    "disease_type",
    "observation_source",
    "comparability_status",
}
_PATH_FIELDS = {"kict_image_path", "kict_mask_path"}
_INTEGER_FIELDS = {"ring_id", "kict_mask_width", "kict_mask_height"}
_DECIMAL_FIELDS = {
    "mileage_m",
    "kict_area_px",
    "kict_bbox_x1",
    "kict_bbox_y1",
    "kict_bbox_x2",
    "kict_bbox_y2",
    "kict_center_x",
    "kict_center_y",
}
_BOOLEAN_FIELDS = {"has_crack"}

_PREPARED_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_INTEGER_PATTERN = re.compile(r"-?(?:0|[1-9][0-9]*)$")
_DECIMAL_PATTERN = re.compile(r"-?[0-9]+(?:\.[0-9]+)?$")
_URI_OR_DRIVE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _require_mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ObservationIdentityError(f"{label} must be an object")
    return value


def _normalize_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ObservationIdentityError(f"{field} must be a non-empty string")
    normalized = unicodedata.normalize("NFC", value.strip())
    if not normalized:
        raise ObservationIdentityError(f"{field} must be a non-empty string")
    return normalized


def _normalize_prepared_identifier(value: Any, *, field: str) -> str:
    identifier = _normalize_text(value, field=field)
    if (
        not _PREPARED_IDENTIFIER_PATTERN.fullmatch(identifier)
        or identifier in {".", ".."}
        or "::" in identifier
    ):
        raise ObservationIdentityError(f"{field} must be a safe local identifier")
    return identifier


def _normalize_dataset_root(value: Any) -> str:
    root = _normalize_text(value, field="dataset_root")
    if root in {".", ".."} or "\\" in root or ":" in root:
        raise ObservationIdentityError("dataset_root must be a project-root-relative POSIX path")
    path = PurePosixPath(root)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ObservationIdentityError("dataset_root must be a project-root-relative POSIX path")
    if path.as_posix() != root:
        raise ObservationIdentityError("dataset_root must be a canonical project-root-relative POSIX path")
    return path.as_posix()


def _normalize_artifact_path(value: Any, *, field: str) -> str:
    raw = _normalize_text(value, field=field).replace("\\", "/")
    if _URI_OR_DRIVE_PATTERN.match(raw) or ":" in raw:
        raise ObservationIdentityError(f"{field} must be relative to dataset_root")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise ObservationIdentityError(f"{field} must be relative to dataset_root")

    if not path.parts:
        raise ObservationIdentityError(f"{field} must identify a file below dataset_root")
    return path.as_posix()


def _normalize_integer(value: Any, *, field: str) -> str:
    if isinstance(value, bool):
        raise ObservationIdentityError(f"{field} must be a canonical integer")
    if isinstance(value, int):
        return str(value)
    if not isinstance(value, str):
        raise ObservationIdentityError(f"{field} must be a canonical integer")
    text = value.strip()
    if not _INTEGER_PATTERN.fullmatch(text) or text == "-0":
        raise ObservationIdentityError(f"{field} must be a canonical integer")
    return text


def _normalize_decimal(value: Any, *, field: str) -> str:
    if isinstance(value, bool) or isinstance(value, float):
        raise ObservationIdentityError(f"{field} must be a finite decimal without scientific notation")
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, Decimal):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        raise ObservationIdentityError(f"{field} must be a finite decimal without scientific notation")

    if "e" in text.lower():
        raise ObservationIdentityError(f"{field} must not use scientific notation")
    if not _DECIMAL_PATTERN.fullmatch(text):
        raise ObservationIdentityError(f"{field} must be a finite decimal without scientific notation")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ObservationIdentityError(f"{field} must be a finite decimal") from exc
    if not number.is_finite():
        raise ObservationIdentityError(f"{field} must be a finite decimal")
    if number == 0:
        return "0"
    canonical = format(number, "f")
    if "." in canonical:
        canonical = canonical.rstrip("0").rstrip(".")
    return canonical


def _normalize_bool(value: Any, *, field: str) -> bool:
    if type(value) is bool:
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ObservationIdentityError(f"{field} must be a JSON boolean or true/false text")


def _load_timezone(value: Any) -> ZoneInfo:
    name = _normalize_text(value, field="dataset_timezone")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ObservationIdentityError(f"dataset_timezone is not a known IANA timezone: {name}") from exc


def _attach_unambiguous_timezone(value: datetime, zone: ZoneInfo) -> datetime:
    candidates: list[datetime] = []
    for fold in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=fold)
        round_trip = candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
        if round_trip == value:
            candidates.append(candidate)
    offsets = {candidate.utcoffset() for candidate in candidates}
    if not candidates:
        raise ObservationIdentityError("timestamp is nonexistent in dataset_timezone")
    if len(offsets) > 1:
        raise ObservationIdentityError("timestamp is ambiguous in dataset_timezone")
    return candidates[0]


def _normalize_timestamp(value: Any, *, dataset_timezone: Any) -> str:
    text = _normalize_text(value, field="timestamp")
    iso_text = f"{text[:-1]}+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(iso_text)
    except ValueError as exc:
        raise ObservationIdentityError("timestamp must be a valid ISO-8601 value") from exc

    zone = _load_timezone(dataset_timezone) if dataset_timezone is not None else None
    if parsed.tzinfo is None:
        if zone is None:
            raise ObservationIdentityError("dataset_timezone is required for a naive legacy timestamp")
        parsed = _attach_unambiguous_timezone(parsed, zone)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _canonical_legacy_source_record(
    record: Mapping[str, Any],
    *,
    dataset_root: Any,
    dataset_timezone: Any,
) -> dict[str, Any]:
    record = _require_mapping(record, label="legacy source record")
    missing = [field for field in LEGACY_FINGERPRINT_FIELDS if field not in record]
    if missing:
        raise ObservationIdentityError("legacy source record missing required fields: " + ", ".join(missing))

    _normalize_dataset_root(dataset_root)
    canonical: dict[str, Any] = {}
    for field in LEGACY_FINGERPRINT_FIELDS:
        value = record[field]
        if field in _STRING_FIELDS:
            canonical[field] = _normalize_text(value, field=field)
        elif field in _PATH_FIELDS:
            canonical[field] = _normalize_artifact_path(value, field=field)
        elif field == "timestamp":
            canonical[field] = _normalize_timestamp(value, dataset_timezone=dataset_timezone)
        elif field in _INTEGER_FIELDS:
            canonical[field] = _normalize_integer(value, field=field)
        elif field in _DECIMAL_FIELDS:
            canonical[field] = _normalize_decimal(value, field=field)
        elif field in _BOOLEAN_FIELDS:
            canonical[field] = _normalize_bool(value, field=field)
        else:  # pragma: no cover - the frozen field partition is asserted below.
            raise ObservationIdentityError(f"no canonical rule for legacy field: {field}")

    inspection_id = canonical["inspection_id"]
    _normalize_prepared_identifier(inspection_id, field="inspection_id")
    return canonical


def _serialize_canonical_legacy_record(canonical: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ObservationIdentityError("legacy canonical JSON must be valid UTF-8") from exc


def _reject_conflicting_derived_field(
    record: Mapping[str, Any],
    *,
    field: str,
    expected: str,
    label: str,
) -> None:
    if field in record and record[field] != expected:
        raise ObservationIdentityError(f"{label} has conflicting {field}")


def canonical_legacy_source_record_bytes(
    record: Mapping[str, Any],
    *,
    dataset_root: Any,
    dataset_timezone: Any = None,
) -> bytes:
    """Return exact UTF-8 canonical JSON bytes for the frozen legacy whitelist."""

    canonical = _canonical_legacy_source_record(
        record,
        dataset_root=dataset_root,
        dataset_timezone=dataset_timezone,
    )
    return _serialize_canonical_legacy_record(canonical)


def legacy_source_record_fingerprint(
    record: Mapping[str, Any],
    *,
    dataset_root: Any,
    dataset_timezone: Any = None,
) -> str:
    """Hash one legacy row without consuming labels, answers, or future columns."""

    payload = canonical_legacy_source_record_bytes(
        record,
        dataset_root=dataset_root,
        dataset_timezone=dataset_timezone,
    )
    return hashlib.sha256(payload).hexdigest()


def project_prepared_observation_identities(
    records: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Copy prepared rows and add neutral IDs, rejecting duplicates per inspection."""

    if isinstance(records, (str, bytes, Mapping)) or not isinstance(records, Iterable):
        raise ObservationIdentityError("prepared records must be an iterable of objects")

    projected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row_number, raw_record in enumerate(records, start=1):
        record = _require_mapping(raw_record, label=f"prepared record {row_number}")
        inspection_id = _normalize_prepared_identifier(
            record.get("association_inspection_id"),
            field="association_inspection_id",
        )
        local_id = _normalize_prepared_identifier(
            record.get("local_observation_id"),
            field="local_observation_id",
        )
        current_id = f"{inspection_id}::{local_id}"
        if current_id in seen:
            raise ObservationIdentityError(f"duplicate current_observation_id: {current_id}")
        seen.add(current_id)
        _reject_conflicting_derived_field(
            record,
            field="current_observation_id",
            expected=current_id,
            label=f"prepared record {row_number}",
        )
        copied = deepcopy(dict(record))
        copied["association_inspection_id"] = inspection_id
        copied["local_observation_id"] = local_id
        copied["current_observation_id"] = current_id
        projected.append(copied)
    return projected


def project_legacy_observation_identities(
    records: Iterable[Mapping[str, Any]],
    *,
    dataset_root: Any,
    dataset_timezone: Any = None,
) -> list[dict[str, Any]]:
    """Copy legacy rows and add deterministic, label-independent neutral IDs."""

    if isinstance(records, (str, bytes, Mapping)) or not isinstance(records, Iterable):
        raise ObservationIdentityError("legacy records must be an iterable of objects")

    projected: list[dict[str, Any]] = []
    seen_fingerprints: set[str] = set()
    for row_number, raw_record in enumerate(records, start=1):
        record = _require_mapping(raw_record, label=f"legacy source record {row_number}")
        canonical = _canonical_legacy_source_record(
            record,
            dataset_root=dataset_root,
            dataset_timezone=dataset_timezone,
        )
        canonical_bytes = _serialize_canonical_legacy_record(canonical)
        fingerprint = hashlib.sha256(canonical_bytes).hexdigest()
        if fingerprint in seen_fingerprints:
            raise ObservationIdentityError(
                f"duplicate source_record_fingerprint at legacy row {row_number}: {fingerprint}"
            )
        seen_fingerprints.add(fingerprint)

        local_id = f"legacy::{fingerprint}"
        current_id = f"{canonical['inspection_id']}::{local_id}"
        for field, expected in (
            ("source_record_fingerprint", fingerprint),
            ("local_observation_id", local_id),
            ("current_observation_id", current_id),
        ):
            _reject_conflicting_derived_field(
                record,
                field=field,
                expected=expected,
                label=f"legacy source record {row_number}",
            )
        copied = deepcopy(dict(record))
        copied.update(canonical)
        copied["source_record_fingerprint"] = fingerprint
        copied["local_observation_id"] = local_id
        copied["current_observation_id"] = current_id
        projected.append(copied)
    return projected


_PARTITIONED_FIELDS = _STRING_FIELDS | _PATH_FIELDS | _INTEGER_FIELDS | _DECIMAL_FIELDS | _BOOLEAN_FIELDS | {
    "timestamp"
}
if _PARTITIONED_FIELDS != set(LEGACY_FINGERPRINT_FIELDS):  # pragma: no cover - import-time developer guard.
    raise RuntimeError("legacy fingerprint field partition does not match the frozen whitelist")


__all__ = [
    "LEGACY_FINGERPRINT_FIELDS",
    "ObservationIdentityError",
    "canonical_legacy_source_record_bytes",
    "legacy_source_record_fingerprint",
    "project_legacy_observation_identities",
    "project_prepared_observation_identities",
]
