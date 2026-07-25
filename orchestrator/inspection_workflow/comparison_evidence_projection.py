"""A1 sandbox projection from validated Run-local sources to Comparison Evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import csv
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
from typing import Any

from orchestrator.claim_policy import ClaimPolicyError, load_claim_policy

from .a1_artifacts import (
    PHASE_A1_EXECUTION_PROFILE,
    PHASE_A1_SOURCE_ARTIFACT_LIMIT,
    PhaseA1ArtifactError,
    snapshot_phase_a1_work_artifact,
    write_phase_a1_work_artifact,
)
from .comparison_evidence import (
    COMPARISON_EVIDENCE_SCHEMA_VERSION,
    validate_comparison_evidence_records,
)
from .memory_snapshot import (
    MEMORY_SNAPSHOT_RECORD_FIELDS,
    validate_history_memory_snapshots,
)
from .observation_identity import project_prepared_observation_identities
from .source_references import (
    SOURCE_REFERENCE_SCHEMA_VERSION,
    validate_source_reference_contract,
)


FRAME_PROJECTION_FIELDS = (
    "source_reference_schema_version",
    "identity_source_kind",
    "association_inspection_id",
    "local_observation_id",
    "inspection_id",
    "current_observation_id",
    "frame_id",
    "image_id",
    "timestamp",
    "mask_area_px",
    "observation_source",
    "comparability_status",
)
ENGINEERING_PROJECTION_FIELDS = (
    "source_reference_schema_version",
    "inspection_id",
    "source_observation_ids",
    "max_area_px",
)
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

_INTEGER_RE = re.compile(r"0|[1-9][0-9]*\Z")
_DECIMAL_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?\Z")
_MAX_MASK_AREA_PX = (1 << 63) - 1
_MAX_MASK_AREA_TEXT = str(_MAX_MASK_AREA_PX)
_MAX_JSON_TEXT_BYTES = 64 * 1024
_MAX_MANIFEST_JSON_BYTES = 1024 * 1024
_MAX_PRODUCER_DECIMAL_CHARACTERS = 128
_MAX_PRODUCER_DECIMAL_ADJUSTED = 64
PROJECTION_RECEIPT_SCHEMA_VERSION = "phase_a1_projection_receipt_v1"
PROJECTION_RECEIPT_PATH_TEMPLATE = "runs/{run_id}/work/projection_receipt.json"
_PROJECTION_RECEIPT_FIELDS = {
    "schema_version",
    "run_id",
    "execution_profile",
    "validation_scope",
    "source_artifacts",
    "projected_artifacts",
}
_PROJECTION_RECEIPT_REFERENCE_FIELDS = {
    "kind",
    "path",
    "size_bytes",
    "sha256",
}
_PROJECTION_SOURCE_KINDS = {
    "prepared_manifest",
    "prepared_observation_records",
    "prepared_frame_records",
    "history_association_records",
    "history_manifest",
    "history_query_frames",
    "history_round_association",
    "history_memory_before",
    "history_memory_after",
}
_PROJECTION_VALIDATION_SCOPE = "prepared_readiness_and_history_contract"
_PREPARED_FINGERPRINT_FIELDS = (
    "association_inspection_id",
    "local_observation_id",
    "frame_id",
    "image_id",
    "timestamp",
    "mask_area_px",
    "observation_source",
    "comparability_status",
)


class ComparisonEvidenceProjectionError(ValueError):
    """Raised when Run-local sources cannot form credible A1 Evidence."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _receipt_reference(
    *,
    kind: str,
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "kind": kind,
        "path": snapshot["path"],
        "size_bytes": snapshot["size_bytes"],
        "sha256": snapshot["sha256"],
    }


def _projected_reference(path: str, data: bytes) -> dict[str, Any]:
    return {
        "kind": "projected_work_artifact",
        "path": path,
        "size_bytes": len(data),
        "sha256": _sha256_bytes(data),
    }


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ComparisonEvidenceProjectionError(
                f"Association manifest contains duplicate key: {key}"
            )
        result[key] = value
    return result


def _parse_json_object(data: bytes, *, label: str) -> dict[str, Any]:
    if len(data) > _MAX_MANIFEST_JSON_BYTES:
        raise ComparisonEvidenceProjectionError(f"{label} exceeds the JSON size limit")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ComparisonEvidenceProjectionError(f"{label} must be UTF-8") from exc
    if text.startswith("\ufeff"):
        raise ComparisonEvidenceProjectionError(f"{label} must be UTF-8 without BOM")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {constant}")
            ),
        )
    except ComparisonEvidenceProjectionError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ComparisonEvidenceProjectionError(f"{label} must contain valid JSON") from exc
    if not isinstance(value, dict):
        raise ComparisonEvidenceProjectionError(f"{label} must contain a JSON object")
    return value


def _validate_receipt_references(
    value: Any,
    *,
    label: str,
    allowed_kinds: set[str],
) -> list[dict[str, Any]]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > PHASE_A1_SOURCE_ARTIFACT_LIMIT
    ):
        raise ComparisonEvidenceProjectionError(
            f"{label} must be a non-empty A1 pilot-sized list"
        )
    normalized: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for index, reference in enumerate(value, start=1):
        item_label = f"{label} item {index}"
        if (
            not isinstance(reference, Mapping)
            or set(reference) != _PROJECTION_RECEIPT_REFERENCE_FIELDS
        ):
            raise ComparisonEvidenceProjectionError(
                f"{item_label} fields are invalid"
            )
        kind = reference["kind"]
        path = reference["path"]
        size_bytes = reference["size_bytes"]
        sha256 = reference["sha256"]
        if not isinstance(kind, str) or kind not in allowed_kinds:
            raise ComparisonEvidenceProjectionError(f"{item_label} kind is invalid")
        if (
            not isinstance(path, str)
            or not path
            or "\\" in path
            or ":" in path
            or path != Path(path).as_posix()
        ):
            raise ComparisonEvidenceProjectionError(f"{item_label} path is invalid")
        if path in seen_paths:
            raise ComparisonEvidenceProjectionError(
                f"{label} contains duplicate path: {path}"
            )
        seen_paths.add(path)
        if type(size_bytes) is not int or size_bytes < 0:
            raise ComparisonEvidenceProjectionError(
                f"{item_label} size_bytes is invalid"
            )
        if (
            not isinstance(sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", sha256) is None
        ):
            raise ComparisonEvidenceProjectionError(f"{item_label} sha256 is invalid")
        normalized.append(
            {
                "kind": kind,
                "path": path,
                "size_bytes": size_bytes,
                "sha256": sha256,
            }
        )
    if normalized != sorted(normalized, key=lambda item: (item["kind"], item["path"])):
        raise ComparisonEvidenceProjectionError(
            f"{label} must use stable kind/path order"
        )
    return normalized


def parse_projection_receipt(
    data: bytes,
    *,
    run_id: str,
    execution_profile: str,
) -> dict[str, Any]:
    """Validate the deterministic receipt that binds producer and projected bytes."""

    receipt = _parse_json_object(data, label="projection_receipt.json")
    if set(receipt) != _PROJECTION_RECEIPT_FIELDS:
        raise ComparisonEvidenceProjectionError(
            "projection_receipt.json fields are invalid"
        )
    if (
        receipt["schema_version"] != PROJECTION_RECEIPT_SCHEMA_VERSION
        or receipt["run_id"] != run_id
        or receipt["execution_profile"] != execution_profile
        or receipt["validation_scope"] != _PROJECTION_VALIDATION_SCOPE
    ):
        raise ComparisonEvidenceProjectionError(
            "projection_receipt.json identity or validation scope is invalid"
        )
    source_artifacts = _validate_receipt_references(
        receipt["source_artifacts"],
        label="projection receipt source_artifacts",
        allowed_kinds=_PROJECTION_SOURCE_KINDS,
    )
    projected_artifacts = _validate_receipt_references(
        receipt["projected_artifacts"],
        label="projection receipt projected_artifacts",
        allowed_kinds={"projected_work_artifact"},
    )
    source_paths = {item["path"] for item in source_artifacts}
    projected_paths = {item["path"] for item in projected_artifacts}
    if source_paths & projected_paths:
        raise ComparisonEvidenceProjectionError(
            "projection receipt source and projected paths must be disjoint"
        )
    return {
        **receipt,
        "source_artifacts": source_artifacts,
        "projected_artifacts": projected_artifacts,
    }


def _parse_csv_rows(
    data: bytes,
    *,
    expected_fieldnames: Sequence[str],
    label: str,
    allow_empty: bool,
) -> list[dict[str, str]]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ComparisonEvidenceProjectionError(f"{label} must be UTF-8") from exc
    if text.startswith("\ufeff"):
        raise ComparisonEvidenceProjectionError(f"{label} must be UTF-8 without BOM")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    try:
        fieldnames = reader.fieldnames
    except csv.Error as exc:
        raise ComparisonEvidenceProjectionError(f"{label} contains malformed CSV") from exc
    if fieldnames != list(expected_fieldnames):
        raise ComparisonEvidenceProjectionError(
            f"{label} fieldnames must exactly match the A1 projection contract"
        )
    rows: list[dict[str, str]] = []
    try:
        for row_number, row in enumerate(reader, start=1):
            if None in row or any(value is None for value in row.values()):
                raise ComparisonEvidenceProjectionError(
                    f"{label} row {row_number} has a malformed column count"
                )
            rows.append(dict(row))
    except csv.Error as exc:
        raise ComparisonEvidenceProjectionError(f"{label} contains malformed CSV") from exc
    if not rows and not allow_empty:
        raise ComparisonEvidenceProjectionError(f"{label} must not be empty")
    return rows


def _parse_producer_csv_rows(
    data: bytes,
    *,
    expected_fieldnames: Sequence[str],
    label: str,
    allow_empty: bool,
) -> list[dict[str, str]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ComparisonEvidenceProjectionError(f"{label} must be UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    try:
        fieldnames = reader.fieldnames
        rows = list(reader)
    except csv.Error as exc:
        raise ComparisonEvidenceProjectionError(f"{label} contains malformed CSV") from exc
    if fieldnames != list(expected_fieldnames):
        raise ComparisonEvidenceProjectionError(
            f"{label} fieldnames do not match the existing producer contract"
        )
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ComparisonEvidenceProjectionError(f"{label} has a malformed column count")
    if not rows and not allow_empty:
        raise ComparisonEvidenceProjectionError(f"{label} must not be empty")
    return [dict(row) for row in rows]


def _canonical_csv_bytes(
    fieldnames: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
) -> bytes:
    projected_rows = []
    for row_number, row in enumerate(rows, start=1):
        missing = [field for field in fieldnames if field not in row]
        if missing:
            raise ComparisonEvidenceProjectionError(
                f"materialized CSV row {row_number} is missing fields: {', '.join(missing)}"
            )
        projected_rows.append({field: row[field] for field in fieldnames})
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=list(fieldnames),
        lineterminator="\n",
        extrasaction="raise",
    )
    writer.writeheader()
    writer.writerows(projected_rows)
    return buffer.getvalue().encode("utf-8")


def _canonical_string(value: Any, *, field: str, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ComparisonEvidenceProjectionError(
            f"{label} {field} must be a canonical non-empty string"
        )
    return value


def _optional_string(value: Any, *, field: str, label: str) -> str | None:
    if value == "":
        return None
    return _canonical_string(value, field=field, label=label)


def _canonical_bool(value: Any, *, field: str, label: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise ComparisonEvidenceProjectionError(f"{label} {field} must be true or false")


def _canonical_integer(value: Any, *, field: str, label: str) -> int:
    if not isinstance(value, str) or _INTEGER_RE.fullmatch(value) is None:
        raise ComparisonEvidenceProjectionError(
            f"{label} {field} must use canonical non-negative integer encoding"
        )
    if (
        len(value) > len(_MAX_MASK_AREA_TEXT)
        or (
            len(value) == len(_MAX_MASK_AREA_TEXT)
            and value > _MAX_MASK_AREA_TEXT
        )
    ):
        raise ComparisonEvidenceProjectionError(
            f"{label} {field} must not exceed {_MAX_MASK_AREA_PX}"
        )
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ComparisonEvidenceProjectionError(
            f"{label} {field} must use canonical non-negative integer encoding"
        ) from exc
    return parsed


def _optional_decimal(value: Any, *, field: str, label: str) -> str | None:
    if value == "":
        return None
    if not isinstance(value, str) or _DECIMAL_RE.fullmatch(value) is None:
        raise ComparisonEvidenceProjectionError(
            f"{label} {field} must use canonical decimal encoding"
        )
    return value


def _canonical_string_list(value: Any, *, field: str, label: str) -> list[str]:
    if not isinstance(value, str):
        raise ComparisonEvidenceProjectionError(f"{label} {field} must be canonical JSON")
    if len(value.encode("utf-8")) > _MAX_JSON_TEXT_BYTES:
        raise ComparisonEvidenceProjectionError(
            f"{label} {field} exceeds the JSON size limit"
        )
    try:
        parsed = json.loads(
            value,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {constant}")
            ),
        )
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ComparisonEvidenceProjectionError(f"{label} {field} must be canonical JSON") from exc
    if (
        not isinstance(parsed, list)
        or not parsed
        or any(not isinstance(item, str) or not item or item != item.strip() for item in parsed)
        or parsed != sorted(set(parsed))
    ):
        raise ComparisonEvidenceProjectionError(
            f"{label} {field} must be a non-empty sorted unique string list"
        )
    canonical = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    if value != canonical:
        raise ComparisonEvidenceProjectionError(f"{label} {field} must use canonical JSON")
    return parsed


def _source_reference(
    *,
    role: str,
    snapshot: Mapping[str, Any],
) -> dict[str, str]:
    return {
        "role": role,
        "path": snapshot["path"],
        "expected_sha256": snapshot["sha256"],
    }


def _fixed_work_path(run_id: str, filename: str) -> str:
    return f"runs/{run_id}/work/{filename}"


def _snapshot(
    project_root: Any,
    *,
    run_id: str,
    relative_path: str,
) -> dict[str, Any]:
    try:
        return snapshot_phase_a1_work_artifact(
            project_root,
            run_id=run_id,
            execution_profile=PHASE_A1_EXECUTION_PROFILE,
            relative_path=relative_path,
        )
    except PhaseA1ArtifactError as exc:
        raise ComparisonEvidenceProjectionError(str(exc)) from exc


def _require_prepared_snapshot_fingerprint(
    manifest: Mapping[str, Any],
    *,
    output_name: str,
    snapshot: Mapping[str, Any],
) -> None:
    outputs = manifest.get("outputs")
    if not isinstance(outputs, Mapping):
        raise ComparisonEvidenceProjectionError(
            "Prepared manifest outputs must be an object"
        )
    reference = outputs.get(output_name)
    expected_path = f"{output_name}.csv"
    if (
        not isinstance(reference, Mapping)
        or reference.get("path") != expected_path
        or reference.get("size_bytes") != snapshot["size_bytes"]
        or reference.get("sha256") != snapshot["sha256"]
    ):
        raise ComparisonEvidenceProjectionError(
            f"Prepared {expected_path} snapshot does not match its validated manifest"
        )


def _validate_prepared_snapshot_bytes(
    manifest: Mapping[str, Any],
    *,
    observation_snapshot: Mapping[str, Any],
    frame_snapshot: Mapping[str, Any],
    validator: Any,
) -> None:
    row_counts = manifest.get("row_counts")
    if not isinstance(row_counts, Mapping):
        raise ComparisonEvidenceProjectionError(
            "Prepared manifest row_counts must be an object"
        )
    observation_count = row_counts.get("observation_records")
    frame_count = row_counts.get("frame_records")
    if (
        type(observation_count) is not int
        or observation_count < 0
        or type(frame_count) is not int
        or frame_count < 0
    ):
        raise ComparisonEvidenceProjectionError(
            "Prepared manifest row_counts are invalid"
        )
    try:
        with tempfile.TemporaryDirectory(prefix="phase-a1-prepared-snapshot-") as temp_dir:
            snapshot_dir = Path(temp_dir)
            observation_path = snapshot_dir / "observation_records.csv"
            frame_path = snapshot_dir / "frame_records.csv"
            observation_path.write_bytes(observation_snapshot["data"])
            frame_path.write_bytes(frame_snapshot["data"])
            validator(
                observation_path,
                frame_path,
                expected_observation_count=observation_count,
                expected_frame_count=frame_count,
            )
    except (OSError, ValueError) as exc:
        raise ComparisonEvidenceProjectionError(
            f"Prepared captured CSV snapshots are invalid: {exc}"
        ) from exc


def _canonical_receipt_bytes(receipt: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            receipt,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _normalize_frame_rows(rows: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    try:
        policy = load_claim_policy()
    except ClaimPolicyError as exc:
        raise ComparisonEvidenceProjectionError(
            f"unable to load Claim Policy for Frame source validation: {exc}"
        ) from exc
    source_enum = set(policy["observation_source_enum"])
    status_enum = set(policy["comparability_status_enum"])
    verified_sources = set(
        policy["source_comparability_rules"]["verified_comparable_allowed_sources"]
    )
    try:
        projected = project_prepared_observation_identities(rows)
    except ValueError as exc:
        raise ComparisonEvidenceProjectionError(
            f"frame_records.csv prepared observation identity is invalid: {exc}"
        ) from exc
    normalized: list[dict[str, Any]] = []
    for row_number, row in enumerate(projected, start=1):
        label = f"frame_records.csv row {row_number}"
        if row["identity_source_kind"] != "prepared":
            raise ComparisonEvidenceProjectionError(
                f"{label} identity_source_kind must be prepared in this A1 slice"
            )
        inspection_id = _canonical_string(
            row["inspection_id"],
            field="inspection_id",
            label=label,
        )
        if row["association_inspection_id"] != inspection_id:
            raise ComparisonEvidenceProjectionError(
                f"{label} association_inspection_id must equal inspection_id"
            )
        observation_source = row["observation_source"]
        comparability_status = row["comparability_status"]
        if observation_source not in source_enum or observation_source == "mixed_sources":
            raise ComparisonEvidenceProjectionError(
                f"{label} observation_source is invalid or derived-only"
            )
        if comparability_status not in status_enum:
            raise ComparisonEvidenceProjectionError(
                f"{label} comparability_status is invalid"
            )
        if observation_source == "verified_fixture":
            raise ComparisonEvidenceProjectionError(
                f"{label} verified_fixture is not allowed in phase_a1_sandbox"
            )
        if (
            comparability_status == "verified_comparable"
            and observation_source not in verified_sources
        ):
            raise ComparisonEvidenceProjectionError(
                f"{label} observation_source cannot declare verified_comparable"
            )
        area = _canonical_integer(
            row["mask_area_px"],
            field="mask_area_px",
            label=label,
        )
        fingerprint_payload = {
            field: area if field == "mask_area_px" else row[field]
            for field in _PREPARED_FINGERPRINT_FIELDS
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        normalized.append(
            {
                **dict(row),
                "mask_area_px": area,
                "source_record_fingerprint": fingerprint,
            }
        )
    return normalized


def _project_prepared_frame_rows(
    rows: Sequence[Mapping[str, str]],
) -> list[dict[str, Any]]:
    projected = []
    for row_number, row in enumerate(rows, start=1):
        label = f"prepared frame_records.csv row {row_number}"
        required = {
            "association_inspection_id",
            "inspection_id",
            "local_observation_id",
            "frame_id",
            "image_id",
            "timestamp",
            "kict_area_px",
            "observation_source",
            "comparability_status",
        }
        if not required.issubset(row):
            raise ComparisonEvidenceProjectionError(
                f"{label} is missing the A1 projection fields"
            )
        if row["association_inspection_id"] != row["inspection_id"]:
            raise ComparisonEvidenceProjectionError(
                f"{label} association_inspection_id must equal inspection_id"
            )
        timestamp = row["timestamp"]
        try:
            parsed_timestamp = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%fZ")
        except ValueError:
            try:
                parsed_timestamp = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
            except (TypeError, ValueError) as exc:
                raise ComparisonEvidenceProjectionError(
                    f"{label} timestamp must be canonical UTC"
                ) from exc
        canonical_timestamp = parsed_timestamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        projected.append(
            {
                "source_reference_schema_version": SOURCE_REFERENCE_SCHEMA_VERSION,
                "identity_source_kind": "prepared",
                "association_inspection_id": row["association_inspection_id"],
                "local_observation_id": row["local_observation_id"],
                "inspection_id": row["inspection_id"],
                "current_observation_id": (
                    f"{row['association_inspection_id']}::{row['local_observation_id']}"
                ),
                "frame_id": row["frame_id"],
                "image_id": row["image_id"],
                "timestamp": canonical_timestamp,
                "mask_area_px": row["kict_area_px"],
                "observation_source": row["observation_source"],
                "comparability_status": row["comparability_status"],
            }
        )
    return _normalize_frame_rows(projected)


def _project_engineering_rows(
    frame_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in frame_rows:
        grouped.setdefault(
            (row["inspection_id"], row["current_observation_id"]),
            [],
        ).append(row)
    result = []
    for (inspection_id, observation_id), source_rows in sorted(grouped.items()):
        result.append(
            {
                "source_reference_schema_version": SOURCE_REFERENCE_SCHEMA_VERSION,
                "inspection_id": inspection_id,
                "source_observation_ids": json.dumps(
                    [observation_id],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "max_area_px": str(max(row["mask_area_px"] for row in source_rows)),
            }
        )
    return result


def _normalize_engineering_rows(rows: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row_number, row in enumerate(rows, start=1):
        label = f"engineering_records.csv row {row_number}"
        normalized.append(
            {
                **dict(row),
                "source_observation_ids": _canonical_string_list(
                    row["source_observation_ids"],
                    field="source_observation_ids",
                    label=label,
                ),
                "max_area_px": _canonical_integer(
                    row["max_area_px"],
                    field="max_area_px",
                    label=label,
                ),
            }
        )
    return normalized


def _normalize_association_rows(rows: Sequence[Mapping[str, str]], *, label: str) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row_number, row in enumerate(rows, start=1):
        row_label = f"{label} row {row_number}"
        normalized.append(
            {
                **dict(row),
                "memory_id": _optional_string(
                    row["memory_id"],
                    field="memory_id",
                    label=row_label,
                ),
                "use_disease_id_score": _canonical_bool(
                    row["use_disease_id_score"],
                    field="use_disease_id_score",
                    label=row_label,
                ),
                "association_score": _optional_decimal(
                    row["association_score"],
                    field="association_score",
                    label=row_label,
                ),
                "candidate_count": _canonical_integer(
                    row["candidate_count"],
                    field="candidate_count",
                    label=row_label,
                ),
                "score_margin": _optional_decimal(
                    row["score_margin"],
                    field="score_margin",
                    label=row_label,
                ),
                "conflict_reason": _optional_string(
                    row["conflict_reason"],
                    field="conflict_reason",
                    label=row_label,
                ),
                "needs_manual_review": _canonical_bool(
                    row["needs_manual_review"],
                    field="needs_manual_review",
                    label=row_label,
                ),
            }
        )
    return normalized


def _engineering_by_observation(
    engineering_rows: Sequence[Mapping[str, Any]],
    frame_rows: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    frame_index = {
        (row["inspection_id"], row["current_observation_id"]): row
        for row in frame_rows
    }
    result: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row_number, row in enumerate(engineering_rows, start=1):
        source_frames = [
            frame_index[(row["inspection_id"], current_observation_id)]
            for current_observation_id in row["source_observation_ids"]
        ]
        expected_max_area = max(frame["mask_area_px"] for frame in source_frames)
        if row["max_area_px"] != expected_max_area:
            raise ComparisonEvidenceProjectionError(
                f"engineering_records.csv row {row_number} max_area_px must equal "
                "the maximum source Frame mask_area_px"
            )
        source_values = sorted({frame["observation_source"] for frame in source_frames})
        current_source = source_values[0] if len(source_values) == 1 else "mixed_sources"
        statuses = {frame["comparability_status"] for frame in source_frames}
        if "insufficient_history" in statuses:
            current_status = "insufficient_history"
        elif statuses == {"verified_comparable"} and current_source != "mixed_sources":
            current_status = "verified_comparable"
        elif len(statuses) == 1 and current_source != "mixed_sources":
            current_status = next(iter(statuses))
        else:
            current_status = "not_longitudinally_comparable"
        projected_engineering = {
            **dict(row),
            "current_observation_source": current_source,
            "current_comparability_status": current_status,
        }
        for current_observation_id in row["source_observation_ids"]:
            result[(row["inspection_id"], current_observation_id)] = projected_engineering
    return result


def _validate_inspection_chronology(
    frame_rows: Sequence[Mapping[str, Any]],
    inspection_order: Sequence[str],
) -> None:
    timestamps_by_inspection: dict[str, list[datetime]] = {
        inspection_id: [] for inspection_id in inspection_order
    }
    for row_number, row in enumerate(frame_rows, start=1):
        try:
            timestamp = datetime.strptime(
                row["timestamp"],
                "%Y-%m-%dT%H:%M:%S.%fZ",
            )
        except (TypeError, ValueError) as exc:
            raise ComparisonEvidenceProjectionError(
                f"frame_records.csv row {row_number} timestamp must use canonical UTC microseconds"
            ) from exc
        timestamps_by_inspection[row["inspection_id"]].append(timestamp)

    for previous_id, current_id in zip(inspection_order, inspection_order[1:]):
        previous_times = timestamps_by_inspection[previous_id]
        current_times = timestamps_by_inspection[current_id]
        if max(previous_times) >= min(current_times):
            raise ComparisonEvidenceProjectionError(
                f"Association manifest order is not chronological: {previous_id} must be earlier than {current_id}"
            )


def _build_static_evidence(
    *,
    frame: Mapping[str, Any],
    engineering: Mapping[str, Any],
    association: Mapping[str, Any] | None,
    association_hash: str,
    manifest_hash: str,
    engineering_hash: str,
) -> dict[str, Any]:
    if association is not None:
        if association["association_status"] == "matched":
            raise ComparisonEvidenceProjectionError(
                "matched Association projection requires a source-proof Memory schema upgrade"
            )
        if association["association_status"] != "unmatched":
            raise ComparisonEvidenceProjectionError(
                "Association status must be matched or unmatched"
            )
        if association["memory_id"] is not None:
            raise ComparisonEvidenceProjectionError(
                "unmatched Association must use canonical empty memory_id"
            )
        identity_state = "association_rejected"
        comparability_reason = "no_history_match"
    else:
        identity_state = "association_not_applicable"
        comparability_reason = "baseline_current_only"

    current_observation_id = frame["current_observation_id"]
    association_values = {
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
    }
    if association is not None:
        association_values.update(
            {
                field: association[field]
                for field in association_values
            }
        )

    return {
        "comparison_evidence_schema_version": COMPARISON_EVIDENCE_SCHEMA_VERSION,
        "evidence_id": f"EVD::{current_observation_id}::inspection_level_max_mask_area_px",
        "execution_profile": PHASE_A1_EXECUTION_PROFILE,
        "evidence_schema_valid": True,
        "current_record_valid": True,
        "current_inspection_id": frame["inspection_id"],
        "current_frame_id": frame["frame_id"],
        "current_image_id": frame["image_id"],
        "current_observation_id": current_observation_id,
        "current_timestamp": frame["timestamp"],
        "current_observation_source_declared": True,
        "current_observation_source": engineering["current_observation_source"],
        "current_comparability_status": engineering["current_comparability_status"],
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
        "comparison_group_id": (
            f"{current_observation_id}::inspection_level_max_mask_area_px"
        ),
        "metric_name": "inspection_level_max_mask_area_px",
        "metric_type": "area",
        "value_domain": "mask_pixel_area",
        "measurement_method": "inspection_level_max_mask_area_px",
        "measurement_unit": "pixel²",
        "current_value": engineering["max_area_px"],
        "previous_memory_snapshot_value": None,
        "absolute_difference": None,
        "relative_difference": None,
        **association_values,
        "association_supported_pair": False,
        "identity_evidence_state": identity_state,
        "valid_timepoint_count": 1,
        "metric_consistent": True,
        "measurement_method_consistent": True,
        "temporal_order_valid": False,
        "difference_valid": False,
        "relative_difference_valid": False,
        "evidence_valid": True,
        "invalid_reason": None,
        "comparison_comparability_status": "insufficient_history",
        "comparability_reason": comparability_reason,
        "registration_status": "not_verified",
        "registration_evidence_source": "none",
        "registration_evidence_sha256": None,
        "physical_scale_calibrated": False,
        "scale_calibration_source": "none",
        "scale_calibration_sha256": None,
        "measurement_uncertainty": None,
        "uncertainty_source": "not_available",
        "uncertainty_unit": None,
        "source_current_record_fingerprint": frame["source_record_fingerprint"],
        "source_association_artifact_sha256": association_hash,
        "source_association_manifest_sha256": manifest_hash,
        "source_memory_snapshot_sha256": None,
        "source_engineering_artifact_sha256": engineering_hash,
    }


def _project_producer_association_rows(
    rows: Sequence[Mapping[str, str]],
    *,
    frame_rows: Sequence[Mapping[str, Any]],
    label: str,
) -> list[dict[str, Any]]:
    frames_by_query: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for frame in frame_rows:
        frames_by_query.setdefault(
            (frame["inspection_id"], frame["frame_id"], frame["image_id"]),
            [],
        ).append(frame)
    projected = []
    for row_number, row in enumerate(rows, start=1):
        query_key = (row["inspection_id"], row["frame_id"], row["image_id"])
        candidates = frames_by_query.get(query_key, [])
        if len(candidates) != 1:
            raise ComparisonEvidenceProjectionError(
                f"{label} row {row_number} cannot resolve one neutral observation "
                "from inspection_id/frame_id/image_id"
            )
        projected.append(
            {
                "source_reference_schema_version": SOURCE_REFERENCE_SCHEMA_VERSION,
                "association_id": row["association_id"],
                "inspection_id": row["inspection_id"],
                "current_observation_id": candidates[0]["current_observation_id"],
                "frame_id": row["frame_id"],
                "image_id": row["image_id"],
                "memory_id": _project_memory_id(row["memory_id"]),
                "association_status": row["association_status"],
                "association_mode": row["association_mode"],
                "use_disease_id_score": row["use_disease_id_score"],
                "association_score": _canonical_decimal_from_producer(
                    row["association_score"],
                    field="association_score",
                    label=f"{label} row {row_number}",
                ),
                "match_type": row["match_type"],
                "candidate_count": row["candidate_count"],
                "score_margin": _canonical_decimal_from_producer(
                    row["score_margin"],
                    field="score_margin",
                    label=f"{label} row {row_number}",
                ),
                "conflict_reason": row["conflict_reason"],
                "needs_manual_review": row["needs_manual_review"],
            }
        )
    return projected


def _project_memory_id(value: str) -> str:
    if value == "":
        return ""
    if not isinstance(value, str) or value != value.strip():
        raise ComparisonEvidenceProjectionError(
            "producer memory_id must be a canonical string"
        )
    return f"MEM-A1-{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _project_memory_rows(
    rows: Sequence[Mapping[str, str]],
) -> list[dict[str, str]]:
    projected = []
    for row in rows:
        value = {
            **dict(row),
            "memory_id": _project_memory_id(row["memory_id"]),
        }
        if value["comparability_status"] == "insufficient_history":
            value["growth_trend"] = "数据不足"
        elif value["comparability_status"] != "verified_comparable":
            value["growth_trend"] = "不可比较"
        projected.append(value)
    return projected


def _canonical_decimal_from_producer(
    value: str,
    *,
    field: str,
    label: str,
) -> str:
    if value == "":
        return ""
    if (
        not isinstance(value, str)
        or len(value) > _MAX_PRODUCER_DECIMAL_CHARACTERS
    ):
        raise ComparisonEvidenceProjectionError(
            f"{label} {field} exceeds the producer decimal limit"
        )
    try:
        from decimal import Decimal, InvalidOperation

        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ComparisonEvidenceProjectionError(
            f"{label} {field} is not a finite decimal"
        ) from exc
    if not parsed.is_finite():
        raise ComparisonEvidenceProjectionError(
            f"{label} {field} is not a finite decimal"
        )
    if parsed == 0:
        return "0"
    if abs(parsed.adjusted()) > _MAX_PRODUCER_DECIMAL_ADJUSTED:
        raise ComparisonEvidenceProjectionError(
            f"{label} {field} exceeds the producer decimal magnitude limit"
        )
    canonical = format(parsed, "f").rstrip("0").rstrip(".")
    if _DECIMAL_RE.fullmatch(canonical) is None:
        raise ComparisonEvidenceProjectionError(
            f"{label} {field} cannot be canonically encoded"
        )
    return canonical


def materialize_prepared_history_projection_sources(
    project_root: Any,
    *,
    run_id: str,
    execution_profile: str,
    prepared_manifest_path: str,
    history_association_path: str,
    history_manifest_path: str,
) -> dict[str, str]:
    """Bridge validated Prepared + history-only outputs into A1 neutral relations.

    Legacy ``label_disease_id`` is deliberately ignored. Association rows resolve
    through inspection/frame/image only and therefore fail closed when that tuple
    identifies more than one current observation. Matched rows remain blocked by
    the downstream source-proof Memory boundary.
    """

    if execution_profile != PHASE_A1_EXECUTION_PROFILE:
        raise ComparisonEvidenceProjectionError(
            f"execution_profile must be {PHASE_A1_EXECUTION_PROFILE}"
        )
    root = Path(project_root)
    try:
        # Validate containment before passing a filesystem path to the existing
        # readiness gate. The bytes used below are captured again after that gate.
        snapshot_phase_a1_work_artifact(
            project_root,
            run_id=run_id,
            execution_profile=execution_profile,
            relative_path=prepared_manifest_path,
        )
        history_association_snapshot = snapshot_phase_a1_work_artifact(
            project_root,
            run_id=run_id,
            execution_profile=execution_profile,
            relative_path=history_association_path,
        )
        history_manifest_snapshot = snapshot_phase_a1_work_artifact(
            project_root,
            run_id=run_id,
            execution_profile=execution_profile,
            relative_path=history_manifest_path,
        )
    except PhaseA1ArtifactError as exc:
        raise ComparisonEvidenceProjectionError(str(exc)) from exc

    prepared_manifest_file = root.joinpath(*prepared_manifest_path.split("/"))
    try:
        from scripts.prepare_real_inspection_pilot import (
            FRAME_FIELDNAMES as PREPARED_FRAME_FIELDNAMES,
            require_inference_ready,
            validate_prepared_artifacts,
        )

        require_inference_ready(prepared_manifest_file)
    except (OSError, ValueError) as exc:
        raise ComparisonEvidenceProjectionError(
            f"Prepared input is not inference-ready: {exc}"
        ) from exc

    prepared_frame_path = prepared_manifest_file.parent / "frame_records.csv"
    prepared_observation_path = prepared_manifest_file.parent / "observation_records.csv"
    try:
        prepared_frame_relative = prepared_frame_path.resolve().relative_to(
            root.resolve()
        ).as_posix()
        prepared_observation_relative = prepared_observation_path.resolve().relative_to(
            root.resolve()
        ).as_posix()
    except ValueError as exc:
        raise ComparisonEvidenceProjectionError(
            "Prepared artifact paths must remain inside the A1 sandbox"
        ) from exc
    prepared_manifest_snapshot = _snapshot(
        project_root,
        run_id=run_id,
        relative_path=prepared_manifest_path,
    )
    prepared_frame_snapshot = _snapshot(
        project_root,
        run_id=run_id,
        relative_path=prepared_frame_relative,
    )
    prepared_observation_snapshot = _snapshot(
        project_root,
        run_id=run_id,
        relative_path=prepared_observation_relative,
    )
    prepared_manifest = _parse_json_object(
        prepared_manifest_snapshot["data"],
        label="Prepared preparation_manifest.json",
    )
    try:
        require_inference_ready(prepared_manifest)
    except ValueError as exc:
        raise ComparisonEvidenceProjectionError(
            f"Prepared manifest snapshot is not logically inference-ready: {exc}"
        ) from exc
    _require_prepared_snapshot_fingerprint(
        prepared_manifest,
        output_name="observation_records",
        snapshot=prepared_observation_snapshot,
    )
    _require_prepared_snapshot_fingerprint(
        prepared_manifest,
        output_name="frame_records",
        snapshot=prepared_frame_snapshot,
    )
    _validate_prepared_snapshot_bytes(
        prepared_manifest,
        observation_snapshot=prepared_observation_snapshot,
        frame_snapshot=prepared_frame_snapshot,
        validator=validate_prepared_artifacts,
    )
    receipt_sources = [
        _receipt_reference(
            kind="prepared_manifest",
            snapshot=prepared_manifest_snapshot,
        ),
        _receipt_reference(
            kind="prepared_observation_records",
            snapshot=prepared_observation_snapshot,
        ),
        _receipt_reference(
            kind="prepared_frame_records",
            snapshot=prepared_frame_snapshot,
        ),
        _receipt_reference(
            kind="history_association_records",
            snapshot=history_association_snapshot,
        ),
        _receipt_reference(
            kind="history_manifest",
            snapshot=history_manifest_snapshot,
        ),
    ]
    prepared_rows = _parse_producer_csv_rows(
        prepared_frame_snapshot["data"],
        expected_fieldnames=PREPARED_FRAME_FIELDNAMES,
        label="Prepared frame_records.csv",
        allow_empty=False,
    )
    frame_rows = _project_prepared_frame_rows(prepared_rows)

    from orchestrator.agents.association_agent import AssociationAgent

    producer_association_rows = _parse_producer_csv_rows(
            history_association_snapshot["data"],
            expected_fieldnames=AssociationAgent.fieldnames(),
            label="history-only association_records.csv",
            allow_empty=True,
    )
    association_rows = _project_producer_association_rows(
        producer_association_rows,
        frame_rows=frame_rows,
        label="history-only association_records.csv",
    )

    raw_history_manifest = _parse_json_object(
        history_manifest_snapshot["data"],
        label="history-only association_manifest.json",
    )
    raw_snapshot_records: dict[str, list[dict[str, str]]] = {}
    raw_snapshot_fieldnames: dict[str, Sequence[str]] = {}
    raw_round_sources: list[dict[str, Any]] = []
    for round_number, round_entry in enumerate(
        raw_history_manifest.get("rounds", []),
        start=1,
    ):
        if not isinstance(round_entry, Mapping):
            raise ComparisonEvidenceProjectionError(
                f"history-only manifest round {round_number} must be an object"
            )
        query_path = round_entry.get("query_frames")
        if not isinstance(query_path, str):
            raise ComparisonEvidenceProjectionError(
                f"history-only manifest round {round_number} query_frames is invalid"
            )
        query_snapshot = _snapshot(
            project_root,
            run_id=run_id,
            relative_path=query_path,
        )
        receipt_sources.append(
            _receipt_reference(
                kind="history_query_frames",
                snapshot=query_snapshot,
            )
        )
        query_rows = _project_prepared_frame_rows(
            _parse_producer_csv_rows(
                query_snapshot["data"],
                expected_fieldnames=PREPARED_FRAME_FIELDNAMES,
                label=query_path,
                allow_empty=False,
            )
        )
        round_source: dict[str, Any] = {
            "query_rows": query_rows,
            "raw_round": round_entry,
        }
        if round_entry.get("mode") == "history_only":
            for field in ("memory_before", "memory_after"):
                path_value = round_entry.get(field)
                if not isinstance(path_value, str):
                    raise ComparisonEvidenceProjectionError(
                        f"history-only manifest round {round_number} {field} is invalid"
                    )
                memory_snapshot = _snapshot(
                    project_root,
                    run_id=run_id,
                    relative_path=path_value,
                )
                receipt_sources.append(
                    _receipt_reference(
                        kind=(
                            "history_memory_before"
                            if field == "memory_before"
                            else "history_memory_after"
                        ),
                        snapshot=memory_snapshot,
                    )
                )
                memory_rows = _project_memory_rows(
                    _parse_producer_csv_rows(
                        memory_snapshot["data"],
                        expected_fieldnames=MEMORY_SNAPSHOT_RECORD_FIELDS,
                        label=path_value,
                        allow_empty=True,
                    )
                )
                round_source[f"{field}_rows"] = memory_rows
                if field == "memory_before":
                    raw_snapshot_records[path_value] = memory_rows
                    raw_snapshot_fieldnames[path_value] = MEMORY_SNAPSHOT_RECORD_FIELDS
            round_association_path = round_entry.get("association_records")
            if not isinstance(round_association_path, str):
                raise ComparisonEvidenceProjectionError(
                    f"history-only manifest round {round_number} association_records is invalid"
                )
            round_association_snapshot = _snapshot(
                project_root,
                run_id=run_id,
                relative_path=round_association_path,
            )
            receipt_sources.append(
                _receipt_reference(
                    kind="history_round_association",
                    snapshot=round_association_snapshot,
                )
            )
            round_source["association_rows"] = _project_producer_association_rows(
                _parse_producer_csv_rows(
                    round_association_snapshot["data"],
                    expected_fieldnames=AssociationAgent.fieldnames(),
                    label=round_association_path,
                    allow_empty=True,
                ),
                frame_rows=frame_rows,
                label=round_association_path,
            )
        raw_round_sources.append(round_source)
    try:
        memory_contract = validate_history_memory_snapshots(
            raw_history_manifest,
            raw_snapshot_records,
            snapshot_fieldnames_by_path=raw_snapshot_fieldnames,
        )
    except ValueError as exc:
        raise ComparisonEvidenceProjectionError(
            f"history-only manifest is invalid: {exc}"
        ) from exc
    if raw_history_manifest["source_frame_records"] != prepared_frame_relative:
        raise ComparisonEvidenceProjectionError(
            "history-only manifest source_frame_records does not match Prepared frame records"
        )
    engineering_rows = _project_engineering_rows(frame_rows)
    projected_rounds = []
    outputs: dict[str, bytes] = {}
    for round_number, round_source in enumerate(raw_round_sources, start=1):
        raw_round = round_source["raw_round"]
        round_root = f"runs/{run_id}/work/main_progressive/round_{round_number:03d}"
        fixed_query_path = f"{round_root}/query_frames.csv"
        query_rows = round_source["query_rows"]
        expected_query_rows = [
            row
            for row in frame_rows
            if row["inspection_id"] == raw_round["query_inspection"]
        ]
        key = lambda row: (row["inspection_id"], row["current_observation_id"])
        if sorted(query_rows, key=key) != sorted(expected_query_rows, key=key):
            raise ComparisonEvidenceProjectionError(
                f"history-only round {round_number} query frames do not match Prepared frames"
            )
        outputs[fixed_query_path] = _canonical_csv_bytes(
            FRAME_PROJECTION_FIELDS,
            query_rows,
        )
        projected_round = {
            "round_index": round_number,
            "query_inspection": raw_round["query_inspection"],
            "history_inspection_ids": raw_round["history_inspection_ids"],
            "query_frame_count": len(query_rows),
            "query_frames": fixed_query_path,
            "mode": raw_round["mode"],
        }
        if raw_round["mode"] == "history_only":
            fixed_association_path = f"{round_root}/association_records.csv"
            fixed_memory_before = f"{round_root}/memory_before_query.csv"
            fixed_memory_after = f"{round_root}/memory_after_query.csv"
            outputs[fixed_association_path] = _canonical_csv_bytes(
                ASSOCIATION_PROJECTION_FIELDS,
                round_source["association_rows"],
            )
            outputs[fixed_memory_before] = _canonical_csv_bytes(
                MEMORY_SNAPSHOT_RECORD_FIELDS,
                round_source["memory_before_rows"],
            )
            outputs[fixed_memory_after] = _canonical_csv_bytes(
                MEMORY_SNAPSHOT_RECORD_FIELDS,
                round_source["memory_after_rows"],
            )
            projected_round.update(
                {
                    "memory_before": fixed_memory_before,
                    "association_records": fixed_association_path,
                    "memory_after": fixed_memory_after,
                }
            )
        projected_rounds.append(projected_round)
    projected_manifest = {
        "mode": "history_only",
        "source_frame_records": _fixed_work_path(run_id, "frame_records.csv"),
        "inspection_order": memory_contract["inspection_order"],
        "rounds": projected_rounds,
    }
    outputs.update({
        _fixed_work_path(run_id, "frame_records.csv"): _canonical_csv_bytes(
            FRAME_PROJECTION_FIELDS,
            frame_rows,
        ),
        _fixed_work_path(run_id, "engineering_records.csv"): _canonical_csv_bytes(
            ENGINEERING_PROJECTION_FIELDS,
            engineering_rows,
        ),
        _fixed_work_path(run_id, "association_records.csv"): _canonical_csv_bytes(
            ASSOCIATION_PROJECTION_FIELDS,
            association_rows,
        ),
        _fixed_work_path(run_id, "association_manifest.json"): (
            json.dumps(
                projected_manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8"),
    })
    canonical_receipt_sources = sorted(
        receipt_sources,
        key=lambda item: (item["kind"], item["path"]),
    )
    receipt = {
        "schema_version": PROJECTION_RECEIPT_SCHEMA_VERSION,
        "run_id": run_id,
        "execution_profile": execution_profile,
        "validation_scope": _PROJECTION_VALIDATION_SCOPE,
        "source_artifacts": canonical_receipt_sources,
        "projected_artifacts": sorted(
            (
                _projected_reference(relative_path, data)
                for relative_path, data in outputs.items()
            ),
            key=lambda item: (item["kind"], item["path"]),
        ),
    }
    receipt_path = PROJECTION_RECEIPT_PATH_TEMPLATE.format(run_id=run_id)
    receipt_bytes = _canonical_receipt_bytes(receipt)
    parse_projection_receipt(
        receipt_bytes,
        run_id=run_id,
        execution_profile=execution_profile,
    )
    try:
        for relative_path, data in outputs.items():
            write_phase_a1_work_artifact(
                project_root,
                run_id=run_id,
                execution_profile=execution_profile,
                relative_path=relative_path,
                data=data,
            )
        write_phase_a1_work_artifact(
            project_root,
            run_id=run_id,
            execution_profile=execution_profile,
            relative_path=receipt_path,
            data=receipt_bytes,
        )
    except PhaseA1ArtifactError as exc:
        raise ComparisonEvidenceProjectionError(
            f"unable to materialize A1 projection sources: {exc}"
        ) from exc
    return {
        "prepared_manifest_path": prepared_manifest_snapshot["path"],
        "history_association_path": history_association_snapshot["path"],
        "history_manifest_path": history_manifest_snapshot["path"],
        "projection_frame_path": _fixed_work_path(run_id, "frame_records.csv"),
        "projection_association_manifest_path": _fixed_work_path(
            run_id,
            "association_manifest.json",
        ),
        "projection_receipt_path": receipt_path,
    }


def _load_projection_receipt_snapshots(
    project_root: Any,
    *,
    run_id: str,
    execution_profile: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    receipt_path = PROJECTION_RECEIPT_PATH_TEMPLATE.format(run_id=run_id)
    receipt_snapshot = _snapshot(
        project_root,
        run_id=run_id,
        relative_path=receipt_path,
    )
    receipt = parse_projection_receipt(
        receipt_snapshot["data"],
        run_id=run_id,
        execution_profile=execution_profile,
    )
    source_snapshots: dict[str, dict[str, Any]] = {}
    projected_snapshots: dict[str, dict[str, Any]] = {}
    for references, destination in (
        (receipt["source_artifacts"], source_snapshots),
        (receipt["projected_artifacts"], projected_snapshots),
    ):
        for reference in references:
            snapshot = _snapshot(
                project_root,
                run_id=run_id,
                relative_path=reference["path"],
            )
            if (
                snapshot["size_bytes"] != reference["size_bytes"]
                or snapshot["sha256"] != reference["sha256"]
            ):
                raise ComparisonEvidenceProjectionError(
                    f"projection receipt artifact changed: {reference['path']}"
                )
            destination[reference["path"]] = snapshot
    return receipt_snapshot, source_snapshots, projected_snapshots


def project_run_local_comparison_evidence(
    project_root: Any,
    *,
    run_id: str,
    execution_profile: str,
) -> dict[str, Any]:
    """Project fixed Run-local relations into validated static-audit Evidence."""

    if execution_profile != PHASE_A1_EXECUTION_PROFILE:
        raise ComparisonEvidenceProjectionError(
            f"execution_profile must be {PHASE_A1_EXECUTION_PROFILE}"
        )

    (
        receipt_snapshot,
        receipt_source_snapshots,
        receipt_projected_snapshots,
    ) = _load_projection_receipt_snapshots(
        project_root,
        run_id=run_id,
        execution_profile=execution_profile,
    )

    def projected_snapshot(relative_path: str) -> dict[str, Any]:
        snapshot = receipt_projected_snapshots.get(relative_path)
        if snapshot is None:
            raise ComparisonEvidenceProjectionError(
                f"projection receipt is missing projected artifact: {relative_path}"
            )
        return snapshot

    frame_snapshot = projected_snapshot(_fixed_work_path(run_id, "frame_records.csv"))
    engineering_snapshot = projected_snapshot(
        _fixed_work_path(run_id, "engineering_records.csv")
    )
    association_snapshot = projected_snapshot(
        _fixed_work_path(run_id, "association_records.csv")
    )
    manifest_snapshot = projected_snapshot(
        _fixed_work_path(run_id, "association_manifest.json")
    )

    frame_rows = _normalize_frame_rows(
        _parse_csv_rows(
            frame_snapshot["data"],
            expected_fieldnames=FRAME_PROJECTION_FIELDS,
            label="frame_records.csv",
            allow_empty=False,
        )
    )
    engineering_rows = _normalize_engineering_rows(
        _parse_csv_rows(
            engineering_snapshot["data"],
            expected_fieldnames=ENGINEERING_PROJECTION_FIELDS,
            label="engineering_records.csv",
            allow_empty=False,
        )
    )
    association_rows = _normalize_association_rows(
        _parse_csv_rows(
            association_snapshot["data"],
            expected_fieldnames=ASSOCIATION_PROJECTION_FIELDS,
            label="association_records.csv",
            allow_empty=True,
        ),
        label="association_records.csv",
    )
    association_manifest = _parse_json_object(
        manifest_snapshot["data"],
        label="association_manifest.json",
    )

    source_artifacts = [
        _source_reference(role="frame_artifact", snapshot=frame_snapshot),
        _source_reference(role="engineering_artifact", snapshot=engineering_snapshot),
        _source_reference(role="association_artifact", snapshot=association_snapshot),
        _source_reference(role="association_manifest", snapshot=manifest_snapshot),
        _source_reference(role="projection_receipt", snapshot=receipt_snapshot),
        *(
            _source_reference(role="projection_input", snapshot=snapshot)
            for snapshot in receipt_source_snapshots.values()
        ),
    ]
    snapshot_records_by_path: dict[str, list[dict[str, str]]] = {}
    snapshot_fieldnames_by_path: dict[str, Sequence[str]] = {}
    round_association_rows: list[dict[str, Any]] = []

    rounds = association_manifest.get("rounds")
    if not isinstance(rounds, list):
        raise ComparisonEvidenceProjectionError("Association manifest rounds must be a list")
    for round_number, round_entry in enumerate(rounds, start=1):
        if not isinstance(round_entry, Mapping):
            raise ComparisonEvidenceProjectionError(
                f"Association manifest round {round_number} must be an object"
            )
        query_path = round_entry.get("query_frames")
        if isinstance(query_path, str):
            query_snapshot = projected_snapshot(query_path)
            source_artifacts.append(
                _source_reference(role="history_round_context", snapshot=query_snapshot)
            )
            query_rows = _normalize_frame_rows(
                _parse_csv_rows(
                    query_snapshot["data"],
                    expected_fieldnames=FRAME_PROJECTION_FIELDS,
                    label=query_path,
                    allow_empty=False,
                )
            )
            expected_query_rows = [
                row
                for row in frame_rows
                if row["inspection_id"] == round_entry.get("query_inspection")
            ]
            row_key = lambda row: (row["inspection_id"], row["current_observation_id"])
            if sorted(query_rows, key=row_key) != sorted(expected_query_rows, key=row_key):
                raise ComparisonEvidenceProjectionError(
                    f"{query_path} must exactly match its query inspection in frame_records.csv"
                )
        if round_entry.get("mode") != "history_only":
            continue
        for field, role in (
            ("association_records", "association_round_artifact"),
            ("memory_after", "history_round_context"),
        ):
            path_value = round_entry.get(field)
            if not isinstance(path_value, str):
                raise ComparisonEvidenceProjectionError(
                    f"Association manifest round {round_number} {field} must be a path"
                )
            artifact_snapshot = projected_snapshot(path_value)
            source_artifacts.append(
                _source_reference(role=role, snapshot=artifact_snapshot)
            )
            if field == "association_records":
                normalized_round_rows = _normalize_association_rows(
                    _parse_csv_rows(
                        artifact_snapshot["data"],
                        expected_fieldnames=ASSOCIATION_PROJECTION_FIELDS,
                        label=path_value,
                        allow_empty=True,
                    ),
                    label=path_value,
                )
                if any(
                    row["inspection_id"] != round_entry.get("query_inspection")
                    for row in normalized_round_rows
                ):
                    raise ComparisonEvidenceProjectionError(
                        f"{path_value} contains an Association query for another round"
                    )
                round_association_rows.extend(normalized_round_rows)
            else:
                _parse_csv_rows(
                    artifact_snapshot["data"],
                    expected_fieldnames=MEMORY_SNAPSHOT_RECORD_FIELDS,
                    label=path_value,
                    allow_empty=True,
                )

        memory_path = round_entry.get("memory_before")
        if not isinstance(memory_path, str):
            raise ComparisonEvidenceProjectionError(
                f"Association manifest round {round_number} memory_before must be a path"
            )
        memory_snapshot = projected_snapshot(memory_path)
        source_artifacts.append(
            _source_reference(role="history_memory_context", snapshot=memory_snapshot)
        )
        memory_rows = _parse_csv_rows(
            memory_snapshot["data"],
            expected_fieldnames=MEMORY_SNAPSHOT_RECORD_FIELDS,
            label=memory_path,
            allow_empty=True,
        )
        snapshot_records_by_path[memory_path] = memory_rows
        snapshot_fieldnames_by_path[memory_path] = MEMORY_SNAPSHOT_RECORD_FIELDS

    try:
        memory_contract = validate_history_memory_snapshots(
            association_manifest,
            snapshot_records_by_path,
            snapshot_fieldnames_by_path=snapshot_fieldnames_by_path,
        )
    except ValueError as exc:
        raise ComparisonEvidenceProjectionError(
            f"history-only Association manifest is invalid: {exc}"
        ) from exc

    expected_frame_path = _fixed_work_path(run_id, "frame_records.csv")
    if memory_contract["source_frame_records"] != expected_frame_path:
        raise ComparisonEvidenceProjectionError(
            "Association manifest source_frame_records must reference the fixed Run-local frame_records.csv"
        )
    inspection_order = memory_contract["inspection_order"]
    frame_inspections = {row["inspection_id"] for row in frame_rows}
    if frame_inspections != set(inspection_order):
        raise ComparisonEvidenceProjectionError(
            "frame_records.csv inspections must exactly match Association manifest inspection_order"
        )
    inspection_position = {
        inspection_id: position for position, inspection_id in enumerate(inspection_order)
    }
    _validate_inspection_chronology(frame_rows, inspection_order)
    for round_entry in association_manifest["rounds"]:
        inspection_id = round_entry["query_inspection"]
        query_observation_count = sum(
            row["inspection_id"] == inspection_id for row in frame_rows
        )
        if round_entry["query_frame_count"] != query_observation_count:
            raise ComparisonEvidenceProjectionError(
                f"Association manifest query_frame_count does not match {inspection_id}"
            )

    association_key = lambda row: (
        row["inspection_id"],
        row["current_observation_id"],
        row["association_id"],
    )
    if sorted(round_association_rows, key=association_key) != sorted(
        association_rows,
        key=association_key,
    ):
        raise ComparisonEvidenceProjectionError(
            "round Association records must exactly match the main association_records.csv"
        )

    try:
        relations = validate_source_reference_contract(
            frame_rows,
            association_rows,
            engineering_rows,
            baseline_inspection_ids=[inspection_order[0]],
            artifact_schema_version=SOURCE_REFERENCE_SCHEMA_VERSION,
            frame_fieldnames=FRAME_PROJECTION_FIELDS,
            association_fieldnames=ASSOCIATION_PROJECTION_FIELDS,
            engineering_fieldnames=ENGINEERING_PROJECTION_FIELDS,
        )
    except ValueError as exc:
        raise ComparisonEvidenceProjectionError(
            f"Run-local source relations are invalid: {exc}"
        ) from exc

    engineering_index = _engineering_by_observation(
        relations["engineering_records"],
        relations["frame_records"],
    )
    association_index = {
        (row["inspection_id"], row["current_observation_id"]): row
        for row in relations["association_records"]
    }
    records = [
        _build_static_evidence(
            frame=frame,
            engineering=engineering_index[
                (frame["inspection_id"], frame["current_observation_id"])
            ],
            association=association_index.get(
                (frame["inspection_id"], frame["current_observation_id"])
            ),
            association_hash=association_snapshot["sha256"],
            manifest_hash=manifest_snapshot["sha256"],
            engineering_hash=engineering_snapshot["sha256"],
        )
        for frame in sorted(
            relations["frame_records"],
            key=lambda row: (
                inspection_position[row["inspection_id"]],
                row["current_observation_id"],
            ),
        )
    ]
    try:
        validated = validate_comparison_evidence_records(records)
    except ValueError as exc:
        raise ComparisonEvidenceProjectionError(
            f"projected Comparison Evidence is invalid: {exc}"
        ) from exc
    return {
        "records": validated,
        "source_artifacts": source_artifacts,
    }


__all__ = [
    "ASSOCIATION_PROJECTION_FIELDS",
    "ENGINEERING_PROJECTION_FIELDS",
    "FRAME_PROJECTION_FIELDS",
    "ComparisonEvidenceProjectionError",
]
