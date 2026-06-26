"""Run only the MemoryAgent for Goal 2 validation."""

from pathlib import Path
import sys


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from orchestrator.agents.memory_agent import MemoryAgent

    context = {
        "inputs": {
            "memory": {
                "engineering_report": "data/simulated/disease_engineering_report.csv",
                "growth_analysis": "data/simulated/disease_growth_analysis.csv",
                "output_path": "data/simulated/disease_memory_bank.csv",
                "report_path": "outputs/memory_agent_report.md",
                "summary_path": "outputs/disease_memory_bank_summary.md",
                "log_path": "logs/memory_agent.log",
            }
        },
        "outputs": {},
        "shared": {"project_root": str(project_root)},
    }
    result = MemoryAgent().run(context)
    print("Memory Agent finished")
    print(f"disease_memory_bank_path: {result['disease_memory_bank_path']}")
    print(f"memory_agent_report_path: {result['memory_agent_report_path']}")
    print(f"memory_agent_log_path: {result['memory_agent_log_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
