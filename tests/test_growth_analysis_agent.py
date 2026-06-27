import csv
from pathlib import Path

from orchestrator.agents.growth_analysis_agent import GrowthAnalysisAgent


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def engineering_row(**overrides):
    row = {
        "inspection_id": "I001",
        "disease_id": "D001",
        "disease_type": "crack",
        "frame_count": "1",
        "start_frame": "1",
        "end_frame": "1",
        "start_time": "2026-06-01 10:00:00",
        "end_time": "2026-06-01 10:00:00",
        "start_mileage_m": "12000.0",
        "end_mileage_m": "12000.0",
        "start_mileage_text": "K12+000.0",
        "end_mileage_text": "K12+000.0",
        "start_ring": "1000",
        "end_ring": "1000",
        "main_clock_direction": "12点",
        "max_area_px": "1000",
        "mean_area_px": "1000",
        "total_area_px": "1000",
        "risk_level": "低",
        "engineering_description": "病害描述",
    }
    row.update(overrides)
    return row


def test_growth_analysis_agent_generates_standard_and_legacy_csv(tmp_path):
    engineering_report = tmp_path / "data" / "simulated" / "disease_engineering_report.csv"
    output_path = tmp_path / "data" / "simulated" / "disease_growth_results.csv"
    legacy_output_path = tmp_path / "data" / "simulated" / "disease_growth_analysis.csv"
    markdown_path = tmp_path / "outputs" / "disease_growth_analysis_report.md"
    summary_path = tmp_path / "outputs" / "disease_growth_analysis_summary.md"
    write_csv(
        engineering_report,
        [
            engineering_row(),
            engineering_row(inspection_id="I002", max_area_px="2000", mean_area_px="2000", risk_level="高"),
        ],
    )
    context = {
        "inputs": {
            "growth_analysis": {
                "engineering_report": str(engineering_report),
                "output_path": str(output_path),
                "legacy_output_path": str(legacy_output_path),
                "markdown_path": str(markdown_path),
                "summary_path": str(summary_path),
            }
        },
        "outputs": {},
        "shared": {"project_root": str(tmp_path)},
    }

    result = GrowthAnalysisAgent().run(context)

    assert result["growth_rows"] == 1
    assert output_path.exists()
    assert legacy_output_path.exists()
    assert markdown_path.exists()
    assert summary_path.exists()
