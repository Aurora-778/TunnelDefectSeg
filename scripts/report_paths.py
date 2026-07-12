"""Portable path labels for generated reports."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def report_path(path: Path | str, project_root: Path = PROJECT_ROOT) -> str:
    """Return a project-relative POSIX path, or an explicit external marker."""

    raw_value = str(path)
    value = Path(raw_value)
    root = project_root.resolve()
    if value.is_absolute():
        resolved = value.resolve()
    elif PureWindowsPath(raw_value).is_absolute() or PurePosixPath(raw_value).is_absolute():
        name = PureWindowsPath(raw_value).name if PureWindowsPath(raw_value).is_absolute() else PurePosixPath(raw_value).name
        return f"external_input:{name or 'unnamed'}"
    else:
        resolved = (root / value).resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return f"external_input:{value.name or 'unnamed'}"
