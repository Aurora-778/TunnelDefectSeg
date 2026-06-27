"""Manage repeatable orchestrator runs and their artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any


RUN_PATTERN = re.compile(r"^run_(\d{3,})$")


@dataclass(frozen=True)
class RunInfo:
    """Small value object describing one run directory."""

    run_id: str
    run_dir: Path
    metadata_path: Path


class RunManager:
    """Create, load, list, and compare platform run artifacts."""

    def __init__(self, project_root: Path, runs_dir: Path | None = None) -> None:
        self.project_root = Path(project_root)
        self.runs_dir = runs_dir or self.project_root / "runs"

    def create_run(self, dag_config: str | None = None) -> RunInfo:
        """Create the next run_NNN folder and write initial metadata."""

        self.runs_dir.mkdir(parents=True, exist_ok=True)
        run_number = self._next_run_number()
        run_id = f"run_{run_number:03d}"
        run_dir = self.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        info = RunInfo(run_id=run_id, run_dir=run_dir, metadata_path=run_dir / "metadata.json")
        self.write_json(
            info.metadata_path,
            {
                "run_id": run_id,
                "created_at": self._now(),
                "updated_at": self._now(),
                "dag_config": dag_config,
                "status": "created",
            },
        )
        return info

    def load_run(self, run_id: str) -> dict[str, Any]:
        """Load a run summary with metadata, state, DAG JSON, and timeline."""

        run_dir = self.runs_dir / run_id
        if not run_dir.exists():
            raise FileNotFoundError(f"run not found: {run_id}")
        return {
            "run_id": run_id,
            "metadata": self.read_json(run_dir / "metadata.json"),
            "state": self.read_json(run_dir / "state.json"),
            "dag": self.read_json(run_dir / "dag.json"),
            "timeline": self.read_json(run_dir / "timeline.json", default=[]),
            "contexts": [path.name for path in sorted(run_dir.glob("context_v*.json"))],
            "artifacts": [path.name for path in sorted(run_dir.iterdir()) if path.is_file()],
        }

    def list_runs(self) -> list[dict[str, Any]]:
        """Return newest-last run summaries for the dashboard and API."""

        summaries: list[dict[str, Any]] = []
        for run_dir in self._run_dirs():
            metadata = self.read_json(run_dir / "metadata.json")
            state = self.read_json(run_dir / "state.json")
            task_status = state.get("task_status", {})
            summaries.append(
                {
                    "run_id": run_dir.name,
                    "created_at": metadata.get("created_at"),
                    "updated_at": metadata.get("updated_at"),
                    "status": metadata.get("status", self._overall_status(task_status)),
                    "task_status": task_status,
                    "completed_tasks": state.get("completed_tasks", []),
                    "failed_tasks": state.get("failed_tasks", []),
                }
            )
        return summaries

    def latest_run_id(self) -> str | None:
        """Return the newest run id, if one exists."""

        run_dirs = self._run_dirs()
        return run_dirs[-1].name if run_dirs else None

    def compare_runs(self, left_id: str | None = None, right_id: str | None = None) -> dict[str, Any]:
        """Compare two runs by status, output keys, and context versions."""

        runs = self.list_runs()
        if not runs:
            return {"ok": True, "runs": [], "diff": {}}
        if left_id is None or right_id is None:
            selected = runs[-2:] if len(runs) >= 2 else runs[-1:]
            left_id = left_id or selected[0]["run_id"]
            right_id = right_id or selected[-1]["run_id"]

        left = self.load_run(left_id)
        right = self.load_run(right_id)
        left_context = left.get("state", {}).get("context_snapshot", {})
        right_context = right.get("state", {}).get("context_snapshot", {})
        left_output_map = left_context.get("outputs", {})
        right_output_map = right_context.get("outputs", {})
        left_outputs = set(left_output_map)
        right_outputs = set(right_output_map)
        left_memory = left_output_map.get("memory", {})
        right_memory = right_output_map.get("memory", {})
        left_memory_keys = set(left_memory) if isinstance(left_memory, dict) else set()
        right_memory_keys = set(right_memory) if isinstance(right_memory, dict) else set()
        return {
            "ok": True,
            "left_run_id": left_id,
            "right_run_id": right_id,
            "diff": {
                "status_changed": left.get("state", {}).get("task_status", {}) != right.get("state", {}).get("task_status", {}),
                "outputs_added": sorted(right_outputs - left_outputs),
                "outputs_removed": sorted(left_outputs - right_outputs),
                "memory_diff": {
                    "changed": left_memory != right_memory,
                    "keys_added": sorted(right_memory_keys - left_memory_keys),
                    "keys_removed": sorted(left_memory_keys - right_memory_keys),
                },
                "left_context_versions": left.get("contexts", []),
                "right_context_versions": right.get("contexts", []),
                "left_timeline_events": len(left.get("timeline", [])),
                "right_timeline_events": len(right.get("timeline", [])),
            },
        }

    def save_context_version(self, run_id: str, context: dict[str, Any]) -> Path:
        """Persist the next context_vN.json snapshot for replay."""

        run_dir = self.runs_dir / run_id
        version = len(list(run_dir.glob("context_v*.json"))) + 1
        path = run_dir / f"context_v{version}.json"
        self.write_json(path, context)
        return path

    def update_metadata(self, run_id: str, **updates: Any) -> None:
        """Patch metadata while keeping timestamps centralized."""

        path = self.runs_dir / run_id / "metadata.json"
        metadata = self.read_json(path)
        metadata.update(updates)
        metadata["updated_at"] = self._now()
        self.write_json(path, metadata)

    def write_json(self, path: Path, payload: Any) -> None:
        """Write JSON using the same encoding everywhere."""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def read_json(self, path: Path, default: Any | None = None) -> Any:
        """Read JSON and return a safe default when the file is missing or partial."""

        if default is None:
            default = {}
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default

    def _next_run_number(self) -> int:
        numbers = []
        for run_dir in self._run_dirs():
            match = RUN_PATTERN.match(run_dir.name)
            if match:
                numbers.append(int(match.group(1)))
        return max(numbers, default=0) + 1

    def _run_dirs(self) -> list[Path]:
        if not self.runs_dir.exists():
            return []
        return sorted(
            [path for path in self.runs_dir.iterdir() if path.is_dir() and RUN_PATTERN.match(path.name)],
            key=lambda path: int(RUN_PATTERN.match(path.name).group(1)),  # type: ignore[union-attr]
        )

    def _overall_status(self, task_status: dict[str, str]) -> str:
        if not task_status:
            return "created"
        if any(status == "failed" for status in task_status.values()):
            return "failed"
        if all(status == "success" for status in task_status.values()):
            return "success"
        if any(status == "running" for status in task_status.values()):
            return "running"
        return "partial"

    def _now(self) -> str:
        return datetime.now().isoformat(timespec="seconds")
