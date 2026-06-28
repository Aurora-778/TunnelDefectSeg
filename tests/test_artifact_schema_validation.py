import csv
import json
from pathlib import Path

from orchestrator.schema import validate_csv_schema
from scripts.validate_artifacts import validate_artifacts


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_validate_csv_schema_reports_missing_columns(tmp_path):
    path = tmp_path / "association.csv"
    write_csv(path, ["association_id"], [{"association_id": "A1"}])

    errors = validate_csv_schema(path, "disease_association_records")

    assert errors
    assert "missing required columns" in errors[0]


def test_validate_csv_schema_reports_enum_errors(tmp_path):
    path = tmp_path / "growth.csv"
    fieldnames = [
        "disease_id",
        "disease_type",
        "inspection_count",
        "first_inspection",
        "last_inspection",
        "first_area_px",
        "last_area_px",
        "area_growth_px",
        "area_growth_rate",
        "first_risk_level",
        "last_risk_level",
        "risk_level_change",
        "growth_trend",
        "attention_level",
        "measurement_basis",
        "claim_level",
        "comparability_status",
        "main_clock_direction",
        "growth_description",
    ]
    write_csv(path, fieldnames, [{name: "x" for name in fieldnames}])

    errors = validate_csv_schema(path, "disease_growth_results")

    assert any("growth_trend" in error for error in errors)
    assert any("attention_level" in error for error in errors)


def test_validate_csv_schema_accepts_valid_association_record(tmp_path):
    path = tmp_path / "association.csv"
    fieldnames = [
        "association_id",
        "inspection_id",
        "frame_id",
        "image_id",
        "label_disease_id",
        "memory_id",
        "association_status",
        "rule_basis",
        "use_disease_id_score",
        "association_mode",
        "association_score",
        "spatial_distance_score",
        "area_similarity_score",
        "temporal_continuity_score",
        "risk_similarity_score",
        "confidence_level",
        "match_type",
        "candidate_count",
        "top_candidate_ids",
        "score_margin",
        "conflict_reason",
        "needs_manual_review",
        "bbox_fields_present",
        "geometry_score_applied",
        "geometry_feature_available",
        "geometry_limit_note",
    ]
    row = {name: "" for name in fieldnames}
    row.update(
        {
            "association_id": "A1",
            "association_status": "matched",
            "confidence_level": "medium",
            "match_type": "soft",
            "needs_manual_review": "false",
        }
    )
    write_csv(path, fieldnames, [row])

    assert validate_csv_schema(path, "disease_association_records") == []


def test_association_schema_requires_candidate_fields(tmp_path):
    path = tmp_path / "association.csv"
    fieldnames = [
        "association_id",
        "inspection_id",
        "frame_id",
        "image_id",
        "label_disease_id",
        "memory_id",
        "association_status",
        "rule_basis",
        "association_score",
        "spatial_distance_score",
        "area_similarity_score",
        "temporal_continuity_score",
        "risk_similarity_score",
        "confidence_level",
        "match_type",
    ]
    write_csv(path, fieldnames, [{name: "" for name in fieldnames}])

    errors = validate_csv_schema(path, "disease_association_records")

    assert any("candidate_count" in error for error in errors)


def test_memory_schema_requires_manual_review_field(tmp_path):
    path = tmp_path / "memory.csv"
    fieldnames = [
        "memory_id",
        "memory_version",
        "disease_id",
        "disease_type",
        "source_record_count",
        "source_inspection_ids",
        "memory_update_mode",
        "memory_confidence",
        "memory_limit_note",
        "first_seen_inspection",
        "last_seen_inspection",
        "inspection_count",
        "last_area_px",
        "max_area_px",
        "growth_trend",
        "attention_level",
        "mileage_range",
        "memory_description",
    ]
    write_csv(path, fieldnames, [{name: "" for name in fieldnames}])

    errors = validate_csv_schema(path, "disease_memory_bank")

    assert any("requires_manual_review" in error for error in errors)


def test_validate_artifacts_reports_missing_progressive_outputs(tmp_path):
    errors = validate_artifacts(tmp_path)

    assert "progressive_evaluation" in errors
    assert any("progressive_evaluation_manifest.json" in error for error in errors["progressive_evaluation"])
    assert any("association_evaluation_report.md" in error for error in errors["progressive_evaluation"])


def test_validate_artifacts_reports_non_object_progressive_round(tmp_path):
    manifest_path = tmp_path / "data" / "simulated" / "progressive" / "progressive_evaluation_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"rounds": [1]}), encoding="utf-8")
    write_progressive_report(tmp_path)

    errors = validate_artifacts(tmp_path)

    assert any("round 1 must be an object" in error for error in errors["progressive_evaluation"])


def valid_memory_row() -> dict[str, str]:
    return {
        "memory_id": "MEM-D001",
        "memory_version": "v1",
        "disease_id": "D001",
        "disease_type": "crack",
        "source_record_count": "1",
        "source_inspection_ids": "I001",
        "memory_update_mode": "batch_rebuild",
        "memory_confidence": "very_low",
        "memory_limit_note": "",
        "first_seen_inspection": "I001",
        "last_seen_inspection": "I001",
        "inspection_count": "1",
        "last_area_px": "1000",
        "max_area_px": "1000",
        "growth_trend": "数据不足",
        "attention_level": "待补充巡检",
        "mileage_range": "K12+000.0",
        "requires_manual_review": "false",
        "memory_description": "demo",
    }


def valid_association_row() -> dict[str, str]:
    return {
        "association_id": "A1",
        "inspection_id": "I002",
        "frame_id": "1",
        "image_id": "I002_000001",
        "label_disease_id": "D001",
        "memory_id": "MEM-D001",
        "association_status": "matched",
        "rule_basis": "best scored candidate",
        "use_disease_id_score": "false",
        "association_mode": "no_id",
        "association_score": "0.8",
        "spatial_distance_score": "0.8",
        "area_similarity_score": "0.8",
        "temporal_continuity_score": "0.8",
        "risk_similarity_score": "0.8",
        "confidence_level": "medium",
        "match_type": "soft",
        "candidate_count": "1",
        "top_candidate_ids": "MEM-D001:0.8",
        "score_margin": "1.0",
        "conflict_reason": "",
        "needs_manual_review": "false",
        "bbox_fields_present": "false",
        "geometry_score_applied": "false",
        "geometry_feature_available": "false",
        "geometry_limit_note": "missing bbox/mask shape fields in current artifacts",
    }


def write_progressive_report(project_root: Path) -> None:
    report_path = project_root / "outputs" / "association_evaluation_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("# Report\n\n禁用 disease_id\n\n### Baseline / Ablation\n", encoding="utf-8")


def write_progressive_manifest(project_root: Path, round_info: dict[str, str]) -> None:
    manifest_path = project_root / "data" / "simulated" / "progressive" / "progressive_evaluation_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "rounds": [
            {
                "history_inspections": ["I001"],
                "query_inspection": "I002",
                "metrics": {},
                **round_info,
            }
        ]
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def test_memory_schema_rejects_invalid_requires_manual_review_enum(tmp_path):
    path = tmp_path / "memory.csv"
    row = valid_memory_row()
    row["requires_manual_review"] = "maybe"
    write_csv(path, list(row), [row])

    errors = validate_csv_schema(path, "disease_memory_bank")

    assert any("requires_manual_review" in error for error in errors)


def test_validate_artifacts_reports_missing_progressive_referenced_file(tmp_path):
    memory_path = tmp_path / "memory.csv"
    write_csv(memory_path, list(valid_memory_row()), [valid_memory_row()])
    write_progressive_report(tmp_path)
    write_progressive_manifest(
        tmp_path,
        {
            "memory_before": memory_path.as_posix(),
            "association_records": (tmp_path / "missing_association.csv").as_posix(),
            "memory_after": memory_path.as_posix(),
        },
    )

    errors = validate_artifacts(tmp_path)

    assert any("association_records missing file" in error for error in errors["progressive_evaluation"])


def test_validate_artifacts_reports_progressive_association_schema_errors(tmp_path):
    memory_path = tmp_path / "memory.csv"
    bad_association_path = tmp_path / "bad_association.csv"
    write_csv(memory_path, list(valid_memory_row()), [valid_memory_row()])
    write_csv(bad_association_path, ["association_id"], [{"association_id": "A1"}])
    write_progressive_report(tmp_path)
    write_progressive_manifest(
        tmp_path,
        {
            "memory_before": memory_path.as_posix(),
            "association_records": bad_association_path.as_posix(),
            "memory_after": memory_path.as_posix(),
        },
    )

    errors = validate_artifacts(tmp_path)

    assert any("association_records" in error and "missing required columns" in error for error in errors["progressive_evaluation"])


def test_validate_artifacts_reports_progressive_memory_after_schema_errors(tmp_path):
    memory_before = tmp_path / "memory_before.csv"
    memory_after = tmp_path / "memory_after.csv"
    association_path = tmp_path / "association.csv"
    memory_after_row = valid_memory_row()
    memory_after_fields = [field for field in memory_after_row if field != "requires_manual_review"]
    write_csv(memory_before, list(valid_memory_row()), [valid_memory_row()])
    write_csv(memory_after, memory_after_fields, [{field: memory_after_row[field] for field in memory_after_fields}])
    write_csv(association_path, list(valid_association_row()), [valid_association_row()])
    write_progressive_report(tmp_path)
    write_progressive_manifest(
        tmp_path,
        {
            "memory_before": memory_before.as_posix(),
            "association_records": association_path.as_posix(),
            "memory_after": memory_after.as_posix(),
        },
    )

    errors = validate_artifacts(tmp_path)

    assert any("memory_after" in error and "requires_manual_review" in error for error in errors["progressive_evaluation"])
