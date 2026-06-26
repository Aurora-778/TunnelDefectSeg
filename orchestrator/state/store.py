"""Tiny JSON checkpoint store."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any


def save_checkpoint(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def make_state(run_id: str, context: dict[str, Any]) -> dict[str, Any]:
    status = context.get("task_status", {})
    return {
        "run_id": run_id,
        "task_status": status,
        "completed_tasks": [name for name, value in status.items() if value == "success"],
        "failed_tasks": [name for name, value in status.items() if value == "failed"],
        "context_snapshot": deepcopy(context),
    }
