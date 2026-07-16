"""Prepare one real-inspection sequence for the existing history-only pipeline.

This adapter deliberately stops at answer-free frame records. It does not read
cross-inspection ground truth and does not evaluate Association accuracy.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
from io import BytesIO, StringIO
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
from typing import Any, Iterable, Mapping
from uuid import uuid4

import numpy as np
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.schema import REQUIRED_SCHEMAS, validate_csv_schema


DATA_CONTRACT_VERSION = "real_inspection_pilot_v1"
OBSERVATION_SOURCE = "real_inspection_mask_input"
COMPARABILITY_STATUS = "not_longitudinally_comparable"
SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
FORMULA_PREFIXES = ("=", "+", "-", "@")
VALID_CLOCK_DIRECTIONS = {f"{hour}点" for hour in range(1, 13)}
FORBIDDEN_METADATA_COLUMNS = {
    "global_disease_id",
    "label_disease_id",
    "matched_disease_id",
    "expected_disease_id",
    "ground_truth",
    "gt",
    "split",
    "review_status",
    "audit_status",
    "adjudication_status",
    "gt_complete",
    "audit_complete",
}

REQUIRED_METADATA_COLUMNS = [
    "sequence_id",
    "source_inspection_id",
    "frame_id",
    "timestamp",
    "mileage_m",
    "ring_id",
    "clock_direction",
    "image_file",
    "mask_file",
    "local_observation_id",
    "disease_type",
]

OBSERVATION_FIELDNAMES = [
    "sequence_id",
    "source_inspection_id",
    "association_inspection_id",
    "frame_id",
    "local_observation_id",
    "image_path",
    "mask_path",
    "timestamp",
    "mileage_m",
    "mileage_text",
    "ring_id",
    "clock_direction",
    "disease_type",
    "area_px",
    "bbox_x1",
    "bbox_y1",
    "bbox_x2",
    "bbox_y2",
    "center_x",
    "center_y",
    "bbox_width",
    "bbox_height",
    "mask_canvas_width",
    "mask_canvas_height",
    "association_eligible",
    "exclusion_reason",
    "observation_source",
    "comparability_status",
    "data_contract_version",
]

FRAME_EXTRA_FIELDNAMES = [
    "sequence_id",
    "source_inspection_id",
    "association_inspection_id",
    "local_observation_id",
    "data_contract_version",
    "kict_mask_width",
    "kict_mask_height",
    "mask_canvas_width",
    "mask_canvas_height",
]
FRAME_FIELDNAMES = list(dict.fromkeys(REQUIRED_SCHEMAS["robot_kict_frame_records"] + FRAME_EXTRA_FIELDNAMES))

ARTIFACT_NAMES = (
    "observation_records.csv",
    "frame_records.csv",
    "preparation_manifest.json",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare one real-inspection sequence for history-only Association.")
    parser.add_argument("--dataset-root", "--dataset_root", required=True, type=Path)
    parser.add_argument("--output-dir", "--output_dir", required=True, type=Path)
    parser.add_argument("--validate-only", "--validate_only", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _has_formula_prefix(value: str) -> bool:
    return value.lstrip().startswith(FORMULA_PREFIXES)


def _is_forbidden_answer_column(column: str) -> bool:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", column.strip())
    expanded = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", expanded)
    normalized = expanded.lower()
    tokens = re.findall(r"[a-z0-9]+", normalized)
    compact = "".join(tokens)
    forbidden_compact = {
        "".join(re.findall(r"[a-z0-9]+", value.lower()))
        for value in FORBIDDEN_METADATA_COLUMNS
    }
    return (
        normalized in FORBIDDEN_METADATA_COLUMNS
        or compact in forbidden_compact
        or normalized.startswith("gt_")
        or normalized.endswith("_gt")
        or "ground_truth" in normalized
        or "groundtruth" in normalized
        or "gt" in tokens
        or any(marker in compact for marker in ("gtid", "gtlabel"))
        or "split" in tokens
        or "expected" in tokens
        or any(token.startswith(("review", "audit", "adjudicat")) for token in tokens)
    )


def _contains_absolute_path_text(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return bool(
        re.search(r"(?:^|[\s\"'\(\[])[A-Za-z]:/", normalized)
        or re.search(r"(?:^|[\s\"'\(\[])/(?!/)(?:[^/\s]+/)*[^/\s]+", normalized)
        or re.search(r"(?:^|[\s\"'\(\[])//[^/\s]+/", normalized)
    )


def _validate_identifier(value: str, field: str, line_number: int) -> str:
    if (
        not SAFE_ID_PATTERN.fullmatch(value)
        or value in {".", ".."}
        or "::" in value
        or _has_formula_prefix(value)
    ):
        raise ValueError(
            f"metadata.csv line {line_number}: {field} must match [A-Za-z0-9._-]+ and cannot contain '::'"
        )
    return value


def _parse_non_negative_int(value: str, field: str, line_number: int) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"metadata.csv line {line_number}: {field} must be a non-negative integer") from exc
    if parsed < 0:
        raise ValueError(f"metadata.csv line {line_number}: {field} must be a non-negative integer")
    return parsed


def _parse_non_negative_decimal(value: str, field: str, line_number: int) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"metadata.csv line {line_number}: {field} must be a finite non-negative number") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"metadata.csv line {line_number}: {field} must be a finite non-negative number")
    return parsed


def _parse_timestamp(value: str, line_number: int) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(
            f"metadata.csv line {line_number}: timestamp must be timezone-aware ISO-8601"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"metadata.csv line {line_number}: timestamp must include Z or a UTC offset")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _format_decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_mileage_text(value: Decimal) -> str:
    try:
        rounded = value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("mileage_m exceeds supported decimal precision") from exc
    kilometre = int(rounded // Decimal("1000"))
    metre = rounded - Decimal(kilometre * 1000)
    return f"K{kilometre}+{metre:05.1f}"


def _relative_input_path(
    dataset_root: Path,
    raw_value: str,
    expected_dir: Path,
    field: str,
    line_number: int,
) -> tuple[str, Path]:
    normalized = raw_value.replace("\\", "/")
    pure_path = PurePosixPath(normalized)
    if (
        pure_path.is_absolute()
        or re.match(r"^[A-Za-z]:[/\\]", raw_value)
        or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", normalized)
        or any(":" in part for part in pure_path.parts)
        or ".." in pure_path.parts
    ):
        raise ValueError(f"metadata.csv line {line_number}: {field} must be relative to dataset_root")
    if not pure_path.parts or pure_path.parts[0] != expected_dir.name:
        raise ValueError(f"metadata.csv line {line_number}: {field} must stay inside {expected_dir.name}/")
    candidate = (dataset_root / Path(*pure_path.parts)).resolve(strict=False)
    if not _is_within(candidate, dataset_root) or not _is_within(candidate, expected_dir):
        raise ValueError(f"metadata.csv line {line_number}: {field} must stay inside {expected_dir.name}/")
    if not candidate.is_file():
        raise FileNotFoundError(f"metadata.csv line {line_number}: {field} not found: {normalized}")
    if candidate.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(f"metadata.csv line {line_number}: {field} has unsupported suffix: {candidate.suffix}")
    return candidate.relative_to(dataset_root).as_posix(), candidate


def _fingerprint_bytes(data: bytes, relative_path: str, role: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": relative_path,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    if role:
        result["role"] = role
    return result


def _read_source_bytes(path: Path, relative_path: str, role: str | None = None) -> tuple[bytes, dict[str, Any]]:
    data = path.read_bytes()
    return data, _fingerprint_bytes(data, relative_path, role)


def load_metadata(metadata_path: Path) -> tuple[list[tuple[int, dict[str, str]]], dict[str, Any]]:
    if not metadata_path.is_file():
        raise FileNotFoundError(f"metadata.csv not found: {metadata_path}")
    try:
        data, fingerprint = _read_source_bytes(metadata_path, "metadata.csv")
        text = data.decode("utf-8-sig")
        reader = csv.DictReader(StringIO(text, newline=""))
        fieldnames = reader.fieldnames or []
        duplicate_fields = sorted({field for field in fieldnames if fieldnames.count(field) > 1})
        if duplicate_fields:
            raise ValueError("metadata.csv contains duplicate columns: " + ", ".join(duplicate_fields))
        missing = [column for column in REQUIRED_METADATA_COLUMNS if column not in fieldnames]
        if missing:
            raise ValueError(f"metadata.csv missing required columns: {', '.join(missing)}")
        forbidden = sorted(column for column in fieldnames if _is_forbidden_answer_column(column))
        if forbidden:
            raise ValueError(
                "metadata.csv contains forbidden answer/evaluation columns: " + ", ".join(forbidden)
            )
        rows = [(line_number, dict(row)) for line_number, row in enumerate(reader, start=2)]
    except UnicodeDecodeError as exc:
        raise ValueError(f"metadata.csv must be UTF-8 encoded: {metadata_path}") from exc
    if not rows:
        raise ValueError(f"metadata.csv is empty: {metadata_path}")
    for line_number, row in rows:
        if None in row:
            raise ValueError(f"metadata.csv line {line_number}: unexpected extra unnamed cells")
        for column in REQUIRED_METADATA_COLUMNS:
            raw_value = row.get(column)
            if raw_value is None or not str(raw_value).strip():
                raise ValueError(f"metadata.csv line {line_number}: missing {column}")
    return rows, fingerprint


def _extract_mask_geometry_bytes(data: bytes) -> dict[str, int | str]:
    """Use the existing half-open KICT bbox convention."""

    with Image.open(BytesIO(data)) as image:
        array = np.asarray(image.convert("L"))
    height, width = array.shape[:2]
    foreground = array > 0
    area = int(foreground.sum())
    if area == 0:
        return {
            "area_px": 0,
            "bbox_x1": -1,
            "bbox_y1": -1,
            "bbox_x2": -1,
            "bbox_y2": -1,
            "center_x": "-1",
            "center_y": "-1",
            "bbox_width": 0,
            "bbox_height": 0,
            "mask_canvas_width": width,
            "mask_canvas_height": height,
        }

    ys, xs = np.nonzero(foreground)
    x1, y1 = int(xs.min()), int(ys.min())
    x2, y2 = int(xs.max()) + 1, int(ys.max()) + 1
    return {
        "area_px": area,
        "bbox_x1": x1,
        "bbox_y1": y1,
        "bbox_x2": x2,
        "bbox_y2": y2,
        "center_x": f"{(x1 + x2 - 1) / 2:.2f}",
        "center_y": f"{(y1 + y2 - 1) / 2:.2f}",
        "bbox_width": x2 - x1,
        "bbox_height": y2 - y1,
        "mask_canvas_width": width,
        "mask_canvas_height": height,
    }


def extract_mask_geometry(mask_path: Path) -> dict[str, int | str]:
    return _extract_mask_geometry_bytes(mask_path.read_bytes())


def _image_size_bytes(data: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(data)) as image:
        return image.size


def validate_and_normalize_rows(
    raw_rows: list[tuple[int, dict[str, str]]], dataset_root: Path
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[dict[str, Any]]]:
    image_dir = dataset_root / "images"
    mask_dir = dataset_root / "masks"
    if not image_dir.is_dir():
        raise FileNotFoundError(f"images directory not found: {image_dir}")
    if not mask_dir.is_dir():
        raise FileNotFoundError(f"masks directory not found: {mask_dir}")

    normalized_rows: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, int, str]] = set()
    frame_metadata: dict[tuple[str, str, int], tuple[Any, ...]] = {}
    observation_types: dict[tuple[str, str, str], str] = {}
    geometry_cache: dict[Path, dict[str, int | str]] = {}
    image_size_cache: dict[Path, tuple[int, int]] = {}
    input_artifacts: dict[str, dict[str, Any]] = {}

    for line_number, raw in raw_rows:
        values = {key: str(raw[key]).strip() for key in REQUIRED_METADATA_COLUMNS}
        sequence_id = _validate_identifier(values["sequence_id"], "sequence_id", line_number)
        source_inspection_id = _validate_identifier(
            values["source_inspection_id"], "source_inspection_id", line_number
        )
        local_observation_id = _validate_identifier(
            values["local_observation_id"], "local_observation_id", line_number
        )
        frame_id = _parse_non_negative_int(values["frame_id"], "frame_id", line_number)
        ring_id = _parse_non_negative_int(values["ring_id"], "ring_id", line_number)
        timestamp = _parse_timestamp(values["timestamp"], line_number)
        mileage = _parse_non_negative_decimal(values["mileage_m"], "mileage_m", line_number)
        clock_direction = values["clock_direction"]
        if clock_direction not in VALID_CLOCK_DIRECTIONS:
            raise ValueError(f"metadata.csv line {line_number}: clock_direction must be 1点 through 12点")
        disease_type = values["disease_type"]
        if _has_formula_prefix(disease_type):
            raise ValueError(f"metadata.csv line {line_number}: disease_type cannot start with =, +, -, or @")
        if _contains_absolute_path_text(disease_type):
            raise ValueError(f"metadata.csv line {line_number}: disease_type must not contain an absolute path")

        image_rel, image_path = _relative_input_path(
            dataset_root, values["image_file"], image_dir, "image_file", line_number
        )
        mask_rel, mask_path = _relative_input_path(
            dataset_root, values["mask_file"], mask_dir, "mask_file", line_number
        )
        if image_path not in image_size_cache:
            try:
                image_bytes, image_fingerprint = _read_source_bytes(image_path, image_rel, "image")
                image_size_cache[image_path] = _image_size_bytes(image_bytes)
            except (OSError, ValueError) as exc:
                raise ValueError(
                    f"metadata.csv line {line_number}: image_file cannot be read as an image: {image_rel}"
                ) from exc
            input_artifacts[image_rel] = {**image_fingerprint, "resolved_path": image_path}
        if mask_path not in geometry_cache:
            try:
                mask_bytes, mask_fingerprint = _read_source_bytes(mask_path, mask_rel, "mask")
                geometry_cache[mask_path] = _extract_mask_geometry_bytes(mask_bytes)
            except (OSError, ValueError) as exc:
                raise ValueError(
                    f"metadata.csv line {line_number}: mask_file cannot be read as an image: {mask_rel}"
                ) from exc
            input_artifacts[mask_rel] = {**mask_fingerprint, "resolved_path": mask_path}
        image_size = image_size_cache[image_path]
        geometry = geometry_cache[mask_path]
        mask_size = (int(geometry["mask_canvas_width"]), int(geometry["mask_canvas_height"]))
        if image_size != mask_size:
            raise ValueError(
                f"metadata.csv line {line_number}: image/mask size mismatch for frame_id {frame_id}: "
                f"image={image_size}, mask={mask_size}"
            )

        composite_key = (sequence_id, source_inspection_id, frame_id, local_observation_id)
        if composite_key in seen_keys:
            raise ValueError(f"metadata.csv line {line_number}: duplicate observation composite key: {composite_key}")
        seen_keys.add(composite_key)

        frame_key = (sequence_id, source_inspection_id, frame_id)
        frame_values = (
            timestamp,
            mileage,
            ring_id,
            clock_direction,
            image_rel,
        )
        previous_frame = frame_metadata.setdefault(frame_key, frame_values)
        if previous_frame != frame_values:
            raise ValueError(f"metadata.csv line {line_number}: inconsistent frame metadata for {frame_key}")

        observation_key = (sequence_id, source_inspection_id, local_observation_id)
        previous_type = observation_types.setdefault(observation_key, disease_type)
        if previous_type != disease_type:
            raise ValueError(
                f"metadata.csv line {line_number}: inconsistent disease_type for local observation {observation_key}"
            )

        normalized_rows.append(
            {
                "line_number": line_number,
                "sequence_id": sequence_id,
                "source_inspection_id": source_inspection_id,
                "frame_id": frame_id,
                "timestamp_dt": timestamp,
                "timestamp": _format_utc(timestamp),
                "mileage_decimal": mileage,
                "mileage_m": _format_decimal(mileage),
                "mileage_text": format_mileage_text(mileage),
                "ring_id": ring_id,
                "clock_direction": clock_direction,
                "image_path": image_rel,
                "mask_path": mask_rel,
                "local_observation_id": local_observation_id,
                "disease_type": disease_type,
                "geometry": geometry,
            }
        )

    sequences = sorted({row["sequence_id"] for row in normalized_rows})
    if len(sequences) != 1:
        raise ValueError(f"metadata.csv must contain exactly one sequence_id; found: {sequences}")

    intervals: list[tuple[datetime, datetime, str]] = []
    for inspection_id in sorted({row["source_inspection_id"] for row in normalized_rows}):
        times = [row["timestamp_dt"] for row in normalized_rows if row["source_inspection_id"] == inspection_id]
        intervals.append((min(times), max(times), inspection_id))
    intervals.sort(key=lambda item: (item[0], item[1], item[2]))
    for previous, current in zip(intervals, intervals[1:]):
        if current[0] <= previous[1]:
            raise ValueError(
                "source inspection time intervals must be strictly ordered and non-overlapping: "
                f"{previous[2]} ends {_format_utc(previous[1])}, {current[2]} starts {_format_utc(current[0])}"
            )

    inspection_mapping: list[dict[str, str]] = []
    association_by_source: dict[str, str] = {}
    for index, (start, end, source_id) in enumerate(intervals, start=1):
        association_id = f"I{index:04d}"
        association_by_source[source_id] = association_id
        inspection_mapping.append(
            {
                "source_inspection_id": source_id,
                "association_inspection_id": association_id,
                "start_timestamp": _format_utc(start),
                "end_timestamp": _format_utc(end),
            }
        )

    for row in normalized_rows:
        row["association_inspection_id"] = association_by_source[row["source_inspection_id"]]
    normalized_rows.sort(
        key=lambda row: (
            row["association_inspection_id"],
            row["frame_id"],
            row["local_observation_id"],
            row["mask_path"],
        )
    )

    return normalized_rows, inspection_mapping, [input_artifacts[path] for path in sorted(input_artifacts)]


def build_records(
    normalized_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    observation_rows: list[dict[str, Any]] = []
    frame_rows: list[dict[str, Any]] = []
    for row in normalized_rows:
        geometry = row["geometry"]
        eligible = int(geometry["area_px"]) > 0
        common = {
            "sequence_id": row["sequence_id"],
            "source_inspection_id": row["source_inspection_id"],
            "association_inspection_id": row["association_inspection_id"],
            "frame_id": str(row["frame_id"]),
            "local_observation_id": row["local_observation_id"],
            "timestamp": row["timestamp"],
            "mileage_m": row["mileage_m"],
            "mileage_text": row["mileage_text"],
            "ring_id": str(row["ring_id"]),
            "clock_direction": row["clock_direction"],
            "disease_type": row["disease_type"],
            "observation_source": OBSERVATION_SOURCE,
            "comparability_status": COMPARABILITY_STATUS,
            "data_contract_version": DATA_CONTRACT_VERSION,
        }
        observation_rows.append(
            {
                **common,
                "image_path": row["image_path"],
                "mask_path": row["mask_path"],
                **geometry,
                "association_eligible": str(eligible).lower(),
                "exclusion_reason": "" if eligible else "empty_mask",
            }
        )
        if not eligible:
            continue
        inspection_id = row["association_inspection_id"]
        frame_rows.append(
            {
                "image_id": f"{row['sequence_id']}::{inspection_id}::{row['frame_id']}",
                "inspection_id": inspection_id,
                "frame_id": str(row["frame_id"]),
                "timestamp": row["timestamp"],
                "mileage_m": row["mileage_m"],
                "mileage_text": row["mileage_text"],
                "ring_id": str(row["ring_id"]),
                "clock_direction": row["clock_direction"],
                "disease_id": f"{row['sequence_id']}::{inspection_id}::{row['local_observation_id']}",
                "disease_type": row["disease_type"],
                "kict_image_path": row["image_path"],
                "kict_mask_path": row["mask_path"],
                "kict_area_px": str(geometry["area_px"]),
                "kict_bbox_x1": str(geometry["bbox_x1"]),
                "kict_bbox_y1": str(geometry["bbox_y1"]),
                "kict_bbox_x2": str(geometry["bbox_x2"]),
                "kict_bbox_y2": str(geometry["bbox_y2"]),
                "kict_center_x": str(geometry["center_x"]),
                "kict_center_y": str(geometry["center_y"]),
                "has_crack": "true",
                "observation_source": OBSERVATION_SOURCE,
                "comparability_status": COMPARABILITY_STATUS,
                "sequence_id": row["sequence_id"],
                "source_inspection_id": row["source_inspection_id"],
                "association_inspection_id": inspection_id,
                "local_observation_id": row["local_observation_id"],
                "data_contract_version": DATA_CONTRACT_VERSION,
                "kict_mask_width": str(geometry["bbox_width"]),
                "kict_mask_height": str(geometry["bbox_height"]),
                "mask_canvas_width": str(geometry["mask_canvas_width"]),
                "mask_canvas_height": str(geometry["mask_canvas_height"]),
            }
        )
    return observation_rows, frame_rows


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_fingerprint(path: Path, relative_path: str, role: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": relative_path,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }
    if role:
        result["role"] = role
    return result


def _write_csv(path: Path, rows: Iterable[Mapping[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_csv_with_header(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _assert_portable_path(value: str, label: str, expected_root: str | None = None) -> None:
    normalized = value.replace("\\", "/")
    pure_path = PurePosixPath(normalized)
    if (
        Path(value).is_absolute()
        or pure_path.is_absolute()
        or re.match(r"^[A-Za-z]:[/\\]", value)
        or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", normalized)
        or any(":" in part for part in pure_path.parts)
        or ".." in pure_path.parts
    ):
        raise ValueError(f"{label} must be a portable relative path: {value}")
    if expected_root and (not pure_path.parts or pure_path.parts[0] != expected_root):
        raise ValueError(f"{label} must stay inside {expected_root}/: {value}")


def validate_prepared_artifacts(
    observation_path: Path,
    frame_path: Path,
    expected_observation_count: int,
    expected_frame_count: int,
) -> None:
    errors = validate_csv_schema(frame_path, "robot_kict_frame_records", allow_empty=True)
    if errors:
        raise ValueError("frame_records schema validation failed: " + "; ".join(errors))
    observation_fields, observation_rows = _read_csv_with_header(observation_path)
    frame_fields, frame_rows = _read_csv_with_header(frame_path)
    for label, fieldnames in (("observation_records", observation_fields), ("frame_records", frame_fields)):
        duplicates = sorted({field for field in fieldnames if fieldnames.count(field) > 1})
        if duplicates:
            raise ValueError(f"{label} contains duplicate columns: {', '.join(duplicates)}")
    missing_observation = [field for field in OBSERVATION_FIELDNAMES if field not in observation_fields]
    if missing_observation:
        raise ValueError(
            "observation_records missing required columns: " + ", ".join(missing_observation)
        )
    missing_frame = [field for field in FRAME_FIELDNAMES if field not in frame_fields]
    if missing_frame:
        raise ValueError("frame_records missing required projection columns: " + ", ".join(missing_frame))
    for label, fieldnames in (("observation_records", observation_fields), ("frame_records", frame_fields)):
        forbidden = sorted(field for field in fieldnames if _is_forbidden_answer_column(field))
        if forbidden:
            raise ValueError(f"{label} contains forbidden answer/evaluation columns: {', '.join(forbidden)}")
    unexpected_observation = sorted(set(observation_fields) - set(OBSERVATION_FIELDNAMES))
    unexpected_frame = sorted(set(frame_fields) - set(FRAME_FIELDNAMES))
    if unexpected_observation:
        raise ValueError(
            "observation_records contains unexpected columns: " + ", ".join(unexpected_observation)
        )
    if unexpected_frame:
        raise ValueError("frame_records contains unexpected columns: " + ", ".join(unexpected_frame))
    if len(observation_rows) != expected_observation_count:
        raise ValueError("observation_records row count changed during staging")
    if len(frame_rows) != expected_frame_count:
        raise ValueError("frame_records row count changed during staging")
    for label, rows in (("observation_records", observation_rows), ("frame_records", frame_rows)):
        for line_number, row in enumerate(rows, start=2):
            if None in row:
                raise ValueError(f"{label} line {line_number}: unexpected extra unnamed cells")
            if row.get("data_contract_version") != DATA_CONTRACT_VERSION:
                raise ValueError(f"{label} line {line_number}: unsupported data_contract_version")
            if "global_disease_id" in row or any("global_disease_id" in str(value) for value in row.values()):
                raise ValueError(f"{label} line {line_number}: global_disease_id is forbidden")
            for path_field, expected_root in (
                ("image_path", "images"),
                ("mask_path", "masks"),
                ("kict_image_path", "images"),
                ("kict_mask_path", "masks"),
            ):
                if row.get(path_field):
                    _assert_portable_path(
                        row[path_field],
                        f"{label} line {line_number} {path_field}",
                        expected_root,
                    )

    eligible_observations: dict[tuple[str, str, str, str], dict[str, str]] = {}
    seen_observations: set[tuple[str, str, str, str]] = set()
    for line_number, row in enumerate(observation_rows, start=2):
        required_values = [
            field for field in OBSERVATION_FIELDNAMES if field != "exclusion_reason" and not str(row.get(field, "")).strip()
        ]
        if required_values:
            raise ValueError(
                f"observation_records line {line_number}: empty required fields: {', '.join(required_values)}"
            )
        observation_key = (
            row.get("sequence_id", ""),
            row.get("association_inspection_id", ""),
            row.get("frame_id", ""),
            row.get("local_observation_id", ""),
        )
        if not all(observation_key) or observation_key in seen_observations:
            raise ValueError(f"observation_records line {line_number}: invalid or duplicate observation key")
        seen_observations.add(observation_key)
        for identifier_field in (
            "sequence_id",
            "source_inspection_id",
            "association_inspection_id",
            "local_observation_id",
        ):
            value = row[identifier_field]
            if not SAFE_ID_PATTERN.fullmatch(value) or value in {".", ".."} or "::" in value:
                raise ValueError(
                    f"observation_records line {line_number}: invalid {identifier_field}"
                )
        try:
            frame_id = int(row["frame_id"])
            ring_id = int(row["ring_id"])
            mileage = Decimal(row["mileage_m"])
            timestamp = _parse_timestamp(row["timestamp"], line_number)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"observation_records line {line_number}: invalid engineering metadata") from exc
        if frame_id < 0 or ring_id < 0 or not mileage.is_finite() or mileage < 0:
            raise ValueError(f"observation_records line {line_number}: invalid engineering metadata")
        if row["timestamp"] != _format_utc(timestamp):
            raise ValueError(f"observation_records line {line_number}: timestamp must be canonical UTC")
        if row["mileage_m"] != _format_decimal(mileage) or row["mileage_text"] != format_mileage_text(mileage):
            raise ValueError(f"observation_records line {line_number}: invalid mileage projection")
        if row["clock_direction"] not in VALID_CLOCK_DIRECTIONS:
            raise ValueError(f"observation_records line {line_number}: invalid clock_direction")
        if _has_formula_prefix(row["disease_type"]) or _contains_absolute_path_text(row["disease_type"]):
            raise ValueError(f"observation_records line {line_number}: unsafe disease_type")
        if row.get("observation_source") != OBSERVATION_SOURCE:
            raise ValueError(f"observation_records line {line_number}: invalid observation_source")
        if row.get("comparability_status") != COMPARABILITY_STATUS:
            raise ValueError(f"observation_records line {line_number}: invalid comparability_status")
        eligible = row.get("association_eligible")
        if eligible not in {"true", "false"}:
            raise ValueError(f"observation_records line {line_number}: association_eligible must be true/false")
        try:
            area = int(row["area_px"])
            x1, y1 = int(row["bbox_x1"]), int(row["bbox_y1"])
            x2, y2 = int(row["bbox_x2"]), int(row["bbox_y2"])
            bbox_width, bbox_height = int(row["bbox_width"]), int(row["bbox_height"])
            canvas_width = int(row["mask_canvas_width"])
            canvas_height = int(row["mask_canvas_height"])
            center_x, center_y = float(row["center_x"]), float(row["center_y"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"observation_records line {line_number}: invalid geometry value") from exc
        if canvas_width <= 0 or canvas_height <= 0:
            raise ValueError(f"observation_records line {line_number}: mask canvas must be positive")
        if eligible == "false":
            if not (
                area == 0
                and (x1, y1, x2, y2) == (-1, -1, -1, -1)
                and (center_x, center_y) == (-1.0, -1.0)
                and (bbox_width, bbox_height) == (0, 0)
                and row.get("exclusion_reason") == "empty_mask"
            ):
                raise ValueError(f"observation_records line {line_number}: invalid empty-mask semantics")
        else:
            if not (
                area > 0
                and 0 <= x1 < x2 <= canvas_width
                and 0 <= y1 < y2 <= canvas_height
                and bbox_width == x2 - x1
                and bbox_height == y2 - y1
                and area <= bbox_width * bbox_height
                and math.isclose(center_x, (x1 + x2 - 1) / 2, abs_tol=1e-9)
                and math.isclose(center_y, (y1 + y2 - 1) / 2, abs_tol=1e-9)
                and not row.get("exclusion_reason")
            ):
                raise ValueError(f"observation_records line {line_number}: invalid eligible-mask semantics")
            eligible_observations[observation_key] = row

    seen_frames: set[tuple[str, str, str, str]] = set()
    for line_number, frame in enumerate(frame_rows, start=2):
        empty_frame_fields = [field for field in FRAME_FIELDNAMES if not str(frame.get(field, "")).strip()]
        if empty_frame_fields:
            raise ValueError(
                f"frame_records line {line_number}: empty required fields: {', '.join(empty_frame_fields)}"
            )
        frame_key = (
            frame.get("sequence_id", ""),
            frame.get("association_inspection_id", ""),
            frame.get("frame_id", ""),
            frame.get("local_observation_id", ""),
        )
        if not all(frame_key) or frame_key in seen_frames:
            raise ValueError(f"frame_records line {line_number}: invalid or duplicate projection key")
        seen_frames.add(frame_key)
        observation = eligible_observations.get(frame_key)
        if observation is None:
            raise ValueError(f"frame_records line {line_number}: no eligible observation for projection key {frame_key}")
        sequence_id, inspection_id, frame_id, local_id = frame_key
        expected = {
            "image_id": f"{sequence_id}::{inspection_id}::{frame_id}",
            "inspection_id": inspection_id,
            "frame_id": frame_id,
            "timestamp": observation["timestamp"],
            "mileage_m": observation["mileage_m"],
            "mileage_text": observation["mileage_text"],
            "ring_id": observation["ring_id"],
            "clock_direction": observation["clock_direction"],
            "disease_id": f"{sequence_id}::{inspection_id}::{local_id}",
            "disease_type": observation["disease_type"],
            "kict_image_path": observation["image_path"],
            "kict_mask_path": observation["mask_path"],
            "kict_area_px": observation["area_px"],
            "kict_bbox_x1": observation["bbox_x1"],
            "kict_bbox_y1": observation["bbox_y1"],
            "kict_bbox_x2": observation["bbox_x2"],
            "kict_bbox_y2": observation["bbox_y2"],
            "kict_center_x": observation["center_x"],
            "kict_center_y": observation["center_y"],
            "has_crack": "true",
            "observation_source": OBSERVATION_SOURCE,
            "comparability_status": COMPARABILITY_STATUS,
            "sequence_id": sequence_id,
            "source_inspection_id": observation["source_inspection_id"],
            "association_inspection_id": inspection_id,
            "local_observation_id": local_id,
            "data_contract_version": DATA_CONTRACT_VERSION,
            "kict_mask_width": observation["bbox_width"],
            "kict_mask_height": observation["bbox_height"],
            "mask_canvas_width": observation["mask_canvas_width"],
            "mask_canvas_height": observation["mask_canvas_height"],
        }
        mismatched = [field for field, value in expected.items() if frame.get(field) != value]
        if mismatched:
            raise ValueError(
                f"frame_records line {line_number}: projection mismatch for {frame_key}: {', '.join(mismatched)}"
            )
    if seen_frames != set(eligible_observations):
        missing = sorted(set(eligible_observations) - seen_frames)
        raise ValueError(f"frame_records missing eligible observation projections: {missing}")


def _inspection_counts(
    observation_rows: list[dict[str, Any]], inspection_mapping: list[dict[str, str]]
) -> tuple[list[dict[str, Any]], bool, list[str]]:
    counts: list[dict[str, Any]] = []
    reasons: list[str] = []
    if len(inspection_mapping) < 2:
        reasons.append("at_least_two_inspections_required")
    for mapping in inspection_mapping:
        inspection_id = mapping["association_inspection_id"]
        rows = [row for row in observation_rows if row["association_inspection_id"] == inspection_id]
        eligible = sum(row["association_eligible"] == "true" for row in rows)
        excluded = len(rows) - eligible
        counts.append(
            {
                "source_inspection_id": mapping["source_inspection_id"],
                "association_inspection_id": inspection_id,
                "raw_observation_count": len(rows),
                "eligible_observation_count": eligible,
                "excluded_observation_count": excluded,
                "exclusion_reasons": {"empty_mask": excluded} if excluded else {},
            }
        )
        if eligible == 0:
            reasons.append(f"{inspection_id}_has_no_eligible_observations")
    return counts, not reasons, reasons


def _assert_fingerprint_unchanged(path: Path, expected: Mapping[str, Any], label: str) -> None:
    try:
        current = _file_fingerprint(path, str(expected["path"]), expected.get("role"))
    except OSError as exc:
        raise RuntimeError(f"source changed during preparation: {label} ({expected['path']})") from exc
    if current["size_bytes"] != expected["size_bytes"] or current["sha256"] != expected["sha256"]:
        raise RuntimeError(f"source changed during preparation: {label} ({expected['path']})")


def verify_source_stability(
    metadata_path: Path,
    metadata_fingerprint: Mapping[str, Any],
    input_artifacts: list[dict[str, Any]],
) -> None:
    _assert_fingerprint_unchanged(metadata_path, metadata_fingerprint, "metadata")
    for item in input_artifacts:
        _assert_fingerprint_unchanged(item["resolved_path"], item, str(item.get("role", "input")))


def build_preparation_manifest(
    metadata_fingerprint: Mapping[str, Any],
    input_artifacts: list[dict[str, Any]],
    observation_path: Path,
    frame_path: Path,
    observation_rows: list[dict[str, Any]],
    frame_rows: list[dict[str, Any]],
    inspection_mapping: list[dict[str, str]],
) -> dict[str, Any]:
    counts, inference_ready, readiness_reasons = _inspection_counts(observation_rows, inspection_mapping)
    source_artifacts = [
        {key: item[key] for key in ("role", "path", "size_bytes", "sha256")}
        for item in input_artifacts
    ]
    return {
        "data_contract_version": DATA_CONTRACT_VERSION,
        "generated_at": _format_utc(datetime.now(timezone.utc)),
        "path_base": "dataset_root",
        "sequence_id": observation_rows[0]["sequence_id"],
        "source_metadata": {
            key: metadata_fingerprint[key] for key in ("path", "size_bytes", "sha256")
        },
        "source_artifacts": source_artifacts,
        "inspection_mapping": inspection_mapping,
        "inspection_counts": counts,
        "inference_ready": inference_ready,
        "readiness_reasons": readiness_reasons,
        "row_counts": {
            "observation_records": len(observation_rows),
            "frame_records": len(frame_rows),
        },
        "outputs": {
            "observation_records": _file_fingerprint(
                observation_path, "observation_records.csv"
            ),
            "frame_records": _file_fingerprint(frame_path, "frame_records.csv"),
        },
        "artifact_set_complete": True,
        "claim_boundary": (
            "fixture-backed input contract only; no ground truth evaluation or longitudinal growth claim"
        ),
    }


def require_inference_ready(manifest: Mapping[str, Any] | Path) -> Mapping[str, Any]:
    if isinstance(manifest, Path):
        manifest = json.loads(manifest.read_text(encoding="utf-8"))
    if manifest.get("data_contract_version") != DATA_CONTRACT_VERSION:
        raise ValueError("prepared manifest has an unsupported data_contract_version")
    if manifest.get("artifact_set_complete") is not True:
        raise ValueError("prepared manifest is not a complete artifact set")
    readiness_reasons = manifest.get("readiness_reasons")
    if not isinstance(readiness_reasons, list):
        raise ValueError("prepared manifest readiness_reasons must be a list")
    if manifest.get("inference_ready") is not True or readiness_reasons:
        reasons = readiness_reasons or ["unspecified"]
        raise ValueError("prepared sequence is not inference-ready: " + ", ".join(map(str, reasons)))
    return manifest


def _target_paths(output_dir: Path) -> dict[str, Path]:
    return {name: output_dir / name for name in ARTIFACT_NAMES}


def validate_dataset_layout(dataset_root: Path) -> tuple[Path, Path, Path]:
    metadata_path = dataset_root / "metadata.csv"
    image_dir = dataset_root / "images"
    mask_dir = dataset_root / "masks"
    resolved_paths: list[Path] = []
    for label, path, expected_kind in (
        ("metadata.csv", metadata_path, "file"),
        ("images", image_dir, "directory"),
        ("masks", mask_dir, "directory"),
    ):
        resolved = path.resolve(strict=False)
        if not _is_within(resolved, dataset_root):
            raise ValueError(f"{label} resolves outside dataset_root: {path}")
        if expected_kind == "file" and not resolved.is_file():
            raise FileNotFoundError(f"metadata.csv not found: {path}")
        if expected_kind == "directory" and not resolved.is_dir():
            raise FileNotFoundError(f"{label} directory not found: {path}")
        resolved_paths.append(resolved)
    return resolved_paths[0], resolved_paths[1], resolved_paths[2]


def _validate_known_target_types(targets: Mapping[str, Path]) -> None:
    for name, target in targets.items():
        if target.is_symlink():
            raise ValueError(f"output target must not be a symbolic link: {name}")
        if target.exists() and not target.is_file():
            raise ValueError(f"output target must be a regular file: {name}")


def validate_output_preflight(
    dataset_root: Path,
    output_dir: Path,
    metadata_path: Path,
    overwrite: bool,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Path]:
    dataset_root = dataset_root.resolve(strict=False)
    output_dir = output_dir.resolve(strict=False)
    protected_roots = [
        dataset_root,
        (project_root / "data" / "simulated").resolve(strict=False),
        (project_root / "outputs").resolve(strict=False),
    ]
    for protected in protected_roots:
        if _is_within(output_dir, protected):
            raise ValueError(f"output_dir must not be inside protected root: {protected}")
    targets = _target_paths(output_dir)
    input_paths = {
        metadata_path.resolve(strict=False),
        (dataset_root / "images").resolve(strict=False),
        (dataset_root / "masks").resolve(strict=False),
    }
    for target in targets.values():
        if target.resolve(strict=False) in input_paths:
            raise ValueError(f"output artifact overlaps an input path: {target}")
    if output_dir.is_dir():
        recovery_entries = sorted(
            path.name
            for pattern in (".prepare-real-inspection-backup-*", ".prepare-real-inspection-staging-*")
            for path in output_dir.glob(pattern)
        )
        if recovery_entries:
            raise RuntimeError(
                "output_dir contains unfinished preparation recovery data; inspect it before rerunning: "
                + ", ".join(recovery_entries)
            )
    _validate_known_target_types(targets)
    existing = [path for path in targets.values() if path.exists() or path.is_symlink()]
    if existing and not overwrite:
        raise FileExistsError(
            "preparation output already exists; pass --overwrite to replace only known artifacts: "
            + ", ".join(path.name for path in existing)
        )
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError(f"output_dir is not a directory: {output_dir}")
    return targets


def _atomic_replace(source: Path, target: Path) -> None:
    os.replace(source, target)


def publish_artifacts(staging_dir: Path, output_dir: Path, overwrite: bool) -> dict[str, Path]:
    """Publish the manifest last and restore the complete prior set on failure."""

    output_dir.mkdir(parents=True, exist_ok=True)
    targets = _target_paths(output_dir)
    stage_paths = _target_paths(staging_dir)
    backup_dir = output_dir / f".prepare-real-inspection-backup-{uuid4().hex}"
    backups: dict[str, Path] = {}
    published: list[str] = []
    cleanup_backup = False
    preserve_staging = False
    try:
        _validate_known_target_types(targets)
        _validate_known_target_types(stage_paths)
        backup_dir.mkdir(parents=False, exist_ok=False)
        # Remove the old commit marker before any CSV can change.
        backup_order = ["preparation_manifest.json", "observation_records.csv", "frame_records.csv"]
        for name in backup_order:
            target = targets[name]
            if target.exists():
                if not overwrite:
                    raise FileExistsError(f"preparation output already exists: {target}")
                backup = backup_dir / name
                _atomic_replace(target, backup)
                backups[name] = backup
        for name in ("observation_records.csv", "frame_records.csv", "preparation_manifest.json"):
            _atomic_replace(stage_paths[name], targets[name])
            published.append(name)
        cleanup_backup = True
    except Exception as publish_error:
        rollback_errors: list[str] = []
        for name in reversed(published):
            target = targets[name]
            try:
                if target.exists() or target.is_symlink():
                    target.unlink()
            except Exception as exc:  # pragma: no cover - platform-specific filesystem failure
                rollback_errors.append(f"remove new {name}: {exc}")
        for name, backup in backups.items():
            if backup.exists():
                try:
                    _atomic_replace(backup, targets[name])
                except Exception as exc:
                    rollback_errors.append(f"restore {name}: {exc}")
        if rollback_errors:
            preserve_staging = True
            recovery_note = (
                "publish failed and rollback was incomplete\n"
                f"original error: {publish_error}\n"
                + "\n".join(rollback_errors)
            )
            try:
                (backup_dir / "RECOVERY_REQUIRED.txt").write_text(recovery_note, encoding="utf-8")
                (staging_dir / ".recovery_required").write_text(recovery_note, encoding="utf-8")
            except OSError:
                pass
            raise RuntimeError(
                "preparation publish failed and rollback was incomplete; "
                f"manual recovery backup preserved at {backup_dir}; staging preserved at {staging_dir}; "
                + "; ".join(rollback_errors)
            ) from publish_error
        cleanup_backup = True
        raise
    finally:
        if cleanup_backup:
            shutil.rmtree(backup_dir, ignore_errors=True)
        if not preserve_staging:
            shutil.rmtree(staging_dir, ignore_errors=True)
    return targets


def prepare_real_inspection_pilot(
    dataset_root: Path,
    output_dir: Path,
    *,
    validate_only: bool = False,
    overwrite: bool = False,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    dataset_root = dataset_root.resolve(strict=False)
    output_dir = output_dir.resolve(strict=False)
    if not dataset_root.is_dir():
        raise FileNotFoundError(f"dataset_root not found: {dataset_root}")
    metadata_path, _, _ = validate_dataset_layout(dataset_root)
    targets = validate_output_preflight(dataset_root, output_dir, metadata_path, overwrite, project_root)

    raw_rows, metadata_fingerprint = load_metadata(metadata_path)
    normalized_rows, inspection_mapping, input_artifacts = validate_and_normalize_rows(raw_rows, dataset_root)
    observation_rows, frame_rows = build_records(normalized_rows)

    if validate_only:
        temp_context = tempfile.TemporaryDirectory(prefix="real-inspection-validate-")
        staging_dir = Path(temp_context.name)
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
        staging_dir = output_dir / f".prepare-real-inspection-staging-{uuid4().hex}"
        staging_dir.mkdir(parents=False, exist_ok=False)
        temp_context = None

    publish_started = False
    try:
        observation_path = staging_dir / "observation_records.csv"
        frame_path = staging_dir / "frame_records.csv"
        manifest_path = staging_dir / "preparation_manifest.json"
        _write_csv(observation_path, observation_rows, OBSERVATION_FIELDNAMES)
        _write_csv(frame_path, frame_rows, FRAME_FIELDNAMES)
        validate_prepared_artifacts(
            observation_path,
            frame_path,
            expected_observation_count=len(observation_rows),
            expected_frame_count=len(frame_rows),
        )
        manifest = build_preparation_manifest(
            metadata_fingerprint,
            input_artifacts,
            observation_path,
            frame_path,
            observation_rows,
            frame_rows,
            inspection_mapping,
        )
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        json.loads(manifest_path.read_text(encoding="utf-8"))
        # Recheck immediately before publication so hashes describe the bytes processed above.
        verify_source_stability(metadata_path, metadata_fingerprint, input_artifacts)
        if validate_only:
            return {"manifest": manifest, "published": False, "output_paths": {}}
        publish_started = True
        published = publish_artifacts(staging_dir, output_dir, overwrite)
        return {"manifest": manifest, "published": True, "output_paths": published}
    finally:
        if temp_context is not None:
            temp_context.cleanup()
        elif not publish_started and staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = prepare_real_inspection_pilot(
            args.dataset_root,
            args.output_dir,
            validate_only=args.validate_only,
            overwrite=args.overwrite,
        )
    except Exception as exc:
        print(f"Real inspection preparation failed: {exc}", file=sys.stderr)
        return 1

    manifest = result["manifest"]
    mode = "validation" if args.validate_only else "preparation"
    print(f"Real inspection {mode} completed")
    print(f"sequence_id: {manifest['sequence_id']}")
    print(f"observation rows: {manifest['row_counts']['observation_records']}")
    print(f"frame rows: {manifest['row_counts']['frame_records']}")
    print(f"inference_ready: {str(manifest['inference_ready']).lower()}")
    if not args.validate_only:
        print(f"output_dir: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
