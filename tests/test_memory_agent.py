import csv
from pathlib import Path

from orchestrator.agents.memory_agent import MemoryAgent


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def test_memory_agent_builds_cross_inspection_memory(tmp_path):
    data_dir = tmp_path / "data" / "simulated"
    report_fields = [
        "inspection_id",
        "disease_id",
        "disease_type",
        "frame_count",
        "max_area_px",
        "risk_level",
        "main_clock_direction",
        "start_mileage_text",
        "end_mileage_text",
        "representative_image_path",
        "representative_mask_path",
    ]
    # Intentionally write I002 before I001 to verify inspection_id sorting.
    write_csv(
        data_dir / "disease_engineering_report.csv",
        [
            {
                "inspection_id": "I002",
                "disease_id": "D001",
                "disease_type": "crack",
                "frame_count": "3",
                "max_area_px": "150",
                "risk_level": "高",
                "main_clock_direction": "3点",
                "start_mileage_text": "K12+010",
                "end_mileage_text": "K12+012",
                "representative_image_path": "images/new.png",
                "representative_mask_path": "masks/new.png",
            },
            {
                "inspection_id": "I001",
                "disease_id": "D001",
                "disease_type": "crack",
                "frame_count": "2",
                "max_area_px": "100",
                "risk_level": "中",
                "main_clock_direction": "2点",
                "start_mileage_text": "K12+010",
                "end_mileage_text": "K12+011",
                "representative_image_path": "images/old.png",
                "representative_mask_path": "masks/old.png",
            },
        ],
        report_fields,
    )
    write_csv(
        data_dir / "disease_growth_analysis.csv",
        [{"disease_id": "D001", "main_clock_direction": "3点"}],
        ["disease_id", "main_clock_direction"],
    )

    context = {
        "inputs": {
            "memory": {
                "engineering_report": "data/simulated/disease_engineering_report.csv",
                "growth_analysis": "data/simulated/disease_growth_analysis.csv",
                "output_path": "data/simulated/disease_memory_bank.csv",
                "report_path": "outputs/memory_agent_report.md",
                "summary_path": "outputs/disease_memory_bank_summary.md",
                "log_path": "logs/memory_agent.log",
            }
        },
        "outputs": {},
        "shared": {"project_root": str(tmp_path)},
    }

    result = MemoryAgent().run(context)

    rows = read_csv(Path(result["disease_memory_bank_path"]))
    assert len(rows) == 1
    memory = rows[0]
    assert memory["first_seen_inspection"] == "I001"
    assert memory["last_seen_inspection"] == "I002"
    assert memory["inspection_count"] == "2"
    assert memory["total_seen_frames"] == "5"
    assert memory["first_area_px"] == "100"
    assert memory["last_area_px"] == "150"
    assert memory["area_growth_px"] == "50"
    assert memory["area_growth_rate"] == "0.500000"
    assert memory["risk_level_change"] == "1"
    assert memory["growth_trend"] == "increasing"
    assert memory["attention_level"] == "high"
    assert "病害D001为裂缝" in memory["memory_description"]

    assert Path(result["memory_agent_report_path"]).exists()
    assert Path(result["memory_agent_log_path"]).exists()
    assert context["outputs"]["memory"]["memory_bank_rows"] == 1
