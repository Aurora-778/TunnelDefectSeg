"""Claim-gated A1 Engineering, Visualization, and Recheck outputs."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
import csv
import io
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from orchestrator.claim_policy import load_claim_policy

from . import a1_reports
from .a1_artifacts import PhaseA1ArtifactError


_ENGINEERING_REPORT_FILES = (
    "disease_engineering_report.md",
    "disease_engineering_report_summary.md",
)
_RECHECK_FIELDS = (
    "rank",
    "evidence_id",
    "current_observation_id",
    "current_inspection_id",
    "identity_evidence_state",
    "comparison_comparability_status",
    "evidence_valid",
    "needs_manual_review",
    "static_audit_status",
    "current_value",
    "capabilities_json",
    "capability_reasons_json",
    "template_ids_json",
    "reason_codes_json",
    "required_language_qualifiers_json",
    "recheck_reason",
)
def _json_list(value: Any) -> str:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PhaseA1ArtifactError("A1 visualization list field is invalid")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _json_object(value: Any) -> str:
    if not isinstance(value, Mapping):
        raise PhaseA1ArtifactError("A1 visualization mapping field is invalid")
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _decision_index(inputs: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    decisions = inputs["claim_decision"].get("record_decisions")
    if not isinstance(decisions, list):
        raise PhaseA1ArtifactError("ClaimDecision record_decisions must be a list")
    result: dict[str, Mapping[str, Any]] = {}
    for decision in decisions:
        if not isinstance(decision, Mapping) or not isinstance(decision.get("evidence_id"), str):
            raise PhaseA1ArtifactError("ClaimDecision contains an invalid record decision")
        if decision["evidence_id"] in result:
            raise PhaseA1ArtifactError("ClaimDecision contains duplicate evidence_id")
        result[decision["evidence_id"]] = decision
    return result


def _validated_rows(inputs: Mapping[str, Any]) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    decisions = _decision_index(inputs)
    rows: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for evidence in inputs["records"]:
        decision = decisions.get(evidence["evidence_id"])
        if decision is None or decision.get("evidence_id") != evidence["evidence_id"]:
            raise PhaseA1ArtifactError(
                f"ClaimDecision is missing or inconsistent for Evidence: {evidence['evidence_id']}"
            )
        qualifiers = decision.get("required_language_qualifiers")
        if not isinstance(qualifiers, list) or any(not isinstance(item, str) for item in qualifiers):
            raise PhaseA1ArtifactError("ClaimDecision language qualifiers are invalid")
        if (
            evidence["comparison_comparability_status"] != "verified_comparable"
            and _static_audit_allowed(decision)
        ):
            static_only = load_claim_policy()["required_language_qualifiers"]["static_only"]
            if static_only not in qualifiers:
                raise PhaseA1ArtifactError(
                    f"Evidence {evidence['evidence_id']} is missing the static-only qualifier"
                )
        rows.append((evidence, decision))
    return rows


def _static_audit_allowed(decision: Mapping[str, Any]) -> bool:
    return decision["capabilities"]["static_descriptive_audit"] in {
        "allowed",
        "allowed_with_limits",
    }


def _render_engineering_reports(inputs: Mapping[str, Any]) -> dict[str, bytes]:
    rows = _validated_rows(inputs)
    policy = load_claim_policy()
    report: list[str] = [
        "# Run-local Engineering Evidence Report",
        "",
        "本报告仅陈述已验证 Comparison Evidence 与 ClaimDecision 的静态审计状态。",
        "source_validation_scope=byte_binding_only，不构成来源认证或正式工程结论。",
        "",
    ]
    for evidence, decision in rows:
        current_value_text = (
            f"{evidence['current_value']}"
            if _static_audit_allowed(decision)
            else "Claim Gate blocked"
        )
        report.extend(
            [
                f"## {evidence['current_observation_id']}",
                "",
                f"- evidence_id：{evidence['evidence_id']}",
                f"- identity_evidence_state：{evidence['identity_evidence_state']}",
                f"- comparison_comparability_status：{evidence['comparison_comparability_status']}",
                f"- evidence_valid：{str(evidence['evidence_valid']).lower()}",
                f"- current_value_px：{current_value_text}",
                f"- static_audit_status：{decision['capabilities']['static_descriptive_audit']}",
                f"- static_audit_template：{decision['template_ids']['static_descriptive_audit'] or 'null'}",
                "- capabilities：" + _json_object(decision["capabilities"]),
                "- capability_reasons：" + _json_object(decision["capability_reasons"]),
                "- reason_codes：" + _json_list(decision["reason_codes"]),
                "- required_language_qualifiers：" + "；".join(decision["required_language_qualifiers"]),
                "",
            ]
        )
    summary = inputs["claim_decision"]["summary"]
    summary_lines = [
        "# Run-local Engineering Evidence Summary",
        "",
        f"- record_count：{summary['total_records']}",
        f"- static_audit_allowed：{summary['static_audit_allowed']}",
        f"- blocked_or_review_records：{sum(1 for evidence, decision in rows if evidence['needs_manual_review'] or decision['capabilities']['static_descriptive_audit'] == 'blocked')}",
        "- 统计口径：当前静态面积审计与 Claim 状态，不包含方向性变化结论。",
        f"- static_only_qualifier：{policy['required_language_qualifiers']['static_only']}",
        "- publication：Run-local Staging only",
        "",
    ]
    return {
        _ENGINEERING_REPORT_FILES[0]: ("\n".join(report).rstrip() + "\n").encode("utf-8"),
        _ENGINEERING_REPORT_FILES[1]: "\n".join(summary_lines).encode("utf-8"),
    }


def _priority_rows(inputs: Mapping[str, Any]) -> list[dict[str, str]]:
    rows = _validated_rows(inputs)
    selected: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for evidence, decision in rows:
        static_status = decision["capabilities"]["static_descriptive_audit"]
        if evidence["needs_manual_review"] or static_status == "blocked" or evidence["identity_evidence_state"] in {
            "association_rejected",
            "association_pending_review",
            "association_invalid",
        }:
            selected.append((evidence, decision))

    def sort_key(item: tuple[Mapping[str, Any], Mapping[str, Any]]) -> tuple[Any, ...]:
        evidence, decision = item
        state_priority = {"association_pending_review": 0, "association_invalid": 1, "association_rejected": 2}.get(
            evidence["identity_evidence_state"], 3
        )
        blocked_priority = 0 if decision["capabilities"]["static_descriptive_audit"] == "blocked" else 1
        return (
            0 if evidence["needs_manual_review"] else 1,
            state_priority,
            blocked_priority,
            -int(evidence["current_value"]) if _static_audit_allowed(decision) else 0,
            evidence["current_observation_id"],
            evidence["evidence_id"],
        )

    selected.sort(key=sort_key)
    output: list[dict[str, str]] = []
    for rank, (evidence, decision) in enumerate(selected, start=1):
        state = evidence["identity_evidence_state"]
        reason = (
            "manual_review_required"
            if evidence["needs_manual_review"]
            else "claim_gate_blocked"
            if decision["capabilities"]["static_descriptive_audit"] == "blocked"
            else state
        )
        output.append(
            {
                "rank": str(rank),
                "evidence_id": evidence["evidence_id"],
                "current_observation_id": evidence["current_observation_id"],
                "current_inspection_id": evidence["current_inspection_id"],
                "identity_evidence_state": state,
                "comparison_comparability_status": evidence["comparison_comparability_status"],
                "evidence_valid": str(evidence["evidence_valid"]).lower(),
                "needs_manual_review": str(evidence["needs_manual_review"]).lower(),
                "static_audit_status": decision["capabilities"]["static_descriptive_audit"],
                "current_value": (
                    str(evidence["current_value"])
                    if _static_audit_allowed(decision)
                    else ""
                ),
                "capabilities_json": _json_object(decision["capabilities"]),
                "capability_reasons_json": _json_object(decision["capability_reasons"]),
                "template_ids_json": _json_object(decision["template_ids"]),
                "reason_codes_json": _json_list(decision["reason_codes"]),
                "required_language_qualifiers_json": _json_list(decision["required_language_qualifiers"]),
                "recheck_reason": reason,
            }
        )
    return output


def _bar_png(title: str, counts: Mapping[str, int]) -> bytes:
    image = Image.new("RGB", (900, 520), "white")
    draw = ImageDraw.Draw(image)
    draw.text((35, 25), title, fill="black")
    if not counts:
        draw.text((35, 250), "No validated data", fill="black")
    else:
        items = list(counts.items())
        max_count = max(counts.values()) or 1
        baseline = 430
        width = max(20, 760 // len(items) - 12)
        for index, (label, count) in enumerate(items):
            x = 70 + index * (width + 12)
            height = int(300 * count / max_count)
            draw.rectangle((x, baseline - height, x + width, baseline), fill=(45, 105, 170))
            draw.text((x, baseline + 12), str(label)[:18], fill="black")
            draw.text((x + 4, baseline - height - 20), str(count), fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def _static_area_png(areas: Mapping[str, int]) -> bytes:
    image = Image.new("RGB", (900, 720), "white")
    draw = ImageDraw.Draw(image)
    draw.text((35, 25), "Static area audit (descriptive only)", fill="black")
    if not areas:
        draw.text((35, 340), "No validated data", fill="black")
    else:
        max_area = max(areas.values()) or 1
        for index, (observation_id, area) in enumerate(areas.items()):
            y = 70 + index * 31
            draw.text((35, y), str(observation_id)[:30], fill="black")
            width = int(400 * area / max_area)
            draw.rectangle((330, y, 330 + width, y + 16), fill=(45, 105, 170))
            draw.text((750, y), str(area), fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def _render_visualization_outputs(inputs: Mapping[str, Any]) -> dict[str, bytes]:
    rows = _validated_rows(inputs)
    recheck_rows = _priority_rows(inputs)
    static_audit_counts = Counter(
        decision["capabilities"]["static_descriptive_audit"] for _, decision in rows
    )
    comparability_counts = Counter(evidence["comparison_comparability_status"] for evidence, _ in rows)
    area_rows = sorted(
        (
            (evidence["current_observation_id"], int(evidence["current_value"]))
            for evidence, decision in rows
            if _static_audit_allowed(decision)
        ),
        key=lambda item: (-item[1], item[0]),
    )[:20]
    area_counts = dict(area_rows)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=_RECHECK_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(recheck_rows)
    report = [
        "# Claim-gated Visualization Report",
        "",
        "本报告仅展示已验证 Evidence/ClaimDecision 的静态审计状态。",
        "不读取旧 Growth/Visualization 报告，不生成方向性变化结论。",
        "",
        f"- recheck_count：{len(recheck_rows)}",
        f"- static_audit_status_distribution：{dict(sorted(static_audit_counts.items()))}",
        f"- comparability_status_distribution：{dict(sorted(comparability_counts.items()))}",
        "- source_validation_scope：byte_binding_only",
        "",
    ]
    summary = [
        "# Visualization Summary",
        "",
        "- 展示口径：Claim 状态、纵向可比性状态和静态面积审计。",
        "- 不构成来源认证、方向性变化结论或正式发布。",
        f"- records：{len(rows)}",
        f"- priority_recheck_records：{len(recheck_rows)}",
        "- static_area_chart_scope：按当前静态面积排序，最多展示 20 条；不用于方向判断。",
        "- chart_files：static_audit_status_distribution.png|comparability_status_distribution.png|static_area_audit.png",
        "",
    ]
    recheck_report = [
        "# Priority Recheck List",
        "",
        "清单只表示需要人工复核或 Claim Gate 阻断的记录。排序不使用面积变化率或风险变化。",
        "",
    ]
    for item in recheck_rows:
        area_text = (
            f"当前静态面积 {item['current_value']} px²"
            if item["current_value"]
            else "当前静态面积 Claim Gate blocked"
        )
        recheck_report.append(
            f"- {item['rank']}：{item['current_observation_id']}，{item['recheck_reason']}，"
            f"{area_text}；可比性 {item['comparison_comparability_status']}"
        )
    return {
        "priority_recheck_list.csv": buffer.getvalue().encode("utf-8"),
        "visualizations/static_audit_status_distribution.png": _bar_png(
            "Static audit status distribution (audit only)", static_audit_counts
        ),
        "visualizations/comparability_status_distribution.png": _bar_png(
            "Comparability status distribution (audit only)", comparability_counts
        ),
        "visualizations/static_area_audit.png": _static_area_png(area_counts),
        "visualization_report.md": ("\n".join(report) + "\n").encode("utf-8"),
        "visualization_summary.md": ("\n".join(summary) + "\n").encode("utf-8"),
        "recheck_list_report.md": ("\n".join(recheck_report) + "\n").encode("utf-8"),
    }


def write_gated_engineering_reports(
    project_root: Path, *, run_id: str, execution_profile: str, plan_fingerprint: str
) -> dict[str, Any]:
    return a1_reports._write_report_bundle(
        project_root,
        run_id=run_id,
        execution_profile=execution_profile,
        plan_fingerprint=plan_fingerprint,
        stage="engineering_claim_report_commit",
        renderer=_render_engineering_reports,
    )


def write_gated_visualization_outputs(
    project_root: Path, *, run_id: str, execution_profile: str, plan_fingerprint: str
) -> dict[str, Any]:
    return a1_reports._write_report_bundle(
        project_root,
        run_id=run_id,
        execution_profile=execution_profile,
        plan_fingerprint=plan_fingerprint,
        stage="visualization_recheck_commit",
        renderer=_render_visualization_outputs,
    )


__all__ = ["write_gated_engineering_reports", "write_gated_visualization_outputs"]
