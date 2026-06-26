"""Configuration-driven goal orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator.core.logger import GoalLogger


class Orchestrator:
    """Run registered agents in dependency-aware pipeline order."""

    def __init__(self, project_root: Path, config_path: Path) -> None:
        self.project_root = project_root
        self.config_path = config_path
        self.registry: dict[str, Any] = {}
        self.logs: list[dict[str, Any]] = []
        self.logger = GoalLogger(project_root)

    def register(self, name: str, agent: Any) -> None:
        self.registry[name] = agent

    def run_goal(self, goal_name: str, context: dict[str, Any]) -> dict[str, Any]:
        self.logger.goal_start(goal_name, context)
        try:
            agent = self.registry[goal_name]
            result = agent.run(context)
            if not isinstance(result, dict):
                raise TypeError(f"{goal_name} returned {type(result).__name__}, expected dict")
            self.logger.goal_end(goal_name, result)
            return result
        except Exception as exc:
            # v1 keeps later goals running so one failed agent does not stop the demo.
            self.logger.goal_failure(goal_name, exc)
            return {"failed_goal": goal_name, "failure_reason": repr(exc)}

    def run_pipeline(self, goals: list[str] | None = None) -> dict[str, Any]:
        config = self._load_config()
        pipeline = goals or config.get("pipeline", [])
        ordered_goals = self._resolve_dependencies(pipeline, config.get("goals", {}))

        self.logger.start_run()
        context: dict[str, Any] = {"project_root": str(self.project_root)}
        executed: list[str] = []
        failed: list[str] = []

        for goal_name in ordered_goals:
            result = self.run_goal(goal_name, context)
            context.update(result)
            if result.get("failed_goal"):
                failed.append(goal_name)
            else:
                executed.append(goal_name)

        context["executed_goals"] = executed
        context["failed_goals"] = failed
        self.logger.end_run()
        return context

    def _load_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            raise FileNotFoundError(f"Missing orchestrator config: {self.config_path}")

        text = self.config_path.read_text(encoding="utf-8")
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(text) or {}
            if not isinstance(data, dict):
                raise ValueError("goals.yaml must contain a mapping")
            return data
        except ModuleNotFoundError:
            return self._parse_minimal_yaml(text)

    def _parse_minimal_yaml(self, text: str) -> dict[str, Any]:
        """Parse the simple goals.yaml shape used by v1 when PyYAML is absent."""
        pipeline: list[str] = []
        goals: dict[str, dict[str, list[str]]] = {}
        section: str | None = None
        current_goal: str | None = None

        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped == "pipeline:":
                section = "pipeline"
                current_goal = None
                continue
            if stripped == "goals:":
                section = "goals"
                current_goal = None
                continue
            if section == "pipeline" and stripped.startswith("- "):
                pipeline.append(stripped[2:].strip())
                continue
            if section == "goals" and not raw_line.startswith("    ") and stripped.endswith(":"):
                current_goal = stripped[:-1]
                goals[current_goal] = {"depends_on": []}
                continue
            if section == "goals" and current_goal and stripped.startswith("depends_on:"):
                value = stripped.split(":", 1)[1].strip()
                goals[current_goal]["depends_on"] = [
                    item.strip() for item in value.strip("[]").split(",") if item.strip()
                ]

        return {"pipeline": pipeline, "goals": goals}

    def _resolve_dependencies(self, pipeline: list[str], goal_config: dict[str, Any]) -> list[str]:
        ordered: list[str] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(goal_name: str) -> None:
            if goal_name in visited:
                return
            if goal_name in visiting:
                raise ValueError(f"Dependency cycle detected at goal: {goal_name}")
            if goal_name not in self.registry:
                raise KeyError(f"Goal is not registered: {goal_name}")

            visiting.add(goal_name)
            depends_on = goal_config.get(goal_name, {}).get("depends_on", [])
            for dep in depends_on:
                visit(dep)
            visiting.remove(goal_name)
            visited.add(goal_name)
            ordered.append(goal_name)

        for goal in pipeline:
            visit(goal)
        return ordered
