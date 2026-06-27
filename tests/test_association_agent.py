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
    assert rows[0]["match_type"] == "hard"
    assert rows[0]["confidence_level"] == "high"
    assert float(rows[0]["association_score"]) >= 0.9

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
