from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from orchestrator.dag import builder
from orchestrator.dag.builder import Task, build_dag
from orchestrator.inspection_workflow import lifecycle
from orchestrator.inspection_workflow.planning import task_plan_fingerprint
from orchestrator.registry import AgentRegistry, build_default_registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DAG_PATH = PROJECT_ROOT / "config" / "dag.yaml"


def test_default_and_phase_a_profile_use_one_dag_authority() -> None:
    legacy_tasks, _ = build_dag(DAG_PATH)
    phase_a_tasks, _ = build_dag(DAG_PATH, profile="phase_a_agent_sandbox")

    assert list(legacy_tasks) == [
        "engineering_report",
        "growth_analysis",
        "memory",
        "association",
        "visualization",
        "final_report",
    ]
    assert {
        name: (task.agent, task.deps, task.retries, task.cache)
        for name, task in legacy_tasks.items()
    } == {
        "engineering_report": ("engineering_report", [], 2, False),
        "growth_analysis": ("growth_analysis", ["engineering_report"], 2, False),
        "memory": ("memory", ["growth_analysis"], 2, False),
        "association": ("association", ["growth_analysis"], 2, False),
        "visualization": ("visualization", ["growth_analysis", "association"], 2, False),
        "final_report": ("final_report", ["memory", "association", "visualization"], 2, False),
    }
    assert set(phase_a_tasks) == {
        "phase_a_association",
        "phase_a_comparison_evidence",
        "phase_a_claim_gate",
        "phase_a_growth_report",
        "phase_a_memory_report",
        "phase_a_engineering_claim_report",
        "phase_a_claim_visualization",
    }
    assert phase_a_tasks["phase_a_claim_visualization"].deps == [
        "phase_a_claim_gate",
        "phase_a_growth_report",
        "phase_a_memory_report",
        "phase_a_engineering_claim_report",
    ]


def test_profile_selection_rejects_unknown_terminal_and_dependency_drift(tmp_path: Path) -> None:
    dag_path = tmp_path / "dag.yaml"
    dag_path.write_text(
        "\n".join(
            [
                "tasks:",
                "  alpha:",
                "    agent: alpha",
                "    deps: [missing]",
                "execution_profiles:",
                "  phase:",
                "    terminal_tasks: [missing]",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing task"):
        build_dag(dag_path, profile="phase")

    dag_path.write_text(
        "\n".join(
            [
                "tasks:",
                "  alpha:",
                "    agent: alpha",
                "    deps: []",
                "execution_profiles:",
                "  phase:",
                "    terminal_tasks: [alpha]",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown"):
        build_dag(dag_path, profile="other")
    with pytest.raises(ValueError, match="only terminal_tasks"):
        dag_path.write_text(
            dag_path.read_text(encoding="utf-8").replace(
                "    terminal_tasks: [alpha]", "    terminal_tasks: [alpha]\n    deps: []"
            ),
            encoding="utf-8",
        )
        build_dag(dag_path, profile="phase")


def test_profile_closure_fingerprint_is_stable_and_binds_profile_and_policy() -> None:
    phase_a_tasks, _ = build_dag(DAG_PATH, profile="phase_a_agent_sandbox")
    reordered = dict(reversed(list(phase_a_tasks.items())))

    original = task_plan_fingerprint(
        phase_a_tasks,
        resolved_input_descriptor_sha256="0" * 64,
        execution_profile="phase_a_agent_sandbox",
    )
    assert original == task_plan_fingerprint(
        reordered,
        resolved_input_descriptor_sha256="0" * 64,
        execution_profile="phase_a_agent_sandbox",
    )
    assert original != task_plan_fingerprint(
        phase_a_tasks,
        resolved_input_descriptor_sha256="0" * 64,
        execution_profile="legacy_default",
    )
    changed_agent = dict(phase_a_tasks)
    association = changed_agent["phase_a_association"]
    changed_agent[association.name] = Task(
        association.name,
        "different_agent",
        association.deps,
        association.retries,
        association.cache,
    )
    changed_deps = dict(phase_a_tasks)
    growth = changed_deps["phase_a_growth_report"]
    changed_deps[growth.name] = Task(
        growth.name,
        growth.agent,
        ["phase_a_association", *growth.deps],
        growth.retries,
        growth.cache,
    )
    changed_retries = dict(phase_a_tasks)
    memory = changed_retries["phase_a_memory_report"]
    changed_retries[memory.name] = Task(
        memory.name,
        memory.agent,
        memory.deps,
        memory.retries + 1,
        memory.cache,
    )
    changed_cache = dict(phase_a_tasks)
    report = changed_cache["phase_a_engineering_claim_report"]
    changed_cache[report.name] = Task(
        report.name,
        report.agent,
        report.deps,
        report.retries,
        not report.cache,
    )
    for changed_tasks in (
        changed_agent,
        changed_deps,
        changed_retries,
        changed_cache,
    ):
        assert original != task_plan_fingerprint(
            changed_tasks,
            resolved_input_descriptor_sha256="0" * 64,
            execution_profile="phase_a_agent_sandbox",
        )
    assert original != task_plan_fingerprint(
        phase_a_tasks,
        resolved_input_descriptor_sha256="1" * 64,
        execution_profile="phase_a_agent_sandbox",
    )


def test_default_registry_registers_real_phase_a_agents_once() -> None:
    registry = build_default_registry()
    assert {
        "association",
        "comparison_evidence",
        "claim_gate",
        "growth_report",
        "memory_report",
        "engineering_claim_report",
        "claim_visualization",
    } <= set(registry.list())

    duplicate = AgentRegistry()
    duplicate.register(registry.get("association"))
    with pytest.raises(ValueError, match="already registered"):
        duplicate.register(registry.get("association"))


def test_minimal_parser_selects_the_same_profile_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_tasks, _ = build_dag(DAG_PATH, profile="phase_a_agent_sandbox")
    original_import = builtins.__import__

    def without_pyyaml(name: str, *args: object, **kwargs: object) -> object:
        if name == "yaml":
            raise ModuleNotFoundError("No module named 'yaml'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_pyyaml)
    fallback_tasks, _ = build_dag(DAG_PATH, profile="phase_a_agent_sandbox")
    assert fallback_tasks == native_tasks


def test_minimal_parser_preserves_legacy_dag_without_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = "\n".join(
        [
            "tasks:",
            "  alpha:",
            "    agent: alpha",
            "    deps: []",
        ]
    )
    parsed = builder._parse_minimal_dag_yaml(text)
    assert "execution_profiles" not in parsed
    monkeypatch.setattr(builder, "load_yaml", lambda _path: parsed)
    tasks, _ = build_dag(tmp_path / "legacy.yaml")
    assert list(tasks) == ["alpha"]


def test_lifecycle_contains_no_private_phase_a_execution_path() -> None:
    assert not hasattr(lifecycle, "_FixedPhaseA1PipelineAgent")
    assert not hasattr(lifecycle, "_run_fixed_a1_pipeline")
    assert not hasattr(lifecycle, "_fixed_execution")


def test_lifecycle_rejects_unregistered_profile_agent_before_run_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    tasks = {"phase_a_unknown": Task("phase_a_unknown", "not_registered", [], 0, False)}
    monkeypatch.setattr(lifecycle, "build_dag", lambda *args, **kwargs: (tasks, {}))

    with pytest.raises(lifecycle.InspectionWorkflowLifecycleError, match="unregistered"):
        lifecycle._run_lifecycle(
            root,
            input_mode="legacy_simulated",
            task_id="legacy_simulated",
            run_id="run_401",
            resume=False,
            source_capture={
                "descriptor": {
                    "schema_version": "test",
                    "run_id": "run_401",
                },
                "descriptor_sha256": "0" * 64,
                "sources": [],
            },
        )
    assert not (root / "runs").exists()
