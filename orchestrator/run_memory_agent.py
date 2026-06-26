"""Run only the MemoryAgent for Goal 2 validation."""

from pathlib import Path
import sys


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from orchestrator.agents.memory_agent import MemoryAgent

    result = MemoryAgent(project_root).run({"project_root": str(project_root)})
    print("Memory Agent finished")
    print(f"disease_memory_bank_path: {result['disease_memory_bank_path']}")
    print(f"memory_agent_report_path: {result['memory_agent_report_path']}")
    print(f"memory_agent_log_path: {result['memory_agent_log_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
