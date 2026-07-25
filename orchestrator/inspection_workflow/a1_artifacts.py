"""Run-local artifact boundary for the explicitly enabled Phase A1 sandbox."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Any

from .claim_decision import (
    build_claim_decision_document,
    validate_claim_decision_document,
)
from .comparison_evidence import (
    COMPARISON_EVIDENCE_FIELDS,
    COMPARISON_EVIDENCE_SCHEMA_VERSION,
    validate_comparison_evidence_records,
)


COMPARISON_EVIDENCE_MANIFEST_SCHEMA_VERSION = "comparison_evidence_manifest_v4"
PHASE_A1_EXECUTION_PROFILE = "phase_a1_sandbox"
PHASE_A1_SANDBOX_MARKER_SCHEMA_VERSION = "phase_a1_sandbox_marker_v2"
A1_RECOVERY_MARKER_SCHEMA_VERSION = "phase_a1_recovery_marker_v1"
SOURCE_VALIDATION_SCOPE = "byte_binding_only"

_RUN_ID_RE = re.compile(r"run_[0-9]{3,}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_CANONICAL_INTEGER_RE = re.compile(r"(?:0|-?[1-9][0-9]*)\Z")
_SANDBOX_MARKER_NAME = ".phase_a1_sandbox.json"
_RECOVERY_MARKER_NAME = ".a1_recovery_required.json"
_SOURCE_ARTIFACT_ROLES = {
    "association_artifact",
    "association_manifest",
    "association_round_artifact",
    "engineering_artifact",
    "frame_artifact",
    "history_memory_context",
    "history_round_context",
    "memory_snapshot",
    "projection_input",
    "projection_receipt",
    "registration_evidence",
    "scale_calibration",
}
_SINGLETON_SOURCE_ROLES = {
    "association_artifact",
    "association_manifest",
    "engineering_artifact",
}
_LIST_FIELDS = {
    "previous_source_inspection_ids",
    "previous_observation_sources",
}
_BOOLEAN_FIELDS = {
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
    "physical_scale_calibrated",
}
_OPTIONAL_BOOLEAN_FIELDS = {"use_disease_id_score"}
_INTEGER_FIELDS = {
    "previous_source_record_count",
    "current_value",
    "previous_memory_snapshot_value",
    "absolute_difference",
    "candidate_count",
    "valid_timepoint_count",
}
_MANIFEST_FIELDS = {
    "schema_version",
    "source_bundle_kind",
    "run_id",
    "plan_fingerprint",
    "execution_profile",
    "comparison_evidence_schema_version",
    "comparison_evidence_path",
    "comparison_evidence_size_bytes",
    "comparison_evidence_sha256",
    "record_count",
    "source_validation_scope",
    "source_artifacts",
}
_SOURCE_BUNDLE_KINDS = {"normalized_records", "run_local_projection"}
_SOURCE_REFERENCE_FIELDS = {"role", "path", "size_bytes", "sha256"}
_SANDBOX_MARKER_FIELDS = {
    "schema_version",
    "run_id",
    "execution_profile",
    "evidence_source_mode",
}
_MAX_JSON_BYTES = 1024 * 1024
_MAX_A1_WORK_ARTIFACT_BYTES = 8 * 1024 * 1024
PHASE_A1_SOURCE_ARTIFACT_LIMIT = 256
PHASE_A1_MAX_INSPECTION_ROUNDS = 31


class PhaseA1ArtifactError(ValueError):
    """Raised when a Run-local A1 artifact set is unsafe or inconsistent."""


def _require_projection_pilot_round_count(round_count: Any) -> int:
    if type(round_count) is not int or round_count <= 0:
        raise PhaseA1ArtifactError(
            "A1 prepared-history inspection round count must be a positive integer"
        )
    if round_count > PHASE_A1_MAX_INSPECTION_ROUNDS:
        raise PhaseA1ArtifactError(
            "A1 prepared-history pilot supports at most "
            f"{PHASE_A1_MAX_INSPECTION_ROUNDS} inspection rounds"
        )
    return round_count


def _validate_run_identity(*, run_id: str, execution_profile: str) -> None:
    if execution_profile != PHASE_A1_EXECUTION_PROFILE:
        raise PhaseA1ArtifactError(
            f"execution_profile must be {PHASE_A1_EXECUTION_PROFILE}"
        )
    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        raise PhaseA1ArtifactError("run_id must use canonical run_NNN format")


def _controlled_temporary_root(project_root: Path) -> Path:
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise PhaseA1ArtifactError("Phase A1 project_root must be an existing directory")
    repository_root = Path(__file__).resolve().parents[2]
    if root == repository_root or repository_root in root.parents:
        raise PhaseA1ArtifactError(
            "Phase A1 sandbox must use a temporary project root outside the live repository"
        )
    temporary_root = Path(tempfile.gettempdir()).resolve()
    try:
        relative = root.relative_to(temporary_root)
    except ValueError as exc:
        raise PhaseA1ArtifactError(
            "Phase A1 sandbox must be inside the process temporary directory"
        ) from exc
    if not relative.parts:
        raise PhaseA1ArtifactError(
            "Phase A1 sandbox must not use the temporary directory root itself"
        )
    return root


def validate_phase_a1_sandbox(
    project_root: Path,
    *,
    run_id: str,
    execution_profile: str,
) -> Path:
    """Return the sandbox root after enforcing the A1 activation boundary."""

    _validate_run_identity(run_id=run_id, execution_profile=execution_profile)
    root = _controlled_temporary_root(project_root)
    _load_phase_a1_sandbox_marker(
        root,
        run_id=run_id,
        execution_profile=execution_profile,
    )
    return root


def _load_phase_a1_sandbox_marker(
    root: Path,
    *,
    run_id: str,
    execution_profile: str,
) -> dict[str, Any]:
    marker_path = root / _SANDBOX_MARKER_NAME
    if (
        not marker_path.is_file()
        or _path_is_reparse_point(marker_path)
    ):
        raise PhaseA1ArtifactError(
            "Phase A1 sandbox marker is missing or unsafe; initialize the sandbox first"
        )
    marker = _load_json_object(marker_path, label="Phase A1 sandbox marker")
    if set(marker) != _SANDBOX_MARKER_FIELDS:
        raise PhaseA1ArtifactError("Phase A1 sandbox marker fields are invalid")
    if (
        marker["schema_version"] != PHASE_A1_SANDBOX_MARKER_SCHEMA_VERSION
        or marker["run_id"] != run_id
        or marker["execution_profile"] != execution_profile
        or marker["evidence_source_mode"] not in _SOURCE_BUNDLE_KINDS
    ):
        raise PhaseA1ArtifactError("Phase A1 sandbox marker identity does not match")
    return marker


def _require_sha256(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise PhaseA1ArtifactError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _artifact_relative_path(run_id: str, filename: str) -> str:
    return f"runs/{run_id}/artifacts/{filename}"


def _staging_relative_path(run_id: str, filename: str) -> str:
    return f"runs/{run_id}/staging/{filename}"


def _path_is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    if os.name != "nt" or not path.exists():
        return False
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError as exc:
        raise PhaseA1ArtifactError(f"unable to inspect path safety: {path.name}") from exc
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400))


def _assert_path_is_contained_and_plain(
    project_root: Path,
    path: Path,
    *,
    include_leaf: bool,
    label: str,
) -> None:
    root = project_root.resolve()
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise PhaseA1ArtifactError(f"{label} is outside the controlled sandbox") from exc
    cursor = root
    parts = relative.parts if include_leaf else relative.parent.parts
    for part in parts:
        cursor /= part
        if cursor.exists() and _path_is_reparse_point(cursor):
            raise PhaseA1ArtifactError(f"{label} must not use a symlink or reparse point")
    resolved_target = path.resolve() if path.exists() else path.parent.resolve() / path.name
    try:
        resolved_target.relative_to(root)
    except ValueError as exc:
        raise PhaseA1ArtifactError(f"{label} resolves outside the controlled sandbox") from exc


def _resolve_fixed_path(project_root: Path, relative_path: str) -> Path:
    path = project_root.joinpath(*PurePosixPath(relative_path).parts)
    _assert_path_is_contained_and_plain(
        project_root,
        path,
        include_leaf=True,
        label=f"artifact path {relative_path}",
    )
    return path


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _preflight_idempotent_target(path: Path, data: bytes) -> None:
    if path.exists():
        if not path.is_file() or path.is_symlink():
            raise PhaseA1ArtifactError(f"refusing to replace non-regular artifact: {path.name}")
        try:
            existing = path.read_bytes()
        except OSError as exc:
            raise PhaseA1ArtifactError(f"unable to read existing A1 artifact: {path.name}") from exc
        if existing == data:
            return
        raise PhaseA1ArtifactError(f"refusing to overwrite changed A1 artifact: {path.name}")


def _atomic_write_idempotent(
    path: Path,
    data: bytes,
    *,
    allowed_root: Path,
) -> bool:
    _assert_path_is_contained_and_plain(
        allowed_root,
        path,
        include_leaf=True,
        label=f"A1 artifact {path.name}",
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PhaseA1ArtifactError(
            f"unable to create A1 artifact directory for: {path.name}"
        ) from exc
    _assert_path_is_contained_and_plain(
        allowed_root,
        path,
        include_leaf=True,
        label=f"A1 artifact {path.name}",
    )
    _preflight_idempotent_target(path, data)
    _assert_path_is_contained_and_plain(
        allowed_root,
        path,
        include_leaf=True,
        label=f"A1 artifact {path.name}",
    )
    if path.exists():
        return False

    temporary_path: Path | None = None
    replace_attempted = False
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _assert_path_is_contained_and_plain(
            allowed_root,
            path,
            include_leaf=True,
            label=f"A1 artifact {path.name}",
        )
        replace_attempted = True
        os.replace(temporary_path, path)
        temporary_path = None
    except (OSError, PhaseA1ArtifactError) as exc:
        cleanup_error: OSError | None = None
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                cleanup_error = cleanup_exc
        message = f"unable to atomically write A1 artifact: {path.name}"
        if cleanup_error is not None:
            message += (
                f"; temporary cleanup also failed: "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
            )
        error = PhaseA1ArtifactError(message)
        error.write_state_uncertain = replace_attempted or cleanup_error is not None
        raise error from exc
    return True


def _write_failure_requires_recovery(error: Exception) -> bool:
    return bool(getattr(error, "write_state_uncertain", False))


def initialize_phase_a1_sandbox(
    project_root: Path,
    *,
    run_id: str,
    execution_profile: str = PHASE_A1_EXECUTION_PROFILE,
    evidence_source_mode: str = "normalized_records",
) -> Path:
    """Initialize the explicit marker required by a temporary A1 sandbox."""

    _validate_run_identity(run_id=run_id, execution_profile=execution_profile)
    if evidence_source_mode not in _SOURCE_BUNDLE_KINDS:
        raise PhaseA1ArtifactError("evidence_source_mode is invalid")
    root = _controlled_temporary_root(project_root)
    marker = {
        "schema_version": PHASE_A1_SANDBOX_MARKER_SCHEMA_VERSION,
        "run_id": run_id,
        "execution_profile": execution_profile,
        "evidence_source_mode": evidence_source_mode,
    }
    marker_path = root / _SANDBOX_MARKER_NAME
    _atomic_write_idempotent(
        marker_path,
        _canonical_json_bytes(marker),
        allowed_root=root,
    )
    return marker_path


def _serialize_csv_value(field: str, value: Any) -> str:
    if value is None:
        return ""
    if field in _LIST_FIELDS:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if field in _BOOLEAN_FIELDS or field in _OPTIONAL_BOOLEAN_FIELDS:
        return "true" if value is True else "false"
    return str(value)


def comparison_evidence_csv_bytes(records: Iterable[Mapping[str, Any]]) -> tuple[bytes, list[dict[str, Any]]]:
    """Validate and encode deterministic Comparison Evidence CSV bytes."""

    try:
        validated = validate_comparison_evidence_records(records)
    except ValueError as exc:
        raise PhaseA1ArtifactError(f"comparison evidence records are invalid: {exc}") from exc
    validated.sort(
        key=lambda record: (
            record["current_inspection_id"],
            record["current_observation_id"],
            record["evidence_id"],
        )
    )
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=list(COMPARISON_EVIDENCE_FIELDS),
        lineterminator="\n",
    )
    writer.writeheader()
    for record in validated:
        writer.writerow(
            {
                field: _serialize_csv_value(field, record[field])
                for field in COMPARISON_EVIDENCE_FIELDS
            }
        )
    return buffer.getvalue().encode("utf-8"), validated


def _parse_bool(value: str, *, field: str, row_number: int, optional: bool) -> bool | None:
    if value == "" and optional:
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    raise PhaseA1ArtifactError(
        f"comparison_evidence.csv row {row_number} {field} must be true, false, or canonical empty"
    )


def _parse_csv_value(field: str, value: str, *, row_number: int) -> Any:
    if field in _LIST_FIELDS:
        def reject_non_finite(token: str) -> None:
            raise PhaseA1ArtifactError(
                f"comparison_evidence.csv row {row_number} {field} "
                f"must not contain non-finite JSON value: {token}"
            )

        try:
            parsed = json.loads(value, parse_constant=reject_non_finite)
        except PhaseA1ArtifactError:
            raise
        except json.JSONDecodeError as exc:
            raise PhaseA1ArtifactError(
                f"comparison_evidence.csv row {row_number} {field} must be a JSON list"
            ) from exc
        if not isinstance(parsed, list):
            raise PhaseA1ArtifactError(
                f"comparison_evidence.csv row {row_number} {field} must be a JSON list"
            )
        try:
            canonical_value = json.dumps(
                parsed,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise PhaseA1ArtifactError(
                f"comparison_evidence.csv row {row_number} {field} "
                "must use finite canonical JSON"
            ) from exc
        if value != canonical_value:
            raise PhaseA1ArtifactError(
                f"comparison_evidence.csv row {row_number} {field} must use canonical JSON"
            )
        return parsed
    if field in _BOOLEAN_FIELDS:
        return _parse_bool(value, field=field, row_number=row_number, optional=False)
    if field in _OPTIONAL_BOOLEAN_FIELDS:
        return _parse_bool(value, field=field, row_number=row_number, optional=True)
    if field in _INTEGER_FIELDS:
        if value == "":
            return None
        if _CANONICAL_INTEGER_RE.fullmatch(value) is None:
            raise PhaseA1ArtifactError(
                f"comparison_evidence.csv row {row_number} {field} must use canonical integer encoding"
            )
        try:
            return int(value)
        except ValueError as exc:
            raise PhaseA1ArtifactError(
                f"comparison_evidence.csv row {row_number} {field} must be an integer"
            ) from exc
    return None if value == "" else value


def parse_comparison_evidence_csv(data: bytes) -> list[dict[str, Any]]:
    """Decode canonical Comparison Evidence CSV and rerun the Phase 0 validator."""

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PhaseA1ArtifactError("comparison_evidence.csv must be UTF-8 without BOM") from exc
    if text.startswith("\ufeff"):
        raise PhaseA1ArtifactError("comparison_evidence.csv must be UTF-8 without BOM")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    fieldnames = reader.fieldnames
    if fieldnames != list(COMPARISON_EVIDENCE_FIELDS):
        raise PhaseA1ArtifactError(
            "comparison_evidence.csv fieldnames must exactly match comparison_evidence_v1"
        )
    rows: list[dict[str, Any]] = []
    for row_number, row in enumerate(reader, start=1):
        if None in row:
            raise PhaseA1ArtifactError(
                f"comparison_evidence.csv row {row_number} has extra columns"
            )
        rows.append(
            {
                field: _parse_csv_value(field, row[field], row_number=row_number)
                for field in COMPARISON_EVIDENCE_FIELDS
            }
        )
    try:
        validated = validate_comparison_evidence_records(rows)
    except ValueError as exc:
        raise PhaseA1ArtifactError(f"comparison_evidence.csv is invalid: {exc}") from exc
    canonical_bytes, canonical_records = comparison_evidence_csv_bytes(validated)
    if data != canonical_bytes:
        raise PhaseA1ArtifactError(
            "comparison_evidence.csv must use canonical row order and byte encoding"
        )
    return canonical_records


def _require_relative_run_work_path(project_root: Path, run_id: str, value: Any) -> tuple[str, Path]:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise PhaseA1ArtifactError("source artifact path must be a Run-local POSIX path")
    pure_path = PurePosixPath(value)
    if (
        pure_path.is_absolute()
        or value != pure_path.as_posix()
        or any(part in {"", ".", ".."} for part in pure_path.parts)
    ):
        raise PhaseA1ArtifactError("source artifact path must be a Run-local POSIX path")
    expected_prefix = PurePosixPath("runs") / run_id / "work"
    try:
        pure_path.relative_to(expected_prefix)
    except ValueError as exc:
        raise PhaseA1ArtifactError(
            f"source artifact must be inside runs/{run_id}/work"
        ) from exc
    path = project_root.joinpath(*pure_path.parts)
    _assert_path_is_contained_and_plain(
        project_root,
        path,
        include_leaf=True,
        label=f"source artifact {value}",
    )
    if not path.is_file():
        raise PhaseA1ArtifactError(f"source artifact must be an existing regular file: {value}")
    resolved = path.resolve()
    work_root = project_root.joinpath("runs", run_id, "work").resolve()
    try:
        resolved.relative_to(work_root)
    except ValueError as exc:
        raise PhaseA1ArtifactError(f"source artifact resolves outside Run work: {value}") from exc
    return pure_path.as_posix(), resolved


def snapshot_phase_a1_work_artifact(
    project_root: Path,
    *,
    run_id: str,
    execution_profile: str,
    relative_path: str,
    max_size_bytes: int = _MAX_A1_WORK_ARTIFACT_BYTES,
) -> dict[str, Any]:
    """Read one plain Run-local work artifact as a point-in-time byte snapshot."""

    root = validate_phase_a1_sandbox(
        project_root,
        run_id=run_id,
        execution_profile=execution_profile,
    )
    _reject_recovery_marker(root, run_id, "work")
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or "\\" in relative_path
        or ":" in relative_path
    ):
        raise PhaseA1ArtifactError("A1 work artifact path must be a Run-local POSIX path")
    pure_path = PurePosixPath(relative_path)
    expected_prefix = PurePosixPath("runs") / run_id / "work"
    try:
        pure_path.relative_to(expected_prefix)
    except ValueError as exc:
        raise PhaseA1ArtifactError(
            f"A1 work artifact must be inside runs/{run_id}/work"
        ) from exc
    if (
        pure_path.is_absolute()
        or relative_path != pure_path.as_posix()
        or any(part in {"", ".", ".."} for part in pure_path.parts)
    ):
        raise PhaseA1ArtifactError("A1 work artifact path must be a Run-local POSIX path")
    path_text = relative_path
    path = _resolve_fixed_path(root, path_text)
    if (
        type(max_size_bytes) is not int
        or max_size_bytes <= 0
    ):
        raise PhaseA1ArtifactError("A1 work artifact size limit must be a positive integer")
    try:
        size_bytes = path.stat().st_size
        if size_bytes > max_size_bytes:
            raise PhaseA1ArtifactError(
                f"source artifact exceeds the A1 pilot size limit: {path_text}"
            )
        data = path.read_bytes()
    except OSError as exc:
        raise PhaseA1ArtifactError(f"unable to read source artifact: {path_text}") from exc
    if len(data) > max_size_bytes:
        raise PhaseA1ArtifactError(
            f"source artifact exceeds the A1 pilot size limit: {path_text}"
        )
    return {
        "path": path_text,
        "data": data,
        "size_bytes": len(data),
        "sha256": _sha256_bytes(data),
    }


def write_phase_a1_work_artifact(
    project_root: Path,
    *,
    run_id: str,
    execution_profile: str,
    relative_path: str,
    data: bytes,
) -> bool:
    """Idempotently materialize a preparatory file inside one A1 Run work tree."""

    root = validate_phase_a1_sandbox(
        project_root,
        run_id=run_id,
        execution_profile=execution_profile,
    )
    _reject_recovery_marker(root, run_id, "work")
    marker = _load_phase_a1_sandbox_marker(
        root,
        run_id=run_id,
        execution_profile=execution_profile,
    )
    if marker["evidence_source_mode"] != "run_local_projection":
        raise PhaseA1ArtifactError(
            "A1 projection work artifacts require run_local_projection sandbox mode"
        )
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or "\\" in relative_path
        or ":" in relative_path
    ):
        raise PhaseA1ArtifactError("A1 work artifact path must be a Run-local POSIX path")
    pure_path = PurePosixPath(relative_path)
    expected_prefix = PurePosixPath("runs") / run_id / "work"
    try:
        pure_path.relative_to(expected_prefix)
    except ValueError as exc:
        raise PhaseA1ArtifactError(
            f"A1 work artifact must be inside runs/{run_id}/work"
        ) from exc
    if (
        pure_path.is_absolute()
        or relative_path != pure_path.as_posix()
        or any(part in {"", ".", ".."} for part in pure_path.parts)
    ):
        raise PhaseA1ArtifactError("A1 work artifact path must be a Run-local POSIX path")
    path_text = relative_path
    path = _resolve_fixed_path(root, path_text)
    if not isinstance(data, bytes):
        raise PhaseA1ArtifactError(f"A1 work artifact data must be bytes: {path_text}")
    return _atomic_write_idempotent(path, data, allowed_root=root)


def _snapshot_source_artifacts(
    project_root: Path,
    run_id: str,
    source_artifacts: Any,
) -> list[dict[str, Any]]:
    if isinstance(source_artifacts, (str, bytes, Mapping)) or not isinstance(
        source_artifacts, Iterable
    ):
        raise PhaseA1ArtifactError("source_artifacts must be a list of objects")
    snapshots: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    role_counts: dict[str, int] = {}
    for index, reference in enumerate(source_artifacts, start=1):
        if index > PHASE_A1_SOURCE_ARTIFACT_LIMIT:
            raise PhaseA1ArtifactError(
                "source_artifacts exceeds the A1 pilot reference limit"
            )
        allowed_fields = {"role", "path", "expected_sha256"}
        if (
            not isinstance(reference, Mapping)
            or not {"role", "path"}.issubset(reference)
            or not set(reference).issubset(allowed_fields)
        ):
            raise PhaseA1ArtifactError(
                f"source_artifacts item {index} must contain role, path, and optional expected_sha256"
            )
        role = reference["role"]
        if not isinstance(role, str) or role not in _SOURCE_ARTIFACT_ROLES:
            raise PhaseA1ArtifactError(f"source_artifacts item {index} has unsupported role")
        path_text, path = _require_relative_run_work_path(
            project_root,
            run_id,
            reference["path"],
        )
        if path_text in seen_paths:
            raise PhaseA1ArtifactError(f"duplicate source artifact path: {path_text}")
        seen_paths.add(path_text)
        role_counts[role] = role_counts.get(role, 0) + 1
        try:
            if path.stat().st_size > _MAX_A1_WORK_ARTIFACT_BYTES:
                raise PhaseA1ArtifactError(
                    f"source artifact exceeds the A1 pilot size limit: {path_text}"
                )
            data = path.read_bytes()
        except OSError as exc:
            raise PhaseA1ArtifactError(f"unable to read source artifact: {path_text}") from exc
        if len(data) > _MAX_A1_WORK_ARTIFACT_BYTES:
            raise PhaseA1ArtifactError(
                f"source artifact exceeds the A1 pilot size limit: {path_text}"
            )
        actual_sha256 = _sha256_bytes(data)
        expected_sha256 = reference.get("expected_sha256")
        if expected_sha256 is not None:
            _require_sha256(
                expected_sha256,
                field=f"source_artifacts item {index} expected_sha256",
            )
            if expected_sha256 != actual_sha256:
                raise PhaseA1ArtifactError(
                    f"source artifact changed after projection: {path_text}"
                )
        snapshots.append(
            {
                "role": role,
                "path": path_text,
                "size_bytes": len(data),
                "sha256": actual_sha256,
            }
        )
    for role in _SINGLETON_SOURCE_ROLES:
        if role_counts.get(role) != 1:
            raise PhaseA1ArtifactError(f"source_artifacts must contain exactly one {role}")
    snapshots.sort(key=lambda item: (item["role"], item["path"]))
    return snapshots


def _source_hashes_by_role(source_artifacts: Iterable[Mapping[str, Any]]) -> dict[str, set[str]]:
    hashes = {role: set() for role in _SOURCE_ARTIFACT_ROLES}
    for item in source_artifacts:
        hashes[item["role"]].add(item["sha256"])
    return hashes


def _bind_evidence_provenance(
    records: Iterable[Mapping[str, Any]],
    source_artifacts: Iterable[Mapping[str, Any]],
) -> None:
    hashes = _source_hashes_by_role(source_artifacts)
    referenced_hashes = {role: set() for role in _SOURCE_ARTIFACT_ROLES}
    bindings = (
        ("source_association_artifact_sha256", "association_artifact"),
        ("source_association_manifest_sha256", "association_manifest"),
        ("source_engineering_artifact_sha256", "engineering_artifact"),
        ("source_memory_snapshot_sha256", "memory_snapshot"),
        ("registration_evidence_sha256", "registration_evidence"),
        ("scale_calibration_sha256", "scale_calibration"),
    )
    for row_number, record in enumerate(records, start=1):
        for field, role in bindings:
            value = record[field]
            if value is not None and value not in hashes[role]:
                raise PhaseA1ArtifactError(
                    f"comparison evidence row {row_number} {field} is not bound to a "
                    f"validated {role} artifact"
                )
            if value is not None:
                referenced_hashes[role].add(value)
    for role in {"memory_snapshot", "registration_evidence", "scale_calibration"}:
        if hashes[role] != referenced_hashes[role]:
            raise PhaseA1ArtifactError(
                f"validated {role} source set must exactly match Evidence references"
            )


def _validate_projection_source_set(
    run_id: str,
    declared_inputs: Iterable[Mapping[str, Any]],
    source_bytes_by_path: Mapping[str, bytes],
) -> None:
    declared = list(declared_inputs)
    actual = {(item["role"], item["path"]) for item in declared}
    actual_by_path = {item["path"]: item for item in declared}
    manifest_relative = f"runs/{run_id}/work/association_manifest.json"
    receipt_relative = f"runs/{run_id}/work/projection_receipt.json"
    expected = {
        ("frame_artifact", f"runs/{run_id}/work/frame_records.csv"),
        ("engineering_artifact", f"runs/{run_id}/work/engineering_records.csv"),
        ("association_artifact", f"runs/{run_id}/work/association_records.csv"),
        ("association_manifest", manifest_relative),
        ("projection_receipt", receipt_relative),
    }
    association_manifest_bytes = source_bytes_by_path.get(manifest_relative)
    if association_manifest_bytes is None:
        raise PhaseA1ArtifactError(
            "run_local_projection is missing the Association manifest snapshot"
        )
    association_manifest = _parse_json_object_bytes(
        association_manifest_bytes,
        label="Run-local Association manifest",
    )
    rounds = association_manifest.get("rounds")
    if not isinstance(rounds, list) or not rounds:
        raise PhaseA1ArtifactError(
            "Run-local Association manifest rounds are invalid"
        )
    _require_projection_pilot_round_count(len(rounds))
    for round_number, round_entry in enumerate(rounds, start=1):
        if not isinstance(round_entry, Mapping):
            raise PhaseA1ArtifactError(
                f"Run-local Association manifest round {round_number} is invalid"
            )
        query_frames = round_entry.get("query_frames")
        if not isinstance(query_frames, str):
            raise PhaseA1ArtifactError(
                f"Run-local Association manifest round {round_number} query_frames is invalid"
            )
        expected.add(("history_round_context", query_frames))
        if round_entry.get("mode") != "history_only":
            continue
        for field, role in (
            ("association_records", "association_round_artifact"),
            ("memory_before", "history_memory_context"),
            ("memory_after", "history_round_context"),
        ):
            path_text = round_entry.get(field)
            if not isinstance(path_text, str):
                raise PhaseA1ArtifactError(
                    f"Run-local Association manifest round {round_number} {field} is invalid"
                )
            expected.add((role, path_text))

    receipt_bytes = source_bytes_by_path.get(receipt_relative)
    if receipt_bytes is None:
        raise PhaseA1ArtifactError(
            "run_local_projection is missing projection_receipt.json"
        )
    from .comparison_evidence_projection import (
        ComparisonEvidenceProjectionError,
        parse_projection_receipt,
    )

    try:
        receipt = parse_projection_receipt(
            receipt_bytes,
            run_id=run_id,
            execution_profile=PHASE_A1_EXECUTION_PROFILE,
        )
    except ComparisonEvidenceProjectionError as exc:
        raise PhaseA1ArtifactError(f"projection receipt is invalid: {exc}") from exc
    receipt_source_counts: dict[str, int] = {}
    for reference in receipt["source_artifacts"]:
        kind = reference["kind"]
        receipt_source_counts[kind] = receipt_source_counts.get(kind, 0) + 1
    history_round_count = sum(
        round_entry.get("mode") == "history_only"
        for round_entry in rounds
        if isinstance(round_entry, Mapping)
    )
    expected_source_counts = {
        "prepared_manifest": 1,
        "prepared_observation_records": 1,
        "prepared_frame_records": 1,
        "history_association_records": 1,
        "history_manifest": 1,
        "history_query_frames": len(rounds),
        "history_round_association": history_round_count,
        "history_memory_before": history_round_count,
        "history_memory_after": history_round_count,
    }
    expected_source_counts = {
        kind: count for kind, count in expected_source_counts.items() if count
    }
    if receipt_source_counts != expected_source_counts:
        raise PhaseA1ArtifactError(
            "projection receipt producer source set is incomplete"
        )
    for reference in receipt["source_artifacts"]:
        expected.add(("projection_input", reference["path"]))
        declared_reference = actual_by_path.get(reference["path"])
        if (
            declared_reference is None
            or declared_reference["role"] != "projection_input"
            or declared_reference["size_bytes"] != reference["size_bytes"]
            or declared_reference["sha256"] != reference["sha256"]
        ):
            raise PhaseA1ArtifactError(
                f"projection input does not match receipt: {reference['path']}"
            )

    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details = []
        if missing:
            details.append(f"missing={missing}")
        if extra:
            details.append(f"unexpected={extra}")
        raise PhaseA1ArtifactError(
            "run_local_projection source artifact set is incomplete"
            + (f": {'; '.join(details)}" if details else "")
        )

    expected_projected_paths = {
        path
        for role, path in expected
        if role not in {"projection_input", "projection_receipt"}
    }
    receipt_projected_paths = {
        reference["path"] for reference in receipt["projected_artifacts"]
    }
    if receipt_projected_paths != expected_projected_paths:
        raise PhaseA1ArtifactError(
            "projection receipt projected artifact set is incomplete"
        )
    for reference in receipt["projected_artifacts"]:
        declared_reference = actual_by_path.get(reference["path"])
        if (
            declared_reference is None
            or declared_reference["size_bytes"] != reference["size_bytes"]
            or declared_reference["sha256"] != reference["sha256"]
        ):
            raise PhaseA1ArtifactError(
                f"projected artifact does not match receipt: {reference['path']}"
            )


def _enforce_byte_binding_only_evidence(records: Iterable[Mapping[str, Any]]) -> None:
    for row_number, record in enumerate(records, start=1):
        if record["comparison_comparability_status"] == "verified_comparable":
            raise PhaseA1ArtifactError(
                "comparison evidence row "
                f"{row_number} cannot use verified_comparable while source_validation_scope "
                f"is {SOURCE_VALIDATION_SCOPE}; a trusted semantic validation receipt is required"
            )


def _enforce_byte_binding_only_decision(document: Mapping[str, Any]) -> None:
    for row_number, decision in enumerate(document["record_decisions"], start=1):
        if decision["capabilities"]["directional_change_claim"] != "blocked":
            raise PhaseA1ArtifactError(
                "ClaimDecision row "
                f"{row_number} cannot enable directional_change_claim while "
                f"source_validation_scope is {SOURCE_VALIDATION_SCOPE}"
            )


def _recovery_marker_path(project_root: Path, run_id: str, area: str) -> Path:
    if area == "artifacts":
        relative = _artifact_relative_path(run_id, _RECOVERY_MARKER_NAME)
    elif area == "staging":
        relative = _staging_relative_path(run_id, _RECOVERY_MARKER_NAME)
    elif area == "work":
        relative = f"runs/{run_id}/work/{_RECOVERY_MARKER_NAME}"
    else:
        raise PhaseA1ArtifactError(f"unsupported A1 recovery area: {area}")
    return _resolve_fixed_path(project_root, relative)


def _reject_recovery_marker(project_root: Path, run_id: str, area: str) -> None:
    marker_path = _recovery_marker_path(project_root, run_id, area)
    if marker_path.exists():
        raise PhaseA1ArtifactError(
            f"A1 {area} recovery marker exists; inspect the partial Run-local artifacts "
            "and remove the marker only after manual recovery"
        )


def _raise_recovery_required(
    *,
    project_root: Path,
    run_id: str,
    area: str,
    stage: str,
    committed_paths: Iterable[str],
    primary_error: Exception,
) -> None:
    marker = {
        "schema_version": A1_RECOVERY_MARKER_SCHEMA_VERSION,
        "run_id": run_id,
        "area": area,
        "stage": stage,
        "committed_paths": sorted(set(committed_paths)),
        "primary_error_type": type(primary_error).__name__,
        "primary_error": str(primary_error),
    }
    marker_error: Exception | None = None
    try:
        marker_path = _recovery_marker_path(project_root, run_id, area)
        _atomic_write_idempotent(
            marker_path,
            _canonical_json_bytes(marker),
            allowed_root=project_root,
        )
    except Exception as exc:  # marker diagnostics must not replace the primary failure
        marker_error = exc
    message = (
        f"{primary_error}; A1 {area} recovery is required after {stage}"
    )
    if marker_error is not None:
        message += (
            "; recovery marker write also failed: "
            f"{type(marker_error).__name__}: {marker_error}"
        )
    cause = primary_error.__cause__ or primary_error
    raise PhaseA1ArtifactError(message) from cause


def _write_comparison_evidence_bundle(
    project_root: Path,
    *,
    run_id: str,
    execution_profile: str,
    plan_fingerprint: str,
    records: Iterable[Mapping[str, Any]],
    source_artifacts: Any,
    source_bundle_kind: str,
) -> dict[str, Any]:
    """Write validated Evidence CSV and a manifest-last Run-local bundle."""

    root = validate_phase_a1_sandbox(
        project_root,
        run_id=run_id,
        execution_profile=execution_profile,
    )
    marker = _load_phase_a1_sandbox_marker(
        root,
        run_id=run_id,
        execution_profile=execution_profile,
    )
    _require_sha256(plan_fingerprint, field="plan_fingerprint")
    if source_bundle_kind not in _SOURCE_BUNDLE_KINDS:
        raise PhaseA1ArtifactError("source_bundle_kind is invalid")
    if marker["evidence_source_mode"] != source_bundle_kind:
        raise PhaseA1ArtifactError(
            "source bundle kind does not match the immutable sandbox evidence_source_mode"
        )
    _reject_recovery_marker(root, run_id, "artifacts")
    evidence_bytes, validated = comparison_evidence_csv_bytes(records)
    sources = _snapshot_source_artifacts(root, run_id, source_artifacts)
    _bind_evidence_provenance(validated, sources)
    _enforce_byte_binding_only_evidence(validated)

    evidence_relative = _artifact_relative_path(run_id, "comparison_evidence.csv")
    evidence_path = _resolve_fixed_path(root, evidence_relative)
    manifest_relative = _artifact_relative_path(
        run_id,
        "comparison_evidence_manifest.json",
    )
    manifest_path = _resolve_fixed_path(root, manifest_relative)
    manifest = {
        "schema_version": COMPARISON_EVIDENCE_MANIFEST_SCHEMA_VERSION,
        "source_bundle_kind": source_bundle_kind,
        "run_id": run_id,
        "plan_fingerprint": plan_fingerprint,
        "execution_profile": execution_profile,
        "comparison_evidence_schema_version": COMPARISON_EVIDENCE_SCHEMA_VERSION,
        "comparison_evidence_path": evidence_relative,
        "comparison_evidence_size_bytes": len(evidence_bytes),
        "comparison_evidence_sha256": _sha256_bytes(evidence_bytes),
        "record_count": len(validated),
        "source_validation_scope": SOURCE_VALIDATION_SCOPE,
        "source_artifacts": sources,
    }
    manifest_bytes = _canonical_json_bytes(manifest)

    _preflight_idempotent_target(evidence_path, evidence_bytes)
    _preflight_idempotent_target(manifest_path, manifest_bytes)
    committed_paths: list[str] = []
    try:
        if _atomic_write_idempotent(
            evidence_path,
            evidence_bytes,
            allowed_root=root,
        ):
            committed_paths.append(evidence_relative)
        if _atomic_write_idempotent(
            manifest_path,
            manifest_bytes,
            allowed_root=root,
        ):
            committed_paths.append(manifest_relative)
        committed = validate_comparison_evidence_bundle(
            root,
            run_id=run_id,
            execution_profile=execution_profile,
            plan_fingerprint=plan_fingerprint,
        )
    except Exception as exc:
        if committed_paths or _write_failure_requires_recovery(exc):
            _raise_recovery_required(
                project_root=root,
                run_id=run_id,
                area="artifacts",
                stage="comparison_evidence_bundle",
                committed_paths=committed_paths,
                primary_error=exc,
            )
        raise
    return {
        "comparison_evidence_path": evidence_relative,
        "comparison_evidence_manifest_path": manifest_relative,
        "comparison_evidence_sha256": committed["comparison_evidence_sha256"],
        "record_count": len(committed["records"]),
    }


def write_comparison_evidence_bundle(
    project_root: Path,
    *,
    run_id: str,
    execution_profile: str,
    plan_fingerprint: str,
    records: Iterable[Mapping[str, Any]],
    source_artifacts: Any,
) -> dict[str, Any]:
    """Write caller-normalized Evidence in a normalized-records sandbox."""

    return _write_comparison_evidence_bundle(
        project_root,
        run_id=run_id,
        execution_profile=execution_profile,
        plan_fingerprint=plan_fingerprint,
        records=records,
        source_artifacts=source_artifacts,
        source_bundle_kind="normalized_records",
    )


def write_prepared_history_comparison_evidence_bundle(
    project_root: Path,
    *,
    run_id: str,
    execution_profile: str,
    plan_fingerprint: str,
    prepared_manifest_path: str,
    history_association_path: str,
    history_manifest_path: str,
) -> dict[str, Any]:
    """Materialize validated producer inputs and commit one V4 Evidence bundle."""

    from .comparison_evidence_projection import (
        ComparisonEvidenceProjectionError,
        materialize_prepared_history_projection_sources,
        project_run_local_comparison_evidence,
    )

    try:
        materialize_prepared_history_projection_sources(
            project_root,
            run_id=run_id,
            execution_profile=execution_profile,
            prepared_manifest_path=prepared_manifest_path,
            history_association_path=history_association_path,
            history_manifest_path=history_manifest_path,
        )
        projected = project_run_local_comparison_evidence(
            project_root,
            run_id=run_id,
            execution_profile=execution_profile,
        )
    except ComparisonEvidenceProjectionError as exc:
        raise PhaseA1ArtifactError(
            f"Comparison Evidence prepared-history projection failed: {exc}"
        ) from exc
    return _write_comparison_evidence_bundle(
        project_root,
        run_id=run_id,
        execution_profile=execution_profile,
        plan_fingerprint=plan_fingerprint,
        records=projected["records"],
        source_artifacts=projected["source_artifacts"],
        source_bundle_kind="run_local_projection",
    )


def _parse_json_object_bytes(data: bytes, *, label: str) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise PhaseA1ArtifactError(f"{label} contains duplicate key: {key}")
            result[key] = value
        return result

    if len(data) > _MAX_JSON_BYTES:
        raise PhaseA1ArtifactError(f"{label} exceeds the JSON size limit")
    try:
        payload = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {value}")
            ),
        )
    except PhaseA1ArtifactError:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise PhaseA1ArtifactError(f"{label} must be valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise PhaseA1ArtifactError(f"{label} root must be an object")
    return payload


def _read_file_bytes(path: Path, *, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise PhaseA1ArtifactError(f"unable to read {label}: {path.name}") from exc


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    return _parse_json_object_bytes(
        _read_file_bytes(path, label=label),
        label=label,
    )


def validate_comparison_evidence_bundle(
    project_root: Path,
    *,
    run_id: str,
    execution_profile: str,
    plan_fingerprint: str,
) -> dict[str, Any]:
    """Validate the committed Run-local Evidence bundle and all source bytes."""

    root = validate_phase_a1_sandbox(
        project_root,
        run_id=run_id,
        execution_profile=execution_profile,
    )
    marker = _load_phase_a1_sandbox_marker(
        root,
        run_id=run_id,
        execution_profile=execution_profile,
    )
    _require_sha256(plan_fingerprint, field="plan_fingerprint")
    _reject_recovery_marker(root, run_id, "artifacts")
    manifest_relative = _artifact_relative_path(
        run_id,
        "comparison_evidence_manifest.json",
    )
    manifest_path = _resolve_fixed_path(root, manifest_relative)
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise PhaseA1ArtifactError("comparison_evidence_manifest.json is missing")
    manifest = _load_json_object(manifest_path, label="Comparison Evidence manifest")
    if set(manifest) != _MANIFEST_FIELDS:
        raise PhaseA1ArtifactError(
            "Comparison Evidence manifest fields do not match its schema"
        )
    if manifest["schema_version"] != COMPARISON_EVIDENCE_MANIFEST_SCHEMA_VERSION:
        raise PhaseA1ArtifactError("Comparison Evidence manifest schema_version is invalid")
    if manifest["source_bundle_kind"] not in _SOURCE_BUNDLE_KINDS:
        raise PhaseA1ArtifactError("Comparison Evidence manifest source_bundle_kind is invalid")
    if manifest["source_bundle_kind"] != marker["evidence_source_mode"]:
        raise PhaseA1ArtifactError(
            "Comparison Evidence manifest source_bundle_kind does not match sandbox mode"
        )
    if manifest["run_id"] != run_id:
        raise PhaseA1ArtifactError("Comparison Evidence manifest run_id does not match")
    if manifest["plan_fingerprint"] != plan_fingerprint:
        raise PhaseA1ArtifactError(
            "Comparison Evidence manifest plan_fingerprint does not match"
        )
    if manifest["execution_profile"] != execution_profile:
        raise PhaseA1ArtifactError(
            "Comparison Evidence manifest execution_profile does not match"
        )
    if manifest["comparison_evidence_schema_version"] != COMPARISON_EVIDENCE_SCHEMA_VERSION:
        raise PhaseA1ArtifactError(
            "Comparison Evidence manifest evidence schema_version is invalid"
        )
    if manifest["source_validation_scope"] != SOURCE_VALIDATION_SCOPE:
        raise PhaseA1ArtifactError(
            "Comparison Evidence manifest source_validation_scope is invalid"
        )

    expected_evidence_relative = _artifact_relative_path(run_id, "comparison_evidence.csv")
    if manifest["comparison_evidence_path"] != expected_evidence_relative:
        raise PhaseA1ArtifactError("Comparison Evidence manifest path is not the fixed Run path")
    evidence_path = _resolve_fixed_path(root, expected_evidence_relative)
    if not evidence_path.is_file() or evidence_path.is_symlink():
        raise PhaseA1ArtifactError("comparison_evidence.csv is missing")
    evidence_bytes = _read_file_bytes(
        evidence_path,
        label="comparison_evidence.csv",
    )
    if (
        type(manifest["comparison_evidence_size_bytes"]) is not int
        or manifest["comparison_evidence_size_bytes"] != len(evidence_bytes)
    ):
        raise PhaseA1ArtifactError("comparison_evidence.csv size does not match manifest")
    if manifest["comparison_evidence_sha256"] != _sha256_bytes(evidence_bytes):
        raise PhaseA1ArtifactError("comparison_evidence.csv SHA-256 does not match manifest")
    records = parse_comparison_evidence_csv(evidence_bytes)
    if type(manifest["record_count"]) is not int or manifest["record_count"] != len(records):
        raise PhaseA1ArtifactError("comparison_evidence.csv row count does not match manifest")

    references = manifest["source_artifacts"]
    if (
        not isinstance(references, list)
        or not references
        or len(references) > PHASE_A1_SOURCE_ARTIFACT_LIMIT
    ):
        raise PhaseA1ArtifactError(
            "Comparison Evidence manifest source_artifacts must be a non-empty "
            "A1 pilot-sized list"
        )
    declared_inputs = []
    source_bytes_by_path: dict[str, bytes] = {}
    for index, reference in enumerate(references, start=1):
        if not isinstance(reference, Mapping) or set(reference) != _SOURCE_REFERENCE_FIELDS:
            raise PhaseA1ArtifactError(
                f"Comparison Evidence manifest source_artifacts item {index} is invalid"
            )
        role = reference["role"]
        if not isinstance(role, str) or role not in _SOURCE_ARTIFACT_ROLES:
            raise PhaseA1ArtifactError(
                f"Comparison Evidence manifest source_artifacts item {index} role is invalid"
            )
        path_text, path = _require_relative_run_work_path(root, run_id, reference["path"])
        try:
            if path.stat().st_size > _MAX_A1_WORK_ARTIFACT_BYTES:
                raise PhaseA1ArtifactError(
                    f"source artifact exceeds the A1 pilot size limit: {path_text}"
                )
            data = path.read_bytes()
        except OSError as exc:
            raise PhaseA1ArtifactError(f"unable to read source artifact: {path_text}") from exc
        if len(data) > _MAX_A1_WORK_ARTIFACT_BYTES:
            raise PhaseA1ArtifactError(
                f"source artifact exceeds the A1 pilot size limit: {path_text}"
            )
        if type(reference["size_bytes"]) is not int or reference["size_bytes"] != len(data):
            raise PhaseA1ArtifactError(f"source artifact size does not match: {path_text}")
        if reference["sha256"] != _sha256_bytes(data):
            raise PhaseA1ArtifactError(f"source artifact SHA-256 does not match: {path_text}")
        declared_inputs.append(
            {
                "role": role,
                "path": path_text,
                "size_bytes": len(data),
                "sha256": reference["sha256"],
            }
        )
        source_bytes_by_path[path_text] = data
    canonical_inputs = sorted(declared_inputs, key=lambda item: (item["role"], item["path"]))
    if declared_inputs != canonical_inputs:
        raise PhaseA1ArtifactError(
            "Comparison Evidence manifest source_artifacts must use stable sorted order"
        )
    if len({item["path"] for item in declared_inputs}) != len(declared_inputs):
        raise PhaseA1ArtifactError("Comparison Evidence manifest has duplicate source paths")
    for role in _SINGLETON_SOURCE_ROLES:
        if sum(item["role"] == role for item in declared_inputs) != 1:
            raise PhaseA1ArtifactError(
                f"Comparison Evidence manifest must contain exactly one {role}"
            )
    if manifest["source_bundle_kind"] == "run_local_projection":
        _validate_projection_source_set(
            run_id,
            declared_inputs,
            source_bytes_by_path,
        )
    _bind_evidence_provenance(records, declared_inputs)
    _enforce_byte_binding_only_evidence(records)
    return {
        "manifest": manifest,
        "records": records,
        "comparison_evidence_sha256": manifest["comparison_evidence_sha256"],
        "comparison_evidence_path": expected_evidence_relative,
        "comparison_evidence_manifest_path": manifest_relative,
    }


def write_claim_decision_artifact(
    project_root: Path,
    *,
    run_id: str,
    execution_profile: str,
    plan_fingerprint: str,
) -> dict[str, Any]:
    """Build, validate, and atomically write the authoritative Run-local decision."""

    bundle = validate_comparison_evidence_bundle(
        project_root,
        run_id=run_id,
        execution_profile=execution_profile,
        plan_fingerprint=plan_fingerprint,
    )
    try:
        document = build_claim_decision_document(
            bundle["records"],
            run_id=run_id,
            plan_fingerprint=plan_fingerprint,
            source_comparison_evidence_sha256=bundle["comparison_evidence_sha256"],
        )
        validate_claim_decision_document(
            document,
            bundle["records"],
            expected_run_id=run_id,
            expected_plan_fingerprint=plan_fingerprint,
            expected_source_comparison_evidence_sha256=bundle[
                "comparison_evidence_sha256"
            ],
        )
    except ValueError as exc:
        raise PhaseA1ArtifactError(f"unable to build valid ClaimDecision: {exc}") from exc
    _enforce_byte_binding_only_decision(document)
    data = _canonical_json_bytes(document)
    relative_path = _artifact_relative_path(run_id, "claim_decision.json")
    root = Path(project_root).resolve()
    path = _resolve_fixed_path(root, relative_path)
    _preflight_idempotent_target(path, data)
    committed_paths: list[str] = []
    try:
        if _atomic_write_idempotent(path, data, allowed_root=root):
            committed_paths.append(relative_path)
        persisted = load_validated_claim_artifacts(
            project_root,
            run_id=run_id,
            execution_profile=execution_profile,
            plan_fingerprint=plan_fingerprint,
        )
    except Exception as exc:
        if committed_paths or _write_failure_requires_recovery(exc):
            _raise_recovery_required(
                project_root=root,
                run_id=run_id,
                area="artifacts",
                stage="claim_decision_commit",
                committed_paths=committed_paths,
                primary_error=exc,
            )
        raise
    return {
        "claim_decision_path": relative_path,
        "claim_decision_sha256": _sha256_bytes(
            persisted["claim_decision_bytes"]
        ),
        "record_count": len(persisted["records"]),
    }


def load_validated_claim_artifacts(
    project_root: Path,
    *,
    run_id: str,
    execution_profile: str,
    plan_fingerprint: str,
) -> dict[str, Any]:
    """Load Evidence and ClaimDecision only after recomputing their contracts."""

    bundle = validate_comparison_evidence_bundle(
        project_root,
        run_id=run_id,
        execution_profile=execution_profile,
        plan_fingerprint=plan_fingerprint,
    )
    decision_relative = _artifact_relative_path(run_id, "claim_decision.json")
    decision_path = _resolve_fixed_path(Path(project_root).resolve(), decision_relative)
    if not decision_path.is_file() or decision_path.is_symlink():
        raise PhaseA1ArtifactError("claim_decision.json is missing")
    decision_bytes = _read_file_bytes(decision_path, label="ClaimDecision")
    decision = _parse_json_object_bytes(decision_bytes, label="ClaimDecision")
    try:
        validate_claim_decision_document(
            decision,
            bundle["records"],
            expected_run_id=run_id,
            expected_plan_fingerprint=plan_fingerprint,
            expected_source_comparison_evidence_sha256=bundle[
                "comparison_evidence_sha256"
            ],
        )
    except ValueError as exc:
        raise PhaseA1ArtifactError(f"claim_decision.json is invalid: {exc}") from exc
    _enforce_byte_binding_only_decision(decision)
    return {
        **bundle,
        "claim_decision": decision,
        "claim_decision_path": decision_relative,
        "claim_decision_bytes": decision_bytes,
    }


def write_gated_claim_audit_report(
    project_root: Path,
    *,
    run_id: str,
    execution_profile: str,
    plan_fingerprint: str,
) -> dict[str, Any]:
    """Render one controlled, non-published A1 audit report from ClaimDecision."""

    artifacts = load_validated_claim_artifacts(
        project_root,
        run_id=run_id,
        execution_profile=execution_profile,
        plan_fingerprint=plan_fingerprint,
    )
    root = Path(project_root).resolve()
    _reject_recovery_marker(root, run_id, "staging")
    evidence_by_id = {
        record["evidence_id"]: record for record in artifacts["records"]
    }
    lines = [
        "# Phase A1 Claim Audit",
        "",
        "本报告仅来自当前 Run 中经过合同校验的 Comparison Evidence 与 ClaimDecision。",
        "Manifest 仅绑定声明来源字节；当前未验证来源文件的业务语义。",
        "它不是正式发布物，不构成真实身份确认、物理量变化、长期模式或预测结论。",
        "",
    ]
    for decision in artifacts["claim_decision"]["record_decisions"]:
        evidence = evidence_by_id[decision["evidence_id"]]
        lines.extend(
            [
                f"## {decision['current_observation_id']}",
                "",
                f"- 证据状态：{decision['identity_evidence_state']}",
                f"- 纵向可比性：{decision['comparison_comparability_status']}",
                f"- 当前静态面积审计：{evidence['current_value']} px²",
            ]
        )
        difference_status = decision["capabilities"]["descriptive_difference_claim"]
        if difference_status == "allowed_with_limits":
            lines.append(
                f"- 描述性面积差：{evidence['absolute_difference']} px²"
            )
            if evidence["relative_difference"] is not None:
                lines.append(
                    f"- 描述性面积相对差：{evidence['relative_difference']}"
                )
        else:
            lines.append("- 描述性差值结论：未授权")
        if decision["capabilities"]["directional_change_claim"] == "allowed_with_limits":
            lines.append("- 方向性提示：仅在受限 Claim capability 下可用")
        else:
            lines.append("- 方向性变化结论：未授权")
        qualifiers = decision["required_language_qualifiers"]
        if qualifiers:
            lines.append("- 限定语：" + "；".join(qualifiers))
        lines.append("")

    report_data = ("\n".join(lines).rstrip() + "\n").encode("utf-8")
    report_relative = _staging_relative_path(run_id, "claim_audit_report.md")
    report_path = _resolve_fixed_path(root, report_relative)
    mirror_relative = _staging_relative_path(run_id, "claim_decision.json")
    mirror_path = _resolve_fixed_path(root, mirror_relative)
    authoritative_path = _resolve_fixed_path(
        root,
        artifacts["claim_decision_path"],
    )
    _preflight_idempotent_target(report_path, report_data)
    _preflight_idempotent_target(mirror_path, artifacts["claim_decision_bytes"])
    committed_paths: list[str] = []
    final_consistency_check_started = False
    try:
        if _atomic_write_idempotent(
            report_path,
            report_data,
            allowed_root=root,
        ):
            committed_paths.append(report_relative)
        if _atomic_write_idempotent(
            mirror_path,
            artifacts["claim_decision_bytes"],
            allowed_root=root,
        ):
            committed_paths.append(mirror_relative)
        # Point-in-time best-effort check only; A3 owns concurrent-writer fencing.
        final_consistency_check_started = True
        mirror_bytes = _read_file_bytes(
            mirror_path,
            label="Staging ClaimDecision mirror",
        )
        authoritative_bytes = _read_file_bytes(
            authoritative_path,
            label="authoritative ClaimDecision",
        )
        if not (
            authoritative_bytes
            == artifacts["claim_decision_bytes"]
            == mirror_bytes
        ):
            raise PhaseA1ArtifactError(
                "authoritative ClaimDecision, validated snapshot, and Staging "
                "mirror are not byte-identical"
            )
    except Exception as exc:
        if (
            committed_paths
            or _write_failure_requires_recovery(exc)
            or final_consistency_check_started
        ):
            _raise_recovery_required(
                project_root=root,
                run_id=run_id,
                area="staging",
                stage="claim_audit_report_commit",
                committed_paths=committed_paths,
                primary_error=exc,
            )
        raise
    return {
        "claim_audit_report_path": report_relative,
        "claim_decision_mirror_path": mirror_relative,
        "record_count": len(artifacts["records"]),
    }


__all__ = [
    "COMPARISON_EVIDENCE_MANIFEST_SCHEMA_VERSION",
    "PHASE_A1_EXECUTION_PROFILE",
    "PHASE_A1_SANDBOX_MARKER_SCHEMA_VERSION",
    "SOURCE_VALIDATION_SCOPE",
    "PhaseA1ArtifactError",
    "comparison_evidence_csv_bytes",
    "initialize_phase_a1_sandbox",
    "load_validated_claim_artifacts",
    "parse_comparison_evidence_csv",
    "validate_comparison_evidence_bundle",
    "validate_phase_a1_sandbox",
    "write_claim_decision_artifact",
    "write_comparison_evidence_bundle",
    "write_gated_claim_audit_report",
]
