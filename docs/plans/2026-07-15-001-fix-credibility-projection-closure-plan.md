---
title: TunnelDefect Patch 1.2：可信度展示投影收口计划
type: fix
status: active
date: 2026-07-15
origin: docs/brainstorms/2026-07-15-credibility-projection-closure-requirements.md
---

# TunnelDefect Patch 1.2：可信度展示投影收口计划

## Summary

本计划关闭不可比较数据在复检 CSV、Markdown、风险图和 Web 中的剩余方向性泄漏，并修复正式产物路径测试的跨工作目录与 URL 边界。原始 Growth CSV 继续保存审计数值，核心算法和路由保持不变。

## Problem Frame

当前实现已能在主数据上输出“不可比较”和“暂无可比数据”，但门控仍接受一个非 schema 状态，复检出口没有覆盖单巡检反例，Web 还会直接展示原始 `area_growth_rate` 和风险首末值。路径扫描也依赖调用目录，并可能把 URL 中的 `/tmp/` 误判成本机路径。这些问题属于展示可信度与验证可移植性，不需要重新设计算法。

## Requirements

**Comparability contract**

- R1. 只有 `comparability_status=verified_comparable` 可以触发方向性筛选、排序、风险变化图和增长展示。
- R2. 所有非 `verified_comparable` 复检记录必须输出 `growth_trend=不可比较`，包括 `insufficient_history` 和旧状态。
- R3. 当前合法 `verified_comparable` 行为和真实单巡检在非复检页面的“数据不足”语义保持兼容。

**Presentation boundary**

- R4. 原始 Growth CSV 的面积和风险审计字段不得修改。
- R5. 不可比较复检 Markdown 不得输出风险升降、增长率或方向性趋势；可展示当前风险和静态面积审计。
- R6. Web 摘要、机器人总览、增长分析和复检 payload 必须使用中性展示副本，且不得改变 API 路由或源 CSV。
- R7. 现有 KICT 正式产物重新生成后，CSV、报告、图表和 Web 口径保持一致。

**Portable verification**

- R8. 正式产物路径扫描必须绑定仓库根目录，从仓库外 cwd 运行仍可通过。
- R9. HTTP/HTTPS URL 中的路径片段不得触发本机绝对路径告警。
- R10. Session 级 Artifact isolation 的保护范围和结构/哈希检测保持不变。

## Key Technical Decisions

- KTD1. **使用严格相等门控。** 方向性业务判断直接检查 `comparability_status == "verified_comparable"`；不再把 legacy 字符串当成可比状态。正式 schema 已提供唯一 canonical 值，无需兼容层。
- KTD2. **区分源数据和展示副本。** Growth CSV 保持原样；复检构建和 Web loader 对 `dict` 副本做中性化，避免修改共享输入或引入新 schema。
- KTD3. **不建设通用投影框架。** 在现有复检脚本和 `web_app.py` 各保留一个小型纯函数，职责分别是复检出口和 Web payload；不新增 registry、基类或规则 DSL。
- KTD4. **复检合同优先于通用状态标签。** `insufficient_history` 可在 Growth/Final Report 中显示“数据不足”，但进入 `priority_recheck_list.csv` 时按合同输出“不可比较”。
- KTD5. **Web 路由保持稳定。** 只调整现有 loader 返回的字段值和前端标签门控，不新增、删除或重命名 API。
- KTD6. **路径检查锚定项目根目录。** `git ls-files` 显式使用仓库 cwd，返回路径用项目根解析；URL 先从本机路径匹配范围中排除。

## High-Level Technical Design

```text
Growth CSV（原始审计值，保持不变）
        |
        +--> 复检出口投影 --> priority_recheck_list.csv / recheck Markdown
        |                    非 verified => 不可比较 + 静态审计
        |
        +--> Web 展示投影 --> summary / robot dashboard / growth / recheck
                             非 verified => 中性标签，不显示方向性指标
        |
        +--> 图表门控 ------> 仅 verified 进入风险变化和方向统计
```

## Implementation Units

### U1. 固定 canonical 可比性与复检出口合同

- **Goal:** 关闭 legacy 状态和 `insufficient_history` 绕过复检/图表合同的路径。
- **Files:** `scripts/generate_visualization_and_recheck_list.py`, `tests/test_growth_non_comparable_reporting.py`, `tests/test_generate_visualization_and_recheck_list.py`
- **Approach:** 将方向性 helper 收紧为仅接受 `verified_comparable`；构建复检行时对所有其他状态强制写入“不可比较”和中性描述。保留原始面积、相对差及风险字段作为审计值，但任何筛选、排序、理由和图表都不能使用这些值形成方向判断。
- **Constraints:** 不修改 `orchestrator/schema.py` 的 enum，不修改 Growth 计算，不改变合法可比记录排序。
- **Risks:** 通用 `display_growth_status()` 仍需服务“数据不足”展示，不能为了复检合同破坏其他报告。
- **Test scenarios:**
  - `longitudinally_comparable + risk_level_change=2` 不进入风险变化图；
  - `insufficient_history + high risk` 进入复检后 `growth_trend=不可比较`；
  - 非可比记录不按 `area_growth_rate` 或 `risk_level_change` 排序；
  - `verified_comparable` 继续保留真实趋势和风险变化图；
  - 原始输入行在构建过程中不被原地修改。
- **Acceptance:** R1-R5 对应测试通过，现有可比记录行为无回归。

### U2. 收紧 Markdown 与 Web 展示投影

- **Goal:** 阻止保留的审计数值在报告或 Web 中重新被标记为 Growth、增长率或风险升降。
- **Files:** `scripts/generate_visualization_and_recheck_list.py`, `web_app.py`, `web_demo/index.html`, `tests/test_growth_non_comparable_reporting.py`, `tests/test_web_app.py`
- **Approach:** 非可比复检报告以“当前风险等级”和“静态面积审计”替代“风险变化”；在 `web_app.py` 中对 Growth/recheck 行复制后投影，供项目摘要、机器人总览、增长分析和复检接口复用。前端根据投影后的可比性显示静态审计标签，不直接读取方向性原始值。
- **Constraints:** 不改路由、CSV schema、API 顶层结构或原始文件；不触碰单图检测和视频页面。实施前必须保留 `web_app.py`、`web_demo/index.html` 当前未提交用户修改。
- **Risks:** 同一行被多个页面复用，遗漏任一 loader 会继续泄漏旧字段；测试必须逐入口覆盖。
- **Test scenarios:**
  - 非可比 `低 -> 高` 在 Markdown 中只显示当前风险“高”；
  - 项目摘要不把非可比旧 `明显增长` 计入增长数量；
  - robot dashboard 的趋势分布将旧方向标签映射为“不可比较”；
  - `/api/growth-analysis` 和 `/api/recheck-list` 不把非可比相对差标成 Growth；
  - verified 行继续展示趋势、增长率和风险变化；
  - Web 路由集合和响应顶层 key 保持不变。
- **Acceptance:** R5-R7 对应测试通过，异常旧产物不能在任一现有 Web 页面形成方向结论。

### U3. 修复正式产物路径扫描的可移植性

- **Goal:** 让绝对路径合同测试从任意 cwd 运行，并避免 URL 误报。
- **Files:** `tests/test_documentation_runtime_contract.py`
- **Approach:** 为 `git ls-files` 指定项目根目录，将返回项解析为项目根下路径；匹配本机绝对路径前排除 HTTP/HTTPS URL，保留 Windows 驱动器和 POSIX 用户/临时目录检测。
- **Constraints:** 继续只扫描 tracked 正式 md/json/csv，不把日志、缓存、视频 WIP 和本地环境说明纳入范围。
- **Risks:** 过宽 URL 排除可能隐藏 URL query 中真正嵌入的本机路径；测试应同时保留普通绝对路径命中案例。
- **Test scenarios:**
  - 从仓库外 cwd 调用正式产物扫描测试；
  - `http://127.0.0.1:8000/`、`https://example.test/tmp/report.csv`、`https://example.test/opt/a.json` 不误报；
  - `C:/Users/name/a.csv`、`D:\\data\\a.csv`、`/home/name/a.csv`、`/tmp/a.csv` 仍会被识别；
  - tracked 正式输出中的本机路径继续导致测试失败。
- **Acceptance:** R8-R10 对应测试通过，仓库外 cwd 反例关闭。

### U4. 重新生成产物并完成可信度回归

- **Goal:** 证明代码门控、正式产物和 Web 展示口径一致，同时没有污染其他工作区修改。
- **Files:** `data/simulated/priority_recheck_list.csv`, `outputs/recheck_list_report.md`, `outputs/visualizations/risk_level_change_distribution.png`，以及 full pipeline 实际重生成且内容确有变化的正式报告。
- **Approach:** 先运行专项测试，再运行 full pipeline 和 validator；对生成差异逐文件审计，仅保留 Patch 1.2 必要产物。最后运行全量 pytest，由 session 级 Artifact isolation 验证测试没有污染正式输出。
- **Constraints:** 不提交日志、state、视频目录、Algorithm Events 生成物或其他本地 WIP；不恢复或覆盖用户已有修改。
- **Risks:** full pipeline 会机械重写无关 CSV/报告；必须按语义差异筛选，不能整批暂存。
- **Test scenarios:**
  - 当前全部 KICT 非可比数据的风险变化图显示“暂无可比数据”；
  - priority recheck 和 Markdown 不包含方向性结论；
  - artifact validator 通过；
  - 全量 pytest 前后正式 Artifact manifest 无变化；
  - 核心文件 diff 为空。
- **Acceptance:** 所有验收命令通过，提交只包含 Patch 1.2 文件和必要正式产物。

## System-Wide Impact

- **Data:** Growth CSV 不变；priority recheck 的展示字段更保守。
- **Web:** URL 和页面结构不变，现有页面获得可信度门控后的展示值。
- **Reports:** 非可比记录只保留静态审计和当前风险，不再表达方向变化。
- **Tests:** 路径合同可从任意 cwd 执行；session Artifact isolation 保持现有覆盖。

## Risks & Dependencies

- 当前工作区的 `web_app.py`、`web_demo/index.html` 和相关测试存在用户 WIP；执行时必须在其基础上局部修改并分开暂存。
- 若某个下游消费者依赖 priority recheck 中旧方向标签，修复会暴露该隐式依赖；应更新消费者，不恢复不可信标签。
- `verified_comparable` 是唯一 canonical 状态；若未来需要迁移 legacy 数据，应另开数据迁移计划。

## Scope Boundaries

本计划不修改：

- AssociationAgent 评分、阈值、no-id 语义或 benchmark 策略；
- Memory Bank 核心更新规则；
- Growth 核心计算和原始审计字段；
- `config/dag.yaml`、`run.py` 或 Web 路由；
- 模型、训练、视频、上传、数据库、LLM 或 Agent 架构；
- 真实巡检数据 Adapter。

## Verification Plan

专项验证：

```text
python -m pytest tests/test_growth_non_comparable_reporting.py tests/test_generate_visualization_and_recheck_list.py tests/test_web_app.py tests/test_documentation_runtime_contract.py -q -p no:cacheprovider
```

管线与合同验证：

```text
python run.py --mode full_pipeline
python scripts/validate_artifacts.py --project-root .
```

完整回归：

```text
python -m pytest -q -p no:cacheprovider
```

边界审计：

```text
git diff -- orchestrator/agents/association_agent.py orchestrator/agents/memory_agent.py config/dag.yaml run.py
git status --short
```

## Completion Criteria

- 最新 review 的 3 个 P1 和 2 个 P2 均有失败优先的反例测试并关闭。
- 非 `verified_comparable` 数据无法在 CSV、Markdown、图表或 Web 中形成方向性结论。
- 原始 Growth CSV 审计值、核心算法、DAG、主流程和路由语义均未变化。
- 专项测试、full pipeline、artifact validator 和全量 pytest 通过。
- 提交不包含既有 Web WIP 之外的无关文件、日志、state 或本地生成目录。

## Sources / Research

- `docs/brainstorms/2026-07-15-credibility-projection-closure-requirements.md`
- `docs/plans/2026-07-10-001-fix-temporal-credibility-patch-0-plan.md`
- `orchestrator/schema.py:162-167`
- `scripts/generate_visualization_and_recheck_list.py:261-318`
- `scripts/generate_visualization_and_recheck_list.py:431-506`
- `web_app.py:599-724`
- `web_app.py:904-917`
- `web_demo/index.html:2664-2687`
- `web_demo/index.html:2810-2847`
- `tests/test_growth_non_comparable_reporting.py`
- `tests/test_documentation_runtime_contract.py`
