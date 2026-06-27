from orchestrator.runs import RunManager


def test_run_manager_creates_lists_and_compares_runs(tmp_path):
    manager = RunManager(tmp_path)

    first = manager.create_run(dag_config="config/dag.yaml")
    manager.write_json(first.run_dir / "state.json", {"task_status": {"memory": "success"}, "context_snapshot": {"outputs": {"memory": {}}}})
    manager.write_json(first.run_dir / "timeline.json", [{"task": "memory", "status": "success"}])
    manager.save_context_version(first.run_id, {"outputs": {"memory": {}}})

    second = manager.create_run(dag_config="config/dag.yaml")
    manager.write_json(second.run_dir / "state.json", {"task_status": {"memory": "success", "doc": "success"}, "context_snapshot": {"outputs": {"memory": {}, "doc": {}}}})
    manager.write_json(second.run_dir / "timeline.json", [{"task": "doc", "status": "success"}])
    manager.save_context_version(second.run_id, {"outputs": {"memory": {}, "doc": {}}})

    runs = manager.list_runs()
    assert [run["run_id"] for run in runs] == ["run_001", "run_002"]
    assert manager.latest_run_id() == "run_002"

    diff = manager.compare_runs()
    assert diff["left_run_id"] == "run_001"
    assert diff["right_run_id"] == "run_002"
    assert diff["diff"]["outputs_added"] == ["doc"]
    assert diff["diff"]["memory_diff"]["changed"] is False
