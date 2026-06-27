from orchestrator.api import dag_payload, run_payload, runs_payload, status_payload
from orchestrator.runs import RunManager


def test_orchestrator_api_reads_latest_run(tmp_path):
    manager = RunManager(tmp_path)
    info = manager.create_run(dag_config="config/dag.yaml")
    manager.write_json(
        info.run_dir / "state.json",
        {
            "run_id": info.run_id,
            "task_status": {"memory": "success", "doc": "running"},
            "context_snapshot": {"outputs": {"memory": {}}},
        },
    )
    manager.write_json(
        info.run_dir / "dag.json",
        {
            "nodes": [{"id": "memory", "agent": "memory", "status": "success"}],
            "edges": [],
            "status": {"memory": "success"},
        },
    )
    manager.write_json(info.run_dir / "timeline.json", [{"task": "memory", "status": "success", "duration_seconds": 0.1}])
    manager.save_context_version(info.run_id, {"outputs": {"memory": {}}})

    status = status_payload(tmp_path)
    assert status["run_id"] == "run_001"
    assert status["progress"] == 0.5
    assert status["running_task"] == "doc"

    dag = dag_payload(tmp_path)
    assert dag["nodes"][0]["id"] == "memory"

    runs = runs_payload(tmp_path)
    assert runs["runs"][0]["run_id"] == "run_001"

    one_run = run_payload(tmp_path, "run_001")
    assert one_run["contexts"] == ["context_v1.json"]
    assert one_run["timeline"][0]["duration_seconds"] == 0.1
