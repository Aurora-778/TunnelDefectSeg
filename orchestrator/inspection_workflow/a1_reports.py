"""Claim-gated Phase A1 Growth and Memory Staging reports."""

from __future__ import annotations

from collections.abc import Mapping
import csv
import io
from pathlib import Path
from typing import Any

from orchestrator.claim_policy import load_claim_policy

from . import a1_artifacts
from .a1_artifacts import PhaseA1ArtifactError
from .memory_snapshot import (
    MemorySnapshotContractError,
    validate_history_memory_snapshots,
)


_GROWTH_REPORT_FILES = (
    "disease_growth_analysis_report.md",
    "disease_growth_analysis_summary.md",
)
_MEMORY_REPORT_FILES = (
    "memory_agent_report.md",
    "disease_memory_bank_summary.md",
)


def _parse_memory_csv(data: bytes, *, path: str) -> tuple[list[str], list[dict[str, str]]]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PhaseA1ArtifactError(f"Memory Snapshot must be UTF-8: {path}") from exc
    if text.startswith("\ufeff"):
        raise PhaseA1ArtifactError(f"Memory Snapshot must not contain a BOM: {path}")
    try:
        rows = list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error as exc:
        raise PhaseA1ArtifactError(f"Memory Snapshot CSV is invalid: {path}") from exc
    if not rows:
        raise PhaseA1ArtifactError(f"Memory Snapshot CSV is empty: {path}")
    fieldnames = rows[0]
    records: list[dict[str, str]] = []
    for row_number, values in enumerate(rows[1:], start=2):
        if len(values) != len(fieldnames):
            raise PhaseA1ArtifactError(
                f"Memory Snapshot row {row_number} has the wrong column count: {path}"
            )
        records.append(dict(zip(fieldnames, values, strict=True)))
    return fieldnames, records


def _require_memory_evidence_binding(
    evidence: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> None:
    bindings = {
        "memory_version": "previous_memory_version",
        "last_seen_inspection": "previous_last_seen_inspection",
        "source_inspection_ids": "previous_source_inspection_ids",
        "source_record_count": "previous_source_record_count",
        "last_area_px": "previous_memory_snapshot_value",
        "comparability_status": "previous_comparability_status",
    }
    for candidate_field, evidence_field in bindings.items():
        if candidate[candidate_field] != evidence[evidence_field]:
            raise PhaseA1ArtifactError(
                f"Evidence {evidence['evidence_id']} {evidence_field} "
                "does not match its Memory Snapshot"
            )


def _load_report_inputs(
    project_root: Path,
    *,
    run_id: str,
    execution_profile: str,
    plan_fingerprint: str,
) -> dict[str, Any]:
    artifacts = a1_artifacts.load_validated_claim_artifacts(
        project_root,
        run_id=run_id,
        execution_profile=execution_profile,
        plan_fingerprint=plan_fingerprint,
    )
    if artifacts["manifest"]["source_bundle_kind"] != "run_local_projection":
        raise PhaseA1ArtifactError(
            "A1 Growth/Memory reports require the prepared history run_local_projection bundle"
        )

    references = artifacts["source_artifacts"]

    def source_snapshot(reference: Mapping[str, Any]) -> bytes:
        snapshot = a1_artifacts.snapshot_phase_a1_work_artifact(
            project_root,
            run_id=run_id,
            execution_profile=execution_profile,
            relative_path=reference["path"],
        )
        if (
            snapshot["size_bytes"] != reference["size_bytes"]
            or snapshot["sha256"] != reference["sha256"]
        ):
            raise PhaseA1ArtifactError(
                f"A1 report source changed after bundle validation: {reference['path']}"
            )
        return snapshot["data"]

    manifest_reference = next(
        (item for item in references if item["role"] == "association_manifest"),
        None,
    )
    if manifest_reference is None:
        raise PhaseA1ArtifactError("A1 report inputs are missing the Association manifest")
    association_manifest = a1_artifacts._parse_json_object_bytes(
        source_snapshot(manifest_reference),
        label="Run-local Association manifest",
    )

    snapshot_records: dict[str, list[dict[str, str]]] = {}
    snapshot_fieldnames: dict[str, list[str]] = {}
    for reference in references:
        if reference["role"] != "history_memory_context":
            continue
        path = reference["path"]
        fieldnames, records = _parse_memory_csv(source_snapshot(reference), path=path)
        snapshot_fieldnames[path] = fieldnames
        snapshot_records[path] = records
    try:
        memory_snapshot = validate_history_memory_snapshots(
            association_manifest,
            snapshot_records,
            snapshot_fieldnames_by_path=snapshot_fieldnames,
        )
    except MemorySnapshotContractError as exc:
        raise PhaseA1ArtifactError(f"Memory Snapshot inputs are invalid: {exc}") from exc

    decisions_by_evidence = {
        item["evidence_id"]: item
        for item in artifacts["claim_decision"]["record_decisions"]
    }
    rounds_by_inspection = {
        item["query_inspection"]: item for item in memory_snapshot["rounds"]
    }
    for evidence in artifacts["records"]:
        decision = decisions_by_evidence.get(evidence["evidence_id"])
        if decision is None:
            raise PhaseA1ArtifactError(
                f"ClaimDecision is missing Evidence: {evidence['evidence_id']}"
            )
        if evidence["previous_entity_type"] != "memory_snapshot":
            continue
        round_record = rounds_by_inspection.get(evidence["current_inspection_id"])
        memory_id = evidence["previous_memory_id"]
        candidate = (
            round_record["memory_by_id"].get(memory_id)
            if round_record is not None and memory_id is not None
            else None
        )
        if candidate is None:
            raise PhaseA1ArtifactError(
                f"Evidence {evidence['evidence_id']} does not resolve to a validated Memory Snapshot"
            )
        _require_memory_evidence_binding(evidence, candidate)
    return {**artifacts, "memory_snapshot": memory_snapshot}


def _decision_lines(
    evidence: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> list[str]:
    capabilities = decision["capabilities"]
    capability_reasons = decision["capability_reasons"]
    templates = decision["template_ids"]
    capability_order = load_claim_policy()["capability_order"]
    lines = [
        f"## {decision['current_observation_id']}",
        "",
        f"- evidence_id：{decision['evidence_id']}",
        f"- decision_id：{decision['decision_id']}",
        f"- 身份证据状态：{decision['identity_evidence_state']}",
        f"- 纵向可比性：{decision['comparison_comparability_status']}",
    ]
    for capability in capability_order:
        lines.append(
            f"- {capability}：status={capabilities[capability]}；"
            f"template_id={templates[capability] or 'null'}；"
            f"reason={capability_reasons[capability]}"
        )
    if capabilities["static_descriptive_audit"] in {"allowed", "allowed_with_limits"}:
        lines.append(f"- 当前静态面积审计：{evidence['current_value']} px²")
    else:
        lines.append("- 当前静态面积审计：Claim Gate 已阻断")
    if capabilities["descriptive_difference_claim"] == "allowed_with_limits":
        lines.append(f"- 描述性面积差：{evidence['absolute_difference']} px²")
        if evidence["relative_difference"] is not None:
            lines.append(f"- 描述性面积相对差：{evidence['relative_difference']}")
    else:
        lines.append("- 描述性差值：Claim Gate 未授权")
    lines.extend(
        [
            "- 限定语：" + "；".join(decision["required_language_qualifiers"]),
            "- reason_codes：" + "|".join(decision["reason_codes"]),
            "",
        ]
    )
    return lines


def _render_growth_reports(inputs: Mapping[str, Any]) -> dict[str, bytes]:
    decisions = {
        item["evidence_id"]: item
        for item in inputs["claim_decision"]["record_decisions"]
    }
    report = [
        "# 病害面积审计与 Claim Gate 报告",
        "",
        "本报告仅使用当前 Run 已验证的 Comparison Evidence 与 ClaimDecision。",
        "来源范围为 byte_binding_only，不构成来源认证或正式发布。",
        "",
    ]
    for evidence in inputs["records"]:
        report.extend(_decision_lines(evidence, decisions[evidence["evidence_id"]]))
    summary = inputs["claim_decision"]["summary"]
    summary_lines = [
        "# 病害面积审计摘要",
        "",
        f"- 记录数：{summary['total_records']}",
        f"- 允许静态审计：{summary['static_audit_allowed']}",
        f"- 允许受限描述性差值：{summary['difference_allowed_with_limits']}",
        f"- 描述性差值已阻断：{summary['difference_blocked']}",
        f"- 允许方向性 capability：{summary['directional_allowed']}",
        "- 来源验证范围：byte_binding_only",
        "- 发布状态：Run-local Staging，非正式发布物",
        "",
    ]
    return {
        _GROWTH_REPORT_FILES[0]: ("\n".join(report).rstrip() + "\n").encode("utf-8"),
        _GROWTH_REPORT_FILES[1]: "\n".join(summary_lines).encode("utf-8"),
    }


def _render_memory_reports(inputs: Mapping[str, Any]) -> dict[str, bytes]:
    snapshot = inputs["memory_snapshot"]
    evidence_by_id = {item["evidence_id"]: item for item in inputs["records"]}
    decisions_by_memory: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for decision in inputs["claim_decision"]["record_decisions"]:
        evidence = evidence_by_id[decision["evidence_id"]]
        memory_id = evidence["previous_memory_id"]
        if evidence["previous_entity_type"] == "memory_snapshot" and memory_id is not None:
            key = (evidence["current_inspection_id"], memory_id)
            decisions_by_memory.setdefault(key, []).append(decision)
    static_only_qualifier = load_claim_policy()["required_language_qualifiers"][
        "static_only"
    ]
    report = [
        "# History-only Memory Snapshot 审计报告",
        "",
        "内部候选 Memory Snapshot，非正式工程结论。",
        "仅展示 Memory Snapshot Validator 返回的可信字段投影。",
        "候选面积只有在当前 ClaimDecision 明确允许静态审计时才展示。",
        "",
    ]
    candidate_count = 0
    for round_record in snapshot["rounds"]:
        report.extend(
            [
                f"## Round {round_record['round_index']:03d} / {round_record['query_inspection']}",
                "",
                "- 历史巡检：" + ("|".join(round_record["history_inspection_ids"]) or "无"),
            ]
        )
        candidates = round_record["memory_by_id"]
        if not candidates:
            report.extend(["- 候选 Memory：无", ""])
            continue
        for memory_id in sorted(candidates):
            candidate_count += 1
            candidate = candidates[memory_id]
            linked_decisions = decisions_by_memory.get(
                (round_record["query_inspection"], memory_id),
                [],
            )
            static_allowed = bool(linked_decisions) and all(
                decision["capabilities"]["static_descriptive_audit"]
                in {"allowed", "allowed_with_limits"}
                for decision in linked_decisions
            )
            report.extend(
                [
                    f"- memory_id：{candidate['memory_id']}",
                    f"- memory_version：{candidate['memory_version']}",
                    "- source_inspection_ids：" + "|".join(candidate["source_inspection_ids"]),
                    f"- source_record_count：{candidate['source_record_count']}",
                    f"- last_seen_inspection：{candidate['last_seen_inspection']}",
                    f"- comparability_status：{candidate['comparability_status']}",
                    "- 关联 decision_id：" + (
                        "|".join(
                            sorted(decision["decision_id"] for decision in linked_decisions)
                        )
                        or "无"
                    ),
                    (
                        f"- last_area_px 静态审计：{candidate['last_area_px']} px²"
                        if static_allowed
                        else "- last_area_px 静态审计：Claim Gate 未授权展示"
                    ),
                    f"- 限定语：{static_only_qualifier}",
                    "",
                ]
            )
    summary = [
        "# History-only Memory Snapshot 摘要",
        "",
        f"- 巡检轮数：{len(snapshot['rounds'])}",
        f"- 候选快照条目数：{candidate_count}",
        "- memory_id：唯一候选解析键",
        "- disease_id：不参与候选解析",
        "- 报告边界：内部候选 Memory Snapshot，非正式工程结论",
        f"- 限定语：{static_only_qualifier}",
        "",
    ]
    return {
        _MEMORY_REPORT_FILES[0]: ("\n".join(report).rstrip() + "\n").encode("utf-8"),
        _MEMORY_REPORT_FILES[1]: "\n".join(summary).encode("utf-8"),
    }


def _write_report_bundle(
    project_root: Path,
    *,
    run_id: str,
    execution_profile: str,
    plan_fingerprint: str,
    stage: str,
    renderer: Any,
) -> dict[str, Any]:
    inputs = _load_report_inputs(
        project_root,
        run_id=run_id,
        execution_profile=execution_profile,
        plan_fingerprint=plan_fingerprint,
    )
    root = Path(project_root).resolve()
    a1_artifacts._reject_recovery_marker(root, run_id, "staging")
    rendered: dict[str, bytes] = renderer(inputs)
    paths = {
        name: a1_artifacts._resolve_fixed_path(
            root, a1_artifacts._staging_relative_path(run_id, name)
        )
        for name in rendered
    }
    for name, path in paths.items():
        a1_artifacts._preflight_idempotent_target(path, rendered[name])

    committed_paths: list[str] = []
    final_consistency_check_started = False
    try:
        for name, path in paths.items():
            relative = a1_artifacts._staging_relative_path(run_id, name)
            if a1_artifacts._atomic_write_idempotent(
                path,
                rendered[name],
                allowed_root=root,
            ):
                committed_paths.append(relative)
        final_consistency_check_started = True
        refreshed = _load_report_inputs(
            project_root,
            run_id=run_id,
            execution_profile=execution_profile,
            plan_fingerprint=plan_fingerprint,
        )
        if (
            refreshed["comparison_evidence_bytes"] != inputs["comparison_evidence_bytes"]
            or refreshed["claim_decision_bytes"] != inputs["claim_decision_bytes"]
            or refreshed["source_artifacts"] != inputs["source_artifacts"]
        ):
            raise PhaseA1ArtifactError(
                "A1 report inputs changed during the point-in-time consistency check"
            )
        for name, path in paths.items():
            if a1_artifacts._read_file_bytes(path, label=name) != rendered[name]:
                raise PhaseA1ArtifactError(f"Staging report bytes changed: {name}")
    except Exception as exc:
        if (
            committed_paths
            or a1_artifacts._write_failure_requires_recovery(exc)
            or final_consistency_check_started
        ):
            a1_artifacts._raise_recovery_required(
                project_root=root,
                run_id=run_id,
                area="staging",
                stage=stage,
                committed_paths=committed_paths,
                primary_error=exc,
            )
        raise
    return {
        "report_paths": [
            a1_artifacts._staging_relative_path(run_id, name) for name in rendered
        ],
        "record_count": len(inputs["records"]),
    }


def write_gated_growth_reports(
    project_root: Path,
    *,
    run_id: str,
    execution_profile: str,
    plan_fingerprint: str,
) -> dict[str, Any]:
    """Write the fixed Phase A1 Growth audit reports to Run-local Staging."""

    return _write_report_bundle(
        project_root,
        run_id=run_id,
        execution_profile=execution_profile,
        plan_fingerprint=plan_fingerprint,
        stage="growth_report_commit",
        renderer=_render_growth_reports,
    )


def write_gated_memory_reports(
    project_root: Path,
    *,
    run_id: str,
    execution_profile: str,
    plan_fingerprint: str,
) -> dict[str, Any]:
    """Write the fixed Phase A1 Memory audit reports to Run-local Staging."""

    return _write_report_bundle(
        project_root,
        run_id=run_id,
        execution_profile=execution_profile,
        plan_fingerprint=plan_fingerprint,
        stage="memory_report_commit",
        renderer=_render_memory_reports,
    )


__all__ = ["write_gated_growth_reports", "write_gated_memory_reports"]
