---
date: 2026-07-15
topic: credibility-projection-closure
---

# TunnelDefect 下一阶段：可信度展示投影收口

## Summary

下一阶段优先完成 Patch 1.2：统一不可纵向比较数据在复检报告、统计图和 Web 中的展示边界，同时保留原始 Growth CSV 的面积审计值。完成该收口后，再进入真实巡检数据 Adapter。

## Problem Frame

Patch 0 和 Patch 1.1 已经解决 history-only Association、循环 KICT 样本不可纵向比较和多数报告夸大问题，但最新 review 仍发现展示出口不一致：旧可比性状态可能绕过门控，单巡检记录在复检 CSV 中不符合统一合同，原始面积差和风险差仍可能被 Web 标记成 Growth 或风险变化。此时直接接入真实数据，会把这些口径问题带入新链路并降低论文、专利和答辩材料的可信度。

## Candidate Directions

### Direction A：Patch 1.2 可信度展示投影收口

只修复可比性门控、复检输出、Web 展示投影和路径测试。改动范围小、验收明确，能够先建立“原始审计数据”和“可展示结论”之间的稳定边界。

- 优点：直接关闭现有 P1/P2；不需要新数据、模型或依赖；为真实数据接入提供可靠前置合同。
- 缺点：不会提高 Association 准确率，也不会增加新功能。
- 主要风险：Web 文件已有本地修改，实施时必须合并而不能覆盖。

### Direction B：真实巡检数据 Adapter

将真实图像、mask、时间、里程、环号、姿态和跨巡检 GT 转换为现有统一输入。该方向研究价值最高，但当前展示边界未闭合时启动，会扩大调试面并混淆数据问题与结论口径问题。

- 优点：能开始验证真实跨巡检 Association 和变化分析。
- 缺点：依赖真实元数据质量和跨巡检 GT；错误接入成本高。
- 适用时机：Patch 1.2 完成并通过全量可信度回归之后。

### Direction C：Association 实验增强

继续扩充 benchmark、阈值分析或匹配策略对照。当前 fixture 已表明 weighted no-id 未普遍优于 spatial-only，在缺少真实跨巡检 GT 时继续优化容易只适配仿真案例。

- 优点：可以补充论文实验表格和误匹配分析。
- 缺点：当前外部有效性弱，容易产生新的指标叙事而没有真实证据。
- 适用时机：取得真实跨巡检 GT 后，与真实数据 Adapter 联动开展。

## Key Decisions

- **先收口、后扩展。** Patch 1.2 是真实数据 Adapter 的前置条件，不与新功能混做。
- **原始证据与展示结论分离。** Growth CSV 保留首末面积、差值和风险字段供审计；报告与 Web 只有在 `comparability_status=verified_comparable` 时才展示方向性结论。
- **使用显式门控，不建设新框架。** 本轮不引入通用规则引擎、展示 DSL、Agent、数据库或插件系统。
- **真实数据是下一阶段主线。** Patch 1.2 完成后，优先规划真实巡检数据 Adapter，而不是继续增加 Web 页面。

## Requirements

**Canonical comparability**

- R1. 只有 `comparability_status=verified_comparable` 可以进入方向性筛选、排序、统计图和增长展示。
- R2. `longitudinally_comparable`、`simulated_metadata_comparable`、`insufficient_history`、空值和未知值都不得绕过方向性门控。
- R3. `priority_recheck_list.csv` 中所有非 `verified_comparable` 记录的 `growth_trend` 必须为“不可比较”；其他报告可继续将真实单巡检状态解释为“数据不足”。

**Neutral presentation projection**

- R4. 原始 Growth CSV 必须保留首末面积、面积差、相对差和风险审计值，不因展示需求被覆盖或清空。
- R5. 不可比较记录的复检报告只展示当前风险和静态面积审计，不展示风险上升、下降、增长率、增长、减小或稳定结论。
- R6. Web 项目摘要、机器人总览、增长分析和复检页面必须按可比性生成展示副本，不得直接把原始审计字段标记为 Growth 或风险变化。
- R7. Web 路由和 CSV schema 保持不变；归一化只发生在输出或展示投影层。

**Verification portability**

- R8. 正式产物绝对路径扫描必须从任意工作目录运行，并以仓库根目录解析 tracked 正式文件。
- R9. HTTP/HTTPS URL 即使包含 `/tmp/` 或 `/opt/` 也不能被误判成本机绝对路径。
- R10. Artifact isolation 现有保护范围、内容哈希以及新增/删除/修改/空目录检测不得降低。

## Acceptance Examples

- AE1. **Covers R1, R2.** 给旧记录设置 `comparability_status=longitudinally_comparable`、`risk_level_change=2`，风险变化图仍显示“暂无可比数据”。
- AE2. **Covers R3.** 单巡检高风险记录进入复检清单时，CSV 的 `growth_trend` 为“不可比较”，说明文字表达“数据不足/待真实复检”，但不形成方向判断。
- AE3. **Covers R4-R7.** 不可比较记录保留 `area_growth_rate=-0.4` 的原始审计值；Web 只显示静态面积审计，不显示 `Growth -0.4%`。
- AE4. **Covers R5.** 不可比较记录携带 `first_risk_level=低`、`last_risk_level=高` 时，复检报告显示“当前风险等级：高”，不显示“风险变化：低 -> 高”。
- AE5. **Covers R8, R9.** 从仓库外目录调用路径合同测试可以通过，包含 `https://example.test/tmp/report.csv` 的文档不会误报。

## Success Criteria

- 最新严格 review 中的 3 个 P1 和 2 个 P2 均有对应反例测试并关闭。
- 当前 KICT 循环静态样本在 CSV、Markdown、图表和 Web 中均不产生方向性结论。
- `python scripts/validate_artifacts.py --project-root .` 和全量 pytest 通过。
- AssociationAgent 评分、Memory 核心规则、DAG、`run.py`、Web 路由和原始 Growth CSV 语义保持不变。

## Scope Boundaries

本阶段不做：

- 真实巡检数据 Adapter；
- 新 Association 策略、阈值调参或 one-to-one assignment；
- 模型训练、推理或权重更新；
- 新 Web 页面、前后端拆分或上传任务；
- 数据库、LLM、Agent hierarchy 或规则 DSL；
- 对历史无效状态做自动迁移。

## Dependencies / Assumptions

- `verified_comparable` 是唯一允许方向性结论的 canonical 状态，以 `orchestrator/schema.py` 为准。
- 当前 Web 工作区存在未提交修改；实施必须基于现状做局部合并，不能覆盖用户改动。
- 真实数据 Adapter 将以本 requirements 完成作为启动条件。

## Sources / Research

- `docs/plans/2026-07-10-001-fix-temporal-credibility-patch-0-plan.md`
- `scripts/generate_visualization_and_recheck_list.py`
- `orchestrator/schema.py`
- `web_app.py`
- `web_demo/index.html`
- `tests/test_growth_non_comparable_reporting.py`
- `tests/test_documentation_runtime_contract.py`
