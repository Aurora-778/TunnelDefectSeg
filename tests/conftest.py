from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


PROTECTED_ARTIFACTS = [
    PROJECT_ROOT / "data/simulated",
    PROJECT_ROOT / "logs",
    PROJECT_ROOT / "orchestrator/state/run_state.json",
    PROJECT_ROOT / "outputs/visualizations",
    PROJECT_ROOT / "outputs/association_benchmark",
    PROJECT_ROOT / "outputs/progressive_evaluation",
    PROJECT_ROOT / "outputs/disease_engineering_report.md",
    PROJECT_ROOT / "outputs/disease_engineering_report_summary.md",
    PROJECT_ROOT / "outputs/disease_growth_analysis_report.md",
    PROJECT_ROOT / "outputs/disease_growth_analysis_summary.md",
    PROJECT_ROOT / "outputs/disease_memory_bank_summary.md",
    PROJECT_ROOT / "outputs/memory_agent_report.md",
    PROJECT_ROOT / "outputs/association_evaluation_report.md",
    PROJECT_ROOT / "outputs/visualization_report.md",
    PROJECT_ROOT / "outputs/visualization_summary.md",
    PROJECT_ROOT / "outputs/recheck_list_report.md",
    PROJECT_ROOT / "outputs/final_project_report.md",
    PROJECT_ROOT / "outputs/system_summary.md",
    PROJECT_ROOT / "outputs/key_insights.md",
    PROJECT_ROOT / "outputs/orchestrator_v1_summary.md",
    PROJECT_ROOT / "outputs/orchestrator_web_manifest.md",
    PROJECT_ROOT / "outputs/project_review_report.md",
    PROJECT_ROOT / "outputs/robot_kict_merge_report.md",
    PROJECT_ROOT / "docs/orchestrator_v1_summary.md",
]


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_snapshot(
    paths: list[Path] | None = None,
    base: Path = PROJECT_ROOT,
) -> dict[str, tuple[str, int, str]]:
    """Capture protected files and empty directories without using mtimes."""

    manifest: dict[str, tuple[str, int, str]] = {}
    for protected in PROTECTED_ARTIFACTS if paths is None else paths:
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


def artifact_manifest_diff(
    before: dict[str, tuple[str, int, str]],
    after: dict[str, tuple[str, int, str]],
) -> tuple[set[str], set[str], set[str]]:
    added = set(after) - set(before)
    removed = set(before) - set(after)
    modified = {path for path in set(before) & set(after) if before[path] != after[path]}
    return added, removed, modified


@pytest.fixture(scope="session", autouse=True)
def preserve_formal_artifacts_for_test_session():
    """Fail the test session if any test mutates a formal repository artifact."""

    before = artifact_snapshot()
    yield
    added, removed, modified = artifact_manifest_diff(before, artifact_snapshot())
    assert not added, f"formal artifacts added during test session: {sorted(added)}"
    assert not removed, f"formal artifacts removed during test session: {sorted(removed)}"
    assert not modified, f"formal artifacts modified during test session: {sorted(modified)}"
