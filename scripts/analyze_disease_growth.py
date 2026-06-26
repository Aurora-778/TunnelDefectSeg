from __future__ import annotations

import argparse
import csv
import math
from collections import Counter, defaultdict
from pathlib import Path


DEFAULT_INPUT = Path("data/simulated/disease_engineering_report.csv")
DEFAULT_OUTPUT_CSV = Path("data/simulated/disease_growth_analysis.csv")
DEFAULT_MARKDOWN_REPORT = Path("outputs/disease_growth_analysis_report.md")
DEFAULT_SUMMARY_REPORT = Path("outputs/disease_growth_analysis_summary.md")

REQUIRED_COLUMNS = [
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
    "risk_level",
    "engineering_description",
]

OUTPUT_COLUMNS = [
    "disease_id",
    "disease_type",
    "inspection_count",
    "first_inspection",
    "last_inspection",
    "first_area_px",
    "last_area_px",
    "area_growth_px",
    "area_growth_rate",
    "first_mean_area_px",
    "last_mean_area_px",
    "mean_area_growth_rate",
    "first_frame_count",
    "last_frame_count",
    "frame_count_change",
    "first_risk_level",
    "last_risk_level",
    "risk_level_change",
    "growth_trend",
    "attention_level",
    "first_mileage_range",
    "last_mileage_range",
    "main_clock_direction",
    "growth_description",
]

DISEASE_TYPE_ZH = {
    "crack": "裂缝",
    "water_leakage": "渗水",
    "spalling": "剥落",
    "unknown": "未知病害",
}
RISK_SCORE = {"低": 1, "中": 2, "高": 3}
ATTENTION_ORDER = ["重点关注", "持续观察", "常规记录", "待补充巡检"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze disease growth across robot inspections.")
    parser.add_argument("--input-csv", type=Path, default=DEFAULT_INPUT, help="disease_engineering_report.csv path.")
    parser.add_argument("--output-csv", type=Path, default=DEFAULT_OUTPUT_CSV, help="Growth analysis CSV path.")
    parser.add_argument(
        "--markdown-report",
        type=Path,
        default=DEFAULT_MARKDOWN_REPORT,
        help="Detailed growth Markdown report path.",
    )
    parser.add_argument(
        "--summary-report",
        type=Path,
        default=DEFAULT_SUMMARY_REPORT,
        help="Summary Markdown report path.",
    )
    return parser.parse_args()


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

    # 提前验证核心数值字段，避免后续报告里出现隐性脏数据。
    for index, row in enumerate(rows, start=2):
        parse_int(row["max_area_px"], f"max_area_px at CSV line {index}")
        parse_float(row["mean_area_px"], f"mean_area_px at CSV line {index}")
        parse_int(row["frame_count"], f"frame_count at CSV line {index}")
    return rows


def inspection_sort_key(inspection_id: str) -> tuple[int, str]:
    digits = "".join(char for char in inspection_id if char.isdigit())
    return (int(digits) if digits else 0, inspection_id)


def disease_type_zh(disease_type: str) -> str:
    return DISEASE_TYPE_ZH.get(disease_type, "未知类型病害")


def mileage_range(row: dict[str, str]) -> str:
    if row["start_mileage_text"] == row["end_mileage_text"]:
        return row["start_mileage_text"]
    return f"{row['start_mileage_text']} 至 {row['end_mileage_text']}"


def safe_growth_rate(delta: float, base: float) -> float:
    if base == 0:
        return 0.0
    return round(delta / base, 4)


def risk_level_change(first_risk: str, last_risk: str) -> int:
    return RISK_SCORE.get(last_risk, 0) - RISK_SCORE.get(first_risk, 0)


def growth_trend(inspection_count: int, area_growth_rate: float, risk_change: int) -> str:
    if inspection_count == 1:
        return "数据不足"
    if area_growth_rate >= 0.5 or risk_change >= 1:
        return "明显增长"
    if 0.15 <= area_growth_rate < 0.5:
        return "轻微增长"
    if -0.15 < area_growth_rate < 0.15:
        return "基本稳定"
    if area_growth_rate <= -0.15:
        return "面积减小"
    return "基本稳定"


def attention_level(last_risk_level: str, trend: str) -> str:
    # 单次巡检无法比较增长趋势，优先归入待补充巡检。
    if trend == "数据不足":
        return "待补充巡检"
    if last_risk_level == "高" or trend == "明显增长":
        return "重点关注"
    if last_risk_level == "中" or trend == "轻微增长":
        return "持续观察"
    if last_risk_level == "低" and trend == "基本稳定":
        return "常规记录"
    return "持续观察"


def build_growth_description(record: dict[str, str]) -> str:
    type_name = disease_type_zh(record["disease_type"])
    if record["inspection_count"] == "1":
        return (
            f"病害 {record['disease_id']} 为{type_name}，目前仅在 {record['first_inspection']} 中出现，"
            f"位于 {record['first_mileage_range']}，{record['main_clock_direction']}方向。"
            "由于缺少跨巡检对比数据，暂无法判断增长趋势，建议后续巡检继续跟踪。"
        )

    area_growth_percent = round(float(record["area_growth_rate"]) * 100, 1)
    return (
        f"病害 {record['disease_id']} 为{type_name}，主要位于 {record['last_mileage_range']}，"
        f"{record['main_clock_direction']}方向。该病害共出现在 {record['inspection_count']} 次巡检中，"
        f"首次巡检 {record['first_inspection']} 最大面积约为 {record['first_area_px']} px²，"
        f"末次巡检 {record['last_inspection']} 最大面积约为 {record['last_area_px']} px²，"
        f"面积变化 {record['area_growth_px']} px²，增长率约为 {area_growth_percent}%。"
        f"风险等级由{record['first_risk_level']}变为{record['last_risk_level']}，"
        f"趋势判断为{record['growth_trend']}，关注等级为{record['attention_level']}。"
    )


def most_common_clock(rows: list[dict[str, str]]) -> str:
    return Counter(row["main_clock_direction"] for row in rows).most_common(1)[0][0]


def aggregate_disease(rows: list[dict[str, str]]) -> dict[str, str]:
    if not rows:
        raise ValueError("Each disease_id group must contain at least one row")
    ordered = sorted(rows, key=lambda row: inspection_sort_key(row["inspection_id"]))
    first = ordered[0]
    last = ordered[-1]

    first_area = parse_int(first["max_area_px"], "first max_area_px")
    last_area = parse_int(last["max_area_px"], "last max_area_px")
    area_growth = last_area - first_area
    area_rate = safe_growth_rate(area_growth, first_area)
    first_mean = parse_float(first["mean_area_px"], "first mean_area_px")
    last_mean = parse_float(last["mean_area_px"], "last mean_area_px")
    mean_rate = safe_growth_rate(last_mean - first_mean, first_mean)
    first_frames = parse_int(first["frame_count"], "first frame_count")
    last_frames = parse_int(last["frame_count"], "last frame_count")
    risk_change = risk_level_change(first["risk_level"], last["risk_level"])
    trend = growth_trend(len(ordered), area_rate, risk_change)
    attention = attention_level(last["risk_level"], trend)

    record = {
        "disease_id": first["disease_id"],
        "disease_type": first["disease_type"],
        "inspection_count": str(len(ordered)),
        "first_inspection": first["inspection_id"],
        "last_inspection": last["inspection_id"],
        "first_area_px": str(first_area),
        "last_area_px": str(last_area),
        "area_growth_px": str(area_growth),
        "area_growth_rate": str(area_rate),
        "first_mean_area_px": str(first_mean),
        "last_mean_area_px": str(last_mean),
        "mean_area_growth_rate": str(mean_rate),
        "first_frame_count": str(first_frames),
        "last_frame_count": str(last_frames),
        "frame_count_change": str(last_frames - first_frames),
        "first_risk_level": first["risk_level"],
        "last_risk_level": last["risk_level"],
        "risk_level_change": str(risk_change),
        "growth_trend": trend,
        "attention_level": attention,
        "first_mileage_range": mileage_range(first),
        "last_mileage_range": mileage_range(last),
        "main_clock_direction": most_common_clock(ordered),
    }
    record["growth_description"] = build_growth_description(record)
    return record


def aggregate_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[row["disease_id"]].append(row)

    records = [aggregate_disease(group_rows) for _, group_rows in sorted(groups.items())]
    if not records:
        raise ValueError("Output CSV would be empty")
    for record in records:
        if not record["growth_description"]:
            raise ValueError("growth_description should not be empty")
        if not math.isfinite(float(record["area_growth_rate"])) or not math.isfinite(
            float(record["mean_area_growth_rate"])
        ):
            raise ValueError("Growth rates should not be NaN or inf")
    return records


def write_csv_report(records: list[dict[str, str]], output_csv: Path) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(records)


def percent_text(rate: str) -> str:
    return f"{round(float(rate) * 100, 1)}%"


def write_markdown_report(
    records: list[dict[str, str]],
    input_rows: list[dict[str, str]],
    input_csv: Path,
    report_path: Path,
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    attention_counts = Counter(record["attention_level"] for record in records)
    inspection_ids = {row["inspection_id"] for row in input_rows}
    lines = [
        "# 机器人隧道巡检病害增长变化分析报告",
        "",
        f"数据来源：`{input_csv.as_posix()}`",
        "",
        "## 总体概况",
        "",
        f"- 分析病害数量：{len(records)}",
        f"- 涉及巡检次数：{len(inspection_ids)}",
        f"- 重点关注病害数量：{attention_counts.get('重点关注', 0)}",
        f"- 持续观察病害数量：{attention_counts.get('持续观察', 0)}",
        f"- 常规记录病害数量：{attention_counts.get('常规记录', 0)}",
        f"- 待补充巡检病害数量：{attention_counts.get('待补充巡检', 0)}",
        "",
    ]

    records_by_attention: dict[str, list[dict[str, str]]] = defaultdict(list)
    for record in records:
        records_by_attention[record["attention_level"]].append(record)

    for level in ATTENTION_ORDER:
        lines.extend([f"## {level}病害", ""])
        for record in records_by_attention.get(level, []):
            type_name = disease_type_zh(record["disease_type"])
            lines.extend(
                [
                    f"### {record['disease_id']} - {type_name}",
                    "",
                    f"- 首次巡检：{record['first_inspection']}",
                    f"- 末次巡检：{record['last_inspection']}",
                    f"- 面积变化：{record['first_area_px']} px² -> {record['last_area_px']} px²",
                    f"- 增长率：{percent_text(record['area_growth_rate'])}",
                    f"- 风险变化：{record['first_risk_level']} -> {record['last_risk_level']}",
                    f"- 趋势判断：{record['growth_trend']}",
                    f"- 关注等级：{record['attention_level']}",
                    "",
                    "分析描述：",
                    record["growth_description"],
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
    inspection_ids = {row["inspection_id"] for row in input_rows}
    trend_counts = Counter(record["growth_trend"] for record in records)
    attention_counts = Counter(record["attention_level"] for record in records)
    risk_change_counts = Counter(record["risk_level_change"] for record in records)
    trend_lines = "\n".join(f"- {name}: {count}" for name, count in sorted(trend_counts.items()))
    attention_lines = "\n".join(f"- {name}: {count}" for name, count in sorted(attention_counts.items()))
    risk_change_lines = "\n".join(f"- {name}: {count}" for name, count in sorted(risk_change_counts.items()))

    content = f"""# 病害增长变化分析摘要

## 输入文件

- {input_csv.as_posix()}

## 输出文件

- {output_csv.as_posix()}
- {markdown_report.as_posix()}
- {summary_report.as_posix()}

## 统计信息

- 输入记录行数: {len(input_rows)}
- 输出病害数量: {len(records)}
- 巡检次数: {len(inspection_ids)}

## 增长趋势分布

{trend_lines}

## 关注等级分布

{attention_lines}

## 风险等级变化统计

{risk_change_lines}

## 说明

本阶段基于 disease_engineering_report.csv，对同一 disease_id 在多次巡检中的面积、可见帧数和风险等级进行跨巡检比较，生成病害增长变化分析结果。该结果可用于病害变形监测、风险预警和后续趋势预测模块。
"""
    summary_report.write_text(content, encoding="utf-8")


def main() -> None:
    args = parse_args()
    input_rows = read_input_rows(args.input_csv)
    records = aggregate_rows(input_rows)
    write_csv_report(records, args.output_csv)
    write_markdown_report(records, input_rows, args.input_csv, args.markdown_report)
    write_summary_report(args.input_csv, args.output_csv, args.markdown_report, args.summary_report, input_rows, records)

    # 后续可基于 disease_growth_analysis.csv 做趋势预测、增长曲线可视化、
    # 按里程段统计风险、生成重点复检清单、接入 dashboard、导出 Word/PDF 报告。
    print("跨巡检病害增长分析完成")
    print(f"input rows: {len(input_rows)}")
    print(f"disease count: {len(records)}")
    print(f"output csv: {args.output_csv.as_posix()}")
    print(f"markdown report: {args.markdown_report.as_posix()}")
    print(f"summary report: {args.summary_report.as_posix()}")


if __name__ == "__main__":
    main()
