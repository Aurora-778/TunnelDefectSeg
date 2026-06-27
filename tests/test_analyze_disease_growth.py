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
        "inspection_id": "I001",
        "disease_id": "D001",
        "disease_type": "crack",
        "frame_count": "1",
        "start_frame": "10",
        "end_frame": "10",
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
        "mean_area_px": "900.0",
        "total_area_px": "900",
        "risk_level": "低",
        "engineering_description": "desc",
    }
    row.update(overrides)
    return row


def test_analyze_disease_growth_outputs_trends_and_attention(tmp_path):
    input_csv = tmp_path / "disease_engineering_report.csv"
    output_csv = tmp_path / "disease_growth_analysis.csv"
    markdown_report = tmp_path / "disease_growth_analysis_report.md"
    summary_report = tmp_path / "disease_growth_analysis_summary.md"
    write_csv(
        input_csv,
        [
            base_row(),
            base_row(
                inspection_id="I002",
                start_time="2026-07-01 10:00:00",
                end_time="2026-07-01 10:00:00",
                max_area_px="1600",
                mean_area_px="1200.0",
                risk_level="中",
            ),
            base_row(
                inspection_id="I003",
                start_time="2026-08-01 10:00:00",
                end_time="2026-08-01 10:00:00",
                start_mileage_text="K12+001.0",
                end_mileage_text="K12+001.0",
                max_area_px="2600",
                mean_area_px="1800.0",
                risk_level="高",
            ),
            base_row(
                disease_id="D002",
                disease_type="spalling",
                max_area_px="800",
                mean_area_px="800.0",
                risk_level="低",
            ),
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/analyze_disease_growth.py",
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

    rows = {row["disease_id"]: row for row in read_csv(output_csv)}
    d001 = rows["D001"]
    d002 = rows["D002"]
    assert "跨巡检病害增长分析完成" in result.stdout
    assert d001["inspection_count"] == "3"
    assert d001["first_inspection"] == "I001"
    assert d001["last_inspection"] == "I003"
    assert d001["area_growth_px"] == "1600"
    assert d001["area_growth_rate"] == "1.6"
    assert d001["mean_area_growth_rate"] == "1.0"
    assert d001["risk_level_change"] == "2"
    assert d001["growth_trend"] == "明显增长"
    assert d001["attention_level"] == "重点关注"
    assert d001["measurement_basis"] == "cross_inspection_area_rule"
    assert d001["claim_level"] == "rule_evidence_only"
    assert d001["comparability_status"] == "simulated_metadata_comparable"
    assert "增长率约为 160.0%" in d001["growth_description"]
    assert d002["growth_trend"] == "数据不足"
    assert d002["attention_level"] == "待补充巡检"
    assert d002["claim_level"] == "baseline_only"
    assert "当前仅作为基线记录" in d002["growth_description"]
    assert markdown_report.exists()
    assert "涉及巡检次数：3" in markdown_report.read_text(encoding="utf-8")
    assert summary_report.exists()


def test_analyze_disease_growth_fails_on_missing_required_column(tmp_path):
    input_csv = tmp_path / "bad.csv"
    row = base_row()
    row.pop("max_area_px")
    write_csv(input_csv, [row])

    result = subprocess.run(
        [sys.executable, "scripts/analyze_disease_growth.py", "--input-csv", str(input_csv)],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "missing required columns" in result.stderr
