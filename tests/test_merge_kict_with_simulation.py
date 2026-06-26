import csv
import subprocess
import sys
from pathlib import Path


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def test_merge_kict_with_simulation_outputs_robot_frame_records(tmp_path):
    sim_dir = tmp_path / "simulated"
    output_csv = tmp_path / "robot_kict_frame_records.csv"
    report_file = tmp_path / "robot_kict_merge_report.md"
    kict_features = sim_dir / "kict_mask_features.csv"

    write_csv(
        sim_dir / "inspection_sequence.csv",
        [
            {
                "image_id": "I001_000001",
                "inspection_id": "I001",
                "frame_id": "1",
                "timestamp": "2026-06-01 10:00:00",
                "mileage_m": "12000.0",
                "mileage_text": "K12+000.0",
                "ring_id": "1000",
                "clock_direction": "12点",
                "image_path": "data/images/I001_000001.jpg",
            },
            {
                "image_id": "I001_000002",
                "inspection_id": "I001",
                "frame_id": "2",
                "timestamp": "2026-06-01 10:00:01",
                "mileage_m": "12000.5",
                "mileage_text": "K12+000.5",
                "ring_id": "1000",
                "clock_direction": "1点",
                "image_path": "data/images/I001_000002.jpg",
            },
        ],
    )
    write_csv(
        sim_dir / "frame_disease_mapping.csv",
        [
            {
                "image_id": "I001_000001",
                "inspection_id": "I001",
                "frame_id": "1",
                "disease_id": "D001",
                "disease_type": "crack",
                "mileage_m": "999",
                "mileage_text": "wrong",
                "ring_id": "999",
                "clock_direction": "wrong",
                "area_px": "100",
                "length_m": "1.1",
                "width_mm": "2.2",
            },
            {
                "image_id": "I001_000002",
                "inspection_id": "I001",
                "frame_id": "2",
                "disease_id": "D002",
                "disease_type": "spalling",
                "mileage_m": "999",
                "mileage_text": "wrong",
                "ring_id": "999",
                "clock_direction": "wrong",
                "area_px": "200",
                "length_m": "1.2",
                "width_mm": "0.0",
            },
        ],
    )
    write_csv(
        kict_features,
        [
            {
                "image_file": "images/a.jpg",
                "mask_file": "masks/a.png",
                "image_path": "images/a.jpg",
                "mask_path": "masks/a.png",
                "area_px": "12",
                "bbox_x1": "1",
                "bbox_y1": "2",
                "bbox_x2": "5",
                "bbox_y2": "6",
                "center_x": "3.0",
                "center_y": "4.0",
                "mask_width": "4",
                "mask_height": "4",
                "has_crack": "TRUE",
            }
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/merge_kict_with_simulation.py",
            "--sim-dir",
            str(sim_dir),
            "--kict-features",
            str(kict_features),
            "--output-csv",
            str(output_csv),
            "--report-file",
            str(report_file),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    rows = read_csv(output_csv)
    assert "机器人巡检仿真表与 KICT mask 特征合并完成" in result.stdout
    assert len(rows) == 2
    assert all(row["kict_area_px"] == "12" for row in rows)
    assert all(row["kict_image_path"] == "images/a.jpg" for row in rows)
    assert rows[0]["mileage_text"] == "K12+000.0"
    assert rows[1]["clock_direction"] == "1点"
    assert rows[0]["sim_area_px"] == "100"
    assert rows[1]["disease_id"] == "D002"
    assert report_file.exists()
