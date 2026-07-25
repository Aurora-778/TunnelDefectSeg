from __future__ import annotations

import csv
import hashlib
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
    parse_comparison_evidence_csv,
    validate_comparison_evidence_bundle,
    write_comparison_evidence_bundle,
)
from orchestrator.inspection_workflow.a1_artifacts import (
    PHASE_A1_MAX_INSPECTION_ROUNDS,
    PHASE_A1_SOURCE_ARTIFACT_LIMIT,
    _projection_source_reference_count,
    _require_projection_pilot_round_count,
    _validate_projection_source_set,
    snapshot_phase_a1_work_artifact,
)
from orchestrator.inspection_workflow.comparison_evidence_projection import (
    ComparisonEvidenceProjectionError,
    PROJECTION_RECEIPT_SCHEMA_VERSION,
    _canonical_decimal_from_producer,
    project_run_local_comparison_evidence,
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
                "projection_mode": "test_normalized_relations",
            }
        },
    }
    return root, work, context


def _artifact_reference(root: Path, path: Path, *, kind: str):
    data = path.read_bytes()
    return {
        "kind": kind,
        "path": path.relative_to(root).as_posix(),
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _complete_v4_source_set(round_count: int):
    work_prefix = f"runs/{RUN_ID}/work"
    projected_roles = {
        f"{work_prefix}/frame_records.csv": "frame_artifact",
        f"{work_prefix}/engineering_records.csv": "engineering_artifact",
        f"{work_prefix}/association_records.csv": "association_artifact",
        f"{work_prefix}/association_manifest.json": "association_manifest",
    }
    source_specs = [
        ("prepared_manifest", f"{work_prefix}/producer/prepared_manifest.json"),
        (
            "prepared_observation_records",
            f"{work_prefix}/producer/observation_records.csv",
        ),
        ("prepared_frame_records", f"{work_prefix}/producer/frame_records.csv"),
        (
            "history_association_records",
            f"{work_prefix}/producer/association_records.csv",
        ),
        ("history_manifest", f"{work_prefix}/producer/association_manifest.json"),
    ]
    rounds = []
    for round_index in range(1, round_count + 1):
        round_base = f"{work_prefix}/main_progressive/round_{round_index:03d}"
        query_path = f"{round_base}/query_frames.csv"
        projected_roles[query_path] = "history_round_context"
        source_specs.append(
            (
                "history_query_frames",
                f"{work_prefix}/producer/round_{round_index:03d}/query_frames.csv",
            )
        )
        round_entry = {
            "round_index": round_index,
            "query_inspection": f"I{round_index:03d}",
            "query_frames": query_path,
            "mode": "baseline" if round_index == 1 else "history_only",
        }
        if round_index > 1:
            association_path = f"{round_base}/association_records.csv"
            memory_before_path = f"{round_base}/memory_before_query.csv"
            memory_after_path = f"{round_base}/memory_after_query.csv"
            round_entry.update(
                {
                    "association_records": association_path,
                    "memory_before": memory_before_path,
                    "memory_after": memory_after_path,
                }
            )
            projected_roles[association_path] = "association_round_artifact"
            projected_roles[memory_before_path] = "history_memory_context"
            projected_roles[memory_after_path] = "history_round_context"
            producer_round = f"{work_prefix}/producer/round_{round_index:03d}"
            source_specs.extend(
                [
                    (
                        "history_round_association",
                        f"{producer_round}/association_records.csv",
                    ),
                    (
                        "history_memory_before",
                        f"{producer_round}/memory_before_query.csv",
                    ),
                    (
                        "history_memory_after",
                        f"{producer_round}/memory_after_query.csv",
                    ),
                ]
            )
        rounds.append(round_entry)

    manifest_path = f"{work_prefix}/association_manifest.json"
    source_bytes_by_path = {
        path: f"{kind}:{path}\n".encode("utf-8") for kind, path in source_specs
    }
    source_bytes_by_path.update(
        {
            path: f"projected:{path}\n".encode("utf-8")
            for path in projected_roles
        }
    )
    source_bytes_by_path[manifest_path] = (
        json.dumps(
            {"mode": "history_only", "rounds": rounds},
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")

    def reference(path: str, *, kind: str):
        data = source_bytes_by_path[path]
        return {
            "kind": kind,
            "path": path,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    receipt_sources = sorted(
        (reference(path, kind=kind) for kind, path in source_specs),
        key=lambda item: (item["kind"], item["path"]),
    )
    receipt_projected = sorted(
        (
            reference(path, kind="projected_work_artifact")
            for path in projected_roles
        ),
        key=lambda item: (item["kind"], item["path"]),
    )
    receipt_path = f"{work_prefix}/projection_receipt.json"
    source_bytes_by_path[receipt_path] = (
        json.dumps(
            {
                "schema_version": PROJECTION_RECEIPT_SCHEMA_VERSION,
                "run_id": RUN_ID,
                "execution_profile": "phase_a1_sandbox",
                "validation_scope": "prepared_readiness_and_history_contract",
                "source_artifacts": receipt_sources,
                "projected_artifacts": receipt_projected,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")

    declared = []
    for path, role in projected_roles.items():
        data = source_bytes_by_path[path]
        declared.append(
            {
                "role": role,
                "path": path,
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    receipt_data = source_bytes_by_path[receipt_path]
    declared.append(
        {
            "role": "projection_receipt",
            "path": receipt_path,
            "size_bytes": len(receipt_data),
            "sha256": hashlib.sha256(receipt_data).hexdigest(),
        }
    )
    for item in receipt_sources:
        declared.append(
            {
                "role": "projection_input",
                "path": item["path"],
                "size_bytes": item["size_bytes"],
                "sha256": item["sha256"],
            }
        )
    declared.sort(key=lambda item: (item["role"], item["path"]))
    return declared, source_bytes_by_path


def _refresh_test_projection_receipt(root: Path, context) -> None:
    """Bind hand-authored relation fixtures without exposing a production bypass."""

    work = root / "runs" / RUN_ID / "work"
    origins = work / "test_projection_origins"
    origins.mkdir(exist_ok=True)

    projected_paths = [
        work / "frame_records.csv",
        work / "engineering_records.csv",
        work / "association_records.csv",
        work / "association_manifest.json",
        *sorted((work / "main_progressive").glob("round_*/*.csv")),
    ]
    source_specs = [
        ("prepared_manifest", "prepared_manifest.json"),
        ("prepared_observation_records", "prepared_observation_records.csv"),
        ("prepared_frame_records", "prepared_frame_records.csv"),
        ("history_association_records", "history_association_records.csv"),
        ("history_manifest", "history_manifest.json"),
    ]
    for index, path in enumerate(projected_paths, start=1):
        if path.name == "query_frames.csv":
            source_specs.append(
                ("history_query_frames", f"history_query_frames_{index:03d}.csv")
            )
        elif path.name == "association_records.csv" and path.parent.name.startswith(
            "round_"
        ):
            source_specs.append(
                ("history_round_association", f"history_round_association_{index:03d}.csv")
            )
        elif path.name == "memory_before_query.csv":
            source_specs.append(
                ("history_memory_before", f"history_memory_before_{index:03d}.csv")
            )
        elif path.name == "memory_after_query.csv":
            source_specs.append(
                ("history_memory_after", f"history_memory_after_{index:03d}.csv")
            )
    source_paths = []
    for kind, filename in source_specs:
        path = origins / filename
        path.write_text(
            json.dumps(
                {"fixture_kind": kind, "fixture_path": filename},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        source_paths.append((kind, path))
    source_artifacts = sorted(
        (
            _artifact_reference(
                root,
                path,
                kind=kind,
            )
            for kind, path in source_paths
        ),
        key=lambda item: (item["kind"], item["path"]),
    )
    projected_artifacts = sorted(
        (
            _artifact_reference(
                root,
                path,
                kind="projected_work_artifact",
            )
            for path in projected_paths
        ),
        key=lambda item: (item["kind"], item["path"]),
    )
    receipt = {
        "schema_version": PROJECTION_RECEIPT_SCHEMA_VERSION,
        "run_id": RUN_ID,
        "execution_profile": context["shared"]["execution_profile"],
        "validation_scope": "prepared_readiness_and_history_contract",
        "source_artifacts": source_artifacts,
        "projected_artifacts": projected_artifacts,
    }
    (work / "projection_receipt.json").write_text(
        json.dumps(
            receipt,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _run_projection(context):
    root = Path(context["shared"]["project_root"])
    _refresh_test_projection_receipt(root, context)
    shared = context["shared"]
    try:
        return project_run_local_comparison_evidence(
            root,
            run_id=shared["run_id"],
            execution_profile=shared["execution_profile"],
        )
    except ComparisonEvidenceProjectionError as exc:
        raise PhaseA1ArtifactError(str(exc)) from exc


def _run_and_load(root: Path, context):
    result = _run_projection(context)
    return result, result["records"]


def _prepared_history_agent_fixture(
    tmp_path: Path,
    *,
    include_query: bool,
    inspection_count: int | None = None,
):
    if inspection_count is None:
        inspection_count = 2 if include_query else 1
    if inspection_count < 1 or include_query != (inspection_count > 1):
        raise AssertionError(
            "fixture inspection_count/include_query contract is invalid"
        )

    root = tmp_path / "prepared-history-projection"
    work = root / "runs" / RUN_ID / "work"
    dataset = root / "dataset"
    (dataset / "images").mkdir(parents=True)
    (dataset / "masks").mkdir()
    Image.new("RGB", (4, 4), color=(90, 100, 110)).save(
        dataset / "images" / "frame.jpg"
    )
    mask = Image.new("L", (4, 4), color=0)
    mask.putpixel((1, 2), 255)
    mask.save(dataset / "masks" / "mask.png")
    metadata_rows = [
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
    ]
    if include_query:
        Image.new("RGB", (4, 4), color=(40, 50, 60)).save(
            dataset / "images" / "frame_late.jpg"
        )
        Image.new("L", (4, 4), color=255).save(
            dataset / "masks" / "mask_late.png"
        )
        metadata_rows.append(
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
    for inspection_index in range(3, inspection_count + 1):
        image_name = f"frame_{inspection_index:03d}.jpg"
        mask_name = f"mask_{inspection_index:03d}.png"
        mask_area = [1, 50, 2000, 10000, 100][
            (inspection_index - 1) % 5
        ]
        Image.new(
            "RGB",
            (100, 100),
            color=(
                inspection_index % 255,
                (inspection_index * 2) % 255,
                (inspection_index * 3) % 255,
            ),
        ).save(dataset / "images" / image_name)
        inspection_mask = Image.new("L", (100, 100), color=0)
        inspection_mask.putdata(
            ([255] * mask_area) + ([0] * (10000 - mask_area))
        )
        inspection_mask.save(dataset / "masks" / mask_name)
        metadata_rows.append(
            {
                "sequence_id": "S01",
                "source_inspection_id": f"visit_{inspection_index}",
                "frame_id": str(inspection_index),
                "timestamp": (
                    f"2026-07-{inspection_index:02d}T10:00:00Z"
                ),
                "mileage_m": str(inspection_index * 10000),
                "ring_id": str(inspection_index * 9000),
                "clock_direction": (
                    f"{((inspection_index - 1) % 12) + 1}点"
                ),
                "image_file": f"images/{image_name}",
                "mask_file": f"masks/{mask_name}",
                "local_observation_id": f"obs_{inspection_index:02d}",
                "disease_type": "crack",
            }
        )
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
        writer.writerows(metadata_rows)

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
    context = {
        "shared": {
            "project_root": str(root),
            "run_id": RUN_ID,
            "execution_profile": "phase_a1_sandbox",
            "plan_fingerprint": PLAN_FINGERPRINT,
        },
        "inputs": {
            "comparison_evidence": {
                "projection_mode": "prepared_history_sources",
                "prepared_manifest_path": (
                    f"runs/{RUN_ID}/work/raw_prepared/preparation_manifest.json"
                ),
                "history_association_path": (
                    f"runs/{RUN_ID}/work/raw_history/association_records.csv"
                ),
                "history_manifest_path": (
                    f"runs/{RUN_ID}/work/raw_history/association_manifest.json"
                ),
            }
        },
    }
    return root, work, prepared_dir, context


def test_baseline_sources_project_to_static_audit_bundle(tmp_path):
    root, _, context = _projection_fixture(tmp_path, include_query=False)

    _, records = _run_and_load(root, context)

    assert len(records) == 1
    assert set(records[0]) == set(COMPARISON_EVIDENCE_FIELDS)
    assert records[0]["identity_evidence_state"] == "association_not_applicable"
    assert records[0]["current_value"] == 120
    assert records[0]["difference_valid"] is False
    assert records[0]["source_current_record_fingerprint"] == (
        "5fcf13e1da1d402755a1e276793d72f08e1a079c97d38d191257d1fcff75fa81"
    )
    assert not (root / "runs" / RUN_ID / "artifacts").exists()


def test_agent_rejects_hand_authored_run_local_projection_mode(tmp_path):
    _, _, context = _projection_fixture(tmp_path, include_query=False)
    context["inputs"]["comparison_evidence"] = {
        "projection_mode": "run_local_sources",
    }

    with pytest.raises(
        PhaseA1ArtifactError,
        match="prepared_history_sources projection fields",
    ):
        ComparisonEvidenceAgent().run(context)


def test_v4_projection_writer_is_not_a_public_test_seam():
    import orchestrator.inspection_workflow.a1_artifacts as artifacts

    assert not hasattr(artifacts, "write_projected_comparison_evidence_bundle")


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
    root, _, _, context = _prepared_history_agent_fixture(
        tmp_path,
        include_query=True,
    )
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
        _run_projection(context)


def test_engineering_area_must_equal_source_frame_maximum(tmp_path):
    _, work, context = _projection_fixture(tmp_path, include_query=False)
    (work / "engineering_records.csv").write_bytes(
        _csv_bytes(ENGINEERING_PROJECTION_FIELDS, [_engineering("I001", "999")])
    )

    with pytest.raises(PhaseA1ArtifactError, match="maximum source Frame mask_area_px"):
        _run_projection(context)


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
        _run_projection(context)


def test_nonbaseline_association_disconnect_fails_closed(tmp_path):
    _, work, context = _projection_fixture(tmp_path, include_query=True)
    empty = _csv_bytes(ASSOCIATION_PROJECTION_FIELDS, [])
    (work / "association_records.csv").write_bytes(empty)
    (work / "main_progressive" / "round_002" / "association_records.csv").write_bytes(empty)

    with pytest.raises(PhaseA1ArtifactError, match="missing Association query"):
        _run_projection(context)


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
        _run_projection(context)


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
        _run_projection(context)


def test_round_query_frames_must_match_main_frame_relation(tmp_path):
    _, work, context = _projection_fixture(tmp_path, include_query=True)
    wrong_query = _frame("I002")
    wrong_query["mask_area_px"] = "141"
    (work / "main_progressive" / "round_002" / "query_frames.csv").write_bytes(
        _csv_bytes(FRAME_PROJECTION_FIELDS, [wrong_query])
    )

    with pytest.raises(PhaseA1ArtifactError, match="must exactly match"):
        _run_projection(context)


def test_memory_after_requires_exact_memory_snapshot_schema(tmp_path):
    _, work, context = _projection_fixture(tmp_path, include_query=True)
    (work / "main_progressive" / "round_002" / "memory_after_query.csv").write_bytes(
        b"unexpected\nvalue\n"
    )

    with pytest.raises(PhaseA1ArtifactError, match="fieldnames must exactly match"):
        _run_projection(context)


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
        _run_projection(context)


def test_producer_decimal_rejects_extreme_exponent_before_formatting():
    with pytest.raises(
        ComparisonEvidenceProjectionError,
        match="producer decimal magnitude limit",
    ):
        _canonical_decimal_from_producer(
            "1e1000000000",
            label="association_records.csv row 2",
            field="association_score",
        )


def test_a1_work_snapshot_rejects_oversized_pilot_source(tmp_path):
    root, work, _ = _projection_fixture(tmp_path, include_query=False)
    oversized = work / "oversized.csv"
    oversized.write_bytes(b"x" * ((8 * 1024 * 1024) + 1))

    with pytest.raises(PhaseA1ArtifactError, match="pilot size limit"):
        snapshot_phase_a1_work_artifact(
            root,
            run_id=RUN_ID,
            execution_profile="phase_a1_sandbox",
            relative_path=f"runs/{RUN_ID}/work/oversized.csv",
        )


def test_phase_a1_projection_round_limit_accepts_maximum():
    assert (
        _require_projection_pilot_round_count(PHASE_A1_MAX_INSPECTION_ROUNDS)
        == PHASE_A1_MAX_INSPECTION_ROUNDS
    )
    assert (
        _projection_source_reference_count(PHASE_A1_MAX_INSPECTION_ROUNDS)
        == 252
    )
    assert (
        _projection_source_reference_count(PHASE_A1_MAX_INSPECTION_ROUNDS)
        <= PHASE_A1_SOURCE_ARTIFACT_LIMIT
    )


def test_phase_a1_projection_round_limit_rejects_next_round():
    assert (
        _projection_source_reference_count(
            PHASE_A1_MAX_INSPECTION_ROUNDS + 1
        )
        == 260
    )
    assert (
        _projection_source_reference_count(
            PHASE_A1_MAX_INSPECTION_ROUNDS + 1
        )
        > PHASE_A1_SOURCE_ARTIFACT_LIMIT
    )
    with pytest.raises(
        PhaseA1ArtifactError,
        match="supports at most 31 inspection rounds",
    ):
        _require_projection_pilot_round_count(
            PHASE_A1_MAX_INSPECTION_ROUNDS + 1
        )


def test_complete_v4_source_set_fits_maximum_round_budget():
    declared, source_bytes_by_path = _complete_v4_source_set(
        PHASE_A1_MAX_INSPECTION_ROUNDS
    )

    assert len(declared) == 252
    assert len(declared) <= PHASE_A1_SOURCE_ARTIFACT_LIMIT
    _validate_projection_source_set(
        RUN_ID,
        declared,
        source_bytes_by_path,
    )


def test_source_headers_cannot_smuggle_label_fields(tmp_path):
    _, work, context = _projection_fixture(tmp_path, include_query=False)
    data = (work / "frame_records.csv").read_text(encoding="utf-8")
    (work / "frame_records.csv").write_text(
        data.replace("\n", ",label_disease_id\n", 1),
        encoding="utf-8",
    )

    with pytest.raises(PhaseA1ArtifactError, match="fieldnames must exactly match"):
        _run_projection(context)


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
        _run_projection(context)


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
        _run_projection(context)


def test_projection_source_change_is_rejected_before_manifest_commit(tmp_path, monkeypatch):
    root, work, _, context = _prepared_history_agent_fixture(
        tmp_path,
        include_query=True,
    )
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
    root, _, _, context = _prepared_history_agent_fixture(
        tmp_path,
        include_query=True,
    )
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


def test_projection_receipt_requires_complete_producer_source_set(tmp_path):
    root, work, _, context = _prepared_history_agent_fixture(
        tmp_path,
        include_query=True,
    )
    result = ComparisonEvidenceAgent().run(context)
    receipt_path = work / "projection_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    removed = next(
        item
        for item in receipt["source_artifacts"]
        if item["kind"] == "prepared_frame_records"
    )
    receipt["source_artifacts"].remove(removed)
    receipt_path.write_text(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    receipt_bytes = receipt_path.read_bytes()

    manifest_path = root / result["comparison_evidence_manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_artifacts"] = [
        item
        for item in manifest["source_artifacts"]
        if item["path"] != removed["path"]
    ]
    receipt_reference = next(
        item
        for item in manifest["source_artifacts"]
        if item["role"] == "projection_receipt"
    )
    receipt_reference["size_bytes"] = len(receipt_bytes)
    receipt_reference["sha256"] = hashlib.sha256(receipt_bytes).hexdigest()
    manifest["source_artifacts"].sort(key=lambda item: (item["role"], item["path"]))
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(
        PhaseA1ArtifactError,
        match="producer source set is incomplete",
    ):
        validate_comparison_evidence_bundle(
            root,
            run_id=RUN_ID,
            execution_profile="phase_a1_sandbox",
            plan_fingerprint=PLAN_FINGERPRINT,
        )


def test_projection_bundle_kind_cannot_be_downgraded_in_manifest(tmp_path):
    root, _, _, context = _prepared_history_agent_fixture(
        tmp_path,
        include_query=True,
    )
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
        _run_projection(context)


def test_deep_engineering_list_json_fails_closed(tmp_path):
    _, work, context = _projection_fixture(tmp_path, include_query=False)
    engineering = _engineering("I001", "120")
    engineering["source_observation_ids"] = "[" * 1100 + "0" + "]" * 1100
    (work / "engineering_records.csv").write_bytes(
        _csv_bytes(ENGINEERING_PROJECTION_FIELDS, [engineering])
    )

    with pytest.raises(PhaseA1ArtifactError, match="canonical JSON"):
        _run_projection(context)


def test_deep_association_manifest_json_fails_closed(tmp_path):
    _, work, context = _projection_fixture(tmp_path, include_query=False)
    (work / "association_manifest.json").write_text(
        '{"rounds":' + "[" * 1100 + "0" + "]" * 1100 + "}",
        encoding="utf-8",
    )

    with pytest.raises(PhaseA1ArtifactError, match="valid JSON"):
        _run_projection(context)


def test_bundle_validation_reuses_verified_association_manifest_bytes(
    tmp_path,
    monkeypatch,
):
    root, _, _, context = _prepared_history_agent_fixture(
        tmp_path,
        include_query=True,
    )
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
    assert result["record_count"] == 2


def test_existing_prepared_and_history_producers_materialize_static_evidence(
    tmp_path,
    monkeypatch,
):
    root, _, prepared_dir, context = _prepared_history_agent_fixture(
        tmp_path,
        include_query=True,
    )
    agent_result = ComparisonEvidenceAgent().run(context)
    records = parse_comparison_evidence_csv(
        (root / agent_result["comparison_evidence_path"]).read_bytes()
    )

    assert len(records) == 2
    assert records[0]["identity_evidence_state"] == "association_not_applicable"
    assert records[0]["current_observation_id"] == "I0001::obs_01"
    assert records[0]["current_value"] == 1
    assert records[1]["identity_evidence_state"] == "association_rejected"
    assert records[1]["current_observation_id"] == "I0002::obs_02"
    assert records[1]["current_value"] == 16
    manifest = json.loads(
        (root / agent_result["comparison_evidence_manifest_path"]).read_text(
            encoding="utf-8"
        )
    )
    projection_inputs = {
        item["path"]
        for item in manifest["source_artifacts"]
        if item["role"] == "projection_input"
    }
    assert {
        f"runs/{RUN_ID}/work/raw_prepared/preparation_manifest.json",
        f"runs/{RUN_ID}/work/raw_prepared/observation_records.csv",
        f"runs/{RUN_ID}/work/raw_prepared/frame_records.csv",
        f"runs/{RUN_ID}/work/raw_history/association_records.csv",
        f"runs/{RUN_ID}/work/raw_history/association_manifest.json",
    } <= projection_inputs

    original_gate = preparation.require_inference_ready

    def mutate_prepared_frame_after_path_gate(value):
        result = original_gate(value)
        if isinstance(value, Path):
            (prepared_dir / "frame_records.csv").write_bytes(
                (prepared_dir / "frame_records.csv").read_bytes() + b"\n"
            )
        return result

    monkeypatch.setattr(
        preparation,
        "require_inference_ready",
        mutate_prepared_frame_after_path_gate,
    )
    with pytest.raises(
        PhaseA1ArtifactError,
        match="snapshot does not match its validated manifest",
    ):
        ComparisonEvidenceAgent().run(context)


def test_prepared_history_agent_materializes_maximum_round_budget(tmp_path):
    root, work, _, context = _prepared_history_agent_fixture(
        tmp_path,
        include_query=True,
        inspection_count=PHASE_A1_MAX_INSPECTION_ROUNDS,
    )

    result = ComparisonEvidenceAgent().run(context)
    bundle = validate_comparison_evidence_bundle(
        root,
        run_id=RUN_ID,
        execution_profile="phase_a1_sandbox",
        plan_fingerprint=PLAN_FINGERPRINT,
    )

    assert result["record_count"] == PHASE_A1_MAX_INSPECTION_ROUNDS
    assert (work / "projection_receipt.json").is_file()
    assert bundle["manifest"]["source_bundle_kind"] == "run_local_projection"
    assert len(bundle["manifest"]["source_artifacts"]) == 252
    assert (
        len(bundle["manifest"]["source_artifacts"])
        == _projection_source_reference_count(PHASE_A1_MAX_INSPECTION_ROUNDS)
    )


def test_prepared_history_agent_rejects_manifest_over_pilot_round_limit(tmp_path):
    _, work, _, context = _prepared_history_agent_fixture(
        tmp_path,
        include_query=True,
    )
    manifest_path = (
        Path(context["shared"]["project_root"])
        / context["inputs"]["comparison_evidence"]["history_manifest_path"]
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rounds"] = [
        dict(manifest["rounds"][0])
        for _ in range(PHASE_A1_MAX_INSPECTION_ROUNDS + 1)
    ]
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(
        PhaseA1ArtifactError,
        match="supports at most 31 inspection rounds",
    ):
        ComparisonEvidenceAgent().run(context)
    assert not (work / "projection_receipt.json").exists()
    assert not (work / "frame_records.csv").exists()


def test_projection_work_partial_commit_records_only_actual_paths(
    tmp_path,
    monkeypatch,
):
    root, work, _, context = _prepared_history_agent_fixture(
        tmp_path,
        include_query=True,
    )
    import orchestrator.inspection_workflow.comparison_evidence_projection as projection

    original = projection.write_phase_a1_work_artifact
    attempted_paths = []

    def fail_second_write(project_root, **kwargs):
        attempted_paths.append(kwargs["relative_path"])
        if len(attempted_paths) == 2:
            raise PhaseA1ArtifactError("injected second projection write failure")
        return original(project_root, **kwargs)

    monkeypatch.setattr(
        projection,
        "write_phase_a1_work_artifact",
        fail_second_write,
    )

    with pytest.raises(PhaseA1ArtifactError, match="work recovery is required"):
        ComparisonEvidenceAgent().run(context)

    marker = json.loads(
        (work / ".a1_recovery_required.json").read_text(encoding="utf-8")
    )
    assert marker["area"] == "work"
    assert marker["stage"] == "prepared_history_projection_materialization"
    assert marker["committed_paths"] == [attempted_paths[0]]
    assert (root / attempted_paths[0]).is_file()
    assert not (root / attempted_paths[1]).exists()

    with pytest.raises(PhaseA1ArtifactError, match="work recovery marker exists"):
        ComparisonEvidenceAgent().run(context)


def test_projection_work_uncertain_first_replace_requires_recovery(
    tmp_path,
    monkeypatch,
):
    _, work, _, context = _prepared_history_agent_fixture(
        tmp_path,
        include_query=True,
    )
    import orchestrator.inspection_workflow.comparison_evidence_projection as projection

    def uncertain_first_write(project_root, **kwargs):
        error = PhaseA1ArtifactError("injected uncertain projection write")
        error.write_state_uncertain = True
        raise error

    monkeypatch.setattr(
        projection,
        "write_phase_a1_work_artifact",
        uncertain_first_write,
    )

    with pytest.raises(PhaseA1ArtifactError, match="work recovery is required"):
        ComparisonEvidenceAgent().run(context)

    marker = json.loads(
        (work / ".a1_recovery_required.json").read_text(encoding="utf-8")
    )
    assert marker["committed_paths"] == []


def test_projection_work_clean_zero_commit_failure_can_retry(
    tmp_path,
    monkeypatch,
):
    _, work, _, context = _prepared_history_agent_fixture(
        tmp_path,
        include_query=True,
    )
    import orchestrator.inspection_workflow.comparison_evidence_projection as projection

    original = projection.write_phase_a1_work_artifact
    failed_once = False

    def fail_cleanly_once(project_root, **kwargs):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise PhaseA1ArtifactError("injected clean projection write failure")
        return original(project_root, **kwargs)

    monkeypatch.setattr(
        projection,
        "write_phase_a1_work_artifact",
        fail_cleanly_once,
    )

    with pytest.raises(
        PhaseA1ArtifactError,
        match="unable to materialize A1 projection sources",
    ):
        ComparisonEvidenceAgent().run(context)
    assert not (work / ".a1_recovery_required.json").exists()

    result = ComparisonEvidenceAgent().run(context)
    assert result["record_count"] == 2
