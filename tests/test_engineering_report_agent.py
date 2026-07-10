import csv
from pathlib import Path

from orchestrator.agents.engineering_report_agent import EngineeringReportAgent


def write_csv(path: Path, rows: list[dict]) -> None:
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
        "kict_image_path": "images/a.jpg",
        "kict_mask_path": "masks/a.png",
        "kict_area_px": "1000",
        "kict_bbox_x1": "1",
        "kict_bbox_y1": "2",
        "kict_bbox_x2": "5",
        "kict_bbox_y2": "6",
        "kict_center_x": "3.0",
        "kict_center_y": "4.0",
        "has_crack": "True",
        "observation_source": "verified_fixture",
        "comparability_status": "verified_comparable",
    }
    row.update(overrides)
    return row


def test_engineering_report_agent_generates_csv_and_reports(tmp_path):
    frame_records = tmp_path / "data" / "simulated" / "robot_kict_frame_records.csv"
    output_path = tmp_path / "data" / "simulated" / "disease_engineering_report.csv"
    markdown_path = tmp_path / "outputs" / "disease_engineering_report.md"
    summary_path = tmp_path / "outputs" / "disease_engineering_report_summary.md"
    write_csv(frame_records, [frame_row(), frame_row(frame_id="2", kict_area_px="1200")])
    context = {
        "inputs": {
            "engineering_report": {
                "frame_records": str(frame_records),
                "output_path": str(output_path),
                "markdown_path": str(markdown_path),
                "summary_path": str(summary_path),
            }
        },
        "outputs": {},
        "shared": {"project_root": str(tmp_path)},
    }

    result = EngineeringReportAgent().run(context)

    assert result["engineering_rows"] == 1
    assert output_path.exists()
    assert markdown_path.exists()
    assert summary_path.exists()
