# TunnelDefect 工程 Agent 化 CEPlan v6 Review 关闭表

> 对应计划：`docs/plans/2026-07-20-001-feat-inspection-engineering-agent-phase-a-plan.md`
> 基线：Git `main@7203da7`
> 状态：历次 Review 问题已写入计划；最新修订仅条件关闭，Phase 0 schema review 尚未完成
> 更新日期：2026-07-20

## 1. 使用说明

本表只记录 CEPlan 的设计问题如何处理，不代表代码已经实现或验收通过。

状态含义：

- `事实已核验`：已对 `main@7203da7` 做只读核验。
- `本轮已修订`：本轮 Review 指出的计划问题已写入 CEPlan。
- `条件关闭`：计划已有明确方案，但必须经过 Phase 0 schema review 和反例审查后才能进入实现。
- `Phase 0 待实施`：合同、配置或 fixture 尚未落盘。
- `Phase A1/A2/A3 待实施`：代码、测试和验收均未开始。
- `阻断`：解决前不得进入对应后续阶段。

## 2. 本轮 Review 问题

|问题|状态|修订结果|后续条件|
|---|---|---|---|
|可比性门控缺失|本轮已修订|Evidence 与 Claim Policy 增加 observation source 和三层 comparability；非 verified 只能 Static Audit|Phase 0 固定 schema 与反例|
|Physical/Multi-timepoint 能力超出证据|本轮已修订|Phase A 与 Prediction 一并固定 blocked|后续阶段另立计划|
|Evidence/Claim 权威路径断链|本轮已修订|唯一权威路径固定为当前 Run 的 `artifacts/`；Staging 只保存发布物和受控镜像|A1 验证原子生成与 Hash|
|Manifest 早于 final summary|本轮已修订|final summary 和全部必要文件先落盘，Manifest 最后提交|A2 做崩溃反例|
|Manifest 提交后恢复缺失|本轮已修订|增加 transaction phase 和幂等完成/隔离回滚规则|A2 验证每个崩溃点|
|Web 读取范围冲突|本轮已修订|Phase A consumers 限定为 workflow/CLI；现有 Web 保持 legacy reader，延后到 Phase E|Phase A 不得宣称 Web 已接入|
|面积口径不唯一|本轮已修订|固定为 `inspection_level_max_mask_area_px`，两侧均使用巡检级最大 mask 面积|A1 验证唯一 Engineering 映射|
|allocation token 二次写入窗口|本轮已修订|token 必须进入 Run metadata 首次写入|A3 验证初始写失败恢复|
|Phase A 过大|本轮已修订|拆为 A1 Claim/Evidence、A2 Publication、A3 State/Lock/WAL|逐段 review，禁止合并大提交|
|关闭表过早宣称完成|本轮已修订|关键合同统一改为条件关闭|Phase 0 review 后再更新|
|受限词扫描误伤免责声明|本轮已修订|主校验改为 template/decision 映射；词扫描仅为补充防线|Phase 0 固定模板合同|

## 2.1 第二轮严格 Review 的条件关闭项

下列条目进入本轮时均为“待修订”。当前只表示计划已经给出待实现合同，因此最多标记“条件关闭”，不表示代码已修复、测试已通过或 Phase A 可以开始：

|问题|Review 入场状态|当前状态|计划修订|实现条件|
|---|---|---|---|---|
|非法可比性被降级为 Static Audit|待修订|条件关闭|required/enum 校验前置；缺失、空值、未知枚举一律 Fail Closed；只有合法的明确非 verified 状态可 Static Audit|Phase 0 固定枚举与非法 fixture；A1 实现 Gate|
|Baseline/Unmatched Evidence 分支断链|待修订|条件关闭|以当前 Engineering/Frame 为主表左连接 Association；baseline、unmatched、matched、invalid 分支不再混用|Phase 0 固定 branch/schema；A1 覆盖 header-only baseline 与 orphan query|
|无历史 comparability/null 合同冲突|待修订|条件关闭|`insufficient_history` 优先；`not_applicable` 的 previous 字段固定 CSV/JSON canonical null、空列表和零值|Phase 0 固定 normalization；A1 补反例|
|A1 仍可能写正式产物|待修订|条件关闭|A1 新节点默认不注册、不调度；只在 tmp sandbox profile 直接测试组件；A3 才接入唯一 DAG/Registry|A1 补默认 CLI 未激活和 Artifact Isolation 测试|
|Publication Manifest 不完整|待修订|条件关闭|Manifest 穷举全部发布和来源文件；expected source 集合显式包含 final_summary；可视化/Association round 逐文件 Hash|A2 补新增、遗漏、重复和集合差异反例|
|Publication 新文件无法回滚|待修订|条件关闭|transaction target 记录 existed_before；回滚恢复旧文件并删除首次发布新增文件|A2 覆盖首次发布和混合新旧目标故障|
|跨目录发布 durability 不完整|待修订|条件关闭|对 outputs、visualizations、data/simulated、runs 和 Manifest 等每个受影响父目录分别 fsync|A2 补 parent fsync 调用与失败反例|
|提交后回滚遗留 final_summary|待修订|条件关闭|任何最终非 COMPLETED 的 Run 都使用 transaction-scoped 文件隔离 final_summary；已存在时 Fail Closed|A2 覆盖 Manifest 已提交后 state 冲突、重试和隔离失败|
|Run 目录无 token 崩溃窗口|待修订|条件关闭|初始锁包含 `reserved_run_id:null`；目录创建前原子持久化预留 ID；null 和非 null 分支分别恢复|A3 覆盖锁初写后、预留后和 metadata 首写前崩溃|
|Context Checkpoint 不在 WAL|待修订|条件关闭|checkpoint 与 status transition 使用同一 State Journal、统一 CAS/pending/committed/aborted 恢复|A3 固定 journal schema 并补幂等/冲突反例|
|State recover 没有强制调用点|待修订|条件关闭|Controller resume 和全部 mutation 在新写前恢复或拒绝 unresolved pending，并复用同一内部算法|A3 补 pending 后 resume/mutation 反例|
|不可比较限定语未强制|待修订|条件关闭|ClaimDecision 必须携带受控“仅静态审计、不构成方向性变化结论”模板；缺失不得发布|Phase 0 固定模板 ID；A1 补 renderer/validator 测试|
|生产 Evidence 依赖评估标签|待修订|条件关闭|以 query composite key/current_observation_id/来源指纹连接 Engineering；`label_disease_id` 只保留评估用途|Phase 0 固定中性 join schema；A1 补删除 label 后结果不变测试|
|A1/A2/A3 独立验收路径含糊|待修订|条件关闭|A1 只做禁用态组件与 sandbox；A2 只在 sandbox 验证发布；A3 才接管双入口和正式发布|每段单独提交、review；上一段 P0/P1 清零后继续|

## 3. 基线与范围

|检查项|状态|证据|实现状态|
|---|---|---|---|
|仓库基线|事实已核验|计划基于 `main@7203da7`|无代码变更|
|工作树状态|事实已核验|存在与计划无关的 Web/视频 WIP，必须隔离|不得触碰或提交 WIP|
|核心算法边界|条件关闭|禁止修改 Association 阈值/评分、Memory 聚合和 Growth 数值公式|A1/A2/A3 均需 semantic snapshot|
|主流程语义|条件关闭|保持 history-only、no-id；with-id 仅为评估上界|尚未迁移 DAG|
|禁止扩展|条件关闭|排除 LLM、多 Agent 对话、数据库、向量检索、Hungarian、SLAM 等|无新增依赖|

## 4. Claim 与证据合同

|检查项|状态|证据|实现状态|
|---|---|---|---|
|Association 身份边界|条件关闭|规则 Association 只能作为支持证据，不等于身份确认|A1 待实施|
|Comparability 硬门控|条件关闭|缺失/非法字段 Fail Closed；只有 schema 合法且明确非 verified 时只允许 Static Audit|Phase 0/A1 待实施|
|Phase A Capability|条件关闭|Difference/Directional 受严格门控；Physical/Pattern/Prediction 固定 blocked|Phase 0/A1 待实施|
|历史证据来源|条件关闭|Previous Value 只能来自对应 round 的 `memory_before_query.csv`；当前记录使用中性 observation join，不依赖评估标签|A1 待实施|
|面积测量口径|条件关闭|当前与历史统一为巡检级最大 mask 面积|A1 待实施|
|Registration/Scale/Uncertainty|条件关闭|均要求显式 provenance，缺失时采用保守默认值|Phase 0/A1 待实施|
|ClaimDecision|条件关闭|逐记录 schema、模板映射、来源 Hash、可比性限定语和失效规则已定义|仍需 Phase 0 schema review|

## 5. DAG、发布与状态

|检查项|状态|证据|实现状态|
|---|---|---|---|
|唯一 DAG/Executor|条件关闭|只扩展现有 DAG、DAGExecutor、Registry 和 RunManager|A1/A3 待实施|
|A1 Claim/Evidence|条件关闭|新节点默认不激活；sandbox 中逐节点重定向到 Run-local work/artifacts/staging，不得修改正式目录|A1 待实施|
|A2 Publication|条件关闭|Manifest-last、完整文件集合、existed_before 回滚、逐父目录 fsync 和 transaction-scoped final_summary 隔离已定义|A2 待实施|
|A3 State/Lock/WAL|条件关闭|双入口、nullable/预留 run_id、初始 allocation token、Canonical State、统一 checkpoint/transition WAL 和强制恢复入口已定义|A3 待实施|
|Memory/Growth 报告分离|条件关闭|结构化数据保留，正式报告必须经过 Claim Gate|A1 待实施|
|Visualization/FinalReport|条件关闭|必须读取当前 Run 的 ClaimDecision 和 Comparison Evidence|A1 待实施|
|Web 边界|条件关闭|Phase A 不修改 Web，也不声称 Web 是 Manifest 权威 reader|Phase E 待实施|

## 6. 安全与验收

|检查项|状态|证据|实现状态|
|---|---|---|---|
|Path Safety|条件关闭|Task dataset_id、项目根与路径级检查已定义|A3 待实施|
|Artifact Isolation|条件关闭|要求 added/removed/modified 为空，CLI 在临时项目副本执行|A1/A2/A3 待实施|
|Semantic Snapshot|条件关闭|默认只 diff，更新必须提供 reviewed reason|A1/A2/A3 待实施|
|CLI 退出码|条件关闭|成功、readiness、锁、执行、验证和状态冲突均有固定码|A3 待实施|
|阶段验收|条件关闭|A1/A2/A3 必须分别 review；上一阶段有 P0/P1 时不得继续|尚未执行|

## 7. 当前阻断项

- [ ] 创建 Phase 0 五份合同文档。
- [ ] 创建 `config/inspection_workflow.yaml`。
- [ ] 创建有效与无效 task fixture。
- [ ] 对 ClaimDecision、Comparison Evidence、Publication Manifest 和 StateStore API 做 schema review。
- [ ] 对 baseline/current-only、不可比较输入、物理量、多时点、Manifest 首次发布/提交后崩溃、allocation 各窗口和 unresolved pending 建立反例。
- [ ] 明确现有 Web/视频 WIP 的隔离方式。
- [ ] 对计划最终自检逐项给出代码或测试证据。

## 8. 结论

本轮 Review 指出的合同矛盾已进入 CEPlan，但整体计划仍只是“条件关闭”。当前只允许进入 Phase 0 合同与 schema review，不允许直接开始 Phase A1，更不能宣布 Phase A、Web Manifest 接入或工程 Agent 闭环已经完成。
