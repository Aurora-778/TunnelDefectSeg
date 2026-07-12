import csv
import re
from pathlib import Path

from scripts.report_paths import report_path


ABSOLUTE_USER_PATH = re.compile(r"(?:[A-Za-z]:[\\/](?:Users|users)[\\/]|/(?:home|Users)/[^/\s`]+/)")
FORMAL_TEXT_ARTIFACTS = [
    Path("outputs/disease_engineering_report.md"),
    Path("outputs/disease_engineering_report_summary.md"),
    Path("outputs/disease_growth_analysis_report.md"),
    Path("outputs/disease_growth_analysis_summary.md"),
    Path("outputs/disease_memory_bank_summary.md"),
    Path("outputs/memory_agent_report.md"),
    Path("outputs/visualization_report.md"),
    Path("outputs/visualization_summary.md"),
    Path("outputs/recheck_list_report.md"),
    Path("outputs/final_project_report.md"),
    Path("outputs/system_summary.md"),
    Path("outputs/key_insights.md"),
    Path("outputs/association_evaluation_report.md"),
    Path("outputs/association_benchmark/association_benchmark_report.md"),
    Path("outputs/association_benchmark/benchmark_manifest.json"),
]
for artifact_root in (Path("data/simulated/main_progressive"), Path("data/simulated/progressive")):
    FORMAL_TEXT_ARTIFACTS.extend(
        path for path in artifact_root.rglob("*") if path.is_file() and path.suffix.lower() in {".md", ".json", ".csv"}
    )


def test_readme_association_count_matches_history_only_demo():
    readme = Path("README.md").read_text(encoding="utf-8")
    with Path("data/simulated/disease_association_records.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert "association rows: 30" not in readme
    assert f"association rows: {len(rows)}" in readme
    assert "I001 只建立 baseline" in readme


def test_documentation_keeps_with_id_as_upper_bound_and_benchmark_separate():
    readme = Path("README.md").read_text(encoding="utf-8")
    contract = Path("docs/artifact_contract.md").read_text(encoding="utf-8")

    assert "upper-bound / sanity check" in readme
    assert "Association Benchmark" in readme
    assert "nearest-mileage" in contract


def test_report_path_uses_relative_posix_or_external_marker(tmp_path):
    project_root = Path.cwd().resolve()

    assert report_path(project_root / "outputs" / "report.md") == "outputs/report.md"
    assert report_path(tmp_path / "external.csv") == "external_input:external.csv"


def test_formal_text_artifacts_do_not_expose_user_absolute_paths():
    for path in FORMAL_TEXT_ARTIFACTS:
        text = path.read_text(encoding="utf-8")
        assert not ABSOLUTE_USER_PATH.search(text), f"absolute user path found in {path.as_posix()}"
