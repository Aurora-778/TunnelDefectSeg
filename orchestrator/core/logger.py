"""Run logging for goal execution."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any


class GoalLogger:
    """Write human-readable and structured goal logs."""

    def __init__(self, project_root: Path) -> None:
        self.logs_dir = project_root / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.run_log_path = self.logs_dir / "run.log"
        self.goals_json_path = self.logs_dir / "goals.json"
        self.events: list[dict[str, Any]] = []

    def start_run(self) -> None:
        self.events = []
        self._write_line("=== orchestrator run start ===")

    def end_run(self) -> None:
        self._write_line("=== orchestrator run end ===")
        self.goals_json_path.write_text(
            json.dumps(self.events, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def goal_start(self, goal_name: str, context: dict[str, Any]) -> None:
        summary = self._summarize(context)
        event = {
            "time": self._now(),
            "event": "goal_start",
            "goal": goal_name,
            "input_summary": summary,
        }
        self.events.append(event)
        self._write_line(f"[START] {goal_name} inputs={summary}")

    def goal_end(self, goal_name: str, result: dict[str, Any]) -> None:
        summary = self._summarize(result)
        event = {
            "time": self._now(),
            "event": "goal_end",
            "goal": goal_name,
            "output_summary": summary,
        }
        self.events.append(event)
        self._write_line(f"[END] {goal_name} outputs={summary}")

    def goal_failure(self, goal_name: str, exc: Exception) -> None:
        event = {
            "time": self._now(),
            "event": "goal_failure",
            "goal": goal_name,
            "failure_reason": repr(exc),
        }
        self.events.append(event)
        self._write_line(f"[FAIL] {goal_name} reason={exc!r}")

    def _write_line(self, message: str) -> None:
        timestamp = self._now()
        with self.run_log_path.open("a", encoding="utf-8") as f:
            f.write(f"{timestamp} {message}\n")

    def _summarize(self, payload: dict[str, Any]) -> dict[str, str]:
        # Keep logs readable: store compact value previews instead of full tables.
        summary: dict[str, str] = {}
        for key, value in payload.items():
            text = str(value)
            summary[key] = text if len(text) <= 160 else text[:157] + "..."
        return summary

    def _now(self) -> str:
        return datetime.now().isoformat(timespec="seconds")
