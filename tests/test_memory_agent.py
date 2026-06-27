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
    assert memory["growth_trend"] == "明显增长"
    assert memory["attention_level"] == "重点关注"
    assert memory["memory_version"] == "v1"
    assert memory["memory_update_mode"] == "batch_rebuild"
    assert memory["memory_confidence"] == "low"
    assert memory["source_record_count"] == "2"
    assert memory["source_inspection_ids"] == "I001|I002"
    assert "病害D001为裂缝" in memory["memory_description"]

    assert Path(result["memory_agent_report_path"]).exists()
    assert Path(result["memory_agent_log_path"]).exists()
    assert result["memory_bank_rows"] == 1


def test_memory_agent_incremental_update_appends_current_inspection(tmp_path):
    data_dir = tmp_path / "data" / "simulated"
    memory_fields = [
        "memory_id",
        "memory_version",
        "disease_id",
        "disease_type",
        "source_record_count",
        "source_inspection_ids",
        "memory_update_mode",
        "memory_confidence",
        "memory_limit_note",
        "first_seen_inspection",
        "last_seen_inspection",
        "inspection_count",
        "total_seen_frames",
        "first_area_px",
        "last_area_px",
        "max_area_px",
        "area_growth_px",
        "area_growth_rate",
        "first_risk_level",
        "last_risk_level",
        "risk_level_change",
        "growth_trend",
        "attention_level",
        "main_clock_direction",
        "mileage_range",
        "representative_image_path",
        "representative_mask_path",
        "requires_manual_review",
        "memory_description",
    ]
    write_csv(
        data_dir / "memory.csv",
        [
            {
                "memory_id": "MEM-D001",
                "memory_version": "v1",
                "disease_id": "D001",
                "disease_type": "crack",
                "source_record_count": "1",
                "source_inspection_ids": "I001",
                "memory_update_mode": "batch_rebuild",
                "memory_confidence": "very_low",
                "memory_limit_note": "",
                "first_seen_inspection": "I001",
                "last_seen_inspection": "I001",
                "inspection_count": "1",
                "total_seen_frames": "1",
                "first_area_px": "1000",
                "last_area_px": "1000",
                "max_area_px": "1000",
                "area_growth_px": "0",
                "area_growth_rate": "0.000000",
                "first_risk_level": "低",
                "last_risk_level": "低",
                "risk_level_change": "0",
                "growth_trend": "数据不足",
                "attention_level": "待补充巡检",
                "main_clock_direction": "12点",
                "mileage_range": "K12+000.0",
                "representative_image_path": "images/a.jpg",
                "representative_mask_path": "masks/a.png",
                "requires_manual_review": "false",
                "memory_description": "old",
            }
        ],
        memory_fields,
    )
    frame_fields = [
        "image_id",
        "inspection_id",
        "frame_id",
        "disease_id",
        "disease_type",
        "mileage_text",
        "clock_direction",
        "kict_area_px",
        "kict_image_path",
        "kict_mask_path",
    ]
    write_csv(
        data_dir / "query.csv",
        [
            {
                "image_id": "I002_000001",
                "inspection_id": "I002",
                "frame_id": "1",
                "disease_id": "D001",
                "disease_type": "crack",
                "mileage_text": "K12+002.0",
                "clock_direction": "12点",
                "kict_area_px": "1800",
                "kict_image_path": "images/b.jpg",
                "kict_mask_path": "masks/b.png",
            }
        ],
        frame_fields,
    )
    write_csv(
        data_dir / "association.csv",
        [
            {
                "image_id": "I002_000001",
                "memory_id": "MEM-D001",
                "association_status": "matched",
                "needs_manual_review": "false",
            }
        ],
        ["image_id", "memory_id", "association_status", "needs_manual_review"],
    )

    context = {
        "inputs": {
            "memory": {
                "mode": "incremental_update",
                "previous_memory": "data/simulated/memory.csv",
                "frame_records": "data/simulated/query.csv",
                "association_records": "data/simulated/association.csv",
                "output_path": "data/simulated/memory_next.csv",
                "report_path": "outputs/memory_incremental_report.md",
                "log_path": "logs/memory_incremental.log",
            }
        },
        "outputs": {},
        "shared": {"project_root": str(tmp_path)},
    }

    result = MemoryAgent().run(context)
    row = read_csv(Path(result["disease_memory_bank_path"]))[0]

    assert row["memory_version"] == "v2"
    assert row["memory_update_mode"] == "incremental_update"
    assert row["source_inspection_ids"] == "I001|I002"
    assert row["last_seen_inspection"] == "I002"
    assert row["last_area_px"] == "1800"
    assert row["requires_manual_review"] == "false"
