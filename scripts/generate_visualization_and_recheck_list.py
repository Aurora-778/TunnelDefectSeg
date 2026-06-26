from __future__ import annotations

import argparse
import math
from collections import Counter
from pathlib import Path

import matplotlib
import pandas as pd
from matplotlib import font_manager

matplotlib.use("Agg")
from matplotlib import pyplot as plt


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
]

ATTENTION_ORDER = ["重点关注", "持续观察", "常规记录", "待补充巡检"]
GROWTH_TREND_ORDER = ["明显增长", "轻微增长", "基本稳定", "面积减小", "数据不足"]
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
    "growth_trend_distribution.png": "增长趋势分布图",
    "risk_level_change_distribution.png": "风险等级变化分布图",
    "top10_area_growth_rate.png": "面积增长率 Top 10",
    "disease_type_distribution.png": "病害类型分布图",
    "mileage_risk_distribution.png": "里程段风险统计图",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate disease growth visualizations and priority recheck list.")
    parser.add_argument("--growth-csv", type=Path, default=DEFAULT_GROWTH_CSV, help="disease_growth_analysis.csv path.")
    parser.add_argument(
        "--engineering-csv",
        type=Path,
        default=DEFAULT_ENGINEERING_CSV,
        help="Optional disease_engineering_report.csv path.",
    )
    parser.add_argument("--recheck-csv", type=Path, default=DEFAULT_RECHECK_CSV, help="Priority recheck CSV path.")
    parser.add_argument("--visualization-dir", type=Path, default=DEFAULT_VIS_DIR, help="PNG output directory.")
    parser.add_argument("--visualization-report", type=Path, default=DEFAULT_VIS_REPORT, help="Visualization report path.")
    parser.add_argument("--recheck-report", type=Path, default=DEFAULT_RECHECK_REPORT, help="Recheck report path.")
    parser.add_argument("--summary-report", type=Path, default=DEFAULT_SUMMARY_REPORT, help="Summary report path.")
    return parser.parse_args()


def configure_matplotlib_font() -> str:
    candidates = ["SimHei", "Microsoft YaHei", "Microsoft JhengHei", "Noto Sans CJK SC", "Arial Unicode MS"]
    installed = {font.name for font in font_manager.fontManager.ttflist}
    for font_name in candidates:
        if font_name in installed:
            plt.rcParams["font.sans-serif"] = [font_name]
            plt.rcParams["axes.unicode_minus"] = False
            return f"已使用中文字体：{font_name}"
    plt.rcParams["axes.unicode_minus"] = False
    return "未检测到 SimHei 等中文字体，图表仍已生成，但部分中文可能显示为方框。"


def read_growth_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"disease_growth_analysis.csv not found: {path}")
    df = pd.read_csv(path, encoding="utf-8-sig")
    if df.empty:
        raise ValueError(f"disease_growth_analysis.csv is empty: {path}")
    missing = [column for column in REQUIRED_GROWTH_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"disease_growth_analysis.csv missing required columns: {', '.join(missing)}")

    # 核心数值列先统一转换，后续排序、筛选和画图都基于干净数值。
    for column in ["area_growth_rate", "risk_level_change", "last_area_px", "first_area_px", "area_growth_px"]:
        df[column] = pd.to_numeric(df[column], errors="raise")
    return df


def read_engineering_csv(path: Path) -> tuple[pd.DataFrame | None, str]:
    if not path.exists():
        return None, f"缺少工程报告输入：{path.as_posix()}，里程段风险统计可能受限。"
    df = pd.read_csv(path, encoding="utf-8-sig")
    if df.empty:
        return None, f"工程报告输入为空：{path.as_posix()}，里程段风险统计可能受限。"
    missing = [column for column in ENGINEERING_COLUMNS if column not in df.columns]
    if missing:
        return None, f"工程报告缺少建议字段：{', '.join(missing)}，里程段风险统计可能受限。"
    df["start_mileage_m"] = pd.to_numeric(df["start_mileage_m"], errors="coerce")
    df["end_mileage_m"] = pd.to_numeric(df["end_mileage_m"], errors="coerce")
    return df, ""


def disease_type_label(value: str) -> str:
    return DISEASE_TYPE_ZH.get(str(value), "未知类型病害")


def ensure_output_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        raise OSError(f"Visualization output directory was not created: {path}")


def save_bar_chart(labels: list[str], values: list[int | float], title: str, xlabel: str, ylabel: str, output: Path) -> Path:
    fig_width = max(7, len(labels) * 1.1)
    fig, ax = plt.subplots(figsize=(fig_width, 4.8), dpi=150)
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
    except Exception as exc:  # pragma: no cover - matplotlib backend details are environment-specific.
        raise RuntimeError(f"Failed to generate chart {output}: {exc}") from exc
    finally:
        plt.close(fig)
    return output


def ordered_counts(series: pd.Series, order: list[str]) -> tuple[list[str], list[int]]:
    counts = Counter(series.fillna("未知"))
    labels = [label for label in order if counts.get(label, 0) > 0]
    labels.extend(sorted(label for label in counts if label not in order))
    return labels, [counts[label] for label in labels]


def risk_change_label(value: int) -> str:
    if value == 0:
        return "0\n无变化"
    if value > 0:
        return f"{value}\n上升{value}级"
    return f"{value}\n下降{abs(value)}级"


def generate_standard_visualizations(growth_df: pd.DataFrame, output_dir: Path) -> list[Path]:
    paths: list[Path] = []

    labels, values = ordered_counts(growth_df["attention_level"], ATTENTION_ORDER)
    paths.append(
        save_bar_chart(labels, values, "关注等级分布", "关注等级", "病害数量", output_dir / CHART_FILES["attention"])
    )

    labels, values = ordered_counts(growth_df["growth_trend"], GROWTH_TREND_ORDER)
    paths.append(
        save_bar_chart(labels, values, "增长趋势分布", "增长趋势", "病害数量", output_dir / CHART_FILES["trend"])
    )

    risk_counts = Counter(growth_df["risk_level_change"].astype(int))
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

    top_growth = growth_df.sort_values("area_growth_rate", ascending=False).head(10)
    paths.append(
        save_bar_chart(
            top_growth["disease_id"].astype(str).tolist(),
            top_growth["area_growth_rate"].round(4).tolist(),
            "病害面积增长率 Top 10",
            "病害编号",
            "面积增长率",
            output_dir / CHART_FILES["top10_growth"],
        )
    )

    type_labels, type_values = ordered_counts(growth_df["disease_type"].map(disease_type_label), [])
    paths.append(
        save_bar_chart(type_labels, type_values, "病害类型分布", "病害类型", "病害数量", output_dir / CHART_FILES["type"])
    )
    return paths


def mileage_bucket_label(start_m: float) -> str:
    bucket_start = math.floor(start_m / 10) * 10
    bucket_end = bucket_start + 10
    return f"K{bucket_start // 1000}+{bucket_start % 1000:03d} - K{bucket_end // 1000}+{bucket_end % 1000:03d}"


def build_mileage_risk_table(engineering_df: pd.DataFrame | None, growth_df: pd.DataFrame) -> pd.DataFrame:
    if engineering_df is None:
        return pd.DataFrame(columns=["mileage_bucket", "disease_count", "priority_count"])

    usable = engineering_df.dropna(subset=["start_mileage_m"]).copy()
    if usable.empty:
        return pd.DataFrame(columns=["mileage_bucket", "disease_count", "priority_count"])

    attention = growth_df[["disease_id", "attention_level"]].drop_duplicates("disease_id")
    usable = usable.merge(attention, on="disease_id", how="left")
    usable["bucket_start_m"] = (usable["start_mileage_m"] // 10 * 10).astype(int)
    usable["mileage_bucket"] = usable["bucket_start_m"].apply(mileage_bucket_label)
    grouped = (
        usable.groupby(["bucket_start_m", "mileage_bucket"], sort=True)
        .agg(
            disease_count=("disease_id", "count"),
            priority_count=("attention_level", lambda values: int((values == "重点关注").sum())),
        )
        .reset_index()
        .sort_values("bucket_start_m")
    )
    return grouped[["mileage_bucket", "disease_count", "priority_count"]]


def generate_mileage_visualization(mileage_df: pd.DataFrame, output_dir: Path) -> Path:
    output = output_dir / CHART_FILES["mileage"]
    if mileage_df.empty:
        return save_bar_chart(["暂无数据"], [0], "里程段风险统计", "里程段", "病害数量", output)

    labels = mileage_df["mileage_bucket"].astype(str).tolist()
    fig_width = max(8, len(labels) * 1.25)
    fig, ax = plt.subplots(figsize=(fig_width, 5), dpi=150)
    x_positions = range(len(labels))
    width = 0.38
    bars_all = ax.bar(
        [x - width / 2 for x in x_positions],
        mileage_df["disease_count"].tolist(),
        width=width,
        label="病害总数",
        color="#2563eb",
    )
    bars_priority = ax.bar(
        [x + width / 2 for x in x_positions],
        mileage_df["priority_count"].tolist(),
        width=width,
        label="重点关注数",
        color="#f97316",
    )
    ax.set_title("里程段风险统计")
    ax.set_xlabel("里程段")
    ax.set_ylabel("病害数量")
    ax.set_xticks(list(x_positions))
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.grid(axis="y", linestyle="--", alpha=0.28)
    ax.legend()
    ax.bar_label(bars_all, padding=3)
    ax.bar_label(bars_priority, padding=3)
    fig.tight_layout()
    try:
        fig.savefig(output)
    except Exception as exc:  # pragma: no cover - matplotlib backend details are environment-specific.
        raise RuntimeError(f"Failed to generate chart {output}: {exc}") from exc
    finally:
        plt.close(fig)
    return output


def recheck_reason(row: pd.Series) -> str:
    reasons: list[str] = []
    if row["attention_level"] == "重点关注":
        reasons.append("该病害被判定为重点关注对象")
    if float(row["area_growth_rate"]) >= 0.5:
        reasons.append("面积增长率超过 50%，存在明显扩展趋势")
    if row["last_risk_level"] == "高":
        reasons.append("末次巡检风险等级为高")
    if int(row["risk_level_change"]) >= 1:
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


def build_priority_recheck_list(growth_df: pd.DataFrame) -> pd.DataFrame:
    primary_mask = (
        (growth_df["attention_level"] == "重点关注")
        | (growth_df["growth_trend"] == "明显增长")
        | (growth_df["last_risk_level"] == "高")
        | (growth_df["area_growth_rate"] >= 0.5)
    )
    selected = growth_df[primary_mask].copy()
    if selected.empty:
        fallback_mask = (
            (growth_df["attention_level"] == "持续观察")
            | (growth_df["growth_trend"] == "轻微增长")
            | (growth_df["last_risk_level"] == "中")
        )
        selected = growth_df[fallback_mask].copy()

    if selected.empty:
        return pd.DataFrame(columns=RECHECK_COLUMNS)

    selected["_attention_rank"] = selected["attention_level"].map(ATTENTION_RANK).fillna(len(ATTENTION_ORDER))
    selected = selected.sort_values(
        ["_attention_rank", "area_growth_rate", "risk_level_change", "last_area_px"],
        ascending=[True, False, False, False],
    ).reset_index(drop=True)
    selected["priority_rank"] = selected.index + 1
    selected["recheck_reason"] = selected.apply(recheck_reason, axis=1)
    selected["recheck_suggestion"] = selected["attention_level"].apply(recheck_suggestion)
    return selected[RECHECK_COLUMNS]


def write_recheck_csv(recheck_df: pd.DataFrame, output_csv: Path) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    recheck_df.to_csv(output_csv, index=False, encoding="utf-8-sig", columns=RECHECK_COLUMNS)
    if not output_csv.exists():
        raise OSError(f"priority_recheck_list.csv was not generated: {output_csv}")


def count_by_order(series: pd.Series, order: list[str]) -> dict[str, int]:
    counts = Counter(series.fillna("未知"))
    result = {label: counts.get(label, 0) for label in order}
    for label in sorted(label for label in counts if label not in result):
        result[label] = counts[label]
    return result


def write_visualization_report(
    growth_df: pd.DataFrame,
    engineering_path: Path,
    growth_path: Path,
    chart_paths: list[Path],
    font_note: str,
    engineering_note: str,
    report_path: Path,
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    attention_counts = count_by_order(growth_df["attention_level"], ATTENTION_ORDER)
    obvious_growth_count = int((growth_df["growth_trend"] == "明显增长").sum())
    high_risk_count = int((growth_df["last_risk_level"] == "高").sum())
    chart_lines = "\n".join(f"- {CHART_TITLES.get(path.name, path.stem)}：`{path.as_posix()}`" for path in chart_paths)
    note_lines = "\n".join(f"- {note}" for note in [font_note, engineering_note] if note)
    if not note_lines:
        note_lines = "- 无"

    content = f"""# 病害增长结果可视化报告

数据来源：
- `{growth_path.as_posix()}`
- `{engineering_path.as_posix()}`

## 1. 图表清单

{chart_lines}

## 2. 总体统计

- 病害总数：{len(growth_df)}
- 重点关注数量：{attention_counts.get('重点关注', 0)}
- 持续观察数量：{attention_counts.get('持续观察', 0)}
- 常规记录数量：{attention_counts.get('常规记录', 0)}
- 待补充巡检数量：{attention_counts.get('待补充巡检', 0)}
- 明显增长数量：{obvious_growth_count}
- 高风险病害数量：{high_risk_count}

## 3. 说明

{note_lines}

## 4. 简要结论

根据增长率、风险等级变化和关注等级，本阶段生成了可视化图表和复检清单，为后续重点复检和报告展示提供依据。
"""
    report_path.write_text(content, encoding="utf-8")


def percent_text(rate: float) -> str:
    return f"{round(rate * 100, 1)}%"


def write_recheck_report(recheck_df: pd.DataFrame, report_path: Path, recheck_csv: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 重点复检病害清单",
        "",
        f"数据来源：`{recheck_csv.as_posix()}`",
        "",
        "## 总体情况",
        "",
        f"- 进入复检清单的病害数量：{len(recheck_df)}",
        f"- 重点关注：{int((recheck_df.get('attention_level', pd.Series(dtype=str)) == '重点关注').sum())}",
        f"- 持续观察：{int((recheck_df.get('attention_level', pd.Series(dtype=str)) == '持续观察').sum())}",
        f"- 高风险病害：{int((recheck_df.get('last_risk_level', pd.Series(dtype=str)) == '高').sum())}",
        f"- 明显增长病害：{int((recheck_df.get('growth_trend', pd.Series(dtype=str)) == '明显增长').sum())}",
        "",
        "## 复检清单",
        "",
    ]
    if recheck_df.empty:
        lines.append("当前未筛选出需要重点复检的病害对象。")
    else:
        for _, row in recheck_df.sort_values("priority_rank").iterrows():
            type_name = disease_type_label(row["disease_type"])
            lines.extend(
                [
                    f"### {int(row['priority_rank'])}. {row['disease_id']} - {type_name}",
                    "",
                    f"- 关注等级：{row['attention_level']}",
                    f"- 增长趋势：{row['growth_trend']}",
                    f"- 面积变化：{row['first_area_px']} px² -> {row['last_area_px']} px²",
                    f"- 增长率：{percent_text(float(row['area_growth_rate']))}",
                    f"- 风险变化：{row['first_risk_level']} -> {row['last_risk_level']}",
                    f"- 位置：{row['last_mileage_range']}，{row['main_clock_direction']}方向",
                    f"- 复检原因：{row['recheck_reason']}",
                    f"- 复检建议：{row['recheck_suggestion']}",
                    "",
                    "分析描述：",
                    str(row["growth_description"]),
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
    growth_df: pd.DataFrame,
    recheck_df: pd.DataFrame,
) -> None:
    summary_report.parent.mkdir(parents=True, exist_ok=True)
    attention_lines = "\n".join(
        f"- {name}: {count}" for name, count in count_by_order(growth_df["attention_level"], ATTENTION_ORDER).items()
    )
    trend_lines = "\n".join(
        f"- {name}: {count}" for name, count in count_by_order(growth_df["growth_trend"], GROWTH_TREND_ORDER).items()
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
- 复检清单记录数: {len(recheck_df)}

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
    growth_df = read_growth_csv(args.growth_csv)
    engineering_df, engineering_note = read_engineering_csv(args.engineering_csv)
    ensure_output_dir(args.visualization_dir)

    chart_paths = generate_standard_visualizations(growth_df, args.visualization_dir)
    mileage_df = build_mileage_risk_table(engineering_df, growth_df)
    chart_paths.append(generate_mileage_visualization(mileage_df, args.visualization_dir))

    recheck_df = build_priority_recheck_list(growth_df)
    write_recheck_csv(recheck_df, args.recheck_csv)
    write_visualization_report(
        growth_df,
        args.engineering_csv,
        args.growth_csv,
        chart_paths,
        font_note,
        engineering_note,
        args.visualization_report,
    )
    write_recheck_report(recheck_df, args.recheck_report, args.recheck_csv)
    write_summary_report(
        args.growth_csv,
        args.engineering_csv,
        args.recheck_csv,
        chart_paths,
        args.visualization_report,
        args.recheck_report,
        args.summary_report,
        growth_df,
        recheck_df,
    )
    validate_generated_outputs(
        chart_paths,
        args.recheck_csv,
        [args.visualization_report, args.recheck_report, args.summary_report],
    )

    # 后续可基于本阶段结果继续做最终项目总报告、Word/PDF 导出、dashboard、
    # 真实模型预测接入、简单趋势预测模型，以及按隧道区间生成风险热力图。
    print("病害增长可视化与重点复检清单生成完成")
    print(f"growth analysis rows: {len(growth_df)}")
    print(f"engineering report rows: {0 if engineering_df is None else len(engineering_df)}")
    print(f"visualizations generated: {len(chart_paths)}")
    print(f"priority recheck rows: {len(recheck_df)}")
    print(f"output csv: {args.recheck_csv.as_posix()}")
    print(f"visualization report: {args.visualization_report.as_posix()}")
    print(f"recheck report: {args.recheck_report.as_posix()}")
    print(f"summary report: {args.summary_report.as_posix()}")


if __name__ == "__main__":
    main()
