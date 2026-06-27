import csv
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
        "disease_id",
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
        "candidate_count",
        "top_candidate_ids",
        "score_margin",
        "conflict_reason",
        "needs_manual_review",
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
        "disease_id",
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
