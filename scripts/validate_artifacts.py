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
        schema_errors = validate_csv_schema(path, schema_name)
        if schema_errors:
            errors[schema_name] = schema_errors
    main_association_path = project_root / ARTIFACTS["disease_association_records"]
    main_mode_errors = validate_association_mode(main_association_path, "false", "no_id")
    if main_mode_errors:
        errors.setdefault("disease_association_records", []).extend(main_mode_errors)
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
        errors.extend(f"{relative_path}: {error}" for error in validate_csv_schema(path, "progressive_association_records"))
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
                    "memory_after",
                    "metrics",
                ]:
                    if key not in round_info:
                        errors.append(f"progressive manifest round {index} missing {key}")
                allowed_inputs = round_info.get("allowed_inputs", [])
                if not isinstance(allowed_inputs, list):
                    errors.append(f"progressive manifest round {index} allowed_inputs must be a list")
                    allowed_inputs = []
                if source_dataset and source_dataset in allowed_inputs:
                    errors.append(f"progressive manifest round {index} allowed_inputs must not contain source_dataset")
                errors.extend(validate_manifest_file(project_root, round_info, index, "memory_before", "disease_memory_bank"))
                errors.extend(validate_manifest_file(project_root, round_info, index, "association_records", "progressive_association_records"))
                errors.extend(validate_manifest_file(project_root, round_info, index, "no_id_association_records", "progressive_association_records"))
                errors.extend(validate_manifest_file(project_root, round_info, index, "with_id_association_records", "progressive_association_records"))
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


def validate_manifest_file(project_root: Path, round_info: dict, round_index: int, key: str, schema_name: str) -> list[str]:
    if key not in round_info:
        return []
    path = resolve_manifest_path(project_root, round_info.get(key, ""))
    if not path.exists():
        return [f"progressive manifest round {round_index} {key} missing file: {path}"]
    return [f"progressive manifest round {round_index} {key}: {error}" for error in validate_csv_schema(path, schema_name)]


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
