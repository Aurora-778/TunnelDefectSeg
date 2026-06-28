"""Test progressive evaluation produces required outputs and report content."""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path


def load_progressive_script():
    spec = importlib.util.spec_from_file_location(
        "run_progressive_inspection_evaluation",
        Path("scripts/run_progressive_inspection_evaluation.py"),
    )
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


def test_progressive_evaluation_produces_outputs(tmp_path):
    """progressive evaluation 必须生成 no-id CSV、with-id CSV 和 markdown 报告。"""
    script = load_progressive_script()

    # 构造 3 个 inspection 的 frame 数据
    input_csv = tmp_path / "frames.csv"
    write_csv(
        input_csv,
        [
            frame_row(),
            frame_row(
                image_id="I002_000001",
                inspection_id="I002",
                timestamp="2026-07-01 10:00:00",
                mileage_m="12010.0",
                mileage_text="K12+010.0",
                ring_id="1001",
            ),
            frame_row(
                image_id="I003_000001",
                inspection_id="I003",
                timestamp="2026-08-01 10:00:00",
                mileage_m="12020.0",
                mileage_text="K12+020.0",
                ring_id="1002",
            ),
        ],
    )

    output_dir = tmp_path / "progressive"
    no_id_csv = tmp_path / "disease_association_records_no_id.csv"
    with_id_csv = tmp_path / "disease_association_records_with_id.csv"
    report_path = tmp_path / "association_evaluation_report.md"

    script.run_progressive(input_csv, output_dir, no_id_csv, with_id_csv, report_path)

    # 产物存在
    assert no_id_csv.exists(), "no-id CSV not generated"
    assert with_id_csv.exists(), "with-id CSV not generated"
    assert report_path.exists(), "evaluation report not generated"

    # no-id CSV 标记
    no_id_rows = read_csv(no_id_csv)
    assert len(no_id_rows) > 0
    for row in no_id_rows:
        assert row["use_disease_id_score"] == "false"
        assert row["association_mode"] == "no_id"

    # with-id CSV 标记
    with_id_rows = read_csv(with_id_csv)
    assert len(with_id_rows) > 0
    for row in with_id_rows:
        assert row["use_disease_id_score"] == "true"
        assert row["association_mode"] == "with_id_upper_bound"

    # 报告包含必需关键词
    report_text = report_path.read_text(encoding="utf-8")
    for keyword in [
        "no-id",
        "with-id",
        "upper-bound",
        "progressive",
        "future memory",
    ]:
        assert keyword.lower() in report_text.lower(), f"report missing keyword: {keyword}"


def test_progressive_evaluation_does_not_overwrite_main_pipeline_output(tmp_path):
    """progressive evaluation 不得覆盖主 pipeline 输出。"""
    script = load_progressive_script()

    input_csv = tmp_path / "frames.csv"
    write_csv(
        input_csv,
        [
            frame_row(),
            frame_row(
                image_id="I002_000001",
                inspection_id="I002",
                timestamp="2026-07-01 10:00:00",
                mileage_m="12010.0",
                mileage_text="K12+010.0",
                ring_id="1001",
            ),
        ],
    )

    # 预写一个假的"主 pipeline 输出"
    main_output = tmp_path / "disease_association_records.csv"
    main_output.write_text("FAKE_MAIN_OUTPUT", encoding="utf-8")

    output_dir = tmp_path / "progressive"
    no_id_csv = tmp_path / "disease_association_records_no_id.csv"
    with_id_csv = tmp_path / "disease_association_records_with_id.csv"
    report_path = tmp_path / "association_evaluation_report.md"

    script.run_progressive(input_csv, output_dir, no_id_csv, with_id_csv, report_path)

    # 主 pipeline 输出未被覆盖
    assert main_output.read_text(encoding="utf-8") == "FAKE_MAIN_OUTPUT"
