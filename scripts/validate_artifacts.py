"""Validate core CSV artifacts against the pipeline schema contract."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.schema import validate_csv_schema


ARTIFACTS = {
    "robot_kict_frame_records": Path("data/simulated/robot_kict_frame_records.csv"),
    "disease_engineering_report": Path("data/simulated/disease_engineering_report.csv"),
    "disease_growth_results": Path("data/simulated/disease_growth_results.csv"),
    "disease_memory_bank": Path("data/simulated/disease_memory_bank.csv"),
    "disease_association_records": Path("data/simulated/disease_association_records.csv"),
    "priority_recheck_list": Path("data/simulated/priority_recheck_list.csv"),
}

PROGRESSIVE_MANIFEST = Path("data/simulated/progressive/progressive_evaluation_manifest.json")
MAIN_PROGRESSIVE_MANIFEST = Path("data/simulated/main_progressive/association_manifest.json")
ASSOCIATION_EVALUATION_REPORT = Path("outputs/association_evaluation_report.md")
PROGRESSIVE_ASSOCIATION_OUTPUTS = [
    (Path("data/simulated/disease_association_records_no_id.csv"), "false", "no_id"),
    (Path("data/simulated/disease_association_records_with_id.csv"), "true", "with_id_upper_bound"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate generated robot inspection artifacts.")
    parser.add_argument("--project-root", type=Path, default=Path("."), help="Repository root containing data/simulated.")
    return parser.parse_args()


def validate_artifacts(project_root: Path) -> dict[str, list[str]]:
    """Validate every known artifact and return errors grouped by schema name."""

    errors: dict[str, list[str]] = {}
    for schema_name, relative_path in ARTIFACTS.items():
        path = project_root / relative_path
        schema_errors = validate_csv_schema(path, schema_name, allow_empty=schema_name == "disease_association_records")
        if schema_errors:
            errors[schema_name] = schema_errors
    main_association_path = project_root / ARTIFACTS["disease_association_records"]
    main_mode_errors = validate_association_mode(main_association_path, "false", "no_id")
    if main_mode_errors:
        errors.setdefault("disease_association_records", []).extend(main_mode_errors)
    main_history_errors = validate_main_history_only_association(project_root)
    if main_history_errors:
        errors.setdefault("disease_association_records", []).extend(main_history_errors)
    progressive_errors = validate_progressive_artifacts(project_root)
    if progressive_errors:
        errors["progressive_evaluation"] = progressive_errors
    return errors


def validate_progressive_artifacts(project_root: Path) -> list[str]:
    errors: list[str] = []
    manifest_path = project_root / PROGRESSIVE_MANIFEST
    report_path = project_root / ASSOCIATION_EVALUATION_REPORT

    for relative_path, expected_use_id, expected_mode in PROGRESSIVE_ASSOCIATION_OUTPUTS:
        path = project_root / relative_path
        errors.extend(
            f"{relative_path}: {error}"
            for error in validate_csv_schema(path, "progressive_association_records", allow_empty=True)
        )
        errors.extend(f"{relative_path}: {error}" for error in validate_association_mode(path, expected_use_id, expected_mode))

    if not manifest_path.exists():
        errors.append(f"missing file: {manifest_path}")
    else:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"invalid JSON: {manifest_path}: {exc}")
        else:
            source_dataset = manifest.get("source_dataset")
            if not source_dataset:
                errors.append("progressive manifest missing source_dataset")
            rounds = manifest.get("rounds")
            if not isinstance(rounds, list) or not rounds:
                errors.append("progressive manifest must contain non-empty rounds")
            for index, round_info in enumerate(rounds or [], start=1):
                if not isinstance(round_info, dict):
                    errors.append(f"progressive manifest round {index} must be an object")
                    continue
                for key in [
                    "history_inspections",
                    "query_inspection",
                    "memory_before",
                    "association_records",
                    "no_id_association_records",
                    "with_id_association_records",
                    "allowed_inputs",
                    "memory_after",
                    "metrics",
                ]:
                    if key not in round_info:
                        errors.append(f"progressive manifest round {index} missing {key}")
                allowed_inputs = round_info.get("allowed_inputs", [])
                if not isinstance(allowed_inputs, list):
                    errors.append(f"progressive manifest round {index} allowed_inputs must be a list")
                    allowed_inputs = []
                if source_dataset and any(
                    manifest_paths_equal(project_root, source_dataset, allowed_path) for allowed_path in allowed_inputs
                ):
                    errors.append(f"progressive manifest round {index} allowed_inputs must not contain source_dataset")
                errors.extend(validate_manifest_file(project_root, round_info, index, "memory_before", "disease_memory_bank"))
                errors.extend(
                    validate_manifest_file(project_root, round_info, index, "association_records", "progressive_association_records", allow_empty=True)
                )
                errors.extend(
                    validate_manifest_file(project_root, round_info, index, "no_id_association_records", "progressive_association_records", allow_empty=True)
                )
                errors.extend(
                    validate_manifest_file(project_root, round_info, index, "with_id_association_records", "progressive_association_records", allow_empty=True)
                )
                errors.extend(validate_manifest_association_mode(project_root, round_info, index, "association_records", "false", "no_id"))
                errors.extend(validate_manifest_association_mode(project_root, round_info, index, "no_id_association_records", "false", "no_id"))
                errors.extend(
                    validate_manifest_association_mode(
                        project_root,
                        round_info,
                        index,
                        "with_id_association_records",
                        "true",
                        "with_id_upper_bound",
                    )
                )
                errors.extend(validate_manifest_file(project_root, round_info, index, "memory_after", "disease_memory_bank"))
                errors.extend(
                    validate_association_history_rows(
                        project_root,
                        round_info,
                        index,
                        "association_records",
                        source_dataset,
                    )
                )

    if not report_path.exists():
        errors.append(f"missing file: {report_path}")
    else:
        report = report_path.read_text(encoding="utf-8")
        if "Baseline / Ablation" not in report:
            errors.append("association evaluation report missing Baseline / Ablation section")
        if "禁用 disease_id" not in report and "disabled disease_id" not in report:
            errors.append("association evaluation report must state disease_id is disabled during scoring")
    return errors


def resolve_manifest_path(project_root: Path, value: object) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else project_root / path


def normalize_manifest_path(project_root: Path, value: object) -> Path:
    # Normalize manifest paths for semantic comparisons without requiring files to exist.
    path_text = str(value).strip().replace("\\", "/")
    path = Path(path_text)
    candidate = path if path.is_absolute() else project_root / path
    return candidate.resolve(strict=False)


def manifest_paths_equal(project_root: Path, left: object, right: object) -> bool:
    return normalize_manifest_path(project_root, left) == normalize_manifest_path(project_root, right)


def validate_manifest_file(
    project_root: Path,
    round_info: dict,
    round_index: int,
    key: str,
    schema_name: str,
    *,
    allow_empty: bool = False,
) -> list[str]:
    if key not in round_info:
        return []
    path = resolve_manifest_path(project_root, round_info.get(key, ""))
    if not path.exists():
        return [f"progressive manifest round {round_index} {key} missing file: {path}"]
    return [
        f"progressive manifest round {round_index} {key}: {error}"
        for error in validate_csv_schema(path, schema_name, allow_empty=allow_empty)
    ]


def validate_manifest_association_mode(
    project_root: Path,
    round_info: dict,
    round_index: int,
    key: str,
    expected_use_id: str,
    expected_mode: str,
) -> list[str]:
    if key not in round_info:
        return []
    path = resolve_manifest_path(project_root, round_info.get(key, ""))
    return [
        f"progressive manifest round {round_index} {key}: {error}"
        for error in validate_association_mode(path, expected_use_id, expected_mode)
    ]


def validate_association_mode(path: Path, expected_use_id: str, expected_mode: str) -> list[str]:
    if not path.exists():
        return []
    errors: list[str] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for line_number, row in enumerate(reader, start=2):
            use_value = str(row.get("use_disease_id_score", "")).strip().lower()
            mode_value = str(row.get("association_mode", "")).strip()
            if use_value != expected_use_id:
                errors.append(
                    f"line {line_number}: use_disease_id_score={use_value!r} expected {expected_use_id!r}"
                )
            if mode_value != expected_mode:
                errors.append(f"line {line_number}: association_mode={mode_value!r} expected {expected_mode!r}")
    return errors


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _inspection_key(inspection_id: str) -> tuple[int, str]:
    digits = "".join(char for char in str(inspection_id) if char.isdigit())
    return (int(digits) if digits else 0, str(inspection_id))


def _association_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return (
        row.get("inspection_id", ""),
        row.get("frame_id", ""),
        row.get("image_id", ""),
        row.get("label_disease_id", row.get("disease_id", "")),
    )


def _source_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return (
        row.get("inspection_id", ""),
        row.get("frame_id", ""),
        row.get("image_id", ""),
        row.get("disease_id", ""),
    )


def _validate_source_rows(source_rows: list[dict[str, str]]) -> tuple[set[tuple[str, str, str, str]], list[str]]:
    seen: set[tuple[str, str, str, str]] = set()
    errors: list[str] = []
    for line_number, row in enumerate(source_rows, start=2):
        key = _source_key(row)
        if not all(key):
            errors.append(f"source frame records line {line_number} has incomplete composite key: {key}")
        elif key in seen:
            errors.append(f"source frame records duplicate composite key at line {line_number}: {key}")
        seen.add(key)
    return seen, errors


def _validate_association_rows(
    records: list[dict[str, str]],
    source_keys: set[tuple[str, str, str, str]],
    *,
    expected_history: list[str] | None = None,
    label: str,
) -> list[str]:
    errors: list[str] = []
    seen: set[tuple[str, str, str, str]] = set()
    expected_text = "|".join(expected_history or []) if expected_history is not None else None
    for line_number, row in enumerate(records, start=2):
        key = _association_key(row)
        if key in seen:
            errors.append(f"{label} duplicate association composite key at line {line_number}: {key}")
        seen.add(key)
        if key not in source_keys:
            errors.append(f"{label} orphan association at line {line_number}: {key}")
        history_text = str(row.get("history_inspection_ids", "")).strip()
        if not history_text:
            errors.append(f"{label} line {line_number} history_inspection_ids must not be empty")
            continue
        history_ids = history_text.split("|")
        if history_ids != sorted(history_ids, key=_inspection_key):
            errors.append(f"{label} line {line_number} history_inspection_ids must be sorted")
        if any(_inspection_key(history_id) >= _inspection_key(row.get("inspection_id", "")) for history_id in history_ids):
            errors.append(f"{label} line {line_number} history_inspection_ids include current or future inspection")
        if expected_text is not None and history_text != expected_text:
            errors.append(f"{label} line {line_number} history_inspection_ids disagree with manifest")
    return errors


def validate_association_history_rows(
    project_root: Path,
    round_info: dict,
    round_index: int,
    artifact_key: str,
    source_dataset: object,
) -> list[str]:
    if artifact_key not in round_info or not source_dataset:
        return []
    artifact_path = resolve_manifest_path(project_root, round_info[artifact_key])
    source_path = resolve_manifest_path(project_root, source_dataset)
    source_rows = _read_rows(source_path)
    source_keys, errors = _validate_source_rows(source_rows)
    history = round_info.get("history_inspections", round_info.get("history_inspection_ids", []))
    if not isinstance(history, list):
        return errors + [f"progressive manifest round {round_index} history_inspections must be a list"]
    return errors + _validate_association_rows(
        _read_rows(artifact_path), source_keys, expected_history=history, label=f"progressive manifest round {round_index}",
    )


def validate_main_history_only_association(project_root: Path) -> list[str]:
    """Verify the main no-id output was produced by history-only rounds."""

    source_path = project_root / ARTIFACTS["robot_kict_frame_records"]
    association_path = project_root / ARTIFACTS["disease_association_records"]
    manifest_path = project_root / MAIN_PROGRESSIVE_MANIFEST
    source_rows = _read_rows(source_path)
    source_keys, errors = _validate_source_rows(source_rows)
    if not manifest_path.exists():
        return errors + [f"missing file: {manifest_path}"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return errors + [f"invalid JSON: {manifest_path}: {exc}"]
    if manifest.get("mode") != "history_only":
        errors.append("main association manifest mode must be history_only")
    rounds = manifest.get("rounds")
    if not isinstance(rounds, list):
        return errors + ["main association manifest rounds must be a list"]
    expected_history_by_query: dict[str, list[str]] = {}
    for round_info in rounds:
        if not isinstance(round_info, dict):
            errors.append("main association manifest round must be an object")
            continue
        query = str(round_info.get("query_inspection", ""))
        history = round_info.get("history_inspection_ids")
        if not query or not isinstance(history, list):
            errors.append("main association manifest round missing query_inspection or history_inspection_ids")
            continue
        expected_history_by_query[query] = history
    records = _read_rows(association_path)
    for query, history in expected_history_by_query.items():
        if history:
            continue
        if any(record.get("inspection_id") == query for record in records):
            errors.append(f"main association baseline {query} must not generate self-matches")
    for record in records:
        history = expected_history_by_query.get(record.get("inspection_id", ""))
        if history is None:
            errors.append(f"main association query inspection missing from manifest: {record.get('inspection_id', '')}")
            continue
        errors.extend(_validate_association_rows([record], source_keys, expected_history=history, label="main association"))
    return errors


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    errors = validate_artifacts(project_root)
    if errors:
        print("Artifact schema validation failed")
        for schema_name, schema_errors in errors.items():
            print(f"- {schema_name}:")
            for error in schema_errors:
                print(f"  - {error}")
        return 1

    print("Artifact schema validation passed")
    for schema_name, relative_path in ARTIFACTS.items():
        print(f"- {schema_name}: {(project_root / relative_path).as_posix()}")
    print(f"- progressive_evaluation: {(project_root / PROGRESSIVE_MANIFEST).as_posix()}")
    print(f"- association_evaluation_report: {(project_root / ASSOCIATION_EVALUATION_REPORT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
