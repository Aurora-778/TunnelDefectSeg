"""Command-line entrypoint for the lightweight goal orchestrator."""

import argparse
from pathlib import Path
import sys


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))

    from orchestrator.core.orchestrator import Orchestrator
    from orchestrator.registry import build_default_registry

    parser = argparse.ArgumentParser(description="Run the generic multi-agent orchestrator.")
    parser.add_argument(
        "--pipeline",
        default="config/pipeline.yaml",
        help="Pipeline YAML path, relative to the project root unless absolute.",
    )
    parser.add_argument(
        "--dag",
        help="DAG YAML path, relative to the project root unless absolute.",
    )
    args = parser.parse_args()

    registry = build_default_registry()
    if args.dag:
        from orchestrator.dag.builder import build_dag
        from orchestrator.executor import DAGExecutor

        dag_path = Path(args.dag)
        if not dag_path.is_absolute():
            dag_path = project_root / dag_path
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
        result = DAGExecutor(registry, project_root).run(tasks, context)
        print("DAG Multi-Agent Orchestrator finished")
        print(f"dag: {dag_path}")
        print(f"task status: {result.get('task_status', {})}")
        print(f"dag log: {project_root / 'logs' / 'dag_execution.json'}")
        return 0

    config_path = Path(args.pipeline)
    if not config_path.is_absolute():
        config_path = project_root / config_path

    orchestrator = Orchestrator(
        project_root=project_root,
        config_path=config_path,
        registry=registry,
    )

    result = orchestrator.run_pipeline()
    shared = result.get("shared", {})

    print("Generic Multi-Agent Orchestrator finished")
    print(f"pipeline: {config_path}")
    print(f"executed goals: {', '.join(shared.get('executed_goals', []))}")
    print(f"failed goals: {', '.join(shared.get('failed_goals', [])) or 'none'}")
    print(f"run log: {project_root / 'logs' / 'run.log'}")
    print(f"pipeline log: {project_root / 'logs' / 'pipeline.json'}")
    print(f"agent log: {project_root / 'logs' / 'agent.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
