"""Shared helpers for simple CSV-based agents."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


class BaseAgent:
    """Base class required by the orchestrator prompt."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.data_dir = project_root / "data" / "simulated"
        self.outputs_dir = project_root / "outputs"
        self.outputs_dir.mkdir(parents=True, exist_ok=True)

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def read_csv(self, path: Path) -> list[dict[str, str]]:
        if not path.exists():
            raise FileNotFoundError(path)
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))

    def write_csv(self, path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({name: row.get(name, "") for name in fieldnames})

    def write_markdown(self, path: Path, title: str, lines: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = [f"# {title}", ""]
        content.extend(lines)
        path.write_text("\n".join(content) + "\n", encoding="utf-8")
