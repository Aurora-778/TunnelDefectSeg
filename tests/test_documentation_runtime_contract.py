import csv
import re
import subprocess
from pathlib import Path

from orchestrator.agents.doc_agent import DocAgent
from orchestrator.agents.web_agent import WebAgent
from scripts.report_paths import report_path


ABSOLUTE_USER_PATH = re.compile(r"(?:(?<![A-Za-z])[A-Za-z]:[\\/]|/(?:home|Users|tmp|opt)(?:[\\/]|$))")
FORMAL_TEXT_ROOTS = [
    "data/simulated",
    "outputs",
    "docs/orchestrator_v1_summary.md",
    "docs/presentations/tunnel-defect-project-speaker-output",
]


def tracked_formal_text_artifacts() -> list[Path]:
    """Return tracked formal text artifacts, excluding local logs and WIP outputs."""

    result = subprocess.run(
        ["git", "ls-files", "--", *FORMAL_TEXT_ROOTS],
        check=True,
        capture_output=True,
        text=True,
    )
    return sorted(
        Path(line)
        for line in result.stdout.splitlines()
        if line and Path(line).suffix.lower() in {".md", ".json", ".csv"}
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


def test_report_path_recognizes_foreign_platform_absolute_paths(tmp_path):
    assert report_path(r"C:\Users\alice\input.csv", tmp_path) == "external_input:input.csv"
    assert report_path(r"D:\datasets\input.csv", tmp_path) == "external_input:input.csv"
    assert report_path("/home/alice/input.csv", tmp_path) == "external_input:input.csv"
    assert report_path("/opt/data/input.csv", tmp_path) == "external_input:input.csv"


def test_absolute_path_pattern_does_not_treat_http_url_as_drive_path():
    assert not ABSOLUTE_USER_PATH.search("Open http://127.0.0.1:8000/ in a browser")


def test_doc_and_web_agents_write_project_relative_paths(tmp_path):
    context = {
        "inputs": {
            "web": {"manifest_path": "outputs/orchestrator_web_manifest.md"},
            "doc": {"summary_path": "outputs/orchestrator_v1_summary.md", "docs_path": "docs/orchestrator_v1_summary.md"},
        },
        "outputs": {
            "memory": {"disease_memory_bank_path": str(tmp_path / "data/simulated/disease_memory_bank.csv"), "memory_bank_rows": 1},
            "association": {"association_records_path": str(tmp_path / "data/simulated/association_records.csv"), "association_rows": 1},
        },
        "shared": {"project_root": str(tmp_path)},
    }
    context["outputs"]["web"] = WebAgent().run(context)
    DocAgent().run(context)

    for path in (
        tmp_path / "outputs/orchestrator_web_manifest.md",
        tmp_path / "outputs/orchestrator_v1_summary.md",
        tmp_path / "docs/orchestrator_v1_summary.md",
    ):
        text = path.read_text(encoding="utf-8")
        assert str(tmp_path) not in text
        assert not ABSOLUTE_USER_PATH.search(text)


def test_formal_text_artifacts_do_not_expose_user_absolute_paths():
    for path in tracked_formal_text_artifacts():
        text = path.read_text(encoding="utf-8")
        assert not ABSOLUTE_USER_PATH.search(text), f"absolute user path found in {path.as_posix()}"
