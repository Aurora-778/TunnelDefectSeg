"""Project-level runner for the robot tunnel inspection application."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run project-level workflows.")
    parser.add_argument(
        "--mode",
        choices=["full_pipeline"],
        default="full_pipeline",
        help="Workflow mode. full_pipeline runs the multi-stage Orchestrator DAG.",
    )
    return parser.parse_args()


def run_full_pipeline(project_root: Path = ROOT) -> dict[str, Any]:
    """Run the full pipeline through the multi-stage Orchestrator DAG."""

    from orchestrator.dag.builder import build_dag
    from orchestrator.executor import DAGExecutor
    from orchestrator.registry import build_default_registry

    dag_path = project_root / "config" / "dag.yaml"
    if not dag_path.exists():
        dag_path = ROOT / "config" / "dag.yaml"
    tasks, config = build_dag(dag_path)
    shared = dict(config.get("shared", {}))
    shared.setdefault("project_root", str(project_root))
    shared.setdefault("dag_config", str(dag_path))
    context = {
        "inputs": config.get("inputs", {}),
        "outputs": {},
        "shared": shared,
        "task_status": {},
    }

    executor = DAGExecutor(build_default_registry(), project_root, dag_config=str(dag_path))
    result = executor.run(tasks, context)
    outputs = result.get("outputs", {})
    return {
        "engineering_rows": outputs.get("engineering_report", {}).get("engineering_rows", 0),
        "growth_rows": outputs.get("growth_analysis", {}).get("growth_rows", 0),
        "memory_rows": outputs.get("memory", {}).get("memory_bank_rows", 0),
        "association_rows": outputs.get("association", {}).get("association_rows", 0),
        "association_matched_rows": outputs.get("association", {}).get("association_matched_rows", 0),
        "recheck_rows": outputs.get("visualization", {}).get("recheck_rows", 0),
        "chart_count": outputs.get("visualization", {}).get("chart_count", 0),
        "outputs": {
            "disease_memory_bank": outputs.get("memory", {}).get("memory_bank_path"),
            "disease_association_records": outputs.get("association", {}).get("association_records_path"),
            "disease_growth_results": outputs.get("growth_analysis", {}).get("growth_results_path"),
            "final_project_report": outputs.get("final_report", {}).get("final_project_report_path"),
            "system_summary": outputs.get("final_report", {}).get("system_summary_path"),
            "key_insights": outputs.get("final_report", {}).get("key_insights_path"),
        },
        "run_id": executor.run_id,
        "run_dir": str(executor.run_info.run_dir),
        "task_status": result.get("task_status", {}),
    }


def run_full_pipeline_direct(project_root: Path = ROOT) -> dict[str, Any]:
    """Compatibility alias for older callers; use run_full_pipeline instead."""

    return run_full_pipeline(project_root)


def main() -> int:
    args = parse_args()
    if args.mode == "full_pipeline":
        result = run_full_pipeline(ROOT)
        print("机器人隧道巡检病害监测完整 pipeline 运行完成")
        print(f"engineering rows: {result['engineering_rows']}")
        print(f"memory rows: {result['memory_rows']}")
        print(f"association rows: {result['association_rows']}")
        print(f"growth rows: {result['growth_rows']}")
        print(f"recheck rows: {result['recheck_rows']}")
        print(f"visual artifacts: {result['chart_count']}")
        print(f"final report: {result['outputs']['final_project_report']}")
        return 0
    raise ValueError(f"Unsupported mode: {args.mode}")


if __name__ == "__main__":
    raise SystemExit(main())
