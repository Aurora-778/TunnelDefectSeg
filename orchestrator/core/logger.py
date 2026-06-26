"""Run logging for pipeline and agent execution."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any


class GoalLogger:
    """Write human-readable and structured pipeline logs."""

    def __init__(self, project_root: Path) -> None:
        self.logs_dir = project_root / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.run_log_path = self.logs_dir / "run.log"
        self.pipeline_json_path = self.logs_dir / "pipeline.json"
        self.agent_json_path = self.logs_dir / "agent.json"
        self.goals_json_path = self.logs_dir / "goals.json"
        self.pipeline_events: list[dict[str, Any]] = []
        self.agent_events: list[dict[str, Any]] = []

    def start_run(self, pipeline: list[str]) -> None:
        self.pipeline_events = []
        self.agent_events = []
        event = {"time": self._now(), "event": "pipeline_start", "pipeline": pipeline}
        self.pipeline_events.append(event)
        self._write_line(f"=== pipeline start: {pipeline} ===")

    def end_run(self, context: dict[str, Any]) -> None:
        event = {
            "time": self._now(),
            "event": "pipeline_end",
            "output_summary": self._summarize(context.get("outputs", {})),
        }
        self.pipeline_events.append(event)
        self._write_line("=== pipeline end ===")
        self.pipeline_json_path.write_text(
            json.dumps(self.pipeline_events, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.agent_json_path.write_text(
            json.dumps(self.agent_events, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # Keep the v1 filename as a compatibility alias for older checks.
        self.goals_json_path.write_text(
            json.dumps(self.agent_events, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def agent_start(self, agent_name: str, context: dict[str, Any]) -> None:
        summary = self._summarize(context)
        event = {
            "time": self._now(),
            "event": "agent_start",
            "agent": agent_name,
            "input_summary": summary,
        }
        self.agent_events.append(event)
        self._write_line(f"[START] {agent_name} inputs={summary}")

    def agent_end(self, agent_name: str, result: dict[str, Any], runtime_seconds: float) -> None:
        summary = self._summarize(result)
        event = {
            "time": self._now(),
            "event": "agent_end",
            "agent": agent_name,
            "runtime_seconds": round(runtime_seconds, 6),
            "output_summary": summary,
        }
        self.agent_events.append(event)
        self._write_line(f"[END] {agent_name} runtime={runtime_seconds:.3f}s outputs={summary}")

    def agent_failure(self, agent_name: str, exc: Exception, runtime_seconds: float) -> None:
        event = {
            "time": self._now(),
            "event": "agent_failure",
            "agent": agent_name,
            "runtime_seconds": round(runtime_seconds, 6),
            "failure_reason": repr(exc),
        }
        self.agent_events.append(event)
        self._write_line(f"[FAIL] {agent_name} runtime={runtime_seconds:.3f}s reason={exc!r}")

    # Compatibility wrappers for the v1 method names.
    def goal_start(self, goal_name: str, context: dict[str, Any]) -> None:
        self.agent_start(goal_name, context)

    def goal_end(self, goal_name: str, result: dict[str, Any]) -> None:
        self.agent_end(goal_name, result, 0.0)

    def goal_failure(self, goal_name: str, exc: Exception) -> None:
        self.agent_failure(goal_name, exc, 0.0)

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
