"""Guard representative CLI paths against writing tracked demo artifacts."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTECTED = [
    ROOT / "data/simulated",
    ROOT / "outputs/visualizations",
    ROOT / "outputs/association_benchmark",
    ROOT / "outputs/progressive_evaluation",
    ROOT / "outputs/disease_engineering_report.md",
    ROOT / "outputs/disease_engineering_report_summary.md",
    ROOT / "outputs/disease_growth_analysis_report.md",
    ROOT / "outputs/disease_growth_analysis_summary.md",
    ROOT / "outputs/disease_memory_bank_summary.md",
    ROOT / "outputs/memory_agent_report.md",
    ROOT / "outputs/association_evaluation_report.md",
    ROOT / "outputs/visualization_report.md",
    ROOT / "outputs/visualization_summary.md",
    ROOT / "outputs/recheck_list_report.md",
    ROOT / "outputs/final_project_report.md",
    ROOT / "outputs/system_summary.md",
    ROOT / "outputs/key_insights.md",
    ROOT / "outputs/orchestrator_v1_summary.md",
    ROOT / "outputs/orchestrator_web_manifest.md",
    ROOT / "outputs/project_review_report.md",
    ROOT / "outputs/robot_kict_merge_report.md",
    ROOT / "docs/orchestrator_v1_summary.md",
]


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot(paths: list[Path] | None = None, base: Path = ROOT) -> dict[str, tuple[str, int, str]]:
    """Capture names, structure, sizes and content without relying on mtimes."""

    manifest: dict[str, tuple[str, int, str]] = {}
    for protected in PROTECTED if paths is None else paths:
        relative_root = protected.relative_to(base).as_posix()
        if not protected.exists():
            manifest[relative_root] = ("missing", 0, "")
            continue
        candidates = [protected]
        if protected.is_dir():
            candidates.extend(sorted(protected.rglob("*")))
        for candidate in candidates:
            relative = candidate.relative_to(base).as_posix()
            if candidate.is_dir():
                manifest[relative] = ("directory", 0, "")
            else:
                manifest[relative] = ("file", candidate.stat().st_size, _file_digest(candidate))
    return manifest


def _manifest_diff(
    before: dict[str, tuple[str, int, str]],
    after: dict[str, tuple[str, int, str]],
) -> tuple[set[str], set[str], set[str]]:
    added = set(after) - set(before)
    removed = set(before) - set(after)
    modified = {path for path in set(before) & set(after) if before[path] != after[path]}
    return added, removed, modified


def test_manifest_detects_added_removed_modified_and_directory_changes(tmp_path):
    protected = tmp_path / "protected"
    protected.mkdir()
    modified_file = protected / "modified.txt"
    removed_file = protected / "removed.txt"
    removed_dir = protected / "removed-empty-dir"
    modified_file.write_text("before", encoding="utf-8")
    removed_file.write_text("remove", encoding="utf-8")
    removed_dir.mkdir()
    before = _snapshot([protected], tmp_path)

    modified_file.write_text("after", encoding="utf-8")
    removed_file.unlink()
    removed_dir.rmdir()
    (protected / "added.txt").write_text("added", encoding="utf-8")
    (protected / "added-empty-dir").mkdir()
    added, removed, modified = _manifest_diff(before, _snapshot([protected], tmp_path))

    assert added == {"protected/added.txt", "protected/added-empty-dir"}
    assert removed == {"protected/removed.txt", "protected/removed-empty-dir"}
    assert modified == {"protected/modified.txt"}


def test_representative_cli_generation_keeps_formal_artifacts_unchanged(tmp_path):
    before = _snapshot()
    engineering_dir = tmp_path / "engineering"
    growth_dir = tmp_path / "growth"
    visualization_dir = tmp_path / "visualization"
    progressive_dir = tmp_path / "progressive"
    benchmark_dir = tmp_path / "benchmark"
    commands = [
        [sys.executable, "scripts/generate_engineering_report.py", "--input-csv", "data/simulated/robot_kict_frame_records.csv", "--output-csv", str(engineering_dir / "report.csv"), "--markdown-report", str(engineering_dir / "report.md"), "--summary-report", str(engineering_dir / "summary.md")],
        [sys.executable, "scripts/analyze_disease_growth.py", "--input-csv", "data/simulated/disease_engineering_report.csv", "--output-csv", str(growth_dir / "growth.csv"), "--markdown-report", str(growth_dir / "growth.md"), "--summary-report", str(growth_dir / "summary.md")],
        [sys.executable, "scripts/generate_visualization_and_recheck_list.py", "--growth-csv", "data/simulated/disease_growth_results.csv", "--engineering-csv", "data/simulated/disease_engineering_report.csv", "--recheck-csv", str(visualization_dir / "recheck.csv"), "--visualization-dir", str(visualization_dir / "charts"), "--visualization-report", str(visualization_dir / "report.md"), "--recheck-report", str(visualization_dir / "recheck.md"), "--summary-report", str(visualization_dir / "summary.md")],
        [sys.executable, "scripts/run_progressive_inspection_evaluation.py", "--input-csv", "data/simulated/robot_kict_frame_records.csv", "--output-dir", str(progressive_dir / "rounds"), "--no-id-csv", str(progressive_dir / "no_id.csv"), "--with-id-csv", str(progressive_dir / "with_id.csv"), "--report-path", str(progressive_dir / "report.md")],
        [sys.executable, "scripts/run_association_benchmark.py", "--output-dir", str(benchmark_dir)],
    ]
    env = os.environ.copy()
    env["FAST_TEST_MODE"] = "1"
    for command in commands:
        subprocess.run(command, cwd=ROOT, check=True, timeout=30, capture_output=True, text=True, env=env)

    added, removed, modified = _manifest_diff(before, _snapshot())
    assert not added, f"formal artifacts added: {sorted(added)}"
    assert not removed, f"formal artifacts removed: {sorted(removed)}"
    assert not modified, f"formal artifacts modified: {sorted(modified)}"
