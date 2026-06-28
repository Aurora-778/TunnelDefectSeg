import csv
from pathlib import Path

from orchestrator.agents.final_report_agent import FinalReportAgent


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_final_report_agent_writes_reports_and_validates_outputs(tmp_path):
    data_dir = tmp_path / "data" / "simulated"
    outputs = tmp_path / "outputs"
    visualization_dir = outputs / "visualizations"
    visualization_dir.mkdir(parents=True)
    for index in range(7):
        (visualization_dir / f"chart_{index}.png").write_bytes(b"png")

    engineering_report = data_dir / "disease_engineering_report.csv"
    growth_results = data_dir / "disease_growth_results.csv"
    memory_bank = data_dir / "disease_memory_bank.csv"
    association_records = data_dir / "disease_association_records.csv"
    recheck_list = data_dir / "priority_recheck_list.csv"
    write_csv(engineering_report, [{"disease_id": "D001"}])
    write_csv(
        growth_results,
        [{"disease_id": "D001", "growth_trend": "明显增长", "last_risk_level": "高", "area_growth_rate": "1.0"}],
    )
    write_csv(memory_bank, [{"memory_id": "MEM-D001", "disease_id": "D001"}])
    write_csv(association_records, [{"association_status": "matched", "label_disease_id": "D001"}])
    write_csv(
        recheck_list,
        [{"disease_id": "D001", "attention_level": "重点关注", "recheck_reason": "需复核"}],
    )
    context = {
        "inputs": {
            "final_report": {
                "engineering_report": str(engineering_report),
                "growth_results": str(growth_results),
                "memory_bank": str(memory_bank),
                "association_records": str(association_records),
                "recheck_list": str(recheck_list),
                "visualization_dir": str(visualization_dir),
                "final_report": str(outputs / "final_project_report.md"),
                "system_summary": str(outputs / "system_summary.md"),
                "key_insights": str(outputs / "key_insights.md"),
            }
        },
        "outputs": {},
        "shared": {"project_root": str(tmp_path)},
    }

    result = FinalReportAgent().run(context)

    assert result["final_report_ready"] is True
    assert (outputs / "final_project_report.md").exists()
    assert (outputs / "system_summary.md").exists()
    assert (outputs / "key_insights.md").exists()
