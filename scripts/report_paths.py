"""Portable path labels for generated reports."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def report_path(path: Path | str, project_root: Path = PROJECT_ROOT) -> str:
    """Return a project-relative POSIX path, or an explicit external marker."""

    value = Path(path)
    root = project_root.resolve()
    resolved = value.resolve() if value.is_absolute() else (root / value).resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return f"external_input:{value.name or 'unnamed'}"
