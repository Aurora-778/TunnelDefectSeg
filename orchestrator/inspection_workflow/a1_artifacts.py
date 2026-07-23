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


COMPARISON_EVIDENCE_MANIFEST_SCHEMA_VERSION = "comparison_evidence_manifest_v1"
PHASE_A1_EXECUTION_PROFILE = "phase_a1_sandbox"

_RUN_ID_RE = re.compile(r"run_[0-9]{3,}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE_ARTIFACT_ROLES = {
    "association_artifact",
    "association_manifest",
    "engineering_artifact",
    "memory_snapshot",
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
    "run_id",
    "plan_fingerprint",
    "execution_profile",
    "comparison_evidence_schema_version",
    "comparison_evidence_path",
    "comparison_evidence_size_bytes",
    "comparison_evidence_sha256",
    "record_count",
    "source_artifacts",
}
_SOURCE_REFERENCE_FIELDS = {"role", "path", "size_bytes", "sha256"}


class PhaseA1ArtifactError(ValueError):
    """Raised when a Run-local A1 artifact set is unsafe or inconsistent."""


def validate_phase_a1_sandbox(
    project_root: Path,
    *,
    run_id: str,
    execution_profile: str,
) -> Path:
    """Return the sandbox root after enforcing the A1 activation boundary."""

    if execution_profile != PHASE_A1_EXECUTION_PROFILE:
        raise PhaseA1ArtifactError(
            f"execution_profile must be {PHASE_A1_EXECUTION_PROFILE}"
        )
    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        raise PhaseA1ArtifactError("run_id must use canonical run_NNN format")
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise PhaseA1ArtifactError("Phase A1 project_root must be an existing directory")
    repository_root = Path(__file__).resolve().parents[2]
    if root == repository_root or repository_root in root.parents:
        raise PhaseA1ArtifactError(
            "Phase A1 sandbox must use a temporary project root outside the live repository"
        )
    return root


def _require_sha256(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise PhaseA1ArtifactError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _artifact_relative_path(run_id: str, filename: str) -> str:
    return f"runs/{run_id}/artifacts/{filename}"


def _staging_relative_path(run_id: str, filename: str) -> str:
    return f"runs/{run_id}/staging/{filename}"


def _resolve_fixed_path(project_root: Path, relative_path: str) -> Path:
    path = project_root.joinpath(*PurePosixPath(relative_path).parts)
    resolved_parent = path.parent.resolve()
    try:
        resolved_parent.relative_to(project_root.resolve())
    except ValueError as exc:
        raise PhaseA1ArtifactError(f"artifact parent resolves outside its fixed Run path: {relative_path}")
    lexical_parent = project_root
    for part in PurePosixPath(relative_path).parent.parts:
        lexical_parent /= part
        if lexical_parent.exists() and lexical_parent.is_symlink():
            raise PhaseA1ArtifactError(
                f"artifact parent must not use symbolic links: {relative_path}"
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


def _atomic_write_idempotent(path: Path, data: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PhaseA1ArtifactError(
            f"unable to create A1 artifact directory for: {path.name}"
        ) from exc
    _preflight_idempotent_target(path, data)
    if path.exists():
        return

    temporary_path: Path | None = None
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
        os.replace(temporary_path, path)
        temporary_path = None
    except OSError as exc:
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
        raise PhaseA1ArtifactError(message) from exc


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
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise PhaseA1ArtifactError(
                f"comparison_evidence.csv row {row_number} {field} must be a JSON list"
            ) from exc
        if not isinstance(parsed, list):
            raise PhaseA1ArtifactError(
                f"comparison_evidence.csv row {row_number} {field} must be a JSON list"
            )
        return parsed
    if field in _BOOLEAN_FIELDS:
        return _parse_bool(value, field=field, row_number=row_number, optional=False)
    if field in _OPTIONAL_BOOLEAN_FIELDS:
        return _parse_bool(value, field=field, row_number=row_number, optional=True)
    if field in _INTEGER_FIELDS:
        if value == "":
            return None
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
        return validate_comparison_evidence_records(rows)
    except ValueError as exc:
        raise PhaseA1ArtifactError(f"comparison_evidence.csv is invalid: {exc}") from exc


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
    cursor = project_root
    for part in pure_path.parts:
        cursor /= part
        if cursor.exists() and cursor.is_symlink():
            raise PhaseA1ArtifactError(
                f"source artifact path must not use symbolic links: {value}"
            )
    if path.is_symlink() or not path.is_file():
        raise PhaseA1ArtifactError(f"source artifact must be an existing regular file: {value}")
    resolved = path.resolve()
    work_root = project_root.joinpath("runs", run_id, "work").resolve()
    try:
        resolved.relative_to(work_root)
    except ValueError as exc:
        raise PhaseA1ArtifactError(f"source artifact resolves outside Run work: {value}") from exc
    return pure_path.as_posix(), resolved


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
        if not isinstance(reference, Mapping) or set(reference) != {"role", "path"}:
            raise PhaseA1ArtifactError(
                f"source_artifacts item {index} must contain exactly role and path"
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
            data = path.read_bytes()
        except OSError as exc:
            raise PhaseA1ArtifactError(f"unable to read source artifact: {path_text}") from exc
        snapshots.append(
            {
                "role": role,
                "path": path_text,
                "size_bytes": len(data),
                "sha256": _sha256_bytes(data),
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


def write_comparison_evidence_bundle(
    project_root: Path,
    *,
    run_id: str,
    execution_profile: str,
    plan_fingerprint: str,
    records: Iterable[Mapping[str, Any]],
    source_artifacts: Any,
) -> dict[str, Any]:
    """Write validated Evidence CSV and a manifest-last Run-local bundle."""

    root = validate_phase_a1_sandbox(
        project_root,
        run_id=run_id,
        execution_profile=execution_profile,
    )
    _require_sha256(plan_fingerprint, field="plan_fingerprint")
    evidence_bytes, validated = comparison_evidence_csv_bytes(records)
    sources = _snapshot_source_artifacts(root, run_id, source_artifacts)
    _bind_evidence_provenance(validated, sources)

    evidence_relative = _artifact_relative_path(run_id, "comparison_evidence.csv")
    evidence_path = _resolve_fixed_path(root, evidence_relative)
    manifest_relative = _artifact_relative_path(
        run_id,
        "comparison_evidence_manifest.json",
    )
    manifest_path = _resolve_fixed_path(root, manifest_relative)
    manifest = {
        "schema_version": COMPARISON_EVIDENCE_MANIFEST_SCHEMA_VERSION,
        "run_id": run_id,
        "plan_fingerprint": plan_fingerprint,
        "execution_profile": execution_profile,
        "comparison_evidence_schema_version": COMPARISON_EVIDENCE_SCHEMA_VERSION,
        "comparison_evidence_path": evidence_relative,
        "comparison_evidence_size_bytes": len(evidence_bytes),
        "comparison_evidence_sha256": _sha256_bytes(evidence_bytes),
        "record_count": len(validated),
        "source_artifacts": sources,
    }
    manifest_bytes = _canonical_json_bytes(manifest)

    _preflight_idempotent_target(evidence_path, evidence_bytes)
    _preflight_idempotent_target(manifest_path, manifest_bytes)
    _atomic_write_idempotent(evidence_path, evidence_bytes)
    _atomic_write_idempotent(manifest_path, manifest_bytes)
    committed = validate_comparison_evidence_bundle(
        root,
        run_id=run_id,
        execution_profile=execution_profile,
        plan_fingerprint=plan_fingerprint,
    )
    return {
        "comparison_evidence_path": evidence_relative,
        "comparison_evidence_manifest_path": manifest_relative,
        "comparison_evidence_sha256": committed["comparison_evidence_sha256"],
        "record_count": len(committed["records"]),
    }


def _parse_json_object_bytes(data: bytes, *, label: str) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise PhaseA1ArtifactError(f"{label} contains duplicate key: {key}")
            result[key] = value
        return result

    try:
        payload = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
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
    _require_sha256(plan_fingerprint, field="plan_fingerprint")
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
    if not isinstance(references, list):
        raise PhaseA1ArtifactError("Comparison Evidence manifest source_artifacts must be a list")
    declared_inputs = []
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
            data = path.read_bytes()
        except OSError as exc:
            raise PhaseA1ArtifactError(f"unable to read source artifact: {path_text}") from exc
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
    _bind_evidence_provenance(records, declared_inputs)
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
    data = _canonical_json_bytes(document)
    relative_path = _artifact_relative_path(run_id, "claim_decision.json")
    path = _resolve_fixed_path(Path(project_root).resolve(), relative_path)
    _atomic_write_idempotent(path, data)
    persisted = load_validated_claim_artifacts(
        project_root,
        run_id=run_id,
        execution_profile=execution_profile,
        plan_fingerprint=plan_fingerprint,
    )
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
    evidence_by_id = {
        record["evidence_id"]: record for record in artifacts["records"]
    }
    lines = [
        "# Phase A1 Claim Audit",
        "",
        "本报告仅来自当前 Run 的已验证 Comparison Evidence 与 ClaimDecision。",
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
    root = Path(project_root).resolve()
    report_relative = _staging_relative_path(run_id, "claim_audit_report.md")
    report_path = _resolve_fixed_path(root, report_relative)
    mirror_relative = _staging_relative_path(run_id, "claim_decision.json")
    mirror_path = _resolve_fixed_path(root, mirror_relative)
    _preflight_idempotent_target(report_path, report_data)
    _preflight_idempotent_target(mirror_path, artifacts["claim_decision_bytes"])
    _atomic_write_idempotent(report_path, report_data)
    _atomic_write_idempotent(mirror_path, artifacts["claim_decision_bytes"])
    if mirror_path.read_bytes() != artifacts["claim_decision_bytes"]:
        raise PhaseA1ArtifactError("Staging ClaimDecision mirror is not byte-identical")
    return {
        "claim_audit_report_path": report_relative,
        "claim_decision_mirror_path": mirror_relative,
        "record_count": len(artifacts["records"]),
    }


__all__ = [
    "COMPARISON_EVIDENCE_MANIFEST_SCHEMA_VERSION",
    "PHASE_A1_EXECUTION_PROFILE",
    "PhaseA1ArtifactError",
    "comparison_evidence_csv_bytes",
    "load_validated_claim_artifacts",
    "parse_comparison_evidence_csv",
    "validate_comparison_evidence_bundle",
    "validate_phase_a1_sandbox",
    "write_claim_decision_artifact",
    "write_comparison_evidence_bundle",
    "write_gated_claim_audit_report",
]
