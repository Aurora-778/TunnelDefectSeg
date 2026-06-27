import csv
import importlib.util
from pathlib import Path


def load_project_runner():
    spec = importlib.util.spec_from_file_location("project_runner", Path("run.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


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
        "sim_area_px": "1000",
        "sim_length_m": "0.2",
        "sim_width_mm": "2.0",
        "kict_image_file": "a.jpg",
        "kict_mask_file": "a.png",
        "kict_image_path": "images/a.jpg",
        "kict_mask_path": "masks/a.png",
        "kict_area_px": "1000",
        "kict_bbox_x1": "1",
        "kict_bbox_y1": "2",
        "kict_bbox_x2": "5",
        "kict_bbox_y2": "6",
        "kict_center_x": "3.0",
        "kict_center_y": "4.0",
        "kict_mask_width": "10",
        "kict_mask_height": "10",
        "has_crack": "True",
    }
    row.update(overrides)
    return row


def test_full_pipeline_runner_creates_end_to_end_outputs(tmp_path):
    runner = load_project_runner()
    frame_csv = tmp_path / "data" / "simulated" / "robot_kict_frame_records.csv"
    write_csv(
        frame_csv,
        [
            frame_row(),
            frame_row(
                image_id="I002_000001",
                inspection_id="I002",
                timestamp="2026-07-01 10:00:00",
                mileage_m="12010.0",
                mileage_text="K12+010.0",
                ring_id="1001",
                kict_image_path="images/b.jpg",
                kict_mask_path="masks/b.png",
                kict_area_px="3600",
                kict_bbox_x1="10",
                kict_bbox_y1="11",
                kict_bbox_x2="20",
                kict_bbox_y2="21",
            ),
        ],
    )

    result = runner.run_full_pipeline(tmp_path)

    assert result["engineering_rows"] == 2
    assert result["growth_rows"] == 1
    assert result["memory_rows"] == 1
    assert result["association_rows"] == 2
    assert result["association_matched_rows"] == 2
    assert result["chart_count"] >= 7
    assert result["task_status"] == {"full_pipeline": "success"}
    assert result["run_id"].startswith("run_")
    assert (tmp_path / "runs" / result["run_id"] / "dag.json").exists()

    data_dir = tmp_path / "data" / "simulated"
    output_dir = tmp_path / "outputs"
    assert read_csv(data_dir / "disease_memory_bank.csv")[0]["disease_id"] == "D001"
    assert read_csv(data_dir / "disease_association_records.csv")[0]["association_status"] == "matched"
    assert read_csv(data_dir / "disease_growth_results.csv")[0]["growth_trend"] == "明显增长"
    assert (data_dir / "disease_growth_analysis.csv").exists()
    assert (data_dir / "association_records.csv").exists()
    assert (output_dir / "visualizations" / "association_relationship_graph.png").exists()

    final_report = (output_dir / "final_project_report.md").read_text(encoding="utf-8")
    assert "# 隧道巡检病害监测系统完整报告" in final_report
    assert "## 创新点" in final_report
    assert "## 局限性" in final_report
    assert "## 未来扩展" in final_report
    assert (output_dir / "system_summary.md").exists()
    assert (output_dir / "key_insights.md").exists()
