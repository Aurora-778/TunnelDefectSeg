"""Phase 0 contract for history-only Association memory snapshots."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import PurePosixPath
import re
from typing import Any

from orchestrator.claim_policy import load_claim_policy


MEMORY_SNAPSHOT_CONTRACT_VERSION = "history_memory_snapshot_v1"
MEMORY_SNAPSHOT_RECORD_FIELDS = (
    "memory_id",
    "memory_version",
    "disease_id",
    "disease_type",
    "source_record_count",
    "source_inspection_ids",
    "memory_update_mode",
    "memory_confidence",
    "memory_limit_note",
    "first_seen_inspection",
    "last_seen_inspection",
    "inspection_count",
    "total_seen_frames",
    "first_area_px",
    "last_area_px",
    "max_area_px",
    "area_growth_px",
    "area_growth_rate",
    "first_risk_level",
    "last_risk_level",
    "risk_level_change",
    "growth_trend",
    "attention_level",
    "comparability_status",
    "main_clock_direction",
    "mileage_range",
    "representative_image_path",
    "representative_mask_path",
    "requires_manual_review",
    "memory_description",
)

_MANIFEST_FIELDS = (
    "mode",
    "source_frame_records",
    "inspection_order",
    "rounds",
)
_BASELINE_ROUND_FIELDS = (
    "round_index",
    "query_inspection",
    "history_inspection_ids",
    "query_frame_count",
    "query_frames",
    "mode",
)
_HISTORY_ROUND_FIELDS = (
    *_BASELINE_ROUND_FIELDS,
    "memory_before",
    "association_records",
    "memory_after",
)
_CANONICAL_INTEGER_RE = re.compile(r"0|[1-9][0-9]*\Z")
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_MAX_MASK_AREA_PX = (1 << 63) - 1


class MemorySnapshotContractError(ValueError):
    """Raised when a history-only candidate Memory snapshot is not credible."""


def _require_exact_fields(
    value: Mapping[str, Any],
    expected_fields: Sequence[str],
    *,
    label: str,
) -> None:
    if any(not isinstance(field, str) for field in value):
        raise MemorySnapshotContractError(f"{label} field names must be strings")
    expected = set(expected_fields)
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        raise MemorySnapshotContractError(
            f"{label} missing fields: {', '.join(missing)}"
        )
    if unknown:
        raise MemorySnapshotContractError(
            f"{label} has unknown fields: {', '.join(unknown)}"
        )


def _require_string(value: Any, *, field: str, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise MemorySnapshotContractError(
            f"{label} {field} must be a canonical non-empty string"
        )
    return value


def _require_identifier(value: Any, *, field: str, label: str) -> str:
    identifier = _require_string(value, field=field, label=label)
    if _IDENTIFIER_RE.fullmatch(identifier) is None:
        raise MemorySnapshotContractError(
            f"{label} {field} must use the portable identifier character set"
        )
    return identifier


def _require_portable_path(value: Any, *, field: str, label: str) -> str:
    path_text = _require_string(value, field=field, label=label)
    if "\\" in path_text or ":" in path_text:
        raise MemorySnapshotContractError(
            f"{label} {field} must be a project-relative POSIX path"
        )
    path = PurePosixPath(path_text)
    if (
        path.is_absolute()
        or str(path) != path_text
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise MemorySnapshotContractError(
            f"{label} {field} must be a canonical project-relative POSIX path"
        )
    return path_text


def _require_nonnegative_integer(
    value: Any,
    *,
    field: str,
    label: str,
    allow_zero: bool = True,
) -> int:
    if not isinstance(value, str) or _CANONICAL_INTEGER_RE.fullmatch(value) is None:
        raise MemorySnapshotContractError(
            f"{label} {field} must be a canonical non-negative integer string"
        )
    if len(value) > 19:
        raise MemorySnapshotContractError(
            f"{label} {field} must not exceed {_MAX_MASK_AREA_PX}"
        )
    number = int(value)
    if number > _MAX_MASK_AREA_PX:
        raise MemorySnapshotContractError(
            f"{label} {field} must not exceed {_MAX_MASK_AREA_PX}"
        )
    if not allow_zero and number == 0:
        raise MemorySnapshotContractError(f"{label} {field} must be greater than zero")
    return number


def _require_identifier_list(value: Any, *, field: str, label: str) -> list[str]:
    if not isinstance(value, list):
        raise MemorySnapshotContractError(f"{label} {field} must be a list")
    result = [
        _require_identifier(item, field=field, label=label)
        for item in value
    ]
    if len(result) != len(set(result)):
        raise MemorySnapshotContractError(f"{label} {field} must be unique")
    return result


def _require_records(value: Any, *, label: str) -> list[Mapping[str, Any]]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        raise MemorySnapshotContractError(f"{label} must be an iterable of objects")
    records: list[Mapping[str, Any]] = []
    for row_number, record in enumerate(value, start=1):
        if not isinstance(record, Mapping):
            raise MemorySnapshotContractError(
                f"{label} row {row_number} must be an object"
            )
        records.append(record)
    return records


def _validate_fieldnames(value: Any, *, path: str) -> None:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise MemorySnapshotContractError(
            f"snapshot fieldnames for {path} must be a sequence of strings"
        )
    names = list(value)
    if any(not isinstance(name, str) or not name for name in names):
        raise MemorySnapshotContractError(
            f"snapshot fieldnames for {path} must contain non-empty strings"
        )
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise MemorySnapshotContractError(
            f"snapshot fieldnames for {path} contain duplicates: {', '.join(duplicates)}"
        )
    if tuple(names) != MEMORY_SNAPSHOT_RECORD_FIELDS:
        raise MemorySnapshotContractError(
            f"snapshot fieldnames for {path} must exactly match "
            f"{MEMORY_SNAPSHOT_CONTRACT_VERSION}"
        )


def _validate_memory_rows(
    records: Any,
    *,
    path: str,
    history_inspection_ids: list[str],
    inspection_positions: Mapping[str, int],
    comparability_statuses: set[str],
) -> dict[str, dict[str, Any]]:
    rows = _require_records(records, label=f"memory snapshot {path}")
    memory_by_id: dict[str, dict[str, Any]] = {}
    history_set = set(history_inspection_ids)

    for row_number, row in enumerate(rows, start=1):
        label = f"memory snapshot {path} row {row_number}"
        _require_exact_fields(row, MEMORY_SNAPSHOT_RECORD_FIELDS, label=label)
        if any(not isinstance(row[field], str) for field in MEMORY_SNAPSHOT_RECORD_FIELDS):
            raise MemorySnapshotContractError(
                f"{label} fields must contain CSV string values"
            )
        memory_id = _require_identifier(
            row["memory_id"],
            field="memory_id",
            label=label,
        )
        if not memory_id.startswith("MEM-") or memory_id == "MEM-":
            raise MemorySnapshotContractError(
                f"{label} memory_id must use the MEM- prefix with a non-empty suffix"
            )
        if memory_id in memory_by_id:
            raise MemorySnapshotContractError(
                f"memory snapshot {path} has duplicate memory_id: {memory_id}"
            )
        if row["memory_version"] != "v1":
            raise MemorySnapshotContractError(f"{label} memory_version must be v1")
        if row["memory_update_mode"] != "batch_rebuild":
            raise MemorySnapshotContractError(
                f"{label} memory_update_mode must be batch_rebuild for history-only input"
            )

        source_ids_text = _require_string(
            row["source_inspection_ids"],
            field="source_inspection_ids",
            label=label,
        )
        source_ids = source_ids_text.split("|")
        source_ids = [
            _require_identifier(
                item,
                field="source_inspection_ids",
                label=label,
            )
            for item in source_ids
        ]
        if len(source_ids) != len(set(source_ids)):
            raise MemorySnapshotContractError(
                f"{label} source_inspection_ids must be unique"
            )
        unknown_sources = [item for item in source_ids if item not in history_set]
        if unknown_sources:
            raise MemorySnapshotContractError(
                f"{label} source_inspection_ids contains current, future, or unknown "
                f"inspection: {unknown_sources[0]}"
            )
        expected_order = sorted(source_ids, key=inspection_positions.__getitem__)
        if source_ids != expected_order:
            raise MemorySnapshotContractError(
                f"{label} source_inspection_ids must follow manifest inspection order"
            )

        source_record_count = _require_nonnegative_integer(
            row["source_record_count"],
            field="source_record_count",
            label=label,
            allow_zero=False,
        )
        inspection_count = _require_nonnegative_integer(
            row["inspection_count"],
            field="inspection_count",
            label=label,
            allow_zero=False,
        )
        if inspection_count != len(source_ids):
            raise MemorySnapshotContractError(
                f"{label} inspection_count must equal source_inspection_ids count"
            )
        if source_record_count < inspection_count:
            raise MemorySnapshotContractError(
                f"{label} source_record_count must cover every source inspection"
            )
        total_seen_frames = _require_nonnegative_integer(
            row["total_seen_frames"],
            field="total_seen_frames",
            label=label,
            allow_zero=False,
        )
        if total_seen_frames < source_record_count:
            raise MemorySnapshotContractError(
                f"{label} total_seen_frames must be at least source_record_count"
            )

        first_seen = _require_identifier(
            row["first_seen_inspection"],
            field="first_seen_inspection",
            label=label,
        )
        last_seen = _require_identifier(
            row["last_seen_inspection"],
            field="last_seen_inspection",
            label=label,
        )
        if first_seen != source_ids[0] or last_seen != source_ids[-1]:
            raise MemorySnapshotContractError(
                f"{label} first/last seen inspections must match source_inspection_ids"
            )

        last_area_px = _require_nonnegative_integer(
            row["last_area_px"],
            field="last_area_px",
            label=label,
        )

        comparability_status = _require_string(
            row["comparability_status"],
            field="comparability_status",
            label=label,
        )
        if comparability_status not in comparability_statuses:
            raise MemorySnapshotContractError(
                f"{label} comparability_status is not allowed by Claim Policy"
            )
        growth_trend = _require_string(
            row["growth_trend"],
            field="growth_trend",
            label=label,
        )
        if len(source_ids) == 1 and comparability_status != "insufficient_history":
            raise MemorySnapshotContractError(
                f"{label} one source inspection requires insufficient_history"
            )
        if comparability_status == "verified_comparable":
            raise MemorySnapshotContractError(
                f"{label} verified_comparable requires an A1 source-proof schema upgrade"
            )
        if (
            comparability_status == "insufficient_history"
            and growth_trend != "数据不足"
        ):
            raise MemorySnapshotContractError(
                f"{label} insufficient_history requires growth_trend=数据不足"
            )
        if (
            comparability_status
            in {"not_longitudinally_comparable", "simulated_metadata_comparable"}
            and growth_trend != "不可比较"
        ):
            raise MemorySnapshotContractError(
                f"{label} non-verified comparison requires growth_trend=不可比较"
            )
        memory_by_id[memory_id] = {
            "memory_id": memory_id,
            "memory_version": row["memory_version"],
            "last_seen_inspection": last_seen,
            "source_inspection_ids": list(source_ids),
            "source_record_count": source_record_count,
            "last_area_px": last_area_px,
            "comparability_status": comparability_status,
        }

    return memory_by_id


def validate_history_memory_snapshots(
    association_manifest: Mapping[str, Any],
    snapshot_records_by_path: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    snapshot_fieldnames_by_path: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    """Validate in-memory history-only rounds and candidate Memory snapshots."""

    if not isinstance(association_manifest, Mapping):
        raise MemorySnapshotContractError("Association manifest must be an object")
    if not isinstance(snapshot_records_by_path, Mapping):
        raise MemorySnapshotContractError("snapshot_records_by_path must be an object")
    if not isinstance(snapshot_fieldnames_by_path, Mapping):
        raise MemorySnapshotContractError(
            "snapshot_fieldnames_by_path must be an object"
        )
    _require_exact_fields(
        association_manifest,
        _MANIFEST_FIELDS,
        label="Association manifest",
    )
    if association_manifest["mode"] != "history_only":
        raise MemorySnapshotContractError(
            "Association manifest mode must be history_only"
        )
    source_frame_records = _require_portable_path(
        association_manifest["source_frame_records"],
        field="source_frame_records",
        label="Association manifest",
    )
    inspection_order = _require_identifier_list(
        association_manifest["inspection_order"],
        field="inspection_order",
        label="Association manifest",
    )
    if not inspection_order:
        raise MemorySnapshotContractError(
            "Association manifest inspection_order must not be empty"
        )
    rounds = association_manifest["rounds"]
    if not isinstance(rounds, list):
        raise MemorySnapshotContractError("Association manifest rounds must be a list")
    if len(rounds) != len(inspection_order):
        raise MemorySnapshotContractError(
            "Association manifest must contain exactly one round per inspection"
        )

    inspection_positions = {
        inspection_id: position
        for position, inspection_id in enumerate(inspection_order)
    }
    expected_snapshot_paths: list[str] = []
    normalized_rounds: list[dict[str, Any]] = []
    seen_memory_paths: set[str] = set()

    for position, round_entry in enumerate(rounds):
        round_number = position + 1
        label = f"Association manifest round {round_number}"
        if not isinstance(round_entry, Mapping):
            raise MemorySnapshotContractError(f"{label} must be an object")
        expected_mode = "baseline_only" if position == 0 else "history_only"
        expected_fields = (
            _BASELINE_ROUND_FIELDS
            if expected_mode == "baseline_only"
            else _HISTORY_ROUND_FIELDS
        )
        _require_exact_fields(round_entry, expected_fields, label=label)
        if (
            not isinstance(round_entry["round_index"], int)
            or isinstance(round_entry["round_index"], bool)
            or round_entry["round_index"] != round_number
        ):
            raise MemorySnapshotContractError(
                f"{label} round_index must equal {round_number}"
            )
        query_inspection = _require_identifier(
            round_entry["query_inspection"],
            field="query_inspection",
            label=label,
        )
        if query_inspection != inspection_order[position]:
            raise MemorySnapshotContractError(
                f"{label} query_inspection must follow inspection_order"
            )
        history_ids = _require_identifier_list(
            round_entry["history_inspection_ids"],
            field="history_inspection_ids",
            label=label,
        )
        expected_history = inspection_order[:position]
        if history_ids != expected_history:
            raise MemorySnapshotContractError(
                f"{label} history_inspection_ids must equal the prior inspection prefix"
            )
        if round_entry["mode"] != expected_mode:
            raise MemorySnapshotContractError(
                f"{label} mode must be {expected_mode}"
            )
        if (
            not isinstance(round_entry["query_frame_count"], int)
            or isinstance(round_entry["query_frame_count"], bool)
            or round_entry["query_frame_count"] <= 0
        ):
            raise MemorySnapshotContractError(
                f"{label} query_frame_count must be a positive integer"
            )
        query_frames = _require_portable_path(
            round_entry["query_frames"],
            field="query_frames",
            label=label,
        )
        if PurePosixPath(query_frames).name != "query_frames.csv":
            raise MemorySnapshotContractError(
                f"{label} query_frames must end with query_frames.csv"
            )
        round_parent = PurePosixPath(query_frames).parent
        expected_round_directory = f"round_{round_number:03d}"
        if round_parent.name != expected_round_directory:
            raise MemorySnapshotContractError(
                f"{label} artifacts must use the {expected_round_directory} directory"
            )

        memory_path: str | None = None
        if expected_mode == "history_only":
            memory_path = _require_portable_path(
                round_entry["memory_before"],
                field="memory_before",
                label=label,
            )
            association_path = _require_portable_path(
                round_entry["association_records"],
                field="association_records",
                label=label,
            )
            memory_after_path = _require_portable_path(
                round_entry["memory_after"],
                field="memory_after",
                label=label,
            )
            if PurePosixPath(memory_path).name != "memory_before_query.csv":
                raise MemorySnapshotContractError(
                    f"{label} memory_before must end with memory_before_query.csv"
                )
            if PurePosixPath(association_path).name != "association_records.csv":
                raise MemorySnapshotContractError(
                    f"{label} association_records must end with association_records.csv"
                )
            if PurePosixPath(memory_after_path).name != "memory_after_query.csv":
                raise MemorySnapshotContractError(
                    f"{label} memory_after must end with memory_after_query.csv"
                )
            if any(
                PurePosixPath(path).parent != round_parent
                for path in (memory_path, association_path, memory_after_path)
            ):
                raise MemorySnapshotContractError(
                    f"{label} artifacts must share the query_frames round directory"
                )
            if memory_path in seen_memory_paths:
                raise MemorySnapshotContractError(
                    f"{label} reuses memory_before path: {memory_path}"
                )
            seen_memory_paths.add(memory_path)
            expected_snapshot_paths.append(memory_path)

        normalized_rounds.append(
            {
                "round_index": round_number,
                "query_inspection": query_inspection,
                "history_inspection_ids": list(history_ids),
                "mode": expected_mode,
                "memory_before_path": memory_path,
                "memory_by_id": {},
            }
        )

    snapshot_paths = {
        _require_portable_path(
            path,
            field="snapshot path",
            label="snapshot_records_by_path",
        )
        for path in snapshot_records_by_path
    }
    fieldname_paths = {
        _require_portable_path(
            path,
            field="snapshot path",
            label="snapshot_fieldnames_by_path",
        )
        for path in snapshot_fieldnames_by_path
    }
    expected_paths = set(expected_snapshot_paths)
    if snapshot_paths != expected_paths:
        missing = sorted(expected_paths - snapshot_paths)
        extra = sorted(snapshot_paths - expected_paths)
        detail = []
        if missing:
            detail.append("missing: " + ", ".join(missing))
        if extra:
            detail.append("unexpected: " + ", ".join(extra))
        raise MemorySnapshotContractError(
            "snapshot_records_by_path does not match manifest memory_before paths"
            + (f" ({'; '.join(detail)})" if detail else "")
        )
    if fieldname_paths != expected_paths:
        raise MemorySnapshotContractError(
            "snapshot_fieldnames_by_path must exactly match manifest memory_before paths"
        )

    comparability_statuses = set(load_claim_policy()["comparability_status_enum"])
    for round_record in normalized_rounds:
        memory_path = round_record["memory_before_path"]
        if memory_path is None:
            continue
        _validate_fieldnames(
            snapshot_fieldnames_by_path[memory_path],
            path=memory_path,
        )
        round_record["memory_by_id"] = _validate_memory_rows(
            snapshot_records_by_path[memory_path],
            path=memory_path,
            history_inspection_ids=round_record["history_inspection_ids"],
            inspection_positions=inspection_positions,
            comparability_statuses=comparability_statuses,
        )

    return {
        "schema_version": MEMORY_SNAPSHOT_CONTRACT_VERSION,
        "source_frame_records": source_frame_records,
        "inspection_order": list(inspection_order),
        "rounds": normalized_rounds,
    }


__all__ = [
    "MEMORY_SNAPSHOT_CONTRACT_VERSION",
    "MEMORY_SNAPSHOT_RECORD_FIELDS",
    "MemorySnapshotContractError",
    "validate_history_memory_snapshots",
]
