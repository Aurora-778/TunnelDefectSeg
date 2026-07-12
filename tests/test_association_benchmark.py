from pathlib import Path
import subprocess
import sys

from evaluation.association_benchmark import (
    CASE_FIELDS,
    STRATEGIES,
    SUMMARY_FIELDS,
    _eligible_memories,
    evaluate_strategy,
    load_fixture,
    summarize,
    write_benchmark_outputs,
)
from orchestrator.agents.association_agent import (
    AssociationAgent,
    NO_ID_MARGIN_THRESHOLD,
    NO_ID_MATCH_THRESHOLD,
    NO_ID_SOFT_THRESHOLD,
)


FIXTURE = Path("tests/fixtures/association_benchmark/benchmark_fixture.json")


def test_benchmark_runs_all_strategies_on_one_fixture():
    fixture = load_fixture(FIXTURE)
    results = {strategy: evaluate_strategy(fixture, strategy) for strategy in STRATEGIES}

    assert {len(rows) for rows in results.values()} == {len(fixture["cases"])}
    assert {row["case_id"] for row in results["weighted_no_id"]} == {
        row["case_id"] for row in fixture["cases"]
    }


def test_weighted_no_id_delegates_to_production_association(monkeypatch):
    fixture = load_fixture(FIXTURE)
    original = AssociationAgent._best_memory_match
    calls = []

    def wrapped(self, frame, memories, **kwargs):
        calls.append(kwargs["use_disease_id_score"])
        return original(self, frame, memories, **kwargs)

    monkeypatch.setattr(AssociationAgent, "_best_memory_match", wrapped)
    evaluate_strategy(fixture, "weighted_no_id")

    assert calls and set(calls) == {False}


def test_weighted_no_id_preserves_production_selected_score():
    fixture = load_fixture(FIXTURE)
    case = fixture["cases"][0]
    agent = AssociationAgent()
    memory_by_id = {row["memory_id"]: row for row in fixture["memory_rows"]}
    memories = _eligible_memories(agent, case, memory_by_id)
    frame = dict(case["current_record"], inspection_id=case["inspection_id"])
    _, scores, _ = agent._best_memory_match(frame, memories, use_disease_id_score=False)
    benchmark_row = evaluate_strategy(fixture, "weighted_no_id")[0]

    assert benchmark_row["selected_score"] == f"{scores['association_score']:.4f}"


def test_benchmark_outputs_are_complete_and_repeatable(tmp_path):
    fixture = load_fixture(FIXTURE)
    first = write_benchmark_outputs(fixture, tmp_path)
    first_cases = first["cases"].read_text(encoding="utf-8")
    first_summary = first["summary"].read_text(encoding="utf-8")
    second = write_benchmark_outputs(fixture, tmp_path)

    assert first_cases == second["cases"].read_text(encoding="utf-8")
    assert first_summary == second["summary"].read_text(encoding="utf-8")
    assert first["summary"].read_text(encoding="utf-8-sig").splitlines()[0].split(",") == SUMMARY_FIELDS
    assert first["cases"].read_text(encoding="utf-8-sig").splitlines()[0].split(",") == CASE_FIELDS
    report = first["report"].read_text(encoding="utf-8")
    assert "with_id_upper_bound" in report
    assert "未优于 spatial_only" in report
    assert "不执行 query 之间的全局 one-to-one assignment" in report


def test_conflict_metric_excludes_rejections_and_uses_shared_thresholds():
    rows = [
        {"strategy": "weighted_no_id", "expected_memory_id": "", "predicted_action": "reject", "is_top1_correct": "false", "selected_score": "0.1", "score_margin": "0.0", "candidate_count": "2", "expected_action": "reject"},
        {"strategy": "weighted_no_id", "expected_memory_id": "MEM-A", "predicted_action": "manual_review", "is_top1_correct": "true", "selected_score": "0.8", "score_margin": "0.0", "candidate_count": "1", "expected_action": "manual_review"},
        {"strategy": "weighted_no_id", "expected_memory_id": "MEM-A", "predicted_action": "manual_review", "is_top1_correct": "true", "selected_score": "0.8", "score_margin": f"{NO_ID_MARGIN_THRESHOLD / 2}", "candidate_count": "2", "expected_action": "manual_review"},
    ]

    summary = summarize(rows)

    assert summary["conflict_count"] == "1"
    assert NO_ID_MATCH_THRESHOLD < NO_ID_SOFT_THRESHOLD


def test_report_defines_conflict_and_benchmark_boundaries(tmp_path):
    report = write_benchmark_outputs(load_fixture(FIXTURE), tmp_path)["report"].read_text(encoding="utf-8")

    assert "conflict_count 仅表示单个 query" in report
    assert "reject 与单候选不计入" in report
    assert "不表示全局 one-to-one 分配冲突" in report
    assert "未优于 spatial_only（0.9333 vs 1.0000）" in report
    assert "with_id_upper_bound 不是可部署 baseline" in report


def test_benchmark_cli_refuses_existing_outputs_without_overwrite(tmp_path):
    output_dir = tmp_path / "benchmark"
    write_benchmark_outputs(load_fixture(FIXTURE), output_dir)
    result = subprocess.run(
        [sys.executable, "scripts/run_association_benchmark.py", "--output-dir", str(output_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "pass --overwrite" in result.stderr
