"""Guard representative CLI paths against writing tracked demo artifacts."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import conftest as artifact_conftest
from conftest import PROTECTED_ARTIFACTS as PROTECTED
from conftest import artifact_manifest_diff as _manifest_diff
from conftest import artifact_snapshot as _snapshot

ROOT = Path(__file__).resolve().parents[1]


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


def test_formal_outputs_snapshot_covers_public_artifacts_and_excludes_local_wip():
    before = _snapshot()

    assert "outputs" in before
    assert "outputs/current_publication_manifest.json" in before
    assert "outputs/visualizations" in before
    assert "outputs/progressive_evaluation" in before
    assert "outputs/association_benchmark" in before
    assert "logs" in before
    assert "orchestrator/state/run_state.json" in before
    assert not any(path.startswith("outputs/video_inspection/") for path in before)
    assert not any(path.startswith("outputs/algorithm_visualization/") for path in before)


def test_snapshot_fails_closed_for_reparse_and_unreadable_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protected = tmp_path / "protected"
    protected.mkdir()
    entry = protected / "report.md"
    entry.write_text("formal", encoding="utf-8")
    original_lstat = Path.lstat

    def reparse_lstat(path: Path):
        if path == entry:
            return SimpleNamespace(
                st_mode=stat.S_IFREG | 0o644,
                st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400),
            )
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    with pytest.raises(AssertionError, match="link or reparse point"):
        _snapshot([protected], tmp_path)

    def unreadable_lstat(path: Path):
        if path == entry:
            raise PermissionError("injected protected artifact inspection failure")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", unreadable_lstat)
    with pytest.raises(AssertionError, match="cannot inspect protected formal artifact"):
        _snapshot([protected], tmp_path)


def test_snapshot_fails_closed_for_linked_and_broken_entries(tmp_path: Path) -> None:
    protected = tmp_path / "protected"
    protected.mkdir()
    target = tmp_path / "target.md"
    target.write_text("formal", encoding="utf-8")
    linked = protected / "linked.md"
    broken = protected / "broken.md"
    try:
        linked.symlink_to(target)
        broken.symlink_to(tmp_path / "missing.md")
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable on this filesystem: {exc}")

    with pytest.raises(AssertionError, match="link or reparse point"):
        _snapshot([protected], tmp_path)
    broken.unlink()
    with pytest.raises(AssertionError, match="link or reparse point"):
        _snapshot([protected], tmp_path)


def test_snapshot_fails_closed_when_read_or_post_read_lstat_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protected = tmp_path / "protected"
    protected.mkdir()
    entry = protected / "report.md"
    entry.write_text("formal", encoding="utf-8")
    original_open = Path.open

    def unreadable_open(path: Path, *args, **kwargs):
        if path == entry:
            raise PermissionError("injected protected artifact read failure")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", unreadable_open)
    with pytest.raises(AssertionError, match="cannot read protected formal artifact"):
        _snapshot([protected], tmp_path)

    monkeypatch.undo()
    original_lstat = Path.lstat
    original_open = Path.open
    opened = {"value": False}

    def reparse_after_read(path: Path):
        if path == entry and opened["value"]:
            return SimpleNamespace(
                st_mode=stat.S_IFREG | 0o644,
                st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400),
            )
        return original_lstat(path)

    def mark_opened(path: Path, *args, **kwargs):
        if path == entry:
            opened["value"] = True
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", reparse_after_read)
    monkeypatch.setattr(Path, "open", mark_opened)
    with pytest.raises(AssertionError, match="link or reparse point"):
        _snapshot([protected], tmp_path)


def test_snapshot_fails_closed_when_formal_subdirectory_cannot_be_enumerated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protected = tmp_path / "protected"
    unreadable = protected / "unreadable"
    unreadable.mkdir(parents=True)
    (unreadable / "report.md").write_text("formal", encoding="utf-8")
    original_scandir = artifact_conftest.os.scandir

    def fail_subdirectory_scan(path):
        if Path(path) == unreadable:
            raise PermissionError("injected protected directory scan failure")
        return original_scandir(path)

    monkeypatch.setattr(artifact_conftest.os, "scandir", fail_subdirectory_scan)
    with pytest.raises(AssertionError, match="cannot enumerate protected formal artifact"):
        _snapshot([protected], tmp_path)


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
