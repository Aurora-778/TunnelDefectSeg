from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path


DEFAULT_INPUT = Path("data/simulated/robot_kict_frame_records.csv")
DEFAULT_OUTPUT_CSV = Path("data/simulated/disease_engineering_report.csv")
DEFAULT_MARKDOWN_REPORT = Path("outputs/disease_engineering_report.md")
DEFAULT_SUMMARY_REPORT = Path("outputs/disease_engineering_report_summary.md")

REQUIRED_COLUMNS = [
    "image_id",
    "inspection_id",
    "frame_id",
    "timestamp",
    "mileage_m",
    "mileage_text",
    "ring_id",
    "clock_direction",
    "disease_id",
    "disease_type",
    "kict_image_path",
    "kict_mask_path",
    "kict_area_px",
    "kict_bbox_x1",
    "kict_bbox_y1",
    "kict_bbox_x2",
    "kict_bbox_y2",
    "kict_center_x",
    "kict_center_y",
    "has_crack",
    "observation_source",
    "comparability_status",
]

OUTPUT_COLUMNS = [
    "inspection_id",
    "disease_id",
    "disease_type",
    "frame_count",
    "start_frame",
    "end_frame",
    "start_time",
    "end_time",
    "start_mileage_m",
    "end_mileage_m",
    "start_mileage_text",
    "end_mileage_text",
    "start_ring",
    "end_ring",
    "main_clock_direction",
    "max_area_px",
    "mean_area_px",
    "total_area_px",
    "max_bbox_x1",
    "max_bbox_y1",
    "max_bbox_x2",
    "max_bbox_y2",
    "representative_image_path",
    "representative_mask_path",
    "risk_level",
    "observation_source",
    "comparability_status",
    "engineering_description",
]

DISEASE_TYPE_ZH = {
    "crack": "裂缝",
    "water_leakage": "渗水",
    "spalling": "剥落",
    "unknown": "未知病害",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate engineering disease reports from robot/KICT frame records.")
    parser.add_argument("--input-csv", type=Path, default=DEFAULT_INPUT, help="robot_kict_frame_records.csv path.")
    parser.add_argument("--output-csv", type=Path, default=DEFAULT_OUTPUT_CSV, help="Object-level output CSV path.")
    parser.add_argument(
        "--markdown-report",
        type=Path,
        default=DEFAULT_MARKDOWN_REPORT,
        help="Detailed Markdown report path.",
    )
    parser.add_argument(
        "--summary-report",
        type=Path,
        default=DEFAULT_SUMMARY_REPORT,
        help="Summary Markdown report path.",
    )
    return parser.parse_args()


def read_input_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    if not rows:
        raise ValueError(f"Input table is empty: {path}")

    missing = [column for column in REQUIRED_COLUMNS if column not in rows[0]]
    if missing:
        raise ValueError(f"Input table missing required columns: {', '.join(missing)}")

    # 先做输入字段验证，后续聚合就可以直接使用已知结构。
    for index, row in enumerate(rows, start=2):
        parse_int(row["frame_id"], f"frame_id at CSV line {index}")
        parse_float(row["mileage_m"], f"mileage_m at CSV line {index}")
        parse_int(row["ring_id"], f"ring_id at CSV line {index}")
        parse_int(row["kict_area_px"], f"kict_area_px at CSV line {index}")
    return rows


def parse_int(value: str, label: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric, got {value!r}") from exc


def parse_float(value: str, label: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric, got {value!r}") from exc


def disease_type_zh(disease_type: str) -> str:
    return DISEASE_TYPE_ZH.get(disease_type, "未知类型病害")


def risk_by_area(max_area_px: int) -> str:
    if max_area_px < 1500:
        return "低"
    if max_area_px < 3000:
        return "中"
    return "高"


def range_text(start: str, end: str, same_template: str, range_template: str) -> str:
    if start == end:
        return same_template.format(start=start)
    return range_template.format(start=start, end=end)


def build_description(record: dict[str, str]) -> str:
    mileage_phrase = range_text(
        record["start_mileage_text"],
        record["end_mileage_text"],
        "在 {start}",
        "在 {start} 至 {end}",
    )
    ring_phrase = range_text(
        record["start_ring"],
        record["end_ring"],
        "第 {start} 环",
        "{start} 至 {end} 环",
    )
    visible_phrase = "单帧可见" if record["frame_count"] == "1" else f"连续 {record['frame_count']} 帧可见"
    type_name = disease_type_zh(record["disease_type"])
    return (
        f"巡检 {record['inspection_id']} 中，机器人于 {record['start_time']} 至 {record['end_time']} "
        f"{mileage_phrase}，{ring_phrase}，{record['main_clock_direction']}方向发现{type_name} "
        f"{record['disease_id']}。该病害{visible_phrase}，最大裂缝面积约为 {record['max_area_px']} px²，"
        f"平均面积约为 {record['mean_area_px']} px²，风险等级为{record['risk_level']}，建议进行人工复核。"
    )


def aggregate_group(rows: list[dict[str, str]]) -> dict[str, str]:
    if not rows:
        raise ValueError("Each inspection_id + disease_id group must contain at least one row")

    rows_by_frame = sorted(rows, key=lambda row: parse_int(row["frame_id"], "frame_id"))
    rows_by_time = sorted(rows, key=lambda row: row["timestamp"])
    rows_by_mileage = sorted(rows, key=lambda row: parse_float(row["mileage_m"], "mileage_m"))
    rows_by_area = sorted(rows, key=lambda row: parse_int(row["kict_area_px"], "kict_area_px"), reverse=True)
    representative = rows_by_area[0]
    areas = [parse_int(row["kict_area_px"], "kict_area_px") for row in rows]
    max_area = max(areas)
    mean_area = round(sum(areas) / len(areas), 2)
    total_area = sum(areas)
    clock_counts = Counter(row["clock_direction"] for row in rows)
    main_clock_direction = clock_counts.most_common(1)[0][0]
    # Direct helper callers may carry legacy rows; degrade safely instead of inventing comparability.
    sources = sorted({row.get("observation_source") or "legacy_unverified_source" for row in rows})
    statuses = {row.get("comparability_status") or "not_longitudinally_comparable" for row in rows}
    # Directional conclusions require explicit verified comparability for every frame.
    comparability_status = "verified_comparable" if statuses == {"verified_comparable"} else "not_longitudinally_comparable"

    record = {
        "inspection_id": rows[0]["inspection_id"],
        "disease_id": rows[0]["disease_id"],
        "disease_type": rows[0]["disease_type"],
        "frame_count": str(len(rows)),
        "start_frame": rows_by_frame[0]["frame_id"],
        "end_frame": rows_by_frame[-1]["frame_id"],
        "start_time": rows_by_time[0]["timestamp"],
        "end_time": rows_by_time[-1]["timestamp"],
        "start_mileage_m": rows_by_mileage[0]["mileage_m"],
        "end_mileage_m": rows_by_mileage[-1]["mileage_m"],
        "start_mileage_text": rows_by_mileage[0]["mileage_text"],
        "end_mileage_text": rows_by_mileage[-1]["mileage_text"],
        "start_ring": str(min(parse_int(row["ring_id"], "ring_id") for row in rows)),
        "end_ring": str(max(parse_int(row["ring_id"], "ring_id") for row in rows)),
        "main_clock_direction": main_clock_direction,
        "max_area_px": str(max_area),
        "mean_area_px": str(mean_area),
        "total_area_px": str(total_area),
        "max_bbox_x1": representative["kict_bbox_x1"],
        "max_bbox_y1": representative["kict_bbox_y1"],
        "max_bbox_x2": representative["kict_bbox_x2"],
        "max_bbox_y2": representative["kict_bbox_y2"],
        "representative_image_path": representative["kict_image_path"],
        "representative_mask_path": representative["kict_mask_path"],
        "risk_level": risk_by_area(max_area),
        "observation_source": sources[0] if len(sources) == 1 else "mixed_sources",
        "comparability_status": comparability_status,
    }
    record["engineering_description"] = build_description(record)
    return record


def aggregate_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[(row["inspection_id"], row["disease_id"])].append(row)

    records = [aggregate_group(group_rows) for _, group_rows in sorted(groups.items())]
    if not records:
        raise ValueError("Output CSV would be empty")
    if any(not record["engineering_description"] for record in records):
        raise ValueError("engineering_description should not be empty")
    return records


def write_csv_report(records: list[dict[str, str]], output_csv: Path) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(records)


def write_markdown_report(records: list[dict[str, str]], input_csv: Path, report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    records_by_inspection: dict[str, list[dict[str, str]]] = defaultdict(list)
    for record in records:
        records_by_inspection[record["inspection_id"]].append(record)

    lines = [
        "# 机器人隧道巡检工程化病害报告",
        "",
        f"本报告来自：`{input_csv.as_posix()}`",
        "",
    ]
    for inspection_id, inspection_records in sorted(records_by_inspection.items()):
        lines.extend([f"## 巡检 {inspection_id}", ""])
        for record in inspection_records:
            type_name = disease_type_zh(record["disease_type"])
            lines.extend(
                [
                    f"### {record['disease_id']} - {type_name}",
                    "",
                    f"- 里程范围：{record['start_mileage_text']} 至 {record['end_mileage_text']}",
                    f"- 环号范围：{record['start_ring']} 至 {record['end_ring']} 环",
                    f"- 方位：{record['main_clock_direction']}方向",
                    f"- 可见帧数：{record['frame_count']}",
                    f"- 最大面积：{record['max_area_px']} px²",
                    f"- 平均面积：{record['mean_area_px']} px²",
                    f"- 风险等级：{record['risk_level']}",
                    f"- 代表图像：{record['representative_image_path']}",
                    f"- 代表 mask：{record['representative_mask_path']}",
                    "",
                    "工程描述：",
                    record["engineering_description"],
                    "",
                    "---",
                    "",
                ]
            )
    report_path.write_text("\n".join(lines), encoding="utf-8")


def write_summary_report(
    input_csv: Path,
    output_csv: Path,
    markdown_report: Path,
    summary_report: Path,
    input_rows: list[dict[str, str]],
    records: list[dict[str, str]],
) -> None:
    summary_report.parent.mkdir(parents=True, exist_ok=True)
    inspection_counts = Counter(record["inspection_id"] for record in records)
    risk_counts = Counter(record["risk_level"] for record in records)
    type_counts = Counter(record["disease_type"] for record in records)
    inspection_lines = "\n".join(f"- {name}: {count}" for name, count in sorted(inspection_counts.items()))
    risk_lines = "\n".join(f"- {name}: {count}" for name, count in sorted(risk_counts.items()))
    type_lines = "\n".join(f"- {disease_type_zh(name)}({name}): {count}" for name, count in sorted(type_counts.items()))

    content = f"""# 工程化病害报告摘要

## 输入文件

- {input_csv.as_posix()}

## 输出文件

- {output_csv.as_posix()}
- {markdown_report.as_posix()}
- {summary_report.as_posix()}

## 统计信息

- 总记录行数: {len(input_rows)}
- 聚合后的病害对象数量: {len(records)}
- 巡检次数: {len(inspection_counts)}

## 每次巡检的病害对象数量

{inspection_lines}

## 风险等级分布

{risk_lines}

## 病害类型分布

{type_lines}

## 说明

本阶段基于 robot_kict_frame_records.csv，将连续帧中的同一 disease_id 聚合为工程化病害对象，并生成了病害对象级 CSV 报告与 Markdown 报告。该结果可用于后续时空聚合验证、病害变化监测、风险趋势分析和工程化文本输出。
"""
    summary_report.write_text(content, encoding="utf-8")


def main() -> None:
    args = parse_args()
    input_rows = read_input_rows(args.input_csv)
    records = aggregate_rows(input_rows)
    write_csv_report(records, args.output_csv)
    write_markdown_report(records, args.input_csv, args.markdown_report)
    write_summary_report(args.input_csv, args.output_csv, args.markdown_report, args.summary_report, input_rows, records)

    # 后续可基于 disease_engineering_report.csv 继续做跨巡检变化分析、面积增长率、
    # 长宽/骨架变化监测、风险趋势判断、简单时间序列预测，以及 dashboard 或报告导出。
    print("工程化病害报告生成完成")
    print(f"input rows: {len(input_rows)}")
    print(f"aggregated disease records: {len(records)}")
    print(f"output csv: {args.output_csv}")
    print(f"markdown report: {args.markdown_report}")
    print(f"summary report: {args.summary_report}")


if __name__ == "__main__":
    main()
