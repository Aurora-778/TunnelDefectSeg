"""Project-level runner for the robot tunnel inspection application."""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path
import shutil
from typing import Any

from orchestrator.agents.association_agent import AssociationAgent
from orchestrator.agents.memory_agent import MemoryAgent
from scripts import analyze_disease_growth
from scripts import generate_engineering_report
from scripts import generate_visualization_and_recheck_list


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data" / "simulated"
OUTPUT_DIR = ROOT / "outputs"
VIS_DIR = OUTPUT_DIR / "visualizations"

FRAME_RECORDS = DATA_DIR / "robot_kict_frame_records.csv"
ENGINEERING_REPORT = DATA_DIR / "disease_engineering_report.csv"
GROWTH_RESULTS = DATA_DIR / "disease_growth_results.csv"
LEGACY_GROWTH_ANALYSIS = DATA_DIR / "disease_growth_analysis.csv"
MEMORY_BANK = DATA_DIR / "disease_memory_bank.csv"
ASSOCIATION_RECORDS = DATA_DIR / "disease_association_records.csv"
LEGACY_ASSOCIATION_RECORDS = DATA_DIR / "association_records.csv"
RECHECK_LIST = DATA_DIR / "priority_recheck_list.csv"

FINAL_REPORT = OUTPUT_DIR / "final_project_report.md"
SYSTEM_SUMMARY = OUTPUT_DIR / "system_summary.md"
KEY_INSIGHTS = OUTPUT_DIR / "key_insights.md"
ASSOCIATION_GRAPH = VIS_DIR / "association_relationship_graph.png"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run project-level workflows.")
    parser.add_argument(
        "--mode",
        choices=["full_pipeline"],
        default="full_pipeline",
        help="Workflow mode. Goal 7 requires full_pipeline.",
    )
    return parser.parse_args()


def run_full_pipeline(project_root: Path = ROOT) -> dict[str, Any]:
    """Run the full pipeline through the Orchestrator DAG."""

    from orchestrator.dag.builder import build_dag
    from orchestrator.executor import DAGExecutor
    from orchestrator.registry import build_default_registry

    dag_path = project_root / "config" / "dag.yaml"
    if not dag_path.exists():
        dag_path = ROOT / "config" / "dag.yaml"
    tasks, config = build_dag(dag_path)
    shared = dict(config.get("shared", {}))
    shared.setdefault("project_root", str(project_root))
    shared.setdefault("dag_config", str(dag_path))
    context = {
        "inputs": config.get("inputs", {}),
        "outputs": {},
        "shared": shared,
        "task_status": {},
    }
    executor = DAGExecutor(build_default_registry(), project_root, dag_config=str(dag_path))
    result = executor.run(tasks, context)
    output = result.get("outputs", {}).get("full_pipeline", {})
    output["run_id"] = executor.run_id
    output["run_dir"] = str(executor.run_info.run_dir)
    output["task_status"] = result.get("task_status", {})
    return output


def run_full_pipeline_direct(project_root: Path = ROOT) -> dict[str, Any]:
    """Run the complete application workflow from data tables to final reports."""

    paths = _paths(project_root)
    _ensure_inputs(paths)
    context: dict[str, Any] = {
        "inputs": {},
        "outputs": {},
        "shared": {"project_root": str(project_root)},
    }

    engineering_rows = _run_engineering_report(paths)
    growth_rows = _run_growth_analysis(paths)
    memory_output = _run_memory_agent(context, paths)
    association_output = _run_association_agent(context, paths)
    chart_paths, recheck_rows = _run_visualization_and_recheck(paths)
    association_graph = _write_association_graph(paths["association_records"], paths["association_graph"])
    final_reports = _write_final_reports(paths, engineering_rows, growth_rows, recheck_rows, chart_paths + [association_graph])

    result = {
        "engineering_rows": len(engineering_rows),
        "growth_rows": len(growth_rows),
        "memory_rows": memory_output["memory_bank_rows"],
        "association_rows": association_output["association_rows"],
        "association_matched_rows": association_output["association_matched_rows"],
        "recheck_rows": len(recheck_rows),
        "chart_count": len(chart_paths) + 1,
        "outputs": {
            "disease_memory_bank": str(paths["memory_bank"]),
            "disease_association_records": str(paths["association_records"]),
            "disease_growth_results": str(paths["growth_results"]),
            "final_project_report": str(paths["final_report"]),
            "system_summary": str(paths["system_summary"]),
            "key_insights": str(paths["key_insights"]),
        },
    }
    _validate_full_pipeline(paths, result)
    return result


def _paths(project_root: Path) -> dict[str, Path]:
    data_dir = project_root / "data" / "simulated"
    output_dir = project_root / "outputs"
    vis_dir = output_dir / "visualizations"
    return {
        "project_root": project_root,
        "frame_records": data_dir / "robot_kict_frame_records.csv",
        "engineering_report": data_dir / "disease_engineering_report.csv",
        "engineering_markdown": output_dir / "disease_engineering_report.md",
        "engineering_summary": output_dir / "disease_engineering_report_summary.md",
        "growth_results": data_dir / "disease_growth_results.csv",
        "legacy_growth_analysis": data_dir / "disease_growth_analysis.csv",
        "growth_markdown": output_dir / "disease_growth_analysis_report.md",
        "growth_summary": output_dir / "disease_growth_analysis_summary.md",
        "memory_bank": data_dir / "disease_memory_bank.csv",
        "memory_report": output_dir / "memory_agent_report.md",
        "memory_summary": output_dir / "disease_memory_bank_summary.md",
        "memory_log": project_root / "logs" / "memory_agent.log",
        "association_records": data_dir / "disease_association_records.csv",
        "legacy_association_records": data_dir / "association_records.csv",
        "recheck_list": data_dir / "priority_recheck_list.csv",
        "visualization_dir": vis_dir,
        "visualization_report": output_dir / "visualization_report.md",
        "recheck_report": output_dir / "recheck_list_report.md",
        "visualization_summary": output_dir / "visualization_summary.md",
        "association_graph": vis_dir / "association_relationship_graph.png",
        "final_report": output_dir / "final_project_report.md",
        "system_summary": output_dir / "system_summary.md",
        "key_insights": output_dir / "key_insights.md",
    }


def _ensure_inputs(paths: dict[str, Path]) -> None:
    required = [paths["frame_records"]]
    missing = [path for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing required input files: {missing}")


def _run_engineering_report(paths: dict[str, Path]) -> list[dict[str, str]]:
    rows = generate_engineering_report.read_input_rows(paths["frame_records"])
    records = generate_engineering_report.aggregate_rows(rows)
    generate_engineering_report.write_csv_report(records, paths["engineering_report"])
    generate_engineering_report.write_markdown_report(records, paths["frame_records"], paths["engineering_markdown"])
    generate_engineering_report.write_summary_report(
        paths["frame_records"],
        paths["engineering_report"],
        paths["engineering_markdown"],
        paths["engineering_summary"],
        rows,
        records,
    )
    return records


def _run_growth_analysis(paths: dict[str, Path]) -> list[dict[str, str]]:
    input_rows = analyze_disease_growth.read_input_rows(paths["engineering_report"])
    records = analyze_disease_growth.aggregate_rows(input_rows)
    analyze_disease_growth.write_csv_report(records, paths["growth_results"])
    analyze_disease_growth.write_markdown_report(records, input_rows, paths["engineering_report"], paths["growth_markdown"])
    analyze_disease_growth.write_summary_report(
        paths["engineering_report"],
        paths["growth_results"],
        paths["growth_markdown"],
        paths["growth_summary"],
        input_rows,
        records,
    )
    # Existing Web dashboard reads the legacy name, so keep it synchronized.
    shutil.copyfile(paths["growth_results"], paths["legacy_growth_analysis"])
    return records


def _run_memory_agent(context: dict[str, Any], paths: dict[str, Path]) -> dict[str, Any]:
    context["inputs"]["memory"] = {
        "engineering_report": str(paths["engineering_report"]),
        "growth_analysis": str(paths["growth_results"]),
        "output_path": str(paths["memory_bank"]),
        "report_path": str(paths["memory_report"]),
        "summary_path": str(paths["memory_summary"]),
        "log_path": str(paths["memory_log"]),
    }
    return MemoryAgent().run(context)


def _run_association_agent(context: dict[str, Any], paths: dict[str, Path]) -> dict[str, Any]:
    context["inputs"]["association"] = {
        "frame_records": str(paths["frame_records"]),
        "output_path": str(paths["association_records"]),
    }
    result = AssociationAgent().run(context)
    shutil.copyfile(paths["association_records"], paths["legacy_association_records"])
    return result


def _run_visualization_and_recheck(paths: dict[str, Path]) -> tuple[list[Path], list[dict[str, str]]]:
    font_note = generate_visualization_and_recheck_list.configure_matplotlib_font()
    growth_df = generate_visualization_and_recheck_list.read_growth_csv(paths["growth_results"])
    engineering_df, engineering_note = generate_visualization_and_recheck_list.read_engineering_csv(paths["engineering_report"])
    generate_visualization_and_recheck_list.ensure_output_dir(paths["visualization_dir"])

    chart_paths = generate_visualization_and_recheck_list.generate_standard_visualizations(growth_df, paths["visualization_dir"])
    mileage_df = generate_visualization_and_recheck_list.build_mileage_risk_table(engineering_df, growth_df)
    chart_paths.append(
        generate_visualization_and_recheck_list.generate_mileage_visualization(mileage_df, paths["visualization_dir"])
    )
    recheck_df = generate_visualization_and_recheck_list.build_priority_recheck_list(growth_df)
    generate_visualization_and_recheck_list.write_recheck_csv(recheck_df, paths["recheck_list"])
    generate_visualization_and_recheck_list.write_visualization_report(
        growth_df,
        paths["engineering_report"],
        paths["growth_results"],
        chart_paths,
        font_note,
        engineering_note,
        paths["visualization_report"],
    )
    generate_visualization_and_recheck_list.write_recheck_report(recheck_df, paths["recheck_report"], paths["recheck_list"])
    generate_visualization_and_recheck_list.write_summary_report(
        paths["growth_results"],
        paths["engineering_report"],
        paths["recheck_list"],
        chart_paths,
        paths["visualization_report"],
        paths["recheck_report"],
        paths["visualization_summary"],
        growth_df,
        recheck_df,
    )
    generate_visualization_and_recheck_list.validate_generated_outputs(
        chart_paths,
        paths["recheck_list"],
        [paths["visualization_report"], paths["recheck_report"], paths["visualization_summary"]],
    )
    return chart_paths, _read_csv(paths["recheck_list"])


def _write_association_graph(association_csv: Path, output_path: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    rows = _read_csv(association_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    counts = Counter(row.get("disease_id", "") for row in rows if row.get("association_status") == "matched")
    labels = list(counts)[:12] or ["暂无关联"]
    values = [counts[label] for label in labels] or [0]

    # 简化成“病害对象 -> 关联帧数”的关系图，便于答辩时说明跨帧关联是否闭环。
    fig, ax = plt.subplots(figsize=(max(7, len(labels) * 0.8), 4.8), dpi=150)
    bars = ax.bar(labels, values, color="#14b8a6", edgecolor="#0f172a")
    ax.set_title("病害对象关联关系图")
    ax.set_xlabel("disease_id")
    ax.set_ylabel("matched frame records")
    ax.grid(axis="y", linestyle="--", alpha=0.25)
    ax.bar_label(bars, padding=3)
    fig.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)
    return output_path


def _write_final_reports(
    paths: dict[str, Path],
    engineering_rows: list[dict[str, str]],
    growth_rows: list[dict[str, str]],
    recheck_rows: list[dict[str, str]],
    chart_paths: list[Path],
) -> dict[str, Path]:
    report = _final_report_text(paths, engineering_rows, growth_rows, recheck_rows, chart_paths)
    summary = _system_summary_text(paths, engineering_rows, growth_rows, recheck_rows)
    insights = _key_insights_text(growth_rows, recheck_rows)
    paths["final_report"].parent.mkdir(parents=True, exist_ok=True)
    paths["final_report"].write_text(report, encoding="utf-8")
    paths["system_summary"].write_text(summary, encoding="utf-8")
    paths["key_insights"].write_text(insights, encoding="utf-8")
    return {
        "final_report": paths["final_report"],
        "system_summary": paths["system_summary"],
        "key_insights": paths["key_insights"],
    }


def _final_report_text(
    paths: dict[str, Path],
    engineering_rows: list[dict[str, str]],
    growth_rows: list[dict[str, str]],
    recheck_rows: list[dict[str, str]],
    chart_paths: list[Path],
) -> str:
    risk_counts = Counter(row.get("last_risk_level") or row.get("risk_level", "") for row in growth_rows)
    trend_counts = Counter(row.get("growth_trend", "") for row in growth_rows)
    matched_rows = _count_csv_rows(paths["association_records"])
    chart_lines = "\n".join(f"- `{_display_path(paths, path)}`" for path in chart_paths)
    return f"""# 隧道巡检病害监测系统完整报告

## 系统能力总结

本系统面向机器人隧道连续巡检场景，已形成从真实 KICT 裂缝 mask 几何特征、仿真巡检时间/里程/环号/方位元数据，到病害对象记忆、跨巡检关联、增长分析、风险排序、Web 展示和最终报告的端到端闭环。

## 病害分析结果

- 工程化病害记录数：{len(engineering_rows)}
- 长期病害对象数：{len(growth_rows)}
- 重点复检记录数：{len(recheck_rows)}

## 关联分析结果

- 关联记录数：{matched_rows}
- 关联依据：Association Agent 综合 `disease_id`、空间距离、面积相似度、巡检时间连续性和风险相似度进行匹配，并输出 association_score、confidence_level 与 match_type。
- 输出文件：`{_display_path(paths, paths['association_records'])}`

## 风险分布

{_counter_lines(risk_counts)}

## 增长趋势分布

{_counter_lines(trend_counts)}

## 可视化输出

{chart_lines}

## 创新点

- 将单图裂缝 mask 结果接入机器人巡检的时间、里程、环号和方位信息，形成面向工程定位的病害对象。
- 引入 Disease Memory Bank，把 disease_id 的跨巡检历史沉淀为可复用记忆。
- 通过带评分的跨巡检关联和增长分析，把“看见裂缝”升级为“跟踪同一病害的变化”。
- 输出重点复检清单，使系统结果能直接服务现场复核和运维决策。

## 局限性

- 当前关联评分仍主要依赖仿真元数据和 mask 几何特征，尚未接入真实机器人位姿、深度或视觉重识别。
- 当前增长趋势是基于面积和风险规则的工程判断，不等同于结构安全结论。
- KICT 数据主要提供静态裂缝 mask，真实跨时间病害演化仍需要长期巡检数据支撑。

## 未来扩展

- 接入真实机器人里程计、位姿和相机标定，提高空间定位精度。
- 在不改变主链路的前提下，后续可引入视觉相似度、人工确认机制或真实位姿约束增强跨巡检关联。
- 增加长期时间序列数据后，可扩展为更严格的病害增长预测。
"""


def _system_summary_text(
    paths: dict[str, Path],
    engineering_rows: list[dict[str, str]],
    growth_rows: list[dict[str, str]],
    recheck_rows: list[dict[str, str]],
) -> str:
    return f"""# 系统闭环摘要

## 执行链

Memory Agent → Disease Memory Bank → Association Agent → Cross-inspection Matching → Growth Analysis → Risk Scoring → Web Visualization → Final Report

## 核心输出

- `{_display_path(paths, paths['memory_bank'])}`
- `{_display_path(paths, paths['association_records'])}`
- `{_display_path(paths, paths['growth_results'])}`
- `{_display_path(paths, paths['final_report'])}`
- `{_display_path(paths, paths['system_summary'])}`
- `{_display_path(paths, paths['key_insights'])}`

## 数量统计

- 工程化病害记录：{len(engineering_rows)}
- 增长分析病害：{len(growth_rows)}
- 重点复检病害：{len(recheck_rows)}

## Web 展示

启动原有 Web 服务后，可通过 Dashboard 查看工程报告、增长分析、重点复检清单和可视化图表。
"""


def _key_insights_text(growth_rows: list[dict[str, str]], recheck_rows: list[dict[str, str]]) -> str:
    top_growth = sorted(growth_rows, key=lambda row: float(row.get("area_growth_rate") or 0), reverse=True)[:5]
    top_lines = "\n".join(
        f"- {row['disease_id']}：{row['growth_trend']}，增长率 {round(float(row['area_growth_rate']) * 100, 1)}%，风险 {row['last_risk_level']}"
        for row in top_growth
    )
    recheck_lines = "\n".join(
        f"- {row['disease_id']}：{row['attention_level']}，{row['recheck_reason']}" for row in recheck_rows[:5]
    ) or "- 当前没有重点复检记录。"
    return f"""# 关键洞察

## 增长最明显的病害

{top_lines}

## 优先复检建议

{recheck_lines}

## 一句话结论

当前系统已经能把机器人连续巡检数据整理成病害对象、增长趋势、风险等级和复检清单，适合用于课程展示、项目答辩和后续论文/专利方向论证。
"""


def _validate_full_pipeline(paths: dict[str, Path], result: dict[str, Any]) -> None:
    required_outputs = [
        paths["memory_bank"],
        paths["association_records"],
        paths["growth_results"],
        paths["final_report"],
        paths["system_summary"],
        paths["key_insights"],
        paths["association_graph"],
    ]
    missing = [path for path in required_outputs if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Full pipeline missing outputs: {missing}")
    if result["memory_rows"] <= 0 or result["association_rows"] <= 0 or result["growth_rows"] <= 0:
        raise ValueError("Full pipeline produced empty core outputs")
    if result["association_matched_rows"] <= 0:
        raise ValueError("Association pipeline has no matched records")
    if result["chart_count"] < 7:
        raise ValueError(f"Expected at least 7 visual artifacts, got {result['chart_count']}")


def _display_path(paths: dict[str, Path], path: Path) -> str:
    try:
        return path.relative_to(paths["project_root"]).as_posix()
    except ValueError:
        return path.as_posix()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _count_csv_rows(path: Path) -> int:
    return len(_read_csv(path))


def _counter_lines(counter: Counter) -> str:
    return "\n".join(f"- {key or '未知'}：{value}" for key, value in sorted(counter.items())) or "- 暂无数据"


def main() -> int:
    args = parse_args()
    if args.mode == "full_pipeline":
        result = run_full_pipeline(ROOT)
        print("机器人隧道巡检病害监测完整 pipeline 运行完成")
        print(f"engineering rows: {result['engineering_rows']}")
        print(f"memory rows: {result['memory_rows']}")
        print(f"association rows: {result['association_rows']}")
        print(f"growth rows: {result['growth_rows']}")
        print(f"recheck rows: {result['recheck_rows']}")
        print(f"visual artifacts: {result['chart_count']}")
        print(f"final report: {result['outputs']['final_project_report']}")
        return 0
    raise ValueError(f"Unsupported mode: {args.mode}")


if __name__ == "__main__":
    raise SystemExit(main())
