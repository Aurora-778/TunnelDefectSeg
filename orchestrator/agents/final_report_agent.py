"""Agent for final project reports and output validation."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Any

from orchestrator.agents.base import BaseAgent


class FinalReportAgent(BaseAgent):
    """Write final Markdown reports after all pipeline artifacts exist."""

    name = "final_report"

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        inputs = self.agent_inputs(context)
        engineering_report = self.resolve_path(context, self._required_input(inputs, "engineering_report"))
        growth_results = self.resolve_path(context, self._required_input(inputs, "growth_results"))
        memory_bank = self.resolve_path(context, self._required_input(inputs, "memory_bank"))
        association_records = self.resolve_path(context, self._required_input(inputs, "association_records"))
        recheck_list = self.resolve_path(context, self._required_input(inputs, "recheck_list"))
        visualization_dir = self.resolve_path(context, self._required_input(inputs, "visualization_dir"))
        final_report = self.resolve_path(context, self._required_input(inputs, "final_report"))
        system_summary = self.resolve_path(context, self._required_input(inputs, "system_summary"))
        key_insights = self.resolve_path(context, self._required_input(inputs, "key_insights"))

        engineering_rows = self._read_csv(engineering_report)
        growth_rows = self._read_csv(growth_results)
        recheck_rows = self._read_csv(recheck_list)
        chart_paths = sorted(visualization_dir.glob("*.png"))

        final_report.parent.mkdir(parents=True, exist_ok=True)
        final_report.write_text(
            self._final_report_text(
                engineering_rows,
                growth_rows,
                recheck_rows,
                chart_paths,
                association_records,
            ),
            encoding="utf-8",
        )
        system_summary.write_text(
            self._system_summary_text(engineering_rows, growth_rows, recheck_rows, memory_bank, association_records, growth_results),
            encoding="utf-8",
        )
        key_insights.write_text(self._key_insights_text(growth_rows, recheck_rows), encoding="utf-8")
        self._validate_outputs(
            [memory_bank, association_records, growth_results, recheck_list, final_report, system_summary, key_insights],
            engineering_rows,
            growth_rows,
            recheck_rows,
            chart_paths,
            association_records,
        )

        return {
            "final_project_report_path": str(final_report),
            "system_summary_path": str(system_summary),
            "key_insights_path": str(key_insights),
            "final_report_ready": True,
        }

    def _final_report_text(
        self,
        engineering_rows: list[dict[str, str]],
        growth_rows: list[dict[str, str]],
        recheck_rows: list[dict[str, str]],
        chart_paths: list[Path],
        association_records: Path,
    ) -> str:
        risk_counts = Counter(row.get("last_risk_level") or row.get("risk_level", "") for row in growth_rows)
        trend_counts = Counter(row.get("growth_trend", "") for row in growth_rows)
        association_rows = self._read_csv(association_records)
        chart_lines = "\n".join(f"- `{path.as_posix()}`" for path in chart_paths)
        return f"""# 隧道巡检病害监测系统完整报告

## 系统能力总结

本系统面向机器人隧道连续巡检场景，形成从 KICT 裂缝 mask 几何特征、仿真巡检时间/里程/环号/方位元数据，到病害对象记忆、跨巡检关联、增长分析、风险排序、Web 展示和最终报告的端到端闭环。

## 病害分析结果

- 工程化病害记录数：{len(engineering_rows)}
- 长期病害对象数：{len(growth_rows)}
- 重点复检记录数：{len(recheck_rows)}

## 关联分析结果

- 关联记录数：{len(association_rows)}
- 关联依据：Association Agent 综合空间距离、面积相似度、巡检时间连续性、风险相似度和 disease_id 辅助信息进行评分，并输出 candidate、margin、conflict 和 manual review 标记。
- 输出文件：`{association_records.as_posix()}`

## 风险分布

{self._counter_lines(risk_counts)}

## 增长趋势分布

{self._counter_lines(trend_counts)}

## 可视化输出

{chart_lines}

## 创新点

- 面向机器人巡检的病害对象级建模。
- Disease Memory Bank 批处理记忆表。
- 基于空间、面积、时间和风险的规则关联评分。
- DAG 多阶段工程闭环。
- 可视化报告和重点复检清单。

## 数据边界

当前系统使用 KICT 静态裂缝 mask 与仿真机器人巡检元数据构建端到端流程。系统可以验证病害对象建模、跨巡检关联、增长分析和报告展示的工程闭环，但不能直接证明真实隧道病害长期演化规律。

## 局限性

- 当前关联评分仍主要依赖仿真元数据和 mask 几何特征，尚未接入真实机器人位姿、深度或视觉重识别。
- 当前增长趋势是基于面积和风险规则的工程判断，不等同于结构安全结论。
- KICT 数据主要提供静态裂缝 mask，真实跨时间病害演化仍需要长期巡检数据支撑。

## 未来扩展

- 接入真实机器人里程计、位姿和相机标定，提高空间定位精度。
- 引入视觉相似度、人工确认机制或真实位姿约束增强跨巡检关联。
- 增加长期时间序列数据后，再扩展为更严格的病害增长趋势分析。
"""

    def _system_summary_text(
        self,
        engineering_rows: list[dict[str, str]],
        growth_rows: list[dict[str, str]],
        recheck_rows: list[dict[str, str]],
        memory_bank: Path,
        association_records: Path,
        growth_results: Path,
    ) -> str:
        return f"""# 系统闭环摘要

## 执行链

Engineering Report -> Growth Analysis -> Disease Memory Bank -> Association Agent -> Visualization/Recheck -> Final Report

## 核心输出

- `{memory_bank.as_posix()}`
- `{association_records.as_posix()}`
- `{growth_results.as_posix()}`

## 数量统计

- 工程化病害记录：{len(engineering_rows)}
- 增长分析病害：{len(growth_rows)}
- 重点复检病害：{len(recheck_rows)}

## Web 展示

启动原有 Web 服务后，可通过 Dashboard 查看工程报告、增长分析、重点复检清单和可视化图表。
"""

    def _key_insights_text(self, growth_rows: list[dict[str, str]], recheck_rows: list[dict[str, str]]) -> str:
        top_growth = sorted(growth_rows, key=lambda row: self._to_float(row.get("area_growth_rate")), reverse=True)[:5]
        top_lines = "\n".join(
            f"- {row['disease_id']}：{row['growth_trend']}，增长率 {round(self._to_float(row.get('area_growth_rate')) * 100, 1)}%，风险 {row['last_risk_level']}"
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

    def _validate_outputs(
        self,
        required_outputs: list[Path],
        engineering_rows: list[dict[str, str]],
        growth_rows: list[dict[str, str]],
        recheck_rows: list[dict[str, str]],
        chart_paths: list[Path],
        association_records: Path,
    ) -> None:
        missing = [path for path in required_outputs if not path.exists()]
        if missing:
            raise FileNotFoundError(f"Full pipeline missing outputs: {missing}")
        if not engineering_rows or not growth_rows:
            raise ValueError("Full pipeline produced empty core outputs")
        if len(self._read_csv(association_records)) <= 0:
            raise ValueError("Association pipeline has no records")
        if len(chart_paths) < 7:
            raise ValueError(f"Expected at least 7 visual artifacts, got {len(chart_paths)}")
        if recheck_rows is None:
            raise ValueError("Priority recheck list was not loaded")

    def _read_csv(self, path: Path) -> list[dict[str, str]]:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def _counter_lines(self, counter: Counter) -> str:
        return "\n".join(f"- {key or '未知'}：{value}" for key, value in sorted(counter.items())) or "- 暂无数据"

    def _to_float(self, value: object) -> float:
        try:
            if value in (None, ""):
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _required_input(self, inputs: dict[str, Any], key: str) -> str:
        value = inputs.get(key)
        if not value:
            raise ValueError(f"FinalReportAgent missing required input: {key}")
        return str(value)
