from __future__ import annotations

import argparse
import csv
import math
import os
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt

try:  # Direct ``python scripts/...`` execution has scripts/ on sys.path.
    from scripts.plotting_fonts import configure_chinese_font
except ModuleNotFoundError:  # pragma: no cover - exercised by CLI subprocesses.
    from plotting_fonts import configure_chinese_font


DEFAULT_GROWTH_CSV = Path("data/simulated/disease_growth_analysis.csv")
DEFAULT_ENGINEERING_CSV = Path("data/simulated/disease_engineering_report.csv")
DEFAULT_RECHECK_CSV = Path("data/simulated/priority_recheck_list.csv")
DEFAULT_VIS_DIR = Path("outputs/visualizations")
DEFAULT_VIS_REPORT = Path("outputs/visualization_report.md")
DEFAULT_RECHECK_REPORT = Path("outputs/recheck_list_report.md")
DEFAULT_SUMMARY_REPORT = Path("outputs/visualization_summary.md")

REQUIRED_GROWTH_COLUMNS = [
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
    "comparability_status",
]

ENGINEERING_COLUMNS = [
    "inspection_id",
    "disease_id",
    "disease_type",
    "frame_count",
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

RECHECK_COLUMNS = [
    "priority_rank",
    "disease_id",
    "disease_type",
    "attention_level",
    "growth_trend",
    "first_inspection",
    "last_inspection",
    "first_area_px",
    "last_area_px",
    "area_growth_rate",
    "area_growth_px",
    "first_risk_level",
    "last_risk_level",
    "risk_level_change",
    "last_mileage_range",
    "main_clock_direction",
    "recheck_reason",
    "recheck_suggestion",
    "growth_description",
    "comparability_status",
]

ATTENTION_ORDER = ["重点关注", "持续观察", "常规记录", "待补充巡检"]
GROWTH_TREND_ORDER = ["明显增长", "轻微增长", "基本稳定", "面积减小", "不可比较", "数据不足"]
ATTENTION_RANK = {name: index for index, name in enumerate(ATTENTION_ORDER)}
DISEASE_TYPE_ZH = {
    "crack": "裂缝",
    "water_leakage": "渗水",
    "spalling": "剥落",
    "unknown": "未知病害",
}

CHART_FILES = {
    "attention": "attention_level_distribution.png",
    "trend": "growth_trend_distribution.png",
    "risk_change": "risk_level_change_distribution.png",
    "top10_growth": "top10_area_growth_rate.png",
    "type": "disease_type_distribution.png",
    "mileage": "mileage_risk_distribution.png",
}
CHART_TITLES = {
    "attention_level_distribution.png": "关注等级分布图",
    "growth_trend_distribution.png": "跨巡检可比性状态分布图",
    "risk_level_change_distribution.png": "风险等级变化分布图",
    "top10_area_growth_rate.png": "可比跨巡检面积审计 Top 10",
    "disease_type_distribution.png": "病害类型分布图",
    "mileage_risk_distribution.png": "里程段风险统计图",
}

_CHINESE_FONT_AVAILABLE = True
_FALLBACK_TEXT = {
    "关注等级分布": "Attention level distribution",
    "关注等级": "Attention level",
    "病害数量": "Defect count",
    "跨巡检可比性状态分布": "Cross-inspection comparability",
    "状态": "Status",
    "风险等级变化分布": "Risk level change distribution",
    "风险等级变化值": "Risk level change",
    "可比跨巡检面积审计 Top 10": "Comparable area audit Top 10",
    "病害类型分布": "Defect type distribution",
    "病害类型": "Defect type",
    "里程段风险统计": "Mileage-section risk summary",
    "里程段": "Mileage section",
    "风险记录数": "Risk records",
    "重点复检数": "Priority rechecks",
    "明显增长": "Marked increase",
    "轻微增长": "Slight increase",
    "基本稳定": "Stable",
    "面积减小": "Area decrease",
    "不可比较": "Not comparable",
    "数据不足": "Insufficient history",
    "重点关注": "Priority attention",
    "持续观察": "Monitor",
    "常规记录": "Routine record",
    "待补充巡检": "Needs follow-up",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate disease growth visualizations and priority recheck list.")
    parser.add_argument("--growth-csv", type=Path, default=DEFAULT_GROWTH_CSV, help="disease_growth_analysis.csv path.")
    parser.add_argument("--engineering-csv", type=Path, default=DEFAULT_ENGINEERING_CSV, help="Optional engineering CSV path.")
    parser.add_argument("--recheck-csv", type=Path, default=DEFAULT_RECHECK_CSV, help="Priority recheck CSV path.")
    parser.add_argument("--visualization-dir", type=Path, default=DEFAULT_VIS_DIR, help="PNG output directory.")
    parser.add_argument("--visualization-report", type=Path, default=DEFAULT_VIS_REPORT, help="Visualization report path.")
    parser.add_argument("--recheck-report", type=Path, default=DEFAULT_RECHECK_REPORT, help="Recheck report path.")
    parser.add_argument("--summary-report", type=Path, default=DEFAULT_SUMMARY_REPORT, help="Summary report path.")
    return parser.parse_args()


def configure_matplotlib_font() -> str:
    global _CHINESE_FONT_AVAILABLE
    _CHINESE_FONT_AVAILABLE, note = configure_chinese_font(plt)
    return note


def read_growth_csv(path: Path) -> list[dict[str, str]]:
    rows = read_csv(path, "disease_growth_analysis.csv")
    missing = [column for column in REQUIRED_GROWTH_COLUMNS if column not in rows[0]]
    if missing:
        raise ValueError(f"disease_growth_analysis.csv missing required columns: {', '.join(missing)}")
    for row in rows:
        # 这里提前验证数值列，后续排序、筛选和画图就不会遇到隐性脏数据。
        for column in ["area_growth_rate", "risk_level_change", "last_area_px", "first_area_px", "area_growth_px"]:
            parse_float(row[column], column)
    return rows


def read_engineering_csv(path: Path) -> tuple[list[dict[str, str]] | None, str]:
    if not path.exists():
        return None, f"缺少工程报告输入：{path.as_posix()}，里程段风险统计可能受限。"
    rows = read_csv(path, "disease_engineering_report.csv")
    missing = [column for column in ENGINEERING_COLUMNS if column not in rows[0]]
    if missing:
        return None, f"工程报告缺少建议字段：{', '.join(missing)}，里程段风险统计可能受限。"
    return rows, ""


def read_csv(path: Path, label: str) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"{label} is empty: {path}")
    return rows


def parse_float(value: object, label: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric, got {value!r}") from exc


def parse_int(value: object, label: str) -> int:
    return int(parse_float(value, label))


def disease_type_label(value: str) -> str:
    return DISEASE_TYPE_ZH.get(str(value), "未知类型病害")


def ensure_output_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        raise OSError(f"Visualization output directory was not created: {path}")


def save_bar_chart(labels: list[str], values: list[int | float], title: str, xlabel: str, ylabel: str, output: Path) -> Path:
    if not _CHINESE_FONT_AVAILABLE:
        labels = [_FALLBACK_TEXT.get(label, "Item") for label in labels]
        title = _FALLBACK_TEXT.get(title, "Tunnel defect summary")
        xlabel = _FALLBACK_TEXT.get(xlabel, "Category")
        ylabel = _FALLBACK_TEXT.get(ylabel, "Count")
    fig_width = max(7, len(labels) * 1.1)
    dpi = 90 if os.environ.get("FAST_TEST_MODE") == "1" else 150
    fig, ax = plt.subplots(figsize=(fig_width, 4.8), dpi=dpi)
    bars = ax.bar(labels, values, color="#0ea5a8", edgecolor="#083344", linewidth=0.8)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", linestyle="--", alpha=0.28)
    ax.bar_label(bars, padding=3)
    plt.xticks(rotation=20, ha="right")
    fig.tight_layout()
    try:
        fig.savefig(output)
    except Exception as exc:  # pragma: no cover - backend details are environment-specific.
        raise RuntimeError(f"Failed to generate chart {output}: {exc}") from exc
    finally:
        plt.close(fig)
    return output


def display_text(value: str) -> str:
    """Use ASCII labels only when no installed font can render Chinese safely."""

    return value if _CHINESE_FONT_AVAILABLE else _FALLBACK_TEXT.get(value, "Item")


def ordered_counts(values: list[str], order: list[str]) -> tuple[list[str], list[int]]:
    counts = Counter(value or "未知" for value in values)
    labels = [label for label in order if counts.get(label, 0) > 0]
    labels.extend(sorted(label for label in counts if label not in order))
    return labels, [counts[label] for label in labels]


def risk_change_label(value: int) -> str:
    if value == 0:
        return "0\n无变化"
    if value > 0:
        return f"{value}\n上升{value}级"
    return f"{value}\n下降{abs(value)}级"


def generate_standard_visualizations(growth_rows: list[dict[str, str]], output_dir: Path) -> list[Path]:
    paths: list[Path] = []

    labels, values = ordered_counts([row["attention_level"] for row in growth_rows], ATTENTION_ORDER)
    paths.append(save_bar_chart(labels, values, "关注等级分布", "关注等级", "病害数量", output_dir / CHART_FILES["attention"]))

    labels, values = ordered_counts([row["growth_trend"] for row in growth_rows], GROWTH_TREND_ORDER)
    paths.append(save_bar_chart(labels, values, "跨巡检可比性状态分布", "状态", "病害数量", output_dir / CHART_FILES["trend"]))

    risk_counts = Counter(parse_int(row["risk_level_change"], "risk_level_change") for row in growth_rows)
    risk_keys = sorted(risk_counts)
    paths.append(
        save_bar_chart(
            [risk_change_label(value) for value in risk_keys],
            [risk_counts[value] for value in risk_keys],
            "风险等级变化分布",
            "风险等级变化值",
            "病害数量",
            output_dir / CHART_FILES["risk_change"],
        )
    )

    comparable_rows = [row for row in growth_rows if row.get("comparability_status") == "verified_comparable"]
    top_growth = sorted(comparable_rows, key=lambda row: parse_float(row["area_growth_rate"], "area_growth_rate"), reverse=True)[:10]
    if not top_growth:
        paths.append(
            save_bar_chart(
                ["暂无可比数据"],
                [0],
                "可比跨巡检面积审计",
                "病害编号",
                "可比记录数",
                output_dir / CHART_FILES["top10_growth"],
            )
        )
    else:
        paths.append(
            save_bar_chart(
                [row["disease_id"] for row in top_growth],
                [round(parse_float(row["area_growth_rate"], "area_growth_rate"), 4) for row in top_growth],
                "可比跨巡检面积变化 Top 10",
                "病害编号",
                "面积变化率",
                output_dir / CHART_FILES["top10_growth"],
            )
        )

    type_labels, type_values = ordered_counts([disease_type_label(row["disease_type"]) for row in growth_rows], [])
    paths.append(save_bar_chart(type_labels, type_values, "病害类型分布", "病害类型", "病害数量", output_dir / CHART_FILES["type"]))
    return paths


def mileage_bucket_label(start_m: float) -> str:
    bucket_start = math.floor(start_m / 10) * 10
    bucket_end = bucket_start + 10
    return f"K{bucket_start // 1000}+{bucket_start % 1000:03d} - K{bucket_end // 1000}+{bucket_end % 1000:03d}"


def build_mileage_risk_table(engineering_rows: list[dict[str, str]] | None, growth_rows: list[dict[str, str]]) -> list[dict[str, int | str]]:
    if not engineering_rows:
        return []

    attention_by_id = {row["disease_id"]: row["attention_level"] for row in growth_rows}
    buckets: dict[int, dict[str, int | str]] = {}
    for row in engineering_rows:
        try:
            start_m = parse_float(row["start_mileage_m"], "start_mileage_m")
        except ValueError:
            continue
        bucket_start = math.floor(start_m / 10) * 10
        bucket = buckets.setdefault(
            bucket_start,
            {"bucket_start_m": bucket_start, "mileage_bucket": mileage_bucket_label(start_m), "disease_count": 0, "priority_count": 0},
        )
        bucket["disease_count"] = int(bucket["disease_count"]) + 1
        if attention_by_id.get(row["disease_id"]) == "重点关注":
            bucket["priority_count"] = int(bucket["priority_count"]) + 1
    return [buckets[key] for key in sorted(buckets)]


def generate_mileage_visualization(mileage_rows: list[dict[str, int | str]], output_dir: Path) -> Path:
    output = output_dir / CHART_FILES["mileage"]
    if not mileage_rows:
        return save_bar_chart(["暂无数据"], [0], "里程段风险统计", "里程段", "病害数量", output)

    labels = [str(row["mileage_bucket"]) for row in mileage_rows]
    fig_width = max(8, len(labels) * 1.25)
    dpi = 90 if os.environ.get("FAST_TEST_MODE") == "1" else 150
    fig, ax = plt.subplots(figsize=(fig_width, 5), dpi=dpi)
    x_positions = range(len(labels))
    width = 0.38
    bars_all = ax.bar(
        [x - width / 2 for x in x_positions],
        [int(row["disease_count"]) for row in mileage_rows],
        width=width,
        label=display_text("病害总数"),
        color="#2563eb",
    )
    bars_priority = ax.bar(
        [x + width / 2 for x in x_positions],
        [int(row["priority_count"]) for row in mileage_rows],
        width=width,
        label=display_text("重点复检数"),
        color="#f97316",
    )
    ax.set_title(display_text("里程段风险统计"))
    ax.set_xlabel(display_text("里程段"))
    ax.set_ylabel(display_text("病害数量"))
    ax.set_xticks(list(x_positions))
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.grid(axis="y", linestyle="--", alpha=0.28)
    ax.legend()
    ax.bar_label(bars_all, padding=3)
    ax.bar_label(bars_priority, padding=3)
    fig.tight_layout()
    try:
        fig.savefig(output)
    except Exception as exc:  # pragma: no cover - backend details are environment-specific.
        raise RuntimeError(f"Failed to generate chart {output}: {exc}") from exc
    finally:
        plt.close(fig)
    return output


def recheck_reason(row: dict[str, str]) -> str:
    reasons: list[str] = []
    if row["attention_level"] == "重点关注":
        reasons.append("该病害被判定为重点关注对象")
    if row.get("comparability_status") == "verified_comparable" and parse_float(row["area_growth_rate"], "area_growth_rate") >= 0.5:
        reasons.append("面积增长率超过 50%，存在明显扩展趋势")
    if row["last_risk_level"] == "高":
        reasons.append("末次巡检风险等级为高")
    if parse_int(row["risk_level_change"], "risk_level_change") >= 1:
        reasons.append("风险等级较首次巡检出现上升")
    if not reasons:
        reasons.append("该病害满足持续观察条件")
    return "；".join(reasons) + "。"


def recheck_suggestion(attention_level: str) -> str:
    suggestions = {
        "重点关注": "建议优先安排人工复核，并在下一次巡检中重点跟踪。",
        "持续观察": "建议保持周期性复检，关注面积和风险等级变化。",
        "常规记录": "建议纳入常规巡检记录。",
        "待补充巡检": "建议补充后续巡检数据后再判断趋势。",
    }
    return suggestions.get(attention_level, "建议结合现场情况安排复检。")


def build_priority_recheck_list(growth_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    selected = [
        row
        for row in growth_rows
        if row["attention_level"] == "重点关注"
        or row["growth_trend"] == "明显增长"
        or row["last_risk_level"] == "高"
        or (
            row.get("comparability_status") == "verified_comparable"
            and parse_float(row["area_growth_rate"], "area_growth_rate") >= 0.5
        )
    ]
    if not selected:
        selected = [
            row
            for row in growth_rows
            if row["attention_level"] == "持续观察" or row["growth_trend"] == "轻微增长" or row["last_risk_level"] == "中"
        ]
    selected = sorted(
        selected,
        key=lambda row: (
            ATTENTION_RANK.get(row["attention_level"], len(ATTENTION_ORDER)),
            -parse_float(row["area_growth_rate"], "area_growth_rate"),
            -parse_int(row["risk_level_change"], "risk_level_change"),
            -parse_float(row["last_area_px"], "last_area_px"),
        ),
    )

    rows: list[dict[str, str]] = []
    for index, row in enumerate(selected, start=1):
        item = {key: row.get(key, "") for key in RECHECK_COLUMNS}
        item["priority_rank"] = str(index)
        item["recheck_reason"] = recheck_reason(row)
        item["recheck_suggestion"] = recheck_suggestion(row["attention_level"])
        rows.append(item)
    return rows


def write_recheck_csv(recheck_rows: list[dict[str, str]], output_csv: Path) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RECHECK_COLUMNS)
        writer.writeheader()
        writer.writerows([{name: row.get(name, "") for name in RECHECK_COLUMNS} for row in recheck_rows])
    if not output_csv.exists():
        raise OSError(f"priority_recheck_list.csv was not generated: {output_csv}")


def count_by_order(values: list[str], order: list[str]) -> dict[str, int]:
    counts = Counter(value or "未知" for value in values)
    result = {label: counts.get(label, 0) for label in order}
    for label in sorted(label for label in counts if label not in result):
        result[label] = counts[label]
    return result


def write_visualization_report(
    growth_rows: list[dict[str, str]],
    engineering_path: Path,
    growth_path: Path,
    chart_paths: list[Path],
    font_note: str,
    engineering_note: str,
    report_path: Path,
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    attention_counts = count_by_order([row["attention_level"] for row in growth_rows], ATTENTION_ORDER)
    comparable_count = sum(1 for row in growth_rows if row.get("comparability_status") == "verified_comparable")
    high_risk_count = sum(1 for row in growth_rows if row["last_risk_level"] == "高")
    chart_lines = "\n".join(f"- {CHART_TITLES.get(path.name, path.stem)}：`{path.as_posix()}`" for path in chart_paths)
    note_lines = "\n".join(f"- {note}" for note in [font_note, engineering_note] if note) or "- 无"
    content = f"""# 病害面积审计可视化报告

数据来源：
- `{growth_path.as_posix()}`
- `{engineering_path.as_posix()}`

## 1. 图表清单

{chart_lines}

## 2. 总体统计

- 病害总数：{len(growth_rows)}
- 重点关注数量：{attention_counts.get('重点关注', 0)}
- 持续观察数量：{attention_counts.get('持续观察', 0)}
- 常规记录数量：{attention_counts.get('常规记录', 0)}
- 待补充巡检数量：{attention_counts.get('待补充巡检', 0)}
- 可纵向比较记录数量：{comparable_count}
- 高风险病害数量：{high_risk_count}

## 3. 说明

{note_lines}

## 4. 简要结论

根据静态面积审计、风险等级变化、可比性状态和关注等级，本阶段生成了可视化图表和复检清单，为后续重点复检和报告展示提供依据。
"""
    report_path.write_text(content, encoding="utf-8")


def percent_text(rate: float) -> str:
    return f"{round(rate * 100, 1)}%"


def write_recheck_report(recheck_rows: list[dict[str, str]], report_path: Path, recheck_csv: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 重点复检病害清单",
        "",
        f"数据来源：`{recheck_csv.as_posix()}`",
        "",
        "## 总体情况",
        "",
        f"- 进入复检清单的病害数量：{len(recheck_rows)}",
        f"- 重点关注：{sum(1 for row in recheck_rows if row.get('attention_level') == '重点关注')}",
        f"- 持续观察：{sum(1 for row in recheck_rows if row.get('attention_level') == '持续观察')}",
        f"- 高风险病害：{sum(1 for row in recheck_rows if row.get('last_risk_level') == '高')}",
        f"- 可纵向比较病害：{sum(1 for row in recheck_rows if row.get('comparability_status') == 'verified_comparable')}",
        "",
        "## 复检清单",
        "",
    ]
    if not recheck_rows:
        lines.append("当前未筛选出需要重点复检的病害对象。")
    else:
        for row in sorted(recheck_rows, key=lambda item: parse_int(item["priority_rank"], "priority_rank")):
            type_name = disease_type_label(row["disease_type"])
            comparable = row.get("comparability_status") == "verified_comparable"
            evidence_lines = (
                [
                    f"- 增长趋势：{row['growth_trend']}",
                    f"- 面积变化：{row['first_area_px']} px² -> {row['last_area_px']} px²",
                    f"- 增长率：{percent_text(parse_float(row['area_growth_rate'], 'area_growth_rate'))}",
                ]
                if comparable
                else [
                    "- 可比性：不可纵向比较",
                    f"- 静态面积审计：{row['first_area_px']} px² -> {row['last_area_px']} px²",
                ]
            )
            lines.extend(
                [
                    f"### {int(row['priority_rank'])}. {row['disease_id']} - {type_name}",
                    "",
                    f"- 关注等级：{row['attention_level']}",
                    *evidence_lines,
                    f"- 风险变化：{row['first_risk_level']} -> {row['last_risk_level']}",
                    f"- 位置：{row['last_mileage_range']}，{row['main_clock_direction']}方向",
                    f"- 复检原因：{row['recheck_reason']}",
                    f"- 复检建议：{row['recheck_suggestion']}",
                    "",
                    "分析描述：",
                    row["growth_description"],
                    "",
                    "---",
                    "",
                ]
            )
    report_path.write_text("\n".join(lines), encoding="utf-8")


def write_summary_report(
    growth_path: Path,
    engineering_path: Path,
    recheck_csv: Path,
    chart_paths: list[Path],
    vis_report: Path,
    recheck_report: Path,
    summary_report: Path,
    growth_rows: list[dict[str, str]],
    recheck_rows: list[dict[str, str]],
) -> None:
    summary_report.parent.mkdir(parents=True, exist_ok=True)
    attention_lines = "\n".join(
        f"- {name}: {count}" for name, count in count_by_order([row["attention_level"] for row in growth_rows], ATTENTION_ORDER).items()
    )
    trend_lines = "\n".join(
        f"- {name}: {count}" for name, count in count_by_order([row["growth_trend"] for row in growth_rows], GROWTH_TREND_ORDER).items()
    )
    chart_lines = "\n".join(f"- {path.as_posix()}" for path in chart_paths)
    content = f"""# 可视化与重点复检清单摘要

## 输入文件

- {growth_path.as_posix()}
- {engineering_path.as_posix()}

## 输出文件

- {recheck_csv.as_posix()}
- {vis_report.as_posix()}
- {recheck_report.as_posix()}
- {summary_report.as_posix()}

## 图表文件

{chart_lines}

## 统计信息

- 图表数量: {len(chart_paths)}
- 复检清单记录数: {len(recheck_rows)}

## 关注等级分布

{attention_lines}

## 增长趋势分布

{trend_lines}

## 说明

本阶段基于 disease_growth_analysis.csv 和 disease_engineering_report.csv，生成了病害增长结果可视化图表、里程段风险统计和重点复检清单。该结果可用于项目展示、工程汇报和后续 dashboard 或 Word/PDF 报告导出。
"""
    summary_report.write_text(content, encoding="utf-8")


def validate_generated_outputs(chart_paths: list[Path], recheck_csv: Path, reports: list[Path]) -> None:
    if len(chart_paths) < 5:
        raise ValueError(f"Expected at least 5 visualizations, got {len(chart_paths)}")
    missing_charts = [path for path in chart_paths if not path.exists()]
    if missing_charts:
        raise FileNotFoundError(f"Missing visualization files: {missing_charts}")
    if not recheck_csv.exists():
        raise FileNotFoundError(f"Missing priority recheck CSV: {recheck_csv}")
    missing_reports = [path for path in reports if not path.exists()]
    if missing_reports:
        raise FileNotFoundError(f"Missing Markdown reports: {missing_reports}")


def main() -> None:
    args = parse_args()
    font_note = configure_matplotlib_font()
    growth_rows = read_growth_csv(args.growth_csv)
    engineering_rows, engineering_note = read_engineering_csv(args.engineering_csv)
    ensure_output_dir(args.visualization_dir)

    chart_paths = generate_standard_visualizations(growth_rows, args.visualization_dir)
    mileage_rows = build_mileage_risk_table(engineering_rows, growth_rows)
    chart_paths.append(generate_mileage_visualization(mileage_rows, args.visualization_dir))

    recheck_rows = build_priority_recheck_list(growth_rows)
    write_recheck_csv(recheck_rows, args.recheck_csv)
    write_visualization_report(
        growth_rows,
        args.engineering_csv,
        args.growth_csv,
        chart_paths,
        font_note,
        engineering_note,
        args.visualization_report,
    )
    write_recheck_report(recheck_rows, args.recheck_report, args.recheck_csv)
    write_summary_report(
        args.growth_csv,
        args.engineering_csv,
        args.recheck_csv,
        chart_paths,
        args.visualization_report,
        args.recheck_report,
        args.summary_report,
        growth_rows,
        recheck_rows,
    )
    validate_generated_outputs(chart_paths, args.recheck_csv, [args.visualization_report, args.recheck_report, args.summary_report])

    print("病害增长可视化与重点复检清单生成完成")
    print(f"growth analysis rows: {len(growth_rows)}")
    print(f"engineering report rows: {0 if engineering_rows is None else len(engineering_rows)}")
    print(f"visualizations generated: {len(chart_paths)}")
    print(f"priority recheck rows: {len(recheck_rows)}")
    print(f"output csv: {args.recheck_csv.as_posix()}")
    print(f"visualization report: {args.visualization_report.as_posix()}")
    print(f"recheck report: {args.recheck_report.as_posix()}")
    print(f"summary report: {args.summary_report.as_posix()}")


if __name__ == "__main__":
    main()
