import csv
import json
from pathlib import Path

import pytest

from scripts import run_progressive_inspection_evaluation as progressive
from scripts.run_progressive_inspection_evaluation import evaluate_round, run_progressive


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def frame_row(**overrides):
    row = {
        "image_id": "I001_000001",
        "inspection_id": "I001",
        "frame_id": "1",
        "timestamp": "2026-06-01 10:00:00",
        "mileage_m": "12000.0",
        "mileage_text": "K12+000.0",
        "ring_id": "1000",
        "clock_direction": "12点",
        "disease_id": "D001",
        "disease_type": "crack",
        "sim_area_px": "1000",
        "sim_length_m": "0.2",
        "sim_width_mm": "2.0",
        "kict_image_file": "a.jpg",
        "kict_mask_file": "a.png",
        "kict_image_path": "images/a.jpg",
        "kict_mask_path": "masks/a.png",
        "kict_area_px": "1000",
        "kict_bbox_x1": "1",
        "kict_bbox_y1": "2",
        "kict_bbox_x2": "5",
        "kict_bbox_y2": "6",
        "kict_center_x": "3.0",
        "kict_center_y": "4.0",
        "kict_mask_width": "10",
        "kict_mask_height": "10",
        "has_crack": "True",
    }
    row.update(overrides)
    return row


def test_progressive_evaluation_splits_history_and_query_without_future_leakage(tmp_path):
    input_csv = tmp_path / "robot_kict_frame_records.csv"
    write_csv(
        input_csv,
        [
            frame_row(),
            frame_row(
                image_id="I002_000001",
                inspection_id="I002",
                timestamp="2026-07-01 10:00:00",
                kict_area_px="1200",
            ),
            frame_row(
                image_id="I003_000001",
                inspection_id="I003",
                timestamp="2026-08-01 10:00:00",
                kict_area_px="1500",
            ),
        ],
    )

    manifest = run_progressive(input_csv, tmp_path / "progressive", tmp_path / "association_report.md")
    manifest_path = tmp_path / "progressive" / "progressive_evaluation_manifest.json"
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert len(manifest["rounds"]) == 2
    assert saved["rounds"][0]["history_inspections"] == ["I001"]
    assert saved["rounds"][0]["query_inspection"] == "I002"
    assert saved["rounds"][1]["history_inspections"] == ["I001", "I002"]
    assert saved["rounds"][1]["query_inspection"] == "I003"
    assert all("I003" not in " ".join(round_info["allowed_inputs"]) for round_info in saved["rounds"][:1])
    assert (tmp_path / "association_report.md").read_text(encoding="utf-8").count("Baseline / Ablation") == 2


def test_progressive_evaluation_cleans_old_round_outputs(tmp_path):
    input_csv = tmp_path / "robot_kict_frame_records.csv"
    output_dir = tmp_path / "progressive"
    report_path = tmp_path / "association_report.md"
    old_round = output_dir / "round_999"
    old_round.mkdir(parents=True)
    (old_round / "stale.csv").write_text("stale", encoding="utf-8")
    (output_dir / "progressive_evaluation_manifest.json").write_text("stale", encoding="utf-8")
    report_path.write_text("stale", encoding="utf-8")
    write_csv(
        input_csv,
        [
            frame_row(),
            frame_row(image_id="I002_000001", inspection_id="I002", timestamp="2026-07-01 10:00:00"),
        ],
    )

    run_progressive(input_csv, output_dir, report_path)

    assert not old_round.exists()
    assert "stale" not in (output_dir / "progressive_evaluation_manifest.json").read_text(encoding="utf-8")
    assert "stale" not in report_path.read_text(encoding="utf-8")


def test_progressive_evaluation_requires_inspection_id(tmp_path):
    input_csv = tmp_path / "bad.csv"
    write_csv(input_csv, [{"image_id": "x"}])

    with pytest.raises(ValueError, match="inspection_id"):
        run_progressive(input_csv, tmp_path / "progressive", tmp_path / "association_report.md")


def test_evaluate_round_uses_composite_key_for_same_image_records():
    query_rows = [
        frame_row(image_id="I002_same", frame_id="1", disease_id="D001", kict_area_px="1000"),
        frame_row(image_id="I002_same", frame_id="2", disease_id="D002", kict_area_px="2000"),
    ]
    memory_rows = [
        {"memory_id": "MEM-D001", "disease_id": "D001", "mileage_range": "K12+000.0", "last_area_px": "1000"},
        {"memory_id": "MEM-D002", "disease_id": "D002", "mileage_range": "K12+000.0", "last_area_px": "2000"},
    ]
    association_rows = [
        {
            "image_id": "I002_same",
            "frame_id": "1",
            "disease_id": "D001",
            "memory_id": "MEM-D001",
            "association_status": "matched",
            "needs_manual_review": "false",
        },
        {
            "image_id": "I002_same",
            "frame_id": "2",
            "disease_id": "D002",
            "memory_id": "MEM-D002",
            "association_status": "matched",
            "needs_manual_review": "false",
        },
    ]

    metrics = evaluate_round(query_rows, memory_rows, association_rows)

    assert metrics["strategy_accuracy"]["weighted_score_no_id"] == 1.0
    assert metrics["failure_examples"] == []


def test_evaluate_round_rejects_duplicate_association_composite_key():
    query_rows = [frame_row(image_id="I002_same", frame_id="1", disease_id="D001")]
    memory_rows = [{"memory_id": "MEM-D001", "disease_id": "D001", "mileage_range": "K12+000.0", "last_area_px": "1000"}]
    association = {
        "image_id": "I002_same",
        "frame_id": "1",
        "disease_id": "D001",
        "memory_id": "MEM-D001",
        "association_status": "matched",
        "needs_manual_review": "false",
    }

    with pytest.raises(ValueError, match="Duplicate association composite key"):
        evaluate_round(query_rows, memory_rows, [association, dict(association)])


def test_progressive_manifest_uses_relative_paths_for_project_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(progressive, "PROJECT_ROOT", tmp_path)
    input_csv = tmp_path / "data" / "simulated" / "robot_kict_frame_records.csv"
    write_csv(
        input_csv,
        [
            frame_row(),
            frame_row(image_id="I002_000001", inspection_id="I002", timestamp="2026-07-01 10:00:00"),
        ],
    )

    progressive.run_progressive(
        Path("data/simulated/robot_kict_frame_records.csv"),
        Path("data/simulated/progressive"),
        Path("outputs/association_evaluation_report.md"),
    )
    manifest_path = tmp_path / "data" / "simulated" / "progressive" / "progressive_evaluation_manifest.json"
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    first_round = saved["rounds"][0]

    assert saved["input_csv"] == "data/simulated/robot_kict_frame_records.csv"
    assert first_round["memory_before"].startswith("data/simulated/progressive/")
    assert first_round["association_records"].startswith("data/simulated/progressive/")
    assert not Path(first_round["memory_before"]).is_absolute()
