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


def growth_row(**overrides):
    row = {
        "disease_id": "D001",
        "disease_type": "crack",
        "inspection_count": "3",
        "first_inspection": "I001",
        "last_inspection": "I003",
        "first_area_px": "1000",
        "last_area_px": "2600",
        "area_growth_px": "1600",
        "area_growth_rate": "1.6",
        "first_mean_area_px": "900.0",
        "last_mean_area_px": "1800.0",
        "mean_area_growth_rate": "1.0",
        "first_frame_count": "1",
        "last_frame_count": "2",
        "frame_count_change": "1",
        "first_risk_level": "低",
        "last_risk_level": "高",
        "risk_level_change": "2",
        "growth_trend": "明显增长",
        "attention_level": "重点关注",
        "first_mileage_range": "K12+000.0",
        "last_mileage_range": "K12+001.0",
        "main_clock_direction": "12点",
        "growth_description": "病害 D001 增长明显。",
        "comparability_status": "verified_comparable",
    }
    row.update(overrides)
    return row


def engineering_row(**overrides):
    row = {
        "inspection_id": "I003",
        "disease_id": "D001",
        "disease_type": "crack",
        "frame_count": "2",
        "start_mileage_m": "12001.0",
        "end_mileage_m": "12001.5",
        "start_mileage_text": "K12+001.0",
        "end_mileage_text": "K12+001.5",
        "start_ring": "1000",
        "end_ring": "1001",
        "main_clock_direction": "12点",
        "max_area_px": "2600",
        "mean_area_px": "1800.0",
        "total_area_px": "3600",
        "risk_level": "高",
        "engineering_description": "工程描述。",
    }
    row.update(overrides)
    return row


def test_generate_visualizations_and_recheck_list(tmp_path):
    growth_csv = tmp_path / "disease_growth_analysis.csv"
    engineering_csv = tmp_path / "disease_engineering_report.csv"
    recheck_csv = tmp_path / "priority_recheck_list.csv"
    vis_dir = tmp_path / "visualizations"
    vis_report = tmp_path / "visualization_report.md"
    recheck_report = tmp_path / "recheck_list_report.md"
    summary_report = tmp_path / "visualization_summary.md"
    write_csv(
        growth_csv,
        [
            growth_row(),
            growth_row(
                disease_id="D002",
                disease_type="water_leakage",
                first_area_px="900",
                last_area_px="1000",
                area_growth_px="100",
                area_growth_rate="0.1111",
                risk_level_change="0",
                growth_trend="基本稳定",
                attention_level="常规记录",
                first_risk_level="低",
                last_risk_level="低",
                last_mileage_range="K12+011.0",
            ),
            growth_row(
                disease_id="D003",
                disease_type="spalling",
                first_area_px="2000",
                last_area_px="2400",
                area_growth_px="400",
                area_growth_rate="0.2",
                risk_level_change="0",
                growth_trend="轻微增长",
                attention_level="持续观察",
                first_risk_level="中",
                last_risk_level="中",
                last_mileage_range="K12+021.0",
            ),
        ],
    )
    write_csv(
        engineering_csv,
        [
            engineering_row(),
            engineering_row(
                disease_id="D002",
                disease_type="water_leakage",
                start_mileage_m="12011.0",
                end_mileage_m="12011.5",
                risk_level="低",
            ),
            engineering_row(
                disease_id="D003",
                disease_type="spalling",
                start_mileage_m="12021.0",
                end_mileage_m="12021.5",
                risk_level="中",
            ),
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/generate_visualization_and_recheck_list.py",
            "--growth-csv",
            str(growth_csv),
            "--engineering-csv",
            str(engineering_csv),
            "--recheck-csv",
            str(recheck_csv),
            "--visualization-dir",
            str(vis_dir),
            "--visualization-report",
            str(vis_report),
            "--recheck-report",
            str(recheck_report),
            "--summary-report",
            str(summary_report),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    chart_names = {
        "attention_level_distribution.png",
        "growth_trend_distribution.png",
        "risk_level_change_distribution.png",
        "top10_area_growth_rate.png",
        "disease_type_distribution.png",
        "mileage_risk_distribution.png",
    }
    assert "病害增长可视化与重点复检清单生成完成" in result.stdout
    assert chart_names == {path.name for path in vis_dir.glob("*.png")}
    assert all((vis_dir / name).stat().st_size > 0 for name in chart_names)
    recheck_rows = read_csv(recheck_csv)
    assert len(recheck_rows) == 1
    assert recheck_rows[0]["priority_rank"] == "1"
    assert recheck_rows[0]["disease_id"] == "D001"
    assert "面积增长率超过 50%" in recheck_rows[0]["recheck_reason"]
    assert "末次巡检风险等级为高" in recheck_rows[0]["recheck_reason"]
    assert "优先安排人工复核" in recheck_rows[0]["recheck_suggestion"]
    assert "图表数量: 6" in summary_report.read_text(encoding="utf-8")
    assert "D001 - 裂缝" in recheck_report.read_text(encoding="utf-8")
    assert "病害总数：3" in vis_report.read_text(encoding="utf-8")


def test_visualization_script_fails_on_missing_growth_column(tmp_path):
    growth_csv = tmp_path / "bad_growth.csv"
    row = growth_row()
    row.pop("area_growth_rate")
    write_csv(growth_csv, [row])

    result = subprocess.run(
        [
            sys.executable,
            "scripts/generate_visualization_and_recheck_list.py",
            "--growth-csv",
            str(growth_csv),
            "--engineering-csv",
            str(tmp_path / "missing_engineering.csv"),
            "--visualization-dir",
            str(tmp_path / "visualizations"),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "missing required columns: area_growth_rate" in result.stderr
