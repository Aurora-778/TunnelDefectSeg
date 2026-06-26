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


class FileAgent(BaseAgent):
    name = "file"
    calls = 0

    def run(self, context):
        FileAgent.calls += 1
        path = self.resolve_path(context, self.agent_inputs(context)["path"])
        return {"content": path.read_text(encoding="utf-8")}


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
    assert (tmp_path / "orchestrator" / "state" / "run_state.json").exists()
    assert (tmp_path / "logs" / "execution_trace.json").exists()

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

    resumed = DAGExecutor(registry, tmp_path, resume=True).run(tasks, cached_context)
    assert resumed["outputs"]["tail"]["value"] == "ok-flaky-tail"
    assert resumed["task_status"]["tail"] == "success"


def test_dag_cache_invalidates_when_input_file_changes(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("one", encoding="utf-8")
    dag_path = tmp_path / "dag.yaml"
    dag_path.write_text(
        "\n".join(
            [
                "tasks:",
                "  file:",
                "    agent: file",
                "    deps: []",
                "inputs:",
                "  file:",
                "    path: source.txt",
            ]
        ),
        encoding="utf-8",
    )
    tasks, config = build_dag(dag_path)
    registry = AgentRegistry()
    registry.register(FileAgent())
    context = {"inputs": config["inputs"], "outputs": {}, "shared": {"project_root": str(tmp_path)}, "task_status": {}}

    FileAgent.calls = 0
    first = DAGExecutor(registry, tmp_path).run(tasks, context)
    assert first["outputs"]["file"]["content"] == "one"
    assert FileAgent.calls == 1

    second = DAGExecutor(registry, tmp_path).run(tasks, {"inputs": config["inputs"], "outputs": {}, "shared": {"project_root": str(tmp_path)}, "task_status": {}})
    assert second["outputs"]["file"]["content"] == "one"
    assert FileAgent.calls == 1

    source.write_text("two", encoding="utf-8")
    third = DAGExecutor(registry, tmp_path).run(tasks, {"inputs": config["inputs"], "outputs": {}, "shared": {"project_root": str(tmp_path)}, "task_status": {}})
    assert third["outputs"]["file"]["content"] == "two"
    assert FileAgent.calls == 2
