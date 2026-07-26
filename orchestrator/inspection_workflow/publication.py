"""Phase A2 Run-local publication transaction.

This module deliberately stays below the A3 workflow boundary.  It owns one
deterministic publication transaction inside a validated temporary sandbox; it
does not create locks, StateStore records, or a second scheduler.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from typing import Any

from . import a1_artifacts, a1_reports
from .a1_artifacts import PhaseA1ArtifactError


PUBLICATION_MANIFEST_SCHEMA_VERSION = "publication_manifest_v1"
PUBLICATION_TRANSACTION_SCHEMA_VERSION = "publication_transaction_v1"
PUBLICATION_RECOVERY_MARKER_SCHEMA_VERSION = "publication_recovery_marker_v1"
PUBLICATION_EXECUTION_PROFILE = a1_artifacts.PHASE_A1_EXECUTION_PROFILE

PUBLICATION_MANIFEST_PATH = "outputs/current_publication_manifest.json"
PUBLICATION_TRANSACTION_PATH_TEMPLATE = "runs/{run_id}/publication_transaction.json"
PUBLICATION_RECOVERY_MARKER_TEMPLATE = "runs/{run_id}/.publication_recovery_required.json"
FINAL_SUMMARY_PATH_TEMPLATE = "runs/{run_id}/final_summary.md"

_FINAL_PATHS = (
    "outputs/final_project_report.md",
    "outputs/system_summary.md",
    "outputs/key_insights.md",
)
_REQUIRED_STAGING_FILES = (
    "disease_growth_analysis_report.md",
    "disease_growth_analysis_summary.md",
    "memory_agent_report.md",
    "disease_memory_bank_summary.md",
    "disease_engineering_report.md",
    "disease_engineering_report_summary.md",
    "priority_recheck_list.csv",
    "visualization_report.md",
    "visualization_summary.md",
    "recheck_list_report.md",
)
_REQUIRED_CHARTS = (
    "visualizations/comparability_status_distribution.png",
    "visualizations/static_area_audit.png",
    "visualizations/static_audit_status_distribution.png",
)
_LEGACY_REPORT_FIELDS = (
    "growth_trend",
    "growth_description",
    "area_growth_rate",
    "risk_level_change",
    "memory_description",
)
_TX_PHASES = {
    "backup_ready",
    "publishing",
    "manifest_commit_intent",
    "manifest_committed",
    "cleanup_pending",
    "cleanup_complete",
}
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_RECOVERY_MARKER_FIELDS = {
    "schema_version",
    "run_id",
    "stage",
    "committed_paths",
    "primary_error_type",
    "primary_error",
    "cleanup_error",
    "invalidated_final_summary_path",
}
_MANIFEST_FIELDS = {
    "schema_version",
    "run_id",
    "plan_fingerprint",
    "execution_profile",
    "transaction_id",
    "source_validation_scope",
    "publication_manifest_path",
    "publication_transaction_path",
    "final_summary_path",
    "expected_source_artifact_paths",
    "source_artifacts",
    "publication_files",
}
_SOURCE_REFERENCE_FIELDS = {"kind", "path", "size_bytes", "sha256"}
_PUBLICATION_REFERENCE_FIELDS = {"path", "size_bytes", "sha256"}
_TRANSACTION_FIELDS = {
    "schema_version",
    "run_id",
    "plan_fingerprint",
    "execution_profile",
    "transaction_id",
    "phase",
    "manifest_path",
    "manifest_sha256",
    "backup_dir",
    "temporary_dir",
    "target_records",
    "committed_paths",
}
_TRANSACTION_OPTIONAL_FIELDS = {"cleanup_error"}
_TARGET_RECORD_FIELDS = {
    "path",
    "existed_before",
    "old_sha256",
    "new_sha256",
}


class PublicationTransactionError(PhaseA1ArtifactError):
    """Raised when a Run-local publication cannot be safely completed."""


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PublicationTransactionError("publication JSON is not canonicalizable") from exc
    return (text + "\n").encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _relative_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise PublicationTransactionError(f"{label} must be a project-relative POSIX path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise PublicationTransactionError(f"{label} must be a project-relative POSIX path")
    return value


def _path(root: Path, relative: str, *, label: str, include_leaf: bool = True) -> Path:
    relative = _relative_path(relative, label=label)
    result = root.joinpath(*PurePosixPath(relative).parts)
    try:
        a1_artifacts._assert_path_is_contained_and_plain(
            root,
            result,
            include_leaf=include_leaf,
            label=label,
        )
    except PhaseA1ArtifactError as exc:
        raise PublicationTransactionError(str(exc)) from exc
    return result


def _lstat_sentinel(path: Path, *, label: str) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except (OSError, ValueError) as exc:
        raise PublicationTransactionError(f"unable to inspect {label}") from exc
    return True


def _recovery_marker_path(root: Path, run_id: str) -> Path:
    return _path(
        root,
        PUBLICATION_RECOVERY_MARKER_TEMPLATE.format(run_id=run_id),
        label="publication recovery marker",
        include_leaf=False,
    )


def _reject_recovery_marker(root: Path, run_id: str) -> None:
    marker = _recovery_marker_path(root, run_id)
    if _lstat_sentinel(marker, label="publication recovery marker"):
        raise PublicationTransactionError(
            "publication recovery marker exists; inspect the transaction before retrying"
        )


def _read_file(path: Path, *, label: str) -> bytes:
    try:
        entry = path.lstat()
    except FileNotFoundError as exc:
        raise PublicationTransactionError(f"missing {label}: {path}") from exc
    except (OSError, ValueError) as exc:
        raise PublicationTransactionError(f"unable to inspect {label}: {path}") from exc
    if not stat.S_ISREG(entry.st_mode) or stat.S_ISLNK(entry.st_mode):
        raise PublicationTransactionError(f"{label} must be a regular file: {path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise PublicationTransactionError(f"unable to read {label}: {path}") from exc


def _atomic_replace(path: Path, data: bytes, *, root: Path, label: str) -> None:
    """Replace one plain file and retain whether replace state became uncertain."""

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        a1_artifacts._assert_path_is_contained_and_plain(
            root, path, include_leaf=True, label=label
        )
    except OSError as exc:
        raise PublicationTransactionError(f"unable to create {label} parent") from exc
    except PhaseA1ArtifactError as exc:
        raise PublicationTransactionError(str(exc)) from exc

    temporary: Path | None = None
    replace_attempted = False
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        a1_artifacts._assert_path_is_contained_and_plain(
            root, path, include_leaf=True, label=label
        )
        replace_attempted = True
        os.replace(temporary, path)
        temporary = None
    except (OSError, PhaseA1ArtifactError) as exc:
        cleanup_error: OSError | None = None
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                cleanup_error = cleanup_exc
        message = f"unable to publish {label}"
        if cleanup_error is not None:
            message += (
                "; temporary cleanup also failed: "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
            )
        error = PublicationTransactionError(message)
        error.write_state_uncertain = replace_attempted or cleanup_error is not None
        error.cleanup_error = cleanup_error
        raise error from exc


def _sync_directory(path: Path, *, label: str) -> None:
    """Synchronize directory metadata where the Python runtime exposes it."""

    if os.name == "nt":
        # CPython cannot open Windows directories with os.open(), and
        # FlushFileBuffers rejects directory handles. Atomic same-volume
        # replacement remains the strongest portable A2 sandbox guarantee;
        # A3 must supply platform fencing before formal publication is enabled.
        return

    try:
        descriptor = os.open(str(path), os.O_RDONLY)
    except OSError as exc:
        raise PublicationTransactionError(f"unable to open {label} for durability sync") from exc
    sync_error: OSError | None = None
    close_error: OSError | None = None
    try:
        os.fsync(descriptor)
    except OSError as exc:
        sync_error = exc
    try:
        os.close(descriptor)
    except OSError as exc:
        close_error = exc
    if sync_error is not None:
        message = f"unable to sync {label}"
        if close_error is not None:
            message += f"; descriptor close also failed: {close_error}"
        raise PublicationTransactionError(message) from sync_error
    if close_error is not None:
        raise PublicationTransactionError(f"unable to close {label} after sync") from close_error


def _write_transaction(root: Path, relative: str, document: Mapping[str, Any]) -> bytes:
    data = _canonical_json_bytes(document)
    transaction_path = _path(root, relative, label="publication transaction")
    _atomic_replace(
        transaction_path,
        data,
        root=root,
        label="publication transaction",
    )
    _sync_directory(transaction_path.parent, label="publication transaction parent")
    return data


def _load_transaction(root: Path, relative: str) -> dict[str, Any]:
    data = _read_file(_path(root, relative, label="publication transaction"), label="publication transaction")
    try:
        document = json.loads(
            data.decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise PublicationTransactionError("publication transaction is invalid JSON") from exc
    if not isinstance(document, dict) or _canonical_json_bytes(document) != data:
        raise PublicationTransactionError("publication transaction must use canonical JSON")
    if document.get("schema_version") != PUBLICATION_TRANSACTION_SCHEMA_VERSION:
        raise PublicationTransactionError("publication transaction schema_version is invalid")
    if not _TRANSACTION_FIELDS.issubset(document) or not set(document).issubset(
        _TRANSACTION_FIELDS | _TRANSACTION_OPTIONAL_FIELDS
    ):
        raise PublicationTransactionError("publication transaction fields are invalid")
    if document.get("phase") not in _TX_PHASES:
        raise PublicationTransactionError("publication transaction phase is invalid")
    target_records = document.get("target_records")
    committed_paths = document.get("committed_paths")
    if not isinstance(target_records, list) or not isinstance(committed_paths, list):
        raise PublicationTransactionError("publication transaction file lists are invalid")
    seen_targets: set[str] = set()
    for record in target_records:
        if not isinstance(record, Mapping) or set(record) != _TARGET_RECORD_FIELDS:
            raise PublicationTransactionError("publication transaction target record is invalid")
        target = _relative_path(record["path"], label="publication transaction target")
        if target in seen_targets:
            raise PublicationTransactionError("publication transaction target paths must be unique")
        seen_targets.add(target)
        if type(record["existed_before"]) is not bool:
            raise PublicationTransactionError("publication transaction existed_before must be boolean")
        if record["old_sha256"] is not None and (
            not isinstance(record["old_sha256"], str)
            or _SHA256_RE.fullmatch(record["old_sha256"]) is None
        ):
            raise PublicationTransactionError("publication transaction old SHA-256 is invalid")
        if (
            not isinstance(record["new_sha256"], str)
            or _SHA256_RE.fullmatch(record["new_sha256"]) is None
        ):
            raise PublicationTransactionError("publication transaction new SHA-256 is invalid")
    if any(not isinstance(path, str) or path not in seen_targets for path in committed_paths):
        raise PublicationTransactionError("publication transaction committed_paths are invalid")
    return document


def _snapshot_staging(root: Path, run_id: str) -> dict[str, bytes]:
    try:
        a1_artifacts._reject_recovery_marker(root, run_id, "staging")
    except PhaseA1ArtifactError as exc:
        raise PublicationTransactionError(str(exc)) from exc
    staging_relative = f"runs/{run_id}/staging"
    staging = _path(root, staging_relative, label="Run staging directory")
    if not staging.is_dir() or staging.is_symlink():
        raise PublicationTransactionError("Run staging directory is missing")
    files: dict[str, bytes] = {}
    for candidate in sorted(staging.rglob("*")):
        relative = candidate.relative_to(root).as_posix()
        _path(root, relative, label=f"staging path {relative}")
        try:
            entry = candidate.lstat()
        except (OSError, ValueError) as exc:
            raise PublicationTransactionError(f"unable to inspect staging path: {relative}") from exc
        if stat.S_ISLNK(entry.st_mode) or (
            os.name == "nt"
            and bool(getattr(entry, "st_file_attributes", 0) & 0x0400)
        ):
            raise PublicationTransactionError(f"staging path must not be a symlink or reparse point: {relative}")
        if stat.S_ISDIR(entry.st_mode):
            continue
        if not stat.S_ISREG(entry.st_mode):
            raise PublicationTransactionError(f"staging path must be a regular file: {relative}")
        files[relative] = _read_file(candidate, label=f"staging file {relative}")

    names = {PurePosixPath(path).relative_to(PurePosixPath(staging_relative)).as_posix() for path in files}
    missing = sorted(set(_REQUIRED_STAGING_FILES) - names)
    missing_charts = sorted(set(_REQUIRED_CHARTS) - names)
    if missing:
        raise PublicationTransactionError(
            "Run staging is incomplete; missing required files: " + ", ".join(missing)
        )
    if missing_charts:
        raise PublicationTransactionError(
            "Run staging visualizations are incomplete; missing: " + ", ".join(missing_charts)
        )
    if not any(path.startswith(f"{staging_relative}/visualizations/") for path in files):
        raise PublicationTransactionError("Run staging visualizations must not be empty")
    for path, data in files.items():
        if path.endswith((".md", ".csv")):
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise PublicationTransactionError(f"staging report must be UTF-8: {path}") from exc
            for forbidden in _LEGACY_REPORT_FIELDS:
                if forbidden in text:
                    raise PublicationTransactionError(
                        f"staging report contains unvalidated legacy field {forbidden}: {path}"
                    )
    return dict(sorted(files.items()))


def _source_entries(
    root: Path,
    run_id: str,
    inputs: Mapping[str, Any],
    staging: Mapping[str, bytes],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for reference in inputs["source_artifacts"]:
        path = reference["path"]
        snapshot = a1_artifacts.snapshot_phase_a1_work_artifact(
            root,
            run_id=run_id,
            execution_profile=PUBLICATION_EXECUTION_PROFILE,
            relative_path=path,
        )
        if snapshot["size_bytes"] != reference["size_bytes"] or snapshot["sha256"] != reference["sha256"]:
            raise PublicationTransactionError(f"A1 source changed during publication validation: {path}")
        entries.append({"kind": reference["role"], "path": path, "size_bytes": snapshot["size_bytes"], "sha256": snapshot["sha256"]})

    fixed_artifacts = (
        (inputs["comparison_evidence_path"], inputs["comparison_evidence_bytes"], "comparison_evidence"),
        (inputs["comparison_evidence_manifest_path"], inputs["comparison_evidence_manifest_bytes"], "comparison_evidence_manifest"),
        (inputs["claim_decision_path"], inputs["claim_decision_bytes"], "claim_decision"),
    )
    for path, data, kind in fixed_artifacts:
        entries.append({"kind": kind, "path": path, "size_bytes": len(data), "sha256": _sha256(data)})
    for path, data in staging.items():
        entries.append({"kind": "staging_visualization" if "/visualizations/" in path else "staging_report", "path": path, "size_bytes": len(data), "sha256": _sha256(data)})
    entries.sort(key=lambda item: (item["path"], item["kind"]))
    if len({item["path"] for item in entries}) != len(entries):
        raise PublicationTransactionError("publication source artifact paths must be unique")
    return entries


def _load_a1_inputs(
    root: Path,
    *,
    run_id: str,
    execution_profile: str,
    plan_fingerprint: str,
) -> dict[str, Any]:
    try:
        return a1_reports._load_report_inputs(
            root,
            run_id=run_id,
            execution_profile=execution_profile,
            plan_fingerprint=plan_fingerprint,
        )
    except PhaseA1ArtifactError as exc:
        raise PublicationTransactionError(f"A1 publication inputs are invalid: {exc}") from exc


def _build_final_documents(
    run_id: str,
    transaction_id: str,
    staging: Mapping[str, bytes],
) -> dict[str, bytes]:
    markdown_parts: list[str] = [
        "# TunnelDefect Run-local Final Project Report",
        "",
        "本报告由当前 Run 已通过 Claim Gate 的 staging 产物生成。",
        "来源范围为 byte_binding_only，不构成来源认证或真实纵向工程结论。",
        "",
    ]
    for path, data in staging.items():
        if not path.endswith(".md"):
            continue
        markdown_parts.extend(
            [f"## Source: {PurePosixPath(path).relative_to(PurePosixPath(f'runs/{run_id}/staging')).as_posix()}", "", data.decode("utf-8").rstrip(), ""]
        )
    final_project = ("\n".join(markdown_parts).rstrip() + "\n").encode("utf-8")
    system_summary = (
        "# TunnelDefect Run Summary\n\n"
        "- publication_scope：Run-local A2 sandbox only\n"
        f"- run_id：{run_id}\n"
        f"- transaction_id：{transaction_id}\n"
        f"- staging_file_count：{len(staging)}\n"
        "- claim_scope：static audit and Claim Gate status only\n"
        "- source_validation_scope：byte_binding_only\n"
        "- A3 workflow integration：not enabled\n"
    ).encode("utf-8")
    key_insights = (
        "# Claim-gated Run Insights\n\n"
        "- 当前输出仅反映已验证的静态审计状态。\n"
        "- 不构成方向性变化、物理量变化、多时点模式或预测结论。\n"
        "- 需要进一步使用时，应先完成人工复核和后续阶段的来源语义验证。\n"
        "- final_summary 是本 transaction 的审计摘要，不替代 A3 状态完成证明。\n"
    ).encode("utf-8")
    final_summary_relative = FINAL_SUMMARY_PATH_TEMPLATE.format(run_id=run_id)
    final_summary = (
        "# Publication Transaction Summary\n\n"
        f"- run_id：{run_id}\n"
        f"- transaction_id：{transaction_id}\n"
        "- publication_state：manifest_commit_candidate\n"
        "- claim_scope：static audit and Claim Gate status only\n"
        "- source_validation_scope：byte_binding_only\n"
        "- completion_authority：current_publication_manifest.json\n"
        "- boundary：当前证据不可纵向比较，仅允许静态描述审计，不构成方向性变化结论。\n"
    ).encode("utf-8")
    return {
        _FINAL_PATHS[0]: final_project,
        _FINAL_PATHS[1]: system_summary,
        _FINAL_PATHS[2]: key_insights,
        final_summary_relative: final_summary,
    }


def _transaction_id(run_id: str, plan_fingerprint: str, entries: Iterable[Mapping[str, Any]]) -> str:
    payload = {"run_id": run_id, "plan_fingerprint": plan_fingerprint, "entries": list(entries)}
    return "pub_" + _sha256(_canonical_json_bytes(payload))[:24]


def _manifest_document(
    *,
    root: Path,
    run_id: str,
    plan_fingerprint: str,
    transaction_id: str,
    source_entries: list[dict[str, Any]],
    final_documents: Mapping[str, bytes],
) -> dict[str, Any]:
    publication_files = [
        {"path": path, "size_bytes": len(data), "sha256": _sha256(data)}
        for path, data in sorted(final_documents.items())
    ]
    final_summary_path = FINAL_SUMMARY_PATH_TEMPLATE.format(run_id=run_id)
    final_summary_entry = next(item for item in publication_files if item["path"] == final_summary_path)
    source_entries = list(source_entries) + [{"kind": "final_summary", **final_summary_entry}]
    source_entries.sort(key=lambda item: (item["path"], item["kind"]))
    return {
        "schema_version": PUBLICATION_MANIFEST_SCHEMA_VERSION,
        "run_id": run_id,
        "plan_fingerprint": plan_fingerprint,
        "execution_profile": PUBLICATION_EXECUTION_PROFILE,
        "transaction_id": transaction_id,
        "source_validation_scope": a1_artifacts.SOURCE_VALIDATION_SCOPE,
        "publication_manifest_path": PUBLICATION_MANIFEST_PATH,
        "publication_transaction_path": PUBLICATION_TRANSACTION_PATH_TEMPLATE.format(run_id=run_id),
        "final_summary_path": final_summary_path,
        "expected_source_artifact_paths": [item["path"] for item in source_entries],
        "source_artifacts": source_entries,
        "publication_files": publication_files,
    }


def _target_records(root: Path, target_bytes: Mapping[str, bytes]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path, data in sorted(target_bytes.items()):
        target = _path(root, path, label=f"publication target {path}")
        existed = False
        old_sha = None
        if target.exists() or target.is_symlink():
            old = _read_file(target, label=f"existing publication target {path}")
            existed = True
            old_sha = _sha256(old)
            if old != data:
                raise PublicationTransactionError(
                    f"existing publication target differs; refusing to overwrite: {path}"
                )
        records.append({"path": path, "existed_before": existed, "old_sha256": old_sha, "new_sha256": _sha256(data)})
    return records


def _write_recovery_marker(
    root: Path,
    run_id: str,
    *,
    stage: str,
    committed_paths: Iterable[str],
    primary_error: Exception,
    cleanup_error: Exception | None = None,
    invalidated_final_summary_path: str | None = None,
) -> None:
    marker = {
        "schema_version": PUBLICATION_RECOVERY_MARKER_SCHEMA_VERSION,
        "run_id": run_id,
        "stage": stage,
        "committed_paths": sorted(set(committed_paths)),
        "primary_error_type": type(primary_error).__name__,
        "primary_error": str(primary_error),
        "cleanup_error": None if cleanup_error is None else f"{type(cleanup_error).__name__}: {cleanup_error}",
        "invalidated_final_summary_path": invalidated_final_summary_path,
    }
    marker_path = _recovery_marker_path(root, run_id)
    try:
        _atomic_replace(marker_path, _canonical_json_bytes(marker), root=root, label="publication recovery marker")
    except Exception as marker_error:
        raise PublicationTransactionError(
            f"{primary_error}; recovery marker write also failed: {marker_error}"
        ) from (primary_error.__cause__ or primary_error)


def _load_recovery_marker(root: Path, run_id: str) -> dict[str, Any]:
    path = _recovery_marker_path(root, run_id)
    data = _read_file(path, label="publication recovery marker")
    try:
        marker = json.loads(
            data.decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise PublicationTransactionError("publication recovery marker is invalid JSON") from exc
    if (
        not isinstance(marker, dict)
        or set(marker) != _RECOVERY_MARKER_FIELDS
        or _canonical_json_bytes(marker) != data
    ):
        raise PublicationTransactionError("publication recovery marker is invalid")
    if marker["schema_version"] != PUBLICATION_RECOVERY_MARKER_SCHEMA_VERSION:
        raise PublicationTransactionError("publication recovery marker schema_version is invalid")
    if marker["run_id"] != run_id:
        raise PublicationTransactionError("publication recovery marker run_id does not match")
    paths = marker["committed_paths"]
    if not isinstance(paths, list) or any(not isinstance(item, str) for item in paths):
        raise PublicationTransactionError("publication recovery marker committed_paths are invalid")
    if paths != sorted(set(paths)):
        raise PublicationTransactionError("publication recovery marker committed_paths are not canonical")
    if marker["invalidated_final_summary_path"] is not None:
        _relative_path(
            marker["invalidated_final_summary_path"],
            label="invalidated final_summary path",
        )
    return marker


def _remove_path(path: Path, *, label: str) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()
    except OSError as exc:
            raise PublicationTransactionError(f"unable to clean {label}") from exc


def _isolate_final_summary(
    root: Path,
    *,
    run_id: str,
    transaction_id: str,
) -> str:
    source_relative = FINAL_SUMMARY_PATH_TEMPLATE.format(run_id=run_id)
    destination_relative = (
        f"runs/{run_id}/staging/invalidated_final_summary.{transaction_id}.md"
    )
    source = _path(root, source_relative, label="published final_summary")
    destination = _path(root, destination_relative, label="invalidated final_summary")
    if not source.exists():
        return destination_relative
    if destination.exists() or destination.is_symlink():
        raise PublicationTransactionError(
            "transaction-scoped invalidated final_summary already exists"
        )
    try:
        os.replace(source, destination)
    except OSError as exc:
        raise PublicationTransactionError(
            "unable to isolate failed final_summary"
        ) from exc
    return destination_relative


def _validate_manifest_files(root: Path, manifest: Mapping[str, Any]) -> None:
    if set(manifest) != _MANIFEST_FIELDS:
        raise PublicationTransactionError("publication manifest fields are invalid")
    if manifest.get("schema_version") != PUBLICATION_MANIFEST_SCHEMA_VERSION:
        raise PublicationTransactionError("publication manifest schema_version is invalid")
    if manifest.get("source_validation_scope") != a1_artifacts.SOURCE_VALIDATION_SCOPE:
        raise PublicationTransactionError("publication manifest source_validation_scope is invalid")
    source_entries = manifest.get("source_artifacts")
    publication_files = manifest.get("publication_files")
    if not isinstance(source_entries, list) or not isinstance(publication_files, list):
        raise PublicationTransactionError("publication manifest file collections are invalid")
    if manifest.get("publication_manifest_path") != PUBLICATION_MANIFEST_PATH:
        raise PublicationTransactionError("publication manifest path is not fixed")
    expected_transaction_path = PUBLICATION_TRANSACTION_PATH_TEMPLATE.format(
        run_id=manifest.get("run_id")
    )
    if manifest.get("publication_transaction_path") != expected_transaction_path:
        raise PublicationTransactionError("publication transaction path is not fixed")
    expected_final_summary = FINAL_SUMMARY_PATH_TEMPLATE.format(run_id=manifest.get("run_id"))
    if manifest.get("final_summary_path") != expected_final_summary:
        raise PublicationTransactionError("publication final_summary path is not fixed")
    seen: set[str] = set()
    for collection_name, collection, expected_fields in (
        ("source_artifacts", source_entries, _SOURCE_REFERENCE_FIELDS),
        ("publication_files", publication_files, _PUBLICATION_REFERENCE_FIELDS),
    ):
        for item in collection:
            if not isinstance(item, Mapping) or set(item) != expected_fields:
                raise PublicationTransactionError(
                    f"publication manifest {collection_name} reference is invalid"
                )
            if collection_name == "source_artifacts" and (
                not isinstance(item["kind"], str) or not item["kind"]
            ):
                raise PublicationTransactionError("publication source artifact kind is invalid")
            path_text = _relative_path(item["path"], label="publication manifest path")
            if path_text in seen and path_text not in {manifest.get("final_summary_path")}:
                raise PublicationTransactionError(f"publication manifest contains duplicate path: {path_text}")
            seen.add(path_text)
            path = _path(root, path_text, label=f"publication file {path_text}")
            data = _read_file(path, label=f"publication file {path_text}")
            if type(item["size_bytes"]) is not int or item["size_bytes"] != len(data):
                raise PublicationTransactionError(f"publication file size does not match: {path_text}")
            if item["sha256"] != _sha256(data):
                raise PublicationTransactionError(f"publication file SHA-256 does not match: {path_text}")
    expected = manifest.get("expected_source_artifact_paths")
    actual_source_paths = [item["path"] for item in source_entries]
    if expected != actual_source_paths or actual_source_paths != sorted(actual_source_paths):
        raise PublicationTransactionError("publication source artifact path set is not stable")
    if manifest.get("final_summary_path") not in actual_source_paths:
        raise PublicationTransactionError("publication manifest must include final_summary as a source artifact")
    actual_publication_paths = [item["path"] for item in publication_files]
    expected_publication_paths = sorted(
        [*_FINAL_PATHS, FINAL_SUMMARY_PATH_TEMPLATE.format(run_id=manifest["run_id"])]
    )
    if actual_publication_paths != expected_publication_paths:
        raise PublicationTransactionError("publication file path set is incomplete")


def validate_publication(
    project_root: Path,
    *,
    run_id: str,
    execution_profile: str = PUBLICATION_EXECUTION_PROFILE,
    plan_fingerprint: str,
    _allow_recovery_state: bool = False,
) -> dict[str, Any]:
    root = a1_artifacts.validate_phase_a1_sandbox(
        project_root, run_id=run_id, execution_profile=execution_profile
    )
    if not _allow_recovery_state:
        _reject_recovery_marker(root, run_id)
    manifest_relative = PUBLICATION_MANIFEST_PATH
    manifest_path = _path(root, manifest_relative, label="publication manifest")
    data = _read_file(manifest_path, label="publication manifest")
    try:
        manifest = json.loads(data.decode("utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise PublicationTransactionError("publication manifest is invalid JSON") from exc
    if not isinstance(manifest, dict) or _canonical_json_bytes(manifest) != data:
        raise PublicationTransactionError("publication manifest must use canonical JSON")
    if manifest.get("run_id") != run_id or manifest.get("plan_fingerprint") != plan_fingerprint:
        raise PublicationTransactionError("publication manifest identity does not match the requested Run")
    inputs = _load_a1_inputs(
        root,
        run_id=run_id,
        execution_profile=execution_profile,
        plan_fingerprint=plan_fingerprint,
    )
    staging = _snapshot_staging(root, run_id)
    source_entries = _source_entries(root, run_id, inputs, staging)
    expected_transaction_id = _transaction_id(
        run_id,
        plan_fingerprint,
        [
            {key: item[key] for key in ("kind", "path", "size_bytes", "sha256")}
            for item in source_entries
        ],
    )
    if manifest.get("transaction_id") != expected_transaction_id:
        raise PublicationTransactionError("publication transaction_id does not match current A1 sources")
    final_documents = _build_final_documents(run_id, expected_transaction_id, staging)
    expected_manifest = _manifest_document(
        root=root,
        run_id=run_id,
        plan_fingerprint=plan_fingerprint,
        transaction_id=expected_transaction_id,
        source_entries=source_entries,
        final_documents=final_documents,
    )
    if manifest != expected_manifest:
        raise PublicationTransactionError("publication manifest does not match current A1 sources")
    _validate_manifest_files(root, manifest)
    tx_relative = PUBLICATION_TRANSACTION_PATH_TEMPLATE.format(run_id=run_id)
    transaction = _load_transaction(root, tx_relative)
    if (
        transaction.get("run_id") != run_id
        or transaction.get("plan_fingerprint") != plan_fingerprint
        or transaction.get("execution_profile") != execution_profile
        or transaction.get("manifest_path") != PUBLICATION_MANIFEST_PATH
    ):
        raise PublicationTransactionError("publication transaction identity does not match")
    if transaction.get("transaction_id") != manifest.get("transaction_id"):
        raise PublicationTransactionError("publication transaction identity does not match Manifest")
    if transaction.get("manifest_sha256") != _sha256(data):
        raise PublicationTransactionError("publication transaction Manifest hash does not match")
    allowed_phases = {"manifest_committed", "cleanup_pending", "cleanup_complete"}
    if _allow_recovery_state:
        allowed_phases.add("manifest_commit_intent")
    if transaction.get("phase") not in allowed_phases:
        raise PublicationTransactionError("publication transaction has not committed its Manifest")
    return {"manifest": manifest, "manifest_bytes": data, "transaction": transaction}


def _cleanup_transaction(root: Path, transaction: Mapping[str, Any]) -> None:
    errors: list[Exception] = []
    for relative, label in (
        (transaction.get("backup_dir"), "publication backup"),
        (transaction.get("temporary_dir"), "publication temporary directory"),
    ):
        if not isinstance(relative, str):
            continue
        try:
            _remove_path(_path(root, relative, label=label), label=label)
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise PublicationTransactionError("publication cleanup failed: " + " | ".join(str(item) for item in errors)) from errors[0]


def _rollback_uncommitted_transaction(
    root: Path,
    transaction: Mapping[str, Any],
    *,
    committed_paths: Iterable[str],
) -> None:
    errors: list[Exception] = []
    records_by_path = {item["path"]: item for item in transaction["target_records"]}
    committed = list(committed_paths)
    if any(path not in records_by_path for path in committed):
        raise PublicationTransactionError("recovery marker references an unknown transaction target")
    for path_text in reversed(committed):
        record = records_by_path[path_text]
        target = _path(root, record["path"], label=f"rollback target {record['path']}")
        try:
            if record["existed_before"]:
                raise PublicationTransactionError(
                    "A2 cannot automatically roll back a pre-existing target"
                )
            if target.exists() or target.is_symlink():
                target.unlink()
        except Exception as exc:
            errors.append(exc)
    try:
        _cleanup_transaction(root, transaction)
    except Exception as exc:
        errors.append(exc)
    if errors:
        raise PublicationTransactionError(
            "publication rollback failed: " + " | ".join(str(item) for item in errors)
        ) from errors[0]


def recover_publication(
    project_root: Path,
    *,
    run_id: str,
    execution_profile: str = PUBLICATION_EXECUTION_PROFILE,
    plan_fingerprint: str,
) -> dict[str, Any]:
    """Recover only a transaction whose identity and committed bytes are provable."""

    root = a1_artifacts.validate_phase_a1_sandbox(
        project_root, run_id=run_id, execution_profile=execution_profile
    )
    marker = _recovery_marker_path(root, run_id)
    if not _lstat_sentinel(marker, label="publication recovery marker"):
        raise PublicationTransactionError("publication recovery marker is missing")
    marker_document = _load_recovery_marker(root, run_id)
    tx_relative = PUBLICATION_TRANSACTION_PATH_TEMPLATE.format(run_id=run_id)
    transaction = _load_transaction(root, tx_relative)
    if transaction.get("run_id") != run_id or transaction.get("plan_fingerprint") != plan_fingerprint:
        raise PublicationTransactionError("recovery transaction identity does not match")
    if transaction.get("execution_profile") != execution_profile:
        raise PublicationTransactionError("recovery transaction execution profile does not match")
    if transaction.get("phase") in {"backup_ready", "publishing"}:
        if marker_document["stage"] == "publication_zero_commit_uncertain":
            raise PublicationTransactionError(
                "zero-commit replace state is uncertain; manual target inspection is required"
            )
        try:
            _rollback_uncommitted_transaction(
                root,
                transaction,
                committed_paths=marker_document["committed_paths"],
            )
            _remove_path(
                _path(root, tx_relative, label="publication transaction"),
                label="publication transaction",
            )
            marker.unlink()
        except Exception as exc:
            raise PublicationTransactionError(
                "publication rollback recovery remains incomplete"
            ) from exc
        return {"run_id": run_id, "transaction_id": transaction["transaction_id"], "rolled_back": True}
    if transaction.get("phase") == "manifest_commit_intent":
        manifest_path = _path(root, PUBLICATION_MANIFEST_PATH, label="publication Manifest")
        if not manifest_path.exists():
            try:
                _rollback_uncommitted_transaction(
                    root,
                    transaction,
                    committed_paths=transaction["committed_paths"],
                )
                _remove_path(
                    _path(root, tx_relative, label="publication transaction"),
                    label="publication transaction",
                )
                marker.unlink()
            except Exception as exc:
                raise PublicationTransactionError(
                    "manifest-intent rollback recovery remains incomplete"
                ) from exc
            return {"run_id": run_id, "transaction_id": transaction["transaction_id"], "rolled_back": True}
    if transaction.get("phase") in {"manifest_commit_intent", "manifest_committed", "cleanup_pending", "cleanup_complete"}:
        try:
            current = validate_publication(
                root,
                run_id=run_id,
                execution_profile=execution_profile,
                plan_fingerprint=plan_fingerprint,
                _allow_recovery_state=True,
            )
        except PublicationTransactionError as exc:
            raise PublicationTransactionError(
                "recovery cannot prove a committed publication; refusing to modify targets"
            ) from exc
        if transaction.get("phase") == "manifest_commit_intent":
            transaction = {**transaction, "phase": "manifest_committed"}
            _write_transaction(root, tx_relative, transaction)
        try:
            _cleanup_transaction(root, transaction)
        except Exception as exc:
            raise PublicationTransactionError("committed publication remains valid but cleanup requires manual recovery") from exc
        transaction = {**transaction, "phase": "cleanup_complete"}
        _write_transaction(root, tx_relative, transaction)
        try:
            marker.unlink()
        except OSError as exc:
            raise PublicationTransactionError(
                "publication recovered but recovery marker cleanup failed"
            ) from exc
        return {**current, "transaction": transaction, "recovered": True}
    raise PublicationTransactionError(
        f"publication transaction phase {transaction.get('phase')} requires manual rollback inspection"
    )


def publish_run_local_artifacts(
    project_root: Path,
    *,
    run_id: str,
    plan_fingerprint: str,
    execution_profile: str = PUBLICATION_EXECUTION_PROFILE,
) -> dict[str, Any]:
    """Publish the current A1 staging set inside a temporary project sandbox."""

    root = a1_artifacts.validate_phase_a1_sandbox(
        project_root, run_id=run_id, execution_profile=execution_profile
    )
    _reject_recovery_marker(root, run_id)
    try:
        a1_artifacts._require_sha256(plan_fingerprint, field="plan_fingerprint")
    except PhaseA1ArtifactError as exc:
        raise PublicationTransactionError(str(exc)) from exc

    inputs = _load_a1_inputs(
        root,
        run_id=run_id,
        execution_profile=execution_profile,
        plan_fingerprint=plan_fingerprint,
    )
    staging = _snapshot_staging(root, run_id)
    source_entries = _source_entries(root, run_id, inputs, staging)
    identity_entries = [{key: item[key] for key in ("kind", "path", "size_bytes", "sha256")} for item in source_entries]
    transaction_id = _transaction_id(run_id, plan_fingerprint, identity_entries)
    tx_relative = PUBLICATION_TRANSACTION_PATH_TEMPLATE.format(run_id=run_id)
    tx_path = _path(root, tx_relative, label="publication transaction")

    if tx_path.exists() or tx_path.is_symlink():
        existing = _load_transaction(root, tx_relative)
        if existing.get("transaction_id") != transaction_id:
            raise PublicationTransactionError("a different publication transaction already exists for this Run")
        if existing.get("phase") == "cleanup_complete":
            return validate_publication(
                root,
                run_id=run_id,
                execution_profile=execution_profile,
                plan_fingerprint=plan_fingerprint,
            ) | {"idempotent": True}
        raise PublicationTransactionError("publication transaction requires recovery before rerun")

    final_documents = _build_final_documents(run_id, transaction_id, staging)
    manifest = _manifest_document(
        root=root,
        run_id=run_id,
        plan_fingerprint=plan_fingerprint,
        transaction_id=transaction_id,
        source_entries=source_entries,
        final_documents=final_documents,
    )
    manifest_bytes = _canonical_json_bytes(manifest)
    target_bytes = {**final_documents, PUBLICATION_MANIFEST_PATH: manifest_bytes}
    target_records = _target_records(root, target_bytes)
    existing_manifest = next(
        item
        for item in target_records
        if item["path"] == PUBLICATION_MANIFEST_PATH
    )
    if existing_manifest["existed_before"]:
        raise PublicationTransactionError(
            "publication Manifest already exists without the matching transaction"
        )

    backup_relative = f"runs/{run_id}/publication_backup/{transaction_id}"
    temporary_relative = f"runs/{run_id}/.publication_{transaction_id}.tmp"
    backup_path = _path(root, backup_relative, label="publication backup")
    temporary_path = _path(root, temporary_relative, label="publication temporary directory")
    backup_path.mkdir(parents=True, exist_ok=True)
    temporary_path.mkdir(parents=True, exist_ok=True)
    _path(root, backup_relative, label="publication backup")
    _path(root, temporary_relative, label="publication temporary directory")
    _sync_directory(backup_path.parent, label="publication backup parent")
    _sync_directory(temporary_path.parent, label="publication temporary parent")
    transaction = {
        "schema_version": PUBLICATION_TRANSACTION_SCHEMA_VERSION,
        "run_id": run_id,
        "plan_fingerprint": plan_fingerprint,
        "execution_profile": execution_profile,
        "transaction_id": transaction_id,
        "phase": "backup_ready",
        "manifest_path": PUBLICATION_MANIFEST_PATH,
        "manifest_sha256": _sha256(manifest_bytes),
        "backup_dir": backup_relative,
        "temporary_dir": temporary_relative,
        "target_records": target_records,
        "committed_paths": [],
    }
    try:
        _write_transaction(root, tx_relative, transaction)
    except Exception as exc:
        if getattr(exc, "write_state_uncertain", False):
            _write_recovery_marker(
                root,
                run_id,
                stage="publication_transaction_initialization",
                committed_paths=[],
                primary_error=exc,
                cleanup_error=getattr(exc, "cleanup_error", None),
            )
        raise
    _sync_directory(_path(root, f"runs/{run_id}", label="Run directory", include_leaf=False), label="Run directory")

    committed_paths: list[str] = []
    manifest_committed = False
    recovery_marked = False
    rollback_error: Exception | None = None
    invalidated_final_summary_path: str | None = None
    primary_error: Exception | None = None
    try:
        transaction = {**transaction, "phase": "publishing"}
        _write_transaction(root, tx_relative, transaction)
        target_records_by_path = {item["path"]: item for item in target_records}
        for path, data in sorted(final_documents.items()):
            if target_records_by_path[path]["existed_before"]:
                continue
            _atomic_replace(_path(root, path, label=f"publication target {path}"), data, root=root, label=f"publication target {path}")
            committed_paths.append(path)
        _sync_directory(
            _path(root, "outputs", label="outputs directory", include_leaf=False),
            label="outputs directory",
        )
        _sync_directory(
            _path(root, f"runs/{run_id}", label="Run directory", include_leaf=False),
            label="Run directory",
        )
        refreshed_inputs = _load_a1_inputs(
            root,
            run_id=run_id,
            execution_profile=execution_profile,
            plan_fingerprint=plan_fingerprint,
        )
        refreshed_staging = _snapshot_staging(root, run_id)
        refreshed_sources = _source_entries(root, run_id, refreshed_inputs, refreshed_staging)
        if refreshed_staging != staging or refreshed_sources != source_entries:
            raise PublicationTransactionError("A1 source or staging bytes changed during publication")
        for path, data in final_documents.items():
            if _read_file(_path(root, path, label=f"published target {path}"), label=f"published target {path}") != data:
                raise PublicationTransactionError(f"published target changed during verification: {path}")
        transaction = {**transaction, "phase": "manifest_commit_intent", "committed_paths": list(committed_paths)}
        _write_transaction(root, tx_relative, transaction)
        _atomic_replace(_path(root, PUBLICATION_MANIFEST_PATH, label="publication Manifest"), manifest_bytes, root=root, label="publication Manifest")
        manifest_committed = True
        committed_paths.append(PUBLICATION_MANIFEST_PATH)
        _sync_directory(_path(root, "outputs", label="outputs directory", include_leaf=False), label="outputs directory")
        transaction = {**transaction, "phase": "manifest_committed", "committed_paths": list(committed_paths)}
        _write_transaction(root, tx_relative, transaction)
        _sync_directory(_path(root, f"runs/{run_id}", label="Run directory", include_leaf=False), label="Run directory")
        validate_publication(
            root,
            run_id=run_id,
            execution_profile=execution_profile,
            plan_fingerprint=plan_fingerprint,
        )
        try:
            _cleanup_transaction(root, transaction)
        except Exception as exc:
            transaction = {**transaction, "phase": "cleanup_pending", "cleanup_error": str(exc)}
            try:
                _write_transaction(root, tx_relative, transaction)
            except Exception as transaction_error:
                exc = PublicationTransactionError(f"{exc}; transaction cleanup state write failed: {transaction_error}")
            _write_recovery_marker(root, run_id, stage="publication_cleanup", committed_paths=committed_paths, primary_error=exc)
            recovery_marked = True
            raise PublicationTransactionError("publication committed but cleanup requires recovery") from exc
        transaction = {**transaction, "phase": "cleanup_complete"}
        _write_transaction(root, tx_relative, transaction)
        return validate_publication(
            root,
            run_id=run_id,
            execution_profile=execution_profile,
            plan_fingerprint=plan_fingerprint,
        ) | {"idempotent": False}
    except Exception as exc:
        primary_error = exc
        if recovery_marked:
            raise
        if manifest_committed:
            _write_recovery_marker(root, run_id, stage="manifest_commit_recovery", committed_paths=committed_paths, primary_error=exc)
            raise
        if committed_paths:
            # A2 has not committed the Manifest. Restore known targets, but keep
            # a marker: partial publication is an auditable recovery state.
            try:
                final_summary_path = FINAL_SUMMARY_PATH_TEMPLATE.format(run_id=run_id)
                if final_summary_path in committed_paths:
                    invalidated_final_summary_path = _isolate_final_summary(
                        root,
                        run_id=run_id,
                        transaction_id=transaction_id,
                    )
                for record in reversed(target_records):
                    if record["path"] not in committed_paths:
                        continue
                    target = _path(root, record["path"], label=f"rollback target {record['path']}")
                    if record["existed_before"]:
                        raise PublicationTransactionError("rollback of pre-existing target requires an explicit backup")
                    if target.exists() or target.is_symlink():
                        target.unlink()
                _sync_directory(_path(root, "outputs", label="outputs directory", include_leaf=False), label="outputs directory")
            except Exception as rollback_exc:
                rollback_error = rollback_exc
            _write_recovery_marker(
                root,
                run_id,
                stage="publication_partial_commit",
                committed_paths=committed_paths,
                primary_error=exc,
                cleanup_error=rollback_error,
                invalidated_final_summary_path=invalidated_final_summary_path,
            )
            if rollback_error is not None:
                raise PublicationTransactionError(f"{exc}; rollback also failed: {rollback_error}") from (exc.__cause__ or exc)
            raise
        if getattr(exc, "write_state_uncertain", False) or getattr(exc, "cleanup_error", None) is not None:
            _write_recovery_marker(
                root,
                run_id,
                stage="publication_zero_commit_uncertain",
                committed_paths=[],
                primary_error=exc,
                cleanup_error=getattr(exc, "cleanup_error", None),
            )
        else:
            clean_failure_error: Exception | None = None
            try:
                _remove_path(backup_path, label="publication backup")
                _remove_path(temporary_path, label="publication temporary directory")
                _remove_path(tx_path, label="publication transaction")
            except Exception as cleanup_exc:
                clean_failure_error = cleanup_exc
            if clean_failure_error is not None:
                _write_recovery_marker(
                    root,
                    run_id,
                    stage="publication_zero_commit_cleanup",
                    committed_paths=[],
                    primary_error=exc,
                    cleanup_error=clean_failure_error,
                )
                raise PublicationTransactionError(
                    f"{exc}; zero-commit cleanup also failed: {clean_failure_error}"
                ) from (exc.__cause__ or exc)
        raise


publish = publish_run_local_artifacts


__all__ = [
    "FINAL_SUMMARY_PATH_TEMPLATE",
    "PUBLICATION_EXECUTION_PROFILE",
    "PUBLICATION_MANIFEST_PATH",
    "PUBLICATION_MANIFEST_SCHEMA_VERSION",
    "PUBLICATION_RECOVERY_MARKER_SCHEMA_VERSION",
    "PUBLICATION_TRANSACTION_PATH_TEMPLATE",
    "PUBLICATION_TRANSACTION_SCHEMA_VERSION",
    "PublicationTransactionError",
    "publish",
    "publish_run_local_artifacts",
    "recover_publication",
    "validate_publication",
]
