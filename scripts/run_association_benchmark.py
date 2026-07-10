"""Run the fixture-backed Association benchmark without touching production data."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.association_benchmark import load_fixture, write_benchmark_outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the reproducible Association benchmark.")
    parser.add_argument("--fixture", type=Path, default=Path("tests/fixtures/association_benchmark/benchmark_fixture.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/association_benchmark"))
    parser.add_argument("--overwrite", action="store_true", help="Allow replacing prior benchmark artifacts in output-dir.")
    return parser.parse_args()


def resolve(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def main() -> None:
    args = parse_args()
    fixture_path = resolve(args.fixture)
    output_dir = resolve(args.output_dir)
    expected = [output_dir / name for name in (
        "association_benchmark_summary.csv", "association_benchmark_cases.csv", "association_benchmark_report.md", "benchmark_manifest.json"
    )]
    if any(path.exists() for path in expected) and not args.overwrite:
        raise FileExistsError("Benchmark output already exists; pass --overwrite to replace only benchmark artifacts.")
    fixture = load_fixture(fixture_path)
    paths = write_benchmark_outputs(fixture, output_dir)
    print("Association benchmark completed")
    for label, path in paths.items():
        print(f"{label}: {path.relative_to(PROJECT_ROOT).as_posix() if path.is_relative_to(PROJECT_ROOT) else path}")


if __name__ == "__main__":
    main()
