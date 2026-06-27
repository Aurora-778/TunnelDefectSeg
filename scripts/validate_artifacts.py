"""Validate core CSV artifacts against the pipeline schema contract."""

from __future__ import annotations

import argparse
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
