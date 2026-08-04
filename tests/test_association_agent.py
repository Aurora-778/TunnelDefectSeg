from __future__ import annotations

import csv
from pathlib import Path

from orchestrator.agents.association_agent import AssociationAgent


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def memory_row(**overrides):
    row = {
        "memory_id": "MEM-D001",
        "disease_id": "D001",
        "disease_type": "crack",
        "first_seen_inspection": "I001",
        "last_seen_inspection": "I002",
        "last_area_px": "1000",
        "max_area_px": "1200",
        "last_risk_level": "低",
        "main_clock_direction": "12点",
        "mileage_range": "K12+000.0 - K12+010.0",
    }
    row.update(overrides)
    return row


def frame_row(**overrides):
    row = {
        "inspection_id": "I002",
        "frame_id": "1",
        "image_id": "I002_000001",
        "disease_id": "D001",
        "disease_type": "crack",
        "mileage_text": "K12+008.0",
        "clock_direction": "12点",
        "kict_area_px": "1100",
        "kict_image_path": "images/a.jpg",
        "kict_mask_path": "masks/a.png",
    }
    row.update(overrides)
    return row


def test_association_agent_outputs_explainable_match_scores(tmp_path):
    frame_path = tmp_path / "frames.csv"
    memory_path = tmp_path / "memory.csv"
    output_path = tmp_path / "association.csv"
    write_csv(
        memory_path,
        [
            memory_row(),
            memory_row(
                memory_id="MEM-D009",
                disease_id="D009",
                last_area_px="3000",
                last_risk_level="高",
                main_clock_direction="3点",
                mileage_range="K13+000.0 - K13+010.0",
            ),
        ],
    )
    write_csv(
        frame_path,
        [
            frame_row(),
            frame_row(disease_id="D404", mileage_text="K12+006.0", kict_area_px="1050"),
            frame_row(disease_id="D999", mileage_text="K15+000.0", clock_direction="6点", kict_area_px="100"),
        ],
    )
    context = {
        "inputs": {
            "association": {
                "frame_records": str(frame_path),
                "memory_bank": str(memory_path),
                "output_path": str(output_path),
            }
        },
        "outputs": {},
        "shared": {"project_root": str(tmp_path)},
    }

    result = AssociationAgent().run(context)
    rows = read_csv(output_path)

    assert result["association_rows"] == 3
    assert rows[0]["match_type"] == "soft"
    assert rows[0]["confidence_level"] == "high"
    assert float(rows[0]["association_score"]) >= 0.8
    assert rows[0]["use_disease_id_score"] == "false"
    assert rows[0]["association_mode"] == "no_id"
    assert rows[0]["label_disease_id"] == "D001"
    assert rows[0]["bbox_fields_present"] == "false"
    assert rows[0]["geometry_score_applied"] == "false"
    assert rows[0]["geometry_feature_available"] == "false"
    assert rows[0]["geometry_limit_note"] == "missing bbox/mask shape fields in current artifacts"

    assert rows[1]["match_type"] == "soft"
    assert rows[1]["memory_id"] == "MEM-D001"
    assert rows[1]["association_status"] == "matched"
    assert float(rows[1]["spatial_distance_score"]) > 0.8

    assert rows[2]["match_type"] == "uncertain"
    assert rows[2]["association_status"] == "unmatched"
    assert rows[2]["confidence_level"] == "low"
    assert "candidate_count" in rows[0]
    assert "score_margin" in rows[0]
    assert "needs_manual_review" in rows[0]
    assert "same disease_id" not in rows[0]["rule_basis"]


def test_same_id_with_spatial_conflict_is_not_high_confidence(tmp_path):
    frame_path = tmp_path / "frames.csv"
    memory_path = tmp_path / "memory.csv"
    output_path = tmp_path / "association.csv"
    write_csv(memory_path, [memory_row(disease_id="D001", mileage_range="K12+000.0 - K12+010.0")])
    write_csv(frame_path, [frame_row(disease_id="D001", mileage_text="K99+000.0", kict_area_px="1000")])
    context = {
        "inputs": {"association": {"frame_records": str(frame_path), "memory_bank": str(memory_path), "output_path": str(output_path)}},
        "outputs": {},
        "shared": {"project_root": str(tmp_path)},
    }

    AssociationAgent().run(context)
    row = read_csv(output_path)[0]

    assert row["needs_manual_review"] == "true"
    assert row["match_type"] in {"uncertain", "soft"}
    assert row["confidence_level"] != "high"
    assert "spatial mismatch" in row["conflict_reason"]


def test_multiple_close_candidates_reports_score_margin(tmp_path):
    frame_path = tmp_path / "frames.csv"
    memory_path = tmp_path / "memory.csv"
    output_path = tmp_path / "association.csv"
    write_csv(
        memory_path,
        [
            memory_row(memory_id="MEM-D010", disease_id="D010", mileage_range="K12+000.0 - K12+010.0", last_area_px="1000"),
            memory_row(memory_id="MEM-D011", disease_id="D011", mileage_range="K12+001.0 - K12+011.0", last_area_px="1020"),
        ],
    )
    write_csv(frame_path, [frame_row(disease_id="D404", mileage_text="K12+006.0", kict_area_px="1010")])
    context = {
        "inputs": {"association": {"frame_records": str(frame_path), "memory_bank": str(memory_path), "output_path": str(output_path)}},
        "outputs": {},
        "shared": {"project_root": str(tmp_path)},
    }

    AssociationAgent().run(context)
    row = read_csv(output_path)[0]

    assert float(row["score_margin"]) < 0.15
    assert row["needs_manual_review"] == "true"
    assert row["candidate_count"] == "2"
    assert "|" in row["top_candidate_ids"]


def test_missing_disease_id_can_soft_match_by_spatial_and_area(tmp_path):
    frame_path = tmp_path / "frames.csv"
    memory_path = tmp_path / "memory.csv"
    output_path = tmp_path / "association.csv"
    write_csv(memory_path, [memory_row()])
    write_csv(frame_path, [frame_row(disease_id="", mileage_text="K12+006.0", kict_area_px="1000")])
    context = {
        "inputs": {"association": {"frame_records": str(frame_path), "memory_bank": str(memory_path), "output_path": str(output_path)}},
        "outputs": {},
        "shared": {"project_root": str(tmp_path)},
    }

    AssociationAgent().run(context)
    row = read_csv(output_path)[0]

    assert row["match_type"] == "soft"
    assert row["association_status"] == "matched"


def test_association_can_disable_disease_id_score(tmp_path):
    frame_path = tmp_path / "frames.csv"
    memory_path = tmp_path / "memory.csv"
    output_path = tmp_path / "association.csv"
    write_csv(
        memory_path,
        [
            memory_row(memory_id="MEM-D001", disease_id="D001", mileage_range="K99+000.0 - K99+010.0", last_area_px="9000"),
            memory_row(memory_id="MEM-D002", disease_id="D002", mileage_range="K12+000.0 - K12+010.0", last_area_px="1000"),
        ],
    )
    write_csv(frame_path, [frame_row(disease_id="D001", mileage_text="K12+006.0", kict_area_px="1000")])
    context = {
        "inputs": {
            "association": {
                "frame_records": str(frame_path),
                "memory_bank": str(memory_path),
                "output_path": str(output_path),
                "use_disease_id_score": "false",
            }
        },
        "outputs": {},
        "shared": {"project_root": str(tmp_path)},
    }

    AssociationAgent().run(context)
    row = read_csv(output_path)[0]

    assert row["memory_id"] == "MEM-D002"


def test_disabled_disease_id_score_does_not_create_hard_match_or_rule_basis(tmp_path):
    frame_path = tmp_path / "frames.csv"
    memory_path = tmp_path / "memory.csv"
    output_path = tmp_path / "association.csv"
    write_csv(memory_path, [memory_row()])
    write_csv(frame_path, [frame_row(disease_id="D001", mileage_text="K12+006.0", kict_area_px="1000")])
    context = {
        "inputs": {
            "association": {
                "frame_records": str(frame_path),
                "memory_bank": str(memory_path),
                "output_path": str(output_path),
                "use_disease_id_score": "false",
            }
        },
        "outputs": {},
        "shared": {"project_root": str(tmp_path)},
    }

    AssociationAgent().run(context)
    row = read_csv(output_path)[0]

    assert row["memory_id"] == "MEM-D001"
    assert row["match_type"] == "soft"
    assert "same disease_id" not in row["rule_basis"]


def test_default_association_mode_is_no_id(tmp_path):
    frame_path = tmp_path / "frames.csv"
    memory_path = tmp_path / "memory.csv"
    output_path = tmp_path / "association.csv"
    write_csv(memory_path, [memory_row()])
    write_csv(frame_path, [frame_row(disease_id="D001", mileage_text="K12+006.0", kict_area_px="1000")])
    context = {
        "inputs": {"association": {"frame_records": str(frame_path), "memory_bank": str(memory_path), "output_path": str(output_path)}},
        "outputs": {},
        "shared": {"project_root": str(tmp_path)},
    }

    AssociationAgent().run(context)
    row = read_csv(output_path)[0]

    assert row["use_disease_id_score"] == "false"
    assert row["association_mode"] == "no_id"
    assert row["label_disease_id"] == "D001"
    assert row["match_type"] == "soft"
    assert "same disease_id" not in row["rule_basis"]


def test_with_id_upper_bound_still_rejects_spatial_conflict_as_hard_match(tmp_path):
    frame_path = tmp_path / "frames.csv"
    memory_path = tmp_path / "memory.csv"
    output_path = tmp_path / "association.csv"
    write_csv(memory_path, [memory_row(disease_id="D001", mileage_range="K12+000.0 - K12+010.0")])
    write_csv(frame_path, [frame_row(disease_id="D001", mileage_text="K99+000.0", kict_area_px="1000")])
    context = {
        "inputs": {
            "association": {
                "frame_records": str(frame_path),
                "memory_bank": str(memory_path),
                "output_path": str(output_path),
                "use_disease_id_score": "true",
                "association_mode": "with_id_upper_bound",
            }
        },
        "outputs": {},
        "shared": {"project_root": str(tmp_path)},
    }

    AssociationAgent().run(context)
    row = read_csv(output_path)[0]

    assert row["use_disease_id_score"] == "true"
    assert row["association_mode"] == "with_id_upper_bound"
    assert row["match_type"] != "hard"
    assert row["needs_manual_review"] == "true"
    assert "spatial mismatch" in row["conflict_reason"]


def test_top_candidate_ids_use_memory_ids_when_disease_id_score_disabled(tmp_path):
    frame_path = tmp_path / "frames.csv"
    memory_path = tmp_path / "memory.csv"
    output_path = tmp_path / "association.csv"
    write_csv(memory_path, [memory_row(memory_id="MEM-D001", disease_id="D001")])
    write_csv(frame_path, [frame_row(disease_id="D001", mileage_text="K12+006.0", kict_area_px="1000")])
    context = {
        "inputs": {
            "association": {
                "frame_records": str(frame_path),
                "memory_bank": str(memory_path),
                "output_path": str(output_path),
                "use_disease_id_score": "false",
            }
        },
        "outputs": {},
        "shared": {"project_root": str(tmp_path)},
    }

    AssociationAgent().run(context)
    row = read_csv(output_path)[0]

    assert row["top_candidate_ids"].startswith("MEM-D001:")
    assert not row["top_candidate_ids"].startswith("D001:")


def test_no_id_mode_disease_id_equivariance(tmp_path):
    """no-id 模式下 disease_id 任意取值都不改变输出（除 label_disease_id）。"""
    base_memory = memory_row(disease_id="D001")
    base_frame = frame_row(disease_id="D001", mileage_text="K12+006.0", kict_area_px="1000")

    # 第一份：原始 disease_id
    frame_a = tmp_path / "frames_a.csv"
    memory_a = tmp_path / "memory_a.csv"
    output_a = tmp_path / "assoc_a.csv"
    write_csv(memory_a, [base_memory])
    write_csv(frame_a, [base_frame])

    # 第二份：所有 disease_id 重排为完全不同的值
    frame_b = tmp_path / "frames_b.csv"
    memory_b = tmp_path / "memory_b.csv"
    output_b = tmp_path / "assoc_b.csv"
    write_csv(memory_b, [memory_row(disease_id="D777")])
    write_csv(frame_b, [frame_row(disease_id="D999", mileage_text="K12+006.0", kict_area_px="1000")])

    context_a = {
        "inputs": {"association": {"frame_records": str(frame_a), "memory_bank": str(memory_a), "output_path": str(output_a)}},
        "outputs": {},
        "shared": {"project_root": str(tmp_path)},
    }
    context_b = {
        "inputs": {"association": {"frame_records": str(frame_b), "memory_bank": str(memory_b), "output_path": str(output_b)}},
        "outputs": {},
        "shared": {"project_root": str(tmp_path)},
    }

    AssociationAgent().run(context_a)
    AssociationAgent().run(context_b)

    row_a = read_csv(output_a)[0]
    row_b = read_csv(output_b)[0]

    # 这些字段必须完全一致
    for field in [
        "association_id",
        "association_score",
        "spatial_distance_score",
        "area_similarity_score",
        "temporal_continuity_score",
        "risk_similarity_score",
        "match_type",
        "confidence_level",
        "needs_manual_review",
        "rule_basis",
        "conflict_reason",
        "association_mode",
        "use_disease_id_score",
        "candidate_count",
        "score_margin",
    ]:
        assert row_a[field] == row_b[field], f"disease_id equivariance violated on '{field}': {row_a[field]} != {row_b[field]}"

    # label_disease_id 允许不同
    assert row_a["label_disease_id"] == "D001"
    assert row_b["label_disease_id"] == "D999"


def test_no_id_mode_does_not_compute_same_id_for_scoring(tmp_path):
    """no-id 模式下，即使 frame.disease_id == memory.disease_id，空间/面积冲突也不能 hard match 或提高 score。"""
    frame_path = tmp_path / "frames.csv"
    memory_path = tmp_path / "memory.csv"
    output_path = tmp_path / "association.csv"
    # frame 与 memory disease_id 相同，但空间完全冲突
    write_csv(memory_path, [memory_row(disease_id="D001", mileage_range="K12+000.0 - K12+010.0")])
    write_csv(frame_path, [frame_row(disease_id="D001", mileage_text="K99+000.0", kict_area_px="1000")])
    context = {
        "inputs": {"association": {"frame_records": str(frame_path), "memory_bank": str(memory_path), "output_path": str(output_path)}},
        "outputs": {},
        "shared": {"project_root": str(tmp_path)},
    }

    AssociationAgent().run(context)
    row = read_csv(output_path)[0]

    assert row["use_disease_id_score"] == "false"
    assert row["association_mode"] == "no_id"
    # disease_id 相同也不得 hard match
    assert row["match_type"] != "hard"
    assert "same disease_id" not in row["rule_basis"]
    # 空间冲突应触发 review
    assert row["needs_manual_review"] == "true"
    assert "spatial mismatch" in row["conflict_reason"]
