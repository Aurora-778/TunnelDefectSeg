from __future__ import annotations

import hashlib
import os
import stat
import sys
from collections.abc import Callable
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


PROTECTED_ARTIFACTS = [
    PROJECT_ROOT / "data/simulated",
    PROJECT_ROOT / "logs",
    PROJECT_ROOT / "orchestrator/state/run_state.json",
    # Protect the complete formal outputs tree.  The explicitly excluded
    # directories below contain local video/demo WIP or caches and are not
    # part of the repository's formal artifact contract.
    PROJECT_ROOT / "outputs",
    PROJECT_ROOT / "outputs/current_publication_manifest.json",
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


_FORMAL_OUTPUT_EXCLUDED_DIRS = frozenset(
    {
        "video_inspection",
        "algorithm_visualization",
        ".pytest_cache",
        "__pycache__",
        ".cache",
        "cache",
        "caches",
    }
)


def _file_snapshot(path: Path) -> tuple[int, str]:
    """Hash one protected regular file without accepting a link replacement."""

    if _formal_entry_kind(path) != "file":
        raise AssertionError(
            f"protected formal artifact entry is not a regular file: {path.as_posix()}"
        )
    try:
        before = path.lstat()
    except (OSError, ValueError) as exc:
        raise AssertionError(
            f"cannot inspect protected formal artifact entry: {path.as_posix()}"
        ) from exc
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise AssertionError(
            f"cannot read protected formal artifact entry: {path.as_posix()}"
        ) from exc
    if _formal_entry_kind(path) != "file":
        raise AssertionError(
            f"protected formal artifact entry changed while being hashed: {path.as_posix()}"
        )
    try:
        after = path.lstat()
    except (OSError, ValueError) as exc:
        raise AssertionError(
            f"cannot inspect protected formal artifact entry after hashing: {path.as_posix()}"
        ) from exc
    if after.st_size != before.st_size:
        raise AssertionError(
            f"protected formal artifact size changed while being hashed: {path.as_posix()}"
        )
    return before.st_size, digest.hexdigest()


def _formal_entry_kind(path: Path) -> str | None:
    """Return a safe artifact kind without following links or reparse entries."""

    try:
        entry = path.lstat()
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise AssertionError(
            f"cannot inspect protected formal artifact entry: {path.as_posix()}"
        ) from exc
    attributes = getattr(entry, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
    if stat.S_ISLNK(entry.st_mode) or attributes & reparse_flag:
        raise AssertionError(
            f"protected formal artifact entry is a link or reparse point: {path.as_posix()}"
        )
    if stat.S_ISDIR(entry.st_mode):
        return "directory"
    if stat.S_ISREG(entry.st_mode):
        return "file"
    raise AssertionError(
        f"protected formal artifact entry is not a regular file or directory: {path.as_posix()}"
    )


def _walk_formal_entries(
    directory: Path,
    *,
    exclude: Callable[[Path], bool] | None = None,
) -> list[tuple[Path, str]]:
    """Enumerate regular formal entries without swallowing inaccessible directories."""

    try:
        with os.scandir(directory) as scan:
            children = sorted(scan, key=lambda item: item.name)
    except (OSError, ValueError) as exc:
        raise AssertionError(
            f"cannot enumerate protected formal artifact directory: {directory.as_posix()}"
        ) from exc
    result: list[tuple[Path, str]] = []
    for child in children:
        candidate = Path(child.path)
        if exclude is not None and exclude(candidate):
            continue
        kind = _formal_entry_kind(candidate)
        if kind is None:
            raise AssertionError(
                "protected formal artifact disappeared during snapshot: "
                f"{candidate.as_posix()}"
            )
        result.append((candidate, kind))
        if kind == "directory":
            result.extend(_walk_formal_entries(candidate, exclude=exclude))
    return result


def artifact_snapshot(
    paths: list[Path] | None = None,
    base: Path = PROJECT_ROOT,
) -> dict[str, tuple[str, int, str]]:
    """Capture protected files and empty directories without using mtimes."""

    manifest: dict[str, tuple[str, int, str]] = {}
    for protected in PROTECTED_ARTIFACTS if paths is None else paths:
        relative_root = protected.relative_to(base).as_posix()
        protected_kind = _formal_entry_kind(protected)
        if protected_kind is None:
            manifest[relative_root] = ("missing", 0, "")
            continue
        if paths is None and protected == PROJECT_ROOT / "outputs":
            if protected_kind != "directory":
                raise AssertionError("protected formal outputs entry is not a directory")
            manifest[relative_root] = ("directory", 0, "")
            for candidate, candidate_kind in _walk_formal_entries(
                protected,
                exclude=lambda item: any(
                    part in _FORMAL_OUTPUT_EXCLUDED_DIRS
                    for part in item.relative_to(protected).parts
                ),
            ):
                relative = candidate.relative_to(base).as_posix()
                if candidate_kind == "directory":
                    manifest[relative] = ("directory", 0, "")
                else:
                    size, digest = _file_snapshot(candidate)
                    manifest[relative] = (
                        "file",
                        size,
                        digest,
                    )
            continue
        candidates = [protected]
        if protected_kind == "directory":
            candidates.extend(
                candidate for candidate, _ in _walk_formal_entries(protected)
            )
        for candidate in candidates:
            relative = candidate.relative_to(base).as_posix()
            candidate_kind = _formal_entry_kind(candidate)
            if candidate_kind is None:
                raise AssertionError(
                    "protected formal artifact disappeared during snapshot: "
                    f"{candidate.as_posix()}"
                )
            if candidate_kind == "directory":
                manifest[relative] = ("directory", 0, "")
            else:
                size, digest = _file_snapshot(candidate)
                manifest[relative] = ("file", size, digest)
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
