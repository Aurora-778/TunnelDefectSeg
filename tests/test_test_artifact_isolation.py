"""Guard representative CLI paths against writing tracked demo artifacts."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTECTED = [
    ROOT / "data/simulated",
    ROOT / "outputs/visualizations",
    ROOT / "outputs/association_benchmark",
    ROOT / "outputs/disease_engineering_report.md",
    ROOT / "outputs/disease_growth_analysis_report.md",
    ROOT / "outputs/disease_growth_analysis_summary.md",
    ROOT / "outputs/association_evaluation_report.md",
    ROOT / "outputs/visualization_report.md",
    ROOT / "outputs/visualization_summary.md",
    ROOT / "outputs/recheck_list_report.md",
    ROOT / "outputs/final_project_report.md",
]


def _snapshot() -> dict[Path, str]:
    files = []
    for path in PROTECTED:
        if path.is_dir():
            files.extend(child for child in path.rglob("*") if child.is_file())
        elif path.exists():
            files.append(path)
    return {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(files)}


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
    for command in commands:
        subprocess.run(command, cwd=ROOT, check=True, timeout=30, capture_output=True, text=True)

    assert _snapshot() == before
