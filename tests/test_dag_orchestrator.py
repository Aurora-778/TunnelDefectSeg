from orchestrator.base_agent import BaseAgent
from orchestrator.dag.builder import build_dag
from orchestrator.dag.scheduler import execution_layers
from orchestrator.executor import DAGExecutor
from orchestrator.registry import AgentRegistry


class SeedAgent(BaseAgent):
    name = "seed"

    def run(self, context):
        return {"value": self.agent_inputs(context)["value"]}


class FlakyAgent(BaseAgent):
    name = "flaky"
    calls = 0

    def run(self, context):
        FlakyAgent.calls += 1
        if FlakyAgent.calls == 1:
            raise RuntimeError("try again")
        return {"value": context["outputs"]["seed"]["value"] + "-flaky"}


class TailAgent(BaseAgent):
    name = "tail"

    def run(self, context):
        return {"value": context["outputs"]["flaky"]["value"] + "-tail"}


def test_dag_layers_retry_and_cache(tmp_path):
    dag_path = tmp_path / "dag.yaml"
    dag_path.write_text(
        "\n".join(
            [
                "tasks:",
                "  seed:",
                "    agent: seed",
                "    deps: []",
                "  flaky:",
                "    agent: flaky",
                "    deps: [seed]",
                "    retries: 2",
                "  tail:",
                "    agent: tail",
                "    deps: [flaky]",
                "inputs:",
                "  seed:",
                "    value: ok",
            ]
        ),
        encoding="utf-8",
    )
    tasks, config = build_dag(dag_path)
    assert execution_layers(tasks) == [["seed"], ["flaky"], ["tail"]]

    registry = AgentRegistry()
    registry.register(SeedAgent())
    registry.register(FlakyAgent())
    registry.register(TailAgent())
    context = {
        "inputs": config["inputs"],
        "outputs": {},
        "shared": {"project_root": str(tmp_path)},
        "task_status": {},
    }

    FlakyAgent.calls = 0
    result = DAGExecutor(registry, tmp_path).run(tasks, context)

    assert result["task_status"] == {"seed": "success", "flaky": "success", "tail": "success"}
    assert result["outputs"]["tail"]["value"] == "ok-flaky-tail"
    assert FlakyAgent.calls == 2

    cached_context = {
        "inputs": config["inputs"],
        "outputs": {},
        "shared": {"project_root": str(tmp_path)},
        "task_status": {},
    }
    FlakyAgent.calls = 0
    cached = DAGExecutor(registry, tmp_path).run(tasks, cached_context)
    assert cached["outputs"]["tail"]["value"] == "ok-flaky-tail"
    assert FlakyAgent.calls == 0
