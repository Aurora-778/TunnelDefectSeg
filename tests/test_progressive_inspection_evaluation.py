import csv
import json
from pathlib import Path

import pytest

from scripts.run_progressive_inspection_evaluation import run_progressive


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


def test_progressive_evaluation_requires_inspection_id(tmp_path):
    input_csv = tmp_path / "bad.csv"
    write_csv(input_csv, [{"image_id": "x"}])

    with pytest.raises(ValueError, match="inspection_id"):
        run_progressive(input_csv, tmp_path / "progressive", tmp_path / "association_report.md")
