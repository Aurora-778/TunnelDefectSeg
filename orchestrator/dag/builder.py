"""Build DAG tasks from YAML."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Task:
    name: str
    agent: str
    deps: list[str]
    retries: int = 3
    cache: bool = True


def load_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError(f"YAML root must be a mapping: {path}")
        return data
    except ModuleNotFoundError:
        return _parse_minimal_dag_yaml(text)


def build_dag(path: Path) -> tuple[dict[str, Task], dict[str, Any]]:
    config = load_yaml(path)
    raw_tasks = config.get("tasks", {})
    if not isinstance(raw_tasks, dict) or not raw_tasks:
        raise ValueError("DAG config must contain non-empty tasks")

    tasks: dict[str, Task] = {}
    for name, raw in raw_tasks.items():
        raw = raw or {}
        deps = raw.get("deps", [])
        tasks[name] = Task(
            name=name,
            agent=raw.get("agent", name),
            deps=list(deps),
            retries=int(raw.get("retries", 2)),
            cache=bool(raw.get("cache", True)),
        )

    missing = sorted({dep for task in tasks.values() for dep in task.deps if dep not in tasks})
    if missing:
        raise ValueError(f"DAG references missing task(s): {missing}")
    _check_cycles(tasks)
    return tasks, config


def _check_cycles(tasks: dict[str, Task]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visited:
            return
        if name in visiting:
            raise ValueError(f"DAG cycle detected at task: {name}")
        visiting.add(name)
        for dep in tasks[name].deps:
            visit(dep)
        visiting.remove(name)
        visited.add(name)

    for name in tasks:
        visit(name)


def _parse_minimal_dag_yaml(text: str) -> dict[str, Any]:
    # ponytail: tiny fallback parser for this repo's YAML shape; use PyYAML for richer YAML.
    data: dict[str, Any] = {"tasks": {}, "inputs": {}, "shared": {}}
    section: str | None = None
    current_task: str | None = None
    current_agent: str | None = None

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent == 0 and stripped.endswith(":"):
            section = stripped[:-1]
            current_task = None
            current_agent = None
            data.setdefault(section, {})
            continue
        if section == "tasks" and indent == 2 and stripped.endswith(":"):
            current_task = stripped[:-1]
            data["tasks"][current_task] = {}
            continue
        if section == "tasks" and current_task and indent >= 4 and ":" in stripped:
            key, value = stripped.split(":", 1)
            data["tasks"][current_task][key.strip()] = _parse_value(value.strip())
            continue
        if section == "inputs" and indent == 2 and stripped.endswith(":"):
            current_agent = stripped[:-1]
            data["inputs"][current_agent] = {}
            continue
        if section == "inputs" and current_agent and indent >= 4 and ":" in stripped:
            key, value = stripped.split(":", 1)
            data["inputs"][current_agent][key.strip()] = value.strip()
            continue
        if section == "shared" and ":" in stripped:
            key, value = stripped.split(":", 1)
            data["shared"][key.strip()] = value.strip()
    return data


def _parse_value(value: str) -> Any:
    if value.startswith("[") and value.endswith("]"):
        return [item.strip() for item in value.strip("[]").split(",") if item.strip()]
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    return value
