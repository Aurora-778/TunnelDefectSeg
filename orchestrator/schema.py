"""Machine-readable artifact schema checks for the robot inspection pipeline."""

from __future__ import annotations

import csv
from pathlib import Path


REQUIRED_SCHEMAS = {
    "robot_kict_frame_records": [
        "image_id",
        "inspection_id",
        "frame_id",
        "timestamp",
        "mileage_m",
        "mileage_text",
        "ring_id",
        "clock_direction",
        "disease_id",
        "disease_type",
        "kict_image_path",
        "kict_mask_path",
        "kict_area_px",
        "kict_bbox_x1",
        "kict_bbox_y1",
        "kict_bbox_x2",
        "kict_bbox_y2",
        "kict_center_x",
        "kict_center_y",
        "has_crack",
    ],
    "disease_engineering_report": [
        "inspection_id",
        "disease_id",
        "disease_type",
        "frame_count",
        "start_frame",
        "end_frame",
        "start_time",
        "end_time",
        "start_mileage_m",
        "end_mileage_m",
        "start_mileage_text",
        "end_mileage_text",
        "start_ring",
        "end_ring",
        "main_clock_direction",
        "max_area_px",
        "mean_area_px",
        "total_area_px",
        "risk_level",
        "engineering_description",
    ],
    "disease_growth_results": [
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
    ],
    "disease_memory_bank": [
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
        "requires_manual_review",
        "memory_description",
    ],
    "disease_association_records": [
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
    ],
    "priority_recheck_list": [
        "priority_rank",
        "disease_id",
        "disease_type",
        "attention_level",
        "growth_trend",
        "first_inspection",
        "last_inspection",
        "area_growth_rate",
        "last_risk_level",
        "recheck_reason",
        "recheck_suggestion",
    ],
}

REQUIRED_SCHEMAS["progressive_association_records"] = [
    *REQUIRED_SCHEMAS["disease_association_records"],
    "matched_disease_id",
]

ENUMS = {
    "risk_level": {"低", "中", "高"},
    "first_risk_level": {"低", "中", "高"},
    "last_risk_level": {"低", "中", "高"},
    "growth_trend": {"明显增长", "轻微增长", "基本稳定", "面积减小", "数据不足"},
    "attention_level": {"重点关注", "持续观察", "常规记录", "待补充巡检"},
    "association_status": {"matched", "unmatched"},
    "confidence_level": {"high", "medium", "low"},
    "match_type": {"hard", "soft", "uncertain"},
    "memory_update_mode": {"batch_rebuild", "incremental_update"},
    "memory_confidence": {"medium", "low", "very_low"},
    "needs_manual_review": {"true", "false"},
    "requires_manual_review": {"true", "false"},
    "claim_level": {"baseline_only", "rule_evidence_only", "suspected_growth"},
    "comparability_status": {"insufficient_history", "simulated_metadata_comparable", "verified_comparable"},
}


def validate_csv_schema(path: Path, schema_name: str) -> list[str]:
    """Return schema validation errors for a CSV artifact."""

    errors: list[str] = []
    required = REQUIRED_SCHEMAS.get(schema_name)
    if required is None:
        return [f"unknown schema: {schema_name}"]
    if not path.exists():
        return [f"missing file: {path}"]

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        missing = [field for field in required if field not in fieldnames]
        if missing:
            errors.append(f"{schema_name} missing required columns: {', '.join(missing)}")
        rows = list(reader)

    if not rows:
        errors.append(f"{schema_name} is empty: {path}")
        return errors

    for line_number, row in enumerate(rows, start=2):
        for column, allowed in ENUMS.items():
            if column not in fieldnames:
                continue
            value = str(row.get(column, "")).strip()
            if value and value not in allowed:
                errors.append(f"{schema_name} line {line_number}: {column}={value!r} not in {sorted(allowed)}")
    return errors
