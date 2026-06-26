"""DAG task executor."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
from time import perf_counter
from typing import Any

from orchestrator.dag.builder import Task
from orchestrator.dag.scheduler import execution_layers
from orchestrator.registry import AgentRegistry


class DAGExecutor:
    """Run DAG tasks layer by layer with small retry/cache support."""

    def __init__(self, registry: AgentRegistry, project_root: Path) -> None:
        self.registry = registry
        self.project_root = project_root
        self.log_path = project_root / "logs" / "dag_execution.json"
        self.cache_path = project_root / "logs" / "dag_cache.json"

    def run(self, tasks: dict[str, Task], context: dict[str, Any]) -> dict[str, Any]:
        context.setdefault("task_status", {name: "pending" for name in tasks})
        context.setdefault("outputs", {})
        events: list[dict[str, Any]] = []
        cache = self._read_json(self.cache_path)

        for layer in execution_layers(tasks):
            runnable = [name for name in layer if self._deps_ok(tasks[name], context)]
            for skipped in sorted(set(layer) - set(runnable)):
                context["task_status"][skipped] = "skipped"
                events.append(self._event(skipped, "skipped", reason="dependency failed"))

            with ThreadPoolExecutor(max_workers=max(1, len(runnable))) as pool:
                futures = {
                    pool.submit(self._run_task, tasks[name], context, cache): name
                    for name in runnable
                }
                for future in as_completed(futures):
                    name = futures[future]
                    status, result, event = future.result()
                    context["task_status"][name] = status
                    if result:
                        context["outputs"][name] = result
                    events.append(event)

        self._write_json(self.cache_path, cache)
        self._write_json(self.log_path, events)
        return context

    def _run_task(
        self,
        task: Task,
        context: dict[str, Any],
        cache: dict[str, Any],
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        key = self._cache_key(task, context)
        if task.cache and cache.get(task.name, {}).get("key") == key:
            return "success", cache[task.name]["result"], self._event(task.name, "success", cached=True)

        attempts = 0
        last_error = ""
        while attempts <= task.retries:
            attempts += 1
            start = perf_counter()
            context["task_status"][task.name] = "running"
            try:
                agent = self.registry.get(task.agent)
                # ponytail: shallow isolated context; enough for read-mostly inputs and deps outputs.
                task_context = {
                    "inputs": context.get("inputs", {}),
                    "outputs": deepcopy(context.get("outputs", {})),
                    "shared": context.get("shared", {}),
                    "task_status": context.get("task_status", {}),
                }
                result = agent.run(task_context)
                if task.cache:
                    cache[task.name] = {"key": key, "result": result}
                return "success", result, self._event(
                    task.name,
                    "success",
                    attempts=attempts,
                    runtime_seconds=perf_counter() - start,
                )
            except Exception as exc:
                last_error = repr(exc)

        return "failed", {}, self._event(task.name, "failed", attempts=attempts, error=last_error)

    def _deps_ok(self, task: Task, context: dict[str, Any]) -> bool:
        return all(context.get("task_status", {}).get(dep) == "success" for dep in task.deps)

    def _cache_key(self, task: Task, context: dict[str, Any]) -> str:
        payload = {
            "agent": task.agent,
            "inputs": context.get("inputs", {}).get(task.agent, {}),
            "deps": {dep: context.get("outputs", {}).get(dep, {}) for dep in task.deps},
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _event(self, task: str, status: str, **extra: Any) -> dict[str, Any]:
        return {"time": datetime.now().isoformat(timespec="seconds"), "task": task, "status": status, **extra}

    def _read_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
