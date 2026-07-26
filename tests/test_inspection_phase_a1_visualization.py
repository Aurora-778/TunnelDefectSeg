"""Adversarial tests for the A1.3 Engineering/Visualization slice."""

from __future__ import annotations

import csv
import importlib.util
import io
import json
from pathlib import Path

import pytest

from orchestrator.agents.claim_visualization_agent import ClaimVisualizationAgent
from orchestrator.agents.engineering_claim_report_agent import EngineeringClaimReportAgent
from orchestrator.claim_policy import load_claim_policy
from orchestrator.inspection_workflow import (
    PhaseA1ArtifactError,
    a1_artifacts,
    a1_reports,
    a1_visualization,
)


_REPORT_FIXTURES = Path(__file__).with_name("test_inspection_phase_a1_reports.py")


def _report_fixture_module():
    spec = importlib.util.spec_from_file_location("a1_report_test_helpers", _REPORT_FIXTURES)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture(tmp_path: Path, *, include_query: bool = True):
    return _report_fixture_module()._fixture(tmp_path, include_query=include_query)


def _text(root: Path, relative: str) -> str:
    return (root / relative).read_text(encoding="utf-8")


def _synthetic_row(
    suffix: str,
    *,
    state: str,
    evidence_valid: bool,
    manual_review: bool,
    static_status: str,
    area: int,
) -> tuple[dict, dict]:
    qualifier = load_claim_policy()["required_language_qualifiers"]["static_only"]
    evidence = {
        "evidence_id": f"EVD-{suffix}",
        "current_observation_id": f"OBS-{suffix}",
        "current_inspection_id": "I002",
        "identity_evidence_state": state,
        "comparison_comparability_status": "not_longitudinally_comparable",
        "evidence_valid": evidence_valid,
        "needs_manual_review": manual_review,
        "current_value": area,
        "growth_trend": "明显增长",
        "area_growth_rate": "999",
        "risk_level_change": "9",
    }
    capabilities = {
        "static_descriptive_audit": static_status,
        "descriptive_difference_claim": "blocked",
        "directional_change_claim": "blocked",
        "physical_quantity_change_claim": "blocked",
        "multi_timepoint_pattern_claim": "blocked",
        "prediction_claim": "blocked",
    }
    decision = {
        "evidence_id": evidence["evidence_id"],
        "decision_id": f"CD-{suffix}",
        "current_observation_id": evidence["current_observation_id"],
        "identity_evidence_state": state,
        "comparison_comparability_status": "not_longitudinally_comparable",
        "capabilities": capabilities,
        "capability_reasons": {key: "CONTROLLED_REASON" for key in capabilities},
        "template_ids": {
            key: "static_audit_v1" if key == "static_descriptive_audit" and static_status != "blocked" else None
            for key in capabilities
        },
        "required_language_qualifiers": [qualifier],
        "reason_codes": ["CONTROLLED_REASON"],
    }
    return evidence, decision


def _synthetic_inputs(rows: list[tuple[dict, dict]]) -> dict:
    return {
        "records": [row[0] for row in rows],
        "claim_decision": {
            "record_decisions": [row[1] for row in rows],
            "summary": {
                "total_records": len(rows),
                "static_audit_allowed": sum(
                    row[1]["capabilities"]["static_descriptive_audit"] != "blocked"
                    for row in rows
                ),
            },
        },
    }


def test_engineering_and_visualization_agents_generate_only_claim_gated_staging(tmp_path):
    root, context = _fixture(tmp_path)

    engineering = EngineeringClaimReportAgent().run(context)
    visualization = ClaimVisualizationAgent().run(context)

    expected_engineering = {
        f"runs/run_202/staging/disease_engineering_report.md",
        f"runs/run_202/staging/disease_engineering_report_summary.md",
    }
    assert set(engineering["report_paths"]) == expected_engineering
    expected_visualization = {
        "runs/run_202/staging/priority_recheck_list.csv",
        "runs/run_202/staging/visualizations/claim_status_distribution.png",
        "runs/run_202/staging/visualizations/comparability_status_distribution.png",
        "runs/run_202/staging/visualizations/static_area_audit.png",
        "runs/run_202/staging/visualization_report.md",
        "runs/run_202/staging/visualization_summary.md",
        "runs/run_202/staging/recheck_list_report.md",
    }
    assert set(visualization["report_paths"]) == expected_visualization
    assert (root / "runs/run_202/staging/visualizations/claim_status_distribution.png").read_bytes().startswith(b"\x89PNG")
    assert not (root / "outputs").exists()
    assert not (root / "data/simulated").exists()

    report_text = "".join(
        _text(root, path)
        for path in engineering["report_paths"] + visualization["report_paths"]
        if path.endswith(".md")
    )
    assert "byte_binding_only" in report_text
    assert "不构成方向性变化结论" in report_text
    assert "明显增长" not in report_text
    assert "风险上升" not in report_text
    assert "area_growth_rate" not in report_text
    assert "risk_level_change" not in report_text


def test_baseline_row_generates_static_audit_without_recheck_entry(tmp_path):
    root, context = _fixture(tmp_path, include_query=True)
    engineering = EngineeringClaimReportAgent().run(context)
    visualization = ClaimVisualizationAgent().run(context)

    engineering_text = "".join(_text(root, path) for path in engineering["report_paths"])
    assert "association_not_applicable" in engineering_text
    decision = json.loads(
        (root / "runs/run_202/artifacts/claim_decision.json").read_text(encoding="utf-8")
    )
    baseline = next(
        item
        for item in decision["record_decisions"]
        if item["identity_evidence_state"] == "association_not_applicable"
    )
    static_status = baseline["capabilities"]["static_descriptive_audit"]
    assert static_status in {"allowed", "allowed_with_limits"}
    assert static_status in engineering_text
    with (root / "runs/run_202/staging/priority_recheck_list.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert all(row["identity_evidence_state"] != "association_not_applicable" for row in rows)
    assert all((root / path).is_file() for path in visualization["report_paths"])


def test_unmatched_query_is_priority_recheck_without_directional_sorting(tmp_path):
    root, context = _fixture(tmp_path)
    ClaimVisualizationAgent().run(context)
    rows = list(
        csv.DictReader(
            (root / "runs/run_202/staging/priority_recheck_list.csv").open(
                encoding="utf-8", newline=""
            )
        )
    )
    assert len(rows) == 1
    assert rows[0]["identity_evidence_state"] == "association_rejected"
    assert rows[0]["recheck_reason"] in {
        "manual_review_required",
        "association_rejected",
        "claim_gate_blocked",
    }
    assert "area_growth_rate" not in rows[0]
    assert "risk_level_change" not in rows[0]
    assert "增长" not in _text(root, "runs/run_202/staging/recheck_list_report.md")


def test_pending_invalid_and_legacy_direction_fields_render_only_controlled_audit():
    pending = _synthetic_row(
        "PENDING",
        state="association_pending_review",
        evidence_valid=True,
        manual_review=True,
        static_status="allowed_with_limits",
        area=10,
    )
    invalid = _synthetic_row(
        "INVALID",
        state="association_invalid",
        evidence_valid=False,
        manual_review=False,
        static_status="blocked",
        area=999,
    )
    inputs = _synthetic_inputs([invalid, pending])

    engineering = a1_visualization._render_engineering_reports(inputs)
    visualization = a1_visualization._render_visualization_outputs(inputs)
    text = b"".join(engineering.values()) + b"".join(
        value for name, value in visualization.items() if name.endswith((".md", ".csv"))
    )
    decoded = text.decode("utf-8")
    assert "association_pending_review" in decoded
    assert "association_invalid" in decoded
    assert "static_audit_status：blocked" in decoded
    assert "明显增长" not in decoded
    assert "area_growth_rate" not in decoded
    assert "risk_level_change" not in decoded
    assert "不构成方向性变化结论" in decoded
    assert "current_value_px：Claim Gate blocked" in decoded
    priority = list(
        csv.DictReader(
            io.StringIO(visualization["priority_recheck_list.csv"].decode("utf-8"))
        )
    )
    invalid_row = next(item for item in priority if item["current_observation_id"] == "OBS-INVALID")
    assert invalid_row["current_value"] == ""


def test_recheck_order_uses_review_block_area_and_stable_ids_only():
    rows = [
        _synthetic_row(
            "REJECT-SMALL",
            state="association_rejected",
            evidence_valid=True,
            manual_review=False,
            static_status="allowed_with_limits",
            area=1,
        ),
        _synthetic_row(
            "PENDING",
            state="association_pending_review",
            evidence_valid=True,
            manual_review=True,
            static_status="allowed_with_limits",
            area=2,
        ),
        _synthetic_row(
            "INVALID",
            state="association_invalid",
            evidence_valid=False,
            manual_review=False,
            static_status="blocked",
            area=3,
        ),
        _synthetic_row(
            "REJECT-LARGE",
            state="association_rejected",
            evidence_valid=True,
            manual_review=False,
            static_status="allowed_with_limits",
            area=100,
        ),
    ]
    priority = a1_visualization._priority_rows(_synthetic_inputs(rows))
    assert [item["current_observation_id"] for item in priority] == [
        "OBS-PENDING",
        "OBS-INVALID",
        "OBS-REJECT-LARGE",
        "OBS-REJECT-SMALL",
    ]


@pytest.mark.parametrize("agent", [EngineeringClaimReportAgent(), ClaimVisualizationAgent()])
def test_a13_agents_reject_path_and_source_overrides(tmp_path, agent):
    _, context = _fixture(tmp_path)
    context["inputs"] = {agent.name: {"output_path": "../../outside.md"}}
    with pytest.raises(PhaseA1ArtifactError, match="does not accept"):
        agent.run(context)


def test_a13_agents_reject_missing_qualifier_before_staging(tmp_path):
    root, context = _fixture(tmp_path)
    decision_path = root / "runs/run_202/artifacts/claim_decision.json"
    document = json.loads(decision_path.read_text(encoding="utf-8"))
    document["record_decisions"][0]["required_language_qualifiers"] = []
    decision_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(PhaseA1ArtifactError):
        EngineeringClaimReportAgent().run(context)
    assert not (root / "runs/run_202/staging").exists()


@pytest.mark.parametrize(
    ("relative_path", "error_pattern"),
    [
        ("runs/run_202/artifacts/comparison_evidence.csv", "size does not match|SHA-256"),
        ("runs/run_202/artifacts/claim_decision.json", "valid UTF-8 JSON|invalid"),
        ("runs/run_202/work/projection_receipt.json", "size does not match|SHA-256"),
    ],
)
def test_evidence_decision_manifest_receipt_tampering_fails_before_staging(
    tmp_path,
    relative_path,
    error_pattern,
):
    root, context = _fixture(tmp_path)
    target = root / relative_path
    target.write_bytes(target.read_bytes() + b" ")

    with pytest.raises(PhaseA1ArtifactError, match=error_pattern):
        ClaimVisualizationAgent().run(context)
    assert not (root / "runs/run_202/staging").exists()


def test_a13_visualization_is_idempotent_and_changed_staging_fails_closed(tmp_path):
    root, context = _fixture(tmp_path)
    first = ClaimVisualizationAgent().run(context)
    before = {path: (root / path).read_bytes() for path in first["report_paths"]}
    second = ClaimVisualizationAgent().run(context)
    assert second == first
    assert before == {path: (root / path).read_bytes() for path in before}

    target = root / "runs/run_202/staging/visualization_summary.md"
    target.write_bytes(target.read_bytes() + b"changed")
    with pytest.raises(PhaseA1ArtifactError, match="overwrite changed"):
        ClaimVisualizationAgent().run(context)


def test_partial_visualization_commit_records_only_real_paths(tmp_path, monkeypatch):
    root, context = _fixture(tmp_path)
    original = a1_artifacts._atomic_write_idempotent

    def fail_after_first(path, data, *, allowed_root):
        if path.name == "visualizations":
            raise AssertionError("unexpected directory leaf")
        if path.name == "visualization_report.md":
            raise PhaseA1ArtifactError("visualization report write failed")
        return original(path, data, allowed_root=allowed_root)

    monkeypatch.setattr(a1_artifacts, "_atomic_write_idempotent", fail_after_first)
    with pytest.raises(PhaseA1ArtifactError, match="recovery is required"):
        ClaimVisualizationAgent().run(context)

    marker = json.loads(
        (root / "runs/run_202/staging/.a1_recovery_required.json").read_text(encoding="utf-8")
    )
    assert marker["stage"] == "visualization_recheck_commit"
    assert marker["committed_paths"] == [
        "runs/run_202/staging/priority_recheck_list.csv",
        "runs/run_202/staging/visualizations/claim_status_distribution.png",
        "runs/run_202/staging/visualizations/comparability_status_distribution.png",
        "runs/run_202/staging/visualizations/static_area_audit.png",
    ]
    assert not (root / "runs/run_202/staging/visualization_report.md").exists()


def test_manifest_byte_change_during_write_marks_all_commits_for_recovery(
    tmp_path,
    monkeypatch,
):
    root, context = _fixture(tmp_path)
    original = a1_reports._load_report_inputs
    calls = {"count": 0}

    def change_manifest_before_final_reload(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            manifest = root / "runs/run_202/artifacts/comparison_evidence_manifest.json"
            manifest.write_bytes(manifest.read_bytes() + b" ")
        return original(*args, **kwargs)

    monkeypatch.setattr(a1_reports, "_load_report_inputs", change_manifest_before_final_reload)
    with pytest.raises(PhaseA1ArtifactError, match="recovery is required"):
        EngineeringClaimReportAgent().run(context)

    marker = json.loads(
        (root / "runs/run_202/staging/.a1_recovery_required.json").read_text(encoding="utf-8")
    )
    assert marker["committed_paths"] == [
        "runs/run_202/staging/disease_engineering_report.md",
        "runs/run_202/staging/disease_engineering_report_summary.md",
    ]


def test_clean_zero_commit_failure_can_retry(tmp_path, monkeypatch):
    root, context = _fixture(tmp_path)
    original = a1_artifacts._atomic_write_idempotent
    state = {"failed": False}

    def fail_once(path, data, *, allowed_root):
        if not state["failed"]:
            state["failed"] = True
            raise PhaseA1ArtifactError("clean first visualization write failure")
        return original(path, data, allowed_root=allowed_root)

    monkeypatch.setattr(a1_artifacts, "_atomic_write_idempotent", fail_once)
    with pytest.raises(PhaseA1ArtifactError, match="clean first visualization write failure"):
        ClaimVisualizationAgent().run(context)
    assert not (root / "runs/run_202/staging/.a1_recovery_required.json").exists()

    monkeypatch.setattr(a1_artifacts, "_atomic_write_idempotent", original)
    result = ClaimVisualizationAgent().run(context)
    assert all((root / path).is_file() for path in result["report_paths"])


def test_low_level_renderer_rejects_non_mapping_decision(tmp_path):
    del tmp_path
    evidence = {
        "evidence_id": "EV-1",
        "current_observation_id": "OBS-1",
        "current_inspection_id": "I001",
        "identity_evidence_state": "association_not_applicable",
        "comparison_comparability_status": "insufficient_history",
        "evidence_valid": True,
        "needs_manual_review": False,
        "current_value": 1,
    }
    with pytest.raises(PhaseA1ArtifactError):
        a1_visualization._validated_rows(
            {"records": [evidence], "claim_decision": {"record_decisions": [None]}}
        )


def test_empty_renderer_state_is_neutral_and_does_not_invent_counts():
    rendered = a1_visualization._render_visualization_outputs(
        {"records": [], "claim_decision": {"record_decisions": []}}
    )
    assert rendered["visualizations/claim_status_distribution.png"].startswith(b"\x89PNG")
    assert rendered["visualizations/comparability_status_distribution.png"].startswith(b"\x89PNG")
    assert rendered["visualizations/static_area_audit.png"].startswith(b"\x89PNG")
    assert list(
        csv.DictReader(
            io.StringIO(rendered["priority_recheck_list.csv"].decode("utf-8"))
        )
    ) == []
    summary = rendered["visualization_summary.md"].decode("utf-8")
    assert "records：0" in summary
    assert "priority_recheck_records：0" in summary
