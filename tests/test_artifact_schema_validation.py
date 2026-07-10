import csv
import json
from pathlib import Path

from orchestrator.schema import validate_csv_schema
from scripts.validate_artifacts import validate_artifacts, validate_main_history_only_association


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
    row = valid_association_row()
    write_csv(path, list(row), [row])

    assert validate_csv_schema(path, "disease_association_records") == []


def test_validate_csv_schema_reports_invalid_bool_value(tmp_path):
    path = tmp_path / "association.csv"
    row = valid_association_row()
    row["needs_manual_review"] = "maybe"
    write_csv(path, list(row), [row])

    errors = validate_csv_schema(path, "disease_association_records")

    assert any("needs_manual_review" in error and "true/false" in error for error in errors)


def test_validate_csv_schema_reports_empty_bool_value(tmp_path):
    path = tmp_path / "association.csv"
    row = valid_association_row()
    row["needs_manual_review"] = ""
    write_csv(path, list(row), [row])

    errors = validate_csv_schema(path, "disease_association_records")

    assert any("needs_manual_review" in error and "true/false" in error for error in errors)


def test_validate_csv_schema_accepts_zero_one_bool_values(tmp_path):
    path = tmp_path / "association.csv"
    row = valid_association_row()
    row["use_disease_id_score"] = "0"
    row["needs_manual_review"] = "1"
    write_csv(path, list(row), [row])

    assert validate_csv_schema(path, "disease_association_records") == []


def test_validate_csv_schema_accepts_titlecase_bool_values(tmp_path):
    path = tmp_path / "association.csv"
    row = valid_association_row()
    row["use_disease_id_score"] = "False"
    row["needs_manual_review"] = "True"
    write_csv(path, list(row), [row])

    assert validate_csv_schema(path, "disease_association_records") == []


def test_validate_csv_schema_reports_score_out_of_range(tmp_path):
    path = tmp_path / "association.csv"
    row = valid_association_row()
    row["association_score"] = "1.5"
    write_csv(path, list(row), [row])

    errors = validate_csv_schema(path, "disease_association_records")

    assert any("association_score" in error and "<= 1.0" in error for error in errors)


def test_validate_csv_schema_allows_score_margin_greater_than_one(tmp_path):
    path = tmp_path / "association.csv"
    row = valid_association_row()
    row["score_margin"] = "2.0"
    write_csv(path, list(row), [row])

    assert validate_csv_schema(path, "disease_association_records") == []


def test_validate_csv_schema_reports_negative_score_margin(tmp_path):
    path = tmp_path / "association.csv"
    row = valid_association_row()
    row["score_margin"] = "-0.1"
    write_csv(path, list(row), [row])

    errors = validate_csv_schema(path, "disease_association_records")

    assert any("score_margin" in error and ">= 0.0" in error for error in errors)


def test_validate_csv_schema_reports_non_integer_candidate_count(tmp_path):
    path = tmp_path / "association.csv"
    row = valid_association_row()
    row["candidate_count"] = "1.2"
    write_csv(path, list(row), [row])

    errors = validate_csv_schema(path, "disease_association_records")

    assert any("candidate_count" in error and "integer" in error for error in errors)


def test_validate_csv_schema_allows_negative_area_growth(tmp_path):
    path = tmp_path / "growth.csv"
    row = valid_growth_row()
    row["area_growth_px"] = "-10"
    write_csv(path, list(row), [row])

    assert validate_csv_schema(path, "disease_growth_results") == []


def test_validate_csv_schema_reports_non_numeric_area_growth(tmp_path):
    path = tmp_path / "growth.csv"
    row = valid_growth_row()
    row["area_growth_px"] = "not-a-number"
    write_csv(path, list(row), [row])

    errors = validate_csv_schema(path, "disease_growth_results")

    assert any("area_growth_px" in error and "not a number" in error for error in errors)


def test_validate_csv_schema_reports_negative_non_growth_area(tmp_path):
    path = tmp_path / "growth.csv"
    row = valid_growth_row()
    row["first_area_px"] = "-1"
    write_csv(path, list(row), [row])

    errors = validate_csv_schema(path, "disease_growth_results")

    assert any("first_area_px" in error and ">= 0.0" in error for error in errors)


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


def test_optional_geometry_fields_are_not_required_when_geometry_unavailable(tmp_path):
    path = tmp_path / "association.csv"
    row = valid_association_row()
    row.pop("bbox_fields_present")
    row.pop("geometry_score_applied")
    write_csv(path, list(row), [row])

    assert validate_csv_schema(path, "disease_association_records") == []


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


def test_main_history_validator_rejects_current_inspection_in_history(tmp_path):
    source_path = tmp_path / "data" / "simulated" / "robot_kict_frame_records.csv"
    association_path = tmp_path / "data" / "simulated" / "disease_association_records.csv"
    manifest_path = tmp_path / "data" / "simulated" / "main_progressive" / "association_manifest.json"
    source_row = {
        "inspection_id": "I002",
        "frame_id": "1",
        "image_id": "I002_000001",
        "disease_id": "D001",
    }
    association_row = valid_association_row()
    association_row["history_inspection_ids"] = "I001|I002"
    write_csv(source_path, list(source_row), [source_row])
    write_csv(association_path, list(association_row), [association_row])
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "mode": "history_only",
                "rounds": [
                    {
                        "query_inspection": "I002",
                        "history_inspection_ids": ["I001", "I002"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    errors = validate_main_history_only_association(tmp_path)

    assert any("current or future inspection" in error for error in errors)


def test_main_history_validator_rejects_duplicate_source_composite_key(tmp_path):
    source_path = tmp_path / "data" / "simulated" / "robot_kict_frame_records.csv"
    source_row = {
        "inspection_id": "I002",
        "frame_id": "1",
        "image_id": "I002_000001",
        "disease_id": "D001",
    }
    write_csv(source_path, list(source_row), [source_row, dict(source_row)])

    errors = validate_main_history_only_association(tmp_path)

    assert any("duplicate composite key" in error for error in errors)


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
        "comparability_status": "insufficient_history",
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
        "history_inspection_ids": "I001",
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


def valid_progressive_association_row() -> dict[str, str]:
    row = valid_association_row()
    row["matched_disease_id"] = "D001"
    return row


def valid_growth_row() -> dict[str, str]:
    return {
        "disease_id": "D001",
        "disease_type": "crack",
        "inspection_count": "2",
        "first_inspection": "I001",
        "last_inspection": "I002",
        "first_area_px": "100",
        "last_area_px": "90",
        "area_growth_px": "-10",
        "area_growth_rate": "-0.1",
        "first_risk_level": "低",
        "last_risk_level": "低",
        "risk_level_change": "stable",
        "growth_trend": "面积减小",
        "attention_level": "常规记录",
        "measurement_basis": "mask_area",
        "claim_level": "rule_evidence_only",
        "comparability_status": "simulated_metadata_comparable",
        "main_clock_direction": "3点",
        "growth_description": "面积减小。",
    }


def write_progressive_report(project_root: Path) -> None:
    report_path = project_root / "outputs" / "association_evaluation_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("# Report\n\n禁用 disease_id\n\n### Baseline / Ablation\n", encoding="utf-8")


def write_progressive_manifest(
    project_root: Path,
    round_info: dict[str, str],
    *,
    include_mode_association_records: bool = True,
    include_allowed_inputs: bool = True,
    source_dataset: str = "data/source.csv",
) -> None:
    manifest_path = project_root / "data" / "simulated" / "progressive" / "progressive_evaluation_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    full_round_info = dict(round_info)
    if include_allowed_inputs:
        full_round_info.setdefault("allowed_inputs", [])
    if include_mode_association_records and "association_records" in full_round_info:
        full_round_info.setdefault("no_id_association_records", full_round_info["association_records"])
        full_round_info.setdefault("with_id_association_records", full_round_info["association_records"])
    manifest = {
        "source_dataset": source_dataset,
        "rounds": [
            {
                "history_inspections": ["I001"],
                "query_inspection": "I002",
                "metrics": {},
                **full_round_info,
            }
        ]
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def write_valid_progressive_outputs(project_root: Path) -> tuple[Path, Path]:
    no_id_path = project_root / "data" / "simulated" / "disease_association_records_no_id.csv"
    with_id_path = project_root / "data" / "simulated" / "disease_association_records_with_id.csv"
    row = valid_progressive_association_row()
    write_csv(no_id_path, list(row), [row])
    row_with_id = dict(row)
    row_with_id["use_disease_id_score"] = "true"
    row_with_id["association_mode"] = "with_id_upper_bound"
    write_csv(with_id_path, list(row_with_id), [row_with_id])
    return no_id_path, with_id_path


def test_memory_schema_rejects_invalid_requires_manual_review_enum(tmp_path):
    path = tmp_path / "memory.csv"
    row = valid_memory_row()
    row["requires_manual_review"] = "maybe"
    write_csv(path, list(row), [row])

    errors = validate_csv_schema(path, "disease_memory_bank")

    assert any("requires_manual_review" in error for error in errors)


def test_memory_schema_rejects_empty_requires_manual_review(tmp_path):
    path = tmp_path / "memory.csv"
    row = valid_memory_row()
    row["requires_manual_review"] = ""
    write_csv(path, list(row), [row])

    errors = validate_csv_schema(path, "disease_memory_bank")

    assert any("requires_manual_review" in error and "true/false" in error for error in errors)


def test_memory_schema_rejects_invalid_memory_version(tmp_path):
    for version in ["1", "version1", "v", "vabc", ""]:
        path = tmp_path / f"memory_invalid_{version or 'empty'}.csv"
        row = valid_memory_row()
        row["memory_version"] = version
        write_csv(path, list(row), [row])

        errors = validate_csv_schema(path, "disease_memory_bank")

        assert any("memory_version" in error for error in errors)


def test_memory_schema_accepts_valid_memory_versions(tmp_path):
    for version in ["v1", "v1.0", "v2"]:
        path = tmp_path / f"memory_{version.replace('.', '_')}.csv"
        row = valid_memory_row()
        row["memory_version"] = version
        write_csv(path, list(row), [row])

        assert validate_csv_schema(path, "disease_memory_bank") == []


def test_validate_artifacts_reports_main_association_not_no_id(tmp_path):
    association_path = tmp_path / "data" / "simulated" / "disease_association_records.csv"
    row = valid_association_row()
    row["use_disease_id_score"] = "true"
    row["association_mode"] = "with_id_upper_bound"
    write_csv(association_path, list(row), [row])

    errors = validate_artifacts(tmp_path)

    assert any("association_mode" in error and "no_id" in error for error in errors["disease_association_records"])


def test_validate_artifacts_requires_canonical_main_association_bool(tmp_path):
    association_path = tmp_path / "data" / "simulated" / "disease_association_records.csv"
    row = valid_association_row()
    row["use_disease_id_score"] = "0"
    row["association_mode"] = "no_id"
    write_csv(association_path, list(row), [row])

    errors = validate_artifacts(tmp_path)

    assert any("use_disease_id_score='0' expected 'false'" in error for error in errors["disease_association_records"])


def test_validate_artifacts_reports_progressive_no_id_mode_error(tmp_path):
    no_id_path, _ = write_valid_progressive_outputs(tmp_path)
    rows = [valid_progressive_association_row()]
    rows[0]["use_disease_id_score"] = "true"
    write_csv(no_id_path, list(rows[0]), rows)
    write_progressive_report(tmp_path)

    errors = validate_artifacts(tmp_path)

    assert any("disease_association_records_no_id.csv" in error and "false" in error for error in errors["progressive_evaluation"])


def test_validate_artifacts_reports_progressive_with_id_mode_error(tmp_path):
    _, with_id_path = write_valid_progressive_outputs(tmp_path)
    rows = [valid_progressive_association_row()]
    rows[0]["use_disease_id_score"] = "true"
    rows[0]["association_mode"] = "no_id"
    write_csv(with_id_path, list(rows[0]), rows)
    write_progressive_report(tmp_path)

    errors = validate_artifacts(tmp_path)

    assert any("disease_association_records_with_id.csv" in error and "with_id_upper_bound" in error for error in errors["progressive_evaluation"])


def test_validate_artifacts_reports_missing_progressive_referenced_file(tmp_path):
    memory_path = tmp_path / "memory.csv"
    write_csv(memory_path, list(valid_memory_row()), [valid_memory_row()])
    write_valid_progressive_outputs(tmp_path)
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


def test_validate_artifacts_reports_missing_round_no_id_or_with_id_records(tmp_path):
    memory_path = tmp_path / "memory.csv"
    association_path = tmp_path / "association.csv"
    write_csv(memory_path, list(valid_memory_row()), [valid_memory_row()])
    write_csv(association_path, list(valid_progressive_association_row()), [valid_progressive_association_row()])
    write_valid_progressive_outputs(tmp_path)
    write_progressive_report(tmp_path)
    write_progressive_manifest(
        tmp_path,
        {
            "memory_before": memory_path.as_posix(),
            "association_records": association_path.as_posix(),
            "memory_after": memory_path.as_posix(),
        },
        include_mode_association_records=False,
    )

    errors = validate_artifacts(tmp_path)

    assert any("missing no_id_association_records" in error for error in errors["progressive_evaluation"])
    assert any("missing with_id_association_records" in error for error in errors["progressive_evaluation"])


def test_validate_artifacts_reports_source_dataset_inside_allowed_inputs(tmp_path):
    memory_path = tmp_path / "memory.csv"
    association_path = tmp_path / "association.csv"
    source_dataset = "data/source.csv"
    write_csv(memory_path, list(valid_memory_row()), [valid_memory_row()])
    write_csv(association_path, list(valid_progressive_association_row()), [valid_progressive_association_row()])
    write_valid_progressive_outputs(tmp_path)
    write_progressive_report(tmp_path)
    write_progressive_manifest(
        tmp_path,
        {
            "memory_before": memory_path.as_posix(),
            "association_records": association_path.as_posix(),
            "memory_after": memory_path.as_posix(),
            "allowed_inputs": [source_dataset],
        },
        source_dataset=source_dataset,
    )

    errors = validate_artifacts(tmp_path)

    assert any("allowed_inputs must not contain source_dataset" in error for error in errors["progressive_evaluation"])


def test_validate_artifacts_reports_equivalent_source_dataset_inside_allowed_inputs(tmp_path):
    memory_path = tmp_path / "memory.csv"
    association_path = tmp_path / "association.csv"
    source_dataset = "data/source.csv"
    write_csv(memory_path, list(valid_memory_row()), [valid_memory_row()])
    write_csv(association_path, list(valid_progressive_association_row()), [valid_progressive_association_row()])
    write_valid_progressive_outputs(tmp_path)
    write_progressive_report(tmp_path)
    write_progressive_manifest(
        tmp_path,
        {
            "memory_before": memory_path.as_posix(),
            "association_records": association_path.as_posix(),
            "memory_after": memory_path.as_posix(),
            "allowed_inputs": [(tmp_path / "data" / "source.csv").resolve().as_posix()],
        },
        source_dataset=source_dataset,
    )

    errors = validate_artifacts(tmp_path)

    assert any("allowed_inputs must not contain source_dataset" in error for error in errors["progressive_evaluation"])


def test_validate_artifacts_reports_missing_allowed_inputs(tmp_path):
    memory_path = tmp_path / "memory.csv"
    association_path = tmp_path / "association.csv"
    write_csv(memory_path, list(valid_memory_row()), [valid_memory_row()])
    write_csv(association_path, list(valid_progressive_association_row()), [valid_progressive_association_row()])
    write_valid_progressive_outputs(tmp_path)
    write_progressive_report(tmp_path)
    write_progressive_manifest(
        tmp_path,
        {
            "memory_before": memory_path.as_posix(),
            "association_records": association_path.as_posix(),
            "memory_after": memory_path.as_posix(),
        },
        include_allowed_inputs=False,
    )

    errors = validate_artifacts(tmp_path)

    assert any("missing allowed_inputs" in error for error in errors["progressive_evaluation"])


def test_validate_artifacts_reports_progressive_association_schema_errors(tmp_path):
    memory_path = tmp_path / "memory.csv"
    bad_association_path = tmp_path / "bad_association.csv"
    write_csv(memory_path, list(valid_memory_row()), [valid_memory_row()])
    write_csv(bad_association_path, ["association_id"], [{"association_id": "A1"}])
    write_valid_progressive_outputs(tmp_path)
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


def test_validate_artifacts_reports_progressive_association_missing_matched_disease_id(tmp_path):
    memory_path = tmp_path / "memory.csv"
    association_path = tmp_path / "association.csv"
    row = valid_progressive_association_row()
    fieldnames = [field for field in row if field != "matched_disease_id"]
    write_csv(memory_path, list(valid_memory_row()), [valid_memory_row()])
    write_csv(association_path, fieldnames, [{field: row[field] for field in fieldnames}])
    write_valid_progressive_outputs(tmp_path)
    write_progressive_report(tmp_path)
    write_progressive_manifest(
        tmp_path,
        {
            "memory_before": memory_path.as_posix(),
            "association_records": association_path.as_posix(),
            "memory_after": memory_path.as_posix(),
        },
    )

    errors = validate_artifacts(tmp_path)

    assert any(
        "association_records" in error and "matched_disease_id" in error
        for error in errors["progressive_evaluation"]
    )


def test_validate_artifacts_reports_progressive_memory_after_schema_errors(tmp_path):
    memory_before = tmp_path / "memory_before.csv"
    memory_after = tmp_path / "memory_after.csv"
    association_path = tmp_path / "association.csv"
    memory_after_row = valid_memory_row()
    memory_after_fields = [field for field in memory_after_row if field != "requires_manual_review"]
    write_csv(memory_before, list(valid_memory_row()), [valid_memory_row()])
    write_csv(memory_after, memory_after_fields, [{field: memory_after_row[field] for field in memory_after_fields}])
    write_csv(association_path, list(valid_progressive_association_row()), [valid_progressive_association_row()])
    write_valid_progressive_outputs(tmp_path)
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


def main_source_row(inspection_id: str, **overrides: str) -> dict[str, str]:
    row = {
        "image_id": f"{inspection_id}_000001",
        "inspection_id": inspection_id,
        "frame_id": "1",
        "timestamp": "2026-06-01 10:00:00",
        "mileage_m": "12000",
        "mileage_text": "K12+000.0",
        "ring_id": "1000",
        "clock_direction": "12点",
        "disease_id": "D001",
        "disease_type": "crack",
        "kict_image_path": "images/a.jpg",
        "kict_mask_path": "masks/a.png",
        "kict_area_px": "1000",
        "kict_bbox_x1": "1",
        "kict_bbox_y1": "1",
        "kict_bbox_x2": "2",
        "kict_bbox_y2": "2",
        "kict_center_x": "1.5",
        "kict_center_y": "1.5",
        "has_crack": "true",
        "observation_source": "verified_fixture",
        "comparability_status": "verified_comparable",
    }
    row.update(overrides)
    return row


def write_main_history_fixture(tmp_path: Path, main_rows: list[dict[str, str]] | None = None) -> None:
    source_path = tmp_path / "data" / "simulated" / "robot_kict_frame_records.csv"
    main_path = tmp_path / "data" / "simulated" / "disease_association_records.csv"
    root = tmp_path / "data" / "simulated" / "main_progressive"
    baseline_query = root / "round_001" / "query_frames.csv"
    round_query = root / "round_002" / "query_frames.csv"
    round_assoc = root / "round_002" / "association_records.csv"
    memory_before = root / "round_002" / "memory_before.csv"
    memory_after = root / "round_002" / "memory_after.csv"
    source_rows = [main_source_row("I001"), main_source_row("I002")]
    row = valid_association_row()
    write_csv(source_path, list(source_rows[0]), source_rows)
    write_csv(baseline_query, list(source_rows[0]), [source_rows[0]])
    write_csv(round_query, list(source_rows[1]), [source_rows[1]])
    write_csv(round_assoc, list(row), [row])
    write_csv(memory_before, list(valid_memory_row()), [valid_memory_row()])
    write_csv(memory_after, list(valid_memory_row()), [valid_memory_row()])
    write_csv(main_path, list(row), main_rows or [row])
    (root / "association_manifest.json").write_text(
        json.dumps(
            {
                "mode": "history_only",
                "source_frame_records": "data/simulated/robot_kict_frame_records.csv",
                "rounds": [
                    {
                        "round_index": 1,
                        "query_inspection": "I001",
                        "history_inspection_ids": [],
                        "query_frames": "data/simulated/main_progressive/round_001/query_frames.csv",
                    },
                    {
                        "round_index": 2,
                        "query_inspection": "I002",
                        "history_inspection_ids": ["I001"],
                        "query_frames": "data/simulated/main_progressive/round_002/query_frames.csv",
                        "memory_before": "data/simulated/main_progressive/round_002/memory_before.csv",
                        "association_records": "data/simulated/main_progressive/round_002/association_records.csv",
                        "memory_after": "data/simulated/main_progressive/round_002/memory_after.csv",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def test_main_history_validator_rejects_duplicate_main_association_key(tmp_path):
    row = valid_association_row()
    write_main_history_fixture(tmp_path, [row, dict(row)])

    errors = validate_main_history_only_association(tmp_path)

    assert any("duplicate association composite key" in error for error in errors)


def test_main_history_validator_rejects_missing_round_memory_after(tmp_path):
    write_main_history_fixture(tmp_path)
    (tmp_path / "data" / "simulated" / "main_progressive" / "round_002" / "memory_after.csv").unlink()

    errors = validate_main_history_only_association(tmp_path)

    assert any("memory_after missing file" in error for error in errors)


def test_main_history_validator_rejects_round_main_record_mismatch(tmp_path):
    write_main_history_fixture(tmp_path)
    main_path = tmp_path / "data" / "simulated" / "disease_association_records.csv"
    row = valid_association_row()
    row["association_status"] = "unmatched"
    write_csv(main_path, list(row), [row])

    errors = validate_main_history_only_association(tmp_path)

    assert any("records disagree with main association CSV" in error for error in errors)


def test_progressive_validator_rejects_future_history_in_with_id_artifact(tmp_path):
    source_path = tmp_path / "data" / "source.csv"
    memory_path = tmp_path / "memory.csv"
    association_path = tmp_path / "association.csv"
    no_id_path, with_id_path = write_valid_progressive_outputs(tmp_path)
    source_row = {"inspection_id": "I002", "frame_id": "1", "image_id": "I002_000001", "disease_id": "D001"}
    with_id_row = valid_progressive_association_row()
    with_id_row["use_disease_id_score"] = "true"
    with_id_row["association_mode"] = "with_id_upper_bound"
    with_id_row["history_inspection_ids"] = "I001|I002"
    write_csv(source_path, list(source_row), [source_row])
    write_csv(memory_path, list(valid_memory_row()), [valid_memory_row()])
    write_csv(association_path, list(valid_progressive_association_row()), [valid_progressive_association_row()])
    write_csv(with_id_path, list(with_id_row), [with_id_row])
    write_progressive_report(tmp_path)
    write_progressive_manifest(
        tmp_path,
        {
            "memory_before": memory_path.as_posix(),
            "association_records": association_path.as_posix(),
            "no_id_association_records": no_id_path.as_posix(),
            "with_id_association_records": with_id_path.as_posix(),
            "memory_after": memory_path.as_posix(),
        },
        source_dataset=source_path.as_posix(),
    )

    errors = validate_artifacts(tmp_path)

    assert any("with_id_association_records" in error and "current or future" in error for error in errors["progressive_evaluation"])
