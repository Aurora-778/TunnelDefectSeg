"""Verify no-id / with-id evaluation outputs do not pollute each other or the main pipeline."""

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


def _run_eval(tmp_path):
    """Helper: run progressive eval and return output paths."""
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

    main_output = tmp_path / "disease_association_records.csv"
    main_output.write_text("FAKE_MAIN", encoding="utf-8")

    output_dir = tmp_path / "progressive"
    no_id_csv = tmp_path / "disease_association_records_no_id.csv"
    with_id_csv = tmp_path / "disease_association_records_with_id.csv"
    report_path = tmp_path / "association_evaluation_report.md"

    script.run_progressive(input_csv, output_dir, no_id_csv, with_id_csv, report_path)
    return main_output, no_id_csv, with_id_csv, report_path


def test_no_id_csv_generated(tmp_path):
    main_output, no_id_csv, with_id_csv, report_path = _run_eval(tmp_path)
    assert no_id_csv.exists()


def test_with_id_csv_generated(tmp_path):
    main_output, no_id_csv, with_id_csv, report_path = _run_eval(tmp_path)
    assert with_id_csv.exists()


def test_no_id_marked_as_primary(tmp_path):
    main_output, no_id_csv, with_id_csv, report_path = _run_eval(tmp_path)
    rows = read_csv(no_id_csv)
    for row in rows:
        assert row["use_disease_id_score"] == "false"
        assert row["association_mode"] == "no_id"


def test_with_id_marked_as_upper_bound(tmp_path):
    main_output, no_id_csv, with_id_csv, report_path = _run_eval(tmp_path)
    rows = read_csv(with_id_csv)
    for row in rows:
        assert row["use_disease_id_score"] == "true"
        assert row["association_mode"] == "with_id_upper_bound"


def test_main_pipeline_not_overwritten(tmp_path):
    main_output, no_id_csv, with_id_csv, report_path = _run_eval(tmp_path)
    assert main_output.read_text(encoding="utf-8") == "FAKE_MAIN"


def test_report_states_with_id_not_final(tmp_path):
    main_output, no_id_csv, with_id_csv, report_path = _run_eval(tmp_path)
    text = report_path.read_text(encoding="utf-8")
    assert "upper-bound" in text.lower() or "sanity check" in text.lower()
    # 报告必须说明 with-id 不是正式结论
    assert "primary" in text.lower()


def test_progressive_does_not_use_full_pipeline_memory(tmp_path):
    """progressive evaluation 不直接使用 full pipeline 的全量 memory_bank。"""
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

    # 构造一个假的全量 memory_bank，如果被使用会导致匹配结果不同
    fake_memory = tmp_path / "disease_memory_bank.csv"
    write_csv(
        fake_memory,
        [
            {
                "memory_id": "MEM-FAKE",
                "disease_id": "D_FAKE",
                "disease_type": "crack",
                "first_seen_inspection": "I001",
                "last_seen_inspection": "I002",
                "last_area_px": "99999",
                "max_area_px": "99999",
                "last_risk_level": "高",
                "main_clock_direction": "9点",
                "mileage_range": "K12+000.0 - K12+010.0",
            }
        ],
    )

    output_dir = tmp_path / "progressive"
    no_id_csv = tmp_path / "disease_association_records_no_id.csv"
    with_id_csv = tmp_path / "disease_association_records_with_id.csv"
    report_path = tmp_path / "association_evaluation_report.md"

    script.run_progressive(input_csv, output_dir, no_id_csv, with_id_csv, report_path)

    # 验证 no-id 输出中没有引用 MEM-FAKE
    no_id_rows = read_csv(no_id_csv)
    for row in no_id_rows:
        assert row.get("memory_id", "") != "MEM-FAKE", "progressive eval leaked full pipeline memory"

    # 报告必须说明没有使用全量 memory
    report_text = report_path.read_text(encoding="utf-8")
    assert "future memory" in report_text.lower() or "leakage" in report_text.lower()
