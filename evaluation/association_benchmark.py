"""Reproducible, fixture-backed comparison for Association strategies.

The weighted no-id path delegates candidate scoring and acceptance to the
production ``AssociationAgent``.  The other strategies are deliberately
simple reference baselines, not alternative production implementations.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.agents.association_agent import (
    AssociationAgent,
    NO_ID_MARGIN_THRESHOLD,
    NO_ID_MATCH_THRESHOLD,
    NO_ID_SOFT_THRESHOLD,
    WITH_ID_MARGIN_THRESHOLD,
)


ALLOWED_ACTIONS = {"match", "reject", "manual_review"}
STRATEGIES = (
    "nearest_mileage",
    "area_only",
    "spatial_only",
    "weighted_no_id",
    "with_id_upper_bound",
)
SUMMARY_FIELDS = [
    "strategy", "total_cases", "evaluable_cases", "top1_correct_count", "top1_accuracy",
    "accepted_count", "accepted_accuracy", "rejection_count", "rejection_rate",
    "manual_review_count", "manual_review_rate", "conflict_count", "conflict_rate",
    "false_match_count", "false_reject_count", "mean_selected_score", "mean_margin",
]
CASE_FIELDS = [
    "case_id", "inspection_id", "current_record_id", "strategy", "case_type", "expected_action",
    "expected_memory_id", "predicted_action", "predicted_memory_id", "is_top1_correct",
    "is_action_correct", "selected_score", "second_best_score", "score_margin", "review_required",
    "candidate_count", "failure_reason",
]


def load_fixture(path: Path) -> dict[str, Any]:
    """Load and lightly validate the committed benchmark fixture."""

    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or not isinstance(payload.get("memory_rows"), list) or not isinstance(payload.get("cases"), list):
        raise ValueError("Benchmark fixture requires memory_rows and cases lists")
    if not payload["cases"]:
        raise ValueError("Benchmark fixture must include at least one case")
    for case in payload["cases"]:
        missing = [key for key in ("case_id", "inspection_id", "current_record_id", "case_type", "case_description", "expected_action", "expected_memory_id", "expected_match_status", "expected_review_required", "current_record") if key not in case]
        if missing:
            raise ValueError(f"Benchmark case missing fields: {', '.join(missing)}")
        if case["expected_action"] not in ALLOWED_ACTIONS:
            raise ValueError(f"Benchmark case {case['case_id']} has invalid expected_action")
    return payload


def _inspection_index(value: str) -> int:
    digits = "".join(char for char in str(value) if char.isdigit())
    return int(digits) if digits else 0


def _eligible_memories(agent: AssociationAgent, case: dict[str, Any], memory_by_id: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    """Return only named candidate memories that precede this query inspection."""

    query_index = _inspection_index(case["inspection_id"])
    candidates = []
    for memory_id in case.get("candidate_memory_ids", []):
        memory = memory_by_id.get(memory_id)
        if not memory:
            raise ValueError(f"Unknown benchmark memory_id {memory_id!r} in {case['case_id']}")
        if _inspection_index(memory.get("last_seen_inspection", "")) < query_index:
            candidates.append(memory)
    return candidates


def _simple_scores(agent: AssociationAgent, frame: dict[str, str], memories: list[dict[str, str]], strategy: str) -> list[tuple[dict[str, str], float]]:
    """Score intentionally narrow baselines without reading disease_id."""

    scored: list[tuple[dict[str, str], float]] = []
    for memory in memories:
        if strategy == "nearest_mileage":
            score = agent._mileage_score(frame.get("mileage_text", ""), memory.get("mileage_range", ""))
        elif strategy == "area_only":
            score = agent._area_similarity_score(frame, memory)
        elif strategy == "spatial_only":
            score = agent._spatial_distance_score(frame, memory)
        else:  # pragma: no cover - callers only use explicit strategies.
            raise ValueError(f"Unsupported baseline strategy: {strategy}")
        scored.append((memory, score))
    return sorted(scored, key=lambda item: (-item[1], item[0].get("memory_id", "")))


def _decision_from_scores(agent: AssociationAgent, scored: list[tuple[dict[str, str], float]]) -> tuple[str, dict[str, str], float, float, float, bool, str]:
    """Use the production no-id thresholds for comparable baseline decisions."""

    if not scored:
        return "reject", {}, 0.0, 0.0, 0.0, True, "no eligible historical candidate"
    memory, selected = scored[0]
    second = scored[1][1] if len(scored) > 1 else 0.0
    margin = selected - second if len(scored) > 1 else 1.0
    if selected < NO_ID_MATCH_THRESHOLD:
        return "reject", {}, selected, second, margin, True, "below production no-id acceptance threshold"
    if selected < NO_ID_SOFT_THRESHOLD or margin < NO_ID_MARGIN_THRESHOLD:
        return "manual_review", memory, selected, second, margin, True, "ambiguous baseline candidate"
    return "match", memory, selected, second, margin, False, ""


def _production_decision(agent: AssociationAgent, frame: dict[str, str], memories: list[dict[str, str]], *, use_disease_id_score: bool) -> tuple[str, dict[str, str], float, float, float, bool, str]:
    """Delegate weighted scoring, thresholding, type and review policy to production code."""

    memory, scores, candidates = agent._best_memory_match(frame, memories, use_disease_id_score=use_disease_id_score)
    selected = scores["association_score"]
    second = candidates[1][1]["association_score"] if len(candidates) > 1 else 0.0
    margin = agent._score_margin(candidates)
    conflict = agent._conflict_reason(scores)
    match_type = agent._match_type(frame, memory, selected, conflict, use_disease_id_score=use_disease_id_score)
    review = agent._needs_manual_review(bool(memory), match_type, conflict, margin, use_disease_id_score=use_disease_id_score)
    if not memory:
        return "reject", {}, selected, second, margin, True, conflict or "no candidate above production threshold"
    if review:
        return "manual_review", memory, selected, second, margin, True, conflict or "production review policy"
    return "match", memory, selected, second, margin, False, ""


def evaluate_strategy(fixture: dict[str, Any], strategy: str) -> list[dict[str, str]]:
    """Evaluate one strategy against exactly the same ground-truth fixture."""

    if strategy not in STRATEGIES:
        raise ValueError(f"Unsupported strategy: {strategy}")
    agent = AssociationAgent()
    memory_by_id = {str(row["memory_id"]): dict(row) for row in fixture["memory_rows"]}
    results: list[dict[str, str]] = []
    for case in fixture["cases"]:
        frame = dict(case["current_record"])
        frame["inspection_id"] = str(case["inspection_id"])
        memories = _eligible_memories(agent, case, memory_by_id)
        if strategy in {"nearest_mileage", "area_only", "spatial_only"}:
            action, memory, score, second, margin, review, reason = _decision_from_scores(
                agent, _simple_scores(agent, frame, memories, strategy)
            )
        else:
            action, memory, score, second, margin, review, reason = _production_decision(
                agent, frame, memories, use_disease_id_score=strategy == "with_id_upper_bound"
            )
        predicted_memory_id = memory.get("memory_id", "")
        expected_memory_id = str(case["expected_memory_id"])
        top1_correct = bool(expected_memory_id) and predicted_memory_id == expected_memory_id
        action_correct = action == case["expected_action"]
        if action == "match" and not top1_correct:
            reason = reason or "false match"
        elif action == "reject" and case["expected_action"] != "reject":
            reason = reason or "false reject"
        results.append({
            "case_id": str(case["case_id"]),
            "inspection_id": str(case["inspection_id"]),
            "current_record_id": str(case["current_record_id"]),
            "strategy": strategy,
            "case_type": str(case["case_type"]),
            "expected_action": str(case["expected_action"]),
            "expected_memory_id": expected_memory_id,
            "predicted_action": action,
            "predicted_memory_id": predicted_memory_id,
            "is_top1_correct": "true" if top1_correct else "false",
            "is_action_correct": "true" if action_correct else "false",
            "selected_score": f"{score:.4f}",
            "second_best_score": f"{second:.4f}",
            "score_margin": f"{margin:.4f}",
            "review_required": "true" if review else "false",
            "candidate_count": str(len(memories)),
            "failure_reason": reason,
        })
    return results


def summarize(results: list[dict[str, str]]) -> dict[str, str]:
    """Compute all published metrics from case-level outcomes, not hidden counters."""

    total = len(results)
    evaluable = [row for row in results if row["expected_memory_id"]]
    accepted = [row for row in results if row["predicted_action"] == "match"]
    rejected = [row for row in results if row["predicted_action"] == "reject"]
    reviewed = [row for row in results if row["predicted_action"] == "manual_review"]
    conflicts = [row for row in results if _is_candidate_conflict(row)]
    top1_correct = sum(row["is_top1_correct"] == "true" for row in evaluable)
    accepted_correct = sum(row["is_top1_correct"] == "true" for row in accepted)
    false_match = sum(row["predicted_action"] == "match" and row["is_top1_correct"] != "true" for row in results)
    false_reject = sum(row["predicted_action"] == "reject" and row["expected_action"] != "reject" for row in results)
    return {
        "strategy": results[0]["strategy"] if results else "",
        "total_cases": str(total),
        "evaluable_cases": str(len(evaluable)),
        "top1_correct_count": str(top1_correct),
        "top1_accuracy": f"{top1_correct / len(evaluable):.4f}" if evaluable else "0.0000",
        "accepted_count": str(len(accepted)),
        "accepted_accuracy": f"{accepted_correct / len(accepted):.4f}" if accepted else "0.0000",
        "rejection_count": str(len(rejected)),
        "rejection_rate": f"{len(rejected) / total:.4f}" if total else "0.0000",
        "manual_review_count": str(len(reviewed)),
        "manual_review_rate": f"{len(reviewed) / total:.4f}" if total else "0.0000",
        "conflict_count": str(len(conflicts)),
        "conflict_rate": f"{len(conflicts) / total:.4f}" if total else "0.0000",
        "false_match_count": str(false_match),
        "false_reject_count": str(false_reject),
        "mean_selected_score": f"{sum(float(row['selected_score']) for row in results) / total:.4f}" if total else "0.0000",
        "mean_margin": f"{sum(float(row['score_margin']) for row in results) / total:.4f}" if total else "0.0000",
    }


def _is_candidate_conflict(row: dict[str, str]) -> bool:
    """Count only accepted/reviewed multi-candidate ambiguity, never low-score rejection."""

    if row["predicted_action"] == "reject" or int(row["candidate_count"]) < 2:
        return False
    threshold = WITH_ID_MARGIN_THRESHOLD if row["strategy"] == "with_id_upper_bound" else NO_ID_MARGIN_THRESHOLD
    return float(row["score_margin"]) < threshold


def _write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_benchmark_outputs(fixture: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    """Create deterministic case/summary artifacts plus a truthful report."""

    output_dir.mkdir(parents=True, exist_ok=True)
    all_cases = [row for strategy in STRATEGIES for row in evaluate_strategy(fixture, strategy)]
    summary = [summarize([row for row in all_cases if row["strategy"] == strategy]) for strategy in STRATEGIES]
    summary_path = output_dir / "association_benchmark_summary.csv"
    cases_path = output_dir / "association_benchmark_cases.csv"
    manifest_path = output_dir / "benchmark_manifest.json"
    report_path = output_dir / "association_benchmark_report.md"
    _write_csv(summary_path, summary, SUMMARY_FIELDS)
    _write_csv(cases_path, all_cases, CASE_FIELDS)
    action_counts = Counter(case["expected_action"] for case in fixture["cases"])
    weighted = next(row for row in summary if row["strategy"] == "weighted_no_id")
    nearest = next(row for row in summary if row["strategy"] == "nearest_mileage")
    spatial = next(row for row in summary if row["strategy"] == "spatial_only")
    comparison = "优于 nearest_mileage" if float(weighted["top1_accuracy"]) > float(nearest["top1_accuracy"]) else "未优于 nearest_mileage"
    lines = [
        "# Association Benchmark Report", "",
        "## 数据与边界", "",
        "本 benchmark 使用提交到 `tests/fixtures/association_benchmark/` 的困难合成案例。"
        "它用于检验规则的候选选择、拒识与人工复核边界，不代表真实线路部署准确率。",
        "`weighted_no_id` 直接调用生产 `AssociationAgent` 的 no-id 评分、阈值与复核策略；"
        "`with_id_upper_bound` 仅为标签可见时的上界 / sanity check。", "",
        "## 场景", "",
        f"- 案例数：{len(fixture['cases'])}",
        f"- Ground Truth action：match {action_counts['match']}，reject {action_counts['reject']}，manual_review {action_counts['manual_review']}",
        "- 覆盖：里程/方位漂移、相似候选、面积与空间冲突、新病害、字段缺失、风险变化及未来候选过滤。", "",
        "## 统一指标", "",
        "| strategy | top1_accuracy | accepted_accuracy | rejection_rate | manual_review_rate | conflict_rate | false_match | false_reject | mean_margin |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary:
        lines.append(
            f"| {row['strategy']} | {row['top1_accuracy']} | {row['accepted_accuracy']} | {row['rejection_rate']} | {row['manual_review_rate']} | {row['conflict_rate']} | {row['false_match_count']} | {row['false_reject_count']} | {row['mean_margin']} |"
        )
    lines.extend([
        "", "## 真实结论", "",
        f"- weighted_no_id 相对 nearest_mileage：{comparison}（以本 fixture 的 top-1 accuracy 为准）。",
        f"- weighted_no_id 相对 spatial_only：{'未优于' if float(weighted['top1_accuracy']) <= float(spatial['top1_accuracy']) else '优于'} spatial_only（{weighted['top1_accuracy']} vs {spatial['top1_accuracy']}）。",
        "- 自动接受、拒识和人工复核均由同一逐案例结果表统计；错误接受和错误拒识不会被隐藏。",
        "- 当前 benchmark 不能证明真实机器人连续巡检中的长期泛化能力，也不能替代带跨巡检 GT 的现场验证。", "",
        "## 重复命中边界", "",
        "C11/C12 是两个独立逐帧 query 对同一历史对象的重复命中案例；本 benchmark 不执行 one-to-one 分配，也不声称解决 query 间唯一匹配问题。", "",
        "## 自动接受案例", "",
        "逐案例结果中 `predicted_action=match` 的记录可见于 `association_benchmark_cases.csv`。", "",
        "## 拒绝案例", "",
        "逐案例结果中 `predicted_action=reject` 的记录可见于 `association_benchmark_cases.csv`。", "",
        "## Manual Review 案例", "",
        "逐案例结果中 `predicted_action=manual_review` 的记录保留为人工复核边界，而不是强行自动关联。", "",
        "## False Match 与 False Reject", "",
        "两个错误计数来自统一逐案例表；报告不删除失败案例，也不把 with-id 结果当作 no-id 结果。", "",
        "## weighted_no_id 的优势与局限", "",
        "该策略同时使用生产规则中的空间、面积、时间与风险信号，因此可在单一信号不足时保留复核选项。"
        "但若简单 baseline 指标相同或更高，报告会如实保留该局限，不得据此声称普遍优越。", "",
        "## 策略说明", "",
        "- nearest_mileage：仅里程最近的简单 baseline，不推荐作为生产策略。",
        "- area_only：仅面积相似度 baseline。",
        "- spatial_only：仅里程和方位的空间 baseline。",
        "- weighted_no_id：生产 no-id Association。",
        "- with_id_upper_bound：标签上界，不是可部署 baseline。",
    ])
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest_path.write_text(json.dumps({
        "schema_version": "v1",
        "fixture_case_count": len(fixture["cases"]),
        "strategies": list(STRATEGIES),
        "source_fixture": "tests/fixtures/association_benchmark/benchmark_fixture.json",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "outputs": [path.name for path in (summary_path, cases_path, report_path)],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"summary": summary_path, "cases": cases_path, "report": report_path, "manifest": manifest_path}
