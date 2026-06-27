"""Small API layer for the multi-agent engineering platform."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator.runs import RunManager
from orchestrator.state.store import load_checkpoint


def status_payload(project_root: Path, state_path: Path | None = None) -> dict[str, Any]:
    """Return current run progress from the latest run or the legacy checkpoint."""

    manager = RunManager(project_root)
    latest = manager.latest_run_id()
    if latest:
        state = manager.load_run(latest).get("state", {})
        run_id = latest
    else:
        state = load_checkpoint(state_path or project_root / "orchestrator" / "state" / "run_state.json")
        run_id = state.get("run_id")

    task_status = state.get("task_status", {})
    completed = [name for name, status in task_status.items() if status == "success"]
    failed = [name for name, status in task_status.items() if status == "failed"]
    running = [name for name, status in task_status.items() if status == "running"]
    total = len(task_status) or 1
    return {
        "ok": True,
        "run_id": run_id,
        "progress": round(len(completed) / total, 3),
        "running_task": running[0] if running else None,
        "completed": completed,
        "failed": failed,
        "task_status": task_status,
    }


def dag_payload(project_root: Path, run_id: str | None = None) -> dict[str, Any]:
    """Return DAG nodes, edges, and task statuses for one run."""

    manager = RunManager(project_root)
    selected = run_id or manager.latest_run_id()
    if not selected:
        return {"ok": True, "run_id": None, "nodes": [], "edges": [], "status": {}}
    dag = manager.load_run(selected).get("dag", {})
    return {
        "ok": True,
        "run_id": selected,
        "nodes": dag.get("nodes", []),
        "edges": dag.get("edges", []),
        "status": dag.get("status", {}),
    }


def runs_payload(project_root: Path) -> dict[str, Any]:
    """Return all known runs and default comparison data."""

    manager = RunManager(project_root)
    return {"ok": True, "runs": manager.list_runs(), "compare": manager.compare_runs()}


def run_payload(project_root: Path, run_id: str) -> dict[str, Any]:
    """Return one run, including replayable context versions and timeline."""

    manager = RunManager(project_root)
    try:
        return {"ok": True, **manager.load_run(run_id)}
    except FileNotFoundError:
        return {"ok": False, "error": f"run not found: {run_id}"}


def create_app(project_root: Path):
    """Create an optional Flask API app when Flask is installed."""

    try:
        from flask import Flask, jsonify
    except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("Flask is not installed; use web_app.py or install Flask.") from exc

    app = Flask(__name__)

    @app.get("/api/status")
    def status_route():
        return jsonify(status_payload(project_root))

    @app.get("/api/dag")
    def dag_route():
        return jsonify(dag_payload(project_root))

    @app.get("/api/runs")
    def runs_route():
        return jsonify(runs_payload(project_root))

    @app.get("/api/run/<run_id>")
    def run_route(run_id: str):
        return jsonify(run_payload(project_root, run_id))

    return app
