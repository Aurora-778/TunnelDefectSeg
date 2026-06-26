from pathlib import Path

from orchestrator.base_agent import BaseAgent
from orchestrator.core.orchestrator import Orchestrator
from orchestrator.registry import AgentRegistry


class FirstAgent(BaseAgent):
    name = "first"

    def run(self, context):
        result = {"value": self.agent_inputs(context)["seed"]}
        context["outputs"][self.name] = result
        return result


class SecondAgent(BaseAgent):
    name = "second"

    def run(self, context):
        result = {"combined": f"{context['outputs']['first']['value']}-done"}
        context["outputs"][self.name] = result
        return result


def test_orchestrator_runs_registry_agents_with_context_contract(tmp_path):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        "\n".join(
            [
                "pipeline:",
                "  - second",
                "goals:",
                "  first:",
                "    depends_on: []",
                "  second:",
                "    depends_on: [first]",
                "shared:",
                "  project_name: generic-test",
                "inputs:",
                "  first:",
                "    seed: alpha",
            ]
        ),
        encoding="utf-8",
    )

    registry = AgentRegistry()
    registry.register(FirstAgent())
    registry.register(SecondAgent())

    context = Orchestrator(tmp_path, pipeline_path, registry=registry).run_pipeline()

    assert context["inputs"]["first"]["seed"] == "alpha"
    assert context["outputs"]["first"]["value"] == "alpha"
    assert context["outputs"]["second"]["combined"] == "alpha-done"
    assert context["shared"]["executed_goals"] == ["first", "second"]
    assert context["shared"]["failed_goals"] == []
    assert Path(tmp_path / "logs" / "pipeline.json").exists()
    assert Path(tmp_path / "logs" / "agent.json").exists()
    assert "combined" not in context
