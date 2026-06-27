"""Async DAG task executor with checkpointing."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
from time import perf_counter
from typing import Any

from orchestrator.dag.builder import Task
from orchestrator.dag.scheduler import execution_layers
from orchestrator.queue.task_queue import TaskQueue
from orchestrator.registry import AgentRegistry
from orchestrator.runs.manager import RunInfo, RunManager
from orchestrator.state.store import load_checkpoint, make_state, save_checkpoint


class DAGExecutor:
    """Run DAG tasks layer by layer with retry, cache, and checkpoint support."""

    def __init__(
        self,
        registry: AgentRegistry,
        project_root: Path,
        *,
        resume: bool = False,
        debug: bool = False,
        dag_config: str | None = None,
        run_id: str | None = None,
    ) -> None:
        self.registry = registry
        self.project_root = project_root
        self.resume = resume
        self.debug = debug
        self.run_manager = RunManager(project_root)
        self.run_info = self._select_run(dag_config=dag_config, run_id=run_id)
        self.log_path = project_root / "logs" / "dag_execution.json"
        self.trace_path = project_root / "logs" / "execution_trace.json"
        self.cache_path = project_root / "logs" / "dag_cache.json"
        self.state_path = project_root / "orchestrator" / "state" / "run_state.json"
        self.run_id = self.run_info.run_id
        self.run_log_path = self.run_info.run_dir / "dag_execution.json"
        self.run_trace_path = self.run_info.run_dir / "execution_trace.json"
        self.run_state_path = self.run_info.run_dir / "state.json"
        self.run_timeline_path = self.run_info.run_dir / "timeline.json"
        self.run_dag_path = self.run_info.run_dir / "dag.json"

    def run(self, tasks: dict[str, Task], context: dict[str, Any]) -> dict[str, Any]:
        return asyncio.run(self.run_async(tasks, context))

    async def run_async(self, tasks: dict[str, Task], context: dict[str, Any]) -> dict[str, Any]:
        context = self._resume_context(context) if self.resume else context
        context.setdefault("task_status", {name: "pending" for name in tasks})
        context.setdefault("outputs", {})
        for name in tasks:
            context["task_status"].setdefault(name, "pending")
        context.setdefault("shared", {})
        context["shared"]["run_id"] = self.run_id

        events: list[dict[str, Any]] = []
        cache = self._read_json(self.cache_path)
        self._write_dag_json(tasks, context)
        self.run_manager.update_metadata(self.run_id, status="running")
        self._checkpoint(context)

        for layer in execution_layers(tasks):
            runnable = [name for name in layer if context["task_status"].get(name) != "success"]
            runnable = [name for name in runnable if self._deps_ok(tasks[name], context)]
            for skipped in sorted(set(layer) - set(runnable)):
                if context["task_status"].get(skipped) == "success":
                    continue
                context["task_status"][skipped] = "skipped"
                self._append_event(events, self._event(skipped, "skipped", reason="dependency failed"))
                self._checkpoint(context)
                self._write_dag_json(tasks, context)

            queue = TaskQueue()
            for name in runnable:
                queue.enqueue(name)
            batch = []
            while True:
                name = queue.dequeue()
                if name is None:
                    break
                batch.append(name)

            coros = [self._run_task(tasks[name], context, cache, events, queue) for name in batch]
            for done in asyncio.as_completed(coros):
                name, status, result, event = await done
                context["task_status"][name] = status
                if result:
                    context["outputs"][name] = result
                self._append_event(events, event)
                self._checkpoint(context)
                self._write_dag_json(tasks, context)

        self._write_json(self.cache_path, cache)
        self._write_json(self.log_path, events)
        self._write_json(self.trace_path, events)
        self._write_json(self.run_log_path, events)
        self._write_json(self.run_trace_path, events)
        self._write_json(self.run_timeline_path, events)
        self.run_manager.update_metadata(self.run_id, status=self._overall_status(context))
        return context

    async def _run_task(
        self,
        task: Task,
        context: dict[str, Any],
        cache: dict[str, Any],
        events: list[dict[str, Any]],
        queue: TaskQueue,
    ) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
        key = self._cache_key(task, context)
        if task.cache and cache.get(task.name, {}).get("key") == key:
            return task.name, "success", cache[task.name]["result"], self._event(
                task.name,
                "success",
                cached=True,
                retry_count=0,
                start_time=datetime.now().isoformat(timespec="seconds"),
                end_time=datetime.now().isoformat(timespec="seconds"),
                duration_seconds=0.0,
            )

        attempts = 0
        last_error = ""
        while attempts <= task.retries:
            attempts += 1
            start = perf_counter()
            start_time = datetime.now().isoformat(timespec="seconds")
            context["task_status"][task.name] = "running"
            self._append_event(events, self._event(task.name, "running", start_time=start_time, retry_count=attempts - 1))
            self._checkpoint(context)
            try:
                agent = self.registry.get(task.agent)
                # ponytail: sync agents run in a worker thread; replace with native async agents if needed.
                task_inputs = self._task_inputs(task, context)
                inputs = deepcopy(context.get("inputs", {}))
                inputs[task.agent] = task_inputs
                inputs[task.name] = task_inputs
                task_context = {
                    "inputs": inputs,
                    "outputs": deepcopy(context.get("outputs", {})),
                    "shared": context.get("shared", {}),
                    "task_status": context.get("task_status", {}),
                }
                result = await asyncio.to_thread(agent.run, task_context)
                if task.cache:
                    cache[task.name] = {"key": key, "result": result}
                duration = perf_counter() - start
                return task.name, "success", result, self._event(
                    task.name,
                    "success",
                    retry_count=attempts - 1,
                    runtime_seconds=duration,
                    start_time=start_time,
                    end_time=datetime.now().isoformat(timespec="seconds"),
                    duration_seconds=duration,
                )
            except Exception as exc:
                last_error = repr(exc)
                if attempts <= task.retries:
                    queue.mark_retry(task.name)
                    self._append_event(
                        events,
                        self._event(
                            task.name,
                            "retry",
                            retry_count=attempts,
                            error=last_error,
                            start_time=start_time,
                            end_time=datetime.now().isoformat(timespec="seconds"),
                            duration_seconds=perf_counter() - start,
                        ),
                    )
                    await asyncio.sleep(min(0.1 * (2 ** (attempts - 1)), 1.0))

        queue.mark_failed(task.name)
        return task.name, "failed", {}, self._event(task.name, "failed", retry_count=attempts - 1, error=last_error)

    def _resume_context(self, context: dict[str, Any]) -> dict[str, Any]:
        state = load_checkpoint(self.run_state_path) or load_checkpoint(self.state_path)
        snapshot = state.get("context_snapshot")
        if not snapshot:
            return context
        snapshot["inputs"] = context.get("inputs", snapshot.get("inputs", {}))
        snapshot["shared"] = {**snapshot.get("shared", {}), **context.get("shared", {})}
        return snapshot

    def _checkpoint(self, context: dict[str, Any]) -> None:
        state = make_state(self.run_id, context)
        save_checkpoint(self.state_path, state)
        save_checkpoint(self.run_state_path, state)
        self.run_manager.save_context_version(self.run_id, context)

    def _deps_ok(self, task: Task, context: dict[str, Any]) -> bool:
        return all(context.get("task_status", {}).get(dep) == "success" for dep in task.deps)

    def _cache_key(self, task: Task, context: dict[str, Any]) -> str:
        payload = {
            "agent": task.agent,
            "inputs": self._fingerprint(self._task_inputs(task, context), context),
            "deps": {dep: context.get("outputs", {}).get(dep, {}) for dep in task.deps},
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _task_inputs(self, task: Task, context: dict[str, Any]) -> dict[str, Any]:
        inputs = context.get("inputs", {})
        return inputs.get(task.name) or inputs.get(task.agent, {})

    def _fingerprint(self, value: Any, context: dict[str, Any], key_name: str = "") -> Any:
        if isinstance(value, dict):
            return {key: self._fingerprint(item, context, key) for key, item in value.items()}
        if isinstance(value, list):
            return [self._fingerprint(item, context, key_name) for item in value]
        if isinstance(value, str):
            path = Path(value)
            if not path.is_absolute():
                path = self.project_root / path
            if path.is_file() and not self._looks_like_output_key(key_name):
                # ponytail: content hash is enough; no metadata database until files get huge.
                return {"path": value, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        return value

    def _looks_like_output_key(self, key_name: str) -> bool:
        return key_name in {"output_path", "report_path", "summary_path", "log_path", "manifest_path", "docs_path"}

    def _append_event(self, events: list[dict[str, Any]], event: dict[str, Any]) -> None:
        events.append({"run_id": self.run_id, **event})
        self._write_json(self.trace_path, events)
        self._write_json(self.run_trace_path, events)
        self._write_json(self.run_timeline_path, events)

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
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def _select_run(self, *, dag_config: str | None, run_id: str | None) -> RunInfo:
        if run_id:
            run_dir = self.run_manager.runs_dir / run_id
            if not run_dir.exists():
                raise FileNotFoundError(f"run not found: {run_id}")
            return RunInfo(run_id=run_id, run_dir=run_dir, metadata_path=run_dir / "metadata.json")
        if self.resume:
            latest = self.run_manager.latest_run_id()
            if latest:
                run_dir = self.run_manager.runs_dir / latest
                return RunInfo(run_id=latest, run_dir=run_dir, metadata_path=run_dir / "metadata.json")
        return self.run_manager.create_run(dag_config=dag_config)

    def _write_dag_json(self, tasks: dict[str, Task], context: dict[str, Any]) -> None:
        payload = {
            "nodes": [
                {"id": name, "agent": task.agent, "status": context.get("task_status", {}).get(name, "pending")}
                for name, task in tasks.items()
            ],
            "edges": [{"source": dep, "target": name} for name, task in tasks.items() for dep in task.deps],
            "status": context.get("task_status", {}),
        }
        self._write_json(self.run_dag_path, payload)

    def _overall_status(self, context: dict[str, Any]) -> str:
        statuses = context.get("task_status", {})
        if not statuses:
            return "created"
        if any(status == "failed" for status in statuses.values()):
            return "failed"
        if all(status == "success" for status in statuses.values()):
            return "success"
        return "partial"
