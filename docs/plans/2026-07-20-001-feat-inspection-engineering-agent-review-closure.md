# TunnelDefect 工程 Agent 化 CEPlan v6 Review 关闭表

> 对应计划：`docs/plans/2026-07-20-001-feat-inspection-engineering-agent-phase-a-plan.md`  
> 基线：Git `main@7203da7`  
> 状态：计划审查已收束，Phase 0 与 Phase A 尚未实施  
> 更新日期：2026-07-20

## 1. 使用说明

本表只记录 CEPlan 的设计问题是否已经在计划中得到明确回答，不代表对应代码已经实现或验收通过。

状态含义：

- `计划已关闭`：计划已经给出唯一边界、合同或验收方式。
- `事实已核验`：已对当前仓库基线做只读核验。
- `Phase 0 待实施`：需要先落地合同、配置和 fixture。
- `Phase A 待实施`：需要后续单独执行开发与验收。
- `阻断`：进入下一阶段前必须先解决。

## 2. 基线与范围

|检查项|状态|关闭证据|实现状态|
|---|---|---|---|
|仓库基线|事实已核验|当前 `main@7203da7`，与计划基线一致|无代码变更|
|工作树状态|事实已核验|存在与计划无关的 Web/视频 WIP，计划已改为要求隔离|不得触碰或提交 WIP|
|核心算法边界|计划已关闭|禁止修改 Association 阈值与评分、Memory 聚合、Growth 数值公式|Phase A 必须用 semantic snapshot 复核|
|主流程语义|计划已关闭|保持 history-only、no-id；with-id 仅为评估上界|尚未迁移 DAG|
|禁止扩展|计划已关闭|排除 LLM、多 Agent 对话、数据库、向量检索、Hungarian、SLAM 等|无新增依赖|

## 3. Claim 与证据合同

|检查项|状态|关闭证据|实现状态|
|---|---|---|---|
|Association 身份边界|计划已关闭|规则 Association 只能作为支持证据，不等于身份确认|Phase A 待实施|
|Claim Capability|计划已关闭|区分静态审计、描述性差异、方向性变化、物理量变化、多时点模式和预测|Phase 0 待实施|
|规则关联语言上限|计划已关闭|`association_supported` 最高只能 `allowed_with_limits`|Phase A 待实施|
|历史证据来源|计划已关闭|Previous Value 只能定位到对应 round 的 `memory_before_query.csv`|Phase A 待实施|
|伪 Observation 防线|计划已关闭|禁止构造不存在的 `previous_observation_id`|Phase A 待实施|
|Registration/Scale/Uncertainty|计划已关闭|均要求显式 provenance，缺失时使用保守默认值|Phase 0 待实施|
|ClaimDecision v4|计划已关闭|逐记录决策 schema、状态与发布语言已定义|Phase 0 待实施|

## 4. DAG 与报告发布

|检查项|状态|关闭证据|实现状态|
|---|---|---|---|
|唯一 Phase A DAG|计划已关闭|只在现有 DAG 中增加 comparison evidence、claim gate 与报告节点|Phase A 待实施|
|重复框架风险|计划已关闭|禁止新增第二套 Executor、Registry、RunManager 或 DAG Builder|Phase A 实现时复核|
|Memory 数据与报告分离|计划已关闭|Memory CSV 保留聚合语义，正式报告必须经过 Claim Gate|Phase A 待实施|
|Growth 数据与报告分离|计划已关闭|Growth 保留现有数值公式，正式报告发布权移到门控后|Phase A 待实施|
|Visualization/Recheck 门控|计划已关闭|必须实际读取并验证 ClaimDecision|Phase A 待实施|
|FinalReport 门控|计划已关闭|必须消费 ClaimDecision、Comparison Evidence 和受门控报告|Phase A 待实施|
|旧成功产物误用|计划已关闭|当前发布结果必须由 Publication Manifest 与 hash 唯一标识|Phase A 待实施|

## 5. 事务、状态与恢复

|检查项|状态|关闭证据|实现状态|
|---|---|---|---|
|Staging 与 manifest-last|计划已关闭|全部正式产物先进入 Run Staging，最后提交 Publication Manifest|Phase A 待实施|
|发布回滚|计划已关闭|逐文件发布失败必须回滚；回滚不完整时隔离 manifest 并写 recovery marker|Phase A 待实施|
|双入口合同|计划已关闭|Prepared Dataset 与 Legacy Simulated 共用锁、StateStore、DAGExecutor 和发布事务|Phase 0/Phase A 待实施|
|Active Run Lock|计划已关闭|保留 `run_NNN`，定义 allocation 过渡与崩溃恢复|Phase A 待实施|
|Canonical State|计划已关闭|区分 Context Checkpoint 与顶层状态迁移|Phase A 待实施|
|CAS/WAL|计划已关闭|定义状态锁、CAS、transition journal、截断与损坏规则|Phase A 待实施|
|COMPLETED 顺序|计划已关闭|Publication Commit 与 final summary 必须早于 COMPLETED|Phase A 待实施|

## 6. 安全与验收

|检查项|状态|关闭证据|实现状态|
|---|---|---|---|
|Path Safety|计划已关闭|所有输入、Staging、Run 与发布路径要求项目根约束和路径级检查|Phase A 待实施|
|Artifact Isolation|计划已关闭|要求 added/removed/modified 均为空，CLI 在临时项目副本执行|Phase A 待实施|
|Semantic Snapshot|计划已关闭|默认只 diff；写入必须显式给出 reviewed reason|Phase A 待实施|
|CLI 退出码|计划已关闭|成功、请求错误、readiness、锁、执行、验证、review 和状态冲突均有固定退出码|Phase A 待实施|
|Phase A 必测|计划已关闭|计划列出 32 项边界与故障测试|尚未创建测试|
|Phase A 验收|计划已关闭|给出 plan-only、prepared、legacy、validator、重点、快速和全量回归命令|尚未执行|

## 7. 当前阻断项

进入 Phase A 前，以下项目必须先完成：

- [ ] 创建 Phase 0 五份合同文档。
- [ ] 创建 `config/inspection_workflow.yaml`。
- [ ] 创建有效与无效 task fixture。
- [ ] 对 ClaimDecision、Comparison Evidence、Publication Manifest 和 StateStore API 做 schema review。
- [ ] 明确现有 Web/视频 WIP 的隔离方式，确保 Phase A 提交不包含这些文件。
- [ ] 对计划第 29 节的 20 项最终自检逐项给出证据。

## 8. 结论

CEPlan v6 已把工程 Agent 化限制为现有 DAG 上的确定性协调、证据门控、状态恢复和发布事务，没有把项目扩展为 LLM Agent 平台或第二套编排框架。

当前可以宣布的是“计划审查已收束”。当前不能宣布 Phase 0 或 Phase A 已完成，也不能使用计划第 30 节的完成式项目表述。
