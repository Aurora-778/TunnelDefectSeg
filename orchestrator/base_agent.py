"""Generic agent interface and file helpers for the orchestrator framework."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


class BaseAgent:
    """Base class for pluggable agents.

    Agents read from context["inputs"], write to context["outputs"], and may use
    context["shared"] for framework-level values such as project_root.
    """

    name = ""

    def __init__(self, name: str | None = None) -> None:
        self.name = name or self.name or self.__class__.__name__.replace("Agent", "").lower()

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def agent_inputs(self, context: dict[str, Any]) -> dict[str, Any]:
        return context.setdefault("inputs", {}).setdefault(self.name, {})

    def agent_outputs(self, context: dict[str, Any]) -> dict[str, Any]:
        return context.setdefault("outputs", {}).setdefault(self.name, {})

    def shared(self, context: dict[str, Any]) -> dict[str, Any]:
        return context.setdefault("shared", {})

    def project_root(self, context: dict[str, Any]) -> Path:
        return Path(self.shared(context).get("project_root", ".")).resolve()

    def resolve_path(self, context: dict[str, Any], value: str | Path) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.project_root(context) / path

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
