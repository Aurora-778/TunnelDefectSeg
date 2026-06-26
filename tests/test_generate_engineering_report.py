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


def base_row(**overrides):
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
        "kict_image_path": "images/a.jpg",
        "kict_mask_path": "masks/a.png",
        "kict_area_px": "1200",
        "kict_bbox_x1": "1",
        "kict_bbox_y1": "2",
        "kict_bbox_x2": "5",
        "kict_bbox_y2": "6",
        "kict_center_x": "3.0",
        "kict_center_y": "4.0",
        "has_crack": "True",
    }
    row.update(overrides)
    return row


def test_generate_engineering_report_aggregates_robot_kict_records(tmp_path):
    input_csv = tmp_path / "robot_kict_frame_records.csv"
    output_csv = tmp_path / "disease_engineering_report.csv"
    markdown_report = tmp_path / "disease_engineering_report.md"
    summary_report = tmp_path / "disease_engineering_report_summary.md"
    write_csv(
        input_csv,
        [
            base_row(),
            base_row(
                image_id="I001_000002",
                frame_id="2",
                timestamp="2026-06-01 10:00:01",
                mileage_m="12000.5",
                mileage_text="K12+000.5",
                ring_id="1001",
                clock_direction="1点",
                kict_image_path="images/b.jpg",
                kict_mask_path="masks/b.png",
                kict_area_px="3200",
                kict_bbox_x1="10",
                kict_bbox_y1="11",
                kict_bbox_x2="20",
                kict_bbox_y2="21",
            ),
            base_row(
                inspection_id="I002",
                disease_id="D002",
                disease_type="water_leakage",
                timestamp="2026-07-01 10:00:00",
                kict_area_px="1800",
            ),
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/generate_engineering_report.py",
            "--input-csv",
            str(input_csv),
            "--output-csv",
            str(output_csv),
            "--markdown-report",
            str(markdown_report),
            "--summary-report",
            str(summary_report),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    rows = read_csv(output_csv)
    first = rows[0]
    assert "工程化病害报告生成完成" in result.stdout
    assert len(rows) == 2
    assert first["inspection_id"] == "I001"
    assert first["disease_id"] == "D001"
    assert first["frame_count"] == "2"
    assert first["start_frame"] == "1"
    assert first["end_frame"] == "2"
    assert first["start_mileage_text"] == "K12+000.0"
    assert first["end_mileage_text"] == "K12+000.5"
    assert first["max_area_px"] == "3200"
    assert first["mean_area_px"] == "2200.0"
    assert first["total_area_px"] == "4400"
    assert first["representative_image_path"] == "images/b.jpg"
    assert first["risk_level"] == "高"
    assert "连续 2 帧可见" in first["engineering_description"]
    assert "发现裂缝 D001" in first["engineering_description"]
    assert markdown_report.exists()
    assert summary_report.exists()


def test_generate_engineering_report_fails_on_missing_required_column(tmp_path):
    input_csv = tmp_path / "bad.csv"
    row = base_row()
    row.pop("kict_area_px")
    write_csv(input_csv, [row])

    result = subprocess.run(
        [sys.executable, "scripts/generate_engineering_report.py", "--input-csv", str(input_csv)],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "missing required columns" in result.stderr
