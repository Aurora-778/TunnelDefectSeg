"""Command-line entrypoint for the lightweight goal orchestrator."""

from pathlib import Path
import sys


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))

    from orchestrator.core.orchestrator import Orchestrator
    from orchestrator.core.registry import build_default_registry

    config_path = Path(__file__).resolve().parent / "config" / "goals.yaml"
    orchestrator = Orchestrator(project_root=project_root, config_path=config_path)

    for name, agent in build_default_registry(project_root).items():
        orchestrator.register(name, agent)

    result = orchestrator.run_pipeline()

    print("Multi-Agent Goal Orchestrator v1 finished")
    print(f"executed goals: {', '.join(result.get('executed_goals', []))}")
    print(f"failed goals: {', '.join(result.get('failed_goals', [])) or 'none'}")
    print(f"run log: {project_root / 'logs' / 'run.log'}")
    print(f"goals log: {project_root / 'logs' / 'goals.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
