from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from PIL import Image
import pytest

from orchestrator.agents.association_agent import AssociationAgent
from orchestrator.agents.comparison_evidence_agent import ComparisonEvidenceAgent
from orchestrator.agents.claim_audit_report_agent import ClaimAuditReportAgent
from orchestrator.agents.claim_gate_agent import ClaimGateAgent
from orchestrator.inspection_workflow import (
    ASSOCIATION_PROJECTION_FIELDS,
    COMPARISON_EVIDENCE_FIELDS,
    ENGINEERING_PROJECTION_FIELDS,
    FRAME_PROJECTION_FIELDS,
    MEMORY_SNAPSHOT_RECORD_FIELDS,
    PhaseA1ArtifactError,
    initialize_phase_a1_sandbox,
    materialize_prepared_history_projection_sources,
    parse_comparison_evidence_csv,
    project_run_local_comparison_evidence,
    validate_comparison_evidence_bundle,
    write_comparison_evidence_bundle,
)
from scripts import prepare_real_inspection_pilot as preparation


RUN_ID = "run_101"
PLAN_FINGERPRINT = "f" * 64


def _csv_bytes(fieldnames, rows):
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=fieldnames,
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _frame(inspection_id: str, observation_suffix: str = "obs_01"):
    return {
        "source_reference_schema_version": "inspection_source_references_v1",
        "identity_source_kind": "prepared",
        "association_inspection_id": inspection_id,
        "local_observation_id": observation_suffix,
        "inspection_id": inspection_id,
        "current_observation_id": f"{inspection_id}::{observation_suffix}",
        "frame_id": "40",
        "image_id": f"{inspection_id}_000040",
        "timestamp": (
            "2026-07-01T10:00:00.000000Z"
            if inspection_id == "I001"
            else "2026-07-02T10:00:00.000000Z"
        ),
        "mask_area_px": "120" if inspection_id == "I001" else "140",
        "observation_source": "real_inspection_mask_input",
        "comparability_status": "insufficient_history",
    }


def _engineering(inspection_id: str, area: str):
    return {
        "source_reference_schema_version": "inspection_source_references_v1",
        "inspection_id": inspection_id,
        "source_observation_ids": json.dumps(
            [f"{inspection_id}::obs_01"],
            separators=(",", ":"),
        ),
        "max_area_px": area,
    }


def _association(
    *,
    status: str = "unmatched",
    memory_id: str = "",
    score: str = "0",
    match_type: str = "uncertain",
    candidate_count: str = "0",
    needs_manual_review: str = "true",
):
    return {
        "source_reference_schema_version": "inspection_source_references_v1",
        "association_id": "ASSOC-I002-obs-01",
        "inspection_id": "I002",
        "current_observation_id": "I002::obs_01",
        "frame_id": "40",
        "image_id": "I002_000040",
        "memory_id": memory_id,
        "association_status": status,
        "association_mode": "no_id",
        "use_disease_id_score": "false",
        "association_score": score,
        "match_type": match_type,
        "candidate_count": candidate_count,
        "score_margin": "",
        "conflict_reason": "",
        "needs_manual_review": needs_manual_review,
    }


def _manifest(run_id: str, inspection_order):
    rounds = [
        {
            "round_index": 1,
            "query_inspection": "I001",
            "history_inspection_ids": [],
            "query_frame_count": 1,
            "query_frames": f"runs/{run_id}/work/main_progressive/round_001/query_frames.csv",
            "mode": "baseline_only",
        }
    ]
    if len(inspection_order) == 2:
        base = f"runs/{run_id}/work/main_progressive/round_002"
        rounds.append(
            {
                "round_index": 2,
                "query_inspection": "I002",
                "history_inspection_ids": ["I001"],
                "query_frame_count": 1,
                "query_frames": f"{base}/query_frames.csv",
                "mode": "history_only",
                "memory_before": f"{base}/memory_before_query.csv",
                "association_records": f"{base}/association_records.csv",
                "memory_after": f"{base}/memory_after_query.csv",
            }
        )
    return {
        "mode": "history_only",
        "source_frame_records": f"runs/{run_id}/work/frame_records.csv",
        "inspection_order": list(inspection_order),
        "rounds": rounds,
    }


def _projection_fixture(tmp_path: Path, *, include_query: bool):
    root = tmp_path / "a1-projection"
    work = root / "runs" / RUN_ID / "work"
    work.mkdir(parents=True)
    initialize_phase_a1_sandbox(
        root,
        run_id=RUN_ID,
        evidence_source_mode="run_local_projection",
    )

    frames = [_frame("I001")]
    engineering = [_engineering("I001", "120")]
    associations = []
    inspection_order = ["I001"]
    if include_query:
        frames.append(_frame("I002"))
        engineering.append(_engineering("I002", "140"))
        associations.append(_association())
        inspection_order.append("I002")

    (work / "frame_records.csv").write_bytes(
        _csv_bytes(FRAME_PROJECTION_FIELDS, frames)
    )
    (work / "engineering_records.csv").write_bytes(
        _csv_bytes(ENGINEERING_PROJECTION_FIELDS, engineering)
    )
    (work / "association_records.csv").write_bytes(
        _csv_bytes(ASSOCIATION_PROJECTION_FIELDS, associations)
    )
    manifest = _manifest(RUN_ID, inspection_order)
    (work / "association_manifest.json").write_text(
        json.dumps(manifest, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    round_1 = work / "main_progressive" / "round_001"
    round_1.mkdir(parents=True)
    (round_1 / "query_frames.csv").write_bytes(
        _csv_bytes(FRAME_PROJECTION_FIELDS, [frames[0]])
    )
    if include_query:
        round_2 = work / "main_progressive" / "round_002"
        round_2.mkdir(parents=True)
        (round_2 / "query_frames.csv").write_bytes(
            _csv_bytes(FRAME_PROJECTION_FIELDS, [frames[1]])
        )
        (round_2 / "association_records.csv").write_bytes(
            _csv_bytes(ASSOCIATION_PROJECTION_FIELDS, associations)
        )
        (round_2 / "memory_before_query.csv").write_bytes(
            _csv_bytes(MEMORY_SNAPSHOT_RECORD_FIELDS, [])
        )
        (round_2 / "memory_after_query.csv").write_bytes(
            _csv_bytes(MEMORY_SNAPSHOT_RECORD_FIELDS, [])
        )

    context = {
        "shared": {
            "project_root": str(root),
            "run_id": RUN_ID,
            "execution_profile": "phase_a1_sandbox",
            "plan_fingerprint": PLAN_FINGERPRINT,
        },
        "inputs": {
            "comparison_evidence": {
                "projection_mode": "run_local_sources",
            }
        },
    }
    return root, work, context


def _run_and_load(root: Path, context):
    result = ComparisonEvidenceAgent().run(context)
    evidence_path = root / result["comparison_evidence_path"]
    return result, parse_comparison_evidence_csv(evidence_path.read_bytes())


def test_baseline_sources_project_to_static_audit_bundle(tmp_path):
    root, _, context = _projection_fixture(tmp_path, include_query=False)

    result, records = _run_and_load(root, context)

    assert len(records) == 1
    assert set(records[0]) == set(COMPARISON_EVIDENCE_FIELDS)
    assert records[0]["identity_evidence_state"] == "association_not_applicable"
    assert records[0]["current_value"] == 120
    assert records[0]["difference_valid"] is False
    assert records[0]["source_current_record_fingerprint"] == (
        "5fcf13e1da1d402755a1e276793d72f08e1a079c97d38d191257d1fcff75fa81"
    )
    manifest = json.loads(
        (root / result["comparison_evidence_manifest_path"]).read_text(encoding="utf-8")
    )
    assert {item["role"] for item in manifest["source_artifacts"]} >= {
        "frame_artifact",
        "engineering_artifact",
        "association_artifact",
        "association_manifest",
    }
    assert manifest["source_bundle_kind"] == "run_local_projection"


def test_unmatched_query_projects_to_rejected_static_audit(tmp_path):
    root, _, context = _projection_fixture(tmp_path, include_query=True)

    _, records = _run_and_load(root, context)

    assert [row["current_inspection_id"] for row in records] == ["I001", "I002"]
    query = records[1]
    assert query["identity_evidence_state"] == "association_rejected"
    assert query["association_status"] == "unmatched"
    assert query["needs_manual_review"] is True
    assert query["previous_entity_type"] == "not_applicable"
    assert query["current_value"] == 140
    assert query["comparison_comparability_status"] == "insufficient_history"


def test_projected_sources_flow_through_claim_gate_and_static_report(tmp_path):
    root, _, context = _projection_fixture(tmp_path, include_query=True)
    context["inputs"]["claim_gate"] = {}
    context["inputs"]["claim_audit_report"] = {}

    ComparisonEvidenceAgent().run(context)
    claim_result = ClaimGateAgent().run(context)
    report_result = ClaimAuditReportAgent().run(context)

    decision = json.loads(
        (root / claim_result["claim_decision_path"]).read_text(encoding="utf-8")
    )
    assert decision["summary"]["static_audit_allowed"] == 2
    assert decision["summary"]["directional_allowed"] == 0
    report = (root / report_result["claim_audit_report_path"]).read_text(
        encoding="utf-8"
    )
    assert "方向性变化结论：未授权" in report
    assert "不构成方向性变化结论" in report


def test_same_frame_multiple_observations_remain_observation_grain(tmp_path):
    root, work, context = _projection_fixture(tmp_path, include_query=False)
    frames = [_frame("I001"), _frame("I001", "obs_02")]
    frames[1]["mask_area_px"] = "150"
    engineering = [
        {
            **_engineering("I001", "150"),
            "source_observation_ids": '["I001::obs_01","I001::obs_02"]',
        }
    ]
    frame_bytes = _csv_bytes(FRAME_PROJECTION_FIELDS, frames)
    (work / "frame_records.csv").write_bytes(frame_bytes)
    (work / "engineering_records.csv").write_bytes(
        _csv_bytes(ENGINEERING_PROJECTION_FIELDS, engineering)
    )
    (work / "main_progressive" / "round_001" / "query_frames.csv").write_bytes(
        _csv_bytes(FRAME_PROJECTION_FIELDS, list(reversed(frames)))
    )
    manifest_path = work / "association_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rounds"][0]["query_frame_count"] = 2
    manifest_path.write_text(
        json.dumps(manifest, separators=(",", ":")),
        encoding="utf-8",
    )

    _, records = _run_and_load(root, context)

    assert [row["current_observation_id"] for row in records] == [
        "I001::obs_01",
        "I001::obs_02",
    ]
    assert {row["current_value"] for row in records} == {150}


def test_prepared_identity_is_recomputed_instead_of_trusting_current_id(tmp_path):
    _, work, context = _projection_fixture(tmp_path, include_query=False)
    forged = _frame("I001")
    forged["current_observation_id"] = "I001::forged"
    forged_bytes = _csv_bytes(FRAME_PROJECTION_FIELDS, [forged])
    (work / "frame_records.csv").write_bytes(forged_bytes)
    (work / "main_progressive" / "round_001" / "query_frames.csv").write_bytes(
        forged_bytes
    )
    engineering = _engineering("I001", "120")
    engineering["source_observation_ids"] = '["I001::forged"]'
    (work / "engineering_records.csv").write_bytes(
        _csv_bytes(ENGINEERING_PROJECTION_FIELDS, [engineering])
    )

    with pytest.raises(PhaseA1ArtifactError, match="conflicting current_observation_id"):
        ComparisonEvidenceAgent().run(context)


def test_engineering_area_must_equal_source_frame_maximum(tmp_path):
    _, work, context = _projection_fixture(tmp_path, include_query=False)
    (work / "engineering_records.csv").write_bytes(
        _csv_bytes(ENGINEERING_PROJECTION_FIELDS, [_engineering("I001", "999")])
    )

    with pytest.raises(PhaseA1ArtifactError, match="maximum source Frame mask_area_px"):
        ComparisonEvidenceAgent().run(context)


def test_mixed_engineering_sources_are_derived_as_static_only(tmp_path):
    root, work, context = _projection_fixture(tmp_path, include_query=False)
    frames = [_frame("I001"), _frame("I001", "obs_02")]
    frames[1].update(
        {
            "mask_area_px": "150",
            "observation_source": "kict_static_mask_cyclic_demo",
            "comparability_status": "not_longitudinally_comparable",
        }
    )
    engineering = [
        {
            **_engineering("I001", "150"),
            "source_observation_ids": '["I001::obs_01","I001::obs_02"]',
        }
    ]
    (work / "frame_records.csv").write_bytes(
        _csv_bytes(FRAME_PROJECTION_FIELDS, frames)
    )
    (work / "engineering_records.csv").write_bytes(
        _csv_bytes(ENGINEERING_PROJECTION_FIELDS, engineering)
    )
    (work / "main_progressive" / "round_001" / "query_frames.csv").write_bytes(
        _csv_bytes(FRAME_PROJECTION_FIELDS, frames)
    )
    manifest_path = work / "association_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rounds"][0]["query_frame_count"] = 2
    manifest_path.write_text(
        json.dumps(manifest, separators=(",", ":")),
        encoding="utf-8",
    )

    _, records = _run_and_load(root, context)

    assert {row["current_observation_source"] for row in records} == {"mixed_sources"}
    assert {row["current_comparability_status"] for row in records} == {
        "insufficient_history"
    }
    assert {row["comparison_comparability_status"] for row in records} == {
        "insufficient_history"
    }
    assert all(row["difference_valid"] is False for row in records)


def test_matched_query_fails_closed_until_memory_source_proof_upgrade(tmp_path):
    _, work, context = _projection_fixture(tmp_path, include_query=True)
    matched = _association(
        status="matched",
        memory_id="MEM-001",
        score="0.8",
        match_type="soft",
        candidate_count="1",
        needs_manual_review="false",
    )
    data = _csv_bytes(ASSOCIATION_PROJECTION_FIELDS, [matched])
    (work / "association_records.csv").write_bytes(data)
    (work / "main_progressive" / "round_002" / "association_records.csv").write_bytes(data)

    with pytest.raises(
        PhaseA1ArtifactError,
        match="matched Association projection requires a source-proof Memory schema upgrade",
    ):
        ComparisonEvidenceAgent().run(context)


def test_nonbaseline_association_disconnect_fails_closed(tmp_path):
    _, work, context = _projection_fixture(tmp_path, include_query=True)
    empty = _csv_bytes(ASSOCIATION_PROJECTION_FIELDS, [])
    (work / "association_records.csv").write_bytes(empty)
    (work / "main_progressive" / "round_002" / "association_records.csv").write_bytes(empty)

    with pytest.raises(PhaseA1ArtifactError, match="missing Association query"):
        ComparisonEvidenceAgent().run(context)


def test_round_and_main_association_bytes_must_agree(tmp_path):
    _, work, context = _projection_fixture(tmp_path, include_query=True)
    different = _association(needs_manual_review="false")
    (work / "main_progressive" / "round_002" / "association_records.csv").write_bytes(
        _csv_bytes(ASSOCIATION_PROJECTION_FIELDS, [different])
    )

    with pytest.raises(
        PhaseA1ArtifactError,
        match="round Association records must exactly match",
    ):
        ComparisonEvidenceAgent().run(context)


def test_round_association_cannot_be_reassigned_to_another_inspection(tmp_path):
    _, work, context = _projection_fixture(tmp_path, include_query=True)
    wrong_round = _association()
    wrong_round["inspection_id"] = "I001"
    wrong_round["current_observation_id"] = "I001::obs_01"
    wrong_round["image_id"] = "I001_000040"
    (work / "main_progressive" / "round_002" / "association_records.csv").write_bytes(
        _csv_bytes(ASSOCIATION_PROJECTION_FIELDS, [wrong_round])
    )

    with pytest.raises(PhaseA1ArtifactError, match="another round"):
        ComparisonEvidenceAgent().run(context)


def test_round_query_frames_must_match_main_frame_relation(tmp_path):
    _, work, context = _projection_fixture(tmp_path, include_query=True)
    wrong_query = _frame("I002")
    wrong_query["mask_area_px"] = "141"
    (work / "main_progressive" / "round_002" / "query_frames.csv").write_bytes(
        _csv_bytes(FRAME_PROJECTION_FIELDS, [wrong_query])
    )

    with pytest.raises(PhaseA1ArtifactError, match="must exactly match"):
        ComparisonEvidenceAgent().run(context)


@pytest.mark.parametrize("target", ["engineering", "association"])
def test_oversized_integers_fail_with_projection_error(tmp_path, target):
    _, work, context = _projection_fixture(tmp_path, include_query=True)
    huge = "9" * 5000
    if target == "engineering":
        rows = [_engineering("I001", huge), _engineering("I002", "140")]
        (work / "engineering_records.csv").write_bytes(
            _csv_bytes(ENGINEERING_PROJECTION_FIELDS, rows)
        )
    else:
        row = _association(candidate_count=huge)
        data = _csv_bytes(ASSOCIATION_PROJECTION_FIELDS, [row])
        (work / "association_records.csv").write_bytes(data)
        (work / "main_progressive" / "round_002" / "association_records.csv").write_bytes(data)

    with pytest.raises(PhaseA1ArtifactError, match="must not exceed"):
        ComparisonEvidenceAgent().run(context)


def test_source_headers_cannot_smuggle_label_fields(tmp_path):
    _, work, context = _projection_fixture(tmp_path, include_query=False)
    data = (work / "frame_records.csv").read_text(encoding="utf-8")
    (work / "frame_records.csv").write_text(
        data.replace("\n", ",label_disease_id\n", 1),
        encoding="utf-8",
    )

    with pytest.raises(PhaseA1ArtifactError, match="fieldnames must exactly match"):
        ComparisonEvidenceAgent().run(context)


def test_manifest_baseline_and_frame_inspections_must_agree(tmp_path):
    _, work, context = _projection_fixture(tmp_path, include_query=False)
    manifest = _manifest(RUN_ID, ["I001"])
    manifest["inspection_order"] = ["I999"]
    manifest["rounds"][0]["query_inspection"] = "I999"
    (work / "association_manifest.json").write_text(
        json.dumps(manifest, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(
        PhaseA1ArtifactError,
        match="query inspection|inspections must exactly match",
    ):
        ComparisonEvidenceAgent().run(context)


def test_manifest_history_order_must_be_earlier_than_query_timestamps(tmp_path):
    _, work, context = _projection_fixture(tmp_path, include_query=True)
    frames = [_frame("I001"), _frame("I002")]
    frames[0]["timestamp"] = "2026-07-03T10:00:00.000000Z"
    (work / "frame_records.csv").write_bytes(
        _csv_bytes(FRAME_PROJECTION_FIELDS, frames)
    )
    (work / "main_progressive" / "round_001" / "query_frames.csv").write_bytes(
        _csv_bytes(FRAME_PROJECTION_FIELDS, [frames[0]])
    )

    with pytest.raises(PhaseA1ArtifactError, match="order is not chronological"):
        ComparisonEvidenceAgent().run(context)


def test_projection_source_change_is_rejected_before_manifest_commit(tmp_path, monkeypatch):
    root, work, context = _projection_fixture(tmp_path, include_query=False)
    import orchestrator.inspection_workflow.a1_artifacts as artifacts

    original = artifacts._snapshot_source_artifacts

    def mutate_then_snapshot(project_root, run_id, references):
        (work / "frame_records.csv").write_bytes(
            (work / "frame_records.csv").read_bytes() + b"\n"
        )
        return original(project_root, run_id, references)

    monkeypatch.setattr(artifacts, "_snapshot_source_artifacts", mutate_then_snapshot)

    with pytest.raises(PhaseA1ArtifactError, match="changed after projection"):
        ComparisonEvidenceAgent().run(context)
    assert not (root / "runs" / RUN_ID / "artifacts" / "comparison_evidence_manifest.json").exists()


def test_projection_manifest_requires_complete_source_set(tmp_path):
    root, _, context = _projection_fixture(tmp_path, include_query=False)
    result = ComparisonEvidenceAgent().run(context)
    manifest_path = root / result["comparison_evidence_manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_artifacts"] = [
        item for item in manifest["source_artifacts"] if item["role"] != "frame_artifact"
    ]
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(PhaseA1ArtifactError, match="source artifact set is incomplete"):
        validate_comparison_evidence_bundle(
            root,
            run_id=RUN_ID,
            execution_profile="phase_a1_sandbox",
            plan_fingerprint=PLAN_FINGERPRINT,
        )


def test_projection_bundle_kind_cannot_be_downgraded_in_manifest(tmp_path):
    root, _, context = _projection_fixture(tmp_path, include_query=False)
    result = ComparisonEvidenceAgent().run(context)
    manifest_path = root / result["comparison_evidence_manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_bundle_kind"] = "normalized_records"
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(PhaseA1ArtifactError, match="does not match sandbox mode"):
        validate_comparison_evidence_bundle(
            root,
            run_id=RUN_ID,
            execution_profile="phase_a1_sandbox",
            plan_fingerprint=PLAN_FINGERPRINT,
        )


def test_normalized_writer_cannot_claim_projection_sandbox(tmp_path):
    root, _, _ = _projection_fixture(tmp_path, include_query=False)

    with pytest.raises(PhaseA1ArtifactError, match="immutable sandbox"):
        write_comparison_evidence_bundle(
            root,
            run_id=RUN_ID,
            execution_profile="phase_a1_sandbox",
            plan_fingerprint=PLAN_FINGERPRINT,
            records=[],
            source_artifacts=[],
        )


@pytest.mark.parametrize(
    ("source", "status"),
    [
        ("unknown_source", "insufficient_history"),
        ("mixed_sources", "insufficient_history"),
        ("legacy_unverified_source", "verified_comparable"),
    ],
)
def test_frame_source_values_cannot_be_laundered_by_aggregation(
    tmp_path,
    source,
    status,
):
    _, work, context = _projection_fixture(tmp_path, include_query=False)
    frame = _frame("I001")
    frame["observation_source"] = source
    frame["comparability_status"] = status
    data = _csv_bytes(FRAME_PROJECTION_FIELDS, [frame])
    (work / "frame_records.csv").write_bytes(data)
    (work / "main_progressive" / "round_001" / "query_frames.csv").write_bytes(data)

    with pytest.raises(
        PhaseA1ArtifactError,
        match="observation_source is invalid|cannot declare verified_comparable",
    ):
        ComparisonEvidenceAgent().run(context)


def test_deep_engineering_list_json_fails_closed(tmp_path):
    _, work, context = _projection_fixture(tmp_path, include_query=False)
    engineering = _engineering("I001", "120")
    engineering["source_observation_ids"] = "[" * 1100 + "0" + "]" * 1100
    (work / "engineering_records.csv").write_bytes(
        _csv_bytes(ENGINEERING_PROJECTION_FIELDS, [engineering])
    )

    with pytest.raises(PhaseA1ArtifactError, match="canonical JSON"):
        ComparisonEvidenceAgent().run(context)


def test_deep_association_manifest_json_fails_closed(tmp_path):
    _, work, context = _projection_fixture(tmp_path, include_query=False)
    (work / "association_manifest.json").write_text(
        '{"rounds":' + "[" * 1100 + "0" + "]" * 1100 + "}",
        encoding="utf-8",
    )

    with pytest.raises(PhaseA1ArtifactError, match="valid JSON"):
        ComparisonEvidenceAgent().run(context)


def test_bundle_validation_reuses_verified_association_manifest_bytes(
    tmp_path,
    monkeypatch,
):
    root, _, context = _projection_fixture(tmp_path, include_query=False)
    result = ComparisonEvidenceAgent().run(context)
    import orchestrator.inspection_workflow.a1_artifacts as artifacts

    original = artifacts._load_json_object

    def reject_second_association_manifest_read(path, *, label):
        if path.name == "association_manifest.json":
            raise AssertionError("association manifest was reopened")
        return original(path, label=label)

    monkeypatch.setattr(
        artifacts,
        "_load_json_object",
        reject_second_association_manifest_read,
    )

    validate_comparison_evidence_bundle(
        root,
        run_id=RUN_ID,
        execution_profile="phase_a1_sandbox",
        plan_fingerprint=PLAN_FINGERPRINT,
    )
    assert result["record_count"] == 1


def test_existing_prepared_and_history_producers_materialize_static_evidence(tmp_path):
    root = tmp_path / "producer-integration"
    work = root / "runs" / RUN_ID / "work"
    dataset = root / "dataset"
    (dataset / "images").mkdir(parents=True)
    (dataset / "masks").mkdir()
    Image.new("RGB", (4, 4), color=(90, 100, 110)).save(
        dataset / "images" / "frame.jpg"
    )
    Image.new("RGB", (4, 4), color=(40, 50, 60)).save(
        dataset / "images" / "frame_late.jpg"
    )
    mask = Image.new("L", (4, 4), color=0)
    mask.putpixel((1, 2), 255)
    mask.save(dataset / "masks" / "mask.png")
    Image.new("L", (4, 4), color=255).save(dataset / "masks" / "mask_late.png")
    with (dataset / "metadata.csv").open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=preparation.REQUIRED_METADATA_COLUMNS,
        )
        writer.writeheader()
        writer.writerow(
            {
                "sequence_id": "S01",
                "source_inspection_id": "visit_1",
                "frame_id": "1",
                "timestamp": "2026-07-01T10:00:00Z",
                "mileage_m": "12.0",
                "ring_id": "1",
                "clock_direction": "12点",
                "image_file": "images/frame.jpg",
                "mask_file": "masks/mask.png",
                "local_observation_id": "obs_01",
                "disease_type": "crack",
            }
        )
        writer.writerow(
            {
                "sequence_id": "S01",
                "source_inspection_id": "visit_2",
                "frame_id": "2",
                "timestamp": "2026-07-02T10:00:00Z",
                "mileage_m": "10000.0",
                "ring_id": "9000",
                "clock_direction": "6点",
                "image_file": "images/frame_late.jpg",
                "mask_file": "masks/mask_late.png",
                "local_observation_id": "obs_02",
                "disease_type": "crack",
            }
        )

    initialize_phase_a1_sandbox(
        root,
        run_id=RUN_ID,
        evidence_source_mode="run_local_projection",
    )
    prepared_dir = work / "raw_prepared"
    result = preparation.prepare_real_inspection_pilot(dataset, prepared_dir)
    assert result["published"] is True
    preparation.require_inference_ready(prepared_dir / "preparation_manifest.json")

    history_dir = work / "raw_history"
    association_path = history_dir / "association_records.csv"
    history_manifest_path = history_dir / "association_manifest.json"
    AssociationAgent().run(
        {
            "inputs": {
                "association": {
                    "history_only": "true",
                    "frame_records": str(prepared_dir / "frame_records.csv"),
                    "output_path": str(association_path),
                    "history_output_dir": str(history_dir / "main_progressive"),
                    "manifest_path": str(history_manifest_path),
                    "use_disease_id_score": "false",
                    "association_mode": "no_id",
                }
            },
            "outputs": {},
            "shared": {"project_root": str(root)},
        }
    )

    materialize_prepared_history_projection_sources(
        root,
        run_id=RUN_ID,
        execution_profile="phase_a1_sandbox",
        prepared_manifest_path=(
            f"runs/{RUN_ID}/work/raw_prepared/preparation_manifest.json"
        ),
        history_association_path=(
            f"runs/{RUN_ID}/work/raw_history/association_records.csv"
        ),
        history_manifest_path=(
            f"runs/{RUN_ID}/work/raw_history/association_manifest.json"
        ),
    )
    context = {
        "shared": {
            "project_root": str(root),
            "run_id": RUN_ID,
            "execution_profile": "phase_a1_sandbox",
            "plan_fingerprint": PLAN_FINGERPRINT,
        },
        "inputs": {"comparison_evidence": {"projection_mode": "run_local_sources"}},
    }
    _, records = _run_and_load(root, context)

    assert len(records) == 2
    assert records[0]["identity_evidence_state"] == "association_not_applicable"
    assert records[0]["current_observation_id"] == "I0001::obs_01"
    assert records[0]["current_value"] == 1
    assert records[1]["identity_evidence_state"] == "association_rejected"
    assert records[1]["current_observation_id"] == "I0002::obs_02"
    assert records[1]["current_value"] == 16
