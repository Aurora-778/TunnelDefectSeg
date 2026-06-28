import csv
from pathlib import Path

from orchestrator.agents.visualization_agent import VisualizationAgent


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def growth_row(**overrides):
    row = {
        "disease_id": "D001",
        "disease_type": "crack",
        "inspection_count": "2",
        "first_inspection": "I001",
        "last_inspection": "I002",
        "first_area_px": "1000",
        "last_area_px": "2000",
        "area_growth_px": "1000",
        "area_growth_rate": "1.0",
        "first_mean_area_px": "1000",
        "last_mean_area_px": "2000",
        "mean_area_growth_rate": "1.0",
        "first_frame_count": "1",
        "last_frame_count": "1",
        "frame_count_change": "0",
        "first_risk_level": "低",
        "last_risk_level": "高",
        "risk_level_change": "2",
        "growth_trend": "明显增长",
        "attention_level": "重点关注",
        "first_mileage_range": "K12+000.0",
        "last_mileage_range": "K12+010.0",
        "main_clock_direction": "12点",
        "growth_description": "增长明显",
    }
    row.update(overrides)
    return row


def engineering_row(**overrides):
    row = {
        "inspection_id": "I001",
        "disease_id": "D001",
        "disease_type": "crack",
        "frame_count": "1",
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


def test_visualization_agent_generates_charts_recheck_and_graph(tmp_path, monkeypatch):
    monkeypatch.setenv("FAST_TEST_MODE", "1")
    growth_results = tmp_path / "data" / "simulated" / "disease_growth_results.csv"
    engineering_report = tmp_path / "data" / "simulated" / "disease_engineering_report.csv"
    association_records = tmp_path / "data" / "simulated" / "disease_association_records.csv"
    recheck_list = tmp_path / "data" / "simulated" / "priority_recheck_list.csv"
    visualization_dir = tmp_path / "outputs" / "visualizations"
    write_csv(growth_results, [growth_row()])
    write_csv(engineering_report, [engineering_row()])
    write_csv(
        association_records,
        [{"label_disease_id": "D001", "association_status": "matched"}],
    )
    context = {
        "inputs": {
            "visualization": {
                "engineering_report": str(engineering_report),
                "growth_results": str(growth_results),
                "association_records": str(association_records),
                "recheck_list": str(recheck_list),
                "visualization_dir": str(visualization_dir),
                "visualization_report": str(tmp_path / "outputs" / "visualization_report.md"),
                "recheck_report": str(tmp_path / "outputs" / "recheck_list_report.md"),
                "visualization_summary": str(tmp_path / "outputs" / "visualization_summary.md"),
                "association_graph": str(visualization_dir / "association_relationship_graph.png"),
            }
        },
        "outputs": {},
        "shared": {"project_root": str(tmp_path)},
    }

    result = VisualizationAgent().run(context)

    assert result["chart_count"] >= 7
    assert result["recheck_rows"] == 1
    assert recheck_list.exists()
    assert (visualization_dir / "association_relationship_graph.png").exists()
